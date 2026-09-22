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

from platform_service.ai_review import generate_review
from platform_service.review_diff import generate_diff

from ..schemas import EnrollRequest, ReviewRequest, SubmitRequest, instance_out
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


@router.post("/instances/{instance_id}/submit")
def submit(
    instance_id: str,
    request: Request,
    body: SubmitRequest,
    scenario: GradingScenario = "fail",
) -> dict[str, Any]:
    """跑阅卷桩 → GradingReport，再由 ``service.submit`` 落到铸造的 submission 上。

    ``scenario`` 选 pass/fail fixture，让前端能演示 decide 两条分支。
    """
    state = request.app.state
    instance = must_instance(request, instance_id)
    bundle = bundle_for_instance(request, instance)
    ticket = bundle.tickets[instance.ticket_id]
    workspace = bundle.forge.provision(instance)
    report = state.grading_runner(scenario).grade(
        instance=instance,
        ticket=ticket,
        track=bundle.track,
        workspace=workspace.path,
    )
    submission = bundle.service.submit(
        instance_id, grading_report=report, pr_url=body.pr_url, defense=body.defense
    )
    return submission.model_dump(mode="json")


@router.get("/submissions/{submission_id}/report")
def grading_report(submission_id: str, request: Request) -> dict[str, Any]:
    """阅卷报告（§7.4）：**不可信自报告**，但学员必须看得到——否则真阅卷等于没做。

    报告在 ``submit`` 时产生；此前调用返回 404 ``report_not_found``。真阅卷下这里带
    ``provenance.host_env``（maven/java 版本、退出码、耗时）与**逐条测试**的真实结果，
    正是学员定位失败所需的信息。
    """
    report = request.app.state.store.get_report(submission_id)
    if report is None:
        raise HTTPException(status_code=404, detail="report_not_found")
    return report.model_dump(mode="json")


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
