"""HTTP API tests for /api/chats.

Per khimaira CLAUDE.md rule: every endpoint gets happy + unhappy paths,
including the cross-cutting unknown-id 404s and the 403 sender-gating
checks. SSE stream is exercised via TestClient's stream API.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest


@pytest.fixture
def chats_api_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    state_root = tmp_path / "state"
    state_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("XDG_STATE_HOME", str(state_root))

    from khimaira.monitor import sessions as sessions_mod

    importlib.reload(sessions_mod)
    from khimaira.monitor import chats as chats_mod

    importlib.reload(chats_mod)
    from khimaira.monitor.api import chats as api_mod

    importlib.reload(api_mod)

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(api_mod.build_router(), prefix="/api")
    client = TestClient(app)

    # Plant alice + bob + carol sessions.
    for sid in ("alice", "bob", "carol"):
        sd = sessions_mod._session_dir(sid)
        (sd / "status.json").write_text(
            json.dumps({"status": "implementing", "name": sid}), encoding="utf-8"
        )
    yield client, chats_mod
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    importlib.reload(sessions_mod)
    importlib.reload(chats_mod)
    importlib.reload(api_mod)


# ---------------------------------------------------------------------------
# create + list
# ---------------------------------------------------------------------------


def test_post_create_room(chats_api_client):
    client, _ = chats_api_client
    resp = client.post(
        "/api/chats",
        json={
            "creator_session_id": "alice",
            "member_session_ids": ["bob"],
            "title": "alice + bob",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["meta"]["title"] == "alice + bob"
    assert body["members"]["alice"]["state"] == "accepted"
    assert body["members"]["bob"]["state"] == "pending"


def test_post_create_room_unknown_member_returns_404(chats_api_client):
    client, _ = chats_api_client
    resp = client.post(
        "/api/chats",
        json={
            "creator_session_id": "alice",
            "member_session_ids": ["ghost"],
        },
    )
    assert resp.status_code == 404


def test_get_my_chats_returns_pending_and_accepted(chats_api_client):
    client, _ = chats_api_client
    client.post(
        "/api/chats",
        json={"creator_session_id": "alice", "member_session_ids": ["bob"]},
    )
    resp = client.get("/api/chats?session_id=bob")
    assert resp.status_code == 200
    chats = resp.json()["chats"]
    assert len(chats) == 1
    assert chats[0]["my_state"] == "pending"


# ---------------------------------------------------------------------------
# accept + send + history
# ---------------------------------------------------------------------------


def test_accept_then_send_then_history(chats_api_client):
    client, _ = chats_api_client
    created = client.post(
        "/api/chats",
        json={"creator_session_id": "alice", "member_session_ids": ["bob"]},
    ).json()
    chat_id = created["meta"]["chat_id"]

    accept = client.post(f"/api/chats/{chat_id}/accept", json={"session_id": "bob"})
    assert accept.status_code == 200

    send = client.post(
        f"/api/chats/{chat_id}/messages",
        json={"sender_session_id": "alice", "body": "hello"},
    )
    assert send.status_code == 200

    history = client.get(f"/api/chats/{chat_id}/messages?session_id=bob")
    assert history.status_code == 200
    msgs = history.json()["messages"]
    # Phase B v1.5: filter system role-directive (sent on chat_create_room
    # to the creator) to assert on user messages only.
    user_msgs = [m for m in msgs if m.get("sender_id") != "khimaira-system"]
    assert len(user_msgs) == 1
    assert user_msgs[0]["body"] == "hello"


def test_send_by_pending_member_returns_403(chats_api_client):
    client, _ = chats_api_client
    created = client.post(
        "/api/chats",
        json={"creator_session_id": "alice", "member_session_ids": ["bob"]},
    ).json()
    chat_id = created["meta"]["chat_id"]
    # bob never accepts
    resp = client.post(
        f"/api/chats/{chat_id}/messages",
        json={"sender_session_id": "bob", "body": "premature"},
    )
    assert resp.status_code == 403


def test_send_by_non_member_returns_403(chats_api_client):
    client, _ = chats_api_client
    created = client.post(
        "/api/chats",
        json={"creator_session_id": "alice", "member_session_ids": ["bob"]},
    ).json()
    chat_id = created["meta"]["chat_id"]
    resp = client.post(
        f"/api/chats/{chat_id}/messages",
        json={"sender_session_id": "carol", "body": "I'm not even invited"},
    )
    assert resp.status_code == 403


def test_history_by_non_member_returns_403(chats_api_client):
    client, _ = chats_api_client
    created = client.post(
        "/api/chats",
        json={"creator_session_id": "alice", "member_session_ids": ["bob"]},
    ).json()
    chat_id = created["meta"]["chat_id"]
    resp = client.get(f"/api/chats/{chat_id}/messages?session_id=carol")
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# leave + delete
# ---------------------------------------------------------------------------


def test_leave_returns_200(chats_api_client):
    client, _ = chats_api_client
    created = client.post(
        "/api/chats",
        json={"creator_session_id": "alice", "member_session_ids": ["bob"]},
    ).json()
    chat_id = created["meta"]["chat_id"]
    client.post(f"/api/chats/{chat_id}/accept", json={"session_id": "bob"})
    resp = client.post(f"/api/chats/{chat_id}/leave", json={"session_id": "bob"})
    assert resp.status_code == 200
    assert resp.json()["state"] == "left"


def test_delete_by_non_creator_returns_403(chats_api_client):
    client, _ = chats_api_client
    created = client.post(
        "/api/chats",
        json={"creator_session_id": "alice", "member_session_ids": ["bob"]},
    ).json()
    chat_id = created["meta"]["chat_id"]
    client.post(f"/api/chats/{chat_id}/accept", json={"session_id": "bob"})
    resp = client.delete(f"/api/chats/{chat_id}?by_session_id=bob")
    assert resp.status_code == 403


def test_delete_by_creator_returns_200(chats_api_client):
    client, _ = chats_api_client
    created = client.post(
        "/api/chats",
        json={"creator_session_id": "alice", "member_session_ids": ["bob"]},
    ).json()
    chat_id = created["meta"]["chat_id"]
    resp = client.delete(f"/api/chats/{chat_id}?by_session_id=alice")
    assert resp.status_code == 200
    assert "archived_to" in resp.json()


def test_reject_pending_returns_200(chats_api_client):
    client, _ = chats_api_client
    created = client.post(
        "/api/chats",
        json={"creator_session_id": "alice", "member_session_ids": ["bob"]},
    ).json()
    chat_id = created["meta"]["chat_id"]
    resp = client.post(f"/api/chats/{chat_id}/reject", json={"session_id": "bob"})
    assert resp.status_code == 200
    assert resp.json()["state"] == "rejected"


def test_reject_unknown_chat_returns_404(chats_api_client):
    client, _ = chats_api_client
    resp = client.post("/api/chats/chat-doesnotexis/reject", json={"session_id": "bob"})
    assert resp.status_code == 404


def test_register_pending_session_then_lookup(chats_api_client):
    """Hook posts {ppid, session_id}; subprocess looks up by ppid."""
    client, _ = chats_api_client
    resp = client.post(
        "/api/chats/register-pending-session",
        json={"ppid": 88888, "session_id": "session-xyz"},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    lookup = client.get("/api/chats/session-by-ppid?ppid=88888")
    assert lookup.status_code == 200
    assert lookup.json()["session_id"] == "session-xyz"


def test_session_by_ppid_returns_null_when_unknown(chats_api_client):
    client, _ = chats_api_client
    resp = client.get("/api/chats/session-by-ppid?ppid=99999")
    assert resp.status_code == 200
    assert resp.json()["session_id"] is None


def test_latest_pending_returns_chat_id(chats_api_client):
    client, _ = chats_api_client
    created = client.post(
        "/api/chats",
        json={"creator_session_id": "alice", "member_session_ids": ["bob"]},
    ).json()
    expected_chat_id = created["meta"]["chat_id"]

    resp = client.get("/api/chats/pending/latest?session_id=bob")
    assert resp.status_code == 200
    assert resp.json()["chat_id"] == expected_chat_id


def test_latest_pending_returns_null_when_none(chats_api_client):
    client, _ = chats_api_client
    resp = client.get("/api/chats/pending/latest?session_id=alice")
    assert resp.status_code == 200
    assert resp.json()["chat_id"] is None


def test_delete_unknown_returns_404(chats_api_client):
    client, _ = chats_api_client
    resp = client.delete("/api/chats/chat-doesnotexis?by_session_id=alice")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# get_room
# ---------------------------------------------------------------------------


def test_get_room_returns_full_state(chats_api_client):
    client, _ = chats_api_client
    created = client.post(
        "/api/chats",
        json={"creator_session_id": "alice", "member_session_ids": ["bob"]},
    ).json()
    chat_id = created["meta"]["chat_id"]
    resp = client.get(f"/api/chats/{chat_id}?session_id=alice")
    assert resp.status_code == 200
    body = resp.json()
    assert body["meta"]["chat_id"] == chat_id
    assert "alice" in body["members"]
    assert "bob" in body["members"]


def test_get_room_by_non_member_returns_403(chats_api_client):
    client, _ = chats_api_client
    created = client.post(
        "/api/chats",
        json={"creator_session_id": "alice", "member_session_ids": ["bob"]},
    ).json()
    chat_id = created["meta"]["chat_id"]
    resp = client.get(f"/api/chats/{chat_id}?session_id=carol")
    assert resp.status_code == 403


def test_get_unknown_room_returns_404(chats_api_client):
    client, _ = chats_api_client
    resp = client.get("/api/chats/chat-doesnotexis?session_id=alice")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# invite
# ---------------------------------------------------------------------------


def test_invite_by_accepted_member(chats_api_client):
    client, _ = chats_api_client
    created = client.post(
        "/api/chats",
        json={"creator_session_id": "alice", "member_session_ids": ["bob"]},
    ).json()
    chat_id = created["meta"]["chat_id"]
    client.post(f"/api/chats/{chat_id}/accept", json={"session_id": "bob"})
    resp = client.post(
        f"/api/chats/{chat_id}/invite",
        json={"by_session_id": "bob", "invitee_session_id": "carol"},
    )
    assert resp.status_code == 200
    assert resp.json()["state"] == "pending"


def test_invite_by_pending_member_rejected(chats_api_client):
    client, _ = chats_api_client
    created = client.post(
        "/api/chats",
        json={"creator_session_id": "alice", "member_session_ids": ["bob"]},
    ).json()
    chat_id = created["meta"]["chat_id"]
    # bob is still pending
    resp = client.post(
        f"/api/chats/{chat_id}/invite",
        json={"by_session_id": "bob", "invitee_session_id": "carol"},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Phase B v1.2: transfer_membership endpoint
# ---------------------------------------------------------------------------


def _plant_session(name: str) -> None:
    """Helper — write a state dir for sessions not in the fixture's defaults."""
    from khimaira.monitor import sessions as sessions_mod

    sd = sessions_mod._session_dir(name)
    (sd / "status.json").write_text(
        json.dumps({"status": "implementing", "name": name}), encoding="utf-8"
    )


def test_transfer_membership_happy_path_returns_200(chats_api_client):
    client, _ = chats_api_client
    _plant_session("dave")
    created = client.post(
        "/api/chats",
        json={"creator_session_id": "alice", "member_session_ids": ["bob"]},
    ).json()
    chat_id = created["meta"]["chat_id"]
    client.post(f"/api/chats/{chat_id}/accept", json={"session_id": "bob"})

    resp = client.post(
        f"/api/chats/{chat_id}/transfer-membership",
        json={"from_session_id": "bob", "to_session_id": "dave"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["transfer_id"].startswith("xfer-")
    assert body["from"]["state"] == "transferred-out"
    assert body["to"]["state"] == "accepted"


def test_transfer_membership_unknown_target_returns_404(chats_api_client):
    """Required by project CLAUDE.md: every session-resolving endpoint
    needs unknown-name coverage. Resolving 'ghost' raises ValueError →
    handler must map to 404, not let it become a 500."""
    client, _ = chats_api_client
    created = client.post(
        "/api/chats",
        json={"creator_session_id": "alice", "member_session_ids": ["bob"]},
    ).json()
    chat_id = created["meta"]["chat_id"]
    client.post(f"/api/chats/{chat_id}/accept", json={"session_id": "bob"})

    resp = client.post(
        f"/api/chats/{chat_id}/transfer-membership",
        json={"from_session_id": "bob", "to_session_id": "ghost"},
    )
    assert resp.status_code == 404


def test_transfer_membership_pending_source_returns_403(chats_api_client):
    """A pending session has nothing to transfer — 403 (forbidden), not 404."""
    client, _ = chats_api_client
    _plant_session("dave")
    created = client.post(
        "/api/chats",
        json={"creator_session_id": "alice", "member_session_ids": ["bob"]},
    ).json()
    chat_id = created["meta"]["chat_id"]
    # bob is still pending — has not accepted

    resp = client.post(
        f"/api/chats/{chat_id}/transfer-membership",
        json={"from_session_id": "bob", "to_session_id": "dave"},
    )
    assert resp.status_code == 403


def test_transfer_membership_duplicate_target_returns_409(chats_api_client):
    """Recipient is already an accepted member → 409 conflict, not silent
    state overwrite."""
    client, _ = chats_api_client
    created = client.post(
        "/api/chats",
        json={"creator_session_id": "alice", "member_session_ids": ["bob", "carol"]},
    ).json()
    chat_id = created["meta"]["chat_id"]
    client.post(f"/api/chats/{chat_id}/accept", json={"session_id": "bob"})
    client.post(f"/api/chats/{chat_id}/accept", json={"session_id": "carol"})

    resp = client.post(
        f"/api/chats/{chat_id}/transfer-membership",
        json={"from_session_id": "bob", "to_session_id": "carol"},
    )
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Phase B v1.2: signal-start endpoint
# ---------------------------------------------------------------------------


def test_signal_task_start_returns_200(chats_api_client):
    """Master posts signal-start on a pending task → 200 + task_signal record."""
    client, _ = chats_api_client
    created = client.post(
        "/api/chats",
        json={"creator_session_id": "alice", "member_session_ids": ["bob"]},
    ).json()
    chat_id = created["meta"]["chat_id"]
    client.post(f"/api/chats/{chat_id}/accept", json={"session_id": "bob"})
    task = client.post(
        f"/api/chats/{chat_id}/tasks",
        json={"sender_session_id": "alice", "body": "do thing", "assignee_session_id": "bob"},
    ).json()

    resp = client.post(
        f"/api/chats/{chat_id}/tasks/{task['id']}/signal-start",
        json={"by_session_id": "alice", "note": "go ahead"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["kind"] == "task_signal"
    assert body["signal"] == "start"
    assert body["task_id"] == task["id"]
    assert body["note"] == "go ahead"


def test_signal_task_start_unknown_task_returns_404(chats_api_client):
    """Unknown task_id → 404 (project CLAUDE.md unknown-resource coverage)."""
    client, _ = chats_api_client
    created = client.post(
        "/api/chats",
        json={"creator_session_id": "alice", "member_session_ids": ["bob"]},
    ).json()
    chat_id = created["meta"]["chat_id"]

    resp = client.post(
        f"/api/chats/{chat_id}/tasks/task-doesnotexist/signal-start",
        json={"by_session_id": "alice"},
    )
    assert resp.status_code == 404
