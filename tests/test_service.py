"""End-to-end unit tests for platform_service.service (design v0.4 §9.2 / §11.3).

Drive the whole offline loop against an :class:`InMemoryStore` over a 3-ticket linear
DAG (stage-00 → 01 → 02): enroll mints correctly-initialised instances; a green
submission + approving Review passes the gate and unlocks the immediate dependent; a
red report or a ``request_changes`` Review fails it without unlocking; retry reopens
with an incremented ``seq``; reissue stales the old instance and re-anchors a fresh one.

No engine, no HTTP, no DB — the AI Review and grading report are injected inputs.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from platform_service.lifecycle import IllegalTransitionError
from platform_service.models import (
    GradingReport,
    InstanceState,
    Recommendation,
    Review,
    TicketSpec,
    TrackManifest,
    Verdict,
)
from platform_service.service import PlatformService
from platform_service.store import InMemoryStore

_LEARNER = "deployer"


@pytest.fixture
def env(track_factory: Any, ticket_factory: Any, grading_report_factory: Any) -> Any:
    track = TrackManifest.model_validate(track_factory()["track"])
    tickets = {
        tid: TicketSpec.model_validate(
            ticket_factory(ticket_id=tid, stage=stage, prerequisites=prereqs)
        )
        for tid, stage, prereqs in (
            ("db-stage-00", 0, []),
            ("db-stage-01", 1, ["db-stage-00"]),
            ("db-stage-02", 2, ["db-stage-01"]),
        )
    }
    store = InMemoryStore()
    service = PlatformService(store, track, tickets, engine_version="v0.1.0")
    return SimpleNamespace(
        service=service, store=store, tickets=tickets, report=grading_report_factory
    )


def _live(store: InMemoryStore, ticket_id: str):
    live = [
        inst
        for inst in store.list_instances(learner=_LEARNER, ticket_id=ticket_id)
        if inst.state is not InstanceState.STALE
    ]
    assert live, f"no live instance for {ticket_id}"
    return live[-1]


def _make_review(submission_id: str, recommendation: Recommendation) -> Review:
    return Review(
        id=f"rev-{submission_id}",
        submission_id=submission_id,
        recommendation=recommendation,
        summary="评审结论",
    )


def _attempt(
    env: Any, instance_id: str, *, passed: bool, recommendation: Recommendation
):
    """Submit a grading claim + advisory Review for an already in_progress instance."""
    report = GradingReport.model_validate(env.report(passed=passed))
    submission = env.service.submit(instance_id, grading_report=report)
    env.service.review(submission.id, _make_review(submission.id, recommendation))
    return submission


def test_enroll_sets_dag_initial_states(env: Any) -> None:
    env.service.enroll(_LEARNER)
    assert _live(env.store, "db-stage-00").state is InstanceState.AVAILABLE
    assert _live(env.store, "db-stage-01").state is InstanceState.LOCKED
    assert _live(env.store, "db-stage-02").state is InstanceState.LOCKED


def test_full_pass_flow_unlocks_immediate_dependent(env: Any) -> None:
    env.service.enroll(_LEARNER)
    inst = _live(env.store, "db-stage-00")
    env.service.start(inst.id)
    sub = _attempt(env, inst.id, passed=True, recommendation=Recommendation.APPROVE)
    decision = env.service.decide(sub.id)

    assert decision.verdict is Verdict.PASS
    assert decision.decided_by == "auto"
    assert env.store.get_decision(sub.id).inputs.correctness_gate is True
    assert _live(env.store, "db-stage-00").state is InstanceState.PASSED
    assert _live(env.store, "db-stage-01").state is InstanceState.AVAILABLE  # unlocked
    assert _live(env.store, "db-stage-02").state is InstanceState.LOCKED  # not yet


def test_request_changes_fails_without_unlock(env: Any) -> None:
    env.service.enroll(_LEARNER)
    inst = _live(env.store, "db-stage-00")
    env.service.start(inst.id)
    sub = _attempt(
        env, inst.id, passed=True, recommendation=Recommendation.REQUEST_CHANGES
    )
    decision = env.service.decide(sub.id)

    assert decision.verdict is Verdict.FAIL
    assert _live(env.store, "db-stage-00").state is InstanceState.FAILED
    assert _live(env.store, "db-stage-01").state is InstanceState.LOCKED


def test_red_correctness_fails_even_with_approve(env: Any) -> None:
    env.service.enroll(_LEARNER)
    inst = _live(env.store, "db-stage-00")
    env.service.start(inst.id)
    sub = _attempt(env, inst.id, passed=False, recommendation=Recommendation.APPROVE)
    assert env.service.decide(sub.id).verdict is Verdict.FAIL
    assert _live(env.store, "db-stage-00").state is InstanceState.FAILED


def test_retry_reopens_and_increments_seq(env: Any) -> None:
    env.service.enroll(_LEARNER)
    inst = _live(env.store, "db-stage-00")
    env.service.start(inst.id)
    first = _attempt(env, inst.id, passed=False, recommendation=Recommendation.APPROVE)
    assert env.service.decide(first.id).verdict is Verdict.FAIL

    env.service.retry(inst.id)
    assert env.store.get_instance(inst.id).state is InstanceState.IN_PROGRESS

    second = _attempt(env, inst.id, passed=True, recommendation=Recommendation.APPROVE)
    assert second.seq == 2
    assert env.service.decide(second.id).verdict is Verdict.PASS
    assert [s.seq for s in env.store.list_submissions(inst.id)] == [1, 2]
    assert _live(env.store, "db-stage-01").state is InstanceState.AVAILABLE


def test_reissue_stales_old_and_reanchors_new(env: Any) -> None:
    env.service.enroll(_LEARNER)
    old = _live(env.store, "db-stage-00")
    fresh = env.service.reissue(
        _LEARNER,
        "db-stage-00",
        ticket_version=2,
        test_version=2,
        engine_version="v0.2.0",
    )

    assert fresh.id != old.id
    assert env.store.get_instance(old.id).state is InstanceState.STALE
    assert fresh.state is InstanceState.AVAILABLE  # root restarts available
    assert (fresh.ticket_version, fresh.test_version) == (2, 2)
    assert fresh.engine_version == "v0.2.0"


def test_decide_requires_both_report_and_review(env: Any) -> None:
    env.service.enroll(_LEARNER)
    inst = _live(env.store, "db-stage-00")
    env.service.start(inst.id)
    report = GradingReport.model_validate(env.report(passed=True))
    submission = env.service.submit(inst.id, grading_report=report)  # review missing

    with pytest.raises(ValueError):
        env.service.decide(submission.id)


def test_submit_requires_in_progress(env: Any) -> None:
    env.service.enroll(_LEARNER)
    inst = _live(env.store, "db-stage-00")  # available, not started
    report = GradingReport.model_validate(env.report(passed=True))

    with pytest.raises(IllegalTransitionError):
        env.service.submit(inst.id, grading_report=report)
