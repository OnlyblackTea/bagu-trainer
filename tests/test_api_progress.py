"""submit→review→decide 两分支 + retry；提交是**异步**的（202 + 轮询进度）。

提交不再同步返回报告（§C.3 步骤 6）：真阅卷要重置容器再 ``mvn test``。所以这里的每个用例都
经 ``submit_and_wait``（= 前端做的事：拿 202、轮询到报告就绪）。"
"""

from __future__ import annotations

from typing import Any


def _stage00(client: Any) -> str:
    client.post("/enrollments", json={"learner": "deployer"})
    inst = client.get("/instances", params={"learner": "deployer"}).json()
    iid = next(i for i in inst if i["ticket_id"] == "db-stage-00")["id"]
    client.post(f"/instances/{iid}/start")
    return iid


def test_submit_is_accepted_immediately_with_progress(client: Any) -> None:
    """提交必须**立刻**回 202 + 进度，而不是挂在一个请求上等 maven。"""
    iid = _stage00(client)
    r = client.post(f"/instances/{iid}/submit", params={"scenario": "pass"}, json={})
    assert r.status_code == 202
    body = r.json()
    assert body["submission"]["instance_id"] == iid and body["submission"]["seq"] == 1
    assert body["progress"]["submission_id"] == body["submission"]["id"]
    assert body["progress"]["status"] in ("queued", "running", "done")
    # 实例立刻就是"已提交"——学员不必等阅卷跑完才知道提交成功了。
    assert client.get(f"/instances/{iid}").json()["state"] == "submitted"


def test_progress_reports_the_stage_chain(client: Any, submit_and_wait: Any) -> None:
    """进度给出**完整**阶段链（含未到达的），前端才画得出进度条。"""
    iid = _stage00(client)
    sub = submit_and_wait(client, iid, scenario="pass")

    body = client.get(f"/submissions/{sub['id']}/progress").json()
    assert body["status"] == "done" and body["report_ready"] is True
    assert [s["step"] for s in body["steps"]] == ["reset_infra", "run_tests", "parse"]
    assert all(s["status"] == "done" for s in body["steps"])
    assert body["elapsed_ms"] is not None


def test_review_then_decide_pass_unlocks(client: Any, submit_and_wait: Any) -> None:
    iid = _stage00(client)
    sub = submit_and_wait(client, iid, scenario="pass")
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


def test_decide_fail_then_retry(client: Any, submit_and_wait: Any) -> None:
    iid = _stage00(client)
    sub = submit_and_wait(client, iid, scenario="fail")
    client.post(f"/submissions/{sub['id']}/review", params={"scenario": "approve"}, json={})
    dec = client.post(f"/submissions/{sub['id']}/decide").json()
    assert dec["decision"]["verdict"] == "fail"
    assert {i["ticket_id"]: i["state"] for i in dec["instances"]}["db-stage-00"] == "failed"
    r = client.post(f"/instances/{iid}/retry")
    assert r.status_code == 200 and r.json()["state"] == "in_progress"


def test_request_changes_review_fails_the_gate(client: Any, submit_and_wait: Any) -> None:
    """阅卷绿但评审要改 → 门仍然 fail（评审是证据之一，不是分数）。"""
    iid = _stage00(client)
    sub = submit_and_wait(client, iid, scenario="pass")
    rev = client.post(
        f"/submissions/{sub['id']}/review", params={"scenario": "request_changes"}, json={}
    )
    assert rev.json()["recommendation"] == "request_changes"
    assert rev.json()["comments"], "request_changes 应带一条锚定评论"
    dec = client.post(f"/submissions/{sub['id']}/decide").json()
    assert dec["decision"]["verdict"] == "fail"


def test_decide_without_review_is_409(client: Any, submit_and_wait: Any) -> None:
    iid = _stage00(client)
    sub = submit_and_wait(client, iid, scenario="pass")
    r = client.post(f"/submissions/{sub['id']}/decide")
    assert r.status_code == 409 and r.json()["detail"] == "decide_needs_report_and_review"


def test_submitted_report_is_readable(client: Any, submit_and_wait: Any) -> None:
    """报告必须能从 REST 读到。

    真阅卷时这是学员**唯一**能看到"哪条测试红了、为什么、在什么环境跑的"的地方——
    桩时代看不出来（反正都是预置的），所以这个端点是接真后端时才显得必需。
    """
    iid = _stage00(client)
    sub = submit_and_wait(client, iid, scenario="fail")
    r = client.get(f"/submissions/{sub['id']}/report")
    assert r.status_code == 200
    body = r.json()
    assert body["correctness"]["passed"] is False
    assert body["submission_id"] == sub["id"]
    assert body["provenance"]["runner_trust"] == "self"


def test_report_not_ready_is_distinct_from_report_not_found(client: Any) -> None:
    """**"等一会儿"和"不会有报告了"必须分开**：两者要学员做的事完全不同。

    用一个永远不结束的假 runner 把任务钉在 running 上——不能靠"桩跑得快"来碰运气。
    """
    import threading

    from platform_service.grading import GradingStep, report_progress

    iid = _stage00(client)
    release = threading.Event()

    class _Stuck:
        def grade(self, *, instance, ticket, track, workspace, on_progress=None):  # noqa: ANN001
            report_progress(on_progress, GradingStep.RESET_INFRA, "卡住")
            release.wait(10)
            raise AssertionError("released")

    client.app.state.grading_jobs._runner_factory = lambda scenario: _Stuck()
    try:
        r = client.post(f"/instances/{iid}/submit", params={"scenario": "pass"}, json={})
        sub_id = r.json()["submission"]["id"]
        assert client.get(f"/submissions/{sub_id}/report").status_code == 404
        assert (
            client.get(f"/submissions/{sub_id}/report").json()["detail"] == "report_not_ready"
        )
        # 还没提交过的 id：那是"不会有报告了"，不是"还没好"。
        assert client.get("/submissions/ghost/report").json()["detail"] == "report_not_found"
    finally:
        release.set()


def test_grading_unavailable_is_503_not_a_failed_verdict(
    tmp_path: Any, monkeypatch: Any
) -> None:
    """真阅卷跑不起来（本机没有 maven）时要给 **503 + 短码**。

    关键在"不能做成 passed=False"：那是把平台配置问题栽给学员，学员会以为自己代码错了。
    异步之后失败发生在后台线程里，异常处理器够不着——所以这条断言同时守着
    ``jobs.classify_grading_error`` 那条就地分类的路。
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
    assert r.status_code == 202  # 提交本身成功了；跑不起来是**阅卷**的事
    sub_id = r.json()["submission"]["id"]

    import time

    for _ in range(200):
        progress = real_client.get(f"/submissions/{sub_id}/progress").json()
        if progress["status"] == "failed":
            break
        time.sleep(0.02)
    assert progress["status"] == "failed"
    assert progress["error"]["detail"] == "grading_unavailable"

    report = real_client.get(f"/submissions/{sub_id}/report")
    assert report.status_code == 503
    assert report.json()["detail"] == "grading_unavailable"
    assert "maven" in report.json()["reason"]


def test_submit_on_locked_is_409(client: Any) -> None:
    client.post("/enrollments", json={"learner": "deployer"})
    inst = client.get("/instances", params={"learner": "deployer"}).json()
    locked = next(i for i in inst if i["ticket_id"] == "db-stage-01")["id"]
    r = client.post(f"/instances/{locked}/submit", params={"scenario": "pass"}, json={})
    assert r.status_code == 409


def test_progress_of_an_unknown_submission_is_404(client: Any) -> None:
    r = client.get("/submissions/nope/progress")
    assert r.status_code == 404 and r.json()["detail"] == "submission_not_found"


def test_review_unknown_submission_404(client: Any) -> None:
    assert client.post("/submissions/nope/review", json={}).status_code == 404


def test_decide_unknown_submission_404(client: Any) -> None:
    assert client.post("/submissions/nope/decide").status_code == 404
