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
