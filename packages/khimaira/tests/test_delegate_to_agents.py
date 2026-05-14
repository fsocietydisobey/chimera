"""Tests for `mcp__khimaira__delegate_to_agents` — the master/agent fan-out tool.

The tool fires N questions sequentially via daemon HTTP (cheap) then
asyncio.gathers N session_wait_for_answer long-polls (expensive but
parallel). These tests mock both layers — the daemon HTTP via
monitor_tools._post + the long-polls via monitor_tools.session_wait_for_answer —
so the suite runs without a live daemon.
"""

from __future__ import annotations

import asyncio
import json

import pytest

# Import the MCP server module to access delegate_to_agents.
# The tool's @mcp.tool() decorator wraps it but the underlying function
# is still callable as the module attribute.
from khimaira.server import mcp as mcp_mod


# Counter for stable mock question ids across the test session
class _IdCounter:
    n = 0

    @classmethod
    def next(cls) -> str:
        cls.n += 1
        return f"qid-{cls.n:04d}"


@pytest.fixture(autouse=True)
def reset_id_counter():
    _IdCounter.n = 0
    yield


def _make_post_mock(question_ids: dict[str, str], errors: dict[str, str] | None = None):
    """Build a _post mock that returns a question_id for each target
    OR an error string for targets in `errors`."""
    errors = errors or {}

    def _fake_post(path, body, *, timeout=5.0, base=None):
        # body has {"text": ..., "target_session_id": <name>}
        target = body.get("target_session_id", "?")
        if target in errors:
            return errors[target]  # daemon-returned error string
        qid = question_ids.get(target) or _IdCounter.next()
        question_ids[target] = qid
        return {"id": qid, "target_session_id": target}

    return _fake_post


def _make_wait_mock(answers: dict[str, str]):
    """Build a session_wait_for_answer mock keyed by question_id.
    Values can be:
      - "✅ ..." → simulated answered prose
      - "...HTTP 408..." → simulated timeout
      - anything else → treated as error
    """

    async def _fake_wait(session_id, question_id, timeout):
        # answers map keys can be either question_id or target name —
        # caller's choice. Find by question_id substring.
        for k, v in answers.items():
            if k == question_id or question_id.endswith(k):
                return v
        return f"khimaira-monitor wait → HTTP 408: No answer to {question_id} within {timeout:.0f}s"

    return _fake_wait


# -------------------- happy path -------------------- #


@pytest.mark.asyncio
async def test_delegate_fan_out_two_agents_both_answer(monkeypatch):
    """2 agents both answer cleanly → results dict has both,
    no fire_errors, both status='answered'."""
    qids: dict[str, str] = {"agent-1": "qid-a1", "agent-2": "qid-a2"}
    monkeypatch.setattr(
        mcp_mod._monitor_tools,
        "_post",
        _make_post_mock(qids),
    )
    monkeypatch.setattr(
        mcp_mod._monitor_tools,
        "session_wait_for_answer",
        _make_wait_mock(
            {
                "qid-a1": "✅ answer received for q=qid-a1 (answered by agent-1):\n\nresult one",
                "qid-a2": "✅ answer received for q=qid-a2 (answered by agent-2):\n\nresult two",
            }
        ),
    )

    raw = await mcp_mod.delegate_to_agents(
        from_session_id="master-id",
        targets=["agent-1", "agent-2"],
        task="do the thing",
        timeout=60,
    )

    payload = json.loads(raw)
    assert payload["fired"] == {"agent-1": "qid-a1", "agent-2": "qid-a2"}
    assert payload["results"]["agent-1"]["status"] == "answered"
    assert payload["results"]["agent-2"]["status"] == "answered"
    assert "result one" in payload["results"]["agent-1"]["body"]
    assert "result two" in payload["results"]["agent-2"]["body"]
    assert "fire_errors" not in payload  # no fire errors → key absent


# -------------------- timeout path -------------------- #


@pytest.mark.asyncio
async def test_delegate_one_agent_times_out_others_return_cleanly(monkeypatch):
    """3 agents: 2 answer fast, 1 times out → results dict has all 3,
    timeout one tagged as 'timeout'. Fan-out is per-target — one slow
    agent doesn't poison the others."""
    qids = {"agent-1": "qid-1", "agent-2": "qid-2", "agent-3": "qid-3"}
    monkeypatch.setattr(
        mcp_mod._monitor_tools, "_post", _make_post_mock(qids)
    )
    monkeypatch.setattr(
        mcp_mod._monitor_tools,
        "session_wait_for_answer",
        _make_wait_mock(
            {
                "qid-1": "✅ answer received for q=qid-1 (answered by agent-1):\n\nfast",
                "qid-2": "khimaira-monitor wait → HTTP 408: No answer to qid-2 within 60s",
                "qid-3": "✅ answer received for q=qid-3 (answered by agent-3):\n\nalso fast",
            }
        ),
    )

    raw = await mcp_mod.delegate_to_agents(
        from_session_id="master-id",
        targets=["agent-1", "agent-2", "agent-3"],
        task="task",
        timeout=60,
    )

    payload = json.loads(raw)
    assert payload["results"]["agent-1"]["status"] == "answered"
    assert payload["results"]["agent-2"]["status"] == "timeout"
    assert payload["results"]["agent-3"]["status"] == "answered"


# -------------------- empty targets -------------------- #


@pytest.mark.asyncio
async def test_delegate_empty_targets_returns_immediately(monkeypatch):
    """targets=[] → no questions fired, no waits, empty results."""
    monkeypatch.setattr(
        mcp_mod._monitor_tools,
        "_post",
        _make_post_mock({}),
    )

    raw = await mcp_mod.delegate_to_agents(
        from_session_id="master-id",
        targets=[],
        task="any task",
        timeout=60,
    )

    payload = json.loads(raw)
    assert payload["fired"] == {}
    assert payload["results"] == {}


# -------------------- fire errors -------------------- #


@pytest.mark.asyncio
async def test_delegate_fire_error_for_one_target_others_proceed(monkeypatch):
    """If daemon returns an error string for one target's question-open
    (e.g. 422 unknown session), that target lands in fire_errors and is
    NOT awaited. Other targets proceed normally."""
    qids = {"agent-1": "qid-1"}
    monkeypatch.setattr(
        mcp_mod._monitor_tools,
        "_post",
        _make_post_mock(
            qids,
            errors={"missing-agent": "khimaira-monitor /api/.../question → HTTP 422: unknown session"},
        ),
    )
    monkeypatch.setattr(
        mcp_mod._monitor_tools,
        "session_wait_for_answer",
        _make_wait_mock(
            {"qid-1": "✅ answer received for q=qid-1 (answered by agent-1):\n\nok"}
        ),
    )

    raw = await mcp_mod.delegate_to_agents(
        from_session_id="master-id",
        targets=["agent-1", "missing-agent"],
        task="task",
        timeout=60,
    )

    payload = json.loads(raw)
    assert payload["fired"] == {"agent-1": "qid-1"}
    assert payload["results"]["agent-1"]["status"] == "answered"
    assert "missing-agent" not in payload["results"]
    assert "missing-agent" in payload["fire_errors"]
    assert "422" in payload["fire_errors"]["missing-agent"]


# -------------------- single-target degenerate case -------------------- #


@pytest.mark.asyncio
async def test_delegate_single_target_works_same_as_multi(monkeypatch):
    """A 1-element target list works (no special-casing needed). Confirms
    asyncio.gather over a single coroutine is fine."""
    qids = {"only-agent": "qid-only"}
    monkeypatch.setattr(
        mcp_mod._monitor_tools, "_post", _make_post_mock(qids)
    )
    monkeypatch.setattr(
        mcp_mod._monitor_tools,
        "session_wait_for_answer",
        _make_wait_mock(
            {"qid-only": "✅ answer received for q=qid-only (answered by only-agent):\n\ndone"}
        ),
    )

    raw = await mcp_mod.delegate_to_agents(
        from_session_id="master-id",
        targets=["only-agent"],
        task="single",
        timeout=60,
    )

    payload = json.loads(raw)
    assert list(payload["fired"].keys()) == ["only-agent"]
    assert payload["results"]["only-agent"]["status"] == "answered"
    assert "done" in payload["results"]["only-agent"]["body"]


# -------------------- parallelism check (not strict; documentary) -------------------- #


@pytest.mark.asyncio
async def test_delegate_waits_run_in_parallel(monkeypatch):
    """3 agents each take 0.1s to answer. If serial, total wait ≥ 0.3s;
    parallel ≤ 0.15s. Looser bound (0.25s) to absorb scheduler jitter.

    Documents the contract: the gather is real (not sequential).
    """
    import time

    qids = {f"agent-{i}": f"qid-{i}" for i in (1, 2, 3)}
    monkeypatch.setattr(
        mcp_mod._monitor_tools, "_post", _make_post_mock(qids)
    )

    async def _slow_wait(session_id, question_id, timeout):
        await asyncio.sleep(0.1)
        return f"✅ answer received for q={question_id} (answered by x):\n\nslow"

    monkeypatch.setattr(
        mcp_mod._monitor_tools, "session_wait_for_answer", _slow_wait
    )

    start = time.monotonic()
    await mcp_mod.delegate_to_agents(
        from_session_id="master-id",
        targets=["agent-1", "agent-2", "agent-3"],
        task="task",
        timeout=10,
    )
    elapsed = time.monotonic() - start

    # 3 × 0.1s sequential = 0.3s; parallel = ~0.1s. Allow generous slack.
    assert elapsed < 0.25, f"fan-out waits look sequential (took {elapsed:.3f}s)"
