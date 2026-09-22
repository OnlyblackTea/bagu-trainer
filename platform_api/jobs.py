"""提交后的阅卷任务（设计 spec §C.3 步骤 6）。

``submit`` 返回 **202** 并立刻把阅卷丢进后台，学员轮询 ``GET /submissions/{id}/progress``。
为什么必须异步：真阅卷要 ``docker compose down -v`` 再 ``mvn test``，几十秒起步；同步做的话
一个 HTTP 请求就一直挂着，中间没有任何"在干什么"的反馈——这正是"提交要有进度反馈"要修的东西。

**为什么单线程**：环境重置会 ``down -v`` 掉 Track 的**共享**基础设施，maven 也要抢端口与
``~/.m2``。两个阅卷并行不是"更快"，而是互相砸场子（一个把另一个的数据库删了）。所以这里是
一个 ``max_workers=1`` 的执行器：任务排队，不并行。

**为什么进度不落库**：进度是**运行时瞬时状态**，进程一没就失去意义（那条 submission 的
report 要么已落库、要么就是没跑完）。给它加一张表只会让 ``Store`` 缝为一件转瞬即逝的事变宽，
而且重启后读到的"正在跑第 2 步"必然是假的。所以：登记处只在内存，重启后**如实说不知道**
（``status=unknown``），而不是编一个进度出来。
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from platform_service.grading import (
    GradingProgress,
    GradingRunner,
    GradingStep,
    report_progress,
)
from platform_service.models import TicketInstance, TicketSpec, TrackManifest

JobStatus = Literal["queued", "running", "done", "failed"]

#: 保留多少个已结束的任务。进度只对"刚刚那次提交"有用；无限留着就是内存泄漏。
_KEEP_FINISHED = 200

#: 阶段顺序。前端按它把步骤渲染成一条链（没到的显示"待跑"）。
STEP_ORDER: tuple[GradingStep, ...] = (
    GradingStep.RESET_INFRA,
    GradingStep.RUN_TESTS,
    GradingStep.PARSE,
)


@dataclass
class StepState:
    step: GradingStep
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"step": self.step.value, "detail": self.detail}


@dataclass
class GradingJob:
    """一次后台阅卷的可观察状态。``error_code`` 是 §C.8 的短码，不是 Python 类名。"""

    submission_id: str
    instance_id: str
    track_id: str
    scenario: str
    status: JobStatus = "queued"
    current: GradingStep = GradingStep.QUEUED
    detail: str = ""
    started_at: datetime | None = None
    ended_at: datetime | None = None
    error_code: str | None = None
    error_reason: str | None = None
    #: 已经走过的阶段（按到达顺序）。用它而不是"当前步"来渲染，重跑/跳步都不会错。
    visited: list[StepState] = field(default_factory=list)
    report_ready: bool = False

    @property
    def elapsed_ms(self) -> int | None:
        if self.started_at is None:
            return None
        end = self.ended_at or datetime.now(UTC)
        return int((end - self.started_at).total_seconds() * 1000)

    def as_dict(self) -> dict[str, Any]:
        """给前端的投影。``steps`` 是**完整**的阶段链（含未到达的），进度条才画得出来。"""
        return {
            "submission_id": self.submission_id,
            "instance_id": self.instance_id,
            "track": self.track_id,
            "status": self.status,
            "step": self.current.value,
            "detail": self.detail,
            "elapsed_ms": self.elapsed_ms,
            "report_ready": self.report_ready,
            "error": (
                {"detail": self.error_code, "reason": self.error_reason}
                if self.error_code
                else None
            ),
            "steps": [
                {
                    "step": step.value,
                    "status": _step_status(step, self),
                    "detail": next(
                        (v.detail for v in self.visited if v.step == step), ""
                    ),
                }
                for step in STEP_ORDER
            ],
        }


def _step_status(step: GradingStep, job: GradingJob) -> str:
    if any(v.step == step for v in job.visited):
        return "done"
    if job.current == step and job.status in ("queued", "running"):
        return "running"
    if job.status == "failed" and job.current == step:
        return "failed"
    return "pending"


class GradingJobs:
    """提交后的阅卷任务登记处：**单线程**排队执行 + 可查询进度。

    runner 由 ``runner_factory`` 按 scenario 现造（桩按 scenario 分叉，真阅卷不看 scenario），
    与 ``app.state.grading_runner`` 同一个来源，所以两个后端的行为差别在这一层是透明的。
    """

    def __init__(self, runner_factory: Callable[[str], GradingRunner]) -> None:
        self._runner_factory = runner_factory
        # max_workers=1：见模块 docstring（共享基础设施 + 端口，并行等于互相砸场子）。
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="bagu-grading")
        self._jobs: dict[str, GradingJob] = {}
        self._lock = threading.Lock()

    # -- 查询 ----------------------------------------------------------------

    def get(self, submission_id: str) -> GradingJob | None:
        with self._lock:
            return self._jobs.get(submission_id)

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
    ) -> GradingJob:
        """登记并排队一次阅卷。立刻返回（``queued``），不等结果。

        ``on_done`` / ``on_error`` 让调用方（路由层）在任务结束时把报告落库、或记下失败短码——
        登记处自己不认识 ``Store``，那是组合根的事。
        """
        job = GradingJob(
            submission_id=submission_id,
            instance_id=instance.id,
            track_id=track.id,
            scenario=scenario,
        )
        with self._lock:
            self._jobs[submission_id] = job
            self._evict_finished_locked()
        self._executor.submit(
            self._run, job, instance, ticket, track, workspace, on_done, on_error
        )
        return job

    # -- 执行 ----------------------------------------------------------------

    def _run(
        self,
        job: GradingJob,
        instance: TicketInstance,
        ticket: TicketSpec,
        track: TrackManifest,
        workspace: Path,
        on_done: Callable[[str, Any], None] | None,
        on_error: Callable[[str, str, str], None] | None,
    ) -> None:
        job.status = "running"
        job.started_at = datetime.now(UTC)
        job.current = GradingStep.QUEUED
        job.detail = "排队完成，开始阅卷"

        def on_progress(progress: GradingProgress) -> None:
            with self._lock:
                job.current = progress.step
                job.detail = progress.detail
                if not any(v.step == progress.step for v in job.visited):
                    job.visited.append(StepState(progress.step, progress.detail))

        try:
            report = self._runner_factory(job.scenario).grade(
                instance=instance,
                ticket=ticket,
                track=track,
                workspace=workspace,
                on_progress=on_progress,
            )
        except Exception as exc:  # noqa: BLE001 — 失败要落成可查询的状态，不是消失在日志里
            code, reason = classify_grading_error(exc)
            with self._lock:
                job.status = "failed"
                job.error_code = code
                job.error_reason = reason
                job.ended_at = datetime.now(UTC)
            if on_error is not None:
                try:
                    on_error(job.submission_id, code, reason)
                except Exception:  # noqa: BLE001 — 见下：回调问题不改变"这次阅卷失败了"
                    pass
            return

        if on_done is not None:
            try:
                on_done(job.submission_id, report)
            except Exception as exc:  # noqa: BLE001
                code, reason = classify_grading_error(exc)
                with self._lock:
                    job.status = "failed"
                    job.error_code = code
                    job.error_reason = reason
                    job.ended_at = datetime.now(UTC)
                return

        # 注意：on_progress 自己会拿 self._lock，所以**不能**在持锁时调它（普通 Lock 不可重入，
        # 那就是一个必然的死锁）。
        report_progress(on_progress, GradingStep.DONE, "阅卷完成")
        with self._lock:
            job.status = "done"
            job.report_ready = True
            job.ended_at = datetime.now(UTC)

    def _evict_finished_locked(self) -> None:
        """只淘汰**已结束**的任务，且按开始时间从旧到新。跑着的永远不被淘汰。"""
        if len(self._jobs) <= _KEEP_FINISHED:
            return
        finished = [
            (job.started_at or datetime.min.replace(tzinfo=UTC), sid)
            for sid, job in self._jobs.items()
            if job.status in ("done", "failed")
        ]
        for _, sid in sorted(finished)[: len(self._jobs) - _KEEP_FINISHED]:
            self._jobs.pop(sid, None)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)


def classify_grading_error(exc: BaseException) -> tuple[str, str]:
    """把异常翻成 §C.8 的短码 + 原因，且**不把环境问题说成"你的代码没过"**（§7.1 / D3）。

    这里刻意与 ``app.py`` 的处理器用同一套短码：异步之后失败发生在后台线程里，异常处理器
    够不着，只能在登记处就地分类，并让 ``GET report`` / ``GET progress`` 原样报出来。
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
