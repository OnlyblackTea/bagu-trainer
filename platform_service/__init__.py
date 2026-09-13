"""Platform control plane (§2.1) — the deployer's self-study / drill loop.

This package is the platform side of the three-layer topology. It reaches the engine
ONLY through ``orchestrator.prar_bridge`` (GPL boundary, §6.6), runs ZERO grading
compute and forces ZERO LLM cost (D3 — grading runs on the learner's machine; any LLM
use is the deployer's own key). It is a practice ground and a seckill-lab extension
that admits community Track contributions — there are no classes, rankings or scores
(removed in v0.4).

Implemented — pure, offline, unit-tested domain logic:

* ``models``         — the v0.4 domain: TicketSpec/TrackManifest content projections
                       plus runtime entities. ``extra="forbid"`` rejects every field
                       the v0.4 contraction removed (fixture_seed/signature/score/...).
* ``content_loader`` — YAML -> JSON-Schema (Draft 2020-12) -> typed models (§4.2/§4.3).
* ``store``          — the persistence seam: a ``Store`` Protocol + ``InMemoryStore``.
* ``lifecycle``      — the TicketInstance state machine (§9.2): locked -> available ->
                       in_progress -> submitted -> grading -> passed | failed, plus
                       retry (failed -> in_progress) and re-issue (stale + re-anchor).
* ``unlock``         — the prerequisites-DAG evaluation that flips dependents to
                       available once a submission passes (§9.2).
* ``acceptance``     — the deterministic, auditable gate (§11.3), decoupled from the
                       advisory AI Review (§11.2) so unlock never hinges on an LLM.
* ``admission``      — the static half of ``paper validate-track`` (§4.6): schema,
                       skeleton files, bagu-map anchors, runtime digest-pinning,
                       trust_level, the D1 role-reversal invariant, spoiler filter and
                       an injected discriminance runner.
* ``service``        — ``PlatformService``: the facade threading the above into the
                       end-to-end enroll -> start -> submit -> review -> decide loop.

Implemented — engine-integration layer (the v0.2.0 payoff). Each drives ``prar_bridge``
through an *injected* engine Protocol, so it imports neither orchestrator nor the engine
and is unit-tested offline against a fake (GPL boundary intact):

* ``review_diff``    — diff prep for the AI Review (契约三): ``git diff`` with the default
                       ``a/`` ``b/`` prefix plus the 120k whole-file volume preflight that
                       catches the first-file-overflow trap before any token is burned.
* ``ai_review``      — AI-Review *generation* (§6.5/§11.2): register the submission, call
                       ``POST /reviews`` by ``submission_id`` (XOR), and faithfully persist
                       the response — the anchor triple, ``dimension_findings`` and
                       ``truncated_files`` — since §5 exposes no GET /reviews.
* ``mentor``         — ``MentorSession``: the 发卷 -> 接卷 -> 开发 arc (§6.2/§6.3) over a
                       human-actor session, entering ACTING *without* an Executor and never
                       connecting ``ws_act`` (D1 is structural — the Protocol names no
                       executor method). ``orchestrator.session_pool`` opens + self-attests
                       those mentor sessions (``actor="human"`` + external workspace).

Pending — needs HTTP, a real DB, external infra or an open decision (out of scope here):
``paper grade`` grading *execution* (BLOCKED on appendix C Q2 — the ``paper_cli`` form is
undecided), FastAPI routers (``[service]`` extra), a SQLAlchemy/PostgreSQL ``Store``, gitea
fork/PR plumbing (incl. TODO 4 — write ``scratch/`` into the student repo's ``.gitignore``
at fork/instantiation), and LearnerProfile (§10).
"""
