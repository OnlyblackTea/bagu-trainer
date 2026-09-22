"""``platform_service.history``：历史与比对（纯域层，离线可测）。

比对规则只写一处（后端），因为它有**语义**："红→绿"和"绿→红"对学员是两个完全不同的消息。
如果让前端再实现一遍，两边的措辞和判定迟早会漂。
"""

from __future__ import annotations

from typing import Any

from platform_service.history import (
    AttemptRecord,
    attempt_delta,
    elapsed_ms_of,
)
from platform_service.models import (
    GradingReport,
    GradingRun,
    GradingRunStatus,
    Submission,
)


def _report(
    submission_id: str, tests: list[tuple[str, str]], *, passed: bool, duration_s: Any = None
) -> GradingReport:
    host_env: dict[str, Any] = {"simulated": False}
    if duration_s is not None:
        host_env["duration_s"] = duration_s
    return GradingReport.model_validate(
        {
            "submission_id": submission_id,
            "ticket": "db-stage-01",
            "ticket_version": 1,
            "test_version": 1,
            "engine_version": "a-prime-0.1.0",
            "learner": "deployer",
            "provenance": {
                "generated_at": "2026-09-10T00:00:00+00:00",
                "generated_by": "docker-grading/a-prime",
                "host_env": host_env,
                "runner_trust": "self",
            },
            "correctness": {
                "test_group": "stage01",
                "passed": passed,
                "tests": [{"name": n, "status": s} for n, s in tests],
            },
        }
    )


def test_first_attempt_has_nothing_to_compare_with() -> None:
    """第一次尝试没有"上一次"——`delta` 就该是 None，而不是编一个空比对。"""
    record = AttemptRecord(submission=Submission(id="sub-1", instance_id="ti-1"))
    assert record.delta is None
    assert record.verdict is None


def test_delta_reports_only_the_tests_that_actually_changed() -> None:
    """没动的测试对学员是噪音：真 Track 的 canonical 测试常有十几条，全列出来等于什么都没说。"""
    before = _report(
        "sub-1",
        [
            ("usesIndex", "fail"),
            ("returnsOrderedResults", "error"),
            ("stable", "pass"),
        ],
        passed=False,
    )
    after = _report(
        "sub-2",
        [
            ("usesIndex", "pass"),
            ("returnsOrderedResults", "pass"),
            ("stable", "pass"),
        ],
        passed=True,
    )

    delta = attempt_delta(against=1, previous_report=before, current_report=after)

    assert delta.against == 1
    assert [c.name for c in delta.tests] == ["returnsOrderedResults", "usesIndex"]
    assert [(c.frm, c.to, c.kind) for c in delta.tests] == [
        ("error", "pass", "fixed"),
        ("fail", "pass", "fixed"),
    ]
    assert delta.passed_before is False and delta.passed_after is True


def test_delta_distinguishes_fixed_from_broke_from_added_and_removed() -> None:
    """四种迁移各有各的说法，且**不合并**：绿→红和红→绿是相反的消息。"""
    before = _report(
        "sub-1", [("wasGreen", "pass"), ("wasRed", "fail"), ("removed", "pass")], passed=False
    )
    after = _report(
        "sub-2", [("wasGreen", "fail"), ("wasRed", "pass"), ("added", "fail")], passed=False
    )

    kinds = {
        c.name: c.kind
        for c in attempt_delta(
            against=1, previous_report=before, current_report=after
        ).tests
    }

    assert kinds == {
        "wasGreen": "broke",
        "wasRed": "fixed",
        "added": "added",
        "removed": "removed",
    }


def test_delta_survives_a_missing_side() -> None:
    """有一侧没报告（没跑完 / 老记录）时，比对仍然给出**能给的部分**，不整体炸掉。"""
    current = _report("sub-1", [("t", "pass")], passed=True)

    first = attempt_delta(against=1, previous_report=None, current_report=current)
    assert first.passed_before is None and first.passed_after is True
    assert [(c.frm, c.to, c.kind) for c in first.tests] == [(None, "pass", "added")]

    second = attempt_delta(against=2, previous_report=current, current_report=None)
    assert [(c.frm, c.to, c.kind) for c in second.tests] == [("pass", None, "removed")]


def test_elapsed_prefers_the_run_over_the_reports_maven_time() -> None:
    """耗时优先取**运行记录**：它含"重置基础设施"那一段，而那正是"阅卷为什么这么慢"的答案
    （实测 120 秒里 109 秒在重置）。报告里的 ``duration_s`` 只是 maven 那一段，故只作兜底。"""
    run = GradingRun(
        submission_id="sub-1",
        instance_id="ti-1",
        track="database-bagu",
        ticket_id="db-stage-01",
        learner="deployer",
        status=GradingRunStatus.DONE,
        elapsed_ms=120_217,
    )
    report = _report("sub-1", [("t", "fail")], passed=False, duration_s=10.891)

    assert elapsed_ms_of(run, report) == 120_217
    # 没有运行记录时退回报告里的 maven 耗时（毫秒）
    assert elapsed_ms_of(None, report) == 10_891
    # 两者都没有就如实 None，不编 0
    assert elapsed_ms_of(None, None) is None


def test_elapsed_change_needs_both_sides() -> None:
    """只有一侧有耗时时不给"变化量"——那会看起来像"快了 0 秒"。"""
    current = _report("sub-1", [("t", "pass")], passed=True)
    delta = attempt_delta(
        against=1, previous_report=None, current_report=current, current_elapsed_ms=500
    )
    assert delta.elapsed_ms_change is None

    both = attempt_delta(
        against=1,
        previous_report=current,
        current_report=current,
        previous_elapsed_ms=120_000,
        current_elapsed_ms=20_000,
    )
    assert both.elapsed_ms_change == -100_000
