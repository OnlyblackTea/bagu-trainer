"""Orchestration facade for the platform control plane (design v0.5 §9.2 / §11.3).

:class:`PlatformService` threads the pure slices — :mod:`~platform_service.store`,
:mod:`~platform_service.lifecycle`, :mod:`~platform_service.unlock` and
:mod:`~platform_service.acceptance` — into one end-to-end self-study loop that is
fully unit-testable offline:

    enroll → start → submit → review → decide → (pass ⇒ unlock | fail ⇒ retry)

It owns identity minting and the version anchor (``engine_version`` here; the ticket
supplies ``ticket_version`` / ``test_version``). The AI Review and the grading report
are *inputs* the caller hands in — generating them needs a real backend and is out of scope
for this slice. No engine, no HTTP, no DB (D4).
"""

from __future__ import annotations

import itertools

from .acceptance import decide_acceptance, find_tampering
from .lifecycle import record_submission, transition
from .lifecycle import reissue as reissue_instance
from .models import (
    AcceptanceDecision,
    AcceptanceInputs,
    Enrollment,
    GradingReport,
    InstanceState,
    Review,
    Submission,
    TicketInstance,
    TicketSpec,
    TrackManifest,
    Verdict,
)
from .store import Store
from .unlock import build_prereq_graph, initial_state, unlock_dependents


def _assert_identity_keys(tickets: dict[str, TicketSpec]) -> None:
    """Refuse a ticket map whose keys are not each ticket's ``identity.id``.

    See :class:`PlatformService` for why: a stem-style key (a stage *directory* name)
    would decouple the unlock gate from the tickets it gates without any visible error.
    """
    diverged = sorted(
        f"{key!r} -> {ticket.identity.id!r}"
        for key, ticket in tickets.items()
        if key != ticket.identity.id
    )
    if diverged:
        raise ValueError(
            "ticket map keys must be ticket.identity.id "
            f"(the prerequisite DAG keys on it); diverged: {diverged}"
        )


class PlatformService:
    """Single-entry orchestration over one track's tickets for one store.

    ``tickets`` maps ``ticket_id → TicketSpec``; the prerequisites DAG is derived once
    at construction. Every mutation persists through the :class:`Store` seam, so a
    DB-backed store can replace :class:`~platform_service.store.InMemoryStore` without
    touching this class.

    Invariant: every key must equal its ``ticket.identity.id``. :meth:`enroll` keys
    instances on the mapping key while :func:`~platform_service.unlock.build_prereq_graph`
    keys the DAG on ``identity.id`` — if those diverge, dependents silently mint
    ``available`` instead of ``locked`` and the prerequisite gate disappears. Enforced
    here rather than trusted, because the divergence is invisible at the call site.
    """

    def __init__(
        self,
        store: Store,
        track: TrackManifest,
        tickets: dict[str, TicketSpec],
        *,
        engine_version: str,
    ) -> None:
        self._store = store
        self._track = track
        self._tickets = tickets
        self._engine_version = engine_version
        _assert_identity_keys(tickets)
        self._prereq_graph = build_prereq_graph(tickets.values())
        self._ids = itertools.count(1)

    # --- identity ------------------------------------------------------------

    def _next_id(self, prefix: str) -> str:
        return f"{prefix}-{next(self._ids)}"

    def _must_get_instance(self, instance_id: str) -> TicketInstance:
        instance = self._store.get_instance(instance_id)
        if instance is None:
            raise KeyError(f"unknown instance: {instance_id}")
        return instance

    def _must_get_submission(self, submission_id: str) -> Submission:
        submission = self._store.get_submission(submission_id)
        if submission is None:
            raise KeyError(f"unknown submission: {submission_id}")
        return submission

    # --- lifecycle operations ------------------------------------------------

    def enroll(self, learner: str) -> Enrollment:
        """Admit ``learner`` and mint one TicketInstance per ticket (§9.1).

        Each instance starts at its DAG-derived :func:`initial_state` (root tickets
        ``available``, the rest ``locked``) and is anchored to the current versions.
        """
        enrollment = Enrollment(
            id=self._next_id("en"), learner=learner, track=self._track.id
        )
        self._store.save_enrollment(enrollment)
        for ticket_id, ticket in self._tickets.items():
            instance = TicketInstance(
                id=self._next_id("ti"),
                learner=learner,
                ticket_id=ticket_id,
                ticket_version=ticket.identity.ticket_version,
                engine_version=self._engine_version,
                test_version=ticket.grader.correctness.test_version,
                state=initial_state(ticket_id, self._prereq_graph),
            )
            self._store.save_instance(instance)
        return enrollment

    def start(self, instance_id: str) -> TicketInstance:
        """``available → in_progress``: the learner picks up the ticket."""
        instance = self._must_get_instance(instance_id)
        transition(instance, InstanceState.IN_PROGRESS)
        self._store.save_instance(instance)
        return instance

    def submit(
        self,
        instance_id: str,
        *,
        grading_report: GradingReport,
        pr_url: str | None = None,
        defense: str | None = None,
    ) -> Submission:
        """``in_progress → submitted``: record a PR-as-defense attempt (§11).

        The Submission ``seq`` auto-increments over the instance's history, so a retry
        yields a NEW Submission (never overwrites). The untrusted grading report is
        re-keyed to the minted submission id before it is stored.
        """
        instance = self._must_get_instance(instance_id)
        seq = len(self._store.list_submissions(instance_id)) + 1
        submission = Submission(
            id=self._next_id("sub"),
            instance_id=instance_id,
            seq=seq,
            pr_url=pr_url,
            defense_text=defense,
        )
        record_submission(instance, submission)
        self._store.save_instance(instance)
        self._store.save_submission(submission)
        self._store.save_report(
            grading_report.model_copy(update={"submission_id": submission.id})
        )
        return submission

    def review(self, submission_id: str, review: Review) -> Review:
        """``submitted → grading``: store the advisory AI Review (§11.2).

        The Review carries only a ``recommendation`` — no score. It feeds the gate but
        never decides it alone.
        """
        submission = self._must_get_submission(submission_id)
        instance = self._must_get_instance(submission.instance_id)
        transition(instance, InstanceState.GRADING)
        self._store.save_instance(instance)
        aligned = review.model_copy(update={"submission_id": submission_id})
        self._store.save_review(aligned)
        return aligned

    def decide(self, submission_id: str) -> AcceptanceDecision:
        """``grading → passed | failed`` via the deterministic gate (§11.3).

        Consumes the report's ``correctness.passed``, the Review's recommendation, the
        track's ``trust_level`` as evidence trust, and whether the learner touched the
        track's **judged surface** (``runtime.protected_paths``) — the last one is a hard
        fail regardless of red/green (§11.3). On ``pass`` the instance goes ``passed`` and
        its DAG dependents unlock; on ``fail`` it goes ``failed`` (retry path). The
        snapshotted inputs make the verdict auditable.
        """
        submission = self._must_get_submission(submission_id)
        instance = self._must_get_instance(submission.instance_id)
        report = self._store.get_report(submission_id)
        review = self._store.get_review(submission_id)
        if report is None or review is None:
            raise ValueError(
                f"submission {submission_id} needs both a grading report and a review"
            )

        # 判定面被改动 ⇒ 硬门 fail。注意这条**不依赖** AI 评审：它是确定性的（§11.3）。
        tampering = find_tampering(report.files_changed, self._track.runtime.protected_paths)
        inputs = AcceptanceInputs(
            correctness_gate=report.correctness.passed,
            review_recommendation=review.recommendation,
            evidence_trust=self._track.trust_level,
            tampering=tampering,
        )
        verdict = decide_acceptance(
            correctness_gate=inputs.correctness_gate,
            review_recommendation=inputs.review_recommendation,
            evidence_trust=inputs.evidence_trust,
            tampering=inputs.tampering,
            config=inputs.config,
        )
        decision = AcceptanceDecision(
            id=self._next_id("ad"),
            submission_id=submission_id,
            inputs=inputs,
            verdict=verdict,
        )
        transition(
            instance,
            InstanceState.PASSED if verdict is Verdict.PASS else InstanceState.FAILED,
        )
        self._store.save_instance(instance)
        self._store.save_decision(decision)

        if verdict is Verdict.PASS:
            for unlocked in unlock_dependents(
                instance.ticket_id,
                self._prereq_graph,
                self._store.list_instances(learner=instance.learner),
            ):
                self._store.save_instance(unlocked)
        return decision

    def retry(self, instance_id: str) -> TicketInstance:
        """``failed → in_progress``: reopen for another attempt (§9.2).

        The anchor is preserved; the next :meth:`submit` mints a Submission with an
        incremented ``seq``.
        """
        instance = self._must_get_instance(instance_id)
        transition(instance, InstanceState.IN_PROGRESS)
        self._store.save_instance(instance)
        return instance

    def reissue(
        self,
        learner: str,
        ticket_id: str,
        *,
        ticket_version: int,
        test_version: int,
        engine_version: str,
    ) -> TicketInstance:
        """Park the live instance as ``stale`` and mint one anchoring new versions.

        Used when the ticket/test/engine version a PR was based on drifts (§9.2). The
        fresh instance restarts at its DAG-derived initial state; the stale one is kept
        for audit.
        """
        live = [
            inst
            for inst in self._store.list_instances(learner=learner, ticket_id=ticket_id)
            if inst.state is not InstanceState.STALE
        ]
        if not live:
            raise ValueError(f"no live instance for learner {learner} ticket {ticket_id}")
        old, fresh = reissue_instance(
            live[-1],
            new_id=self._next_id("ti"),
            ticket_version=ticket_version,
            test_version=test_version,
            engine_version=engine_version,
            state=initial_state(ticket_id, self._prereq_graph),
        )
        self._store.save_instance(old)
        self._store.save_instance(fresh)
        return fresh
