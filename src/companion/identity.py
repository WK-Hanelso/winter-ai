"""Persistent stable Companion Identity, separate from user preferences."""
from __future__ import annotations
from dataclasses import asdict, dataclass
import json
from pathlib import Path

class IdentityRepositoryError(RuntimeError): pass

@dataclass(frozen=True)
class CompanionIdentity:
    name: str; role: str; core_personality: tuple[str, ...]; values: tuple[str, ...]
    relationship_policy: tuple[str, ...]; immutable_boundaries: tuple[str, ...]; version: str
    def system_message(self) -> str:
        return "\n".join((f"You are {self.name}.", f"Role: {self.role}", "Core personality: " + ", ".join(self.core_personality), "Values: " + ", ".join(self.values), "Relationship policy: " + "; ".join(self.relationship_policy), "Immutable boundaries: " + "; ".join(self.immutable_boundaries), f"Identity version: {self.version}"))

class JsonIdentityRepository:
    def __init__(self, path: Path) -> None: self._path = path
    def load(self) -> CompanionIdentity:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            identity = CompanionIdentity(raw["name"], raw["role"], tuple(raw["core_personality"]), tuple(raw["values"]), tuple(raw["relationship_policy"]), tuple(raw["immutable_boundaries"]), raw["version"])
        except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
            raise IdentityRepositoryError(f"could not load identity from {self._path}: {error}") from error
        _validate(identity); return identity
    def save(self, identity: CompanionIdentity) -> None:
        _validate(identity)
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(asdict(identity), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except OSError as error: raise IdentityRepositoryError(f"could not save identity to {self._path}: {error}") from error

def _validate(identity: CompanionIdentity) -> None:
    if not all(x.strip() for x in (identity.name, identity.role, identity.version)) or not all(group and all(x.strip() for x in group) for group in (identity.core_personality, identity.values, identity.relationship_policy, identity.immutable_boundaries)):
        raise IdentityRepositoryError("identity requires non-empty fields and lists")
