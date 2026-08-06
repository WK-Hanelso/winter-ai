"""Explicit PulseAudio command adapters for manual push-to-talk testing."""

from __future__ import annotations

import subprocess

from companion.adapters.fake import AdapterUnavailableError
from companion.contracts import AudioInput, AudioOutput


class PulseAudioRecorder:
    def __init__(self) -> None:
        self._process: subprocess.Popen[bytes] | None = None

    def start(self) -> None:
        if self._process is not None:
            raise AdapterUnavailableError("PulseAudio recorder is already running")
        try:
            self._process = subprocess.Popen(
                ["parecord", "--file-format=wav", "--rate=16000", "--channels=1", "-"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except OSError as error:
            raise AdapterUnavailableError(f"could not start PulseAudio recording: {error}") from error

    def stop(self) -> AudioInput:
        if self._process is None:
            raise AdapterUnavailableError("PulseAudio recorder was not started")
        process, self._process = self._process, None
        process.terminate()
        data, stderr = process.communicate()
        if process.returncode not in (0, -15) or not data:
            detail = stderr.decode("utf-8", errors="replace").strip()
            raise AdapterUnavailableError(f"PulseAudio recording failed: {detail or process.returncode}")
        return AudioInput(data=data, media_type="audio/wav")


class PulseAudioPlayer:
    def play(self, audio: AudioOutput) -> None:
        if audio.media_type != "audio/wav":
            raise AdapterUnavailableError(f"PulseAudio player cannot play {audio.media_type}")
        try:
            completed = subprocess.run(
                ["paplay", "--file-format=wav", "-"],
                input=audio.data,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        except OSError as error:
            raise AdapterUnavailableError(f"could not start PulseAudio playback: {error}") from error
        if completed.returncode:
            detail = completed.stderr.decode("utf-8", errors="replace").strip()
            raise AdapterUnavailableError(f"PulseAudio playback failed: {detail or completed.returncode}")
