"""Tests for the GitHub Issues task source adapter.

Strategy: monkey-patch asyncio.create_subprocess_exec to return canned
gh-shaped output. Don't shell out to real gh — that would require a
networked test environment + gh auth.

Covers:
  - gh not on PATH → silent skip
  - gh exits non-zero (auth error) → silent skip with warning
  - gh times out → silent skip
  - gh returns malformed JSON → silent skip
  - gh returns valid issue list → parse correctly into Task objects
  - empty issue list → return []
  - issue with missing repository field still parses
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from khimaira.task_sources import Task
from khimaira.task_sources.github import GithubTaskSource


@pytest.fixture
def source():
    return GithubTaskSource()


def _fake_proc(stdout: bytes, stderr: bytes = b"", returncode: int = 0):
    """Build a mock asyncio subprocess object with the given output."""

    class _MockProc:
        def __init__(self):
            self.returncode = returncode

        async def communicate(self):
            return stdout, stderr

        def kill(self):
            pass

    return _MockProc()


async def test_hook_safe(source):
    assert source.hook_safe() is True


async def test_gh_not_on_path_returns_empty(source):
    with patch("khimaira.task_sources.github.shutil.which", return_value=None):
        tasks = await source.fetch_open_tasks()
    assert tasks == []


async def test_nonzero_exit_returns_empty(source):
    """gh returning exit 1 (typical for unauthed users) → silent skip."""
    with (
        patch("khimaira.task_sources.github.shutil.which", return_value="/usr/bin/gh"),
        patch(
            "khimaira.task_sources.github.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=_fake_proc(b"", b"not authed", returncode=1)),
        ),
    ):
        tasks = await source.fetch_open_tasks()
    assert tasks == []


async def test_timeout_returns_empty(source):
    """If gh hangs past timeout_s, kill the process and return []."""

    class _HangingProc:
        returncode = None

        async def communicate(self):
            await asyncio.sleep(10)  # would block past the test timeout

        def kill(self):
            self.returncode = -9

    proc = _HangingProc()

    src = GithubTaskSource(timeout_s=0.05)
    with (
        patch("khimaira.task_sources.github.shutil.which", return_value="/usr/bin/gh"),
        patch(
            "khimaira.task_sources.github.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        ),
    ):
        tasks = await src.fetch_open_tasks()
    assert tasks == []


async def test_malformed_json_returns_empty(source):
    """gh returning non-JSON (shouldn't happen but be defensive) → skip."""
    with (
        patch("khimaira.task_sources.github.shutil.which", return_value="/usr/bin/gh"),
        patch(
            "khimaira.task_sources.github.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=_fake_proc(b"not-json-{", returncode=0)),
        ),
    ):
        tasks = await source.fetch_open_tasks()
    assert tasks == []


async def test_empty_issue_list(source):
    """gh returns `[]` when the user has no assigned issues → return []."""
    with (
        patch("khimaira.task_sources.github.shutil.which", return_value="/usr/bin/gh"),
        patch(
            "khimaira.task_sources.github.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=_fake_proc(b"[]", returncode=0)),
        ),
    ):
        tasks = await source.fetch_open_tasks()
    assert tasks == []


async def test_parses_real_gh_output_shape(source):
    """Real gh `issue list --json number,title,state,url,repository` output
    shape. Verify each field maps to the Task dataclass correctly."""
    canned = json.dumps(
        [
            {
                "number": 12,
                "title": "Add per-project budget for mcp__khimaira__auto",
                "state": "OPEN",
                "url": "https://github.com/fsocietydisobey/khimaira/issues/12",
                "repository": {"name": "khimaira"},
            },
            {
                "number": 7,
                "title": "Wire pricing into checkout flow",
                "state": "OPEN",
                "url": "https://github.com/example/llama/issues/7",
                "repository": {"name": "llama"},
            },
        ]
    ).encode("utf-8")

    with (
        patch("khimaira.task_sources.github.shutil.which", return_value="/usr/bin/gh"),
        patch(
            "khimaira.task_sources.github.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=_fake_proc(canned, returncode=0)),
        ),
    ):
        tasks = await source.fetch_open_tasks()

    assert len(tasks) == 2
    t = tasks[0]
    assert t.id == "khimaira#12"
    assert t.title.startswith("Add per-project budget")
    assert t.state == "open"
    assert t.source == "github"
    assert t.project == "khimaira"
    assert t.url == "https://github.com/fsocietydisobey/khimaira/issues/12"

    t = tasks[1]
    assert t.id == "llama#7"
    assert t.project == "llama"


async def test_issue_without_repository_still_parses(source):
    """Defensive — if repository field is missing or non-dict, fall back
    to an id without the repo prefix."""
    canned = json.dumps(
        [{"number": 99, "title": "edge case", "state": "OPEN", "url": ""}]
    ).encode("utf-8")

    with (
        patch("khimaira.task_sources.github.shutil.which", return_value="/usr/bin/gh"),
        patch(
            "khimaira.task_sources.github.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=_fake_proc(canned, returncode=0)),
        ),
    ):
        tasks = await source.fetch_open_tasks()

    assert len(tasks) == 1
    assert tasks[0].id == "#99"
    assert tasks[0].project == ""


async def test_config_recognizes_github_kind(tmp_path, monkeypatch):
    """Verify config loader builds a GithubTaskSource from `kind: github`."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    cfg = tmp_path / "khimaira" / "task_sources.yaml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(
        "sources:\n"
        "  - kind: github\n"
        "    limit: 50\n"
    )

    import importlib

    from khimaira.task_sources import config as config_mod
    importlib.reload(config_mod)
    sources = config_mod.load_configured_sources()
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    importlib.reload(config_mod)

    assert len(sources) == 1
    assert sources[0].name == "github"
    assert isinstance(sources[0], GithubTaskSource)
    assert sources[0].limit == 50
