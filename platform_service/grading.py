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

from pathlib import Path
from typing import Protocol, runtime_checkable

from .models import GradingReport, TicketInstance, TicketSpec, TrackManifest


@runtime_checkable
class GradingRunner(Protocol):
    """对某个 in_progress 实例的工作区跑阅卷，产出 GradingReport。

    返回值**不含 submission_id 绑定**——由 :meth:`platform_service.service.PlatformService.submit`
    重新键到铸造出的 submission 上。
    """

    def grade(
        self,
        *,
        instance: TicketInstance,
        ticket: TicketSpec,
        track: TrackManifest,
        workspace: Path,
    ) -> GradingReport: ...
