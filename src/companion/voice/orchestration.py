from companion.contracts import AudioInput, AudioOutput, SpeechRequest
from companion.core import CompanionCore
from companion.ports import AudioPlayer, AudioRecorder, SpeechToText, TextToSpeech


class VoiceOrchestrator:
    def __init__(self, core: CompanionCore, stt: SpeechToText, tts: TextToSpeech) -> None:
        self._core = core
        self._stt = stt
        self._tts = tts

    def handle_audio(self, audio: AudioInput) -> AudioOutput:
        transcript = self._stt.transcribe(audio)
        response = self._core.respond_to_text(transcript.text)
        return self._tts.synthesize(SpeechRequest(text=response.text, emotion=response.prosody.emotion))

    def push_to_talk(self, recorder: AudioRecorder, player: AudioPlayer) -> None:
        recorder.start()
        audio = recorder.stop()
        player.play(self.handle_audio(audio))
