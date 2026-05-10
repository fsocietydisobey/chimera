"""Watch Claude Code transcripts for /rename events; sync to chimera session names.

Closes the latency gap between `/rename foo` in Claude Code and the new
name being addressable from other chimera sessions. Without this watcher,
the UserPromptSubmit hook handles sync — but only fires on the next user
prompt. With the watcher, names sync within ~100ms of the rename hitting
the transcript file.

Architecture mirrors `attach_supervisor.watch_loop`: an asyncio task on
the daemon's event loop using `watchfiles.awatch` for cross-platform
inotify/fsevents.

What it does NOT do:
  - Sync if chimera already has an explicit name (set via MCP tool).
    The watcher only FILLS IN missing names; it never clobbers
    deliberate set_name calls. Explicit > inferred.
  - Watch transcripts in directories the user hasn't opened claude in.
    The walk starts from ~/.claude/projects/; if you're on a non-
    default config, set CHIMERA_CLAUDE_PROJECTS_DIR.
  - Block daemon startup if Claude Code isn't installed. Missing
    projects dir is a silent no-op.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from chimera.log import get_logger
from chimera.monitor import sessions

log = get_logger("monitor.transcript_watcher")

_CLAUDE_PROJECTS_DIR = Path(
    os.environ.get(
        "CHIMERA_CLAUDE_PROJECTS_DIR",
        os.path.expanduser("~/.claude/projects"),
    )
)

# Per-session debounce — Claude Code writes transcripts on every turn,
# but custom-title changes are rare. Don't re-scan unchanged files.
_last_synced: dict[str, str] = {}


async def watch_loop() -> None:
    """Long-running: watch Claude Code project dirs; sync rename events.

    Safe to run as a daemon background task. Tolerates the projects dir
    not existing (no-ops); tolerates per-file scan errors (logs + continues).
    """
    if not _CLAUDE_PROJECTS_DIR.exists():
        log.info(
            "transcript_watcher: %s doesn't exist; not starting (this is "
            "fine if Claude Code isn't installed yet)",
            _CLAUDE_PROJECTS_DIR,
        )
        return

    try:
        from watchfiles import awatch
    except ImportError:
        log.warning(
            "transcript_watcher: watchfiles not installed; rename sync "
            "will be deferred to UserPromptSubmit hook on next prompt"
        )
        return

    log.info("transcript_watcher: watching %s for /rename events",
             _CLAUDE_PROJECTS_DIR)

    # Initial pass — sync any names that already exist but chimera doesn't
    # know about. Catches the case where the user renamed a session before
    # the daemon was running.
    try:
        _initial_pass()
    except Exception as exc:
        log.warning("transcript_watcher: initial pass failed: %s", exc)

    async for changes in awatch(
        _CLAUDE_PROJECTS_DIR,
        recursive=True,
        debounce=200,  # ms — coalesce rapid writes from a single turn
    ):
        for _change_type, path_str in changes:
            try:
                path = Path(path_str)
                if not path.name.endswith(".jsonl"):
                    continue
                if not path.is_file():
                    continue
                session_id = path.stem  # filename without .jsonl
                _maybe_sync_name(session_id, path)
            except Exception as exc:
                log.warning(
                    "transcript_watcher: error processing %s: %s",
                    path_str, exc,
                )


def _initial_pass() -> None:
    """One-shot scan at startup — sync any pre-existing /rename events.

    Bounded to recently-modified transcripts (last 24h) so we don't
    re-scan every transcript ever on every daemon start.
    """
    import time
    cutoff = time.time() - 86400  # last 24h
    for project_dir in _CLAUDE_PROJECTS_DIR.iterdir():
        if not project_dir.is_dir():
            continue
        for transcript in project_dir.glob("*.jsonl"):
            try:
                if transcript.stat().st_mtime < cutoff:
                    continue
                _maybe_sync_name(transcript.stem, transcript)
            except OSError:
                continue


def _maybe_sync_name(session_id: str, transcript: Path) -> None:
    """Read transcript for latest /rename; call set_name if different.

    Only syncs if chimera DOESN'T already have a name for this session.
    Explicit `session_set_name` calls win over Claude Code's /rename;
    the watcher only fills in defaults.
    """
    latest_title = _find_latest_custom_title(transcript)
    if not latest_title:
        return

    # Debounce — skip if we already synced this exact title for this session
    if _last_synced.get(session_id) == latest_title:
        return

    # Check current chimera state. If session already has a name, don't
    # clobber it. If it doesn't exist in chimera yet, we'll create it
    # via set_name (which writes status.json).
    try:
        state = sessions.state(session_id)
        current_name = (state.get("status") or {}).get("name") or ""
        if current_name:
            # Already named explicitly — record so we don't re-check
            _last_synced[session_id] = current_name
            return
    except ValueError:
        # Session doesn't exist in chimera yet — that's fine, set_name
        # will create the status.json.
        pass

    try:
        sessions.set_name(session_id, latest_title)
        _last_synced[session_id] = latest_title
        log.info(
            "transcript_watcher: synced %s → %s",
            session_id[:8], latest_title,
        )
    except Exception as exc:
        log.warning(
            "transcript_watcher: set_name failed for %s: %s",
            session_id, exc,
        )


def _find_latest_custom_title(transcript: Path) -> str | None:
    """Scan transcript JSONL for the most-recent {type: 'custom-title'} entry.

    Reads the whole file because:
      - Most transcripts are <100MB
      - custom-title entries are rare (~1-5 per session typically)
      - Reverse-iteration is more complex than a single forward pass
      - We don't run this on the hot path — only on file changes

    Returns the title string, or None if no custom-title entry found.
    """
    try:
        latest: str | None = None
        with transcript.open("r", encoding="utf-8") as f:
            for line in f:
                if '"custom-title"' not in line:
                    continue
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("type") != "custom-title":
                    continue
                # Possible shapes seen in Claude Code transcripts:
                #   {"type": "custom-title", "title": "..."}
                #   {"type": "custom-title", "customTitle": "..."}
                title = (
                    rec.get("title")
                    or rec.get("customTitle")
                    or rec.get("name")
                    or ""
                )
                if title:
                    latest = title
        return latest
    except OSError:
        return None
