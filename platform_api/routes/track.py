"""发卷路由：渲染 Track 固定内容（设计 spec §C.3/§C.5）。

内容是 Track 固定的（作者写好的 ticket.yaml + stage 分支），**不经 LLM**。防剧透（D2）：
本路由只暴露工单内容（problem/concepts/files_to_change/hints），**不暴露解法分支**——解法
只在 workspace.diff 里、且仅对已 provision 的实例可达。

``manifest.stages`` 是**看板清单**而非 ticket 清单：它可以包含"只上榜、不出卷"的 stage
（真 Track 的 ``stage-00-onboarding`` 即如此，见 Plan 1 Task 0）。看板按目录名书写，而
ticket 的权威身份是 ``identity.id``，两者允许不同，故经 ``app.state.stage_tickets``
（目录名 → ticket）解析，缺失即 ``board_only``。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from platform_service.models import TicketSpec

router = APIRouter(tags=["track"])

ASSESSED = "assessed"
BOARD_ONLY = "board_only"


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


@router.get("/tracks")
def list_tracks(request: Request) -> list[dict[str, Any]]:
    track = request.app.state.track
    return [
        {
            "id": track.id,
            "name": track.name,
            "language": track.language,
            "trust_level": track.trust_level.value,
            "stages": list(track.manifest.stages),
        }
    ]


@router.get("/tracks/{track_id}")
def get_track(track_id: str, request: Request) -> dict[str, Any]:
    state = request.app.state
    if state.track.id != track_id:
        raise HTTPException(status_code=404, detail="track_not_found")
    return {
        "id": state.track.id,
        "name": state.track.name,
        "language": state.track.language,
        "trust_level": state.track.trust_level.value,
        "stages": [
            _stage_out(name, state.stage_tickets.get(name))
            for name in state.track.manifest.stages
        ],
    }


@router.get("/tickets/{ticket_id}")
def get_ticket(ticket_id: str, request: Request) -> dict[str, Any]:
    ticket = request.app.state.tickets.get(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="ticket_not_found")
    correctness = ticket.grader.correctness
    return {
        "id": ticket.identity.id,
        "stage": ticket.identity.stage,
        "title": ticket.identity.title,
        "ticket_version": ticket.identity.ticket_version,
        "problem": ticket.brief.problem,
        "concepts": list(ticket.brief.concepts),
        "files_to_change": list(ticket.scope.files_to_change),
        "hints": [{"id": h.id, "text": h.text} for h in ticket.hints],
        "grader": {
            "test_group": correctness.test_group,
            "canonical_test": correctness.canonical_test,
            "expect": correctness.expect,
        },
    }
