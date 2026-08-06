"""Concrete runtime adapters for companion ports."""
from companion.adapters.llama_cpp import LlamaCppHttpChatModel
from companion.adapters.sqlite_repository import SqliteConversationRepository
from companion.adapters.voice_http import MeloTtsHttpTextToSpeech, WhisperCppHttpSpeechToText

__all__ = [
    "LlamaCppHttpChatModel",
    "MeloTtsHttpTextToSpeech",
    "SqliteConversationRepository",
    "WhisperCppHttpSpeechToText",
]
