"""Speech output through the pinned MeloTTS image.

This runs on the Host, not inside the dev container: synthesis happens in its
own pinned image (`Dockerfile.melotts-probe`, ~10 GiB) and the dev container has
no Docker socket. So the voice path is a Host process that drives two
containers — the model server over HTTP, the synthesiser over `docker run`.

The image is used as-is rather than rebuilt for production. It is pinned to a
MeloTTS commit and a lock file, so it is reproducible; the only thing wrong with
it is its name. Renaming it means a 10 GiB rebuild for no behaviour change.

This voice is not the Reference voice. MeloTTS ships one Korean speaker and
cannot clone. Replacing it with a cloned voice is Order 7, and this adapter
exists partly so that swap is a one-class change.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
import tempfile

from companion.adapters.fake import AdapterUnavailableError
from companion.contracts import AudioOutput, SpeechRequest

IMAGE = "winter-ai:melotts-probe"
# MeloTTS writes 44.1 kHz mono 16-bit PCM, measured rather than assumed.
# Recorded here because the Reference clips are 16 kHz: anything comparing the
# two has to resample first.
MEDIA_TYPE = "audio/wav"
SAMPLE_RATE = 44_100
# One mount serves as both the model cache and the container's HOME. The image
# downloads ~650 MiB on first use and reuses it after.
CACHE_MOUNT = "/cache"


@dataclass(frozen=True)
class MeloTtsSpeechModel:
    """Synthesizes Korean speech by running one pinned container per request.

    A container per utterance costs a few seconds of model load. That is the
    price of not holding 10 GiB resident between turns, and it is the reason
    this is a baseline rather than the real-time path the roadmap wants.
    """

    cache_dir: Path
    timeout_seconds: float = 300.0
    user: str | None = None

    def synthesize(self, request: SpeechRequest) -> AudioOutput:
        text = request.text.strip()
        if not text:
            raise ValueError("cannot synthesize empty text")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        # A private temporary directory rather than a shared output folder: two
        # turns synthesising at once must not collide on a filename.
        with tempfile.TemporaryDirectory(prefix="winter-tts-") as staging:
            output = Path(staging) / "speech.wav"
            completed = subprocess.run(
                self.command(output, text, request.pace),
                capture_output=True,
                timeout=self.timeout_seconds,
                check=False,
            )
            if completed.returncode != 0 or not output.exists():
                # MeloTTS is noisy on stderr even when it succeeds, so the tail
                # is only surfaced when the run actually failed.
                detail = completed.stderr.decode("utf-8", "replace").strip().splitlines()
                raise AdapterUnavailableError(
                    f"MeloTTS container failed ({completed.returncode}): "
                    f"{detail[-1] if detail else 'no output'}"
                )
            return AudioOutput(data=output.read_bytes(), media_type=MEDIA_TYPE)

    def command(self, output: Path, text: str, pace: float) -> list[str]:
        """Built separately so the command can be asserted without Docker."""
        command = ["docker", "run", "--rm"]
        if self.user:
            command += ["--user", self.user]
        command += [
            "-v", f"{self.cache_dir.resolve()}:{CACHE_MOUNT}:rw",
            "-v", f"{output.parent.resolve()}:/output:rw",
            # Every cache the stack writes to, pointed at the one writable
            # mount. The image bakes its caches under /root, which a `--user`
            # run cannot even traverse; without these, numba falls back to
            # caching beside librosa's own source and the run dies with
            # "no locator available".
            "-e", f"HOME={CACHE_MOUNT}",
            "-e", f"HF_HOME={CACHE_MOUNT}/huggingface",
            "-e", f"NUMBA_CACHE_DIR={CACHE_MOUNT}/numba",
            "-e", f"MPLCONFIGDIR={CACHE_MOUNT}/matplotlib",
            IMAGE,
            "--text", text,
            "--output-path", f"/output/{output.name}",
            "--speed", str(pace),
        ]
        return command
