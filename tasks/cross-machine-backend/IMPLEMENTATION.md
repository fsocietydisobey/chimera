# Cross-machine khimaira backend

**Status**: scope-corrected 2026-05-13 — see banner below
**Phase**: NORTH_STAR Phase 1.5 (3-5 days for the MVP task-dispatch primitive)
**Owner**: TBD
**Last reviewed**: 2026-05-13

> **🎯 Primary goal: cross-machine TASK DISPATCH, not full state
> replication.** The user wants to assign a piece of work to another
> machine (with the context bundle the work needs) and have that
> machine pull it on its next SessionStart. Most of this doc was
> originally written for the broader state-replication problem; the
> MVP is much narrower. See "Phase 1.5a — Task dispatch MVP" below
> for what actually ships. Full state replication (the rest of this
> doc) is deferred to **Phase 1.5b**, gated on the MVP proving
> demand for it.

## Why

Joseph runs khimaira on two machines (desktop + laptop, as of 2026-05-13).
When he's at the desktop and realizes "the laptop is faster at running
the test suite" — or "the laptop has the Postgres replica already
loaded" — he wants to *say "do this work over there"* and walk away.
Today that requires manual context-shuffling: SSH in, open Claude
Code, paste the prompt, wait, copy back the answer.

The MVP closes that loop:
- One side: `khimaira assign <target> "<task>" --refs <files>`
- Other side: SessionStart hook on next boot surfaces the assigned
  task with its context bundle; agent picks it up and runs.

**Out of scope for the MVP** (and that's the point):
- Sessions on machine A being visible to machine B (full state replication)
- `khimaira usage savings` aggregating across machines
- Cross-session `/ask` / `/tell` routing between hosts
- Read-after-write consistency on every primitive

If those become daily-use needs later, that's Phase 1.5b.

## Phase 1.5a — Task dispatch MVP (3-5 days)

### The insight

A handoff with an `assignee` field and a `context_refs` list **is a
task**. Khimaira already has:

- `handoffs.jsonl` storage + 7-day TTL + cwd-scoped consume on SessionStart
- `attached.json` mapping `project_path` → `label` per machine
- `khimaira attach` workflow to register a project on each machine
- SessionStart hook that surfaces matching handoffs as `📦 khimaira handoffs`

What's missing: routing a handoff to a *specific machine* (not just
"any session in this cwd") and stapling a context bundle to it
(files, refs, commit SHAs) so the receiving agent can do the work
without back-and-forth.

### Data-model changes

Two new optional fields on `handoffs.jsonl` records:

```python
class HandoffRecord:
    # ... existing fields ...
    assignee: str | None = None              # project label of the target machine
    context_refs: list[ContextRef] = []      # files / commits / URLs / pasted text the agent needs
    pulled_by: str | None = None             # session_id that claimed it (replaces read_by for assigned items)
    pulled_at: str | None = None             # ISO 8601 timestamp of claim
```

`ContextRef` is a tagged union:

```python
ContextRef = (
    {"kind": "file", "path": "src/foo.py", "machine": "desktop"}      # path on the SENDING machine — receiver fetches via the dispatch payload
  | {"kind": "commit", "sha": "abc1234", "repo": "khimaira"}          # git ref, receiver resolves locally
  | {"kind": "url", "url": "https://..."}                              # external link
  | {"kind": "inline", "text": "..."}                                  # pasted prompt context
)
```

For file refs, the sender bundles the file *contents* into the
dispatch payload — receiver may not have the same working tree state.

### The assignee primitive — project label

`assignee` is a **project label** (registered in `attached.json`), not
a machine identifier or free-form username. Rationale:

- **Already exists** — `attached.json` stores `project_path` + `label`
  per registered project on every machine.
- **Machine-stable** — laptop reinstalls, hostname changes, dotfile
  migrations all preserve the label as long as the user
  `khimaira attach`-es with the same name.
- **Trivially multi-user-extensible** — when teams come along, the
  label gets prefixed (`alice/jeevy-portal`, `joseph/khimaira`); the
  resolution logic doesn't change.

If multiple machines have the same label (Joseph attaches `khimaira`
on both desktop and laptop), the handoff goes to whichever pulls
first. That's the intended semantic for a solo user with a
load-balanced pool.

### New endpoints + CLI

```
POST   /api/handoffs                          # extended: optional assignee + context_refs
GET    /api/handoffs/pending?assignee=<label> # NEW: receiver pulls anything assigned to this label
POST   /api/handoffs/{id}/claim               # NEW: mark as pulled_by/pulled_at (atomic, returns false if already claimed)
```

```bash
# Sender side:
khimaira assign laptop "Run the integration suite and report results" \
  --ref tests/integration/test_dispatch.py \
  --ref tests/integration/test_runners.py

# Receiver side: auto-pulls on next SessionStart hook (no manual command needed)
# Or explicitly:
khimaira pull                                  # show pending tasks for THIS machine
khimaira pull --claim <handoff_id>             # mark as taken
```

### SessionStart hook integration

Today's SessionStart consumes cwd-scoped handoffs. Extend to also
fetch `assignee=<this-machine-label>` handoffs and surface them with
their context bundle pre-rendered. The agent sees:

```
📦 khimaira tasks assigned to this machine (1):
  [handoff abc12345 · 2026-05-13T14:22:00 · from joseph@desktop]
  Task: Run the integration suite and report results

  Context bundle (2 files):
  - tests/integration/test_dispatch.py (3.2KB)
  - tests/integration/test_runners.py (1.8KB)

  Treat as a directive. The sender is awaiting results.
```

### Implementation order

| Day | Work |
|---|---|
| 1 | Extend `handoffs.jsonl` schema with `assignee` + `context_refs` + `pulled_by/at`. Backward-compat: old records load with defaults. |
| 1 | `context_refs` resolver — file-kind reads + bundles into the dispatch payload at post time (sender side). |
| 2 | New endpoints: `GET /api/handoffs/pending?assignee=`, `POST /api/handoffs/{id}/claim` (atomic). |
| 2 | `khimaira assign` + `khimaira pull` CLI commands. |
| 3 | SessionStart hook extension: pull `assignee=<label>` handoffs alongside the existing cwd-scoped consume. Render the context bundle inline. |
| 3-4 | Tests: assignment + pull + claim race + context-bundle round-trip + unknown-assignee + already-claimed. |
| 4-5 | Documentation: README section, `khimaira assign --help`, an `INTEGRATING.md` snippet. End-to-end smoke (Joseph: desktop assigns to laptop, laptop pulls and runs). |

### Open questions (for the implementing session — not blockers)

1. **Context bundle size cap** — if a user runs `khimaira assign laptop "..." --ref entire-repo/`, we shouldn't send 50MB through. Hard cap (say, 1MB total) + clear error when exceeded? Allowlist of file extensions?

2. **Receiver authentication for the claim race** — when two machines have the same label and both pull, the `claim` POST is FCFS via the daemon's lock. But how does the daemon know which machine is asking? For now: just an opaque session_id sent in the body. Multi-user adds auth on top.

3. **Result reporting back** — once the receiver finishes, how does the sender see the result? MVP option: receiver posts a notice to the sender's session id. Cleaner option: a `result` field on the handoff record itself. The MVP can ship without this and ask agents to use `session_post_notice` manually; v2 makes it structured.

4. **Live-machine vs. dormant-machine semantics** — if you `khimaira assign laptop "..."` and the laptop is asleep, the task waits in `handoffs.jsonl` (TTL 7 days). When the laptop wakes, SessionStart surfaces it. That's fine. But if Joseph wants "do this ONLY if a laptop session boots in the next hour" semantics, the TTL needs to be per-handoff. Defer to v2.

### What this does NOT solve (and that's the point)

- Session state from machine A is still invisible on machine B.
- `khimaira usage savings` still only sees this-machine spend.
- Cross-session `/ask` doesn't reach across hosts.
- `mcp__khimaira__session_list()` shows local sessions only.

The 80/20 read: task dispatch is the daily-use need. The rest is
"would be nice if we ever do teams" — and that's a much bigger build
that should wait for evidence of demand.

---

## Phase 1.5b — Full state replication (deferred, only if MVP proves demand)

Everything below this line is the original spike — kept for reference
in case Phase 1.5a graduates into needing genuine multi-machine state.
**Do not implement any of it before the MVP ships and we have evidence
of need.**

## What's actually local-only today

### State surface
```
~/.local/state/khimaira/
  attached.json          ← per-machine project registry
  handoffs.jsonl         ← project-scoped directives (cwd-keyed → leaks paths)
  usage.jsonl            ← every dispatch's tokens + cost + mode
  mcp-calls.jsonl        ← MCP tool call log
  monitor-anomalies.jsonl
  monitor-heartbeat.json ← live observer state
  hook-counters/         ← per-session counters (UserPromptSubmit, etc.)
  sessions/<uuid>/
    status.json
    decisions.jsonl      ← logged commitments
    questions.jsonl      ← open/answered cross-session questions
    files_touched.jsonl  ← automatic via PostToolUse hook
    inbox.jsonl          ← notes from other sessions (answers + notices)
    archive.jsonl        ← read inbox notes
```

### How the MCP server writes to it
**Mostly direct Python imports, NOT via HTTP.** The MCP server tools
in `khimaira/server/mcp.py` call functions like `sessions.log_decision()`
that write to JSONL files directly. The HTTP API exists at `:8740` but
it's a SECONDARY surface — used by:
- The web dashboard (SPA reads /api/sessions, /heartbeats, etc.)
- External tooling (curl/scripts)
- Some cross-session primitives where the daemon is the canonical
  process (handoffs, transcript watcher events)

This duality matters for the architecture: pointing `KHIMAIRA_BACKEND_URL`
at a remote daemon doesn't automatically route the MCP server's writes
there — those bypass HTTP entirely.

### HTTP API surface (~50 endpoints)
Sessions, handoffs, mcp-calls, heartbeats, processes, anomalies,
projects, schema_drift, frontend_components. Mostly REST, some
streaming (SSE) for live runs.

### Bind + auth
- `uvicorn` binds `127.0.0.1:8740` only (loopback)
- No authentication (loopback is treated as trusted)
- No TLS

## Architecture options

### Option A — Single backend daemon, thin local clients
One designated machine runs `khimaira-monitor`. Other machines run
ONLY the MCP server stub that forwards every state operation to
the remote daemon's HTTP API. Local file storage at
`~/.local/state/khimaira/` is empty (or cache-only).

**Pros**:
- Single source of truth → no conflict resolution
- The existing HTTP API does almost everything we need; just need
  to route writes through it instead of direct file access
- Simple mental model: "one server, many clients"

**Cons**:
- The "backend" machine is a SPOF — if it's off, nobody's khimaira works
- Latency: every `session_log_decision` is now a network roundtrip
  (LAN: ~1-2ms, WAN/Tailscale: 10-100ms)
- Doesn't degrade gracefully when offline
- Requires the MCP server's direct-Python-call code paths to be
  refactored to go through HTTP (~50-100 call sites)

**Best for**: small team where one workstation is "always on" (or a
small VPS), all dev machines on same LAN or Tailscale net.

### Option B — Postgres as the shared store
Each machine runs its own daemon. The JSONL files are replaced by
Postgres tables. Writes go to Postgres directly via psycopg (already
a dep — used for LangGraph checkpointer access).

**Pros**:
- Native multi-writer support (no DIY locking)
- Mature operational story (backups, replication, indexing)
- Schema is enforced (Pydantic → SQLAlchemy/sqlmodel one-to-one)
- Query power: `khimaira usage savings --aggregate` becomes a single
  GROUP BY across all writers

**Cons**:
- Requires Postgres infra somewhere (RDS/Supabase/self-host)
- Schema migration cost — every JSONL primitive becomes a table
- Still has the latency issue for hot-path writes (mitigated by
  connection pooling)
- More complex install story (set DATABASE_URL, run migrations)

**Best for**: team with existing Postgres infra (or willing to add it),
production-grade deployment, longer time horizon.

### Option C — Read-only remote view
Add a `--remote ssh://laptop` flag to khimaira CLI that proxies
SELECT queries (sessions list, usage savings, etc.) over SSH but
doesn't replicate state. Each machine still writes locally.

**Pros**:
- Zero new infra
- Uses existing SSH setup
- Works offline (just can't query the remote)
- Two-day implementation

**Cons**:
- Doesn't actually make state cross-machine; just lets you peek
- Hand-offs still don't flow between machines
- `khimaira usage savings` can't aggregate cleanly (would need to
  pull JSONL from remote and concat)

**Best for**: stop-gap. "I want to see what my laptop did from my
desktop" without committing to real sync.

### Option D — Sync via syncthing / object storage
Each machine's JSONL files get synced to a shared location (S3,
syncthing, or git). Daemons periodically reconcile.

**Pros**:
- Works offline (local writes always succeed; sync on reconnect)
- No SPOF (eventually consistent across all peers)

**Cons**:
- Conflict resolution is hard for JSONL append-only files (two
  machines append independently → merge is straightforward but
  ordering is lost)
- Worse for stateful primitives like inbox `mark_read` flags
  (last-write-wins is wrong here)
- Latency for "did the other machine see my handoff" can be minutes

**Best for**: occasional sync between machines that aren't networked.
Not the right call for active collaboration.

### Option E — SSH tunnel + Option A (recommended starting point)
Refinement of A: instead of binding the backend daemon to a network
interface, keep it on `127.0.0.1` and use SSH tunneling to expose it
to other machines:
```
# On laptop, expose desktop's khimaira locally:
ssh -L 8740:127.0.0.1:8740 desktop
# Laptop's khimaira clients now treat localhost:8740 as the desktop's daemon
```
This sidesteps the auth question entirely — SSH already authenticated
the user.

**Pros of E over A**:
- No need to design a new auth scheme (token? mTLS?)
- Reuses the SSH infra Joseph already has working (this thread set it up)
- Local-only daemon binding stays — no exposed port on LAN
- Trivially "drop the backend" by closing the tunnel

**Cons**:
- Tunnel has to be active to use khimaira from the non-backend machine
- Single-user only (each user runs their own tunnel)

## Recommendation

**Phase 1 (MVP)**: Option E — SSH-tunneled Option A.
- Add `KHIMAIRA_BACKEND_URL` env var. If set, every state operation
  in the MCP server + CLI goes through HTTP to that URL instead of
  local files.
- Document the SSH tunnel install pattern: `ssh -L 8740:127.0.0.1:8740 backend-machine`
- Designate one machine as "backend" (Joseph: probably the desktop).
- Refactor the ~50-100 direct-Python call sites in the MCP server
  to use a `StateClient` abstraction that picks local-file vs HTTP
  based on env.

Estimated effort: 1-2 weeks. The refactor is the bulk of it; the
network glue is trivial.

**Phase 2 (later)**: Optional Postgres backend (Option B).
Once Phase 1 proves the multi-machine pattern works, add an opt-in
DATABASE_URL path for users who want a proper backend. Same
StateClient abstraction; new implementation behind it.

**Phase 3 (much later, only if demand)**: Offline-tolerant hybrid.
Local-first writes with async replication. Complex; defer until
someone actually needs it.

## Open questions for whoever picks this up

1. **Which call sites bypass HTTP?** Quick grep needed for direct
   `sessions.<fn>()` calls in `server/mcp.py` to know the scope of
   the refactor. Estimate: 30-60 sites across MCP tools.

2. **What's the hot-path-write latency budget?** Today
   `session_log_decision` is ~1ms (file append). Acceptable to make
   it 10ms (HTTP-over-localhost) or 100ms (HTTP-over-LAN)? Hooks fire
   on every tool call — if hook writes balloon, the whole CC experience
   slows.

3. **Project-scope handoffs use `cwd` as the key.** Two machines have
   the SAME cwd (e.g. `/home/_3ntropy/dev/khimaira`) — desirable, the
   handoff flows. But what about machine-relative paths
   (`/Users/joseph/...` vs `/home/_3ntropy/...`)? Need a normalization
   step or a "project label" abstraction independent of literal path.

4. **Session uuids are unique across machines** (UUIDv4), so no
   conflict on identity. But session NAMES (`session_set_name`) — could
   collide. Decide: namespace by machine, first-write-wins, or refuse
   duplicates?

5. **MCP server registration**: in remote-backend mode, each machine's
   Claude Code still talks to its LOCAL khimaira MCP server, which
   then forwards to remote daemon. The MCP tools still need to be
   registered locally. Bootstrap profile needs a "client-only" mode
   that skips the daemon install + just registers the MCP.

6. **Auth, even for SSH-tunneled mode**: if you mistype and bind to
   `0.0.0.0:8740`, you've just exposed unauthenticated session state
   to the LAN. Add a token-header check at minimum, even if the daemon
   binds loopback by default.

7. **Observer/transcript watcher**: per-machine (watches that machine's
   `~/.claude/projects/`). Should the remote daemon receive observer
   events from all machines? Probably yes, via a separate
   `POST /api/observer/event` push from each machine's local agent.

8. **Postgres future-proofing**: if we know Phase 2 is Postgres-backed,
   write the StateClient abstraction such that the file-based
   implementation is a thin wrapper too. Saves a second refactor.

## Validation hooks (how to know it works)

After Phase 1 ships:
1. Joseph's desktop posts a handoff scoped to
   `/home/_3ntropy/dev/khimaira`. On the laptop, the SessionStart
   hook surfaces it on the next session boot.
2. `khimaira usage savings` on either machine shows aggregate spend
   across both.
3. `mcp__khimaira__session_list` on either machine shows sessions
   from both.
4. `/ask laptop-session "..."` from desktop session unblocks when
   the laptop session wakes — works today within a machine; should
   work cross-machine after Phase 1.

## What this does NOT solve

- Multi-user / team scenarios — Phase 1 is still single-user.
  Adding multi-user means real auth (OAuth? per-user tokens?) and
  user-scoped namespacing in the storage layer. Defer.
- Conflict resolution for concurrent edits to the same session's
  status (two machines both running session X at the same time).
  Postgres in Phase 2 handles this naturally; Phase 1 should
  document the limitation.
- Encryption at rest. State JSONL is plaintext. For a personal home
  network this is fine; for a hosted backend it's a real question.

## Bibliography / prior art

- LiteLLM proxy server — single-process gateway pattern, similar to
  Option A
- langfuse — cloud-hosted observability for LLM apps with multi-machine
  tracing; Postgres backend
- Helicone — same shape, hosted
- aider's `/load` and `/save` — file-based sync, similar to Option D
- AnythingLLM workspaces — Postgres-backed multi-user; closer to
  Phase 2's end-state

None of these solve the exact "personal multi-machine khimaira" use
case. Solo dev with 2-3 machines is an underserved niche.
