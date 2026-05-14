"""Tests for `scarlet.analyzer.project._detect_project_type`.

The detection heuristic chooses ONE project type from signals in
package.json + pyproject.toml. The interesting case (and the one
that motivated the first test here) is **a Python workspace with an
embedded SPA in a subdirectory** — that should NOT be classified as
the SPA's framework just because the merged-deps view picks up its
package.json.

Bug history (2026-05-14): the khimaira workspace (Python uv monorepo
with `apps/monitor-ui/` Vite SPA) was reporting `project_type="vite"`
+ `state_management="redux-toolkit"`, misleading `khimaira-orient`
and any other consumer of `scarlet_analyze_project`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scarlet.analyzer.project import analyze_project


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# -------------------- pure cases (no ambiguity) -------------------- #


def test_pure_python_pyproject_returns_python(tmp_path):
    """Just a pyproject.toml at root → `python`."""
    _write(tmp_path / "pyproject.toml", '[project]\nname = "x"\n')
    m = analyze_project(tmp_path)
    assert m.project_type == "python"


def test_pure_python_fastapi_returns_fastapi(tmp_path):
    """pyproject mentions fastapi → narrower `fastapi` type."""
    _write(
        tmp_path / "pyproject.toml",
        '[project]\nname = "x"\ndependencies = ["fastapi>=0.115"]\n',
    )
    assert analyze_project(tmp_path).project_type == "fastapi"


def test_pure_python_django_returns_django(tmp_path):
    """pyproject mentions django → `django`."""
    _write(
        tmp_path / "pyproject.toml",
        '[project]\nname = "x"\ndependencies = ["django"]\n',
    )
    assert analyze_project(tmp_path).project_type == "django"


def test_pure_vite_returns_vite(tmp_path):
    """package.json at root with vite dep → `vite`."""
    _write(
        tmp_path / "package.json",
        json.dumps({"dependencies": {"vite": "^5", "react": "^18"}}),
    )
    assert analyze_project(tmp_path).project_type == "vite"


def test_pure_nextjs_returns_nextjs(tmp_path):
    """package.json at root with next dep → `nextjs`."""
    _write(
        tmp_path / "package.json",
        json.dumps({"dependencies": {"next": "^14", "react": "^18"}}),
    )
    assert analyze_project(tmp_path).project_type == "nextjs"


def test_empty_project_returns_generic(tmp_path):
    """No package.json, no pyproject.toml → `generic`."""
    assert analyze_project(tmp_path).project_type == "generic"


# -------------------- the bug fixture: Python workspace + embedded SPA -------------------- #


def test_python_workspace_with_nested_vite_spa_returns_python(tmp_path):
    """Regression: khimaira's shape — root pyproject.toml (Python uv
    workspace) + nested `apps/monitor-ui/package.json` with vite/react.

    Before the 2026-05-14 fix this returned `vite` because the merged
    package_json view picked up the nested SPA's deps and the
    type-detector checked package.json BEFORE pyproject.toml.

    After: root signals trump nested ones — `pyproject.toml` at root
    with no `package.json` at root is a definitive Python project,
    regardless of what's in subdirs.
    """
    _write(
        tmp_path / "pyproject.toml",
        '[project]\nname = "workspace"\ndependencies = ["fastapi"]\n',
    )
    _write(
        tmp_path / "apps" / "monitor-ui" / "package.json",
        json.dumps(
            {
                "dependencies": {
                    "vite": "^5",
                    "react": "^18",
                    "@reduxjs/toolkit": "^2",
                }
            }
        ),
    )

    m = analyze_project(tmp_path)
    assert m.project_type == "fastapi", (
        f"expected 'fastapi' for Python workspace with embedded SPA, "
        f"got {m.project_type!r}"
    )


def test_python_workspace_without_framework_marker_returns_python(tmp_path):
    """Same shape as above but pyproject has no framework dep — should
    still return `python` (not the nested SPA's type)."""
    _write(tmp_path / "pyproject.toml", '[project]\nname = "x"\n')
    _write(
        tmp_path / "apps" / "ui" / "package.json",
        json.dumps({"dependencies": {"next": "^14"}}),
    )

    assert analyze_project(tmp_path).project_type == "python"


# -------------------- edge: both root pyproject AND root package.json -------------------- #


def test_root_pyproject_and_root_package_json_treats_as_python(tmp_path):
    """Rare but possible: Python project that uses npm for build tools
    (e.g. tailwindcss CLI). Should still classify as Python — the
    pyproject signal is more definitive of the project's primary nature.
    """
    _write(
        tmp_path / "pyproject.toml",
        '[project]\nname = "x"\ndependencies = ["fastapi"]\n',
    )
    _write(
        tmp_path / "package.json",
        json.dumps({"devDependencies": {"tailwindcss": "^3"}}),
    )

    assert analyze_project(tmp_path).project_type == "fastapi"


def test_root_package_json_without_pyproject_still_detects_js(tmp_path):
    """No regression on the standard JS case — root package.json alone
    should classify correctly regardless of subdir contents."""
    _write(
        tmp_path / "package.json",
        json.dumps({"dependencies": {"vite": "^5"}}),
    )
    # Add a nested subdir with NO files just to confirm subdir presence
    # doesn't alter the decision
    (tmp_path / "src").mkdir()

    assert analyze_project(tmp_path).project_type == "vite"
