"""Learner-device client (§7.2). SCAFFOLD ONLY — form is provisional (design Q2:
pure CLI vs a light local Web UI reusing the prar-agent frontend).

Intended commands (tracked in the design doc; not yet implemented):

* ``paper enroll``  — pick a track/stage, check prerequisites, start a TicketInstance.
* ``paper up``      — bring up the track's local infra (reuse seckill-lab docker/:
                      MySQL@13306 + Redis@16379) via the track runtime.compose.
* ``paper dev``     — open the workspace for the forked skeleton.
* ``paper mentor``  — talk to the AI mentor sub-loop for hints/思路 (NEVER代写; D1).
* ``paper grade``   — inject canonical tests at runtime and run them LOCALLY,
                      emitting a structured grading-report.json (§7.3, §7.4). The
                      report is an UNTRUSTED self-claim with provenance +
                      runner_trust, not a verdict (§7.4).
* ``paper submit``  — fill the defense template, attach the grading report, open the PR.
* ``paper sim``     — run the concurrency/load simulator (Stage3+, §7.5).

All grading happens on this device (D3). The client talks to the platform center
over HTTP and to the engine ONLY through orchestrator.prar_bridge (GPL boundary).
"""
