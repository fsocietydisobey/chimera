"""Emotion detection — opt-in node that reuses the already-uploaded audio.

When `state["with_emotions"]` is True, this node runs in parallel with
summarize + extract. It references the audio file uploaded by the
transcribe node (via state["audio_file_name"]) rather than re-uploading
the bytes — that was the biggest fix in the 2026-05-13 token-burn refactor.

When `state["with_emotions"]` is False (the default for standups), this
node is a no-op — the graph skips it via a conditional edge. Saves ~50%
of total pipeline cost for meetings where emotional tone isn't relevant.
"""

import json
import time

from sibyl.nodes import (
    get_audio_model,
    get_client,
    gemini_usage_to_tokens,
    record_node_usage,
)
from sibyl.state import MeetingState


async def detect_emotions(state: MeetingState) -> dict:
    """Analyze speaker emotions from the audio + transcript."""
    if not state.get("with_emotions"):
        return {"speaker_emotions": [], "meeting_mood": "not-analyzed"}

    transcript = state["transcript"]
    audio_file_name = state.get("audio_file_name")
    model = get_audio_model()

    client = get_client()
    file = client.files.get(name=audio_file_name) if audio_file_name else None
    if file is None:
        return {"speaker_emotions": [], "meeting_mood": "audio-unavailable"}

    t0 = time.monotonic()
    response = await client.aio.models.generate_content(
        model=model,
        contents=[
            file,
            (
                "Analyze the emotional tone of each speaker in this meeting audio. "
                "You have both the audio (for vocal cues like pitch, pace, volume, "
                "tension, hesitation) and the transcript below for context.\n\n"
                "Return JSON with this structure:\n"
                "```json\n"
                "{\n"
                '  "speaker_emotions": [\n'
                "    {\n"
                '      "speaker": "Name or Speaker label",\n'
                '      "overall_tone": "e.g. calm, frustrated, enthusiastic",\n'
                '      "emotions_detected": ["confident", "slightly anxious"],\n'
                '      "notable_moments": ["Got tense when discussing deadlines"]\n'
                "    }\n"
                "  ],\n"
                '  "meeting_mood": "Overall emotional temperature of the meeting"\n'
                "}\n"
                "```\n\n"
                "Rules:\n"
                "- Base your analysis primarily on vocal tone, not just word choice\n"
                "- Note shifts in emotion throughout the meeting\n"
                "- Be specific about notable moments where tone changed\n"
                "- Return ONLY valid JSON, no markdown fences\n\n"
                f"## Transcript\n\n{transcript}"
            ),
        ],
    )
    latency_s = time.monotonic() - t0

    in_tok, out_tok = gemini_usage_to_tokens(response)
    await record_node_usage(
        role="emotion",
        model=model,
        input_tokens=in_tok,
        output_tokens=out_tok,
        latency_s=latency_s,
        task_id=state.get("task_id"),
    )

    try:
        raw = response.text or ""
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1]
            text = text.rsplit("```", 1)[0]
        data = json.loads(text)
    except (json.JSONDecodeError, IndexError):
        data = {"speaker_emotions": [], "meeting_mood": "unknown"}

    return {
        "speaker_emotions": data.get("speaker_emotions", []),
        "meeting_mood": data.get("meeting_mood", "unknown"),
    }
