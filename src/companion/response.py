from dataclasses import dataclass


@dataclass(frozen=True)
class ProsodyPlan:
    emotion: str = "neutral"
    pace: float = 1.0
    energy: float = 1.0
    pitch_offset: float = 0.0


@dataclass(frozen=True)
class VerbalStylePlan:
    tone: str = "neutral"
    directness: float = 0.7
    sentence_length: str = "normal"
    # Plain vs polite speech is a visible part of who the companion sounds like,
    # so it travels with the plan rather than living only in the prompt.
    register: str = "polite_casual"
    max_sentences: int = 2
    max_words_per_sentence: int | None = None


@dataclass(frozen=True)
class CompanionResponse:
    text: str
    dialogue_act: str
    prosody: ProsodyPlan
    memory_candidate_ids: tuple[str, ...] = ()
    automatic_memory_ids: tuple[str, ...] = ()
    verbal_style: VerbalStylePlan = VerbalStylePlan()
    dialogue_behavior: str = "answer"
    opened_loop_ids: tuple[str, ...] = ()
    recalled_loop_ids: tuple[str, ...] = ()
    # The observer is deliberately non-operative. This id only makes a raw turn
    # traceable to its later Shadow analysis; no analysis field affects text.
    shadow_turn_id: str | None = None
