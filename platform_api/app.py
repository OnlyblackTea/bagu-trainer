"""FastAPI 应用工厂 + 异常映射（设计 spec §C.3/§C.8）。

``create_app`` 是组合根入口：建 store/service/forge/桩工厂挂到 ``app.state``，注册把域异常
翻成 ``{"detail": "<短码>"}`` 的处理器，并挂三组路由。纯 REST，无 SSE/WS（无 AI 流可推）。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from platform_service.ai_review import ReviewDiffTooLargeError
from platform_service.content_loader import load_stage_refs, load_ticket_dirs
from platform_service.forge import WorkspaceRefMissingError
from platform_service.lifecycle import IllegalTransitionError

from .deps import PLATFORM_VERSION, build_service, grading_runner, review_engine
from .stubs_wiring import build_forge


def _register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(IllegalTransitionError)
    async def _illegal(request: Request, exc: IllegalTransitionError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": "illegal_transition"})

    @app.exception_handler(KeyError)
    async def _key(request: Request, exc: KeyError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": "not_found"})

    @app.exception_handler(ValueError)
    async def _value(request: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(
            status_code=409, content={"detail": "decide_needs_report_and_review"}
        )

    @app.exception_handler(ReviewDiffTooLargeError)
    async def _too_large(request: Request, exc: ReviewDiffTooLargeError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": "diff_too_large"})

    @app.exception_handler(subprocess.CalledProcessError)
    async def _git(request: Request, exc: subprocess.CalledProcessError) -> JSONResponse:
        """git 非零退出（真故障，例如仓库损坏）。

        §C.8 要求错误一律 ``{"detail": "<短码>"}`` 且不泄露堆栈——git 失败若不映射，
        会以裸 ``Internal Server Error`` 冒出去，前端无从判断。
        """
        return JSONResponse(status_code=500, content={"detail": "git_command_failed"})

    @app.exception_handler(WorkspaceRefMissingError)
    async def _ref_missing(
        request: Request, exc: WorkspaceRefMissingError
    ) -> JSONResponse:
        """Track 声明的 stage 分支在 track-repo 里不存在（内容侧配置错误）。

        与上面的 ``git_command_failed`` 分开：那是真故障，这是**内容不符合 §C.5 的
        git 进度模型**（一个阶段一个分支）。带上缺失的 ref 与现有 ref，便于直接修内容。
        """
        return JSONResponse(
            status_code=500,
            content={
                "detail": "workspace_ref_missing",
                "ref": exc.ref,
                "available": exc.available,
            },
        )


def create_app(
    *, content_dir: Path, forge_source: Path | None = None, forge_base: Path | None = None
) -> FastAPI:
    app = FastAPI(title="bagu-trainer A′", version=PLATFORM_VERSION)
    store, track, tickets, service = build_service(content_dir)
    base = forge_base or (content_dir.parent / "_workspaces")
    # ref 布局来自内容：stage 分支按阶段命名，不拿 ticket id 当分支名（§C.5）。
    forge = build_forge(
        source_repo=forge_source or content_dir,
        base_dir=base,
        stage_refs=load_stage_refs(content_dir),
    )

    app.state.store = store
    app.state.track = track
    app.state.tickets = tickets
    # 看板按 stage 目录名书写而 ticket 按 identity.id 标识，故发卷路由需要这份索引。
    app.state.stage_tickets = load_ticket_dirs(content_dir)
    app.state.service = service
    app.state.forge = forge
    app.state.grading_runner = grading_runner
    app.state.review_engine = review_engine

    _register_exception_handlers(app)

    from .routes import progress, workspace
    from .routes import track as track_routes

    app.include_router(track_routes.router)
    app.include_router(progress.router)
    app.include_router(workspace.router)
    return app
