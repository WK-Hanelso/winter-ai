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


@dataclass(frozen=True)
class CompanionResponse:
    text: str
    dialogue_act: str
    prosody: ProsodyPlan
    memory_candidate_ids: tuple[str, ...] = ()
    verbal_style: VerbalStylePlan = VerbalStylePlan()
