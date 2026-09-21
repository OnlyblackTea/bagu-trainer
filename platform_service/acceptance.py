"""Deterministic acceptance gate (design v0.5 §11.3).

Decoupled from the advisory AI Review (§11.2): the verdict is a pure function of the
objective correctness result, the Review's recommendation, and the evidence's trust
tier — never of an LLM's free-form mood. ``config`` is ``auto`` only (v0.4 removed
``teacher-confirm``), so a decision is reproducible and auditable from the snapshotted
:class:`~platform_service.models.AcceptanceInputs`.

Pure logic: no engine, no HTTP, no DB (D4).
"""

from __future__ import annotations

from typing import Literal

from .models import Recommendation, TrustLevel, Verdict

# Policy constant: which evidence-trust tiers the gate accepts. Self-study is always
# ``self``; ``verified`` / ``community`` cover contributed tracks (§13). Narrow this
# set (e.g. drop COMMUNITY) to harden the gate without touching any call site.
ACCEPTABLE_EVIDENCE_TRUST: frozenset[TrustLevel] = frozenset(
    {TrustLevel.SELF, TrustLevel.VERIFIED, TrustLevel.COMMUNITY}
)


def decide_acceptance(
    *,
    correctness_gate: bool,
    review_recommendation: Recommendation,
    evidence_trust: TrustLevel,
    config: Literal["auto"] = "auto",
) -> Verdict:
    """Return the deterministic verdict for one submission (§11.3).

    ``pass`` iff the objective correctness gate is green AND the Review recommends
    approval AND the evidence trust tier is acceptable; otherwise ``fail``. Pure and
    side-effect free. ``config`` is the audit-snapshotted decision mode — ``auto`` is
    the only mode in v0.4, so it does not branch here.
    """
    accepted = (
        correctness_gate
        and review_recommendation == Recommendation.APPROVE
        and evidence_trust in ACCEPTABLE_EVIDENCE_TRUST
    )
    return Verdict.PASS if accepted else Verdict.FAIL
