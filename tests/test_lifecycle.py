"""Unit tests for platform_service.lifecycle (design v0.5 §9.2 state machine)."""

from __future__ import annotations

import pytest

from platform_service.lifecycle import (
    LEGAL_TRANSITIONS,
    IllegalTransitionError,
    can_transition,
    record_submission,
    reissue,
    transition,
)
from platform_service.models import InstanceState, Submission, TicketInstance


def _instance(state: InstanceState = InstanceState.LOCKED) -> TicketInstance:
    return TicketInstance(
        id="ti-1",
        learner="deployer",
        track="database-bagu",
        ticket_id="db-stage-01",
        ticket_version=1,
        engine_version="v0.1.0",
        test_version=1,
        state=state,
    )


def test_happy_path_walks_the_full_chain() -> None:
    inst = _instance()
    for target in (
        InstanceState.AVAILABLE,
        InstanceState.IN_PROGRESS,
        InstanceState.SUBMITTED,
        InstanceState.GRADING,
        InstanceState.PASSED,
    ):
        transition(inst, target)
    assert inst.state is InstanceState.PASSED


def test_passed_is_terminal_except_stale() -> None:
    assert can_transition(InstanceState.PASSED, InstanceState.STALE)
    assert not can_transition(InstanceState.PASSED, InstanceState.IN_PROGRESS)
    assert LEGAL_TRANSITIONS[InstanceState.STALE] == set()


def test_illegal_transition_raises_and_leaves_state_untouched() -> None:
    inst = _instance(InstanceState.LOCKED)
    with pytest.raises(IllegalTransitionError):
        transition(inst, InstanceState.SUBMITTED)
    assert inst.state is InstanceState.LOCKED


def test_every_non_stale_state_can_be_staled() -> None:
    for state in InstanceState:
        if state is InstanceState.STALE:
            continue
        assert can_transition(state, InstanceState.STALE), state


def test_record_submission_requires_in_progress() -> None:
    inst = _instance(InstanceState.IN_PROGRESS)
    record_submission(inst, Submission(id="s1", instance_id="ti-1", seq=1))
    assert inst.state is InstanceState.SUBMITTED


def test_record_submission_rejects_failed_without_retry() -> None:
    inst = _instance(InstanceState.FAILED)
    with pytest.raises(IllegalTransitionError):
        record_submission(inst, Submission(id="s2", instance_id="ti-1", seq=2))


def test_record_submission_rejects_foreign_submission() -> None:
    inst = _instance(InstanceState.IN_PROGRESS)
    with pytest.raises(ValueError):
        record_submission(inst, Submission(id="s1", instance_id="OTHER", seq=1))


def test_retry_preserves_version_anchor() -> None:
    inst = _instance(InstanceState.FAILED)
    anchor = (inst.ticket_version, inst.engine_version, inst.test_version)
    transition(inst, InstanceState.IN_PROGRESS)  # retry re-opens the same instance
    record_submission(inst, Submission(id="s2", instance_id="ti-1", seq=2))
    assert inst.state is InstanceState.SUBMITTED
    assert (inst.ticket_version, inst.engine_version, inst.test_version) == anchor


def test_reissue_stales_old_and_anchors_new() -> None:
    old = _instance(InstanceState.PASSED)
    stale, fresh = reissue(
        old,
        new_id="ti-2",
        ticket_version=2,
        test_version=3,
        engine_version="v0.2.0",
        state=InstanceState.AVAILABLE,
    )
    assert stale is old
    assert stale.state is InstanceState.STALE
    assert fresh.id == "ti-2"
    assert fresh.learner == old.learner
    assert fresh.ticket_id == old.ticket_id
    assert (fresh.ticket_version, fresh.test_version, fresh.engine_version) == (2, 3, "v0.2.0")
    assert fresh.state is InstanceState.AVAILABLE


def test_instance_anchor_is_five_tuple_without_fixture_seed() -> None:
    assert "fixture_seed" not in TicketInstance.model_fields
    assert {
        "learner",
        "ticket_id",
        "ticket_version",
        "engine_version",
        "test_version",
    } <= set(TicketInstance.model_fields)
