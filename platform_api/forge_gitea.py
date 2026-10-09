"""真 Forge：Gitea。

* ``provision`` —— clone **Track 内容仓本身**到工作区 → checkout 阶段分支 → 建 ``dev`` 分支 →
  把基线 SHA 记进本地 git config → 装好 push 凭据。**它只准备场地**：``dev`` 是空的起点，代码与
  push 都由学员自己做（§C.5：平台不代跑 git 写操作）。

**没有 fork，也没有 PR**（L3）：学员把 ``dev`` 直接 push 到这个仓（loader 会把他加成协作者），
后端从同一个仓 clone。早期照搬 GitHub 的 fork→PR 那套，实测是纯负债——见
:mod:`platform_service.forge` 的模块 docstring 里那段账。

与桩的分工差别也在这里：桩"假装学员已经做完了"（diff 两端都是内容预置的 ref，``simulated=True``），
真 Forge 不会替学员产出任何东西，所以 ``Workspace.simulated=False``，而且 **diff 比对的是学员自己
的工作区**（含未提交改动）。

**push 凭据**：学员要 ``git push origin dev``，所以工作区必须能推送。做法是
:meth:`install_push_credentials`——token 写进**仓库外**的 0600 文件，仓库里只留一条指向它的
``credential.helper``。这样：``.git/config`` 里没有秘密；``git add -A`` 也永远不可能把它提交进去
（它根本不在仓库目录里）。平台自己仍然不跑任何 git 写操作。
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from platform_service.forge import (
    DEV_BRANCH,
    ForgeUnavailableError,
    Workspace,
    git,
    is_ancestor,
    prepare_workspace,
    stage_branch_for,
    taken_over_workspace,
)
from platform_service.models import TicketInstance

from .gitea import GiteaApi

__all__ = ["GiteaForgeClient"]

#: 工作区根下的凭据文件名（在每个 ``<instance.id>`` 工作区**之外**）。
CREDENTIALS_FILE = ".git-credentials"


class GiteaForgeClient:
    """``ForgeClient`` 缝的真实现（结构化满足，无需继承）。"""

    #: 供 UI / ``/meta`` 判断"这一环是不是真后端"。
    SIMULATED = False

    def __init__(
        self,
        *,
        api: GiteaApi,
        upstream: str,
        base_dir: Path,
        stage_refs: Mapping[str, str],
    ) -> None:
        self._api = api
        self._upstream = upstream
        self._base_dir = base_dir
        self._stage_refs = dict(stage_refs)

    @property
    def repo_name(self) -> str:
        return self._upstream.split("/", 1)[1]

    def workspace_path(self, instance: TicketInstance) -> Path:
        return self._base_dir / instance.id

    # -- provision -------------------------------------------------------------

    def provision(self, instance: TicketInstance) -> Workspace:
        """clone **Track 内容仓本身**（不再有 fork）。

        学员把 ``dev`` push 到这个仓（loader 会把他加成协作者，见 ``track_loader``）；后端 clone
        它、在里面跑测试。少一个 fork 就少一整套麻烦：fork 是创建那刻的快照（新阶段在旧 fork 里
        永远缺席）、Gitea 会给程序化 fork 打上 ``empty``（分支 API 全 404）、学员第一次 push 会
        抢走 fork 的默认分支。
        """
        repo = self._api.repo(*self._upstream.split("/", 1))
        clone_url = str((repo or {}).get("clone_url") or "")
        if not clone_url:
            raise ForgeUnavailableError(
                f"gitea repo {self._upstream} has no clone_url"
                "（仓不存在？把内容载入 Gitea 的 track_loader 跑过了吗）"
            )
        stage = stage_branch_for(instance.ticket_id, self._stage_refs)
        base_ref = f"origin/{stage}"
        # 学员登记过自己的目录就用**他的**（指引式 provision），否则用平台自己准备的那个。
        dest = taken_over_workspace(instance, self.workspace_path(instance))
        if (dest / ".git").is_dir():
            # 学员自己 clone 的那种：先把远端 ref 拉新，后面的 diff 才看得见最新状态。
            # `fetch` 是只读的（只动 remote-tracking ref，不碰他的工作树与提交）。
            git(dest, "fetch", "-q", "origin", check=False)
        dest = prepare_workspace(
            dest=dest,
            # 带 token clone，**随后立刻把 remote 改回干净 URL**——别让 token 落进 .git/config。
            clone_url=self._api.authenticated_clone_url(clone_url),
            base_ref=base_ref,
            make_dev_branch=True,
            # 按实例分键记基线：同一目录可以被后续阶段的实例登记复用（指引式 provision）。
            instance_id=instance.id,
        )
        git(dest, "remote", "set-url", "origin", clone_url)
        self.install_push_credentials(dest, clone_url)
        self.ensure_git_identity(dest, instance.learner)
        self.disable_commit_signing(dest)
        return Workspace(
            path=dest,
            branch=DEV_BRANCH,
            base_ref=base_ref,
            head_ref=DEV_BRANCH,
            simulated=self.SIMULATED,
            # None = 与**工作区**比（含未提交改动）。学员改完还没 commit 就想看 diff 是最正常的
            # 动作；拿 dev（只含已提交）去比会显示"没有改动"。
            diff_head_ref=None,
            push_hint=self.push_hint(),
            clone_hint=self.clone_hint(clone_url, stage),
            merge_hint=self.merge_hint(dest, base_ref),
        )

    def merge_hint(self, dest: Path, base_ref: str) -> str | None:
        """本阶段基线还没合进 ``dev`` 时给出的那一句（见 ``Workspace.merge_hint``）。

        为什么值得出现在界面上：链式 Track 结构下，**本阶段的验收测试住在 ``origin/<stage>``
        上**（阶段分支 = 上一阶段分支 + 本阶段测试，且不含任何参考解）。不把它合进来，工作区里
        就压根没有那批判据——``mvn -Dgroups=stage0N`` 一条用例都跑不到，红在一个跟代码无关的
        地方。相对分岔点这条合并**只加不删**，所以它不会和学员自己的实现打架，可以照抄。
        """
        if is_ancestor(dest, base_ref):
            return None
        return (
            f"git merge {base_ref}   "
            "# 本阶段的验收测试在这个分支上；相对分岔点只加不删，不会和你写的实现打架"
        )

    def clone_hint(self, clone_url: str, stage: str) -> str:
        """让学员**在自己的目录里**拉取的命令（指引式 provision）。

        用的是干净 clone URL（**不含 token**）：他自己的凭据他自己给——平台不去碰他机器上的 git
        凭据配置。分支形状与平台自己那份一致（从阶段分支开出 ``dev``），所以后续 diff 的语义
        完全相同。
        """
        return "\n".join(
            [
                f"git clone {clone_url} ~/my-bagu-{stage}",
                f"cd ~/my-bagu-{stage}",
                f"git checkout -b {DEV_BRANCH} origin/{stage}",
            ]
        )

    def ensure_git_identity(self, dest: Path, learner: str) -> bool:
        """工作区解析不到 git 身份时，就地给一个——否则学员的**第一次 commit 必然失败**。

        "Please tell me who you are" 是新手最容易被卡住的一步，而它跟本题考的东西毫无关系。这里
        只在*真的*没有身份时补（``git config user.email`` 会把 global/system 一并解析进来），所以有
        自己配置的机器不受影响。写入的是**仓库本地**配置，不碰学员的全局设置。
        """
        if git(dest, "config", "user.email", check=False).stdout.strip():
            return False
        git(dest, "config", "user.email", f"{learner}@localhost")
        git(dest, "config", "user.name", learner)
        return True

    def disable_commit_signing(self, dest: Path) -> None:
        """在平台这份工作区里关掉提交签名——宿主的签名偏好不该渗进阅卷工作区。

        为什么必须做：宿主（或学员）全局开着 ``commit.gpgsign`` 时，平台这份 checkout 会**继承**它，
        于是任何"要造提交"的动作都会去弹 pinentry。无头环境里那等于必然失败：

            error: gpg failed to sign the data: … gpg: signing failed: Timeout
            fatal: failed to write commit object        （退出码 128）

        而 `merge_hint` 让学员做的事正好是造一个**合并提交**（`git merge origin/stage-0N`）——
        真踩过：本机全局开着签名，同一台机器上跑测试时那条 merge 就死在 pinentry 超时上，红在一个
        跟代码毫无关系的地方。写的是**仓库本地**配置，不碰学员的全局设置。
        """
        git(dest, "config", "commit.gpgsign", "false", check=False)
        git(dest, "config", "tag.gpgsign", "false", check=False)

    def push_hint(self) -> str:
        """学员推代码要跑的命令。**不含凭据**——凭据由 credential.helper 提供。"""
        return (
            'git add -A && git commit -m "stage 完成" && git push -u origin ' + DEV_BRANCH
        )

    def install_push_credentials(self, dest: Path, clone_url: str) -> Path | None:
        """让 ``git push`` 在这个工作区里开箱可用，且秘密不落在仓库内。

        公开而不是私有：它是**一格可单独验证的行为**（"凭据写对了、位置对了、仓库里没有秘密"），
        单测直接调它，不必为了验它去架一个真 http 远端。

        ``ssh``/``scp`` 形态返回 ``None``：那种情况该用学员自己的 ssh key，平台不掺和。
        """
        line = self._api.credential_line(clone_url)
        if line is None:
            return None
        creds = self._base_dir / CREDENTIALS_FILE
        creds.parent.mkdir(parents=True, exist_ok=True)
        creds.write_text(line + "\n", encoding="utf-8")
        try:
            creds.chmod(0o600)
        except OSError:  # pragma: no cover - 某些文件系统不支持（例如部分 Windows 挂载）
            pass
        # 先清掉继承来的 helper（系统/全局可能挂着凭据管理器，会先弹窗或先失败），再只挂我们这一个。
        # 第一条空值在 git 里表示"清空之前累积的列表"。
        git(dest, "config", "--unset-all", "credential.helper", check=False)
        git(dest, "config", "--add", "credential.helper", "")
        git(dest, "config", "--add", "credential.helper", f"store --file={creds.as_posix()}")
        return creds
