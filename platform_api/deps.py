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

* ``sqlite``（**默认**）—— :class:`~platform_api.store_sqlite.SqliteStore`：标准库自带的单文件
  数据库，**零额外依赖、重启不失忆**。这是"一个人自托管"的正确默认值：进程内字典会让攒了一周
  的进度消失，PostgreSQL 又要求先装一个服务。
* ``postgres`` —— :class:`~platform_api.store_postgres.PostgresStore`：多人/多实例共库时用。
* ``memory`` —— :class:`~platform_service.store.InMemoryStore`：进程内字典，跑测试与临时演示用，
  **重启即失忆**。

DSN/路径取自 ``--database-url`` 或 ``BAGU_DATABASE_URL``：postgres 是 libpq 连接串，sqlite 是
数据库文件路径（``sqlite:///…`` 也认）。不给就用 ``<workspace-root>/bagu.sqlite3``。

**真阅卷前的环境重置**（``BAGU_GRADING_RESET`` 环境变量或 ``serve --grading-reset``）：

* ``auto``（默认）—— 每次阅卷前按 ``runtime.compose`` 做 ``down -v`` + ``up -d --wait``。
  必须如此：验收测试的 ``@Sql`` 跑的是**长活**数据库，一旦有人在同一个库里跑过参考解的迁移
  （比如瞄了一眼答案），索引就留在那儿了，此后骨架一个字节不写也能绿——那是零成本的绕过。
* ``never`` —— 关掉重置。**等于把上面那条绕过又放回来**，只在你确实要保留库内状态时用。
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from platform_service.content_loader import (
    load_stage_refs,
    load_ticket_dirs,
    load_tickets,
    load_track,
)
from platform_service.forge import ForgeClient
from platform_service.grading import GradingRunner
from platform_service.models import TicketSpec, TrackManifest
from platform_service.service import PlatformService
from platform_service.store import InMemoryStore, Store
from platform_service.stubs import StubGradingRunner, StubReviewEngine

from .grading_docker import DockerGradingRunner
from .stubs_wiring import ForgeBackend, build_forge

PLATFORM_VERSION = "a-prime-0.1.0"

GradingScenario = Literal["pass", "fail"]
ReviewScenario = Literal["approve", "request_changes"]
GradingBackend = Literal["stub", "docker"]
StoreBackend = Literal["sqlite", "postgres", "memory"]

#: 选阅卷后端的开关。默认 ``stub``：装好 JDK17+Maven 之前，平台照样能整条跑通。
GRADING_BACKEND_ENV = "BAGU_GRADING"
#: 真阅卷前是否重置 Track 基础设施：``auto``（默认，重置）/ ``never``（不重置，会放大"假绿"）。
GRADING_RESET_ENV = "BAGU_GRADING_RESET"
#: 选持久化后端的开关。**默认 sqlite**：重启不失忆是底线，而它零依赖。
STORE_BACKEND_ENV = "BAGU_STORE"
#: ``--store postgres`` 的 libpq 连接串 / ``--store sqlite`` 的数据库文件路径。
DATABASE_URL_ENV = "BAGU_DATABASE_URL"
#: ``--store sqlite`` 不给路径时的落点：**启动时的当前目录**。不是源码树里，也不是临时目录——
#: 部署的人应当能一眼说出"我的进度存在哪个文件里"，启动横幅会把绝对路径打出来。
DEFAULT_SQLITE_PATH = "bagu.sqlite3"

#: 容器里那个数据根（进度库 + 工作区）。compose 栈把它绑定挂载自宿主机的一个目录。
CONTAINER_DATA_DIR = "/var/lib/bagu"
#: 上面那个目录在**宿主机**上的路径。只在容器里才有意义：设了它，API 就能把工作区的路径翻译成
#: 部署者在自己机器上看得到的那条（否则界面显示的 `/var/lib/bagu/ws/...` 对容器外的人毫无用处——
#: 用户就是这么反馈的）。
HOST_DATA_DIR_ENV = "BAGU_HOST_DATA_DIR"


def host_visible_path(path: str | Path) -> str | None:
    """把容器内的工作区路径翻成宿主机的路径；没配 ``BAGU_HOST_DATA_DIR`` 或不在那个根下则 ``None``。

    只做前缀替换，不猜、不做符号链接解析：容器路径与宿主路径是**同一个目录**（绑定挂载），
    所以这条映射是忠实的。翻译不出来就返回 ``None``——界面据此不显示那一行，而不是显示一个假的。
    """
    host_root = os.environ.get(HOST_DATA_DIR_ENV, "").strip()
    if not host_root:
        return None
    text = str(path)
    if text == CONTAINER_DATA_DIR or text.startswith(CONTAINER_DATA_DIR + "/"):
        return host_root.rstrip("/") + text[len(CONTAINER_DATA_DIR):]
    return None

_TRUTHY = frozenset({"auto", "1", "true", "yes"})
_FALSY = frozenset({"never", "0", "false", "no"})


def resolve_store_backend(explicit: StoreBackend | None = None) -> StoreBackend:
    """显式参数 > 环境变量 > ``sqlite``。取值非法时**立刻**炸。"""
    if explicit is not None:
        return explicit
    raw = os.environ.get(STORE_BACKEND_ENV, "sqlite").strip().lower()
    if raw not in ("sqlite", "postgres", "memory"):
        raise ValueError(
            f"{STORE_BACKEND_ENV} must be 'sqlite', 'postgres' or 'memory', got {raw!r}"
        )
    return raw  # type: ignore[return-value]


def build_store(backend: StoreBackend | None = None, *, dsn: str | None = None) -> Store:
    """按 ``backend``（缺省读环境变量）产出一个 :class:`Store`。

    ``sqlite``/``postgres`` 的实现都是**惰性导入**：选 ``memory`` 的人不该因为没装驱动而打不开；
    反过来选了某个后端又缺依赖/缺路径，应当在这里立刻炸（配错要现在就知道）。
    """
    resolved = resolve_store_backend(backend)
    target = (dsn or os.environ.get(DATABASE_URL_ENV, "")).strip()
    if resolved == "postgres":
        from .store_postgres import PostgresStore

        return PostgresStore(target)
    if resolved == "sqlite":
        from .store_sqlite import SqliteStore

        return SqliteStore(target or DEFAULT_SQLITE_PATH)
    return InMemoryStore()


def store_backend_name(store: Store) -> StoreBackend:
    """如实上报当前用的是哪个 store（``/meta`` 用；前端据此说清"重启会不会丢"）。"""
    name = type(store).__name__
    if name == "PostgresStore":
        return "postgres"
    if name == "SqliteStore":
        return "sqlite"
    return "memory"


def store_target_name(store: Store) -> str:
    """给人看的**存储落点**（sqlite 是那个文件的绝对路径）。

    部署的人应当能一眼回答"我的做题进度到底存哪儿了"——所以启动横幅与 ``/meta`` 都带上它，
    而不是让人去猜。
    """
    path = getattr(store, "path", None)
    if path is not None:
        return str(Path(path).resolve())
    return "（进程内，重启即失忆）" if store_backend_name(store) == "memory" else "（见 DSN）"


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


@dataclass(frozen=True)
class TrackBundle:
    """一个已部署 Track 的**运行时装配**：内容 + 它自己的 service 与 forge。

    每个 Track 一个 :class:`~platform_service.forge.ForgeClient`：stage 分支名、上游仓库、
    本地工作区都是这个 Track 自己的（§C.5），共用会把两个 Track 的工作区串在一起。
    :class:`~platform_service.store.Store` 反而是**全局**的——实例靠 ``TicketInstance.track``
    归属，不靠给每个 Track 分一个库（那样"我这一届都干了啥"就得跨库拼）。
    """

    track: TrackManifest
    tickets: dict[str, TicketSpec]
    #: 看板按 stage 目录名书写，ticket 按 ``identity.id`` 标识，故发卷路由需要这份索引。
    stage_tickets: dict[str, TicketSpec]
    service: PlatformService
    forge: ForgeClient
    content_dir: Path


def build_service(
    content_dir: Path,
    store: Store | None = None,
) -> tuple[Store, TrackManifest, dict[str, TicketSpec], PlatformService]:
    """加载 ``content_dir`` 的 track + 全部 ticket，装配服务（单 Track 便捷入口）。

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


def build_tracks(
    content_dirs: Sequence[Path],
    store: Store,
    *,
    forge_source: Path | None = None,
    forge_base: Path | None = None,
    forge: ForgeBackend | None = None,
) -> dict[str, TrackBundle]:
    """把每个内容目录装配成一个 :class:`TrackBundle`，按 ``track.id`` 索引。

    ``forge_source`` 只对**单 Track** 有意义（"内容在 A、git 上游在 B"的对照场景）；多 Track
    时每个 Track 的 git 上游就是它自己的内容目录，硬套一个 source 会让所有 Track 指向同一个仓
    同一个仓库。
    """
    if not content_dirs:
        raise ValueError("至少需要一个 Track 内容目录")
    if forge_source is not None and len(content_dirs) > 1:
        raise ValueError("forge_source 只能配一个 Track（多 Track 时各自用内容目录当上游）")

    bundles: dict[str, TrackBundle] = {}
    for content_dir in content_dirs:
        track = load_track(content_dir / "track.yaml")
        if track.id in bundles:
            raise ValueError(f"重复的 track.id: {track.id!r}（来自 {content_dir}）")
        tickets = load_tickets(content_dir)
        service = PlatformService(store, track, tickets, engine_version=PLATFORM_VERSION)
        base = forge_base or (content_dir.parent / "_workspaces")
        bundles[track.id] = TrackBundle(
            track=track,
            tickets=tickets,
            stage_tickets=load_ticket_dirs(content_dir),
            service=service,
            # 工作区再按 track.id 分一层：同名 stage 分支在两个 Track 里是两个仓库。
            forge=build_forge(
                source_repo=forge_source or content_dir,
                base_dir=base / track.id,
                stage_refs=load_stage_refs(content_dir),
                backend=forge,
            ),
            content_dir=content_dir,
        )
    return bundles


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
