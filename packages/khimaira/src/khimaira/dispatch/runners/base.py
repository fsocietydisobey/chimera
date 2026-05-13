"""CLIRunner protocol + subprocess primitives.

The pure-CLI substrate. Every LLM call khimaira makes goes through one of
these. No API SDK calls anywhere else in the tree — this is what makes the
"no API keys, no surprise bills" pitch true.

The base file owns:
  - `CLIRunner` Protocol — the shape every runner exposes
  - `cli_available()` — installation probe
  - `run_subprocess()` — async subprocess wrapper, thread-pooled so it doesn't
    block the event loop
  - `kill_all_subprocesses()` — graceful shutdown hook
"""

from __future__ import annotations

import asyncio
import os
import shutil
import signal
import subprocess
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol

from khimaira.log import get_logger

log = get_logger("dispatch.runners")

# Track all running subprocesses so server shutdown can SIGKILL them. Without
# this, an uvicorn reload while a 10-min Claude CLI is mid-flight strands the
# subprocess, holds the prompt-cache, and burns through subscription quota.
_active_pids: set[int] = set()

# Default per-call timeout. Individual runners may override.
DEFAULT_TIMEOUT_S = 600

# Reject command lines bigger than this. Prevents accidental dumping of an
# entire codebase as one CLI argument (which would either OOM or hit shell
# arg-length limits silently).
MAX_COMMAND_BYTES = 500_000


@dataclass(frozen=True)
class RunnerResult:
    """The return shape of every CLIRunner.run() call.

    Tracking input/output token counts here lets the usage tracker bill
    accurately even when the underlying CLI output isn't structured JSON.
    Runners that can't read token counts populate 0 — the dashboard
    surfaces "0-token records" as a hint that the runner needs better
    parsing.
    """

    text: str
    runner: str           # which runner produced this — "claude", "ollama", etc.
    model: str            # the specific model used
    input_tokens: int = 0
    output_tokens: int = 0
    latency_s: float = 0.0
    session_id: str | None = None
    raw: str = ""         # raw stdout (when runner emits structured JSON, this is the unparsed payload)


@dataclass(frozen=True)
class StreamChunk:
    """One chunk emitted by `CLIRunner.stream()`.

    A stream is a sequence of these. Most chunks are incremental text
    deltas (`text` field, `is_final=False`); the LAST chunk has
    `is_final=True` and may carry the final `usage` / `model` /
    `session_id` metadata that's only known at the end.

    Runners that can't actually stream (no `--output-format stream-json`
    equivalent) implement `stream()` as a degenerate one-chunk iterator
    that yields a single final chunk — same data shape as `run()`'s
    return value. Callers code against `stream()` and don't need to
    distinguish runners that support real streaming from those that don't.
    """

    text: str = ""                  # incremental text delta (or full text on final-only)
    is_final: bool = False           # True on the LAST chunk
    model: str = ""                  # populated on final chunk
    input_tokens: int = 0            # populated on final chunk
    output_tokens: int = 0           # populated on final chunk
    session_id: str | None = None    # populated on final chunk
    raw: str = ""                    # raw stream line (final chunk: full raw response)


class CLIRunner(Protocol):
    """Every concrete runner implements this interface."""

    name: str
    """Stable identifier — 'claude', 'codex', 'gemini', 'ollama', 'llm'."""

    def is_available(self) -> bool:
        """True iff the underlying CLI binary exists and is executable."""
        ...

    async def run(
        self,
        prompt: str,
        *,
        model: str | None = None,
        timeout: int | None = None,
        cwd: str | None = None,
        session_id: str | None = None,
        **kwargs: object,
    ) -> RunnerResult:
        """Execute the prompt and return text + accounting.

        Concrete runners accept additional kwargs they understand (effort,
        thinking_budget, permission_mode, etc.). Unknown kwargs MUST be
        ignored, never raise — this lets the AMR pass a generic kwarg
        bundle without each runner having to filter.
        """
        ...

    def stream(
        self,
        prompt: str,
        *,
        model: str | None = None,
        timeout: int | None = None,
        cwd: str | None = None,
        session_id: str | None = None,
        **kwargs: object,
    ) -> "AsyncIterator[StreamChunk]":
        """Execute the prompt as a stream of `StreamChunk`s.

        Yields one or more chunks. The LAST chunk has `is_final=True`
        and carries the final usage / model / session_id metadata.

        Runners that wrap a CLI without native streaming implement this
        as a degenerate one-chunk iterator (see `default_stream_via_run`
        below) — the protocol is uniform; the user-visible difference is
        just latency-to-first-chunk.
        """
        ...


async def default_stream_via_run(
    runner: CLIRunner,
    prompt: str,
    **kwargs: object,
) -> "AsyncIterator[StreamChunk]":
    """Default `stream()` implementation for runners without a native
    streaming mode. Calls the runner's `run()` and yields one final
    chunk with the full text + metadata.

    Usage from a concrete runner:

        async def stream(self, prompt, **kwargs):
            async for chunk in default_stream_via_run(self, prompt, **kwargs):
                yield chunk
    """
    result = await runner.run(prompt, **kwargs)  # type: ignore[arg-type]
    yield StreamChunk(
        text=result.text,
        is_final=True,
        model=result.model,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        session_id=result.session_id,
        raw=result.raw,
    )


def cli_available(cmd: str) -> bool:
    """Check if a CLI tool exists (absolute path or on PATH)."""
    if not cmd:
        return False
    if os.path.isabs(cmd):
        return os.path.isfile(cmd) and os.access(cmd, os.X_OK)
    return shutil.which(cmd) is not None


def kill_all_subprocesses() -> None:
    """Kill all tracked subprocesses. Called on server shutdown."""
    for pid in list(_active_pids):
        try:
            os.kill(pid, signal.SIGKILL)
            log.info("killed subprocess pid=%d on shutdown", pid)
        except ProcessLookupError:
            pass
    _active_pids.clear()


# Auth env vars khimaira scrubs from spawned CLI subprocesses by default.
# Why: when the user has ANTHROPIC_API_KEY (or equivalent) set in their
# shell AND also has Claude Code / Codex / Gemini OAuth subscriptions,
# the spawned `claude -p ...` subprocess inherits the env and prefers the
# API key — billing API instead of subscription. This breaks khimaira's
# core "no API spend" pitch silently.
#
# Scrubbing these env vars forces the spawned CLI to fall back to its
# OAuth subscription auth. Users who EXPLICITLY want API billing can set
# KHIMAIRA_USE_API=true to disable scrubbing.
_API_KEY_ENV_VARS = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "OPENAI_API_KEY",
    "GOOGLE_AI_API_KEY",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
)


def _build_subprocess_env() -> dict[str, str]:
    """Return os.environ minus API auth vars (unless user opted in via
    KHIMAIRA_USE_API=true). Subprocesses inherit this so spawned CLIs use
    their OAuth subscription auth, not API keys."""
    env = dict(os.environ)
    if env.get("KHIMAIRA_USE_API", "").lower() in ("1", "true", "yes"):
        return env
    for var in _API_KEY_ENV_VARS:
        env.pop(var, None)
    return env


def _run_subprocess_sync(
    cmd: list[str],
    timeout: int,
    cwd: str | None,
    stdin: str | None = None,
) -> tuple[bytes, bytes, int]:
    """Spawn a subprocess in the calling thread. Returns (stdout, stderr, code).

    Env: API auth keys are scrubbed by default (see _build_subprocess_env)
    so spawned CLIs use OAuth subscription, not API. Override with
    KHIMAIRA_USE_API=true.
    """
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        env=_build_subprocess_env(),
        stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    _active_pids.add(proc.pid)
    try:
        stdin_bytes = stdin.encode("utf-8") if stdin is not None else None
        stdout, stderr = proc.communicate(input=stdin_bytes, timeout=timeout)
        return stdout, stderr, proc.returncode
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        raise TimeoutError(
            f"CLI command timed out after {timeout}s: {' '.join(cmd[:2])}..."
        ) from None
    finally:
        _active_pids.discard(proc.pid)


async def run_subprocess(
    cmd: list[str],
    *,
    timeout: int | None = None,
    cwd: str | None = None,
    label: str = "",
    stdin: str | None = None,
    raise_on_nonzero: bool = False,
) -> str:
    """Async wrapper around _run_subprocess_sync — runs in a thread.

    Critical: keeps the event loop responsive. Other MCP requests
    (status pings, monitor polls) keep working while a 10-min Claude
    CLI is in flight.

    Default behavior: returns stdout regardless of exit code. CLIs like
    Claude Code, Codex, and Gemini emit structured JSON containing useful
    error info (e.g. 'credit balance too low', 'rate limited') even when
    they exit non-zero. The runner that owns the protocol understanding
    parses this JSON and decides what counts as a real failure — losing
    that information at this layer was the cause of an early bug where
    khimaira retried 3× through `run_structured` on credit-low errors,
    burning quota the user explicitly didn't want burned.

    Pass `raise_on_nonzero=True` to get the legacy behavior (raise on rc != 0).
    Use that for runners whose CLIs follow strict POSIX semantics (Ollama,
    most plain shell tools).
    """
    if timeout is None:
        timeout = DEFAULT_TIMEOUT_S

    total_len = sum(len(arg) for arg in cmd)
    if total_len > MAX_COMMAND_BYTES:
        raise ValueError(f"Command too large ({total_len} chars). Max {MAX_COMMAND_BYTES}.")

    short = " ".join(cmd[:3])
    log.info("running: %s (cwd=%s, timeout=%ds)", short, cwd or "<inherit>", timeout)
    t0 = time.monotonic()

    try:
        stdout, stderr, rc = await asyncio.to_thread(
            _run_subprocess_sync, cmd, timeout, cwd, stdin
        )
    except TimeoutError:
        log.error("timeout after %.1fs: %s", time.monotonic() - t0, short)
        raise

    elapsed = time.monotonic() - t0
    out = stdout.decode("utf-8", errors="replace")

    if rc != 0:
        err = stderr.decode("utf-8", errors="replace").strip()
        if raise_on_nonzero:
            log.error("failed (code=%d, %.1fs): %s — %s", rc, elapsed, short, err[:200])
            raise RuntimeError(f"CLI exited with code {rc}: {err or '(no stderr)'}")
        # Permissive path — return stdout, log the warning
        log.warning(
            "non-zero exit (code=%d, %.1fs) but returning stdout: %s — stderr: %s",
            rc, elapsed, short, err[:200] or "(empty)",
        )
        return out

    log.info("completed in %.1fs (%d chars): %s", elapsed, len(out), label or short)
    return out
