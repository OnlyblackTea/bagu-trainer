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
    RemoteBranchMissingError,
    git,
)
from platform_service.models import InstanceState, TicketInstance
from platform_service.stubs import StubForgeClient

_BASE = "https://gitea.test"
_UPSTREAM = "upstream/track"
_FORK_OWNER = "deployer"
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
        fork_owner=_FORK_OWNER,
        base_dir=tmp_path / "ws",
        stage_refs=stage_refs if stage_refs is not None else {"db-stage-01": "main"},
    )


# --- API 客户端：幂等与错误翻译 ------------------------------------------------


@respx.mock
def test_ensure_fork_reuses_an_existing_fork() -> None:
    """已经 fork 过就直接用——学员会在同一条工单上反复 provision。"""
    get = respx.get(f"{_BASE}/api/v1/repos/{_FORK_OWNER}/track").mock(
        return_value=httpx.Response(200, json={"clone_url": f"{_BASE}/{_FORK_OWNER}/track.git"})
    )
    post = respx.post(f"{_BASE}/api/v1/repos/upstream/track/forks").mock(
        return_value=httpx.Response(201, json={})
    )

    fork = _api().ensure_fork(upstream=_UPSTREAM, fork_owner=_FORK_OWNER)

    assert fork["clone_url"].endswith("deployer/track.git")
    assert get.called and not post.called


@respx.mock
def test_ensure_fork_creates_when_absent() -> None:
    respx.get(f"{_BASE}/api/v1/repos/{_FORK_OWNER}/track").mock(
        return_value=httpx.Response(404, json={"message": "not found"})
    )
    respx.post(f"{_BASE}/api/v1/repos/upstream/track/forks").mock(
        return_value=httpx.Response(201, json={"clone_url": f"{_BASE}/{_FORK_OWNER}/track.git"})
    )

    fork = _api().ensure_fork(upstream=_UPSTREAM, fork_owner=_FORK_OWNER)

    assert fork["clone_url"].endswith("deployer/track.git")


@respx.mock
def test_create_pull_returns_the_html_url() -> None:
    respx.post(f"{_BASE}/api/v1/repos/upstream/track/pulls").mock(
        return_value=httpx.Response(201, json={"html_url": f"{_BASE}/upstream/track/pulls/7"})
    )
    url = _api().create_pull(
        upstream=_UPSTREAM,
        fork_owner=_FORK_OWNER,
        head_branch=DEV_BRANCH,
        base_branch="main",
        title="t",
        body="b",
    )
    assert url == f"{_BASE}/upstream/track/pulls/7"


@respx.mock
def test_create_pull_without_a_remote_branch_is_not_a_500() -> None:
    """学员还没 push：这是**预期内**的普通状况，要翻译成能让前端说清"先 push"的错误。"""
    respx.post(f"{_BASE}/api/v1/repos/upstream/track/pulls").mock(
        return_value=httpx.Response(404, json={"message": "head branch not found"})
    )
    respx.get(f"{_BASE}/api/v1/repos/{_FORK_OWNER}/track/branches/{DEV_BRANCH}").mock(
        return_value=httpx.Response(404, json={"message": "not found"})
    )

    with pytest.raises(RemoteBranchMissingError) as exc:
        _api().create_pull(
            upstream=_UPSTREAM,
            fork_owner=_FORK_OWNER,
            head_branch=DEV_BRANCH,
            base_branch="main",
            title="t",
            body="b",
        )
    assert exc.value.branch == DEV_BRANCH and exc.value.repo == f"{_FORK_OWNER}/track"


@respx.mock
def test_reopening_the_same_pull_is_idempotent() -> None:
    """重复开 PR 不是错误：Gitea 给 409/422，我们应找回已有 PR 并返回同一个 URL。

    实测（Gitea 1.22）fork PR 的 ``head.label`` 就是分支名 ``dev``，**不是** ``owner:branch``；
    按后者去比会找不到已有 PR，然后带着原始 409 抛出去、退化成 503
    ——这个 bug 是 VM 上的端到端才暴露出来的，所以这里补一条回归。
    """
    respx.post(f"{_BASE}/api/v1/repos/upstream/track/pulls").mock(
        return_value=httpx.Response(409, json={"message": "pull request already exists"})
    )
    respx.get(f"{_BASE}/api/v1/repos/upstream/track/pulls").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "html_url": "https://gitea.test/upstream/track/pulls/9",
                    "head": {"label": DEV_BRANCH, "ref": DEV_BRANCH},
                },
                {"html_url": "https://gitea.test/other", "head": {"label": "feature"}},
            ],
        )
    )

    url = _api().create_pull(
        upstream=_UPSTREAM,
        fork_owner=_FORK_OWNER,
        head_branch=DEV_BRANCH,
        base_branch="main",
        title="t",
        body="b",
    )

    assert url == "https://gitea.test/upstream/track/pulls/9"


@respx.mock
def test_create_pull_auth_failure_is_unavailable() -> None:
    respx.post(f"{_BASE}/api/v1/repos/upstream/track/pulls").mock(
        return_value=httpx.Response(401, json={"message": "token is invalid"})
    )
    with pytest.raises(ForgeUnavailableError, match="401"):
        _api().create_pull(
            upstream=_UPSTREAM,
            fork_owner=_FORK_OWNER,
            head_branch=DEV_BRANCH,
            base_branch="main",
            title="t",
            body="b",
        )


@respx.mock
def test_unreachable_gitea_is_unavailable() -> None:
    respx.get(f"{_BASE}/api/v1/repos/{_FORK_OWNER}/track").mock(
        side_effect=httpx.ConnectError("refused")
    )
    with pytest.raises(ForgeUnavailableError, match="unreachable"):
        _api().ensure_fork(upstream=_UPSTREAM, fork_owner=_FORK_OWNER)


def test_authenticated_clone_url_injects_the_token_but_leaves_ssh_alone() -> None:
    api = GiteaApi(base_url=_BASE, token="tok-123")
    assert api.authenticated_clone_url("https://gitea.test/a/b.git") == (
        "https://tok-123@gitea.test/a/b.git"
    )
    assert api.authenticated_clone_url("git@gitea.test:a/b.git") == "git@gitea.test:a/b.git"


# --- provision：真 clone / 真 dev 分支 / 记基线 ---------------------------------


@respx.mock
def test_provision_clones_the_fork_and_creates_the_dev_branch(tmp_path: Path) -> None:
    fork_repo = _local_repo(tmp_path)
    respx.get(f"{_BASE}/api/v1/repos/{_FORK_OWNER}/track").mock(
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
    # token 不许留在 .git/config 里
    assert git(ws.path, "remote", "get-url", "origin").stdout.strip() == str(fork_repo)
    # 幂等：再 provision 一次不炸、也不重置学员的分支
    assert client.provision(instance).path == ws.path


@respx.mock
def test_provision_against_a_local_fork_needs_no_credentials(tmp_path: Path) -> None:
    """fork 的 clone_url 是**本地路径**时不装凭据——本地仓库本来就不需要认证。

    （真 Gitea 走的是 http，那条路径由下面那条测试与 VM 实测覆盖。）
    """
    fork_repo = _local_repo(tmp_path)
    respx.get(f"{_BASE}/api/v1/repos/{_FORK_OWNER}/track").mock(
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
    respx.get(f"{_BASE}/api/v1/repos/{_FORK_OWNER}/track").mock(
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
    respx.get(f"{_BASE}/api/v1/repos/{_FORK_OWNER}/track").mock(
        return_value=httpx.Response(200, json={"clone_url": str(fork)})
    )

    def client(base_dir: Path) -> GiteaForgeClient:
        return GiteaForgeClient(
            api=_api(),
            upstream=_UPSTREAM,
            fork_owner=_FORK_OWNER,
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
    respx.get(f"{_BASE}/api/v1/repos/{_FORK_OWNER}/track").mock(
        return_value=httpx.Response(200, json={"name": "track"})
    )
    with pytest.raises(ForgeUnavailableError, match="clone_url"):
        _client(tmp_path).provision(_instance())


@respx.mock
def test_create_pr_uses_the_fork_prefix_and_strips_origin(tmp_path: Path) -> None:
    """本地 ref 是 ``origin/<stage>`` / ``dev``；Gitea 要 ``<owner>:<branch>`` / ``<branch>``。"""
    route = respx.post(f"{_BASE}/api/v1/repos/upstream/track/pulls").mock(
        return_value=httpx.Response(201, json={"html_url": "https://gitea.test/pulls/1"})
    )
    url = _client(tmp_path).create_pr(_instance(), base="origin/main", head=DEV_BRANCH)

    assert url == "https://gitea.test/pulls/1"
    sent = route.calls[0].request
    import json

    body = json.loads(sent.content)
    assert body["head"] == f"{_FORK_OWNER}:{DEV_BRANCH}" and body["base"] == "main"


# --- 装配与配置 ---------------------------------------------------------------


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
        settings=GiteaSettings(
            base_url=_BASE, token="t", upstream=_UPSTREAM, fork_owner=_FORK_OWNER
        ),
    )
    assert isinstance(real, GiteaForgeClient) and forge_backend_name(real) == "gitea"


def _unused(_: Any) -> None: ...  # 让 typing 导入不至于"看起来没用"
