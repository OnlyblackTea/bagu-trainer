"""Deterministic acceptance gate (design v0.5 §11.3).

Decoupled from the advisory AI Review (§11.2): the verdict is a pure function of the
objective correctness result, the Review's recommendation, the evidence's trust tier and
whether the learner touched the **judged surface** — never of an LLM's free-form mood.
``config`` is ``auto`` only (v0.4 removed ``teacher-confirm``), so a decision is
reproducible and auditable from the snapshotted :class:`~platform_service.models.AcceptanceInputs`.

Pure logic: no engine, no HTTP, no DB (D4).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from fnmatch import fnmatch
from typing import Literal

from .models import Recommendation, TrustLevel, Verdict

# Policy constant: which evidence-trust tiers the gate accepts. Self-study is always
# ``self``; ``verified`` / ``community`` cover contributed tracks (§13). Narrow this
# set (e.g. drop COMMUNITY) to harden the gate without touching any call site.
ACCEPTABLE_EVIDENCE_TRUST: frozenset[TrustLevel] = frozenset(
    {TrustLevel.SELF, TrustLevel.VERIFIED, TrustLevel.COMMUNITY}
)


def _normalise(ref: str) -> str:
    return ref.strip().replace("\\", "/").removeprefix("./")


def _covered(path: str, pattern: str) -> bool:
    """Whether ``path`` falls inside one declared judged-surface ``pattern``."""
    if path == pattern or fnmatch(path, pattern):
        return True
    # A directory-style glob (``.../stage*/``) must also cover what is *inside* it:
    # fnmatch requires the trailing slash to be present in the path, which it never is.
    if pattern.endswith("/") and fnmatch(path, pattern + "*"):
        return True
    # ``**/x`` must also match a root-level ``x`` (fnmatch needs the slash to exist).
    if fnmatch(f"/{path}", pattern):
        return True
    # A bare directory (``src/test``) protects everything under it.
    base = pattern.rstrip("/")
    return bool(base) and (path == base or path.startswith(base + "/"))


def find_tampering(changed: Iterable[str], protected: Iterable[str]) -> list[str]:
    """Return the changed paths that fall inside the Track's **judged surface** (§11.3).

    The surface is *declared by the Track* (``runtime.protected_paths``) rather than
    inferred, because only the content author knows what the verdict actually depends on
    (canonical tests, build config, fixtures, the track's own infra). A non-empty result
    **fails the gate**: with §7.3 (runtime test injection) still unimplemented the
    canonical tests live inside the learner's repo, and this is what keeps "edit the test
    until it goes green" from being a free pass.

    Matching is deliberately generous — fnmatch glob plus directory prefix. A *missed*
    pattern is a silent hole (the failure mode this whole function exists to close), while
    an over-broad pattern is merely annoying and the author narrows it later.
    """
    patterns = [p for p in (_normalise(p) for p in protected) if p]
    hits = {
        _normalise(raw)
        for raw in changed
        if _normalise(raw) and any(_covered(_normalise(raw), p) for p in patterns)
    }
    return sorted(hits)


def decide_acceptance(
    *,
    correctness_gate: bool,
    review_recommendation: Recommendation,
    evidence_trust: TrustLevel,
    tampering: Sequence[str] = (),
    config: Literal["auto"] = "auto",
) -> Verdict:
    """Return the deterministic verdict for one submission (§11.3).

    ``pass`` iff the objective correctness gate is green AND the Review recommends
    approval AND the evidence trust tier is acceptable AND the learner did **not** touch
    the judged surface; otherwise ``fail``. Pure and side-effect free. ``config`` is the
    audit-snapshotted decision mode — ``auto`` is the only mode, so it does not branch.
    """
    accepted = (
        correctness_gate
        and review_recommendation == Recommendation.APPROVE
        and evidence_trust in ACCEPTABLE_EVIDENCE_TRUST
        and not tampering
    )
    return Verdict.PASS if accepted else Verdict.FAIL
