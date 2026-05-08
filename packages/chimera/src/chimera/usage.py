"""LLM usage tracker — record every CLI runner call to a JSONL log.

Migrated from chimera-legacy. The dev-tool pitch ("chimera makes your
subscription stretch 5x") requires concrete numbers — this is the
audit trail those numbers come from.

Persists to `~/.local/state/chimera/usage.jsonl`. Read by:
  - /api/usage (rolling totals — chimera monitor dashboard)
  - /api/savings (counterfactual — "you'd have spent $X without AMR")
  - check_usage_rate self-watch invariant (rate-anomaly alarm)
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from chimera_types import UsageRecord

from chimera.log import get_logger

log = get_logger("usage")

_LOG_DIR = Path(
    os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state"))
) / "chimera"
_LOG_FILE = _LOG_DIR / "usage.jsonl"

# Per-million-token prices in USD. Best-effort — unknown models record
# token counts but estimate $0. Update when pricing changes.
#
# Match by *prefix*: "claude-opus-4-7-20251022" → "claude-opus-4-7"
# so future minor revs don't need code changes here.
_PRICES: dict[str, tuple[float, float]] = {
    # Anthropic Claude 4.x
    "claude-opus-4-7":   (15.0, 75.0),
    "claude-opus-4-6":   (15.0, 75.0),
    "claude-opus-4":     (15.0, 75.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-sonnet-4":   (3.0, 15.0),
    "claude-haiku-4-5":  (0.8, 4.0),
    "claude-haiku-4":    (0.8, 4.0),
    # Google Gemini 2.5
    "gemini-2.5-pro":    (1.25, 10.0),
    "gemini-2.5-flash":  (0.075, 0.30),
    "gemini-2.0-pro":    (1.25, 10.0),
    "gemini-2.0-flash":  (0.075, 0.30),
    # OpenAI Codex (rough; pricing varies across regions/tiers)
    "gpt-5-codex":       (3.0, 12.0),
    "gpt-4o":            (2.50, 10.0),
    "gpt-4o-mini":       (0.15, 0.60),
}


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate USD cost. Returns 0 for unknown models."""
    if not model:
        return 0.0
    matches = [k for k in _PRICES if model.startswith(k)]
    if not matches:
        return 0.0
    key = max(matches, key=len)
    in_per_m, out_per_m = _PRICES[key]
    return (input_tokens * in_per_m + output_tokens * out_per_m) / 1_000_000.0


def log_file_path() -> Path:
    return _LOG_FILE


@dataclass
class _Recorder:
    """Singleton — append usage records to JSONL, async-safe."""

    _lock: asyncio.Lock | None = None

    def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def record(
        self,
        *,
        runner: str,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        latency_s: float,
        role: str | None = None,
        task_id: str | None = None,
        source: str = "cli",
        escalation_count: int = 0,
    ) -> None:
        cost = estimate_cost(model, input_tokens, output_tokens)
        record = UsageRecord(
            ts=datetime.now(timezone.utc).isoformat(),
            task_id=task_id,
            runner=runner,
            provider=provider,  # type: ignore[arg-type]  (Pydantic literal narrowing)
            model=model,
            role=role,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_s=latency_s,
            estimated_cost_usd=cost,
            source=source,  # type: ignore[arg-type]
            escalation_count=escalation_count,
        )
        try:
            async with self._get_lock():
                await asyncio.to_thread(self._append, record)
        except Exception as exc:
            log.warning("usage: failed to record %s/%s: %s", runner, model, exc)

    @staticmethod
    def _append(record: UsageRecord) -> None:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        with _LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(record.model_dump_json() + "\n")


_recorder = _Recorder()


def get_recorder() -> _Recorder:
    return _recorder


def runner_to_provider(runner: str) -> str:
    """Map runner name → provider for usage records."""
    return {
        "claude": "anthropic",
        "codex": "openai",
        "gemini": "google",
        "ollama": "local",
        "llm": "other",  # depends on model; "other" is least-wrong default
    }.get(runner, "other")
