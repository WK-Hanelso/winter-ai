from companion.contracts import ChatRequest, ChatResult
from companion.ports import ChatModel
from companion.response import CompanionResponse, ProsodyPlan


class ExampleChatModel:
    def generate(self, request: ChatRequest) -> ChatResult:
        return ChatResult(text=request.prompt)


def test_chat_model_contract_is_usable() -> None:
    model: ChatModel = ExampleChatModel()
    assert model.generate(ChatRequest(prompt="안녕")).text == "안녕"


def test_companion_response_keeps_prosody_separate_from_text() -> None:
    response = CompanionResponse(
        text="반가워요.", dialogue_act="greeting", prosody=ProsodyPlan(emotion="warm")
    )
    assert response.prosody.emotion == "warm"
