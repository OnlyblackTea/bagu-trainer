"""FastAPI 应用工厂 + 异常映射（设计 spec §C.3/§C.8）。

``create_app`` 是组合根入口：建 store/service/forge/后端工厂挂到 ``app.state``，注册把域异常
翻成 ``{"detail": "<短码>"}`` 的处理器，并挂三组路由。纯 REST，无 SSE/WS（无 AI 流可推）。

阅卷后端由 ``grading`` 参数（或 ``BAGU_GRADING``）选定：``stub`` 离线可跑，``docker`` 在本机
真跑 Track 的 canonical 测试（见 :mod:`platform_api.deps`）。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from platform_service.ai_review import ReviewDiffTooLargeError
from platform_service.content_loader import load_stage_refs, load_ticket_dirs
from platform_service.errors import GateNotReadyError, NotFoundError
from platform_service.forge import (
    ForgeUnavailableError,
    RemoteBranchMissingError,
    WorkspaceRefMissingError,
)
from platform_service.lifecycle import IllegalTransitionError

from .deps import (
    PLATFORM_VERSION,
    GradingBackend,
    build_service,
    grading_runner,
    resolve_grading_backend,
    resolve_grading_reset,
    review_engine,
)
from .grading_docker import GradingUnavailableError
from .stubs_wiring import ForgeBackend, build_forge, forge_backend_name


def _register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(IllegalTransitionError)
    async def _illegal(request: Request, exc: IllegalTransitionError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": "illegal_transition"})

    @app.exception_handler(NotFoundError)
    async def _missing(request: Request, exc: NotFoundError) -> JSONResponse:
        """实例 / 提交 / 工单不存在 → 404 ``not_found``。

        **只认这一种类型**：以前这里挂的是 ``KeyError``，于是任何一处打错字的 KeyError 都会被
        说成"资源不存在"（§C.8 要短码是为了让人看懂，不是为了让所有错看起来一样）。
        """
        return JSONResponse(status_code=404, content={"detail": "not_found"})

    @app.exception_handler(GateNotReadyError)
    async def _gate_not_ready(request: Request, exc: GateNotReadyError) -> JSONResponse:
        """验收门前置未齐（缺阅卷报告或评审）→ 409 ``decide_needs_report_and_review``。"""
        return JSONResponse(
            status_code=409, content={"detail": "decide_needs_report_and_review"}
        )

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
        """没被识别的异常 → 500 ``internal_error``。

        仍然给短码（§C.8：错误一律 ``{"detail": "<短码>"}``），但**不假装知道原因**——
        以前它会被 ValueError 的兜底映射伪装成"你还没评审"，学员照着做也过不去。
        """
        return JSONResponse(status_code=500, content={"detail": "internal_error"})

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

    @app.exception_handler(ForgeUnavailableError)
    async def _forge_down(request: Request, exc: ForgeUnavailableError) -> JSONResponse:
        """Forge（Gitea）不可达 / 认证失败 / API 报错。

        503 而不是 500：这是**基础设施**问题（跟"你的代码没过"毫无关系），短码 + 原因让
        部署者一眼知道该去修 Gitea 还是修代码。
        """
        return JSONResponse(
            status_code=503,
            content={"detail": "forge_unavailable", "reason": str(exc)[:300]},
        )

    @app.exception_handler(RemoteBranchMissingError)
    async def _no_remote_branch(
        request: Request, exc: RemoteBranchMissingError
    ) -> JSONResponse:
        """远端还没有学员的 dev 分支，所以开不了 PR。

        409（不是 500）：**预期内**的普通状况——学员只是还没 push。带着分支名回去，
        前端才能说清"你要先 push 哪个分支"。
        """
        return JSONResponse(
            status_code=409,
            content={
                "detail": "remote_branch_missing",
                "repo": exc.repo,
                "branch": exc.branch,
            },
        )

    @app.exception_handler(GradingUnavailableError)
    async def _grading_down(request: Request, exc: GradingUnavailableError) -> JSONResponse:
        """阅卷**跑不起来**：缺 maven、工作区没了、ticket 没声明 test_group。

        503 而不是 500——"这台机器暂时没条件跑"不是服务坏了；更要紧的是**不能**把它做成
        ``passed=False``，那等于把平台配置问题栽给学员（§7.1 / D3）。
        """
        return JSONResponse(
            status_code=503,
            content={"detail": "grading_unavailable", "reason": str(exc)[:300]},
        )


def create_app(
    *,
    content_dir: Path,
    forge_source: Path | None = None,
    forge_base: Path | None = None,
    grading: GradingBackend | None = None,
    grading_reset: bool | None = None,
    forge: ForgeBackend | None = None,
) -> FastAPI:
    app = FastAPI(title="bagu-trainer A′", version=PLATFORM_VERSION)
    store, track, tickets, service = build_service(content_dir)
    base = forge_base or (content_dir.parent / "_workspaces")
    # ref 布局来自内容：stage 分支按阶段命名，不拿 ticket id 当分支名（§C.5）。
    forge = build_forge(
        source_repo=forge_source or content_dir,
        base_dir=base,
        stage_refs=load_stage_refs(content_dir),
        backend=forge,
    )
    # 后端在**启动时**定下来（配错了立刻炸），而不是每个请求再解析一次环境变量。
    backend = resolve_grading_backend(grading)
    reset_infra = resolve_grading_reset(grading_reset)

    app.state.store = store
    app.state.track = track
    app.state.tickets = tickets
    # 看板按 stage 目录名书写而 ticket 按 identity.id 标识，故发卷路由需要这份索引。
    app.state.stage_tickets = load_ticket_dirs(content_dir)
    app.state.service = service
    app.state.forge = forge
    app.state.forge_backend = forge_backend_name(forge)
    app.state.grading_runner = lambda scenario: grading_runner(
        scenario, backend=backend, reset_infra=reset_infra
    )
    app.state.grading_backend = backend
    app.state.grading_reset = reset_infra
    app.state.review_engine = review_engine

    @app.get("/meta")
    def meta() -> dict[str, object]:
        """前端据此决定"哪些环节其实是占位"。

        桩/真后端必须**逐项**如实上报（`grading_backend` / `forge_backend`）：一个笼统的"模拟"
        徽标会把真阅卷也标成假的（§15 风险 5）。
        """
        return {
            "platform_version": PLATFORM_VERSION,
            "grading_backend": app.state.grading_backend,
            "grading_reset": app.state.grading_reset,
            "forge_backend": app.state.forge_backend,
        }

    _register_exception_handlers(app)

    from .routes import progress, workspace
    from .routes import track as track_routes

    app.include_router(track_routes.router)
    app.include_router(progress.router)
    app.include_router(workspace.router)
    return app
