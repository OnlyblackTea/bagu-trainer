"""Learner-side client (design v0.5 §7.2). SCAFFOLD ONLY.

The client form is settled as **REST control plane + learner SPA** (``platform_api/`` +
``frontend/``); a CLI is an optional future convenience, not a requirement. "命令面即
端点表" — every command below is one REST call.

Intended commands (tracked in the design doc; NOT yet implemented):

* ``paper enroll``  — pick a track/stage, check prerequisites, start a TicketInstance.
* ``paper up``      — bring up the track's local infra (reuse e.g. seckill-lab docker/:
                      MySQL@13306 + Redis@16379) via the track runtime.compose.
* ``paper dev``     — open the workspace provisioned from the stage's ``base_ref``.
* ``paper grade``   — inject canonical tests at runtime and run them LOCALLY,
                      emitting a structured grading-report.json (§7.3, §7.4). The
                      report is an UNTRUSTED self-claim with provenance +
                      runner_trust, not a verdict (§7.4).
* ``paper submit``  — fill the defense template, attach the grading report, open the PR.
* ``paper sim``     — run the concurrency/load simulator (Stage3+, §7.5).

There is deliberately **no** ``paper mentor``: the platform has no mentor/executor
component — D1 is structural, not a permission flag (design §1.2). All grading happens
on this device (D3); the client reaches the platform over plain REST.
"""
