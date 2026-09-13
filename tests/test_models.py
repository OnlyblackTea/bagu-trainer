"""Unit tests for platform_service.models (design v0.4 §9.1 / §11).

Beyond construction + JSON round-trip, these lock the v0.4 contraction: every
removed field (``fixture_seed`` / ``signature`` / score / weight / teacher-confirm
/ ``parametric``) must be REJECTED by ``extra="forbid"``, not silently ignored.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from platform_service.models import (
    AcceptanceDecision,
    AcceptanceInputs,
    Enrollment,
    GradingReport,
    InstanceState,
    Recommendation,
    ReportProvenance,
    Review,
    ReviewComment,
    RunnerTrust,
    Submission,
    TicketInstance,
    TicketSpec,
    TrackManifest,
    TrustLevel,
    User,
    Verdict,
)

_INSTANCE_ANCHOR = {
    "id": "ti-1",
    "learner": "deployer",
    "ticket_id": "db-stage-01",
    "ticket_version": 1,
    "engine_version": "v0.1.0",
    "test_version": 1,
}


# --- enums --------------------------------------------------------------------


def test_enum_values_match_design_vocabulary() -> None:
    assert [s.value for s in InstanceState] == [
        "locked",
        "available",
        "in_progress",
        "submitted",
        "grading",
        "passed",
        "failed",
        "stale",
    ]
    assert {level.value for level in TrustLevel} == {"self", "verified", "community"}
    assert {rec.value for rec in Recommendation} == {"approve", "request_changes"}
    assert {verdict.value for verdict in Verdict} == {"pass", "fail"}
    assert [rt.value for rt in RunnerTrust] == ["self"]  # self-study only


# --- content projections ------------------------------------------------------


def test_ticket_spec_projects_from_sample(sample_ticket) -> None:
    spec = TicketSpec.model_validate(sample_ticket)
    assert spec.identity.id == "db-stage-01"
    assert spec.identity.ticket_version == 1
    assert spec.scope.files_to_change
    assert spec.grader.correctness.test_group == "stage01"
    assert spec.mentor_policy.mode == "pure_mentor"
    assert spec.progression.prerequisites == ["db-stage-00"]
    assert [hint.id for hint in spec.hints] == ["H1", "H2"]


def test_track_manifest_projects_from_sample(sample_track) -> None:
    track = TrackManifest.model_validate(sample_track["track"])
    assert track.id == "database-bagu"
    assert track.trust_level is TrustLevel.SELF
    assert track.manifest.stages
    assert track.runtime.digest_pin["mysql"].startswith("sha256:")
    assert track.contribute is not None
    assert track.contribute.review is not None
    assert track.contribute.review.ci == "paper validate-track"


# --- runtime entities ---------------------------------------------------------


def test_ticket_instance_defaults_and_round_trip() -> None:
    inst = TicketInstance.model_validate(_INSTANCE_ANCHOR)
    assert inst.state is InstanceState.LOCKED
    assert inst.created_at.tzinfo is not None
    assert TicketInstance.model_validate_json(inst.model_dump_json()) == inst


def test_submission_seq_defaults_to_one() -> None:
    assert Submission(id="sub-1", instance_id="ti-1").seq == 1


def test_enrollment_and_user_round_trip() -> None:
    enr = Enrollment(id="en-1", learner="deployer", track="database-bagu")
    assert Enrollment.model_validate_json(enr.model_dump_json()) == enr
    usr = User(id="deployer", kind="deployer")
    assert User.model_validate_json(usr.model_dump_json()) == usr


def test_grading_report_from_claim(sample_grading_report) -> None:
    report = GradingReport.model_validate(sample_grading_report)
    assert report.correctness.passed is True
    assert report.provenance.runner_trust is RunnerTrust.SELF
    assert report.provenance.generated_at == "2026-09-07T10:00:00+00:00"
    assert GradingReport.model_validate_json(report.model_dump_json()) == report


def test_provenance_defaults_runner_trust_self() -> None:
    prov = ReportProvenance(generated_at="now", generated_by="paper-cli/0.1.0")
    assert prov.runner_trust is RunnerTrust.SELF


def test_review_is_advisory_without_score() -> None:
    review = Review(
        id="rev-1",
        submission_id="sub-1",
        recommendation=Recommendation.APPROVE,
        comments=[
            ReviewComment(dimension="思路质量", anchor="Foo.java#L10", text="索引列序合理")
        ],
        summary="证据完整，建议通过。",
    )
    assert review.recommendation is Recommendation.APPROVE
    assert review.comments[0].dimension == "思路质量"
    assert Review.model_validate_json(review.model_dump_json()) == review


def test_acceptance_inputs_config_defaults_auto() -> None:
    inputs = AcceptanceInputs(
        correctness_gate=True,
        review_recommendation=Recommendation.APPROVE,
        evidence_trust=TrustLevel.SELF,
    )
    assert inputs.config == "auto"


def test_acceptance_decision_round_trip() -> None:
    decision = AcceptanceDecision(
        id="ad-1",
        submission_id="sub-1",
        inputs=AcceptanceInputs(
            correctness_gate=True,
            review_recommendation=Recommendation.APPROVE,
            evidence_trust=TrustLevel.SELF,
        ),
        verdict=Verdict.PASS,
    )
    assert decision.decided_by == "auto"
    assert AcceptanceDecision.model_validate_json(decision.model_dump_json()) == decision


# --- v0.4 contraction guards: removed fields must be REJECTED -----------------


def test_ticket_instance_rejects_fixture_seed() -> None:
    assert "fixture_seed" not in TicketInstance.model_fields
    with pytest.raises(ValidationError):
        TicketInstance.model_validate({**_INSTANCE_ANCHOR, "fixture_seed": "learner-42"})


def test_grading_report_rejects_signature_and_fixture_seed(sample_grading_report) -> None:
    assert "signature" not in GradingReport.model_fields
    assert "fixture_seed" not in GradingReport.model_fields
    with pytest.raises(ValidationError):
        GradingReport.model_validate({**sample_grading_report, "signature": "deadbeef"})


def test_provenance_rejects_signature(sample_grading_report) -> None:
    assert "signature" not in ReportProvenance.model_fields
    provenance = {**sample_grading_report["provenance"], "signature": "deadbeef"}
    with pytest.raises(ValidationError):
        ReportProvenance.model_validate(provenance)


def test_review_rejects_score_and_comment_rejects_weight() -> None:
    assert "score" not in Review.model_fields
    assert "weight" not in ReviewComment.model_fields
    with pytest.raises(ValidationError):
        Review.model_validate(
            {
                "id": "rev-1",
                "submission_id": "sub-1",
                "recommendation": "approve",
                "summary": "ok",
                "score": 92,
            }
        )
    with pytest.raises(ValidationError):
        ReviewComment.model_validate({"dimension": "代码质量", "text": "good", "weight": 0.4})


def test_acceptance_rejects_teacher_confirm() -> None:
    with pytest.raises(ValidationError):
        AcceptanceInputs.model_validate(
            {
                "correctness_gate": True,
                "review_recommendation": "approve",
                "evidence_trust": "self",
                "config": "teacher-confirm",
            }
        )
    with pytest.raises(ValidationError):
        AcceptanceDecision.model_validate(
            {
                "id": "ad-1",
                "submission_id": "sub-1",
                "inputs": {
                    "correctness_gate": True,
                    "review_recommendation": "approve",
                    "evidence_trust": "self",
                },
                "verdict": "pass",
                "decided_by": "teacher",
            }
        )


def test_ticket_spec_rejects_parametric(sample_ticket) -> None:
    assert "parametric" not in TicketSpec.model_fields
    with pytest.raises(ValidationError):
        TicketSpec.model_validate({**sample_ticket, "parametric": {"fixture_seed": "x"}})


def test_review_retains_engine_anchor_detail_and_incompleteness_signal() -> None:
    """CONTRACTS §5 has no GET /reviews, so the stored Review is the ONLY copy: it must
    retain the anchor triple + engine-corrected quote (契约三) and ``truncated_files``
    (the sole incompleteness signal). New fields are optional, so an unanchored comment
    still constructs from ``(dimension, text)`` alone."""
    review = Review(
        id="rev-1",
        submission_id="sub-1",
        recommendation=Recommendation.REQUEST_CHANGES,
        summary="越界改了测试，建议打回。",
        comments=[
            ReviewComment(
                dimension="security",
                text="这里把用户输入拼进了 SQL",
                anchor="Foo.java#L10",
                path="Foo.java",
                line=10,
                side="right",
                quote='String sql = "select * from t where id=" + id;',
            ),
            ReviewComment(dimension="test_coverage", text="缺并发超卖用例"),
        ],
        dimension_findings=[{"dimension": "correctness", "commentary": "红绿正确"}],
        truncated_files=["Huge.java"],
    )
    assert review.comments[0].path == "Foo.java"
    assert review.comments[0].line == 10
    assert review.comments[0].side == "right"
    assert review.comments[0].quote == 'String sql = "select * from t where id=" + id;'
    assert review.comments[1].anchor is None
    assert review.comments[1].path is None
    assert review.dimension_findings[0].dimension == "correctness"
    assert review.dimension_findings[0].commentary == "红绿正确"
    assert review.truncated_files == ["Huge.java"]
    assert Review.model_validate_json(review.model_dump_json()) == review
