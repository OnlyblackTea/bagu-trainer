"""FastAPI 应用工厂 + 异常映射（设计 spec §C.3/§C.8）。

``create_app`` 是组合根入口：建 store/service/forge/后端工厂挂到 ``app.state``，注册把域异常
翻成 ``{"detail": "<短码>"}`` 的处理器，并挂三组路由。纯 REST，无 SSE/WS（无 AI 流可推）。

阅卷后端由 ``grading`` 参数（或 ``BAGU_GRADING``）选定：``stub`` 离线可跑，``docker`` 在本机
真跑 Track 的 canonical 测试（见 :mod:`platform_api.deps`）。持久化后端由 ``store`` /
``store_backend``（或 ``BAGU_STORE``）选定：``memory`` 重启即失忆，``postgres`` 落库。
"""

from __future__ import annotations

import subprocess
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from platform_service.ai_review import ReviewDiffTooLargeError
from platform_service.errors import GateNotReadyError, NotFoundError, WorkspacePathError
from platform_service.forge import ForgeUnavailableError, WorkspaceRefMissingError
from platform_service.lifecycle import IllegalTransitionError

from .deps import (
    PLATFORM_VERSION,
    GradingBackend,
    StoreBackend,
    build_store,
    build_tracks,
    grading_runner,
    resolve_grading_backend,
    resolve_grading_reset,
    review_engine,
    store_backend_name,
    store_target_name,
)
from .grading_docker import GradingUnavailableError
from .jobs import GradingJobs, recover_interrupted_runs, resolve_instance_id
from .stubs_wiring import ForgeBackend, forge_backend_name


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

    @app.exception_handler(WorkspacePathError)
    async def _bad_workspace(request: Request, exc: WorkspacePathError) -> JSONResponse:
        """学员登记的目录用不了 → 422 ``workspace_path_invalid``。

        422 而不是 500：目录是他给的，问题在那一侧；也不是 404——**实例是好的**，只是那个路径不行。
        ``reason`` 带上细分口径（``not_a_directory`` / ``not_a_repo``），前端据此说清该怎么办。
        """
        return JSONResponse(
            status_code=422,
            content={"detail": "workspace_path_invalid", "reason": exc.reason},
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
    content_dir: Path | None = None,
    content_dirs: Sequence[Path] | None = None,
    forge_source: Path | None = None,
    forge_base: Path | None = None,
    grading: GradingBackend | None = None,
    grading_reset: bool | None = None,
    forge: ForgeBackend | None = None,
    store: Any = None,
    store_backend: StoreBackend | None = None,
    database_url: str | None = None,
    instance_id: str | None = None,
) -> FastAPI:
    """组装 app。``content_dir``（单个）与 ``content_dirs``（多个）二选一。

    多 Track 支持的形状是"一个 store + 每 Track 一个 :class:`TrackBundle`"（见
    :func:`~platform_api.deps.build_tracks`）：报名时指定 track，实例上盖 ``track`` 戳，
    此后每个请求都能从实例反查出该用哪个 Track 的 ticket / forge / stage refs。
    """
    @asynccontextmanager
    async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
        """启动时清扫孤儿阅卷记录；退出时收掉后台线程池。

        启动那一步是**必须**的：崩掉的进程没法给自己写墓志铭，卡在 ``running`` 的记录会在重启后
        一直撒谎说"正在阅卷"。清扫只动 ``owner`` 是本实例名下的记录（见 :mod:`platform_api.jobs`）。
        """
        recovered = recover_interrupted_runs(app.state.store, owner=jobs_owner)
        if recovered:
            print(f"清扫了 {recovered} 条被重启打断的阅卷记录（owner={jobs_owner}）")
        try:
            yield
        finally:
            app.state.grading_jobs.shutdown()

    app = FastAPI(title="bagu-trainer A′", version=PLATFORM_VERSION, lifespan=_lifespan)
    roots = list(content_dirs) if content_dirs is not None else [content_dir]
    if content_dir is not None and content_dirs is not None:
        raise ValueError("content_dir 与 content_dirs 只能给一个")
    if any(root is None for root in roots):
        raise ValueError("至少要给一个 Track 内容目录（content_dir 或 content_dirs）")
    # store 可以先建好传进来（测试常用），也可以只给 backend/dsn 让这里建。
    store = store if store is not None else build_store(store_backend, dsn=database_url)
    tracks = build_tracks(
        [root for root in roots if root is not None],
        store,
        forge_source=forge_source,
        forge_base=forge_base,
        forge=forge,
    )
    # 后端在**启动时**定下来（配错了立刻炸），而不是每个请求再解析一次环境变量。
    backend = resolve_grading_backend(grading)
    reset_infra = resolve_grading_reset(grading_reset)

    app.state.store = store
    app.state.store_backend = store_backend_name(store)
    app.state.store_target = store_target_name(store)
    app.state.tracks = tracks
    runner_for = lambda scenario: grading_runner(  # noqa: E731 - 见下：同一个来源喂两处
        scenario, backend=backend, reset_infra=reset_infra
    )
    app.state.grading_runner = runner_for
    app.state.grading_backend = backend
    app.state.grading_reset = reset_infra
    # 提交后的阅卷跑在后台（单线程排队），运行记录写穿到 store，进度经
    # GET /submissions/{id}/progress 查、历史经 GET /instances/{id}/history 查。
    jobs_owner = resolve_instance_id(instance_id)
    app.state.grading_jobs = GradingJobs(store, runner_for, owner=jobs_owner)
    app.state.instance_id = jobs_owner
    app.state.review_engine = review_engine
    # 多 Track 时逐个上报；单 Track 就是一个元素的 map。Forge 后端是**一个**开关，
    # 所以名字取自任一 bundle——但"这个 Track 的 forge 到底是不是真的"仍逐项可查。
    app.state.forge_backend = (
        forge_backend_name(next(iter(tracks.values())).forge) if tracks else "stub"
    )

    @app.get("/meta")
    def meta() -> dict[str, object]:
        """前端据此决定"哪些环节其实是占位"。

        桩/真后端必须**逐项**如实上报（`grading_backend` / `forge_backend`）：一个笼统的"模拟"
        徽标会把真阅卷也标成假的（§15 风险 5）。`store_backend` 同理——学员有权知道攒的进度
        是"重启就没"（memory）还是落了库（postgres）。
        """
        return {
            "platform_version": PLATFORM_VERSION,
            "grading_backend": app.state.grading_backend,
            "grading_reset": app.state.grading_reset,
            "forge_backend": app.state.forge_backend,
            "store_backend": app.state.store_backend,
            "store_target": app.state.store_target,
            "instance_id": app.state.instance_id,
        }

    _register_exception_handlers(app)

    from .routes import progress, workspace
    from .routes import track as track_routes

    app.include_router(track_routes.router)
    app.include_router(progress.router)
    app.include_router(workspace.router)
    return app
