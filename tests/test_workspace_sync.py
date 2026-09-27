"""工作区与远端 ``dev`` 的对账：**代码写在别的机器上也可以**。

反馈原话是"工作区在其他机器也完全可以啊，只要 git 能推送后端能收到就 ok"。对，而且这条得由平台
兜住：学员在笔记本上 commit + push 之后，后端那份 checkout 是**旧**的，直接拿去阅卷就会给出一个
看起来像"你代码没过"的结论。

所以这里有两条必须成立的性质：

* 无损时（工作树干净、本地不领先、就在 ``dev`` 上）自动快进——"在别处写"这条路上零额外动作；
* 其余情况**一个字都不动**他的分支，只如实报数（未提交改动永远不该被平台的 fetch 冲掉）。

全部用真 git 跑（假 Forge、真仓库），因为要验的正是 git 的行为本身。
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from platform_service.forge import DEV_BRANCH, Workspace, git, sync_workspace

# --- 真 git 的场地 --------------------------------------------------------------


def _init_repo(path: Path) -> None:
    """建一个有初始提交的普通仓库。

    ``commit.gpgsign false`` 不是可选的：本机全局开着签名时 ``git commit`` 会去弹 pinentry，
    在无人值守的测试里表现为卡一分钟后失败 128——一个跟被测代码毫无关系的红（踩过）。
    """
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.email", "t@example.com")
    git(path, "config", "user.name", "t")
    git(path, "config", "commit.gpgsign", "false")
    (path / "a.txt").write_text("v1\n", encoding="utf-8")
    git(path, "add", "-A")
    git(path, "commit", "-qm", "init")


def _remote(tmp_path: Path) -> Path:
    """一个**裸**仓当远端（Gitea 的仓库就是裸的）。"""
    src = tmp_path / "upstream-src"
    _init_repo(src)
    bare = tmp_path / "fork.git"
    git(tmp_path, "clone", "-q", "--bare", str(src), str(bare))
    return bare


def _checkout(tmp_path: Path, remote: Path, name: str) -> Path:
    """从远端 clone 一份（可能是"后端那份"，也可能是"学员的另一台机器"）。"""
    dest = tmp_path / name
    git(tmp_path, "clone", "-q", str(remote), str(dest))
    git(dest, "config", "user.email", "t@example.com")
    git(dest, "config", "user.name", "t")
    git(dest, "config", "commit.gpgsign", "false")
    return dest


def _dev_branch(repo: Path) -> Path:
    git(repo, "checkout", "-q", "-b", DEV_BRANCH, "origin/main")
    return repo


def _commit_and_push(repo: Path, body: str, message: str) -> str:
    (repo / "a.txt").write_text(body, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", message)
    git(repo, "push", "-q", "origin", f"{DEV_BRANCH}:{DEV_BRANCH}")
    return git(repo, "rev-parse", "HEAD").stdout.strip()


def _commit(repo: Path, body: str, message: str) -> str:
    (repo / "a.txt").write_text(body, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", message)
    return git(repo, "rev-parse", "HEAD").stdout.strip()


def _head(repo: Path) -> str:
    return git(repo, "rev-parse", "HEAD").stdout.strip()


def _backend_and_laptop(tmp_path: Path) -> tuple[Path, Path, Path]:
    """建出"后端那份 checkout"和"学员笔记本那份"，返回 (远端, 后端, 笔记本)。"""
    remote = _remote(tmp_path)
    backend = _dev_branch(_checkout(tmp_path, remote, "backend"))
    laptop = _dev_branch(_checkout(tmp_path, remote, "laptop"))
    return remote, backend, laptop


# --- sync_workspace：单元 -------------------------------------------------------


def test_push_from_another_machine_fast_forwards_the_backend_checkout(
    tmp_path: Path,
) -> None:
    """核心场景：笔记本上 push，后端 clean 且没领先 ⇒ 自动快进到那次 push。"""
    _remote_path, backend, laptop = _backend_and_laptop(tmp_path)
    pushed = _commit_and_push(laptop, "from the laptop\n", "laptop work")

    sync = sync_workspace(backend)

    assert sync.fetched is True
    assert sync.fast_forwarded is True
    assert (sync.local_ahead, sync.remote_ahead) == (0, 0)
    assert sync.revision == pushed
    # 真的落到工作树里了（不是只动了 ref）——阅卷跑的就是这份文件
    assert (backend / "a.txt").read_text(encoding="utf-8") == "from the laptop\n"


def test_probe_reports_but_never_moves_the_branch(tmp_path: Path) -> None:
    """``GET diff`` 用的只读探针：报得出"远端比你新"，但绝不动分支。"""
    _remote_path, backend, laptop = _backend_and_laptop(tmp_path)
    before = _head(backend)
    _commit_and_push(laptop, "from the laptop\n", "laptop work")

    sync = sync_workspace(backend, apply=False)

    assert sync.remote_ahead == 1
    assert sync.remote_only_changes is True
    assert sync.fast_forwarded is False
    assert _head(backend) == before  # 分支原地不动
    assert (backend / "a.txt").read_text(encoding="utf-8") == "v1\n"


def test_uncommitted_work_is_never_flushed_away_by_a_fetch(tmp_path: Path) -> None:
    """工作区有未提交改动 ⇒ 不快进（那会把学员正在写的东西冲掉，或造出冲突）。"""
    _remote_path, backend, laptop = _backend_and_laptop(tmp_path)
    (backend / "scratch.txt").write_text("正在写\n", encoding="utf-8")
    before = _head(backend)
    _commit_and_push(laptop, "from the laptop\n", "laptop work")

    sync = sync_workspace(backend)

    assert sync.dirty is True
    assert sync.remote_ahead == 1
    assert sync.fast_forwarded is False
    assert _head(backend) == before
    assert (backend / "scratch.txt").read_text(encoding="utf-8") == "正在写\n"
    assert "未提交" in sync.note


def test_local_commits_not_yet_pushed_are_reported(tmp_path: Path) -> None:
    """本地领先远端：这次按本地内容走，但开 PR 前必须 push——文案要说到这件事。"""
    _remote_path, backend, _laptop = _backend_and_laptop(tmp_path)
    _commit_and_push(backend, "local only\n", "local work")
    _commit(backend, "local only v2\n", "local work v2")

    sync = sync_workspace(backend)

    assert (sync.local_ahead, sync.remote_ahead) == (1, 0)
    assert sync.fast_forwarded is False
    assert "push" in sync.note


def test_diverged_branches_are_left_for_the_learner(tmp_path: Path) -> None:
    """两边分叉：平台**不替学员合并**（那会把他的历史改成平台想要的样子）。"""
    _remote_path, backend, laptop = _backend_and_laptop(tmp_path)
    # 笔记本先 push，后端在本地另起一条——两边各有对方没有的提交。
    _commit_and_push(laptop, "laptop line\n", "laptop work")
    _commit(backend, "backend line\n", "backend work")

    sync = sync_workspace(backend)

    assert (sync.local_ahead, sync.remote_ahead) == (1, 1)
    assert sync.fast_forwarded is False
    assert "没有替你合并" in sync.note


def test_no_remote_branch_means_the_learner_has_not_pushed(tmp_path: Path) -> None:
    """远端还没有 ``dev``：先在本地 commit + push，PR 才有东西可开。"""
    _remote_path, backend, _laptop = _backend_and_laptop(tmp_path)

    sync = sync_workspace(backend)

    assert sync.remote_branch is False
    assert sync.remote_ahead == 0
    assert "还没 push" in sync.note


def test_unreachable_remote_is_reported_not_silently_treated_as_latest(
    tmp_path: Path,
) -> None:
    """远端读不到时**不能**说"已是最新"——那会把"不知道"伪装成"没问题"。"""
    _remote_path, backend, laptop = _backend_and_laptop(tmp_path)
    _commit_and_push(laptop, "from the laptop\n", "laptop work")
    sync_workspace(backend, apply=False)  # 先成功 fetch 一次，让 origin/dev 落在本地
    git(backend, "remote", "set-url", "origin", str(tmp_path / "gone.git"))

    sync = sync_workspace(backend)

    assert sync.fetched is False
    assert "fetch 失败" in sync.note


# --- 路由：/sync、/diff、submit --------------------------------------------------


def _real_forge_app(tmp_path: Path, workspace: Path) -> tuple[Any, Any]:
    """把 demo app 的 forge 换成"指向一个真 git 工作区"的假实现（假 Forge、真 git）。"""
    from fastapi.testclient import TestClient

    from platform_api.app import create_app
    from platform_api.demo_repo import build_demo_track_repo

    repo = build_demo_track_repo(tmp_path / "demo")
    app = create_app(
        content_dir=repo,
        forge_base=tmp_path / "ws",
        store_backend="sqlite",
        database_url=str(tmp_path / "bagu.sqlite3"),
    )
    ws = Workspace(
        path=workspace,
        branch=DEV_BRANCH,
        base_ref="origin/main",
        head_ref=DEV_BRANCH,
        simulated=False,
        diff_head_ref=None,
        push_hint="git add -A && git commit -m x && git push -u origin dev",
    )

    class _RealForge:
        def provision(self, instance: Any) -> Workspace:
            return ws

    bundle = app.state.tracks["database-bagu"]
    app.state.tracks["database-bagu"] = replace(bundle, forge=_RealForge())
    return app, TestClient(app)


def _enrolled_started(client: Any) -> str:
    client.post("/enrollments", json={"learner": "deployer"})
    instances = client.get("/instances", params={"learner": "deployer"}).json()
    stage00 = next(i for i in instances if i["ticket_id"] == "db-stage-00")
    client.post(f"/instances/{stage00['id']}/start")
    return stage00["id"]


def test_sync_endpoint_fast_forwards_and_says_so(tmp_path: Path) -> None:
    _remote_path, backend, laptop = _backend_and_laptop(tmp_path)
    pushed = _commit_and_push(laptop, "from the laptop\n", "laptop work")
    _app, client = _real_forge_app(tmp_path, backend)
    iid = _enrolled_started(client)

    body = client.post(f"/instances/{iid}/sync").json()

    assert body["sync"]["fast_forwarded"] is True
    assert body["sync"]["revision"] == pushed
    assert body["sync"]["remote_only_changes"] is False
    assert (backend / "a.txt").read_text(encoding="utf-8") == "from the laptop\n"


def test_diff_warns_that_the_backend_checkout_is_behind(tmp_path: Path) -> None:
    """学员在别处 push 完回来看 diff：绝不能显示"没有改动"而不解释为什么。"""
    _remote_path, backend, laptop = _backend_and_laptop(tmp_path)
    before = _head(backend)
    _commit_and_push(laptop, "from the laptop\n", "laptop work")
    _app, client = _real_forge_app(tmp_path, backend)
    iid = _enrolled_started(client)

    body = client.get(f"/instances/{iid}/diff").json()

    assert body["sync"]["remote_only_changes"] is True
    assert body["sync"]["remote_ahead"] == 1
    assert "from the laptop" not in body["diff"]  # 这份 checkout 确实还没有那次改动
    assert _head(backend) == before  # 而读接口没有偷偷动分支


def test_submit_syncs_first_so_grading_sees_the_pushed_code(tmp_path: Path) -> None:
    _remote_path, backend, laptop = _backend_and_laptop(tmp_path)
    pushed = _commit_and_push(laptop, "from the laptop\n", "laptop work")
    _app, client = _real_forge_app(tmp_path, backend)
    iid = _enrolled_started(client)

    r = client.post(f"/instances/{iid}/submit", params={"scenario": "pass"}, json={})

    assert r.status_code == 202, r.text
    sync = r.json()["sync"]
    assert sync["fast_forwarded"] is True and sync["revision"] == pushed
    # 阅卷是在工作区里跑的 ⇒ 这一刻它必须是学员 push 的那一版
    assert (backend / "a.txt").read_text(encoding="utf-8") == "from the laptop\n"


def test_stub_workspace_reports_no_sync_at_all(client: Any) -> None:
    """桩工作区没有远端 ⇒ ``null``，而不是一个"已是最新"的假状态。"""
    iid = _enrolled_started(client)

    assert client.post(f"/instances/{iid}/sync").json()["sync"] is None
    assert client.get(f"/instances/{iid}/diff").json()["sync"] is None


def test_sync_unknown_instance_404(client: Any) -> None:
    assert client.post("/instances/nope/sync").status_code == 404


@pytest.mark.parametrize("apply", [True, False])
def test_sync_failure_is_not_a_500(tmp_path: Path, apply: bool) -> None:
    """Forge 不可达时对账失败只意味着"不知道"，不该把提交/预览整个打挂。"""
    cleanup = tmp_path / "ws-gone"
    cleanup.mkdir()
    git(cleanup, "init", "-q", "-b", DEV_BRANCH)

    sync = sync_workspace(cleanup, apply=apply)

    assert sync.fetched is False
    assert sync.revision is not None
