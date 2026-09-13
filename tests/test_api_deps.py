"""build_service 从 demo repo 装配 PlatformService；桩工厂按 scenario 出对应后端。"""

from __future__ import annotations

from pathlib import Path

from platform_api.demo_repo import build_demo_track_repo
from platform_api.deps import PLATFORM_VERSION, build_service, grading_runner, review_engine
from platform_service.models import InstanceState
from platform_service.service import PlatformService
from platform_service.stubs import StubGradingRunner, StubReviewEngine


def test_build_service_wires_dag(tmp_path: Path) -> None:
    repo = build_demo_track_repo(tmp_path / "demo")
    store, track, tickets, service = build_service(repo)
    assert isinstance(service, PlatformService)
    assert track.id == "database-bagu"
    # 键是 identity.id（不是 stage 目录名），且 board-only stage 不产生 ticket。
    assert sorted(tickets) == ["db-stage-00", "db-stage-01", "db-stage-02"]
    enrollment = service.enroll("deployer")
    states = {i.ticket_id: i.state for i in store.list_instances(learner="deployer")}
    assert states["db-stage-00"] is InstanceState.AVAILABLE
    assert states["db-stage-01"] is InstanceState.LOCKED
    assert enrollment.track == track.id
    assert PLATFORM_VERSION


def test_build_service_does_not_require_a_ticket_per_manifest_stage(tmp_path: Path) -> None:
    """board-only stage 只在看板里；装配不得因为它缺 ticket 而失败（真 Track 同理）。"""
    repo = build_demo_track_repo(tmp_path / "demo")
    _, track, tickets, _ = build_service(repo)
    assert len(track.manifest.stages) > len(tickets)


def test_factories_pick_scenario() -> None:
    assert isinstance(grading_runner("pass"), StubGradingRunner)
    assert isinstance(review_engine("approve"), StubReviewEngine)
