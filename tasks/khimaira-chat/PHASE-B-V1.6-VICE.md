# PHASE-B v1.6 — Vice / Deputy pattern (master-as-bottleneck mitigation)

**Status**: spec (no code yet)
**Date**: 2026-05-15
**Author**: khimaira-0
**Trigger**: Joseph flagged during Round 9 close that multi-agent orchestration hits a "master-as-bottleneck" failure mode: when the master/orchestrator is busy answering the user's questions or doing research, agents stall waiting for outline-greenlights and done-task approvals.

## Top-line recommendation

Ship the deputy/vice pattern as **convention + two slash commands** built on shipped primitives. No new MCP tools required. No new role enum values. The protocol composes `chat_transfer_membership` (Phase B v1.2) + `chat_grant_role` (Phase B v2) + v1.5's role-directive emit, plus a "pause-and-handoff" workflow that doesn't put the donor into `transferred-out` state.

Per the gap-typology banked in `PHASE-B-V2-ROLES-AUDIT.md` postscript: master-as-bottleneck is an **application gap** — masters know they could delegate; they don't have the protocol ergonomics to do it at the right moment. The matched primitive shape is a just-in-time recommendation (slash-command surface that makes delegation a one-liner), not enforcement (auto-promotion on idle, conflict detection, etc.).

## The problem in concrete terms

Round 9 surfaced the pattern by living through it:

- khimaira-0 (Opus master) was simultaneously: (a) reviewing L1+L2 outlines + drafts, (b) answering Joseph's questions about programmatic /effort/model switching, (c) drafting 4 GitHub issues for the Anthropic outreach, (d) writing a Gmail draft, (e) setting up the systemd watcher timer.
- L1 + L2 done-confirmations arrived during (b)-(e); review was delayed.
- The team didn't stall — the lanes were still parallel — but the master's review pipeline became the critical path.

Naive scaling: add more agents → more work in flight → more reviews queued → master becomes more of a bottleneck. The pattern is structural, not accidental.

## The shape that doesn't work

- **Auto-promotion on idle.** Idle is hard to define correctly; false-fires cost more than they save.
- **Multi-master.** Explicitly rejected by the v2 audit (single-master invariant; see `PHASE-B-V2-ROLES-AUDIT.md` Group A A2).
- **Long-running deputy agent.** Hits the same context-bloat problem masters do — the deputy's context window fills, deputy becomes the bottleneck. Joseph's intuition: spawn fresh per handoff.
- **New role enum value.** v2 closed at 4 roles (master/agent/observer/critic) for a reason; deputy isn't conceptually distinct from "temporary master."

## The shape that works

A protocol layer that:

1. Master decides "I'm about to be busy" (a long question, deep research, off-keyboard for a meeting).
2. **Master signals Joseph (the human user) to spawn a fresh Claude Code session.** Master CAN'T spawn a new Claude Code window itself — only the user can. The deputize command fires a `PushNotification` + posts notices to all of Joseph's active sessions with the requested vice name and reason: `khimaira-0 requesting deputy: khimaira-0-vice — answering Joseph's question, expect 5-10min`.
3. Joseph opens a new Claude Code window in the project, names it the requested slug (`session_set_name`).
4. Master's `/khimaira-deputize` command, which has been polling `session_list` for the vice-name to register, detects the new session and proceeds:
   a. Triggers `chat_transfer_membership` for each chat the master is in, transferring master role to the vice.
   b. v1.5's role-directive emit fires automatically: the vice sees `🎚️ Role updated: you are now master. /model opus, /effort max.` in each chat.
   c. Master's status flips to `paused` (NOT `transferred-out` — donor stays alive).
   d. A meta record gets appended noting "this chat is in deputized mode; original master is X, current acting master is Y."
   e. Master posts a session_post_notice to the vice with context — which chats were transferred, which tasks are mid-flight, why master is pausing, what to prioritize.
5. Master goes to handle the interruption. Vice drives orchestration: reviews outlines, approves done tasks, can spawn its own sub-tasks.
6. Master returns. Types `/khimaira-resume`. This:
   a. Looks up "original master" from chat meta; verifies caller is that session.
   b. Fires the resumption primitive (paired `/khimaira-hand-back` from vice, OR a new `chat_resume_original_creator` MCP tool — see Open Design Questions §1).
   c. v1.5 role-directive fires for both sides (vice becomes agent or observer; master becomes master again).
   d. Master's status flips to `orchestrating` or whatever the in-flight detail is.

The whole thing is composition. No new mechanism for the core transfer; the new affordances are (a) the protocol UX, (b) the spawn-request signaling, (c) optionally the resume primitive if convention-only doesn't suffice.

### The spawn-request signaling (load-bearing)

Without an explicit signaling step, the deputize flow has a chicken-and-egg problem: master needs vice to exist BEFORE running deputize, but master can't tell Joseph "spawn a vice" without using a master action. The signaling closes that gap:

- **PushNotification**: lands on Joseph's phone + desktop. `"khimaira-0 wants you to spawn deputy <vice-name>: <reason>"`. Under 200 chars; one line; actionable.
- **session_post_notice to all of Joseph's active Claude Code sessions**: surfaces the request in any window Joseph is currently in. The notice text includes the suggested vice name (kebab-case slug) so Joseph can copy-paste into `session_set_name`.
- **Optional: chat broadcast**: a system message in each chat saying "khimaira-0 has requested deputization to <vice-name>" so peers see the round is paused.

Polling on the master side: after sending the signal, `/khimaira-deputize` polls `session_list` every 5-10 seconds for up to ~120 seconds. When the vice registers under the requested name, it proceeds. Timeout → emit a "deputization request timed out; vice never spawned" error and Joseph can rerun with the same name.

## Why convention-only is the right v1 shape

Per the application-gap → just-in-time-recommendation principle, the right primitive is the lightest mechanism that makes the action easy at the right moment. Two slash commands that wrap existing tool calls is the lightest such mechanism.

What we explicitly defer to a possible v1.7+:
- **Auto-detection of master overload.** Could be a daemon-side heartbeat that fires `notify-paused-master` if the master hasn't logged a chat or decision in N minutes while tasks are awaiting review. Useful if v1.6 reveals masters chronically forget to deputize.
- **Multi-chat batch deputization.** `/khimaira-deputize` currently iterates over all chats the master is in. If masters end up wanting to deputize FOR a specific chat but not others, add a `--chat <id>` flag.
- **Chained deputization.** Vice deputizes a vice-vice. Currently allowed by the mechanism (vice IS master in their chats; `/khimaira-deputize` works for them too) but no UX guidance. v1.7 could surface "deputization depth" in the chat meta for audit.
- **Conflict semantics if original master tries to deputize a second time.** Reject? Replace? v1.6 picks: reject with a hint to `/khimaira-resume` first.

## Slash command shapes

### `/khimaira-deputize <vice-name> [reason]`

- Required arg: the name of the new vice session (kebab-case slug).
- Optional arg: a free-form reason ("answering Joseph's question", "deep research on X", "off-keyboard 30min") that flows into the user-facing notification.
- Behavior:
  1. Resolve own session id; verify own status is not already `paused` or `transferred-out` (reject otherwise).
  2. Check `session_list` for an existing session named `<vice-name>`:
     a. If found and active → proceed to step 4.
     b. If not found → step 3 (request spawn).
  3. **Request spawn from user**:
     a. Fire `PushNotification(message="khimaira-0 requesting deputy: <vice-name> — <reason>", status="proactive")`.
     b. Post `session_post_notice` to every active session belonging to the user with body `"🪪 Deputize request from <my-name>: spawn a new Claude Code window in this project, then run session_set_name(name='<vice-name>'). Reason: <reason>."`.
     c. Poll `session_list` every 5s for up to 120s, looking for `<vice-name>` to register.
     d. Timeout → emit "deputization request timed out; vice never spawned. Rerun when ready." and stop.
  4. For each accepted chat where caller is master (creator OR holds `master` role via `member_roles`):
     a. Call `chat_transfer_membership(from_session_id=me, to_session_id=vice)` — atomic master-swap fires.
     b. v1.5 directive emit fires automatically (no separate call).
  5. Update own status to `paused` with detail `"deputized to <vice-name>; resume via /khimaira-resume"`.
  6. Post a session_post_notice to the vice with the deputization context (which chats, which tasks are mid-flight, why master is pausing, what to prioritize).
  7. Print summary to user (which chats transferred, vice name, how to resume).

### `/khimaira-resume`

- No args. Resumes from whatever vice currently holds master role across the donor's deputized chats.
- Behavior:
  1. Resolve own session id; verify status is `paused` (reject otherwise — caller is not currently deputized).
  2. For each chat the original master was in (lookup from session state):
     a. Call `chat_grant_role(by_session_id=me, target_session_id=me, role="master")` — wait, this requires caller to currently hold master. We don't.
     b. **Alternative**: vice has to call `chat_grant_role` to hand back. But vice might not be present.
     c. **Better**: master calls `chat_transfer_membership(from_session_id=vice, to_session_id=me)` — but transfer_membership requires `by_session_id` to be the master (vice).
     d. **Best**: a new helper that admin-style swaps creator back, similar to `chat_set_creator` but for resumption. Or: requires vice's cooperation (vice calls `chat_grant_role(me, "master")` on themselves to demote and promote-original).

The resume-side has a design issue. The mechanism either needs:
- Vice cooperation (vice runs a complementary `/khimaira-hand-back` slash command), OR
- An admin primitive like `chat_set_creator` but scoped to "I was the original creator; restore me."

Recommendation: ship `/khimaira-deputize` in v1.6 as one-way (master-to-vice); ship `/khimaira-resume` as either (a) requires vice's cooperation via a paired `/khimaira-hand-back`, OR (b) a new `chat_resume_original_creator` MCP primitive that's gated on the requesting session having been the original `created_by` before deputization.

Lean toward (b) — small new primitive, but it captures the resumption semantics cleanly. Equivalent in spirit to `chat_set_creator`'s admin-orphan-unlock for the transfer-out case; here it's "admin-resume for the paused case."

## Open design questions (for the round)

1. **Resumption primitive**: paired slash command (`/khimaira-hand-back` on vice) vs new MCP tool (`chat_resume_original_creator`)? The former is convention-only; the latter is a small mechanism addition but cleaner semantics.

2. **Meta tracking**: should chat meta carry `paused_original_master: <sid>` when deputized? Helps the resume primitive find the right session to restore. Adds a tiny schema field.

3. **Chats not transferred**: if the master is in chats they're NOT the master of, deputize is a no-op for those chats. Surface this in the user-facing summary so users don't think deputize failed?

4. **What does the vice know about the master's queued work?** The session_post_notice could include "tasks awaiting your review: [list]" so vice doesn't have to grep chat_task_status manually.

## Lanes for v1.6

| Lane | Owner | Scope |
|---|---|---|
| L1 | test-master | `/khimaira-deputize` slash command (`~/dotfiles/claude/commands/khimaira-deputize.md`); compose chat_transfer_membership; integrate v1.5 directive emit |
| L2 | test-agent | `/khimaira-resume` slash command + decide on resumption primitive (paired hand-back vs new MCP tool); ship the chosen mechanism |
| L3 | khimaira-0 | `docs/khimaira-chat.md` new section "Deputy / vice — pause-and-handoff orchestration"; spec-doc updates from any design decisions made during outline |
| L4 (optional) | test-agent-2 | Tests for the deputize/resume flow — round-trip happy path + edge cases (deputize-when-not-master, resume-when-not-paused, etc.) |

## Acceptance criteria

- `/khimaira-deputize <vice>` transfers master role across all of caller's chats; donor status flips to `paused`; vice receives directive emit per v1.5.
- `/khimaira-resume` (or paired flow) restores master role to the original master; vice demotes to agent or observer; status returns to active.
- Tests verify: round-trip happy path, deputize-when-already-paused rejection, resume-when-not-paused rejection, the multi-chat case.
- Docs describe when to use, when not to, and the trade-offs vs `/khimaira-transfer-session` (the existing terminal-handoff command).

## Why this is important enough to ship soon

- Multi-agent orchestration is the load-bearing pattern for khimaira's launch (per `tasks/launch/PLAN.md`). Master-bottleneck is the most visible failure mode for the pattern.
- Future rounds will hit this more, not less — as test-agent-2 demonstrated joining mid-round, the team is getting larger. Larger teams compound the bottleneck.
- The implementation is small (estimated <200 LOC of skill markdown + docs + optional 30 LOC for the resume primitive). Closing the application gap is cheap; deferring it is more expensive in compound terms.

## References

- [`PHASE-B-V2-ROLES-AUDIT.md`](./PHASE-B-V2-ROLES-AUDIT.md) — v2 roles model + Round 7-9 postscript banking the gap-typology
- [`PHASE-B-VISION.md`](./PHASE-B-VISION.md) — broader Phase B+ design space
- `docs/khimaira-chat.md` Token-cost budgeting + Surfaces wiring sections (v1.4 + v1.5)
- Phase B v1.2 commit `89b93ac` — chat_transfer_membership primitive (the mechanism deputize composes from)
- Phase B v1.3 commit `bd7f1af` — v1.3 fix that propagates master role on creator-transfer (load-bearing for deputize)
- Phase B v2 commit `29d901e` — chat_grant_role atomic promote-demote (load-bearing for resume)
- Phase B v1.5 commit `aa58930` — role-directive emit on role change (composes with deputize for free)
