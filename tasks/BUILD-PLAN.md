# chimera — Build Plan

> Cross-session tracker. Updated at the end of every session. Use this
> as the source of truth for "what's done vs. what's next."

**Status legend:**
- ✅ Done — implemented and tested
- 🟡 Scaffolded — structure in place, real implementation pending
- ⏳ In progress — being built now
- ⬜ Pending — not started

**Last updated:** 2026-05-08 (session 1 — initial scaffold + core build)

---

## Vision (single source of truth)

```
[ user's terminal AI CLI ]      ← shell (Claude Code, Codex, Gemini CLI)
         ↓ MCP
    [ chimera ]                  ← orchestrator — never makes API calls itself
         ↓ subprocess
[ terminal AI CLIs (any) ]      ← brain — also subprocess-only
```

**Three pillars:**
1. **Context resolver** (Séance + Scarlet + Serena) — minimize prompt tokens
2. **Runtime manager** (`chimera dev`) — dev server + Chrome DevTools + Postgres
3. **AI dispatcher** (AMR auto-router) — pick cheapest competent runner per task

**Audience:** the 80% of devs who paste files into Claude Code, hit subscription
limits, and don't manually compose Séance/Scarlet/Specter. Pitch:
*"chimera makes your terminal AI tool 5–10× more efficient. Zero config to start.
Local model fills the gaps for free."*

---

## Phase 0 — Foundations

| Item | Status | Notes |
|---|---|---|
| Monorepo scaffold | ✅ | commit `0b1d901` — uv workspace, 4 packages + 2 shared |
| `docs/ARCHITECTURE.md` | ✅ | structural map captured |
| `tasks/BUILD-PLAN.md` | ✅ | this file |
| README + `.gitignore` + workspace `pyproject.toml` | ✅ | |

---

## Phase 1 — Shared types

| Item | Status | Where | Notes |
|---|---|---|---|
| `TaskClassification` | ⏳ | `shared/types/src/chimera_types/classification.py` | AMR classifier output |
| `FileContext` + `ContextBundle` | ⏳ | `shared/types/src/chimera_types/context.py` | resolver output |
| `UsageRecord` | ⏳ | `shared/types/src/chimera_types/usage.py` | tracker schema |
| `RoutingDecision` | ⏳ | `shared/types/src/chimera_types/routing.py` | router output |
| `RuntimeStatus` | ⏳ | `shared/types/src/chimera_types/runtime.py` | dev server / browser / DB status |

---

## Phase 2 — CLI Runners (the pure-CLI substrate)

The only place chimera talks to LLMs. No API SDK calls anywhere else.

| Runner | Status | File | Notes |
|---|---|---|---|
| `CLIRunner` protocol | ⏳ | `dispatch/runners/base.py` | |
| `claude` runner | ⏳ | `dispatch/runners/claude.py` | migrated from legacy `cli/runners.py` |
| `codex` runner | ⏳ | `dispatch/runners/codex.py` | NEW |
| `gemini` runner | ⏳ | `dispatch/runners/gemini.py` | migrated from legacy |
| `ollama` runner | ⏳ | `dispatch/runners/ollama.py` | NEW (local) |
| `llm` runner | ⏳ | `dispatch/runners/llm.py` | NEW (Simon Willison's, covers OpenRouter+rest) |
| `run_structured()` | ⏳ | `dispatch/structured.py` | prompt-engineered + parsed + retry-on-fail |
| `cli_available()`, subprocess helpers | ⏳ | `dispatch/runners/base.py` | from legacy `cli/cli.py` |

---

## Phase 3 — AMR (Automatic Model Router)

| Item | Status | Where | Notes |
|---|---|---|---|
| `classifier.py` (cheap-model task classifier) | ⏳ | `dispatch/classifier.py` | uses Haiku-tier runner |
| `router.py` (classification → runner+model) | ⏳ | `dispatch/router.py` | reads `routing_table.yaml` |
| `routing_table.yaml` (default routing matrix) | ⏳ | `config/routing_table.yaml` | task_type × complexity → runner |
| Validator-gated escalation | ⬜ | `dispatch/escalation.py` | |
| `mcp__chimera__route` MCP tool | ⬜ | `server/tools/route.py` | classify-only, returns recommendation |
| `mcp__chimera__chain_auto` MCP tool | ⬜ | `server/tools/chain_auto.py` | end-to-end auto-routed dispatch |

---

## Phase 4 — Context Resolver (Pillar 1)

| Item | Status | Where | Notes |
|---|---|---|---|
| `resolver.py` | ⬜ | `context/resolver.py` | primitive: `resolve_context(task) → ContextBundle` |
| `relevance.py` | ⬜ | `context/relevance.py` | merge Séance + Scarlet + Serena scores |
| `budget.py` | ⬜ | `context/budget.py` | per-task token budget enforcement |
| `cache.py` | ⬜ | `context/cache.py` | memoize per (project, task-hash) |
| Séance library API | ⬜ | `packages/seance/src/seance/api/` | will use grep-fallback initially |
| Scarlet library API | ⬜ | `packages/scarlet/src/scarlet/api/` | will read existing CLAUDE.md initially |
| `seance_client.py` | ⬜ | `tools/seance_client.py` | `from seance.api import semantic_search` |
| `scarlet_client.py` | ⬜ | `tools/scarlet_client.py` | |

---

## Phase 5 — Runtime Manager (Pillar 2)

| Item | Status | Where | Notes |
|---|---|---|---|
| `chimera dev` command | ⬜ | `cli/dev.py` | one-command stack startup |
| `lifecycle.py` | ⬜ | `runtime/lifecycle.py` | start/stop everything |
| `dev_server.py` | ⬜ | `runtime/dev_server.py` | npm/pnpm/uv detection |
| `browser.py` | ⬜ | `runtime/browser.py` | Chrome `--remote-debugging-port` |
| `postgres.py` | ⬜ | `runtime/postgres.py` | discover + connect project DB |
| `logs.py` | ⬜ | `runtime/logs.py` | aggregate stdout/stderr |
| `healthcheck.py` | ⬜ | `runtime/healthcheck.py` | readiness probes |
| Specter integration | ⬜ | hooked from `runtime/browser.py` | |

---

## Phase 6 — chimera CLI commands

| Command | Status | File | Notes |
|---|---|---|---|
| `chimera task <description>` | ⏳ | `cli/task.py` | end-to-end: context → AMR → dispatch |
| `chimera dev <project>` | ⬜ | `cli/dev.py` | (Phase 5) |
| `chimera init` | ⬜ | `cli/init.py` | first-run UX, detects + suggests Ollama |
| `chimera doctor` | ⬜ | `cli/doctor.py` | env diagnostic, lists available runners |
| `chimera install --target` | ⬜ | `cli/install.py` | configures Claude Code / Gemini / Codex MCP |
| `chimera monitor {start,stop,restart}` | ⏳ | `cli/monitor.py` | migrate from legacy |
| Entry point (`chimera.cli:main`) | ⏳ | `cli/__init__.py` | argparse / typer dispatch |

---

## Phase 7 — Monitor migration (from chimera-legacy)

The observability daemon already works in legacy. Mostly mechanical move.

| Item | Status | Notes |
|---|---|---|
| `monitor/server.py` | ⏳ | FastAPI on 127.0.0.1:8740 |
| `monitor/api/projects.py` | ⏳ | |
| `monitor/api/topology.py` | ⏳ | |
| `monitor/api/threads.py` (incl SSE) | ⏳ | |
| `monitor/api/usage.py` | ⏳ | |
| `monitor/api/anomalies.py` | ⏳ | |
| `monitor/api/api_routes.py` | ⏳ | FastAPI route extractor |
| `monitor/api/frontend_components.py` | ⏳ | React component extractor |
| `monitor/api/schema_drift.py` | ⏳ | |
| `monitor/anomalies.py` (self-watch) | ⏳ | |
| `monitor/watchdog.py` (zombie detector) | ⏳ | |
| `monitor/usage.py` (LLM tracker) | ⏳ | |
| `monitor/auto_fix.py` | ⏳ | |
| `monitor/discovery/*` | ⏳ | project + connection + topology discovery |
| `monitor/metadata/*` | ⏳ | observation collector + scan |
| **NEW** `monitor/api/savings.py` | ⬜ | burn-down chart data |
| **NEW** `monitor/api/runtime.py` | ⬜ | dev/browser/db status |
| **NEW** `monitor/api/routing.py` | ⬜ | AMR decision log |

---

## Phase 8 — Patterns migration (from chimera-legacy)

The existing 8 patterns (SPR-4, CLR, PDE, HVD, ACL, DCE, POB, plus AMR new). Mostly mechanical move.

| Pattern | Designation | Status | Notes |
|---|---|---|---|
| SPR-4 | Sequential Phase Runner | ⬜ | `chain_pipeline` MCP tool |
| TFB | Tri-Force Balancer (inside SPR-4) | ⬜ | 6 balanced force nodes |
| CLR | Closed-Loop Refiner | ⬜ | `chain_refiner` |
| PDE | Parallel Dispatch Engine | ⬜ | `swarm` |
| HVD | Hypervisor Daemon | ⬜ | `chain_hypervisor` |
| **AMR** | **Automatic Model Router** | ⏳ | **NEW — Phase 3** |
| ACL | Atomic Component Library | ⬜ | `chain_components` |
| DCE | Dead Code Eliminator | ⬜ | `chain_deadcode` |
| POB | Proactive Observation Builder | ⬜ | `chain_toolbuilder` |

---

## Phase 9 — Frontend migration (apps/monitor-ui)

| Item | Status | Notes |
|---|---|---|
| Migrate `monitor_ui/` → `apps/monitor-ui/` | ⬜ | mostly file move |
| Trail rendering (already in legacy commit `7155061`) | ⬜ | brings into new repo |
| **NEW** Burn-down savings widget | ⬜ | shows "you saved X this week" |
| **NEW** Runtime status panel | ⬜ | dev/browser/db |
| **NEW** AMR routing decisions log | ⬜ | "this task routed to Y because Z" |

---

## Phase 10 — API removal (the deprecation path)

The dev-tool pitch requires "no API keys, no surprise bills." Migration sequence:

| Step | Status | Notes |
|---|---|---|
| Flip every node default to CLI | ⬜ | API stays as opt-in (`CHIMERA_USE_API=true`) |
| Build `run_structured` helper | ⏳ | (Phase 2) |
| Migrate API-using nodes one at a time | ⬜ | watching parse-failure rate via usage tracker |
| Delete API provider code | ⬜ | once parse-failures stabilize <1% |
| Remove `langchain_anthropic` dep | ⬜ | |

API-using nodes inventory (from legacy):
- `validator`, `supervisor`, `critic`, `stress_tester`, `scope_analyzer`, `arbitrator`, `retry_controller`, `compliance`, `refiner/classifier`, `swarm/task_decomposer`, `hypervisor_dispatcher`, `toolbuilder/friction`, `toolbuilder/proposer`, `nodes/balanced/integration_gate`

---

## Decisions & rationale (sticky notes)

- **Repo:** `chimera` reclaimed; old code lives in `fsocietydisobey/chimera-legacy` (archived).
- **Naming:** Sigil/Séance/Scarlet/Specter retained for now. Marketing-readiness later.
- **Substrate:** pure CLI subprocess. No API SDK calls in the tree (eventual goal).
- **Library mode:** each perception package exposes `<pkg>.api.*` for in-process import + `<pkg>.server.mcp` for direct shell use. Same logic, two transports.
- **Workspace:** uv workspaces. Each package independently versionable + publishable.
- **Default audience:** single-CLI-subscription dev (most devs). Local-Ollama as the cost-relief story. Multi-provider routing is a power-user feature.
- **Auto-router:** AMR pattern. Classifier on cheap runner; router picks runner+model; validator-gated escalation when output fails quality bar.

---

## Next session pickup

Sorted by priority for resuming:

1. Finish whatever's marked ⏳ in this file
2. Write tests for the runners + AMR (currently untested)
3. Migrate monitor + patterns (mechanical moves from legacy)
4. Begin context resolver (Phase 4)
5. Begin runtime manager (Phase 5)
