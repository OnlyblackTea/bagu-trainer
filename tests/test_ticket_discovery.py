"""Directory-level ticket discovery: the canonical ``stages/*/ticket.yaml`` scan.

``ticket.identity.id`` — not the stage directory name — is the identity the prerequisite
DAG and every instance are keyed on (``unlock.build_prereq_graph`` already keys on it).
Discovery must therefore be directory-driven (the same rule ``admission.validate_track``
uses) and hand back a mapping whose keys are guaranteed to be ``identity.id``.

This is what keeps a descriptive stage directory (``stage-01-product-query-index``) from
silently decoupling the unlock gate from the tickets it gates: mismatched keys made every
dependent instance mint ``available`` instead of ``locked``, defeating the DAG.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from platform_service.content_loader import (
    ContentValidationError,
    discover_ticket_paths,
    load_stage_refs,
    load_tickets,
)

Ticket = dict[str, Any]
Stage = tuple[str, Ticket]


def _lay_out(
    root: Path,
    *,
    write_tree: Callable[..., Path],
    make_track: Callable[..., dict[str, Any]],
    make_ticket: Callable[..., Ticket],
    stages: list[Stage],
    manifest_stages: list[str] | None = None,
) -> Path:
    """Write a track tree whose manifest may declare stages that ship no ticket."""
    track = make_track(
        stages=manifest_stages if manifest_stages is not None else [name for name, _ in stages]
    )
    return write_tree(root, track=track, stages=stages)


def _ticket(make_ticket: Callable[..., Ticket], ticket_id: str, prereqs: list[str]) -> Ticket:
    return make_ticket(ticket_id=ticket_id, prerequisites=prereqs)


# --- happy path ---------------------------------------------------------------


def test_discover_paths_is_sorted(
    tmp_path: Path,
    track_tree_builder: Callable[..., Path],
    track_factory: Callable[..., dict[str, Any]],
    ticket_factory: Callable[..., Ticket],
) -> None:
    root = tmp_path / "track"
    _lay_out(
        root,
        write_tree=track_tree_builder,
        make_track=track_factory,
        make_ticket=ticket_factory,
        stages=[
            ("stage-02-order-transaction", _ticket(ticket_factory, "db-stage-02", [])),
            ("stage-00-onboarding", _ticket(ticket_factory, "db-stage-00", [])),
        ],
    )
    names = [path.parent.name for path in discover_ticket_paths(root)]
    assert names == ["stage-00-onboarding", "stage-02-order-transaction"]


def test_load_tickets_keys_by_identity_id_not_dirname(
    tmp_path: Path,
    track_tree_builder: Callable[..., Path],
    track_factory: Callable[..., dict[str, Any]],
    ticket_factory: Callable[..., Ticket],
) -> None:
    """A descriptive directory name must not become the ticket's identity."""
    root = tmp_path / "track"
    _lay_out(
        root,
        write_tree=track_tree_builder,
        make_track=track_factory,
        make_ticket=ticket_factory,
        stages=[("stage-01-product-query-index", _ticket(ticket_factory, "db-stage-01", []))],
    )
    tickets = load_tickets(root)
    assert list(tickets) == ["db-stage-01"]
    assert tickets["db-stage-01"].identity.id == "db-stage-01"


def test_load_tickets_ignores_manifest_stages_without_a_ticket(
    tmp_path: Path,
    track_tree_builder: Callable[..., Path],
    track_factory: Callable[..., dict[str, Any]],
    ticket_factory: Callable[..., Ticket],
) -> None:
    """``stage-00-onboarding`` is declared for the board but ships no ticket (by design)."""
    root = tmp_path / "track"
    _lay_out(
        root,
        write_tree=track_tree_builder,
        make_track=track_factory,
        make_ticket=ticket_factory,
        stages=[("stage-01-product-query-index", _ticket(ticket_factory, "db-stage-01", []))],
        manifest_stages=["stage-00-onboarding", "stage-01-product-query-index"],
    )
    assert list(load_tickets(root)) == ["db-stage-01"]


def test_load_tickets_preserves_prerequisites(
    tmp_path: Path,
    track_tree_builder: Callable[..., Path],
    track_factory: Callable[..., dict[str, Any]],
    ticket_factory: Callable[..., Ticket],
) -> None:
    root = tmp_path / "track"
    _lay_out(
        root,
        write_tree=track_tree_builder,
        make_track=track_factory,
        make_ticket=ticket_factory,
        stages=[
            ("stage-00-onboarding", _ticket(ticket_factory, "db-stage-00", [])),
            (
                "stage-01-product-query-index",
                _ticket(ticket_factory, "db-stage-01", ["db-stage-00"]),
            ),
        ],
    )
    assert load_tickets(root)["db-stage-01"].progression.prerequisites == ["db-stage-00"]


# --- rejection ----------------------------------------------------------------


def test_load_tickets_rejects_duplicate_identity_id(
    tmp_path: Path,
    track_tree_builder: Callable[..., Path],
    track_factory: Callable[..., dict[str, Any]],
    ticket_factory: Callable[..., Ticket],
) -> None:
    """Two stage dirs claiming one identity would make the DAG ambiguous."""
    root = tmp_path / "track"
    _lay_out(
        root,
        write_tree=track_tree_builder,
        make_track=track_factory,
        make_ticket=ticket_factory,
        stages=[
            ("stage-01-product-query-index", _ticket(ticket_factory, "db-stage-01", [])),
            ("stage-01-product-query-index-v2", _ticket(ticket_factory, "db-stage-01", [])),
        ],
    )
    with pytest.raises(ContentValidationError) as exc:
        load_tickets(root)
    assert "db-stage-01" in str(exc.value)


def test_load_tickets_rejects_track_with_no_tickets(
    tmp_path: Path,
    track_tree_builder: Callable[..., Path],
    track_factory: Callable[..., dict[str, Any]],
) -> None:
    """Booting a service with zero tickets 404s every endpoint; fail loudly instead."""
    root = tmp_path / "track"
    _lay_out(
        root,
        write_tree=track_tree_builder,
        make_track=track_factory,
        make_ticket=lambda **_: {},
        stages=[],
        manifest_stages=["stage-00-onboarding"],
    )
    with pytest.raises(ContentValidationError):
        load_tickets(root)


def test_load_tickets_propagates_schema_violations(
    tmp_path: Path,
    track_tree_builder: Callable[..., Path],
    track_factory: Callable[..., dict[str, Any]],
    ticket_factory: Callable[..., Ticket],
) -> None:
    bad = _ticket(ticket_factory, "db-stage-01", [])
    del bad["grader"]
    root = tmp_path / "track"
    _lay_out(
        root,
        write_tree=track_tree_builder,
        make_track=track_factory,
        make_ticket=ticket_factory,
        stages=[("stage-01-product-query-index", bad)],
    )
    with pytest.raises(ContentValidationError):
        load_tickets(root)


# --- stage refs（§C.5：分支按阶段命名，不按 ticket id） --------------------------


def test_load_stage_refs_maps_identity_to_stage_name(
    tmp_path: Path,
    track_tree_builder: Callable[..., Path],
    track_factory: Callable[..., dict[str, Any]],
    ticket_factory: Callable[..., Ticket],
) -> None:
    """Forge 靠这张表取分支；若拿 identity.id 当分支名，在描述式目录名的 Track 上会取错 ref。"""
    root = tmp_path / "track"
    _lay_out(
        root,
        write_tree=track_tree_builder,
        make_track=track_factory,
        make_ticket=ticket_factory,
        stages=[
            ("stage-00-onboarding", _ticket(ticket_factory, "db-stage-00", [])),
            (
                "stage-01-product-query-index",
                _ticket(ticket_factory, "db-stage-01", ["db-stage-00"]),
            ),
        ],
    )
    assert load_stage_refs(root) == {
        "db-stage-00": "stage-00-onboarding",
        "db-stage-01": "stage-01-product-query-index",
    }


def test_load_stage_refs_omits_board_only_stages(
    tmp_path: Path,
    track_tree_builder: Callable[..., Path],
    track_factory: Callable[..., dict[str, Any]],
    ticket_factory: Callable[..., Ticket],
) -> None:
    """只上榜、不出卷的 stage 不该产生 ref（否则会去要一个不存在的分支）。"""
    root = tmp_path / "track"
    _lay_out(
        root,
        write_tree=track_tree_builder,
        make_track=track_factory,
        make_ticket=ticket_factory,
        stages=[("stage-01-product-query-index", _ticket(ticket_factory, "db-stage-01", []))],
        manifest_stages=["stage-00-onboarding", "stage-01-product-query-index"],
    )
    assert load_stage_refs(root) == {"db-stage-01": "stage-01-product-query-index"}
