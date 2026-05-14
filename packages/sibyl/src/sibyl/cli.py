"""CLI entry point for Sibyl."""

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import cast

from sibyl.config import load_config
from sibyl.graph import process_meeting
from sibyl.live import live_transcribe
from sibyl.recorder import record_meeting
from sibyl.state import MeetingState


def _print_results(result: MeetingState) -> None:
    """Pretty-print pipeline results to stdout."""
    print("\n" + "=" * 60)
    print("SIBYL — Results")
    print("=" * 60)

    if result.get("transcript"):
        print("\n## Transcript\n")
        print(result["transcript"])

    if result.get("summary"):
        print("\n## Summary\n")
        print(result["summary"])

    if result.get("action_items"):
        print("\n## Action Items\n")
        for item in result["action_items"]:
            print(f"  - {item}")

    if result.get("decisions"):
        print("\n## Decisions\n")
        for decision in result["decisions"]:
            print(f"  - {decision}")

    if result.get("participants"):
        print("\n## Participants\n")
        for p in result["participants"]:
            print(f"  - {p}")

    if result.get("meeting_mood"):
        print(f"\n## Meeting Mood\n")
        print(f"  {result['meeting_mood']}")

    if result.get("speaker_emotions"):
        print("\n## Speaker Emotions\n")
        for se in result["speaker_emotions"]:
            speaker = se.get("speaker", "Unknown")
            tone = se.get("overall_tone", "unknown")
            print(f"  {speaker} — {tone}")
            for emotion in se.get("emotions_detected", []):
                print(f"    - {emotion}")
            for moment in se.get("notable_moments", []):
                print(f"    * {moment}")

    print("\n" + "=" * 60)

    # Also save as JSON alongside the audio
    audio_path = Path(result.get("audio_path", ""))
    if audio_path.exists():
        json_path = audio_path.with_suffix(".json")
        json_path.write_text(json.dumps(result, indent=2))
        print(f"\nResults saved to: {json_path}")


def cmd_record(args: argparse.Namespace) -> None:
    """Record a meeting and optionally process it."""
    config = load_config()
    audio_path = record_meeting(config)

    if args.process:
        print("\nProcessing recording...")
        result = asyncio.run(process_meeting(audio_path))
        _print_results(result)


def cmd_process(args: argparse.Namespace) -> None:
    """Process an existing audio file."""
    audio_path = Path(args.file)
    if not audio_path.exists():
        print(f"File not found: {audio_path}", file=sys.stderr)
        sys.exit(1)

    print(f"Processing: {audio_path}")
    result = asyncio.run(process_meeting(audio_path))
    _print_results(result)


def cmd_live(args: argparse.Namespace) -> None:
    """Live transcription with real-time streaming."""
    config = load_config()
    result = asyncio.run(live_transcribe(config))

    if args.summarize and result.get("transcript"):
        print("\nRunning summary pipeline...")
        full_result = asyncio.run(process_meeting(result["audio_path"]))
        # Keep the live transcript, add summary + extraction
        full_result["transcript"] = result["transcript"]
        _print_results(full_result)
    else:
        _print_results(cast(MeetingState, result))


def cmd_list(_args: argparse.Namespace) -> None:
    """List recorded meetings."""
    config = load_config()
    recordings = sorted(config.output_dir.glob("meeting_*.wav"))

    if not recordings:
        print("No recordings found.")
        return

    print(f"Recordings in {config.output_dir}:\n")
    for rec in recordings:
        size_mb = rec.stat().st_size / (1024 * 1024)
        json_path = rec.with_suffix(".json")
        processed = " [processed]" if json_path.exists() else ""
        print(f"  {rec.name}  ({size_mb:.1f} MB){processed}")


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="sibyl",
        description="Record meetings and process with Gemini AI",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # record
    rec_parser = subparsers.add_parser("record", help="Record a meeting")
    rec_parser.add_argument(
        "--process",
        "-p",
        action="store_true",
        help="Process the recording immediately after stopping",
    )
    rec_parser.set_defaults(func=cmd_record)

    # process
    proc_parser = subparsers.add_parser("process", help="Process a recorded audio file")
    proc_parser.add_argument("file", help="Path to the audio file")
    proc_parser.set_defaults(func=cmd_process)

    # live
    live_parser = subparsers.add_parser(
        "live", help="Live transcription with real-time streaming"
    )
    live_parser.add_argument(
        "--summarize",
        "-s",
        action="store_true",
        help="Run summary + extraction after stopping",
    )
    live_parser.set_defaults(func=cmd_live)

    # list
    list_parser = subparsers.add_parser("list", help="List recorded meetings")
    list_parser.set_defaults(func=cmd_list)

    args = parser.parse_args()
    args.func(args)
