"""LangGraph pipeline: transcribe → (summarize + extract [+ emotions]) in parallel.

Emotion detection is conditionally skipped when state["with_emotions"] is
False (the default). This is the biggest cost win in the 2026-05-13
refactor — emotion was previously always-on and re-uploaded the full
audio, ~doubling the audio bill on every meeting whether or not the
emotional analysis was actually used.
"""

import asyncio
from pathlib import Path
from typing import cast

from langgraph.graph import StateGraph

from scribe.log import get_tracer
from scribe.nodes.emotion import detect_emotions
from scribe.nodes.extract import extract_actions
from scribe.nodes.summarize import summarize
from scribe.nodes.transcribe import transcribe
from scribe.state import MeetingState


def _route_after_transcribe(state: MeetingState) -> list[str]:
    """Conditional fan-out — emotion only if explicitly enabled.

    LangGraph's conditional edges accept a list to fan out to multiple
    target nodes. We always run summarize + extract; emotion only when
    state["with_emotions"] is truthy.
    """
    targets = ["summarize", "extract_actions"]
    if state.get("with_emotions"):
        targets.append("detect_emotions")
    return targets


def build_graph() -> StateGraph:
    """Build the meeting processing pipeline.

    After transcription, summarize + extract (+ emotions, optional) run
    in parallel. The conditional edge after transcribe filters out the
    emotion branch when with_emotions is False.
    """
    graph = StateGraph(MeetingState)

    graph.add_node("transcribe", transcribe)
    graph.add_node("summarize", summarize)
    graph.add_node("extract_actions", extract_actions)
    graph.add_node("detect_emotions", detect_emotions)

    graph.set_entry_point("transcribe")
    graph.add_conditional_edges(
        "transcribe",
        _route_after_transcribe,
        # LangGraph requires the path map for conditional fan-out
        {
            "summarize": "summarize",
            "extract_actions": "extract_actions",
            "detect_emotions": "detect_emotions",
        },
    )
    # All parallel branches join at END. detect_emotions only fires if
    # the conditional edge above included it.
    graph.add_edge("summarize", "__end__")
    graph.add_edge("extract_actions", "__end__")
    graph.add_edge("detect_emotions", "__end__")

    return graph


def compile_graph():
    """Compile the graph ready for invocation."""
    return build_graph().compile()


async def process_meeting(
    audio_path: str | Path,
    *,
    with_emotions: bool = False,
    task_id: str | None = None,
    known_speakers: list[str] | None = None,
    accent_hint: str = "",
) -> MeetingState:
    """Run the full pipeline on an audio file. Returns final state.

    Args:
        audio_path: WAV file to transcribe.
        with_emotions: Run emotion detection (extra audio submission cost).
            Default False — useful for standups, retros, demos where vocal
            tone isn't the point. Enable for performance reviews, customer
            calls, etc.
        task_id: Optional project label for khimaira usage attribution. All
            node-level usage records land in this bucket — so
            `khimaira usage savings --by task_id` shows per-meeting spend.
        known_speakers: Optional list of meeting participant names. When
            set, transcribe filters non-participant voices and uses the
            list for labeling. khimaira passes names through verbatim
            from the caller — never stored as defaults.
        accent_hint: Optional acoustic context to help Gemini's audio
            understanding (e.g. "Indian English", "British + American").
    """
    app = compile_graph()
    initial: MeetingState = {
        "audio_path": str(audio_path),
        "with_emotions": with_emotions,
        "task_id": task_id,
        "known_speakers": list(known_speakers or []),
        "accent_hint": accent_hint or "",
    }
    result = await app.ainvoke(
        initial, config={"callbacks": [get_tracer()]}
    )
    return cast(MeetingState, result)


def process_meeting_sync(
    audio_path: str | Path,
    *,
    with_emotions: bool = False,
    task_id: str | None = None,
    known_speakers: list[str] | None = None,
    accent_hint: str = "",
) -> MeetingState:
    """Synchronous wrapper for process_meeting."""
    return asyncio.run(
        process_meeting(
            audio_path,
            with_emotions=with_emotions,
            task_id=task_id,
            known_speakers=known_speakers,
            accent_hint=accent_hint,
        )
    )
