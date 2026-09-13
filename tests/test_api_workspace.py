"""provision 返回工作区、diff 真本地 git 非空、pr 占位 URL；未知实例 → 404。"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def _enroll_start(client: Any) -> str:
    client.post("/enrollments", json={"learner": "deployer"})
    inst = client.get("/instances", params={"learner": "deployer"}).json()
    stage00 = next(i for i in inst if i["ticket_id"] == "db-stage-00")
    client.post(f"/instances/{stage00['id']}/start")
    return stage00["id"]


def test_provision_returns_workspace(client: Any) -> None:
    iid = _enroll_start(client)
    r = client.post(f"/instances/{iid}/provision")
    assert r.status_code == 200
    body = r.json()
    assert body["branch"] == "dev" and body["simulated"] is True
    assert body["base_ref"] == "origin/db-stage-00"


def test_diff_is_real_local_git(client: Any) -> None:
    iid = _enroll_start(client)
    client.post(f"/instances/{iid}/provision")
    r = client.get(f"/instances/{iid}/diff")
    assert r.status_code == 200
    body = r.json()
    assert "diff --git" in body["diff"] and body["total_chars"] > 0


def test_pr_is_placeholder(client: Any) -> None:
    iid = _enroll_start(client)
    r = client.post(f"/instances/{iid}/pr")
    assert r.status_code == 200
    assert r.json()["pr_url"].startswith("https://") and r.json()["simulated"] is True


def test_provision_unknown_instance_404(client: Any) -> None:
    assert client.post("/instances/nope/provision").status_code == 404


def test_diff_unknown_instance_404(client: Any) -> None:
    assert client.get("/instances/nope/diff").status_code == 404


def test_git_failure_maps_to_short_code_without_leaking_a_traceback(tmp_path: Path) -> None:
    """§C.8：错误一律 ``{"detail": "<短码>"}``，不泄露堆栈。

    git 失败（这里是 Track 的分支布局与占位 Forge 的 ``<ticket_id>``/``<ticket_id>-sol``
    约定不一致，即"该 ref 不存在"）不得以未映射的 500 裸奔出去。
    """
    import subprocess

    from fastapi.testclient import TestClient

    from platform_api.app import create_app
    from platform_api.demo_repo import build_demo_track_repo

    repo = build_demo_track_repo(tmp_path / "demo")
    subprocess.run(
        ["git", "branch", "-D", "db-stage-00-sol"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    app = create_app(content_dir=repo, forge_base=tmp_path / "ws")
    c = TestClient(app, raise_server_exceptions=False)
    c.post("/enrollments", json={"learner": "deployer"})
    iid = next(
        i["id"]
        for i in c.get("/instances", params={"learner": "deployer"}).json()
        if i["ticket_id"] == "db-stage-00"
    )
    c.post(f"/instances/{iid}/start")
    c.post(f"/instances/{iid}/provision")

    r = c.get(f"/instances/{iid}/diff")
    assert r.status_code == 500
    assert r.json()["detail"] == "git_command_failed"
