"""Platform control plane (§2.1) — FastAPI service. SCAFFOLD ONLY.

Intended responsibilities (tracked in the design doc; not yet implemented):

* catalog        — list deployed tracks + stages + tickets, with trust_level (§4, §13).
* enrollment     — admit a learner, check prerequisites, create the Enrollment.
* lifecycle      — issue/track the TicketInstance state machine (§9.2):
                   locked -> available -> in_progress -> submitted -> grading ->
                   passed | failed.
* unlock         — the prerequisites-DAG evaluation that flips dependents to
                   available once an AcceptanceDecision passes (§9.2, §11.3).
* rubric         — store AI Review (advisory) and AcceptanceDecision (the
                   deterministic, auditable gate) separately (§11.2, §11.3).
* gitea          — fork the skeleton, open/track the submission PR.

The center runs ZERO grading compute and forces ZERO LLM cost (D3): grading runs
on the learner's machine, and any LLM use is the deployer's own key. This service
reaches the engine ONLY through orchestrator.prar_bridge (GPL boundary, §6.6).
"""
