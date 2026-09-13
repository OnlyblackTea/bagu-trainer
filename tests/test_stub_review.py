"""StubReviewEngine 经 ai_review.generate_review 产出正确 recommendation 的两套 fixture。"""

from __future__ import annotations

from platform_service import ai_review
from platform_service.models import Recommendation
from platform_service.stubs import StubReviewEngine

_DIFF = (
    "diff --git a/src/Foo.java b/src/Foo.java\n"
    "--- a/src/Foo.java\n"
    "+++ b/src/Foo.java\n"
    "@@ -1 +1 @@\n"
    "-a\n"
    "+b\n"
)


async def test_approve_scenario() -> None:
    engine = StubReviewEngine(scenario="approve")
    review = await ai_review.generate_review(
        engine, "sess-1", diff=_DIFF, dimensions=["correctness"]
    )
    assert review.recommendation is Recommendation.APPROVE
    assert review.summary


async def test_request_changes_scenario_has_anchored_comment() -> None:
    engine = StubReviewEngine(scenario="request_changes")
    review = await ai_review.generate_review(
        engine, "sess-1", diff=_DIFF, dimensions=["correctness", "security"]
    )
    assert review.recommendation is Recommendation.REQUEST_CHANGES
    assert review.comments  # 至少一条行级评论
    anchored = review.comments[0]
    assert anchored.path is not None and anchored.line is not None
    assert anchored.anchor == f"{anchored.path}#L{anchored.line}"
    assert {f.dimension for f in review.dimension_findings} == {"correctness", "security"}
