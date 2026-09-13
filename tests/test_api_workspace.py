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
    # §C.5：base 是该 ticket 所属**阶段**的分支，不是 ticket id。
    assert body["base_ref"] == "origin/stage-00-onboarding"


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


def test_missing_stage_branch_maps_to_short_code_without_leaking_a_traceback(
    tmp_path: Path,
) -> None:
    """§C.8：错误一律 ``{"detail": "<短码>"}``，不泄露堆栈。

    这里模拟内容侧的配置错误：Track 声明的阶段分支在 track-repo 里不存在（真 Track 样板
    把答案都塞在单一 ``solutions`` 分支上就是这个症状，不符合 §C.5）。应当在 provision
    就返回 ``workspace_ref_missing`` 并带上缺失的 ref，而不是等到 diff 抛非零退出。
    """
    import subprocess

    from fastapi.testclient import TestClient

    from platform_api.app import create_app
    from platform_api.demo_repo import build_demo_track_repo

    repo = build_demo_track_repo(tmp_path / "demo")
    subprocess.run(
        ["git", "branch", "-D", "stage-00-onboarding-sol"],
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

    r = c.post(f"/instances/{iid}/provision")
    assert r.status_code == 500
    assert r.json()["detail"] == "workspace_ref_missing"
    assert r.json()["ref"] == "origin/stage-00-onboarding-sol"
    assert "origin/stage-00-onboarding" in r.json()["available"]
