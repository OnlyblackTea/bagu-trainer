"""Conformance tests for the persistence seam (design v0.5 §9.1).

One suite, **every** :class:`~platform_service.store.Store` implementation. A seam is only
worth having if its implementations are interchangeable, so the value here is not
"does the dict do dict things" but "does the PostgreSQL backend answer exactly the same
questions the same way" — same filters, same ordering, same upsert-by-identity, same
``None`` when absent.

The PostgreSQL leg needs a real server; set ``BAGU_TEST_POSTGRES_DSN`` (e.g.
``postgresql://bagu:pw@127.0.0.1:5432/bagu_test``) to run it. Without the variable it
**skips** rather than silently passing: the memory leg alone would prove nothing about
SQL. It truncates ``bagu_documents`` / ``bagu_id_seq`` before each test, so point it at a
throwaway database.
"""

from __future__ import annotations

from typing import Any

import pytest

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


@pytest.fixture(params=["memory", "postgres"])
def store(request: pytest.FixtureRequest) -> Any:
    """Every store implementation, one suite.

    ``request.param`` doubles as the backend name so a failure says which one broke.
    The PostgreSQL leg skips (via ``clean_postgres``) when no test DSN is configured.
    """
    if request.param == "memory":
        return InMemoryStore()
    dsn = request.getfixturevalue("clean_postgres")
    from platform_api.store_postgres import PostgresStore

    return PostgresStore(dsn)


def _instance(instance_id: str, learner: str, ticket_id: str) -> TicketInstance:
    return TicketInstance(
        id=instance_id,
        learner=learner,
        track="database-bagu",
        ticket_id=ticket_id,
        ticket_version=1,
        engine_version="v0.1.0",
        test_version=1,
    )


def test_store_conforms_to_protocol(store: Store) -> None:
    assert isinstance(store, Store)


def test_user_and_enrollment_round_trip(store: Store) -> None:
    user = User(id="deployer", kind="deployer")
    store.save_user(user)
    assert store.get_user("deployer") == user
    assert store.get_user("ghost") is None

    enrollment = Enrollment(id="en-1", learner="deployer", track="database-bagu")
    store.save_enrollment(enrollment)
    assert store.get_enrollment("en-1") == enrollment
    assert store.get_enrollment("ghost") is None


def test_instance_save_get_and_upsert(store: Store) -> None:
    inst = _instance("ti-1", "deployer", "db-stage-01")
    store.save_instance(inst)
    assert store.get_instance("ti-1") == inst

    store.save_instance(inst.model_copy(update={"state": InstanceState.AVAILABLE}))
    assert store.get_instance("ti-1").state is InstanceState.AVAILABLE


def test_list_instances_filters_by_learner_and_ticket(store: Store) -> None:
    store.save_instance(_instance("ti-1", "alice", "db-stage-01"))
    store.save_instance(_instance("ti-2", "alice", "db-stage-02"))
    store.save_instance(_instance("ti-3", "bob", "db-stage-01"))

    assert len(store.list_instances()) == 3
    assert {i.id for i in store.list_instances(learner="alice")} == {"ti-1", "ti-2"}
    assert {i.id for i in store.list_instances(ticket_id="db-stage-01")} == {"ti-1", "ti-3"}
    assert {
        i.id for i in store.list_instances(learner="alice", ticket_id="db-stage-01")
    } == {"ti-1"}


def test_list_instances_keeps_a_stable_order(store: Store) -> None:
    """A board that reshuffles on every read is a bug, not a detail.

    The in-memory store gets insertion order for free; the SQL one has to ask for it
    explicitly, so the ordering is asserted here rather than assumed.
    """
    for i in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11):
        store.save_instance(_instance(f"ti-{i}", "alice", "db-stage-01"))
    first = [i.id for i in store.list_instances(learner="alice")]
    assert first == [f"ti-{i}" for i in range(1, 12)]
    assert [i.id for i in store.list_instances(learner="alice")] == first


def test_list_submissions_scoped_and_ordered_by_seq(store: Store) -> None:
    store.save_submission(Submission(id="s2", instance_id="ti-1", seq=2))
    store.save_submission(Submission(id="s1", instance_id="ti-1", seq=1))
    store.save_submission(Submission(id="other", instance_id="ti-2", seq=1))

    assert [s.seq for s in store.list_submissions("ti-1")] == [1, 2]
    assert store.get_submission("s1").id == "s1"
    assert store.get_submission("ghost") is None


def test_report_review_decision_keyed_by_submission(
    store: Store, sample_grading_report: dict[str, Any]
) -> None:
    """Reports/reviews/decisions are keyed by ``submission_id``, not their own ``id``.

    The report has no ``id`` field at all, which is exactly the case where a storage
    layer is tempted to invent one and then fail to read its own row back
    (every model forbids extra keys).
    """
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


def test_next_id_mints_fresh_unique_identities(store: Store) -> None:
    ids = [store.next_id("ti") for _ in range(50)]
    assert len(set(ids)) == 50
    assert ids[0] == "ti-1"
    # Per-prefix, so minting instances never burns submission numbers.
    assert store.next_id("sub") == "sub-1"


def test_next_id_does_not_collide_across_store_instances(clean_postgres: str) -> None:
    """The reason ``next_id`` is on the seam at all.

    ``save_*`` upserts by identity. A counter owned by the process restarts at 1 and the
    next ``save`` would silently overwrite an existing row instead of adding one — the
    §9.2 "a retry is a NEW Submission" invariant breaking without any error. Only a
    store that outlives the process can be asked this question.
    """
    from platform_api.store_postgres import PostgresStore

    first = PostgresStore(clean_postgres)
    assert first.next_id("ti") == "ti-1"
    first.save_instance(_instance("ti-1", "deployer", "db-stage-01"))

    # A brand-new store object == a restarted process (no shared Python state).
    second = PostgresStore(clean_postgres)
    assert second.next_id("ti") == "ti-2"
    second.save_instance(_instance("ti-2", "deployer", "db-stage-01"))
    assert {i.id for i in second.list_instances(learner="deployer")} == {"ti-1", "ti-2"}


def test_next_id_seeds_from_existing_documents_when_sequence_is_lost(
    clean_postgres: str,
) -> None:
    """If ``bagu_id_seq`` is dropped while documents remain, renumbering from 1 would
    overwrite live rows. The schema step seeds the watermark back from the ids it finds."""
    import psycopg

    from platform_api.store_postgres import PostgresStore

    store = PostgresStore(clean_postgres)
    store.save_instance(_instance("ti-41", "deployer", "db-stage-01"))
    with psycopg.connect(clean_postgres) as conn:
        conn.execute("TRUNCATE bagu_id_seq")

    assert PostgresStore(clean_postgres).next_id("ti") == "ti-42"
