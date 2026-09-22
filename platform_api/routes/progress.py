"""生命周期路由（设计 spec §C.3）：enroll → start → submit → review → decide → retry。

业务全在 PlatformService；路由是薄适配 + gate 指示。scenario 查询参数选阅卷/评审桩的
fixture 分支（pass/fail、approve/request_changes），让前端能演示 decide 两条分支。

**多 Track**：实例自带 ``track`` 戳，所以除了 ``enroll`` 需要显式指定 Track，其余入口都能从
实例反查出该用哪个 Track 的 ticket / forge。``enroll`` 的 ``track`` 可省略——只有一个已部署
Track 时省略是安全默认；有多个又不说，则 400 ``enroll_needs_track``（猜错了等于把学员报进
另一个 Track，比报错糟得多）。
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from platform_service.ai_review import generate_review
from platform_service.review_diff import generate_diff

from ..jobs import ERROR_STATUS
from ..schemas import (
    EnrollRequest,
    ReviewRequest,
    SubmitRequest,
    attempt_out,
    grading_run_out,
    instance_out,
)
from ._resolve import bundle_for_instance, bundle_for_track, must_instance

router = APIRouter(tags=["progress"])

DEFAULT_DIMENSIONS = ["correctness", "readability", "test_coverage", "security"]
GradingScenario = Literal["pass", "fail"]
ReviewScenario = Literal["approve", "request_changes"]


@router.post("/enrollments", status_code=201)
def enroll(body: EnrollRequest, request: Request) -> dict[str, Any]:
    state = request.app.state
    track_id = body.track
    if track_id is None:
        if len(state.tracks) != 1:
            # 多 Track 又不说报哪个：不猜（猜错就把学员报进了另一个 Track 的 DAG）。
            raise HTTPException(status_code=400, detail="enroll_needs_track")
        track_id = next(iter(state.tracks))
    bundle = bundle_for_track(request, track_id)
    enrollment = bundle.service.enroll(body.learner)
    instances = state.store.list_instances(learner=body.learner, track=track_id)
    return {
        "enrollment": enrollment.model_dump(mode="json"),
        "instances": [instance_out(i) for i in instances],
    }


@router.get("/instances")
def list_instances(
    request: Request, learner: str, track: str | None = None
) -> list[dict[str, Any]]:
    """看板数据源。``track`` 省略时给**全部** Track 的实例（每条都带自己的 ``track``），
    这样"我这届都干了啥"是一屏能看全的，而不是逼前端按 Track 循环请求。
    """
    return [
        instance_out(i)
        for i in request.app.state.store.list_instances(learner=learner, track=track)
    ]


@router.get("/instances/{instance_id}")
def get_instance(instance_id: str, request: Request) -> dict[str, Any]:
    return instance_out(must_instance(request, instance_id))


@router.post("/instances/{instance_id}/start")
def start(instance_id: str, request: Request) -> dict[str, Any]:
    instance = must_instance(request, instance_id)
    return instance_out(bundle_for_instance(request, instance).service.start(instance_id))


@router.post("/instances/{instance_id}/submit", status_code=202)
def submit(
    instance_id: str,
    request: Request,
    body: SubmitRequest,
    scenario: GradingScenario = "fail",
) -> dict[str, Any]:
    """**异步**提交：立刻 202 + 进度，阅卷在后台单线程跑（§C.3 步骤 6）。

    真阅卷要重置容器再 ``mvn test``，几十秒起步；同步做的话这一个请求就干挂着，中间没有
    任何"现在在干什么"。所以这里只做三件事：铸造 submission（状态 ``submitted``）、登记一个
    后台任务、把进度交回去。报告由后台任务跑完后经 ``service.attach_report`` 落库，学员轮询
    ``GET /submissions/{id}/progress``。

    ``scenario`` 仍然在提交时选定：桩按它分叉，真阅卷不看它——这个差别对调用方是透明的，
    但**桩确实会立刻答完**，所以进度条在桩下是一闪而过的（不编造等待时间）。
    """
    state = request.app.state
    instance = must_instance(request, instance_id)
    bundle = bundle_for_instance(request, instance)
    ticket = bundle.tickets[instance.ticket_id]
    workspace = bundle.forge.provision(instance)
    submission = bundle.service.submit(
        instance_id, pr_url=body.pr_url, defense=body.defense
    )
    run = state.grading_jobs.start(
        submission_id=submission.id,
        instance=instance,
        ticket=ticket,
        track=bundle.track,
        workspace=workspace.path,
        scenario=scenario,
        on_done=bundle.service.attach_report,
    )
    return {
        "submission": submission.model_dump(mode="json"),
        "progress": grading_run_out(run, live=True),
    }


@router.get("/submissions/{submission_id}/progress")
def grading_progress(submission_id: str, request: Request) -> dict[str, Any]:
    """阅卷进度（§C.3 步骤 6）——**读库**，不读内存。

    进度与历史是同一份数据（:class:`~platform_service.models.GradingRun`），所以这里永远和
    ``GET /instances/{id}/history`` 一致，也不会出现"接口说 done、历史里还是 running"。

    三种"不是 running"要分清：

    * ``unknown`` —— 压根没有运行记录（本特性之前的历史提交，或提交没真正开始）；
    * ``stalled`` —— 有活跃记录但**不在本进程手里**：服务重启过，或它由另一个平台实例在跑
      （见 :mod:`platform_api.jobs` 关于 owner 的说明）。措辞不替它下结论；
    * ``interrupted`` —— 本进程清扫过自己的孤儿记录，即确定这次阅卷被重启打断了。
    """
    state = request.app.state
    if state.store.get_submission(submission_id) is None:
        raise HTTPException(status_code=404, detail="submission_not_found")
    run = state.store.get_grading_run(submission_id)
    view = grading_run_out(run, live=state.grading_jobs.is_live(submission_id))
    if run is None:
        # 没有运行记录时，报告的到场情况仍如实给出（老提交可能只有报告）。
        view["report_ready"] = state.store.get_report(submission_id) is not None
        view["submission_id"] = submission_id
    return view


@router.get("/instances/{instance_id}/history")
def instance_history(instance_id: str, request: Request) -> dict[str, Any]:
    """该实例的**全部**尝试：提交 → 运行 → 报告 → 评审 → 判定，以及与上一次的比对（§11）。

    学员刷同一道题会提交很多次，而平台此前只留得住"当前状态"——"上次挂在哪条、这次动没动它"
    无从回答。这里把每次尝试连同比对一起给出来。第一次尝试的 ``delta`` 是 ``null``（没有"上一次"）。
    """
    instance = must_instance(request, instance_id)
    attempts = bundle_for_instance(request, instance).service.history(instance_id)
    return {
        "instance_id": instance_id,
        "ticket_id": instance.ticket_id,
        "track": instance.track,
        "learner": instance.learner,
        "attempts": [attempt_out(a) for a in attempts],
    }


def _job_failure(job: Any) -> JSONResponse | None:
    """后台任务失败了 ⇒ 原样报出**当初**那个短码与状态码（§7.1：环境问题不栽给学员）。"""
    if job is None or job.status != "failed" or not job.error_code:
        return None
    return JSONResponse(
        status_code=ERROR_STATUS.get(job.error_code, 500),
        content={"detail": job.error_code, "reason": job.error_reason},
    )


@router.get("/submissions/{submission_id}/report")
def grading_report(submission_id: str, request: Request) -> Any:
    """阅卷报告（§7.4）：**不可信自报告**，但学员必须看得到——否则真阅卷等于没做。

    报告由提交后的后台任务产生，所以这里有三种"还没有"：

    * 阅卷正排队/在跑 ⇒ 404 ``report_not_ready``（**不是** ``report_not_found``：
      前者是"等一会儿"，后者是"这条提交不会有报告了"——两种提示让学员做的事完全不同）；
    * 阅卷失败（缺 maven、工作区没了……）⇒ 按失败短码报 503/500，绝不伪装成"没过"；
    * 压根没有这条提交的阅卷记录 ⇒ 404 ``report_not_found``。

    报告本身带 ``provenance.host_env``（maven/java 版本、退出码、耗时、**这次有没有重置环境**）
    与**逐条测试**的真实结果，正是学员定位失败所需的信息。
    """
    state = request.app.state
    report = state.store.get_report(submission_id)
    if report is not None:
        return report.model_dump(mode="json")

    failure = _job_failure(state.grading_jobs.get(submission_id))
    if failure is not None:
        return failure
    if state.grading_jobs.get(submission_id) is not None:
        raise HTTPException(status_code=404, detail="report_not_ready")
    raise HTTPException(status_code=404, detail="report_not_found")


@router.post("/submissions/{submission_id}/review")
async def review(
    submission_id: str,
    request: Request,
    body: ReviewRequest,
    scenario: ReviewScenario = "request_changes",
) -> dict[str, Any]:
    """跑评审桩 → Review（经既有 ``ReviewEngine`` 缝），再 ``service.review`` 落库。"""
    state = request.app.state
    submission = state.store.get_submission(submission_id)
    if submission is None:
        raise HTTPException(status_code=404, detail="submission_not_found")
    instance = state.store.get_instance(submission.instance_id)
    bundle = bundle_for_instance(request, instance)
    workspace = bundle.forge.provision(instance)
    diff = generate_diff(workspace.path, base=workspace.base_ref, head=workspace.head_ref)
    engine = state.review_engine(scenario)
    review_obj = await generate_review(
        engine,
        submission.instance_id,
        diff=diff,
        dimensions=body.dimensions or DEFAULT_DIMENSIONS,
        defense_text=submission.defense_text,
    )
    saved = bundle.service.review(submission_id, review_obj)
    return saved.model_dump(mode="json")


@router.post("/submissions/{submission_id}/decide")
def decide(submission_id: str, request: Request) -> dict[str, Any]:
    """确定性门（真门）：pass ⇒ 解锁后继；fail ⇒ 走 retry。回带全量实例状态。"""
    state = request.app.state
    submission = state.store.get_submission(submission_id)
    if submission is None:
        raise HTTPException(status_code=404, detail="submission_not_found")
    instance = state.store.get_instance(submission.instance_id)
    bundle = bundle_for_instance(request, instance)
    decision = bundle.service.decide(submission_id)
    instances = state.store.list_instances(learner=instance.learner)
    return {
        "decision": decision.model_dump(mode="json"),
        "instances": [instance_out(i) for i in instances],
    }


@router.post("/instances/{instance_id}/retry")
def retry(instance_id: str, request: Request) -> dict[str, Any]:
    instance = must_instance(request, instance_id)
    return instance_out(bundle_for_instance(request, instance).service.retry(instance_id))
