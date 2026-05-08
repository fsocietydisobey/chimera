# chimera

> Multi-model AI orchestration for the terminal AI era.

chimera is a dev framework that makes your terminal AI tool — Claude Code, Codex CLI, Gemini CLI, or local Ollama — 5–10× more efficient. It pre-resolves task-relevant context, manages your dev stack with a debugger-attached browser, and routes every prompt to the cheapest competent model. **No API keys required to start; bring your own when you want premium models.**

---

## How it fits into your workflow

```mermaid
flowchart LR
    User([You]) -->|terminal| Shell["Claude Code · Codex CLI · Gemini CLI<br/>(any AI shell)"]
    Shell -->|MCP| Chimera["⬢ chimera<br/>orchestrator"]
    Chimera -->|subprocess| Claude["claude"]
    Chimera -->|subprocess| Codex["codex"]
    Chimera -->|subprocess| Gemini["gemini"]
    Chimera -->|subprocess| Ollama["ollama (local)"]
    Chimera -->|subprocess| LLM["llm (Simon Willison's<br/>+ OpenRouter)"]
    Chimera -.->|library| Scarlet["Scarlet<br/>cartography"]
    Chimera -.->|library| Seance["Séance<br/>semantic search"]
    Chimera -.->|MCP| Specter["Specter<br/>browser debug"]

    style Chimera fill:#1f6feb,stroke:#0d419d,color:#fff
    style Ollama fill:#2da44e,stroke:#1a7f37,color:#fff
```

You drive your AI shell as usual. Chimera is the layer that picks the right tool for each task and shrinks the prompt before it goes out.

---

## Three pillars

```mermaid
flowchart TB
    subgraph Chimera["chimera orchestrator"]
        direction LR
        P1["⓵ Context resolver<br/><sub>What files matter for THIS task?</sub>"]
        P2["⓶ Runtime manager<br/><sub>chimera dev — full stack + Chrome + DB</sub>"]
        P3["⓷ AI dispatcher<br/><sub>Auto-route to cheapest competent CLI</sub>"]
    end

    P1 --> Out["minimal prompt<br/>(5-10× fewer tokens)"]
    P2 --> Browser["Chrome --remote-debugging-port"]
    P2 --> DB[("Postgres")]
    P2 --> Server["dev server (vite/next/uvicorn)"]
    P3 --> Routing["route → CLI runner<br/>+ usage tracker + budget"]

    style Chimera fill:#0d1117,stroke:#1f6feb,color:#fff
    style P1 fill:#1f6feb,color:#fff
    style P2 fill:#1f6feb,color:#fff
    style P3 fill:#1f6feb,color:#fff
```

1. **Context resolver** — Séance (semantic search) + Scarlet (codebase cartography) + grep + filesystem heuristics. Answers *"what files actually matter?"* before anything hits the LLM. Where the 5-10× token reduction lives.
2. **Runtime manager** — `chimera dev` starts your dev server, launches Chrome with `--remote-debugging-port` for Specter, ensures chimera-monitor is up. One Ctrl-C tears it all down.
3. **AI dispatcher** — auto-router (AMR pattern) classifies each task and dispatches to the cheapest competent CLI runner: Claude Code, Codex, Gemini, Ollama, or `llm` (Simon Willison's, covers OpenRouter + 100+ providers).

---

## How a single task flows through chimera

```mermaid
sequenceDiagram
    autonumber
    participant U as You
    participant S as AI CLI shell
    participant C as chimera
    participant CR as Context resolver
    participant R as AMR router
    participant Run as CLI runner<br/>(claude/ollama/...)
    participant T as Usage tracker

    U->>S: "fix the auth bug where ..."
    S->>C: mcp__chimera__task(description)
    C->>CR: resolve_context(task)
    CR-->>C: ContextBundle (3 files, 2.1k tok)
    C->>R: classify + route(task, context)
    R-->>C: claude / haiku-4-5 (trivial: $0.005 ceiling)
    C->>Run: run_claude(prompt, model=haiku-4-5)
    Run-->>C: RunnerResult
    C->>T: record(runner, model, tokens, cost)
    C-->>S: result + cost summary
    S-->>U: "fix applied. cost: $0.003"
```

Every dispatch is **classify → route → run → record**. The classifier is a small cheap call (~$0.0004); the savings from routing trivial tasks down-tier dwarf its cost.

---

## Why pure CLI substrate

```mermaid
flowchart LR
    Old["chimera v1<br/>Anthropic API SDK"] -.->|deprecated| New["chimera v2<br/>CLI subprocess only"]

    New --> Sub1["claude (Claude Code subscription)"]
    New --> Sub2["codex (OpenAI subscription)"]
    New --> Sub3["gemini (Google subscription)"]
    New --> Sub4["ollama (local — $0 marginal)"]
    New --> Sub5["llm + OpenRouter (mixed)"]

    Old -.->|"❌ surprise bills<br/>(fire_swarm $$$)"| Pain[("budget pain")]
    New -->|"✅ no API keys<br/>required"| Win[("dev-friendly")]

    style Old fill:#cf222e,color:#fff
    style New fill:#2da44e,color:#fff
    style Sub4 fill:#2da44e,color:#fff
```

**Pitch in one sentence:** *"chimera orchestrates your terminal AI tools without ever making an API call of its own. No keys, no surprise bills, no external SDK dependencies."*

---

## Repository layout

```mermaid
flowchart TB
    Root[chimera/<br/>workspace]
    Root --> P[packages/]
    Root --> S[shared/]
    Root --> A[apps/]
    Root --> D[docs/]

    P --> P1[chimera<br/>orchestrator]
    P --> P2[scarlet<br/>cartography]
    P --> P3[seance<br/>semantic search]
    P --> P4[specter<br/>browser debug]

    S --> S1[chimera-types<br/>schemas]
    S --> S2[chimera-transport<br/>MCP/SSE helpers]

    A --> A1[monitor-ui<br/>React dashboard]

    style Root fill:#1f6feb,color:#fff
    style P1 fill:#1f6feb,color:#fff
```

Each `packages/<name>/` has both:
- a **library API** (`<name>.api.*`) for in-process use by chimera
- an **MCP server** (`<name>.server.mcp`) for direct shell use

Same logic, two transports — like an SDK and a SQL interface to the same database engine.

---

## Quick start

```bash
# clone + install (uv handles the workspace)
git clone https://github.com/fsocietydisobey/chimera.git
cd chimera
uv sync --package chimera

# diagnose your environment
uv run chimera doctor

# auto-routed dispatch (dry-run first to see what it'd do)
uv run chimera task --dry-run "rename this variable"

# start the observability daemon
uv run chimera monitor start
# → http://127.0.0.1:8740 (loopback only — that IS the auth layer)

# spin up a project's full dev stack with one command
uv run chimera dev /path/to/project
```

To use chimera as an MCP server from Claude Code / Codex CLI / Gemini CLI:

```jsonc
// in .claude.json or equivalent
{
  "mcpServers": {
    "chimera": {
      "type": "stdio",
      "command": "bash",
      "args": ["-lc", "uv --directory /path/to/chimera run chimera mcp"]
    }
  }
}
```

42+ MCP tools available: orchestration, monitor, process observability, multi-session shared state.

---

## Pillars in detail

### Pillar 1 — Context resolver

Pre-LLM "what's relevant?" — minimizes prompt before anything bills.

```mermaid
flowchart LR
    Task[user task] --> R[resolver]
    R --> S1["Séance<br/>(semantic vector)"]
    R --> S2["Scarlet<br/>(CLAUDE.md + dep graphs)"]
    R --> S3["grep<br/>(keyword fallback)"]
    R --> S4["fs heuristics<br/>(recently modified)"]
    S1 --> Merge[merge + score + budget]
    S2 --> Merge
    S3 --> Merge
    S4 --> Merge
    Merge --> Bundle[ContextBundle<br/>~3 files, ~2k tokens]

    style R fill:#1f6feb,color:#fff
    style Bundle fill:#2da44e,color:#fff
```

When Séance/Scarlet aren't installed, the resolver falls back to grep + fs heuristics. **Quality scales with what's available; the interface doesn't change.**

### Pillar 2 — Runtime manager

`chimera dev` is the demoable wow-moment.

```mermaid
sequenceDiagram
    participant U as You
    participant CD as chimera dev
    participant DS as dev server
    participant CR as Chrome+CDP
    participant M as monitor daemon
    participant SP as Specter

    U->>CD: chimera dev /path/to/project
    CD->>CD: detect framework (vite/next/uvicorn)
    CD->>M: ensure running (start if not)
    CD->>DS: spawn (tracked process)
    DS-->>CD: "Local: http://localhost:5173"
    CD->>CR: launch with --remote-debugging-port
    CR-->>SP: ready for browser debug
    Note over U,SP: working state
    U->>CD: Ctrl-C
    CD->>CR: kill (registry order)
    CD->>DS: kill (registry order)
    CD-->>U: clean shutdown
```

Without `chimera dev`, the same setup is 4-5 manual commands and orphaned processes when something crashes.

### Pillar 3 — AI dispatcher (AMR — automatic model router)

```mermaid
flowchart LR
    Task[task] --> Cl["Classifier<br/>cheap CLI<br/>(~$0.0004)"]
    Cl --> Class["TaskClassification<br/>type, complexity, model rec"]
    Class --> Rt[Router]
    Rt --> Avail[availability gate]
    Rt --> Priv[privacy gate<br/>CHIMERA_LOCAL_ONLY]
    Rt --> Bud[budget gate]
    Avail --> Pick{pick}
    Priv --> Pick
    Bud --> Pick
    Pick --> Disp[dispatch to chosen runner]

    style Cl fill:#1f6feb,color:#fff
    style Pick fill:#2da44e,color:#fff
```

The router picks among installed runners using a YAML routing table that ships with sensible defaults (overridable per-user / per-project).

---

## Multi-session shared state

When one Claude Code session is grinding on a task, you can't ask related questions in another window without losing context. Chimera externalizes session state so parallel sessions can collaborate.

```mermaid
sequenceDiagram
    participant A as Session A<br/>(working)
    participant Ch as chimera
    participant B as Session B<br/>(side conversation)

    A->>Ch: session_log_decision("use Postgres read-only")
    A->>Ch: session_log_question("bcrypt or argon2?") → q_id
    Note right of A: A keeps working...
    B->>Ch: session_state(A) — reads digest
    B->>Ch: session_post_answer(A, q_id, "argon2id")
    Note left of A: A finishes its task
    A->>Ch: session_pending_notes(A) — auto on SessionStart
    Ch-->>A: "Session B answered Q3"
```

The bidirectional inbox closes the loop — without write-back, the design collapses to "B reads A, human relays."

---

## Process observability — replace polling with one blocking call

```mermaid
flowchart LR
    Old["agent: cat log.txt<br/>cat log.txt<br/>cat log.txt<br/>... 30× per run"] -.->|wasteful| OldCost[("30 MCP roundtrips<br/>burns context window")]
    New["agent: wait_for_process(<br/>'tests',<br/>completion_signal=r'\\d+ passed'<br/>)"] -->|one call| NewCost[("1 blocking call<br/>returns when matched")]

    style Old fill:#cf222e,color:#fff
    style New fill:#2da44e,color:#fff
```

The chimera daemon tails the process internally; the agent makes one blocking MCP call. Single roundtrip replaces dozens of polls.

---

## Status & roadmap

See [`tasks/BUILD-PLAN.md`](tasks/BUILD-PLAN.md) for full status. Cliff-notes:

| Phase | Status |
|---|---|
| 0 — Monorepo scaffold | ✅ |
| 1 — Shared types | ✅ |
| 2 — CLI runners (pure-CLI substrate) | ✅ |
| 3 — AMR (auto model router) | ✅ |
| 4 — Context resolver (with grep/fs fallbacks) | ✅ |
| 5 — `chimera dev` runtime manager | ✅ |
| 6 — `chimera task/route/doctor/monitor/mcp/dev` CLI | ✅ |
| 7 — Monitor daemon migration | ✅ |
| 8 — All 8 LangGraph patterns migrated | ✅ |
| 9 — Frontend (`apps/monitor-ui`) | ✅ |
| 11 — Multi-session shared state | ✅ (backend) |
| 12 — Process observability | ✅ (backend) |
| 4½ — Séance/Scarlet library APIs | ⬜ |
| 10 — API removal (deprecate langchain_anthropic) | ⬜ |
| Hooks for Phase 11 (PostToolUse, SessionStart) | ⬜ |
| Burn-down savings dashboard widget | ⬜ |

---

## Status

Pre-alpha. Active development. Legacy version archived at [`fsocietydisobey/chimera-legacy`](https://github.com/fsocietydisobey/chimera-legacy) for historical reference.
