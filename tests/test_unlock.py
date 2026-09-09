"""Unit tests for platform_service.unlock (design v0.4 §4.3/§9.2 prerequisites DAG)."""

from __future__ import annotations

from platform_service.models import InstanceState, TicketInstance, TicketSpec
from platform_service.unlock import (
    build_prereq_graph,
    detect_cycle,
    initial_state,
    prereqs_satisfied,
    unlock_dependents,
)


def _instance(
    ticket_id: str, state: InstanceState = InstanceState.LOCKED, learner: str = "deployer"
) -> TicketInstance:
    return TicketInstance(
        id=f"ti-{ticket_id}",
        learner=learner,
        ticket_id=ticket_id,
        ticket_version=1,
        engine_version="v0.1.0",
        test_version=1,
        state=state,
    )


def test_build_prereq_graph_from_ticket_specs(ticket_factory) -> None:
    tickets = [
        TicketSpec.model_validate(ticket_factory(ticket_id="db-00", prerequisites=[])),
        TicketSpec.model_validate(ticket_factory(ticket_id="db-01", prerequisites=["db-00"])),
    ]
    assert build_prereq_graph(tickets) == {"db-00": [], "db-01": ["db-00"]}


def test_linear_chain_has_no_cycle() -> None:
    assert detect_cycle({"c": ["b"], "b": ["a"], "a": []}) is None


def test_diamond_has_no_cycle() -> None:
    graph = {"d": ["b", "c"], "b": ["a"], "c": ["a"], "a": []}
    assert detect_cycle(graph) is None


def test_cycle_is_detected_as_closed_path() -> None:
    cycle = detect_cycle({"a": ["b"], "b": ["a"]})
    assert cycle is not None
    assert cycle[0] == cycle[-1]
    assert set(cycle) == {"a", "b"}


def test_self_cycle_is_detected() -> None:
    assert detect_cycle({"a": ["a"]}) is not None


def test_initial_state_root_available_dependent_locked() -> None:
    graph = {"a": [], "b": ["a"]}
    assert initial_state("a", graph) is InstanceState.AVAILABLE
    assert initial_state("b", graph) is InstanceState.LOCKED


def test_prereqs_satisfied_needs_all() -> None:
    graph = {"d": ["b", "c"], "b": ["a"], "c": ["a"], "a": []}
    assert not prereqs_satisfied("d", {"b"}, graph)
    assert prereqs_satisfied("d", {"b", "c"}, graph)
    assert prereqs_satisfied("a", set(), graph)


def test_unlock_dependent_flips_locked_to_available() -> None:
    graph = {"b": ["a"], "a": []}
    a = _instance("a", InstanceState.PASSED)
    b = _instance("b", InstanceState.LOCKED)
    unlocked = unlock_dependents("a", graph, [a, b])
    assert b in unlocked
    assert b.state is InstanceState.AVAILABLE


def test_diamond_join_waits_for_both_branches() -> None:
    graph = {"d": ["b", "c"], "b": ["a"], "c": ["a"], "a": []}
    a = _instance("a", InstanceState.PASSED)
    b = _instance("b", InstanceState.PASSED)
    c = _instance("c", InstanceState.LOCKED)
    d = _instance("d", InstanceState.LOCKED)

    # Passing b alone must NOT unlock d — c is still outstanding.
    assert unlock_dependents("b", graph, [a, b, c, d]) == []
    assert d.state is InstanceState.LOCKED

    # Once c passes too, the join node d unlocks.
    c.state = InstanceState.PASSED
    assert d in unlock_dependents("c", graph, [a, b, c, d])
    assert d.state is InstanceState.AVAILABLE


def test_unlock_skips_non_locked_and_unknown_dependents() -> None:
    graph = {"b": ["a"], "a": []}
    a = _instance("a", InstanceState.PASSED)
    in_progress = _instance("b", InstanceState.IN_PROGRESS)
    assert unlock_dependents("a", graph, [a, in_progress]) == []
    assert unlock_dependents("a", {"z": ["a"]}, [a]) == []
