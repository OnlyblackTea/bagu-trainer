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
    ensure_default_branch,
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
    assert config.learner_write is True

    with pytest.raises(LoadError, match="git 仓库"):
        LoaderConfig.from_env({**env, "BAGU_CONTENT_DIR": str(tmp_path)})

    with pytest.raises(LoadError, match="BAGU_GITEA_URL"):
        LoaderConfig.from_env({**env, "BAGU_GITEA_URL": ""})


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
def test_learner_write_can_be_turned_off(tmp_path: Path) -> None:
    env = {
        "BAGU_GITEA_URL": GITEA,
        "BAGU_CONTENT_DIR": str(_content_repo(tmp_path, ["main"])),
    }
    assert LoaderConfig.from_env(env).learner_write is True  # 默认开
    assert LoaderConfig.from_env({**env, "BAGU_LEARNER_WRITE": "0"}).learner_write is False


@respx.mock
# --- 编排：顺序是这条路的全部难点 --------------------------------------------------


@respx.mock
def test_load_track_pushes_branches_then_grants_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """编排：建仓 → **推全部分支** → 给学员写权限 → **保护内容分支** → 落令牌。

    没有 fork 之后，"学员能 push 到内容仓"是整条路的命脉（后端也从这个仓 clone）。漏了授权，
    学员第一次 push 就被拒，而平台侧一切正常——所以这里既验授权被调用，也验它**在推完分支之后**
    （仓刚建出来时授权没有意义）。

    保护同样必须钉住：授权之后学员对**所有**分支都有写权限，而 ``stage-*`` 是平台的基线——
    他推上去一次（手滑的 `git push origin --all` 就够），平台就会拿他刚写的东西当"内容作者的
    版本"，diff 与判定面当场失真。真踩过。
    """
    config = _config(tmp_path)
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(config.content_dir), str(bare)], check=True)
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
    grant = respx.put(
        f"{GITEA}/api/v1/repos/trackowner/seckill-lab/collaborators/deployer"
    ).mock(return_value=httpx.Response(204))
    protection = respx.post(
        f"{GITEA}/api/v1/repos/trackowner/seckill-lab/branch_protections"
    ).mock(return_value=httpx.Response(201, json={}))

    # 把 push 目标换成本地裸仓：真 git push，不需要网络
    def fake_push(content_dir: Path, push_url: str, *, force: bool = True) -> list[str]:
        events.append("push")
        subprocess.run(
            ["git", "-C", str(content_dir), "push", "--quiet", "--force", str(bare),
             "+refs/heads/*:refs/heads/*"],
            check=True, capture_output=True,
        )
        return local_branches(content_dir)

    monkeypatch.setattr("platform_api.track_loader.push_all_branches", fake_push)
    monkeypatch.setattr(
        "platform_api.track_loader.token_push_url", lambda url, owner, name, tok: str(bare)
    )

    result = load_track(config, log=lambda *_: None)

    assert result.branches == ["main", "stage-01", "stage-01-sol"]
    assert result.learner_has_write is True
    assert events == ["push"], "只该推一次（没有 fork 了）"
    assert grant.called, "必须给学员写权限——否则他 push 不上来"
    assert remote_branches(str(bare)) == ["main", "stage-01", "stage-01-sol"]
    assert result.token_path is not None
    assert result.token_path.read_text(encoding="utf-8").strip() == "LEARNER"

    # 保护：每条内容分支各一条规则 + `dev` 一条（共 3 内容分支 + 1）
    bodies = [json.loads(call.request.content) for call in protection.calls]
    by_branch = {b["branch_name"]: b for b in bodies}
    assert set(by_branch) == {"main", "stage-01", "stage-01-sol", "dev"}
    for name in ("main", "stage-01", "stage-01-sol"):
        rule = by_branch[name]
        assert rule["enable_push"] is True
        assert rule["push_whitelist_usernames"] == ["trackowner"], "内容分支只有内容所有者能推"
        assert rule["enable_force_push"] is True, "loader 自己每次都要强制对齐内容仓"
        assert rule["force_push_whitelist_usernames"] == ["trackowner"]
    dev = by_branch["dev"]
    assert dev["enable_push"] is True and dev["enable_push_whitelist"] is False, "dev 谁都能推"
    assert dev["enable_force_push"] is False, "但谁都不能 force——重写 dev 的代价见 load_track"
    assert sorted(result.protected_branches) == ["dev", "main", "stage-01", "stage-01-sol"]


@respx.mock
def test_protection_is_idempotent_and_never_fatal(tmp_path: Path) -> None:
    """保护是**加固**：已存在要说得出（Gitea 1.22 回 403 "already exist"），失败也不能把栈拖下水。

    `platform` 依赖 `track-loader` 成功退出。真踩过：第二次跑 loader 时 Gitea 对重复规则回 403，
    我把它当致命错误，于是 loader exit 1 —— platform 压根没被创建，界面全挂。
    """
    from platform_api.track_loader import protect_branches

    respx.post(f"{GITEA}/api/v1/repos/trackowner/track/branch_protections").mock(
        return_value=httpx.Response(
            403, json={"message": "Branch protection already exist"}
        )
    )
    lines: list[str] = []

    added = protect_branches(
        GITEA,
        "OWNER",
        "trackowner",
        "track",
        content_branches=["main", "stage-01"],
        allow_push="trackowner",
        log=lines.append,
    )

    assert added == [], "已经保护过 ⇒ 什么都不算新加"
    assert any("已就位" in line for line in lines)


@respx.mock
def test_protection_failure_is_a_warning_not_an_exception(tmp_path: Path) -> None:
    """真失败（比如 Gitea 权限不足）也只告警：不能让整栈起不来。"""
    from platform_api.track_loader import protect_branches

    respx.post(f"{GITEA}/api/v1/repos/trackowner/track/branch_protections").mock(
        return_value=httpx.Response(500, text="boom")
    )
    lines: list[str] = []

    added = protect_branches(
        GITEA,
        "OWNER",
        "trackowner",
        "track",
        content_branches=["main"],
        allow_push="trackowner",
        log=lines.append,
    )

    assert added == []
    assert any(line.startswith("   !! ") for line in lines), "要有显眼的告警"
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
