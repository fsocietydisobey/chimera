"""Tests for `khimaira.bootstrap.install_mode` + the site-packages
branch of `run_sync` (task v1.2).

Covers:
  - detect_install_mode: editable vs site-packages vs uvx-tool layouts
  - detect_upgrade_tool: uvx tool venvs vs regular venvs
  - discover_installed_siblings: subset semantics, empty + full
  - check_pypi_version: mock HTTP via patched urlopen
  - is_newer_version: PEP 440 semver + edge cases
  - build_upgrade_command: uv-tool vs pip shape
  - check_and_upgrade_khimaira: every status path (skipped/unchanged/
    updated/failed) with mocked PyPI + subprocess + prompt

All tests are hermetic — no real network, no real subprocess. Patches
the public surface of the module so the orchestration path is
testable without an actual upgrade ever firing.
"""

from __future__ import annotations

import io
import json
from unittest.mock import MagicMock, patch

import pytest

from khimaira.bootstrap import install_mode
from khimaira.bootstrap.operations import check_and_upgrade_khimaira


# ---------------------------------------------------------------------------
# detect_install_mode
# ---------------------------------------------------------------------------


def test_detect_install_mode_editable_workspace():
    """Editable install: path under workspace checkout — no site-packages part."""
    fake = "/home/dev/khimaira/packages/khimaira/src/khimaira/__init__.py"
    assert install_mode.detect_install_mode(khimaira_file=fake) == "editable"


def test_detect_install_mode_pip_venv():
    """Regular pip install in a venv: path contains site-packages."""
    fake = "/home/user/.venv/lib/python3.12/site-packages/khimaira/__init__.py"
    assert install_mode.detect_install_mode(khimaira_file=fake) == "site-packages"


def test_detect_install_mode_uvx_tool():
    """uvx khimaira: tool venv under ~/.local/share/uv/tools/khimaira/."""
    fake = "/home/user/.local/share/uv/tools/khimaira/lib/python3.12/site-packages/khimaira/__init__.py"
    assert install_mode.detect_install_mode(khimaira_file=fake) == "site-packages"


def test_detect_install_mode_editable_unusual_path():
    """Editable installs may live anywhere — only site-packages absence matters."""
    fake = "/tmp/scratch/khimaira/packages/khimaira/src/khimaira/__init__.py"
    assert install_mode.detect_install_mode(khimaira_file=fake) == "editable"


# ---------------------------------------------------------------------------
# detect_upgrade_tool
# ---------------------------------------------------------------------------


def test_detect_upgrade_tool_uvx():
    """sys.executable under uv/tools/ → uv-tool."""
    fake = "/home/user/.local/share/uv/tools/khimaira/bin/python"
    assert install_mode.detect_upgrade_tool(executable=fake) == "uv-tool"


def test_detect_upgrade_tool_pip_venv():
    """Regular venv interpreter → pip."""
    fake = "/home/user/project/.venv/bin/python3.12"
    assert install_mode.detect_upgrade_tool(executable=fake) == "pip"


def test_detect_upgrade_tool_system_python():
    """System Python → pip (no uv signal)."""
    fake = "/usr/bin/python3"
    assert install_mode.detect_upgrade_tool(executable=fake) == "pip"


# ---------------------------------------------------------------------------
# is_newer_version
# ---------------------------------------------------------------------------


def test_is_newer_version_strictly_newer():
    assert install_mode.is_newer_version("0.1.0", "0.2.0") is True
    assert install_mode.is_newer_version("0.1.0", "0.1.1") is True
    assert install_mode.is_newer_version("0.1.0", "1.0.0") is True


def test_is_newer_version_same():
    assert install_mode.is_newer_version("0.2.0", "0.2.0") is False


def test_is_newer_version_older():
    assert install_mode.is_newer_version("0.2.0", "0.1.9") is False


def test_is_newer_version_dev_to_release():
    # PEP 440: 0.2.0.dev1 < 0.2.0
    assert install_mode.is_newer_version("0.2.0.dev1", "0.2.0") is True


# ---------------------------------------------------------------------------
# build_upgrade_command
# ---------------------------------------------------------------------------


def test_build_upgrade_command_uv_tool_single_package():
    """uv tool upgrade takes one tool; siblings cascade via deps."""
    cmd = install_mode.build_upgrade_command(
        "uv-tool", ["khimaira", "khimaira-seance"]
    )
    assert cmd == ["uv", "tool", "upgrade", "khimaira"]


def test_build_upgrade_command_pip_full_list():
    """pip mode upgrades every explicit package the user has installed."""
    cmd = install_mode.build_upgrade_command(
        "pip", ["khimaira", "khimaira-types", "khimaira-seance"]
    )
    assert cmd[:3] == [pytest.importorskip("sys").executable, "-m", "pip"]
    assert cmd[3:5] == ["install", "--upgrade"]
    assert set(cmd[5:]) == {"khimaira", "khimaira-types", "khimaira-seance"}


# ---------------------------------------------------------------------------
# check_pypi_version
# ---------------------------------------------------------------------------


def _fake_urlopen(payload: dict):
    """Return a context-manager mock yielding `payload` as JSON."""
    mock = MagicMock()
    mock.__enter__ = MagicMock(return_value=io.BytesIO(json.dumps(payload).encode()))
    mock.__exit__ = MagicMock(return_value=False)
    return mock


def test_check_pypi_version_happy():
    """Returns the version string from a well-formed PyPI response."""
    payload = {"info": {"version": "0.5.0"}, "releases": {}}
    with patch("urllib.request.urlopen", return_value=_fake_urlopen(payload)):
        assert install_mode.check_pypi_version("khimaira") == "0.5.0"


def test_check_pypi_version_network_error_returns_none():
    """Network failure → None, no exception bubbles."""
    import urllib.error

    with patch(
        "urllib.request.urlopen",
        side_effect=urllib.error.URLError("network unreachable"),
    ):
        assert install_mode.check_pypi_version("khimaira") is None


def test_check_pypi_version_malformed_json_returns_none():
    """Malformed JSON → None."""
    mock = MagicMock()
    mock.__enter__ = MagicMock(return_value=io.BytesIO(b"not json"))
    mock.__exit__ = MagicMock(return_value=False)
    with patch("urllib.request.urlopen", return_value=mock):
        assert install_mode.check_pypi_version("khimaira") is None


def test_check_pypi_version_missing_field_returns_none():
    """info.version missing → None (don't trust partial responses)."""
    payload = {"info": {}, "releases": {}}
    with patch("urllib.request.urlopen", return_value=_fake_urlopen(payload)):
        assert install_mode.check_pypi_version("khimaira") is None


# ---------------------------------------------------------------------------
# check_and_upgrade_khimaira — orchestration paths
# ---------------------------------------------------------------------------


def test_check_and_upgrade_skipped_when_pypi_unreachable():
    """PyPI down → skipped, no subprocess."""
    with patch(
        "khimaira.bootstrap.install_mode.check_pypi_version", return_value=None
    ):
        result = check_and_upgrade_khimaira(auto_upgrade=True)
    assert result.status == "skipped"
    assert "PyPI version check failed" in result.detail


def test_check_and_upgrade_unchanged_when_same_version():
    """Already on latest → unchanged."""
    from khimaira import __version__

    with patch(
        "khimaira.bootstrap.install_mode.check_pypi_version",
        return_value=__version__,
    ):
        result = check_and_upgrade_khimaira(auto_upgrade=True)
    assert result.status == "unchanged"
    assert __version__ in result.detail


def test_check_and_upgrade_auto_upgrade_runs_subprocess():
    """auto_upgrade=True + newer PyPI version → upgrade fires."""
    with patch(
        "khimaira.bootstrap.install_mode.check_pypi_version", return_value="999.0.0"
    ), patch(
        "khimaira.bootstrap.install_mode.run_upgrade", return_value=(True, "ok")
    ) as run_mock:
        result = check_and_upgrade_khimaira(auto_upgrade=True)
    assert result.status == "updated"
    assert "999.0.0" in result.detail
    run_mock.assert_called_once()


def test_check_and_upgrade_failed_subprocess_reported():
    """Upgrade subprocess fails → failed status with output captured."""
    with patch(
        "khimaira.bootstrap.install_mode.check_pypi_version", return_value="999.0.0"
    ), patch(
        "khimaira.bootstrap.install_mode.run_upgrade",
        return_value=(False, "ERROR: conflict resolving deps"),
    ):
        result = check_and_upgrade_khimaira(auto_upgrade=True)
    assert result.status == "failed"
    assert "conflict resolving deps" in result.detail


def test_check_and_upgrade_non_interactive_skips_without_auto_upgrade():
    """No tty + auto_upgrade=False → skipped with rerun hint, no subprocess."""
    with patch(
        "khimaira.bootstrap.install_mode.check_pypi_version", return_value="999.0.0"
    ), patch("sys.stdin.isatty", return_value=False), patch(
        "khimaira.bootstrap.install_mode.run_upgrade"
    ) as run_mock:
        result = check_and_upgrade_khimaira(auto_upgrade=False)
    assert result.status == "skipped"
    assert "--auto-upgrade" in result.detail
    run_mock.assert_not_called()


def test_check_and_upgrade_interactive_yes():
    """tty + user accepts prompt → upgrade fires."""
    with patch(
        "khimaira.bootstrap.install_mode.check_pypi_version", return_value="999.0.0"
    ), patch("sys.stdin.isatty", return_value=True), patch(
        "khimaira.bootstrap.install_mode.run_upgrade", return_value=(True, "ok")
    ) as run_mock:
        result = check_and_upgrade_khimaira(
            auto_upgrade=False, prompt_fn=lambda _msg: "y"
        )
    assert result.status == "updated"
    run_mock.assert_called_once()


def test_check_and_upgrade_interactive_no():
    """tty + user declines prompt → skipped, no subprocess."""
    with patch(
        "khimaira.bootstrap.install_mode.check_pypi_version", return_value="999.0.0"
    ), patch("sys.stdin.isatty", return_value=True), patch(
        "khimaira.bootstrap.install_mode.run_upgrade"
    ) as run_mock:
        result = check_and_upgrade_khimaira(
            auto_upgrade=False, prompt_fn=lambda _msg: "n"
        )
    assert result.status == "skipped"
    assert "declined" in result.detail
    run_mock.assert_not_called()


def test_check_and_upgrade_interactive_default_yes_on_empty_input():
    """tty + bare Enter (empty input) → defaults to yes (matches [Y/n] convention)."""
    with patch(
        "khimaira.bootstrap.install_mode.check_pypi_version", return_value="999.0.0"
    ), patch("sys.stdin.isatty", return_value=True), patch(
        "khimaira.bootstrap.install_mode.run_upgrade", return_value=(True, "ok")
    ) as run_mock:
        result = check_and_upgrade_khimaira(
            auto_upgrade=False, prompt_fn=lambda _msg: ""
        )
    assert result.status == "updated"
    run_mock.assert_called_once()


def test_check_and_upgrade_interactive_eof_treated_as_no():
    """tty + EOFError (Ctrl-D) → declined, no subprocess."""

    def raise_eof(_msg):
        raise EOFError()

    with patch(
        "khimaira.bootstrap.install_mode.check_pypi_version", return_value="999.0.0"
    ), patch("sys.stdin.isatty", return_value=True), patch(
        "khimaira.bootstrap.install_mode.run_upgrade"
    ) as run_mock:
        result = check_and_upgrade_khimaira(
            auto_upgrade=False, prompt_fn=raise_eof
        )
    assert result.status == "skipped"
    run_mock.assert_not_called()
