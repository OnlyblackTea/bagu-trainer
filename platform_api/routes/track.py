"""发卷路由：渲染 Track 固定内容（设计 spec §C.3/§C.5）。

内容是 Track 固定的（作者写好的 ticket.yaml + stage 分支），**不经 LLM**。防剧透（D2）：
本路由只暴露工单内容（problem/concepts/files_to_change/hints），**不暴露解法分支**——解法
只在 workspace.diff 里、且仅对已 provision 的实例可达。

``manifest.stages`` 是**看板清单**而非 ticket 清单：它可以包含"只上榜、不出卷"的 stage
（真 Track 的 ``stage-00-onboarding`` 即如此，见 Plan 1 Task 0）。看板按目录名书写，而
ticket 的权威身份是 ``identity.id``，两者允许不同，故经 ``stage_tickets``（目录名 → ticket）
解析，缺失即 ``board_only``。

**多 Track 下 ticket id 只在 Track 内唯一**：两个 Track 都可以有 ``db-stage-01``。权威地址是
``GET /tracks/{track_id}/tickets/{ticket_id}``；扁平的 ``GET /tickets/{ticket_id}`` 保留给
单 Track 部署与旧客户端，一旦撞名就回 409 ``ticket_id_ambiguous`` 并把候选 Track 列出来——
**不猜**哪个才是用户想看的。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from platform_service.models import TicketSpec

from ..deps import TrackBundle
from ._resolve import bundle_for_track

router = APIRouter(tags=["track"])

ASSESSED = "assessed"
BOARD_ONLY = "board_only"


def _track_summary(bundle: TrackBundle) -> dict[str, Any]:
    """`GET /tracks` 的一条：只给"选方向"需要的信息，不含任何 ticket 正文。"""
    track = bundle.track
    assessed = sum(
        1 for name in track.manifest.stages if bundle.stage_tickets.get(name) is not None
    )
    return {
        "id": track.id,
        "name": track.name,
        "language": track.language,
        "trust_level": track.trust_level.value,
        "stages": list(track.manifest.stages),
        "assessed_stages": assessed,
        "board_only_stages": len(track.manifest.stages) - assessed,
    }


def _stage_out(stage_name: str, ticket: TicketSpec | None) -> dict[str, Any]:
    """一个看板条目；``ticket_id`` 是权威身份（``identity.id``），不是目录名。"""
    if ticket is None:
        return {
            "stage": stage_name,
            "kind": BOARD_ONLY,
            "ticket_id": None,
            "stage_index": None,
            "title": None,
            "prerequisites": [],
        }
    return {
        "stage": stage_name,
        "kind": ASSESSED,
        "ticket_id": ticket.identity.id,
        "stage_index": ticket.identity.stage,
        "title": ticket.identity.title,
        "prerequisites": list(ticket.progression.prerequisites),
    }


def _ticket_out(ticket: TicketSpec) -> dict[str, Any]:
    correctness = ticket.grader.correctness
    return {
        "id": ticket.identity.id,
        "stage": ticket.identity.stage,
        "title": ticket.identity.title,
        "ticket_version": ticket.identity.ticket_version,
        "problem": ticket.brief.problem,
        "concepts": list(ticket.brief.concepts),
        "files_to_change": list(ticket.scope.files_to_change),
        "hints": [{"id": h.id, "title": h.title, "text": h.text} for h in ticket.hints],
        "grader": {
            "test_group": correctness.test_group,
            "canonical_test": correctness.canonical_test,
            "expect": correctness.expect,
        },
    }


@router.get("/tracks")
def list_tracks(request: Request) -> list[dict[str, Any]]:
    """所有已部署 Track。这是"步骤 1 选方向"的数据源，故必须列出**全部**。"""
    return [_track_summary(b) for b in request.app.state.tracks.values()]


@router.get("/tracks/{track_id}")
def get_track(track_id: str, request: Request) -> dict[str, Any]:
    bundle = bundle_for_track(request, track_id)
    return {
        **_track_summary(bundle),
        "stages": [
            _stage_out(name, bundle.stage_tickets.get(name))
            for name in bundle.track.manifest.stages
        ],
    }


@router.get("/tracks/{track_id}/tickets/{ticket_id}")
def get_track_ticket(track_id: str, ticket_id: str, request: Request) -> dict[str, Any]:
    """权威的取卷地址：Track 显式给出，故不存在撞名歧义。"""
    bundle = bundle_for_track(request, track_id)
    ticket = bundle.tickets.get(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="ticket_not_found")
    return {**_ticket_out(ticket), "track": track_id}


@router.get("/tickets/{ticket_id}")
def get_ticket(ticket_id: str, request: Request) -> dict[str, Any]:
    """扁平取卷（单 Track 部署与旧客户端用）。撞名时 409，不猜。"""
    hits = [
        bundle
        for bundle in request.app.state.tracks.values()
        if ticket_id in bundle.tickets
    ]
    if not hits:
        raise HTTPException(status_code=404, detail="ticket_not_found")
    if len(hits) > 1:
        raise HTTPException(status_code=409, detail="ticket_id_ambiguous")
    bundle = hits[0]
    return {**_ticket_out(bundle.tickets[ticket_id]), "track": bundle.track.id}
