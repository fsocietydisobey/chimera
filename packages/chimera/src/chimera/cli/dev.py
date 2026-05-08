"""`chimera dev` — single-command stack startup.

Pillar 2 of the chimera vision. One command spins up:
  - the project's dev server (auto-detected: vite/next/uvicorn/django)
  - Chrome with --remote-debugging-port so Specter can attach
  - chimera-monitor (if not already running) for LangGraph observability

Single Ctrl-C tears it all down — every spawned process is in chimera's
process registry, so cleanup is deterministic. No orphaned dev servers
hanging around after a session ends.

The user lifecycle:
  1. `chimera dev .` (or no arg = $PWD)
  2. chimera prints what it detected + spawned, with URLs
  3. user works in their AI CLI shell — Specter sees the browser,
     chimera-monitor sees any LangGraph runs the dev server triggers
  4. Ctrl-C → graceful tear-down

Without `chimera dev`, the same setup takes 4-5 manual commands and
fails to clean up reliably. This is the demoable wow-moment of the vision.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import signal
import sys
import time

from chimera.log import get_logger
from chimera.monitor import processes
from chimera.runtime import browser, dev_server

log = get_logger("cli.dev")


def add_subparser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "dev",
        help="Spin up the project's dev stack (server + browser + monitor).",
        description=(
            "One command starts your dev server, opens Chrome with "
            "--remote-debugging-port for Specter, and ensures chimera-monitor "
            "is running. Ctrl-C tears it all down cleanly."
        ),
    )
    p.add_argument(
        "project_path", nargs="?", default=".",
        help="Project directory (default: current dir).",
    )
    p.add_argument(
        "--no-browser", action="store_true",
        help="Skip Chrome auto-launch — useful in CI or remote dev.",
    )
    p.add_argument(
        "--browser-port", type=int, default=browser.DEFAULT_PORT,
        help=f"Chrome --remote-debugging-port (default {browser.DEFAULT_PORT}).",
    )
    p.add_argument(
        "--command", default=None,
        help="Override dev-server command (space-separated). Skips auto-detect.",
    )
    p.add_argument(
        "--label", default="dev-server",
        help="Process registry label for the dev server (default 'dev-server').",
    )
    p.add_argument(
        "--no-monitor", action="store_true",
        help="Skip starting chimera-monitor (assume it's already running or unwanted).",
    )
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    return asyncio.run(_run_async(args))


async def _run_async(args: argparse.Namespace) -> int:
    project = os.path.abspath(args.project_path)

    # 1) Pick the dev server command
    if args.command:
        cmd_parts = args.command.split()
        chosen = dev_server.DevCommand(
            cmd=cmd_parts,
            label=args.command,
            working_dir=project,
            framework="custom",
        )
    else:
        candidates = dev_server.detect(project)
        if not candidates:
            print(
                f"\n[chimera dev] no dev server detected in {project}.\n"
                "Pass --command to specify explicitly, e.g.\n"
                "  chimera dev . --command 'pnpm dev'",
                file=sys.stderr,
            )
            return 2
        chosen = candidates[0]
        if len(candidates) > 1:
            print(f"[chimera dev] detected {len(candidates)} candidates; using: {chosen.label}", file=sys.stderr)

    print(
        f"\n[chimera dev] project: {project}\n"
        f"[chimera dev] dev server: {chosen.label} ({chosen.framework})",
        file=sys.stderr,
    )

    # 2) Ensure monitor is running (unless --no-monitor)
    if not args.no_monitor:
        await _ensure_monitor()

    # 3) Spawn the dev server (tracked in process registry — cleanup on Ctrl-C)
    try:
        handle = await processes.spawn(
            chosen.cmd,
            label=args.label,
            cwd=chosen.working_dir,
            replace_existing=True,
        )
    except FileNotFoundError as e:
        print(f"\n[chimera dev] failed to launch dev server: {e}", file=sys.stderr)
        return 3

    print(
        f"[chimera dev] dev server pid={handle.pid} (label='{args.label}')",
        file=sys.stderr,
    )

    # 4) Wait for the dev server to print its URL — pattern from the framework heuristic
    print(f"[chimera dev] waiting for dev server to be ready...", file=sys.stderr)
    wait_result = await processes.wait_for_process(
        args.label,
        completion_signal=chosen.expected_url_pattern,
        timeout_s=60.0,
    )

    if wait_result["reason"] == "exit":
        print(
            f"\n[chimera dev] dev server exited unexpectedly (code={wait_result['exit_code']}). "
            f"Last output:\n{wait_result['stdout_text'][-1000:]}\n{wait_result['stderr_text'][-500:]}",
            file=sys.stderr,
        )
        return 4
    if wait_result["reason"] == "timeout":
        print(
            f"\n[chimera dev] dev server didn't emit a recognizable URL within 60s. "
            f"It may still be coming up — open Chrome manually if needed.\n"
            f"Recent output:\n{wait_result['stdout_text'][-500:]}",
            file=sys.stderr,
        )
    else:
        # Try to extract the actual URL from the matched substring
        m = re.search(r"https?://\S+", wait_result.get("matched", "")) \
            or re.search(r"https?://\S+", wait_result["stdout_text"])
        url = m.group(0).rstrip(".,;)") if m else None
        if url:
            print(f"[chimera dev] dev server ready: {url}", file=sys.stderr)
        else:
            url = None
            print(f"[chimera dev] dev server signaled ready but URL parse failed.", file=sys.stderr)

    # 5) Launch Chrome with --remote-debugging-port (if installed + not --no-browser)
    chrome_url = locals().get("url")
    if not args.no_browser:
        chrome_cmd = browser.build_launch_cmd(
            url=chrome_url,
            port=browser.free_port(args.browser_port),
        )
        if chrome_cmd:
            try:
                chrome_handle = await processes.spawn(
                    chrome_cmd,
                    label="dev-browser",
                    replace_existing=True,
                )
                print(
                    f"[chimera dev] chrome pid={chrome_handle.pid} "
                    f"(remote-debugging-port={args.browser_port}). "
                    f"Specter can attach.",
                    file=sys.stderr,
                )
            except Exception as e:
                print(f"[chimera dev] chrome launch failed: {e}", file=sys.stderr)
        else:
            print(f"[chimera dev] {browser.installation_hint()}", file=sys.stderr)

    # 6) Wait for SIGINT — clean up everything we spawned on Ctrl-C
    print(
        f"\n[chimera dev] running. Ctrl-C to stop.\n"
        f"  dashboard:  http://127.0.0.1:8740\n",
        file=sys.stderr,
    )

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _signal_handler():
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    try:
        await stop.wait()
    finally:
        print("\n[chimera dev] tearing down...", file=sys.stderr)
        # Kill in reverse spawn order
        for label in ("dev-browser", args.label):
            try:
                stopped = await processes.kill(label)
                if stopped:
                    print(f"  ✓ stopped {label}", file=sys.stderr)
            except processes.ProcessNotFound:
                pass
        print("[chimera dev] done.", file=sys.stderr)

    return 0


async def _ensure_monitor() -> None:
    """If chimera-monitor isn't already running, start it as a background daemon."""
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen("http://127.0.0.1:8740/api/projects", timeout=1.0) as _:
            print("[chimera dev] monitor: already running", file=sys.stderr)
            return
    except urllib.error.URLError:
        pass

    print("[chimera dev] monitor: starting daemon in background...", file=sys.stderr)
    # Use the existing monitor CLI's start command — handles daemonization
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "chimera", "monitor", "start", "--no-browser",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.wait()
    # Give it a moment to bind the port
    for _ in range(20):
        try:
            with urllib.request.urlopen("http://127.0.0.1:8740/api/projects", timeout=0.5) as _:
                print("[chimera dev] monitor: ready", file=sys.stderr)
                return
        except urllib.error.URLError:
            await asyncio.sleep(0.5)
    print("[chimera dev] monitor: did not come up within 10s — continuing anyway", file=sys.stderr)
