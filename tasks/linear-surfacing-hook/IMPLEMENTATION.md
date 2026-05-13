# Linear-surfacing hook — Phase 1.5 (1-2 days)

**Status**: spec'd 2026-05-13, not started
**Phase**: NORTH_STAR Phase 1.5 (replaces the previous cross-machine-backend scope)
**Owner**: TBD

## Why

Joseph runs khimaira on multiple machines and uses Linear for task
tracking. The Linear MCP server is already wired up — querying Linear
from any session works today (`mcp__linear__list_issues`). What's
missing is the **auto-surface-on-boot** UX that khimaira's existing
handoff system has: when you open Claude Code in a project, you don't
have to *ask* what handoffs are pending; SessionStart shows them.

Linear assignments should work the same way. Today the agent has to
know to query Linear; tomorrow the answer lands automatically.

## Why not the previous Phase 1.5 scope

This task replaces an earlier (now-killed) plan to build a parallel
task-dispatch system on top of `handoffs.jsonl`. Reasoning for the
kill: Linear is already canonical for Joseph's tasks. Building a
second system would be parallel-system syndrome — two places to look
for "what should I do next," two schemas to evolve, two query
surfaces, two places things can go stale.

The Linear-surfacing hook captures the *user-visible value* of the
original idea (auto-surface assigned work on session boot) without
the cost (a parallel system).

See `tasks/cross-machine-backend/IMPLEMENTATION.md` for the killed
scope, kept for archeology.

## Design

### Where the hook lives

Extend the existing SessionStart hook
(`packages/khimaira/src/khimaira/hooks/session_start.py`). It already
surfaces inbox + handoffs + other-active-sessions; Linear issues
join that block.

### What it fetches

```python
# Pseudocode — actual call is via the linear MCP client
issues = mcp__linear__list_issues(
    assignee="me",       # current Linear user
    state="open",        # exclude done / cancelled
    limit=10,            # don't render unbounded lists
)
```

Resolved decisions on the open questions (chimera-extension's spec
flagged these as needing answers):

- **Filter strategy: `assignee=me + status≠done` only, NOT cwd-scoped.**
  Cross-project assignments are common (Joseph has KHI-* issues from
  his khimaira repo and LL-* issues from another project); cwd-scoping
  hides items he actually needs to see. If the result count grows past
  a useful size, add a `--project` filter to `/linear` later — but
  default to "show me everything assigned to me."

- **Render: inline in SessionStart**, matching the handoff pattern.
  The whole point is auto-surface; on-demand only would just be a
  thinner wrapper around the existing `list_issues` call.

- **Cache: 5-minute file cache** at
  `~/.local/state/khimaira/linear-cache.json`. Trade: avoid 200-500ms
  Linear-API roundtrip on every SessionStart (slow boots on flaky
  network); accept up to 5min of staleness (acceptable for "what's
  assigned to me"). `/linear` slash command bypasses the cache.

### Render format

```
📋 Linear issues assigned to you (3, cached 2m ago):
  • KHI-12 (in progress) — "Add per-project budget for mcp__khimaira__auto"
  • KHI-15 (todo)        — "Investigate Claude Agent SDK"
  • LL-7  (in review)    — "Wire pricing into checkout flow"
```

Sort: in-progress first, then in-review, then todo. Within a state,
sort by `updatedAt` descending. Truncate titles at ~70 chars.

### Failure handling

Linear API down or rate-limited → render nothing (don't show a stale
cache if it's older than 1 hour; don't show an error to the user
either — the rest of SessionStart is what they came for). Log a
warning so we can see the failure rate in observer.

### Optional half-day add-on (not in MVP)

Aggregated `khimaira usage savings --across-machines` — fetch
usage.jsonl from configured remote khimaira instances and sum the
spend. Defers to a per-machine SSH config or `KHIMAIRA_REMOTE_HOSTS`
env var. Skip in v1 unless Joseph asks; today's per-machine view is
"good enough" for the savings-tracking value-prop.

## Implementation steps

1. **Linear MCP client wrapper** (0.5d) — a thin Python module that
   calls `mcp__linear__list_issues` via the existing daemon-side MCP
   client (or via HTTP if simpler), with retry + timeout + the 5-min
   file cache.

2. **Hook integration** (0.5d) — extend `session_start.py` to fetch
   Linear issues alongside the existing inbox+handoffs+sessions block.
   Render inline. Skip if Linear MCP isn't registered for this user
   (defensive — the hook must work when Linear isn't set up).

3. **`/linear` slash command** (0.5d, optional) — bypass-the-cache
   variant for when the user just assigned themselves a task in the
   Linear UI and wants to see it immediately.

4. **Tests** (0.5d):
   - happy path: 3 issues returned → all surface in render
   - empty: assignee has nothing → block doesn't render at all
   - Linear unreachable: no error to user, warning logged
   - cache hit: second call within 5min doesn't hit Linear
   - cache stale (>1h) + Linear unreachable: don't render stale data

## Done when

- A fresh Claude Code SessionStart in any project shows assigned
  Linear issues inline alongside handoffs.
- The render takes <50ms on cache hit, <500ms on cache miss + Linear
  reachable, <100ms on Linear unreachable (cache fallback or skip).
- `/linear` shows the same data, bypassing cache.

## References

- Existing SessionStart hook: `packages/khimaira/src/khimaira/hooks/session_start.py`
- Linear MCP tool list: `mcp__linear__list_issues`, `mcp__linear__get_issue`
- Cross-machine spike (killed): `tasks/cross-machine-backend/IMPLEMENTATION.md`
- Original chimera-extension scope-shift notice: session
  2cac13b6, notice id `afa359a1ab43`, 2026-05-13.
