"""Stage 1 over HTTP, whichever model is behind it.

Stage 1 says the words and stage 2 makes them 겨울이's, so which model says the
words is meant to be replaceable — that separation is the reason the voice
survived swapping it. Both candidates ended up speaking the same tiny contract,
POST a sentence and receive a wav, so the client is one class and choosing a
stage 1 is choosing a URL.

Whatever was chosen by ear lives in the server, not in the request: speed, the
speaker, the disabled text normalizer. Sending those per call would let two
callers disagree about what she sounds like.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from companion.adapters.fake import AdapterUnavailableError
from companion.contracts import AudioOutput, SpeechRequest

MEDIA_TYPE = "audio/wav"
# Chatterbox is stage 1. 천우 heard it against CosyVoice and said the voice comes
# out as the Reference where CosyVoice only leans that way — and it needs no
# reference prompt, so the leaked opening that every workaround in this path was
# paying for does not happen at all. Its one fault is pace, and stage 2 fixes
# that.
#
# The other two stay reachable. MeloTTS wins every countable measure — nothing
# leaks, nothing is trimmed, it runs on the CPU, no mispronunciations — and he
# listened to a full turn and said it sounds like a machine. CosyVoice is the
# fallback that got us this far.
CHATTERBOX_URL = "http://127.0.0.1:8093"
COSYVOICE_URL = "http://127.0.0.1:8090"
MELOTTS_URL = "http://127.0.0.1:8092"
STAGE_ONE_URLS = {
    "chatterbox": CHATTERBOX_URL,
    "cosyvoice": COSYVOICE_URL,
    "melotts": MELOTTS_URL,
}


@dataclass(frozen=True)
class HttpSpeechModel:
    base_url: str = CHATTERBOX_URL
    timeout_seconds: float = 120.0

    def synthesize(self, request: SpeechRequest) -> AudioOutput:
        text = request.text.strip()
        if not text:
            raise ValueError("cannot synthesize empty text")
        http_request = Request(
            f"{self.base_url.rstrip('/')}/speak",
            data=json.dumps({"text": text}).encode("utf-8"),
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
