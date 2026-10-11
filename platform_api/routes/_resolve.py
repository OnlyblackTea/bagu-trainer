"""路由共用的解析：把 URL 上的 id 变成"该用哪个 Track 的那套零件"。

多 Track 之后每个请求都要先回答一个问题——**这条路径落在哪个 Track 上**。答案只有一个来源：
实例上的 ``track`` 戳（实例路由），或 URL 里显式的 ``track_id``（内容路由）。绝不靠"猜唯一那个"
或"看 ticket id 像哪个 Track"。

这里同时是 §C.8 短码的**唯一**来源：``track_not_found`` / ``instance_not_found`` /
``ticket_not_found`` 都在这一层抛，路由体里不再各写一份 404。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException, Request

from platform_service.forge import (
    Workspace,
    WorkspaceSync,
    set_stage_start,
    sync_workspace,
)
from platform_service.models import InstanceState, TicketInstance

from ..deps import TrackBundle


def bundle_for_track(request: Request, track_id: str) -> TrackBundle:
    bundle = request.app.state.tracks.get(track_id)
    if bundle is None:
        raise HTTPException(status_code=404, detail="track_not_found")
    return bundle


def must_instance(request: Request, instance_id: str) -> TicketInstance:
    instance = request.app.state.store.get_instance(instance_id)
    if instance is None:
        raise HTTPException(status_code=404, detail="instance_not_found")
    return instance


def bundle_for_instance(request: Request, instance: TicketInstance) -> TrackBundle:
    """实例自带的 ``track`` 就是权威答案。

    若它指向一个已经不在部署里的 Track（内容换过、目录撤了），这是**部署事实**而不是学员
    的错，用 409 ``track_not_deployed`` 说清楚；回落到"随便挑一个 Track"会让平台拿着别人的
    forge 与 stage refs 去操作，比报错糟得多。
    """
    bundle = request.app.state.tracks.get(instance.track)
    if bundle is None:
        raise HTTPException(status_code=409, detail="track_not_deployed")
    return bundle


def probe_sync(ws: Workspace) -> WorkspaceSync | None:
    """只读对账：fetch + 报数，**绝不动**学员的分支（``GET diff`` 用）。

    桩工作区（``simulated``）没有远端，返回 ``None``——界面据此不显示同步那一块，而不是显示一个
    "已是最新"的假状态。
    """
    if ws.simulated:
        return None
    return sync_workspace(ws.path, branch=ws.branch, apply=False)


def apply_sync(ws: Workspace) -> WorkspaceSync | None:
    """对账并**在明确无损时**快进（``POST /sync``、``POST submit`` 用）。"""
    if ws.simulated:
        return None
    return sync_workspace(ws.path, branch=ws.branch, apply=True)


def previous_stage_revision(request: Request, instance: TicketInstance) -> str | None:
    """他**上一关交卷时**那条 dev 的 revision（平台自己的记录里查得到）。

    这就是"进入这一关时的 dev"最可靠的证据：与什么时候 provision 无关，重做、换机器、多个 clone
    都不影响。取法：本 ticket 的前置阶段 → 他那些**已通过**的实例 → 里面最新一次提交的报告 revision
    （``provenance.host_env.revision``）。
    """
    state = request.app.state
    bundle = state.tracks.get(instance.track)
    if bundle is None:
        return None
    ticket = bundle.tickets.get(instance.ticket_id)
    if ticket is None:
        return None
    prereqs = set(ticket.progression.prerequisites)
    if not prereqs:
        return None  # 第一关：没有"上一关"可用，交给 provision 时刻的 HEAD 兜底
    best: tuple[str, str] | None = None  # (submitted_at, revision)
    for other in state.store.list_instances(learner=instance.learner, track=instance.track):
        if other.ticket_id not in prereqs or other.state is not InstanceState.PASSED:
            continue
        for submission in state.store.list_submissions(other.id):
            report = state.store.get_report(submission.id)
            if report is None:
                continue
            revision = str(report.provenance.host_env.get("revision") or "")
            if not revision:
                continue
            if best is None or submission.created_at > best[0]:
                best = (submission.created_at, revision)
    return best[1] if best else None


def seed_stage_anchor(request: Request, instance: TicketInstance, workspace: Path) -> bool:
    """把上一关交卷时的 revision 写成这次尝试的"开始线"，返回是否真的改了。

    为什么需要：provision 记 ``HEAD`` 当开始线，只在"先从平台拿工作区、再动手"的顺序下才准确。
    学员（尤其是自部署的自己）常态是**先在别处把这一关写完、push，才来点开始**——那一刻的 ``HEAD``
    已经包含本关的活，于是"这一关你改了什么"退化成"相对阶段基线的全部改动"，把前几关的成果也带
    进来（真被问过："diff 5000+ 字符正常吗"）。用上一关的 revision 当开始线，diff 就精确到本关。
    """
    revision = previous_stage_revision(request, instance)
    if not revision:
        return False
    return set_stage_start(workspace, revision, instance_id=instance.id)
