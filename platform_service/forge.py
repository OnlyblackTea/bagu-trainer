"""Forge 缝（A′）：fork/clone 与 PR 的抽象。

学员驱动本地 git，平台只 gate + 指示（设计 spec §C.5）：provision 把 track-repo 拉到本地
工作区（占位 = 本地 clone；未来 = Gitea fork），diff 用 :mod:`platform_service.review_diff`
跑真本地 git，PR 是占位 URL（未来 = Gitea PR）。平台**不替学员跑 git 写操作**。

**git 进度模型（§C.5，本缝的契约）：** track-repo 里每个阶段（stage）有**自己的分支**，
分支名就是该阶段在 ``manifest.stages`` / ``stages/<name>/`` 里的名字——**不是 ticket 的
``identity.id``**（阶段名与 ticket 身份是两个命名空间，见 Plan 1 Task 0）。

* ``base_ref`` = 该 ticket 所属 **stage 的分支**（阶段起始代码，即"骨架"）；
* ``head_ref`` = 学员的开发 ref（真实模型里是学员自己的 dev 分支）。

**防剧透（§8 / D2）就建立在这条上**：未解锁阶段是独立 ref，clone/fork 只检出已解锁阶段。
若把多个阶段的解法塞进同一个 ref（例如真 Track 样板目前那个单一 ``solutions`` 分支），
一次 clone 就泄露全部答案——那不符合本模型，应由 Track 侧修，而不是让平台去适配。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from .models import TicketInstance


@dataclass(frozen=True)
class Workspace:
    """一个实例的本地工作区。

    ``base_ref`` 是所属 stage 的分支（骨架）；``head_ref`` 是学员的开发 ref。占位 Forge
    没有人真的写代码，故用一条**内容预置的解法 ref** 冒充学员成果。``simulated`` 供 UI 标"模拟"。
    """

    path: Path
    branch: str
    base_ref: str
    head_ref: str
    simulated: bool = True


class WorkspaceRefMissingError(Exception):
    """track-repo 里找不到该阶段的分支，或该 ticket 未声明所属阶段。

    这是**内容/Track 侧的配置错误**，不是临时故障：§C.5 要求一个阶段一个分支。与其让
    ``git diff`` 稍后抛一个对学员毫无解释力的非零退出，不如在 provision 时就点明缺了哪个
    ref、仓库里现有哪些 ref。
    """

    def __init__(
        self, *, repo: Path, ref: str | None, available: Sequence[str], reason: str
    ) -> None:
        self.repo = repo
        self.ref = ref
        self.available = list(available)
        self.reason = reason
        super().__init__(f"{reason}: repo={repo} ref={ref!r} available={sorted(self.available)}")


@runtime_checkable
class ForgeClient(Protocol):
    def workspace_path(self, instance: TicketInstance) -> Path: ...

    def provision(self, instance: TicketInstance) -> Workspace: ...

    def create_pr(self, instance: TicketInstance, *, base: str, head: str) -> str: ...
