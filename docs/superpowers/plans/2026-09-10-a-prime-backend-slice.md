# A′ 引擎无关后端切片 实现计划（Plan 1 / 2）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不依赖 prar-agent 的前提下，用 FastAPI 把已验证的 `PlatformService` 自习闭环（发卷 → provision → submit → review → decide → unlock/retry）暴露成一套纯 REST 控制面，三个外部依赖（阅卷 / 评审 / Forge）以诚实占位桩 + 双分支 fixture 接入。

**Architecture:** 纯域层 `platform_service/` 不动（闭环已在 `test_service.py` 全测过）；新增两个 Protocol 缝（`GradingRunner`、`ForgeClient`，`ReviewEngine` 已存在）+ 三个桩 + 一个 demo track-repo 构建器；新增组合根 `platform_api/`（schemas / deps / app / routes），用 `app.state` 持有 store+service+forge+桩工厂，路由是薄适配。错误经异常处理器映射成 `{"detail": "<短码>"}`。

**Tech Stack:** Python 3.13（target py311）· FastAPI + Starlette TestClient · httpx 0.28 · pydantic 2.13 · 本地 git 2.53（fixture/diff）· pytest 9 + pytest-asyncio（auto）· ruff（E/F/I/UP/B，line-length 100）。

**范围说明：** 这是 A′ 的**后端**计划，单独可跑可测（TestClient 驱动整条闭环）。**前端 SPA（`frontend/`）是 Plan 2**，依赖本计划冻结的 REST 契约，待 Plan 1 执行通过后另写。本计划**不删任何 prar-agent 文件**（退休是 spec Section B，独立授权）。

**测试命令（Git Bash，无 make）：**
- 单测：`.venv/Scripts/python.exe -m pytest <path> -v`
- 全量：`.venv/Scripts/python.exe -m pytest`
- 静态：`.venv/Scripts/python.exe -m ruff check .`

> **沙箱注记（本机执行环境）：** pytest 的 `tmp_path` 在本机沙箱下不可用——`%TEMP%` 在工作区外，
> 且 `tempfile.mkdtemp` 在工作区内建的目录也会被拒写，pytest 的 `--basetemp` 目录还会被锁死。
> 因此执行本计划时用**加宽权限**跑测试；不加宽时所有用 `tmp_path` 的用例会以 `PermissionError`
> 报 ERROR（是环境限制，不是代码缺陷）。对齐后基线：全量 `151 passed`，`ruff` 全绿。

---

## Task 0: 契约对齐（已完成，先于本计划其余任务）

**为什么先做：** 本计划原稿假设"stage 目录名 == ticket 的 `identity.id` == `manifest.stages` 条目"，
于是 `build_service` 按 `manifest.stages` 逐个 `load_ticket`。真实的 Track 样板（`seckill-lab`）**两条都不满足**，
导致原稿在真 Track 上根本 boot 不起来，并会把一个**静默失效的解锁门**固化进代码：

1. **Ticket 发现有两个互相打架的真相源。** `admission.validate_track` 用 glob `stages/*/ticket.yaml`；
   原稿 `build_service` 用 `manifest.stages`。`seckill-lab` 的 manifest 声明了 `stage-00-onboarding`，
   而该 stage **故意不出 ticket**（环境入职而非考核工单，理由见其 `stages/README.md`）
   → `load_ticket` 抛 `ContentValidationError: file not found`，应用启动即失败。
2. **目录名 ≠ `identity.id`，而 DAG 键错了。** 目录是 `stage-01-product-query-index`，
   ticket 的 `identity.id` 却是 `db-stage-01`。`PlatformService.enroll` 用**字典键**铸造实例，
   而 `unlock.build_prereq_graph` 用 **`identity.id`** 建图。两者不一致时**所有后继实例被铸成
   `available` 而不是 `locked`**——前置门被静默绕过、`unlock_dependents` 变成空操作。
   原稿的 demo fixture 让三者同名，因此**掩盖**了这个缺陷而非暴露它。

**已落地（改动 + 测试）：**

- `platform_service/content_loader.py` — 新增 `discover_ticket_paths(track_dir)`（glob `stages/*/ticket.yaml`，
  稳定排序）与 `load_tickets(track_dir) -> dict[str, TicketSpec]`（**按 `identity.id` 为键**；重复 identity
  或零 ticket 抛 `ContentValidationError`）。**`identity.id` 是唯一权威身份**；`manifest.stages` 是
  **看板清单**，允许声明"只上榜、不出卷"的 stage。
- `platform_service/admission.py` — 改用 `load_tickets`（与组合根同一个真相源）；新增**硬门**
  `manifest_coverage`：每个被发现的 ticket 目录都必须声明在 `manifest.stages` 里（**单向**——允许 manifest
  多声明，不允许 ticket 目录无人声明，否则它不上榜却会被铸实例、其 stage 分支也永不设门）。
- `platform_service/service.py` — `PlatformService.__init__` 新增 `_assert_identity_keys`：字典键必须等于
  `identity.id`，否则抛 `ValueError`，让上述静默分歧**结构上不可能**复发。
- 测试：`tests/test_ticket_discovery.py`（新增 7 例）+ `tests/test_admission.py`（新增 4 例）
  + `tests/test_service.py`（新增 2 例）。

**验证：** 全量 `151 passed`（基线 138 + 新增 13），`ruff` 全绿；真 Track 冒烟——`seckill-lab` admission
8 项全 PASS（含 `manifest_coverage`），`load_tickets` 发现 `['db-stage-01']`，`stage-00-onboarding`
正确识别为 board-only，enroll 后根实例 `available`、`start` 转 `in_progress`。

**对本计划其余任务的影响（已同步到下文）：**
- Task 5 的 demo repo 改为**镜像真 Track 形状**：目录名描述式（`stage-01-product-query-index`）而
  `identity.id` 是 `db-stage-01`，并额外声明一个**不出 ticket 的 board-only stage**
  （`stage-03-deadlock-optional`），使 fixture 成为该契约的**回归护栏**而不是掩盖者。
- Task 7 的 `build_service` 改为 `load_tickets(content_dir)`，不再按 `manifest.stages` 逐个加载。
- Task 9 的 `GET /tracks/{track_id}` 必须能表达"上榜但没有 ticket"的 stage。

---

## 文件结构（先锁定边界）

**新建（域层缝 + 桩）：**
- `platform_service/grading.py` — `GradingRunner` Protocol（阅卷缝）
- `platform_service/forge.py` — `Workspace` 冻结数据类 + `ForgeClient` Protocol（fork/clone/PR 缝）
- `platform_service/stubs.py` — `StubGradingRunner` / `StubReviewEngine` / `StubForgeClient`（三个诚实占位后端）

**新建（组合根 `platform_api/`）：**
- `platform_api/__init__.py` — 包说明
- `platform_api/demo_repo.py` — `build_demo_track_repo(dest) -> Path`：写 schema-valid 内容 + 建多阶段 git 分支，一份 fixture 同时喂发卷 / provision / diff
- `platform_api/schemas.py` — 请求/响应 DTO（`extra="forbid"`）+ `gate_instruction` + `instance_out`
- `platform_api/deps.py` — 组合根：`PLATFORM_VERSION`、`build_service(content_dir)`、桩工厂
- `platform_api/stubs_wiring.py` — 把"ForgeClient 用哪个实现"收口在一处（A′ = `StubForgeClient`）
- `platform_api/app.py` — `create_app(...)` + 异常处理器 + 挂路由
- `platform_api/routes/__init__.py`
- `platform_api/routes/track.py` — 发卷（GET /tracks, /tracks/{id}, /tickets/{id}）
- `platform_api/routes/progress.py` — 生命周期（enroll/instances/start/submit/review/decide/retry）
- `platform_api/routes/workspace.py` — git（provision/diff/pr）

**新建（测试）：**
- `tests/test_stub_grading.py` · `tests/test_stub_review.py` · `tests/test_stub_forge.py`
- `tests/test_demo_repo.py`
- `tests/test_api_schemas.py` · `tests/test_api_deps.py` · `tests/test_api_app.py`
- `tests/test_api_track.py` · `tests/test_api_progress.py` · `tests/test_api_workspace.py` · `tests/test_api_e2e.py`

**修改：**
- `pyproject.toml` — packages 增 `platform_api`/`platform_api.routes`；顺带删 `paper_cli`
  （该目录**存在**但只有一个空的 `__init__.py`，是 CLI 降级后的死包，删它是**清理**而非修 bug）
- `tests/conftest.py` — **追加**（不改既有）`demo_app` / `client` 两个 fixture

**关键复用（不改）：** `PlatformService`、`InMemoryStore`、`content_loader.load_track` / `load_tickets`、`review_diff.generate_diff/preflight_diff_volume`、`ai_review.generate_review/ReviewEngine`、`models.*`、`lifecycle.IllegalTransitionError`。

---

## Task 1: 后端依赖与包注册（配置任务，非 TDD）

**Files:**
- Modify: `pyproject.toml:49-50`（`[tool.setuptools] packages`）

- [ ] **Step 1: 安装 fastapi + uvicorn 到 .venv**

`fastapi` 在可选 `service` extra 里、从未装过；A′ 只需 fastapi+uvicorn（不装 sqlalchemy/asyncpg——`InMemoryStore` 够用，且 asyncpg 在 Windows 上要编译，徒增失败面）。

Run:
```bash
cd "F:/workspace/bagu-trainer" && .venv/Scripts/python.exe -m pip install "fastapi>=0.111" "uvicorn>=0.30"
```
Expected: 成功安装；末尾 `Successfully installed fastapi-... starlette-... uvicorn-...`

- [ ] **Step 2: 验证可导入 + TestClient 可用**

Run:
```bash
.venv/Scripts/python.exe -c "import fastapi, uvicorn; from fastapi.testclient import TestClient; print('fastapi', fastapi.__version__, 'TestClient OK')"
```
Expected: 打印 `fastapi 0.x.y TestClient OK`，退出码 0

- [ ] **Step 3: 修改 packages 列表**

把 `pyproject.toml` 第 50 行：
```toml
packages = ["orchestrator", "platform_service", "paper_cli", "contracts"]
```
改为（删 `paper_cli`——该目录只剩一个空的 `__init__.py`，是 CLI 降级为未来可选项后的死包；
加 `platform_api` 与其子包）：
```toml
packages = ["orchestrator", "platform_service", "contracts", "platform_api", "platform_api.routes"]
```

- [ ] **Step 4: 验证既有测试仍全绿（未引入回归）**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: 既有用例全 PASS（`platform_api` 尚未创建，但 packages 改动不影响 pythonpath 导入）

- [ ] **Step 5: 提交**

```bash
git add pyproject.toml
git commit -m "chore: 注册 platform_api 包并装 fastapi/uvicorn（A′ 后端）"
```

---

## Task 2: GradingRunner 缝 + StubGradingRunner（双分支）

**Files:**
- Create: `platform_service/grading.py`
- Create: `platform_service/stubs.py`
- Test: `tests/test_stub_grading.py`

- [ ] **Step 1: 写失败测试**

`tests/test_stub_grading.py`:
```python
"""StubGradingRunner 的双分支 fixture（pass/fail）+ GradingRunner 缝结构化满足。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from platform_service.grading import GradingRunner
from platform_service.models import InstanceState, TicketInstance, TicketSpec
from platform_service.stubs import StubGradingRunner


def _instance(ticket: TicketSpec) -> TicketInstance:
    return TicketInstance(
        id="ti-1",
        learner="deployer",
        ticket_id=ticket.identity.id,
        ticket_version=ticket.identity.ticket_version,
        engine_version="a-prime-0.1.0",
        test_version=ticket.grader.correctness.test_version,
        state=InstanceState.IN_PROGRESS,
    )


def test_pass_scenario_reports_green(ticket_factory: Any) -> None:
    ticket = TicketSpec.model_validate(ticket_factory())
    report = StubGradingRunner(scenario="pass").grade(
        instance=_instance(ticket), ticket=ticket, workspace=Path(".")
    )
    assert report.correctness.passed is True
    assert report.correctness.test_group == ticket.grader.correctness.test_group
    assert report.learner == "deployer"
    assert report.ticket == ticket.identity.id


def test_fail_scenario_reports_red(ticket_factory: Any) -> None:
    ticket = TicketSpec.model_validate(ticket_factory())
    report = StubGradingRunner(scenario="fail").grade(
        instance=_instance(ticket), ticket=ticket, workspace=Path(".")
    )
    assert report.correctness.passed is False


def test_stub_structurally_satisfies_protocol(ticket_factory: Any) -> None:
    assert isinstance(StubGradingRunner(), GradingRunner)
```

- [ ] **Step 2: 跑测试看它失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_stub_grading.py -v`
Expected: FAIL —`ModuleNotFoundError: No module named 'platform_service.grading'`（缝与桩都还没建）

- [ ] **Step 3: 写 GradingRunner 缝**

`platform_service/grading.py`:
```python
"""本地阅卷缝（A′）。

平台在学习者自己的机器上跑测试，结果是**不可信自报告**（设计 §7.4）。A′ 用
:class:`~platform_service.stubs.StubGradingRunner` 占位；真实 ``DockerGradingRunner``
（Docker + JDK17 + Maven，未来切片）将实现同一 Protocol，调用方零改动。
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from .models import GradingReport, TicketInstance, TicketSpec


@runtime_checkable
class GradingRunner(Protocol):
    """对一个 in_progress 实例的工作区跑阅卷，产出 GradingReport（不含 submission_id 绑定，
    由 :meth:`platform_service.service.PlatformService.submit` 重新键到铸造的 submission）。"""

    def grade(
        self, *, instance: TicketInstance, ticket: TicketSpec, workspace: Path
    ) -> GradingReport: ...
```

- [ ] **Step 4: 写 StubGradingRunner**

`platform_service/stubs.py`:
```python
"""A′ 的诚实占位后端（设计 spec §C.4）。

三个桩都不假装是真后端：阅卷/评审返回**预置 fixture**（pass/fail、approve/request_changes
两套，确保 decide 两条分支都被真跑到），Forge 用本地 git clone 占位 fork、返回占位 PR URL。
真实后端（Docker 阅卷 / 轻量 AI harness / Gitea）将来按同名 Protocol 替换，路由与
:class:`~platform_service.service.PlatformService` 不变。UI 一律标"模拟"。
"""

from __future__ import annotations

import itertools
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

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
```

- [ ] **Step 5: 跑测试看它通过**

Run: `.venv/Scripts/python.exe -m pytest tests/test_stub_grading.py -v`
Expected: 3 passed

- [ ] **Step 6: ruff + 提交**

Run: `.venv/Scripts/python.exe -m ruff check platform_service/grading.py platform_service/stubs.py tests/test_stub_grading.py`
Expected: `All checks passed!`
```bash
git add platform_service/grading.py platform_service/stubs.py tests/test_stub_grading.py
git commit -m "feat: GradingRunner 缝 + StubGradingRunner 双分支占位（A′）"
```

---

## Task 3: StubReviewEngine（approve / request_changes）

**Files:**
- Modify: `platform_service/stubs.py`（追加 `StubReviewEngine`）
- Test: `tests/test_stub_review.py`

> `ReviewEngine` Protocol 已在 `platform_service/ai_review.py` 存在；桩结构化满足它，**不改 ai_review.py**。返回 dict 形状严格对齐 `tests/test_ai_review.py` 里 `_REVIEW_RESPONSE` 的 CONTRACTS §5.2 形状（`_map_review_response` 据此投影）。

- [ ] **Step 1: 写失败测试**

`tests/test_stub_review.py`:
```python
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
```

- [ ] **Step 2: 跑测试看它失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_stub_review.py -v`
Expected: FAIL —`ImportError: cannot import name 'StubReviewEngine'`

- [ ] **Step 3: 追加 StubReviewEngine 到 stubs.py**

在 `platform_service/stubs.py` 末尾追加：
```python
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
                    "quote": "String sql = \"select\";",
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
```

- [ ] **Step 4: 跑测试看它通过**

Run: `.venv/Scripts/python.exe -m pytest tests/test_stub_review.py -v`
Expected: 2 passed

- [ ] **Step 5: ruff + 提交**

Run: `.venv/Scripts/python.exe -m ruff check platform_service/stubs.py tests/test_stub_review.py`
Expected: `All checks passed!`
```bash
git add platform_service/stubs.py tests/test_stub_review.py
git commit -m "feat: StubReviewEngine approve/request_changes 占位（A′）"
```

---

## Task 4: ForgeClient 缝 + Workspace + StubForgeClient

**Files:**
- Create: `platform_service/forge.py`
- Modify: `platform_service/stubs.py`（追加 `StubForgeClient`）
- Test: `tests/test_stub_forge.py`

> provision = 本地 `git clone` 源仓库到 `base_dir/<instance.id>`（占位 fork）；diff 用 `origin/<ticket_id>` ↔ `origin/<ticket_id>-sol` 两个内容预置分支（模拟"学员把骨架改成解法"，平台不写代码，D1 不破）。clone 后远端分支即 `origin/*`，故 ref 带 `origin/` 前缀。

- [ ] **Step 1: 写失败测试**

`tests/test_stub_forge.py`:
```python
"""StubForgeClient：clone 占位 fork、diff 非空、PR 占位 URL。源仓库用本地 git fixture。"""

from __future__ import annotations

import subprocess
from pathlib import Path

from platform_service.forge import ForgeClient, Workspace
from platform_service.models import TicketInstance
from platform_service.review_diff import generate_diff
from platform_service.stubs import StubForgeClient


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True, encoding="utf-8"
    )


def _source_repo(path: Path) -> Path:
    """最小源仓库：main + 骨架分支 db-stage-00 + 解法分支 db-stage-00-sol。"""
    path.mkdir(parents=True)
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "t@example.com")
    _git(path, "config", "user.name", "Tester")
    _git(path, "config", "commit.gpgsign", "false")
    (path / "f.txt").write_text("todo\n", encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-qm", "skeleton")
    _git(path, "branch", "db-stage-00")
    _git(path, "checkout", "-q", "-b", "db-stage-00-sol", "db-stage-00")
    (path / "f.txt").write_text("solved\n", encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-qm", "solution")
    _git(path, "checkout", "-q", "main")
    return path


def _instance(ticket_id: str = "db-stage-00") -> TicketInstance:
    return TicketInstance(
        id=f"ti-{ticket_id}",
        learner="deployer",
        ticket_id=ticket_id,
        ticket_version=1,
        engine_version="a-prime-0.1.0",
        test_version=1,
    )


def test_provision_clones_and_diff_is_nonempty(tmp_path: Path) -> None:
    source = _source_repo(tmp_path / "src")
    forge = StubForgeClient(source_repo=source, base_dir=tmp_path / "ws")
    ws = forge.provision(_instance())
    assert isinstance(ws, Workspace)
    assert ws.path.exists() and (ws.path / "f.txt").exists()
    assert ws.base_ref == "origin/db-stage-00"
    assert ws.head_ref == "origin/db-stage-00-sol"
    diff = generate_diff(ws.path, base=ws.base_ref, head=ws.head_ref)
    assert "todo" in diff and "solved" in diff


def test_provision_is_idempotent(tmp_path: Path) -> None:
    source = _source_repo(tmp_path / "src")
    forge = StubForgeClient(source_repo=source, base_dir=tmp_path / "ws")
    first = forge.provision(_instance())
    second = forge.provision(_instance())
    assert first.path == second.path  # 不重复 clone


def test_create_pr_returns_placeholder(tmp_path: Path) -> None:
    source = _source_repo(tmp_path / "src")
    forge = StubForgeClient(source_repo=source, base_dir=tmp_path / "ws")
    inst = _instance()
    url = forge.create_pr(inst, base="origin/db-stage-00", head="origin/db-stage-00-sol")
    assert url.startswith("https://") and inst.id in url


def test_stub_structurally_satisfies_protocol(tmp_path: Path) -> None:
    forge = StubForgeClient(source_repo=tmp_path, base_dir=tmp_path / "ws")
    assert isinstance(forge, ForgeClient)
```

- [ ] **Step 2: 跑测试看它失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_stub_forge.py -v`
Expected: FAIL —`ModuleNotFoundError: No module named 'platform_service.forge'`

- [ ] **Step 3: 写 forge 缝**

`platform_service/forge.py`:
```python
"""Forge 缝（A′）：fork/clone 与 PR 的抽象。

学员驱动本地 git，平台只 gate + 指示（设计 spec §C.5）：provision 把 track-repo 拉到本地
工作区（占位 = 本地 clone；未来 = Gitea fork），diff 用 :mod:`platform_service.review_diff`
跑真本地 git，PR 是占位 URL（未来 = Gitea PR）。平台**不替学员跑 git 写操作**。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from .models import TicketInstance


@dataclass(frozen=True)
class Workspace:
    """一个实例的本地工作区。``base_ref``/``head_ref`` 是 diff 的两端（占位用 origin/* 分支
    模拟"骨架 → 学员解法"）；``simulated`` 供 UI 标"模拟"。"""

    path: Path
    branch: str
    base_ref: str
    head_ref: str
    simulated: bool = True


@runtime_checkable
class ForgeClient(Protocol):
    def workspace_path(self, instance: TicketInstance) -> Path: ...

    def provision(self, instance: TicketInstance) -> Workspace: ...

    def create_pr(self, instance: TicketInstance, *, base: str, head: str) -> str: ...
```

- [ ] **Step 4: 追加 StubForgeClient 到 stubs.py**

先在 `platform_service/stubs.py` 顶部 import 区加入：
```python
import subprocess

from .forge import Workspace
```
再在文件末尾追加：
```python
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
```

- [ ] **Step 5: 跑测试看它通过**

Run: `.venv/Scripts/python.exe -m pytest tests/test_stub_forge.py -v`
Expected: 4 passed

- [ ] **Step 6: ruff + 提交**

Run: `.venv/Scripts/python.exe -m ruff check platform_service/forge.py platform_service/stubs.py tests/test_stub_forge.py`
Expected: `All checks passed!`
```bash
git add platform_service/forge.py platform_service/stubs.py tests/test_stub_forge.py
git commit -m "feat: ForgeClient 缝 + Workspace + StubForgeClient（本地 clone 占位 fork，A′）"
```

---

## Task 5: demo track-repo 构建器（内容 + 多阶段 git 分支）

**Files:**
- Create: `platform_api/__init__.py`
- Create: `platform_api/demo_repo.py`
- Test: `tests/test_demo_repo.py`

> 一份 fixture 同时喂：发卷（`content_loader` 读 `track.yaml` + `stages/<dir>/ticket.yaml`）、provision（被 clone 的源仓库）、diff（每张 ticket 的 `<ticket_id>` 骨架分支 ↔ `<ticket_id>-sol` 解法分支）。内容 dict 严格对齐 `tests/conftest.py` 的 `make_track`/`make_ticket`（已证 schema-valid）。

> **⚠️ 执行时已修订（Task 0 的后果）——下面的代码块是**原稿**，实际落地见 `platform_api/demo_repo.py`。**
> 原稿让"stage 目录名 == `identity.id` == `manifest.stages` 条目"三者同名，那会**掩盖** Task 0 修掉的
> 解锁门静默失效缺陷。实际实现改为**镜像真 Track（seckill-lab）的形状**：
>
> - 目录名描述式（`stage-01-product-query-index`），`identity.id` 是 `db-stage-01`；
> - `DEMO_STAGES` 是**看板清单**（4 条），其中 `BOARD_ONLY_STAGES = ("stage-03-deadlock-optional",)`
>   **只上榜、不出卷**（镜像真 Track 的 `stage-00-onboarding`）；ticket 只有 3 张（`db-stage-00/01/02`）；
> - `TICKET_DIRS` 导出 `identity.id → 目录名`；`skeleton_repo` 用 `"."`（与真 Track 一样"本仓根即骨架仓"），
>   否则准入门的 `files_to_change` 检查会找不到文件；`bagu-map.md` 带 `## Stage` 标题，使
>   `bagu_map_ref` 锚点可解析；
> - `tests/test_demo_repo.py` 相应新增：`test_stage_dirs_diverge_from_identity_ids`、
>   `test_board_only_stage_ships_no_ticket`、`test_demo_repo_passes_admission`（demo 自身准入干净）、
>   `test_enroll_locks_dependents`（DAG 回归护栏）。原稿的 `test_content_loads` 改为
>   `load_tickets(repo)` 断言 3 张 ticket 及其 prerequisites。

- [ ] **Step 1: 写失败测试**

`tests/test_demo_repo.py`:
```python
"""demo track-repo：内容可被 content_loader 加载、阶段分支齐全、diff 非空。"""

from __future__ import annotations

import subprocess
from pathlib import Path

from platform_api.demo_repo import DEMO_STAGES, build_demo_track_repo
from platform_service.content_loader import load_ticket, load_track
from platform_service.models import TicketInstance
from platform_service.review_diff import generate_diff
from platform_service.stubs import StubForgeClient


def _branches(repo: Path) -> set[str]:
    out = subprocess.run(
        ["git", "branch", "--format=%(refname:short)"],
        cwd=repo, check=True, capture_output=True, text=True, encoding="utf-8",
    )
    return set(out.stdout.split())


def test_content_loads(tmp_path: Path) -> None:
    repo = build_demo_track_repo(tmp_path / "demo")
    track = load_track(repo / "track.yaml")
    assert track.manifest.stages == list(DEMO_STAGES)
    ticket = load_ticket(repo / "stages" / "db-stage-00" / "ticket.yaml")
    assert ticket.identity.id == "db-stage-00"
    assert ticket.progression.prerequisites == []
    ticket01 = load_ticket(repo / "stages" / "db-stage-01" / "ticket.yaml")
    assert ticket01.progression.prerequisites == ["db-stage-00"]


def test_stage_branches_exist(tmp_path: Path) -> None:
    repo = build_demo_track_repo(tmp_path / "demo")
    branches = _branches(repo)
    for tid in DEMO_STAGES:
        assert tid in branches and f"{tid}-sol" in branches


def test_provision_diff_nonempty(tmp_path: Path) -> None:
    repo = build_demo_track_repo(tmp_path / "demo")
    forge = StubForgeClient(source_repo=repo, base_dir=tmp_path / "ws")
    inst = TicketInstance(
        id="ti-1", learner="deployer", ticket_id="db-stage-00",
        ticket_version=1, engine_version="a-prime-0.1.0", test_version=1,
    )
    ws = forge.provision(inst)
    assert generate_diff(ws.path, base=ws.base_ref, head=ws.head_ref).strip()
```

- [ ] **Step 2: 跑测试看它失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_demo_repo.py -v`
Expected: FAIL —`ModuleNotFoundError: No module named 'platform_api'`

- [ ] **Step 3: 建包 + 写 demo_repo.py**

`platform_api/__init__.py`:
```python
"""A′ 组合根：把纯域层 platform_service 暴露成 FastAPI REST 控制面。

唯一知道"现在用桩、将来用真后端"的地方（依赖注入）。退休 prar-agent 后无引擎、无 WS，
纯 REST。GPL 边界已随引擎退休消解，本包整体 Apache-2.0。
"""
```

`platform_api/demo_repo.py`:
```python
"""A′ demo track-repo 构建器（诚实占位内容）。

建一个本地 git 仓库，既是发卷内容源（track.yaml + stages/<tid>/ticket.yaml），又是
StubForgeClient 的 clone 源：每阶段一个骨架分支 ``<tid>`` 与一个解法分支 ``<tid>-sol``，
diff 两端模拟"学员把骨架改成解法"。真实 Gitea/内容仓库落地后本模块整体删除。

内容 dict 对齐 content/schema/*.schema.json（与 tests/conftest.py 的 make_track/make_ticket
同构），故能被 content_loader 加载。
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import yaml

DEMO_TRACK_ID = "database-bagu"
DEMO_STAGES = ("db-stage-00", "db-stage-01", "db-stage-02")
_STAGE_TITLES = {
    "db-stage-00": "环境入职：把骨架跑起来",
    "db-stage-01": "商品搜索查询慢：索引与执行计划",
    "db-stage-02": "订单事务：超卖与隔离级别",
}
_PREREQS = {"db-stage-00": [], "db-stage-01": ["db-stage-00"], "db-stage-02": ["db-stage-01"]}


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True, encoding="utf-8"
    )


def _track_dict() -> dict[str, Any]:
    entries = {"compose": "./docker/docker-compose.yml", "build": "./scripts/build.sh",
               "tests": "./stages/*/tests/"}
    digest_pin = {name: f"sha256:{name}0d1e2f" for name in entries}
    digest_pin["mysql"] = "sha256:0d1e2f3a4b5c"
    digest_pin["redis"] = "sha256:9a8b7c6d5e4f"
    runtime: dict[str, Any] = {"digest_pin": digest_pin}
    runtime.update(entries)
    return {
        "track": {
            "id": DEMO_TRACK_ID,
            "name": "数据库八股实战",
            "language": "zh-CN",
            "trust_level": "self",
            "manifest": {
                "requires": {"java": "17", "build": "maven"},
                "skeleton_repo": "./skeleton",
                "bagu_map": "./bagu-map.md",
                "stages": list(DEMO_STAGES),
            },
            "runtime": runtime,
            "contribute": {
                "license": "Apache-2.0",
                "maintainers": ["@onlyblacktea"],
                "review": {"ci": "platform validate-track", "signed_by": []},
            },
        }
    }


def _ticket_dict(tid: str) -> dict[str, Any]:
    stage = int(tid.rsplit("-", 1)[-1])
    return {
        "identity": {
            "id": tid, "schema_version": 1, "ticket_version": 1,
            "stage": stage, "title": _STAGE_TITLES[tid],
        },
        "brief": {
            "problem": f"{_STAGE_TITLES[tid]}：按工单完成本阶段任务（占位内容）。",
            "concepts": ["最左前缀", "覆盖索引", "EXPLAIN"],
        },
        "scope": {"files_to_change": [f"src/{tid}/Solution.java"]},
        "grader": {
            "correctness": {
                "test_group": tid, "test_version": 1,
                "canonical_test": f"tests/{tid}/AcceptanceTest.java",
                "expect": "初始红 → 改对后绿",
            },
            "performance": {"type": "explain", "assert": "type != 'ALL'"},
            "concurrency": None,
        },
        "hints": [{"id": "H1", "text": "先看 EXPLAIN，再决定索引列序。"}],
        "submission": {
            "template": {"require": ["改后的代码", "一句话理由"]},
            "bagu_map_ref": "bagu-map.md#stage",
        },
        "mentor_policy": {"mode": "pure_mentor", "may_write": []},
        "progression": {"prerequisites": list(_PREREQS[tid])},
    }


def build_demo_track_repo(dest: Path) -> Path:
    """在 ``dest`` 建好 demo track-repo（内容 + 阶段分支），返回 ``dest``。"""
    dest.mkdir(parents=True, exist_ok=True)
    _git(dest, "init", "-q", "-b", "main")
    _git(dest, "config", "user.email", "demo@example.com")
    _git(dest, "config", "user.name", "Demo")
    _git(dest, "config", "commit.gpgsign", "false")

    (dest / "track.yaml").write_text(
        yaml.safe_dump(_track_dict(), allow_unicode=True), encoding="utf-8"
    )
    (dest / "bagu-map.md").write_text("# 八股地图\n\n占位内容。\n", encoding="utf-8")
    for tid in DEMO_STAGES:
        stage_dir = dest / "stages" / tid
        stage_dir.mkdir(parents=True, exist_ok=True)
        (stage_dir / "ticket.yaml").write_text(
            yaml.safe_dump(_ticket_dict(tid), allow_unicode=True), encoding="utf-8"
        )
        skel = dest / "src" / tid
        skel.mkdir(parents=True, exist_ok=True)
        (skel / "Solution.java").write_text(f"// {tid} skeleton: TODO\n", encoding="utf-8")
    _git(dest, "add", "-A")
    _git(dest, "commit", "-qm", "track content + skeleton")

    for tid in DEMO_STAGES:
        _git(dest, "checkout", "-q", "-b", tid, "main")
        _git(dest, "checkout", "-q", "-b", f"{tid}-sol", tid)
        (dest / "src" / tid / "Solution.java").write_text(
            f"// {tid} solution: 模拟学员解法\npublic class Solution {{}}\n", encoding="utf-8"
        )
        _git(dest, "add", "-A")
        _git(dest, "commit", "-qm", f"{tid} solution")
    _git(dest, "checkout", "-q", "main")
    return dest
```

- [ ] **Step 4: 跑测试看它通过**

Run: `.venv/Scripts/python.exe -m pytest tests/test_demo_repo.py -v`
Expected: 3 passed

- [ ] **Step 5: ruff + 提交**

Run: `.venv/Scripts/python.exe -m ruff check platform_api/ tests/test_demo_repo.py`
Expected: `All checks passed!`
```bash
git add platform_api/__init__.py platform_api/demo_repo.py tests/test_demo_repo.py
git commit -m "feat: demo track-repo 构建器（内容+阶段分支，A′ 占位）"
```

---

## Task 6: DTO schemas + gate 指示文案

**Files:**
- Create: `platform_api/schemas.py`
- Test: `tests/test_api_schemas.py`

- [ ] **Step 1: 写失败测试**

`tests/test_api_schemas.py`:
```python
"""请求 DTO 的 extra=forbid、instance_out 注入 gate 文案。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from platform_api.schemas import EnrollRequest, ReviewRequest, SubmitRequest, instance_out
from platform_service.models import InstanceState, TicketInstance


def _inst(state: InstanceState) -> TicketInstance:
    return TicketInstance(
        id="ti-1", learner="deployer", ticket_id="db-stage-00",
        ticket_version=1, engine_version="a-prime-0.1.0", test_version=1, state=state,
    )


def test_enroll_request_rejects_extra() -> None:
    with pytest.raises(ValidationError):
        EnrollRequest.model_validate({"learner": "x", "bogus": 1})


def test_submit_and_review_defaults() -> None:
    assert SubmitRequest().pr_url is None
    assert ReviewRequest().dimensions is None


def test_instance_out_carries_state_and_gate() -> None:
    out = instance_out(_inst(InstanceState.FAILED))
    assert out["state"] == "failed"
    assert "retry" in out["gate"]


def test_gate_text_per_state() -> None:
    assert "通过" in instance_out(_inst(InstanceState.PASSED))["gate"]
    assert "未解锁" in instance_out(_inst(InstanceState.LOCKED))["gate"]
```

- [ ] **Step 2: 跑测试看它失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_api_schemas.py -v`
Expected: FAIL —`ModuleNotFoundError: No module named 'platform_api.schemas'`

- [ ] **Step 3: 写 schemas.py**

`platform_api/schemas.py`:
```python
"""A′ REST 的请求/响应 DTO（与域模型解耦，extra=forbid）+ gate 指示文案。

响应里凡域模型本身够用的（Submission/Review/Decision）直接 ``model_dump(mode="json")``；
本模块只补请求体、工作区响应与"实例 + gate 文案"投影。gate 文案体现"平台只 gate + 指示、
学员驱动本地 git"（设计 spec §C.5）。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from platform_service.forge import Workspace
from platform_service.models import InstanceState, TicketInstance


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EnrollRequest(_Strict):
    learner: str = Field(min_length=1)


class SubmitRequest(_Strict):
    pr_url: str | None = None
    defense: str | None = Field(default=None, max_length=20000)


class ReviewRequest(_Strict):
    dimensions: list[str] | None = Field(default=None, min_length=1)


_GATE: dict[InstanceState, str] = {
    InstanceState.LOCKED: "未解锁：先通过全部前置阶段。",
    InstanceState.AVAILABLE: "可开始：start 本实例，再 provision 拉取本地工作区。",
    InstanceState.IN_PROGRESS: "开发中：在本地 dev 分支完成本阶段，再 submit（平台不代写代码）。",
    InstanceState.SUBMITTED: "已提交：对其发起 review。",
    InstanceState.GRADING: "评审中：发起 decide 取确定性结论。",
    InstanceState.PASSED: "已通过：下一阶段已解锁——从其分支 cherry-pick / 提 PR 合入 dev 后继续。",
    InstanceState.FAILED: "未通过：按评审意见在本地改 dev，再 retry 重新提交。",
    InstanceState.STALE: "已作废（版本漂移）：用 reissue 重发新实例。",
}


def gate_instruction(state: InstanceState) -> str:
    return _GATE[state]


def instance_out(instance: TicketInstance) -> dict[str, Any]:
    return {**instance.model_dump(mode="json"), "gate": gate_instruction(instance.state)}


def workspace_out(ws: Workspace) -> dict[str, Any]:
    return {
        "path": str(ws.path),
        "branch": ws.branch,
        "base_ref": ws.base_ref,
        "head_ref": ws.head_ref,
        "simulated": ws.simulated,
    }
```

- [ ] **Step 4: 跑测试看它通过**

Run: `.venv/Scripts/python.exe -m pytest tests/test_api_schemas.py -v`
Expected: 4 passed

- [ ] **Step 5: ruff + 提交**

Run: `.venv/Scripts/python.exe -m ruff check platform_api/schemas.py tests/test_api_schemas.py`
Expected: `All checks passed!`
```bash
git add platform_api/schemas.py tests/test_api_schemas.py
git commit -m "feat: REST DTO + gate 指示文案（A′）"
```

---

## Task 7: 组合根 deps.py

**Files:**
- Create: `platform_api/deps.py`
- Test: `tests/test_api_deps.py`

- [ ] **Step 1: 写失败测试**

`tests/test_api_deps.py`:
```python
"""build_service 从 demo repo 装配 PlatformService；桩工厂按 scenario 出对应后端。"""

from __future__ import annotations

from pathlib import Path

from platform_api.demo_repo import build_demo_track_repo
from platform_api.deps import PLATFORM_VERSION, build_service, grading_runner, review_engine
from platform_service.models import InstanceState, Recommendation
from platform_service.service import PlatformService
from platform_service.stubs import StubGradingRunner, StubReviewEngine


def test_build_service_wires_dag(tmp_path: Path) -> None:
    repo = build_demo_track_repo(tmp_path / "demo")
    store, track, tickets, service = build_service(repo)
    assert isinstance(service, PlatformService)
    assert track.id == "database-bagu"
    assert set(tickets) == {"db-stage-00", "db-stage-01", "db-stage-02"}
    enrollment = service.enroll("deployer")
    states = {i.ticket_id: i.state for i in store.list_instances(learner="deployer")}
    assert states["db-stage-00"] is InstanceState.AVAILABLE
    assert states["db-stage-01"] is InstanceState.LOCKED
    assert enrollment.track == track.id
    assert PLATFORM_VERSION


def test_factories_pick_scenario() -> None:
    assert isinstance(grading_runner("pass"), StubGradingRunner)
    assert isinstance(review_engine("approve"), StubReviewEngine)
```

- [ ] **Step 2: 跑测试看它失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_api_deps.py -v`
Expected: FAIL —`ModuleNotFoundError: No module named 'platform_api.deps'`

- [ ] **Step 3: 写 deps.py**

`platform_api/deps.py`:
```python
"""组合根（设计 spec §C.2）：A′ 唯一知道"现在用桩"的地方。

:func:`build_service` 从内容目录装配纯域层（store + track + tickets + PlatformService）；
桩工厂按 ``scenario`` 产出对应占位后端。真实后端落地时只改这里（与 app.py 的装配），
路由与 PlatformService 不动。``engine_version`` 字段是版本锚（模型遗留命名，退休引擎后
不再指引擎，避免 churn 存活的 models.py 而保留）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from platform_service.content_loader import load_track, load_tickets
from platform_service.service import PlatformService
from platform_service.store import InMemoryStore, Store
from platform_service.models import TicketSpec, TrackManifest
from platform_service.stubs import StubGradingRunner, StubReviewEngine

PLATFORM_VERSION = "a-prime-0.1.0"

GradingScenario = Literal["pass", "fail"]
ReviewScenario = Literal["approve", "request_changes"]


def build_service(
    content_dir: Path,
) -> tuple[Store, TrackManifest, dict[str, TicketSpec], PlatformService]:
    """加载 ``content_dir`` 的 track + 全部 ticket，装配一个 InMemoryStore 上的服务。

    Ticket 发现走 :func:`~platform_service.content_loader.load_tickets`（glob
    ``stages/*/ticket.yaml``、按 ``identity.id`` 为键），**不按 `manifest.stages` 逐个加载**：
    后者是看板清单，允许声明"只上榜、不出卷"的 stage（真 Track 的 ``stage-00-onboarding``
    就是这样），按它加载会让应用在真 Track 上启动失败（Task 0）。
    """
    track = load_track(content_dir / "track.yaml")
    tickets = load_tickets(content_dir)
    store = InMemoryStore()
    service = PlatformService(store, track, tickets, engine_version=PLATFORM_VERSION)
    return store, track, tickets, service


def grading_runner(scenario: GradingScenario) -> StubGradingRunner:
    return StubGradingRunner(scenario=scenario)


def review_engine(scenario: ReviewScenario) -> StubReviewEngine:
    return StubReviewEngine(scenario=scenario)
```

- [ ] **Step 4: 跑测试看它通过**

Run: `.venv/Scripts/python.exe -m pytest tests/test_api_deps.py -v`
Expected: 2 passed

- [ ] **Step 5: ruff + 提交**

Run: `.venv/Scripts/python.exe -m ruff check platform_api/deps.py tests/test_api_deps.py`
Expected: `All checks passed!`（若报 import 排序 I001，按提示调整 `from platform_service...` 顺序：`content_loader` / `models` / `service` / `store` / `stubs` 字母序）
```bash
git add platform_api/deps.py tests/test_api_deps.py
git commit -m "feat: 组合根 deps（build_service + 桩工厂，A′）"
```

---

## Task 8: app 工厂 + 异常处理器

**Files:**
- Create: `platform_api/app.py`
- Test: `tests/test_api_app.py`

> 异常映射（设计 spec §C.8）：`IllegalTransitionError`→409 `illegal_transition`；`KeyError`→404 `not_found`；`ValueError`（decide 缺 report/review）→409 `decide_needs_report_and_review`；`ReviewDiffTooLargeError`→422 `diff_too_large`。路由暂不挂（Task 9-11 挂），本任务只验工厂能 boot + 处理器生效。

- [ ] **Step 1: 写失败测试**

`tests/test_api_app.py`:
```python
"""create_app boot、app.state 装配齐全、异常处理器映射短码。"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from platform_api.app import create_app
from platform_api.demo_repo import build_demo_track_repo
from platform_service.lifecycle import IllegalTransitionError
from platform_service.ai_review import ReviewDiffTooLargeError
from platform_service.review_diff import DiffVolume


def _app(tmp_path: Path) -> FastAPI:
    repo = build_demo_track_repo(tmp_path / "demo")
    return create_app(content_dir=repo, forge_base=tmp_path / "ws")


def test_create_app_wires_state(tmp_path: Path) -> None:
    app = _app(tmp_path)
    assert app.state.track.id == "database-bagu"
    assert set(app.state.tickets) == {"db-stage-00", "db-stage-01", "db-stage-02"}
    assert app.state.service is not None and app.state.forge is not None


def test_illegal_transition_maps_409(tmp_path: Path) -> None:
    app = _app(tmp_path)

    @app.get("/_boom_illegal")
    def _boom() -> None:
        raise IllegalTransitionError("nope")

    assert TestClient(app, raise_server_exceptions=False).get("/_boom_illegal").status_code == 409


def test_value_error_maps_409(tmp_path: Path) -> None:
    app = _app(tmp_path)

    @app.get("/_boom_value")
    def _boom() -> None:
        raise ValueError("needs both")

    r = TestClient(app, raise_server_exceptions=False).get("/_boom_value")
    assert r.status_code == 409 and r.json()["detail"] == "decide_needs_report_and_review"


def test_diff_too_large_maps_422(tmp_path: Path) -> None:
    app = _app(tmp_path)

    @app.get("/_boom_diff")
    def _boom() -> None:
        raise ReviewDiffTooLargeError(
            DiffVolume(total_chars=1, fits=False, first_file_overflows=True,
                       truncated_files=("big.java",))
        )

    assert TestClient(app, raise_server_exceptions=False).get("/_boom_diff").status_code == 422
```

- [ ] **Step 2: 跑测试看它失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_api_app.py -v`
Expected: FAIL —`ModuleNotFoundError: No module named 'platform_api.app'`

- [ ] **Step 3: 写 app.py（先不挂路由）**

`platform_api/app.py`:
```python
"""FastAPI 应用工厂 + 异常映射（设计 spec §C.3/§C.8）。

``create_app`` 是组合根入口：建 store/service/forge/桩工厂挂到 ``app.state``，注册把域异常
翻成 ``{"detail": "<短码>"}`` 的处理器，并挂三组路由。纯 REST，无 SSE/WS（无 AI 流可推）。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from platform_service.ai_review import ReviewDiffTooLargeError
from platform_service.lifecycle import IllegalTransitionError

from .deps import PLATFORM_VERSION, build_service, grading_runner, review_engine
from .stubs_wiring import build_forge  # 见 Step 3 末说明


def _register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(IllegalTransitionError)
    async def _illegal(request: Request, exc: IllegalTransitionError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": "illegal_transition"})

    @app.exception_handler(KeyError)
    async def _key(request: Request, exc: KeyError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": "not_found"})

    @app.exception_handler(ValueError)
    async def _value(request: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(
            status_code=409, content={"detail": "decide_needs_report_and_review"}
        )

    @app.exception_handler(ReviewDiffTooLargeError)
    async def _too_large(request: Request, exc: ReviewDiffTooLargeError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": "diff_too_large"})


def create_app(
    *, content_dir: Path, forge_source: Path | None = None, forge_base: Path | None = None
) -> FastAPI:
    app = FastAPI(title="bagu-trainer A′", version=PLATFORM_VERSION)
    store, track, tickets, service = build_service(content_dir)
    base = forge_base or (content_dir.parent / "_workspaces")
    forge = build_forge(source_repo=forge_source or content_dir, base_dir=base)

    app.state.store = store
    app.state.track = track
    app.state.tickets = tickets
    app.state.service = service
    app.state.forge = forge
    app.state.grading_runner = grading_runner
    app.state.review_engine = review_engine

    _register_exception_handlers(app)

    from .routes import progress, track as track_routes, workspace

    app.include_router(track_routes.router)
    app.include_router(progress.router)
    app.include_router(workspace.router)
    return app
```

`platform_api/stubs_wiring.py`（把"现在用 StubForgeClient"这一个选择收口，便于将来换真 Gitea）:
```python
"""把 ForgeClient 的具体实现选择收口在一处（A′ = StubForgeClient）。"""

from __future__ import annotations

from pathlib import Path

from platform_service.forge import ForgeClient
from platform_service.stubs import StubForgeClient


def build_forge(*, source_repo: Path, base_dir: Path) -> ForgeClient:
    base_dir.mkdir(parents=True, exist_ok=True)
    return StubForgeClient(source_repo=source_repo, base_dir=base_dir)
```

> 注：Step 3 的 app.py 已 `include_router`，但路由模块在 Task 9-11 才建。为让本任务测试可跑，**先建空路由占位**：见 Step 4。

- [ ] **Step 4: 建三组空路由占位（仅本任务用，后续填充）**

`platform_api/routes/__init__.py`:
```python
"""A′ REST 路由：track（发卷）/ progress（生命周期）/ workspace（git）。"""
```

`platform_api/routes/track.py`、`platform_api/routes/progress.py`、`platform_api/routes/workspace.py` 各写：
```python
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()
```

- [ ] **Step 5: 跑测试看它通过**

Run: `.venv/Scripts/python.exe -m pytest tests/test_api_app.py -v`
Expected: 4 passed

- [ ] **Step 6: ruff + 提交**

Run: `.venv/Scripts/python.exe -m ruff check platform_api/ tests/test_api_app.py`
Expected: `All checks passed!`
```bash
git add platform_api/app.py platform_api/stubs_wiring.py platform_api/routes/ tests/test_api_app.py
git commit -m "feat: app 工厂 + 异常映射 + 路由占位（A′）"
```

---

## Task 9: 发卷路由 track.py + 共享 app fixture

**Files:**
- Modify: `platform_api/routes/track.py`（填充）
- Modify: `tests/conftest.py`（**追加** `demo_app` / `client` fixture，不动既有）
- Test: `tests/test_api_track.py`

> **⚠️ 执行时已修订（Task 0 的后果）——下面的 `get_track` 代码是**原稿**，实际落地见
> `platform_api/routes/track.py`。** 原稿 `stages: [...] for tid in track.manifest.stages` 里直接
> `tickets[tid]`，在真 Track / demo fixture 上会因 **board-only stage 没有 ticket** 而 `KeyError`。
> 实际实现：
>
> - `create_app` 额外挂 `app.state.stage_tickets = load_ticket_dirs(content_dir)`
>   （`content_loader` 新增：**stage 目录名 → ticket** 的索引，仅供看板渲染；身份仍是 `identity.id`）；
> - 每个看板条目输出 `kind`：`assessed`（有卷，带 `ticket_id` / `stage_index` / `title` /
>   `prerequisites`）或 `board_only`（`ticket_id: null`）；
> - `stage` 字段是**目录名**，`ticket_id` 才是 `identity.id`——`tests/test_api_track.py` 新增
>   `test_stage_is_the_directory_name_and_ticket_id_is_the_identity` 与
>   `test_get_ticket_is_keyed_by_identity_not_directory`（用目录名取卷必须 404）来钉死这一点；
> - 原稿 `test_get_track_lists_stages_with_prereqs` 改为先按 `kind == "assessed"` 过滤再断言。

- [ ] **Step 1: 追加共享 fixture 到 conftest.py（文件末尾）**

在 `tests/conftest.py` 末尾追加（lazy import，保持 collection 期不依赖 fastapi/platform_api）：
```python
@pytest.fixture
def demo_app(tmp_path: Path) -> Any:
    """建 demo track-repo 并组装 FastAPI app（每个测试独立 tmp）。"""
    from platform_api.app import create_app
    from platform_api.demo_repo import build_demo_track_repo

    repo = build_demo_track_repo(tmp_path / "demo")
    return create_app(content_dir=repo, forge_base=tmp_path / "ws")


@pytest.fixture
def client(demo_app: Any) -> Any:
    from fastapi.testclient import TestClient

    return TestClient(demo_app)
```

- [ ] **Step 2: 写失败测试**

`tests/test_api_track.py`:
```python
"""发卷：列 track、取 track（含阶段 DAG）、取 ticket；未知 id → 404。"""

from __future__ import annotations

from typing import Any


def test_list_tracks(client: Any) -> None:
    r = client.get("/tracks")
    assert r.status_code == 200
    assert r.json()[0]["id"] == "database-bagu"


def test_get_track_lists_stages_with_prereqs(client: Any) -> None:
    r = client.get("/tracks/database-bagu")
    assert r.status_code == 200
    stages = r.json()["stages"]
    assert [s["ticket_id"] for s in stages] == ["db-stage-00", "db-stage-01", "db-stage-02"]
    assert stages[1]["prerequisites"] == ["db-stage-00"]


def test_get_track_404(client: Any) -> None:
    assert client.get("/tracks/nope").status_code == 404


def test_get_ticket_renders_content(client: Any) -> None:
    r = client.get("/tickets/db-stage-00")
    assert r.status_code == 200
    body = r.json()
    assert body["problem"] and body["files_to_change"] and body["hints"]
    assert body["grader"]["test_group"] == "db-stage-00"


def test_get_ticket_404(client: Any) -> None:
    assert client.get("/tickets/nope").status_code == 404
```

- [ ] **Step 3: 跑测试看它失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_api_track.py -v`
Expected: FAIL — 全部 404（路由还是空占位）

- [ ] **Step 4: 填充 track.py**

`platform_api/routes/track.py`:
```python
"""发卷路由：渲染 Track 固定内容（设计 spec §C.3/§C.5）。

内容是 Track 固定的（作者写好的 ticket.yaml + stage 分支），**不经 LLM**。防剧透（D2）：
本路由只暴露工单内容（problem/concepts/files_to_change/hints），**不暴露解法分支**——解法
只在 workspace.diff 里、且仅对已 provision 的实例可达。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(tags=["track"])


@router.get("/tracks")
def list_tracks(request: Request) -> list[dict[str, Any]]:
    track = request.app.state.track
    return [
        {
            "id": track.id,
            "name": track.name,
            "language": track.language,
            "trust_level": track.trust_level.value,
            "stages": list(track.manifest.stages),
        }
    ]


@router.get("/tracks/{track_id}")
def get_track(track_id: str, request: Request) -> dict[str, Any]:
    track = request.app.state.track
    if track.id != track_id:
        raise HTTPException(status_code=404, detail="track_not_found")
    tickets = request.app.state.tickets
    return {
        "id": track.id,
        "name": track.name,
        "language": track.language,
        "trust_level": track.trust_level.value,
        "stages": [
            {
                "ticket_id": tid,
                "stage": tickets[tid].identity.stage,
                "title": tickets[tid].identity.title,
                "prerequisites": list(tickets[tid].progression.prerequisites),
            }
            for tid in track.manifest.stages
        ],
    }


@router.get("/tickets/{ticket_id}")
def get_ticket(ticket_id: str, request: Request) -> dict[str, Any]:
    ticket = request.app.state.tickets.get(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="ticket_not_found")
    correctness = ticket.grader.correctness
    return {
        "id": ticket.identity.id,
        "stage": ticket.identity.stage,
        "title": ticket.identity.title,
        "ticket_version": ticket.identity.ticket_version,
        "problem": ticket.brief.problem,
        "concepts": list(ticket.brief.concepts),
        "files_to_change": list(ticket.scope.files_to_change),
        "hints": [{"id": h.id, "text": h.text} for h in ticket.hints],
        "grader": {
            "test_group": correctness.test_group,
            "canonical_test": correctness.canonical_test,
            "expect": correctness.expect,
        },
    }
```

- [ ] **Step 5: 跑测试看它通过**

Run: `.venv/Scripts/python.exe -m pytest tests/test_api_track.py -v`
Expected: 5 passed

- [ ] **Step 6: ruff + 提交**

Run: `.venv/Scripts/python.exe -m ruff check platform_api/routes/track.py tests/test_api_track.py tests/conftest.py`
Expected: `All checks passed!`
```bash
git add platform_api/routes/track.py tests/test_api_track.py tests/conftest.py
git commit -m "feat: 发卷路由（渲染 Track 固定内容，防剧透，A′）"
```

---

## Task 10: 工作区路由 workspace.py（provision / diff / pr）

**Files:**
- Modify: `platform_api/routes/workspace.py`（填充）
- Test: `tests/test_api_workspace.py`

- [ ] **Step 1: 写失败测试**

`tests/test_api_workspace.py`:
```python
"""provision 返回工作区、diff 真本地 git 非空、pr 占位 URL；未知实例 → 404。"""

from __future__ import annotations

from typing import Any


def _enroll_start(client: Any) -> str:
    client.post("/enrollments", json={"learner": "deployer"})
    inst = client.get("/instances", params={"learner": "deployer"}).json()
    stage00 = next(i for i in inst if i["ticket_id"] == "db-stage-00")
    client.post(f"/instances/{stage00['id']}/start")
    return stage00["id"]


def test_provision_returns_workspace(client: Any) -> None:
    iid = _enroll_start(client)
    r = client.post(f"/instances/{iid}/provision")
    assert r.status_code == 200
    body = r.json()
    assert body["branch"] == "dev" and body["simulated"] is True
    assert body["base_ref"] == "origin/db-stage-00"


def test_diff_is_real_local_git(client: Any) -> None:
    iid = _enroll_start(client)
    client.post(f"/instances/{iid}/provision")
    r = client.get(f"/instances/{iid}/diff")
    assert r.status_code == 200
    body = r.json()
    assert "diff --git" in body["diff"] and body["total_chars"] > 0


def test_pr_is_placeholder(client: Any) -> None:
    iid = _enroll_start(client)
    r = client.post(f"/instances/{iid}/pr")
    assert r.status_code == 200
    assert r.json()["pr_url"].startswith("https://") and r.json()["simulated"] is True


def test_provision_unknown_instance_404(client: Any) -> None:
    assert client.post("/instances/nope/provision").status_code == 404
```

> 注：本测试依赖 enroll/start/instances 路由（Task 11）。为解耦执行顺序，**先在本任务把 progress 的 enroll/instances/start 三个只读+start 端点一并实现**（见 Step 4 附），submit/review/decide/retry 留到 Task 11。

- [ ] **Step 2: 跑测试看它失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_api_workspace.py -v`
Expected: FAIL —`/enrollments`、`/instances`、provision/diff/pr 均 404（未实现）

- [ ] **Step 3: 填充 workspace.py**

`platform_api/routes/workspace.py`:
```python
"""工作区路由：provision（占位 fork=本地 clone）/ diff（真本地 git）/ pr（占位 URL）。

平台只 gate + 指示，不替学员跑 git 写操作（设计 spec §C.5）。diff 用
:func:`platform_service.review_diff.generate_diff` 跑真本地 git；provision 幂等。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from platform_service.models import TicketInstance
from platform_service.review_diff import generate_diff, preflight_diff_volume

from ..schemas import workspace_out

router = APIRouter(tags=["workspace"])


def _must_instance(request: Request, instance_id: str) -> TicketInstance:
    instance = request.app.state.store.get_instance(instance_id)
    if instance is None:
        raise HTTPException(status_code=404, detail="instance_not_found")
    return instance


@router.post("/instances/{instance_id}/provision")
def provision(instance_id: str, request: Request) -> dict[str, Any]:
    instance = _must_instance(request, instance_id)
    return workspace_out(request.app.state.forge.provision(instance))


@router.get("/instances/{instance_id}/diff")
def diff(instance_id: str, request: Request) -> dict[str, Any]:
    instance = _must_instance(request, instance_id)
    ws = request.app.state.forge.provision(instance)  # 幂等：确保已 clone
    text = generate_diff(ws.path, base=ws.base_ref, head=ws.head_ref)
    volume = preflight_diff_volume(text)
    return {
        "base_ref": ws.base_ref,
        "head_ref": ws.head_ref,
        "diff": text,
        "total_chars": volume.total_chars,
        "truncated_files": list(volume.truncated_files),
        "simulated": ws.simulated,
    }


@router.post("/instances/{instance_id}/pr")
def create_pr(instance_id: str, request: Request) -> dict[str, Any]:
    instance = _must_instance(request, instance_id)
    ws = request.app.state.forge.provision(instance)
    url = request.app.state.forge.create_pr(instance, base=ws.base_ref, head=ws.head_ref)
    return {"pr_url": url, "simulated": True}
```

- [ ] **Step 4: 在 progress.py 先实现 enroll / instances / start（其余留 Task 11）**

`platform_api/routes/progress.py`（本任务先写到 start；submit/review/decide/retry 在 Task 11 追加）:
```python
"""生命周期路由（设计 spec §C.3）：enroll → start → submit → review → decide → retry。

业务全在 PlatformService；路由是薄适配 + gate 指示。scenario 查询参数选阅卷/评审桩的
fixture 分支（pass/fail、approve/request_changes），让前端能演示 decide 两条分支。
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request

from ..schemas import EnrollRequest, instance_out

router = APIRouter(tags=["progress"])


@router.post("/enrollments", status_code=201)
def enroll(body: EnrollRequest, request: Request) -> dict[str, Any]:
    enrollment = request.app.state.service.enroll(body.learner)
    instances = request.app.state.store.list_instances(learner=body.learner)
    return {
        "enrollment": enrollment.model_dump(mode="json"),
        "instances": [instance_out(i) for i in instances],
    }


@router.get("/instances")
def list_instances(request: Request, learner: str) -> list[dict[str, Any]]:
    return [instance_out(i) for i in request.app.state.store.list_instances(learner=learner)]


@router.get("/instances/{instance_id}")
def get_instance(instance_id: str, request: Request) -> dict[str, Any]:
    instance = request.app.state.store.get_instance(instance_id)
    if instance is None:
        raise HTTPException(status_code=404, detail="instance_not_found")
    return instance_out(instance)


@router.post("/instances/{instance_id}/start")
def start(instance_id: str, request: Request) -> dict[str, Any]:
    return instance_out(request.app.state.service.start(instance_id))
```

- [ ] **Step 5: 跑测试看它通过**

Run: `.venv/Scripts/python.exe -m pytest tests/test_api_workspace.py -v`
Expected: 4 passed

- [ ] **Step 6: ruff + 提交**

Run: `.venv/Scripts/python.exe -m ruff check platform_api/routes/workspace.py platform_api/routes/progress.py tests/test_api_workspace.py`
Expected: `All checks passed!`
```bash
git add platform_api/routes/workspace.py platform_api/routes/progress.py tests/test_api_workspace.py
git commit -m "feat: 工作区路由 provision/diff/pr + 进度路由 enroll/instances/start（A′）"
```

---

## Task 11: 进度路由补全 submit / review / decide / retry

**Files:**
- Modify: `platform_api/routes/progress.py`（追加 4 端点 + import）
- Test: `tests/test_api_progress.py`

- [ ] **Step 1: 写失败测试**

`tests/test_api_progress.py`:
```python
"""submit→review→decide 两分支 + retry；错误形状（409 非法转移 / 409 缺评审 / 404）。"""

from __future__ import annotations

from typing import Any


def _stage00(client: Any) -> str:
    client.post("/enrollments", json={"learner": "deployer"})
    inst = client.get("/instances", params={"learner": "deployer"}).json()
    iid = next(i for i in inst if i["ticket_id"] == "db-stage-00")["id"]
    client.post(f"/instances/{iid}/start")
    return iid


def test_submit_creates_submission(client: Any) -> None:
    iid = _stage00(client)
    r = client.post(f"/instances/{iid}/submit", params={"scenario": "pass"}, json={})
    assert r.status_code == 200
    assert r.json()["instance_id"] == iid and r.json()["seq"] == 1


def test_review_then_decide_pass_unlocks(client: Any) -> None:
    iid = _stage00(client)
    sub = client.post(f"/instances/{iid}/submit", params={"scenario": "pass"}, json={}).json()
    rev = client.post(
        f"/submissions/{sub['id']}/review", params={"scenario": "approve"}, json={}
    )
    assert rev.status_code == 200 and rev.json()["recommendation"] == "approve"
    dec = client.post(f"/submissions/{sub['id']}/decide")
    assert dec.status_code == 200
    assert dec.json()["decision"]["verdict"] == "pass"
    states = {i["ticket_id"]: i["state"] for i in dec.json()["instances"]}
    assert states["db-stage-00"] == "passed" and states["db-stage-01"] == "available"


def test_decide_fail_then_retry(client: Any) -> None:
    iid = _stage00(client)
    sub = client.post(f"/instances/{iid}/submit", params={"scenario": "fail"}, json={}).json()
    client.post(f"/submissions/{sub['id']}/review", params={"scenario": "approve"}, json={})
    dec = client.post(f"/submissions/{sub['id']}/decide").json()
    assert dec["decision"]["verdict"] == "fail"
    assert {i["ticket_id"]: i["state"] for i in dec["instances"]}["db-stage-00"] == "failed"
    r = client.post(f"/instances/{iid}/retry")
    assert r.status_code == 200 and r.json()["state"] == "in_progress"


def test_decide_without_review_is_409(client: Any) -> None:
    iid = _stage00(client)
    sub = client.post(f"/instances/{iid}/submit", params={"scenario": "pass"}, json={}).json()
    r = client.post(f"/submissions/{sub['id']}/decide")
    assert r.status_code == 409 and r.json()["detail"] == "decide_needs_report_and_review"


def test_submit_on_available_is_409(client: Any) -> None:
    client.post("/enrollments", json={"learner": "deployer"})
    inst = client.get("/instances", params={"learner": "deployer"}).json()
    locked = next(i for i in inst if i["ticket_id"] == "db-stage-01")["id"]
    r = client.post(f"/instances/{locked}/submit", params={"scenario": "pass"}, json={})
    assert r.status_code == 409  # locked 既非 in_progress：KeyError? 否——存在但非法转移
```

> 说明：`db-stage-01` 实例存在但状态 locked，submit 走 `record_submission→transition` 抛 `IllegalTransitionError` → 409。

- [ ] **Step 2: 跑测试看它失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_api_progress.py -v`
Expected: FAIL — submit/review/decide/retry 端点 404/405（未实现）

- [ ] **Step 3: 追加端点到 progress.py**

在 `platform_api/routes/progress.py` 顶部 import 区补：
```python
from platform_service.ai_review import generate_review
from platform_service.review_diff import generate_diff

from ..schemas import ReviewRequest, SubmitRequest
```
在文件中加常量与 4 个端点（接在 `start` 之后）：
```python
DEFAULT_DIMENSIONS = ["correctness", "readability", "test_coverage", "security"]
GradingScenario = Literal["pass", "fail"]
ReviewScenario = Literal["approve", "request_changes"]


def _must_instance(request: Request, instance_id: str):
    instance = request.app.state.store.get_instance(instance_id)
    if instance is None:
        raise HTTPException(status_code=404, detail="instance_not_found")
    return instance


@router.post("/instances/{instance_id}/submit")
def submit(
    instance_id: str,
    request: Request,
    body: SubmitRequest,
    scenario: GradingScenario = "fail",
) -> dict[str, Any]:
    state = request.app.state
    instance = _must_instance(request, instance_id)
    ticket = state.tickets[instance.ticket_id]
    ws = state.forge.provision(instance)
    report = state.grading_runner(scenario).grade(
        instance=instance, ticket=ticket, workspace=ws.path
    )
    submission = state.service.submit(
        instance_id, grading_report=report, pr_url=body.pr_url, defense=body.defense
    )
    return submission.model_dump(mode="json")


@router.post("/submissions/{submission_id}/review")
async def review(
    submission_id: str,
    request: Request,
    body: ReviewRequest,
    scenario: ReviewScenario = "request_changes",
) -> dict[str, Any]:
    state = request.app.state
    submission = state.store.get_submission(submission_id)
    if submission is None:
        raise HTTPException(status_code=404, detail="submission_not_found")
    instance = state.store.get_instance(submission.instance_id)
    ws = state.forge.provision(instance)
    diff = generate_diff(ws.path, base=ws.base_ref, head=ws.head_ref)
    engine = state.review_engine(scenario)
    review_obj = await generate_review(
        engine,
        submission.instance_id,
        diff=diff,
        dimensions=body.dimensions or DEFAULT_DIMENSIONS,
        defense_text=submission.defense_text,
    )
    saved = state.service.review(submission_id, review_obj)
    return saved.model_dump(mode="json")


@router.post("/submissions/{submission_id}/decide")
def decide(submission_id: str, request: Request) -> dict[str, Any]:
    state = request.app.state
    submission = state.store.get_submission(submission_id)
    if submission is None:
        raise HTTPException(status_code=404, detail="submission_not_found")
    decision = state.service.decide(submission_id)
    instance = state.store.get_instance(submission.instance_id)
    instances = state.store.list_instances(learner=instance.learner)
    return {
        "decision": decision.model_dump(mode="json"),
        "instances": [instance_out(i) for i in instances],
    }


@router.post("/instances/{instance_id}/retry")
def retry(instance_id: str, request: Request) -> dict[str, Any]:
    return instance_out(request.app.state.service.retry(instance_id))
```

- [ ] **Step 4: 跑测试看它通过**

Run: `.venv/Scripts/python.exe -m pytest tests/test_api_progress.py -v`
Expected: 6 passed

- [ ] **Step 5: ruff + 提交**

Run: `.venv/Scripts/python.exe -m ruff check platform_api/routes/progress.py tests/test_api_progress.py`
Expected: `All checks passed!`
```bash
git add platform_api/routes/progress.py tests/test_api_progress.py
git commit -m "feat: 进度路由 submit/review/decide/retry（双分支闭环，A′）"
```

---

## Task 12: 端到端集成测试（TestClient 跑通整条闭环）

**Files:**
- Test: `tests/test_api_e2e.py`

> 这是 Plan 1 的收口验收：证明退休引擎后，发卷 → provision → diff → submit → review → decide →（pass⇒unlock | fail⇒retry）整条链在 HTTP 上自洽。直接对应设计 spec §C.7。

- [ ] **Step 1: 写端到端测试**

`tests/test_api_e2e.py`:
```python
"""A′ 后端端到端：纯 REST 跑通自习闭环两条 decide 分支（设计 spec §C.7）。"""

from __future__ import annotations

from typing import Any


def _stage(client: Any, learner: str, ticket_id: str) -> dict[str, Any]:
    inst = client.get("/instances", params={"learner": learner}).json()
    return next(i for i in inst if i["ticket_id"] == ticket_id)


def test_pass_flow_unlocks_next_stage(client: Any) -> None:
    # 发卷
    track = client.get("/tracks/database-bagu").json()
    assert [s["ticket_id"] for s in track["stages"]][0] == "db-stage-00"
    assert client.get("/tickets/db-stage-00").status_code == 200

    # 入学 + 开始
    client.post("/enrollments", json={"learner": "deployer"})
    s00 = _stage(client, "deployer", "db-stage-00")
    assert s00["state"] == "available" and "start" in s00["gate"]
    client.post(f"/instances/{s00['id']}/start")

    # provision + diff（真本地 git）
    prov = client.post(f"/instances/{s00['id']}/provision").json()
    assert prov["simulated"] is True
    diff = client.get(f"/instances/{s00['id']}/diff").json()
    assert "diff --git" in diff["diff"]

    # submit（pass 阅卷）+ review（approve）+ decide
    sub = client.post(f"/instances/{s00['id']}/submit", params={"scenario": "pass"}, json={}).json()
    client.post(f"/submissions/{sub['id']}/review", params={"scenario": "approve"}, json={})
    dec = client.post(f"/submissions/{sub['id']}/decide").json()

    assert dec["decision"]["verdict"] == "pass"
    states = {i["ticket_id"]: i["state"] for i in dec["instances"]}
    assert states["db-stage-00"] == "passed"
    assert states["db-stage-01"] == "available"  # 解锁
    assert states["db-stage-02"] == "locked"     # 仍未解锁


def test_fail_flow_retries_then_passes(client: Any) -> None:
    client.post("/enrollments", json={"learner": "deployer"})
    s00 = _stage(client, "deployer", "db-stage-00")
    client.post(f"/instances/{s00['id']}/start")

    # 第一次：阅卷 fail → decide fail
    sub1 = client.post(f"/instances/{s00['id']}/submit", params={"scenario": "fail"}, json={}).json()
    client.post(f"/submissions/{sub1['id']}/review", params={"scenario": "approve"}, json={})
    assert client.post(f"/submissions/{sub1['id']}/decide").json()["decision"]["verdict"] == "fail"
    assert _stage(client, "deployer", "db-stage-00")["state"] == "failed"

    # retry → 第二次 pass + approve → 解锁
    client.post(f"/instances/{s00['id']}/retry")
    sub2 = client.post(f"/instances/{s00['id']}/submit", params={"scenario": "pass"}, json={}).json()
    assert sub2["seq"] == 2
    client.post(f"/submissions/{sub2['id']}/review", params={"scenario": "approve"}, json={})
    dec = client.post(f"/submissions/{sub2['id']}/decide").json()
    assert dec["decision"]["verdict"] == "pass"
    states = {i["ticket_id"]: i["state"] for i in dec["instances"]}
    assert states["db-stage-00"] == "passed" and states["db-stage-01"] == "available"
```

- [ ] **Step 2: 跑端到端测试**

Run: `.venv/Scripts/python.exe -m pytest tests/test_api_e2e.py -v`
Expected: 2 passed

- [ ] **Step 3: 全量后端套件 + ruff**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: 全部 PASS（既有 + A′ 新增；contracts/ 仍在，退休是独立授权步骤，本计划不动它）

Run: `.venv/Scripts/python.exe -m ruff check .`
Expected: `All checks passed!`

- [ ] **Step 4: 冒烟——真起服务（可选但推荐）**

Run（后台起，再 curl，再停）:
```bash
.venv/Scripts/python.exe -c "from platform_api.app import create_app; from platform_api.demo_repo import build_demo_track_repo; from pathlib import Path; import tempfile; d=Path(tempfile.mkdtemp()); repo=build_demo_track_repo(d/'demo'); app=create_app(content_dir=repo, forge_base=d/'ws'); import uvicorn; uvicorn.run(app, host='127.0.0.1', port=8123)" &
sleep 3 && curl -s http://127.0.0.1:8123/tracks && echo "" && curl -s -X POST http://127.0.0.1:8123/enrollments -H "Content-Type: application/json" -d '{"learner":"deployer"}'
```
Expected: `/tracks` 返回 database-bagu JSON；enroll 返回 201 + instances。验完杀掉后台进程。

> Windows 侧手动验时改 cmd.exe：用 `start /b` 起服务、`curl` 同上；端口 8123 自选。

- [ ] **Step 5: 提交**

```bash
git add tests/test_api_e2e.py
git commit -m "test: A′ 后端端到端闭环（pass→unlock / fail→retry）"
```

---

## 自检结论（plan self-review，对照 spec §C）

**1. spec 覆盖：**
- §C.2 架构（platform_api 组合根 + platform_service 纯域 + 三桩）→ Task 2-8 ✓
- §C.3 端点全集（tracks/tickets/enrollments/instances/start/provision/diff/pr/submit/review/decide/retry）→ Task 9-11 ✓（逐条对上）
- §C.4 三桩 + 双分支 fixture（pass/fail、approve/request_changes）→ Task 2/3/4 ✓
- §C.5 git 进度模型（provision=clone、diff=真git、PR=占位、平台不跑git写、防剧透）→ Task 4/5/9/10 ✓
- §C.7 端到端两分支 → Task 12 ✓
- §C.8 错误处理（409/404/422 短码）→ Task 8 ✓
- §C.9 测试策略（TDD、TestClient、ruff）→ 每任务 Step 1-2 先测后码 + Task 12 收口 ✓（前端 vitest/browser-use 属 Plan 2）
- §C.10 步骤 1-8（后端）→ 本计划 Task 1-12 ✓；步骤 9-12（前端）→ Plan 2

**2. 占位扫描：** 无 TBD/TODO 式步骤；每个改码步骤都给了完整代码；命令都带预期输出。`Solution.java` 里的 `TODO`/`解法` 是 demo 仓库的**内容**（模拟学员代码），不是计划占位。

**3. 类型一致性（跨任务核对）：**
- `GradingRunner.grade(*, instance, ticket, workspace)` — Task 2 定义、Task 11 调用一致 ✓
- `ForgeClient`：`provision(instance)->Workspace`、`workspace_path`、`create_pr(instance,*,base,head)->str` — Task 4 定义、Task 5/10 调用一致 ✓
- `Workspace(path, branch, base_ref, head_ref, simulated)` — Task 4 定义、Task 6 `workspace_out`、Task 10 diff 一致 ✓
- `StubReviewEngine` 返回 §5.2 dict — Task 3 定义、与 `ai_review._map_review_response` 实读字段一致（已核对 test_ai_review.py 的 `_REVIEW_RESPONSE`）✓
- `build_service(content_dir)->(store, track, tickets, service)` — Task 7 定义、Task 8 解包一致 ✓
- `build_demo_track_repo(dest)->Path`、`DEMO_STAGES` — Task 5 定义、Task 5/7/8/9 测试一致 ✓
- `instance_out` / `gate_instruction` / `workspace_out` / `EnrollRequest` / `SubmitRequest` / `ReviewRequest` — Task 6 定义、Task 9/10/11 使用一致 ✓
- `PLATFORM_VERSION` — Task 7 定义、Task 8 使用 ✓
- `create_app(*, content_dir, forge_source=None, forge_base=None)` — Task 8 定义、Task 8/9 测试一致 ✓
- 阶段 id 全程 `db-stage-00/01/02`，track id `database-bagu` — Task 5/7/9/10/11/12 一致 ✓

**已知取舍（非缺陷）：** `ReviewEngine` 的 `session_id` 形参为引擎味命名，A′ 传 `instance_id`，待真实 harness 落地再议改名（spec §B.3 已记）。`engine_version` 字段同理保留为版本锚。

---

## 执行结果（Plan 1 已执行完成）

**状态：** Task 0-12 全部落地。**全量 `215 passed`，`ruff check .` 全绿。**

| 项 | 数值 |
|---|---|
| 执行前基线 | 138 passed |
| Plan 1 执行后 | 209 passed |
| 分支模型跟进（见下节）后 | **215 passed** |
| 其中 Task 0（契约对齐）新增 | 13 例（`test_ticket_discovery.py` 7 + `test_admission.py` 4 + `test_service.py` 2） |
| 其中 Plan 1 新增 | 58 例（桩 9 + demo_repo 7 + schemas 5 + deps 3 + app 6 + track 9 + workspace 5 + progress 8 + e2e 3，另含少量既有文件内追加） |
| 分支模型跟进新增 | 6 例（`load_stage_refs` 2 + 阶段分支/board-only 无分支 2 + ref 缺失 2） |
| 静态检查 | `ruff check .` → `All checks passed!` |

**与原稿的偏差（全部已在正文标注 ⚠️）：**

1. **Task 0 是新增的**（原稿没有）：先修"ticket 发现 / identity / DAG 键"三处契约缺陷，否则原稿在真 Track 上 boot 不起来且会固化一个静默失效的解锁门。详见 Task 0 节。
2. **Task 1**：`paper_cli` 的删除理由由"目录不存在"改为"只剩空 `__init__.py` 的死包"（原稿理由与事实不符）。
3. **Task 5**：demo repo 改为**镜像真 Track 形状**（目录名 ≠ `identity.id`、含 board-only stage、`skeleton_repo="."`、bagu-map 锚点可解析），并新增 4 个护栏测试。
4. **Task 7**：`build_service` 改用 `load_tickets(content_dir)`（glob + 按 `identity.id` 为键），不再按 `manifest.stages` 逐个加载。
5. **Task 9**：`get_track` 增加 `kind`（`assessed` / `board_only`）与 `app.state.stage_tickets` 目录名索引；新增"用目录名取卷必须 404"的回归测试。
6. **文件结构**：补上原稿遗漏的 `platform_api/stubs_wiring.py`；测试清单补上 `test_api_schemas.py` / `test_api_deps.py` / `test_api_app.py`。
7. **环境注记**：本机需 `fastapi 0.141.1` + `uvicorn 0.52.4` + `starlette 1.6.0`（已装）；`TestClient` 会报两条 `StarletteDeprecationWarning`（httpx / anyio 别名），不影响用例。
8. **分支模型跟进（原稿之后新增的一轮）**：占位 Forge 不再拿 `ticket_id` 当分支名，改为按**阶段**解析并校验 ref；新增 `workspace_ref_missing` 短码。见文末决策节。

**提交切分（已落地，工作树干净）：**

| commit | 内容 |
|---|---|
| `5ac14f4` | `feat: 引擎集成层（评审生成 / diff 预处理 / 导师会话 + 工作区取证）`——这是**执行前就存在的未提交工作**（上一提交只落了纯控制面），先单独入库以免与后续混在一起 |
| `1e2d977` | `docs: 补入 A′ 切片设计 spec 与实现计划` |
| `36a0e8b` | `fix: 统一 ticket 发现与身份契约`（Task 0） |
| `b53fa0b` | `feat: A′ 引擎无关后端切片` |
| 见文末 | `fix: 分支模型按阶段解析`（决策跟进） |

**未做的事（有意）：**
- 未删任何 prar-agent 文件（那是 spec Section B，独立授权）。
- 未做前端 `frontend/`（Plan 2）。
- 未起真 uvicorn 做 Task 12 Step 4 的冒烟（TestClient 的 e2e 已覆盖同一路径；需要的话可另跑）。
- 未 push（全部为本地提交）。
- 未修 `seckill-lab`（Track 侧补 per-stage 分支）——见文末决策节的"仍待 Track 侧完成"。

---

## ✅ 已决策：保留 §C.5 的分支模型（Track 侧对齐），并让占位 Forge 不再猜 ref

**决策（用户授权自行判断）：保留我们的设计——§C.5「一个阶段一个分支」，`seckill-lab`
的单一 `solutions` 分支属于 Track 侧不符合规范，由 Track 侧修，不让平台去适配它。**

**决定性理由不是命名偏好，而是 D2 防剧透的不变量。** §8/§C.5 明确：*未解锁阶段是独立 ref，
clone/fork 只检出已解锁阶段*。把多个阶段的解法塞进同一个 `solutions` ref，等于一次 clone
交出全部答案——这直接破坏 D2。泛化 `ForgeClient` 去接受任意布局（原选项 B）会把一个
Track 编排问题塞进平台契约，并让"哪些 ref 该发给学员"从结构性保证退化成约定。
`seckill-lab` 又是由现成项目按我们的设计改造而来（其分支布局是继承来的，不是按 §C.5 设计的），
因此它是**待修的样板**，不是规范的来源。

**顺带修掉一个同类缺陷：占位 Forge 之前把 `ticket_id` 当分支名。** §C.5 的分支是按
**阶段**命名的（= `manifest.stages` 条目 = `stages/<name>/` 目录名），ticket 的 `identity.id`
是另一个命名空间——这和 Task 0 修的是同一类混淆，只是 demo 里三者恰好同名，把它盖住了。

**已落地：**

- `platform_service/forge.py` —— 明确写下 ref 契约（`base_ref` = 所属 **stage** 分支；
  `head_ref` = 学员 dev ref），并新增 `WorkspaceRefMissingError`（带缺失 ref + 现有 ref 清单）。
- `platform_service/content_loader.py` —— 新增 `load_stage_refs`（`identity.id` → 阶段名）：
  ref 布局**来自内容**，不来自占位逻辑的猜测。
- `platform_service/stubs.py` —— `StubForgeClient` 构造时接收 `stage_refs`，按阶段解析
  `origin/<stage>` ↔ `origin/<stage>-sol`，并在 clone 后**校验 ref 真的存在**，
  缺失即抛 `WorkspaceRefMissingError`（不再等到 `git diff` 抛非零退出）。
- `platform_api/app.py` —— 新短码 `workspace_ref_missing`（500，附 `ref` / `available`），
  与真故障的 `git_command_failed` 分开：前者是**内容不符合 §C.5**，后者是真故障。
- `platform_api/demo_repo.py` —— demo 分支改为按**阶段**命名，并断言 `db-stage-00` 这类
  ticket-id 分支**不存在**；board-only stage 不得有分支（否则把未发布阶段的代码也发出去）。
- 测试新增/改写：`test_stub_forge.py`（阶段解析 + 两种 ref 缺失）、`test_demo_repo.py`
  （分支按阶段命名、board-only 无分支）、`test_api_workspace.py`（新短码）、
  `test_ticket_discovery.py`（`load_stage_refs` 映射与 board-only 不产生 ref）。

**实测（A′ app 直接指向真 Track `F:\workspace\seckill-lab`）：**

```
declared stage refs: {'db-stage-01': 'stage-01-product-query-index'}
provision: 500 {'detail': 'workspace_ref_missing',
                'ref': 'origin/stage-01-product-query-index',
                'available': ['main', 'origin', 'origin/main', 'origin/solutions']}
```

失败信息现在**自解释**：平台要的是阶段分支 `origin/stage-01-product-query-index`，
而仓库里只有 `main` 与 `solutions`——一眼就能看出 Track 缺 per-stage 分支。
（此前它要的是 `origin/db-stage-01`，那本身就是错的 ref。）

**仍待 Track 侧完成（不在本计划范围）：** 给 `seckill-lab` 补出各阶段的骨架分支与**每阶段
独立的**解法 ref（取代单一 `solutions` 分支），并把 `manifest.stages` 与
`stages/<name>/`、分支名三者对齐。完成后 provision/diff/submit/review/decide 在真 Track
上即可整条跑通——内容/身份/DAG 这条线已经验证过了。

> Plan 2（前端）"工作区/进度"与"diff 预览"两屏依赖这条契约，现在它已经冻结：
> `base_ref` = 阶段分支、`head_ref` = 学员 dev ref、缺 ref 报 `workspace_ref_missing`。
