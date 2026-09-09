"""TicketInstance lifecycle state machine (design v0.4 §9.2).

States: ``locked → available → in_progress → submitted → grading → passed | failed``.
A failed instance retries (``failed → in_progress``) within the SAME instance under a
new Submission; a content/version change RE-ISSUES: the old instance is parked as
``stale`` and a fresh instance anchors the bumped versions. ``passed`` is terminal in
the happy path (it triggers unlock) but can still be staled by a re-issue.

Pure state logic: no engine, no HTTP, no DB (GPL boundary intact).
"""

from __future__ import annotations

from .models import InstanceState, Submission, TicketInstance

# Every non-terminal state may also go to STALE (re-issue, §9.2); STALE is terminal.
LEGAL_TRANSITIONS: dict[InstanceState, set[InstanceState]] = {
    InstanceState.LOCKED: {InstanceState.AVAILABLE, InstanceState.STALE},
    InstanceState.AVAILABLE: {InstanceState.IN_PROGRESS, InstanceState.STALE},
    InstanceState.IN_PROGRESS: {InstanceState.SUBMITTED, InstanceState.STALE},
    InstanceState.SUBMITTED: {InstanceState.GRADING, InstanceState.STALE},
    InstanceState.GRADING: {InstanceState.PASSED, InstanceState.FAILED, InstanceState.STALE},
    InstanceState.FAILED: {InstanceState.IN_PROGRESS, InstanceState.STALE},
    InstanceState.PASSED: {InstanceState.STALE},
    InstanceState.STALE: set(),
}


class IllegalTransitionError(Exception):
    """Raised when a requested state change is not in :data:`LEGAL_TRANSITIONS`."""


def can_transition(current: InstanceState, target: InstanceState) -> bool:
    """Whether ``current → target`` is a legal edge."""
    return target in LEGAL_TRANSITIONS.get(current, set())


def transition(instance: TicketInstance, target: InstanceState) -> None:
    """Move ``instance`` to ``target`` in place; raise if the edge is illegal.

    The instance is left untouched when the transition is rejected.
    """
    if not can_transition(instance.state, target):
        raise IllegalTransitionError(
            f"instance {instance.id}: {instance.state.value} -> {target.value} is illegal"
        )
    instance.state = target


def record_submission(instance: TicketInstance, submission: Submission) -> None:
    """Park ``instance`` at ``submitted`` for a fresh submission.

    Legal only from ``in_progress`` — a ``failed`` instance must first be retried
    (``failed → in_progress``) by the caller. Guards that the submission actually
    belongs to this instance.
    """
    if submission.instance_id != instance.id:
        raise ValueError(
            f"submission {submission.id} targets {submission.instance_id}, "
            f"not instance {instance.id}"
        )
    transition(instance, InstanceState.SUBMITTED)


def reissue(
    instance: TicketInstance,
    *,
    new_id: str,
    ticket_version: int,
    test_version: int,
    engine_version: str,
    state: InstanceState = InstanceState.LOCKED,
) -> tuple[TicketInstance, TicketInstance]:
    """Stale ``instance`` and mint a fresh one anchoring the new versions (§9.2).

    Returns ``(old, new)``: ``old`` is the same object, now ``stale`` and kept for
    audit; ``new`` carries the bumped anchor (still a 5-tuple, no ``fixture_seed``).
    The caller supplies ``new_id`` and the DAG-derived initial ``state``.
    """
    transition(instance, InstanceState.STALE)
    fresh = TicketInstance(
        id=new_id,
        learner=instance.learner,
        ticket_id=instance.ticket_id,
        ticket_version=ticket_version,
        engine_version=engine_version,
        test_version=test_version,
        state=state,
    )
    return instance, fresh
