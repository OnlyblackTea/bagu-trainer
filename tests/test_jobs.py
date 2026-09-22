"""``platform_api.jobs``：后台阅卷登记处（提交异步化之后的执行与**耐久**记录层）。

这里测的是三件"跑起来才知道"的事，它们在路由测试里看不出来：

* **并发**：路由只看到 202，真正决定"会不会两个阅卷互相砸场子"的是这一层；
* **失败分类**：跑不起来 ≠ 没过（§7.1），而异步之后异常处理器够不着后台线程；
* **耐久**：运行记录必须写穿到 store，重启后要能分清"被打断"和"还在跑"。
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

from platform_api.jobs import (
    GradingJobs,
    classify_grading_error,
    mark_interrupted,
    resolve_instance_id,
)
from platform_api.schemas import grading_run_out
from platform_service.grading import GradingProgress, GradingStep, report_progress
from platform_service.models import (
    GradingRun,
    GradingRunStatus,
    GradingStepStatus,
    InstanceState,
    TicketInstance,
)
from platform_service.store import InMemoryStore
from platform_service.stubs import StubGradingRunner


def _instance(instance_id: str = "ti-1") -> TicketInstance:
    return TicketInstance(
        id=instance_id,
        learner="deployer",
        track="database-bagu",
        ticket_id="db-stage-00",
        ticket_version=1,
        engine_version="v0.1.0",
        test_version=1,
        state=InstanceState.IN_PROGRESS,
    )


def _ticket(ticket_factory: Any) -> Any:
    from platform_service.models import TicketSpec

    return TicketSpec.model_validate(ticket_factory())


def _track(track_factory: Any) -> Any:
    from platform_service.models import TrackManifest

    return TrackManifest.model_validate(track_factory()["track"])


def _start(jobs: GradingJobs, tmp_path: Path, factories: tuple[Any, Any], **over: Any) -> Any:
    ticket_factory, track_factory = factories
    kwargs: dict[str, Any] = {
        "submission_id": over.pop("submission_id", "sub-1"),
        "instance": over.pop("instance", _instance()),
        "ticket": _ticket(ticket_factory),
        "track": _track(track_factory),
        "workspace": tmp_path,
        "scenario": over.pop("scenario", "pass"),
    }
    kwargs.update(over)
    return jobs.start(**kwargs)


def _wait(run: GradingRun, *, timeout_s: float = 10.0) -> None:
    deadline = time.monotonic() + timeout_s
    while run.status in (GradingRunStatus.QUEUED, GradingRunStatus.RUNNING):
        if time.monotonic() > deadline:
            raise AssertionError(f"run never finished: {run.status}")
        time.sleep(0.01)


# --- 并发 ---------------------------------------------------------------------


def test_serializes_grading_never_two_at_once(
    tmp_path: Path, ticket_factory: Any, track_factory: Any
) -> None:
    """**单线程**是设计的一部分，不是省事：环境重置会 ``down -v`` 掉共享基础设施、maven 抢端口，
    并行不是更快而是互相砸场子（一个把另一个的数据库删了）。"""
    live = 0
    peak = 0
    lock = threading.Lock()

    class _Slow:
        def grade(self, *, instance, ticket, track, workspace, on_progress=None):  # noqa: ANN001
            nonlocal live, peak
            with lock:
                live += 1
                peak = max(peak, live)
            time.sleep(0.05)
            with lock:
                live -= 1
            return StubGradingRunner(scenario="pass").grade(
                instance=instance, ticket=ticket, track=track, workspace=workspace
            )

    jobs = GradingJobs(InMemoryStore(), lambda scenario: _Slow())
    runs = [
        _start(
            jobs,
            tmp_path,
            (ticket_factory, track_factory),
            submission_id=f"sub-{i}",
            instance=_instance(f"ti-{i}"),
        )
        for i in range(4)
    ]
    for run in runs:
        _wait(run)
    jobs.shutdown()

    assert peak == 1, f"两个阅卷同时跑了（峰值 {peak}）——共享基础设施会被互相破坏"


# --- 阶段与耐久 ----------------------------------------------------------------


def test_reports_every_phase_with_its_own_bounds(
    tmp_path: Path, ticket_factory: Any, track_factory: Any
) -> None:
    """阶段在启动时就摆好（PENDING），跑到哪一步就翻到哪一步，**每步各自记起止时间**。

    "120 秒里 109 秒在重置基础设施"就是从这些边界读出来的——没有它们，慢阅卷无法解释。
    """
    jobs = GradingJobs(InMemoryStore(), lambda scenario: StubGradingRunner(scenario="pass"))
    run = _start(jobs, tmp_path, (ticket_factory, track_factory))
    _wait(run)
    jobs.shutdown()

    assert [s.step for s in run.steps] == ["reset_infra", "run_tests", "parse"]
    assert all(s.status is GradingStepStatus.DONE for s in run.steps)
    assert all(s.started_at and s.ended_at for s in run.steps)
    assert run.status is GradingRunStatus.DONE and run.report_ready is True
    assert run.elapsed_ms is not None


def test_run_is_written_through_to_the_store(
    tmp_path: Path, ticket_factory: Any, track_factory: Any
) -> None:
    """运行记录必须**落库**：这正是"阅卷很久、重启后还得能查"的前提。"""
    store = InMemoryStore()
    jobs = GradingJobs(store, lambda scenario: StubGradingRunner(scenario="pass"))
    run = _start(jobs, tmp_path, (ticket_factory, track_factory))

    # 一登记就在库里（还没跑完也是"存在"的）——这条必须与"跑得快不快"无关
    assert store.get_grading_run("sub-1") is not None
    _wait(run)
    jobs.shutdown()

    stored = store.get_grading_run("sub-1")
    assert stored is not None and stored.status is GradingRunStatus.DONE
    assert jobs.is_live("sub-1") is False
    # 历史查询按实例取
    assert [r.submission_id for r in store.list_grading_runs(instance_id="ti-1")] == ["sub-1"]


def test_failure_is_recorded_as_a_short_code_not_a_bad_grade(
    tmp_path: Path, ticket_factory: Any, track_factory: Any
) -> None:
    """跑不起来 ≠ 没过。异步之后异常处理器够不着后台线程，所以分类必须在登记处就地做对。"""
    from platform_api.grading_docker import GradingUnavailableError

    class _Broken:
        def grade(self, *, instance, ticket, track, workspace, on_progress=None):  # noqa: ANN001
            raise GradingUnavailableError("maven not found on PATH")

    store = InMemoryStore()
    jobs = GradingJobs(store, lambda scenario: _Broken())
    run = _start(jobs, tmp_path, (ticket_factory, track_factory))
    _wait(run)
    jobs.shutdown()

    assert run.status is GradingRunStatus.FAILED and run.report_ready is False
    assert run.error_code == "grading_unavailable"
    assert run.detail == "maven not found on PATH"
    assert store.get_grading_run("sub-1").status is GradingRunStatus.FAILED
    # 这次是在**第一阶段开始之前**就失败的（缺 maven 在重置环境之前就查出来），所以三个阶段都还是
    # 待跑——这比"把一步标成失败"更诚实：那一步根本没开始。为什么没开始读 run.detail / error。
    assert all(s.status is GradingStepStatus.PENDING for s in run.steps)


def test_unexpected_failure_still_becomes_a_queryable_state(
    tmp_path: Path, ticket_factory: Any, track_factory: Any
) -> None:
    class _Boom:
        def grade(self, *, instance, ticket, track, workspace, on_progress=None):  # noqa: ANN001
            raise RuntimeError("something internal")

    jobs = GradingJobs(InMemoryStore(), lambda scenario: _Boom())
    run = _start(jobs, tmp_path, (ticket_factory, track_factory))
    _wait(run)
    jobs.shutdown()

    assert run.error_code == "internal_error" and jobs.is_live("sub-1") is False


def test_report_write_failure_fails_the_run_too(
    tmp_path: Path, ticket_factory: Any, track_factory: Any
) -> None:
    """报告落库失败也意味着这次阅卷没完成——不能记成 done 让学员以为能 decide 了。"""

    def on_done(submission_id: str, report: Any) -> None:
        raise RuntimeError("report column exploded")

    jobs = GradingJobs(InMemoryStore(), lambda scenario: StubGradingRunner(scenario="pass"))
    run = _start(jobs, tmp_path, (ticket_factory, track_factory), on_done=on_done)
    _wait(run)
    jobs.shutdown()

    assert run.status is GradingRunStatus.FAILED and run.report_ready is False


# --- 重启后的诚实口径 -----------------------------------------------------------


def test_mark_interrupted_only_touches_its_own_owner(
    tmp_path: Path, ticket_factory: Any, track_factory: Any
) -> None:
    """多实例共库时，无故把对方正在跑的阅卷标成中断是有害的。清扫只动自己名下的。"""
    store = InMemoryStore()
    mine = GradingJobs(store, lambda scenario: StubGradingRunner(scenario="pass"), owner="a")
    theirs = GradingJobs(
        store, lambda scenario: StubGradingRunner(scenario="pass"), owner="b"
    )
    # 两个都卡在 queued：用一个不会结束的 runner 占住单线程执行器。
    gate = threading.Event()

    class _Stuck:
        def grade(self, *, instance, ticket, track, workspace, on_progress=None):  # noqa: ANN001
            gate.wait(10)
            return StubGradingRunner(scenario="pass").grade(
                instance=instance, ticket=ticket, track=track, workspace=workspace
            )

    mine._runner_factory = lambda scenario: _Stuck()  # noqa: SLF001
    theirs._runner_factory = lambda scenario: _Stuck()  # noqa: SLF001
    _start(mine, tmp_path, (ticket_factory, track_factory), submission_id="sub-mine")
    _start(theirs, tmp_path, (ticket_factory, track_factory), submission_id="sub-theirs")

    touched = mark_interrupted(store, owner="a", reason="重启")
    gate.set()
    mine.shutdown()
    theirs.shutdown()

    assert touched == 1
    assert store.get_grading_run("sub-mine").status is GradingRunStatus.INTERRUPTED
    assert store.get_grading_run("sub-mine").error_reason == "重启"
    # 别人的那条**没被动**：它可能真的还在跑
    assert store.get_grading_run("sub-theirs") is not None


def test_startup_recovery_reports_interrupted_not_unknown(
    tmp_path: Path, ticket_factory: Any, track_factory: Any
) -> None:
    """重启后那条卡在 running 的记录必须变成 ``interrupted``——而不是永远撒谎说"正在阅卷"。

    直接摆一条"上个进程留下的"记录，再跑一次清扫，然后看**读给前端的口径**。
    """
    store = InMemoryStore()
    store.save_grading_run(
        GradingRun(
            submission_id="sub-old",
            instance_id="ti-1",
            track="database-bagu",
            ticket_id="db-stage-00",
            learner="deployer",
            owner="default",
            status=GradingRunStatus.RUNNING,
            step="run_tests",
            steps=[{"step": "run_tests", "status": "running"}],  # type: ignore[list-item]
        )
    )
    from platform_api.jobs import recover_interrupted_runs

    assert recover_interrupted_runs(store, owner="default") == 1

    run = store.get_grading_run("sub-old")
    assert run.status is GradingRunStatus.INTERRUPTED
    assert "重启" in (run.error_reason or "")
    view = grading_run_out(run, live=False)
    assert view["status"] == "interrupted"
    assert "重启" in view["detail"]


def test_active_run_held_by_nobody_reads_as_stalled(
    tmp_path: Path, ticket_factory: Any, track_factory: Any
) -> None:
    """有活跃记录但本进程没持有 ⇒ ``stalled``，措辞**不替它下结论**（可能是另一个实例在跑）。"""
    run = GradingRun(
        submission_id="sub-x",
        instance_id="ti-1",
        track="database-bagu",
        ticket_id="db-stage-00",
        learner="deployer",
        owner="another-instance",
        status=GradingRunStatus.RUNNING,
    )
    view = grading_run_out(run, live=False)
    assert view["status"] == "stalled"
    assert "另一个平台实例" in view["detail"]
    # 本进程持有就是 running
    assert grading_run_out(run, live=True)["status"] == "running"


def test_no_run_at_all_reads_as_unknown() -> None:
    view = grading_run_out(None, live=False)
    assert view["status"] == "unknown" and view["steps"] == []


def test_instance_id_comes_from_flag_then_env_then_default(monkeypatch: Any) -> None:
    monkeypatch.delenv("BAGU_INSTANCE_ID", raising=False)
    assert resolve_instance_id() == "default"
    monkeypatch.setenv("BAGU_INSTANCE_ID", "b")
    assert resolve_instance_id() == "b"
    assert resolve_instance_id("c") == "c"  # 显式参数压过环境变量


# --- 上报与分类 ----------------------------------------------------------------


def test_a_failing_progress_reporter_cannot_break_grading() -> None:
    """进度是附属品：上报炸了绝不能把一次本来能跑完的阅卷变成失败。

    所以 runner 一律经 :func:`report_progress` 上报，而不是直接调回调——那些调用点都在阅卷的
    **主干**上，一次抛错就会把整次阅卷带走。
    """

    def boom(progress: GradingProgress) -> None:
        raise RuntimeError("reporter exploded")

    report_progress(boom, GradingStep.RUN_TESTS, "x")  # 不抛
    report_progress(None, GradingStep.RUN_TESTS)  # 没有 reporter 也不抛


def test_unknown_exceptions_are_not_dressed_up() -> None:
    code, reason = classify_grading_error(ValueError("whatever"))
    assert code == "internal_error"
    assert "ValueError" in reason
