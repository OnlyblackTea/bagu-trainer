"""真实阅卷后端：在学员自己的机器上跑 Track 的 canonical 测试（设计 D3 / §7）。

本模块是 ``GradingRunner`` 缝的**真实实现**，替换 ``StubGradingRunner``：把
``mvn -B -Dgroups=<test_group> test`` 真跑起来，再把 surefire 的**真实**结果映射成
:class:`~platform_service.models.GradingReport`。它属于**适配层**（``platform_api/``），
因此 ``platform_service/`` 仍是纯的（无 subprocess、无 docker）。

为什么是"本机 subprocess"而不是远程调用：§7.1 说阅卷跑在学员设备上；all-in-one 部署
（§2.3）时"学员设备"就是部署平台的那台机器。远程执行是部署形态问题，不该写进这条缝。

诚实边界（写在这里，免得日后误读）：

* **只跑 canonical 测试**。ticket 另外声明的 ``grader.performance`` / ``concurrency``
  本模块**不会**单独跑，报告里如实标 ``executed_separately: False``。（真 Track 把 EXPLAIN
  断言写进了 canonical 测试内部，那部分确实被跑到；但那是 Track 的选择，不是本模块的保证。）
* **环境故障 ≠ 学员失败**：缺 ``mvn``、工作区不存在、ticket 没声明 ``test_group`` → 抛
  :class:`GradingUnavailableError`（平台配置问题，路由应报 5xx）。测试失败 / 报错 / 超时 →
  正常返回报告（``passed=False``），并在 ``correctness.tests`` 里留下**是哪一条、为什么**。
* ``runner_trust`` 恒为 ``self``：报告是**不可信自报告**（§7.4），平台不假装它是可信计算。
* 已知债务：``grade()`` 的形参里没有 Track，所以无法按 ``runtime.build`` 派发，当前硬编码
  maven。要支持别的构建工具得改 Protocol 或把命令写进 ticket（设计 §3.1 已记）。
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from platform_service.models import (
    GradingReport,
    ReportCorrectness,
    ReportProvenance,
    TicketInstance,
    TicketSpec,
)

__all__ = ["DockerGradingRunner", "GradingUnavailableError"]

#: 报告里的 ``generated_by``。与桩的 ``stub-grading/a-prime`` 同一命名习惯，便于一眼区分真假。
_RUNNER_ID = "docker-grading/a-prime"

_DEFAULT_TIMEOUT_S = 900.0
_MAX_DETAIL = 400


class GradingUnavailableError(RuntimeError):
    """阅卷**跑不起来**（平台/环境问题），而不是学员的代码没通过。

    路由层应映射成 5xx + 短码；把它伪装成 ``passed=False`` 会把平台故障栽给学员。
    """


def _first_error_line(text: str) -> str:
    """从 maven 输出里挑一行能解释失败原因的话（编译错误最常见，而它不出现在 surefire 报告里）。"""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[ERROR]") and len(stripped) > len("[ERROR]"):
            return stripped[: _MAX_DETAIL]
    tail = [line.strip() for line in text.splitlines() if line.strip()]
    return tail[-1][:_MAX_DETAIL] if tail else "no output"


def _tool_version(command: list[str]) -> str | None:
    """跑一个 ``--version`` 风格命令拿首行；``java -version`` 写在 stderr，故两个流都收。"""
    if shutil.which(command[0]) is None:
        return None
    try:
        proc = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30
        )
    except (OSError, subprocess.SubprocessError):
        return None
    blob = f"{proc.stdout}\n{proc.stderr}".strip()
    first = next((line.strip() for line in blob.splitlines() if line.strip()), None)
    return first[:200] if first else None


def _surefire_results(reports_dir: Path) -> list[dict[str, Any]]:
    """把 ``target/surefire-reports/TEST-*.xml`` 读成 ``[{"name","status","detail"?}]``。

    真结果取自 surefire 的 XML，而不是 grep maven 控制台输出——后者格式随版本变，且分不清
    ``failure``（断言失败）与 ``error``（异常/绑定错误），而这两者对学员的意义不同。
    """
    results: list[dict[str, Any]] = []
    if not reports_dir.is_dir():
        return results
    for report in sorted(reports_dir.glob("TEST-*.xml")):
        try:
            suite = ET.parse(report).getroot()
        except ET.ParseError:
            continue
        for case in suite.iter("testcase"):
            classname = case.get("classname") or ""
            name = case.get("name") or "?"
            entry: dict[str, Any] = {"name": f"{classname}#{name}" if classname else name}
            for kind, status in (("failure", "fail"), ("error", "error"), ("skipped", "skip")):
                node = case.find(kind)
                if node is not None:
                    message = (node.get("message") or "").strip()
                    entry["status"] = status
                    if message:
                        entry["detail"] = message[: _MAX_DETAIL]
                    break
            else:
                entry["status"] = "pass"
            results.append(entry)
    return results


def _parse_name_only(text: str) -> set[str]:
    """``git diff --name-only`` 的输出：一行一个路径，无前缀。"""
    return {line.strip().replace("\\", "/") for line in text.splitlines() if line.strip()}


def _parse_porcelain(text: str) -> set[str]:
    """``git status --porcelain`` v1：``XY<space>PATH``，改名是 ``old -> new``。"""
    names: set[str] = set()
    for raw in text.splitlines():
        if not raw.strip():
            continue
        candidate = raw[3:] if len(raw) > 3 else raw
        if " -> " in candidate:
            candidate = candidate.split(" -> ", 1)[1]
        if candidate.strip():
            names.add(candidate.strip().replace("\\", "/"))
    return names


def _git(workspace: Path, *args: str) -> str | None:
    """在 workspace 里跑 git；失败返回 ``None``（"跑不了"与"没有改动"必须分得开）。"""
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=workspace,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout if proc.returncode == 0 else None


def _changed_files(workspace: Path) -> list[str]:
    """学员在工作区里改了哪些文件（相对仓库根、正斜杠）。

    优先按 provision 时记下的基线 ref 比（``git config bagu.baseRef``）——那样**已提交**的改动
    也算数。**记的必须是一个解析好的 ref/SHA，不能是 ``HEAD``**：``HEAD`` 会随学员的提交一起
    前移，等于永远比不出东西来（这个坑测试里踩过，见 ``test_recorded_base_ref_...``）。

    没记基线、或记的 ref 在当前仓库里解析不了（克隆换过、分支被删），就退回工作树相对 ``HEAD``
    的改动 + 未跟踪文件。两条路都拿不到证据时返回空列表。
    """
    base = _git(workspace, "config", "--get", "bagu.baseRef")
    if base and base.strip():
        out = _git(workspace, "diff", "--name-only", base.strip())
        if out is not None:
            return sorted(_parse_name_only(out))
    names = _parse_name_only(_git(workspace, "diff", "--name-only", "HEAD") or "")
    names |= _parse_porcelain(_git(workspace, "status", "--porcelain") or "")
    return sorted(names)


class DockerGradingRunner:
    """在 workspace 里真跑 canonical 测试，产出带**真实 provenance** 的 GradingReport。

    名字里的 "Docker" 指这类 Track 的运行时前提（compose 起 MySQL/Redis，再由 maven 跑集成
    测试）；本类自己不调 docker——容器由 Track 的 ``runtime.compose`` 负责（§7.1 "`paper up`
    是学员的活"），本类只负责**跑测试并如实记录**。
    """

    def __init__(self, *, mvn: str = "mvn", timeout_s: float = _DEFAULT_TIMEOUT_S) -> None:
        self._mvn = mvn
        self._timeout_s = timeout_s

    # -- 可覆盖的执行钩子（测试从这一层注入假 maven，不必真装 JDK）----------------

    def _run(self, command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=self._timeout_s,
        )

    # -- public API（GradingRunner 缝） --------------------------------------

    def grade(
        self, *, instance: TicketInstance, ticket: TicketSpec, workspace: Path
    ) -> GradingReport:
        workspace = Path(workspace)
        if not workspace.is_dir():
            raise GradingUnavailableError(f"workspace does not exist: {workspace}")
        if shutil.which(self._mvn) is None:
            raise GradingUnavailableError(
                f"maven not found on PATH ({self._mvn!r}) — grading runs on the learner's "
                "machine (D3); install JDK17 + Maven there, or keep using the stub"
            )
        group = ticket.grader.correctness.test_group
        if not group:
            raise GradingUnavailableError("ticket declares no grader.correctness.test_group")

        command = [self._mvn, "-B", f"-Dgroups={group}", "test"]
        started = datetime.now(UTC)
        timed_out = False
        try:
            proc = self._run(command, cwd=workspace)
            stdout, stderr, exit_code = proc.stdout, proc.stderr, proc.returncode
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            raw = exc.stdout or b""
            stdout = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
            stderr = f"timed out after {self._timeout_s:.0f}s"
            exit_code = None
        duration_s = (datetime.now(UTC) - started).total_seconds()

        results = _surefire_results(workspace / "target" / "surefire-reports")
        if not results:
            # 没有 surefire 报告 = 根本没跑到测试（编译失败、依赖拉不下来、超时……）。
            # 这条必须留下，否则学员只看到一个没头没脑的 passed=False。
            results = [
                {
                    "name": " ".join(command),
                    "status": "error",
                    "detail": (
                        stderr.strip()[:_MAX_DETAIL]
                        if timed_out
                        else _first_error_line(stdout or stderr)
                    ),
                }
            ]
        passed = exit_code == 0 and all(r["status"] == "pass" for r in results)

        changed = _changed_files(workspace)
        in_scope = {p.replace("\\", "/") for p in ticket.scope.files_to_change}

        return GradingReport(
            submission_id="pending",  # service.submit 会重新键到铸造的 submission id
            ticket=instance.ticket_id,
            ticket_version=instance.ticket_version,
            test_version=instance.test_version,
            engine_version=instance.engine_version,
            learner=instance.learner,
            provenance=ReportProvenance(
                generated_at=started.isoformat(),
                generated_by=_RUNNER_ID,
                host_env={
                    "simulated": False,
                    "cwd": str(workspace),
                    "command": " ".join(command),
                    "exit_code": exit_code,
                    "duration_s": round(duration_s, 3),
                    "java": _tool_version(["java", "-version"]),
                    "maven": _tool_version([self._mvn, "-v"]),
                    "os": platform.platform(),
                },
            ),
            correctness=ReportCorrectness(test_group=group, passed=passed, tests=results),
            performance=self._performance_field(ticket),
            files_changed=changed,
            out_of_scope_edits=sorted(set(changed) - in_scope),
        )

    @staticmethod
    def _performance_field(ticket: TicketSpec) -> dict[str, Any] | None:
        """如实说明"声明了但没单独跑"，而不是留 ``None`` 让人误以为 Track 没要求。"""
        declared = ticket.grader.performance
        if not declared:
            return None
        return {
            "declared": declared,
            "executed_separately": False,
            "note": (
                "this runner executes the canonical test only; a Track that wants a "
                "standalone EXPLAIN/sim grade must declare it as its own runtime entry"
            ),
        }
