"""``platform_api.jobs``：后台阅卷登记处（提交异步化之后的执行层）。

这里测的是**并发与失败**这两件"跑起来才知道"的事——它们在路由测试里看不出来：
路由只看到 202，真正决定"会不会两个阅卷互相砸场子""跑不起来会不会被说成没过"的是这一层。
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

from platform_api.jobs import GradingJobs, classify_grading_error
from platform_service.grading import GradingProgress, GradingStep, report_progress
from platform_service.models import InstanceState, TicketInstance
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


def _wait(job: Any, *, timeout_s: float = 10.0) -> None:
    deadline = time.monotonic() + timeout_s
    while job.status in ("queued", "running") and time.monotonic() < deadline:
        time.sleep(0.01)
    assert job.status in ("done", "failed"), job


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

    jobs = GradingJobs(lambda scenario: _Slow())
    started = [
        jobs.start(
            submission_id=f"sub-{i}",
            instance=_instance(f"ti-{i}"),
            ticket=_ticket(ticket_factory),
            track=_track(track_factory),
            workspace=tmp_path,
            scenario="pass",
        )
        for i in range(4)
    ]
    for job in started:
        _wait(job)
    jobs.shutdown()

    assert peak == 1, f"两个阅卷同时跑了（峰值 {peak}）——共享基础设施会被互相破坏"


def test_reports_every_visited_step(
    tmp_path: Path, ticket_factory: Any, track_factory: Any
) -> None:
    jobs = GradingJobs(lambda scenario: StubGradingRunner(scenario="pass"))
    job = jobs.start(
        submission_id="sub-1",
        instance=_instance(),
        ticket=_ticket(ticket_factory),
        track=_track(track_factory),
        workspace=tmp_path,
        scenario="pass",
    )
    _wait(job)
    jobs.shutdown()

    body = job.as_dict()
    assert body["status"] == "done" and body["report_ready"] is True
    assert [s["step"] for s in body["steps"]] == ["reset_infra", "run_tests", "parse"]
    assert all(s["status"] == "done" for s in body["steps"])
    assert body["elapsed_ms"] is not None


def test_failure_is_recorded_as_a_short_code_not_a_bad_grade(
    tmp_path: Path, ticket_factory: Any, track_factory: Any
) -> None:
    """跑不起来 ≠ 没过。异步之后异常处理器够不着后台线程，所以分类必须在登记处就地做对。"""
    from platform_api.grading_docker import GradingUnavailableError

    class _Broken:
        def grade(self, *, instance, ticket, track, workspace, on_progress=None):  # noqa: ANN001
            raise GradingUnavailableError("maven not found on PATH")

    jobs = GradingJobs(lambda scenario: _Broken())
    job = jobs.start(
        submission_id="sub-1",
        instance=_instance(),
        ticket=_ticket(ticket_factory),
        track=_track(track_factory),
        workspace=tmp_path,
        scenario="pass",
    )
    _wait(job)
    jobs.shutdown()

    body = job.as_dict()
    assert body["status"] == "failed" and body["report_ready"] is False
    assert body["error"] == {"detail": "grading_unavailable", "reason": "maven not found on PATH"}
    assert "你的代码" not in str(body)


def test_unexpected_failure_still_becomes_a_queryable_state(
    tmp_path: Path, ticket_factory: Any, track_factory: Any
) -> None:
    class _Boom:
        def grade(self, *, instance, ticket, track, workspace, on_progress=None):  # noqa: ANN001
            raise RuntimeError("something internal")

    jobs = GradingJobs(lambda scenario: _Boom())
    job = jobs.start(
        submission_id="sub-1",
        instance=_instance(),
        ticket=_ticket(ticket_factory),
        track=_track(track_factory),
        workspace=tmp_path,
        scenario="pass",
    )
    _wait(job)
    jobs.shutdown()

    assert job.as_dict()["error"]["detail"] == "internal_error"


def test_a_failing_progress_reporter_cannot_break_grading() -> None:
    """进度是附属品：上报炸了绝不能把一次本来能跑完的阅卷变成失败。

    所以 runner 一律经 :func:`report_progress` 上报，而不是直接调回调——那些调用点都在
    阅卷的**主干**上，一次抛错就会把整次阅卷带走。
    """

    def boom(progress: GradingProgress) -> None:
        raise RuntimeError("reporter exploded")

    report_progress(boom, GradingStep.RUN_TESTS, "x")  # 不抛
    report_progress(None, GradingStep.RUN_TESTS)  # 没有 reporter 也不抛


def test_unknown_exceptions_are_not_dressed_up() -> None:
    code, reason = classify_grading_error(ValueError("whatever"))
    assert code == "internal_error"
    assert "ValueError" in reason


def test_running_jobs_are_never_evicted(
    tmp_path: Path, ticket_factory: Any, track_factory: Any, monkeypatch: Any
) -> None:
    """淘汰只针对**已结束**的任务，否则长跑的那一个会在轮询时凭空消失。"""
    import platform_api.jobs as jobs_module

    monkeypatch.setattr(jobs_module, "_KEEP_FINISHED", 1)
    gate = threading.Event()

    class _Stuck:
        def grade(self, *, instance, ticket, track, workspace, on_progress=None):  # noqa: ANN001
            gate.wait(10)
            return StubGradingRunner(scenario="pass").grade(
                instance=instance, ticket=ticket, track=track, workspace=workspace
            )

    jobs = GradingJobs(lambda scenario: _Stuck())
    jobs.start(
        submission_id="sub-stuck",
        instance=_instance("ti-stuck"),
        ticket=_ticket(ticket_factory),
        track=_track(track_factory),
        workspace=tmp_path,
        scenario="pass",
    )
    for i in range(4):
        done = jobs.start(
            submission_id=f"sub-{i}",
            instance=_instance(f"ti-{i}"),
            ticket=_ticket(ticket_factory),
            track=_track(track_factory),
            workspace=tmp_path,
            scenario="pass",
        )
        # 后面的任务排在 stuck 之后（单线程），所以它们一直是 queued——正是"不该被淘汰"的那类。
        assert done.status in ("queued", "running")
    assert jobs.get("sub-stuck") is not None
    gate.set()
    jobs.shutdown()
