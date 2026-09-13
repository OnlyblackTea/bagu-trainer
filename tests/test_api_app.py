"""create_app boot、app.state 装配齐全、异常处理器映射短码。"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from platform_api.app import create_app
from platform_api.demo_repo import build_demo_track_repo
from platform_service.ai_review import ReviewDiffTooLargeError
from platform_service.lifecycle import IllegalTransitionError
from platform_service.review_diff import DiffVolume


def _app(tmp_path: Path) -> FastAPI:
    repo = build_demo_track_repo(tmp_path / "demo")
    return create_app(content_dir=repo, forge_base=tmp_path / "ws")


def test_create_app_wires_state(tmp_path: Path) -> None:
    app = _app(tmp_path)
    assert app.state.track.id == "database-bagu"
    assert set(app.state.tickets) == {"db-stage-00", "db-stage-01", "db-stage-02"}
    assert app.state.service is not None and app.state.forge is not None


def test_create_app_exposes_stage_dir_index(tmp_path: Path) -> None:
    """看板按目录名书写，故需要目录名 → ticket 的索引（目录名不是身份）。"""
    app = _app(tmp_path)
    assert app.state.stage_tickets["stage-01-product-query-index"].identity.id == "db-stage-01"


def test_illegal_transition_maps_409(tmp_path: Path) -> None:
    app = _app(tmp_path)

    @app.get("/_boom_illegal")
    def _boom() -> None:
        raise IllegalTransitionError("nope")

    assert TestClient(app, raise_server_exceptions=False).get("/_boom_illegal").status_code == 409


def test_value_error_maps_409(tmp_path: Path) -> None:
    app = _app(tmp_path)

    @app.get("/_boom_value")
    def _boom() -> None:
        raise ValueError("needs both")

    r = TestClient(app, raise_server_exceptions=False).get("/_boom_value")
    assert r.status_code == 409 and r.json()["detail"] == "decide_needs_report_and_review"


def test_key_error_maps_404(tmp_path: Path) -> None:
    app = _app(tmp_path)

    @app.get("/_boom_key")
    def _boom() -> None:
        raise KeyError("nope")

    r = TestClient(app, raise_server_exceptions=False).get("/_boom_key")
    assert r.status_code == 404 and r.json()["detail"] == "not_found"


def test_diff_too_large_maps_422(tmp_path: Path) -> None:
    app = _app(tmp_path)

    @app.get("/_boom_diff")
    def _boom() -> None:
        raise ReviewDiffTooLargeError(
            DiffVolume(
                total_chars=1,
                fits=False,
                first_file_overflows=True,
                truncated_files=("big.java",),
            )
        )

    r = TestClient(app, raise_server_exceptions=False).get("/_boom_diff")
    assert r.status_code == 422 and r.json()["detail"] == "diff_too_large"
