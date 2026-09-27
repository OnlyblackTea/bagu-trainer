"""End-to-end unit tests for platform_service.service (design v0.5 §9.2 / §11.3).

Drive the whole offline loop against an :class:`InMemoryStore` over a 3-ticket linear
DAG (stage-00 → 01 → 02): enroll mints correctly-initialised instances; a green
submission + approving Review passes the gate and unlocks the immediate dependent; a
red report or a ``request_changes`` Review fails it without unlocking; retry reopens
with an incremented ``seq``; reissue stales the old instance and re-anchors a fresh one.

No engine, no HTTP, no DB — the AI Review and grading report are injected inputs.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from platform_service.errors import GateNotReadyError
from platform_service.lifecycle import IllegalTransitionError
from platform_service.models import (
    GradingReport,
    InstanceState,
    Recommendation,
    Review,
    TicketSpec,
    TrackManifest,
    Verdict,
)
from platform_service.service import PlatformService
from platform_service.store import InMemoryStore

_LEARNER = "deployer"


@pytest.fixture
def env(track_factory: Any, ticket_factory: Any, grading_report_factory: Any) -> Any:
    track = TrackManifest.model_validate(track_factory()["track"])
    tickets = {
        tid: TicketSpec.model_validate(
            ticket_factory(ticket_id=tid, stage=stage, prerequisites=prereqs)
        )
        for tid, stage, prereqs in (
            ("db-stage-00", 0, []),
            ("db-stage-01", 1, ["db-stage-00"]),
            ("db-stage-02", 2, ["db-stage-01"]),
        )
    }
    store = InMemoryStore()
    service = PlatformService(store, track, tickets, engine_version="v0.1.0")
    return SimpleNamespace(
        service=service, store=store, tickets=tickets, report=grading_report_factory
    )


def _live(store: InMemoryStore, ticket_id: str):
    live = [
        inst
        for inst in store.list_instances(learner=_LEARNER, ticket_id=ticket_id)
        if inst.state is not InstanceState.STALE
    ]
    assert live, f"no live instance for {ticket_id}"
    return live[-1]


def _make_review(submission_id: str, recommendation: Recommendation) -> Review:
    return Review(
        id=f"rev-{submission_id}",
        submission_id=submission_id,
        recommendation=recommendation,
        summary="评审结论",
    )


def _attempt(
    env: Any, instance_id: str, *, passed: bool, recommendation: Recommendation
):
    """Submit a grading claim + advisory Review for an already in_progress instance."""
    report = GradingReport.model_validate(env.report(passed=passed))
    submission = env.service.submit(instance_id, grading_report=report)
    env.service.review(submission.id, _make_review(submission.id, recommendation))
    return submission


def test_enroll_sets_dag_initial_states(env: Any) -> None:
    env.service.enroll(_LEARNER)
    assert _live(env.store, "db-stage-00").state is InstanceState.AVAILABLE
    assert _live(env.store, "db-stage-01").state is InstanceState.LOCKED
    assert _live(env.store, "db-stage-02").state is InstanceState.LOCKED


def test_full_pass_flow_unlocks_immediate_dependent(env: Any) -> None:
    env.service.enroll(_LEARNER)
    inst = _live(env.store, "db-stage-00")
    env.service.start(inst.id)
    sub = _attempt(env, inst.id, passed=True, recommendation=Recommendation.APPROVE)
    decision = env.service.decide(sub.id)

    assert decision.verdict is Verdict.PASS
    assert decision.decided_by == "auto"
    assert env.store.get_decision(sub.id).inputs.correctness_gate is True
    assert _live(env.store, "db-stage-00").state is InstanceState.PASSED
    assert _live(env.store, "db-stage-01").state is InstanceState.AVAILABLE  # unlocked
    assert _live(env.store, "db-stage-02").state is InstanceState.LOCKED  # not yet


def test_decide_fails_when_the_judged_surface_was_edited(env: Any) -> None:
    """报告全绿也没用：改过判定面（canonical 测试）就是 fail，并在决定里留下证据。

    判定面来自 Track 的 ``runtime.protected_paths``，由服务在 decide 时与报告里的
    ``files_changed`` 求交——所以这条**不依赖** AI 评审，是确定性的（§11.3）。
    """
    env.service.enroll(_LEARNER)
    inst = _live(env.store, "db-stage-00")
    env.service.start(inst.id)
    claim = env.report(passed=True, files_changed=["src/test/java/AcceptanceTest.java"])
    submission = env.service.submit(
        inst.id, grading_report=GradingReport.model_validate(claim)
    )
    env.service.review(submission.id, _make_review(submission.id, Recommendation.APPROVE))

    decision = env.service.decide(submission.id)

    assert decision.inputs.correctness_gate is True
    assert decision.inputs.tampering == ["src/test/java/AcceptanceTest.java"]
    assert decision.verdict is Verdict.FAIL
    assert _live(env.store, "db-stage-00").state is InstanceState.FAILED


def test_decide_uses_the_judged_surface_list_not_the_stage_list(env: Any) -> None:
    """硬门比对的是 ``judged_surface_changes``（相对**内容作者的版本**），不是 ``files_changed``。

    两者问的是不同问题：``files_changed`` = "这一关你写了什么"（基准是学员开始这一阶段时的自己），
    ``judged_surface_changes`` = "判据有没有被动"（基准与学员自己的历史无关）。用错前者的后果是：
    学员只要在**开题之前**就把测试改掉，那份改动就落在"开始线"里，从此再也看不见。
    """
    env.service.enroll(_LEARNER)
    inst = _live(env.store, "db-stage-00")
    env.service.start(inst.id)
    claim = env.report(
        passed=True,
        files_changed=["src/main/ProductMapper.xml"],  # 他自己这一关写的
        judged_surface_changes=["src/test/java/AcceptanceTest.java"],  # 判据被动过
    )
    submission = env.service.submit(inst.id, grading_report=GradingReport.model_validate(claim))
    env.service.review(submission.id, _make_review(submission.id, Recommendation.APPROVE))

    decision = env.service.decide(submission.id)

    assert decision.inputs.tampering == ["src/test/java/AcceptanceTest.java"]
    assert decision.verdict is Verdict.FAIL


def test_decide_falls_back_to_files_changed_for_reports_from_older_platforms(env: Any) -> None:
    """本特性之前落的库没有 ``judged_surface_changes``，那时两者本来就是同一个东西。"""
    env.service.enroll(_LEARNER)
    inst = _live(env.store, "db-stage-00")
    env.service.start(inst.id)
    claim = env.report(passed=True, files_changed=["src/test/java/AcceptanceTest.java"])
    claim.pop("judged_surface_changes", None)
    submission = env.service.submit(inst.id, grading_report=GradingReport.model_validate(claim))
    env.service.review(submission.id, _make_review(submission.id, Recommendation.APPROVE))

    decision = env.service.decide(submission.id)

    assert decision.inputs.tampering == ["src/test/java/AcceptanceTest.java"]


def test_decide_ignores_changes_outside_the_judged_surface(env: Any) -> None:
    """越界但不在判定面的改动（比如业务文件、随手笔记）不该硬拦——那只是告警。"""
    env.service.enroll(_LEARNER)
    inst = _live(env.store, "db-stage-00")
    env.service.start(inst.id)
    claim = env.report(
        passed=True,
        files_changed=["src/main/ProductMapper.xml", "SCRATCH-notes.md"],
        out_of_scope_edits=["SCRATCH-notes.md"],
    )
    submission = env.service.submit(
        inst.id, grading_report=GradingReport.model_validate(claim)
    )
    env.service.review(submission.id, _make_review(submission.id, Recommendation.APPROVE))

    decision = env.service.decide(submission.id)

    assert decision.inputs.tampering == []
    assert decision.verdict is Verdict.PASS


def test_request_changes_fails_without_unlock(env: Any) -> None:
    env.service.enroll(_LEARNER)
    inst = _live(env.store, "db-stage-00")
    env.service.start(inst.id)
    sub = _attempt(
        env, inst.id, passed=True, recommendation=Recommendation.REQUEST_CHANGES
    )
    decision = env.service.decide(sub.id)

    assert decision.verdict is Verdict.FAIL
    assert _live(env.store, "db-stage-00").state is InstanceState.FAILED
    assert _live(env.store, "db-stage-01").state is InstanceState.LOCKED


def test_red_correctness_fails_even_with_approve(env: Any) -> None:
    env.service.enroll(_LEARNER)
    inst = _live(env.store, "db-stage-00")
    env.service.start(inst.id)
    sub = _attempt(env, inst.id, passed=False, recommendation=Recommendation.APPROVE)
    assert env.service.decide(sub.id).verdict is Verdict.FAIL
    assert _live(env.store, "db-stage-00").state is InstanceState.FAILED


def test_retry_reopens_and_increments_seq(env: Any) -> None:
    env.service.enroll(_LEARNER)
    inst = _live(env.store, "db-stage-00")
    env.service.start(inst.id)
    first = _attempt(env, inst.id, passed=False, recommendation=Recommendation.APPROVE)
    assert env.service.decide(first.id).verdict is Verdict.FAIL

    env.service.retry(inst.id)
    assert env.store.get_instance(inst.id).state is InstanceState.IN_PROGRESS

    second = _attempt(env, inst.id, passed=True, recommendation=Recommendation.APPROVE)
    assert second.seq == 2
    assert env.service.decide(second.id).verdict is Verdict.PASS
    assert [s.seq for s in env.store.list_submissions(inst.id)] == [1, 2]
    assert _live(env.store, "db-stage-01").state is InstanceState.AVAILABLE


def test_reissue_stales_old_and_reanchors_new(env: Any) -> None:
    env.service.enroll(_LEARNER)
    old = _live(env.store, "db-stage-00")
    fresh = env.service.reissue(
        _LEARNER,
        "db-stage-00",
        ticket_version=2,
        test_version=2,
        engine_version="v0.2.0",
    )

    assert fresh.id != old.id
    assert env.store.get_instance(old.id).state is InstanceState.STALE
    assert fresh.state is InstanceState.AVAILABLE  # root restarts available
    assert (fresh.ticket_version, fresh.test_version) == (2, 2)
    assert fresh.engine_version == "v0.2.0"


def test_decide_requires_both_report_and_review(env: Any) -> None:
    env.service.enroll(_LEARNER)
    inst = _live(env.store, "db-stage-00")
    env.service.start(inst.id)
    report = GradingReport.model_validate(env.report(passed=True))
    submission = env.service.submit(inst.id, grading_report=report)  # review missing

    with pytest.raises(GateNotReadyError):
        env.service.decide(submission.id)


def test_submit_requires_in_progress(env: Any) -> None:
    env.service.enroll(_LEARNER)
    inst = _live(env.store, "db-stage-00")  # available, not started
    report = GradingReport.model_validate(env.report(passed=True))

    with pytest.raises(IllegalTransitionError):
        env.service.submit(inst.id, grading_report=report)


# --- identity invariant -------------------------------------------------------


def test_rejects_dict_key_diverging_from_identity_id(
    track_factory: Any, ticket_factory: Any
) -> None:
    """The DAG keys on ``identity.id`` while ``enroll`` keys on the dict key.

    If those disagree, every dependent mints ``available`` instead of ``locked`` and
    ``unlock_dependents`` becomes a no-op — the prerequisite gate silently disappears.
    A descriptive stage directory (``stage-01-product-query-index``) must never be able
    to trigger that, so the divergence is rejected at construction.
    """
    track = TrackManifest.model_validate(track_factory()["track"])
    spec = TicketSpec.model_validate(
        ticket_factory(ticket_id="db-stage-01", prerequisites=[])
    )
    with pytest.raises(ValueError) as exc:
        PlatformService(
            InMemoryStore(),
            track,
            {"stage-01-product-query-index": spec},
            engine_version="v0.1.0",
        )
    assert "stage-01-product-query-index" in str(exc.value)
    assert "db-stage-01" in str(exc.value)


def test_accepts_dict_key_equal_to_identity_id(track_factory: Any, ticket_factory: Any) -> None:
    track = TrackManifest.model_validate(track_factory()["track"])
    spec = TicketSpec.model_validate(
        ticket_factory(ticket_id="db-stage-01", prerequisites=[])
    )
    service = PlatformService(
        InMemoryStore(), track, {"db-stage-01": spec}, engine_version="v0.1.0"
    )
    assert service is not None
