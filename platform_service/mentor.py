"""MentorSession orchestration — the 发卷 → 接卷 → 开发 arc (design v0.4 §6.2/§6.3).

This drives ONE assessment's engine session through the front half of the human-actor
flow (CONTRACTS §2.2):

* **发卷 (issue)** — PLANNING: stream plan generation over WS ``/plan``; the AI manager
  produces the ticket plan (problem statement, concept explanations, *clarifying*
  decision questions — never solution decisions, §6.2). On ``plan.done`` the engine
  persists the plan and the phase becomes ``plan_review``.
* **接卷 (accept)** — read the persisted plan (``get_current_plan``) so the learner can
  take it up.
* **开发 (begin_development)** — ACTING: advance to ``acting`` *without* an Executor
  (§6.3). The learner writes the graded code in the host repo; the platform runs the
  grader on the learner's own compute (D3). A rework after a failed review re-enters
  here too — ``action_review → acting`` is a legal transition (§2.1/§4.2).

**D1 (§4.3, 契约二)**: this arc NEVER connects the ``ws_act`` Executor path. The
injected :class:`MentorEngine` Protocol does not even name an executor method, so
reaching one is structurally impossible — not merely avoided.

Phase ↔ TicketInstance alignment (§9.2/§9.3): the mentor session reuses the engine's six
phases verbatim; the platform's TicketInstance lifecycle is *overlaid* on it and is owned
by :class:`platform_service.service.PlatformService` (``issue``/``accept`` sit under
``in_progress``; the later ``create_submission`` moves it to ``submitted``). This module
therefore touches no store and mutates no instance — it is pure engine-session
orchestration, offline-testable against a fake, with the GPL boundary intact.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any, Protocol


class MentorEngine(Protocol):
    """The bridge methods this module drives (structurally typed).

    ``PrarBridge`` satisfies this without being imported, keeping platform_service
    decoupled from orchestrator. Signatures mirror CONTRACTS §2.2 / §4.5 verbatim.
    There is deliberately NO executor / ``ws_act`` member (D1, §4.3).
    """

    def stream_plan_generation(
        self,
        session_id: str,
        *,
        init_request: str,
        adapter_id: str,
        available_tools: Sequence[str] | None = None,
    ) -> AsyncIterator[dict[str, Any]]: ...

    async def get_current_plan(self, session_id: str) -> dict[str, Any]: ...

    async def advance_to_acting_without_executor(self, session_id: str) -> dict[str, Any]: ...


class MentorSession:
    """One in-flight mentor session, bound to an already-open engine ``session_id``.

    The session is opened (and its workspace self-attested) elsewhere — by
    ``orchestrator.session_pool.SessionPool.acquire``; this object only drives its
    phases. The plan and its stream events are returned as the engine's opaque JSON
    (ProseMirror plan, §6.2): this module never re-shapes them, so it cannot drift from
    a contract it does not own.
    """

    def __init__(self, engine: MentorEngine, session_id: str) -> None:
        self._engine = engine
        self._session_id = session_id

    @property
    def session_id(self) -> str:
        return self._session_id

    async def issue(
        self,
        *,
        init_request: str,
        adapter_id: str,
        available_tools: Sequence[str] | None = None,
    ) -> list[dict[str, Any]]:
        """发卷 (§6.2): stream plan generation and return the collected event frames.

        The frames run ``plan.start → plan.node × N → plan.done`` (CONTRACTS §4.5); the
        authoritative plan is then read back via :meth:`accept`. ``available_tools``
        narrows the *plan-time* tool surface — the runtime write scope is frozen on the
        session's ``workspace.tool_policy`` (§3.1), not here.
        """
        stream = self._engine.stream_plan_generation(
            self._session_id,
            init_request=init_request,
            adapter_id=adapter_id,
            available_tools=available_tools,
        )
        return [event async for event in stream]

    async def accept(self) -> dict[str, Any]:
        """接卷: fetch the persisted plan (phase ``plan_review``) for the learner to read."""
        return await self._engine.get_current_plan(self._session_id)

    async def begin_development(self) -> dict[str, Any]:
        """开发 (§6.3): advance to ``acting`` without starting an Executor (D1).

        The human is the actor: the engine structurally cannot reach the Executor tool
        loop for this ``actor="human"`` session (§4.3), and ``ws_act`` is never
        connected. The same call re-enters development on a rework
        (``action_review → acting``, §4.2).
        """
        return await self._engine.advance_to_acting_without_executor(self._session_id)
