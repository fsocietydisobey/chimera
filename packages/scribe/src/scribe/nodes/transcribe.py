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


def _build_prompt(known_speakers: list[str], accent_hint: str) -> str:
    """Build the transcribe prompt, conditioned on caller-provided hints.

    Three optional layers:
      - speaker list: when names are given, Gemini anchors voices to known
        identities (vs guessing) and treats other voices as background to ignore
      - cueing recognition: meeting lead naming each speaker before they talk
        ("Sai, go ahead") gives Gemini a voice-to-name anchor on the speaker's
        first line. Robust whether or not the speakers introduce themselves.
      - accent hint: free-form acoustic context. Gemini adjusts its acoustic
        priors when told what to expect.

    When no hints are provided, falls back to the original "label by
    self-introduction" prompt (back-compat with existing callers).
    """
    lines: list[str] = [
        "Transcribe this meeting audio verbatim.",
    ]

    if known_speakers:
        speaker_list = ", ".join(known_speakers)
        # Build labeling examples dynamically from the actual list so the
        # prompt isn't coupled to any specific team's names. Khimaira is
        # generic; the participant identifiers flow in from the caller
        # at record_start and are passed through here verbatim.
        first = known_speakers[0]
        second = known_speakers[1] if len(known_speakers) > 1 else first
        lines.extend([
            "",
            f"## Known participants",
            f"This meeting is between: **{speaker_list}**. These are the only "
            f"meeting participants — there are exactly {len(known_speakers)} "
            f"of them. Other voices you may hear (background office workers, "
            f"side conversations, hallway chatter) are NOT participants. "
            f"Do not attribute their speech in the transcript at all — skip "
            f"non-participant voices entirely. If a voice is faint, distant, "
            f"or clearly not part of the meeting discussion, it is background "
            f"and should be omitted, not labeled 'Unknown Speaker'.",
            "",
            "## Speaker labeling",
            f"Label each line with the participant's name (e.g. '{first}:', "
            f"'{second}:'). The meeting lead typically cues each speaker by "
            f"name before they talk (e.g. '{first}, go ahead' or '{second}, "
            f"what's on your plate?'). Use these cues as voice-to-name "
            "anchors: the voice that responds immediately after a cue is "
            "that named speaker for the rest of the recording. Once you've "
            "matched a voice to a name via a cue or introduction, persist "
            "that mapping for the rest of the transcript.",
        ])
    else:
        lines.append(
            " When speakers introduce themselves, use their actual names as "
            "labels (e.g., 'Joseph:', 'Mark:') instead of generic labels "
            "like 'Speaker 1'. If a speaker hasn't been identified, use "
            "'Unknown Speaker' until you can match their voice to a name."
        )

    if accent_hint:
        lines.extend([
            "",
            "## Acoustic context",
            f"Speakers have {accent_hint} accents. Preserve technical "
            "vocabulary accurately even when accent pronunciation differs "
            "from standard. If speakers code-switch between languages, "
            "transcribe non-English phrases phonetically and mark them "
            "with [non-English] inline so the meaning isn't lost.",
        ])

    lines.extend([
        "",
        "## Output format",
        "Preserve natural speech patterns but clean up filler words (um, uh, "
        "like). Format as a clean transcript with timestamps where natural "
        "breaks occur.",
    ])

    return "\n".join(lines)


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

    prompt = _build_prompt(
        known_speakers=state.get("known_speakers") or [],
        accent_hint=state.get("accent_hint") or "",
    )

    client = get_client()
    t0 = time.monotonic()
    response = await client.aio.models.generate_content(
        model=model,
        contents=[file, prompt],
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
