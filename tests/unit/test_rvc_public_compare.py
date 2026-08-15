import importlib.util
from pathlib import Path
from typing import Any

import pytest


def _load_module() -> Any:
    path = Path("experiments/rvc_public_compare.py")
    spec = importlib.util.spec_from_file_location("rvc_public_compare", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_source_files_selects_only_fixed_pair_names(tmp_path: Path) -> None:
    for name in ("s1.wav", "q1.wav", "notes.wav", "s10.wav"):
        (tmp_path / name).touch()

    assert [path.name for path in _load_module().source_files(tmp_path)] == ["q1.wav", "s1.wav"]


def test_source_files_requires_at_least_one_pair(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="소스가 없습니다"):
        _load_module().source_files(tmp_path)


def test_destination_has_unambiguous_suffix(tmp_path: Path) -> None:
    module = _load_module()
    assert module.destination_for(Path("q2.wav"), tmp_path) == tmp_path / "q2__rvc-public.wav"
