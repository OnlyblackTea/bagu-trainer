"""组合根（设计 spec §C.2）：A′ 唯一知道"现在用哪个后端"的地方。

:func:`build_service` 从内容目录装配纯域层（store + track + tickets + PlatformService）；
后端工厂按需产出**桩**或**真实现**。换真后端只改这里与 ``app.py`` 的装配，路由与
``PlatformService`` 不动。``engine_version`` 字段是版本锚（模型遗留命名，退休引擎后不再指
引擎，为避免 churn 存活的 models.py 而保留）。

**阅卷后端的选择**（``BAGU_GRADING`` 环境变量或 ``serve --grading``）：

* ``stub``（默认）—— :class:`~platform_service.stubs.StubGradingRunner`：离线可跑，返回预置
  红绿，用于开发与演示；
* ``docker`` —— :class:`~platform_api.grading_docker.DockerGradingRunner`：在**本机**真跑
  Track 的 canonical 测试。需要 JDK17 + Maven，以及 Track 声明的 compose 基础设施（§7.1）——
  也就是 D3 说的"学员自己的机器"。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from platform_service.content_loader import load_tickets, load_track
from platform_service.grading import GradingRunner
from platform_service.models import TicketSpec, TrackManifest
from platform_service.service import PlatformService
from platform_service.store import InMemoryStore, Store
from platform_service.stubs import StubGradingRunner, StubReviewEngine

from .grading_docker import DockerGradingRunner

PLATFORM_VERSION = "a-prime-0.1.0"

GradingScenario = Literal["pass", "fail"]
ReviewScenario = Literal["approve", "request_changes"]
GradingBackend = Literal["stub", "docker"]

#: 选阅卷后端的开关。默认 ``stub``：装好 JDK17+Maven 之前，平台照样能整条跑通。
GRADING_BACKEND_ENV = "BAGU_GRADING"


def resolve_grading_backend(explicit: GradingBackend | None = None) -> GradingBackend:
    """显式参数 > 环境变量 > ``stub``。取值非法时**立刻**炸（而不是发卷时才炸）。"""
    if explicit is not None:
        return explicit
    raw = os.environ.get(GRADING_BACKEND_ENV, "stub").strip().lower()
    if raw not in ("stub", "docker"):
        raise ValueError(
            f"{GRADING_BACKEND_ENV} must be 'stub' or 'docker', got {raw!r}"
        )
    return raw  # type: ignore[return-value]


def build_service(
    content_dir: Path,
) -> tuple[Store, TrackManifest, dict[str, TicketSpec], PlatformService]:
    """加载 ``content_dir`` 的 track + 全部 ticket，装配一个 InMemoryStore 上的服务。

    Ticket 发现走 :func:`~platform_service.content_loader.load_tickets`（glob
    ``stages/*/ticket.yaml``、按 ``identity.id`` 为键），**不按 ``manifest.stages``
    逐个加载**：后者是看板清单，允许声明"只上榜、不出卷"的 stage（真 Track 的
    ``stage-00-onboarding`` 就是如此），按它加载会让应用在真 Track 上启动失败。
    """
    track = load_track(content_dir / "track.yaml")
    tickets = load_tickets(content_dir)
    store = InMemoryStore()
    service = PlatformService(store, track, tickets, engine_version=PLATFORM_VERSION)
    return store, track, tickets, service


def grading_runner(
    scenario: GradingScenario, *, backend: GradingBackend | None = None
) -> GradingRunner:
    """按 ``backend``（缺省读环境变量）产出一个 GradingRunner。

    ``scenario`` **只对桩有意义**：真阅卷跑的是工作区里真实的东西，不认 scenario——
    这也正是"桩的 pass/fail 是装出来的、真的不是"的体现。
    """
    if resolve_grading_backend(backend) == "docker":
        return DockerGradingRunner()
    return StubGradingRunner(scenario=scenario)


def review_engine(scenario: ReviewScenario) -> StubReviewEngine:
    return StubReviewEngine(scenario=scenario)
