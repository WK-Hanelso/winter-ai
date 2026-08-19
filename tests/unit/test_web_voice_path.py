import base64

from companion.contracts import AudioInput, AudioOutput, SpeechRequest, Transcript
from companion.web import Companion


class OneSentenceCore:
    def respond_to_text_streaming(
        self, text: str, on_sentence: object, on_status: object = None
    ) -> None:
        assert text == "안녕"
        on_sentence("응, 안녕.")  # type: ignore[operator]


class DirectVoice:
    def synthesize(self, request: SpeechRequest) -> AudioOutput:
        assert request.text == "응, 안녕."
        return AudioOutput(b"direct-wav", "audio/wav")


class UnusedStt:
    def transcribe(self, audio: AudioInput) -> Transcript:
        return Transcript("안녕")


def test_direct_voice_path_emits_stage_one_without_conversion() -> None:
    events: list[tuple[str, dict[str, str | float]]] = []
    companion = Companion(  # type: ignore[arg-type]
        OneSentenceCore(), DirectVoice(), None, UnusedStt()
    )

    companion.answer("안녕", lambda event, payload: events.append((event, payload)))

    audio = next(payload for event, payload in events if event == "audio")
    assert base64.b64decode(str(audio["wav"])) == b"direct-wav"
