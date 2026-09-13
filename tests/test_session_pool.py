"""Unit tests for orchestrator.session_pool (design v0.4 §6.1, CONTRACTS §3/§4).

The pool is a thin local registry, so it is tested offline against a fake bridge —
no live engine, no HTTP (the real endpoint shapes are gated by contracts/test_prar_contract.py).

The load-bearing v0.2.0 behaviours:

* ``acquire`` opens a MENTOR session (``open_mentor_session`` = ``actor="human"`` +
  an external workspace), never the old executor-actor ``create_session`` — this is the
  structural D1 lock (CONTRACTS §4.3).
* It then self-attests via ``get_workspace`` (CONTRACTS §3.4, the only way to prove the
  write whitelist actually took) and asserts ``shell_confinement`` by ENUM MEMBERSHIP,
  never equality (§3.5 / §6.6.1 iron law) — an unconfined ``"none"`` is refused, a future
  ``"container_mount"`` is accepted without touching this test.
* ``rerun_available`` is captured (not probed via a 409) so the caller can hide rerun (§4.4).
"""

from __future__ import annotations

from typing import Any

import pytest

from orchestrator import session_pool
from orchestrator.session_pool import AssessmentKey

SESSION_ID = "11111222-3333-4444-5555-666677778888"
ADAPTER_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
EXTERNAL_WS: dict[str, Any] = {"kind": "external", "root": "F:/repos/student-42"}


def _workspace_payload(
    *, shell_confinement: str = "not_enabled", rerun_available: bool = False
) -> dict[str, Any]:
    """A CONTRACTS §3.4 GET /workspace body for an external + human-actor session."""
    return {
        "session_id": SESSION_ID,
        "kind": "external",
        "root": "F:/repos/student-42",
        "checkpoint_enabled": False,
        "rerun_available": rerun_available,
        "shell_confinement": shell_confinement,
        "effective_policy": {
            "allowed_tools": ["fs.read", "fs.write"],
            "readonly_include": [],
            "allow_write_include": ["scratch/"],
            "read_scope": "root",
        },
    }


class FakeBridge:
    """Duck-typed stand-in for the PrarBridge methods session_pool drives."""

    def __init__(self, *, workspace: dict[str, Any] | None = None) -> None:
        self._workspace = workspace or _workspace_payload()
        self.open_mentor_calls: list[dict[str, Any]] = []
        self.get_workspace_calls: list[str] = []
        self.create_session_calls: list[dict[str, Any]] = []

    async def open_mentor_session(
        self, *, init_request: str, adapter_id: str, workspace: dict[str, Any]
    ) -> dict[str, Any]:
        self.open_mentor_calls.append(
            {
                "init_request": init_request,
                "adapter_id": adapter_id,
                "workspace": workspace,
            }
        )
        return {
            "id": SESSION_ID,
            "init_request": init_request,
            "phase": "planning",
            "actor": "human",
            "current_plan_version": 0,
            "adapter_id": adapter_id,
            "workspace": workspace,
        }

    async def get_workspace(self, session_id: str) -> dict[str, Any]:
        self.get_workspace_calls.append(session_id)
        return self._workspace

    async def create_session(self, *, init_request: str, adapter_id: str) -> dict[str, Any]:
        # The pre-v0.2.0 executor-actor path. Recording it lets the tests prove the
        # pool no longer opens ordinary sessions (D1 wants actor="human").
        self.create_session_calls.append(
            {"init_request": init_request, "adapter_id": adapter_id}
        )
        return {"id": SESSION_ID, "phase": "planning", "actor": "executor"}


async def test_acquire_opens_a_mentor_session_not_an_executor_one() -> None:
    bridge = FakeBridge()
    pool = session_pool.SessionPool(bridge)
    key = AssessmentKey(learner="deployer", ticket_instance="ti-1")

    pooled = await pool.acquire(
        key, init_request="stage-01", adapter_id=ADAPTER_ID, workspace=EXTERNAL_WS
    )

    assert pooled.session_id == SESSION_ID
    assert pooled.adapter_id == ADAPTER_ID
    # Opened through the mentor (actor="human") path, with the caller's workspace.
    assert bridge.open_mentor_calls == [
        {"init_request": "stage-01", "adapter_id": ADAPTER_ID, "workspace": EXTERNAL_WS}
    ]
    # The old executor-actor create_session is never used.
    assert bridge.create_session_calls == []


async def test_acquire_self_attests_workspace_and_captures_rerun_flag() -> None:
    bridge = FakeBridge()
    pool = session_pool.SessionPool(bridge)
    key = AssessmentKey(learner="deployer", ticket_instance="ti-1")

    pooled = await pool.acquire(
        key, init_request="stage-01", adapter_id=ADAPTER_ID, workspace=EXTERNAL_WS
    )

    # get_workspace is the §3.4 self-attestation call, made right after creation.
    assert bridge.get_workspace_calls == [SESSION_ID]
    assert pooled.shell_confinement == "not_enabled"
    assert pooled.rerun_available is False


async def test_acquire_accepts_a_future_confinement_value() -> None:
    """§3.5: the enum grows additively; "container_mount" must pass without a code change."""
    bridge = FakeBridge(workspace=_workspace_payload(shell_confinement="container_mount"))
    pool = session_pool.SessionPool(bridge)
    key = AssessmentKey(learner="deployer", ticket_instance="ti-1")

    pooled = await pool.acquire(
        key, init_request="stage-01", adapter_id=ADAPTER_ID, workspace=EXTERNAL_WS
    )

    assert pooled.shell_confinement == "container_mount"


@pytest.mark.parametrize("confinement", ["none", "some_unknown_future_value"])
async def test_acquire_refuses_unconfined_or_unknown_shell(confinement: str) -> None:
    """Fail closed: "none" means shell is allowed with NO path confinement (§3.5), and an
    unrecognized value is not yet known-safe — neither may host a mentor session."""
    bridge = FakeBridge(workspace=_workspace_payload(shell_confinement=confinement))
    pool = session_pool.SessionPool(bridge)
    key = AssessmentKey(learner="deployer", ticket_instance="ti-1")

    with pytest.raises(session_pool.WorkspaceAttestationError):
        await pool.acquire(
            key, init_request="stage-01", adapter_id=ADAPTER_ID, workspace=EXTERNAL_WS
        )
    # A refused session is not pooled.
    assert pool.get(key) is None
    assert len(pool) == 0


async def test_acquire_is_idempotent_per_key() -> None:
    bridge = FakeBridge()
    pool = session_pool.SessionPool(bridge)
    key = AssessmentKey(learner="deployer", ticket_instance="ti-1")

    first = await pool.acquire(
        key, init_request="stage-01", adapter_id=ADAPTER_ID, workspace=EXTERNAL_WS
    )
    second = await pool.acquire(
        key, init_request="stage-01", adapter_id=ADAPTER_ID, workspace=EXTERNAL_WS
    )

    assert first is second
    assert len(bridge.open_mentor_calls) == 1
    assert len(pool) == 1


async def test_release_drops_the_local_mapping() -> None:
    bridge = FakeBridge()
    pool = session_pool.SessionPool(bridge)
    key = AssessmentKey(learner="deployer", ticket_instance="ti-1")
    await pool.acquire(
        key, init_request="stage-01", adapter_id=ADAPTER_ID, workspace=EXTERNAL_WS
    )

    pool.release(key)

    assert pool.get(key) is None
    assert len(pool) == 0
