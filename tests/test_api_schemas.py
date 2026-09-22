"""请求 DTO 的 extra=forbid、instance_out 注入 gate 文案。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from platform_api.schemas import EnrollRequest, ReviewRequest, SubmitRequest, instance_out
from platform_service.models import InstanceState, TicketInstance


def _inst(state: InstanceState) -> TicketInstance:
    return TicketInstance(
        id="ti-1",
        learner="deployer",
        track="database-bagu",
        ticket_id="db-stage-00",
        ticket_version=1,
        engine_version="a-prime-0.1.0",
        test_version=1,
        state=state,
    )


def test_enroll_request_rejects_extra() -> None:
    with pytest.raises(ValidationError):
        EnrollRequest.model_validate({"learner": "x", "bogus": 1})


def test_submit_and_review_defaults() -> None:
    assert SubmitRequest().pr_url is None
    assert ReviewRequest().dimensions is None


def test_instance_out_carries_state_and_gate() -> None:
    out = instance_out(_inst(InstanceState.FAILED))
    assert out["state"] == "failed"
    assert "retry" in out["gate"]


def test_gate_text_per_state() -> None:
    assert "通过" in instance_out(_inst(InstanceState.PASSED))["gate"]
    assert "未解锁" in instance_out(_inst(InstanceState.LOCKED))["gate"]


def test_every_state_has_a_gate_instruction() -> None:
    """gate 文案是 UI 的"下一步做什么"——缺一个状态就会 500，必须全覆盖。"""
    for state in InstanceState:
        assert instance_out(_inst(state))["gate"]
