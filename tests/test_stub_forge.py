"""StubForgeClient：clone 占位 fork、diff 非空、PR 占位 URL。源仓库用本地 git fixture。"""

from __future__ import annotations

import subprocess
from pathlib import Path

from platform_service.forge import ForgeClient, Workspace
from platform_service.models import TicketInstance
from platform_service.review_diff import generate_diff
from platform_service.stubs import StubForgeClient


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True, encoding="utf-8"
    )


def _source_repo(path: Path) -> Path:
    """最小源仓库：main + 骨架分支 db-stage-00 + 解法分支 db-stage-00-sol。"""
    path.mkdir(parents=True)
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "t@example.com")
    _git(path, "config", "user.name", "Tester")
    _git(path, "config", "commit.gpgsign", "false")
    (path / "f.txt").write_text("todo\n", encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-qm", "skeleton")
    _git(path, "branch", "db-stage-00")
    _git(path, "checkout", "-q", "-b", "db-stage-00-sol", "db-stage-00")
    (path / "f.txt").write_text("solved\n", encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-qm", "solution")
    _git(path, "checkout", "-q", "main")
    return path


def _instance(ticket_id: str = "db-stage-00") -> TicketInstance:
    return TicketInstance(
        id=f"ti-{ticket_id}",
        learner="deployer",
        ticket_id=ticket_id,
        ticket_version=1,
        engine_version="a-prime-0.1.0",
        test_version=1,
    )


def test_provision_clones_and_diff_is_nonempty(tmp_path: Path) -> None:
    source = _source_repo(tmp_path / "src")
    forge = StubForgeClient(source_repo=source, base_dir=tmp_path / "ws")
    ws = forge.provision(_instance())
    assert isinstance(ws, Workspace)
    assert ws.path.exists() and (ws.path / "f.txt").exists()
    assert ws.base_ref == "origin/db-stage-00"
    assert ws.head_ref == "origin/db-stage-00-sol"
    diff = generate_diff(ws.path, base=ws.base_ref, head=ws.head_ref)
    assert "todo" in diff and "solved" in diff


def test_provision_is_idempotent(tmp_path: Path) -> None:
    source = _source_repo(tmp_path / "src")
    forge = StubForgeClient(source_repo=source, base_dir=tmp_path / "ws")
    first = forge.provision(_instance())
    second = forge.provision(_instance())
    assert first.path == second.path  # 不重复 clone


def test_create_pr_returns_placeholder(tmp_path: Path) -> None:
    source = _source_repo(tmp_path / "src")
    forge = StubForgeClient(source_repo=source, base_dir=tmp_path / "ws")
    inst = _instance()
    url = forge.create_pr(inst, base="origin/db-stage-00", head="origin/db-stage-00-sol")
    assert url.startswith("https://") and inst.id in url


def test_stub_structurally_satisfies_protocol(tmp_path: Path) -> None:
    forge = StubForgeClient(source_repo=tmp_path, base_dir=tmp_path / "ws")
    assert isinstance(forge, ForgeClient)
