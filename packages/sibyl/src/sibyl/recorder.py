"""Audio recorder using sounddevice + PipeWire/PulseAudio.

Records from two sources simultaneously:
  - System audio monitor (what you hear — other meeting participants)
  - Default mic (your voice — headphone mic, built-in, etc.)

Both streams are mixed into a single mono WAV file.
"""

import signal
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

import numpy as np
import sounddevice as sd
import soundfile as sf

from sibyl.config import Config


def _get_default_monitor() -> str | None:
    """Get the monitor source for the current default sink via pactl."""
    try:
        result = subprocess.run(
            ["pactl", "get-default-sink"],
            capture_output=True,
            text=True,
            check=True,
        )
        default_sink = result.stdout.strip()
        return f"{default_sink}.monitor"
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def _get_default_source() -> str | None:
    """Get the current default input source (mic) via pactl."""
    try:
        result = subprocess.run(
            ["pactl", "get-default-source"],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def _get_device_index(device_name: str) -> int | None:
    """Find the sounddevice index for a PulseAudio source name."""
    devices = sd.query_devices()
    for i, dev in enumerate(devices):
        if device_name in dev["name"]:
            return i
    return None


def _record_stream(
    device: int | str,
    sample_rate: int,
    frames: list[np.ndarray],
    stop_event: threading.Event,
) -> None:
    """Record from a single device until stop_event is set."""
    try:
        with sd.InputStream(
            samplerate=sample_rate,
            channels=1,
            device=device,
            dtype="float32",
        ) as stream:
            while not stop_event.is_set():
                data, _overflowed = stream.read(sample_rate)  # ~1 second chunks
                frames.append(data.copy())
    except Exception as e:
        print(f"Recording error on device {device}: {e}", file=sys.stderr)


def record_meeting(config: Config) -> Path:
    """Record audio from system monitor + mic until Ctrl+C.

    Both sources are recorded in parallel threads and mixed into one WAV.
    Returns the path to the saved file.
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = config.output_dir / f"meeting_{timestamp}.wav"

    # Resolve devices
    monitor_device: int | str | None = None
    mic_device: int | str | None = None

    if config.record_system_audio:
        monitor_name = _get_default_monitor()
        if monitor_name:
            idx = _get_device_index(monitor_name)
            if idx is not None:
                monitor_device = idx
                print(f"System audio: {monitor_name}")
            else:
                print(f"Monitor '{monitor_name}' not found in sounddevice.")
        else:
            print("Could not detect default sink monitor.")

    if config.record_mic:
        source_name = _get_default_source()
        if source_name:
            idx = _get_device_index(source_name)
            if idx is not None:
                mic_device = idx
                print(f"Microphone:   {source_name}")
            else:
                # Fall back to sounddevice default
                mic_device = "default"
                print("Microphone:   default input")
        else:
            mic_device = "default"
            print("Microphone:   default input")

    if monitor_device is None and mic_device is None:
        print("No audio devices found. Check PipeWire/PulseAudio is running.")
        sys.exit(1)

    # Collect frames from each source in separate lists
    monitor_frames: list[np.ndarray] = []
    mic_frames: list[np.ndarray] = []
    stop_event = threading.Event()

    def _sigint_handler(_sig: int, _frame: object) -> None:
        stop_event.set()

    original_handler = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGINT, _sigint_handler)

    # Start recording threads
    threads: list[threading.Thread] = []

    if monitor_device is not None:
        t = threading.Thread(
            target=_record_stream,
            args=(monitor_device, config.sample_rate, monitor_frames, stop_event),
            daemon=True,
        )
        threads.append(t)

    if mic_device is not None:
        t = threading.Thread(
            target=_record_stream,
            args=(mic_device, config.sample_rate, mic_frames, stop_event),
            daemon=True,
        )
        threads.append(t)

    print(f"\nRecording... press Ctrl+C to stop.\nSaving to: {output_path}\n")

    for t in threads:
        t.start()

    # Wait for Ctrl+C
    try:
        for t in threads:
            while t.is_alive() and not stop_event.is_set():
                t.join(timeout=0.5)
    finally:
        stop_event.set()
        for t in threads:
            t.join(timeout=2)
        signal.signal(signal.SIGINT, original_handler)

    # Mix sources
    monitor_audio = np.concatenate(monitor_frames) if monitor_frames else None
    mic_audio = np.concatenate(mic_frames) if mic_frames else None

    if monitor_audio is None and mic_audio is None:
        print("No audio captured.")
        sys.exit(1)

    if monitor_audio is not None and mic_audio is not None:
        # Trim to the shorter length and mix
        min_len = min(len(monitor_audio), len(mic_audio))
        audio = monitor_audio[:min_len] + mic_audio[:min_len]
        # Normalize to prevent clipping
        peak = np.abs(audio).max()
        if peak > 0:
            audio = audio / peak * 0.95
        print(f"Mixed system audio + mic ({min_len / config.sample_rate:.1f}s each)")
    elif monitor_audio is not None:
        audio = monitor_audio
    else:
        # mic_audio is guaranteed non-None here (we exit above if both are None)
        assert mic_audio is not None
        audio = mic_audio

    sf.write(str(output_path), audio, config.sample_rate)

    duration = len(audio) / config.sample_rate
    print(f"\nRecording saved: {output_path} ({duration:.1f}s)")
    return output_path
