"""Tests for the dispatch circuit breaker (#52 + #53).

The circuit module tracks per-runner failure state in-memory. Two
failure classes drive different cooldowns:
  - rate-limit / billing → short cooldown (default 60s)
  - K consecutive non-rate-limit errors → open circuit (default 5min)

The pool router uses the circuit's `is_open()` as part of runner
availability so auto-mode falls back to the next-cheapest runner
without the caller doing anything.

Tests:
  - RunnerCircuit state transitions (success / rate-limit / failure /
    K-threshold opening / success-closes-cooldown)
  - is_open / seconds_until_recovered semantics
  - _delegate_impl marks the circuit on dispatch failure
  - auto mode skips cooled-down runners (integration via the
    pool_router callback)
"""

from __future__ import annotations

import asyncio
import time

import pytest


@pytest.fixture
def fresh_circuit(monkeypatch):
    """Each test gets a clean circuit. Resets after the test too so the
    module-level singleton doesn't carry state into other tests."""
    from khimaira.dispatch import circuit as circuit_mod
    circuit_mod.get_circuit().reset()
    yield circuit_mod.get_circuit()
    circuit_mod.get_circuit().reset()


# -------------------- RunnerCircuit unit -------------------- #


def test_fresh_circuit_is_not_open_for_any_runner(fresh_circuit):
    assert not fresh_circuit.is_open("claude")
    assert not fresh_circuit.is_open("gemini")
    assert fresh_circuit.seconds_until_recovered("claude") == 0


def test_rate_limit_opens_short_cooldown(fresh_circuit):
    fresh_circuit.record_rate_limit("claude")
    assert fresh_circuit.is_open("claude")
    remaining = fresh_circuit.seconds_until_recovered("claude")
    # default RATE_LIMIT_COOLDOWN_S is 60s; allow generous bound for test latency
    assert 30 < remaining <= 60


def test_k_consecutive_failures_opens_long_cooldown(fresh_circuit):
    # Default K is 3; record 2 failures — circuit stays closed.
    fresh_circuit.record_failure("ollama")
    fresh_circuit.record_failure("ollama")
    assert not fresh_circuit.is_open("ollama")
    # The 3rd opens it.
    fresh_circuit.record_failure("ollama")
    assert fresh_circuit.is_open("ollama")
    remaining = fresh_circuit.seconds_until_recovered("ollama")
    # default CIRCUIT_COOLDOWN_S is 300s
    assert 200 < remaining <= 300


def test_success_clears_failure_streak(fresh_circuit):
    fresh_circuit.record_failure("ollama")
    fresh_circuit.record_failure("ollama")
    fresh_circuit.record_success("ollama")
    # Streak is gone — next failure starts at 1, doesn't open after just one more
    fresh_circuit.record_failure("ollama")
    fresh_circuit.record_failure("ollama")
    assert not fresh_circuit.is_open("ollama")


def test_success_closes_active_cooldown(fresh_circuit):
    """If a rate-limit cooldown is active and the user retries successfully
    (e.g., because the rate limit lifted faster than our default window),
    success should clear the cooldown so the runner is immediately back
    in the pool."""
    fresh_circuit.record_rate_limit("claude")
    assert fresh_circuit.is_open("claude")
    fresh_circuit.record_success("claude")
    assert not fresh_circuit.is_open("claude")


def test_status_snapshot_shape(fresh_circuit):
    fresh_circuit.record_failure("ollama")
    fresh_circuit.record_rate_limit("claude")
    snap = fresh_circuit.status_snapshot()
    assert "ollama" in snap
    assert snap["ollama"]["consecutive_failures"] == 1
    assert snap["ollama"]["cooldown_remaining_s"] == 0
    assert "claude" in snap
    assert snap["claude"]["cooldown_remaining_s"] > 0


# -------------------- _delegate_impl integration -------------------- #


@pytest.fixture
def isolated_usage(tmp_path, monkeypatch):
    import importlib
    state_root = tmp_path / "state"
    state_root.mkdir()
    monkeypatch.setenv("XDG_STATE_HOME", str(state_root))
    from khimaira import usage as usage_mod
    importlib.reload(usage_mod)
    yield usage_mod
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    importlib.reload(usage_mod)


async def test_dispatch_failure_increments_failure_counter(
    fresh_circuit, isolated_usage, monkeypatch
):
    """A runner that raises a non-rate-limit Exception increments the
    consecutive-failure counter via the circuit."""
    from khimaira.server import mcp as mcp_mod

    class _BoomRunner:
        def is_available(self):
            return True

        async def run(self, prompt, model=None, timeout=None):
            raise RuntimeError("connection refused")

    monkeypatch.setattr(
        "khimaira.dispatch.runners.get_runner",
        lambda runner: _BoomRunner(),
    )

    result = await mcp_mod._delegate_impl(
        "test", tier="haiku", timeout_s=30,
    )
    assert "❌ delegate dispatch failed" in result
    snap = fresh_circuit.status_snapshot()
    assert snap.get("claude", {}).get("consecutive_failures", 0) == 1


async def test_rate_limit_exception_routes_to_rate_limit_cooldown(
    fresh_circuit, isolated_usage, monkeypatch
):
    """A runner that raises with a rate-limit-shaped message marks the
    circuit with the SHORT cooldown, not the long one."""
    from khimaira.server import mcp as mcp_mod

    class _RateLimitedRunner:
        def is_available(self):
            return True

        async def run(self, prompt, model=None, timeout=None):
            raise RuntimeError("HTTP 429: rate limit exceeded")

    monkeypatch.setattr(
        "khimaira.dispatch.runners.get_runner",
        lambda runner: _RateLimitedRunner(),
    )

    result = await mcp_mod._delegate_impl(
        "test", tier="haiku", timeout_s=30,
    )
    assert "❌ delegate dispatch failed" in result
    # Short cooldown only — was NOT bumped to circuit-open via failure counter
    snap = fresh_circuit.status_snapshot()
    assert snap.get("claude", {}).get("consecutive_failures", 0) == 0
    # Cooldown is set
    assert fresh_circuit.is_open("claude")


async def test_success_clears_failure_streak_via_dispatch(
    fresh_circuit, isolated_usage, monkeypatch
):
    """One success after several failures should clear the streak."""
    from khimaira.server import mcp as mcp_mod

    # Pre-seed failures
    fresh_circuit.record_failure("claude")
    fresh_circuit.record_failure("claude")

    class _OkRunner:
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
        lambda runner: _OkRunner(),
    )

    result = await mcp_mod._delegate_impl(
        "test", tier="haiku", timeout_s=30,
    )
    assert "ok" in result
    snap = fresh_circuit.status_snapshot()
    assert snap.get("claude", {}).get("consecutive_failures", 0) == 0


async def test_explicit_tier_refuses_when_runner_circuit_open(
    fresh_circuit, isolated_usage, monkeypatch
):
    """If the user picks tier=haiku but claude's circuit is open, refuse
    immediately — don't invoke the runner."""
    from khimaira.server import mcp as mcp_mod

    fresh_circuit.record_rate_limit("claude")  # open the circuit

    def _runner_must_not_be_called(*a, **kw):
        raise AssertionError(
            "runner should not have been invoked — circuit gate failed"
        )

    # Need is_available to pass so we reach the circuit check
    class _AvailRunner:
        def is_available(self):
            return True

        async def run(self, prompt, model=None, timeout=None):
            _runner_must_not_be_called()

    monkeypatch.setattr(
        "khimaira.dispatch.runners.get_runner",
        lambda runner: _AvailRunner(),
    )

    result = await mcp_mod._delegate_impl(
        "test", tier="haiku", timeout_s=30,
    )
    assert "cooled down" in result
    assert "claude" in result
