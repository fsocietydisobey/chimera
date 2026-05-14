"""Meeting pipeline state."""

from typing import TypedDict


class MeetingState(TypedDict, total=False):
    """State flowing through the LangGraph pipeline.

    Required keys (set by callers): audio_path.
    Required after transcribe: transcript, audio_file_name.
    Optional caller flag: with_emotions (default False — disabled for standups
    where emotional analysis isn't useful and the second audio submission
    doubles the audio bill).
    Optional caller context: task_id (project label for khimaira usage
    attribution; the per-node usage records will land in the same task_id
    bucket).
    Optional transcription hints:
      - known_speakers: list of expected meeting participants. When set,
        the transcribe prompt names them explicitly AND tells Gemini to
        treat any other voices as background workers to ignore (vs the
        default "Unknown Speaker" attribution). Big accuracy win for
        multi-speaker meetings with office background noise.
      - accent_hint: free-form acoustic context (e.g. "Indian English",
        "British + American mix", "speakers may code-switch to Hindi").
        Gemini's audio understanding adjusts its priors when given an
        explicit acoustic profile.
    """

    # Input — caller sets these
    audio_path: str
    with_emotions: bool
    task_id: str | None
    known_speakers: list[str]
    accent_hint: str

    # Set by transcribe (audio_file_name is the Files API handle's name,
    # which emotion uses to reference the same upload without re-sending bytes)
    transcript: str
    audio_file_name: str

    # Set by summarize / extract / emotion (some skipped depending on flags)
    summary: str
    action_items: list[str]
    decisions: list[str]
    participants: list[str]
    speaker_emotions: list[dict]
    meeting_mood: str
