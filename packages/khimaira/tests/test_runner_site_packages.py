"""Tests for `run_sync` in site-packages mode (task v1.2).

Editable mode is exercised by `test_bootstrap_sync.py`; this file
covers the new branch: when `detect_install_mode()` returns
'site-packages', run_sync should skip git/uv-sync ops, fire the
PyPI upgrade check, and still apply the cross-mode bits (MCP, hooks).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from khimaira.bootstrap import runner
from khimaira.bootstrap.operations import OpResult
from khimaira.bootstrap.schema import Profile, SupervisorSpec


@pytest.fixture
def bare_profile():
    """Minimum-viable Profile for the site-packages branch.

    No dotfiles, no repos, no MCP, no hooks — just enough for the
    branch to run end-to-end and emit only the upgrade op.
    """
    return Profile(
        name="test-community",
        description="test fixture",
        mcp_servers=[],
        supervisor=SupervisorSpec(auto_install=False),
        install_claude_hooks=False,
        spa_build=False,
    )


def test_run_sync_site_packages_skips_git_emits_upgrade(bare_profile):
    """In site-packages mode, no repo-pull / uv-sync ops appear; upgrade op does."""
    with patch(
        "khimaira.bootstrap.runner.install_mode.detect_install_mode",
        return_value="site-packages",
    ), patch(
        "khimaira.bootstrap.operations.check_and_upgrade_khimaira",
        return_value=OpResult(
            op="package-upgrade",
            target="khimaira",
            status="unchanged",
            detail="already on 0.2.0",
        ),
    ):
        report = runner.run_sync(bare_profile, auto_upgrade=False)

    ops_seen = {(r.op, r.status) for r in report.results}
    assert ("package-upgrade", "unchanged") in ops_seen
    # Editable-mode-only ops must not appear:
    assert not any(r.op == "repo-pull" for r in report.results)
    assert not any(r.op == "uv-sync" for r in report.results)
    assert not any(r.op == "monitor-freshness" for r in report.results)


def test_run_sync_site_packages_with_upgrade_reports_updated(bare_profile):
    """When upgrade actually fires, report contains an updated row."""
    with patch(
        "khimaira.bootstrap.runner.install_mode.detect_install_mode",
        return_value="site-packages",
    ), patch(
        "khimaira.bootstrap.operations.check_and_upgrade_khimaira",
        return_value=OpResult(
            op="package-upgrade",
            target="khimaira",
            status="updated",
            detail="upgraded 0.1.0 → 0.2.0 via pip",
            meta={"current": "0.1.0", "latest": "0.2.0", "tool": "pip"},
        ),
    ):
        report = runner.run_sync(bare_profile, auto_upgrade=True)

    upgrade_rows = [r for r in report.results if r.op == "package-upgrade"]
    assert len(upgrade_rows) == 1
    assert upgrade_rows[0].status == "updated"


def test_summarize_sync_includes_upgrade_summary(bare_profile):
    """summarize_sync should surface the version delta when an upgrade ran."""
    with patch(
        "khimaira.bootstrap.runner.install_mode.detect_install_mode",
        return_value="site-packages",
    ), patch(
        "khimaira.bootstrap.operations.check_and_upgrade_khimaira",
        return_value=OpResult(
            op="package-upgrade",
            target="khimaira",
            status="updated",
            detail="upgraded 0.1.0 → 0.2.0 via pip",
            meta={"current": "0.1.0", "latest": "0.2.0"},
        ),
    ):
        report = runner.run_sync(bare_profile, auto_upgrade=True)

    summary = runner.summarize_sync(report)
    assert "khimaira 0.1.0 → 0.2.0" in summary


def test_summarize_sync_includes_skipped_upgrade(bare_profile):
    """When upgrade was skipped despite a newer version, surface the hint."""
    with patch(
        "khimaira.bootstrap.runner.install_mode.detect_install_mode",
        return_value="site-packages",
    ), patch(
        "khimaira.bootstrap.operations.check_and_upgrade_khimaira",
        return_value=OpResult(
            op="package-upgrade",
            target="khimaira",
            status="skipped",
            detail="newer release available — rerun with --auto-upgrade",
            meta={"current": "0.1.0", "latest": "0.2.0"},
        ),
    ):
        report = runner.run_sync(bare_profile, auto_upgrade=False)

    summary = runner.summarize_sync(report)
    assert "0.1.0 → 0.2.0" in summary
    assert "skipped" in summary
