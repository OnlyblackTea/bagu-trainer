"""工作区路由：provision（占位 fork=本地 clone）/ diff（真本地 git）/ pr（占位 URL）。

平台只 gate + 指示，不替学员跑 git 写操作（设计 spec §C.5）。diff 用
:func:`platform_service.review_diff.generate_diff` 跑真本地 git；provision 幂等。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from platform_service.models import TicketInstance
from platform_service.review_diff import generate_diff, preflight_diff_volume

from ..schemas import workspace_out

router = APIRouter(tags=["workspace"])


def _must_instance(request: Request, instance_id: str) -> TicketInstance:
    instance = request.app.state.store.get_instance(instance_id)
    if instance is None:
        raise HTTPException(status_code=404, detail="instance_not_found")
    return instance


@router.post("/instances/{instance_id}/provision")
def provision(instance_id: str, request: Request) -> dict[str, Any]:
    instance = _must_instance(request, instance_id)
    return workspace_out(request.app.state.forge.provision(instance))


@router.get("/instances/{instance_id}/diff")
def diff(instance_id: str, request: Request) -> dict[str, Any]:
    instance = _must_instance(request, instance_id)
    ws = request.app.state.forge.provision(instance)  # 幂等：确保已 clone
    text = generate_diff(ws.path, base=ws.base_ref, head=ws.head_ref)
    volume = preflight_diff_volume(text)
    return {
        "base_ref": ws.base_ref,
        "head_ref": ws.head_ref,
        "diff": text,
        "total_chars": volume.total_chars,
        "truncated_files": list(volume.truncated_files),
        "simulated": ws.simulated,
    }


@router.post("/instances/{instance_id}/pr")
def create_pr(instance_id: str, request: Request) -> dict[str, Any]:
    instance = _must_instance(request, instance_id)
    ws = request.app.state.forge.provision(instance)
    url = request.app.state.forge.create_pr(instance, base=ws.base_ref, head=ws.head_ref)
    return {"pr_url": url, "simulated": True}
