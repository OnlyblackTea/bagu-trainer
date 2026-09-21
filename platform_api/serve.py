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
from pathlib import Path

from fastapi import FastAPI

from platform_service.content_loader import ContentValidationError, load_track

from .app import create_app
from .demo_repo import build_demo_track_repo
from .deps import GradingBackend

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
    forge_base: Path | None = None,
    workspace_root: Path | None = None,
    rebuild_demo: bool = False,
    grading: GradingBackend | None = None,
) -> FastAPI:
    """按内容目录装配 app；``content_dir=None`` 时用 demo track-repo。

    demo 仓库**幂等复用**：已存在就不重建——重建会在"内容没变"时让 ``git commit`` 无话可提交
    而报错，重启开发服务器就崩。但复用前会用当前 schema 验一次，不通过就重建（见上）。
    """
    if content_dir is None:
        root = workspace_root or DEFAULT_WORKSPACE_ROOT
        demo = root / "demo"
        if rebuild_demo or not (demo / ".git").exists() or not _demo_is_still_valid(demo):
            shutil.rmtree(demo, ignore_errors=True)
            content_dir = build_demo_track_repo(demo)
        else:
            content_dir = demo
        forge_base = forge_base or root / "ws"
    return create_app(content_dir=content_dir, forge_base=forge_base, grading=grading)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="起 A′ 后端（默认用 demo Track）")
    parser.add_argument(
        "--content-dir",
        type=Path,
        default=None,
        help="Track 内容目录（含 track.yaml 与 stages/*/ticket.yaml）；省略则用 demo Track",
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
    args = parser.parse_args(argv)

    app = build_server(
        args.content_dir,
        forge_base=args.forge_base,
        workspace_root=args.workspace_root,
        grading=args.grading,
    )
    import uvicorn

    print(f"A′ 后端: http://{args.host}:{args.port}  (docs: /docs)")
    print(f"阅卷后端: {app.state.grading_backend}")
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
