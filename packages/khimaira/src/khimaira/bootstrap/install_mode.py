"""Install-mode detection + PyPI upgrade helpers for `khimaira sync`.

Two installation modes:

  - **editable** — contributor workflow: `git clone` + `uv sync
    --all-packages`. `khimaira.__file__` resolves under a workspace
    checkout (`<project>/packages/khimaira/src/khimaira/__init__.py`).
    `khimaira sync` should `git pull` repos + `uv sync` workspace deps.

  - **site-packages** — OSS-user workflow: `uvx khimaira` or `pip
    install khimaira`. `khimaira.__file__` resolves under a venv's
    `site-packages`. `khimaira sync` should check PyPI for a newer
    release + offer to upgrade in-place (`uv tool upgrade khimaira`
    or `pip install -U khimaira`).

The mode determines the shape of the entire sync pipeline, so detect
once at the top of `run_sync` and branch on it.
"""

from __future__ import annotations

import importlib.metadata
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Literal

from khimaira.log import get_logger

log = get_logger("bootstrap.install_mode")

InstallMode = Literal["editable", "site-packages"]
UpgradeTool = Literal["uv-tool", "pip"]

# Sibling distributions that ship from the khimaira monorepo on PyPI.
# Used to discover which ones to include in the upgrade command —
# upgrade what's installed, don't add what isn't.
SIBLING_DISTRIBUTIONS: tuple[str, ...] = (
    "khimaira-types",
    "khimaira-transport",
    "khimaira-seance",
    "khimaira-specter",
    "khimaira-scarlet",
    "khimaira-sibyl",
)


def detect_install_mode(khimaira_file: str | None = None) -> InstallMode:
    """Return 'editable' for a workspace checkout, 'site-packages' otherwise.

    `khimaira_file` defaults to `khimaira.__file__` — overridable for
    unit tests. The check inspects path parts:

      - any path with `site-packages` in it (covers both `pip install`
        into a regular venv and `uvx khimaira`'s `~/.local/share/uv/
        tools/khimaira/lib/python3.X/site-packages/khimaira/...`)
        → 'site-packages'
      - everything else → 'editable' (default to the safer of the two
        for the contributor case)

    Editable installs may live anywhere on disk (the convention is
    `~/dev/khimaira` but isn't load-bearing), so we don't pattern-match
    against `packages/` or `src/`. The site-packages signal is the
    distinguishing one — its absence implies a working tree.
    """
    if khimaira_file is None:
        import khimaira

        khimaira_file = khimaira.__file__

    parts = Path(khimaira_file).resolve().parts
    if "site-packages" in parts:
        return "site-packages"
    return "editable"


def detect_upgrade_tool(executable: str | None = None) -> UpgradeTool:
    """Return 'uv-tool' if installed via uvx/`uv tool install`, else 'pip'.

    Detection: `uv tool install` places the venv under
    `~/.local/share/uv/tools/<pkg>/`. A `sys.executable` containing
    `uv/tools/` is the distinguishing signal. Anything else (regular
    venv, pipx, system python) gets 'pip'.

    `executable` defaults to `sys.executable` — overridable for
    unit tests.
    """
    if executable is None:
        executable = sys.executable

    if "uv/tools/" in executable or "uv\\tools\\" in executable:
        return "uv-tool"
    return "pip"


def discover_installed_siblings() -> list[str]:
    """Return the subset of SIBLING_DISTRIBUTIONS currently installed.

    Used to build an explicit upgrade target list. Pip's `[all]` extra
    is wrong here because it would INSTALL missing siblings on upgrade
    — the user opted out by not installing them, respect that.

    Uses `importlib.metadata.distributions()` so it works regardless of
    whether the package was installed by pip, uv, or any other PEP 517
    installer. Missing distributions are silently skipped.
    """
    found: list[str] = []
    try:
        installed = {dist.metadata["Name"].lower() for dist in importlib.metadata.distributions()}
    except Exception:  # noqa: BLE001 — defensive; metadata read is best-effort
        log.warning("could not enumerate installed distributions")
        return found

    for name in SIBLING_DISTRIBUTIONS:
        if name.lower() in installed:
            found.append(name)
    return found


def check_pypi_version(package: str = "khimaira", *, timeout: float = 5.0) -> str | None:
    """Fetch the latest released version of `package` from PyPI.

    Returns the version string (e.g. "0.2.1") or None if the request
    failed (network down, 404, malformed JSON). Never raises — caller
    gets None and treats it as "skip upgrade check this run".

    Hits `https://pypi.org/pypi/<package>/json` and reads `.info.version`.
    Short timeout (5s default) so `khimaira sync` on a flaky network
    doesn't hang.
    """
    url = f"https://pypi.org/pypi/{package}/json"
    req = urllib.request.Request(url, headers={"User-Agent": "khimaira-sync/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)
        version = data.get("info", {}).get("version")
        if isinstance(version, str) and version:
            return version
        return None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as e:
        log.warning("PyPI version check failed for %s: %s", package, e)
        return None


def is_newer_version(current: str, latest: str) -> bool:
    """True if `latest` is strictly newer than `current` (PEP 440 aware).

    Uses `packaging.version.Version` when available (always present in
    a uv/pip-installed Python), falls back to string compare otherwise.
    Pre-release / dev versions (e.g. `0.2.0.dev1`) compare correctly
    against released versions under PEP 440.
    """
    try:
        from packaging.version import Version

        return Version(latest) > Version(current)
    except Exception:  # noqa: BLE001
        # Fallback: lexicographic. Inaccurate for some edge cases but
        # avoids a hard dependency on `packaging` (it's transitively
        # available via uv but defensive code is cheap).
        return latest != current and latest > current


def build_upgrade_command(tool: UpgradeTool, packages: list[str]) -> list[str]:
    """Build the argv for the upgrade subprocess.

    `uv tool upgrade` only takes one package at a time and ignores
    `khimaira-*` siblings (it manages the khimaira tool's venv as a
    whole — siblings get pulled in automatically as deps of the
    upgraded khimaira). So uv-tool mode always upgrades just
    `khimaira`; pip mode upgrades the full explicit list.
    """
    if tool == "uv-tool":
        return ["uv", "tool", "upgrade", "khimaira"]
    # pip mode: pip install -U for the explicit list. Prefer the
    # current interpreter's pip to avoid hitting a system-wide pip
    # that doesn't see this venv.
    return [sys.executable, "-m", "pip", "install", "--upgrade", *packages]


def run_upgrade(tool: UpgradeTool, packages: list[str]) -> tuple[bool, str]:
    """Execute the upgrade subprocess. Returns (success, combined_output).

    Output is captured so the caller can attach it to an OpResult
    detail line — the user wants to see what pip/uv did, especially
    when something goes wrong (network error, conflict, etc.).
    """
    cmd = build_upgrade_command(tool, packages)
    log.info("upgrade command: %s", " ".join(cmd))
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as e:
        return False, f"{e.filename or cmd[0]} not found on PATH"

    output = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode == 0, output.strip()
