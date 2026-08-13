"""파이프라인이 첫 소리를 먼저 내보내는지, 그 뒤에는 겹쳐 도는지."""

from __future__ import annotations

import threading
import time

import pytest

from companion.voice_pipeline import stream


def test_pieces_come_out_in_order() -> None:
    result = list(stream(["가", "나", "다"], lambda s: s + "1", lambda s: s + "2"))

    assert result == ["가12", "나12", "다12"]


def test_second_sentence_waits_for_the_first_sound() -> None:
    """두 단계가 같은 카드를 쓰므로, 첫 소리가 나오기 전에는 겹치지 않아야 한다.

    겹치면 변환이 1.42초에서 2.58초가 되는 것이 실측이었고, 그 손해가 정확히
    천우가 기다리는 첫 소리에 실린다.
    """
    started: list[str] = []
    first_converted = threading.Event()

    def synthesize(sentence: str) -> str:
        started.append(sentence)
        return sentence

    def convert(sentence: str) -> str:
        if sentence == "가":
            # 첫 문장을 변환하는 동안 두 번째 합성이 시작되면 안 된다.
            time.sleep(0.05)
            assert started == ["가"], f"첫 소리 전에 겹쳤습니다: {started}"
            first_converted.set()
        return sentence

    result = list(stream(["가", "나"], synthesize, convert))

    assert first_converted.is_set()
    assert result == ["가", "나"]


def test_later_sentences_do_overlap() -> None:
    """첫 소리가 나온 뒤에는 다시 겹쳐 돌아야 한다 — 양보는 한 번뿐이다."""
    overlapped = threading.Event()
    converting_second = threading.Event()

    def synthesize(sentence: str) -> str:
        if sentence == "다":
            # 두 번째 변환이 도는 동안 세 번째 합성이 들어왔다는 뜻.
            if converting_second.wait(timeout=1.0):
                overlapped.set()
        return sentence

    def convert(sentence: str) -> str:
        if sentence == "나":
            converting_second.set()
            time.sleep(0.05)
        return sentence

    list(stream(["가", "나", "다"], synthesize, convert))

    assert overlapped.is_set(), "첫 소리 뒤에도 겹치지 않았습니다"


def test_a_failure_does_not_strand_the_first_stage() -> None:
    """첫 변환이 실패해도 합성 스레드가 오지 않을 소리를 기다리면 안 된다."""

    def convert(sentence: str) -> str:
        raise RuntimeError("변환 실패")

    with pytest.raises(RuntimeError, match="변환 실패"):
        list(stream(["가", "나"], lambda s: s, convert))

    # 스레드가 event에 묶여 있으면 여기서 남아 있게 된다.
    time.sleep(0.1)
    names = [t.name for t in threading.enumerate()]
    assert len(names) < 20, f"스레드가 남았습니다: {names}"
