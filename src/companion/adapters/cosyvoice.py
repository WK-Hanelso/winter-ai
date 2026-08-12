"""Speech output in the Reference's voice.

Two ways in. ``CosyVoiceServerSpeechModel`` posts to a process that already has
the model loaded; ``CosyVoiceSpeechModel`` starts a container per utterance.
The difference is not small:

    container per utterance    49 s for a five-second sentence
    resident server             3-6 s, generating at about real time

Loading the model is 43 of those 49 seconds, and synthesis on a GPU runs at
roughly 1.0x real time against 4.7-7.8x on a CPU. The per-utterance path stays
because it needs nothing running first, which makes it the honest fallback and
the easier thing to debug.

Original notes on the container path follow.

Speech output in the Reference's voice, through the pinned CosyVoice image.

Replaces MeloTTS, which could only ever be one fixed Korean speaker. This one
speaks in the voice the flow model was fine-tuned on, conditioned by a reference
clip and a speaker vector averaged over twelve minutes of her speech.

Runs on the Host like the MeloTTS adapter and for the same reason: synthesis
happens in its own image and the dev container has no Docker socket.

The settings are not defaults — each one was chosen by listening to the
alternatives, and `docs/voice-cloning-plan.md` records what the alternatives
sounded like. Changing one silently changes the voice.

Slow, and knowingly so. A container per utterance spends most of a minute
loading a model that then synthesizes for twenty seconds. That is the price of
not holding several gigabytes resident between turns, and it is why this is
version zero rather than something to talk to.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
import tempfile
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from companion.adapters.fake import AdapterUnavailableError
from companion.contracts import AudioOutput, SpeechRequest

IMAGE = "winter-ai:cosyvoice"
# CosyVoice 3 writes 24 kHz mono. Recorded because the Reference clips are
# 48 kHz: anything comparing the two has to resample first.
MEDIA_TYPE = "audio/wav"
SAMPLE_RATE = 24_000
RUNNER = "/probe/cosyvoice_synthesize_trained.py"
# Host loopback, published by compose.voice-server.yaml.
DEFAULT_SERVER_URL = "http://127.0.0.1:8090"
# Faster than the model's default. 천우 asked for "아주 살짝 빨랐으면".
DEFAULT_PACE = 1.12


@dataclass(frozen=True)
class CosyVoiceServerSpeechModel:
    """Asks a process that already holds the voice in memory.

    The settings that were chosen by ear live in the server rather than here,
    because they belong to the loaded model: speed, the disabled text
    normalizer, the empty instruction. Sending them per request would let two
    callers disagree about what 겨울이 sounds like.
    """

    base_url: str = DEFAULT_SERVER_URL
    timeout_seconds: float = 120.0

    def synthesize(self, request: SpeechRequest) -> AudioOutput:
        text = request.text.strip()
        if not text:
            raise ValueError("cannot synthesize empty text")
        payload = json.dumps({"text": text}).encode("utf-8")
        http_request = Request(
            f"{self.base_url.rstrip('/')}/speak",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(http_request, timeout=self.timeout_seconds) as response:
                return AudioOutput(data=response.read(), media_type=MEDIA_TYPE)
        except HTTPError as error:
            detail = error.read().decode("utf-8", "replace")
            raise AdapterUnavailableError(f"목소리 서버가 거절했습니다: {detail}") from error
        except URLError as error:
            raise AdapterUnavailableError(
                f"목소리 서버에 닿지 못했습니다 ({self.base_url}): {error.reason}"
            ) from error
        except TimeoutError as error:
            raise AdapterUnavailableError(
                f"목소리 서버가 {self.timeout_seconds:g}초 안에 답하지 않았습니다"
            ) from error


@dataclass(frozen=True)
class CosyVoiceSpeechModel:
    """Synthesizes Korean speech in the Reference's voice, one container a turn.

    ``speaker`` is the saved profile rather than the clips it was built from:
    building it means embedding every clip and costs about fourteen seconds,
    and it does not change between utterances.
    """

    flow_checkpoint: Path
    speaker: Path
    runner_dir: Path
    timeout_seconds: float = 600.0
    user: str | None = None
    pace: float = DEFAULT_PACE

    def synthesize(self, request: SpeechRequest) -> AudioOutput:
        text = request.text.strip()
        if not text:
            raise ValueError("cannot synthesize empty text")
        for path in (self.flow_checkpoint, self.speaker):
            if not path.exists():
                raise AdapterUnavailableError(f"필요한 파일이 없습니다: {path}")

        # A private directory per call: two turns synthesising at once must not
        # collide on a filename.
        with tempfile.TemporaryDirectory(prefix="winter-cosyvoice-") as staging:
            sentences = Path(staging) / "text.txt"
            sentences.write_text(f"{text}\n", encoding="utf-8")
            output = Path(staging) / "speech-01.wav"
            completed = subprocess.run(
                self.command(Path(staging), sentences),
                capture_output=True,
                timeout=self.timeout_seconds,
                check=False,
            )
            if completed.returncode != 0 or not output.exists():
                detail = completed.stderr.decode("utf-8", "replace").strip().splitlines()
                raise AdapterUnavailableError(
                    f"CosyVoice container failed ({completed.returncode}): "
                    f"{detail[-1] if detail else 'no output'}"
                )
            return AudioOutput(data=output.read_bytes(), media_type=MEDIA_TYPE)

    def command(self, staging: Path, sentences: Path) -> list[str]:
        """Built separately so the command can be asserted without Docker."""
        command = ["docker", "run", "--rm"]
        if self.user:
            command += ["--user", self.user]
        command += [
            "-v", f"{self.flow_checkpoint.parent.resolve()}:/flow:ro",
            "-v", f"{self.speaker.parent.resolve()}:/speaker:ro",
            "-v", f"{self.runner_dir.resolve()}:/probe:ro",
            "-v", f"{staging.resolve()}:/work:rw",
            "-w", "/opt/cosyvoice",
            "--entrypoint", "python",
            IMAGE,
            RUNNER,
            "--flow-checkpoint", f"/flow/{self.flow_checkpoint.name}",
            "--load-speaker", f"/speaker/{self.speaker.name}",
            "--sentences-file", f"/work/{sentences.name}",
            "--output-dir", "/work",
            "--output-prefix", "speech",
            "--speed", str(self.pace),
            # Both chosen by ear. The normalizer split "지금은" into "지 금은",
            # and any instruction before the separator is read immediately
            # before speaking.
            "--no-text-frontend",
            "--instruction", "",
        ]
        return command
