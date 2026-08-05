from dataclasses import dataclass
from importlib import import_module
from typing import Any


class ModelConfigError(ValueError):
    pass


@dataclass(frozen=True)
class ModelRuntimeConfig:
    runtime: str
    model_id: str
    device: str
    backend: str | None = None
    gpu_layers: int = 0
    quantization: str | None = None


@dataclass(frozen=True)
class ModelConfig:
    llm: ModelRuntimeConfig
    stt: ModelRuntimeConfig
    tts: ModelRuntimeConfig


_ALLOWED_PROFILES = {"base", "cpu", "rtx2060_6gb"}
_ALLOWED_RUNTIMES = {"llama_cpp", "whisper_cpp", "melotts"}


def load_model_config(profile: str) -> ModelConfig:
    if profile not in _ALLOWED_PROFILES:
        raise ModelConfigError(f"unsupported model profile: {profile}")
    raw_models: dict[str, dict[str, Any]] = import_module(f"configs.models.{profile}").MODELS
    return ModelConfig(**{name: _parse(name, raw_models.get(name)) for name in ("llm", "stt", "tts")})


def _parse(name: str, raw: dict[str, Any] | None) -> ModelRuntimeConfig:
    if raw is None:
        raise ModelConfigError(f"missing {name} configuration")
    try:
        config = ModelRuntimeConfig(**raw)
    except TypeError as error:
        raise ModelConfigError(f"invalid {name} configuration: {error}") from error
    if config.runtime not in _ALLOWED_RUNTIMES:
        raise ModelConfigError(f"unsupported runtime: {config.runtime}")
    if not config.model_id:
        raise ModelConfigError(f"missing {name} model_id")
    return config
