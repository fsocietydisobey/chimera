"""Tests for multi-turn `mcp__khimaira__auto` (#56).

`continue_task_id` arg lets callers thread successive dispatches into
one conversation. khimaira loads prior turns from
~/.local/state/khimaira/conversations/<id>.jsonl, prepends them to the
current prompt, then appends the new turn after the response lands.

Tests cover:
  - load_history: empty path, existing file, unsafe task_id, missing
    file, corrupt lines
  - append_turn: creates file on first append, appends correctly
  - render_history_as_prompt_prefix: format on empty + multi-turn
  - clear_conversation: removes the file
  - integration via _delegate_impl: 2-turn flow, second call sees the
    first turn's content prepended
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest


@pytest.fixture
def isolated_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Re-root ~/.local/state/khimaira at a tmp path."""
    state_root = tmp_path / "state"
    state_root.mkdir()
    monkeypatch.setenv("XDG_STATE_HOME", str(state_root))
    from khimaira.dispatch import conversations as conv_mod
    from khimaira import usage as usage_mod
    importlib.reload(conv_mod)
    importlib.reload(usage_mod)
    yield conv_mod, state_root
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    importlib.reload(conv_mod)
    importlib.reload(usage_mod)


# -------------------- conversations module unit -------------------- #


def test_load_history_no_file(isolated_state):
    conv_mod, _ = isolated_state
    assert conv_mod.load_history("nonexistent-id") == []


def test_load_history_empty_task_id(isolated_state):
    conv_mod, _ = isolated_state
    assert conv_mod.load_history("") == []


def test_load_history_unsafe_task_id(isolated_state):
    conv_mod, _ = isolated_state
    # Unsafe characters → returns empty (with a warning), doesn't crash
    assert conv_mod.load_history("../escape") == []
    assert conv_mod.load_history("with spaces") == []
    assert conv_mod.load_history("with;semicolon") == []


def test_append_then_load_round_trip(isolated_state):
    conv_mod, _ = isolated_state
    conv_mod.append_turn("convo-1", "first prompt", "first answer")
    conv_mod.append_turn("convo-1", "second prompt", "second answer")

    history = conv_mod.load_history("convo-1")
    assert len(history) == 2
    assert history[0]["user"] == "first prompt"
    assert history[0]["assistant"] == "first answer"
    assert history[1]["user"] == "second prompt"
    assert history[1]["assistant"] == "second answer"
    # Timestamps are populated
    assert history[0]["ts"]
    assert history[1]["ts"]


def test_load_history_skips_corrupt_lines(isolated_state):
    conv_mod, state_root = isolated_state
    conv_mod.append_turn("convo-2", "real", "answer")
    # Inject garbage between good lines
    path = state_root / "khimaira" / "conversations" / "convo-2.jsonl"
    with path.open("a") as f:
        f.write("not-json-{\n")
    conv_mod.append_turn("convo-2", "real-2", "answer-2")

    history = conv_mod.load_history("convo-2")
    assert len(history) == 2
    assert history[0]["user"] == "real"
    assert history[1]["user"] == "real-2"


def test_render_empty_history(isolated_state):
    conv_mod, _ = isolated_state
    assert conv_mod.render_history_as_prompt_prefix([]) == ""


def test_render_multi_turn(isolated_state):
    conv_mod, _ = isolated_state
    history = [
        {"user": "what is 2+2?", "assistant": "4"},
        {"user": "and 4+4?", "assistant": "8"},
    ]
    rendered = conv_mod.render_history_as_prompt_prefix(history)
    assert "[conversation history" in rendered
    assert "User: what is 2+2?" in rendered
    assert "Assistant: 4" in rendered
    assert "User: and 4+4?" in rendered
    assert "Assistant: 8" in rendered
    assert "[/conversation history]" in rendered


def test_clear_conversation_removes_file(isolated_state):
    conv_mod, state_root = isolated_state
    conv_mod.append_turn("c", "u", "a")
    path = state_root / "khimaira" / "conversations" / "c.jsonl"
    assert path.is_file()
    assert conv_mod.clear_conversation("c") is True
    assert not path.is_file()
    # Idempotent: clearing again returns False but doesn't error
    assert conv_mod.clear_conversation("c") is False


def test_clear_conversation_empty_id(isolated_state):
    conv_mod, _ = isolated_state
    # Doesn't blow up on empty string; returns False
    assert conv_mod.clear_conversation("") is False


# -------------------- _delegate_impl integration -------------------- #


@pytest.fixture
def isolated_for_delegate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Like isolated_state but also resets the circuit (it persists
    failures across tests at module scope)."""
    state_root = tmp_path / "state"
    state_root.mkdir()
    monkeypatch.setenv("XDG_STATE_HOME", str(state_root))
    from khimaira.dispatch import circuit as circuit_mod
    from khimaira.dispatch import conversations as conv_mod
    from khimaira import usage as usage_mod
    importlib.reload(usage_mod)
    importlib.reload(conv_mod)
    circuit_mod.get_circuit().reset()
    yield conv_mod, state_root
    circuit_mod.get_circuit().reset()
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    importlib.reload(usage_mod)
    importlib.reload(conv_mod)


async def test_first_turn_with_continue_task_id_persists_turn(
    isolated_for_delegate, monkeypatch
):
    """First call with continue_task_id has no history (none exists yet);
    after dispatch, the turn lands in the conversation log."""
    conv_mod, state_root = isolated_for_delegate

    captured_prompts: list[str] = []

    class _FakeRunner:
        def is_available(self):
            return True

        async def run(self, prompt, model=None, timeout=None):
            captured_prompts.append(prompt)

            class _R:
                text = "answer-1"
                model = "claude-haiku-4-5"
                input_tokens = 5
                output_tokens = 5
                latency_s = 0.01

            return _R()

    monkeypatch.setattr(
        "khimaira.dispatch.runners.get_runner",
        lambda runner: _FakeRunner(),
    )

    from khimaira.server import mcp as mcp_mod

    result = await mcp_mod._delegate_impl(
        "first question",
        tier="haiku",
        timeout_s=30,
        continue_task_id="my-thread",
    )
    assert "answer-1" in result

    # Runner saw the prompt unchanged (no history to prepend)
    assert captured_prompts == ["first question"]

    # Conversation log has one turn
    history = conv_mod.load_history("my-thread")
    assert len(history) == 1
    assert history[0]["user"] == "first question"
    assert history[0]["assistant"] == "answer-1"


async def test_second_turn_prepends_first_to_prompt(
    isolated_for_delegate, monkeypatch
):
    """Second call with the same continue_task_id includes the first
    turn's content as a history prefix in the runner's prompt."""
    conv_mod, state_root = isolated_for_delegate

    # Seed: first turn already in history
    conv_mod.append_turn("my-thread", "first question", "first answer")

    captured_prompts: list[str] = []

    class _FakeRunner:
        def is_available(self):
            return True

        async def run(self, prompt, model=None, timeout=None):
            captured_prompts.append(prompt)

            class _R:
                text = "second answer"
                model = "claude-haiku-4-5"
                input_tokens = 100
                output_tokens = 50
                latency_s = 0.01

            return _R()

    monkeypatch.setattr(
        "khimaira.dispatch.runners.get_runner",
        lambda runner: _FakeRunner(),
    )

    from khimaira.server import mcp as mcp_mod

    result = await mcp_mod._delegate_impl(
        "second question",
        tier="haiku",
        timeout_s=30,
        continue_task_id="my-thread",
    )
    assert "second answer" in result

    # The prompt the runner saw includes the prior turn
    assert len(captured_prompts) == 1
    sent = captured_prompts[0]
    assert "first question" in sent
    assert "first answer" in sent
    assert "second question" in sent
    # Order matters — history first, current prompt last
    assert sent.index("first question") < sent.index("second question")

    # The new turn is now in history (only the user's original prompt
    # gets logged, not the inflated history-prefixed string)
    history = conv_mod.load_history("my-thread")
    assert len(history) == 2
    assert history[1]["user"] == "second question"
    assert history[1]["assistant"] == "second answer"


async def test_one_shot_does_not_touch_conversations_dir(
    isolated_for_delegate, monkeypatch
):
    """Without continue_task_id, no conversation file is created."""
    conv_mod, state_root = isolated_for_delegate

    class _FakeRunner:
        def is_available(self):
            return True

        async def run(self, prompt, model=None, timeout=None):
            class _R:
                text = "ok"
                model = "claude-haiku-4-5"
                input_tokens = 1
                output_tokens = 1
                latency_s = 0.01

            return _R()

    monkeypatch.setattr(
        "khimaira.dispatch.runners.get_runner",
        lambda runner: _FakeRunner(),
    )

    from khimaira.server import mcp as mcp_mod

    await mcp_mod._delegate_impl(
        "anything",
        tier="haiku",
        timeout_s=30,
        continue_task_id="",  # explicitly off
    )

    conv_dir = state_root / "khimaira" / "conversations"
    assert not conv_dir.exists() or not list(conv_dir.iterdir())
