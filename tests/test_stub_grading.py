"""StubGradingRunner 的双分支 fixture（pass/fail）+ GradingRunner 缝结构化满足。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from platform_service.grading import GradingRunner
from platform_service.models import InstanceState, TicketInstance, TicketSpec
from platform_service.stubs import StubGradingRunner


def _instance(ticket: TicketSpec) -> TicketInstance:
    return TicketInstance(
        id="ti-1",
        learner="deployer",
        ticket_id=ticket.identity.id,
        ticket_version=ticket.identity.ticket_version,
        engine_version="a-prime-0.1.0",
        test_version=ticket.grader.correctness.test_version,
        state=InstanceState.IN_PROGRESS,
    )


def test_pass_scenario_reports_green(ticket_factory: Any) -> None:
    ticket = TicketSpec.model_validate(ticket_factory())
    report = StubGradingRunner(scenario="pass").grade(
        instance=_instance(ticket), ticket=ticket, workspace=Path(".")
    )
    assert report.correctness.passed is True
    assert report.correctness.test_group == ticket.grader.correctness.test_group
    assert report.learner == "deployer"
    assert report.ticket == ticket.identity.id


def test_fail_scenario_reports_red(ticket_factory: Any) -> None:
    ticket = TicketSpec.model_validate(ticket_factory())
    report = StubGradingRunner(scenario="fail").grade(
        instance=_instance(ticket), ticket=ticket, workspace=Path(".")
    )
    assert report.correctness.passed is False


def test_stub_structurally_satisfies_protocol() -> None:
    assert isinstance(StubGradingRunner(), GradingRunner)
