"""Tests for `PoolDecision.to_audit_dict()` — the audit-log payload shape.

The auto-route audit trail lands in `~/.local/state/khimaira/khimaira.log`
as a structured single line. Downstream — `khimaira usage savings
--audit` (planned) — reads it back to flag classifier mis-routes. The
fields it reads are contract; this file pins them.
"""

from __future__ import annotations

import pytest

from khimaira.dispatch.pool_router import PoolDecision
from khimaira.dispatch.registry import ModelCost, ModelEntry


def _entry(id: str = "claude-haiku-4-5", runner: str = "claude") -> ModelEntry:
    return ModelEntry(
        id=id,
        runner=runner,
        capabilities=("classification", "syntax"),
        cost_per_1m=ModelCost(input=0.8, output=4.0),
        enabled_for_auto=True,
    )


def test_audit_dict_has_all_documented_fields():
    """to_audit_dict() must include every field the savings --audit
    command reads. Adding fields is fine; removing or renaming breaks
    contract with `khimaira usage savings --audit`."""
    entry = _entry()
    decision = PoolDecision(
        chosen=entry,
        required_caps=frozenset({"classification"}),
        classifier_confidence=0.92,
        pool_size=5,
        available_size=3,
        eligible_size=2,
        top_2=[("claude-haiku-4-5", 0.10), ("gemini-2.5-flash", 0.15)],
        rejected={"claude-opus-4-7": "missing-caps:fast"},
    )
    d = decision.to_audit_dict()

    required_keys = {
        "chosen_id",
        "chosen_runner",
        "required_caps",
        "classifier_confidence",
        "pool_size",
        "available_size",
        "eligible_size",
        "top_2",
        "rejected_reasons",
        "refused",
        "refusal_reason",
    }
    missing = required_keys - set(d.keys())
    assert not missing, f"audit dict missing keys: {missing}"


def test_audit_dict_chosen_fields_populated():
    """When a model is chosen, chosen_id + chosen_runner reflect it."""
    entry = _entry(id="gemini-2.5-flash", runner="gemini")
    decision = PoolDecision(
        chosen=entry,
        required_caps=frozenset(),
        classifier_confidence=0.5,
        pool_size=1,
        available_size=1,
        eligible_size=1,
    )
    d = decision.to_audit_dict()

    assert d["chosen_id"] == "gemini-2.5-flash"
    assert d["chosen_runner"] == "gemini"
    assert d["refused"] is False
    assert d["refusal_reason"] is None


def test_audit_dict_refused_path_carries_reason():
    """When refused (no eligible model), chosen_* are None and refusal_reason
    carries the explanation. The auto-route handler in server/mcp.py reads
    refusal_reason verbatim to surface back to the agent."""
    decision = PoolDecision(
        chosen=None,
        required_caps=frozenset({"classification"}),
        classifier_confidence=0.85,
        pool_size=3,
        available_size=0,
        eligible_size=0,
        top_2=[],
        rejected={"claude-haiku-4-5": "runner-unavailable:claude"},
        refused=True,
        refusal_reason="no auto-pool runner installed (pool size 3; all rejected for runner-unavailable).",
    )
    d = decision.to_audit_dict()

    assert d["chosen_id"] is None
    assert d["chosen_runner"] is None
    assert d["refused"] is True
    assert "no auto-pool runner installed" in d["refusal_reason"]


def test_audit_dict_required_caps_sorted_for_stable_diff():
    """required_caps is a frozenset internally — emitted as a SORTED list
    so log diffs are stable across runs (sets don't have a stable order)."""
    decision = PoolDecision(
        chosen=_entry(),
        required_caps=frozenset({"syntax", "classification", "fast"}),
        classifier_confidence=0.9,
        pool_size=1,
        available_size=1,
        eligible_size=1,
    )
    d = decision.to_audit_dict()

    assert d["required_caps"] == ["classification", "fast", "syntax"]
    # And it's a list (JSON-serializable), not a set
    assert isinstance(d["required_caps"], list)


def test_audit_dict_empty_rejected_is_dict_not_none():
    """rejected={} stays a dict in the audit output — the savings --audit
    command expects to iterate over .items() without a None-check guard."""
    decision = PoolDecision(
        chosen=_entry(),
        required_caps=frozenset(),
        classifier_confidence=1.0,
        pool_size=1,
        available_size=1,
        eligible_size=1,
        top_2=[("claude-haiku-4-5", 0.1)],
        rejected={},
    )
    d = decision.to_audit_dict()

    assert d["rejected_reasons"] == {}
    assert isinstance(d["rejected_reasons"], dict)


def test_audit_dict_top_2_preserves_tuple_score_pairs():
    """top_2 is a list of (model_id, score) tuples — must round-trip through
    the audit dict so downstream tooling can read the second-place score."""
    decision = PoolDecision(
        chosen=_entry(),
        required_caps=frozenset(),
        classifier_confidence=0.7,
        pool_size=2,
        available_size=2,
        eligible_size=2,
        top_2=[("a", 0.10), ("b", 0.25)],
    )
    d = decision.to_audit_dict()

    assert d["top_2"] == [("a", 0.10), ("b", 0.25)]
    assert d["top_2"][0][0] == "a"
    assert d["top_2"][0][1] == 0.10
