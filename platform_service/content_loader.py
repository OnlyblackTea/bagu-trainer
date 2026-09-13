"""Content loading + JSON-Schema validation (design v0.4 §4.2 track / §4.3 ticket).

The single gate between untrusted content-repo YAML and the typed domain models:
read YAML → validate against the frozen JSON Schema (Draft 2020-12) → project into
:mod:`platform_service.models`. Any violation raises :class:`ContentValidationError`
carrying *every* schema error, not just the first — a contributor should see the
whole defect list at once.

Ticket *discovery* lives here too (:func:`discover_ticket_paths` / :func:`load_tickets`)
so that one rule — glob ``stages/*/ticket.yaml`` and key on ``ticket.identity.id`` —
serves both the admission gate and the service composition root. ``track.manifest.stages``
is a *board* list: it may declare a stage that ships no ticket (``stage-00-onboarding``
is an onboarding/environment stage, not an assessed one), so it must never be the thing
that decides which tickets exist.

Pure filesystem + schema logic: no engine, no HTTP, no DB (GPL boundary intact).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

from .models import TicketSpec, TrackManifest

_SCHEMA_DIR = Path(__file__).resolve().parent.parent / "content" / "schema"


class ContentValidationError(Exception):
    """A track/ticket document failed to parse or to satisfy its schema.

    ``errors`` holds one formatted string per schema violation (empty for parse /
    missing-file failures, which are reported through the message alone).
    """

    def __init__(self, message: str, errors: list[str] | None = None) -> None:
        super().__init__(message)
        self.errors: list[str] = errors or []


def load_schema(name: str) -> dict[str, Any]:
    """Load ``content/schema/{name}.schema.json`` (``name`` = ``ticket`` | ``track``)."""
    path = _SCHEMA_DIR / f"{name}.schema.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ContentValidationError(f"unknown schema: {name}") from exc
    except json.JSONDecodeError as exc:
        raise ContentValidationError(f"invalid schema JSON {path.name}: {exc}") from exc


def validate_instance(data: Any, schema: dict[str, Any]) -> None:
    """Validate ``data`` against ``schema`` (Draft 2020-12); raise on any violation."""
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(data), key=lambda e: list(e.path))
    if errors:
        messages = [_format_error(error) for error in errors]
        detail = "\n".join(f"  - {line}" for line in messages)
        raise ContentValidationError(
            f"{len(messages)} schema violation(s):\n{detail}", messages
        )


def _format_error(error: Any) -> str:
    location = "/".join(str(part) for part in error.path) or "<root>"
    return f"{location}: {error.message}"


def _read_yaml(path: Path) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ContentValidationError(f"file not found: {path}") from exc
    except OSError as exc:
        raise ContentValidationError(f"cannot read {path}: {exc}") from exc
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ContentValidationError(f"invalid YAML in {path.name}: {exc}") from exc


def load_ticket(path: str | Path) -> TicketSpec:
    """Load + validate a ``ticket.yaml`` and project it into a :class:`TicketSpec`."""
    data = _read_yaml(Path(path))
    validate_instance(data, load_schema("ticket"))
    return TicketSpec.model_validate(data)


def load_track(path: str | Path) -> TrackManifest:
    """Load + validate a ``track.yaml`` and project it into a :class:`TrackManifest`."""
    data = _read_yaml(Path(path))
    validate_instance(data, load_schema("track"))
    return TrackManifest.model_validate(data["track"])


def discover_ticket_paths(track_dir: str | Path) -> list[Path]:
    """Every ``stages/*/ticket.yaml`` under ``track_dir``, in stable sorted order.

    Directory-driven on purpose: the stage directory is the author's filing choice, so
    the set of real tickets must not be inferred from ``manifest.stages``.
    """
    return sorted((Path(track_dir) / "stages").glob("*/ticket.yaml"))


def load_tickets(track_dir: str | Path) -> dict[str, TicketSpec]:
    """Load every discovered ticket, keyed by ``identity.id`` — the canonical identity.

    ``identity.id`` is what :func:`platform_service.unlock.build_prereq_graph` keys the
    prerequisite DAG on and what every :class:`~platform_service.models.TicketInstance`
    carries, so handing back any other key would silently detach instances from the DAG
    (dependents would mint ``available`` instead of ``locked``). Raises
    :class:`ContentValidationError` on a duplicate identity or an empty track.
    """
    root = Path(track_dir)
    tickets: dict[str, TicketSpec] = {}
    origin: dict[str, Path] = {}
    for path in discover_ticket_paths(root):
        ticket = load_ticket(path)
        ticket_id = ticket.identity.id
        if ticket_id in tickets:
            raise ContentValidationError(
                f"duplicate ticket identity {ticket_id!r}: "
                f"{origin[ticket_id]} and {path} both declare it"
            )
        tickets[ticket_id] = ticket
        origin[ticket_id] = path
    if not tickets:
        raise ContentValidationError(
            f"no tickets found under {root / 'stages'} (expected stages/*/ticket.yaml)"
        )
    return tickets


def load_ticket_dirs(track_dir: str | Path) -> dict[str, TicketSpec]:
    """Map each stage **directory** name to its ticket, for board rendering.

    The board is written in stage directory names (``manifest.stages``) while tickets
    are canonically identified by ``identity.id`` — and the two are allowed to differ.
    Rendering the board therefore needs this index; nothing else may use it, because
    ``identity.id`` is the only identity the DAG and instances may be keyed on.
    """
    return {path.parent.name: load_ticket(path) for path in discover_ticket_paths(track_dir)}


def load_stage_refs(track_dir: str | Path) -> dict[str, str]:
    """Map ``ticket.identity.id`` → its **stage name** (the stage's branch in the repo).

    §C.5 gives every stage its own branch, named after the stage — *not* after the
    ticket id. The Forge needs this mapping to resolve which branch a ticket is diffed
    against; assuming ``identity.id`` were the branch name only works while the two
    happen to coincide (see Plan 1 Task 0), and silently selects the wrong ref otherwise.
    """
    return {ticket.identity.id: stage for stage, ticket in load_ticket_dirs(track_dir).items()}
