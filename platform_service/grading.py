"""本地阅卷缝（A′）。

平台在学习者自己的机器上跑测试，结果是**不可信自报告**（设计 §7.4）。两个实现挂在同一个缝上：
:class:`~platform_service.stubs.StubGradingRunner`（离线预置）与
:class:`~platform_api.grading_docker.DockerGradingRunner`（真跑 canonical 测试）。

**为什么 ``grade()`` 要收 ``track``**（v0.5 修掉的债务）：阅卷需要两样只有 Track 才知道的东西——

* ``runtime.compose``：**判据依赖运行环境**。验收测试用 ``@Sql`` 跑迁移脚本，而"跑过一次参考解的
  迁移"会把索引留在容器里；此后骨架只改回工作区文件、不动数据库，测试**照样绿**——一个零成本的
  绕过。要关掉它，阅卷前必须把基础设施重置到干净状态，而"用哪个 compose"只有 Track 知道。
* ``manifest.requires.build``：按 Track 声明的构建工具派发命令，而不是把 maven 硬编码进来。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol, runtime_checkable

from .models import GradingReport, TicketInstance, TicketSpec, TrackManifest


class GradingStep(StrEnum):
    """阅卷的**阶段**（§C.3 步骤 6）。

    "提交之后要等多久、现在在干什么"必须能答得出来：真阅卷要重置容器（``down -v``）再跑
    ``mvn test``，几十秒起步。没有阶段上报，学员只能对着一个转圈猜是"在跑"还是"卡死了"。

    **刻意是稳定短标识而不是人话**：人话在前端（同一个词在不同语言/措辞下会漂），后端只承诺
    "现在到了哪一步"。加一步是加一个枚举值，不是改字符串。
    """

    QUEUED = "queued"
    RESET_INFRA = "reset_infra"
    RUN_TESTS = "run_tests"
    PARSE = "parse"
    DONE = "done"
    FAILED = "failed"


@dataclass(frozen=True)
class GradingProgress:
    """一次阶段上报。``detail`` 是可选的补充（例如"重置 mysql, redis"），给学生看。"""

    step: GradingStep
    detail: str = ""


#: 阅卷过程中上报阶段。**可选**：不给就不报（桩、单测、别的调用方都不必关心）。
ProgressReporter = Callable[[GradingProgress], None]


def report_progress(
    reporter: ProgressReporter | None, step: GradingStep, detail: str = ""
) -> None:
    """上报一步。没有 reporter 就什么都不做；**回调抛错一律吞掉**。

    进度是附属品，不是判据：一个上报失败（前端断开、登记处被关掉）绝不能把一次本来能跑完的
    阅卷变成失败。所有 runner 实现都必须经这个函数上报，而不是直接调 ``reporter(...)``。
    """
    if reporter is None:
        return
    try:
        reporter(GradingProgress(step=step, detail=detail))
    except Exception:  # noqa: BLE001 — 见 docstring：进度失败不得影响阅卷结果
        pass


@runtime_checkable
class GradingRunner(Protocol):
    """对某个 in_progress 实例的工作区跑阅卷，产出 GradingReport。

    返回值**不含 submission_id 绑定**——由 :meth:`platform_service.service.PlatformService.submit`
    重新键到铸造出的 submission 上。

    ``on_progress`` 让调用方（提交后的后台任务）能把"现在到哪一步了"转达给学员；它**只上报**，
    不改变结果，也不允许抛错影响阅卷（实现侧自行吞掉回调异常）。
    """

    def grade(
        self,
        *,
        instance: TicketInstance,
        ticket: TicketSpec,
        track: TrackManifest,
        workspace: Path,
        on_progress: ProgressReporter | None = None,
    ) -> GradingReport: ...
