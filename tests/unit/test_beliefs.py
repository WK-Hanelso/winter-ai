from pathlib import Path

import pytest

from companion.beliefs import (
    ActiveBeliefRetriever,
    BeliefRepositoryError,
    SqliteBeliefRepository,
    belief_context,
)


def _candidate(repo: SqliteBeliefRepository, *, subject: str, stance: str):  # type: ignore[no-untyped-def]
    return repo.add_candidate(
        subject=subject,
        stance=stance,
        rationale="반복 실험에서 같은 병목이 관찰됐다",
        confidence=0.78,
        evidence=("voice-test-22", "public-rvc-bakeoff"),
        source="reflection",
    )


def test_belief_requires_evidence_and_review_before_it_becomes_active(tmp_path: Path) -> None:
    repo = SqliteBeliefRepository(tmp_path / "beliefs.sqlite")
    candidate = _candidate(
        repo,
        subject="음성 개발 방향",
        stance="추가 VC 학습보다 앞단 TTS 개선이 우선이다",
    )

    assert candidate.status == "candidate"
    assert repo.list_active() == ()
    active = repo.transition(candidate.id, "active")

    assert active.status == "active"
    assert active.evidence == ("voice-test-22", "public-rvc-bakeoff")
    assert SqliteBeliefRepository(tmp_path / "beliefs.sqlite").get(active.id) == active


def test_belief_rejects_missing_evidence_and_invalid_confidence(tmp_path: Path) -> None:
    repo = SqliteBeliefRepository(tmp_path / "beliefs.sqlite")

    with pytest.raises(BeliefRepositoryError, match="evidence"):
        repo.add_candidate(
            subject="음성",
            stance="C가 낫다",
            rationale="청취 결과",
            confidence=0.8,
            evidence=(),
        )
    with pytest.raises(BeliefRepositoryError, match="confidence"):
        repo.add_candidate(
            subject="음성",
            stance="C가 낫다",
            rationale="청취 결과",
            confidence=1.2,
            evidence=("blind-listen-1",),
        )


def test_only_related_active_beliefs_are_retrieved(tmp_path: Path) -> None:
    repo = SqliteBeliefRepository(tmp_path / "beliefs.sqlite")
    voice = _candidate(repo, subject="음성 개발 방향", stance="앞단 TTS 개선이 우선이다")
    memory = _candidate(repo, subject="기억 정책", stance="자동 확정보다 검토가 우선이다")
    pending = _candidate(repo, subject="음성 모델", stance="다른 VC도 시험할 수 있다")
    repo.transition(voice.id, "active")
    repo.transition(memory.id, "active")

    retrieved = ActiveBeliefRetriever(repo).retrieve("음성 TTS 방향을 어떻게 잡을까?")

    assert retrieved == (repo.get(voice.id),)
    assert pending.id not in {belief.id for belief in retrieved}


def test_two_active_beliefs_cannot_silently_disagree_on_the_same_subject(tmp_path: Path) -> None:
    repo = SqliteBeliefRepository(tmp_path / "beliefs.sqlite")
    first = _candidate(repo, subject="음성 개발 방향", stance="TTS 개선이 먼저다")
    second = _candidate(repo, subject="음성 개발 방향", stance="VC 재학습이 먼저다")
    repo.transition(first.id, "active")

    with pytest.raises(BeliefRepositoryError, match="active belief already exists"):
        repo.transition(second.id, "active")


def test_revision_keeps_old_belief_until_the_replacement_is_activated(tmp_path: Path) -> None:
    repo = SqliteBeliefRepository(tmp_path / "beliefs.sqlite")
    old = _candidate(repo, subject="음성 개발 방향", stance="TTS 개선이 먼저다")
    repo.transition(old.id, "active")

    replacement = repo.revise(
        old.id,
        stance="새 직접 TTS가 충분하면 VC를 제거한다",
        rationale="직접 TTS의 유사도와 자연스러움이 함께 개선됐다",
        confidence=0.72,
        evidence=("direct-tts-gate-2",),
        source="reflection",
    )

    assert replacement.status == "candidate"
    assert replacement.supersedes == old.id
    assert repo.get(old.id).status == "active"
    repo.transition(replacement.id, "active")
    assert repo.get(old.id).status == "deprecated"
    assert repo.get(replacement.id).status == "active"


def test_belief_context_marks_stances_as_revisable_judgements(tmp_path: Path) -> None:
    repo = SqliteBeliefRepository(tmp_path / "beliefs.sqlite")
    belief = _candidate(repo, subject="음성 개발 방향", stance="TTS 개선이 먼저다")
    repo.transition(belief.id, "active")

    context = belief_context((repo.get(belief.id),))

    assert belief.id in context
    assert "TTS 개선이 먼저다" in context
    assert "고정된 사실이 아니라" in context

