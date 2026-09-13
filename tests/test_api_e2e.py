"""A′ 后端端到端：纯 REST 跑通自习闭环两条 decide 分支（设计 spec §C.7）。"""

from __future__ import annotations

from typing import Any


def _stage(client: Any, learner: str, ticket_id: str) -> dict[str, Any]:
    inst = client.get("/instances", params={"learner": learner}).json()
    return next(i for i in inst if i["ticket_id"] == ticket_id)


def test_pass_flow_unlocks_next_stage(client: Any) -> None:
    # 发卷
    track = client.get("/tracks/database-bagu").json()
    assessed = [s for s in track["stages"] if s["kind"] == "assessed"]
    assert assessed[0]["ticket_id"] == "db-stage-00"
    assert client.get("/tickets/db-stage-00").status_code == 200

    # 入学 + 开始
    client.post("/enrollments", json={"learner": "deployer"})
    s00 = _stage(client, "deployer", "db-stage-00")
    assert s00["state"] == "available" and "start" in s00["gate"]
    client.post(f"/instances/{s00['id']}/start")

    # provision + diff（真本地 git）
    prov = client.post(f"/instances/{s00['id']}/provision").json()
    assert prov["simulated"] is True
    diff = client.get(f"/instances/{s00['id']}/diff").json()
    assert "diff --git" in diff["diff"]

    # submit（pass 阅卷）+ review（approve）+ decide
    sub = client.post(
        f"/instances/{s00['id']}/submit", params={"scenario": "pass"}, json={}
    ).json()
    client.post(f"/submissions/{sub['id']}/review", params={"scenario": "approve"}, json={})
    dec = client.post(f"/submissions/{sub['id']}/decide").json()

    assert dec["decision"]["verdict"] == "pass"
    states = {i["ticket_id"]: i["state"] for i in dec["instances"]}
    assert states["db-stage-00"] == "passed"
    assert states["db-stage-01"] == "available"  # 解锁
    assert states["db-stage-02"] == "locked"  # 仍未解锁


def test_fail_flow_retries_then_passes(client: Any) -> None:
    client.post("/enrollments", json={"learner": "deployer"})
    s00 = _stage(client, "deployer", "db-stage-00")
    client.post(f"/instances/{s00['id']}/start")

    # 第一次：阅卷 fail → decide fail
    sub1 = client.post(
        f"/instances/{s00['id']}/submit", params={"scenario": "fail"}, json={}
    ).json()
    client.post(f"/submissions/{sub1['id']}/review", params={"scenario": "approve"}, json={})
    assert (
        client.post(f"/submissions/{sub1['id']}/decide").json()["decision"]["verdict"] == "fail"
    )
    assert _stage(client, "deployer", "db-stage-00")["state"] == "failed"

    # retry → 第二次 pass + approve → 解锁
    client.post(f"/instances/{s00['id']}/retry")
    sub2 = client.post(
        f"/instances/{s00['id']}/submit", params={"scenario": "pass"}, json={}
    ).json()
    assert sub2["seq"] == 2
    client.post(f"/submissions/{sub2['id']}/review", params={"scenario": "approve"}, json={})
    dec = client.post(f"/submissions/{sub2['id']}/decide").json()
    assert dec["decision"]["verdict"] == "pass"
    states = {i["ticket_id"]: i["state"] for i in dec["instances"]}
    assert states["db-stage-00"] == "passed" and states["db-stage-01"] == "available"


def test_full_chain_reaches_last_stage(client: Any) -> None:
    """三阶段串行推进：证明 DAG 是链式的，而不是"一次全解锁"。"""
    client.post("/enrollments", json={"learner": "deployer"})
    for ticket_id in ("db-stage-00", "db-stage-01", "db-stage-02"):
        inst = _stage(client, "deployer", ticket_id)
        assert inst["state"] == "available", f"{ticket_id} 应已解锁"
        client.post(f"/instances/{inst['id']}/start")
        sub = client.post(
            f"/instances/{inst['id']}/submit", params={"scenario": "pass"}, json={}
        ).json()
        client.post(
            f"/submissions/{sub['id']}/review", params={"scenario": "approve"}, json={}
        )
        dec = client.post(f"/submissions/{sub['id']}/decide").json()
        assert dec["decision"]["verdict"] == "pass"
    states = {
        i["ticket_id"]: i["state"]
        for i in client.get("/instances", params={"learner": "deployer"}).json()
    }
    assert states == {
        "db-stage-00": "passed",
        "db-stage-01": "passed",
        "db-stage-02": "passed",
    }
