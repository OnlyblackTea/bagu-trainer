"""多 Track 部署（设计 spec §C.3 步骤 1「选方向」→ 步骤 2「报名」）。

一个 store + 每个 Track 一份"内容 + service + forge"的装配。这里把同一份 demo 内容以**两个
track.id** 部署两次——ticket id（``db-stage-01``）因此**故意撞名**，因为那正是多 Track 最容易
出错的地方：任何"按 ticket id 反查 Track"或"按 ticket id 过滤实例"的写法都会在这一撞之下
悄悄串味。所以撞名不是 fixture 的偷懒，是夹具。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from platform_api import demo_repo
from platform_api.app import create_app
from platform_api.demo_repo import build_demo_track_repo

TRACK_A = "database-bagu"
TRACK_B = "cache-bagu"
LEARNER = "deployer"


def _two_tracks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """两个内容目录：同内容、不同 track.id，ticket id 因此撞名。"""
    a = build_demo_track_repo(tmp_path / "a")
    monkeypatch.setattr(demo_repo, "DEMO_TRACK_ID", TRACK_B)
    b = build_demo_track_repo(tmp_path / "b")
    return a, b


@pytest.fixture
def track_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    return _two_tracks(tmp_path, monkeypatch)


@pytest.fixture
def two_track_app(track_dirs: tuple[Path, Path], tmp_path: Path) -> FastAPI:
    return create_app(content_dirs=list(track_dirs), forge_base=tmp_path / "ws")


@pytest.fixture
def client(two_track_app: FastAPI) -> TestClient:
    return TestClient(two_track_app)


def _enroll(client: TestClient, track: str) -> list[dict]:
    r = client.post("/enrollments", json={"learner": LEARNER, "track": track})
    assert r.status_code == 201, r.text
    return r.json()["instances"]


def _instance_of(client: TestClient, track: str, ticket_id: str) -> dict:
    return next(
        i
        for i in client.get("/instances", params={"learner": LEARNER, "track": track}).json()
        if i["ticket_id"] == ticket_id
    )


def test_deploys_every_track(client: TestClient) -> None:
    """`GET /tracks` 是"选方向"的唯一数据源，必须列出**全部**已部署 Track。"""
    tracks = {t["id"]: t for t in client.get("/tracks").json()}
    assert set(tracks) == {TRACK_A, TRACK_B}
    assert tracks[TRACK_A]["stages"]
    # 看板清单含 board-only stage，故"上榜数"大于"出卷数"（§C.3 步骤 1）。
    assert tracks[TRACK_A]["assessed_stages"] == 3
    assert tracks[TRACK_A]["board_only_stages"] == 1


def test_enroll_targets_the_named_track_only(client: TestClient) -> None:
    instances = _enroll(client, TRACK_B)
    assert {i["track"] for i in instances} == {TRACK_B}
    assert len(instances) == 3
    assert client.get("/instances", params={"learner": LEARNER, "track": TRACK_A}).json() == []

    # 不指定 track 时给全部（一屏看全"我这届都干了啥"），每条自带归属。
    everything = client.get("/instances", params={"learner": LEARNER}).json()
    assert {i["track"] for i in everything} == {TRACK_B}


def test_enroll_without_track_is_rejected_when_ambiguous(client: TestClient) -> None:
    """多个 Track 又不说报哪个 ⇒ 400，**不猜**。猜错等于把学员报进另一个 Track 的 DAG，
    而 DAG 决定谁 locked——报错了人自己还看不出来。"""
    r = client.post("/enrollments", json={"learner": LEARNER})
    assert r.status_code == 400
    assert r.json()["detail"] == "enroll_needs_track"


def test_single_track_deployment_still_allows_omitting_track(tmp_path: Path) -> None:
    """只有一个已部署 Track 时省略 track 是安全默认——旧客户端不必改。"""
    repo = build_demo_track_repo(tmp_path / "solo")
    with TestClient(create_app(content_dir=repo, forge_base=tmp_path / "ws")) as c:
        r = c.post("/enrollments", json={"learner": LEARNER})
        assert r.status_code == 201
        assert {i["track"] for i in r.json()["instances"]} == {TRACK_A}


def test_colliding_ticket_id_is_409_and_the_scoped_path_is_not(client: TestClient) -> None:
    """扁平取卷在撞名时回 409 并让客户端去用带 Track 的地址；带 Track 的地址永远无歧义。"""
    flat = client.get("/tickets/db-stage-01")
    assert flat.status_code == 409
    assert flat.json()["detail"] == "ticket_id_ambiguous"

    scoped = client.get(f"/tracks/{TRACK_B}/tickets/db-stage-01")
    assert scoped.status_code == 200
    assert scoped.json()["track"] == TRACK_B
    assert scoped.json()["id"] == "db-stage-01"


def test_unknown_track_and_ticket_are_404(client: TestClient) -> None:
    assert client.get("/tracks/nope").status_code == 404
    assert client.get("/tracks/nope").json()["detail"] == "track_not_found"
    assert client.get(f"/tracks/{TRACK_A}/tickets/nope").status_code == 404


def test_each_track_provisions_into_its_own_workspace(client: TestClient) -> None:
    """每个 Track 一个 forge：同名 stage 分支在两个 Track 里是**两个仓库**，
    共用工作区目录会让 A 的 clone 被 B 的 checkout 顶掉。"""
    _enroll(client, TRACK_A)
    _enroll(client, TRACK_B)
    paths = {}
    for track in (TRACK_A, TRACK_B):
        inst = _instance_of(client, track, "db-stage-00")
        client.post(f"/instances/{inst['id']}/start")
        ws = client.post(f"/instances/{inst['id']}/provision").json()
        paths[track] = ws["path"]
    assert paths[TRACK_A] != paths[TRACK_B]
    assert TRACK_A in paths[TRACK_A] and TRACK_B in paths[TRACK_B]


def test_passing_one_track_does_not_unlock_the_other(client: TestClient) -> None:
    """跨 Track 串味的最隐蔽形态：ticket id 撞名 ⇒ 不按 track 过滤的解锁会让**另一个** Track
    的同名后继阶段解锁。这条断言就是为那个缺陷写的。"""
    _enroll(client, TRACK_A)
    _enroll(client, TRACK_B)
    for track in (TRACK_A, TRACK_B):
        inst = _instance_of(client, track, "db-stage-00")
        client.post(f"/instances/{inst['id']}/start")
        sub = client.post(
            f"/instances/{inst['id']}/submit", params={"scenario": "pass"}, json={}
        ).json()
        client.post(f"/submissions/{sub['id']}/review", params={"scenario": "approve"}, json={})
        decided = client.post(f"/submissions/{sub['id']}/decide").json()
        assert decided["decision"]["verdict"] == "pass"

    # 两边都该解锁自己的 db-stage-01（各自独立地通过了自己的 stage-00）。
    for track in (TRACK_A, TRACK_B):
        states = {
            i["ticket_id"]: i["state"]
            for i in client.get("/instances", params={"learner": LEARNER, "track": track}).json()
        }
        assert states["db-stage-01"] == "available", track


def test_instance_of_a_track_that_is_no_longer_deployed(
    client: TestClient, track_dirs: tuple[Path, Path], tmp_path: Path
) -> None:
    """实例指向一个已撤下的 Track ⇒ 409 ``track_not_deployed``。

    这是**部署事实**（内容换过了），不是学员的错；回落到"随便挑一个 Track"会让平台拿着别人的
    forge 与 stage refs 去操作他的仓库，比报错糟得多。这里把同一个 store 交给只部署了 A 的
    app——模拟"运营把 B 摘了，但库里还有 B 的实例"。
    """
    _enroll(client, TRACK_B)
    iid = _instance_of(client, TRACK_B, "db-stage-00")["id"]

    only_a = TestClient(
        create_app(
            content_dir=track_dirs[0],
            forge_base=tmp_path / "ws2",
            store=client.app.state.store,
        )
    )
    r = only_a.post(f"/instances/{iid}/start")
    assert r.status_code == 409
    assert r.json()["detail"] == "track_not_deployed"
    # 实例本身仍然查得到（只读不涉及 Track），报错只在"要动这个 Track 的东西"时发生。
    assert only_a.get(f"/instances/{iid}").json()["track"] == TRACK_B


def test_duplicate_track_id_is_refused(track_dirs: tuple[Path, Path]) -> None:
    """同一个 track.id 部署两次会让 ``GET /tracks/{id}`` 与 enroll 的归属都变得没意义。"""
    from platform_api.deps import build_tracks
    from platform_service.store import InMemoryStore

    same = track_dirs[0]
    with pytest.raises(ValueError, match="track.id"):
        build_tracks([same, same], InMemoryStore())


def test_forge_source_only_makes_sense_for_one_track(track_dirs: tuple[Path, Path]) -> None:
    """``forge_source`` 是"内容在 A、git 上游在 B"的单 Track 对照场景；多 Track 硬套一个
    source 会让所有 Track 都去 fork 同一个仓库。"""
    from platform_api.deps import build_tracks
    from platform_service.store import InMemoryStore

    with pytest.raises(ValueError, match="forge_source"):
        build_tracks(list(track_dirs), InMemoryStore(), forge_source=track_dirs[0])
