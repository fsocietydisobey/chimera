#!/usr/bin/env bash
# khimaira-role-reminder-broadcast — periodically re-fire v1.5 role-directive
# emits for every (chat, member, role) triplet where role has a recommended
# budget. The directive lands as a channel-block via SSE — same push mechanism
# as task assignments — so idle sessions wake up and surface the
# /model + /effort recommendation in real-time.
#
# Why this exists: v1.5 fires role-directives ONLY on role-CHANGE events
# (chat_create_room / chat_grant_role / chat_set_creator / chat_transfer_
# membership). For stable role assignments, the user never sees a push
# unless they actively submit a prompt (which UserPromptSubmit hook handles
# via v1.7.2). This watcher closes the "stable-role, idle-session" gap by
# re-firing the same directive periodically.
#
# Installed via systemd user timer: `khimaira-role-reminder.timer`.
# Log: ~/.local/state/khimaira/role-reminder.log
#
# To disable: `systemctl --user disable --now khimaira-role-reminder.timer`.

set -euo pipefail

TIMESTAMP=$(date -Iseconds)
LOG="$HOME/.local/state/khimaira/role-reminder.log"
mkdir -p "$(dirname "$LOG")"

# Use the khimaira venv's python so we can import the daemon-side
# `_emit_role_directive` helper. Same write path as v1.5's chat_create_room
# / chat_grant_role / etc. — appends a kind=msg system record with
# to=[member_id] (targeted SSE push, not broadcast).
~/dev/khimaira/.venv/bin/python3 - <<'PYEOF' 2>&1 | tee -a "$LOG"
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Import the daemon-side helper. This module is at the canonical path
# inside the installed khimaira package; .venv/bin/python3 has it on the
# import path because khimaira is installed editable from the repo.
try:
    from khimaira.monitor.chats import _emit_role_directive, ROLE_BUDGET
except ImportError as exc:
    print(f"[{datetime.now(timezone.utc).isoformat()}] import failed: {exc}", flush=True)
    sys.exit(1)

CHATS_DIR = (
    Path(os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state")))
    / "khimaira"
    / "chats"
)
ts = datetime.now(timezone.utc).isoformat()

if not CHATS_DIR.exists():
    print(f"[{ts}] no chats dir; skip", flush=True)
    sys.exit(0)

emitted = 0
skipped = 0
errors = 0
chat_count = 0

for chat_jsonl in sorted(CHATS_DIR.glob("*.jsonl")):
    chat_count += 1
    chat_id = chat_jsonl.stem
    last_meta = None
    try:
        with chat_jsonl.open() as f:
            for line in f:
                try:
                    r = json.loads(line)
                    if r.get("kind") == "meta":
                        last_meta = r
                except json.JSONDecodeError:
                    continue
    except OSError:
        errors += 1
        continue
    if not last_meta:
        skipped += 1
        continue
    member_roles = last_meta.get("member_roles") or {}
    if not member_roles:
        # v1-era chat without materialized member_roles — skip (no targets).
        # v1.6's as_deputize materializes; chats predating that won't have
        # role state to remind about until the next role-change event.
        skipped += 1
        continue
    for member_id, role in member_roles.items():
        if role not in ROLE_BUDGET:
            # Critic role (no default budget) — skip silently per v1.5 design.
            continue
        try:
            _emit_role_directive(chat_id, member_id, role)
            emitted += 1
        except Exception as exc:  # noqa: BLE001 — keep going on per-emit failure
            errors += 1
            print(f"[{ts}] emit failed {chat_id} {member_id[:8]} {role}: {exc}", flush=True)

print(
    f"[{ts}] role-reminder broadcast: "
    f"emitted={emitted} chats={chat_count} skipped={skipped} errors={errors}",
    flush=True,
)
PYEOF
