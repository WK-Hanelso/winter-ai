from collections.abc import Callable
from dataclasses import dataclass

from companion.context import ConversationContextBuilder
from companion.contracts import ChatRequest, ConversationMessage
from companion.dialogue_act import classify as classify_dialogue_act
from companion.grounding import GroundingPolicy
from companion.identity import CompanionIdentity
from companion.memory import ActiveMemoryRetriever, extract_explicit_memory_content, memory_context
from companion.ports import ChatModel, ConversationRepository, MemoryCandidateRepository
from companion.response import CompanionResponse, VerbalStylePlan
from companion.speech_segments import split_sentences
from companion.verbal_style import VerbalStylePlanner
from companion.voice_profile import ProsodyPlanner

_MEMORY_CANDIDATE_NOTICE = "기억 후보로 저장했어. 검토 후 활성화할 수 있어."


@dataclass(frozen=True)
class _Turn:
    """Everything decided before the model is asked, kept for after it answers.

    Streaming split one method into two, and these are what the second half
    needs: deciding them twice would let a turn be planned one way and reported
    another.
    """

    request: ChatRequest
    dialogue_act: str
    candidate_ids: tuple[str, ...]
    style: VerbalStylePlan


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
        turn = self._prepare(text)
        result = self._chat_model.generate(turn.request)
        return self._finish(turn, result.text)

    def respond_to_text_streaming(
        self, text: str, on_sentence: Callable[[str], None]
    ) -> CompanionResponse:
        """Answer, handing each finished sentence over as it is written.

        The voice path cannot start on a sentence that does not exist yet, and
        waiting for the whole answer costs two and a half to four seconds before
        anything is said. ``on_sentence`` is called with each completed
        sentence; the response returned at the end is the same one the
        non-streaming path returns.

        A model without a streaming path is not an error — it answers all at
        once and every sentence is handed over then.
        """
        turn = self._prepare(text)
        stream = getattr(self._chat_model, "generate_stream", None)
        if stream is None:
            answer = self._chat_model.generate(turn.request).text
            for sentence in split_sentences(answer):
                on_sentence(sentence)
            return self._finish(turn, answer)
        written: list[str] = []
        handed = 0
        for piece in stream(turn.request):
            written.append(piece)
            # Only sentences that are certainly finished are handed over: the
            # last piece of a partial answer usually is not one, and saying half
            # a sentence cannot be taken back.
            sentences = split_sentences("".join(written))
            while len(sentences) - 1 > handed:
                on_sentence(sentences[handed])
                handed += 1
        answer = "".join(written)
        for sentence in split_sentences(answer)[handed:]:
            on_sentence(sentence)
        return self._finish(turn, answer)

    def _prepare(self, text: str) -> _Turn:
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
        return _Turn(
            request=ChatRequest(prompt=text, messages=messages),
            dialogue_act=dialogue_act,
            candidate_ids=candidate_ids,
            style=turn_style.plan,
        )

    def _finish(self, turn: _Turn, answer: str) -> CompanionResponse:
        response_text = answer
        if turn.candidate_ids:
            response_text = f"{response_text}\n{_MEMORY_CANDIDATE_NOTICE}"
        self._conversation_repository.append(
            ConversationMessage(role="assistant", content=response_text)
        )
        return CompanionResponse(
            text=response_text,
            dialogue_act=turn.dialogue_act,
            prosody=self._prosody_planner.plan(turn.dialogue_act),
            memory_candidate_ids=turn.candidate_ids,
            verbal_style=turn.style,
        )
