import json

import pytest

from companion.adapters.fake import AdapterUnavailableError, InMemoryConversationRepository
from companion.adapters.llama_cpp import LlamaCppHttpChatModel, _extract_text, _stream_piece
from companion.application.bootstrap import TextRuntimeConfig, build_text_runtime
from companion.contracts import ChatRequest, ChatResult, ConversationMessage
from companion.core import CompanionCore


@pytest.mark.parametrize("reason", ["length", "content_filter"])
def test_incomplete_http_reply_is_not_a_completed_answer(reason):
    body = json.dumps({"choices": [{"message": {"content": "unfinished"},
                                    "finish_reason": reason}]}).encode()
    with pytest.raises(AdapterUnavailableError, match="incomplete"):
        _extract_text(body, "http://local")


def test_stream_length_failure_does_not_save_or_deliver_partial_reply(monkeypatch):
    def interrupted(_self, _request):
        yield "답은 아직"
        _stream_piece(b'data: {"choices":[{"delta":{},"finish_reason":"length"}]}')

    monkeypatch.setattr(LlamaCppHttpChatModel, "generate_stream", interrupted)
    repository = InMemoryConversationRepository()
    core = CompanionCore(LlamaCppHttpChatModel("http://unused"), repository)
    delivered = []
    with pytest.raises(AdapterUnavailableError, match="incomplete"):
        core.respond_to_text_streaming("질문", on_sentence=delivered.append)
    assert not delivered
    assert repository.list_messages() == (ConversationMessage("user", "질문"),)


def test_companion_reply_budget_is_separate_from_short_style_guidance():
    requests = []

    class Model:
        def generate(self, request):
            requests.append(request)
            return ChatResult("안녕!")

    runtime = build_text_runtime(TextRuntimeConfig(dialogue_policy="companion"), chat_model=Model())
    runtime.core.respond_to_text("안녕")
    assert requests[0].max_tokens == 640
    assert runtime.close()


def test_companion_sampling_does_not_change_legacy_sampling():
    from companion.application.bootstrap import build_chat_model

    legacy = build_chat_model(TextRuntimeConfig())
    draft = build_chat_model(TextRuntimeConfig(dialogue_policy="companion"))
    original = json.loads(legacy._payload(ChatRequest("hello"), stream=False))
    assert original["presence_penalty"] == 1.0
    payload = json.loads(draft._payload(ChatRequest("hello"), stream=False))
    assert payload["presence_penalty"] == 0.0
    assert payload["min_p"] == 0.0


@pytest.mark.parametrize("options", [{"presence_penalty": 3}, {"min_p": -1}])
def test_invalid_sampler_configuration_fails_before_request(options):
    with pytest.raises(ValueError):
        LlamaCppHttpChatModel("http://unused", **options)
