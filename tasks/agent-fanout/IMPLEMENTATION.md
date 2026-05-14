# Agent fan-out — master/agent delegation pattern

**Status**: spec + v1 implementation in flight 2026-05-14, session `khimaira-6`.

**Origin**: Joseph's request — one "master" Claude Code session leads a work-block; N "agent" sessions register as listening; master delegates parallel work, collects results, accepts or rejects with rework context.

---

## The shape

```
master window:                        agent-1 window:
  /listen master                        /listen agent-1
                                        (waits for incoming question)
  /delegate agent-1,agent-2,...
            "research X in feature Y;
             cite files + lines"

  → MCP fans out 4 targeted questions
  → master enters wait_for_answer
    on each (parallel)

                                      agent-1's UserPromptSubmit hook surfaces
                                        the question on its next turn
                                      user types anything to wake agent-1
                                      agent-1 does the research, calls
                                        session_post_answer → goes back to wait

  → master collects 4 answers
  → renders them
  → human (you) inspects and decides:

  /accept agent-1,agent-3 "good results"
  /reject agent-2 "missed file X — re-read its tests"
  /reject agent-4 "wrong feature, focus on auth/"

  → master fires FYI notices to accepted agents
  → master fires NEW targeted questions to rejected agents
    with rework context
```

## What already exists in khimaira

The primitives this builds on are all shipped:

| Need | Primitive |
|---|---|
| Agent advertises availability | `session_set_name(name)` + `session_set_status("listening", ...)` |
| Master sends task to agent | `session_log_question(target_session_id=<agent>, text=<task>)` |
| Master waits for one answer | `session_wait_for_answer(qid, timeout=900)` |
| Agent reports back | `session_post_answer(target=master, qid, answer)` |
| Master "accepts" (no-reply ack) | `session_post_notice(target=agent, text="accepted")` |
| Master "rejects" with rework | `session_log_question(target=agent, text=<rework>)` again |
| Master sees who's listening | `session_list()` — filter by status=="listening" |

This task's contribution is the **fan-out orchestrator** + the **slash commands** that wrap the natural workflow.

## Components

### 1. MCP tool — `mcp__khimaira__delegate_to_agents`

Single fan-out helper:

```python
async def delegate_to_agents(
    targets: list[str],          # ["agent-1", "agent-2", ...]
    task: str,                   # task description shared by all agents
    timeout: float = 900.0,      # per-agent wait timeout (15 min default)
    from_session_id: str = "",   # master's id (resolved from caller context)
) -> str:
    """Fan out a task to N listener agents in parallel.

    For each target:
      1. session_log_question(target=target, text=task) — get qid
      2. (parallel) session_wait_for_answer(qid, timeout) — collect answer

    Returns a JSON-encoded dict {target: answer or 'TIMEOUT' or 'ERROR'}.

    Master blocks until all targets answer OR each individual timeout fires.
    Use a generous timeout (default 15min matches /ask). Agents need to be
    manually woken (user types in each agent window) — they don't auto-fire.
    """
```

Lives in `packages/khimaira/src/khimaira/server/mcp.py` alongside the other `session_*` MCP tools. Internally uses `asyncio.gather` over the existing per-question wait calls.

### 2. Slash commands

`~/dotfiles/claude/commands/`:

| Command | Args | What |
|---|---|---|
| `/listen <name>` | name (default = current session id 8-char prefix) | `session_set_name` + `session_set_status("listening", ...)` |
| `/delegate <a1,a2,...> <task>` | comma-separated targets + free-form task | Calls `mcp__khimaira__delegate_to_agents`, renders the collected results |
| `/accept <a1,a2,...> <feedback>` | targets + optional feedback | Sends `session_post_notice` to each — "accepted: <feedback>" |
| `/reject <a1,a2,...> <rework>` | targets + rework context | Sends new `session_log_question` to each with rework framing referring to the previous turn |

### 3. Tests

`packages/khimaira/tests/test_delegate_to_agents.py`:
- Happy path: 2 mocked agents, both answer → master returns dict with both
- Timeout path: 1 agent doesn't answer in time → result includes 'TIMEOUT' for that target, others return cleanly
- Empty targets: returns immediately with empty dict (no question created)
- Single-target fan-out (degenerate case): works the same as multi-target

## Decisions

| Decision | Why |
|---|---|
| **Asyncio gather, not sequential** | Master needs to be able to fan-out 4 questions and collect them as they arrive. Sequential would force 60min worst-case wait when 3/4 agents are blazing fast. |
| **Per-target timeout (not aggregate)** | If one agent is slow, others still return clean. Aggregate timeout would force "everyone fast or all dropped." |
| **Result shape: dict of {target → answer/TIMEOUT/ERROR}** | Master can iterate + render per-agent. Agent failures don't contaminate other agents' results. |
| **Master gets one MCP call, hides 2N daemon round-trips** | Cleaner agent prompt + fewer points where Claude must remember to fire wait_for_answer. |
| **Wake-up is manual** | Each agent's UserPromptSubmit hook surfaces the question only when the user types in that window. Acceptable trade-off — anything more would require either polling (wasted compute) or push notifications (which we don't have). User wake-up is the natural sync point. |
| **Listening status isn't enforced** | `/delegate` could *check* that each target has status="listening" before firing, but doesn't require it. Master can delegate to any named session; the listening status is advisory. |
| **No new state file** | Pure stateless orchestration over existing JSONL state (questions.jsonl, answers, sessions). |

## Anti-patterns (don't)

- **Don't auto-wake agents.** Polling agents to make them answer creates feedback loops and burns compute. User-driven wake-up is the right rhythm.
- **Don't enforce target=listening.** Sometimes the user delegates to a session that's "researching" or "blocked" — they know it's still answerable.
- **Don't cache task text across agents.** Every target gets its own `session_log_question` with the task body. If shared context is huge, the master agent can write it to a tmp file and have each task reference the path (Path A in the spec).
- **Don't have master block on results forever.** Per-target timeout (default 900s) bounds the wait.
- **Don't promote acceptance/rejection to a state machine yet.** It's just chained questions today. If real-world usage shows multi-round rework is common, formalize then.

## v2 follow-ups (deferred)

### Cross-machine fan-out

Today, agent sessions need to be on the **same machine** as master — the daemon brokers via local HTTP at `127.0.0.1:8740`. Cross-machine fan-out (e.g. master on desktop, agents on laptop + cloud-VM-1 + cloud-VM-2) would need:

1. Daemon-level peer-to-peer (each daemon exposes a syncable endpoint, peers reconcile via PSK / Tailscale / etc.)
2. OR a central coordinator (one daemon per "cluster," other machines proxy through it)

Both are real infra commits — out of scope for v1 of this pattern. Noted as v2 work; revisit when a real multi-machine workflow surfaces. (User confirmed this is interesting but not blocking — single-machine fan-out is the immediate need.)

### Other v2 work

- **Status-gated delegation**: `delegate_to_agents` could filter targets by `status=="listening"` and surface `"agent-X is busy (status=researching), skipping"` — currently best-effort, fires regardless.
- **Persistent task state**: store fan-out tasks (parent question_id + child target → child question_id) in a `tasks.jsonl`. Useful for resumption + audit. Today the relationship is encoded only in question text references.
- **Context-handle primitive**: master writes shared context once, distributes a handle; each agent reads via handle. Reduces re-tx of large prompts. Today: master writes a tmp file, agents read it.
- **Streaming partial results**: agent can `session_post_notice("status: 30% done")` mid-task; master surfaces. Today: agents only respond when fully done.

## File map (v1)

```
packages/khimaira/src/khimaira/server/mcp.py
  + @mcp.tool() async def delegate_to_agents(targets, task, timeout)

packages/khimaira/tests/test_delegate_to_agents.py (new)
  + 4 tests: happy path, timeout, empty targets, single-target

~/dotfiles/claude/commands/listen.md
~/dotfiles/claude/commands/delegate.md
~/dotfiles/claude/commands/accept.md
~/dotfiles/claude/commands/reject.md

tasks/agent-fanout/IMPLEMENTATION.md (this file)
```

## Done when

- `/listen agent-1` in agent windows registers them as listening (visible via `session_list`)
- `/delegate agent-1,agent-2,agent-3,agent-4 "task X"` from master fires 4 questions + waits in parallel
- After agents are woken (user types in each window), master collects N answers and renders them
- `/accept` and `/reject` close the loop with FYI or rework
- Unit tests cover the fan-out helper (mock the per-question wait)
- Spec is the source of truth — anything that surprises a future reader gets added here
