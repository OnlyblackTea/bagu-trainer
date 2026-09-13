"""组合根（设计 spec §C.2）：A′ 唯一知道"现在用桩"的地方。

:func:`build_service` 从内容目录装配纯域层（store + track + tickets + PlatformService）；
桩工厂按 ``scenario`` 产出对应占位后端。真实后端落地时只改这里（与 app.py 的装配），
路由与 PlatformService 不动。``engine_version`` 字段是版本锚（模型遗留命名，退休引擎后
不再指引擎，避免 churn 存活的 models.py 而保留）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from platform_service.content_loader import load_tickets, load_track
from platform_service.models import TicketSpec, TrackManifest
from platform_service.service import PlatformService
from platform_service.store import InMemoryStore, Store
from platform_service.stubs import StubGradingRunner, StubReviewEngine

PLATFORM_VERSION = "a-prime-0.1.0"

GradingScenario = Literal["pass", "fail"]
ReviewScenario = Literal["approve", "request_changes"]


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


def grading_runner(scenario: GradingScenario) -> StubGradingRunner:
    return StubGradingRunner(scenario=scenario)


def review_engine(scenario: ReviewScenario) -> StubReviewEngine:
    return StubReviewEngine(scenario=scenario)
