"""Tests for scripts/hooks/session_start.py — HTTP-primary, file fallback.

The hook used to maintain file-direct duplicates of daemon logic
(_consume_inbox, _consume_handoffs, _discover_other_active_sessions).
Each daemon-side change required a parallel hook update — 2 bugs in 24h
came from this drift. The refactor prefers HTTP and only falls back to
file-direct ops when the daemon is unreachable.

These tests verify both paths:
  - HTTP path: with the daemon responding, hook calls the daemon, period
  - Fallback: when daemon is unreachable, the file-direct path still
    archives inbox correctly + applies the target-session filter on
    handoffs (the two bugs we shipped in the last day).
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# The hook is a top-level script outside the package; load it by path so
# tests don't have to set up a sys.path shim.
_HOOK_PATH = (
    Path(__file__).resolve().parents[3] / "scripts" / "hooks" / "session_start.py"
)


@pytest.fixture
def hook_module(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Load session_start.py as a module with XDG_STATE_HOME isolated.

    Reloaded per-test so module-level path constants pick up the env var.
    """
    state_root = tmp_path / "state"
    state_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("XDG_STATE_HOME", str(state_root))

    spec = importlib.util.spec_from_file_location("session_start_hook", _HOOK_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["session_start_hook"] = mod
    spec.loader.exec_module(mod)
    yield mod
    sys.modules.pop("session_start_hook", None)


def test_consume_inbox_uses_http_when_available(hook_module):
    """Happy path: HTTP succeeds → hook returns daemon's response, never
    touches the filesystem."""
    fake_notes = [{"id": "abc", "text": "hello"}]
    with patch.object(
        hook_module, "_http_get_json", return_value={"notes": fake_notes}
    ) as mock_http:
        result = hook_module._consume_inbox("sess-1")

    assert result == fake_notes
    mock_http.assert_called_once()
    assert "/api/sessions/sess-1/pending?mark_read=true" in mock_http.call_args[0][0]


def test_consume_inbox_falls_back_to_file_when_daemon_down(hook_module):
    """Daemon down (HTTP returns None) → fall back to direct file drain."""
    # Pre-populate inbox.jsonl with an unread note
    sid = "sess-fallback"
    inbox = hook_module._session_dir(sid) / "inbox.jsonl"
    inbox.parent.mkdir(parents=True, exist_ok=True)
    inbox.write_text(
        '{"id":"n1","text":"fallback test","read":false,"surface_count":0}\n',
        encoding="utf-8",
    )

    with patch.object(hook_module, "_http_get_json", return_value=None):
        result = hook_module._consume_inbox(sid)

    assert len(result) == 1
    assert result[0]["id"] == "n1"
    # Inbox should have been atomically rewritten to empty
    assert inbox.read_text(encoding="utf-8") == ""
    # Archive should have the drained note
    archive = hook_module._session_dir(sid) / "archive.jsonl"
    assert archive.exists()
    assert "n1" in archive.read_text(encoding="utf-8")


def test_consume_handoffs_uses_http_when_available(hook_module):
    fake_handoffs = [{"id": "hand1", "text": "do thing", "_claim_role": "owner"}]
    with patch.object(
        hook_module,
        "_http_get_json",
        return_value={"handoffs": fake_handoffs},
    ) as mock_http:
        result = hook_module._consume_handoffs("sess-1", "/some/cwd")

    assert result == fake_handoffs
    mock_http.assert_called_once()
    call_url = mock_http.call_args[0][0]
    assert "/api/handoffs/consume" in call_url
    assert "session_id=sess-1" in call_url
    assert "cwd=" in call_url


def test_consume_handoffs_fallback_applies_target_filter(hook_module, tmp_path):
    """Regression: targeted invites must NOT surface on peer sessions even
    on the fallback path. This is the bug the addendum warned about."""
    project = tmp_path / "p"
    project.mkdir()
    project_str = os.path.abspath(str(project))

    # Construct a targeted handoff manually
    import time as time_mod

    hook_module._HANDOFFS_PATH.parent.mkdir(parents=True, exist_ok=True)
    handoff = {
        "id": "targeted1",
        "ts": "2026-05-11T00:00:00Z",
        "from_session_id": "owner",
        "text": "for invitee only",
        "scope_cwd": project_str,
        "target_session_id": "invitee-only",
        "expires_at": time_mod.time() + 3600,
        "read_by": [],
    }
    import json as json_mod

    with hook_module._HANDOFFS_PATH.open("w", encoding="utf-8") as f:
        f.write(json_mod.dumps(handoff) + "\n")

    # Peer session consumes — should NOT see the invite (target filter)
    with patch.object(hook_module, "_http_get_json", return_value=None):
        peer = hook_module._consume_handoffs("some-peer", project_str)
    assert peer == []

    # The named invitee consumes — should see it
    with patch.object(hook_module, "_http_get_json", return_value=None):
        invitee = hook_module._consume_handoffs("invitee-only", project_str)
    assert len(invitee) == 1
    assert invitee[0]["id"] == "targeted1"


def test_discover_uses_http_when_available(hook_module):
    """list_sessions HTTP path returns the daemon's cached digest."""
    fake = {
        "sessions": [
            {
                "session_id": "other-sess",
                "last_active_age_s": 10,
                "status": {"status": "implementing"},
                "decision_count": 3,
                "file_touch_count": 5,
                "open_question_count": 0,
            },
            {
                "session_id": "myself",  # should be filtered
                "last_active_age_s": 1,
                "status": None,
                "decision_count": 0,
                "file_touch_count": 0,
                "open_question_count": 0,
            },
            {
                "session_id": "stale-sess",  # too old, filtered
                "last_active_age_s": 9999,
                "status": None,
                "decision_count": 0,
                "file_touch_count": 0,
                "open_question_count": 0,
            },
        ]
    }
    with patch.object(hook_module, "_http_get_json", return_value=fake):
        result = hook_module._discover_other_active_sessions(
            "myself", within_minutes=30
        )

    assert len(result) == 1
    assert result[0]["session_id"] == "other-sess"
    assert result[0]["decision_count"] == 3
