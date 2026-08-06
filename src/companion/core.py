from companion.contracts import ChatRequest, ConversationMessage
from companion.context import ConversationContextBuilder
from companion.identity import CompanionIdentity
from companion.memory import ActiveMemoryRetriever, extract_explicit_memory_content, memory_context
from companion.ports import ChatModel, ConversationRepository, MemoryCandidateRepository
from companion.response import CompanionResponse, ProsodyPlan
from companion.voice_profile import ProsodyPlanner
from companion.verbal_style import VerbalStylePlanner

_MEMORY_CANDIDATE_NOTICE = "기억 후보로 저장했어. 검토 후 활성화할 수 있어."


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
        prosody_planner: ProsodyPlanner | None = None,
        verbal_style_planner: VerbalStylePlanner | None = None,
    ) -> None:
        self._chat_model = chat_model
        self._conversation_repository = conversation_repository
        self._context_builder = context_builder
        self._identity = identity
        self._memory_retriever = memory_retriever
        self._memory_repository = memory_repository
        self._prosody_planner = prosody_planner or ProsodyPlanner()
        self._verbal_style_planner = verbal_style_planner or VerbalStylePlanner()

    def respond_to_text(self, text: str) -> CompanionResponse:
        self._conversation_repository.append(ConversationMessage(role="user", content=text))
        candidate_ids: tuple[str, ...] = ()
        candidate_content = extract_explicit_memory_content(text)
        if candidate_content is not None and self._memory_repository is not None:
            candidate = self._memory_repository.add_candidate(
                kind="semantic", content=candidate_content
            )
            candidate_ids = (candidate.id,)
        dialogue_act = "memory_candidate" if candidate_ids else "answer"
        messages = (ConversationMessage(role="user", content=text),)
        if self._context_builder is not None:
            messages = self._context_builder.build(self._conversation_repository.list_messages())
        if self._identity is not None:
            messages = (ConversationMessage("system", self._identity.system_message()),) + messages
        if self._memory_retriever is not None:
            selected = self._memory_retriever.retrieve(text)
            if selected:
                messages = (ConversationMessage("system", memory_context(selected)),) + messages
        style_instruction = self._verbal_style_planner.instruction(dialogue_act)
        if style_instruction is not None:
            messages = (ConversationMessage("system", style_instruction),) + messages
        result = self._chat_model.generate(ChatRequest(prompt=text, messages=messages))
        response_text = result.text
        if candidate_ids:
            response_text = f"{response_text}\n{_MEMORY_CANDIDATE_NOTICE}"
        self._conversation_repository.append(
            ConversationMessage(role="assistant", content=response_text)
        )
        return CompanionResponse(
            text=response_text,
            dialogue_act=dialogue_act,
            prosody=self._prosody_planner.plan(dialogue_act),
            memory_candidate_ids=candidate_ids,
            verbal_style=self._verbal_style_planner.plan(dialogue_act),
        )
