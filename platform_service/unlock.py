"""Prerequisites-DAG evaluation for unlocking (design v0.4 §4.3 / §9.2).

v0.3 reversed the old forward-pointing ``unlock_next``: each ticket declares WHO IT
DEPENDS ON via ``progression.prerequisites``, forming a DAG. The unlock check is
"are all my prerequisites passed?". A single prerequisite degenerates to a linear
chain; a diamond's join node waits for every branch.

Pure graph logic: no engine, no HTTP, no DB (GPL boundary intact).
"""

from __future__ import annotations

from collections.abc import Iterable

from .lifecycle import transition
from .models import InstanceState, TicketInstance, TicketSpec


def build_prereq_graph(tickets: Iterable[TicketSpec]) -> dict[str, list[str]]:
    """Map each ticket id to its prerequisite ids (edges point ticket → prereqs)."""
    return {ticket.identity.id: list(ticket.progression.prerequisites) for ticket in tickets}


def detect_cycle(graph: dict[str, list[str]]) -> list[str] | None:
    """Return one dependency cycle as a closed node path (first == last), or None.

    A non-empty result means the graph is NOT a DAG and unlock would deadlock.
    """
    visiting: set[str] = set()
    done: set[str] = set()
    stack: list[str] = []

    def visit(node: str) -> list[str] | None:
        visiting.add(node)
        stack.append(node)
        for dep in graph.get(node, []):
            if dep in visiting:
                start = stack.index(dep)
                return [*stack[start:], dep]
            if dep not in done:
                found = visit(dep)
                if found is not None:
                    return found
        stack.pop()
        visiting.discard(node)
        done.add(node)
        return None

    for node in graph:
        if node not in done:
            found = visit(node)
            if found is not None:
                return found
    return None


def prereqs_satisfied(ticket_id: str, passed_ids: set[str], graph: dict[str, list[str]]) -> bool:
    """Whether every prerequisite of ``ticket_id`` is in ``passed_ids``."""
    return all(prereq in passed_ids for prereq in graph.get(ticket_id, []))


def initial_state(ticket_id: str, graph: dict[str, list[str]]) -> InstanceState:
    """State a fresh instance starts in: ``available`` if a root, else ``locked``."""
    return InstanceState.AVAILABLE if not graph.get(ticket_id, []) else InstanceState.LOCKED


def unlock_dependents(
    passed_id: str, graph: dict[str, list[str]], instances: Iterable[TicketInstance]
) -> list[TicketInstance]:
    """Flip newly-unblocked dependents of ``passed_id`` from locked → available.

    ``instances`` is one learner's instance set; the passed set is derived from it
    (plus ``passed_id`` itself). A dependent unlocks only when ALL its prerequisites
    are passed. Returns the instances actually unlocked; already-available ones and
    dependents with no instance are skipped.
    """
    owned = list(instances)
    passed_ids = {inst.ticket_id for inst in owned if inst.state is InstanceState.PASSED}
    passed_ids.add(passed_id)
    by_ticket = {inst.ticket_id: inst for inst in owned}

    unlocked: list[TicketInstance] = []
    for dependent_id, prereqs in graph.items():
        if passed_id not in prereqs:
            continue
        instance = by_ticket.get(dependent_id)
        if instance is None or instance.state is not InstanceState.LOCKED:
            continue
        if prereqs_satisfied(dependent_id, passed_ids, graph):
            transition(instance, InstanceState.AVAILABLE)
            unlocked.append(instance)
    return unlocked
