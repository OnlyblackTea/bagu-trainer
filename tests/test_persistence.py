"""「重启不失忆」——持久化唯一值得测的性质。

不是"能写进 PG"（那是 ``test_store.py`` 一致性套件的事），而是：**进程整个重来一遍，
攒的进度还在，且新发的号不会盖掉旧的**。这里用两个独立的 ``create_app`` 模拟重启——
第二个 app 不共享任何 Python 状态，它看到的只有库里的东西。

另外还测一条只在"阅卷很久"时才暴露的性质：进程死在 `mvn test` 中途的记录，必须由下一个启动的
进程如实收成 ``interrupted``——见 ``test_a_run_interrupted_by_a_restart_...``。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from platform_api.demo_repo import build_demo_track_repo
from platform_service.models import GradingStepStatus, InstanceState, Submission, TicketInstance


def _instance(instance_id: str, learner: str, ticket_id: str) -> TicketInstance:
    return TicketInstance(
        id=instance_id,
        learner=learner,
        track="database-bagu",
        ticket_id=ticket_id,
        ticket_version=1,
        engine_version="a-prime-0.1.0",
        test_version=1,
        state=InstanceState.IN_PROGRESS,
    )


def test_meta_reports_which_store_and_where_it_is(client: TestClient) -> None:
    """学员有权知道进度存在哪、重启会不会丢；口径必须如实，不能笼统说"已保存"。

    `demo_app` 是测试自己装配的（默认 sqlite）——这条同时钉住默认后端不再是内存。
    """
    meta = client.get("/meta").json()
    assert meta["store_backend"] == "sqlite"
    assert meta["store_target"]


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
def test_progress_survives_an_app_restart(
    tmp_path: Path, request: Any, backend: str
) -> None:
    """**这一条就是"重启不失忆"本身**，两个持久后端各跑一遍。

    SQLite 是默认值，所以它必须被真跑过：新建一个 app 对象就等于重启——它不共享任何 Python
    状态，能看到的只有磁盘上的那个文件/库。
    """
    from platform_api.app import create_app

    if backend == "postgres":
        target = request.getfixturevalue("clean_postgres")
    else:
        target = str(tmp_path / "bagu.sqlite3")

    repo = build_demo_track_repo(tmp_path / "demo")
    forge_base = tmp_path / "ws"

    def boot() -> TestClient:
        return TestClient(
            create_app(
                content_dir=repo,
                forge_base=forge_base,
                store_backend=backend,
                database_url=target,
            )
        )

    first = boot()
    assert first.get("/meta").json()["store_backend"] == backend
    assert first.get("/meta").json()["store_target"]

    enrollment = first.post("/enrollments", json={"learner": "deployer"}).json()
    instances = enrollment["instances"]
    assert len(instances) == 3
    root = next(i for i in instances if i["state"] == "available")
    first.post(f"/instances/{root['id']}/start")
    submitted = first.post(f"/instances/{root['id']}/submit?scenario=fail", json={}).json()
    submission_id = submitted["submission"]["id"]
    # 提交是异步的（202 + 进度）；这里等报告真落库，再断言重启后还读得到——
    # 否则"真阅卷等于没做"，而且这条断言会变成在测"桩跑得够快"。
    for _ in range(200):
        if first.get(f"/submissions/{submission_id}/progress").json()["report_ready"]:
            break
        time.sleep(0.02)
    assert first.get(f"/submissions/{submission_id}/report").status_code == 200

    second = boot()
    after = second.get("/instances", params={"learner": "deployer"}).json()
    assert [i["id"] for i in after] == [i["id"] for i in instances]
    assert next(i for i in after if i["id"] == root["id"])["state"] == "submitted"
    assert second.get(f"/submissions/{submission_id}/report").status_code == 200
    # 运行记录也落库了：重启后"这次阅卷走到哪、每阶段花了多久"仍然查得到。
    progress = second.get(f"/submissions/{submission_id}/progress").json()
    assert progress["status"] == "done" and progress["report_ready"] is True
    assert [s["step"] for s in progress["steps"]] == ["reset_infra", "run_tests", "parse"]

    # 历史查询跨重启仍然完整：尝试 + 报告 + 运行记录 + 判定
    attempts = second.get(f"/instances/{root['id']}/history").json()["attempts"]
    assert len(attempts) == 1
    assert attempts[0]["submission"]["id"] == submission_id
    assert attempts[0]["report"] is not None
    assert attempts[0]["grading"]["status"] == "done"

    # 第二次 enroll 必须**新增** 3 个实例，而不是因为 id 从头数而覆盖掉上面那 3 个。
    again = second.post("/enrollments", json={"learner": "deployer"}).json()["instances"]
    fresh = {i["id"] for i in again} - {i["id"] for i in instances}
    assert len(fresh) == 3
    assert len(second.get("/instances", params={"learner": "deployer"}).json()) == 6


def test_a_run_interrupted_by_a_restart_is_not_left_claiming_to_be_running(
    tmp_path: Path, clean_postgres: str
) -> None:
    """**被重启打断的阅卷必须被如实标成 ``interrupted``**（而不是永远撒谎说"正在阅卷"）。

    这条是"阅卷很久"这个前提下的核心保证：进程死在 `mvn test` 中途时，崩掉的进程没法给自己写
    墓志铭，所以必须由下一个启动的进程把 ``running`` 收成 ``interrupted``——否则学员既不知道
    该等还是该重来，历史里也留不下"这次被打断了"。

    这里手工摆一条"上一个进程留下的、卡在 running 的记录"（真去 kill 一个正在跑的 maven 太脆，
    而且验的是**清扫**这条规则本身），再用 ``with TestClient(...)`` 真跑一次启动流程。
    """
    from platform_api.store_postgres import PostgresStore
    from platform_service.models import GradingRun, GradingRunStatus, GradingRunStep

    store = PostgresStore(clean_postgres)
    store.save_instance(_instance("ti-9", "deployer", "db-stage-00"))
    store.save_submission(Submission(id="sub-9", instance_id="ti-9", seq=1))
    store.save_grading_run(
        GradingRun(
            submission_id="sub-9",
            instance_id="ti-9",
            track="database-bagu",
            ticket_id="db-stage-00",
            learner="deployer",
            owner="default",
            status=GradingRunStatus.RUNNING,
            step="run_tests",
            steps=[GradingRunStep(step="run_tests", status=GradingStepStatus.RUNNING)],
        )
    )

    # 只给一个 store 就能验清扫；app 装配顺便证明它挂在启动流程上。
    from platform_api.app import create_app

    repo = build_demo_track_repo(tmp_path / "demo")
    app = create_app(
        content_dir=repo,
        forge_base=tmp_path / "ws",
        store=store,
        store_backend="postgres",
        database_url=clean_postgres,
    )
    with TestClient(app) as client:  # `with` 才会跑 lifespan（启动清扫在里面）
        body = client.get("/submissions/sub-9/progress").json()
        assert body["status"] == "interrupted"
        assert "重启" in body["detail"]

    stored = PostgresStore(clean_postgres).get_grading_run("sub-9")
    assert stored.status is GradingRunStatus.INTERRUPTED
    assert stored.ended_at is not None and stored.elapsed_ms is not None
