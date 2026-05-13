"""Runner circuit breaker — track per-runner failures, skip cooled-down runners.

Two failure modes the pool router needs to react to:

1. **Rate limit** — runner returned 429 / "rate limit" / "credit exhausted".
   The runner is healthy but throttled. Mark cooled-down for a short window
   (default 60s) and the pool router skips it; auto-mode picks the
   next-cheapest model from the pool. When the user's quota resets, the
   cooldown expires and traffic resumes naturally.

2. **Repeated transient failure** — runner errored K consecutive times for
   non-rate-limit reasons (network, malformed response, runner crash).
   After K=3 (default), open the circuit for a longer window (default 5min)
   so we don't keep slamming a broken runner. One success while the circuit
   is open closes it immediately.

State is in-memory and process-scoped — a process restart resets every
counter. That's the right semantic: the user expects "wait it out" to mean
"wait 5 minutes," not "wait 5 minutes AND restart the daemon." If the
daemon restarts in the middle, the worst case is one extra failed dispatch
before we re-discover the runner is still broken.

Integration:
  - `_delegate_impl` (server/mcp.py) catches the dispatch exception and
    routes it to `record_rate_limit` or `record_failure` based on type.
  - `pool_router.select_from_pool` is called with `runner_available=` set to
    a callable that combines the runner's `is_available()` AND `not circuit.is_open()`,
    so cooled-down runners drop out of the auto-mode pool automatically.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field

from khimaira.log import get_logger

log = get_logger("dispatch.circuit")


# Thresholds — env-overridable for tuning without code changes.
_CIRCUIT_BREAK_K = int(os.environ.get("KHIMAIRA_CIRCUIT_BREAK_K", "3"))
_CIRCUIT_COOLDOWN_S = float(os.environ.get("KHIMAIRA_CIRCUIT_COOLDOWN_S", "300"))
_RATE_LIMIT_COOLDOWN_S = float(os.environ.get("KHIMAIRA_RATE_LIMIT_COOLDOWN_S", "60"))


@dataclass
class RunnerCircuit:
    """Per-runner failure tracking. Thread-safe (the dispatch path may
    invoke this from any async task running on any thread)."""

    # Maps runner_name → consecutive non-rate-limit failure count.
    consecutive_failures: dict[str, int] = field(default_factory=dict)
    # Maps runner_name → unix timestamp when cooldown ends.
    cooldown_until: dict[str, float] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def is_open(self, runner: str) -> bool:
        """True if `runner` is currently cooled-down. Cheap; safe to call
        from the pool router's per-entry availability check."""
        with self._lock:
            return time.time() < self.cooldown_until.get(runner, 0.0)

    def seconds_until_recovered(self, runner: str) -> float:
        """How long until the cooldown expires, in seconds. 0 if not open."""
        with self._lock:
            remaining = self.cooldown_until.get(runner, 0.0) - time.time()
            return max(0.0, remaining)

    def record_success(self, runner: str) -> None:
        """A successful dispatch clears any failure streak AND any active
        cooldown (a runner that just succeeded is by definition healthy)."""
        with self._lock:
            self.consecutive_failures.pop(runner, None)
            self.cooldown_until.pop(runner, None)

    def record_rate_limit(self, runner: str) -> None:
        """The runner reported a rate limit / credit-exhausted / auth-billing
        condition. Short cooldown — the runner is healthy, just throttled."""
        with self._lock:
            self.cooldown_until[runner] = time.time() + _RATE_LIMIT_COOLDOWN_S
        log.warning(
            "circuit: runner %r cooled down for %ss (rate-limit)",
            runner,
            _RATE_LIMIT_COOLDOWN_S,
        )

    def record_failure(self, runner: str) -> None:
        """A non-rate-limit failure. Increments the consecutive-failure
        counter; once it hits K, open the circuit for the longer window."""
        with self._lock:
            n = self.consecutive_failures.get(runner, 0) + 1
            self.consecutive_failures[runner] = n
            if n >= _CIRCUIT_BREAK_K:
                self.cooldown_until[runner] = time.time() + _CIRCUIT_COOLDOWN_S
                # Reset counter after opening — the next failure starts a
                # fresh streak (relative to the cooldown's expiry).
                self.consecutive_failures[runner] = 0
                log.warning(
                    "circuit: runner %r OPEN — %d consecutive failures, "
                    "cooled down for %ss",
                    runner,
                    _CIRCUIT_BREAK_K,
                    _CIRCUIT_COOLDOWN_S,
                )

    def status_snapshot(self) -> dict[str, dict[str, float | int]]:
        """For diagnostics / dashboards — current state per runner."""
        with self._lock:
            now = time.time()
            return {
                runner: {
                    "consecutive_failures": self.consecutive_failures.get(runner, 0),
                    "cooldown_remaining_s": max(0.0, self.cooldown_until.get(runner, 0.0) - now),
                }
                for runner in (
                    set(self.consecutive_failures) | set(self.cooldown_until)
                )
            }

    def reset(self) -> None:
        """Clear ALL state. Test-only helper; production callers should rely
        on cooldowns expiring naturally."""
        with self._lock:
            self.consecutive_failures.clear()
            self.cooldown_until.clear()


_circuit = RunnerCircuit()


def get_circuit() -> RunnerCircuit:
    return _circuit
