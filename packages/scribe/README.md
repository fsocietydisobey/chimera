# Meeting Scribe

Record meeting audio, then transcribe, summarize, extract action items, and detect speaker emotions using Gemini AI — orchestrated by a LangGraph pipeline. Supports both batch processing and real-time live transcription via the Gemini Live API.

## How it works

### Batch mode (`record` / `process`)

```
                          ┌──→ [Summarize]        ──┐
[Record Audio] → [Transcribe] ──├──→ [Extract Actions]  ──├──→ [Results]
                          └──→ [Detect Emotions]  ──┘
      ↑               ↑              ↑                ↑
  sounddevice      Gemini         Gemini            Gemini
  PipeWire       (audio→text)  (parallel)     (parallel, uses
                                              audio + transcript)
```

Records audio first, then runs the full LangGraph pipeline. After transcription, the summarize, extract, and emotion detection nodes run **in parallel**.

### Live mode (`live`)

```
[Mic/System Audio] ──PCM16──→ [Gemini 3.1 Flash Live] ──→ [Terminal]
       ↑              WebSocket (bidirectional)                ↑
   sounddevice         16kHz mono, real-time              transcription
                                                         tokens stream
```

Streams audio to Gemini's Live API over WebSocket in real-time. Transcription appears in your terminal as words are spoken.

## Features

- **Dual-source recording** — captures both system audio (meeting participants) and mic (your voice), mixed into one file
- **Dynamic device detection** — automatically follows your default audio output (speakers, headphones, Bluetooth)
- **Speaker identification** — learns speaker names from introductions (e.g., "Hi, I'm Joseph") and uses real names instead of "Speaker 1"
- **Emotion detection** — analyzes vocal tone (pitch, pace, tension, hesitation) to detect emotions per speaker with notable moments
- **Real-time live transcription** — streams audio to Gemini Live API, see tokens as they're spoken
- **LangGraph pipeline** — transcribe → (summarize + extract + emotions) in parallel, with LangSmith tracing
- **Structured output** — transcript, summary, action items, decisions, participants, speaker emotions, and meeting mood

## Setup

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) package manager
- PipeWire or PulseAudio (Linux)
- A [Gemini API key](https://aistudio.google.com/apikey)

### Install

```bash
git clone https://github.com/fsocietydisobey/meeting-scribe.git
cd meeting-scribe
uv sync
```

### Configure

```bash
export GOOGLE_AI_API_KEY="your-api-key-here"

# Optional
export GEMINI_MODEL="gemini-2.0-flash"                       # Model for batch processing
export SCRIBE_OUTPUT_DIR="~/.local/share/meeting-scribe"     # Where recordings are saved

# LangSmith tracing (optional — traces LangGraph pipeline)
export LANGSMITH_TRACING=true
export LANGSMITH_ENDPOINT=https://api.smith.langchain.com
export LANGSMITH_API_KEY="your-langsmith-key"
export LANGSMITH_PROJECT="meeting-scribe"

# Logging
export LOG_LEVEL=INFO    # INFO (default) or DEBUG for verbose output
```

## Usage

### Live transcription (real-time)

```bash
uv run meeting-scribe live
```

Streams audio to Gemini 3.1 Flash Live via WebSocket. Transcription tokens appear in your terminal as they're spoken. Press `Ctrl+C` to stop — audio is saved to a WAV file.

### Live transcription + full analysis

```bash
uv run meeting-scribe live -s
```

Same as `live`, but after you stop, runs the full LangGraph pipeline to generate a summary, action items, decisions, and emotion analysis.

### Record a meeting (batch)

```bash
uv run meeting-scribe record
```

Records from both system audio monitor and mic simultaneously, mixed into one mono WAV. Press `Ctrl+C` to stop.

### Record and process immediately

```bash
uv run meeting-scribe record -p
```

Records, then runs the full LangGraph pipeline when you stop.

### Process an existing recording

```bash
uv run meeting-scribe process path/to/meeting.wav
```

Runs the LangGraph pipeline on any WAV file.

### List recordings

```bash
uv run meeting-scribe list
```

Shows all recordings with size and processing status.

### Command summary

| Command | What it does | LangGraph | Real-time |
|---|---|---|---|
| `live` | Stream audio, live transcription | No | Yes |
| `live -s` | Live transcription + full analysis after | Yes (after) | Yes |
| `record` | Record audio only | No | No |
| `record -p` | Record + full pipeline after | Yes (after) | No |
| `process <file>` | Run pipeline on existing file | Yes | No |
| `list` | Show saved recordings | No | No |

## Output

Results are printed to stdout and saved as a JSON file alongside the WAV:

```
~/.local/share/meeting-scribe/
  meeting_20260402_140000.wav      # Audio
  meeting_20260402_140000.json     # Full results (all fields below)
```

The JSON contains: `audio_path`, `transcript`, `summary`, `action_items`, `decisions`, `participants`, `speaker_emotions`, `meeting_mood`.

### Example output

```
============================================================
MEETING SCRIBE — Results
============================================================

## Transcript

Joseph: Let's get started. So the API redesign — where are we?
Mark: We've got the schema finalized. I'm a bit worried about the timeline though...

## Summary

Meeting between Joseph and Mark to discuss API redesign progress...

## Action Items

  - Mark: Send updated API schema to the team by Friday
  - Joseph: Schedule follow-up with stakeholders

## Decisions

  - Go with REST over GraphQL for the public API

## Participants

  - Joseph
  - Mark

## Meeting Mood

  Productive but slightly tense around deadlines

## Speaker Emotions

  Joseph — calm, assertive
    - confident
    - slightly impatient
    * Got direct when asking about the timeline

  Mark — enthusiastic, anxious
    - excited about the schema work
    - nervous about delivery dates
    * Voice tightened when discussing deadlines
```

## Audio Sources

Meeting Scribe captures audio via PipeWire's PulseAudio compatibility layer. It dynamically detects your current default devices using `pactl`:

| Source | What it captures | How it's detected |
|---|---|---|
| **System monitor** | What you hear (other participants) | `pactl get-default-sink` + `.monitor` |
| **Microphone** | Your voice (headphone mic, built-in, etc.) | `pactl get-default-source` |

Both sources are recorded in parallel threads and mixed into a single mono WAV. If you switch from speakers to headphones, the recorder automatically follows.

```bash
# See your available audio devices
pactl list short sources
pactl list short sinks
```

## Speaker Identification

Meeting Scribe prompts Gemini to learn speaker names from introductions. If participants say their names early in the meeting:

```
Joseph: Let's get started. I'm Joseph, PM on the platform team.
Mark: Hey, Mark here from engineering.
Joseph: So about the API redesign...
```

Instead of generic `Speaker 1` / `Speaker 2` labels. Works in both live and batch modes.

## Emotion Detection

The emotion detection node sends both the raw audio file and the transcript to Gemini. This allows the model to analyze:

- **Vocal cues** — pitch, pace, volume, tension, hesitation, tremor
- **Speech patterns** — interruptions, long pauses, rapid speech
- **Context** — what was being discussed when tone shifted

It produces per-speaker emotion profiles with an overall meeting mood rating. This runs in parallel with summarization and action item extraction, so it adds minimal extra processing time.

## Architecture

```
src/meeting_scribe/
  cli.py              # CLI entry point (record, process, live, list)
  recorder.py         # Dual-source audio capture (system + mic)
  live.py             # Gemini Live API streaming transcription
  graph.py            # LangGraph pipeline with parallel nodes
  state.py            # Pipeline state (TypedDict)
  config.py           # Config + environment variables
  log.py              # Pipeline tracer (local LangSmith alternative)
  nodes/
    __init__.py       # Shared Gemini client (reads GOOGLE_AI_API_KEY)
    transcribe.py     # Gemini audio → transcript (batch)
    summarize.py      # Transcript → structured summary
    extract.py        # Transcript → action items, decisions, participants
    emotion.py        # Audio + transcript → speaker emotions, meeting mood
```

## Debugging & Tracing

Meeting Scribe includes a built-in pipeline tracer that prints node execution, tool calls, LLM activity, retries, and errors directly to your terminal — no external dashboard required.

### Local tracing (terminal)

```bash
# INFO — node names, timing, input/output keys, parallel detection
uv run meeting-scribe record -p

# DEBUG — adds input/output values, LLM model info, tool args, final state
LOG_LEVEL=DEBUG uv run meeting-scribe record -p
```

**INFO output:**
```
┌ LangGraph  input: audio_path
│
│  ├── transcribe  ← audio_path
│  │   2.1s  → transcript
│
│  ├── summarize  ← audio_path, transcript
│  ├── extract_actions  (parallel)  ← audio_path, transcript
│  ├── detect_emotions  (parallel)  ← audio_path, transcript
│  │   0.7s  → action_items, decisions, participants
│  │   0.8s  → summary
│  │   1.2s  → speaker_emotions, meeting_mood
│
└ Done  3.3s
```

**DEBUG output** adds `in.*` and `out.*` values under each node, plus the full final state.

### What gets traced

| Event | Symbol | What you see |
|---|---|---|
| Node start | `├──` | Name, `(parallel)` tag, input keys |
| Node end | timing | Elapsed (green < 2s, yellow < 10s, red > 10s), output keys |
| Tool call | `⚡` | Tool name, args (DEBUG), result |
| LLM call | `🔮` | Model name, token count, response preview |
| Retry | `↻` | Attempt number, error reason |
| Error | `✗` | Node name, exception type, message |

### LangSmith (cloud dashboard)

For the full visual trace experience, set these env vars and LangGraph traces automatically:

```bash
export LANGSMITH_TRACING=true
export LANGSMITH_ENDPOINT=https://api.smith.langchain.com
export LANGSMITH_API_KEY="your-langsmith-key"
export LANGSMITH_PROJECT="meeting-scribe"
```

Both local and LangSmith tracing can run simultaneously.

### Reusing the tracer in other LangGraph projects

The tracer is a single file (`log.py`) with no project-specific dependencies. Copy it into any LangGraph project and wire it in:

```python
from your_project.log import get_tracer

result = await app.ainvoke(inputs, config={"callbacks": [get_tracer()]})
```

Zero code changes needed in your nodes — the callback handles everything.

## Extending the pipeline

The LangGraph architecture makes it straightforward to add new processing steps:

```python
from meeting_scribe.graph import build_graph

graph = build_graph()

# Add a new node that runs in parallel with the others
graph.add_node("my_node", my_async_function)
graph.add_edge("transcribe", "my_node")
graph.add_edge(["summarize", "extract_actions", "detect_emotions", "my_node"], "__end__")

app = graph.compile()
```

Ideas for extensions:
- **Human review** — add an interrupt after transcription for corrections
- **Refinement loop** — re-process unclear sections with targeted prompts
- **Email/Slack** — auto-send results after processing
- **Checkpointing** — add persistence to resume failed pipelines
- **Follow-up tracker** — compare action items across meetings

## License

MIT
