"""工作区路由：provision（占位 fork=本地 clone）/ diff（真本地 git）/ pr（占位 URL）。

平台只 gate + 指示，不替学员跑 git 写操作（设计 spec §C.5）。diff 用
:func:`platform_service.review_diff.generate_diff` 跑真本地 git；provision 幂等。

多 Track 下 forge 是**每个 Track 一个**（工作区与上游仓库都是 Track 自己的），所以每个
入口都先经实例反查出该用哪个 bundle——绝不拿"第一个 Track 的 forge"去操作别人的实例。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from platform_service.review_diff import generate_diff, preflight_diff_volume

from ..schemas import workspace_out
from ._resolve import bundle_for_instance, must_instance

router = APIRouter(tags=["workspace"])


@router.post("/instances/{instance_id}/provision")
def provision(instance_id: str, request: Request) -> dict[str, Any]:
    instance = must_instance(request, instance_id)
    return workspace_out(bundle_for_instance(request, instance).forge.provision(instance))


@router.get("/instances/{instance_id}/diff")
def diff(instance_id: str, request: Request) -> dict[str, Any]:
    """base ↔ **学员的东西**的 diff（真本地 git）。

    比的是 ``ws.diff_head_ref``，不是 ``head_ref``：真 Forge 下它是 ``None`` = **工作区**，也就是
    "包含你还没 commit 的改动"。学员改完文件第一件事就是想看 diff，这时拿 ``dev``（只含已提交）去
    比会显示"没有改动"——那比不给看更糟。桩的 ``diff_head_ref`` 是内容预置的解法 ref（它没有人真的
    写代码），所以 ``simulated`` 照旧为真。
    """
    instance = must_instance(request, instance_id)
    ws = bundle_for_instance(request, instance).forge.provision(instance)  # 幂等：确保已 clone
    text = generate_diff(ws.path, base=ws.base_ref, head=ws.diff_head_ref)
    volume = preflight_diff_volume(text)
    return {
        "base_ref": ws.base_ref,
        "head_ref": ws.head_ref,
        "diff_head_ref": ws.diff_head_ref,
        # head 为 None ⇒ 和工作区比。前端据此把"比较的是什么"说清楚，而不是笼统写 base ↔ head。
        "worktree": ws.diff_head_ref is None,
        "diff": text,
        "total_chars": volume.total_chars,
        "truncated_files": list(volume.truncated_files),
        "simulated": ws.simulated,
    }


@router.post("/instances/{instance_id}/pr")
def create_pr(instance_id: str, request: Request) -> dict[str, Any]:
    instance = must_instance(request, instance_id)
    forge = bundle_for_instance(request, instance).forge
    ws = forge.provision(instance)
    url = forge.create_pr(instance, base=ws.base_ref, head=ws.head_ref)
    # simulated 如实来自 Forge 本身：真 Gitea 下这里就是 false，不能再写死 true。
    return {"pr_url": url, "simulated": ws.simulated}
