"""路由共用的解析：把 URL 上的 id 变成"该用哪个 Track 的那套零件"。

多 Track 之后每个请求都要先回答一个问题——**这条路径落在哪个 Track 上**。答案只有一个来源：
实例上的 ``track`` 戳（实例路由），或 URL 里显式的 ``track_id``（内容路由）。绝不靠"猜唯一那个"
或"看 ticket id 像哪个 Track"。

这里同时是 §C.8 短码的**唯一**来源：``track_not_found`` / ``instance_not_found`` /
``ticket_not_found`` 都在这一层抛，路由体里不再各写一份 404。
"""

from __future__ import annotations

from fastapi import HTTPException, Request

from platform_service.forge import Workspace, WorkspaceSync, sync_workspace
from platform_service.models import TicketInstance

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
