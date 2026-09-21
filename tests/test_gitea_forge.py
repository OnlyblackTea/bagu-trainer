"""Gitea Forge：API 客户端的幂等与错误翻译 + 真 provision（本地 git，假 Gitea）。

Gitea 本身用 respx 假装——**除了 clone**：``provision`` 里那段 clone / checkout / 记基线走的是
真 git（fork 的 ``clone_url`` 指向一个本地仓库），所以这条路径是被真跑过的，不是 mock 出来的。
"""

from __future__ import annotations

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


def _local_repo(tmp_path: Path) -> Path:
    """假装是 fork 的远端：本地 git 仓库（这样 provision 里的 clone 是真跑）。"""
    repo = tmp_path / "fork-repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "t@example.com")
    git(repo, "config", "user.name", "t")
    (repo / "a.txt").write_text("v1\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "init")
    return repo


def _instance(ticket_id: str = "db-stage-01") -> TicketInstance:
    return TicketInstance(
        id="ti-1",
        learner="deployer",
        ticket_id=ticket_id,
        ticket_version=1,
        engine_version="a-prime-0.1.0",
        test_version=1,
        state=InstanceState.IN_PROGRESS,
    )


def _api() -> GiteaApi:
    return GiteaApi(base_url=_BASE, token="tok-123")


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
