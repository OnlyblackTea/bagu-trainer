"""把 ForgeClient 的具体实现选择收口在一处（A′ = StubForgeClient）。

将来换真 Gitea 只需改这里；路由与 PlatformService 不动。

``stage_refs``（``identity.id`` → 阶段名）必须由内容侧提供（``content_loader.load_stage_refs``）：
§C.5 的 stage 分支是按**阶段**命名的，Forge 不该去猜、更不该拿 ticket id 当分支名。
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from platform_service.forge import ForgeClient
from platform_service.stubs import StubForgeClient


def build_forge(
    *, source_repo: Path, base_dir: Path, stage_refs: Mapping[str, str]
) -> ForgeClient:
    base_dir.mkdir(parents=True, exist_ok=True)
    return StubForgeClient(
        source_repo=source_repo, base_dir=base_dir, stage_refs=stage_refs
    )
