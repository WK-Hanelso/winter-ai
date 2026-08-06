from dataclasses import dataclass


@dataclass(frozen=True)
class ProsodyPlan:
    emotion: str = "neutral"
    pace: float = 1.0
    energy: float = 1.0
    pitch_offset: float = 0.0


@dataclass(frozen=True)
class CompanionResponse:
    text: str
    dialogue_act: str
    prosody: ProsodyPlan
    memory_candidate_ids: tuple[str, ...] = ()
