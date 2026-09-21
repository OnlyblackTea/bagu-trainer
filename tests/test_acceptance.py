"""Unit tests for platform_service.acceptance (design v0.5 §11.3 deterministic gate)."""

from __future__ import annotations

import pytest

from platform_service import acceptance
from platform_service.models import Recommendation, TrustLevel, Verdict


@pytest.mark.parametrize(
    ("gate", "rec", "expected"),
    [
        (True, Recommendation.APPROVE, Verdict.PASS),
        (True, Recommendation.REQUEST_CHANGES, Verdict.FAIL),
        (False, Recommendation.APPROVE, Verdict.FAIL),
        (False, Recommendation.REQUEST_CHANGES, Verdict.FAIL),
    ],
)
def test_truth_table(gate: bool, rec: Recommendation, expected: Verdict) -> None:
    verdict = acceptance.decide_acceptance(
        correctness_gate=gate,
        review_recommendation=rec,
        evidence_trust=TrustLevel.SELF,
    )
    assert verdict is expected


@pytest.mark.parametrize("trust", sorted(acceptance.ACCEPTABLE_EVIDENCE_TRUST))
def test_every_acceptable_trust_tier_can_pass(trust: TrustLevel) -> None:
    verdict = acceptance.decide_acceptance(
        correctness_gate=True,
        review_recommendation=Recommendation.APPROVE,
        evidence_trust=trust,
    )
    assert verdict is Verdict.PASS


def test_unacceptable_trust_tier_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hardening the policy set (dropping COMMUNITY) makes a community track fail."""
    monkeypatch.setattr(
        acceptance,
        "ACCEPTABLE_EVIDENCE_TRUST",
        frozenset({TrustLevel.SELF, TrustLevel.VERIFIED}),
    )
    verdict = acceptance.decide_acceptance(
        correctness_gate=True,
        review_recommendation=Recommendation.APPROVE,
        evidence_trust=TrustLevel.COMMUNITY,
    )
    assert verdict is Verdict.FAIL


def test_config_defaults_to_auto_and_is_accepted() -> None:
    verdict = acceptance.decide_acceptance(
        correctness_gate=True,
        review_recommendation=Recommendation.APPROVE,
        evidence_trust=TrustLevel.SELF,
        config="auto",
    )
    assert verdict is Verdict.PASS


def test_decision_is_deterministic() -> None:
    first = acceptance.decide_acceptance(
        correctness_gate=True,
        review_recommendation=Recommendation.APPROVE,
        evidence_trust=TrustLevel.SELF,
    )
    second = acceptance.decide_acceptance(
        correctness_gate=True,
        review_recommendation=Recommendation.APPROVE,
        evidence_trust=TrustLevel.SELF,
    )
    assert first is second is Verdict.PASS
