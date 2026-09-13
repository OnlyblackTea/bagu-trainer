"""Advisory AI Review generation (design v0.4 §6.5/§11.2, CONTRACTS §5).

This is the integration seam between the platform's domain models and the engine's
review endpoint. It owns three engine facts and nothing else:

* **submission_id XOR diff** (§5.1): the diff is registered once via
  ``create_submission``; the review then references it by ``submission_id`` and never
  re-sends the diff. ``defense_text`` / ``grading_report`` are NOT auto-carried from
  the submission (§5.1), so they are re-sent to the review call here.
* **No GET /reviews** (§5): the engine keeps no retrievable copy, so the returned
  :class:`~platform_service.models.Review` must faithfully retain everything —
  the anchor triple ``(path, line, side)``, the engine-corrected ``quote``,
  ``dimension_findings`` and ``truncated_files``.
* **First-file overflow trap** (§5.8a): a first file over the char limit yields an
  empty-but-*normal*-looking review, so the volume is preflighted
  (:mod:`platform_service.review_diff`) and refused BEFORE any engine call — no
  submission row, no token burn.

The anchor is *built* here as ``f"{path}#L{line}"`` from the authoritative triple, never
parsed back from the engine's convenience ``path#side:line`` string (§5.3).

The engine is injected as a :class:`ReviewEngine` (structurally satisfied by
``orchestrator.prar_bridgeprarBridge``), so this module imports neither the bridge nor
the engine — it stays offline-testable and the GPL boundary intact.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

from .models import DimensionFinding, Recommendation, Review, ReviewComment
from .review_diff import ENGINE_DIFF_CHAR_LIMIT, DiffVolume, preflight_diff_volume


class ReviewEngine(Protocol):
    """The two bridge methods this module drives (structurally typed).

    ``PrarBridge`` satisfies this without being imported, keeping platform_service
    decoupled from orchestrator. Signatures mirror CONTRACTS §4.2 / §5.1 verbatim.
    """

    async def create_submission(
        self,
        session_id: str,
        *,
        diff: str,
        summary: str | None = None,
        defense: str | None = None,
        grading_report: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    async def submit_for_ai_review(
        self,
        session_id: str,
        *,
        dimensions: Sequence[str],
        submission_id: str | None = None,
        diff: str | None = None,
        defense_text: str | None = None,
        grading_report: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...


class ReviewDiffTooLargeError(Exception):
    """The diff's first file alone exceeds the engine's char limit (§5.8a).

    Raised before any engine call: the review would come back empty yet normal-looking,
    so the platform refuses rather than persist a content-free Review. Carries the
    preflighted :class:`~platform_service.review_diff.DiffVolume` for the caller to
    decide how to split or warn.
    """

    def __init__(self, volume: DiffVolume) -> None:
        self.volume = volume
        super().__init__(
            f"diff of {volume.total_chars} chars overflows the engine limit on its "
            f"first file ({volume.truncated_files[0] if volume.truncated_files else '?'});"
            " the review would be empty-but-normal — split the diff"
        )


def _map_comment(raw: dict[str, Any]) -> ReviewComment:
    """Map one engine comment (anchored or unanchored) to a platform ReviewComment.

    The display ``anchor`` is BUILT from the authoritative triple as ``f"{path}#L{line}"``
    (§5.3) — the engine's own ``path#side:line`` convenience string is deliberately
    ignored, never parsed. An unanchored comment (all anchor fields ``None``) keeps
    ``anchor=None``.
    """
    path = raw.get("path")
    line = raw.get("line")
    anchor = f"{path}#L{line}" if path is not None and line is not None else None
    return ReviewComment(
        dimension=raw["dimension"],
        text=raw["text"],
        quote=raw.get("quote"),
        path=path,
        line=line,
        side=raw.get("side"),
        anchor=anchor,
    )


def _map_review_response(response: dict[str, Any], *, submission_id: str) -> Review:
    """Project the engine's ReviewResponse (§5.2) onto the platform Review model.

    Anchored ``comments`` come first, then ``unanchored``; both fold into one list so a
    consumer need not know which had an anchor. ``submission_id`` is the engine
    submission this review references — :meth:`platform_service.service.PlatformService.review`
    later re-keys it to the platform's own submission id.
    """
    result = response["result"]
    comments = [_map_comment(c) for c in result.get("comments", [])]
    comments += [_map_comment(u) for u in result.get("unanchored", [])]
    fields: dict[str, Any] = {
        "id": response["id"],
        "submission_id": submission_id,
        "recommendation": Recommendation(result["recommendation"]),
        "summary": result["summary"],
        "comments": comments,
        "dimension_findings": [
            DimensionFinding(**finding) for finding in result.get("dimension_findings", [])
        ],
        "truncated_files": list(result.get("truncated_files", [])),
    }
    # Pass the engine's timestamp through the constructor (not model_copy) so Pydantic
    # coerces the ISO-8601 string to a datetime; absent it, the model defaults to now.
    created_at = response.get("created_at")
    if created_at is not None:
        fields["created_at"] = created_at
    return Review(**fields)


async def generate_review(
    engine: ReviewEngine,
    session_id: str,
    *,
    diff: str,
    dimensions: Sequence[str],
    defense_text: str | None = None,
    grading_report: dict[str, Any] | None = None,
    summary: str | None = None,
    limit: int = ENGINE_DIFF_CHAR_LIMIT,
) -> Review:
    """Register ``diff`` and request the advisory AI review, returning a persisted Review.

    Order is the cost discipline of §5.1: preflight volume → ``create_submission`` (the
    diff lands once) → ``submit_for_ai_review`` by ``submission_id`` (XOR — the diff is
    never re-sent). ``defense_text`` / ``grading_report`` are re-sent to the review call
    because the engine does not carry them over from the submission. Pure orchestration:
    the engine is injected, so this runs offline against a fake.
    """
    volume = preflight_diff_volume(diff, limit=limit)
    if volume.first_file_overflows:
        raise ReviewDiffTooLargeError(volume)

    submission = await engine.create_submission(
        session_id,
        diff=diff,
        summary=summary,
        defense=defense_text,
        grading_report=grading_report,
    )
    engine_submission_id = submission["id"]

    response = await engine.submit_for_ai_review(
        session_id,
        dimensions=dimensions,
        submission_id=engine_submission_id,
        defense_text=defense_text,
        grading_report=grading_report,
    )
    return _map_review_response(response, submission_id=engine_submission_id)
