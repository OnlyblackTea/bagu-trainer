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
