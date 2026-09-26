"""``track_loader``：compose 栈启动时把 Track 内容载入 Gitea。

为什么值得单独测：这条路的每一步都**只在真的 Gitea 上才暴露**（fork 是快照、官方镜像不认
``GITEA_ADMIN_*``、令牌会越攒越多），而它们的共同症状是同一个 `workspace_ref_missing`——从现象
回推原因极其费时。所以这里把能脱离 Gitea 验证的部分钉死：配置解析、分支发现、HTTP 步骤的幂等
语义（已存在不是错误）、令牌复用，以及"顺序"这件事（推上游在 fork 之前）。

HTTP 用 respx 拦；git 用真的临时仓库（分支名这种东西不该被 mock 掩盖）。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from platform_api.track_loader import (
    DEFAULT_ADMIN_PASSWORD,
    DEFAULT_ADMIN_USER,
    LoaderConfig,
    LoadError,
    delete_repo,
    ensure_default_branch,
    ensure_fork,
    ensure_repo,
    ensure_user,
    load_track,
    local_branches,
    mint_token,
    push_all_branches,
    remote_branches,
    token_for,
    token_push_url,
    valid_token,
    wait_for_admin,
    wait_for_healthy,
    wait_for_remote_branches,
)

GITEA = "http://gitea.test"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _content_repo(tmp_path: Path, branches: list[str]) -> Path:
    """造一个"内容仓"：main + 每个阶段的分支（一半带 -sol，跟真 Track 一样）。"""
    repo = tmp_path / "content"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "commit.gpgsign", "false")
    (repo / "track.yaml").write_text("track: {}\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")
    for branch in branches:
        if branch != "main":  # 已经在 main 上，再 branch main 会 128
            _git(repo, "branch", branch)
    return repo


def _config(tmp_path: Path, **over: Any) -> LoaderConfig:
    base: dict[str, Any] = {
        "gitea_url": GITEA,
        "content_dir": _content_repo(tmp_path, ["stage-01", "stage-01-sol"]),
        "secrets_dir": tmp_path / "secrets",
        "upstream_owner": "trackowner",
        "upstream_name": "seckill-lab",
        "learner": "deployer",
        "timeout_s": 1.0,
    }
    base.update(over)
    return LoaderConfig(**base)


# --- 配置 --------------------------------------------------------------------------


def test_config_comes_from_env_and_refuses_a_non_repo(tmp_path: Path) -> None:
    env = {
        "BAGU_GITEA_URL": GITEA,
        "BAGU_CONTENT_DIR": str(_content_repo(tmp_path, ["stage-01"])),
        "BAGU_SECRETS_DIR": str(tmp_path / "secrets"),
        "BAGU_TRACK_NAME": "seckill-lab",
    }
    config = LoaderConfig.from_env(env)
    assert config.upstream == "trackowner/seckill-lab"
    assert config.admin_user == DEFAULT_ADMIN_USER
    assert config.admin_password == DEFAULT_ADMIN_PASSWORD
    assert config.fork_reset is False

    with pytest.raises(LoadError, match="git 仓库"):
        LoaderConfig.from_env({**env, "BAGU_CONTENT_DIR": str(tmp_path)})

    with pytest.raises(LoadError, match="BAGU_GITEA_URL"):
        LoaderConfig.from_env({**env, "BAGU_GITEA_URL": ""})


def test_fork_reset_flag_accepts_the_usual_truthy_spellings(tmp_path: Path) -> None:
    env = {
        "BAGU_GITEA_URL": GITEA,
        "BAGU_CONTENT_DIR": str(_content_repo(tmp_path, ["main"])),
        "BAGU_FORK_RESET": "1",
    }
    assert LoaderConfig.from_env(env).fork_reset is True
    env["BAGU_FORK_RESET"] = "off"
    assert LoaderConfig.from_env(env).fork_reset is False


# --- 分支：内容里有几条，Gitea 里就该有几条 ----------------------------------------


def test_local_branches_is_the_whole_content_contract(tmp_path: Path) -> None:
    repo = _content_repo(tmp_path, ["stage-02", "stage-01-sol", "stage-01"])
    assert local_branches(repo) == ["main", "stage-01", "stage-01-sol", "stage-02"]


def test_push_pushes_every_branch_with_force(tmp_path: Path) -> None:
    """内容仓是**权威来源**：它的分支就该是 Forge 里的分支，所以带 force。"""
    repo = _content_repo(tmp_path, ["stage-01"])
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(repo), str(bare)], check=True)

    pushed = push_all_branches(repo, str(bare))
    assert pushed == ["main", "stage-01"]
    out = subprocess.run(
        ["git", "-C", str(bare), "for-each-ref", "--format=%(refname:short)", "refs/heads"],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    assert sorted(out) == ["main", "stage-01"]


def test_remote_branches_and_the_waiting_helper_are_git_authoritative(tmp_path: Path) -> None:
    """就绪与否问 git，不问 Gitea API：异步 fork 期间 API 会对**任何**分支回 404。

    （真机现象：`empty: true` 的 fork 上 `GET /branches/main` 404，而 `git ls-remote` 列得出
    全部分支——provision 走的正是后者。）
    """
    repo = _content_repo(tmp_path, ["stage-01"])
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(repo), str(bare)], check=True)

    assert remote_branches(str(bare)) == ["main", "stage-01"]

    # 还缺一条时：等到超时就把"已知的"还回来，由调用方决定补推（这里用不存在分支模拟）
    remote = wait_for_remote_branches(str(bare), ["main", "stage-09"], timeout_s=0.1,
                                      log=lambda *_: None)
    assert remote == ["main", "stage-01"]


def test_push_refuses_an_empty_content_repo(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    _git(empty, "init", "-q", "-b", "main")
    with pytest.raises(LoadError, match="没有任何本地分支"):
        push_all_branches(empty, str(tmp_path / "remote.git"))


def test_token_push_url_carries_the_token_but_stays_a_plain_http_url() -> None:
    assert (
        token_push_url("http://gitea.test:3000", "trackowner", "seckill-lab", "TOK")
        == "http://TOK@gitea.test:3000/trackowner/seckill-lab.git"
    )


# --- HTTP 步骤的幂等语义 -----------------------------------------------------------


@respx.mock
def test_wait_for_healthy_accepts_only_a_passing_healthz(monkeypatch: pytest.MonkeyPatch) -> None:
    route = respx.get(f"{GITEA}/api/healthz").mock(
        return_value=httpx.Response(200, json={"status": "pass"})
    )
    wait_for_healthy(GITEA, timeout_s=5)
    assert route.called


@respx.mock
def test_wait_for_healthy_times_out_with_a_readable_reason() -> None:
    respx.get(f"{GITEA}/api/healthz").mock(return_value=httpx.Response(503, text="starting"))
    with pytest.raises(LoadError, match="没在"):
        wait_for_healthy(GITEA, timeout_s=0.1)


@respx.mock
def test_wait_for_admin_explains_the_cli_sidecar() -> None:
    respx.get(f"{GITEA}/api/v1/user").mock(return_value=httpx.Response(401, json={"message": "x"}))
    with pytest.raises(LoadError, match="gitea-admin"):
        wait_for_admin(GITEA, "baguadmin", "pw", timeout_s=0.1)


@respx.mock
def test_ensure_user_treats_already_exists_as_success() -> None:
    respx.post(f"{GITEA}/api/v1/admin/users").mock(return_value=httpx.Response(201, json={}))
    assert ensure_user(GITEA, ("a", "b"), "deployer", "pw") is True

    respx.post(f"{GITEA}/api/v1/admin/users").mock(
        return_value=httpx.Response(422, json={"message": "user already exists"})
    )
    assert ensure_user(GITEA, ("a", "b"), "deployer", "pw") is False


@respx.mock
def test_ensure_repo_and_fork_are_idempotent() -> None:
    respx.get(f"{GITEA}/api/v1/repos/trackowner/seckill-lab").mock(
        return_value=httpx.Response(200, json={"name": "seckill-lab"})
    )
    assert ensure_repo(GITEA, "TOK", "trackowner", "seckill-lab") is False

    respx.get(f"{GITEA}/api/v1/repos/trackowner/seckill-lab").mock(
        return_value=httpx.Response(404, json={})
    )
    respx.post(f"{GITEA}/api/v1/user/repos").mock(
        return_value=httpx.Response(201, json={"name": "seckill-lab"})
    )
    assert ensure_repo(GITEA, "TOK", "trackowner", "seckill-lab") is True

    respx.get(f"{GITEA}/api/v1/repos/deployer/seckill-lab").mock(
        return_value=httpx.Response(404, json={})
    )
    respx.post(f"{GITEA}/api/v1/repos/trackowner/seckill-lab/forks").mock(
        return_value=httpx.Response(202, json={})
    )
    assert ensure_fork(GITEA, "TOK", "trackowner/seckill-lab", "deployer", "seckill-lab") is True


@respx.mock
def test_mint_token_prefers_sha1_and_fails_loudly_without_one() -> None:
    respx.post(f"{GITEA}/api/v1/users/deployer/tokens").mock(
        return_value=httpx.Response(201, json={"sha1": "abc123"})
    )
    assert mint_token(GITEA, "deployer", "pw", "loader") == "abc123"

    respx.post(f"{GITEA}/api/v1/users/deployer/tokens").mock(
        return_value=httpx.Response(201, json={"name": "loader"})
    )
    with pytest.raises(LoadError, match="没回令牌"):
        mint_token(GITEA, "deployer", "pw", "loader")


@respx.mock
def test_valid_token_answers_false_instead_of_raising() -> None:
    respx.get(f"{GITEA}/api/v1/user").mock(return_value=httpx.Response(200, json={}))
    assert valid_token(GITEA, "abc") is True
    respx.get(f"{GITEA}/api/v1/user").mock(return_value=httpx.Response(401, json={}))
    assert valid_token(GITEA, "abc") is False
    assert valid_token(GITEA, "") is False


@respx.mock
def test_token_for_reuses_a_stored_token_instead_of_minting_a_duplicate(tmp_path: Path) -> None:
    """重启栈时**不能**再造一枚：Gitea 不允许同名令牌，第二次 up 会 400 直接把栈卡死。

    真机症状：`access token name has been used already` → track-loader 退出非 0 →
    platform 起不来。（只有"每次都清卷"的验证方式才看不出这个 bug。）
    """
    path = tmp_path / "secrets" / "gitea-token"
    path.parent.mkdir(parents=True)
    path.write_text("stored-token\n", encoding="utf-8")

    user_route = respx.get(f"{GITEA}/api/v1/user").mock(
        return_value=httpx.Response(200, json={"login": "deployer"})
    )
    mint_route = respx.post(f"{GITEA}/api/v1/users/deployer/tokens").mock(
        return_value=httpx.Response(201, json={"sha1": "fresh"})
    )

    token, reused = token_for(GITEA, "deployer", "pw", "track-loader", path=path)

    assert (token, reused) == ("stored-token", True)
    assert user_route.called and not mint_route.called


@respx.mock
def test_token_for_mints_a_uniquely_named_token_when_the_stored_one_is_dead(
    tmp_path: Path,
) -> None:
    path = tmp_path / "secrets" / "gitea-token"
    path.parent.mkdir(parents=True)
    path.write_text("stale-token\n", encoding="utf-8")
    respx.get(f"{GITEA}/api/v1/user").mock(return_value=httpx.Response(401, json={}))
    mint_route = respx.post(f"{GITEA}/api/v1/users/deployer/tokens").mock(
        return_value=httpx.Response(201, json={"sha1": "fresh-token"})
    )

    token, reused = token_for(GITEA, "deployer", "pw", "track-loader", path=path)

    assert (token, reused) == ("fresh-token", False)
    # 名字带时间戳：免得撞上 Gitea 里残留的同名令牌
    sent = json.loads(mint_route.calls[0].request.content.decode())
    assert sent["name"].startswith("track-loader-")
    assert sent["name"] != "track-loader"
    assert path.read_text(encoding="utf-8").strip() == "fresh-token"


@respx.mock
def test_ensure_default_branch_pins_it_and_skips_when_already_right() -> None:
    """默认分支必须钉在 main：Gitea 会把"空"仓的第一条推送设成默认分支，
    而学员第一次推的正是 dev——之后 dev 就删不掉了（重开一题会被拒）。"""
    respx.get(f"{GITEA}/api/v1/repos/deployer/seckill-lab").mock(
        return_value=httpx.Response(200, json={"default_branch": "main"})
    )
    patch = respx.patch(f"{GITEA}/api/v1/repos/deployer/seckill-lab").mock(
        return_value=httpx.Response(200, json={})
    )
    assert ensure_default_branch(GITEA, "TOK", "deployer", "seckill-lab", "main") is False
    assert not patch.called

    respx.get(f"{GITEA}/api/v1/repos/deployer/seckill-lab").mock(
        return_value=httpx.Response(200, json={"default_branch": "_probe"})
    )
    assert ensure_default_branch(GITEA, "TOK", "deployer", "seckill-lab", "main") is True
    assert json.loads(patch.calls[0].request.content.decode()) == {"default_branch": "main"}


@respx.mock
def test_delete_repo_tolerates_a_missing_repo() -> None:
    respx.delete(f"{GITEA}/api/v1/repos/deployer/seckill-lab").mock(
        return_value=httpx.Response(404, json={})
    )
    delete_repo(GITEA, "TOK", "deployer", "seckill-lab")  # 不抛就是通过


# --- 编排：顺序是这条路的全部难点 --------------------------------------------------


@respx.mock
def test_load_track_pushes_upstream_before_creating_the_fork(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """fork 是**创建那一刻**的快照：先建 fork 再推分支，fork 里就永远缺新阶段。

    这条用真 git 跑 push（远端是本地裸仓），把"上游推完才建 fork"的顺序钉住——顺序错了，
    症状是 provision 报 workspace_ref_missing，而那时没人会想到是启动顺序的问题。
    """
    config = _config(tmp_path)
    bare = tmp_path / "upstream.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(config.content_dir), str(bare)], check=True)
    fork = tmp_path / "fork.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(config.content_dir), str(fork)], check=True)
    events: list[str] = []

    respx.get(f"{GITEA}/api/healthz").mock(
        return_value=httpx.Response(200, json={"status": "pass"})
    )
    respx.get(f"{GITEA}/api/v1/user").mock(return_value=httpx.Response(200, json={"login": "x"}))
    respx.post(f"{GITEA}/api/v1/admin/users").mock(return_value=httpx.Response(201, json={}))
    respx.post(f"{GITEA}/api/v1/users/trackowner/tokens").mock(
        return_value=httpx.Response(201, json={"sha1": "OWNER"})
    )
    respx.post(f"{GITEA}/api/v1/users/deployer/tokens").mock(
        return_value=httpx.Response(201, json={"sha1": "LEARNER"})
    )
    respx.get(f"{GITEA}/api/v1/repos/trackowner/seckill-lab").mock(
        return_value=httpx.Response(200, json={})
    )
    respx.get(f"{GITEA}/api/v1/repos/deployer/seckill-lab").mock(
        return_value=httpx.Response(200, json={"default_branch": "main"})
    )

    def fake_push(content_dir: Path, push_url: str, *, force: bool = True) -> list[str]:
        events.append("push:" + ("upstream" if push_url == str(bare) else "fork"))
        subprocess.run(
            ["git", "-C", str(content_dir), "push", "--quiet", "--force", push_url,
             "+refs/heads/*:refs/heads/*"],
            check=True, capture_output=True,
        )
        return local_branches(content_dir)

    # 把"要 push 到哪里"换成两个本地裸仓：token_push_url 是唯一决定这件事的地方，
    # 于是后面的 ls-remote / push 全都是真 git，不需要网络。
    monkeypatch.setattr(
        "platform_api.track_loader.token_push_url",
        lambda url, owner, name, tok: str(bare) if owner == "trackowner" else str(fork),
    )
    monkeypatch.setattr("platform_api.track_loader.push_all_branches", fake_push)
    monkeypatch.setattr("platform_api.track_loader.ensure_fork", lambda *a, **k: True)
    # fork 本就 clone 自内容仓 ⇒ 它已经"同步好了"（模拟 Gitea 侧异步 fork 已完成）
    result = load_track(config, log=lambda *_: None)

    assert result.branches == ["main", "stage-01", "stage-01-sol"]
    assert result.fork == "deployer/seckill-lab"
    # 上游推了一次；fork 侧因为 git 里已经能看到全部分支，**不再重复推**
    assert [e for e in events if e.startswith("push")] == ["push:upstream"]
    assert result.token_path is not None
    assert result.token_path.read_text(encoding="utf-8").strip() == "LEARNER"
    # fork 里必须也有全部阶段分支（这就是"补推"的意义）
    out = subprocess.run(
        ["git", "-C", str(fork), "for-each-ref", "--format=%(refname:short)", "refs/heads"],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    assert sorted(out) == ["main", "stage-01", "stage-01-sol"]


@respx.mock
def test_load_track_backfills_an_old_fork_and_fails_loudly_if_it_cannot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """旧 fork 缺分支时要**补推**；补不进去就报清楚（"用 BAGU_FORK_RESET=1 重建"）。

    这条对付的是真实处境：fork 是创建那一刻的快照，Track 后来长了新阶段，旧 fork 里就没有——
    症状是"看得到题、拉不出工作区"。别让它悄悄过去。
    """
    config = _config(tmp_path)
    bare = tmp_path / "upstream.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(config.content_dir), str(bare)], check=True)
    # 旧 fork：只 clone 了 main（缺 stage-01 / stage-01-sol）
    stale = tmp_path / "stale-fork"
    stale.mkdir()
    _git(stale, "init", "-q", "-b", "main")
    _git(stale, "config", "user.email", "t@example.com")
    _git(stale, "config", "user.name", "t")
    _git(stale, "commit", "-q", "--allow-empty", "-m", "stale")
    fork = tmp_path / "fork.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(stale), str(fork)], check=True)

    respx.get(f"{GITEA}/api/healthz").mock(
        return_value=httpx.Response(200, json={"status": "pass"})
    )
    respx.get(f"{GITEA}/api/v1/user").mock(return_value=httpx.Response(200, json={"login": "x"}))
    respx.post(f"{GITEA}/api/v1/admin/users").mock(return_value=httpx.Response(201, json={}))
    respx.post(f"{GITEA}/api/v1/users/trackowner/tokens").mock(
        return_value=httpx.Response(201, json={"sha1": "OWNER"})
    )
    respx.post(f"{GITEA}/api/v1/users/deployer/tokens").mock(
        return_value=httpx.Response(201, json={"sha1": "LEARNER"})
    )
    respx.get(f"{GITEA}/api/v1/repos/trackowner/seckill-lab").mock(
        return_value=httpx.Response(200, json={})
    )
    respx.get(f"{GITEA}/api/v1/repos/deployer/seckill-lab").mock(
        return_value=httpx.Response(200, json={"default_branch": "main"})
    )
    monkeypatch.setattr(
        "platform_api.track_loader.token_push_url",
        lambda url, owner, name, tok: str(bare) if owner == "trackowner" else str(fork),
    )
    monkeypatch.setattr("platform_api.track_loader.ensure_fork", lambda *a, **k: True)

    result = load_track(config, log=lambda *_: None)
    assert remote_branches(str(fork)) == ["main", "stage-01", "stage-01-sol"], "旧 fork 该被补齐"
    assert result.branches == ["main", "stage-01", "stage-01-sol"]


@respx.mock
def test_load_track_warns_about_a_loopback_gitea_url(tmp_path: Path) -> None:
    """容器里的 127.0.0.1 是它自己：这个坑要在启动时喊，而不是等 provision 超时。"""
    config = _config(tmp_path, gitea_url="http://127.0.0.1:3000")
    lines: list[str] = []
    respx.get("http://127.0.0.1:3000/api/healthz").mock(
        return_value=httpx.Response(200, json={"status": "pass"})
    )
    respx.get("http://127.0.0.1:3000/api/v1/user").mock(
        return_value=httpx.Response(401, json={})
    )
    with pytest.raises(LoadError):
        load_track(config, log=lines.append)
    assert any("127.0.0.1" in line for line in lines)
