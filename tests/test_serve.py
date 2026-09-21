"""``platform_api.serve`` —— 可启动入口：指定内容目录，或退回 demo Track。

spec §C.9 记的 ``uvicorn platform_api.app:app`` 跑不通（``create_app`` 需要 content_dir，
模块级没有 ``app``），这个入口就是修它。用 TestClient 验装配，不必真起 uvicorn。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from platform_api.demo_repo import build_demo_track_repo
from platform_api.serve import build_server


def test_build_server_uses_given_content_dir(tmp_path: Path) -> None:
    repo = build_demo_track_repo(tmp_path / "demo")
    client = TestClient(build_server(repo, forge_base=tmp_path / "ws"))
    r = client.get("/tracks")
    assert r.status_code == 200 and r.json()[0]["id"] == "database-bagu"


def test_build_server_defaults_to_a_demo_track(tmp_path: Path) -> None:
    """不给 content_dir 也要能起来——否则"手上还没 Track 仓库"就没法看效果。"""
    app = build_server(workspace_root=tmp_path / "root")
    client = TestClient(app)
    assert client.get("/tracks").status_code == 200
    # 发卷身份仍按 identity.id，而不是 stage 目录名
    assert client.get("/tickets/db-stage-00").status_code == 200
    assert client.get("/tickets/stage-00-onboarding").status_code == 404


def test_build_server_reuses_an_existing_demo_repo(tmp_path: Path) -> None:
    """重启开发服务器不能因为"内容没变、git 无话可提交"而崩。"""
    root = tmp_path / "root"
    first = build_server(workspace_root=root)
    second = build_server(workspace_root=root)  # 复用，不重建
    assert TestClient(first).get("/tracks").status_code == 200
    assert TestClient(second).get("/tracks").status_code == 200


def test_demo_repo_and_workspaces_live_under_the_workspace_root(tmp_path: Path) -> None:
    root = tmp_path / "root"
    build_server(workspace_root=root)
    assert (root / "demo" / "track.yaml").exists()
    assert (root / "ws").is_dir()


def test_stale_demo_repo_is_rebuilt_instead_of_crashing(tmp_path: Path) -> None:
    """内容 schema 变严后，复用旧的 demo 会让服务**启动时**抛一个毫不相干的 schema 错。

    复用 demo 的收益是"重启不崩"，不该反过来变成"改了 schema 就起不来"。对 demo（不是用户
    内容）来说，重建才是正确恢复——所以这里不静默降级，也不把锅甩给调用者。
    """
    root = tmp_path / "root"
    build_server(workspace_root=root)
    (root / "demo" / "track.yaml").write_text("track:\n  id: broken\n", encoding="utf-8")

    app = build_server(workspace_root=root)  # 应当重建，而不是抛 ContentValidationError

    assert TestClient(app).get("/tracks").status_code == 200


def test_main_parses_flags_without_starting_a_server(monkeypatch: Any, tmp_path: Path) -> None:
    """``main`` 的职责是解析参数 + 装配 app；真起 uvicorn 由 e2e 覆盖。"""
    import platform_api.serve as serve

    calls: dict[str, Any] = {}

    def fake_run(app: Any, *, host: str, port: int) -> None:
        calls["app"] = app
        calls["host"] = host
        calls["port"] = port

    monkeypatch.setattr("uvicorn.run", fake_run)
    repo = build_demo_track_repo(tmp_path / "demo")
    serve.main(["--content-dir", str(repo), "--host", "0.0.0.0", "--port", "8123"])

    assert calls["host"] == "0.0.0.0" and calls["port"] == 8123
    assert TestClient(calls["app"]).get("/tracks").status_code == 200
