"""Gitea Forge：API 客户端的幂等与错误翻译 + 真 provision（本地 git，假 Gitea）。

Gitea 本身用 respx 假装——**除了 clone**：``provision`` 里那段 clone / checkout / 记基线走的是
真 git（fork 的 ``clone_url`` 指向一个本地仓库），所以这条路径是被真跑过的，不是 mock 出来的。
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from platform_api.forge_gitea import GiteaForgeClient
from platform_api.gitea import GiteaApi
from platform_api.stubs_wiring import (
    GiteaSettings,
    build_forge,
    forge_backend_name,
    gitea_settings_from_env,
    resolve_forge_backend,
)
from platform_service.forge import (
    DEV_BRANCH,
    ForgeUnavailableError,
    git,
)
from platform_service.models import InstanceState, TicketInstance
from platform_service.stubs import StubForgeClient

_BASE = "https://gitea.test"
_UPSTREAM = "upstream/track"
_LEARNER = "deployer"  # 学员账号：他直接 push 到内容仓（没有 fork）
_TOKEN = "tok-123"


def _local_repo(tmp_path: Path) -> Path:
    """假装是 fork 的远端：本地 git 仓库（这样 provision 里的 clone 是真跑）。

    ``commit.gpgsign false`` 不是可有可无：本机全局开着签名时，``git commit`` 会去弹 pinentry，
    在无人值守的测试里表现为**卡 60 秒后失败 128**——一个跟被测代码毫无关系的红。
    """
    repo = tmp_path / "fork-repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "t@example.com")
    git(repo, "config", "user.name", "t")
    git(repo, "config", "commit.gpgsign", "false")
    (repo / "a.txt").write_text("v1\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "init")
    return repo


def _instance(ticket_id: str = "db-stage-01") -> TicketInstance:
    return TicketInstance(
        id="ti-1",
        learner="deployer",
        track="database-bagu",
        ticket_id=ticket_id,
        ticket_version=1,
        engine_version="a-prime-0.1.0",
        test_version=1,
        state=InstanceState.IN_PROGRESS,
    )


def _api() -> GiteaApi:
    return GiteaApi(base_url=_BASE, token=_TOKEN)


def _client(tmp_path: Path, *, stage_refs: dict[str, str] | None = None) -> GiteaForgeClient:
    return GiteaForgeClient(
        api=_api(),
        upstream=_UPSTREAM,
        base_dir=tmp_path / "ws",
        stage_refs=stage_refs if stage_refs is not None else {"db-stage-01": "main"},
    )


# --- API 客户端：幂等与错误翻译 ------------------------------------------------


@respx.mock
def test_unreachable_gitea_is_unavailable() -> None:
    respx.get(f"{_BASE}/api/v1/repos/{_UPSTREAM}").mock(
        side_effect=httpx.ConnectError("refused")
    )
    with pytest.raises(ForgeUnavailableError, match="unreachable"):
        _api().repo(*_UPSTREAM.split("/", 1))


def test_authenticated_clone_url_injects_the_token_but_leaves_ssh_alone() -> None:
    api = GiteaApi(base_url=_BASE, token="tok-123")
    assert api.authenticated_clone_url("https://gitea.test/a/b.git") == (
        "https://tok-123@gitea.test/a/b.git"
    )
    assert api.authenticated_clone_url("git@gitea.test:a/b.git") == "git@gitea.test:a/b.git"


# --- provision：真 clone / 真 dev 分支 / 记基线 ---------------------------------


@respx.mock
def test_provision_clones_the_track_repo_and_creates_the_dev_branch(tmp_path: Path) -> None:
    fork_repo = _local_repo(tmp_path)
    respx.get(f"{_BASE}/api/v1/repos/{_UPSTREAM}").mock(
        return_value=httpx.Response(200, json={"clone_url": str(fork_repo)})
    )
    client = _client(tmp_path)
    instance = _instance()

    ws = client.provision(instance)

    assert ws.path.is_dir() and (ws.path / ".git").is_dir()
    assert ws.base_ref == "origin/main" and ws.head_ref == DEV_BRANCH
    assert ws.simulated is False  # 真 Forge：没有任何东西是装的
    # dev 分支真的建出来了，且从阶段分支起
    assert git(ws.path, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == DEV_BRANCH
    # 基线记成 SHA（阅卷据此比出"学员改了什么"，含已提交的改动）
    baseline = git(ws.path, "config", "--get", "bagu.baseRef").stdout.strip()
    assert len(baseline) == 40
    # 同时记下"开始这一阶段时他自己那条 dev"：刚 provision 出来时它就是基线本身
    assert git(ws.path, "config", "--get", "bagu.stageStart").stdout.strip() == baseline
    # 本来就是从这个分支开出来的 dev ⇒ 不需要"先合并基线"这条指令
    assert ws.merge_hint is None
    # token 不许留在 .git/config 里
    assert git(ws.path, "remote", "get-url", "origin").stdout.strip() == str(fork_repo)
    # 幂等：再 provision 一次不炸、也不重置学员的分支
    assert client.provision(instance).path == ws.path


@respx.mock
def test_stale_baseline_from_a_rewritten_track_is_refreshed(tmp_path: Path) -> None:
    """Track 被重写过（阶段分支换了 lineage）时，旧工作区里钉的旧 SHA 必须被换掉。

    真踩过：把六条兄弟分支改造成一条链之后，老工作区里记的还是**旧**阶段分支的 SHA——它不是学员
    ``dev`` 的祖先。判定面按它比，就会把"合并新基线带进来的验收测试"（``src/test/…``）算成"改了
    判定面"⇒ 硬门 fail，而那次合并完全干净。
    """
    repo = _local_repo(tmp_path)
    git(repo, "checkout", "-q", "-b", "side")  # 另一条 lineage（模拟被重写掉的那条）
    (repo / "old.txt").write_text("old lineage\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "old lineage")
    stale = git(repo, "rev-parse", "HEAD").stdout.strip()
    git(repo, "checkout", "-q", "main")
    respx.get(f"{_BASE}/api/v1/repos/{_UPSTREAM}").mock(
        return_value=httpx.Response(200, json={"clone_url": str(repo)})
    )
    client = _client(tmp_path)
    ws = client.provision(_instance())
    # 人为把记录改成"旧 lineage 的 SHA"（= 重构前 provision 出来的工作区）
    git(ws.path, "config", "bagu.baseRef", stale)
    git(ws.path, "config", "bagu.stageStart", stale)

    fresh = client.provision(_instance())

    recorded = git(ws.path, "config", "--get", "bagu.baseRef").stdout.strip()
    assert recorded != stale
    assert recorded == git(ws.path, "rev-parse", "origin/main").stdout.strip()
    assert fresh.merge_hint is None


@respx.mock
def test_merge_hint_appears_only_while_the_stage_baseline_is_unmerged(tmp_path: Path) -> None:
    """进入下一阶段时，平台要在界面上给出那句 ``git merge origin/<stage>``。

    为什么值得有：链式 Track 结构下**本阶段的验收测试住在阶段分支上**，他的 ``dev`` 是从上一关
    那条线长出来的、天然不含它。不做这次合并，工作区里就没有判据——``mvn -Dgroups=stage0N`` 一条
    用例都跑不到，红在一个跟代码无关的地方（真踩过）。合并只加不删，所以这条指令可以照抄；而
    已经合过的人不该再看到它。
    """
    repo = _local_repo(tmp_path)  # main = 骨架
    skeleton = git(repo, "rev-parse", "HEAD").stdout.strip()
    git(repo, "checkout", "-q", "-b", "stage-02")
    (repo / "stage02-tests.txt").write_text("stage-02 判据\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "stage-02 baseline")
    git(repo, "checkout", "-q", "-b", "dev", skeleton)  # 学员那条跨阶段累积的线
    (repo / "work.txt").write_text("my stage-1 work\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "stage 1 done")
    git(repo, "checkout", "-q", "stage-02")
    respx.get(f"{_BASE}/api/v1/repos/{_UPSTREAM}").mock(
        return_value=httpx.Response(200, json={"clone_url": str(repo)})
    )
    refs = {"db-stage-02": "stage-02"}
    client = _client(tmp_path, stage_refs=refs)

    ws = client.provision(_instance("db-stage-02"))

    assert ws.merge_hint is not None
    assert "git merge origin/stage-02" in ws.merge_hint

    # 照做一遍：合进来之后就不该再给这条命令
    git(ws.path, "fetch", "-q", "origin")
    git(ws.path, "merge", "-q", "--no-edit", "origin/stage-02")
    assert client.provision(_instance("db-stage-02")).merge_hint is None


@respx.mock
def test_provision_against_a_local_path_needs_no_credentials(tmp_path: Path) -> None:
    """fork 的 clone_url 是**本地路径**时不装凭据——本地仓库本来就不需要认证。

    （真 Gitea 走的是 http，那条路径由下面那条测试与 VM 实测覆盖。）
    """
    fork_repo = _local_repo(tmp_path)
    respx.get(f"{_BASE}/api/v1/repos/{_UPSTREAM}").mock(
        return_value=httpx.Response(200, json={"clone_url": str(fork_repo)})
    )
    ws = _client(tmp_path).provision(_instance())

    assert not (tmp_path / "ws" / ".git-credentials").exists()
    # git 把 credential.helper 写成 [credential] 段里的 `helper = …`
    assert "[credential]" not in (ws.path / ".git" / "config").read_text(encoding="utf-8")


def test_push_credentials_are_written_outside_the_repo(tmp_path: Path) -> None:
    """要能 ``git push`` 才谈得上真 PR——而 push 凭据不能落在仓库里。

    token 写进**仓库外**的 0600 文件，仓库里只留 ``credential.helper`` 指向它的路径。于是
    ① ``.git/config`` 里没有秘密 ② ``git add -A`` 永远不可能把它提交进去（它不在仓库目录里）
    ③ 学员 ``git push origin dev`` 开箱可用（真 push 在 VM 上实测，这里只钉住"配置写对了"）。
    """
    client = _client(tmp_path)
    dest = tmp_path / "ws" / "ti-1"
    dest.mkdir(parents=True)
    git(dest, "init", "-q", "-b", "main")

    creds = client.install_push_credentials(dest, "http://gitea.test/deployer/track.git")

    assert creds is not None
    config = (dest / ".git" / "config").read_text(encoding="utf-8")
    assert _TOKEN not in config, "token 不能落进 .git/config"
    # git 把它写成 [credential] 段里的 `helper = store --file=…`：仓库里只有**路径**，没有秘密
    assert "[credential]" in config
    assert "store --file=" in config
    assert str(creds.as_posix()) in config

    # 凭据文件在工作区**之外**：`git add -A` 碰不到它
    assert not creds.is_relative_to(dest)
    body = creds.read_text(encoding="utf-8").strip()
    # 用户名与密码都填 token：Gitea 两条基本认证路径都认
    assert body.startswith("http://") and f"{_TOKEN}:{_TOKEN}@" in body
    if os.name != "nt":  # Windows 上 chmod 只映射只读位，别在那边假装验得到了 0600
        assert (creds.stat().st_mode & 0o777) == 0o600


@respx.mock
def test_real_forge_preview_diffs_the_worktree_and_offers_a_push_command(
    tmp_path: Path,
) -> None:
    """真 Forge 的预览 diff 比的是工作区（含未提交改动），并给出可照抄的 push 命令（不含凭据）。"""
    fork_repo = _local_repo(tmp_path)
    respx.get(f"{_BASE}/api/v1/repos/{_UPSTREAM}").mock(
        return_value=httpx.Response(200, json={"clone_url": str(fork_repo)})
    )
    ws = _client(tmp_path).provision(_instance())

    assert ws.diff_head_ref is None  # None = 与工作区比
    assert ws.push_hint is not None
    assert f"git push -u origin {DEV_BRANCH}" in ws.push_hint
    assert _TOKEN not in ws.push_hint


def test_credential_line_covers_http_and_leaves_ssh_alone() -> None:
    api = _api()
    assert api.credential_line("http://gitea.test/a/b.git") == (
        f"http://{_TOKEN}:{_TOKEN}@gitea.test/"
    )
    assert api.credential_line("https://gitea.test/a/b.git") == (
        f"https://{_TOKEN}:{_TOKEN}@gitea.test/"
    )
    # ssh 形态交给学员自己的 key，平台不把 token 塞进去
    assert api.credential_line("git@gitea.test:a/b.git") is None


@respx.mock
def test_reprovision_continues_the_remote_dev_and_the_push_fast_forwards(
    tmp_path: Path,
) -> None:
    """远端已有 ``dev`` 时，第二次 provision 要**接着学员那条线**，不能从阶段分支重建。

    重建的后果不是"多此一举"，而是学员的下一次 ``git push`` 直接被拒
    （"tip of your current branch is behind its remote counterpart"）——一个跟他这题写得对不对
    毫无关系的失败。这条真跑一遍 push：fork 用**裸仓**（Gitea 的仓库就是裸的；非裸仓还会拒绝更新
    当前检出的分支，那会把一个真实的 git 限制伪装成我们的 bug）。
    """
    fork = tmp_path / "fork.git"
    subprocess.run(
        ["git", "clone", "-q", "--bare", str(_local_repo(tmp_path)), str(fork)],
        check=True,
        capture_output=True,
    )
    respx.get(f"{_BASE}/api/v1/repos/{_UPSTREAM}").mock(
        return_value=httpx.Response(200, json={"clone_url": str(fork)})
    )

    def client(base_dir: Path) -> GiteaForgeClient:
        return GiteaForgeClient(
            api=_api(),
            upstream=_UPSTREAM,
                base_dir=base_dir,
            stage_refs={"db-stage-01": "main"},
        )

    ws = client(tmp_path / "ws").provision(_instance())
    assert git(ws.path, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == DEV_BRANCH

    (ws.path / "a.txt").write_text("learner work\n", encoding="utf-8")
    git(ws.path, "add", "-A")
    git(ws.path, "-c", "commit.gpgsign=false", "commit", "-qm", "learner work")
    learner_sha = git(ws.path, "rev-parse", "HEAD").stdout.strip()
    git(ws.path, "push", "-q", "origin", f"{DEV_BRANCH}:{DEV_BRANCH}")  # 学员自己推

    # 第二次 provision（全新的工作区）：应当从远端 dev 起，而不是回到阶段分支
    again = client(tmp_path / "ws2").provision(_instance())
    assert git(again.path, "rev-parse", "HEAD").stdout.strip() == learner_sha
    assert git(again.path, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == DEV_BRANCH

    # 接着改、接着推：必须能 fast-forward（这正是"重建"会砸掉的那件事）
    (again.path / "a.txt").write_text("learner work v2\n", encoding="utf-8")
    git(again.path, "add", "-A")
    git(again.path, "-c", "commit.gpgsign=false", "commit", "-qm", "learner work v2")
    git(again.path, "push", "-q", "origin", f"{DEV_BRANCH}:{DEV_BRANCH}")
    pushed = git(fork, "rev-parse", DEV_BRANCH).stdout.strip()
    assert pushed == git(again.path, "rev-parse", "HEAD").stdout.strip()


@respx.mock
def test_provision_without_a_clone_url_is_unavailable(tmp_path: Path) -> None:
    respx.get(f"{_BASE}/api/v1/repos/{_UPSTREAM}").mock(
        return_value=httpx.Response(200, json={"name": "track"})
    )
    with pytest.raises(ForgeUnavailableError, match="clone_url"):
        _client(tmp_path).provision(_instance())


def test_resolve_forge_backend_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    assert resolve_forge_backend() == "stub"
    monkeypatch.setenv("BAGU_FORGE", "gitea")
    assert resolve_forge_backend() == "gitea"
    assert resolve_forge_backend("stub") == "stub"  # 显式参数优先
    monkeypatch.setenv("BAGU_FORGE", "svn")
    with pytest.raises(ValueError, match="svn"):
        resolve_forge_backend()


def test_gitea_settings_report_every_missing_var(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("BAGU_GITEA_URL", "BAGU_GITEA_TOKEN", "BAGU_GITEA_UPSTREAM", "BAGU_GITEA_OWNER"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ValueError) as exc:
        gitea_settings_from_env()
    assert "BAGU_GITEA_TOKEN" in str(exc.value) and "BAGU_GITEA_URL" in str(exc.value)


def test_gitea_settings_reject_a_bad_upstream(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BAGU_GITEA_URL", _BASE)
    monkeypatch.setenv("BAGU_GITEA_TOKEN", "t")
    monkeypatch.setenv("BAGU_GITEA_UPSTREAM", "just-a-name")
    monkeypatch.setenv("BAGU_GITEA_OWNER", "deployer")
    with pytest.raises(ValueError, match="owner/repo"):
        gitea_settings_from_env()


def test_build_forge_selects_the_backend(tmp_path: Path) -> None:
    stub = build_forge(
        source_repo=_local_repo(tmp_path), base_dir=tmp_path / "ws", stage_refs={}, backend="stub"
    )
    assert isinstance(stub, StubForgeClient) and forge_backend_name(stub) == "stub"

    real = build_forge(
        source_repo=tmp_path,
        base_dir=tmp_path / "ws2",
        stage_refs={},
        backend="gitea",
        settings=GiteaSettings(base_url=_BASE, token="t", upstream=_UPSTREAM),
    )
    assert isinstance(real, GiteaForgeClient) and forge_backend_name(real) == "gitea"


def _unused(_: Any) -> None: ...  # 让 typing 导入不至于"看起来没用"
