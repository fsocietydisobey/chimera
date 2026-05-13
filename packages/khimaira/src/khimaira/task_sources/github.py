"""GitHub Issues task source — shells out to the `gh` CLI.

Why `gh` instead of GitHub MCP or the REST API:
  - hook-safe (no MCP client required, no API key required when the
    user is already `gh auth login`'d)
  - cross-org and cross-repo by default (`gh issue list --assignee @me`
    spans all repos the user has access to)
  - already widely installed; one binary, no extra deps in khimaira

This adapter is HOOK-SAFE — runs as a subprocess from the SessionStart
hook with no MCP / network dependency beyond the local `gh` binary.

Setup the user needs:
  1. Install gh: https://cli.github.com/
  2. `gh auth login` once
  3. Add to `~/.khimaira/task_sources.yaml`:
        sources:
          - kind: github
            enabled: true

Then a fresh Claude Code SessionStart surfaces open GitHub issues
assigned to the user, alongside JSONL todos and khimaira handoffs.

Failure modes (all handled cleanly — never break SessionStart):
  - gh not installed → return []
  - gh installed but not authed → return [], log warning
  - gh rate-limited / network down → return [], log warning
  - Unexpected gh output → log + return whatever parsed cleanly
"""

from __future__ import annotations

import asyncio
import json
import shutil
from dataclasses import dataclass

from khimaira.log import get_logger

from . import Task

log = get_logger("task_sources.github")


# Truncate response cap — gh issue list defaults to 30; let's match.
_DEFAULT_LIMIT = 30


@dataclass
class GithubTaskSource:
    """Adapter that lists issues assigned to the authenticated `gh` user.

    Args:
        limit: max issues to fetch (default 30).
        cmd: override the `gh` binary path (test injection / non-standard
            install).
        timeout_s: subprocess timeout (default 10s — gh is fast when it
            works; if it's hanging, surface that as "no tasks" rather
            than block SessionStart).
    """

    name: str = "github"
    limit: int = _DEFAULT_LIMIT
    cmd: str = "gh"
    timeout_s: float = 10.0

    def hook_safe(self) -> bool:
        return True

    async def fetch_open_tasks(self) -> list[Task]:
        if shutil.which(self.cmd) is None:
            # `gh` not installed — silent skip. The user added this
            # adapter to task_sources.yaml on purpose, so they probably
            # WILL want to install it; logging a warning helps them
            # discover the gap.
            log.warning(
                "github adapter: %r not on PATH — install gh from https://cli.github.com/",
                self.cmd,
            )
            return []

        # `gh issue list --assignee @me --state open --json ...` returns
        # a JSON array of issues across every repo the user has access
        # to. Limit is required (otherwise gh defaults to 30 silently;
        # we make it explicit so changes here are intentional).
        args = [
            self.cmd,
            "issue",
            "list",
            "--assignee",
            "@me",
            "--state",
            "open",
            "--limit",
            str(self.limit),
            "--json",
            "number,title,state,url,repository",
        ]

        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=self.timeout_s
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                log.warning(
                    "github adapter: `gh issue list` timed out after %.1fs",
                    self.timeout_s,
                )
                return []
        except OSError as exc:
            log.warning("github adapter: subprocess failed: %s", exc)
            return []

        if proc.returncode != 0:
            # Most common: not authed (`gh auth login` required).
            stderr_text = stderr.decode("utf-8", errors="replace").strip()
            log.warning(
                "github adapter: gh returned exit %d: %s",
                proc.returncode,
                stderr_text[:200],
            )
            return []

        try:
            issues = json.loads(stdout.decode("utf-8", errors="replace"))
        except json.JSONDecodeError as exc:
            log.warning("github adapter: gh stdout not JSON: %s", exc)
            return []
        if not isinstance(issues, list):
            return []

        out: list[Task] = []
        for raw in issues:
            if not isinstance(raw, dict):
                continue
            number = raw.get("number")
            title = raw.get("title", "")
            if number is None and not title:
                continue
            # gh's `repository` field is a dict with name/owner/etc
            repo_info = raw.get("repository") or {}
            repo_name = ""
            if isinstance(repo_info, dict):
                repo_name = str(repo_info.get("name", "") or "")
            task_id = f"{repo_name}#{number}" if repo_name else f"#{number}"
            out.append(
                Task(
                    id=task_id,
                    title=str(title),
                    state=str(raw.get("state", "") or "").lower(),
                    source=self.name,
                    project=repo_name,
                    url=str(raw.get("url", "") or ""),
                )
            )
        return out
