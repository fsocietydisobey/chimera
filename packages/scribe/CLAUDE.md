# Project: Meeting Scribe

Meeting audio recorder + AI processing pipeline. Records system and mic audio during meetings, then sends recordings to Gemini for transcription, summarization, and action item extraction via a LangGraph pipeline.

## Commands

```bash
uv run meeting-scribe record              # Start recording a meeting
uv run meeting-scribe process <file>       # Process a recorded audio file
uv run meeting-scribe list                 # List recorded meetings
```

## Architecture

```
src/meeting_scribe/
  __init__.py
  cli.py                  # CLI entry point (record, process, list)
  recorder.py             # Audio capture (PipeWire/PulseAudio)
  graph.py                # LangGraph pipeline (transcribe → summarize → extract)
  state.py                # TypedDict state definition
  config.py               # Config loader (audio devices, GOOGLE_AI_API_KEY env var)
  nodes/
    __init__.py
    transcribe.py          # Gemini audio transcription
    summarize.py           # Meeting summarization
    extract.py             # Action items + decisions extraction
```

## Conventions

### Python
- Python 3.12+. Use modern syntax: `str | None`, `list[str]`, `dict[str, Any]`.
- Async throughout — LangGraph nodes are `async def`.
- Type hints on all function signatures.
- Imports: stdlib, then third-party, then `meeting_scribe.*` (absolute imports).
- Use `uv add <package>` to add dependencies.

### Audio
- PipeWire with PulseAudio compat layer.
- Default mic: `alsa_input.pci-0000_00_1f.3-platform-skl_hda_dsp_generic.HiFi__Mic1__source`
- System audio monitor: `alsa_output.pci-0000_00_1f.3-platform-skl_hda_dsp_generic.HiFi__Speaker__sink.monitor`
- Sample rate: 16kHz mono for transcription quality.
- Output format: WAV (lossless, Gemini-compatible).

### LangGraph
- Single graph: transcribe → summarize → extract_actions
- State is a TypedDict with audio_path, transcript, summary, action_items.

## Rules

All project rules live in `.claude/rules/`. Inherited from chimera-sdk:
- `conventions.md` — Python standards, architecture
- `guardrails.md` — things to avoid, latency targets
- `tasks.md` — task structure, file naming convention
- `workflow.md` — rule sync policy, formatting
- `git.md` — branching strategy, conventional commits, PR standards
- `testing.md` — coverage requirements, test organization
- `error-handling.md` — error patterns, error envelope format
- `security.md` — secrets, input validation, data privacy
- `environment.md` — env vars, config loading
- `versioning.md` — semver, changelog, release process
- `performance.md` — response time SLAs
- `dependencies.md` — evaluation criteria, version pinning

## Things to avoid

- Don't add dependencies without checking if an existing one covers the need.
- Don't commit `.env` files or API keys.
- Don't use sync I/O in async code paths.
- Don't record without user confirmation (privacy).
