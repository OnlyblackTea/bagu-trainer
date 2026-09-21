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

import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from .models import TicketInstance

#: 学员干活的开发分支名（§C.5：``head_ref`` = 学员的开发 ref）。
DEV_BRANCH = "dev"


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


# --- Forge 侧的错误（真 Gitea 才会抛；桩永远不会）--------------------------------


class ForgeUnavailableError(RuntimeError):
    """Forge 不可达 / 认证失败 / API 报错。

    这是**平台/基础设施**问题（§C.8），路由应给 5xx + 短码——不能让它看起来像"学员的代码没过"。
    """


class RemoteBranchMissingError(RuntimeError):
    """远端还没有学员的 dev 分支，所以开不了 PR。

    这是**预期内的普通状况**（学员还没 push），所以给 409 + 短码告诉他"先 push"，
    而不是 500：它既不是故障，也不是学员做错了什么。
    """

    def __init__(self, *, repo: str, branch: str) -> None:
        self.repo = repo
        self.branch = branch
        super().__init__(f"{repo} has no remote branch {branch!r} — push it before opening a PR")


# --- 本地 git 工作区（两个 Forge 共用：只有"仓库从哪来"和"PR 怎么建"不同）-----------


def stage_branch_for(ticket_id: str, stage_refs: Mapping[str, str]) -> str:
    """ticket 身份 → 它所属的 **stage 分支名**。

    §C.5：分支按**阶段**命名，不是 ticket id——两者只是碰巧在 demo 里同过名。
    """
    stage = stage_refs.get(ticket_id)
    if stage is None:
        raise WorkspaceRefMissingError(
            repo=Path("."),
            ref=None,
            available=sorted(set(stage_refs.values())),
            reason=f"track declares no stage branch for ticket {ticket_id!r}",
        )
    return stage


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """跑一条 git 命令（``check=False`` 时由调用方看 returncode）。

    ``check=True`` 且非零退出时抛 :class:`subprocess.CalledProcessError`——路由层已把它映成
    ``git_command_failed``，所以这里不吞错。
    """
    proc = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if check and proc.returncode != 0:
        raise subprocess.CalledProcessError(
            proc.returncode, ["git", *args], proc.stdout, proc.stderr
        )
    return proc


def available_refs(repo: Path) -> list[str]:
    return sorted(
        git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads", "refs/remotes")
        .stdout.split()
    )


def ref_exists(repo: Path, ref: str) -> bool:
    return git(repo, "rev-parse", "--verify", "--quiet", ref, check=False).returncode == 0


def must_have_ref(repo: Path, ref: str, *, reason: str) -> None:
    """ref 不存在就抛 :class:`WorkspaceRefMissingError`（带"仓库里现有哪些 ref"）。

    与其让 ``git diff`` 稍后抛一个对学员毫无解释力的非零退出，不如在 provision 阶段点明。
    """
    if not ref_exists(repo, ref):
        raise WorkspaceRefMissingError(
            repo=repo, ref=ref, available=available_refs(repo), reason=reason
        )


def prepare_workspace(
    *, dest: Path, clone_url: str, base_ref: str, make_dev_branch: bool
) -> None:
    """把工作区准备到"学员可以开始写"的状态（两个 Forge 共用，幂等）。

    做三件事：clone（已存在就跳过）、校验阶段分支存在、把**基线 ref 解析成 SHA 记进本地
    git config**（``bagu.baseRef``）——阅卷据此比出"学员改了什么"，**已提交**的改动也算数；
    记 ``HEAD`` 会随学员的提交一起前移，等于永远比不出东西（这个坑测试里踩过）。
    """
    if not (dest / ".git").is_dir():
        dest.parent.mkdir(parents=True, exist_ok=True)
        git(dest.parent, "clone", "-q", clone_url, str(dest))
    must_have_ref(
        dest,
        base_ref,
        reason="track-repo lacks the stage branch this ticket is diffed against; "
        "§C.5 requires one branch per stage",
    )
    if make_dev_branch and not ref_exists(dest, DEV_BRANCH):
        git(dest, "checkout", "-q", "-b", DEV_BRANCH, base_ref)
    if not git(dest, "config", "--get", "bagu.baseRef", check=False).stdout.strip():
        git(dest, "config", "bagu.baseRef", git(dest, "rev-parse", base_ref).stdout.strip())
