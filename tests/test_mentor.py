"""Unit tests for platform_service.mentor (design v0.4 §6.2/§6.3, CONTRACTS §2.2/§4).

The engine is injected (a fake here), so the 发卷 → 接卷 → 开发 orchestration is tested
offline — no live engine, no HTTP, no WebSocket. The fake mirrors the PrarBridge method
signatures verbatim.

The load-bearing D1 guarantee (§4.3, §6.6.1 契约二): the whole arc enters ACTING via
``advance_to_acting_without_executor`` and NEVER connects the ``ws_act`` Executor path —
the fake carries a ``run_act_executor`` tripwire that fails the test if it is ever reached.
"""

from __future__ import annotations

from typing import Any

import pytest

from platform_service import mentor

SESSION_ID = "11111222-3333-4444-5555-666677778888"
ADAPTER_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

_PLAN_EVENTS: list[dict[str, Any]] = [
    {"type": "plan.start"},
    {"type": "plan.node", "index": 0, "title": "陈述线上问题"},
    {"type": "plan.node", "index": 1, "title": "澄清需求边界"},
    {"type": "plan.done"},
]
_CURRENT_PLAN: dict[str, Any] = {"version": 1, "phase": "plan_review", "nodes": [{"id": "n0"}]}


class FakeMentorEngine:
    """Duck-typed stand-in for the PrarBridge methods MentorSession drives."""

    def __init__(
        self,
        *,
        plan_events: list[dict[str, Any]] | None = None,
        current_plan: dict[str, Any] | None = None,
        acting: dict[str, Any] | None = None,
    ) -> None:
        self._plan_events = plan_events or _PLAN_EVENTS
        self._current_plan = current_plan or _CURRENT_PLAN
        self._acting = acting or {"phase": "acting"}
        self.stream_calls: list[dict[str, Any]] = []
        self.get_plan_calls: list[str] = []
        self.advance_calls: list[str] = []
        self.execute_calls: list[tuple[Any, ...]] = []

    async def stream_plan_generation(
        self,
        session_id: str,
        *,
        init_request: str,
        adapter_id: str,
        available_tools: Any = None,
    ) -> Any:
        self.stream_calls.append(
            {
                "session_id": session_id,
                "init_request": init_request,
                "adapter_id": adapter_id,
                "available_tools": available_tools,
            }
        )
        for event in self._plan_events:
            yield event

    async def get_current_plan(self, session_id: str) -> dict[str, Any]:
        self.get_plan_calls.append(session_id)
        return self._current_plan

    async def advance_to_acting_without_executor(self, session_id: str) -> dict[str, Any]:
        self.advance_calls.append(session_id)
        return self._acting

    async def run_act_executor(self, *args: Any, **kwargs: Any) -> None:
        # D1 tripwire: a human-actor session must never reach the Executor path.
        self.execute_calls.append((args, kwargs))
        pytest.fail("D1 violated: mentor reached the ws_act Executor path")


async def test_issue_streams_plan_generation_and_collects_events() -> None:
    engine = FakeMentorEngine()
    session = mentor.MentorSession(engine, SESSION_ID)

    events = await session.issue(init_request="stage-01", adapter_id=ADAPTER_ID)

    assert events == _PLAN_EVENTS
    assert engine.stream_calls == [
        {
            "session_id": SESSION_ID,
            "init_request": "stage-01",
            "adapter_id": ADAPTER_ID,
            "available_tools": None,
        }
    ]


async def test_issue_narrows_the_plan_time_tool_surface_when_asked() -> None:
    engine = FakeMentorEngine()
    session = mentor.MentorSession(engine, SESSION_ID)

    await session.issue(
        init_request="stage-01", adapter_id=ADAPTER_ID, available_tools=["fs.read"]
    )

    assert engine.stream_calls[0]["available_tools"] == ["fs.read"]


async def test_accept_fetches_the_persisted_plan() -> None:
    engine = FakeMentorEngine()
    session = mentor.MentorSession(engine, SESSION_ID)

    plan = await session.accept()

    assert plan == _CURRENT_PLAN
    assert engine.get_plan_calls == [SESSION_ID]


async def test_begin_development_advances_to_acting_without_an_executor() -> None:
    engine = FakeMentorEngine()
    session = mentor.MentorSession(engine, SESSION_ID)

    out = await session.begin_development()

    assert out == {"phase": "acting"}
    assert engine.advance_calls == [SESSION_ID]


async def test_full_issue_accept_develop_arc_never_touches_the_executor() -> None:
    """The §2.2 human-actor flow end to end: 发卷 → 接卷 → 开发, with no ws_act connection."""
    engine = FakeMentorEngine()
    session = mentor.MentorSession(engine, SESSION_ID)

    await session.issue(init_request="stage-01", adapter_id=ADAPTER_ID)
    await session.accept()
    await session.begin_development()

    assert engine.execute_calls == []
    assert engine.advance_calls == [SESSION_ID]


def test_mentor_engine_protocol_omits_the_executor_path() -> None:
    """The injected Protocol must not even name an Executor method — D1 by construction."""
    assert not hasattr(mentor.MentorEngine, "run_act_executor")
    for name in (
        "stream_plan_generation",
        "get_current_plan",
        "advance_to_acting_without_executor",
    ):
        assert hasattr(mentor.MentorEngine, name)
