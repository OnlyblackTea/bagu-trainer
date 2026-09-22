"""create_app boot、app.state 装配齐全、异常处理器映射短码。"""

from __future__ import annotations

from dataclasses import replace
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
    bundle = app.state.tracks["database-bagu"]
    assert bundle.track.id == "database-bagu"
    assert set(bundle.tickets) == {"db-stage-00", "db-stage-01", "db-stage-02"}
    assert bundle.service is not None and bundle.forge is not None


def test_meta_reports_each_backend_honestly(tmp_path: Path) -> None:
    """前端据此决定"哪些环节是占位"——必须**逐项**如实。

    一个笼统的"模拟"徽标会把真阅卷（已经能真跑 maven）也标成假的（§15 风险 5）。
    """
    body = TestClient(_app(tmp_path)).get("/meta").json()

    assert body["platform_version"]
    assert body["grading_backend"] == "stub" and body["grading_reset"] is True
    assert body["forge_backend"] == "stub"


def test_unreachable_forge_is_503_not_a_bad_grade(tmp_path: Path, monkeypatch) -> None:
    """Gitea 连不上 ⇒ 503 + forge_unavailable，而不是把基础设施问题说成"你的代码没过"。"""
    monkeypatch.setenv("BAGU_GITEA_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("BAGU_GITEA_TOKEN", "tok")
    monkeypatch.setenv("BAGU_GITEA_UPSTREAM", "upstream/track")
    monkeypatch.setenv("BAGU_GITEA_OWNER", "deployer")
    repo = build_demo_track_repo(tmp_path / "demo")
    client = TestClient(
        create_app(content_dir=repo, forge_base=tmp_path / "ws", forge="gitea")
    )
    client.post("/enrollments", json={"learner": "deployer"})
    iid = next(
        i["id"]
        for i in client.get("/instances", params={"learner": "deployer"}).json()
        if i["ticket_id"] == "db-stage-00"
    )
    client.post(f"/instances/{iid}/start")

    r = client.post(f"/instances/{iid}/provision")

    assert r.status_code == 503 and r.json()["detail"] == "forge_unavailable"
    assert r.json()["reason"]


def test_missing_remote_branch_is_409_with_the_branch_name(tmp_path: Path) -> None:
    """学员还没 push ⇒ 409（预期内），并带上分支名——前端才能说清"先 push 哪个分支"。"""
    from platform_service.forge import RemoteBranchMissingError

    app = _app(tmp_path)
    client = TestClient(app)
    client.post("/enrollments", json={"learner": "deployer"})
    iid = next(
        i["id"]
        for i in client.get("/instances", params={"learner": "deployer"}).json()
        if i["ticket_id"] == "db-stage-00"
    )
    client.post(f"/instances/{iid}/start")

    class _NoBranchForge:
        """只在 create_pr 上不同：provision 仍走真桩（否则测不到路由 → 处理器的映射）。"""

        def __init__(self, inner) -> None:
            self._inner = inner

        def provision(self, instance):
            return self._inner.provision(instance)

        def create_pr(self, instance, *, base, head):
            raise RemoteBranchMissingError(repo="deployer/track", branch="dev")

    bundle = app.state.tracks["database-bagu"]
    app.state.tracks["database-bagu"] = replace(bundle, forge=_NoBranchForge(bundle.forge))

    r = client.post(f"/instances/{iid}/pr")

    assert r.status_code == 409
    assert r.json()["detail"] == "remote_branch_missing"
    assert r.json()["branch"] == "dev" and r.json()["repo"] == "deployer/track"


def test_create_app_exposes_stage_dir_index(tmp_path: Path) -> None:
    """看板按目录名书写，故需要目录名 → ticket 的索引（目录名不是身份）。"""
    app = _app(tmp_path)
    stages = app.state.tracks["database-bagu"].stage_tickets
    assert stages["stage-01-product-query-index"].identity.id == "db-stage-01"


def test_illegal_transition_maps_409(tmp_path: Path) -> None:
    app = _app(tmp_path)

    @app.get("/_boom_illegal")
    def _boom() -> None:
        raise IllegalTransitionError("nope")

    assert TestClient(app, raise_server_exceptions=False).get("/_boom_illegal").status_code == 409


def test_unexpected_value_error_is_500_not_a_fake_hint(tmp_path: Path) -> None:
    """裸 ValueError 不再伪装成"你还没评审"。

    以前 ValueError 被兜底映射成 ``decide_needs_report_and_review``：一个真正的编程错误会被
    说成"前置没齐"，学员照着提示做完还是过不去。现在它落成 500 ``internal_error``——
    仍然给短码，但**不假装知道原因**。
    """
    from platform_service.errors import GateNotReadyError, NotFoundError

    app = _app(tmp_path)

    @app.get("/_boom_value")
    def _boom() -> None:
        raise ValueError("some internal invariant broke")

    @app.get("/_boom_key")
    def _boom_key() -> None:
        raise KeyError("typo somewhere")  # 打错字的 KeyError 不再等于"资源不存在"

    @app.get("/_boom_not_found")
    def _boom_nf() -> None:
        raise NotFoundError("no such instance")

    @app.get("/_boom_gate")
    def _boom_gate() -> None:
        raise GateNotReadyError("needs report + review")

    client = TestClient(app, raise_server_exceptions=False)
    assert client.get("/_boom_value").status_code == 500
    assert client.get("/_boom_value").json()["detail"] == "internal_error"
    assert client.get("/_boom_key").status_code == 500
    assert client.get("/_boom_not_found").status_code == 404
    assert client.get("/_boom_not_found").json()["detail"] == "not_found"
    assert client.get("/_boom_gate").status_code == 409
    assert client.get("/_boom_gate").json()["detail"] == "decide_needs_report_and_review"


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
