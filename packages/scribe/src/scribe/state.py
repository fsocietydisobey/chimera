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
    """

    # Input — caller sets these
    audio_path: str
    with_emotions: bool
    task_id: str | None

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
