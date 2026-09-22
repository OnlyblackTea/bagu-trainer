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
* 已知债务：无（v0.5 起 ``grade()`` 收 ``track``，可按 ``runtime.build`` 派发，也能重置
  ``runtime.compose``）。
* **先重置基础设施，再跑测试**：验收测试用 ``@Sql`` 跑迁移脚本，而"跑过一次参考解的迁移"会把
  索引留在容器里——此后骨架只把工作区文件改回去、数据库不动，测试**照样绿**，是一个零成本的
  绕过（§7.3 的注入没实现时尤其致命）。所以默认每次阅卷前按 Track 的 ``runtime.compose`` 做
  ``down -v`` + ``up -d --wait``；用 ``BAGU_GRADING_RESET=never`` 可关掉——**关掉等于把上面那条
  绕过又放回来**，只在你确实想保留库内状态时用。
* 构建工具按 ``manifest.requires.build`` 派发：当前只实现 maven，别的**显式报错**而不是猜。
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from platform_service.grading import GradingStep, ProgressReporter, report_progress
from platform_service.models import (
    GradingReport,
    ReportCorrectness,
    ReportProvenance,
    TicketInstance,
    TicketSpec,
    TrackManifest,
)

__all__ = ["DockerGradingRunner", "GradingUnavailableError"]

#: 报告里的 ``generated_by``。与桩的 ``stub-grading/a-prime`` 同一命名习惯，便于一眼区分真假。
_RUNNER_ID = "docker-grading/a-prime"

_DEFAULT_TIMEOUT_S = 900.0
_DEFAULT_INFRA_TIMEOUT_S = 300.0
_MAX_DETAIL = 400


def _rel(ref: str) -> str:
    """把 Track 里写的相对路径（``./docker/x.yml``）变成干净的相对路径。"""
    return ref.strip().replace("\\", "/").removeprefix("./")


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


def _revision(workspace: Path) -> str | None:
    """阅卷对着的那一版（``HEAD`` 的 SHA）。

    写进报告的 ``host_env`` 是刻意的：代码可以写在别的机器上、由 ``sync`` 拉进来，也可能**没能**
    拉进来（本地有未提交改动、两边分叉）。那时报告里留着"我看到的是哪个 SHA"，学员才能自己判断
    这次结论算不算数，而不是靠猜。
    """
    out = _git(workspace, "rev-parse", "HEAD")
    return (out or "").strip() or None


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

    def __init__(
        self,
        *,
        mvn: str = "mvn",
        timeout_s: float = _DEFAULT_TIMEOUT_S,
        infra_timeout_s: float = _DEFAULT_INFRA_TIMEOUT_S,
        reset_infra: bool = True,
    ) -> None:
        self._mvn = mvn
        self._timeout_s = timeout_s
        self._infra_timeout_s = infra_timeout_s
        #: 默认重置：不重置就无法保证"骨架红"是真的红（见模块 docstring）。
        self._reset_infra = reset_infra

    # -- 可覆盖的执行钩子（测试从这一层注入假 maven / 假 compose，不必真装 JDK）--------

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

    # -- 基础设施（§15 风险 6 的关闭手段）-----------------------------------------

    def _prepare_infra(self, track: TrackManifest, workspace: Path) -> dict[str, Any]:
        """把 Track 声明的容器基础设施重置到干净状态，并返回审计信息。

        为什么必须做：验收测试的 ``@Sql`` 会跑迁移脚本，而数据库是**长活**的。一旦有人在同一个
        库里跑过参考解的迁移（比如瞄了一眼答案），索引就留在那儿了；此后骨架只要把工作区文件
        改回去、一个字节的代码都不写，测试仍然绿。这不是"卫生问题"，是一个零成本的绕过。
        """
        compose_ref = track.runtime.compose
        if not compose_ref:
            return {
                "compose": None,
                "reset": False,
                "reason": "track declares no runtime.compose",
            }
        compose = workspace / _rel(compose_ref)
        if not compose.is_file():
            raise GradingUnavailableError(
                f"track declares runtime.compose={compose_ref!r} but the workspace has no such "
                f"file ({compose}) — refusing to grade without a resettable environment"
            )
        if not self._reset_infra:
            return {
                "compose": str(compose),
                "reset": False,
                "reason": "reset disabled (BAGU_GRADING_RESET=never)",
            }
        if shutil.which("docker") is None:
            raise GradingUnavailableError(
                "docker not found on PATH — this track declares container infrastructure"
            )
        self._compose(compose, "down", "-v", cwd=workspace)
        self._compose(compose, "up", "-d", "--wait", cwd=workspace)
        return {"compose": str(compose), "reset": True}

    def _compose(self, compose: Path, *args: str, cwd: Path) -> None:
        command = ["docker", "compose", "-f", str(compose), *args]
        try:
            proc = subprocess.run(
                command,
                cwd=cwd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self._infra_timeout_s,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise GradingUnavailableError(f"{' '.join(command)} failed: {exc}") from exc
        if proc.returncode != 0:
            blob = f"{proc.stderr}\n{proc.stdout}".strip()
            tail = " / ".join(
                line.strip() for line in blob.splitlines()[-3:] if line.strip()
            )
            raise GradingUnavailableError(
                f"{' '.join(command)} exited {proc.returncode}: {tail[:300]}"
            )

    # -- 命令构造 ---------------------------------------------------------------

    def _test_command(self, track: TrackManifest, ticket: TicketSpec) -> list[str]:
        group = ticket.grader.correctness.test_group
        if not group:
            raise GradingUnavailableError("ticket declares no grader.correctness.test_group")
        build = (track.manifest.requires or {}).get("build")
        if build not in (None, "maven"):
            raise GradingUnavailableError(
                f"track declares requires.build={build!r}; this runner only implements maven"
            )
        return [self._mvn, "-B", f"-Dgroups={group}", "test"]

    # -- public API（GradingRunner 缝） --------------------------------------

    def grade(
        self,
        *,
        instance: TicketInstance,
        ticket: TicketSpec,
        track: TrackManifest,
        workspace: Path,
        on_progress: ProgressReporter | None = None,
    ) -> GradingReport:
        workspace = Path(workspace)
        if not workspace.is_dir():
            raise GradingUnavailableError(f"workspace does not exist: {workspace}")
        if shutil.which(self._mvn) is None:
            raise GradingUnavailableError(
                f"maven not found on PATH ({self._mvn!r}) — grading runs on the learner's "
                "machine (D3); install JDK17 + Maven there, or keep using the stub"
            )
        command = self._test_command(track, ticket)
        # 先校验（省得为一个配置错误白等一次容器重启），再重置环境，最后才跑测试。
        # 三个阶段各自上报：真阅卷是几十秒的事，学员有权知道现在卡在哪一段（§C.3 步骤 6）。
        report_progress(
            on_progress,
            GradingStep.RESET_INFRA,
            (
                f"重置 Track 基础设施（{track.runtime.compose}）"
                if self._reset_infra and track.runtime.compose
                else "跳过环境重置"
            ),
        )
        infra = self._prepare_infra(track, workspace)
        group = ticket.grader.correctness.test_group
        report_progress(on_progress, GradingStep.RUN_TESTS, " ".join(command))
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

        report_progress(on_progress, GradingStep.PARSE, "读 surefire 报告与 git diff")
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
                    # 阅卷对着哪一版：sync 把别处 push 上来的代码拉进来了没有，看这个字段。
                    "revision": _revision(workspace),
                    "branch": (_git(workspace, "rev-parse", "--abbrev-ref", "HEAD") or "").strip()
                    or None,
                    "command": " ".join(command),
                    "exit_code": exit_code,
                    "duration_s": round(duration_s, 3),
                    "java": _tool_version(["java", "-version"]),
                    "maven": _tool_version([self._mvn, "-v"]),
                    "os": platform.platform(),
                    # 这次阅卷是不是在**重置过**的环境里跑的，必须可审计（§15 风险 6）。
                    "infra": infra,
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
