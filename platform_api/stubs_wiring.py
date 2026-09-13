"""把 ForgeClient 的具体实现选择收口在一处（A′ = StubForgeClient）。

将来换真 Gitea 只需改这里；路由与 PlatformService 不动。
"""

from __future__ import annotations

from pathlib import Path

from platform_service.forge import ForgeClient
from platform_service.stubs import StubForgeClient


def build_forge(*, source_repo: Path, base_dir: Path) -> ForgeClient:
    base_dir.mkdir(parents=True, exist_ok=True)
    return StubForgeClient(source_repo=source_repo, base_dir=base_dir)
