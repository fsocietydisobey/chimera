# Session handoff — afternoon of 2026-05-13

Latest state of the khimaira project at session boundary. Read this
first if you booted into this directory fresh.

## TL;DR

- ✅ The chimera→khimaira rename is **complete end-to-end** (code, repo,
  GitHub remote, state migration, MCP server, hooks, systemd, SPA,
  dotfiles, `~/.config/khimaira/`). 147 tests pass on main.
- ✅ Auto-mode shipped earlier today: pool router, registry, `mcp__khimaira__auto` MCP
  tool, audit-trail logging, `khimaira usage savings` command with
  configurable baseline (`CHIMERA_USAGE_BASELINE_MODEL` env or registry
  override).
- ✅ New personal rule landed: "Asking questions well" in
  `~/dotfiles/claude/rules/personal/approach.md` — frame the question
  with the context the reader needs before the decision point.
- 🟡 Open task list is 30+ items; cadence is pinned for two upcoming
  work blocks (see below).

## Read these first

1. **`NORTH_STAR.md`** — vision, phases, principles, anti-goals (last
   reviewed 2026-05-12). The header is the test for every decision.
2. **`CLAUDE.md` (repo root)** — engineering rules captured from real
   bugs we shipped. Especially the cross-session coordination section.
3. **`HANDOFF-TO-KHIMAIRA.md`** — historical doc from the rename
   cutover. Most of it is now reality, but useful if something looks
   off and you want to compare to the planned migration.
4. **This file** — current state + suggested next actions.

## What just shipped (today, 2026-05-13)

Commits on `main`:
- `5a8f6aa` feat(dispatch): model registry + capability-aware pool router
- `e7cab2d` feat(server): `mcp__khimaira__auto` + audit logging
- `cb768ea` feat(usage): mode field on UsageRecord + savings command
- `485e670` docs: NORTH_STAR.md
- `0e630f4` feat(usage): configurable savings baseline
- `dd37684` rename: chimera → khimaira (codebase sweep)
- `6135805` docs: HANDOFF-TO-KHIMAIRA.md
- `cde3d77` Merge branch 'rename/khimaira'

Plus in dotfiles: `197194d` rules(approach): "Asking questions well".

## Pinned cadence (carry over to your work)

- **Task #62** (target 2026-05-14 evening): reframe `NORTH_STAR.md` so
  Phase 0 (Unify MCP registration) moves to "deferred indefinitely"
  per Joseph's call. Community profile (`khimaira-profile.community.yaml`).
  Document install-story-via-bootstrap. Validate on a clean env.
- **Task #63** (target 2026-05-14 morning): daytime work — small,
  mechanical, pause-resumable:
  - `#51` Update README for auto-mode + savings features
  - `#33` Investigate Claude Agent SDK for dispatch path (1-day spike)
  - `#60` Per-project budget for `mcp__khimaira__auto`
  - `#54` End-to-end tests for delegate + auto

## Open task summary (full list via TaskList)

**Operational debt** — small, mostly mechanical:
- `#51` README update
- `#52` Rate-limit / quota-exhaustion handling in dispatch
- `#53` Circuit breakers for repeatedly-failing runners
- `#54` E2E tests for `mcp__khimaira__auto` + delegate
- `#55` Streaming responses through delegate / auto
- `#56` Multi-turn `mcp__khimaira__auto`
- `#57` `khimaira models sync` (upstream registry refresh)
- `#58` Prompt-caching awareness in cost estimates
- `#60` Per-project budget for `auto`

**Phase 1 — Foundation** (NORTH_STAR roadmap):
- `#49` Phase 1.0 MCP-first self-configuration tools
- `#37` Phase 1.1 Document & stabilize the chimera protocol
- `#34` Phase 1.2 Subagent library (`~/.claude/agents/khimaira-*.md`)
- `#35` Phase 1.3 PreToolUse interceptor v1

**Phase 2 — Cross-editor adapters** (`contrib/`):
- `#38–#42` Cursor, Neovim, VS Code Cline/Continue, aider, INTEGRATING.md

**Phase 3 — Open-source distribution**:
- `#43–#46` PyPI package, README rewrite, community profile, demos

**Phase 4 — Stretch**:
- `#32` Phase 4.2 Opus-direct transcript baseline (Phase 4 from peer review)
- `#33` Phase 4.1 Claude Agent SDK investigation
- `#47` Phase 4.4 Web dashboard polish

**Maintenance**:
- `#50` Keep NORTH_STAR.md in sync as the plan evolves

**Completed (don't re-do)**:
- `#27` Model registry — done
- `#28` Pool router — done
- `#29` Mode field on UsageRecord — done
- `#30` Savings command — done
- `#31` `mcp__khimaira__auto` alias + audit — done
- `#59` Configurable counterfactual baseline — done
- `#61` PyPI name / license / repo structure — done (PyPI = `khimaira`,
  no suffix needed; license decision still pending — default MIT
  unless reason not to)
- `#64` Full rename — done

## Known issues to watch for

1. **MCP catalog gap after registration mid-session.** If the khimaira
   MCP server was registered with `claude mcp add` after Claude Code's
   initial catalog scan, `mcp__khimaira__*` tools won't appear in
   ToolSearch in that session. **Restart Claude Code to refresh.** Or
   work around via HTTP API at `localhost:8740` or
   `uv --directory ~/dev/khimaira run python -c "from khimaira.monitor import sessions; sessions.<fn>(...)"`.
   This is the most common friction point — flag it if you see it.

2. **Historical session names.** Many idle sessions are still named
   `chimera-extension`, `chimera-monitor`, etc. — baked into their
   `status.json` from before the rename. Harmless. Rename via
   `session_set_name` if visual consistency matters.

3. **Old project dir.** `~/.claude/projects/-home--3ntropy-dev-chimera/`
   has one orphaned transcript fragment. The khimaira project dir has
   the superset. Safe to delete when confident.

4. **License decision.** `#61` is partially resolved (PyPI name = bare
   `khimaira`), but the actual `LICENSE` file at the repo root still
   needs to be created. Default to MIT unless there's a reason not to.

## Cross-session etiquette (new rule, follow it)

From the just-landed `personal/approach.md` rule "Asking questions
well": when you `/ask`, `session_log_question`, or fire an
`AskUserQuestion`, **frame the question with the context the reader
needs before the decision point.** Don't compress to bare options.
The reader can't decide what you don't know unless they know what you
do know. Read the rule before you fire your first cross-session ask.

## Suggested first action

Pick the highest-leverage item from `#63` that fits your energy:

- If you have a ~1hr window: `#51` README update (chunk by section)
- If you have a ~2hr window: `#33` Agent SDK investigation (read docs,
  write 200-word memo on per-call model swap + subscription auth
  feasibility — gates the Phase 4.1 decision)
- If you have all day: `#34` Subagent library is the highest-impact
  next strategic build — real thinking-token interception in Claude
  Code via `~/.claude/agents/khimaira-*.md`

If none of those, scan `NORTH_STAR.md` for what's most aligned with
the current phase. The phased plan is the source of truth for
strategic ordering; this task list is the tactical breakdown.

## Things NOT to do

- Don't rebuild the rename — it's done end-to-end.
- Don't restart `khimaira-monitor.service` unless you have to — the
  in-memory state takes a few minutes to repopulate after restart
  (see CLAUDE.md note on "daemon restart wipes the in-memory
  heartbeat buffer").
- Don't tackle Phase 0 (Unify MCP registration). It's been
  consciously deferred — the bootstrap profile already provides the
  "one install" pitch. Revisit only with strong evidence.
- Don't compete with Claude Code as an editor (anti-goal in
  NORTH_STAR.md).
- Don't add features beyond what's on the phased roadmap without
  flagging it as scope creep first.

---

Last updated: 2026-05-13 (afternoon, post-rename cutover, post-rule-add).
Next handoff after the 2026-05-14 morning + evening sessions complete
or the major scope shifts.
