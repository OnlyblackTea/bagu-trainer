"""发卷：列 track、取 track（含阶段 DAG 与 board-only 条目）、取 ticket；未知 id → 404。"""

from __future__ import annotations

from typing import Any


def _stages(client: Any) -> list[dict[str, Any]]:
    return client.get("/tracks/database-bagu").json()["stages"]


def test_list_tracks(client: Any) -> None:
    r = client.get("/tracks")
    assert r.status_code == 200
    assert r.json()[0]["id"] == "database-bagu"


def test_get_track_lists_assessed_stages_with_prereqs(client: Any) -> None:
    assessed = [s for s in _stages(client) if s["kind"] == "assessed"]
    assert [s["ticket_id"] for s in assessed] == ["db-stage-00", "db-stage-01", "db-stage-02"]
    assert assessed[0]["prerequisites"] == []
    assert assessed[1]["prerequisites"] == ["db-stage-00"]


def test_stage_is_the_directory_name_and_ticket_id_is_the_identity(client: Any) -> None:
    """看板的 ``stage`` 是目录名（描述式），``ticket_id`` 才是 identity.id。"""
    assessed = [s for s in _stages(client) if s["kind"] == "assessed"]
    assert assessed[1]["stage"] == "stage-01-product-query-index"
    assert assessed[1]["ticket_id"] == "db-stage-01"


def test_board_only_stage_has_no_ticket(client: Any) -> None:
    """只上榜、不出卷的 stage：出现在看板里，但没有 ticket_id（防剧透/未出卷）。"""
    board_only = [s for s in _stages(client) if s["kind"] == "board_only"]
    assert [s["stage"] for s in board_only] == ["stage-03-deadlock-optional"]
    assert board_only[0]["ticket_id"] is None


def test_get_track_404(client: Any) -> None:
    assert client.get("/tracks/nope").status_code == 404


def test_get_ticket_renders_content(client: Any) -> None:
    r = client.get("/tickets/db-stage-00")
    assert r.status_code == 200
    body = r.json()
    assert body["problem"] and body["files_to_change"] and body["hints"]
    assert body["grader"]["test_group"] == "db-stage-00"


def test_get_ticket_is_keyed_by_identity_not_directory(client: Any) -> None:
    """用目录名当 id 取卷必须 404——身份只有 ``identity.id`` 一个。"""
    assert client.get("/tickets/stage-01-product-query-index").status_code == 404


def test_get_ticket_404(client: Any) -> None:
    assert client.get("/tickets/nope").status_code == 404


def test_ticket_does_not_leak_solution_branch(client: Any) -> None:
    """防剧透（D2）：工单内容里不得出现解法分支的文本。"""
    body = client.get("/tickets/db-stage-00").json()
    assert "solution" not in str(body)
