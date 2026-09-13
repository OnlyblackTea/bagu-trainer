"""StubForgeClient：按 Track 声明的 stage 分支 clone、diff 非空、PR 占位 URL。

ref 必须**由内容侧声明**（``stage_refs``：``identity.id`` → 阶段名），占位 Forge 不再拿
``ticket_id`` 当分支名——§C.5 的分支是按阶段命名的。缺 ref 时要在 provision 阶段就报
``WorkspaceRefMissingError``（点明缺哪个、现有哪些），而不是留给 git diff 抛非零退出。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from platform_service.forge import ForgeClient, Workspace, WorkspaceRefMissingError
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
