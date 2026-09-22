"""build_service 从 demo repo 装配 PlatformService；桩工厂按 scenario 出对应后端。"""

from __future__ import annotations

from pathlib import Path

import pytest

from platform_api.demo_repo import build_demo_track_repo
from platform_api.deps import (
    PLATFORM_VERSION,
    build_service,
    build_store,
    grading_runner,
    resolve_store_backend,
    review_engine,
    store_backend_name,
    store_target_name,
)
from platform_service.models import InstanceState
from platform_service.service import PlatformService
from platform_service.store import InMemoryStore, Store
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


def test_build_service_accepts_an_injected_store(tmp_path: Path) -> None:
    """缝的用处：换 store 不需要 PlatformService 或路由知情。"""
    repo = build_demo_track_repo(tmp_path / "demo")
    mine = InMemoryStore()
    store, _, _, service = build_service(repo, mine)
    assert store is mine
    service.enroll("deployer")
    assert len(mine.list_instances(learner="deployer")) == 3


def test_sqlite_is_the_zero_config_default(tmp_path: Path) -> None:
    """默认必须是**真持久**的后端：零配置能跑，且重启不失忆。

    内存后端做默认值的代价是"攒了一周的进度在重启后消失"，而 PostgreSQL 会把零配置的人挡在
    安装步骤上。SQLite 是标准库自带的，两头都不欠。
    """
    assert resolve_store_backend() == "sqlite"
    store = build_store("sqlite", dsn=str(tmp_path / "bagu.sqlite3"))
    assert store_backend_name(store) == "sqlite"
    assert store_target_name(store).endswith("bagu.sqlite3")
    assert isinstance(store, Store)


def test_memory_store_is_available_but_not_the_default() -> None:
    """内存后端还在（跑测试、临时演示），但它必须**显式**选——不能再是默认值。"""
    assert isinstance(build_store("memory"), InMemoryStore)
    assert store_backend_name(build_store("memory")) == "memory"
    assert "失忆" in store_target_name(build_store("memory"))


def test_store_backend_rejects_typos(monkeypatch: pytest.MonkeyPatch) -> None:
    """配错的存储后端必须现在炸，而不是等学员交卷时才发现进度没地方存。"""
    monkeypatch.setenv("BAGU_STORE", "postgress")
    with pytest.raises(ValueError, match="BAGU_STORE"):
        resolve_store_backend()
    assert resolve_store_backend("memory") is not None  # 显式参数压过坏环境变量


def test_postgres_backend_without_dsn_fails_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BAGU_DATABASE_URL", raising=False)
    from platform_api.store_postgres import StoreUnavailableError

    with pytest.raises(StoreUnavailableError):
        build_store("postgres")


def test_build_store_satisfies_the_seam(monkeypatch: pytest.MonkeyPatch) -> None:
    assert isinstance(build_store(), Store)
