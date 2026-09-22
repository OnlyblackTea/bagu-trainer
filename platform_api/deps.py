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

**持久化后端的选择**（``BAGU_STORE`` 环境变量或 ``serve --store``）：

* ``memory``（默认）—— :class:`~platform_service.store.InMemoryStore`：进程内字典，
  **重启即失忆**。够用于跑测试和"我就想看看界面"；
* ``postgres`` —— :class:`~platform_api.store_postgres.PostgresStore`：进度落库，重启后
  enrollment/instance/submission/report/decision 都还在。DSN 取自 ``--database-url`` 或
  ``BAGU_DATABASE_URL``（psycopg3 的 libpq 连接串，例如
  ``postgresql://user:pw@127.0.0.1:5432/bagu``）。

**真阅卷前的环境重置**（``BAGU_GRADING_RESET`` 环境变量或 ``serve --grading-reset``）：

* ``auto``（默认）—— 每次阅卷前按 ``runtime.compose`` 做 ``down -v`` + ``up -d --wait``。
  必须如此：验收测试的 ``@Sql`` 跑的是**长活**数据库，一旦有人在同一个库里跑过参考解的迁移
  （比如瞄了一眼答案），索引就留在那儿了，此后骨架一个字节不写也能绿——那是零成本的绕过。
* ``never`` —— 关掉重置。**等于把上面那条绕过又放回来**，只在你确实要保留库内状态时用。
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
StoreBackend = Literal["memory", "postgres"]

#: 选阅卷后端的开关。默认 ``stub``：装好 JDK17+Maven 之前，平台照样能整条跑通。
GRADING_BACKEND_ENV = "BAGU_GRADING"
#: 真阅卷前是否重置 Track 基础设施：``auto``（默认，重置）/ ``never``（不重置，会放大"假绿"）。
GRADING_RESET_ENV = "BAGU_GRADING_RESET"
#: 选持久化后端的开关。默认 ``memory``：不再默认失忆之后，这个默认值也该显式改过来，
#: 但先保持"零配置能跑"，由 ``serve`` 在启动横幅里如实打印当前用的是哪个。
STORE_BACKEND_ENV = "BAGU_STORE"
#: ``--store postgres`` 时用的 libpq 连接串。
DATABASE_URL_ENV = "BAGU_DATABASE_URL"

_TRUTHY = frozenset({"auto", "1", "true", "yes"})
_FALSY = frozenset({"never", "0", "false", "no"})


def resolve_store_backend(explicit: StoreBackend | None = None) -> StoreBackend:
    """显式参数 > 环境变量 > ``memory``。取值非法时**立刻**炸。"""
    if explicit is not None:
        return explicit
    raw = os.environ.get(STORE_BACKEND_ENV, "memory").strip().lower()
    if raw not in ("memory", "postgres"):
        raise ValueError(f"{STORE_BACKEND_ENV} must be 'memory' or 'postgres', got {raw!r}")
    return raw  # type: ignore[return-value]


def build_store(backend: StoreBackend | None = None, *, dsn: str | None = None) -> Store:
    """按 ``backend``（缺省读环境变量）产出一个 :class:`Store`。

    ``postgres_store`` 是**惰性导入**：``memory`` 路径不该因为"没装 psycopg"而打不开，
    反过来说选了 ``postgres`` 又没装驱动，应当在这里立刻炸（配错要现在就知道）。
    """
    if resolve_store_backend(backend) == "postgres":
        from .store_postgres import PostgresStore

        return PostgresStore(dsn or os.environ.get(DATABASE_URL_ENV, ""))
    return InMemoryStore()


def store_backend_name(store: Store) -> StoreBackend:
    """如实上报当前用的是哪个 store（``/meta`` 用；前端据此说清"重启会不会丢"）。"""
    return "postgres" if type(store).__name__ == "PostgresStore" else "memory"


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


def resolve_grading_reset(explicit: bool | None = None) -> bool:
    """显式参数 > 环境变量 > 重置（``auto``）。取值非法时立刻炸。"""
    if explicit is not None:
        return explicit
    raw = os.environ.get(GRADING_RESET_ENV, "auto").strip().lower()
    if raw in _TRUTHY:
        return True
    if raw in _FALSY:
        return False
    raise ValueError(f"{GRADING_RESET_ENV} must be 'auto' or 'never', got {raw!r}")


def build_service(
    content_dir: Path,
    store: Store | None = None,
) -> tuple[Store, TrackManifest, dict[str, TicketSpec], PlatformService]:
    """加载 ``content_dir`` 的 track + 全部 ticket，装配服务。

    ``store`` 缺省是 :class:`InMemoryStore`；传一个（例如 :class:`PostgresStore`）就用它——
    ``PlatformService`` 只认缝，换后端不改它一行。

    Ticket 发现走 :func:`~platform_service.content_loader.load_tickets`（glob
    ``stages/*/ticket.yaml``、按 ``identity.id`` 为键），**不按 ``manifest.stages``
    逐个加载**：后者是看板清单，允许声明"只上榜、不出卷"的 stage（真 Track 的
    ``stage-00-onboarding`` 就是如此），按它加载会让应用在真 Track 上启动失败。
    """
    track = load_track(content_dir / "track.yaml")
    tickets = load_tickets(content_dir)
    store = store if store is not None else InMemoryStore()
    service = PlatformService(store, track, tickets, engine_version=PLATFORM_VERSION)
    return store, track, tickets, service


def grading_runner(
    scenario: GradingScenario,
    *,
    backend: GradingBackend | None = None,
    reset_infra: bool | None = None,
) -> GradingRunner:
    """按 ``backend``（缺省读环境变量）产出一个 GradingRunner。

    ``scenario`` **只对桩有意义**：真阅卷跑的是工作区里真实的东西，不认 scenario——
    这也正是"桩的 pass/fail 是装出来的、真的不是"的体现。
    """
    if resolve_grading_backend(backend) == "docker":
        return DockerGradingRunner(reset_infra=resolve_grading_reset(reset_infra))
    return StubGradingRunner(scenario=scenario)


def review_engine(scenario: ReviewScenario) -> StubReviewEngine:
    return StubReviewEngine(scenario=scenario)
