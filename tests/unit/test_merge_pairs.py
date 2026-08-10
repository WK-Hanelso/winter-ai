"""Offline coverage for merging per-chunk pair files."""

from __future__ import annotations

import json
from pathlib import Path

from experiments.merge_pairs import MergeError, load_pairs
import pytest


def _write(path: Path, rows: list[dict[str, str]]) -> Path:
    path.write_text(json.dumps(rows), encoding="utf-8")
    return path


def test_pairs_from_several_files_are_combined(tmp_path: Path) -> None:
    first = _write(tmp_path / "pairs-a.json", [{"prompt": "q1", "response": "a1"}])
    second = _write(tmp_path / "pairs-b.json", [{"prompt": "q2", "response": "a2"}])

    merged = load_pairs([first, second])

    assert len(merged) == 2


def test_the_same_exchange_twice_is_counted_once(tmp_path: Path) -> None:
    # Re-running a chunk is normal while the pipeline is being tuned.
    first = _write(tmp_path / "pairs-a.json", [{"prompt": "q", "response": "a"}])
    second = _write(tmp_path / "pairs-b.json", [{"prompt": "q", "response": "a"}])

    assert len(load_pairs([first, second])) == 1


def test_rows_missing_a_side_are_skipped(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "pairs-a.json",
        [{"prompt": "q", "response": ""}, {"prompt": "q2", "response": "a2"}],
    )

    merged = load_pairs([path])

    assert len(merged) == 1
    assert merged[0]["prompt"] == "q2"


def test_an_unreadable_file_fails_loudly(tmp_path: Path) -> None:
    path = tmp_path / "pairs-a.json"
    path.write_text("not json", encoding="utf-8")

    with pytest.raises(MergeError, match="could not read"):
        load_pairs([path])


def test_a_file_that_is_not_a_list_fails_loudly(tmp_path: Path) -> None:
    path = _write(tmp_path / "pairs-a.json", [])
    path.write_text(json.dumps({"prompt": "q"}), encoding="utf-8")

    with pytest.raises(MergeError, match="does not hold a list"):
        load_pairs([path])


def test_merging_nothing_usable_fails_rather_than_writing_an_empty_set(
    tmp_path: Path,
) -> None:
    path = _write(tmp_path / "pairs-a.json", [{"prompt": "", "response": ""}])

    with pytest.raises(MergeError, match="no pairs found"):
        load_pairs([path])
