"""历史与比对（设计 spec §11 的"PR 即答辩"需要一个可回看的过程）。

学员真正会问的两个问题，这里各答一个：

* **"我这一关都干了什么？"** —— :class:`AttemptRecord`：每次尝试（Submission）连同它的
  运行记录、阅卷报告、AI 评审与判定，按 ``seq`` 排成一条时间线。
* **"这次和上次差在哪？"** —— :func:`attempt_delta`：耗时变化 + **逐条测试**的状态迁移
  （红→绿 / 绿→红 / 新增 / 消失）。比"这次过了"信息量大得多，也正好是八股练习最需要的反馈：
  上次挂在哪条、这次动没动它。

**比对是纯函数**，不碰 store、不碰 HTTP：它只吃两份报告，所以能离线单测；也正因为它是纯的，
"什么算一次变化"这条规则只有一个地方写着，前端不用再实现一遍（两份实现的措辞迟早会漂）。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from .models import (
    AcceptanceDecision,
    GradingReport,
    GradingRun,
    InstanceState,
    Review,
    Submission,
    TicketInstance,
)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TestStatusChange(_Strict):
    """一条测试在两次尝试之间的状态迁移。``frm``/``to`` 取报告里的原始 ``status`` 串。"""

    name: str
    #: 上一次的状态；``None`` = 上次报告里没有这条测试（新增/改名）。
    frm: str | None = None
    to: str | None = None

    @property
    def kind(self) -> str:
        if self.frm is None:
            return "added"
        if self.to is None:
            return "removed"
        if self.frm != "pass" and self.to == "pass":
            return "fixed"
        if self.frm == "pass" and self.to != "pass":
            return "broke"
        return "changed"


class AttemptDelta(_Strict):
    """与**上一次**尝试的差别。``against`` 是被比的那次（``seq``）。"""

    against: int
    elapsed_ms_before: int | None = None
    elapsed_ms_after: int | None = None
    passed_before: bool | None = None
    passed_after: bool | None = None
    tests: list[TestStatusChange] = Field(default_factory=list)

    @property
    def elapsed_ms_change(self) -> int | None:
        if self.elapsed_ms_before is None or self.elapsed_ms_after is None:
            return None
        return self.elapsed_ms_after - self.elapsed_ms_before


class AttemptRecord(_Strict):
    """一次尝试的全貌：提交 → 运行 → 报告 → 评审 → 判定。

    ``grading`` 可能是 ``None``（这条提交来自"运行记录落库"之前，或提交还没轮到跑），
    那时其余字段仍然照实给出——历史不该因为缺一块就整条消失。
    """

    submission: Submission
    grading: GradingRun | None = None
    report: GradingReport | None = None
    review: Review | None = None
    decision: AcceptanceDecision | None = None
    delta: AttemptDelta | None = None

    @property
    def verdict(self) -> str | None:
        return self.decision.verdict.value if self.decision else None


class AttemptSummary(_Strict):
    """一次尝试的**一行**摘要——给"最近练习"这类列表用。

    刻意不带整份报告：首页要的是"我最近干了什么、结果如何"，不是把 N 份逐条测试结果一起搬过来
    （要看细节就进那道题，那里有完整的 ``AttemptRecord``）。
    """

    instance_id: str
    ticket_id: str
    track: str
    seq: int
    submission_id: str
    created_at: datetime
    #: 实例**当前**状态（可能已经 retry/passed，与这次提交时的状态不同——两个都要有用）。
    state: InstanceState
    verdict: str | None = None
    correctness_passed: bool | None = None
    elapsed_ms: int | None = None
    #: 判定面被改动的文件数（§11.3）。非 0 时首页就该提示"绿也不算绿"。
    tampering: int = 0


def attempt_summary(
    *,
    instance: TicketInstance,
    submission: Submission,
    run: GradingRun | None,
    report: GradingReport | None,
    decision: AcceptanceDecision | None,
) -> AttemptSummary:
    return AttemptSummary(
        instance_id=instance.id,
        ticket_id=instance.ticket_id,
        track=instance.track,
        seq=submission.seq,
        submission_id=submission.id,
        created_at=submission.created_at,
        state=instance.state,
        verdict=decision.verdict.value if decision else None,
        correctness_passed=report.correctness.passed if report else None,
        elapsed_ms=elapsed_ms_of(run, report),
        tampering=len(decision.inputs.tampering) if decision else 0,
    )


def _status_by_name(report: GradingReport | None) -> dict[str, str]:
    if report is None:
        return {}
    return {t.get("name", "?"): str(t.get("status", "unknown")) for t in report.correctness.tests}


def elapsed_ms_of(run: GradingRun | None, report: GradingReport | None) -> int | None:
    """这次尝试耗了多久。

    优先用**运行记录**的 ``elapsed_ms``：它包含"重置基础设施"那一段，而那正是"阅卷为什么这么慢"
    的答案（实测里 120 秒有 109 秒在重置）。报告里的 ``duration_s`` 只是 maven 那一段，是更弱的
    证据，故只作兜底。两者都没有就如实返回 ``None``，不编 0。
    """
    if run is not None and run.elapsed_ms is not None:
        return run.elapsed_ms
    if report is None:
        return None
    duration = report.provenance.host_env.get("duration_s")
    if isinstance(duration, (int, float)):
        return int(float(duration) * 1000)
    return None


def attempt_delta(
    *,
    against: int,
    previous_report: GradingReport | None,
    current_report: GradingReport | None,
    previous_elapsed_ms: int | None = None,
    current_elapsed_ms: int | None = None,
) -> AttemptDelta:
    """两次尝试的差别。

    只报**真正变了**的测试：一条没动的测试对学员是噪音（真 Track 的 canonical 测试常有十几条，
    全列出来等于什么都没说）。顺序按名字稳定排序，便于前后两次对照。
    """
    before = _status_by_name(previous_report)
    after = _status_by_name(current_report)
    changes = [
        TestStatusChange(name=name, frm=before.get(name), to=after.get(name))
        for name in sorted(set(before) | set(after))
        if before.get(name) != after.get(name)
    ]
    return AttemptDelta(
        against=against,
        elapsed_ms_before=previous_elapsed_ms,
        elapsed_ms_after=current_elapsed_ms,
        passed_before=previous_report.correctness.passed if previous_report else None,
        passed_after=current_report.correctness.passed if current_report else None,
        tests=changes,
    )
