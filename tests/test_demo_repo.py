"""demo track-repo：内容可被 content_loader 加载、阶段分支齐全、diff 非空。

Fixture 刻意**镜像真 Track（seckill-lab）的形状**，而不是让目录名、`identity.id`、
`manifest.stages` 三者同名——那样会掩盖 Task 0 修掉的那个静默失效的解锁门：

* 目录名是描述式的（``stage-01-product-query-index``），``identity.id`` 是 ``db-stage-01``；
* 有一个 stage **只上榜、不出卷**（``stage-03-deadlock-optional``），断言它不会被铸实例；
* 因此这个 fixture 是契约的**回归护栏**：谁再把 identity 与目录名或清单顺序耦合起来，这里就会红。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from platform_api.demo_repo import (
    BOARD_ONLY_STAGES,
    DEMO_STAGES,
    build_demo_track_repo,
)
from platform_service.admission import validate_track
from platform_service.content_loader import load_tickets, load_track
from platform_service.models import InstanceState, TicketInstance
from platform_service.review_diff import generate_diff
from platform_service.service import PlatformService
from platform_service.store import InMemoryStore
from platform_service.stubs import StubForgeClient


def _branches(repo: Path) -> set[str]:
    out = subprocess.run(
        ["git", "branch", "--format=%(refname:short)"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return set(out.stdout.split())


def _instance(ticket_id: str) -> TicketInstance:
    return TicketInstance(
        id="ti-1",
        learner="deployer",
        ticket_id=ticket_id,
        ticket_version=1,
        engine_version="a-prime-0.1.0",
        test_version=1,
    )


# --- 内容 ---------------------------------------------------------------------


def test_content_loads(tmp_path: Path) -> None:
    repo = build_demo_track_repo(tmp_path / "demo")
    track = load_track(repo / "track.yaml")
    assert track.manifest.stages == list(DEMO_STAGES)
    tickets = load_tickets(repo)
    assert sorted(tickets) == ["db-stage-00", "db-stage-01", "db-stage-02"]
    assert tickets["db-stage-00"].progression.prerequisites == []
    assert tickets["db-stage-01"].progression.prerequisites == ["db-stage-00"]
    assert tickets["db-stage-02"].progression.prerequisites == ["db-stage-01"]


def test_stage_dirs_diverge_from_identity_ids(tmp_path: Path) -> None:
    """护栏：目录名不等于 identity.id，正是真 Track 的形状。"""
    repo = build_demo_track_repo(tmp_path / "demo")
    tickets = load_tickets(repo)
    assert "stage-01-product-query-index" not in tickets
    assert (repo / "stages" / "stage-01-product-query-index" / "ticket.yaml").exists()


def test_board_only_stage_ships_no_ticket(tmp_path: Path) -> None:
    """只上榜、不出卷的 stage：在 manifest 里，但没有 ticket，也不该被铸实例。"""
    repo = build_demo_track_repo(tmp_path / "demo")
    assert BOARD_ONLY_STAGES, "fixture 必须至少有一个 board-only stage"
    for stage in BOARD_ONLY_STAGES:
        assert stage in DEMO_STAGES
        assert not (repo / "stages" / stage / "ticket.yaml").exists()
        assert stage not in load_tickets(repo)


def test_demo_repo_passes_admission(tmp_path: Path) -> None:
    """demo fixture 自己是准入干净的（含 manifest_coverage 与 bagu_map 锚点）。"""
    report = validate_track(build_demo_track_repo(tmp_path / "demo"))
    assert report.ok, [c.message for c in report.checks if not c.passed]


# --- git 分支 -----------------------------------------------------------------


def test_stage_branches_exist(tmp_path: Path) -> None:
    repo = build_demo_track_repo(tmp_path / "demo")
    branches = _branches(repo)
    for ticket_id in ("db-stage-00", "db-stage-01", "db-stage-02"):
        assert ticket_id in branches and f"{ticket_id}-sol" in branches


def test_provision_diff_nonempty(tmp_path: Path) -> None:
    repo = build_demo_track_repo(tmp_path / "demo")
    forge = StubForgeClient(source_repo=repo, base_dir=tmp_path / "ws")
    ws = forge.provision(_instance("db-stage-00"))
    assert generate_diff(ws.path, base=ws.base_ref, head=ws.head_ref).strip()


# --- DAG 行为（Task 0 的核心回归） ---------------------------------------------


def test_enroll_locks_dependents(tmp_path: Path) -> None:
    """按真 Track 形状装出来的服务，前置门必须真的生效。"""
    repo = build_demo_track_repo(tmp_path / "demo")
    track = load_track(repo / "track.yaml")
    store = InMemoryStore()
    PlatformService(store, track, load_tickets(repo), engine_version="a-prime-0.1.0").enroll(
        "deployer"
    )
    states = {i.ticket_id: i.state for i in store.list_instances(learner="deployer")}
    assert states == {
        "db-stage-00": InstanceState.AVAILABLE,
        "db-stage-01": InstanceState.LOCKED,
        "db-stage-02": InstanceState.LOCKED,
    }
