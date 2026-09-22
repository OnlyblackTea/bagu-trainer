"""「重启不失忆」——持久化唯一值得测的性质。

不是"能写进 PG"（那是 ``test_store.py`` 一致性套件的事），而是：**进程整个重来一遍，
攒的进度还在，且新发的号不会盖掉旧的**。这里用两个独立的 ``create_app`` 模拟重启——
第二个 app 不共享任何 Python 状态，它看到的只有库里的东西。
"""

from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient

from platform_api.demo_repo import build_demo_track_repo


def test_meta_reports_the_memory_store_by_default(client: TestClient) -> None:
    """学员有权知道攒的进度是"重启就没"还是落了库；口径必须如实，不能笼统说"已保存"。"""
    assert client.get("/meta").json()["store_backend"] == "memory"


def test_progress_survives_an_app_restart(tmp_path: Path, clean_postgres: str) -> None:
    from platform_api.app import create_app

    repo = build_demo_track_repo(tmp_path / "demo")
    forge_base = tmp_path / "ws"

    def boot() -> TestClient:
        return TestClient(
            create_app(
                content_dir=repo,
                forge_base=forge_base,
                store_backend="postgres",
                database_url=clean_postgres,
            )
        )

    first = boot()
    assert first.get("/meta").json()["store_backend"] == "postgres"

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
    # 进度**不**落库（见 platform_api/jobs）：重启后如实说不知道，而不是编一个"正在跑第 2 步"。
    progress = second.get(f"/submissions/{submission_id}/progress").json()
    assert progress["status"] == "unknown" and progress["report_ready"] is True

    # 第二次 enroll 必须**新增** 3 个实例，而不是因为 id 从头数而覆盖掉上面那 3 个。
    again = second.post("/enrollments", json={"learner": "deployer"}).json()["instances"]
    fresh = {i["id"] for i in again} - {i["id"] for i in instances}
    assert len(fresh) == 3
    assert len(second.get("/instances", params={"learner": "deployer"}).json()) == 6
