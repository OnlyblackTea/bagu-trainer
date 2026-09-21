"""Unit tests for platform_service.store (design v0.5 §9.1 persistence seam).

Covers save/get/list for every runtime entity plus the query axes the service
relies on: instances by (learner, ticket), submissions by instance (seq-ordered),
and report/review/decision keyed by submission.
"""

from __future__ import annotations

from platform_service.models import (
    AcceptanceDecision,
    AcceptanceInputs,
    Enrollment,
    GradingReport,
    InstanceState,
    Recommendation,
    Review,
    Submission,
    TicketInstance,
    TrustLevel,
    User,
    Verdict,
)
from platform_service.store import InMemoryStore, Store


def _instance(instance_id: str, learner: str, ticket_id: str) -> TicketInstance:
    return TicketInstance(
        id=instance_id,
        learner=learner,
        ticket_id=ticket_id,
        ticket_version=1,
        engine_version="v0.1.0",
        test_version=1,
    )


def test_in_memory_store_conforms_to_protocol() -> None:
    assert isinstance(InMemoryStore(), Store)


def test_user_and_enrollment_round_trip() -> None:
    store = InMemoryStore()
    user = User(id="deployer", kind="deployer")
    store.save_user(user)
    assert store.get_user("deployer") == user
    assert store.get_user("ghost") is None

    enrollment = Enrollment(id="en-1", learner="deployer", track="database-bagu")
    store.save_enrollment(enrollment)
    assert store.get_enrollment("en-1") == enrollment
    assert store.get_enrollment("ghost") is None


def test_instance_save_get_and_upsert() -> None:
    store = InMemoryStore()
    inst = _instance("ti-1", "deployer", "db-stage-01")
    store.save_instance(inst)
    assert store.get_instance("ti-1") == inst

    store.save_instance(inst.model_copy(update={"state": InstanceState.AVAILABLE}))
    assert store.get_instance("ti-1").state is InstanceState.AVAILABLE


def test_list_instances_filters_by_learner_and_ticket() -> None:
    store = InMemoryStore()
    store.save_instance(_instance("ti-1", "alice", "db-stage-01"))
    store.save_instance(_instance("ti-2", "alice", "db-stage-02"))
    store.save_instance(_instance("ti-3", "bob", "db-stage-01"))

    assert len(store.list_instances()) == 3
    assert {i.id for i in store.list_instances(learner="alice")} == {"ti-1", "ti-2"}
    assert {i.id for i in store.list_instances(ticket_id="db-stage-01")} == {"ti-1", "ti-3"}
    assert {
        i.id for i in store.list_instances(learner="alice", ticket_id="db-stage-01")
    } == {"ti-1"}


def test_list_submissions_scoped_and_ordered_by_seq() -> None:
    store = InMemoryStore()
    store.save_submission(Submission(id="s2", instance_id="ti-1", seq=2))
    store.save_submission(Submission(id="s1", instance_id="ti-1", seq=1))
    store.save_submission(Submission(id="other", instance_id="ti-2", seq=1))

    assert [s.seq for s in store.list_submissions("ti-1")] == [1, 2]
    assert store.get_submission("s1").id == "s1"
    assert store.get_submission("ghost") is None


def test_report_review_decision_keyed_by_submission(sample_grading_report) -> None:
    store = InMemoryStore()
    report = GradingReport.model_validate(sample_grading_report)
    store.save_report(report)
    assert store.get_report(report.submission_id) == report
    assert store.get_report("ghost") is None

    review = Review(
        id="rev-1",
        submission_id=report.submission_id,
        recommendation=Recommendation.APPROVE,
        summary="ok",
    )
    store.save_review(review)
    assert store.get_review(report.submission_id) == review

    decision = AcceptanceDecision(
        id="ad-1",
        submission_id=report.submission_id,
        inputs=AcceptanceInputs(
            correctness_gate=True,
            review_recommendation=Recommendation.APPROVE,
            evidence_trust=TrustLevel.SELF,
        ),
        verdict=Verdict.PASS,
    )
    store.save_decision(decision)
    assert store.get_decision(report.submission_id) == decision
    assert store.get_decision("ghost") is None
