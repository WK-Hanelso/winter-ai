from companion.contracts import ChatRequest, ConversationMessage
from companion.context import ConversationContextBuilder
from companion.identity import CompanionIdentity
from companion.ports import ChatModel, ConversationRepository
from companion.response import CompanionResponse, ProsodyPlan


class CompanionCore:
    """Coordinates a text turn without depending on a concrete model or store."""

    def __init__(
        self,
        chat_model: ChatModel,
        conversation_repository: ConversationRepository,
        context_builder: ConversationContextBuilder | None = None,
        identity: CompanionIdentity | None = None,
    ) -> None:
        self._chat_model = chat_model
        self._conversation_repository = conversation_repository
        self._context_builder = context_builder
        self._identity = identity

    def respond_to_text(self, text: str) -> CompanionResponse:
        self._conversation_repository.append(ConversationMessage(role="user", content=text))
        messages = (ConversationMessage(role="user", content=text),)
        if self._context_builder is not None:
            messages = self._context_builder.build(self._conversation_repository.list_messages())
        if self._identity is not None:
            messages = (ConversationMessage("system", self._identity.system_message()),) + messages
        result = self._chat_model.generate(ChatRequest(prompt=text, messages=messages))
        self._conversation_repository.append(
            ConversationMessage(role="assistant", content=result.text)
        )
        return CompanionResponse(
            text=result.text,
            dialogue_act="answer",
            prosody=ProsodyPlan(),
        )
