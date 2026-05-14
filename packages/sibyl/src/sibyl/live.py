"""Live transcription — streams audio to Gemini 3.1 Flash Live via WebSocket.

Uses the Gemini Live API with bidirectional streaming:
  - Audio in:  raw 16-bit PCM, 16kHz mono, little-endian
  - Response:  audio + text transcription (input & output)

The model responds with audio, but we enable input_audio_transcription and
output_audio_transcription to get real-time text alongside it.
"""

import asyncio
import signal
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import sounddevice as sd
import soundfile as sf
from google import genai
from google.genai import types

import logging

from sibyl.config import Config
from sibyl.recorder import (
    _get_default_monitor,
    _get_default_source,
    _get_device_index,
)

log = logging.getLogger("sibyl.live")

SAMPLE_RATE = 16_000  # Gemini Live API requires 16kHz input
CHUNK_SECONDS = 1.0  # Send audio every 1s
LIVE_MODEL = "gemini-3.1-flash-live-preview"


async def live_transcribe(config: Config) -> dict:
    """Stream audio to Gemini Live API and print transcription in real-time."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = config.output_dir / f"meeting_{timestamp}.wav"

    # Resolve audio device
    device: int | str | None = None
    if config.record_system_audio:
        monitor_name = _get_default_monitor()
        if monitor_name:
            idx = _get_device_index(monitor_name)
            if idx is not None:
                device = idx
                print(f"System audio: {monitor_name}")

    if device is None:
        source_name = _get_default_source()
        if source_name:
            idx = _get_device_index(source_name)
            if idx is not None:
                device = idx
                print(f"Microphone: {source_name}")

    if device is None:
        device = "default"
        print("Using default audio input")

    # Shared state
    all_frames: list[np.ndarray] = []
    audio_queue: asyncio.Queue[bytes | None] = asyncio.Queue()
    stop_event = asyncio.Event()
    transcript_parts: list[str] = []

    chunk_samples = int(SAMPLE_RATE * CHUNK_SECONDS)

    def _audio_callback(
        indata: np.ndarray, frames: int, time_info: object, status: object
    ) -> None:
        """Sounddevice callback — converts float32 to PCM16 little-endian."""
        if status:
            log.warning("Audio status: %s", status)
        all_frames.append(indata.copy())
        pcm16 = (indata * 32767).astype("<i2")
        audio_queue.put_nowait(pcm16.tobytes())

    def _sigint_handler(_sig: int, _frame: object) -> None:
        stop_event.set()

    original_handler = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGINT, _sigint_handler)

    print(f"\nLive transcription... press Ctrl+C to stop.")
    print(f"Saving audio to: {output_path}\n")
    print("-" * 60)

    client = genai.Client(api_key=config.gemini_api_key)

    live_config = types.LiveConnectConfig(
        response_modalities=[types.Modality.AUDIO],
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
        system_instruction=(
            "You are a silent meeting transcriber. Listen to everything and do not respond with speech. Stay quiet. "
            "When speakers introduce themselves at the start of the meeting, learn their names and associate them with their voices. "
            "Use their actual names as speaker labels in the transcription instead of generic labels like 'Speaker 1'. "
            "If a speaker hasn't introduced themselves, use 'Unknown Speaker' until you can identify them."
        ),
    )

    try:
        async with client.aio.live.connect(
            model=LIVE_MODEL,
            config=live_config,
        ) as session:
            log.info("Connected to %s", LIVE_MODEL)

            # Start audio capture
            stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=1,
                device=device,
                dtype="float32",
                blocksize=chunk_samples,
                callback=_audio_callback,
            )
            stream.start()
            log.info("Audio stream started")

            async def send_audio() -> None:
                """Continuously send PCM16 audio chunks to Gemini."""
                while not stop_event.is_set():
                    try:
                        chunk = await asyncio.wait_for(audio_queue.get(), timeout=0.5)
                    except asyncio.TimeoutError:
                        continue
                    if chunk is None:
                        break
                    await session.send_realtime_input(
                        audio=types.Blob(
                            data=chunk,
                            mime_type="audio/pcm;rate=16000",
                        ),
                    )

            async def receive_transcription() -> None:
                """Receive and print transcription text as it streams in."""
                while not stop_event.is_set():
                    try:
                        async for response in session.receive():
                            if stop_event.is_set():
                                break

                            sc = response.server_content
                            if not sc:
                                continue

                            # Input transcription — what was said to the mic
                            if sc.input_transcription and sc.input_transcription.text:
                                text = sc.input_transcription.text
                                print(text, end="", flush=True)
                                transcript_parts.append(text)

                            # Output transcription — model's response text
                            if sc.output_transcription and sc.output_transcription.text:
                                text = sc.output_transcription.text
                                log.debug("Model response: %s", text)

                    except StopAsyncIteration:
                        break
                    except Exception as e:
                        if not stop_event.is_set():
                            log.error("Receive error: %s", e)
                        break

            # Run send and receive concurrently
            send_task = asyncio.create_task(send_audio())
            recv_task = asyncio.create_task(receive_transcription())

            # Wait for Ctrl+C
            await stop_event.wait()

            # Cleanup
            audio_queue.put_nowait(None)
            send_task.cancel()
            recv_task.cancel()

            stream.stop()
            stream.close()

    except Exception as e:
        log.error("Live session error: %s", e)
        raise
    finally:
        signal.signal(signal.SIGINT, original_handler)

    print("\n" + "-" * 60)

    # Save audio
    if all_frames:
        audio = np.concatenate(all_frames)
        sf.write(str(output_path), audio, SAMPLE_RATE)
        duration = len(audio) / SAMPLE_RATE
        print(f"\nRecording saved: {output_path} ({duration:.1f}s)")
    else:
        print("\nNo audio captured.")

    full_transcript = "".join(transcript_parts)
    log.info("Transcript length: %d chars", len(full_transcript))

    return {
        "audio_path": str(output_path),
        "transcript": full_transcript,
    }
