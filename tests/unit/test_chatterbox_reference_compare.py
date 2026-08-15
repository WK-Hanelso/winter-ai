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
