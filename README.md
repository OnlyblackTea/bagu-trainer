# 发卷平台 · Paper Platform

> Open-source, self-hosted **八股 (interview-prep) practical-exam platform**.
> The AI is the **manager / mentor / reviewer**; the **human learner writes the code**.
> Grading runs canonical tests **on the learner's own machine**. Flow is
> GitHub-like: `fork → develop → PR → pass → unlock next`.

Engine = [`prar-agent`](https://github.com/OnlyblackTea/prar-agent) (git submodule).
First content track = [`seckill-lab`](https://github.com/OnlyblackTea/seckill-lab) (database 八股).
Authoritative design: [`paper-platform-design.md`](../paper-platform-design.md) (v0.3).

---

## Quick start (contributors)

```bash
# Clone WITH the engine submodule in one shot — this is the line to remember:
git clone --recursive https://github.com/OnlyblackTea/paper-platform.git

# Already cloned without --recursive? Pull the submodule in:
cd paper-platform
make bootstrap          # == git submodule update --init --recursive
```

The `prar-agent/` directory will be **empty** until you do one of the above.
The submodule is pinned to a public tag (currently `v0.1.0`); upgrading the
engine is an explicit tag bump, never an automatic sync — so a given exam is
always reproducible against the engine version it was issued with.

---

## Three layers (and why they are separate repos)

| Layer | What it is | Repo | License |
|-------|-----------|------|---------|
| **Content** | track / stage / ticket + skeleton + canonical tests + 八股 map + hints + reference solutions | `seckill-lab` (and future community tracks) | **MIT** |
| **Engine** | AI manager / mentor / reviewer + sandbox + git checkpoint + memory | `prar-agent` (submodule) | **GPL-3.0-or-later** |
| **Platform** | multi-session orchestration, fork/PR, grading, unlock, learner profile, board | **this repo** | **Apache-2.0** |

### License topology & the GPL boundary (read before touching `orchestrator/`)

This repo is **Apache-2.0**. That choice rests on one discipline (plus the legal
caveat below):

> **Paper Platform talks to prar-agent exclusively over its out-of-process
> HTTP/WS API, and every such call goes through the single adapter
> [`orchestrator/prar_bridge.py`](orchestrator/prar_bridge.py). No platform
> module imports engine internals (`core/*`, `llm/*`, `memory/*`, `tools/*`).**

That arm's-length boundary is **designed to** keep the two as **separate
programs** and to **reduce the risk** that prar-agent's GPL copyleft reaches this
repo. Collapse it (import engine Python symbols in-process) and Paper Platform
almost certainly becomes a GPL-3.0 derivative. The same boundary is also the
*engineering* win: an engine upgrade only ever touches the adapter. It is guarded
by [`contracts/test_boundary.py`](contracts/test_boundary.py).

> **Legal caveat.** Whether the two are in fact "mere aggregation" rather than a
> single combined program is a **legal determination** that depends on the actual
> distribution structure — it cannot be concluded from "talks over HTTP" alone.
> This is an engineering risk judgment, **not legal advice**; confirm with
> qualified counsel before redistributing.

Full rationale: design §6.6 + §6.7.

---

## Design pillars (locked, non-reversible)

- **D1 — Role reversal.** The AI never writes the graded business code. In
  mentor mode the write/execute tools are disabled for `files_to_change`; the
  ACTING-phase Executor LLM is **not** invoked — the human is the actor.
- **D2 — Honor-system anti-spoiler.** Light friction only (runtime test
  injection, solutions not surfaced until you pass). No hard walls; open-source
  answers can't be hidden anyway.
- **D3 — Grading on the learner's compute.** The platform center runs **zero**
  grading compute and forces **zero** LLM cost; the deployer brings their own
  LLM key and the learner's machine runs the canonical tests.
- **D4 — prar-agent as submodule + single adapter.** Take-code axis = submodule;
  interact axis = out-of-process API via `prar_bridge.py` only.

---

## Repository layout

```
paper-platform/
├── LICENSE                 Apache-2.0 (this repo)
├── NOTICE                  attribution + the GPL-boundary statement
├── Makefile                bootstrap / test / lint / submodule bump
├── pyproject.toml
├── prar-agent/             ← git submodule, pinned to tag v0.1.0 (GPL-3.0)
├── orchestrator/           platform-side engine orchestration
│   ├── prar_bridge.py      ★ the ONLY file that knows prar-agent's API
│   └── session_pool.py     per-(learner, ticket) session multiplexing (§6.1)
├── platform_service/       FastAPI control plane: catalog/enrollment/lifecycle/unlock/rubric/gitea (§2.1)
├── paper_cli/              learner-device client: enroll/up/dev/mentor/grade/submit/sim (§7.2)
├── content/
│   └── schema/             track.schema.json + ticket.schema.json (§4.2/§4.3) — the contribution contract
├── contracts/              boundary-guard test + prar-agent API contract tests (gate every submodule bump)
└── docs/
```

---

## Status

This repository is at the **scaffold** stage — the architecture, boundaries,
schemas, and the single engine adapter are in place; the platform service,
paper-cli, and content admission gate are stubbed and tracked in the design doc.
Items that depend on a **prar-agent-side contract freeze** (mentor-mode tool
whitelist, ACTING-without-Executor session mode, AI-PR-review entry) are marked
`PENDING` in the adapter and are **not** invented ahead of the engine.

See design §14 for the phased roadmap and Appendix C for open questions
(paper-cli form Q2, report signing Q3, second track Q4, track/platform repo
split Q5).

---

## Contributing

- **Content (a new track / 八股 direction):** fork a track repo, draft with
  `paper new-track`, then pass `paper validate-track` (the admission gate checks
  test *discriminativeness*: red on the skeleton, green on the reference
  solution) and open a PR to the catalog. See design §4.
- **Engine changes:** open a PR against [`prar-agent`](https://github.com/OnlyblackTea/prar-agent)
  itself, then bump the submodule pointer here. Do **not** edit `prar-agent/`
  in-place from this repo.
- **Platform changes:** normal PRs here — under Apache-2.0.

## License

Apache-2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE).
