"""把 ForgeClient 的具体实现选择收口在一处。

（文件名是历史遗留：它当初只负责"现在用桩"。现在它同时管**真实现**的装配，路由与
``PlatformService`` 依旧不动。）

* ``stub``（默认）—— :class:`~platform_service.stubs.StubForgeClient`：本地 clone + 占位 PR URL，
  离线可跑；diff 两端都是内容预置的 ref，**假装学员已经做完了**。
* ``gitea`` —— :class:`~platform_api.forge_gitea.GiteaForgeClient`：真 fork + 真 PR。
  需要 ``BAGU_GITEA_URL`` / ``BAGU_GITEA_TOKEN`` / ``BAGU_GITEA_UPSTREAM`` / ``BAGU_GITEA_OWNER``。

``stage_refs``（``identity.id`` → 阶段名）必须由内容侧提供（``content_loader.load_stage_refs``）：
§C.5 的 stage 分支是按**阶段**命名的，Forge 不该去猜、更不该拿 ticket id 当分支名。
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from platform_service.forge import ForgeClient
from platform_service.stubs import StubForgeClient

from .forge_gitea import GiteaForgeClient
from .gitea import GiteaApi

ForgeBackend = Literal["stub", "gitea"]

FORGE_BACKEND_ENV = "BAGU_FORGE"
GITEA_URL_ENV = "BAGU_GITEA_URL"
GITEA_TOKEN_ENV = "BAGU_GITEA_TOKEN"
GITEA_UPSTREAM_ENV = "BAGU_GITEA_UPSTREAM"  # "owner/repo"，Track 内容的上游仓
GITEA_OWNER_ENV = "BAGU_GITEA_OWNER"  # 学员（部署者）在 Gitea 上的账号 = fork 的归属


def resolve_forge_backend(explicit: ForgeBackend | None = None) -> ForgeBackend:
    """显式参数 > 环境变量 > ``stub``。取值非法时**立刻**炸（而不是 provision 时才炸）。"""
    if explicit is not None:
        return explicit
    raw = os.environ.get(FORGE_BACKEND_ENV, "stub").strip().lower()
    if raw not in ("stub", "gitea"):
        raise ValueError(f"{FORGE_BACKEND_ENV} must be 'stub' or 'gitea', got {raw!r}")
    return raw  # type: ignore[return-value]


@dataclass(frozen=True)
class GiteaSettings:
    base_url: str
    token: str
    upstream: str
    fork_owner: str


def gitea_settings_from_env() -> GiteaSettings:
    """读 Gitea 配置；缺哪一项就说清缺哪一项（而不是 provision 时抛一个 KeyError）。"""
    values = {
        GITEA_URL_ENV: os.environ.get(GITEA_URL_ENV, "").strip(),
        GITEA_TOKEN_ENV: os.environ.get(GITEA_TOKEN_ENV, "").strip(),
        GITEA_UPSTREAM_ENV: os.environ.get(GITEA_UPSTREAM_ENV, "").strip(),
        GITEA_OWNER_ENV: os.environ.get(GITEA_OWNER_ENV, "").strip(),
    }
    missing = sorted(name for name, value in values.items() if not value)
    if missing:
        raise ValueError(f"forge=gitea requires these env vars: {', '.join(missing)}")
    upstream = values[GITEA_UPSTREAM_ENV]
    if "/" not in upstream:
        raise ValueError(f"{GITEA_UPSTREAM_ENV} must look like 'owner/repo', got {upstream!r}")
    return GiteaSettings(
        base_url=values[GITEA_URL_ENV],
        token=values[GITEA_TOKEN_ENV],
        upstream=upstream,
        fork_owner=values[GITEA_OWNER_ENV],
    )


def forge_backend_name(forge: ForgeClient) -> ForgeBackend:
    """给 ``/meta`` 用：这个 forge 是真后端还是桩？"""
    return "stub" if getattr(forge, "SIMULATED", True) else "gitea"


def build_forge(
    *,
    source_repo: Path,
    base_dir: Path,
    stage_refs: Mapping[str, str],
    backend: ForgeBackend | None = None,
    settings: GiteaSettings | None = None,
) -> ForgeClient:
    base_dir.mkdir(parents=True, exist_ok=True)
    resolved = resolve_forge_backend(backend)
    if resolved == "gitea":
        cfg = settings or gitea_settings_from_env()
        return GiteaForgeClient(
            api=GiteaApi(base_url=cfg.base_url, token=cfg.token),
            upstream=cfg.upstream,
            fork_owner=cfg.fork_owner,
            base_dir=base_dir,
            stage_refs=stage_refs,
        )
    return StubForgeClient(source_repo=source_repo, base_dir=base_dir, stage_refs=stage_refs)
