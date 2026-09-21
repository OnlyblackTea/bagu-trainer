"""submit→review→decide 两分支 + retry；错误形状（409 非法转移 / 409 缺评审 / 404）。"""

from __future__ import annotations

from typing import Any


def _stage00(client: Any) -> str:
    client.post("/enrollments", json={"learner": "deployer"})
    inst = client.get("/instances", params={"learner": "deployer"}).json()
    iid = next(i for i in inst if i["ticket_id"] == "db-stage-00")["id"]
    client.post(f"/instances/{iid}/start")
    return iid


def test_submit_creates_submission(client: Any) -> None:
    iid = _stage00(client)
    r = client.post(f"/instances/{iid}/submit", params={"scenario": "pass"}, json={})
    assert r.status_code == 200
    assert r.json()["instance_id"] == iid and r.json()["seq"] == 1


def test_review_then_decide_pass_unlocks(client: Any) -> None:
    iid = _stage00(client)
    sub = client.post(f"/instances/{iid}/submit", params={"scenario": "pass"}, json={}).json()
    rev = client.post(
        f"/submissions/{sub['id']}/review", params={"scenario": "approve"}, json={}
    )
    assert rev.status_code == 200 and rev.json()["recommendation"] == "approve"
    dec = client.post(f"/submissions/{sub['id']}/decide")
    assert dec.status_code == 200
    assert dec.json()["decision"]["verdict"] == "pass"
    states = {i["ticket_id"]: i["state"] for i in dec.json()["instances"]}
    assert states["db-stage-00"] == "passed" and states["db-stage-01"] == "available"
    assert states["db-stage-02"] == "locked"  # 仍隔一层，不该解锁


def test_decide_fail_then_retry(client: Any) -> None:
    iid = _stage00(client)
    sub = client.post(f"/instances/{iid}/submit", params={"scenario": "fail"}, json={}).json()
    client.post(f"/submissions/{sub['id']}/review", params={"scenario": "approve"}, json={})
    dec = client.post(f"/submissions/{sub['id']}/decide").json()
    assert dec["decision"]["verdict"] == "fail"
    assert {i["ticket_id"]: i["state"] for i in dec["instances"]}["db-stage-00"] == "failed"
    r = client.post(f"/instances/{iid}/retry")
    assert r.status_code == 200 and r.json()["state"] == "in_progress"


def test_request_changes_review_fails_the_gate(client: Any) -> None:
    """阅卷绿但评审要改 → 门仍然 fail（评审是证据之一，不是分数）。"""
    iid = _stage00(client)
    sub = client.post(f"/instances/{iid}/submit", params={"scenario": "pass"}, json={}).json()
    rev = client.post(
        f"/submissions/{sub['id']}/review", params={"scenario": "request_changes"}, json={}
    )
    assert rev.json()["recommendation"] == "request_changes"
    assert rev.json()["comments"], "request_changes 应带一条锚定评论"
    dec = client.post(f"/submissions/{sub['id']}/decide").json()
    assert dec["decision"]["verdict"] == "fail"


def test_decide_without_review_is_409(client: Any) -> None:
    iid = _stage00(client)
    sub = client.post(f"/instances/{iid}/submit", params={"scenario": "pass"}, json={}).json()
    r = client.post(f"/submissions/{sub['id']}/decide")
    assert r.status_code == 409 and r.json()["detail"] == "decide_needs_report_and_review"


def test_submitted_report_is_readable(client: Any) -> None:
    """报告必须能从 REST 读到。

    真阅卷时这是学员**唯一**能看到"哪条测试红了、为什么、在什么环境跑的"的地方——
    桩时代看不出来（反正都是预置的），所以这个端点是接真后端时才显得必需。
    """
    iid = _stage00(client)
    sub = client.post(f"/instances/{iid}/submit", params={"scenario": "fail"}, json={}).json()
    r = client.get(f"/submissions/{sub['id']}/report")
    assert r.status_code == 200
    body = r.json()
    assert body["correctness"]["passed"] is False
    assert body["submission_id"] == sub["id"]
    assert body["provenance"]["runner_trust"] == "self"


def test_report_before_submit_is_404(client: Any) -> None:
    r = client.get("/submissions/ghost/report")
    assert r.status_code == 404 and r.json()["detail"] == "report_not_found"


def test_grading_unavailable_is_503_not_a_failed_verdict(
    tmp_path: Any, monkeypatch: Any
) -> None:
    """真阅卷跑不起来（本机没有 maven）时要给 **503 + 短码**。

    关键在"不能做成 passed=False"：那是把平台配置问题栽给学员，学员会以为自己代码错了。
    """
    from fastapi.testclient import TestClient

    from platform_api import grading_docker
    from platform_api.app import create_app
    from platform_api.demo_repo import build_demo_track_repo

    monkeypatch.setattr(grading_docker.shutil, "which", lambda name: None)
    repo = build_demo_track_repo(tmp_path / "demo")
    real_client = TestClient(
        create_app(content_dir=repo, forge_base=tmp_path / "ws", grading="docker")
    )
    real_client.post("/enrollments", json={"learner": "deployer"})
    iid = next(
        i["id"]
        for i in real_client.get("/instances", params={"learner": "deployer"}).json()
        if i["ticket_id"] == "db-stage-00"
    )
    real_client.post(f"/instances/{iid}/start")

    r = real_client.post(f"/instances/{iid}/submit", json={})
    assert r.status_code == 503
    assert r.json()["detail"] == "grading_unavailable"
    assert "maven" in r.json()["reason"]


def test_submit_on_locked_is_409(client: Any) -> None:
    client.post("/enrollments", json={"learner": "deployer"})
    inst = client.get("/instances", params={"learner": "deployer"}).json()
    locked = next(i for i in inst if i["ticket_id"] == "db-stage-01")["id"]
    r = client.post(f"/instances/{locked}/submit", params={"scenario": "pass"}, json={})
    assert r.status_code == 409


def test_review_unknown_submission_404(client: Any) -> None:
    assert client.post("/submissions/nope/review", json={}).status_code == 404


def test_decide_unknown_submission_404(client: Any) -> None:
    assert client.post("/submissions/nope/decide").status_code == 404
