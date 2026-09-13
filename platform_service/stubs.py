"""A′ 的诚实占位后端（设计 spec §C.4）。

三个桩都不假装是真后端：阅卷/评审返回**预置 fixture**（pass/fail、approve/request_changes
两套，确保 decide 两条分支都被真跑到），Forge 用本地 git clone 占位 fork、返回占位 PR URL。
真实后端（Docker 阅卷 / 轻量 AI harness / Gitea）将来按同名 Protocol 替换，路由与
:class:`~platform_service.service.PlatformService` 不变。UI 一律标"模拟"。
"""

from __future__ import annotations

import itertools
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

from .forge import Workspace
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

    返回 CONTRACTS §5.2 形状的 ReviewResponse dict；``scenario`` 选 approve/request_changes。
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
    create_pr = 占位 URL。diff 两端用 ``origin/<ticket_id>`` 与 ``origin/<ticket_id>-sol``
    （内容预置分支，模拟学员把骨架改成解法；平台不写代码）。clone 幂等。"""

    def __init__(self, *, source_repo: Path, base_dir: Path) -> None:
        self._source = source_repo
        self._base_dir = base_dir

    def workspace_path(self, instance: TicketInstance) -> Path:
        return self._base_dir / instance.id

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
        return Workspace(
            path=dest,
            branch="dev",
            base_ref=f"origin/{instance.ticket_id}",
            head_ref=f"origin/{instance.ticket_id}-sol",
        )

    def create_pr(self, instance: TicketInstance, *, base: str, head: str) -> str:
        return f"https://gitea.local/{instance.learner}/simulated/pulls/{instance.id}"
