"""Tests for `khimaira.cli.usage._resolve_counterfactual_model`.

The savings baseline (the "what would this have cost if it had all run on
Opus?" counterfactual) is resolved at call time in this priority:

  1. KHIMAIRA_USAGE_BASELINE_MODEL env var
  2. baseline_model: top-level key in ~/.khimaira/models.yaml
  3. Hardcoded default (claude-opus-4-7)

These tests pin the precedence + make sure malformed config never crashes
the savings command.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def isolated_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Re-root the model registry path so tests don't touch real ~/.khimaira.

    `_user_registry_path()` prefers XDG_CONFIG_HOME when set, else HOME.
    We unset XDG_CONFIG_HOME and point HOME at tmp so the resolution lands
    at <tmp>/.khimaira/models.yaml — predictable for the assertions below.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("KHIMAIRA_USAGE_BASELINE_MODEL", raising=False)

    registry_path = tmp_path / ".khimaira" / "models.yaml"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    yield registry_path
    monkeypatch.delenv("KHIMAIRA_USAGE_BASELINE_MODEL", raising=False)


def test_env_var_wins_over_everything(isolated_registry, monkeypatch):
    """KHIMAIRA_USAGE_BASELINE_MODEL takes priority even when registry has its own value."""
    isolated_registry.write_text("baseline_model: claude-sonnet-4-6\n")
    monkeypatch.setenv("KHIMAIRA_USAGE_BASELINE_MODEL", "gemini-2.5-flash")

    from khimaira.cli.usage import _resolve_counterfactual_model

    assert _resolve_counterfactual_model() == "gemini-2.5-flash"


def test_registry_wins_when_no_env_var(isolated_registry):
    """With no env var set, the registry's baseline_model: key wins over the default."""
    isolated_registry.write_text("baseline_model: claude-sonnet-4-6\n")

    from khimaira.cli.usage import _resolve_counterfactual_model

    assert _resolve_counterfactual_model() == "claude-sonnet-4-6"


def test_default_when_neither_env_nor_registry(isolated_registry):
    """Empty registry + no env → fall through to the hardcoded default."""
    # Registry file doesn't exist yet
    assert not isolated_registry.exists()

    from khimaira.cli.usage import _DEFAULT_COUNTERFACTUAL_MODEL, _resolve_counterfactual_model

    assert _resolve_counterfactual_model() == _DEFAULT_COUNTERFACTUAL_MODEL


def test_registry_without_baseline_key_falls_through(isolated_registry):
    """Registry exists but has no baseline_model: key → use default."""
    isolated_registry.write_text("models:\n  - id: claude-haiku-4-5\n    runner: claude\n")

    from khimaira.cli.usage import _DEFAULT_COUNTERFACTUAL_MODEL, _resolve_counterfactual_model

    assert _resolve_counterfactual_model() == _DEFAULT_COUNTERFACTUAL_MODEL


def test_malformed_registry_does_not_crash(isolated_registry):
    """Bad YAML → log a warning, return default. Savings command should
    never crash because the user's registry is malformed."""
    isolated_registry.write_text("not: [valid: yaml: at all")

    from khimaira.cli.usage import _DEFAULT_COUNTERFACTUAL_MODEL, _resolve_counterfactual_model

    # Must return SOMETHING (the default); raising would break the
    # downstream `khimaira usage savings` command.
    assert _resolve_counterfactual_model() == _DEFAULT_COUNTERFACTUAL_MODEL


def test_empty_env_var_falls_through_to_registry(isolated_registry, monkeypatch):
    """KHIMAIRA_USAGE_BASELINE_MODEL='' should NOT count as "env override is set" —
    treat empty-string as not-set so users can clear an inherited env without
    accidentally pinning baseline to an empty model id."""
    isolated_registry.write_text("baseline_model: claude-sonnet-4-6\n")
    monkeypatch.setenv("KHIMAIRA_USAGE_BASELINE_MODEL", "")

    from khimaira.cli.usage import _resolve_counterfactual_model

    assert _resolve_counterfactual_model() == "claude-sonnet-4-6"
