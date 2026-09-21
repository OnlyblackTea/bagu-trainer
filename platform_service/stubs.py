"""A′ 的诚实占位后端（设计 spec §C.4）。

三个桩都不假装是真后端：阅卷/评审返回**预置 fixture**（pass/fail、approve/request_changes
两套，确保 decide 两条分支都被真跑到），Forge 用本地 git clone 占位 fork、返回占位 PR URL。
真实后端（Docker 阅卷 / 轻量 AI harness / Gitea）将来按同名 Protocol 替换，路由与
:class:`~platform_service.service.PlatformService` 不变。UI 一律标"模拟"。
"""

from __future__ import annotations

import itertools
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

from .forge import Workspace, WorkspaceRefMissingError
from .models import (
    GradingReport,
    ReportCorrectness,
    ReportProvenance,
    TicketInstance,
    TicketSpec,
)

_STAMP = "2026-09-10T00:00:00+00:00"


class StubGradingRunner:
    """返回预置 GradingReport；``scenario`` 选 pass/fail（结构化满足 GradingRunner）。"""

    def __init__(self, *, scenario: Literal["pass", "fail"] = "pass") -> None:
        self._scenario = scenario

    def grade(
        self, *, instance: TicketInstance, ticket: TicketSpec, workspace: Path
    ) -> GradingReport:
        passed = self._scenario == "pass"
        correctness = ticket.grader.correctness
        return GradingReport(
            submission_id="pending",  # service.submit 会重新键到铸造的 submission id
            ticket=instance.ticket_id,
            ticket_version=instance.ticket_version,
            test_version=instance.test_version,
            engine_version=instance.engine_version,
            learner=instance.learner,
            provenance=ReportProvenance(
                generated_at=_STAMP,
                generated_by="stub-grading/a-prime",
                host_env={"simulated": True, "workspace": str(workspace)},
            ),
            correctness=ReportCorrectness(
                test_group=correctness.test_group,
                passed=passed,
                tests=[
                    {
                        "name": correctness.canonical_test,
                        "status": "pass" if passed else "fail",
                    }
                ],
            ),
            files_changed=list(ticket.scope.files_to_change),
        )


class StubReviewEngine:
    """结构化满足 :class:`platform_service.ai_review.ReviewEngine`（不 import 它）。

    返回 Review 响应形状的 dict；``scenario`` 选 approve/request_changes。
    request_changes 带一条锚定行级评论，供 UI 演示行级评审；approve 无评论。
    """

    def __init__(
        self, *, scenario: Literal["approve", "request_changes"] = "approve"
    ) -> None:
        self._scenario = scenario
        self._n = itertools.count(1)

    async def create_submission(
        self,
        session_id: str,
        *,
        diff: str,
        summary: str | None = None,
        defense: str | None = None,
        grading_report: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "id": f"stub-sub-{next(self._n)}",
            "session_id": session_id,
            "plan_version": 1,
            "phase": "action_review",
            "created_at": "2026-09-10T00:00:00Z",
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
        request_changes = self._scenario == "request_changes"
        comments = (
            [
                {
                    "dimension": "security",
                    "text": "模拟意见：用户输入被拼进了 SQL（占位后端，非真实评审）。",
                    "quote": 'String sql = "select";',
                    "path": "src/Foo.java",
                    "line": 354,
                    "side": "right",
                    "anchor": "src/Foo.java#right:354",
                }
            ]
            if request_changes
            else []
        )
        return {
            "id": f"stub-rev-{submission_id}",
            "session_id": session_id,
            "submission_id": submission_id,
            "plan_version": 1,
            "created_at": "2026-09-10T00:00:00Z",
            "result": {
                "summary": "模拟评审结论（占位后端）。",
                "recommendation": "request_changes" if request_changes else "approve",
                "dimension_findings": [
                    {"dimension": d, "commentary": f"{d}：模拟评语。"} for d in dimensions
                ],
                "comments": comments,
                "unanchored": [],
                "truncated_files": [],
            },
        }


class StubForgeClient:
    """占位 Forge：provision = 本地 ``git clone`` 源仓库到 ``base_dir/<instance.id>``；
    create_pr = 占位 URL。

    **ref 由 Track 声明，不由占位逻辑发明。** diff 两端是 ``origin/<stage>``（该阶段的分支，
    骨架）与 ``origin/<stage>-sol``（内容预置的解法 ref，冒充"学员已改对"——A′ 没有人真的
    写代码）。``stage`` 来自构造时注入的 ``stage_refs``（``identity.id`` → 阶段名，见
    :func:`platform_service.content_loader.load_stage_refs`）：§C.5 的分支是按**阶段**命名的，
    不是按 ticket id，两者只是碰巧在 demo 里同过名。

    provision 幂等，并在 clone 后**校验这两个 ref 真的存在**——不满足就抛
    :class:`~platform_service.forge.WorkspaceRefMissingError`，把内容侧的配置错误在
    provision 阶段点明，而不是留给稍后的 ``git diff`` 抛一个对学员毫无解释力的非零退出。
    """

    #: 占位用的解法 ref 后缀（真实模型里这个位置应是学员自己的 dev ref）。
    SOLUTION_REF_SUFFIX = "-sol"

    def __init__(
        self, *, source_repo: Path, base_dir: Path, stage_refs: Mapping[str, str]
    ) -> None:
        self._source = source_repo
        self._base_dir = base_dir
        self._stage_refs = dict(stage_refs)

    def workspace_path(self, instance: TicketInstance) -> Path:
        return self._base_dir / instance.id

    def _refs(self, instance: TicketInstance, repo: Path) -> tuple[str, str]:
        stage = self._stage_refs.get(instance.ticket_id)
        if stage is None:
            raise WorkspaceRefMissingError(
                repo=repo,
                ref=None,
                available=sorted(set(self._stage_refs.values())),
                reason=(
                    f"track declares no stage branch for ticket {instance.ticket_id!r}"
                ),
            )
        return f"origin/{stage}", f"origin/{stage}{self.SOLUTION_REF_SUFFIX}"

    @staticmethod
    def _available_refs(repo: Path) -> list[str]:
        out = subprocess.run(
            ["git", "for-each-ref", "--format=%(refname:short)", "refs/heads", "refs/remotes"],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return sorted(out.stdout.split())

    def _must_have_ref(self, repo: Path, ref: str) -> None:
        probe = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", ref],
            cwd=repo,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if probe.returncode != 0:
            raise WorkspaceRefMissingError(
                repo=repo,
                ref=ref,
                available=self._available_refs(repo),
                reason=(
                    "track-repo lacks the stage branch this ticket is diffed against; "
                    "§C.5 requires one branch per stage"
                ),
            )

    def provision(self, instance: TicketInstance) -> Workspace:
        dest = self.workspace_path(instance)
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                ["git", "clone", "-q", str(self._source), str(dest)],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
        base_ref, head_ref = self._refs(instance, dest)
        self._must_have_ref(dest, base_ref)
        self._must_have_ref(dest, head_ref)
        return Workspace(path=dest, branch="dev", base_ref=base_ref, head_ref=head_ref)

    def create_pr(self, instance: TicketInstance, *, base: str, head: str) -> str:
        return f"https://gitea.local/{instance.learner}/simulated/pulls/{instance.id}"
