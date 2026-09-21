"""真 Forge：Gitea。

* ``provision`` —— 确保 fork 存在 → clone fork 到工作区 → checkout 阶段分支 → 建 ``dev`` 分支 →
  把基线 SHA 记进本地 git config。**它只准备场地**：``dev`` 是空的起点，代码与 push 都由学员
  自己做（§C.5：平台不代跑 git 写操作）。
* ``create_pr`` —— 调 Gitea API，从 ``<fork_owner>:dev`` 到上游的阶段分支开一个真 PR，返回
  ``html_url``。

与桩的分工差别也在这里：桩"假装学员已经做完了"（diff 两端都是内容预置的 ref，``simulated=True``），
真 Forge 不会替学员产出任何东西，所以 ``Workspace.simulated=False``。
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from platform_service.forge import (
    DEV_BRANCH,
    ForgeUnavailableError,
    Workspace,
    git,
    prepare_workspace,
    stage_branch_for,
)
from platform_service.models import TicketInstance

from .gitea import GiteaApi

__all__ = ["GiteaForgeClient"]


class GiteaForgeClient:
    """``ForgeClient`` 缝的真实现（结构化满足，无需继承）。"""

    #: 供 UI / ``/meta`` 判断"这一环是不是真后端"。
    SIMULATED = False

    def __init__(
        self,
        *,
        api: GiteaApi,
        upstream: str,
        fork_owner: str,
        base_dir: Path,
        stage_refs: Mapping[str, str],
    ) -> None:
        self._api = api
        self._upstream = upstream
        self._fork_owner = fork_owner
        self._base_dir = base_dir
        self._stage_refs = dict(stage_refs)

    @property
    def fork_repo_name(self) -> str:
        return self._upstream.split("/", 1)[1]

    def workspace_path(self, instance: TicketInstance) -> Path:
        return self._base_dir / instance.id

    # -- provision -------------------------------------------------------------

    def provision(self, instance: TicketInstance) -> Workspace:
        fork = self._api.ensure_fork(upstream=self._upstream, fork_owner=self._fork_owner)
        clone_url = str(fork.get("clone_url") or "")
        if not clone_url:
            raise ForgeUnavailableError(
                f"gitea fork payload has no clone_url (keys: {sorted(fork)})"
            )
        stage = stage_branch_for(instance.ticket_id, self._stage_refs)
        base_ref = f"origin/{stage}"
        dest = self.workspace_path(instance)
        prepare_workspace(
            dest=dest,
            # 带 token clone，**随后立刻把 remote 改回干净 URL**——别让 token 落进 .git/config。
            clone_url=self._api.authenticated_clone_url(clone_url),
            base_ref=base_ref,
            make_dev_branch=True,
        )
        git(dest, "remote", "set-url", "origin", clone_url)
        return Workspace(
            path=dest,
            branch=DEV_BRANCH,
            base_ref=base_ref,
            head_ref=DEV_BRANCH,
            simulated=self.SIMULATED,
        )

    # -- PR --------------------------------------------------------------------

    def create_pr(self, instance: TicketInstance, *, base: str, head: str) -> str:
        """在**上游仓**开 PR。``head`` 是学员的工作区分支（``dev``），``base`` 是阶段分支。

        远端还没有 ``dev``（学员没 push）时 Gitea 会报错，这里翻成
        :class:`~platform_service.forge.RemoteBranchMissingError` → 路由给 409 + 短码。
        """
        return self._api.create_pull(
            upstream=self._upstream,
            fork_owner=self._fork_owner,
            head_branch=head.removeprefix("origin/"),
            base_branch=base.removeprefix("origin/"),
            title=f"[{instance.ticket_id}] {instance.learner} 的提交",
            body=self._pr_body(instance),
        )

    @staticmethod
    def _pr_body(instance: TicketInstance) -> str:
        """PR 描述只放"这是什么"，**答辩由学员自己写**（工单里有模板，平台不代填）。"""
        return (
            "由 bagu-trainer 自动开单。\n\n"
            f"- ticket: `{instance.ticket_id}`\n"
            f"- instance: `{instance.id}`\n"
            f"- learner: `{instance.learner}`\n\n"
            "> 答辩请写在这里：改了什么、为什么这么改、客观证据（阅卷报告随提交附上）。\n"
        )
