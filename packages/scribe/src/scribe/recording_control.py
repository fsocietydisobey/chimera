"""Start / stop the meeting recorder as a managed background subprocess.

The recorder (recorder.py) runs an audio-capture loop that exits on
SIGINT, saving a WAV file. For MCP-driven control we want to:
  - start the recording from one tool call
  - return immediately with a recording_id the user / agent can pass back
  - stop the recording from a separate tool call (different turn)
  - retrieve the saved file path

Approach: spawn `python -m scribe.cli record --output <path>` as a
subprocess, track its PID in a module-level dict keyed by the
recording_id, send SIGINT on stop, wait briefly for the output file to
materialize.

Single-process scope — assumes the khimaira MCP server is the only
caller (the dict isn't shared across processes). Active recordings
across daemon restarts are lost; this is a "live, drive-from-chat"
control surface, not durable scheduling. Multi-host or daemon-restart
durability is a future concern.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class _ActiveRecording:
    """In-memory record of one in-flight recording subprocess."""

    recording_id: str
    pid: int
    output_path: Path
    started_at: str
    proc: subprocess.Popen | None = field(default=None, repr=False)


_active: dict[str, _ActiveRecording] = {}


def _default_output_dir() -> Path:
    """Where recordings land by default. Matches the recorder's existing
    convention; keeps back-compat with the standalone CLI's files."""
    return Path.home() / ".local" / "share" / "meeting-scribe"


def start_recording(output_path: str | None = None) -> dict:
    """Spawn the recorder as a background subprocess.

    Returns a dict with `recording_id`, `output_path`, `pid`, `started_at`.
    The caller (typically an MCP tool wrapper) passes the recording_id
    back to `stop_recording` when ready to finish.
    """
    if output_path:
        out = Path(output_path).expanduser().resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
    else:
        out_dir = _default_output_dir()
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out = out_dir / f"meeting_{ts}.wav"

    recording_id = uuid.uuid4().hex[:12]
    started_at = datetime.now(timezone.utc).isoformat()

    # Spawn `python -m scribe.cli record --output <path>` so the existing
    # CLI handles device detection + SIGINT-to-save. Detached process
    # group so it doesn't inherit Claude Code's signal handlers.
    proc = subprocess.Popen(
        [sys.executable, "-m", "scribe.cli", "record", "--output", str(out)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )

    _active[recording_id] = _ActiveRecording(
        recording_id=recording_id,
        pid=proc.pid,
        output_path=out,
        started_at=started_at,
        proc=proc,
    )

    return {
        "recording_id": recording_id,
        "output_path": str(out),
        "pid": proc.pid,
        "started_at": started_at,
    }


def stop_recording(recording_id: str, *, wait_s: float = 10.0) -> dict:
    """Stop an in-flight recording. Returns the final output_path.

    Sends SIGINT to the subprocess (the recorder's SIGINT handler
    cleanly stops + saves), waits up to `wait_s` for the WAV file to
    appear, returns the result.

    Raises ValueError if recording_id is unknown.
    """
    rec = _active.get(recording_id)
    if rec is None:
        raise ValueError(
            f"unknown recording_id {recording_id!r} — "
            "either it was never started, or this MCP server process restarted."
        )

    # Send SIGINT to the whole process group (start_new_session above)
    # so any audio threads the recorder spawned receive the signal too.
    try:
        os.killpg(rec.pid, signal.SIGINT)
    except (ProcessLookupError, PermissionError) as exc:
        # Process gone — file might still be valid if it saved before we sent SIGINT.
        _active.pop(recording_id, None)
        return {
            "recording_id": recording_id,
            "output_path": str(rec.output_path),
            "stopped_cleanly": False,
            "warning": f"process not found at SIGINT time: {exc}",
        }

    # Wait for the subprocess to exit + the file to materialize.
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        if rec.proc is not None and rec.proc.poll() is not None:
            break
        if rec.output_path.is_file() and rec.output_path.stat().st_size > 1024:
            break
        time.sleep(0.2)

    _active.pop(recording_id, None)

    return {
        "recording_id": recording_id,
        "output_path": str(rec.output_path),
        "stopped_cleanly": rec.output_path.is_file(),
        "size_bytes": rec.output_path.stat().st_size if rec.output_path.is_file() else 0,
        "started_at": rec.started_at,
        "stopped_at": datetime.now(timezone.utc).isoformat(),
    }


def list_active_recordings() -> list[dict]:
    """Return metadata for every in-flight recording. Useful for debugging
    a session where the user lost track of which recording_id is active."""
    return [
        {
            "recording_id": r.recording_id,
            "pid": r.pid,
            "output_path": str(r.output_path),
            "started_at": r.started_at,
        }
        for r in _active.values()
    ]
