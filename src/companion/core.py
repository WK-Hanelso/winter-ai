from companion.contracts import ChatRequest, ConversationMessage
from companion.context import ConversationContextBuilder
from companion.identity import CompanionIdentity
from companion.memory import ActiveMemoryRetriever, extract_explicit_memory_content, memory_context
from companion.ports import ChatModel, ConversationRepository, MemoryCandidateRepository
from companion.response import CompanionResponse, ProsodyPlan


class CompanionCore:
    """Coordinates a text turn without depending on a concrete model or store."""

    def __init__(
        self,
        chat_model: ChatModel,
        conversation_repository: ConversationRepository,
        context_builder: ConversationContextBuilder | None = None,
        identity: CompanionIdentity | None = None,
        memory_retriever: ActiveMemoryRetriever | None = None,
        memory_repository: MemoryCandidateRepository | None = None,
    ) -> None:
        self._chat_model = chat_model
        self._conversation_repository = conversation_repository
        self._context_builder = context_builder
        self._identity = identity
        self._memory_retriever = memory_retriever
        self._memory_repository = memory_repository

    def respond_to_text(self, text: str) -> CompanionResponse:
        self._conversation_repository.append(ConversationMessage(role="user", content=text))
        candidate_ids: tuple[str, ...] = ()
        candidate_content = extract_explicit_memory_content(text)
        if candidate_content is not None and self._memory_repository is not None:
            candidate = self._memory_repository.add_candidate(
                kind="semantic", content=candidate_content
            )
            candidate_ids = (candidate.id,)
        messages = (ConversationMessage(role="user", content=text),)
        if self._context_builder is not None:
            messages = self._context_builder.build(self._conversation_repository.list_messages())
        if self._identity is not None:
            messages = (ConversationMessage("system", self._identity.system_message()),) + messages
        if self._memory_retriever is not None:
            selected = self._memory_retriever.retrieve(text)
            if selected:
                messages = (ConversationMessage("system", memory_context(selected)),) + messages
        result = self._chat_model.generate(ChatRequest(prompt=text, messages=messages))
        self._conversation_repository.append(
            ConversationMessage(role="assistant", content=result.text)
        )
        return CompanionResponse(
            text=result.text,
            dialogue_act="answer",
            prosody=ProsodyPlan(),
            memory_candidate_ids=candidate_ids,
        )
