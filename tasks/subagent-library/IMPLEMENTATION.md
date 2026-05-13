# Subagent Library — `~/.claude/agents/khimaira-*.md`

> **Status:** 1.2a in progress (2026-05-13).
> NORTH_STAR Phase 1.2. Real thinking-token interception inside Claude
> Code via the user-scope subagent system.

## Problem

Opus is too expensive for trivial work, and users on a Pro subscription
burn their Opus weekly budget on prompts that a Haiku-class model would
answer just as well. The dispatch tools (`mcp__khimaira__auto`,
`mcp__khimaira__delegate`) exist but require the user to explicitly call
them — Opus doesn't reach for them on its own for "what does this
function do?" or "format this snippet."

Claude Code's subagent system (`~/.claude/agents/*.md`) fixes this at
the Claude Code layer: when a subagent's `description` matches an
incoming user prompt, Claude Code routes the work to the subagent and
**actually swaps the model** for that turn. No manual delegation by the
parent agent required.

## Design

Ship a curated set of `khimaira-*` subagents at user scope, each pinned
to the right model for its role. Claude Code's automatic delegation
does the routing; native model-swap captures the savings.

### Scope split

This is the **1.2a** scope. The full Phase 1.2 done-gate
(*"savings command shows the dispatch"*) requires usage-record
attribution, which lives in **1.2b** (separate task — `tasks/subagent-usage-hook/`).

Decision rationale: user-visible value (Opus → Haiku delegation) does
not depend on `khimaira usage savings` showing the line item. It
depends on the dispatch actually happening on the cheaper model.
Instrumentation is a research question (what does Claude Code's
`SubagentStop` hook actually expose?); the shipping value is not.

### Architecture

```
[ user prompts Claude Code parent (Opus) ]
        ↓
[ Claude Code reads ~/.claude/agents/*.md descriptions ]
        ↓ matches subagent
[ subagent spawned with declared `model:` ]
        ↓ runs body's system prompt
[ returns to parent (Opus) ]
```

No khimaira MCP involvement on the dispatch path. The model-swap is
Claude Code's responsibility.

### Subagent set — 1.2a

Four agents. Tight enough to ship + observe usage patterns; broad
enough to cover the four obvious archetypes (factual / code-fast /
research / escalation-debug).

| Name | Model | Description heuristic | Allowed tools |
|---|---|---|---|
| `khimaira-factual` | `haiku` | "Answer a factual or syntax-lookup question with no codebase reads required." | (none — knowledge-only) |
| `khimaira-code-fast` | `haiku` | "Make a small mechanical code edit (rename, format, one-liner fix) where the change is fully specified." | `Read, Edit, Glob, Grep` |
| `khimaira-research` | `sonnet` | "Investigate how something works across multiple files — trace data flow, find call sites, build context." | `Read, Glob, Grep, Bash, mcp__seance__semantic_search, mcp__seance__find_similar` |
| `khimaira-deep-debug` | `opus` | "Escalation: investigate a bug after a haiku/sonnet attempt got stuck. Hypothesis-driven, deep reasoning." | `Read, Edit, Bash, Glob, Grep, mcp__specter__debug_snapshot, mcp__specter__get_console_logs, mcp__specter__get_errors, mcp__specter__get_network_log` |

Deferred to 1.2b (after first set proves the pattern):
`khimaira-grep`, `khimaira-code-deep`, `khimaira-architect`,
`khimaira-debug` (as distinct from `deep-debug`).

### Naming

`khimaira-*`, not `chimera-*`. The rename is shipped; new artifacts
match the new namespace.

### Ship path

Subagents live in dotfiles, symlinked into `~/.claude/agents/` like
rules and commands already are.

1. New directory: `~/dotfiles/claude/agents/`
2. Four `.md` files: `khimaira-{factual,code-fast,research,deep-debug}.md`
3. Bootstrap profile update — one new symlink entry:
   `{ src: claude/agents, dest: ~/.claude/agents }`
4. `khimaira bootstrap` / `khimaira sync` picks it up on next run

### Subagent file format

Confirmed from Anthropic docs (https://code.claude.com/docs/en/sub-agents.md):

```yaml
---
name: khimaira-factual
description: Answer a factual or syntax-lookup question with no codebase reads required. Use for "what does X mean", "is this syntax valid", "what's the difference between A and B" type questions.
tools:
model: haiku
---

System prompt body. Markdown. No length limit documented.
```

- `tools:` is a **comma-separated string**, not a YAML list. Empty or
  omitted = inherit all parent tools.
- MCP tools must be listed explicitly by name (`mcp__seance__semantic_search`) — they're blocked if not in the allowlist when `tools:` is specified.
- `model:` accepts aliases (`haiku`/`sonnet`/`opus`/`inherit`) or full
  IDs (`claude-haiku-4-5-20251001`). Aliases are preferred —
  forward-compatible.

## Implementation steps

1. ✅ Research subagent contract (confirmed via Anthropic docs).
2. ✅ Confirm bootstrap profile symlink pattern.
3. ✅ Confirm `UsageRecord` schema location (deferred to 1.2b).
4. ⏳ Write 4 subagent files under `~/dotfiles/claude/agents/`.
5. ⏳ Create `~/.claude/agents/` symlink → `~/dotfiles/claude/agents/`.
6. ⏳ Update `~/dotfiles/khimaira-profile.yaml` with the new symlink
   entry so future machines pick it up automatically.
7. ⏳ Smoke test: restart Claude Code, verify `/agents` shows the four
   subagents, confirm each is invokable (`@"khimaira-factual (agent)"`).
8. ⏳ File 1.2b follow-up task: `tasks/subagent-usage-hook/`.
9. ⏳ Update `NORTH_STAR.md` Phase 1.2 done-gate to reflect 1.2a/1.2b
   split.

## Tests

Per CLAUDE.md's "test the unhappy path" rule, three failure modes to
verify before declaring done:

- **Bad frontmatter:** subagent file with malformed YAML — Claude Code
  should refuse to load it without crashing. (Manual: corrupt one file,
  restart, see error surface.)
- **Unknown model alias:** `model: gpt-4` (not a Claude alias) —
  should error at agent-load time, not at first invocation.
- **Tool not in allowlist:** subagent body asks for a tool that isn't
  declared — should refuse the tool call gracefully, not crash the
  subagent turn.

## Done when

1. Four `khimaira-*.md` files exist in `~/.claude/agents/` (via symlink).
2. `/agents` in Claude Code lists all four.
3. From a fresh Opus session, `@"khimaira-factual (agent)" what does the @classmethod decorator do?` returns a Haiku-model answer (verify via the response's transparency line, or check `~/.claude/sessions/<id>.jsonl` for the model used).
4. Bootstrap profile updated; `khimaira sync` on a fresh machine
   re-creates the symlink.
5. 1.2b follow-up task filed.
6. NORTH_STAR.md updated.

## Caveats

- **MCP catalog gap.** If a subagent's `tools:` includes
  `mcp__khimaira__*` and the parent Claude Code session's MCP catalog
  snapshotted before khimaira was registered, the subagent's tool
  call fails with "tool not found." Mitigation: 1.2a agents that
  reference khimaira MCP are `khimaira-research` (seance) and
  `khimaira-deep-debug` (specter) — not khimaira itself. If a user
  hits this, the answer is restart Claude Code.
- **Subagent transcripts are separate sessions.** They don't see the
  parent's conversation history — only the prompt routed to them.
  System prompts must be self-contained.
- **Description matching is heuristic.** Auto-routing depends on
  Claude Code's interpretation of the `description` field. Phrase
  each description as a clear, specific use-case to maximize the
  hit-rate for the intended pattern.

## References

- Subagent docs: https://code.claude.com/docs/en/sub-agents.md
- Khimaira dispatch: `packages/khimaira/src/khimaira/server/mcp.py:380` (delegate + auto)
- UsageRecord schema: `shared/types/src/khimaira_types/usage.py` (touched in 1.2b, not 1.2a)
- Bootstrap profile: `~/dotfiles/khimaira-profile.yaml`
- Bootstrap symlink logic: `packages/khimaira/src/khimaira/bootstrap/operations.py`
