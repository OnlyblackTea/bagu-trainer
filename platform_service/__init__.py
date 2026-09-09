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

Pending — needs the engine, HTTP or a real DB (out of scope for this slice):
MentorSession and AI-Review *generation* plus ``paper grade`` grading *execution* (all
via ``prar_bridge``), FastAPI routers (``[service]`` extra), a SQLAlchemy/PostgreSQL
``Store``, gitea fork/PR plumbing, the ``paper_cli`` wrappers, and LearnerProfile (§10).
"""
