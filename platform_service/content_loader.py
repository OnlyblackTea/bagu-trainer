"""Content loading + JSON-Schema validation (design v0.4 §4.2 track / §4.3 ticket).

The single gate between untrusted content-repo YAML and the typed domain models:
read YAML → validate against the frozen JSON Schema (Draft 2020-12) → project into
:mod:`platform_service.models`. Any violation raises :class:`ContentValidationError`
carrying *every* schema error, not just the first — a contributor should see the
whole defect list at once.

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
