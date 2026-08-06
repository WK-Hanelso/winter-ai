"""Manual push-to-talk CLI using the shared local CompanionCore."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from companion.adapters.fake import AdapterUnavailableError
from companion.adapters.pulse_audio import PulseAudioPlayer, PulseAudioRecorder
from companion.adapters.voice_http import MeloTtsHttpTextToSpeech, WhisperCppHttpSpeechToText
from companion.cli.__main__ import build_chat_model, build_conversation_repository, _positive_int
from companion.context import ConversationContextBuilder
from companion.contracts import AudioInput
from companion.core import CompanionCore
from companion.voice.orchestration import VoiceOrchestrator


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=("local",), default="local")
    parser.add_argument("--model-url", default="http://llm:8080")
    parser.add_argument("--stt-url", default="http://stt:8081")
    parser.add_argument("--tts-url", default="http://tts:8082")
    parser.add_argument("--conversation-db", type=Path, required=True)
    parser.add_argument("--context-max-messages", type=_positive_int, default=12)
    parser.add_argument("--context-max-characters", type=_positive_int, default=4000)
    parser.add_argument("--input-audio", type=Path, help="WAV file for a no-microphone smoke test")
    parser.add_argument("--no-playback", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        repository = build_conversation_repository(args)
        core = CompanionCore(
            build_chat_model(args), repository,
            ConversationContextBuilder(max_messages=args.context_max_messages, max_characters=args.context_max_characters),
        )
        voice = VoiceOrchestrator(core, WhisperCppHttpSpeechToText(args.stt_url), MeloTtsHttpTextToSpeech(args.tts_url))
        if args.input_audio:
            audio = AudioInput(args.input_audio.read_bytes(), "audio/wav")
        else:
            recorder = PulseAudioRecorder()
            input("Enter를 누르면 녹음을 시작합니다.")
            recorder.start()
            input("말한 뒤 Enter를 누르면 녹음을 끝냅니다.")
            audio = recorder.stop()
        output = voice.handle_audio(audio)
        if not args.no_playback:
            PulseAudioPlayer().play(output)
        print("Voice turn completed.")
        return 0
    except (AdapterUnavailableError, OSError) as error:
        print(f"Voice unavailable: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
