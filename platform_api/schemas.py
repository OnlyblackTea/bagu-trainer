"""A′ REST 的请求/响应 DTO（与域模型解耦，extra=forbid）+ gate 指示文案。

响应里凡域模型本身够用的（Submission/Review/Decision）直接 ``model_dump(mode="json")``；
本模块只补请求体、工作区响应与"实例 + gate 文案"投影。gate 文案体现"平台只 gate + 指示、
学员驱动本地 git"（设计 spec §C.5）。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from platform_service.forge import Workspace
from platform_service.models import InstanceState, TicketInstance


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EnrollRequest(_Strict):
    learner: str = Field(min_length=1)


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
        "simulated": ws.simulated,
    }
