"""platform_api.grading_docker 的单元测试——真实阅卷后端的**映射**与**诚实性**。

真结果由 subprocess 产出，所以这里把"跑 maven"那一层换成假实现（覆写 ``_run`` 钩子），
专测容易骗人的部分：

* surefire XML → ``correctness.tests``（断言失败 ``fail`` 与异常 ``error`` 分开）；
* 工区改动 → ``files_changed`` / ``out_of_scope_edits``（越界告警的依据，§11.2）；
* **环境故障 → 抛错**，而不是栽给学员一个 ``passed=False``；
* 编译失败 / 超时 → 老实留痕（报告里能看出"没跑到测试"而不是"测试没过"）。

末尾一个 ``@skipif`` 真跑冒烟：没装 maven 的机器跳过，装了（例如那台 VM）就真跑一次。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from platform_api import grading_docker
from platform_api.grading_docker import DockerGradingRunner, GradingUnavailableError
from platform_service.grading import GradingRunner
from platform_service.models import (
    InstanceState,
    TicketInstance,
    TicketSpec,
    TrackManifest,
)

_SUREFIRE_ALL_PASS = """<?xml version="1.0" encoding="UTF-8"?>
<testsuite name="com.devteam.seckill.stage01.ProductQueryIndexAcceptanceTest"
           tests="2" failures="0" errors="0" skipped="0">
  <testcase name="searchReturnsCorrectOrderedResults" classname="A" time="1.1"/>
  <testcase name="explainUsesIndex" classname="A" time="0.9"/>
</testsuite>
"""

_SUREFIRE_MIXED = """<?xml version="1.0" encoding="UTF-8"?>
<testsuite name="A" tests="3" failures="1" errors="1" skipped="1">
  <testcase name="asserted" classname="A" time="0.2">
    <failure message="EXPLAIN 的 key 为 NULL，说明未命中任何索引">trace</failure>
  </testcase>
  <testcase name="blewup" classname="A" time="0.3">
    <error message="Invalid bound statement (not found): searchProducts">trace</error>
  </testcase>
  <testcase name="skipped" classname="A" time="0.0"><skipped message="no db"/></testcase>
</testsuite>
"""


@pytest.fixture(autouse=True)
def _fake_which(monkeypatch: pytest.MonkeyPatch) -> None:
    """默认假装 maven 存在；测试要验"缺 maven"时再覆盖一次。"""
    monkeypatch.setattr(grading_docker.shutil, "which", lambda name: f"/fake/{name}")


class _ScriptedRunner(DockerGradingRunner):
    """把 maven / compose 执行脚本化；解析、物证、报告装配全部走真实代码路径。"""

    def __init__(
        self,
        *,
        proc: Any = None,
        raises: Exception | None = None,
        reset_infra: bool = True,
        compose_raises: Exception | None = None,
    ) -> None:
        super().__init__(mvn="mvn", reset_infra=reset_infra)
        self._proc = proc
        self._raises = raises
        self._compose_raises = compose_raises
        self.calls: list[list[str]] = []
        #: 按发生顺序记录两件事，用来断言"先重置环境，再跑测试"。
        self.events: list[str] = []

    def _run(self, command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(command))
        self.events.append("test")
        if self._raises is not None:
            raise self._raises
        return self._proc

    def _compose(self, compose: Path, *args: str, cwd: Path) -> None:
        self.events.append(f"compose {' '.join(args)}")
        if self._compose_raises is not None:
            raise self._compose_raises


def _proc(returncode: int = 0, stdout: str = "", stderr: str = "") -> Any:
    return subprocess.CompletedProcess(
        args=["mvn"], returncode=returncode, stdout=stdout, stderr=stderr
    )


def _ticket(ticket_factory: Any, **kwargs: Any) -> TicketSpec:
    return TicketSpec.model_validate(ticket_factory(**kwargs))


def _track(*, compose: str | None = None, build: str | None = "maven") -> TrackManifest:
    """最小合法 Track（判定面 + 可选 compose / 构建工具），喂给 ``grade()``。

    ``grade()`` 现在收 track，因为阅卷需要两样只有 Track 才知道的东西：用哪个 compose 把环境
    重置干净（否则"跑过一次参考解迁移"会永久变绿），以及按哪种构建工具派发命令。
    """
    requires: dict[str, Any] = {"java": "17"}
    if build is not None:
        requires["build"] = build
    runtime: dict[str, Any] = {
        "tests": "./tests/",
        "digest_pin": {"tests": "sha256:x"},
        "protected_paths": ["./src/test/"],
    }
    if compose is not None:
        runtime["compose"] = compose
    return TrackManifest.model_validate(
        {
            "id": "database-bagu",
            "name": "数据库八股实战",
            "trust_level": "self",
            "manifest": {
                "requires": requires,
                "skeleton_repo": "./skeleton",
                "stages": ["stage-01-product-query-index"],
            },
            "runtime": runtime,
        }
    )


def _instance(ticket: TicketSpec) -> TicketInstance:
    return TicketInstance(
        id="ti-1",
        learner="deployer",
        track="database-bagu",
        ticket_id=ticket.identity.id,
        ticket_version=ticket.identity.ticket_version,
        engine_version="a-prime-0.1.0",
        test_version=ticket.grader.correctness.test_version,
        state=InstanceState.IN_PROGRESS,
    )


def _write_surefire(workspace: Path, xml: str) -> None:
    reports = workspace / "target" / "surefire-reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "TEST-A.xml").write_text(xml, encoding="utf-8")


def _git(root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return proc.stdout


def _init_repo(root: Path, files: dict[str, str]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "tester")
    for name, body in files.items():
        (root / name).write_text(body, encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "init")


# --- 缝 + 环境故障 --------------------------------------------------------------


def test_structurally_satisfies_protocol() -> None:
    assert isinstance(DockerGradingRunner(), GradingRunner)


def test_missing_workspace_is_unavailable_not_failed(tmp_path: Path, ticket_factory: Any) -> None:
    ticket = _ticket(ticket_factory)
    with pytest.raises(GradingUnavailableError, match="workspace does not exist"):
        _ScriptedRunner(proc=_proc()).grade(
            instance=_instance(ticket), ticket=ticket, track=_track(), workspace=tmp_path / "nope"
        )


def test_missing_maven_is_unavailable(
    tmp_path: Path, ticket_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(grading_docker.shutil, "which", lambda name: None)
    ticket = _ticket(ticket_factory)
    with pytest.raises(GradingUnavailableError, match="maven not found"):
        DockerGradingRunner().grade(
            instance=_instance(ticket), ticket=ticket, track=_track(), workspace=tmp_path
        )


def test_missing_test_group_is_unavailable(tmp_path: Path, ticket_factory: Any) -> None:
    raw = ticket_factory()
    raw["grader"]["correctness"]["test_group"] = ""
    ticket = TicketSpec.model_validate(raw)
    with pytest.raises(GradingUnavailableError, match="test_group"):
        _ScriptedRunner(proc=_proc()).grade(
            instance=_instance(ticket), ticket=ticket, track=_track(), workspace=tmp_path
        )


# --- 真结果映射 -----------------------------------------------------------------


def test_green_suite_reports_real_provenance(tmp_path: Path, ticket_factory: Any) -> None:
    ticket = _ticket(ticket_factory)
    runner = _ScriptedRunner(proc=_proc(returncode=0))
    _write_surefire(tmp_path, _SUREFIRE_ALL_PASS)

    report = runner.grade(
        instance=_instance(ticket), ticket=ticket, track=_track(), workspace=tmp_path
    )

    assert report.correctness.passed is True
    assert report.correctness.test_group == "stage01"
    assert [t["status"] for t in report.correctness.tests] == ["pass", "pass"]
    # 命令真的按 ticket 的 test_group 构造，而不是写死
    assert runner.calls == [["mvn", "-B", "-Dgroups=stage01", "test"]]
    assert report.provenance.generated_by == "docker-grading/a-prime"
    assert report.provenance.host_env["simulated"] is False
    assert report.provenance.host_env["exit_code"] == 0
    assert report.provenance.host_env["command"] == "mvn -B -Dgroups=stage01 test"
    assert report.provenance.runner_trust.value == "self"
    assert report.submission_id == "pending"  # service.submit 负责重新键


def test_assertion_failure_and_binding_error_are_distinguished(
    tmp_path: Path, ticket_factory: Any
) -> None:
    """``fail``（断言没过）与 ``error``（异常/绑定错误）对学员意义不同，不能混成一个。"""
    ticket = _ticket(ticket_factory)
    _write_surefire(tmp_path, _SUREFIRE_MIXED)

    report = _ScriptedRunner(proc=_proc(returncode=1)).grade(
        instance=_instance(ticket), ticket=ticket, track=_track(), workspace=tmp_path
    )

    assert report.correctness.passed is False
    by_name = {t["name"]: t for t in report.correctness.tests}
    assert by_name["A#asserted"]["status"] == "fail"
    assert "未命中任何索引" in by_name["A#asserted"]["detail"]
    assert by_name["A#blewup"]["status"] == "error"
    assert "Invalid bound statement" in by_name["A#blewup"]["detail"]
    assert by_name["A#skipped"]["status"] == "skip"


def test_compile_failure_without_reports_records_the_error_line(
    tmp_path: Path, ticket_factory: Any
) -> None:
    """没跑到测试（编译失败）也必须留下可读原因，否则学员只看到没头没脑的红。"""
    ticket = _ticket(ticket_factory)
    out = "[INFO] scanning\n[ERROR] COMPILATION ERROR : \n[ERROR] /x/Foo.java:[3,1] ';' expected\n"
    report = _ScriptedRunner(proc=_proc(returncode=1, stdout=out)).grade(
        instance=_instance(ticket), ticket=ticket, track=_track(), workspace=tmp_path
    )

    assert report.correctness.passed is False
    assert len(report.correctness.tests) == 1
    entry = report.correctness.tests[0]
    assert entry["status"] == "error"
    assert "COMPILATION ERROR" in entry["detail"]
    assert report.provenance.host_env["exit_code"] == 1


def test_timeout_is_reported_not_raised(tmp_path: Path, ticket_factory: Any) -> None:
    ticket = _ticket(ticket_factory)
    exc = subprocess.TimeoutExpired(cmd=["mvn"], timeout=900)

    report = _ScriptedRunner(raises=exc, ).grade(
        instance=_instance(ticket), ticket=ticket, track=_track(), workspace=tmp_path
    )

    assert report.correctness.passed is False
    assert report.provenance.host_env["exit_code"] is None
    assert "timed out" in report.correctness.tests[0]["detail"]


# --- 物证：改了哪些文件 / 越界 ---------------------------------------------------


def test_working_tree_changes_are_flagged_with_out_of_scope(
    tmp_path: Path, ticket_factory: Any
) -> None:
    repo = tmp_path / "ws"
    _init_repo(repo, {"in_scope.xml": "v1", "unrelated.txt": "v1"})
    (repo / "in_scope.xml").write_text("v2", encoding="utf-8")
    (repo / "out_of_scope.txt").write_text("new", encoding="utf-8")

    ticket = _ticket(ticket_factory, files_to_change=["in_scope.xml"])
    report = _ScriptedRunner(proc=_proc(returncode=0)).grade(
        instance=_instance(ticket), ticket=ticket, track=_track(), workspace=repo
    )

    assert report.files_changed == ["in_scope.xml", "out_of_scope.txt"]
    assert report.out_of_scope_edits == ["out_of_scope.txt"]


def test_recorded_base_ref_also_catches_committed_changes(
    tmp_path: Path, ticket_factory: Any
) -> None:
    """学员把改动 commit 掉之后，工作树是干净的——按 provision 记下的 baseline 比才看得见。"""
    repo = tmp_path / "ws"
    _init_repo(repo, {"in_scope.xml": "v1"})
    # 基线必须是**解析好的 SHA**：记 "HEAD" 会随学员的提交一起前移，永远比不出东西。
    _git(repo, "config", "bagu.baseRef", _git(repo, "rev-parse", "HEAD").strip())
    (repo / "in_scope.xml").write_text("v2", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "learner work")

    ticket = _ticket(ticket_factory, files_to_change=["in_scope.xml"])
    report = _ScriptedRunner(proc=_proc(returncode=0)).grade(
        instance=_instance(ticket), ticket=ticket, track=_track(), workspace=repo
    )

    assert report.files_changed == ["in_scope.xml"]
    assert report.out_of_scope_edits == []


def test_unresolvable_base_ref_falls_back_to_working_tree(
    tmp_path: Path, ticket_factory: Any
) -> None:
    """记的 ref 在当前仓库里解析不了（克隆换过 / 分支被删）时，不能假装"没有任何改动"。"""
    repo = tmp_path / "ws"
    _init_repo(repo, {"in_scope.xml": "v1"})
    _git(repo, "config", "bagu.baseRef", "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef")
    (repo / "in_scope.xml").write_text("v2", encoding="utf-8")

    ticket = _ticket(ticket_factory, files_to_change=["in_scope.xml"])
    report = _ScriptedRunner(proc=_proc(returncode=0)).grade(
        instance=_instance(ticket), ticket=ticket, track=_track(), workspace=repo
    )

    assert report.files_changed == ["in_scope.xml"]


# --- 声明的 performance 不被冒充为"已跑" ----------------------------------------


def test_declared_performance_is_marked_not_separately_executed(
    tmp_path: Path, ticket_factory: Any
) -> None:
    ticket = _ticket(ticket_factory)  # conftest 的 ticket 声明了 explain 断言
    assert ticket.grader.performance is not None

    report = _ScriptedRunner(proc=_proc(returncode=0)).grade(
        instance=_instance(ticket), ticket=ticket, track=_track(), workspace=tmp_path
    )

    assert report.performance is not None
    assert report.performance["executed_separately"] is False
    assert report.performance["declared"] == ticket.grader.performance


def test_absent_performance_stays_none(tmp_path: Path, ticket_factory: Any) -> None:
    raw = ticket_factory()
    raw["grader"]["performance"] = None
    ticket = TicketSpec.model_validate(raw)

    report = _ScriptedRunner(proc=_proc(returncode=0)).grade(
        instance=_instance(ticket), ticket=ticket, track=_track(), workspace=tmp_path
    )

    assert report.performance is None


# --- 基础设施重置（§15 风险 6）：不重置，"骨架红"可能是假的 ----------------------


def _with_compose(workspace: Path, ref: str = "./docker/docker-compose.yml") -> Path:
    compose = workspace / ref.removeprefix("./")
    compose.parent.mkdir(parents=True, exist_ok=True)
    compose.write_text("services: {}\n", encoding="utf-8")
    return compose


def test_declared_compose_is_reset_before_the_tests_run(
    tmp_path: Path, ticket_factory: Any
) -> None:
    """**先 down -v 再 up，最后才跑测试。**

    为什么必须重置：验收测试的 ``@Sql`` 跑在长活数据库上。一旦有人在同一个库里跑过参考解的
    迁移（比如瞄了一眼答案），索引就留在那儿了；此后骨架一个字节不写也能绿——零成本绕过。
    """
    ticket = _ticket(ticket_factory)
    _with_compose(tmp_path)
    runner = _ScriptedRunner(proc=_proc(returncode=0))

    report = runner.grade(
        instance=_instance(ticket),
        ticket=ticket,
        track=_track(compose="./docker/docker-compose.yml"),
        workspace=tmp_path,
    )

    assert runner.events == ["compose down -v", "compose up -d --wait", "test"]
    assert report.provenance.host_env["infra"]["reset"] is True


def test_no_declared_compose_means_no_reset(tmp_path: Path, ticket_factory: Any) -> None:
    """Track 不声明基础设施（例如纯内存的题）就不该被强行 compose，但要**如实记录**。"""
    ticket = _ticket(ticket_factory)
    runner = _ScriptedRunner(proc=_proc(returncode=0))

    report = runner.grade(
        instance=_instance(ticket), ticket=ticket, track=_track(), workspace=tmp_path
    )

    assert runner.events == ["test"]
    infra = report.provenance.host_env["infra"]
    assert infra["reset"] is False and infra["compose"] is None


def test_reset_can_be_disabled_but_is_recorded_as_such(
    tmp_path: Path, ticket_factory: Any
) -> None:
    """``never`` 关掉重置时，报告必须留下"这次没重置"的痕迹——那时绿要打折看。"""
    ticket = _ticket(ticket_factory)
    _with_compose(tmp_path)
    runner = _ScriptedRunner(proc=_proc(returncode=0), reset_infra=False)

    report = runner.grade(
        instance=_instance(ticket),
        ticket=ticket,
        track=_track(compose="./docker/docker-compose.yml"),
        workspace=tmp_path,
    )

    assert runner.events == ["test"]
    infra = report.provenance.host_env["infra"]
    assert infra["reset"] is False and "disabled" in infra["reason"]


def test_declared_compose_that_is_missing_is_unavailable(
    tmp_path: Path, ticket_factory: Any
) -> None:
    """声明了 compose 却在工作区里找不到：拒绝阅卷，而不是"没重置也照样跑"。"""
    ticket = _ticket(ticket_factory)
    runner = _ScriptedRunner(proc=_proc(returncode=0))

    with pytest.raises(GradingUnavailableError, match="refusing to grade"):
        runner.grade(
            instance=_instance(ticket),
            ticket=ticket,
            track=_track(compose="./docker/docker-compose.yml"),
            workspace=tmp_path,
        )


def test_compose_failure_is_unavailable_not_a_verdict(
    tmp_path: Path, ticket_factory: Any
) -> None:
    """环境起不来 ⇒ 503 那一类，**不是**学员的代码没过。"""
    ticket = _ticket(ticket_factory)
    _with_compose(tmp_path)
    runner = _ScriptedRunner(
        proc=_proc(returncode=0),
        compose_raises=GradingUnavailableError("docker compose up -d --wait exited 1"),
    )

    with pytest.raises(GradingUnavailableError):
        runner.grade(
            instance=_instance(ticket),
            ticket=ticket,
            track=_track(compose="./docker/docker-compose.yml"),
            workspace=tmp_path,
        )


def test_compose_nonzero_exit_reports_the_tail(monkeypatch: Any, tmp_path: Path) -> None:
    """真 ``_compose`` 的非零退出要带上有用的尾巴，而不是一个光秃秃的 returncode。"""
    compose = _with_compose(tmp_path)

    def fake_run(command: list[str], **kwargs: Any) -> Any:
        return subprocess.CompletedProcess(
            args=command, returncode=125, stdout="", stderr="no such service: mysql\nboom"
        )

    monkeypatch.setattr(grading_docker.subprocess, "run", fake_run)
    with pytest.raises(GradingUnavailableError, match="boom"):
        DockerGradingRunner()._compose(compose, "up", "-d", cwd=tmp_path)


def test_unknown_build_tool_is_unavailable(tmp_path: Path, ticket_factory: Any) -> None:
    """构建工具按 Track 声明派发；不认识的**显式报错**，不猜。"""
    ticket = _ticket(ticket_factory)
    with pytest.raises(GradingUnavailableError, match="gradle"):
        DockerGradingRunner().grade(
            instance=_instance(ticket),
            ticket=ticket,
            track=_track(build="gradle"),
            workspace=tmp_path,
        )


# --- 真跑冒烟（有 maven 的机器才跑，例如那台 VM）--------------------------------

@pytest.mark.skipif(shutil.which("mvn") is None, reason="needs a real JDK17 + Maven")
def test_real_maven_on_a_non_project_reports_failure_honestly(tmp_path: Path) -> None:
    """空目录没有 pom.xml：应当**如实**报一条 error，而不是崩掉或假装通过。"""
    ticket = TicketSpec.model_validate(
        {
            "identity": {
                "id": "db-stage-01",
                "schema_version": 1,
                "ticket_version": 1,
                "stage": 1,
                "title": "t",
            },
            "brief": {"problem": "p", "concepts": []},
            "scope": {"files_to_change": ["a.txt"]},
            "grader": {
                "correctness": {
                    "test_group": "stage01",
                    "test_version": 1,
                    "canonical_test": "T.java",
                }
            },
            "mentor_policy": {"mode": "pure_mentor", "may_write": []},
        }
    )
    report = DockerGradingRunner(timeout_s=120).grade(
        instance=_instance(ticket), ticket=ticket, track=_track(), workspace=tmp_path
    )

    assert report.correctness.passed is False
    assert report.correctness.tests[0]["status"] == "error"
    assert report.provenance.host_env["simulated"] is False
