from pathlib import Path

import pytest

from companion.open_loops import (
    ActiveOpenLoopRetriever,
    OpenLoopRepositoryError,
    SqliteOpenLoopRepository,
    detect_open_loop,
    open_loop_topic,
)


def test_topic_label_keeps_the_subject_not_the_future_instruction() -> None:
    assert open_loop_topic("대화 기억은 내일 다시 이어서 보자") == "대화 기억"
    assert open_loop_topic("질문 억양은 다음에 다시 들어보자") == "질문 억양"


def test_detector_keeps_only_explicitly_unfinished_user_topics() -> None:
    assert detect_open_loop("이 목소리 문제는 내일 다시 보자") is not None
    assert detect_open_loop("아직 질문 억양을 어떻게 할지 고민 중이야") is not None
    assert detect_open_loop("오늘 점심은 김치찌개였어") is None
    assert detect_open_loop("아직 아침이야") is None


def test_open_loop_persists_and_exact_repeat_does_not_duplicate(tmp_path: Path) -> None:
    repository = SqliteOpenLoopRepository(tmp_path / "dialogue-state.sqlite")
    first = repository.remember("이 목소리 문제는 내일 다시 보자")
    repeated = repository.remember("이 목소리 문제는 내일 다시 보자")

    assert repeated.id == first.id
    reopened = SqliteOpenLoopRepository(tmp_path / "dialogue-state.sqlite")
    assert reopened.list_open() == (first,)
    assert reopened.transition(first.id, "resolved").status == "resolved"
    assert reopened.list_open() == ()


def test_open_loop_rejects_invalid_transition(tmp_path: Path) -> None:
    repository = SqliteOpenLoopRepository(tmp_path / "dialogue-state.sqlite")
    loop = repository.remember("내일 다시 확인하자")
    repository.transition(loop.id, "dismissed")

    with pytest.raises(OpenLoopRepositoryError, match="cannot transition"):
        repository.transition(loop.id, "resolved")


def test_retriever_recalls_latest_thread_for_ambiguous_continuation(tmp_path: Path) -> None:
    repository = SqliteOpenLoopRepository(tmp_path / "dialogue-state.sqlite")
    older = repository.remember("질문 억양은 다음에 다시 들어보자")
    latest = repository.remember("기억 구조는 내일 계속 만들자")
    retriever = ActiveOpenLoopRetriever(repository)

    assert tuple(loop.id for loop in retriever.retrieve("아까 그 얘기 이어가자")) == (
        latest.id,
        older.id,
    )
    assert retriever.retrieve("점심 뭐 먹을까?") == ()
    assert tuple(
        loop.id for loop in retriever.retrieve("질문 억양 이야기부터 하자")
    ) == (older.id,)
