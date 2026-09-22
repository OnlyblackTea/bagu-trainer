"""提交后的阅卷任务（设计 spec §C.3 步骤 6）。

``submit`` 返回 **202** 并立刻把阅卷丢进后台，学员轮询 ``GET /submissions/{id}/progress``。
为什么必须异步：真阅卷要 ``docker compose down -v`` 再 ``mvn test``，实测整次 120 秒（其中重置
基础设施就占 109 秒）；同步做的话一个 HTTP 请求就一直挂着，中间没有任何"在干什么"的反馈。

**为什么单线程**：环境重置会 ``down -v`` 掉 Track 的**共享**基础设施，maven 也要抢端口与
``~/.m2``。两个阅卷并行不是"更快"，而是互相砸场子（一个把另一个的数据库删了）。所以这里是
一个 ``max_workers=1`` 的执行器：任务排队，不并行。

**为什么运行记录要落库**（v0.6 改掉了 v0.5 的"进度只留内存"）：阅卷是**分钟级**的事。进程在
中途重启时，只留内存的登记处只能回答"我不知道"——学员分不清"还在跑"和"早就死了"，而且跑完那
一次也没留下任何可供后来比对的痕迹。所以 :class:`~platform_service.models.GradingRun` 是**实体**，
每次状态变化都写穿到 :class:`~platform_service.store.Store`。结果（``GradingReport``）本来就落库，
这里补的是它外面的**执行记录**：走到哪一阶段、每阶段花了多久、没跑完的话是哪个短码。

**为什么要有 owner**：重启后必须把卡在 ``running`` 的孤儿记录标成 ``interrupted``（崩掉的进程
没法给自己写墓志铭），但**两个平台实例共用一个库**时，无故标死别人的在跑任务是有害的。所以每条
记录带 ``owner``（本进程的 ``BAGU_INSTANCE_ID``），清扫**只动自己名下的**；别人的活跃记录在本
进程只读成 ``stalled``（非破坏性，且措辞不替它下结论）。单实例部署是文档写明的主场景，那时
``interrupted`` 就是跨重启的诚实终态。
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from platform_service.grading import (
    GradingProgress,
    GradingRunner,
    GradingStep,
)
from platform_service.models import (
    GradingRun,
    GradingRunStatus,
    GradingRunStep,
    GradingStepStatus,
    TicketInstance,
    TicketSpec,
    TrackManifest,
)
from platform_service.store import Store

#: 本进程的平台实例标识。多实例共库时必须各不相同，否则会互相清扫对方的在跑任务。
INSTANCE_ID_ENV = "BAGU_INSTANCE_ID"
DEFAULT_INSTANCE_ID = "default"

#: 阶段顺序。启动时就把三步写成 PENDING，于是：
#: ① 前端从第一次轮询起就有一条稳定的阶段链可画；② 每阶段的起止时间有确定的位置可填。
STEP_ORDER: tuple[GradingStep, ...] = (
    GradingStep.RESET_INFRA,
    GradingStep.RUN_TESTS,
    GradingStep.PARSE,
)

#: 视为"还没结束"的状态。
_ACTIVE = (GradingRunStatus.QUEUED, GradingRunStatus.RUNNING)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _ms(started: datetime | None, ended: datetime | None = None) -> int | None:
    if started is None:
        return None
    return int(((ended or _utcnow()) - started).total_seconds() * 1000)


def resolve_instance_id(explicit: str | None = None) -> str:
    """``--instance-id`` > ``BAGU_INSTANCE_ID`` > ``default``。"""
    if explicit:
        return explicit
    return os.environ.get(INSTANCE_ID_ENV, "").strip() or DEFAULT_INSTANCE_ID


class GradingJobs:
    """后台阅卷的执行与**耐久**登记处。

    它只做三件事：排队（单线程）、把状态变化写穿到 store、在启动/退出时清扫自己的孤儿记录。
    读路径不经它——``GET progress`` / ``GET history`` 直接读 store。这样"当前进度"与"历史"永远
    是同一份数据，不会出现"接口说 done、历史里还是 running"。
    """

    def __init__(
        self,
        store: Store,
        runner_factory: Callable[[str], GradingRunner],
        *,
        owner: str = DEFAULT_INSTANCE_ID,
    ) -> None:
        self._store = store
        self._runner_factory = runner_factory
        self._owner = owner
        # max_workers=1：见模块 docstring（共享基础设施 + 端口，并行等于互相砸场子）。
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="bagu-grading")
        #: 本进程**正在**持有的 submission id。读路径用它区分"我这儿在跑"与"别人/上一个进程的"。
        self._live: set[str] = set()
        self._lock = threading.Lock()

    @property
    def owner(self) -> str:
        return self._owner

    # -- 查询（活在内存里的部分只有"是不是本进程在跑"）--------------------------

    def is_live(self, submission_id: str) -> bool:
        with self._lock:
            return submission_id in self._live

    def get(self, submission_id: str) -> GradingRun | None:
        return self._store.get_grading_run(submission_id)

    # -- 提交 ----------------------------------------------------------------

    def start(
        self,
        *,
        submission_id: str,
        instance: TicketInstance,
        ticket: TicketSpec,
        track: TrackManifest,
        workspace: Path,
        scenario: str,
        on_done: Callable[[str, Any], None] | None = None,
        on_error: Callable[[str, str, str], None] | None = None,
    ) -> GradingRun:
        """登记并排队一次阅卷。立刻返回（``queued``），不等结果。

        ``on_done`` / ``on_error`` 让调用方（路由层）在任务结束时把报告落库、或记下失败短码——
        登记处自己不认识"报告要重新键到 submission 上"这条域规则，那是
        :meth:`~platform_service.service.PlatformService.attach_report` 的事。
        """
        run = GradingRun(
            submission_id=submission_id,
            instance_id=instance.id,
            track=track.id,
            ticket_id=instance.ticket_id,
            learner=instance.learner,
            owner=self._owner,
            status=GradingRunStatus.QUEUED,
            step=GradingStep.QUEUED.value,
            detail="排队中：阅卷单线程跑，前面的跑完才轮到这次",
            steps=[GradingRunStep(step=s.value) for s in STEP_ORDER],
        )
        self._store.save_grading_run(run)
        with self._lock:
            self._live.add(submission_id)
        self._executor.submit(
            self._run, run, instance, ticket, track, workspace, scenario, on_done, on_error
        )
        return run

    # -- 执行 ----------------------------------------------------------------

    def _run(
        self,
        run: GradingRun,
        instance: TicketInstance,
        ticket: TicketSpec,
        track: TrackManifest,
        workspace: Path,
        scenario: str,
        on_done: Callable[[str, Any], None] | None,
        on_error: Callable[[str, str, str], None] | None,
    ) -> None:
        run.status = GradingRunStatus.RUNNING
        run.started_at = _utcnow()
        run.detail = "开始阅卷"
        self._store.save_grading_run(run)

        def on_progress(progress: GradingProgress) -> None:
            """阶段上报 → 就地更新 run 并落库。

            两条纪律：**不能**在持 ``self._lock`` 时调它（普通 ``Lock`` 不可重入，而它会进来拿锁，
            那就是一个必然的死锁）；**不能**让它抛错影响阅卷——那是
            :func:`platform_service.grading.report_progress` 负责吞掉的。
            """
            now = _utcnow()
            for step in run.steps:
                if step.status is GradingStepStatus.RUNNING:
                    step.status = GradingStepStatus.DONE
                    step.ended_at = now
            current = next((s for s in run.steps if s.step == progress.step.value), None)
            if current is None:  # runner 报了一个 STEP_ORDER 之外的阶段：照样记下来，不丢信息
                current = GradingRunStep(step=progress.step.value)
                run.steps.append(current)
            current.status = GradingStepStatus.RUNNING
            current.detail = progress.detail
            current.started_at = current.started_at or now
            run.step = progress.step.value
            run.detail = progress.detail
            self._store.save_grading_run(run)

        failed = False
        try:
            report = self._runner_factory(scenario).grade(
                instance=instance,
                ticket=ticket,
                track=track,
                workspace=workspace,
                on_progress=on_progress,
            )
        except Exception as exc:  # noqa: BLE001 — 失败要落成可查询的历史，不是消失在日志里
            failed = True
            code, reason = classify_grading_error(exc)
            self._finish_failed(run, code=code, reason=reason)
            if on_error is not None:
                _quietly(on_error, run.submission_id, code, reason)
        else:
            try:
                if on_done is not None:
                    on_done(run.submission_id, report)
            except Exception as exc:  # noqa: BLE001 — 报告落库失败也是这次阅卷没完成
                failed = True
                code, reason = classify_grading_error(exc)
                self._finish_failed(run, code=code, reason=reason)

        if not failed:
            self._finish_ok(run)
        with self._lock:
            self._live.discard(run.submission_id)

    def _finish_ok(self, run: GradingRun) -> None:
        now = _utcnow()
        for step in run.steps:
            if step.status is GradingStepStatus.RUNNING:
                step.status = GradingStepStatus.DONE
                step.ended_at = now
        run.status = GradingRunStatus.DONE
        run.step = GradingStep.DONE.value
        run.detail = "阅卷完成"
        run.report_ready = True
        run.ended_at = now
        run.elapsed_ms = _ms(run.started_at, now)
        self._store.save_grading_run(run)

    def _finish_failed(self, run: GradingRun, *, code: str, reason: str) -> None:
        now = _utcnow()
        for step in run.steps:
            if step.status is GradingStepStatus.RUNNING:
                step.status = GradingStepStatus.FAILED
                step.ended_at = now
        run.status = GradingRunStatus.FAILED
        run.step = GradingStep.FAILED.value
        run.detail = reason
        run.error_code = code
        run.error_reason = reason
        run.ended_at = now
        run.elapsed_ms = _ms(run.started_at, now)
        self._store.save_grading_run(run)

    def shutdown(self) -> None:
        """停掉执行器，并把**本进程**还没跑完的记录标成 interrupted。

        不 ``wait``：等待会把 Ctrl-C 卡住几十秒（正在跑的 maven 不会因为我们要退出就停下）。
        排队中被取消的那些由清扫一起收尾——否则它们会以 ``queued`` 留在库里，下次启动再被标一次，
        白白多一轮误导。
        """
        self._executor.shutdown(wait=False, cancel_futures=True)
        mark_interrupted(self._store, owner=self._owner, reason="服务在阅卷过程中退出")


def _quietly(fn: Callable[..., Any], *args: Any) -> None:
    """调用回调并吞掉异常：回调（记失败短码）出问题不应改变"这次阅卷失败了"这个事实。"""
    try:
        fn(*args)
    except Exception:  # noqa: BLE001
        pass


def mark_interrupted(store: Store, *, owner: str, reason: str) -> int:
    """把 ``owner`` 名下仍处于 queued/running 的记录标成 ``interrupted``，返回条数。

    崩掉的进程没法给自己写墓志铭，所以这件事只能由**下一个**启动的进程（或退出时的自查）来做。
    """
    now = _utcnow()
    touched = 0
    for run in store.list_grading_runs(active_only=True):
        if run.owner != owner:
            continue  # 别人的在跑任务：不替它下结论（见模块 docstring）
        run.status = GradingRunStatus.INTERRUPTED
        run.error_code = None
        run.error_reason = reason
        run.detail = reason
        run.ended_at = now
        run.elapsed_ms = _ms(run.started_at, now)
        for step in run.steps:
            if step.status is GradingStepStatus.RUNNING:
                step.status = GradingStepStatus.FAILED
                step.ended_at = now
        store.save_grading_run(run)
        touched += 1
    return touched


def recover_interrupted_runs(store: Store, *, owner: str) -> int:
    """启动时的孤儿清扫。"""
    return mark_interrupted(store, owner=owner, reason="服务在阅卷过程中重启")


def classify_grading_error(exc: BaseException) -> tuple[str, str]:
    """把异常翻成 §C.8 的短码 + 原因，且**不把环境问题说成"你的代码没过"**（§7.1 / D3）。

    这里刻意与 ``app.py`` 的处理器用同一套短码：异步之后失败发生在后台线程里，异常处理器
    够不着，只能在登记处就地分类，并让 ``GET report`` / ``GET progress`` / 历史原样报出来。
    """
    from platform_service.forge import (
        ForgeUnavailableError,
        RemoteBranchMissingError,
        WorkspaceRefMissingError,
    )

    from .grading_docker import GradingUnavailableError

    if isinstance(exc, GradingUnavailableError):
        return "grading_unavailable", str(exc)[:300]
    if isinstance(exc, ForgeUnavailableError):
        return "forge_unavailable", str(exc)[:300]
    if isinstance(exc, RemoteBranchMissingError):
        return "remote_branch_missing", f"{exc.repo}: {exc.branch}"[:300]
    if isinstance(exc, WorkspaceRefMissingError):
        return "workspace_ref_missing", f"{exc.ref}: {exc.reason}"[:300]
    return "internal_error", f"{type(exc).__name__}: {exc}"[:300]


#: 短码 → HTTP 状态。与 ``app.py`` 里的处理器保持一致（同一个短码不该在两处给不同状态码）。
ERROR_STATUS: dict[str, int] = {
    "grading_unavailable": 503,
    "forge_unavailable": 503,
    "remote_branch_missing": 409,
    "workspace_ref_missing": 500,
    "git_command_failed": 500,
    "internal_error": 500,
}
