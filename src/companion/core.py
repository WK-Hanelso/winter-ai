from companion.context import ConversationContextBuilder
from companion.contracts import ChatRequest, ConversationMessage
from companion.dialogue_act import classify as classify_dialogue_act
from companion.grounding import GroundingPolicy
from companion.identity import CompanionIdentity
from companion.memory import ActiveMemoryRetriever, extract_explicit_memory_content, memory_context
from companion.ports import ChatModel, ConversationRepository, MemoryCandidateRepository
from companion.response import CompanionResponse
from companion.verbal_style import VerbalStylePlanner
from companion.voice_profile import ProsodyPlanner

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
        grounding_policy: GroundingPolicy | None = None,
    ) -> None:
        self._chat_model = chat_model
        self._conversation_repository = conversation_repository
        self._context_builder = context_builder
        self._identity = identity
        self._memory_retriever = memory_retriever
        self._memory_repository = memory_repository
        self._prosody_planner = prosody_planner or ProsodyPlanner()
        self._verbal_style_planner = verbal_style_planner or VerbalStylePlanner()
        self._grounding_policy = grounding_policy or GroundingPolicy()

    def respond_to_text(self, text: str) -> CompanionResponse:
        self._conversation_repository.append(ConversationMessage(role="user", content=text))
        candidate_ids: tuple[str, ...] = ()
        candidate_content = extract_explicit_memory_content(text)
        if candidate_content is not None and self._memory_repository is not None:
            candidate = self._memory_repository.add_candidate(
                kind="semantic", content=candidate_content
            )
            candidate_ids = (candidate.id,)
        # What kind of turn this is decides how long the answer may be. Ordinary
        # conversation stays short; being asked to explain something earns room.
        dialogue_act = classify_dialogue_act(text, has_memory_candidate=bool(candidate_ids))
        messages: tuple[ConversationMessage, ...] = (
            ConversationMessage(role="user", content=text),
        )
        if self._context_builder is not None:
            messages = self._context_builder.build(self._conversation_repository.list_messages())
        # Built in one place and in a deliberate order. Repeated prepends put the
        # last-added block first, which pushed identity behind style and memory.
        # Identity leads because it frames everything else; style sits closest to
        # the turn being generated, where a register instruction is least likely
        # to be dropped.
        system_messages: list[ConversationMessage] = []
        if self._identity is not None:
            system_messages.append(
                ConversationMessage("system", self._identity.system_message())
            )
        if self._memory_retriever is not None:
            selected = self._memory_retriever.retrieve(text)
            if selected:
                system_messages.append(
                    ConversationMessage("system", memory_context(selected))
                )
        turn_style = self._verbal_style_planner.plan_turn(dialogue_act)
        if turn_style.instruction is not None:
            system_messages.append(ConversationMessage("system", turn_style.instruction))
        # Grounding goes last, closest to the generated turn. It is the one
        # constraint that must survive when the others compete for attention:
        # a fluent invented answer is worse than an awkward honest one.
        grounding_instruction = self._grounding_policy.instruction()
        if grounding_instruction is not None:
            system_messages.append(ConversationMessage("system", grounding_instruction))
        messages = tuple(system_messages) + messages
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
            verbal_style=turn_style.plan,
        )
