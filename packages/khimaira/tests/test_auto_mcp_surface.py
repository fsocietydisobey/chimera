"""Unit-level tests for `mcp__khimaira__auto` via `_delegate_impl(tier='auto')`.

The integration tests in test_delegate_auto_e2e.py exercise the real
classifier + real runner path, but they're marked `pytest.mark.integration`
and skip on CI / when no runner is installed. This file fills the gap:
unit-level coverage of the auto-routing surface using mocks for the
classifier, pool router, and runner.

What's tested:
  - tier='auto' produces mode='auto' in usage.jsonl
  - The pool router's decision flows through to the runner.run() call
    (chosen_model + chosen_runner come from select_from_pool's output)
  - Audit-log line is emitted with the documented field set
  - A refused PoolDecision (no eligible model) short-circuits cleanly
    without a usage record being written
"""

from __future__ import annotations

import importlib
import json
import logging
from pathlib import Path

import pytest
from khimaira_types import TaskClassification

from khimaira.dispatch.pool_router import PoolDecision
from khimaira.dispatch.registry import ModelCost, ModelEntry


def _fake_classification(confidence: float = 0.92) -> TaskClassification:
    return TaskClassification(
        task_type="classify",  # type: ignore[arg-type]
        complexity_tier="trivial",  # type: ignore[arg-type]
        thinking_level="none",  # type: ignore[arg-type]
        recommended_runner="claude",
        recommended_model="claude-haiku-4-5",
        thinking_budget_tokens=0,
        estimated_cost_usd_max=0.01,
        reasoning="unit test",
        confidence=confidence,
    )


def _fake_entry() -> ModelEntry:
    return ModelEntry(
        id="claude-haiku-4-5",
        runner="claude",
        capabilities=("classification", "syntax"),
        cost_per_1m=ModelCost(input=0.8, output=4.0),
        enabled_for_auto=True,
    )


class _FakeRunner:
    """Captures the dispatch instead of shelling out."""

    def __init__(self):
        self.run_calls: list[tuple[str, str | None]] = []

    def is_available(self):
        return True

    async def run(self, prompt, model=None, timeout=None):
        self.run_calls.append((prompt, model))

        class _Result:
            text = "fake auto-routed answer"
            model_field = model or "claude-haiku-4-5"
            input_tokens = 12
            output_tokens = 8
            latency_s = 0.05

        # `result.model` is read by the dispatcher; alias to model_field
        _Result.model = _Result.model_field
        return _Result()


@pytest.fixture
def isolated_usage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Re-root usage.jsonl so unit tests don't pollute real usage data."""
    state_root = tmp_path / "state"
    state_root.mkdir()
    monkeypatch.setenv("XDG_STATE_HOME", str(state_root))
    from khimaira import usage as usage_mod

    importlib.reload(usage_mod)
    yield usage_mod
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    importlib.reload(usage_mod)


def _read_usage(log_path: Path) -> list[dict]:
    if not log_path.is_file():
        return []
    return [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]


def _mock_auto_path(monkeypatch, *, decision: PoolDecision, runner: _FakeRunner):
    """Wire up the auto-path mocks: classifier, pool router, runner."""

    async def _fake_classify_task(prompt: str):
        return _fake_classification()

    def _fake_select_from_pool(classification, *, runner_available=None):
        return decision

    monkeypatch.setattr(
        "khimaira.dispatch.classifier.classify_task", _fake_classify_task
    )
    monkeypatch.setattr(
        "khimaira.dispatch.pool_router.select_from_pool", _fake_select_from_pool
    )
    monkeypatch.setattr(
        "khimaira.dispatch.runners.get_runner", lambda name: runner
    )


@pytest.mark.asyncio
async def test_auto_path_writes_usage_with_mode_auto(isolated_usage, monkeypatch):
    """The full auto path lands a UsageRecord with mode='auto' — this is
    what `khimaira usage savings` reads to credit routing savings."""
    runner = _FakeRunner()
    decision = PoolDecision(
        chosen=_fake_entry(),
        required_caps=frozenset({"classification"}),
        classifier_confidence=0.92,
        pool_size=3,
        available_size=2,
        eligible_size=1,
        top_2=[("claude-haiku-4-5", 0.10)],
        rejected={},
    )
    _mock_auto_path(monkeypatch, decision=decision, runner=runner)

    from khimaira.server import mcp as mcp_mod

    result = await mcp_mod._delegate_impl(
        "what is 2 + 2?", tier="auto", timeout_s=30
    )

    assert "fake auto-routed answer" in result, f"unexpected dispatch result: {result}"
    assert "mode=auto" in result

    # Exactly one usage record, mode='auto'
    rows = _read_usage(isolated_usage.log_file_path())
    assert len(rows) == 1, f"expected 1 usage row, got {len(rows)}: {rows}"
    r = rows[0]
    assert r["mode"] == "auto"
    assert r["model"] == "claude-haiku-4-5"
    assert r["runner"] == "claude"
    assert r["input_tokens"] > 0
    assert r["output_tokens"] > 0


@pytest.mark.asyncio
async def test_auto_path_emits_audit_log_line(isolated_usage, monkeypatch, caplog):
    """The auto-route audit log line is emitted at INFO with the documented
    field set. Downstream `khimaira usage savings --audit` greps this line."""
    runner = _FakeRunner()
    decision = PoolDecision(
        chosen=_fake_entry(),
        required_caps=frozenset({"classification"}),
        classifier_confidence=0.85,
        pool_size=4,
        available_size=2,
        eligible_size=1,
        top_2=[("claude-haiku-4-5", 0.10), ("gemini-2.5-flash", 0.30)],
        rejected={"claude-opus-4-7": "missing-caps:cheap"},
    )
    _mock_auto_path(monkeypatch, decision=decision, runner=runner)

    from khimaira.server import mcp as mcp_mod

    with caplog.at_level(logging.INFO, logger="graph-server"):
        await mcp_mod._delegate_impl("hello", tier="auto", timeout_s=30)

    audit_lines = [r.message for r in caplog.records if "auto-route audit" in r.message]
    assert audit_lines, f"no audit line emitted. records: {[r.message for r in caplog.records]}"

    line = audit_lines[0]
    # Every field the audit consumers will read post-hoc
    assert "model=claude-haiku-4-5" in line
    assert "runner=claude" in line
    assert "confidence=0.85" in line
    assert "pool=4" in line
    assert "avail=2" in line
    assert "elig=1" in line
    assert "claude-haiku-4-5" in line  # top_2 entry
    assert "missing-caps:cheap" in line  # rejected reason


@pytest.mark.asyncio
async def test_auto_path_refused_does_not_write_usage(isolated_usage, monkeypatch):
    """When the pool router refuses (no eligible model), the auto path
    returns an error without invoking the runner and without writing a
    usage record. Negative-path coverage — a wasted dispatch would
    pollute the savings ledger."""
    runner = _FakeRunner()
    decision = PoolDecision(
        chosen=None,
        required_caps=frozenset({"impossible-cap"}),
        classifier_confidence=0.9,
        pool_size=3,
        available_size=0,
        eligible_size=0,
        top_2=[],
        rejected={"claude-haiku-4-5": "runner-unavailable:claude"},
        refused=True,
        refusal_reason="no auto-pool runner installed",
    )
    _mock_auto_path(monkeypatch, decision=decision, runner=runner)

    from khimaira.server import mcp as mcp_mod

    result = await mcp_mod._delegate_impl(
        "trigger refusal", tier="auto", timeout_s=30
    )

    assert "❌" in result
    assert "auto routing refused" in result
    assert "no auto-pool runner installed" in result

    # Runner was never invoked, no usage row written
    assert runner.run_calls == []
    rows = _read_usage(isolated_usage.log_file_path())
    assert rows == []
