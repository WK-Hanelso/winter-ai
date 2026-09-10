"""Read-only, per-turn view of existing character state; not a new store."""

from dataclasses import dataclass

from companion.beliefs import Belief
from companion.capabilities import SelfModelSnapshot
from companion.day_reflection import WinterDayReflection
from companion.identity import CompanionIdentity
from companion.improvements import ImprovementSnapshot
from companion.open_loops import OpenLoop


@dataclass(frozen=True)
class CharacterSnapshot:
    """Resolved domain values with their existing versions and evidence intact.

    Core selects these values using existing activation/retrieval policies.
    No new relationship inference, storage writes or permission grants occur here.
    This is a per-turn value view, not an atomic cross-database transaction.
    """

    identity: CompanionIdentity | None = None
    self_model: SelfModelSnapshot | None = None
    improvements: ImprovementSnapshot | None = None
    prior_days: tuple[WinterDayReflection, ...] = ()
    beliefs: tuple[Belief, ...] = ()
    open_loops: tuple[OpenLoop, ...] = ()

    @property
    def capability_claim_scopes(self) -> tuple[str, ...]:
        return self.self_model.authorized_claim_scopes if self.self_model is not None else ()

    @property
    def improvement_statuses(self) -> tuple[str, ...]:
        return (
            tuple(item.status for item in self.improvements.items)
            if self.improvements is not None else ()
        )
