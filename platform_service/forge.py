"""Forge 缝（A′）：git 远端与工作区的抽象。

学员驱动本地 git，平台只 gate + 指示（设计 spec §C.5）：provision 把 track-repo 拉到本地
工作区（占位 = 本地 clone；真 = Gitea），diff 用 :mod:`platform_service.review_diff` 跑真本地 git。
平台**不替学员跑 git 写操作**。

**这里没有 fork，也没有 PR。** 早期设计照搬 GitHub 的 ``fork → 开发 → 提 PR → 过关``，Forge 缝上
因此挂过一个 ``create_pr``。实测下来 fork 是纯负债：它是**创建那一刻的快照**（Track 后来长出新阶段，
旧 fork 里永远缺席 ⇒ provision 报 ``workspace_ref_missing``）；Gitea 把程序化建出的 fork 标成
``empty``（它的分支 API 对任何分支都回 404）；学员第一次 push 会抢走 fork 的默认分支（此后删不掉）；
再加上"自己开的 PR 自己合不了"的权限摩擦。而**阅卷与判词一个都不需要 PR**——判词的输入是报告 +
评审 + 判定面（见 :meth:`~platform_service.service.PlatformService.decide`）。

所以现在必需的东西只有一件：**一个后端读得到的 git 远端**。学员把 ``dev`` 直接 push 上去，
后端 clone 它、在里面跑测试、把 ``revision`` 记进报告。

**git 进度模型（§C.5，本缝的契约）：** track-repo 里每个阶段（stage）有**自己的分支**，
分支名就是该阶段在 ``manifest.stages`` / ``stages/<name>/`` 里的名字——**不是 ticket 的
``identity.id``**（阶段名与 ticket 身份是两个命名空间，见 Plan 1 Task 0）。

* ``base_ref`` = 该 ticket 所属 **stage 的分支**（阶段起始代码，即"骨架"）；
* ``head_ref`` = 学员的开发 ref（真实模型里是 ``dev``；推进下一关时把下一阶段的分支并进 ``dev``）。

**防剧透（§8 / D2）就建立在这条上**：未解锁阶段是独立 ref，clone 只检出已解锁阶段。
若把多个阶段的解法塞进同一个 ref（例如真 Track 样板目前那个单一 ``solutions`` 分支），
一次 clone 就泄露全部答案——那不符合本模型，应由 Track 侧修，而不是让平台去适配。
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from .models import TicketInstance
from .review_diff import drop_baseline_files, generate_diff

#: 学员干活的开发分支名（§C.5：``head_ref`` = 学员的开发 ref）。
DEV_BRANCH = "dev"

#: 本地 git config 里记的两个基准（用途不同，见 :func:`remember_baseline`）：
#: 内容作者的版本（阶段分支）与"学员开始这一阶段时的 dev"。
BASELINE_CONFIG = "bagu.baseRef"
STAGE_START_CONFIG = "bagu.stageStart"


@dataclass(frozen=True)
class Workspace:
    """一个实例的本地工作区。

    ``base_ref`` 是所属 stage 的分支（骨架）；``head_ref`` 是学员的开发 ref（真 Forge 下就是
    ``dev``，学员 push 的就是它）。``simulated`` 供 UI 标"模拟"。

    ``diff_head_ref`` 是**预览/评审时拿什么和 base 比**，与 ``head_ref`` 分开是因为两者的正确
    答案不同：

    * 真 Forge：``None`` = **工作区**（含未提交改动）。学员刚改完还没 commit 就想看 diff 是最
      正常的动作；拿 ``dev``（只含已提交）去比会显示"没有改动"，把人吓一跳。
    * 桩：内容预置的解法 ref（``origin/<stage>-sol``）。桩没有人真的写代码，没有"工作区改动"
      可看——那正是它 ``simulated=True`` 的意思。
    """

    path: Path
    branch: str
    base_ref: str
    head_ref: str
    simulated: bool = True
    diff_head_ref: str | None = None
    #: 学员要把改动推上去时跑的 git 命令（真 Forge 才有意义）。**不含任何凭据**：平台的 token
    #: 放在仓库外的凭据文件里，由 repo-local credential.helper 指过去。
    push_hint: str | None = None
    #: 学员想**在自己的目录里**拉取时照抄的命令（指引式 provision）。``None`` = 这个后端没有可推的
    #: 远端（桩），给了会把人骗去跑一条注定失败的命令。
    clone_hint: str | None = None
    #: **还没把本阶段基线合进 dev** 时才给的一句命令（``git merge origin/<stage>``）。
    #: 链式 Track 结构下这条合并只加不删（相对分岔点只有"新增本阶段验收测试"），所以它是可照抄的；
    #: 不做它的症状很难自查——判据文件根本不在工作区里，``mvn -Dgroups=`` 一条用例都跑不到。
    #: 已经合过（或本来就从该分支开出来的 dev）就是 ``None``：不给人一条多余的指令。
    merge_hint: str | None = None


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


# --- Forge 侧的错误（真 Gitea 才会抛；桩永远不会）--------------------------------


class ForgeUnavailableError(RuntimeError):
    """Forge 不可达 / 认证失败 / API 报错。

    这是**平台/基础设施**问题（§C.8），路由应给 5xx + 短码——不能让它看起来像"学员的代码没过"。
    """


# --- 本地 git 工作区（两个 Forge 共用：只有"仓库从哪来"不同）-------------------------


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


def _absolute_clone_url(clone_url: str) -> str:
    """本地 clone 源必须转成**绝对**路径；URL / scp 形式原样返回。

    起因是一个真实的 500：``git clone`` 的 ``cwd`` 被设成 ``dest.parent``，于是相对的源路径会
    相对**那个**目录解析——``git clone scratch/_e2e/demo scratch/_e2e/ws/ti-1`` 从
    ``scratch/_e2e/ws`` 出发找的是 ``scratch/_e2e/ws/scratch/_e2e/demo``，必然 128。

    这个缺陷长期藏着，是因为它只在**第一次** clone 时发作：工作区一旦留在磁盘上就永远走
    "已存在 ⇒ 跳过 clone"那条路（幂等）。任何让工作区换目录的改动（例如给每个 Track 分一层
    base_dir）都会把它翻出来。
    """
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", clone_url) or re.match(
        r"^[^/\\]+@[^/\\]+:", clone_url
    ):
        return clone_url
    return str(Path(clone_url).resolve())


def remember_baseline(repo: Path, base_ref: str, *, instance_id: str | None = None) -> str:
    """把**基线 ref 解析成 SHA** 记进本地 git config（``bagu.baseRef``），返回它。

    为什么要记 SHA、不能记 ``HEAD``：``HEAD`` 会随学员的提交一起前移，等于永远比不出东西
    （这个坑测试里踩过）。幂等：同一个实例重复 provision 不覆盖。

    记两个基准，用途不同（见 :func:`judged_base` / :func:`diff_base`）：

    * ``bagu.baseRef`` = **内容作者的版本**（那个阶段分支）；
    * ``bagu.stageStart`` = **开始这一阶段时学员那条 dev 所在的提交**。

    **两个都按实例分键**（``bagu.baseRef.<instance.id>``）：工作区可以被多个实例共用——"指引式"
    provision 就是学员在自己那个目录里做**每一关**（登记一次、一路用下去）。只用无实例维度的键，
    第二关就会读到第一关钉下的"开始线"，于是 diff 里又混进上一关的代码——正是这套基准要消灭的东西。
    无实例维度的键仍然写（= 最近一次 provision），给不认识实例 id 的读者与老工作区兜底。
    """
    scoped_base = _scoped(BASELINE_CONFIG, instance_id)
    recorded = _config_get(repo, scoped_base) or _config_get(repo, BASELINE_CONFIG)
    resolved = git(repo, "rev-parse", base_ref).stdout.strip()
    # **Track 被重写过**（阶段分支换了一条 lineage）时，旧记录已经不是 HEAD 的祖先——它在另一条线上。
    # 继续拿它当判定面基准，会把"合并新基线带进来的判据文件"（`src/test/…`）误判成"改了判定面"，
    # 而那是一次干净的合并。所以认出来就**改用当前分支**，并把开始线重设成现在。
    # （实测来源：把六条兄弟分支改造成一条链之后，旧工作区里钉的正是旧 lineage 的 SHA。）
    if recorded and not is_ancestor(repo, recorded):
        recorded = None
    if recorded is None:
        recorded = resolved
        # 开始线**只在第一次 provision 时**钉：重复 provision 不能把它往后推。
        head = git(repo, "rev-parse", "HEAD", check=False).stdout.strip()
        start = head or recorded
        git(repo, "config", _scoped(STAGE_START_CONFIG, instance_id), start)
        git(repo, "config", STAGE_START_CONFIG, start)
    git(repo, "config", scoped_base, recorded)
    git(repo, "config", BASELINE_CONFIG, recorded)
    return recorded


def _config_get(repo: Path, key: str) -> str | None:
    value = git(repo, "config", "--get", key, check=False).stdout.strip()
    return value or None


def _scoped(key: str, instance_id: str | None) -> str:
    """按实例分键：同一个工作区被多个实例共用时（指引式 provision），各自的开始线不能串。"""
    return key if not instance_id else f"{key}.{instance_id}"


def merge_base(repo: Path, ref: str) -> str | None:
    """``ref`` 与 ``HEAD`` 的分岔点（没有共同祖先 ⇒ ``None``）。

    判定面比对要的正是这个点：把"内容作者的版本"和"学员这一侧的改动"放在可比的基准上。
    直接用分支 tip 会在"学员还没把基线合进来"时把**基线自带的文件**报成"他删了验收测试"。
    """
    out = git(repo, "merge-base", ref, "HEAD", check=False).stdout.strip()
    return out or None


def stage_start(repo: Path, instance_id: str | None = None) -> str | None:
    """provision 时记下的"开始这一阶段时你自己那条 dev"（见 :func:`remember_baseline`）。"""
    return _config_get(repo, _scoped(STAGE_START_CONFIG, instance_id)) or _config_get(
        repo, STAGE_START_CONFIG
    )


def baseline_sha(repo: Path, instance_id: str | None = None) -> str | None:
    """provision 时记下的**内容作者的版本**（阶段分支当时那个 SHA）。"""
    return _config_get(repo, _scoped(BASELINE_CONFIG, instance_id)) or _config_get(
        repo, BASELINE_CONFIG
    )


def judged_base(repo: Path, instance_id: str | None = None) -> str | None:
    """**判定面**比对用的基准：内容作者的版本与 HEAD 的分岔点。"""
    recorded = baseline_sha(repo, instance_id)
    if not recorded:
        return None
    return merge_base(repo, recorded) or recorded


def diff_base(repo: Path, base_ref: str, instance_id: str | None = None) -> str:
    """**"这一阶段你改了什么"**该拿哪个提交当基准（diff / 评审 / 越界提醒都用它）。

    优先 ``bagu.stageStart``（开始这一阶段时你自己那条 dev）；老工作区没有这个记录时退回
    ``merge-base(base_ref, HEAD)``（与本阶段基线的分岔点），最后才是 ``base_ref`` 本身。
    绝不用 ``HEAD``：那会永远比不出东西。
    """
    start = stage_start(repo, instance_id)
    if start:
        return start
    return merge_base(repo, base_ref) or base_ref


def learner_diff(ws: Workspace, instance_id: str | None = None) -> str:
    """界面与评审里那个 diff：**"这一阶段你改了什么"**（唯一口径，别再各处自己拼）。

    两件事合起来才构成这个口径：

    * 基准是 :func:`diff_base`（开始这一阶段时他自己那条 dev），不是阶段分支 tip——dev 是跨
      阶段累积的，拿 tip 比会把上一关写的代码也算进来；
    * 与阶段基线**逐字节相同**的文件整块剔掉：那是基线自带的东西（本阶段的验收测试），不是学员
      写的。``src/test/`` 属于判定面，留在 diff 里会被判成"改了判据"。

    只有"和工作区比"（``diff_head_ref is None``）时才剔：桩的 diff 另一端是内容预置的解法 ref，
    工作区里躺的仍是骨架，按内容剔会把整份 diff 剔空。
    """
    base = diff_base(ws.path, ws.base_ref, instance_id)
    text = generate_diff(ws.path, base=base, head=ws.diff_head_ref)
    if ws.diff_head_ref is not None:
        return text
    return drop_baseline_files(ws.path, text, baseline=ws.base_ref)


def is_ancestor(repo: Path, ref: str) -> bool:
    """``ref`` 是不是 ``HEAD`` 的祖先——即"本阶段的基线已经合进来了"。

    用它决定要不要给 ``merge_hint``：已经合过（或 dev 本来就从阶段分支开出来的）就不给，
    免得指一条多余的命令。也用于把"判据文件为什么不在工作区里"说清楚。
    """
    return git(repo, "merge-base", "--is-ancestor", ref, "HEAD", check=False).returncode == 0


def taken_over_workspace(instance: TicketInstance, fallback: Path) -> Path:
    """这次操作该用哪个工作区：学员登记过就用**他的**，否则用平台自己准备的那个。

    指引式 provision 的核心：平台给出 clone 命令，学员在**自己熟悉的地方**拉代码、把路径登记回来，
    此后 diff 与阅卷都对着那个目录跑（D3：阅卷在学员自己的机器上）。没登记就是平台自己的 clone——
    两种都合法，只是"在哪儿"必须说清楚（diff 响应里会带上）。
    """
    return Path(instance.workspace_path) if instance.workspace_path else fallback


def prepare_workspace(
    *,
    dest: Path,
    clone_url: str,
    base_ref: str,
    make_dev_branch: bool,
    instance_id: str | None = None,
) -> Path:
    """把工作区准备到"学员可以开始写"的状态（两个 Forge 共用，幂等）。

    做四件事：clone（已存在就跳过）、校验阶段分支存在、把 ``dev`` 准备好、把基线记进 git config
    （见 :func:`remember_baseline`；``instance_id`` 会让它**按实例分键**——同一个目录被多个实例
    共用时，各自的"开始线"不能串）。

    ``dev`` 的优先级是**本地已有 > 远端已有 > 从阶段分支新建**。第二条是必须的：学员的 ``dev``
    是跨阶段累积的那条线（§C.5：通过一关后把下一阶段的分支合入 ``dev``）。若每次都
    从阶段分支重建，学员第二次 ``provision`` 就会得到一个**落后于自己远端**的本地分支，``git push``
    直接被拒（"tip of your current branch is behind its remote counterpart"）——而那跟他这题写得
    对不对毫无关系。本地已有就 checkout 它（重复 provision 不该把学员切到别处去）。

    返回**绝对**路径（调用方必须拿它去建 :class:`Workspace`）：工作区的后续 git 操作都把
    ``cwd`` 设成这个路径，一个相对路径会在进程 cwd 换个地方之后指到别处去。
    """
    dest = dest.resolve()
    if not (dest / ".git").is_dir():
        # 先落成绝对路径再 clone：下面把 cwd 换成了 dest.parent（见 _absolute_clone_url）。
        dest.parent.mkdir(parents=True, exist_ok=True)
        git(dest.parent, "clone", "-q", _absolute_clone_url(clone_url), str(dest))
    must_have_ref(
        dest,
        base_ref,
        reason="track-repo lacks the stage branch this ticket is diffed against; "
        "§C.5 requires one branch per stage",
    )
    if make_dev_branch:
        remote_dev = f"origin/{DEV_BRANCH}"
        if ref_exists(dest, DEV_BRANCH):
            git(dest, "checkout", "-q", DEV_BRANCH)
        elif ref_exists(dest, remote_dev):
            git(dest, "checkout", "-q", "-b", DEV_BRANCH, remote_dev)
        else:
            git(dest, "checkout", "-q", "-b", DEV_BRANCH, base_ref)
    remember_baseline(dest, base_ref, instance_id=instance_id)
    return dest


# --- 工作区与远端的对账（"代码写在别的机器上"这条路的支撑）-------------------------

#: 默认远端名。学员自己 clone 的那份也是 ``origin``（clone 的原点），所以不需要配置。
REMOTE_NAME = "origin"


@dataclass(frozen=True)
class WorkspaceSync:
    """工作区与它远端 ``dev`` 的同步状态。

    **为什么需要它**：代码完全可以写在**另一台机器**上——后端只要能从 Forge 读到那次 push 就行。
    但那时后端这份 checkout 是落后的，而"拿一份落后的代码去阅卷"是最糟的一种失败：结论看起来
    像学员的代码没过，其实平台压根没看他新写的代码。

    三条纪律（``sync_workspace`` 的实现就是它们）：

    * ``fetch`` 只动 remote-tracking ref——永远安全，任何情况下都做；
    * **只有本地工作树干净、当前分支就是 ``branch``、且本地不领先远端时，才快进**：那正是
      "他在别处写、这边没人动过"这一种情况，快进无损；
    * 其余情况一律**不动他的分支**，如实报出 ``dirty`` / ``local_ahead`` / ``remote_ahead``，
      让界面说清"这次按哪份代码阅卷"。
    """

    #: fetch 是否成功。失败不改任何判断，只是"对不了账"——界面据此别说"已是最新"。
    fetched: bool
    #: 工作树有未提交（含未跟踪）改动 ⇒ 学员是在**这台机器**上写代码，以工作区为准。
    dirty: bool
    #: 本地 ``branch`` 领先远端的提交数。
    local_ahead: int
    #: 远端 ``branch`` 领先本地的提交数。>0 且没快进 ⇒ 界面必须提醒。
    remote_ahead: int
    #: 是否真的把本地分支快进到了远端（``remote_ahead`` 随之归零）。
    fast_forwarded: bool
    #: 对完账后 ``HEAD`` 的 SHA——"阅卷/预览对着哪一版"，可审计。
    revision: str | None
    #: 远端起有没有 ``branch``。没有 ⇒ 学员还没 push 过，PR 也开不了。
    remote_branch: bool
    #: **两边都有对方没有的提交**（``local_ahead > 0 且 remote_ahead > 0``）。这种状态快进不了，
    #: 界面**不许**再劝人点「从远端同步」——那条路按不动（真踩过）。
    diverged: bool = False
    #: 一句话交代这次做了什么（面向学员，不是日志）。
    note: str = ""

    @property
    def remote_only_changes(self) -> bool:
        """远端有新东西、而这次没同步进工作区 ⇒ 即将被阅卷/预览的是旧代码。"""
        return self.remote_ahead > 0


def _rev_count(repo: Path, left: str, right: str) -> tuple[int, int] | None:
    """``git rev-list --left-right --count left...right`` → (left 独有, right 独有)。"""
    proc = git(repo, "rev-list", "--left-right", "--count", f"{left}...{right}", check=False)
    if proc.returncode != 0:
        return None
    parts = proc.stdout.split()
    if len(parts) != 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None


def sync_workspace(
    repo: Path, *, branch: str = DEV_BRANCH, remote: str = REMOTE_NAME, apply: bool = True
) -> WorkspaceSync:
    """:class:`WorkspaceSync` 的实现：fetch 之后**在明确无损时才**快进本地 ``branch``。

    ``apply=False`` 是只读探针（``GET diff`` 用）：照样 fetch、照样报数，但绝不动分支——
    读接口不该改学员的分支。``apply=True``（``POST /sync``、``POST submit``）才可能在
    "干净 + 不领先 + 就在这条分支上"时快进。
    """
    repo = Path(repo)
    remote_ref = f"{remote}/{branch}"
    configured = remote in git(repo, "remote", check=False).stdout.split()
    fetched = configured and git(repo, "fetch", "-q", remote, check=False).returncode == 0
    dirty = bool(git(repo, "status", "--porcelain", check=False).stdout.strip())
    current = git(repo, "rev-parse", "--abbrev-ref", "HEAD", check=False).stdout.strip()
    has_remote = ref_exists(repo, remote_ref)

    local_ahead = remote_ahead = 0
    if has_remote and ref_exists(repo, branch):
        counts = _rev_count(repo, branch, remote_ref)
        if counts is not None:
            local_ahead, remote_ahead = counts
    elif has_remote:
        # 本地还没有这条分支（还没 provision 过 dev）——远端有多少都算"领先"，但绝不在这里建分支：
        # 建分支属于 prepare_workspace 的活，sync 只对账。
        proc = git(repo, "rev-list", "--count", remote_ref, check=False)
        remote_ahead = int(proc.stdout.strip() or 0) if proc.returncode == 0 else 0

    fast_forwarded = False
    note = ""
    revision = git(repo, "rev-parse", "HEAD", check=False).stdout.strip() or None
    revision_short = (revision or "")[:12] or "?"
    if remote_ahead and apply and not dirty and local_ahead == 0 and current == branch:
        proc = git(repo, "merge", "--ff-only", "-q", remote_ref, check=False)
        fast_forwarded = proc.returncode == 0
        if fast_forwarded:
            remote_ahead = 0

    if not configured:
        note = f"这个工作区没有名为 {remote} 的远端：对不了账，也 push 不了。"
    elif not fetched:
        # fetch 都失败了，"远端比你新多少"就是上一次的旧数——不能拿它当现状。
        note = "fetch 失败（Forge 不可达或凭据失效）：无法确认远端状态，这次按本地内容走。"
    elif not has_remote:
        note = f"远端还没有 {branch} 分支：你还没 push 过。"
    elif fast_forwarded:
        note = f"已把工作区快进到远端的 {branch}——这次阅卷看的就是它。"
    elif remote_ahead and dirty:
        note = (
            f"远端 {branch} 比你这边新（领先 {remote_ahead} 个提交），但工作区有未提交改动，"
            "所以没有动你的分支：这次按工作区内容走。"
        )
    elif remote_ahead and local_ahead:
        # **分叉**：两边都有对方没有的提交。这是"无损"的反面，平台一个字都不动（不 merge、不
        # reset），但**绝不能说"点『从远端同步』就行"**——分叉时同步什么也做不了（真踩过：界面让人
        # 去点一个按不动的按钮，然后他提交，阅的却是本地这份旧代码）。
        # 这里要把三件事说清：现在按哪份走、为什么、以及两条真能走通的路。
        note = (
            f"**分叉了**：本地领先 {local_ahead} 个、远端 {branch} 领先 {remote_ahead} 个"
            "——两边都有对方没有的提交。平台不替你 merge、也不 reset，所以按**本地这份**"
            f"（{revision_short}）走，两条出路选一条："
            "① 在**你写代码那台机器**上把两边合好再 push（推荐，历史留得住）；"
            "② 想直接以远端那份为准，用「重做这一题」——平台会给新实例**重新 clone** 一份远端 dev"
            "（旧实例与其提交都留档）。"
        )
    elif remote_ahead and apply:
        note = (
            f"远端 {branch} 领先 {remote_ahead} 个提交，这次没有同步"
            f"（当前分支不是 {branch}，平台不动你的分支）：按本地这份走。"
        )
    elif remote_ahead:
        # 只读探针（GET diff）走到这里：学员在**另一台机器**上写完 push 了，后端这份 checkout 还旧。
        # 这一步不能只说"远端有新东西"，得说清"你该做什么、平台会做什么"。
        note = (
            f"远端 {branch} 领先这份工作区 {remote_ahead} 个提交：你多半是在另一台机器上写的。"
            "提交时会自动同步（工作树干净时无损快进）；想现在就同步，用「从远端同步」。"
        )
    elif local_ahead:
        note = (
            f"本地领先远端 {local_ahead} 个提交（还没 push）：按本地内容走。"
            "代码写在这台机器上就 push 一下，别处也好接着改。"
        )
    else:
        note = f"工作区与远端 {branch} 一致。"

    revision = git(repo, "rev-parse", "HEAD", check=False).stdout.strip() or None
    return WorkspaceSync(
        fetched=fetched,
        dirty=dirty,
        local_ahead=local_ahead,
        remote_ahead=remote_ahead,
        fast_forwarded=fast_forwarded,
        revision=revision,
        remote_branch=has_remote,
        # 分叉 = 两边都有对方没有的提交。界面据此**不要**再劝人点「从远端同步」——那按不动。
        diverged=bool(remote_ahead and local_ahead),
        note=note,
    )
