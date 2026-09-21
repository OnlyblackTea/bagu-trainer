"""Unit tests for platform_service.acceptance (design v0.5 §11.3 deterministic gate)."""

from __future__ import annotations

import pytest

from platform_service import acceptance
from platform_service.models import Recommendation, TrustLevel, Verdict


@pytest.mark.parametrize(
    ("gate", "rec", "expected"),
    [
        (True, Recommendation.APPROVE, Verdict.PASS),
        (True, Recommendation.REQUEST_CHANGES, Verdict.FAIL),
        (False, Recommendation.APPROVE, Verdict.FAIL),
        (False, Recommendation.REQUEST_CHANGES, Verdict.FAIL),
    ],
)
def test_truth_table(gate: bool, rec: Recommendation, expected: Verdict) -> None:
    verdict = acceptance.decide_acceptance(
        correctness_gate=gate,
        review_recommendation=rec,
        evidence_trust=TrustLevel.SELF,
    )
    assert verdict is expected


@pytest.mark.parametrize("trust", sorted(acceptance.ACCEPTABLE_EVIDENCE_TRUST))
def test_every_acceptable_trust_tier_can_pass(trust: TrustLevel) -> None:
    verdict = acceptance.decide_acceptance(
        correctness_gate=True,
        review_recommendation=Recommendation.APPROVE,
        evidence_trust=trust,
    )
    assert verdict is Verdict.PASS


def test_unacceptable_trust_tier_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hardening the policy set (dropping COMMUNITY) makes a community track fail."""
    monkeypatch.setattr(
        acceptance,
        "ACCEPTABLE_EVIDENCE_TRUST",
        frozenset({TrustLevel.SELF, TrustLevel.VERIFIED}),
    )
    verdict = acceptance.decide_acceptance(
        correctness_gate=True,
        review_recommendation=Recommendation.APPROVE,
        evidence_trust=TrustLevel.COMMUNITY,
    )
    assert verdict is Verdict.FAIL


def test_config_defaults_to_auto_and_is_accepted() -> None:
    verdict = acceptance.decide_acceptance(
        correctness_gate=True,
        review_recommendation=Recommendation.APPROVE,
        evidence_trust=TrustLevel.SELF,
        config="auto",
    )
    assert verdict is Verdict.PASS


def test_decision_is_deterministic() -> None:
    first = acceptance.decide_acceptance(
        correctness_gate=True,
        review_recommendation=Recommendation.APPROVE,
        evidence_trust=TrustLevel.SELF,
    )
    second = acceptance.decide_acceptance(
        correctness_gate=True,
        review_recommendation=Recommendation.APPROVE,
        evidence_trust=TrustLevel.SELF,
    )
    assert first is second is Verdict.PASS


# --- 判定面（§11.3）：改了判据，绿也不算绿 ---------------------------------------


def test_tampering_fails_even_when_everything_else_is_green() -> None:
    """本节的重点：报告全绿、评审 approve、信任级合格——只要动过判定面，仍然 fail。

    没有这一条，"把 canonical 测试改成永远绿"就是一条免费通道：§7.3 的运行时注入还没实现，
    测试此刻就躺在学员的工作区里。
    """
    verdict = acceptance.decide_acceptance(
        correctness_gate=True,
        review_recommendation=Recommendation.APPROVE,
        evidence_trust=TrustLevel.SELF,
        tampering=["src/test/java/com/devteam/seckill/stage01/ProductQueryIndexAcceptanceTest.java"],
    )
    assert verdict is Verdict.FAIL


def test_empty_tampering_does_not_block_a_green() -> None:
    verdict = acceptance.decide_acceptance(
        correctness_gate=True,
        review_recommendation=Recommendation.APPROVE,
        evidence_trust=TrustLevel.SELF,
        tampering=[],
    )
    assert verdict is Verdict.PASS


@pytest.mark.parametrize(
    ("changed", "expected"),
    [
        # 目录前缀（声明 "./src/test/"）覆盖其下一切
        (["src/test/java/AcceptanceTest.java"], ["src/test/java/AcceptanceTest.java"]),
        # 具体文件
        (["pom.xml"], ["pom.xml"]),
        # 不在判定面里 → 仍只是"越界告警"，不是篡改（不要用硬门误伤记笔记的人）
        (["SCRATCH-notes.md"], []),
        (["src/main/resources/mapper/ProductMapper.xml"], []),
        # 前缀撞名不能误判：src/testing/ 不在 src/test/ 下
        (["src/testing/Foo.java"], []),
    ],
)
def test_find_tampering(changed: list[str], expected: list[str]) -> None:
    assert acceptance.find_tampering(changed, ["./src/test/", "./pom.xml"]) == expected


def test_find_tampering_handles_globs_and_backslash_paths() -> None:
    protected = ["./src/test/java/com/devteam/seckill/stage*/", "./**/build.gradle"]
    changed = [
        "src\\test\\java\\com\\devteam\\seckill\\stage01\\T.java",
        "sub/build.gradle",
        "build.gradle",
        "src/main/Other.java",
    ]
    assert acceptance.find_tampering(changed, protected) == [
        "build.gradle",
        "src/test/java/com/devteam/seckill/stage01/T.java",
        "sub/build.gradle",
    ]
