import pytest

from companion.model_config import ModelConfigError, load_model_config


def test_rtx2060_profile_overrides_only_llm_runtime_settings() -> None:
    config = load_model_config("rtx2060_6gb")
    assert config.llm.device == "cuda"
    assert config.llm.gpu_layers == 32
    assert config.stt.device == "cpu"


def test_unknown_profile_is_rejected() -> None:
    with pytest.raises(ModelConfigError, match="unsupported model profile"):
        load_model_config("production")
