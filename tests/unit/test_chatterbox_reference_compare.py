import importlib.util
from pathlib import Path
from typing import Any

import pytest


def _load_sentences() -> Any:
    # GPU-only imports live inside the functions that use them, so this parser
    # remains covered by the ordinary offline suite.
    path = Path("experiments/chatterbox_reference_compare.py")
    spec = importlib.util.spec_from_file_location("chatterbox_reference_compare", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.load_sentences


def _load_module() -> Any:
    path = Path("experiments/chatterbox_reference_compare.py")
    spec = importlib.util.spec_from_file_location("chatterbox_reference_compare", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_load_sentences_accepts_comments_and_tab_separated_rows(tmp_path: Path) -> None:
    manifest = tmp_path / "sentences.tsv"
    manifest.write_text("# comment\ns1\t안녕.\nq1\t안녕?\n", encoding="utf-8")

    assert _load_sentences()(manifest) == [("s1", "안녕."), ("q1", "안녕?")]


@pytest.mark.parametrize("body", ["bad row", "../bad\t안녕", "s1\t안녕\ns1\t또 안녕"])
def test_load_sentences_rejects_ambiguous_or_unsafe_rows(tmp_path: Path, body: str) -> None:
    manifest = tmp_path / "sentences.tsv"
    manifest.write_text(body, encoding="utf-8")

    with pytest.raises(ValueError):
        _load_sentences()(manifest)


def test_s3gen_reference_keeps_default_delivery_and_replaces_acoustics() -> None:
    from types import SimpleNamespace

    module = _load_module()
    default = SimpleNamespace(t3="default-delivery", gen="default-acoustics")
    reference = SimpleNamespace(t3="reference-delivery", gen="reference-acoustics")

    selected = module.select_conditioning(default, reference, "s3gen-reference")

    assert selected.t3 == "default-delivery"
    assert selected.gen == "reference-acoustics"
    assert default.gen == "default-acoustics"


def test_unknown_reference_conditioning_fails_closed() -> None:
    from types import SimpleNamespace

    module = _load_module()
    conditions = SimpleNamespace(t3="delivery", gen="acoustics")

    with pytest.raises(ValueError, match="모르는 Chatterbox 조건"):
        module.select_conditioning(conditions, conditions, "invented")
