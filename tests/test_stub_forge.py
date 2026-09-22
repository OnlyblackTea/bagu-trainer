"""StubForgeClient：按 Track 声明的 stage 分支 clone、diff 非空、PR 占位 URL。

ref 必须**由内容侧声明**（``stage_refs``：``identity.id`` → 阶段名），占位 Forge 不再拿
``ticket_id`` 当分支名——§C.5 的分支是按阶段命名的。缺 ref 时要在 provision 阶段就报
``WorkspaceRefMissingError``（点明缺哪个、现有哪些），而不是留给 git diff 抛非零退出。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from platform_service.forge import (
    ForgeClient,
    Workspace,
    WorkspaceRefMissingError,
    _absolute_clone_url,
    git,
)
from platform_service.models import TicketInstance
from platform_service.review_diff import generate_diff
from platform_service.stubs import StubForgeClient

_SKELETON = "db-stage-00"
_SOLUTION = f"{_SKELETON}-sol"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True, encoding="utf-8"
    )


def _source_repo(path: Path) -> Path:
    """最小源仓库：main + 骨架分支 + 解法分支（分支名即"阶段名"）。"""
    path.mkdir(parents=True)
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "t@example.com")
    _git(path, "config", "user.name", "Tester")
    _git(path, "config", "commit.gpgsign", "false")
    (path / "f.txt").write_text("todo\n", encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-qm", "skeleton")
    _git(path, "branch", _SKELETON)
    _git(path, "checkout", "-q", "-b", _SOLUTION, _SKELETON)
    (path / "f.txt").write_text("solved\n", encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-qm", "solution")
    _git(path, "checkout", "-q", "main")
    return path


def _forge(tmp_path: Path, *, stage_refs: dict[str, str] | None = None) -> StubForgeClient:
    return StubForgeClient(
        source_repo=_source_repo(tmp_path / "src"),
        base_dir=tmp_path / "ws",
        stage_refs=stage_refs if stage_refs is not None else {_SKELETON: _SKELETON},
    )


def _instance(ticket_id: str = _SKELETON) -> TicketInstance:
    return TicketInstance(
        id=f"ti-{ticket_id}",
        learner="deployer",
        track="database-bagu",
        ticket_id=ticket_id,
        ticket_version=1,
        engine_version="a-prime-0.1.0",
        test_version=1,
    )


def test_provision_clones_and_resolves_stage_branch(tmp_path: Path) -> None:
    """身份与分支名可以是两套名字：分支取自 stage_refs 声明的阶段。"""
    forge = StubForgeClient(
        source_repo=_source_repo(tmp_path / "src"),
        base_dir=tmp_path / "ws",
        stage_refs={"db-stage-01": _SKELETON},  # identity.id 与阶段名不同
    )
    ws = forge.provision(_instance("db-stage-01"))
    assert isinstance(ws, Workspace)
    assert ws.path.exists() and (ws.path / "f.txt").exists()
    assert ws.base_ref == f"origin/{_SKELETON}"
    assert ws.head_ref == f"origin/{_SOLUTION}"
    diff = generate_diff(ws.path, base=ws.base_ref, head=ws.head_ref)
    assert "todo" in diff and "solved" in diff


def test_provision_is_idempotent(tmp_path: Path) -> None:
    forge = _forge(tmp_path)
    first = forge.provision(_instance())
    second = forge.provision(_instance())
    assert first.path == second.path  # 不重复 clone


def test_stub_has_no_dev_branch_and_no_push_path(tmp_path: Path) -> None:
    """桩**故意**不建 ``dev``、也不给 push 命令：它没有人真的写代码，没有可推的远端。

    （`dev` 的"从远端继续"逻辑因此归真 Forge 测——那才是学员真的会走的那条路。）
    """
    forge = _forge(tmp_path)
    ws = forge.provision(_instance())

    branches = git(ws.path, "for-each-ref", "--format=%(refname:short)", "refs/heads").stdout
    assert "dev" not in branches.split()
    assert ws.push_hint is None


def test_create_pr_returns_placeholder(tmp_path: Path) -> None:
    forge = _forge(tmp_path)
    inst = _instance()
    url = forge.create_pr(inst, base=f"origin/{_SKELETON}", head=f"origin/{_SOLUTION}")
    assert url.startswith("https://") and inst.id in url


def test_stub_structurally_satisfies_protocol(tmp_path: Path) -> None:
    forge = StubForgeClient(source_repo=tmp_path, base_dir=tmp_path / "ws", stage_refs={})
    assert isinstance(forge, ForgeClient)


# --- ref 缺失：在 provision 就报清楚，而不是等 git diff 非零退出 -----------------


def test_missing_stage_branch_is_reported_at_provision(tmp_path: Path) -> None:
    """Track 声明了一个仓库里不存在的阶段分支（例如把答案都塞在一个 solutions 分支）。"""
    forge = StubForgeClient(
        source_repo=_source_repo(tmp_path / "src"),
        base_dir=tmp_path / "ws",
        stage_refs={"db-stage-01": "stage-does-not-exist"},
    )
    with pytest.raises(WorkspaceRefMissingError) as exc:
        forge.provision(_instance("db-stage-01"))
    assert exc.value.ref == "origin/stage-does-not-exist"
    assert f"origin/{_SKELETON}" in exc.value.available  # 现有哪些 ref 一并给出，便于修内容


def test_ticket_without_a_declared_stage_is_rejected(tmp_path: Path) -> None:
    forge = StubForgeClient(
        source_repo=_source_repo(tmp_path / "src"), base_dir=tmp_path / "ws", stage_refs={}
    )
    with pytest.raises(WorkspaceRefMissingError) as exc:
        forge.provision(_instance("db-stage-09"))
    assert "db-stage-09" in str(exc.value)


# --- 相对路径：只在"第一次 clone"时发作的那一类 ---------------------------------


def test_provision_works_with_relative_source_and_dest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """相对源 + 相对目标：``git clone`` 的 cwd 是 ``dest.parent``，相对源会相对**那里**解析。

    于是 ``git clone src ws/deep/ti-x`` 从 ``ws/deep`` 出发去找 ``ws/deep/src``，128。
    这个缺陷只在**第一次** clone 时发作——工作区一旦留在磁盘上就永远走"已存在 ⇒ 跳过"，
    所以上面所有用绝对 ``tmp_path`` 的用例都照不出它来（e2e 里是"给每个 Track 分一层
    base_dir"把它翻出来的）。这里刻意 chdir 到 tmp_path 并给一个全新的深层 dest。
    """
    monkeypatch.chdir(tmp_path)
    _source_repo(tmp_path / "src")
    forge = StubForgeClient(
        source_repo=Path("src"),
        base_dir=Path("ws/nested/one-more-level"),
        stage_refs={_SKELETON: _SKELETON},
    )

    ws = forge.provision(_instance())

    assert (ws.path / "f.txt").exists()
    assert ws.path.is_absolute()


@pytest.mark.parametrize(
    "url",
    [
        "https://gitea.example/owner/repo.git",
        "ssh://git@gitea.example:2222/owner/repo.git",
        "git@gitea.example:owner/repo.git",
    ],
)
def test_remote_clone_urls_are_not_rewritten(url: str) -> None:
    """URL / scp 形式不是本地路径：硬套 ``Path.resolve()`` 会把它变成一个毫无意义的本地路径。"""
    assert _absolute_clone_url(url) == url


def test_local_clone_source_is_resolved_to_absolute(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert _absolute_clone_url("src") == str((tmp_path / "src").resolve())
