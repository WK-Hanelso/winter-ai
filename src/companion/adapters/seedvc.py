"""Stage 2: the same words, in the Reference's voice.

The voice is built in two stages on purpose. Stage 1 is a Korean TTS and owns
the prosody — how a sentence is phrased, where it breathes. Stage 2 owns the
timbre and nothing else. Splitting them means a better Korean TTS can replace
stage 1 whole without 겨울이 sounding like someone else, and it means the two
can be judged separately, which matters because they fail differently: stage 1
sounds stiff, stage 2 sounds like the wrong person.

Only the resident path exists here. A container per conversion loads the model
for most of a minute to do work that takes a couple of seconds, and unlike
synthesis there is no debugging value in the slow path — the same script serves
both.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from companion.adapters.fake import AdapterUnavailableError
from companion.contracts import AudioOutput

DEFAULT_SERVER_URL = "http://127.0.0.1:8091"
MEDIA_TYPE = "audio/wav"


@dataclass(frozen=True)
class SeedVcVoiceConverter:
    """Asks a process that already holds the conversion model in memory.

    Which checkpoint and which reference clip decide whose voice comes out, and
    both live in the server rather than in this request. A caller that could
    pass a reference could change who is speaking by accident.
    """

    base_url: str = DEFAULT_SERVER_URL
    timeout_seconds: float = 120.0

    def convert(self, audio: AudioOutput) -> AudioOutput:
        if not audio.data:
            raise ValueError("cannot convert empty audio")
        http_request = Request(
            f"{self.base_url.rstrip('/')}/convert",
            data=audio.data,
            headers={"Content-Type": MEDIA_TYPE},
            method="POST",
        )
        try:
            with urlopen(http_request, timeout=self.timeout_seconds) as response:
                return AudioOutput(data=response.read(), media_type=MEDIA_TYPE)
        except HTTPError as error:
            detail = error.read().decode("utf-8", "replace")
            raise AdapterUnavailableError(f"변환 서버가 거절했습니다: {detail}") from error
        except URLError as error:
            raise AdapterUnavailableError(
                f"변환 서버에 닿지 못했습니다 ({self.base_url}): {error.reason}"
            ) from error
        except TimeoutError as error:
            raise AdapterUnavailableError(
                f"변환 서버가 {self.timeout_seconds:g}초 안에 답하지 않았습니다"
            ) from error
