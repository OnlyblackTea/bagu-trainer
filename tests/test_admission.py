"""Unit tests for platform_service.admission — the static half of ``validate-track``.

These build a real temporary track tree (via ``track_tree_builder``) and assert each
§4.6 gate: schema validity, files_to_change presence, bagu-map anchors, runtime
digest-pinning, trust-level endorsement, the D1 role-reversal invariant, the soft
spoiler filter, and the injected discriminance runner (red-on-skeleton / green-on-ref).

The mvn/docker compute checks (skeleton builds, infra comes up) are out of static
scope; discriminance is delegated to a fake runner so the suite stays offline.
"""

from __future__ import annotations

from typing import Any

import pytest

from platform_service.admission import AdmissionReport, DiscriminanceRunner, validate_track

_DEFAULT_FILES = [
    "src/main/resources/mapper/ProductMapper.xml",
    "src/main/resources/db/migration/V1__stage01_index.sql",
]
_SKELETON = {rel: "// skeleton placeholder\n" for rel in _DEFAULT_FILES}
# The judged surface (§11.3) must resolve in the skeleton, so the default tree carries a
# smoke-test placeholder and a build file — exactly like a real skeleton does.
_SKELETON["src/test/SmokeTest.java"] = "// smoke placeholder\n"
_SKELETON["pom.xml"] = "<project/>\n"
_SOLUTION_LINE = "CREATE INDEX idx_product_search ON product(category, name);"
_SOLUTIONS = {"ProductQueryIndex.sql": _SOLUTION_LINE + "\n"}


class _FakeRunner:
    """Stand-in for the CI-side mvn/docker discriminance runner."""

    def __init__(self, red: bool, green: bool) -> None:
        self._result = (red, green)

    def run(self) -> tuple[bool, bool]:
        return self._result


@pytest.fixture
def build_track(tmp_path, track_factory, ticket_factory, track_tree_builder) -> Any:
    def _build(
        *,
        track: dict[str, Any] | None = None,
        ticket: dict[str, Any] | None = None,
        skeleton: dict[str, str] | None = None,
        solutions: dict[str, str] | None = None,
    ):
        return track_tree_builder(
            tmp_path,
            track=track or track_factory(),
            stages=[("stage-01-product-query-index", ticket or ticket_factory())],
            skeleton_files=_SKELETON if skeleton is None else skeleton,
            solutions=_SOLUTIONS if solutions is None else solutions,
        )

    return _build


def _check(report: AdmissionReport, name: str):
    found = report.get(name)
    assert found is not None, f"no admission check named {name!r}"
    return found


def test_valid_track_passes(build_track) -> None:
    report = validate_track(build_track())
    assert isinstance(report, AdmissionReport)
    assert report.ok
    for name in ("schema", "files_to_change", "runtime_pinned", "trust_level", "d1_role_reversal"):
        assert _check(report, name).passed


def test_missing_skeleton_file_is_hard_fail(build_track, ticket_factory) -> None:
    ticket = ticket_factory(files_to_change=["src/main/java/Missing.java"])
    report = validate_track(build_track(ticket=ticket, skeleton={}))
    check = _check(report, "files_to_change")
    assert not check.passed and check.hard
    assert not report.ok


def test_d1_may_write_intersecting_files_is_hard_fail(build_track, ticket_factory) -> None:
    ticket = ticket_factory()
    ticket["mentor_policy"]["may_write"] = [ticket["scope"]["files_to_change"][0]]
    report = validate_track(build_track(ticket=ticket))
    check = _check(report, "d1_role_reversal")
    assert not check.passed and check.hard
    assert not report.ok


def test_runtime_entry_without_pin_is_hard_fail(build_track, track_factory) -> None:
    track = track_factory(digest_pin={"mysql": "sha256:abc123"})
    report = validate_track(build_track(track=track))
    check = _check(report, "runtime_pinned")
    assert not check.passed and check.hard
    assert not report.ok


def test_verified_without_signed_by_is_hard_fail(build_track, track_factory) -> None:
    track = track_factory(trust_level="verified")
    report = validate_track(build_track(track=track))
    check = _check(report, "trust_level")
    assert not check.passed and check.hard
    assert not report.ok


def test_verified_with_signed_by_passes(build_track, track_factory) -> None:
    track = track_factory(trust_level="verified", signed_by=["@maintainer"])
    report = validate_track(build_track(track=track))
    assert _check(report, "trust_level").passed
    assert report.ok


def test_bad_bagu_map_anchor_is_hard_fail(build_track, ticket_factory) -> None:
    ticket = ticket_factory(bagu_map_ref="bagu-map.md#不存在的锚点")
    report = validate_track(build_track(ticket=ticket))
    check = _check(report, "bagu_map_anchor")
    assert not check.passed and check.hard
    assert not report.ok


def test_discriminance_red_green_passes_and_is_hard(build_track) -> None:
    runner = _FakeRunner(red=True, green=True)
    assert isinstance(runner, DiscriminanceRunner)
    report = validate_track(build_track(), discriminance_runner=runner)
    check = _check(report, "discriminance")
    assert check.passed and check.hard
    assert report.ok


def test_discriminance_non_red_fails(build_track) -> None:
    report = validate_track(build_track(), discriminance_runner=_FakeRunner(red=False, green=True))
    check = _check(report, "discriminance")
    assert not check.passed and check.hard
    assert not report.ok


def test_discriminance_skipped_without_runner(build_track) -> None:
    report = validate_track(build_track())
    check = _check(report, "discriminance")
    assert check.passed and not check.hard
    assert "skipped" in check.message


def test_spoiler_is_soft_and_does_not_block(build_track, ticket_factory) -> None:
    ticket = ticket_factory()
    ticket["hints"].append({"id": "LEAK", "text": f"直接抄：{_SOLUTION_LINE}"})
    report = validate_track(build_track(ticket=ticket))
    check = _check(report, "no_spoiler")
    assert not check.passed and not check.hard
    assert report.ok  # soft: warns, still admits pending human review


# --- ticket discovery / manifest coverage -------------------------------------


def test_manifest_coverage_passes_when_ticket_dir_is_declared(build_track) -> None:
    report = validate_track(build_track())
    assert _check(report, "manifest_coverage").passed
    assert report.ok


# --- 判定面（§11.3）------------------------------------------------------------


def test_protected_paths_are_verified_against_the_skeleton(build_track) -> None:
    check = _check(validate_track(build_track()), "protected_paths")
    assert check.passed and check.hard


def test_protected_path_matching_nothing_is_hard_fail(build_track, track_factory) -> None:
    """判定面写错一个字母就是**静默漏洞**：门会放过改了 canonical 测试的人。

    宁可准入门响亮地失败，也不要发布一个"声明了判定面、实际什么都没盖住"的 Track。
    """
    track = track_factory(protected_paths=["./src/test/", "./pom.xmlx"])  # 拼错
    report = validate_track(build_track(track=track))
    check = _check(report, "protected_paths")
    assert not check.passed and check.hard
    assert "pom.xmlx" in check.message
    assert not report.ok


def test_ticket_dir_absent_from_manifest_is_hard_fail(build_track, track_factory) -> None:
    """An undeclared ticket dir is invisible on the board but still gets an instance."""
    track = track_factory(stages=["stage-00-onboarding"])
    report = validate_track(build_track(track=track))
    check = _check(report, "manifest_coverage")
    assert not check.passed and check.hard
    assert "stage-01-product-query-index" in check.message
    assert not report.ok


def test_manifest_may_declare_a_stage_that_ships_no_ticket(build_track, track_factory) -> None:
    """``stage-00-onboarding`` is an environment stage: on the board, no assessed ticket."""
    track = track_factory(stages=["stage-00-onboarding", "stage-01-product-query-index"])
    report = validate_track(build_track(track=track))
    assert _check(report, "manifest_coverage").passed
    assert report.ok


def test_schema_check_counts_discovered_tickets(build_track) -> None:
    """Discovery is directory-driven, so the count reflects real tickets, not manifest size."""
    check = _check(validate_track(build_track()), "schema")
    assert check.passed and "1 ticket" in check.message

