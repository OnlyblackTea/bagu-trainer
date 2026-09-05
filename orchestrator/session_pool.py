"""Per-(learner, TicketInstance) multiplexing over independent engine sessions.

§6.1: the platform talks to *N* separate prar-agent sessions, one per assessment
in flight. This module is a thin local registry that maps an assessment key to a
live engine ``session_id``, opening sessions lazily through :class:`PrarBridge`.

It binds only the real v0.1.0 session-create endpoint, through the adapter. The
mentor-mode session semantics (write/execute disabled for ``files_to_change``) are
PENDING in the bridge, so the pool today opens ordinary plan-phase sessions and
relies on the caller never advancing them into an Executor run (D1).
"""

from __future__ import annotations

from dataclasses import dataclass

from .prar_bridge import PrarBridge


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
    key: AssessmentKey
    session_id: str
    adapter_id: str


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
    ) -> PooledSession:
        """Return the session for ``key``, opening a new engine session if absent."""
        existing = self._sessions.get(key)
        if existing is not None:
            return existing
        created = await self._bridge.create_session(
            init_request=init_request, adapter_id=adapter_id
        )
        pooled = PooledSession(key=key, session_id=str(created["id"]), adapter_id=adapter_id)
        self._sessions[key] = pooled
        return pooled

    def release(self, key: AssessmentKey) -> None:
        """Drop the local mapping. The engine session row persists server-side."""
        self._sessions.pop(key, None)

    def __len__(self) -> int:
        return len(self._sessions)
