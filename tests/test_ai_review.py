"""Unit tests for platform_service.ai_review (design v0.5 §11.2).

The reviewer is injected (a fake here), so the response-mapping and the diff preflight
discipline are tested offline — no live backend, no HTTP. The fake's ReviewResponse
carries the full response shape, including the backend's own ``anchor`` string format
(``path#side:line``) so the tests can prove the platform builds its own display anchor
from the triple instead of parsing the backend's.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest

from platform_service import ai_review
from platform_service.models import Recommendation

SESSION = "eng-sess-1"
ENGINE_SUBMISSION = "eng-sub-1"

# Review response shape (design §11.2).
_REVIEW_RESPONSE: dict[str, Any] = {
    "id": "eng-rev-1",
    "session_id": SESSION,
    "submission_id": ENGINE_SUBMISSION,
    "plan_version": 3,
    "created_at": "2026-09-09T02:41:48Z",
    "result": {
        "summary": "证据完整但越界改了测试。",
        "recommendation": "request_changes",
        "dimension_findings": [{"dimension": "correctness", "commentary": "红绿正确"}],
        "comments": [
            {
                "dimension": "security",
                "text": "用户输入被拼进了 SQL",
                "quote": "String sql = \"select\";",
                "path": "src/Foo.java",
                "line": 354,
                "side": "right",
                "anchor": "src/Foo.java#right:354",
            }
        ],
        "unanchored": [
            {
                "dimension": "test_coverage",
                "text": "缺并发超卖用例",
                "quote": None,
                "path": None,
                "line": None,
                "side": None,
                "anchor": None,
            }
        ],
        "truncated_files": ["Huge.java"],
    },
}

_SMALL_DIFF = (
    "diff --git a/src/Foo.java b/src/Foo.java\n"
    "--- a/src/Foo.java\n"
    "+++ b/src/Foo.java\n"
    "@@ -1 +1 @@\n"
    "-a\n"
    "+b\n"
)


class FakeReviewEngine:
    """Duck-typed stand-in for the two ReviewEngine methods ai_review uses."""

    def __init__(
        self,
        *,
        review_response: dict[str, Any] | None = None,
        submission_id: str = ENGINE_SUBMISSION,
    ) -> None:
        self._review_response = review_response or _REVIEW_RESPONSE
        self._submission_id = submission_id
        self.create_submission_calls: list[dict[str, Any]] = []
        self.review_calls: list[dict[str, Any]] = []

    async def create_submission(
        self,
        session_id: str,
        *,
        diff: str,
        summary: str | None = None,
        defense: str | None = None,
        grading_report: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.create_submission_calls.append(
            {
                "session_id": session_id,
                "diff": diff,
                "summary": summary,
                "defense": defense,
                "grading_report": grading_report,
            }
        )
        return {
            "id": self._submission_id,
            "session_id": session_id,
            "plan_version": 1,
            "phase": "action_review",
            "created_at": "2026-09-09T02:40:00Z",
        }

    async def submit_for_ai_review(
        self,
        session_id: str,
        *,
        dimensions: Sequence[str],
        submission_id: str | None = None,
        diff: str | None = None,
        defense_text: str | None = None,
        grading_report: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.review_calls.append(
            {
                "session_id": session_id,
                "dimensions": list(dimensions),
                "submission_id": submission_id,
                "diff": diff,
                "defense_text": defense_text,
                "grading_report": grading_report,
            }
        )
        return self._review_response


async def test_generate_review_maps_engine_response_to_platform_review() -> None:
    engine = FakeReviewEngine()
    review = await ai_review.generate_review(
        engine, SESSION, diff=_SMALL_DIFF, dimensions=["correctness", "security"]
    )
    assert review.id == "eng-rev-1"
    assert review.recommendation is Recommendation.REQUEST_CHANGES
    assert review.summary == "证据完整但越界改了测试。"

    assert len(review.comments) == 2
    anchored = review.comments[0]
    assert anchored.dimension == "security"
    assert (anchored.path, anchored.line, anchored.side) == ("src/Foo.java", 354, "right")
    assert anchored.quote == 'String sql = "select";'
    # Built from the triple (GitHub-style), NOT parsed from the engine's "#right:354".
    assert anchored.anchor == "src/Foo.java#L354"
    assert anchored.anchor != "src/Foo.java#right:354"

    unanchored = review.comments[1]
    assert unanchored.dimension == "test_coverage"
    assert unanchored.anchor is None
    assert unanchored.path is None
    assert unanchored.side is None
    assert unanchored.quote is None

    assert review.dimension_findings[0].dimension == "correctness"
    assert review.dimension_findings[0].commentary == "红绿正确"
    assert review.truncated_files == ["Huge.java"]


async def test_generate_review_sends_submission_id_not_diff() -> None:
    """XOR (design §11.2): the diff goes to create_submission; the review references
    it by submission_id and must NOT re-send the diff."""
    engine = FakeReviewEngine()
    await ai_review.generate_review(
        engine, SESSION, diff=_SMALL_DIFF, dimensions=["correctness"]
    )
    assert engine.create_submission_calls[0]["diff"] == _SMALL_DIFF
    call = engine.review_calls[0]
    assert call["submission_id"] == ENGINE_SUBMISSION
    assert call["diff"] is None


async def test_generate_review_backfills_defense_and_report() -> None:
    """(design §11.2) defense_text / grading_report are NOT auto-carried from the
    submission, so the platform must re-send them to the review call."""
    engine = FakeReviewEngine()
    report = {"correctness": {"passed": True}}
    await ai_review.generate_review(
        engine,
        SESSION,
        diff=_SMALL_DIFF,
        dimensions=["correctness"],
        defense_text="三件套答辩",
        grading_report=report,
    )
    call = engine.review_calls[0]
    assert call["defense_text"] == "三件套答辩"
    assert call["grading_report"] == report


async def test_generate_review_refuses_first_file_overflow_before_touching_engine() -> None:
    """The first-file-overflow trap: a first file over the limit yields an empty-but-normal review,
    so the platform refuses before any engine call (no submission, no token burn)."""
    engine = FakeReviewEngine()
    big = (
        "diff --git a/big.py b/big.py\n"
        "--- a/big.py\n"
        "+++ b/big.py\n"
        "@@ -1 +1 @@\n"
        "-" + ("x" * 100) + "\n"
        "+" + ("y" * 100) + "\n"
    )
    with pytest.raises(ai_review.ReviewDiffTooLargeError):
        await ai_review.generate_review(
            engine, SESSION, diff=big, dimensions=["correctness"], limit=50
        )
    assert engine.create_submission_calls == []
    assert engine.review_calls == []
