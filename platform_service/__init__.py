"""Platform control plane (§2.1) — the deployer's self-study / drill loop.

This package is the platform side of the three-layer topology (content / toolchain /
platform). It runs ZERO grading compute and forces ZERO LLM cost (D3 — grading runs on
the learner's machine; any LLM use is the deployer's own key). It is a practice ground
and a seckill-lab extension that admits community Track contributions — there are no
classes, rankings or scores (removed in v0.4).

It is **engine-free** (D4): no AI sits on the critical path. Grading, git/PR and the
optional advisory review enter through three injected Protocols, so this layer imports
no concrete backend and is fully offline-testable.

Implemented — pure, offline, unit-tested domain logic:

* ``models``         — the v0.4/v0.5 domain: TicketSpec/TrackManifest content projections
                       plus runtime entities. ``extra="forbid"`` rejects every field
                       the v0.4 contraction removed (fixture_seed/signature/score/...).
* ``content_loader`` — YAML -> JSON-Schema (Draft 2020-12) -> typed models (§4.2/§4.3).
                       Ticket identity is ``identity.id``; stages are discovered by
                       globbing ``stages/*/ticket.yaml`` (§8.1).
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

The three seams — a Protocol for each backend, with the concrete implementation
wired in by the ``platform_api`` composition root:

* ``grading``        — ``GradingRunner``: run the track's canonical tests on the
                       learner's machine and return a ``GradingReport`` (§7).
* ``forge``          — ``ForgeClient``: provision a workspace from the stage refs
                       (§8.1) and describe the resulting PR (§8).
* ``ai_review``      — ``ReviewEngine``: the OPTIONAL advisory review (§11.2).
* ``review_diff``    — diff prep *for* that review: the whole-file volume preflight that
                       refuses an over-large diff before any backend is called.
* ``stubs``          — the three honest placeholders used until real backends exist.

Pending — needs HTTP, a real DB, external infra or an open decision (out of scope here):
real grading execution (Docker + JDK17 + Maven), a Gitea-backed ``ForgeClient``, a
SQLAlchemy/PostgreSQL ``Store``, an LLM-backed ``ReviewEngine`` (§11.2), and
LearnerProfile (§10).
"""
