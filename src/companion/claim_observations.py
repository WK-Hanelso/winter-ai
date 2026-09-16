"""Local append-only claim diagnostics, separate from memory and event evidence."""

from dataclasses import asdict, dataclass
import json
from pathlib import Path

from companion.grounding import ClaimViolation, ResponseClaimGate


@dataclass(frozen=True)
class ClaimObservation:
    turn_id: str
    source_ref: str | None
    source: str
    stage: str
    text: str
    checked_text: str
    violations: tuple[ClaimViolation, ...]
    web_grounded: bool = False


class JsonlClaimObservationRecorder:
    def __init__(self, path: Path) -> None:
        self.path = path

    def __call__(self, observation: ClaimObservation) -> None:
        record = asdict(observation)
        blocking = ResponseClaimGate.blocking_violations(observation.violations)
        record["violations"] = [
            {**asdict(item), "action": "block" if item in blocking else "observe",
             **({"web_grounded": observation.web_grounded}
                if item.kind == "outside_event" else {})}
            for item in observation.violations
        ]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
