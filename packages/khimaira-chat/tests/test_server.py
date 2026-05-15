"""Tests for khimaira_chat.server._route_record.

The MCP subprocess's SSE loops both delegate routing to the pure
`_route_record(record, my_session_id)` helper. Testing the helper
directly avoids spinning up the SSE pipeline.

Phase B v1.1 extended routing to cover `kind=task` and `kind=task_update`
records so assignees see new tasks in their channel feed and masters see
agents' transitions without polling chat_task_status.
"""

from __future__ import annotations

from khimaira_chat.server import _route_record

MY_SID = "session-me"
OTHER_SID = "session-other"


# ---------------------------------------------------------------------------
# Existing routes (msg, invite) — kept covered to guard against regressions
# ---------------------------------------------------------------------------


def test_msg_from_other_session_emits():
    record = {
        "kind": "msg",
        "chat_id": "chat-1",
        "sender_id": OTHER_SID,
        "sender_name": "other",
        "id": "msg-abc",
        "body": "hello",
    }
    decision = _route_record(record, MY_SID)
    assert decision is not None
    content, meta = decision
    assert content == "hello"
    assert meta == {"chat_id": "chat-1", "sender": "other", "msg_id": "msg-abc"}


def test_msg_from_self_skipped():
    record = {
        "kind": "msg",
        "chat_id": "chat-1",
        "sender_id": MY_SID,
        "body": "my own message",
    }
    assert _route_record(record, MY_SID) is None


def test_pending_invite_for_me_emits():
    record = {
        "kind": "member",
        "state": "pending",
        "chat_id": "chat-1",
        "session_id": MY_SID,
        "invited_by": "boss",
    }
    decision = _route_record(record, MY_SID)
    assert decision is not None
    content, meta = decision
    assert "boss invited you to chat chat-1" in content
    assert meta == {"chat_id": "chat-1", "kind": "invite", "from": "boss"}


def test_pending_invite_for_other_skipped():
    record = {
        "kind": "member",
        "state": "pending",
        "chat_id": "chat-1",
        "session_id": OTHER_SID,
        "invited_by": "boss",
    }
    assert _route_record(record, MY_SID) is None


# ---------------------------------------------------------------------------
# Phase B v1.1: kind=task routes to assignee (or broadcasts if unassigned)
# ---------------------------------------------------------------------------


def test_task_assigned_to_me_emits_with_pending_status_and_body():
    record = {
        "kind": "task",
        "chat_id": "chat-1",
        "id": "task-abc",
        "sender_id": OTHER_SID,
        "sender_name": "master",
        "assignee_id": MY_SID,
        "assignee_name": "me",
        "body": "implement the foo",
        "status": "pending",
    }
    decision = _route_record(record, MY_SID)
    assert decision is not None
    content, meta = decision
    # Channel-block format spec: "📋 task <id> [<status>] from <by_name>: <body>"
    assert content == "📋 task task-abc [pending] from master: implement the foo"
    assert meta["kind"] == "task"
    assert meta["task_id"] == "task-abc"
    assert meta["status"] == "pending"
    assert meta["sender"] == "master"
    assert meta["chat_id"] == "chat-1"


def test_task_assigned_to_other_skipped():
    """A task assigned to bob shouldn't channel-block carol's feed."""
    record = {
        "kind": "task",
        "chat_id": "chat-1",
        "id": "task-abc",
        "sender_id": OTHER_SID,
        "sender_name": "master",
        "assignee_id": "session-bob",
        "body": "implement the foo",
    }
    assert _route_record(record, MY_SID) is None


def test_unassigned_task_emits_to_non_creator():
    """Unassigned task = broadcast-to-accepted shape; everyone except the
    creator gets a channel block so the open task is visible."""
    record = {
        "kind": "task",
        "chat_id": "chat-1",
        "id": "task-abc",
        "sender_id": OTHER_SID,
        "sender_name": "master",
        "assignee_id": None,
        "body": "anyone want this",
    }
    decision = _route_record(record, MY_SID)
    assert decision is not None
    content, _ = decision
    assert "task-abc" in content
    assert "[pending]" in content


def test_unassigned_task_skipped_for_creator():
    """Creator of an unassigned task doesn't see their own creation echo."""
    record = {
        "kind": "task",
        "chat_id": "chat-1",
        "id": "task-abc",
        "sender_id": MY_SID,
        "sender_name": "me",
        "assignee_id": None,
        "body": "anyone want this",
    }
    assert _route_record(record, MY_SID) is None


# ---------------------------------------------------------------------------
# Phase B v1.1: kind=task_update routes to non-actors
# ---------------------------------------------------------------------------


def test_task_update_done_by_other_emits_to_master():
    """The spec'd test #2 — agent marks task done; master (everyone but
    the actor) sees a channel block. Closes the master-side polling gap."""
    record = {
        "kind": "task_update",
        "chat_id": "chat-1",
        "task_id": "task-abc",
        "status": "done",
        "by_session_id": OTHER_SID,
        "by_name": "agent",
        "note": "PR #042",
    }
    decision = _route_record(record, MY_SID)
    assert decision is not None
    content, meta = decision
    assert content == "📋 task task-abc [done] from agent: PR #042"
    assert meta["kind"] == "task_update"
    assert meta["task_id"] == "task-abc"
    assert meta["status"] == "done"


def test_task_update_by_self_skipped():
    """Actor doesn't see their own transition echoed."""
    record = {
        "kind": "task_update",
        "chat_id": "chat-1",
        "task_id": "task-abc",
        "status": "done",
        "by_session_id": MY_SID,
        "by_name": "me",
        "note": "PR #042",
    }
    assert _route_record(record, MY_SID) is None


def test_task_update_without_note_omits_suffix():
    """When transition has no note, the channel block ends after the actor
    name — no dangling colon."""
    record = {
        "kind": "task_update",
        "chat_id": "chat-1",
        "task_id": "task-abc",
        "status": "in_progress",
        "by_session_id": OTHER_SID,
        "by_name": "agent",
        "note": None,
    }
    decision = _route_record(record, MY_SID)
    assert decision is not None
    content, _ = decision
    assert content == "📋 task task-abc [in_progress] from agent"


# ---------------------------------------------------------------------------
# Unknown / unhandled kinds are skipped cleanly
# ---------------------------------------------------------------------------


def test_unknown_kind_skipped():
    assert _route_record({"kind": "meta"}, MY_SID) is None
    assert _route_record({}, MY_SID) is None


# ---------------------------------------------------------------------------
# Phase B v1.2: task_signal routing
# ---------------------------------------------------------------------------


def test_task_signal_routes_to_assignee():
    """Master sends signal-start on a task assigned to me → I get a
    `🟢 ... [ready to start]` channel block."""
    record = {
        "kind": "task_signal",
        "chat_id": "chat-1",
        "task_id": "task-abc",
        "signal": "start",
        "by_session_id": OTHER_SID,
        "by_name": "master",
        "assignee_id": MY_SID,
        "note": "all blockers cleared",
    }
    decision = _route_record(record, MY_SID)
    assert decision is not None
    content, meta = decision
    assert content == "🟢 task task-abc [ready to start] from master: all blockers cleared"
    assert meta == {
        "chat_id": "chat-1",
        "kind": "task_signal",
        "task_id": "task-abc",
        "sender": "master",
        "signal": "start",
    }


def test_task_signal_skips_non_assignee():
    """Task has assignee X; I'm not X → skip. Prevents siblings spam in
    multi-agent chats where the signal is targeted."""
    record = {
        "kind": "task_signal",
        "chat_id": "chat-1",
        "task_id": "task-abc",
        "signal": "start",
        "by_session_id": OTHER_SID,
        "by_name": "master",
        "assignee_id": "session-someone-else",
    }
    assert _route_record(record, MY_SID) is None


def test_task_signal_broadcasts_when_unassigned():
    """Unassigned task signal → broadcast (any accepted member could claim
    it). Mirrors the kind=task unassigned broadcast precedent from v1.1.a."""
    record = {
        "kind": "task_signal",
        "chat_id": "chat-1",
        "task_id": "task-abc",
        "signal": "start",
        "by_session_id": OTHER_SID,
        "by_name": "master",
        "assignee_id": None,
        "note": None,
    }
    decision = _route_record(record, MY_SID)
    assert decision is not None
    content, _ = decision
    assert content == "🟢 task task-abc [ready to start] from master"


def test_task_signal_skips_own_signal():
    """Master who sent the signal shouldn't see their own echo."""
    record = {
        "kind": "task_signal",
        "chat_id": "chat-1",
        "task_id": "task-abc",
        "signal": "start",
        "by_session_id": MY_SID,
        "by_name": "me",
        "assignee_id": OTHER_SID,
    }
    assert _route_record(record, MY_SID) is None
