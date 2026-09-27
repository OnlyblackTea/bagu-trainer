"""工作区路由：provision（真 clone 内容仓）/ diff（真本地 git）/ sync（与远端对账）。

平台只 gate + 指示，不替学员跑 git 写操作（设计 spec §C.5）。diff 用
:func:`platform_service.review_diff.generate_diff` 跑真本地 git；provision 幂等。

多 Track 下 forge 是**每个 Track 一个**（工作区与上游仓库都是 Track 自己的），所以每个
入口都先经实例反查出该用哪个 bundle——绝不拿"第一个 Track 的 forge"去操作别人的实例。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from platform_service.forge import diff_base, learner_diff, stage_start
from platform_service.review_diff import preflight_diff_volume

from ..deps import host_visible_path
from ..schemas import WorkspaceRequest, sync_out, workspace_out
from ._resolve import apply_sync, bundle_for_instance, must_instance, probe_sync

router = APIRouter(tags=["workspace"])


def _with_host_path(ws: Any) -> dict[str, Any]:
    """工作区响应 + ``host_path``：容器里那条路径在部署者机器上是什么。

    只在 ``BAGU_HOST_DATA_DIR`` 配了（compose 栈会配）且路径落在数据根下时给出；否则 ``None``。
    为什么值得专门做：容器化之后界面显示的是 ``/var/lib/bagu/ws/...``——那是**容器内**的路径，
    对在自己机器上写代码的人毫无用处（用户就是照这条反馈的）。
    """
    return {**workspace_out(ws), "host_path": host_visible_path(ws.path)}


@router.post("/instances/{instance_id}/provision")
def provision(instance_id: str, request: Request) -> dict[str, Any]:
    instance = must_instance(request, instance_id)
    return _with_host_path(bundle_for_instance(request, instance).forge.provision(instance))


@router.post("/instances/{instance_id}/workspace")
def attach_workspace(
    instance_id: str, request: Request, body: WorkspaceRequest
) -> dict[str, Any]:
    """登记（或撤销）学员自己的工作区目录——**指引式 provision** 的落地。

    平台给出 clone 命令（``clone_hint``），学员在**自己熟悉的地方**拉代码，再把那个路径登记回来；
    此后 ``diff`` 与阅卷都对着它跑（D3：阅卷在学员自己的机器上）。``path=null`` 撤销登记。

    注意：路径必须是**后端这台机器**上能看到的目录。**你不需要这条**——代码写在哪台机器上都行，
    只要你能 push 到内容仓的 ``dev``，后端提交时会自动把这份 checkout 快进到它（见
    :func:`sync_workspace_now`）。登记路径是给"工作区就在后端这台机器上"的人用的：那时连 fetch
    都省了，diff 直接看他正在编辑的文件。
    """
    instance = must_instance(request, instance_id)
    bundle = bundle_for_instance(request, instance)
    updated = bundle.service.attach_workspace(instance_id, body.path)
    return {
        **_with_host_path(bundle.forge.provision(updated)),
        "workspace_path": updated.workspace_path,
    }


@router.post("/instances/{instance_id}/sync")
def sync_workspace_now(instance_id: str, request: Request) -> dict[str, Any]:
    """把手动把工作区与远端 ``dev`` 对一次账（fetch，且**只在无损时**快进）。

    为什么需要这个显式入口：代码完全可以写在**另一台机器**上——后端只要能从 Forge 读到那次 push
    就行。但那之后后端这份 checkout 是旧的，而"拿旧代码去阅卷"会给出一个看起来像"你代码没过"的
    结论。提交时平台会自动做这件事（见 ``POST submit``），这个接口是给"我想先自己看一眼"的人用的，
    也是提交被拦下（本地有未提交改动/两边分叉）之后的手动出路。

    与 :func:`diff` 的分工：那个是只读探针，只报数不动分支；这个才可能在"干净 + 不领先 + 就在
    ``dev`` 上"时快进。
    """
    instance = must_instance(request, instance_id)
    bundle = bundle_for_instance(request, instance)
    ws = bundle.forge.provision(instance)
    sync = apply_sync(ws)
    return {
        **_with_host_path(ws),
        "sync": sync_out(sync),
    }


@router.get("/instances/{instance_id}/diff")
def diff(instance_id: str, request: Request) -> dict[str, Any]:
    """base ↔ **学员的东西**的 diff（真本地 git）。

    比的是 ``ws.diff_head_ref``，不是 ``head_ref``：真 Forge 下它是 ``None`` = **工作区**，也就是
    "包含你还没 commit 的改动"。学员改完文件第一件事就是想看 diff，这时拿 ``dev``（只含已提交）去
    比会显示"没有改动"——那比不给看更糟。桩的 ``diff_head_ref`` 是内容预置的解法 ref（它没有人真的
    写代码），所以 ``simulated`` 照旧为真。

    ``sync`` 是**只读**对账：远端若比你这边新，这里会如实报出来（``remote_only_changes``）——
    否则学员在另一台机器上 push 完，回来看 diff 只会看到"没有改动"，而真实原因是这份工作区还没
    拉下来。

    **比的是"你开始这一阶段时的 dev"**（provision 时钉下的 ``bagu.stageStart``），不是阶段分支
    tip：dev 是跨阶段累积的，拿 tip 比会把上一关写的代码也算成"本阶段的改动"（链式 Track 结构下
    阶段基线不含之前阶段的实现，所以那是必然的）。与基线逐字节相同的文件（本阶段的验收测试）
    会被剔掉——那不是学员写的东西，见 ``review_diff.drop_baseline_files``。
    """
    instance = must_instance(request, instance_id)
    bundle = bundle_for_instance(request, instance)
    ws = bundle.forge.provision(instance)  # 幂等：确保已 clone
    sync = probe_sync(ws)
    text = learner_diff(ws, instance.id)
    volume = preflight_diff_volume(text)
    return {
        "base_ref": ws.base_ref,
        "head_ref": ws.head_ref,
        "diff_head_ref": ws.diff_head_ref,
        # 与 base_ref 分开报：真正拿来比的是"你开始这一阶段时的那份 dev"，界面据此说人话。
        "diff_base": diff_base(ws.path, ws.base_ref, instance.id),
        "diff_base_kind": (
            "stage_start" if stage_start(ws.path, instance.id) else "stage_branch"
        ),
        # head 为 None ⇒ 和工作区比。前端据此把"比较的是什么"说清楚，而不是笼统写 base ↔ head。
        "worktree": ws.diff_head_ref is None,
        "diff": text,
        "total_chars": volume.total_chars,
        "truncated_files": list(volume.truncated_files),
        "simulated": ws.simulated,
        "sync": sync_out(sync),
    }
