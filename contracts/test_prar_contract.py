"""prar-agent HTTP/WS contract tests (design §6.6; run via `make test-contract`).

These gate every submodule bump: if a pinned-tag endpoint moves or changes shape,
the bridge must be corrected here too. They bind the REAL v0.2.0 surface frozen in
the engine's ``docs/CONTRACTS.md`` (now vendored in the submodule). The D1 tripwire
is asserted directly (no network); HTTP bindings are asserted with respx. WS
plan-stream fidelity (plan.start -> plan.node x N -> plan.done) is documented in the
bridge and exercised end-to-end only against a live engine, so it is not mocked here.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from orchestrator import (
    D1ForbiddenError,
    EngineAPIError,
    PrarBridge,
)

BASE = "http://engine.test"
ADAPTER_ID = "22222222-2222-2222-2222-222222222222"
SESSION_ID = "11111111-1111-1111-1111-111111111111"
SUBMISSION_ID = "33333333-3333-3333-3333-333333333333"
REVIEW_ID = "44444444-4444-4444-4444-444444444444"

# CONTRACTS §3.5: shell_confinement is an additive enum, never a boolean. Assert
# membership so a future "container_mount" does not break the test.
SHELL_CONFINEMENT_ENUM = {"not_enabled", "none", "container_mount"}
# CONTRACTS §5.2 / §5.8h: recommendation is a fixed 2-value enum (no third value).
RECOMMENDATION_ENUM = {"approve", "request_changes"}


async def test_create_session_binds_real_endpoint() -> None:
    with respx.mock:
        route = respx.post(f"{BASE}/api/sessions").mock(
            return_value=httpx.Response(
                201,
                json={
                    "id": SESSION_ID,
                    "init_request": "stage-01",
                    "phase": "planning",
                    "current_plan_version": 1,
                    "adapter_id": ADAPTER_ID,
                },
            )
        )
        async with PrarBridge(BASE) as bridge:
            out = await bridge.create_session(init_request="stage-01", adapter_id=ADAPTER_ID)
    assert route.called
    assert out["id"] == SESSION_ID
    assert out["phase"] == "planning"


async def test_advance_to_acting_uses_post() -> None:
    with respx.mock:
        route = respx.post(f"{BASE}/api/sessions/{SESSION_ID}/advance-to-acting").mock(
            return_value=httpx.Response(200, json={"phase": "acting"})
        )
        async with PrarBridge(BASE) as bridge:
            out = await bridge.advance_to_acting(SESSION_ID)
    assert route.called
    assert out == {"phase": "acting"}


async def test_list_comments_passes_plan_version_query() -> None:
    with respx.mock:
        route = respx.get(f"{BASE}/api/sessions/{SESSION_ID}/comments").mock(
            return_value=httpx.Response(200, json=[])
        )
        async with PrarBridge(BASE) as bridge:
            await bridge.list_comments(SESSION_ID, plan_version=2)
    assert route.called
    assert route.calls.last.request.url.params["plan_version"] == "2"


async def test_non_2xx_maps_to_engine_api_error() -> None:
    with respx.mock:
        respx.post(f"{BASE}/api/sessions").mock(
            return_value=httpx.Response(400, json={"detail": "boom"})
        )
        async with PrarBridge(BASE) as bridge:
            with pytest.raises(EngineAPIError) as ei:
                await bridge.create_session(init_request="x", adapter_id=ADAPTER_ID)
    assert ei.value.status_code == 400
    assert ei.value.detail == "boom"


async def test_d1_refuses_executor_path() -> None:
    async with PrarBridge(BASE) as bridge:
        with pytest.raises(D1ForbiddenError):
            await bridge.run_act_executor()


# ---- v0.2.0 frozen contracts (CONTRACTS.md §3-§5) -------------------------


async def test_open_mentor_session_posts_human_actor_and_workspace() -> None:
    with respx.mock:
        route = respx.post(f"{BASE}/api/sessions").mock(
            return_value=httpx.Response(
                201,
                json={
                    "id": SESSION_ID,
                    "phase": "planning",
                    "actor": "human",
                    "current_plan_version": 0,
                    "adapter_id": ADAPTER_ID,
                    "workspace": {
                        "kind": "external",
                        "root": "F:/repos/s42",
                        "tool_policy": None,
                    },
                },
            )
        )
        async with PrarBridge(BASE) as bridge:
            out = await bridge.open_mentor_session(
                init_request="stage-01",
                adapter_id=ADAPTER_ID,
                workspace={"kind": "external", "root": "F:/repos/s42"},
            )
    assert route.called
    body = json.loads(route.calls.last.request.content)
    # actor="human" is the structural D1 lock; it must be on the wire.
    assert body["actor"] == "human"
    assert body["workspace"] == {"kind": "external", "root": "F:/repos/s42"}
    assert out["actor"] == "human"


async def test_get_workspace_asserts_shell_confinement_by_membership() -> None:
    with respx.mock:
        route = respx.get(f"{BASE}/api/sessions/{SESSION_ID}/workspace").mock(
            return_value=httpx.Response(
                200,
                json={
                    "session_id": SESSION_ID,
                    "kind": "external",
                    "root": "F:/repos/s42",
                    "checkpoint_enabled": False,
                    "rerun_available": False,
                    "shell_confinement": "not_enabled",
                    "effective_policy": {
                        "allowed_tools": ["fs.read", "fs.write"],
                        "readonly_include": [],
                        "allow_write_include": ["scratch/"],
                        "read_scope": "root",
                    },
                },
            )
        )
        async with PrarBridge(BASE) as bridge:
            ws = await bridge.get_workspace(SESSION_ID)
    assert route.called
    # enum membership, NOT equality — the set grows additively (CONTRACTS §3.5).
    assert ws["shell_confinement"] in SHELL_CONFINEMENT_ENUM
    # external workspace: no checkpoint, so rerun is unavailable; hide the button.
    assert ws["rerun_available"] is False
    assert ws["effective_policy"]["allow_write_include"] == ["scratch/"]


async def test_create_submission_posts_diff_and_advances_to_review() -> None:
    with respx.mock:
        route = respx.post(f"{BASE}/api/sessions/{SESSION_ID}/submissions").mock(
            return_value=httpx.Response(
                201,
                json={
                    "id": SUBMISSION_ID,
                    "session_id": SESSION_ID,
                    "plan_version": 3,
                    "phase": "action_review",
                    "created_at": "2026-09-09T02:41:48Z",
                },
            )
        )
        async with PrarBridge(BASE) as bridge:
            out = await bridge.create_submission(
                SESSION_ID,
                diff="diff --git a/src/a.py b/src/a.py",
                grading_report={"passed": True},
            )
    assert route.called
    body = json.loads(route.calls.last.request.content)
    assert body["diff"].startswith("diff --git")
    assert body["grading_report"] == {"passed": True}
    # optional fields omitted when None (the engine uses extra="forbid" on shapes).
    assert "summary" not in body
    assert "defense" not in body
    assert out["phase"] == "action_review"


async def test_advance_to_acting_without_executor_uses_real_endpoint() -> None:
    with respx.mock:
        route = respx.post(f"{BASE}/api/sessions/{SESSION_ID}/advance-to-acting").mock(
            return_value=httpx.Response(200, json={"phase": "acting"})
        )
        async with PrarBridge(BASE) as bridge:
            out = await bridge.advance_to_acting_without_executor(SESSION_ID)
    # Delegates to the real advance-to-acting endpoint; the no-Executor property is
    # structural for a human-actor session (CONTRACTS §4.3), not a separate call.
    assert route.called
    assert out == {"phase": "acting"}


async def test_submit_for_ai_review_is_advisory_and_has_no_phase() -> None:
    with respx.mock:
        route = respx.post(f"{BASE}/api/sessions/{SESSION_ID}/reviews").mock(
            return_value=httpx.Response(
                201,
                json={
                    "id": REVIEW_ID,
                    "session_id": SESSION_ID,
                    "submission_id": SUBMISSION_ID,
                    "plan_version": 3,
                    "created_at": "2026-09-09T02:41:48Z",
                    "result": {
                        "summary": "one-line verdict",
                        "recommendation": "request_changes",
                        "dimension_findings": [
                            {"dimension": "correctness", "commentary": "..."}
                        ],
                        "comments": [
                            {
                                "dimension": "security",
                                "text": "...",
                                "quote": "x = 1",
                                "path": "src/a.py",
                                "line": 12,
                                "side": "right",
                                "anchor": "src/a.py#right:12",
                            }
                        ],
                        "unanchored": [],
                        "truncated_files": [],
                    },
                },
            )
        )
        async with PrarBridge(BASE) as bridge:
            out = await bridge.submit_for_ai_review(
                SESSION_ID,
                dimensions=["correctness", "security"],
                submission_id=SUBMISSION_ID,
            )
    assert route.called
    body = json.loads(route.calls.last.request.content)
    assert body["dimensions"] == ["correctness", "security"]
    assert body["submission_id"] == SUBMISSION_ID
    # submission_id XOR diff: only one may be on the wire (CONTRACTS §5.1).
    assert "diff" not in body
    # Advisory guarantee: 2-value recommendation, and the response carries no phase
    # field — this endpoint structurally cannot move the state machine (§5.2, §2.1).
    assert out["result"]["recommendation"] in RECOMMENDATION_ENUM
    assert "phase" not in out
