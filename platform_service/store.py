"""Persistence seam for the platform control plane (design v0.4 §9.1).

:class:`Store` fixes the *shape* of persistence; :class:`InMemoryStore` is the
dict-backed implementation used by tests and single-process self-study. A
PostgreSQL/SQLAlchemy backend can land later (with the ``[service]`` extra) without
changing this Protocol — the rest of the platform depends only on the seam.

Pure in-memory logic: no engine, no HTTP, no real DB (GPL boundary intact).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .models import (
    AcceptanceDecision,
    Enrollment,
    GradingReport,
    Review,
    Submission,
    TicketInstance,
    User,
)


@runtime_checkable
class Store(Protocol):
    """Structural persistence contract for every runtime entity (§9.1).

    ``get_*`` returns ``None`` when absent; ``save_*`` upserts by identity.
    Reports, reviews and decisions are keyed by ``submission_id`` — there is at
    most one of each per submission, because a retry is a NEW Submission (§9.2).
    """

    def save_user(self, user: User) -> None:
        ...

    def get_user(self, user_id: str) -> User | None:
        ...

    def save_enrollment(self, enrollment: Enrollment) -> None:
        ...

    def get_enrollment(self, enrollment_id: str) -> Enrollment | None:
        ...

    def save_instance(self, instance: TicketInstance) -> None:
        ...

    def get_instance(self, instance_id: str) -> TicketInstance | None:
        ...

    def list_instances(
        self, *, learner: str | None = None, ticket_id: str | None = None
    ) -> list[TicketInstance]:
        ...

    def save_submission(self, submission: Submission) -> None:
        ...

    def get_submission(self, submission_id: str) -> Submission | None:
        ...

    def list_submissions(self, instance_id: str) -> list[Submission]:
        ...

    def save_report(self, report: GradingReport) -> None:
        ...

    def get_report(self, submission_id: str) -> GradingReport | None:
        ...

    def save_review(self, review: Review) -> None:
        ...

    def get_review(self, submission_id: str) -> Review | None:
        ...

    def save_decision(self, decision: AcceptanceDecision) -> None:
        ...

    def get_decision(self, submission_id: str) -> AcceptanceDecision | None:
        ...


class InMemoryStore(Store):
    """Dict-backed :class:`Store` for tests and single-process self-study (§9.1).

    Insertion order is preserved; ``list_submissions`` re-sorts by ``seq`` so the
    retry history reads chronologically regardless of save order.
    """

    def __init__(self) -> None:
        self._users: dict[str, User] = {}
        self._enrollments: dict[str, Enrollment] = {}
        self._instances: dict[str, TicketInstance] = {}
        self._submissions: dict[str, Submission] = {}
        self._reports: dict[str, GradingReport] = {}
        self._reviews: dict[str, Review] = {}
        self._decisions: dict[str, AcceptanceDecision] = {}

    def save_user(self, user: User) -> None:
        self._users[user.id] = user

    def get_user(self, user_id: str) -> User | None:
        return self._users.get(user_id)

    def save_enrollment(self, enrollment: Enrollment) -> None:
        self._enrollments[enrollment.id] = enrollment

    def get_enrollment(self, enrollment_id: str) -> Enrollment | None:
        return self._enrollments.get(enrollment_id)

    def save_instance(self, instance: TicketInstance) -> None:
        self._instances[instance.id] = instance

    def get_instance(self, instance_id: str) -> TicketInstance | None:
        return self._instances.get(instance_id)

    def list_instances(
        self, *, learner: str | None = None, ticket_id: str | None = None
    ) -> list[TicketInstance]:
        matches: list[TicketInstance] = []
        for instance in self._instances.values():
            if learner is not None and instance.learner != learner:
                continue
            if ticket_id is not None and instance.ticket_id != ticket_id:
                continue
            matches.append(instance)
        return matches

    def save_submission(self, submission: Submission) -> None:
        self._submissions[submission.id] = submission

    def get_submission(self, submission_id: str) -> Submission | None:
        return self._submissions.get(submission_id)

    def list_submissions(self, instance_id: str) -> list[Submission]:
        matches = [s for s in self._submissions.values() if s.instance_id == instance_id]
        return sorted(matches, key=lambda s: s.seq)

    def save_report(self, report: GradingReport) -> None:
        self._reports[report.submission_id] = report

    def get_report(self, submission_id: str) -> GradingReport | None:
        return self._reports.get(submission_id)

    def save_review(self, review: Review) -> None:
        self._reviews[review.submission_id] = review

    def get_review(self, submission_id: str) -> Review | None:
        return self._reviews.get(submission_id)

    def save_decision(self, decision: AcceptanceDecision) -> None:
        self._decisions[decision.submission_id] = decision

    def get_decision(self, submission_id: str) -> AcceptanceDecision | None:
        return self._decisions.get(submission_id)
