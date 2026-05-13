# Khimaira — North Star

> **Khimaira is the orchestration layer that lives below any AI tool.** One
> MCP config line connects the user's editor to a single khimaira server
> that ships routing, semantic search, browser debugging, codebase
> cartography, sessions, observability, and savings tracking — without
> introducing new UI, new keybinds, or another tool to learn.

Whenever a decision is unclear, this is the test: does the work bring us
closer to "one config line, one server, the whole capability surface"?
If not, it doesn't belong on the critical path.

---

## Principles

1. **Editor-agnostic via MCP.** We build infrastructure that any
   MCP-capable AI tool can call. We do not build editor plugins. Adapter
   configs live in `contrib/` as examples, not in khimaira core.

2. **No manual scripts as the primary install path.** The user adds one
   line to their MCP config. Khimaira self-configures on first connect.
   Shell commands remain for non-MCP scenarios; they are not the pitch.

3. **Invisible infrastructure.** Khimaira does NOT replace editors, ship
   a TUI, or introduce a chat UI of its own. It lives below the model
   selection layer. Removing it leaves the user's editor working exactly
   as before.

4. **Consolidate, don't fragment.** seance, specter, scarlet, and any
   future khimaira-family capability lives in one workspace, one process,
   one MCP server, one upgrade path. Users see ONE thing.

5. **Enhance the existing agent ecosystem, do not compete with it.**
   Claude Code, Cursor, codecompanion.nvim, aider — all are consumers
   of khimaira, not competitors. Khimaira makes them smarter; it does not
   try to be them.

6. **Test the unhappy path.** Every primitive that touches user state
   ships with explicit coverage for the bad inputs, the stale data, the
   missing dependencies, the runner-not-installed case. The happy path
   proves a feature; the unhappy path proves we trust it in prod.

7. **Real numbers, not vibes.** Savings are computed from
   `usage.jsonl`, not estimated. The router logs every decision with
   classifier confidence, pool size, top-2 candidates, and rejected
   reasons. Mis-routes must be auditable post-hoc.

---

## What's shipped

- **Routing engine**: classifier, pool router, registry
  (`~/.khimaira/models.yaml`), capability-aware model selection across
  claude/codex/gemini/ollama/llm runners.
- **MCP surface**: `mcp__khimaira__auto`, `delegate`, `chain`, plus
  session/observer/process tools (~50 tools total).
- **Usage tracking**: every dispatch recorded with mode
  (auto / explicit-tier / manual), token counts, latency, cost
  estimate. `khimaira usage savings` computes Opus-direct counterfactual.
- **Observer**: multi-session state, handoffs, decisions, notices,
  LangGraph trace waterfall, daemon supervisor.
- **Bootstrap framework**: profile-driven (`khimaira-profile.yaml`),
  cross-machine portable. `khimaira bootstrap`, `khimaira doctor`,
  `khimaira heal` cover install, drift detection, self-healing.
- **Hooks**: SessionStart, UserPromptSubmit (auto-delegate nudge,
  inbox surfacing), PostToolUse (auto-tracked file touches).
- **Workspace consolidation (code-level)**: `packages/seance`,
  `packages/specter`, `packages/scarlet`, `packages/khimaira` all share
  one uv workspace, one lockfile, one `.venv`.

---

## What's next

Phases ordered by dependency. Each phase has a clear "done" gate.

### Phase 0 — Unify MCP registration  (2-3 days)

Today: 4 separate MCP servers (khimaira, seance, specter, scarlet).
After: one khimaira MCP exposes everyone's tools.

- Collapse duplicate copies at `~/dev/{seance,specter,scarlet}` (keep
  the `packages/` versions as canonical).
- Import seance/specter/scarlet tools into khimaira's MCP server,
  re-expose under one connection.
- Deprecate the standalone `seance serve` / `specter serve` /
  `scarlet serve` commands.

**Done when**: `claude mcp list` shows one khimaira entry with all tools.

### Phase 1.0 — MCP-first self-configuration  (3-5 days)

Today: user runs `khimaira bootstrap`. After: user adds one MCP line,
khimaira configures itself.

- Expose `setup_status`, `setup_run`, `setup_diagnose`, `setup_heal`,
  `setup_attach` as MCP tools wrapping existing bootstrap/doctor/heal.
- First-run detection: on MCP connect, if config is incomplete, surface
  a startup notice the calling agent can read.
- Tool descriptions tuned so the agent naturally walks the user through
  setup conversationally.
- Shell commands remain for non-MCP scenarios.

**Done when**: A new user adds `"khimaira": { "command": "uvx", "args":
["khimaira", "mcp"] }` to their MCP config, restarts their
editor, and is fully set up after a 3-message conversation.

### Phase 1.1 — Protocol documentation  (2-3 days)

- `docs/PROTOCOL.md`: HTTP API, MCP tool surface, CLI commands,
  stability tiers (API-frozen vs experimental).

**Done when**: An adapter author can read this doc and integrate their
tool without reading khimaira's source.

### Phase 1.2 — Subagent library  (3-4 days, split 1.2a + 1.2b)

`~/.claude/agents/khimaira-*.md` curated set, each pinned to the right
model. Real thinking-token interception inside Claude Code.

**1.2a — ship the agents** (done 2026-05-13):

- Tight MVP set: khimaira-factual (haiku), khimaira-code-fast (haiku),
  khimaira-research (sonnet), khimaira-deep-debug (opus).
- Shipped via the bootstrap framework (dotfiles symlink).
- Spec: `tasks/subagent-library/IMPLEMENTATION.md`.

**Done when (1.2a)**: From a fresh Claude Code session, invoking
`@"khimaira-factual (agent) ..."` runs the response on Haiku
(verified via `/agents` listing + transcript model field). ✅

**1.2b — record dispatches in `usage.jsonl`**:

- Add `"subagent"` to the `Mode` Literal in `khimaira_types/usage.py`.
- New `SubagentStop` hook writes a `UsageRecord` per dispatch.
- `khimaira usage savings` includes subagent rows in its tally.
- Spec: `tasks/subagent-usage-hook/IMPLEMENTATION.md`.

**Done when (1.2b)**: Opus delegates a trivial prompt to a haiku-backed
subagent automatically, and the savings command shows the dispatch as
a `mode="subagent"` row. ✅ (verified 2026-05-13 — one khimaira-factual
dispatch produced a haiku record showing 94.7% savings vs the Opus
baseline.)

**1.2c — full subagent set** (done 2026-05-13): khimaira-grep (haiku),
khimaira-code-deep (sonnet), khimaira-architect (opus), khimaira-debug
(sonnet, distinct from deep-debug — first-pass before escalation).
Shipped same path as 1.2a (dotfiles symlink). ✅

### Phase 1.3 — PreToolUse interceptor v1  (3-4 days)

Hook that detects "Opus is about to do trivial work" and softly
suggests delegation. v1 passive (suggest). v2 (later) block-with-override
once heuristic is calibrated.

**Done when**: After a week of real traffic, we have data on leakage
rate and mis-route rate. Decision on v2 is informed by data, not vibes.

### Phase 1.5 — Cross-machine task dispatch  (3-5 days)

> **Scope-corrected 2026-05-13.** Original spike + my first scope-lock
> assumed full state replication (9-10 days). Joseph clarified the
> actual use case: he wants to assign a piece of work to another
> machine with the context bundle attached. That's a much smaller
> build. Full state replication is deferred to Phase 1.5b, only if
> the MVP proves demand for it.
> See spike: `tasks/cross-machine-backend/IMPLEMENTATION.md`.

**The use case** (real, as of 2026-05-13): Joseph runs khimaira on
desktop + laptop. He's working on the desktop, realizes "the laptop
has the Postgres replica already loaded — run the integration suite
there." Today: SSH in, open Claude Code, paste prompt, wait, copy
back. Phase 1.5 closes the loop:

```bash
# On the desktop:
khimaira assign laptop "Run integration suite, report results" \
  --ref tests/integration/test_dispatch.py \
  --ref tests/integration/test_runners.py

# On the laptop, on next SessionStart:
# 📦 khimaira tasks assigned to this machine (1):
#   Task: Run integration suite, report results
#   Context: [tests/integration/test_dispatch.py + 1 more, bundled inline]
# → agent picks it up, runs the work, posts a notice back.
```

**Phase 1.5a — task dispatch MVP** (3-5 days):

The insight: a handoff with `assignee` + `context_refs` IS a task.
Khimaira already has handoffs.jsonl + `attached.json` (project label
registry) + the SessionStart hook that surfaces matching handoffs.
The MVP wires the missing parts.

1. **Schema extension** (1d) — `handoffs.jsonl` records gain optional
   `assignee` (project label), `context_refs` (tagged-union list:
   file / commit / url / inline), `pulled_by` / `pulled_at`.
   Backward-compat: old records load with defaults.

2. **Context-ref resolver** (1d, same day) — sender resolves file refs
   into bundled payload at post time (receiver may not have the same
   working tree). Size cap (~1MB) with clear error.

3. **Pull + claim endpoints** (1d) —
   `GET /api/handoffs/pending?assignee=<label>` for the receiver to
   discover, `POST /api/handoffs/{id}/claim` for atomic FCFS-claim
   when multiple sessions could pick it up.

4. **CLI** (1d, same day) — `khimaira assign <label> "<task>"
   --ref <files...>` on the sender, `khimaira pull` on the receiver
   (auto-runs in SessionStart, also available explicitly).

5. **SessionStart hook extension** (1d) — pull `assignee=<this-label>`
   handoffs alongside cwd-scoped ones. Render the context bundle
   inline so the agent can act without further fetches.

6. **Tests + smoke** (1-2d) — unit + integration. End-to-end smoke:
   desktop assigns to laptop, laptop pulls and runs, sender sees a
   result notice.

Decisions baked in:
- **Assignee = project label** (not machine identifier, not username).
  Already exists in `attached.json`; machine-stable across reinstalls;
  trivially extends to multi-user with namespacing (`alice/proj`).
- **Context refs are tagged-union** (file / commit / url / inline) —
  file kind bundles contents at post time so receiver doesn't need
  the same working tree.
- **Claim race resolution: FCFS at the daemon level.** Two machines
  with the same label race; whoever POSTs `/claim` first wins.
- **No machine identity required.** Daemon doesn't need to know which
  host is asking — labels are the routing primitive.

**Done when (1.5a)**: `khimaira assign laptop "<task>" --ref <files>`
from the desktop surfaces on the laptop's next SessionStart with the
context bundle inline. Laptop's agent picks it up, runs it, posts a
result notice back. Auto-claim prevents duplicate execution when the
laptop has multiple sessions in the same project.

**Open questions for the implementer** (not blockers — sane defaults
documented in the spike):
- Context-bundle size cap policy (hard limit vs. extension allowlist)
- Result reporting shape (free-form notice vs. structured `result`
  field on the handoff record — v2 makes it structured)
- Per-handoff TTL override for "do this in the next hour or skip" semantics

**Phase 1.5b — Full state replication** (deferred indefinitely, only
if the MVP shows demand):

The original 9-10 day spike — `StateClient` abstraction, consolidated
read endpoint, write-queue, `KHIMAIRA_BACKEND_URL` plumbing,
Postgres-backed shared store. Keeps `tasks/cross-machine-backend/IMPLEMENTATION.md`
content alive in case it becomes the right answer later. Trigger to
revisit: real evidence that cross-machine session state (not just
task dispatch) is daily-use friction.

**Strategic position**: Phase 1.5 is now small enough that it doesn't
fight Phase 2 (cross-editor) for sequencing. Either can ship first.
Default order keeps 1.5 before 2 because task dispatch is Joseph's
daily-use need *today*; cross-editor is the launch-story need *later*.

### Phase 2 — Cross-editor adapter configs  (1-2 weeks)

`contrib/` examples, not khimaira core. Demonstrates that the protocol
is genuinely cross-editor.

- 2.1 Cursor (`~/.cursor/mcp.json` snippet + `.cursorrules` example)
- 2.2 Neovim (avante.nvim + codecompanion.nvim provider configs)
- 2.3 VS Code Cline / Continue (custom instructions + MCP entry)
- 2.4 aider (LiteLLM provider config)
- 2.5 `docs/INTEGRATING.md` — the canonical "integrate khimaira into
  your AI tool" guide

**Done when**: Three reference adapters exist + an outsider can write
a fourth in an afternoon using just the guide.

### Phase 3 — Open-source distribution  (1 week)

- 3.1 PyPI package (decide name; bare `khimaira` is taken)
- 3.2 README rewrite — lead with savings, frame as orchestration layer
- 3.3 Community profile (`khimaira-profile.yaml` pointing at public repos)
- 3.4 Demo assets (GIFs + 3-minute walkthrough)

**Done when**: `uvx khimaira mcp` works on a fresh laptop.
README pitches the editor-agnostic story. Someone who saw an HN post
can install and see savings the same day.

### Phase 4 — Stretch (do once 0-3 ship)

- 4.1 Claude Agent SDK investigation (subscription auth + per-call
  model swap path for khimaira dispatches)
- 4.2 Transcript-scrape Opus-direct baseline (Phase 4 from peer review)
- 4.3 PreToolUse interceptor v2 (block-with-override)
- 4.4 Web dashboard polish (savings graphs, audit log viewer, handoff
  visualization)

---

## What we're explicitly NOT building

These are tempting but violate the principles above. Re-evaluate only
with strong evidence.

- **A khimaira-specific TUI.** The web dashboard at `localhost:8740/`
  covers the visibility need, editor-agnostically. A TUI couples us to
  terminal users at the expense of everyone else.
- **A Neovim/Cursor/VS Code plugin in khimaira core.** Adapters live in
  `contrib/` as configs, not as plugins we maintain. Community can
  build plugins on top of the protocol.
- **"Be the editor" (khimaira-tui, khimaira-ide).** Six-month project,
  fights Anthropic on distribution, can't use Claude Pro subscription
  auth cleanly. Wrong fight.
- **A separate MCP server per capability.** seance/specter/scarlet are
  capabilities of khimaira, not peer servers. One MCP, many tools.
- **Re-inventing classification/routing logic in each editor adapter.**
  Routing lives in khimaira core. Adapters call it.
- **Locking in to one provider.** Anthropic, Google, OpenAI, local —
  the pool is provider-agnostic. Anything that ties us to one
  provider's auth model fails the editor-agnostic test.

---

## Open operational debt (not yet phased)

These don't belong in any specific phase but need to be addressed before
the open-source launch. Most are 1-2 hour items that pile up if ignored.

### Immediate (this cycle)

- **Commit + push the auto-mode work shipped this session** — pool
  router, registry, `mcp__khimaira__auto`, mode field on UsageRecord,
  `khimaira usage savings` command, 19 new tests. Currently uncommitted.
- **README update for new features** — `mcp__khimaira__auto`,
  `khimaira usage savings`, the registry at `~/.khimaira/models.yaml`,
  the `mode` field on usage records. Current README pre-dates all of
  these.
- **`_COUNTERFACTUAL_MODEL` in `usage.py` is hardcoded** to
  `claude-opus-4-7`. Should be configurable via env var or registry
  override — different users have different "what would I have used
  instead" baselines.
- **Auto-route audit log lives only in `khimaira.log`** — grep-only,
  no structured viewer. Phase 4.4 (dashboard polish) addresses this;
  in the meantime, the `khimaira usage list --mode auto` command
  partially fills the gap.

### Quality / robustness gaps

- **No rate-limit / quota-exhaustion handling in dispatch path.** If
  a runner returns 429, khimaira surfaces the error to the caller but
  doesn't fall back to the next-cheapest. Should fall back; should
  also mark the runner cooled-down for N minutes.
- **No circuit breakers when a runner repeatedly fails.** Same shape
  as above — if `claude` is broken, khimaira should stop trying it for
  a window rather than failing every dispatch.
- **Pool router tie-break on cost is alphabetical.** Multiple equally
  cheap models (e.g., all-local) always route to the first
  alphabetically. Should weight by recent latency or rotate for load
  balancing.
- **Classifier quality determines mis-route rate** and we don't measure
  it. Phase 4.4 audit log viewer should expose this so we can iterate.
- **`_record_sync` (legacy LangChain callback) doesn't set `mode`.**
  Falls through to `unknown` via default. Fine for now; revisit if
  LangChain dispatches outlive Phase 10 (legacy removal).

### Test coverage gaps

- **`mcp__khimaira__auto` and `delegate` end-to-end tests.** The
  pool_router + savings paths are unit-tested but the MCP tool
  surface isn't.
- **Audit-log assertion tests.** We log classifier_confidence,
  pool_size, top_2, rejected. Nothing tests that those fields actually
  land in `khimaira.log` in the expected shape.
- **Bootstrap MCP self-config flow** (Phase 1.0) — once the tools
  exist, need end-to-end tests against a fresh fake config dir.
- **Cross-editor adapter smoke tests** — at least one CI job that
  runs against Cursor's CLI / aider's CLI to catch regressions in the
  protocol shape.

---

## Open questions (need answers before some phases proceed)

- **PyPI package name.** Bare `khimaira` is taken on PyPI. Candidates:
  `khimaira`, `khimaira-router`, `khimaira-ai`,
  `khimaira-mcp`. Pick before Phase 3.1.
- **License.** MIT (simple, permissive, common) vs Apache 2.0
  (patent grant, more enterprise-friendly) vs BSD-3. Default to
  MIT unless there's a reason not to.
- **Single repo vs split.** Should adapter configs ship in
  `khimaira/contrib/` or as a separate `khimaira-adapters` repo?
  Single repo is simpler for v1; split if maintenance load suggests
  it later.
- **Cursor MCP version compatibility.** Cursor's MCP support has
  shifted across versions. Need to verify the snippet we ship works
  against current Cursor before publishing Phase 2.1.
- **avante.nvim provider API.** Their provider abstraction may or
  may not match what khimaira exposes. Need to read avante source
  before scoping Phase 2.2.
- **Claude Code transcript format for Phase 4.2.** JSONL shape
  varies by Claude Code version. Need a stable parser before the
  savings command can include non-khimaira-routed dispatches.
- **Claude Agent SDK feasibility.** Does it allow per-call model
  swapping AND subscription auth? 1-day spike (Phase 4.1) blocks
  the decision on whether to migrate dispatch off the CLI-shell
  approach.

---

## Known gaps in current capabilities

Features that would be nice but aren't on a phase yet. Track here so
they don't get lost.

- **No per-project model budget enforcement.** `--budget` flag exists
  on `khimaira task` but not on `mcp__khimaira__auto`. Should surface.
- **No streaming for delegate responses.** Today the user waits for the
  whole answer; should stream when the underlying runner supports it.
- **No multi-turn conversation through `mcp__khimaira__auto`.** Each
  call is one-shot. Multi-turn would need session state on khimaira's
  side, which we have infrastructure for but haven't wired in.
- **No automatic model registry refresh.** When Anthropic / Google /
  OpenAI release new models, the user has to manually update
  `~/.khimaira/models.yaml`. A `khimaira models sync` command pulling
  from a curated upstream registry would help.
- **No prompt-caching awareness.** Anthropic offers prompt caching
  for repeated context. Our cost estimates don't account for it. Real
  savings are probably higher than reported when caching applies.
- **No team / multi-user mode.** Usage tracking is per-user. Teams
  wanting aggregate cost visibility don't have a path.

---

## Anti-goals (revisit only with strong evidence)

Documented as "we considered this and chose not to" so we don't
re-argue:

- **Building a khimaira TUI / IDE.** Six-month project, wrong fight,
  loses the "lives below the editor" frame.
- **Per-editor plugins maintained in khimaira core.** Adapter configs
  in `contrib/` only. Plugins (if built) live in separate repos by
  community.
- **API-SDK-based dispatch instead of CLI-shell.** Was considered for
  perf. Loses subscription auth, which is the whole point. May revisit
  per Phase 4.1.
- **Replacing the model registry YAML with a database.** YAML edits
  beat database migrations for a config file users edit by hand.

---

## Working notes

- Memory + persistent context live in
  `~/.claude/projects/-home--3ntropy-dev-khimaira/memory/`. See
  `MEMORY.md` for the index.
- Open task list is the source of truth for what's currently being
  worked on — `TaskList` from any session.
- Session coordination via `mcp__khimaira__session_*`. Cross-session
  handoffs surface in SessionStart hooks.
- Engineering rules: `CLAUDE.md` at repo root + the
  `~/.claude/rules/engineering/*.md` global set.

Last reviewed: 2026-05-12
