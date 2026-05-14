"""Transcription node — uploads audio once via Files API, transcribes via Gemini."""

import time

from scribe.nodes import (
    get_audio_model,
    get_client,
    gemini_usage_to_tokens,
    record_node_usage,
    upload_audio_once,
)
from scribe.state import MeetingState


async def transcribe(state: MeetingState) -> dict:
    """Transcribe audio file using Gemini's native audio understanding.

    Uploads via the Files API (vs the old inline_data + base64 pattern)
    so the same file handle can be reused by the emotion node when
    emotions are enabled — no double-uploading the audio bytes.

    Stashes the uploaded file's name in state so emotion can reference it.
    """
    audio_path = state["audio_path"]
    model = get_audio_model()

    # Upload once. Subsequent nodes reference the file handle by name.
    file = await upload_audio_once(audio_path)

    client = get_client()
    t0 = time.monotonic()
    response = await client.aio.models.generate_content(
        model=model,
        contents=[
            file,
            (
                "Transcribe this meeting audio verbatim. "
                "When speakers introduce themselves, use their actual names as labels "
                "(e.g., 'Joseph:', 'Mark:') instead of generic labels like 'Speaker 1'. "
                "If a speaker hasn't been identified, use 'Unknown Speaker' until you can "
                "match their voice to a name. "
                "Preserve natural speech patterns but clean up filler words. "
                "Format as a clean transcript with timestamps if possible."
            ),
        ],
    )
    latency_s = time.monotonic() - t0

    in_tok, out_tok = gemini_usage_to_tokens(response)
    await record_node_usage(
        role="transcribe",
        model=model,
        input_tokens=in_tok,
        output_tokens=out_tok,
        latency_s=latency_s,
        task_id=state.get("task_id"),
    )

    return {
        "transcript": response.text,
        # File handle's NAME for downstream nodes. The Gemini SDK File
        # object isn't directly JSON-serializable, but the name is the
        # stable reference; client.files.get(name=...) resolves it back.
        "audio_file_name": file.name,
    }
