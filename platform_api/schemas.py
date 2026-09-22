"""A′ REST 的请求/响应 DTO（与域模型解耦，extra=forbid）+ gate 指示文案。

响应里凡域模型本身够用的（Submission/Review/Decision）直接 ``model_dump(mode="json")``；
本模块只补请求体、工作区响应与"实例 + gate 文案"投影。gate 文案体现"平台只 gate + 指示、
学员驱动本地 git"（设计 spec §C.5）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from platform_service.forge import Workspace
from platform_service.history import AttemptRecord
from platform_service.models import (
    GradingRun,
    GradingRunStep,
    InstanceState,
    TicketInstance,
)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EnrollRequest(_Strict):
    """报名。``track`` 可省略——只有一个已部署 Track 时那是唯一的答案；有多个又不说，
    路由回 400 ``enroll_needs_track``（猜错就把学员报进了另一个 Track 的 DAG）。"""

    learner: str = Field(min_length=1)
    track: str | None = None


class SubmitRequest(_Strict):
    pr_url: str | None = None
    defense: str | None = Field(default=None, max_length=20000)


class ReviewRequest(_Strict):
    dimensions: list[str] | None = Field(default=None, min_length=1)


_GATE: dict[InstanceState, str] = {
    InstanceState.LOCKED: "未解锁：先通过全部前置阶段。",
    InstanceState.AVAILABLE: "可开始：start 本实例，再 provision 拉取本地工作区。",
    InstanceState.IN_PROGRESS: "开发中：在本地 dev 分支完成本阶段，再 submit（平台不代写代码）。",
    InstanceState.SUBMITTED: "已提交：对其发起 review。",
    InstanceState.GRADING: "评审中：发起 decide 取确定性结论。",
    InstanceState.PASSED: "已通过：下一阶段已解锁——从其分支 cherry-pick / 提 PR 合入 dev 后继续。",
    InstanceState.FAILED: "未通过：按评审意见在本地改 dev，再 retry 重新提交。",
    InstanceState.STALE: "已作废（版本漂移）：用 reissue 重发新实例。",
}


def gate_instruction(state: InstanceState) -> str:
    return _GATE[state]


def instance_out(instance: TicketInstance) -> dict[str, Any]:
    return {**instance.model_dump(mode="json"), "gate": gate_instruction(instance.state)}


def workspace_out(ws: Workspace) -> dict[str, Any]:
    return {
        "path": str(ws.path),
        "branch": ws.branch,
        "base_ref": ws.base_ref,
        "head_ref": ws.head_ref,
        # 预览/评审拿什么和 base 比：null = 工作区（含未提交）。见 Workspace 的 docstring。
        "diff_head_ref": ws.diff_head_ref,
        "push_hint": ws.push_hint,
        "simulated": ws.simulated,
    }


#: 阅卷运行状态 → 读给前端的口径。``stalled`` **不是**持久化状态，而是"这条活跃记录不在本进程手里"
#: 的读时判断：服务重启过，或它由另一个平台实例在跑。措辞刻意不下结论——两种可能都无法排除。
_STALLED_DETAIL = (
    "这条提交的阅卷不在本进程手里：服务重启过，或它由另一个平台实例在跑。"
    "报告落库之前无法确认它是否还在跑。"
)


def grading_run_out(run: GradingRun | None, *, live: bool) -> dict[str, Any]:
    """一次阅卷运行的可读投影（§C.3 步骤 6）。

    * ``run is None`` ⇒ ``unknown``：这条提交压根没有运行记录（来自本特性之前，或提交没成功）。
    * ``run`` 活跃但本进程没持有 ⇒ ``stalled``：见上面的口径说明。
    * 其余照实投影，含**每阶段的起止时间**——"120 秒里 109 秒在重置基础设施"就是从这里读出来的。
    """
    if run is None:
        return {
            "submission_id": None,
            "instance_id": None,
            "track": None,
            "status": "unknown",
            "step": "unknown",
            "detail": (
                "这条提交没有阅卷运行记录（它可能来自本特性之前，或提交没有真正开始）。"
                "可以 retry 重新提交一次。"
            ),
            "started_at": None,
            "ended_at": None,
            "elapsed_ms": None,
            "report_ready": False,
            "owner": None,
            "error": None,
            "steps": [],
        }
    stalled = run.is_active and not live
    return {
        "submission_id": run.submission_id,
        "instance_id": run.instance_id,
        "track": run.track,
        "ticket_id": run.ticket_id,
        "owner": run.owner,
        "status": "stalled" if stalled else run.status.value,
        "step": run.step,
        "detail": _STALLED_DETAIL if stalled else run.detail,
        "started_at": run.started_at.isoformat(),
        "ended_at": run.ended_at.isoformat() if run.ended_at else None,
        "elapsed_ms": run.elapsed_ms if run.ended_at else elapsed_since(run.started_at),
        "report_ready": run.report_ready,
        "error": (
            {"detail": run.error_code, "reason": run.error_reason} if run.error_code else None
        ),
        "steps": [
            {
                "step": step.step,
                "status": step.status.value,
                "detail": step.detail,
                "started_at": step.started_at.isoformat() if step.started_at else None,
                "ended_at": step.ended_at.isoformat() if step.ended_at else None,
                "elapsed_ms": step_elapsed_ms(step),
            }
            for step in run.steps
        ],
    }


def elapsed_since(started: datetime) -> int:
    return int((datetime.now(UTC) - started).total_seconds() * 1000)


def step_elapsed_ms(step: GradingRunStep) -> int | None:
    if step.started_at is None:
        return None
    return int(((step.ended_at or datetime.now(UTC)) - step.started_at).total_seconds() * 1000)


def attempt_out(attempt: AttemptRecord) -> dict[str, Any]:
    """一条历史尝试。``delta`` 可能是 ``None``（第一次尝试没有"上一次"可比）。"""
    return {
        "submission": attempt.submission.model_dump(mode="json"),
        "grading": (
            grading_run_out(attempt.grading, live=False) if attempt.grading is not None else None
        ),
        "report": attempt.report.model_dump(mode="json") if attempt.report else None,
        "review": attempt.review.model_dump(mode="json") if attempt.review else None,
        "decision": attempt.decision.model_dump(mode="json") if attempt.decision else None,
        "verdict": attempt.verdict,
        "delta": attempt.delta.model_dump(mode="json") if attempt.delta else None,
    }
