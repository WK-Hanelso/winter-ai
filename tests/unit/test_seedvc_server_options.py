import importlib.util
from pathlib import Path
import sys
from types import ModuleType
from typing import Any


def _load_server_module() -> Any:
    path = Path("experiments/seedvc_server.py")
    spec = importlib.util.spec_from_file_location("seedvc_server_options", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    previous = sys.modules.get("inference")
    sys.modules["inference"] = ModuleType("inference")
    try:
        spec.loader.exec_module(module)
    finally:
        if previous is None:
            del sys.modules["inference"]
        else:
            sys.modules["inference"] = previous
    return module


def test_f0_server_options_use_pretrained_f0_model() -> None:
    module = _load_server_module()

    arguments = module.conversion_arguments(
        None,
        None,
        1.15,
        30,
        f0_condition=True,
        auto_f0_adjust=True,
    )

    assert arguments.checkpoint is None
    assert arguments.config is None
    assert arguments.f0_condition is True
    assert arguments.auto_f0_adjust is True


def test_finetuned_server_options_keep_existing_contract() -> None:
    module = _load_server_module()
    checkpoint = Path("checkpoint.pth")
    config = Path("config.yml")

    arguments = module.conversion_arguments(checkpoint, config, 1.15, 30)

    assert arguments.checkpoint == str(checkpoint)
    assert arguments.config == str(config)
    assert arguments.f0_condition is False
    assert arguments.auto_f0_adjust is False
