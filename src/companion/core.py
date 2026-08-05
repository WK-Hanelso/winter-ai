from companion.contracts import ChatRequest, ConversationMessage
from companion.ports import ChatModel, ConversationRepository
from companion.response import CompanionResponse, ProsodyPlan


class CompanionCore:
    """Coordinates a text turn without depending on a concrete model or store."""

    def __init__(
        self, chat_model: ChatModel, conversation_repository: ConversationRepository
    ) -> None:
        self._chat_model = chat_model
        self._conversation_repository = conversation_repository

    def respond_to_text(self, text: str) -> CompanionResponse:
        self._conversation_repository.append(ConversationMessage(role="user", content=text))
        result = self._chat_model.generate(ChatRequest(prompt=text))
        self._conversation_repository.append(
            ConversationMessage(role="assistant", content=result.text)
        )
        return CompanionResponse(
            text=result.text,
            dialogue_act="answer",
            prosody=ProsodyPlan(),
        )
