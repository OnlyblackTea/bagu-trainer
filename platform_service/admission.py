"""Track admission gate — the library behind ``paper validate-track`` (design v0.5 §4.6).

Runs the STATIC half of admission on a track directory: schema validity, skeleton
file presence, bagu-map anchors, runtime digest-pinning, trust-level endorsement, the
D1 role-reversal invariant, manifest coverage of every ticket directory, and a coarse
spoiler filter. The check that needs real compute — running the canonical tests for
*discriminance* (red on skeleton / green on reference) — is delegated to an injected
:class:`DiscriminanceRunner`; absent one it is recorded as **skipped** rather than
failed, so this module stays pure and offline.

The ``paper validate-track`` CLI wrapper lives in ``paper_cli`` (out of scope here).
No engine, no HTTP, no DB (D4).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from .content_loader import (
    ContentValidationError,
    discover_ticket_paths,
    load_tickets,
    load_track,
)
from .models import TicketSpec, TrackManifest, TrustLevel

# Executable entry points declared under track.runtime (§4.2); each must be pinned.
_RUNTIME_ENTRIES = ("compose", "build", "tests", "sim")


@dataclass(frozen=True)
class AdmissionCheck:
    """One gate check. ``hard`` failures block admission; soft ones only warn."""

    name: str
    passed: bool
    message: str
    hard: bool = True


@dataclass(frozen=True)
class AdmissionReport:
    """All checks for one track; :attr:`ok` considers hard checks only."""

    checks: list[AdmissionCheck] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(check.passed for check in self.checks if check.hard)

    def get(self, name: str) -> AdmissionCheck | None:
        return next((check for check in self.checks if check.name == name), None)


@runtime_checkable
class DiscriminanceRunner(Protocol):
    """Runs the canonical tests for discriminance (§4.6, the core check).

    ``run()`` returns ``(red_on_skeleton, green_on_reference)``: the tests must FAIL on
    the skeleton and PASS on the reference solution — otherwise the test is waste
    ("always red" / "always green"). The real mvn/docker runner is CI-side and out of
    scope; unit tests inject a fake.
    """

    def run(self) -> tuple[bool, bool]:
        ...


def validate_track(
    track_dir: str | Path, *, discriminance_runner: DiscriminanceRunner | None = None
) -> AdmissionReport:
    """Statically validate a track directory and return its :class:`AdmissionReport`."""
    root = Path(track_dir)
    checks: list[AdmissionCheck] = []

    try:
        track = load_track(root / "track.yaml")
        tickets = list(load_tickets(root).values())
    except ContentValidationError as exc:
        checks.append(AdmissionCheck("schema", False, str(exc), True))
        return AdmissionReport(checks)
    checks.append(
        AdmissionCheck("schema", True, f"track + {len(tickets)} ticket(s) schema-valid", True)
    )

    checks.append(_check_manifest_coverage(root, track))
    checks.append(_check_files_to_change(root, track, tickets))
    checks.append(_check_bagu_map_anchors(root, track, tickets))
    checks.append(_check_runtime_pinned(track))
    checks.append(_check_trust_level(track))
    checks.append(_check_d1(tickets))
    checks.append(_check_no_spoiler(root, tickets))
    checks.append(_check_discriminance(discriminance_runner))
    return AdmissionReport(checks)


def _resolve(root: Path, ref: str) -> Path:
    return root / (ref[2:] if ref.startswith("./") else ref)


def _check_manifest_coverage(root: Path, track: TrackManifest) -> AdmissionCheck:
    """Every discovered ticket directory must be declared in ``manifest.stages``.

    Deliberately one-directional: the manifest is a *board* list and may name a stage
    that ships no ticket (``stage-00-onboarding`` is an onboarding/environment stage,
    not an assessed one). The reverse — a ticket directory nobody declared — is a hard
    failure, because that ticket would be missing from the board while still being
    minted an instance, and its stage branch would never be gated.
    """
    declared = set(track.manifest.stages)
    undeclared = sorted(
        path.parent.name
        for path in discover_ticket_paths(root)
        if path.parent.name not in declared
    )
    message = (
        "every ticket directory is declared in manifest.stages"
        if not undeclared
        else f"ticket dirs missing from manifest.stages: {undeclared}"
    )
    return AdmissionCheck("manifest_coverage", not undeclared, message, True)


def _check_files_to_change(
    root: Path, track: TrackManifest, tickets: list[TicketSpec]
) -> AdmissionCheck:
    skeleton = _resolve(root, track.manifest.skeleton_repo)
    missing = [
        f"{ticket.identity.id}:{rel}"
        for ticket in tickets
        for rel in ticket.scope.files_to_change
        if not (skeleton / rel).exists()
    ]
    message = "all files_to_change exist in skeleton" if not missing else f"missing: {missing}"
    return AdmissionCheck("files_to_change", not missing, message, True)


def _check_bagu_map_anchors(
    root: Path, track: TrackManifest, tickets: list[TicketSpec]
) -> AdmissionCheck:
    if not track.manifest.bagu_map:
        return AdmissionCheck("bagu_map_anchor", True, "no bagu_map declared", True)
    anchors = _anchors_in(_resolve(root, track.manifest.bagu_map))
    unresolved: list[str] = []
    for ticket in tickets:
        ref = (ticket.submission or {}).get("bagu_map_ref")
        if not ref or "#" not in ref:
            continue
        if ref.split("#", 1)[1] not in anchors:
            unresolved.append(f"{ticket.identity.id}:{ref}")
    message = (
        "all bagu_map_ref anchors resolve" if not unresolved else f"unresolved: {unresolved}"
    )
    return AdmissionCheck("bagu_map_anchor", not unresolved, message, True)


def _check_runtime_pinned(track: TrackManifest) -> AdmissionCheck:
    declared = [name for name in _RUNTIME_ENTRIES if getattr(track.runtime, name)]
    uncovered = [name for name in declared if name not in track.runtime.digest_pin]
    message = (
        "every executable entry is digest-pinned"
        if not uncovered
        else f"entries without digest_pin: {uncovered}"
    )
    return AdmissionCheck("runtime_pinned", not uncovered, message, True)


def _check_trust_level(track: TrackManifest) -> AdmissionCheck:
    if track.trust_level is TrustLevel.VERIFIED:
        review = track.contribute.review if track.contribute else None
        signed_by = review.signed_by if review else []
        message = (
            "verified track endorsed by maintainers"
            if signed_by
            else "verified requires non-empty contribute.review.signed_by"
        )
        return AdmissionCheck("trust_level", bool(signed_by), message, True)
    return AdmissionCheck(
        "trust_level", True, f"trust_level '{track.trust_level.value}' declared", True
    )


def _check_d1(tickets: list[TicketSpec]) -> AdmissionCheck:
    """D1: a ticket's declared writable set must never touch the graded files (§4.3)."""
    violations: list[str] = []
    for ticket in tickets:
        overlap = set(ticket.mentor_policy.may_write) & set(ticket.scope.files_to_change)
        if overlap:
            violations.append(f"{ticket.identity.id}:{sorted(overlap)}")
    message = (
        "may_write never intersects files_to_change (D1)"
        if not violations
        else f"D1 violations: {violations}"
    )
    return AdmissionCheck("d1_role_reversal", not violations, message, True)


def _check_no_spoiler(root: Path, tickets: list[TicketSpec]) -> AdmissionCheck:
    leaks = _scan_spoilers(root, tickets)
    message = (
        "no solution text leaked into tickets/hints"
        if not leaks
        else f"possible spoilers (soft, needs human review): {leaks}"
    )
    return AdmissionCheck("no_spoiler", not leaks, message, False)


def _check_discriminance(runner: DiscriminanceRunner | None) -> AdmissionCheck:
    if runner is None:
        return AdmissionCheck(
            "discriminance",
            True,
            "skipped: no runner injected (real mvn/docker discriminance is CI-side)",
            False,
        )
    red, green = runner.run()
    return AdmissionCheck(
        "discriminance", red and green, f"skeleton red={red}, reference green={green}", True
    )


def _anchors_in(path: Path) -> set[str]:
    if not path.exists():
        return set()
    anchors: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            anchors.add(_github_anchor(stripped.lstrip("#").strip()))
    return anchors


def _github_anchor(heading: str) -> str:
    """Approximate GitHub's heading→anchor slug (lowercase, drop punctuation, sp→hyphen)."""
    kept = "".join(ch for ch in heading.strip().lower() if ch.isalnum() or ch in " -_")
    return kept.replace(" ", "-")


def _scan_spoilers(root: Path, tickets: list[TicketSpec]) -> list[str]:
    """Coarse filter: flag any significant solution line quoted into a ticket's text."""
    solutions = root / "solutions"
    if not solutions.exists():
        return []
    secrets: list[str] = []
    for path in solutions.rglob("*"):
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if len(stripped) >= 12 and not stripped.startswith(("//", "#", "--", "*")):
                secrets.append(stripped)
    leaks: list[str] = []
    for ticket in tickets:
        revealed = " ".join(
            [ticket.brief.problem, *ticket.brief.concepts, *(hint.text for hint in ticket.hints)]
        )
        for secret in secrets:
            if secret in revealed:
                leaks.append(f"{ticket.identity.id} leaks '{secret[:40]}'")
    return leaks
