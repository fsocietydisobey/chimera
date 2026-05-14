"""LangGraph pipeline tracer — local alternative to LangSmith.

Drop-in callback handler that traces nodes, tools, LLM calls, retries, and errors.
No manual log calls needed anywhere. Configure once, reuse across projects.

Usage:
    uv run meeting-scribe record -p                    # INFO: nodes, tools, timing
    LOG_LEVEL=DEBUG uv run meeting-scribe record -p    # DEBUG: + inputs, outputs, tokens

Reuse in any LangGraph project:
    from your_project.log import get_tracer
    result = await app.ainvoke(inputs, config={"callbacks": [get_tracer()]})
"""

import logging
import os
import sys
import time
from typing import Any

from langchain_core.callbacks import AsyncCallbackHandler

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
IS_DEBUG = LOG_LEVEL == "DEBUG"

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s [%(name)s] %(levelname)s — %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stderr,
    force=True,
)

for _name in (
    "httpx",
    "httpcore",
    "urllib3",
    "google.auth",
    "websockets",
    "langsmith.client",
    "asyncio",
):
    logging.getLogger(_name).setLevel(logging.WARNING)

# ---------------------------------------------------------------------------
# ANSI colors
# ---------------------------------------------------------------------------

_GREY = "\033[90m"
_GREEN = "\033[32m"
_RED = "\033[31m"
_YELLOW = "\033[33m"
_CYAN = "\033[36m"
_BLUE = "\033[34m"
_MAGENTA = "\033[35m"
_BOLD = "\033[1m"
_RESET = "\033[0m"
_DIM = "\033[2m"

_PIPE = f"{_GREY}│{_RESET}"


def _time_color(seconds: float) -> str:
    if seconds < 2:
        return _GREEN
    if seconds < 10:
        return _YELLOW
    return _RED


def _truncate(value: Any, max_len: int = 150) -> str:
    s = str(value)
    return f"{s[:max_len]}..." if len(s) > max_len else s


def _kv_lines(data: dict, prefix: str) -> list[str]:
    """Format dict as indented key: value lines."""
    lines = []
    for k, v in data.items():
        lines.append(f"{prefix}{_DIM}{k}{_RESET}: {_truncate(v)}")
    return lines


# ---------------------------------------------------------------------------
# Tracer
# ---------------------------------------------------------------------------


class PipelineTracer(AsyncCallbackHandler):
    """Full-featured pipeline tracer that prints to stderr.

    Traces:
      - Graph start/end with total timing
      - Node start/end with input keys, output keys, timing, parallel detection
      - Tool calls with name, input, output
      - LLM calls with model name, token streaming, response preview
      - Retries with attempt count
      - Errors at every level

    INFO level:
        ┌ LangGraph
        │
        │  ├── transcribe  ← audio_path
        │  │   0.5s  → transcript
        │
        │  ├── summarize  ← audio_path, transcript
        │  ├── extract_actions  (parallel)  ← audio_path, transcript
        │  ├── detect_emotions  (parallel)  ← audio_path, transcript
        │  │   0.2s  → action_items, decisions, participants
        │  │   0.3s  → summary
        │  │   0.4s  → speaker_emotions, meeting_mood
        │
        └ Done (1.2s)

    DEBUG adds input/output values, LLM model info, tool args, and final state.
    """

    def __init__(self) -> None:
        self._timers: dict[str, float] = {}
        self._names: dict[str, str] = {}
        self._types: dict[str, str] = {}  # "graph" | "node" | "tool" | "llm"
        self._parent: str | None = None
        self._active_parallel: set[str] = set()
        self._token_count: dict[str, int] = {}

    def _out(self, msg: str) -> None:
        print(msg, file=sys.stderr, flush=True)

    def _register(self, run_id: Any, name: str, kind: str) -> str:
        rid = str(run_id)
        self._timers[rid] = time.monotonic()
        self._names[rid] = name
        self._types[rid] = kind
        return rid

    def _resolve(self, run_id: Any, **kwargs: Any) -> tuple[str, str, str, float]:
        rid = str(run_id)
        name = self._names.pop(rid, kwargs.get("name", "?"))
        kind = self._types.pop(rid, "?")
        elapsed = time.monotonic() - self._timers.pop(rid, time.monotonic())
        return rid, name, kind, elapsed

    # -------------------------------------------------------------------
    # Graph / Node (chain) events
    # -------------------------------------------------------------------

    async def on_chain_start(
        self, serialized: dict | None, inputs: dict, *, run_id: Any, **kwargs: Any
    ) -> None:
        name = kwargs.get("name") or (serialized or {}).get("id", ["?"])[-1]
        if name.startswith("__"):
            return

        rid = self._register(run_id, name, "graph" if self._parent is None else "node")

        # Top-level graph
        if self._parent is None:
            self._parent = rid
            input_keys = ", ".join(inputs.keys()) if isinstance(inputs, dict) else "?"
            self._out(
                f"\n{_BOLD}{_CYAN}┌ {name}{_RESET}  {_DIM}input: {input_keys}{_RESET}"
            )
            self._out(_PIPE)
            if IS_DEBUG and isinstance(inputs, dict):
                for line in _kv_lines(inputs, f"{_PIPE}  {_DIM}"):
                    self._out(line)
                self._out(_PIPE)
            return

        # Node
        self._active_parallel.add(rid)
        parallel_tag = (
            f"  {_DIM}(parallel){_RESET}" if len(self._active_parallel) > 1 else ""
        )
        input_keys = ", ".join(inputs.keys()) if isinstance(inputs, dict) else "?"

        self._out(
            f"{_PIPE}  {_BOLD}├── {name}{_RESET}{parallel_tag}  {_DIM}← {input_keys}{_RESET}"
        )

        if IS_DEBUG and isinstance(inputs, dict):
            for line in _kv_lines(inputs, f"{_PIPE}  {_GREY}│{_RESET}   {_DIM}in."):
                self._out(line)

    async def on_chain_end(self, outputs: dict, *, run_id: Any, **kwargs: Any) -> None:
        rid, name, kind, elapsed = self._resolve(run_id, **kwargs)
        if name.startswith("__"):
            return

        self._active_parallel.discard(rid)
        tc = _time_color(elapsed)

        # Top-level graph done
        if rid == self._parent:
            self._out(_PIPE)
            self._out(f"{_BOLD}{_GREEN}└ Done{_RESET}  {tc}{elapsed:.1f}s{_RESET}")

            if IS_DEBUG and isinstance(outputs, dict):
                self._out(f"\n{_BOLD}Final state:{_RESET}")
                for line in _kv_lines(outputs, "  "):
                    self._out(line)

            self._out("")
            self._parent = None
            return

        # Node done
        output_keys = ", ".join(outputs.keys()) if isinstance(outputs, dict) else "?"
        self._out(
            f"{_PIPE}  {_GREY}│{_RESET}   {tc}{elapsed:.1f}s{_RESET}  {_DIM}→ {output_keys}{_RESET}"
        )

        if IS_DEBUG and isinstance(outputs, dict):
            for line in _kv_lines(outputs, f"{_PIPE}  {_GREY}│{_RESET}   {_DIM}out."):
                self._out(line)

        self._out(_PIPE)

    async def on_chain_error(
        self, error: BaseException, *, run_id: Any, **kwargs: Any
    ) -> None:
        rid, name, kind, elapsed = self._resolve(run_id, **kwargs)
        self._active_parallel.discard(rid)

        self._out(
            f"{_PIPE}  {_RED}{_BOLD}✗ {name}{_RESET}  {_RED}({elapsed:.1f}s){_RESET}"
        )
        self._out(
            f"{_PIPE}    {_RED}{type(error).__name__}: {_truncate(error, 200)}{_RESET}"
        )
        self._out(_PIPE)

    # -------------------------------------------------------------------
    # Tool events
    # -------------------------------------------------------------------

    async def on_tool_start(
        self, serialized: dict | None, input_str: str, *, run_id: Any, **kwargs: Any
    ) -> None:
        name = kwargs.get("name") or (serialized or {}).get("name", "tool")
        self._register(run_id, name, "tool")

        self._out(
            f"{_PIPE}  {_GREY}│{_RESET}   {_BLUE}⚡ {name}{_RESET}  {_DIM}called{_RESET}"
        )

        if IS_DEBUG:
            inputs = kwargs.get("inputs", input_str)
            self._out(
                f"{_PIPE}  {_GREY}│{_RESET}     {_DIM}args: {_truncate(inputs)}{_RESET}"
            )

    async def on_tool_end(self, output: Any, *, run_id: Any, **kwargs: Any) -> None:
        rid, name, kind, elapsed = self._resolve(run_id, **kwargs)
        tc = _time_color(elapsed)

        self._out(
            f"{_PIPE}  {_GREY}│{_RESET}     "
            f"{_BLUE}↳{_RESET} {tc}{elapsed:.1f}s{_RESET}  {_DIM}{_truncate(output, 100)}{_RESET}"
        )

    async def on_tool_error(
        self, error: BaseException, *, run_id: Any, **kwargs: Any
    ) -> None:
        rid, name, kind, elapsed = self._resolve(run_id, **kwargs)

        self._out(
            f"{_PIPE}  {_GREY}│{_RESET}     "
            f"{_RED}✗ {name} ({elapsed:.1f}s) {type(error).__name__}: {_truncate(error, 150)}{_RESET}"
        )

    # -------------------------------------------------------------------
    # LLM events
    # -------------------------------------------------------------------

    async def on_llm_start(
        self, serialized: dict | None, prompts: list[str], *, run_id: Any, **kwargs: Any
    ) -> None:
        model = (serialized or {}).get("id", ["?"])[-1]
        invocation = kwargs.get("invocation_params", {})
        model_name = invocation.get("model", invocation.get("model_name", model))
        self._register(run_id, str(model_name), "llm")
        self._token_count[str(run_id)] = 0

        self._out(
            f"{_PIPE}  {_GREY}│{_RESET}   {_MAGENTA}🔮 LLM{_RESET}  {_DIM}{model_name}{_RESET}"
        )

        if IS_DEBUG and prompts:
            preview = _truncate(prompts[0], 200)
            self._out(f"{_PIPE}  {_GREY}│{_RESET}     {_DIM}prompt: {preview}{_RESET}")

    async def on_chat_model_start(
        self, serialized: dict | None, messages: list, *, run_id: Any, **kwargs: Any
    ) -> None:
        model = (serialized or {}).get("id", ["?"])[-1]
        invocation = kwargs.get("invocation_params", {})
        model_name = invocation.get("model", invocation.get("model_name", model))
        self._register(run_id, str(model_name), "llm")
        self._token_count[str(run_id)] = 0

        msg_count = sum(len(batch) for batch in messages) if messages else 0
        self._out(
            f"{_PIPE}  {_GREY}│{_RESET}   {_MAGENTA}🔮 Chat{_RESET}  "
            f"{_DIM}{model_name}  ({msg_count} messages){_RESET}"
        )

    async def on_llm_new_token(self, token: str, *, run_id: Any, **kwargs: Any) -> None:
        rid = str(run_id)
        self._token_count[rid] = self._token_count.get(rid, 0) + 1

    async def on_llm_end(self, response: Any, *, run_id: Any, **kwargs: Any) -> None:
        rid, name, kind, elapsed = self._resolve(run_id, **kwargs)
        tc = _time_color(elapsed)
        tokens = self._token_count.pop(rid, 0)

        token_info = f"  {_DIM}{tokens} tokens{_RESET}" if tokens else ""
        self._out(
            f"{_PIPE}  {_GREY}│{_RESET}     "
            f"{_MAGENTA}↳{_RESET} {tc}{elapsed:.1f}s{_RESET}{token_info}"
        )

        if IS_DEBUG and response:
            text = str(getattr(response, "text", ""))
            if not text and hasattr(response, "generations"):
                gens = response.generations
                if gens and gens[0]:
                    text = (
                        str(gens[0][0].text)
                        if hasattr(gens[0][0], "text")
                        else str(gens[0][0])
                    )
            if text:
                self._out(
                    f"{_PIPE}  {_GREY}│{_RESET}     {_DIM}response: {_truncate(text, 200)}{_RESET}"
                )

    async def on_llm_error(
        self, error: BaseException, *, run_id: Any, **kwargs: Any
    ) -> None:
        rid, name, kind, elapsed = self._resolve(run_id, **kwargs)

        self._out(
            f"{_PIPE}  {_GREY}│{_RESET}     "
            f"{_RED}✗ LLM ({elapsed:.1f}s) {type(error).__name__}: {_truncate(error, 150)}{_RESET}"
        )

    # -------------------------------------------------------------------
    # Retry events
    # -------------------------------------------------------------------

    async def on_retry(self, retry_state: Any, *, run_id: Any, **kwargs: Any) -> None:
        attempt = getattr(retry_state, "attempt_number", "?")
        outcome = getattr(retry_state, "outcome", None)
        exc = outcome.exception() if outcome and outcome.failed else None
        reason = (
            f"  {_DIM}{type(exc).__name__}: {_truncate(exc, 100)}{_RESET}"
            if exc
            else ""
        )

        self._out(
            f"{_PIPE}  {_GREY}│{_RESET}   {_YELLOW}↻ Retry{_RESET}  "
            f"{_DIM}attempt {attempt}{_RESET}{reason}"
        )


def get_tracer() -> PipelineTracer:
    """Get a fresh pipeline tracer. Wire into any LangGraph app:

    from your_project.log import get_tracer
    result = await app.ainvoke(inputs, config={"callbacks": [get_tracer()]})
    """
    return PipelineTracer()
