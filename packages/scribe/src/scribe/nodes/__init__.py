"""Shared infrastructure for scribe pipeline nodes.

Three responsibilities consolidated here so each node stays small:

1. **Gemini client + model resolution** — single source of truth for the
   Gemini SDK client + the (now per-role) model selection. Transcribe
   and emotion need an audio-capable model; summarize and extract get
   routed through khimaira's pool router instead.

2. **Audio handling** — uploads the audio file via Gemini's Files API
   ONCE per pipeline run (vs the previous inline_data + base64 pattern
   that sent the bytes on every node). This lets the second use
   (emotion, when enabled) reference the same file handle instead of
   re-uploading the bytes. Combined with khimaira's per-role tiering,
   this is the biggest token-cost win in the refactor.

3. **Usage recording** — every Gemini call writes a UsageRecord with
   `role="transcribe"|"emotion"|"summarize"|"extract"` so
   `khimaira usage savings --by role` can break down meeting costs.

The token-burn diagnosis that motivated this rewrite (2026-05-13):
the original pipeline sent the full audio TWICE (once for transcribe,
once for emotion), used the same flash model for everything including
text-only nodes that don't need an audio model, and never used the
Files API. For a 30-min standup that was ~2x the audio bill plus
~5x the text-node bill compared to the right tiering.
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import Any

from google import genai

# Default audio-capable Gemini model. Per-role overrides below.
DEFAULT_AUDIO_MODEL = "gemini-2.0-flash"


def get_audio_model() -> str:
    """Audio-capable model for transcribe + emotion. Override via env."""
    return os.environ.get(
        "SCRIBE_AUDIO_MODEL",
        os.environ.get("GEMINI_MODEL", DEFAULT_AUDIO_MODEL),
    )


def get_client() -> genai.Client:
    """Create a Gemini client using GOOGLE_AI_API_KEY from the environment."""
    api_key = os.environ.get("GOOGLE_AI_API_KEY", "")
    return genai.Client(api_key=api_key)


# ---------------------------------------------------------------------------
# Files API — load audio once, reuse across nodes
# ---------------------------------------------------------------------------


async def upload_audio_once(audio_path: str | Path) -> Any:
    """Upload the audio file to Gemini's Files API. Returns a file handle
    that subsequent generate_content calls can reference by name instead
    of re-uploading the bytes.

    The Files API stores the file for ~48 hours; well within a single
    meeting pipeline's lifetime. We don't cache across runs — each
    pipeline invocation gets its own upload — but within ONE run, the
    transcribe and (optional) emotion nodes share this handle.
    """
    path = Path(audio_path)
    client = get_client()
    # Wrap the sync upload so we don't block the event loop on multi-MB
    # audio files (typical 30-min standup is 30-60MB; takes 0.5-2s on
    # local network).
    file = await asyncio.to_thread(client.files.upload, file=str(path))
    # Files take a moment to become ACTIVE before they can be used in
    # generate_content. Poll briefly.
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        state = getattr(file, "state", None)
        state_name = getattr(state, "name", str(state)) if state else ""
        if state_name == "ACTIVE":
            break
        await asyncio.sleep(0.5)
        file = await asyncio.to_thread(client.files.get, name=file.name)
    return file


# ---------------------------------------------------------------------------
# Usage recording — wire every dispatch into khimaira's tracker
# ---------------------------------------------------------------------------


async def record_node_usage(
    *,
    role: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    latency_s: float,
    task_id: str | None = None,
) -> None:
    """Record one Gemini call into khimaira's usage.jsonl.

    Wraps khimaira.usage.get_recorder() so scribe doesn't have to know
    the recorder API internals. Records `mode="explicit-tier"` because
    scribe is choosing an audio-capable model on purpose (not via the
    classifier/pool-router auto path).

    `role` is "transcribe", "emotion", "summarize", or "extract" — so
    `khimaira usage savings --by role` shows per-meeting per-node cost.

    Defensive: never raises. Failed usage recording must not break the
    pipeline. If khimaira isn't importable (rare — scribe is a workspace
    sibling so it should always be), this is a no-op.
    """
    try:
        from khimaira.usage import get_recorder
    except ImportError:
        return
    try:
        await get_recorder().record(
            runner="gemini",
            provider="google",
            model=model,
            input_tokens=int(input_tokens),
            output_tokens=int(output_tokens),
            latency_s=float(latency_s),
            role=role,
            task_id=task_id,
            source="cli",
            mode="explicit-tier",
        )
    except Exception:
        # Usage tracking must never break dispatch.
        pass


def gemini_usage_to_tokens(response: Any) -> tuple[int, int]:
    """Extract (input_tokens, output_tokens) from a Gemini response.

    The google-genai SDK exposes usage_metadata with
    prompt_token_count + candidates_token_count.
    """
    um = getattr(response, "usage_metadata", None)
    if not um:
        return 0, 0
    in_tok = int(getattr(um, "prompt_token_count", 0) or 0)
    out_tok = int(getattr(um, "candidates_token_count", 0) or 0)
    return in_tok, out_tok
