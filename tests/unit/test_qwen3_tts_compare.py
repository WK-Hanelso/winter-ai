import importlib.util
from pathlib import Path
from typing import Any

import numpy as np
import pytest


def _load_module() -> Any:
    # qwen-tts and soundfile are GPU-image-only imports, so the ordinary test
    # suite loads only the validation and manifest code.
    path = Path("experiments/qwen3_tts_compare.py")
    spec = importlib.util.spec_from_file_location("qwen3_tts_compare", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_load_sentences_accepts_comments_and_pairs(tmp_path: Path) -> None:
    manifest = tmp_path / "sentences.tsv"
    manifest.write_text("# comment\ns1\t오늘은 집에 있었어.\nq1\t오늘은 집에 있었어?\n")

    assert _load_module().load_sentences(manifest) == [
        ("s1", "오늘은 집에 있었어."),
        ("q1", "오늘은 집에 있었어?"),
    ]


@pytest.mark.parametrize("body", ["bad row", "../bad\t안녕", "s1\t안녕\ns1\t또 안녕"])
def test_load_sentences_rejects_unsafe_or_ambiguous_rows(tmp_path: Path, body: str) -> None:
    manifest = tmp_path / "sentences.tsv"
    manifest.write_text(body)

    with pytest.raises(ValueError):
        _load_module().load_sentences(manifest)


def test_reference_text_must_not_be_empty(tmp_path: Path) -> None:
    transcript = tmp_path / "reference.txt"
    transcript.write_text("  \n")

    with pytest.raises(ValueError, match="비었습니다"):
        _load_module().load_reference_text(transcript)


def test_modes_are_deduplicated_and_default_to_both() -> None:
    module = _load_module()

    assert module.selected_modes(None) == ("icl", "embedding")
    assert module.selected_modes(["embedding", "embedding"]) == ("embedding",)


@pytest.mark.parametrize(
    ("requested", "capability", "expected"),
    [
        ("auto", (7, 5), "float32"),
        ("auto", (8, 6), "bfloat16"),
        ("float16", (7, 5), "float16"),
    ],
)
def test_dtype_avoids_unstable_fp16_on_turing(
    requested: str, capability: tuple[int, int], expected: str
) -> None:
    assert _load_module().selected_dtype(requested, capability) == expected


def test_save_audio_rejects_non_finite_output(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="유효하지 않은"):
        _load_module().save_audio(np.array([0.0, np.nan]), 24000, tmp_path / "bad.wav")
