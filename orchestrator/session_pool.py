"""Per-(learner, TicketInstance) multiplexing over independent engine sessions.

§6.1: the platform talks to *N* separate prar-agent sessions, one per assessment
in flight. This module is a thin local registry that maps an assessment key to a
live engine ``session_id``, opening sessions lazily through :class:`PrarBridge`.

Every session it opens is a **mentor session** (CONTRACTS §3/§4): ``actor="human"``
plus an explicit ``workspace``, created via :meth:`PrarBridge.open_mentor_session`.
``actor="human"`` is the structural D1 lock — the engine cannot reach the Executor
tool loop for such a session (§4.3), so the AI never writes or runs the graded code.
Right after creation the pool calls :meth:`PrarBridge.get_workspace` to *self-attest*
the effective policy (§3.4, the only way to prove the write whitelist actually took)
and refuses a session whose ``shell_confinement`` is not a known-safe enum member.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .prar_bridge import PrarBridge

# CONTRACTS §3.5: ``shell_confinement`` is an additive enum, never a boolean. These are
# the values that are safe to host a mentor session: ``not_enabled`` (shell is not in the
# external whitelist — the default) and ``container_mount`` (a future Docker read-only
# mount). ``"none"`` is deliberately absent: it means shell was allowed with NO path
# confinement. Assert by membership, never ``== "not_enabled"`` (§6.6.1 iron law), and
# fail closed on any value not yet known-safe.
SAFE_SHELL_CONFINEMENT: frozenset[str] = frozenset({"not_enabled", "container_mount"})


class WorkspaceAttestationError(Exception):
    """The engine's self-attested workspace (§3.4) is not D1-safe.

    Raised when ``get_workspace`` reports a ``shell_confinement`` outside
    :data:`SAFE_SHELL_CONFINEMENT` — an unconfined ``"none"`` or a value this platform
    version does not yet recognize. The session is refused, not pooled.
    """


@dataclass(frozen=True)
class AssessmentKey:
    """Identifies one in-flight assessment.

    Keyed on the TicketInstance, not the bare ticket, so a re-issue on a version
    change is a distinct session and a retry within the same instance reuses one.
    """

    learner: str
    ticket_instance: str


@dataclass
class PooledSession:
    """A live mentor session plus the workspace facts the engine self-attested (§3.4).

    ``rerun_available`` is captured here so the caller can hide the rerun affordance
    instead of probing for a 409 (§4.4); for an external + human-actor session it is
    always ``False``.
    """

    key: AssessmentKey
    session_id: str
    adapter_id: str
    shell_confinement: str
    rerun_available: bool


class SessionPool:
    def __init__(self, bridge: PrarBridge) -> None:
        self._bridge = bridge
        self._sessions: dict[AssessmentKey, PooledSession] = {}

    def get(self, key: AssessmentKey) -> PooledSession | None:
        return self._sessions.get(key)

    async def acquire(
        self,
        key: AssessmentKey,
        *,
        init_request: str,
        adapter_id: str,
        workspace: dict[str, Any],
    ) -> PooledSession:
        """Return the mentor session for ``key``, opening + attesting one if absent.

        ``workspace`` is the caller's external-workspace request (§3.2); choosing its
        ``root`` (the learner's forked repo) is the platform's responsibility, not the
        engine's. The session is pooled only after ``get_workspace`` confirms a
        D1-safe ``shell_confinement``.
        """
        existing = self._sessions.get(key)
        if existing is not None:
            return existing

        created = await self._bridge.open_mentor_session(
            init_request=init_request, adapter_id=adapter_id, workspace=workspace
        )
        session_id = str(created["id"])

        attested = await self._bridge.get_workspace(session_id)
        confinement = str(attested["shell_confinement"])
        if confinement not in SAFE_SHELL_CONFINEMENT:
            raise WorkspaceAttestationError(
                f"session {session_id}: shell_confinement {confinement!r} is not a "
                f"known-safe value {sorted(SAFE_SHELL_CONFINEMENT)}; refusing mentor session"
            )

        pooled = PooledSession(
            key=key,
            session_id=session_id,
            adapter_id=adapter_id,
            shell_confinement=confinement,
            rerun_available=bool(attested["rerun_available"]),
        )
        self._sessions[key] = pooled
        return pooled

    def release(self, key: AssessmentKey) -> None:
        """Drop the local mapping. The engine session row persists server-side."""
        self._sessions.pop(key, None)

    def __len__(self) -> int:
        return len(self._sessions)
