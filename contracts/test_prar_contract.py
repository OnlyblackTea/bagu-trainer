"""prar-agent HTTP/WS contract tests (design §6.6; run via `make test-contract`).

These gate every submodule bump: if a pinned-tag endpoint moves or changes shape,
the bridge must be corrected here too. They bind the REAL v0.1.0 surface read from
the submodule at backend/src/app/api/. The D1 and PENDING tripwires are asserted
directly (no network); HTTP bindings are asserted with respx. WS plan-stream
fidelity (plan.start -> plan.node x N -> plan.done) is documented in the bridge and
exercised end-to-end only against a live engine, so it is not mocked here.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from orchestrator import (
    ContractPendingError,
    D1ForbiddenError,
    EngineAPIError,
    PrarBridge,
)

BASE = "http://engine.test"
ADAPTER_ID = "22222222-2222-2222-2222-222222222222"
SESSION_ID = "11111111-1111-1111-1111-111111111111"


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


@pytest.mark.parametrize(
    "method_name",
    [
        "open_mentor_session",
        "advance_to_acting_without_executor",
        "submit_for_ai_review",
    ],
)
async def test_pending_contracts_raise(method_name: str) -> None:
    async with PrarBridge(BASE) as bridge:
        with pytest.raises(ContractPendingError):
            await getattr(bridge, method_name)()
