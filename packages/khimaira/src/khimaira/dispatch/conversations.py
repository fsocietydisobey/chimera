"""Conversation history for multi-turn `mcp__khimaira__auto` (#56).

Today every `mcp__khimaira__auto(prompt)` call is one-shot — the runner
sees only the current prompt with no prior context. For follow-up
questions ("now also do Y", "what about the case where Z"), the agent
has to re-explain the whole setup each time, burning tokens on
preamble that should be implicit.

This module externalizes per-conversation history into
`~/.local/state/khimaira/conversations/<task_id>.jsonl`. The dispatch
path loads history if `continue_task_id` is set, prepends it to the
current prompt, then appends the new turn after the response lands.

Design notes:
  - Cross-runner: works on every runner because we prepend to the
    prompt string itself, no runner-side session API required. Runners
    that DO have native multi-turn (Claude's `--session-id`) get a
    redundant-but-correct prompt; runners that don't (ollama, llm) get
    the only mechanism they have.
  - One conversation == one JSONL file. Append-only; latest-wins on
    crash. No global lock — concurrent appends from the same task_id
    would interleave but that requires two callers using the same id,
    which the user controls.
  - File path keyed by `task_id`, not `continue_task_id` — the field
    on the dispatch is "the conversation I'm continuing," and that ID
    becomes the task_id of every turn in the same conversation.
  - Each turn is one JSONL line: `{"ts": ..., "user": ..., "assistant": ...}`.
    No nesting — keeps the read+append cheap and the file
    grep-friendly.

The caller picks `task_id` themselves (a free-form string — typically
"<cwd>/<feature>" or whatever's stable across the agent's mental
model of "the same conversation").
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from khimaira.log import get_logger

log = get_logger("dispatch.conversations")


_CONVERSATIONS_DIR = (
    Path(os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state")))
    / "khimaira"
    / "conversations"
)

# Sanitize task_id → filename. Allow alphanumeric, dash, underscore,
# dot, forward-slash (which we then replace). Reject anything else —
# don't trust a free-form string with filesystem semantics.
_SAFE_TASK_ID = re.compile(r"^[A-Za-z0-9._/\-]{1,200}$")


def _conversation_path(task_id: str) -> Path:
    """Return the JSONL path for a task_id, or raise if the id is unsafe."""
    if not _SAFE_TASK_ID.match(task_id):
        raise ValueError(
            f"invalid task_id {task_id!r} — must match {_SAFE_TASK_ID.pattern}"
        )
    safe = task_id.replace("/", "__").replace("..", "_")
    return _CONVERSATIONS_DIR / f"{safe}.jsonl"


def load_history(task_id: str) -> list[dict]:
    """Read all prior turns for this conversation. Returns [] if no
    history file exists or the file is empty.

    Each list element is a dict with `user` and `assistant` fields,
    in chronological order (oldest first).
    """
    if not task_id:
        return []
    try:
        path = _conversation_path(task_id)
    except ValueError as exc:
        log.warning("conversations: %s", exc)
        return []
    if not path.is_file():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        out.append(rec)
    return out


def append_turn(task_id: str, user: str, assistant: str) -> None:
    """Append one turn to the conversation. Safe to call even when no
    prior history exists — creates the file on first append."""
    if not task_id:
        return
    try:
        path = _conversation_path(task_id)
    except ValueError as exc:
        log.warning("conversations: %s", exc)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    rec = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "user": user,
        "assistant": assistant,
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, separators=(",", ":")) + "\n")


def render_history_as_prompt_prefix(history: list[dict]) -> str:
    """Format prior turns as a prompt prefix the runner can ingest.

    Returns an empty string if history is empty.

    Format intentionally simple — works on every runner without
    relying on a model-family-specific chat-template format:

        [conversation history]
        User: <prior prompt 1>
        Assistant: <prior response 1>

        User: <prior prompt 2>
        Assistant: <prior response 2>

        [/conversation history]
    """
    if not history:
        return ""
    lines = ["[conversation history — context for this turn]"]
    for turn in history:
        lines.append("")
        lines.append(f"User: {turn.get('user', '')}")
        lines.append(f"Assistant: {turn.get('assistant', '')}")
    lines.append("")
    lines.append("[/conversation history]")
    lines.append("")
    return "\n".join(lines) + "\n"


def clear_conversation(task_id: str) -> bool:
    """Delete the history file for a conversation. Returns True iff a
    file was actually removed."""
    if not task_id:
        return False
    try:
        path = _conversation_path(task_id)
    except ValueError:
        return False
    if path.is_file():
        path.unlink()
        return True
    return False
