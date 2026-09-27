"""A′ 后端启动入口（开发/演示用）。

spec §C.9 写的 ``uvicorn platform_api.app:app`` **跑不通**：``create_app`` 是工厂，必须有
``content_dir``，模块级并没有 ``app``。本模块补上这个入口，并顺带解决"想先看看效果但手上
还没有 Track 仓库"的场景——不给 ``--content-dir`` 就用 Plan 1 的 demo track-repo。

用法::

    .venv/Scripts/python.exe -m platform_api.serve                    # demo Track
    .venv/Scripts/python.exe -m platform_api.serve --content-dir ../seckill-lab
    .venv/Scripts/python.exe -m platform_api.serve --content-dir ../seckill-lab \\
        --grading docker        # 真跑 canonical 测试（需 JDK17 + Maven，见 D3）
"""

from __future__ import annotations

import argparse
import shutil
from collections.abc import Sequence
from pathlib import Path

from fastapi import FastAPI

from platform_service.content_loader import ContentValidationError, load_track

from .app import create_app
from .demo_repo import build_demo_track_repo
from .deps import GradingBackend, StoreBackend
from .stubs_wiring import ForgeBackend

_REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
#: 默认放在 gitignore 的 scratch/ 下，重启后工作区与 demo 仓库都还在。
DEFAULT_WORKSPACE_ROOT = _REPO_ROOT / "scratch" / "_serve"


def _demo_is_still_valid(demo: Path) -> bool:
    """复用的 demo 仓库还能过**当前** schema 吗？

    复用是必须的（见下），但内容 schema 一旦变严（例如 §11.3 新增必填的
    ``runtime.protected_paths``），旧的 demo 就会让服务在**启动时**抛一个跟"我刚改的东西"
    毫不相干的 ``ContentValidationError``。对 demo（不是用户内容）来说，重建才是正确恢复。
    """
    try:
        load_track(demo / "track.yaml")
    except ContentValidationError:
        return False
    return True


def build_server(
    content_dir: Path | None = None,
    *,
    content_dirs: Sequence[Path] | None = None,
    forge_base: Path | None = None,
    workspace_root: Path | None = None,
    rebuild_demo: bool = False,
    grading: GradingBackend | None = None,
    grading_reset: bool | None = None,
    forge: ForgeBackend | None = None,
    store: StoreBackend | None = None,
    database_url: str | None = None,
    instance_id: str | None = None,
) -> FastAPI:
    """按内容目录装配 app；一个都不给时用 demo track-repo（``content_dirs`` 可给多个）。

    demo 仓库**幂等复用**：已存在就不重建——重建会在"内容没变"时让 ``git commit`` 无话可提交
    而报错，重启开发服务器就崩。但复用前会用当前 schema 验一次，不通过就重建（见上）。
    """
    if content_dir is None and not content_dirs:
        root = workspace_root or DEFAULT_WORKSPACE_ROOT
        demo = root / "demo"
        if rebuild_demo or not (demo / ".git").exists() or not _demo_is_still_valid(demo):
            shutil.rmtree(demo, ignore_errors=True)
            content_dir = build_demo_track_repo(demo)
        else:
            content_dir = demo
        forge_base = forge_base or root / "ws"
    return create_app(
        content_dir=content_dir,
        content_dirs=content_dirs,
        forge_base=forge_base,
        grading=grading,
        grading_reset=grading_reset,
        forge=forge,
        store_backend=store,
        database_url=database_url,
        instance_id=instance_id,
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="起 A′ 后端（默认用 demo Track）")
    parser.add_argument(
        "--content-dir",
        type=Path,
        action="append",
        default=None,
        help="Track 内容目录（含 track.yaml 与 stages/*/ticket.yaml）；**可重复**以部署多个 "
        "Track（--content-dir A --content-dir B）；省略则用 demo Track",
    )
    parser.add_argument("--forge-base", type=Path, default=None, help="本地工作区根目录")
    parser.add_argument(
        "--workspace-root",
        type=Path,
        default=None,
        help="demo 模式下的根目录（demo 仓库与工作区都建在这里）",
    )
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--reload", action="store_true", help="改代码自动重启")
    parser.add_argument(
        "--grading",
        choices=("stub", "docker"),
        default=None,
        help="阅卷后端：stub=预置红绿（默认；也可用 BAGU_GRADING 环境变量）；"
        "docker=在本机真跑 Track 的 canonical 测试（需 JDK17 + Maven + Track 的基础设施）",
    )
    parser.add_argument(
        "--grading-reset",
        choices=("auto", "never"),
        default=None,
        help="真阅卷前是否重置 Track 的 compose 基础设施：auto=重置（默认，保证'骨架红'是真红）；"
        "never=不重置——会把'跑过一次参考解迁移就永久变绿'的绕开放回来",
    )
    parser.add_argument(
        "--forge",
        choices=("stub", "gitea"),
        default=None,
        help="Forge 后端：stub=本地 clone + 占位 PR URL（默认；也可用 BAGU_FORGE）；"
        "gitea=真 Gitea（需要 BAGU_GITEA_URL/TOKEN/UPSTREAM；学员直接 push dev 到内容仓）",
    )
    parser.add_argument(
        "--store",
        choices=("sqlite", "postgres", "memory"),
        default=None,
        help="持久化后端：sqlite=单文件数据库，**重启不失忆**（默认；也可用 BAGU_STORE）；"
        "postgres=多人/多实例共库；memory=进程内字典，重启即失忆（只适合临时演示）",
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="sqlite 是数据库文件路径（默认 ./bagu.sqlite3，绝对路径会打印在启动横幅里）；"
        "postgres 是 libpq 连接串，例如 postgresql://bagu:bagu@127.0.0.1:5432/bagu。"
        "也可用 BAGU_DATABASE_URL",
    )
    parser.add_argument(
        "--instance-id",
        default=None,
        help="本平台实例的标识（也可用 BAGU_INSTANCE_ID，默认 default）。**多个实例共用一个库时"
        "必须各不相同**：启动时清扫「被重启打断的阅卷记录」只动自己名下的，同名会互相误伤",
    )
    args = parser.parse_args(argv)
    dirs = args.content_dir or []

    app = build_server(
        dirs[0] if len(dirs) == 1 else None,
        content_dirs=dirs if len(dirs) > 1 else None,
        forge_base=args.forge_base,
        workspace_root=args.workspace_root,
        grading=args.grading,
        grading_reset=None if args.grading_reset is None else args.grading_reset == "auto",
        forge=args.forge,
        store=args.store,
        database_url=args.database_url,
        instance_id=args.instance_id,
    )
    import uvicorn

    print(f"A′ 后端: http://{args.host}:{args.port}  (docs: /docs)")
    print(f"已部署 Track: {', '.join(app.state.tracks)}")
    print(
        f"阅卷后端: {app.state.grading_backend}  环境重置: {app.state.grading_reset}"
        f"  Forge: {app.state.forge_backend}"
    )
    # 存储落点单独一行：部署的人应当能一眼说出"我的做题进度存在哪儿"。
    print(f"存储: {app.state.store_backend} → {app.state.store_target}")
    if args.reload:
        uvicorn.run(
            "platform_api.serve:build_server",
            factory=True,
            host=args.host,
            port=args.port,
            reload=True,
        )
    else:
        uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
