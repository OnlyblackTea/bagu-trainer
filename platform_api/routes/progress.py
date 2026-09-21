"""生命周期路由（设计 spec §C.3）：enroll → start → submit → review → decide → retry。

业务全在 PlatformService；路由是薄适配 + gate 指示。scenario 查询参数选阅卷/评审桩的
fixture 分支（pass/fail、approve/request_changes），让前端能演示 decide 两条分支。
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request

from platform_service.ai_review import generate_review
from platform_service.models import TicketInstance
from platform_service.review_diff import generate_diff

from ..schemas import EnrollRequest, ReviewRequest, SubmitRequest, instance_out

router = APIRouter(tags=["progress"])

DEFAULT_DIMENSIONS = ["correctness", "readability", "test_coverage", "security"]
GradingScenario = Literal["pass", "fail"]
ReviewScenario = Literal["approve", "request_changes"]


def _must_instance(request: Request, instance_id: str) -> TicketInstance:
    instance = request.app.state.store.get_instance(instance_id)
    if instance is None:
        raise HTTPException(status_code=404, detail="instance_not_found")
    return instance


@router.post("/enrollments", status_code=201)
def enroll(body: EnrollRequest, request: Request) -> dict[str, Any]:
    enrollment = request.app.state.service.enroll(body.learner)
    instances = request.app.state.store.list_instances(learner=body.learner)
    return {
        "enrollment": enrollment.model_dump(mode="json"),
        "instances": [instance_out(i) for i in instances],
    }


@router.get("/instances")
def list_instances(request: Request, learner: str) -> list[dict[str, Any]]:
    return [instance_out(i) for i in request.app.state.store.list_instances(learner=learner)]


@router.get("/instances/{instance_id}")
def get_instance(instance_id: str, request: Request) -> dict[str, Any]:
    return instance_out(_must_instance(request, instance_id))


@router.post("/instances/{instance_id}/start")
def start(instance_id: str, request: Request) -> dict[str, Any]:
    return instance_out(request.app.state.service.start(instance_id))


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
    instance = _must_instance(request, instance_id)
    ticket = state.tickets[instance.ticket_id]
    workspace = state.forge.provision(instance)
    report = state.grading_runner(scenario).grade(
        instance=instance, ticket=ticket, workspace=workspace.path
    )
    submission = state.service.submit(
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
    workspace = state.forge.provision(instance)
    diff = generate_diff(workspace.path, base=workspace.base_ref, head=workspace.head_ref)
    engine = state.review_engine(scenario)
    review_obj = await generate_review(
        engine,
        submission.instance_id,
        diff=diff,
        dimensions=body.dimensions or DEFAULT_DIMENSIONS,
        defense_text=submission.defense_text,
    )
    saved = state.service.review(submission_id, review_obj)
    return saved.model_dump(mode="json")


@router.post("/submissions/{submission_id}/decide")
def decide(submission_id: str, request: Request) -> dict[str, Any]:
    """确定性门（真门）：pass ⇒ 解锁后继；fail ⇒ 走 retry。回带全量实例状态。"""
    state = request.app.state
    submission = state.store.get_submission(submission_id)
    if submission is None:
        raise HTTPException(status_code=404, detail="submission_not_found")
    decision = state.service.decide(submission_id)
    instance = state.store.get_instance(submission.instance_id)
    instances = state.store.list_instances(learner=instance.learner)
    return {
        "decision": decision.model_dump(mode="json"),
        "instances": [instance_out(i) for i in instances],
    }


@router.post("/instances/{instance_id}/retry")
def retry(instance_id: str, request: Request) -> dict[str, Any]:
    return instance_out(request.app.state.service.retry(instance_id))
