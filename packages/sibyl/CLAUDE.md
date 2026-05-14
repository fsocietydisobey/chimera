# Project: Sibyl

> **Sibyl** — the prophetic woman of antiquity whose body withered until **only her voice remained**, preserved in the Sibylline Books. Now part of the khimaira monorepo (NORTH_STAR Phase 0). Sibyl's tools are exposed via khimaira's unified MCP server under source-prefixed names: `mcp__khimaira__sibyl_record_start`, `mcp__khimaira__sibyl_transcribe`, etc. The standalone `sibyl serve` command remains for backward compat; the canonical install path is through khimaira (`uvx khimaira mcp`).

Meeting audio recorder + AI processing pipeline. Records system and mic audio during meetings, then sends recordings to Gemini for transcription, summarization, emotion detection, and action-item extraction via a LangGraph pipeline. The captured voice survives the moment — the sibylline mapping is the package's name.

## Commands

```bash
uv run sibyl record              # Start recording a meeting
uv run sibyl process <file>      # Process a recorded audio file (full pipeline)
uv run sibyl transcribe <file>   # Transcript only (no summarize/extract/emotion)
uv run sibyl list                # List recorded meetings
uv run sibyl live                # Real-time streaming transcription via Gemini Live API
uv run sibyl serve               # Standalone MCP server (stdio) — backward-compat
```

## Architecture

```
src/sibyl/
  __init__.py
  cli.py                  # CLI entry point (record, process, transcribe, list, live, serve)
  recorder.py             # Audio capture (PipeWire/PulseAudio)
  recording_control.py    # Background-recording lifecycle (record_start / record_stop)
  graph.py                # LangGraph pipeline (transcribe → {summarize, extract, emotions})
  state.py                # TypedDict state definition
  config.py               # Config loader (audio devices, GEMINI_API_KEY env var)
  log.py                  # Tracing helpers
  nodes/
    __init__.py           # Shared infrastructure (Gemini client, per-role models)
    transcribe.py         # Gemini audio transcription
    summarize.py          # Meeting summarization
    extract.py            # Action items + decisions
    emotion.py            # Vocal-tone emotion detection
  server/
    __init__.py
    mcp.py                # FastMCP server registering sibyl_* tools
```

## MCP Tools (re-exposed under khimaira as `sibyl_*`)

| Tool | What it does |
|---|---|
| `record_start` | Spawn background recording subprocess; returns `recording_id` + `output_path` |
| `record_stop` | Stop a running recording by id |
| `list_active_recordings` | What's currently capturing |
| `transcribe` | One-shot Gemini transcription of an audio file (no pipeline) |
| `summarize` | Summarize an existing transcript (no audio needed) |
| `process` | Full LangGraph pipeline: transcribe → summarize + extract + emotion in parallel |

## Conventions

### Python
- Python 3.12+. Modern syntax: `str | None`, `list[str]`, `dict[str, Any]`.
- Async throughout — LangGraph nodes are `async def`, Gemini SDK calls are awaitable.
- Type hints on all signatures.
- Imports: stdlib → third-party → `sibyl.*` (absolute imports).
- Format with `black` after every change.

### Audio
- PipeWire with PulseAudio compat layer.
- Default mic + system-audio monitor are auto-detected; override via env.
- Sample rate: 16kHz mono for transcription quality.
- Output format: WAV (lossless, Gemini-compatible).
- Storage default: `~/.local/share/sibyl/meeting_<timestamp>.wav`. Override via `SIBYL_OUTPUT_DIR`.

### LangGraph
- Graph: `transcribe → fan-out to {summarize, extract_actions, detect_emotions}` in parallel.
- State is a `MeetingState` TypedDict; required fields set by `transcribe`.
- Per-node Gemini model selectable via `SIBYL_AUDIO_MODEL` etc.

### Usage tracking
- Every Gemini call records a `UsageRecord` via `khimaira.usage.get_recorder()` so `khimaira usage savings` includes Sibyl dispatches in its tally.
- If khimaira isn't importable (workspace breakage), Sibyl logs + skips the recording — never crashes the pipeline.

## Things to avoid

- Don't record without user confirmation (privacy).
- Don't use sync I/O in async code paths.
- Don't add dependencies without checking if an existing one covers the need.
- Don't commit `.env` files or API keys.
- Don't generate prose where Gemini's structured output already suffices — use response schemas, not loose prompts.
