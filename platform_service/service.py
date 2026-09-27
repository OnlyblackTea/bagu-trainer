"""Orchestration facade for the platform control plane (design v0.5 §9.2 / §11.3).

:class:`PlatformService` threads the pure slices — :mod:`~platform_service.store`,
:mod:`~platform_service.lifecycle`, :mod:`~platform_service.unlock` and
:mod:`~platform_service.acceptance` — into one end-to-end self-study loop that is
fully unit-testable offline:

    enroll → start → submit → review → decide → (pass ⇒ unlock | fail ⇒ retry)

It owns identity minting and the version anchor (``engine_version`` here; the ticket
supplies ``ticket_version`` / ``test_version``). The AI Review and the grading report
are *inputs* the caller hands in — generating them needs a real backend and is out of scope
for this slice. No engine, no HTTP, no DB (D4).
"""

from __future__ import annotations

from pathlib import Path

from .acceptance import decide_acceptance, find_tampering
from .errors import GateNotReadyError, NotFoundError, WorkspacePathError
from .history import AttemptRecord, AttemptSummary, attempt_delta, attempt_summary, elapsed_ms_of
from .lifecycle import record_submission, transition
from .lifecycle import reissue as reissue_instance
from .models import (
    AcceptanceDecision,
    AcceptanceInputs,
    Enrollment,
    GradingReport,
    InstanceState,
    Review,
    Submission,
    TicketInstance,
    TicketSpec,
    TrackManifest,
    Verdict,
)
from .store import Store
from .unlock import build_prereq_graph, initial_state, unlock_dependents


def _assert_identity_keys(tickets: dict[str, TicketSpec]) -> None:
    """Refuse a ticket map whose keys are not each ticket's ``identity.id``.

    See :class:`PlatformService` for why: a stem-style key (a stage *directory* name)
    would decouple the unlock gate from the tickets it gates without any visible error.
    """
    diverged = sorted(
        f"{key!r} -> {ticket.identity.id!r}"
        for key, ticket in tickets.items()
        if key != ticket.identity.id
    )
    if diverged:
        raise ValueError(
            "ticket map keys must be ticket.identity.id "
            f"(the prerequisite DAG keys on it); diverged: {diverged}"
        )


class PlatformService:
    """Single-entry orchestration over one track's tickets for one store.

    ``tickets`` maps ``ticket_id → TicketSpec``; the prerequisites DAG is derived once
    at construction. Every mutation persists through the :class:`Store` seam, so a
    DB-backed store can replace :class:`~platform_service.store.InMemoryStore` without
    touching this class.

    Invariant: every key must equal its ``ticket.identity.id``. :meth:`enroll` keys
    instances on the mapping key while :func:`~platform_service.unlock.build_prereq_graph`
    keys the DAG on ``identity.id`` — if those diverge, dependents silently mint
    ``available`` instead of ``locked`` and the prerequisite gate disappears. Enforced
    here rather than trusted, because the divergence is invisible at the call site.
    """

    def __init__(
        self,
        store: Store,
        track: TrackManifest,
        tickets: dict[str, TicketSpec],
        *,
        engine_version: str,
    ) -> None:
        self._store = store
        self._track = track
        self._tickets = tickets
        self._engine_version = engine_version
        _assert_identity_keys(tickets)
        self._prereq_graph = build_prereq_graph(tickets.values())

    # --- identity ------------------------------------------------------------

    def _next_id(self, prefix: str) -> str:
        """Mint through the store, never from a process-local counter.

        A counter local to this object restarts at 1 with the process, and since
        ``save_*`` upserts by identity the first post-restart save would overwrite an
        existing row instead of adding one. See :meth:`Store.next_id`.
        """
        return self._store.next_id(prefix)

    def _must_get_instance(self, instance_id: str) -> TicketInstance:
        instance = self._store.get_instance(instance_id)
        if instance is None:
            raise NotFoundError(f"unknown instance: {instance_id}")
        return instance

    def _must_get_submission(self, submission_id: str) -> Submission:
        submission = self._store.get_submission(submission_id)
        if submission is None:
            raise NotFoundError(f"unknown submission: {submission_id}")
        return submission

    # --- lifecycle operations ------------------------------------------------

    def enroll(self, learner: str) -> Enrollment:
        """Admit ``learner`` and mint one TicketInstance per ticket (§9.1).

        Each instance starts at its DAG-derived :func:`initial_state` (root tickets
        ``available``, the rest ``locked``) and is anchored to the current versions.
        ``track`` is stamped on every instance: ticket ids repeat across tracks, so
        this is what lets the board and every route attribute an instance to its track.
        """
        enrollment = Enrollment(
            id=self._next_id("en"), learner=learner, track=self._track.id
        )
        self._store.save_enrollment(enrollment)
        for ticket_id, ticket in self._tickets.items():
            instance = TicketInstance(
                id=self._next_id("ti"),
                learner=learner,
                track=self._track.id,
                ticket_id=ticket_id,
                ticket_version=ticket.identity.ticket_version,
                engine_version=self._engine_version,
                test_version=ticket.grader.correctness.test_version,
                state=initial_state(ticket_id, self._prereq_graph),
            )
            self._store.save_instance(instance)
        return enrollment

    def start(self, instance_id: str) -> TicketInstance:
        """``available → in_progress``: the learner picks up the ticket."""
        instance = self._must_get_instance(instance_id)
        transition(instance, InstanceState.IN_PROGRESS)
        self._store.save_instance(instance)
        return instance

    def submit(
        self,
        instance_id: str,
        *,
        grading_report: GradingReport | None = None,
        defense: str | None = None,
    ) -> Submission:
        """``in_progress → submitted``: record a submit-as-defense attempt (§11).

        The Submission ``seq`` auto-increments over the instance's history, so a retry
        yields a NEW Submission (never overwrites). The untrusted grading report is
        re-keyed to the minted submission id before it is stored.

        ``grading_report`` is optional because grading is asynchronous (§C.3 step 6): with
        a real runner, ``submit`` must return while ``mvn test`` is still running, so the
        report cannot be an argument. It arrives later through :meth:`attach_report`. The
        submission itself is recorded either way — that is what "已提交" means, and what
        the board shows while grading is in flight.
        """
        instance = self._must_get_instance(instance_id)
        seq = len(self._store.list_submissions(instance_id)) + 1
        submission = Submission(
            id=self._next_id("sub"),
            instance_id=instance_id,
            seq=seq,
            defense_text=defense,
        )
        record_submission(instance, submission)
        self._store.save_instance(instance)
        self._store.save_submission(submission)
        if grading_report is not None:
            self.attach_report(submission.id, grading_report)
        return submission

    def attach_report(self, submission_id: str, report: GradingReport) -> GradingReport:
        """Bind a finished report to an existing submission (the async tail of ``submit``).

        Keyed by ``submission_id``, never minted here: the report must land on the
        submission the learner actually made, and the report the runner produced carries
        only a placeholder key (``pending``).
        """
        self._must_get_submission(submission_id)
        aligned = report.model_copy(update={"submission_id": submission_id})
        self._store.save_report(aligned)
        return aligned

    def review(self, submission_id: str, review: Review) -> Review:
        """``submitted → grading``: store the advisory AI Review (§11.2).

        The Review carries only a ``recommendation`` — no score. It feeds the gate but
        never decides it alone.
        """
        submission = self._must_get_submission(submission_id)
        instance = self._must_get_instance(submission.instance_id)
        transition(instance, InstanceState.GRADING)
        self._store.save_instance(instance)
        aligned = review.model_copy(update={"submission_id": submission_id})
        self._store.save_review(aligned)
        return aligned

    def decide(self, submission_id: str) -> AcceptanceDecision:
        """``grading → passed | failed`` via the deterministic gate (§11.3).

        Consumes the report's ``correctness.passed``, the Review's recommendation, the
        track's ``trust_level`` as evidence trust, and whether the learner touched the
        track's **judged surface** (``runtime.protected_paths``) — the last one is a hard
        fail regardless of red/green (§11.3). On ``pass`` the instance goes ``passed`` and
        its DAG dependents unlock; on ``fail`` it goes ``failed`` (retry path). The
        snapshotted inputs make the verdict auditable.
        """
        submission = self._must_get_submission(submission_id)
        instance = self._must_get_instance(submission.instance_id)
        report = self._store.get_report(submission_id)
        review = self._store.get_review(submission_id)
        if report is None or review is None:
            raise GateNotReadyError(
                f"submission {submission_id} needs both a grading report and a review"
            )

        # 判定面被改动 ⇒ 硬门 fail。注意这条**不依赖** AI 评审：它是确定性的（§11.3）。
        # 比对的是 ``judged_surface_changes``（相对**内容作者的版本**），不是 ``files_changed``
        # （相对学员自己开始这一阶段时的那份 dev）：判据有没有被动，跟学员自己的历史无关，否则
        # "开题之前先把测试改了"就能绕过去。旧报告没有这个字段（本特性之前落的库）时回落到
        # ``files_changed``——那时两者本来就是同一个东西。
        changed_for_gate = report.judged_surface_changes or report.files_changed
        tampering = find_tampering(changed_for_gate, self._track.runtime.protected_paths)
        inputs = AcceptanceInputs(
            correctness_gate=report.correctness.passed,
            review_recommendation=review.recommendation,
            evidence_trust=self._track.trust_level,
            tampering=tampering,
        )
        verdict = decide_acceptance(
            correctness_gate=inputs.correctness_gate,
            review_recommendation=inputs.review_recommendation,
            evidence_trust=inputs.evidence_trust,
            tampering=inputs.tampering,
            config=inputs.config,
        )
        decision = AcceptanceDecision(
            id=self._next_id("ad"),
            submission_id=submission_id,
            inputs=inputs,
            verdict=verdict,
        )
        transition(
            instance,
            InstanceState.PASSED if verdict is Verdict.PASS else InstanceState.FAILED,
        )
        self._store.save_instance(instance)
        self._store.save_decision(decision)

        if verdict is Verdict.PASS:
            # Scoped to the instance's OWN track: ticket ids repeat across tracks, so an
            # unfiltered list could "unlock" a same-named ticket in someone else's track.
            for unlocked in unlock_dependents(
                instance.ticket_id,
                self._prereq_graph,
                self._store.list_instances(learner=instance.learner, track=instance.track),
            ):
                self._store.save_instance(unlocked)
        return decision

    def retry(self, instance_id: str) -> TicketInstance:
        """``failed → in_progress``: reopen for another attempt (§9.2).

        The anchor is preserved; the next :meth:`submit` mints a Submission with an
        incremented ``seq``.
        """
        instance = self._must_get_instance(instance_id)
        transition(instance, InstanceState.IN_PROGRESS)
        self._store.save_instance(instance)
        return instance

    def attach_workspace(self, instance_id: str, path: str | None) -> TicketInstance:
        """登记（或撤销登记）学员自己的工作区目录。

        指引式 provision 的落地：平台给出 clone 命令，学员在**自己熟悉的地方**拉代码，再把路径登记
        回来；此后 diff 与阅卷都对着那个目录跑（D3：阅卷在学员自己的机器上）。``None`` = 撤销登记，
        回到平台自己准备的那个 clone。

        **只做"能不能用"的检查，不替学员修**：不存在就报不存在，不是 git 仓库就说不是。这里**故意**
        不去 clone、不去 checkout、不写他的 config——那都是他机器上的事（§C.5：平台不代跑 git
        写操作）。
        """
        instance = self._must_get_instance(instance_id)
        if path is not None:
            candidate = Path(path).expanduser()
            if not candidate.is_dir():
                raise WorkspacePathError(
                    f"目录不存在：{candidate}", reason="not_a_directory"
                )
            if not (candidate / ".git").exists():
                raise WorkspacePathError(
                    f"这个目录不是 git 仓库：{candidate}（用平台给的 clone 命令拉一份）",
                    reason="not_a_repo",
                )
            instance = instance.model_copy(update={"workspace_path": str(candidate)})
        else:
            instance = instance.model_copy(update={"workspace_path": None})
        self._store.save_instance(instance)
        return instance

    def reissue_current(self, instance_id: str) -> TicketInstance:
        """学员主动"重做这一题"：把当前实例作废（``stale``，留档）并重发一个同版本的新实例。

        为什么需要这条：状态机里 ``retry`` 只从 ``failed`` 出发，而 ``passed`` 是终点、``submitted``
        也只能往前走——"交卷之后才发现还想接着改"或者"这一关是误判/桩结果，我想真的做一遍"都没有
        回退边。``→ stale`` 是那些状态**唯一**的合法出路（§9.2），而域里本来就有 ``reissue``（版本
        漂移时用），只是从没开给学员。这里把它开成学员能主动做的动作。

        三条刻意如此的性质：

        * **留档**：旧实例只是 ``stale``，它的提交/报告/判定都还在历史里，不是删除；
        * **同版本重锚**：新实例按该 ticket **当前**的 ``ticket_version`` / ``test_version`` 与引擎
          锚重新铸造，回到 DAG 派生的初始态（该锁的还是锁着）；
        * **不收回已解锁的后继阶段**：解锁是单调的（§9.2），重做前一关不会把已解锁的下一关收回去。
          这是有意的——学员可能已经在下一关干了活，平台不该把他的进度吃掉。
        """
        instance = self._must_get_instance(instance_id)
        ticket = self._tickets[instance.ticket_id]
        old, fresh = reissue_instance(
            instance,
            new_id=self._next_id("ti"),
            ticket_version=ticket.identity.ticket_version,
            test_version=ticket.grader.correctness.test_version,
            engine_version=self._engine_version,
            state=initial_state(instance.ticket_id, self._prereq_graph),
        )
        if instance.workspace_path:
            # 学员自己登记的目录要**继承**：他的代码就在那儿，重做不该把他踢去一个空目录。
            # （平台自己准备的工作区按新实例 id 重新 clone，那是"新一次尝试"该有的样子。）
            fresh = fresh.model_copy(update={"workspace_path": instance.workspace_path})
        self._store.save_instance(old)
        self._store.save_instance(fresh)
        return fresh

    def recent_attempts(self, learner: str, *, limit: int = 8) -> list[AttemptSummary]:
        """这位学员在本 Track 下**最近**的几次提交，最新的在前（跨实例）。

        首页"最近练习"用它。跨实例是重点：学员想知道的是"我上次干到哪了"，不在乎那条提交属
        于哪个 instance；而**状态取实例的当前状态**——一次 fail 之后 retry 过，首页该显示"开发中"
        而不是那次 fail（两件事都有用，但列表里只放得下一个，见 :class:`AttemptSummary`）。
        """
        rows: list[AttemptSummary] = []
        for instance in self._store.list_instances(learner=learner, track=self._track.id):
            for submission in self._store.list_submissions(instance.id):
                rows.append(
                    attempt_summary(
                        instance=instance,
                        submission=submission,
                        run=self._store.get_grading_run(submission.id),
                        report=self._store.get_report(submission.id),
                        decision=self._store.get_decision(submission.id),
                    )
                )
        rows.sort(key=lambda r: r.created_at, reverse=True)
        return rows[:limit]

    def history(self, instance_id: str) -> list[AttemptRecord]:
        """Every attempt on one instance, oldest first, each diffed against the previous.

        A retry mints a NEW Submission (§9.2), so "all the tries at this ticket" is
        exactly the instance's submission list in ``seq`` order. Assembling it here
        (rather than in the route) keeps the two rules that matter in one place:

        * a missing piece never hides the record — an attempt whose report or review is
          absent is still returned, because "这次没跑完" is itself the answer;
        * the comparison is against the **previous attempt**, not the best one or the
          first one — that is what a learner means by "比上次".

        Only ``seq`` order is used; nothing here depends on wall-clock times, which are
        untrusted self-reports when a real runner wrote them (§7.4).
        """
        self._must_get_instance(instance_id)
        attempts: list[AttemptRecord] = []
        for submission in self._store.list_submissions(instance_id):
            run = self._store.get_grading_run(submission.id)
            report = self._store.get_report(submission.id)
            previous = attempts[-1] if attempts else None
            delta = (
                attempt_delta(
                    against=previous.submission.seq,
                    previous_report=previous.report,
                    current_report=report,
                    previous_elapsed_ms=elapsed_ms_of(previous.grading, previous.report),
                    current_elapsed_ms=elapsed_ms_of(run, report),
                )
                if previous is not None
                else None
            )
            attempts.append(
                AttemptRecord(
                    submission=submission,
                    grading=run,
                    report=report,
                    review=self._store.get_review(submission.id),
                    decision=self._store.get_decision(submission.id),
                    delta=delta,
                )
            )
        return attempts

    def reissue(
        self,
        learner: str,
        ticket_id: str,
        *,
        ticket_version: int,
        test_version: int,
        engine_version: str,
    ) -> TicketInstance:
        """Park the live instance as ``stale`` and mint one anchoring new versions.

        Used when the ticket/test/engine version a PR was based on drifts (§9.2). The
        fresh instance restarts at its DAG-derived initial state; the stale one is kept
        for audit.
        """
        live = [
            inst
            for inst in self._store.list_instances(
                learner=learner, ticket_id=ticket_id, track=self._track.id
            )
            if inst.state is not InstanceState.STALE
        ]
        if not live:
            raise NotFoundError(f"no live instance for learner {learner} ticket {ticket_id}")
        old, fresh = reissue_instance(
            live[-1],
            new_id=self._next_id("ti"),
            ticket_version=ticket_version,
            test_version=test_version,
            engine_version=engine_version,
            state=initial_state(ticket_id, self._prereq_graph),
        )
        self._store.save_instance(old)
        self._store.save_instance(fresh)
        return fresh
