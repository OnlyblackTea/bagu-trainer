"""Domain models for the platform control plane (design v0.5 §9.1 / §11).

Pure Pydantic v2 data — no engine, no HTTP, no DB. Every model forbids extra keys,
so a stale ``parametric`` / ``fixture_seed`` / ``signature`` / score field from the
pre-v0.4 design fails loudly at validation instead of silently riding along.

Two trust axes that must not be conflated:

* :class:`TrustLevel` (self|verified|community) — track supply-chain trust (§13).
* :class:`RunnerTrust` (always ``self``) — honest marker that grading self-ran (§7.4).
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class TrustLevel(StrEnum):
    """Track supply-chain trust tier (§4.2/§13)."""

    SELF = "self"
    VERIFIED = "verified"
    COMMUNITY = "community"


class InstanceState(StrEnum):
    """TicketInstance lifecycle state (§9.2)."""

    LOCKED = "locked"
    AVAILABLE = "available"
    IN_PROGRESS = "in_progress"
    SUBMITTED = "submitted"
    GRADING = "grading"
    PASSED = "passed"
    FAILED = "failed"
    STALE = "stale"


class Recommendation(StrEnum):
    """AI Review advisory outcome (§11.2) — a recommendation, NOT a score."""

    APPROVE = "approve"
    REQUEST_CHANGES = "request_changes"


class Verdict(StrEnum):
    """AcceptanceDecision deterministic gate outcome (§11.3)."""

    PASS = "pass"
    FAIL = "fail"


class RunnerTrust(StrEnum):
    """Who ran the grading (§7.4). Self-study is always ``self`` — an honest
    'this evidence is a self-report' marker, not a security boundary."""

    SELF = "self"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class _Strict(BaseModel):
    """Base for every model: unknown keys are rejected, never ignored."""

    model_config = ConfigDict(extra="forbid")


# --- Content projections (§4.2 track / §4.3 ticket), validated from YAML -------


class TicketIdentity(_Strict):
    """Version anchors pinned into the TicketInstance at issue time (§9.1)."""

    id: str
    schema_version: int = Field(ge=1)
    ticket_version: int = Field(ge=1)
    stage: int = Field(ge=0)
    title: str


class TicketBrief(_Strict):
    problem: str
    concepts: list[str] = Field(default_factory=list)


class TicketScope(_Strict):
    """D1: the graded surface — no AI component in the platform writes these (§1.2)."""

    files_to_change: list[str] = Field(min_length=1)


class GraderCorrectness(_Strict):
    test_group: str
    test_version: int = Field(ge=1)
    canonical_test: str
    expect: str | None = None


class GraderSpec(_Strict):
    """Objective red/green criteria, run locally; result is UNTRUSTED (§7.4)."""

    correctness: GraderCorrectness
    performance: dict[str, Any] | None = None
    concurrency: dict[str, Any] | None = None


class Hint(_Strict):
    id: str
    text: str


class MentorPolicy(_Strict):
    """§4.3 / D1: ``may_write`` must not intersect ``scope.files_to_change``.

    Kept in the schema after v0.5 (the mentor/executor role is gone): published Tracks
    still declare it and a future advisory gadget may read it. See design §4.3.
    """

    mode: Literal["pure_mentor", "mentor_then_reveal"]
    may_write: list[str] = Field(default_factory=list)


class Progression(_Strict):
    """v0.3 reverses unlock_next: a ticket declares WHO IT DEPENDS ON (§4.3)."""

    prerequisites: list[str] = Field(default_factory=list)


class TicketSpec(_Strict):
    """A whole ticket.yaml, projected concern-by-concern (§4.3)."""

    identity: TicketIdentity
    brief: TicketBrief
    scope: TicketScope
    grader: GraderSpec
    mentor_policy: MentorPolicy
    hints: list[Hint] = Field(default_factory=list)
    submission: dict[str, Any] | None = None
    progression: Progression = Field(default_factory=Progression)


class TrackRuntime(_Strict):
    """EXECUTABLE entry points — the supply-chain attack surface (§4.2/§4.6).
    Every declared entry must be covered by ``digest_pin`` (checked in admission)."""

    compose: str | None = None
    build: str | None = None
    tests: str | None = None
    sim: str | None = None
    digest_pin: dict[str, str] = Field(min_length=1)


class TrackManifestBlock(_Strict):
    """INERT declarative metadata; the platform reads it, never executes it."""

    requires: dict[str, Any] = Field(default_factory=dict)
    skeleton_repo: str
    bagu_map: str | None = None
    stages: list[str] = Field(min_length=1)


class TrackReview(_Strict):
    ci: str | None = None
    signed_by: list[str] = Field(default_factory=list)


class TrackContribute(_Strict):
    license: str | None = None
    maintainers: list[str] = Field(default_factory=list)
    review: TrackReview | None = None


class TrackManifest(_Strict):
    """A whole track — the top-level ``track`` object of track.yaml (§4.2)."""

    id: str
    name: str
    language: str | None = None
    trust_level: TrustLevel
    manifest: TrackManifestBlock
    runtime: TrackRuntime
    contribute: TrackContribute | None = None


# --- Runtime entities (§9.1) --------------------------------------------------


class User(_Strict):
    """``deployer`` = the learner (sole user); ``contributor`` = community author."""

    id: str
    kind: Literal["deployer", "contributor"]


class Enrollment(_Strict):
    """Admits a learner to a track. The unlocked set is DERIVED from instance
    states, never stored here (§9.1)."""

    id: str
    learner: str
    track: str
    created_at: datetime = Field(default_factory=_utcnow)


class TicketInstance(_Strict):
    """Version-frozen assessment (§9.1): the 5-tuple anchor answers 'which version
    is this PR/report based on'. NO ``fixture_seed`` — parametric removed in v0.4."""

    id: str
    learner: str
    ticket_id: str
    ticket_version: int = Field(ge=1)
    engine_version: str
    test_version: int = Field(ge=1)
    state: InstanceState = InstanceState.LOCKED
    created_at: datetime = Field(default_factory=_utcnow)


class Submission(_Strict):
    """One PR-as-defense attempt (§11). ``seq`` increments on retry within the
    same instance (§9.2)."""

    id: str
    instance_id: str
    seq: int = Field(ge=1, default=1)
    pr_url: str | None = None
    diff_ref: str | None = None
    defense_text: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)


class ReportProvenance(_Strict):
    """Where the grading claim came from (§7.4). ``generated_at`` stays a raw
    string — untrusted self-report, stored verbatim, not normalized. NO
    ``signature`` and NO ``fixture_seed`` (both removed in v0.4)."""

    generated_at: str
    generated_by: str
    host_env: dict[str, Any] = Field(default_factory=dict)
    runner_trust: RunnerTrust = RunnerTrust.SELF


class ReportCorrectness(_Strict):
    test_group: str
    passed: bool
    tests: list[dict[str, Any]] = Field(default_factory=list)


class GradingReport(_Strict):
    """Local grading result — an UNTRUSTED claim carrying provenance (§7.4).
    NO ``signature``, NO ``fixture_seed`` (removed in v0.4)."""

    submission_id: str
    ticket: str
    ticket_version: int = Field(ge=1)
    test_version: int = Field(ge=1)
    engine_version: str
    learner: str
    provenance: ReportProvenance
    correctness: ReportCorrectness
    performance: dict[str, Any] | None = None
    concurrency: dict[str, Any] | None = None
    lint: dict[str, Any] = Field(default_factory=dict)
    files_changed: list[str] = Field(default_factory=list)
    out_of_scope_edits: list[str] = Field(default_factory=list)


class ReviewComment(_Strict):
    """One anchored, qualitative note (§11.2). NO weight/score — the rubric's
    scoring was removed in v0.4; dimensions survive as a checklist only.

    The anchor is the frozen triple ``(path, line, side)`` (design §11.2) plus the
    backend-corrected ``quote`` (the anchor line's real text). ``anchor`` is a
    platform-built display string, never parsed back from the engine's convenience
    string. All anchor fields are ``None`` for an unanchored comment. They are retained
    verbatim because §5 exposes no GET /reviews — the stored Review is the only copy.
    """

    dimension: str
    anchor: str | None = None
    text: str
    path: str | None = None
    line: int | None = None
    side: Literal["left", "right"] | None = None
    quote: str | None = None


class DimensionFinding(_Strict):
    """Per-dimension commentary from the AI Review (design §11.2 ``dimension_findings``).

    A qualitative checklist verdict per rubric dimension (§11.2) — NO score."""

    dimension: str
    commentary: str


class Review(_Strict):
    """AI Review — advisory, non-deterministic (§11.2). Its ``recommendation``
    feeds the gate but carries NO numeric score.

    ``truncated_files`` is the sole signal that the review was
    incomplete (a file was dropped whole for exceeding the char limit);
    ``dimension_findings`` retains the per-dimension commentary. Both are persisted
    because a review is not retrievable — this Review is the only copy the platform has.
    """

    id: str
    submission_id: str
    recommendation: Recommendation
    comments: list[ReviewComment] = Field(default_factory=list)
    summary: str
    dimension_findings: list[DimensionFinding] = Field(default_factory=list)
    truncated_files: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_utcnow)


class AcceptanceInputs(_Strict):
    """The exact inputs the gate consumed — makes the decision auditable (§11.3).
    ``config`` is ``auto`` only; ``teacher-confirm`` was removed in v0.4."""

    correctness_gate: bool
    review_recommendation: Recommendation
    evidence_trust: TrustLevel
    config: Literal["auto"] = "auto"


class AcceptanceDecision(_Strict):
    """Deterministic, auditable acceptance gate (§11.3), decoupled from the
    advisory Review so unlock never hinges on an LLM's mood."""

    id: str
    submission_id: str
    inputs: AcceptanceInputs
    verdict: Verdict
    decided_by: Literal["auto"] = "auto"
    created_at: datetime = Field(default_factory=_utcnow)
