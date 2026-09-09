"""Unit tests for platform_service.content_loader (design v0.4 §4.2/§4.3).

The loader is the gate from untrusted content YAML to typed models: it must reject
schema violations (including the v0.4-removed ``parametric``) with a
:class:`ContentValidationError`, and project valid docs into TicketSpec /
TrackManifest.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from platform_service.content_loader import (
    ContentValidationError,
    load_schema,
    load_ticket,
    load_track,
    validate_instance,
)


def _write_yaml(path: Path, data: Any) -> Path:
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


# --- schema loading -----------------------------------------------------------


def test_load_schema_reads_ticket_and_track() -> None:
    assert load_schema("ticket")["title"].startswith("bagu-trainer ticket")
    assert load_schema("track")["title"].startswith("bagu-trainer track")


def test_load_schema_unknown_raises() -> None:
    with pytest.raises(ContentValidationError):
        load_schema("does-not-exist")


# --- happy path ---------------------------------------------------------------


def test_load_ticket_ok(tmp_path, sample_ticket) -> None:
    path = _write_yaml(tmp_path / "ticket.yaml", sample_ticket)
    spec = load_ticket(path)
    assert spec.identity.id == "db-stage-01"
    assert spec.grader.correctness.canonical_test.endswith(".java")
    assert spec.progression.prerequisites == ["db-stage-00"]


def test_load_track_ok(tmp_path, sample_track) -> None:
    path = _write_yaml(tmp_path / "track.yaml", sample_track)
    track = load_track(path)
    assert track.id == "database-bagu"
    assert track.trust_level.value == "self"
    assert track.runtime.digest_pin["mysql"].startswith("sha256:")


# --- rejection ----------------------------------------------------------------


def test_load_ticket_rejects_parametric(tmp_path, sample_ticket) -> None:
    """v0.4 removed parametric; a ticket still carrying it must fail validation."""
    sample_ticket["parametric"] = {"fixture_seed": "per-learner"}
    path = _write_yaml(tmp_path / "ticket.yaml", sample_ticket)
    with pytest.raises(ContentValidationError) as exc:
        load_ticket(path)
    assert "parametric" in str(exc.value)


def test_load_ticket_rejects_missing_required(tmp_path, sample_ticket) -> None:
    del sample_ticket["grader"]
    path = _write_yaml(tmp_path / "ticket.yaml", sample_ticket)
    with pytest.raises(ContentValidationError):
        load_ticket(path)


def test_load_track_rejects_unpinned_digest(tmp_path, sample_track) -> None:
    """digest_pin values must match ``^sha256:`` (§4.2 supply-chain pinning)."""
    sample_track["track"]["runtime"]["digest_pin"]["mysql"] = "8.0.36"
    path = _write_yaml(tmp_path / "track.yaml", sample_track)
    with pytest.raises(ContentValidationError):
        load_track(path)


def test_load_missing_file_raises(tmp_path) -> None:
    with pytest.raises(ContentValidationError):
        load_ticket(tmp_path / "nope.yaml")


def test_load_invalid_yaml_raises(tmp_path) -> None:
    path = tmp_path / "broken.yaml"
    path.write_text("identity: {id: [unclosed\n", encoding="utf-8")
    with pytest.raises(ContentValidationError):
        load_ticket(path)


def test_validate_instance_collects_all_errors(sample_ticket) -> None:
    del sample_ticket["grader"]
    del sample_ticket["mentor_policy"]
    with pytest.raises(ContentValidationError) as exc:
        validate_instance(sample_ticket, load_schema("ticket"))
    assert len(exc.value.errors) >= 2
