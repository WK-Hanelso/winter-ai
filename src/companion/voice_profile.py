"""Runtime-independent Voice Identity and Prosody planning."""
from dataclasses import dataclass
from importlib import import_module
from typing import Any

from companion.response import ProsodyPlan


class VoiceProfileError(ValueError): pass


@dataclass(frozen=True)
class VoiceIdentity:
    name: str
    verbal_style: str
    profiles: dict[str, ProsodyPlan]


def load_voice_identity(profile: str = "base") -> VoiceIdentity:
    try:
        raw: dict[str, Any] = import_module(f"configs.voice.{profile}").VOICE_IDENTITY
        profiles = {name: ProsodyPlan(**values) for name, values in raw["profiles"].items()}
    except (ImportError, KeyError, TypeError) as error:
        raise VoiceProfileError(f"invalid voice profile {profile}: {error}") from error
    required = {"neutral", "calm", "warm", "serious"}
    if not raw.get("name") or not raw.get("verbal_style") or not required <= profiles.keys():
        raise VoiceProfileError("voice profile must define identity and neutral/calm/warm/serious plans")
    return VoiceIdentity(raw["name"], raw["verbal_style"], profiles)


class ProsodyPlanner:
    def __init__(self, identity: VoiceIdentity | None = None) -> None:
        self._identity = identity or load_voice_identity()

    def plan(self, dialogue_act: str) -> ProsodyPlan:
        style = {"memory_candidate": "warm", "warning": "serious", "support": "calm"}.get(dialogue_act, "neutral")
        return self._identity.profiles[style]
