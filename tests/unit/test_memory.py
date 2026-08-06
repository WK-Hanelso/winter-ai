from pathlib import Path
import pytest
from companion.memory import MemoryRepositoryError, SqliteMemoryRepository
from companion.memory import ActiveMemoryRetriever

def test_explicit_memory_requires_approval_then_activation(tmp_path: Path) -> None:
    repo = SqliteMemoryRepository(tmp_path / "memory.sqlite")
    candidate = repo.add_candidate(kind="semantic", content="천우는 겨울이를 선택했다")
    assert candidate.status == "candidate"
    assert repo.transition(candidate.id, "approved").status == "approved"
    assert SqliteMemoryRepository(tmp_path / "memory.sqlite").transition(candidate.id, "active").status == "active"

def test_memory_rejects_skipping_approval(tmp_path: Path) -> None:
    repo = SqliteMemoryRepository(tmp_path / "memory.sqlite")
    candidate = repo.add_candidate(kind="semantic", content="테스트")
    with pytest.raises(MemoryRepositoryError, match="cannot transition"):
        repo.transition(candidate.id, "active")

def test_retriever_selects_only_related_active_memory(tmp_path: Path) -> None:
    repo = SqliteMemoryRepository(tmp_path / "memory.sqlite")
    related = repo.add_candidate(kind="preference", content="천우는 Python config를 선호한다")
    unrelated = repo.add_candidate(kind="project", content="저녁에 Voice 검증을 한다")
    pending = repo.add_candidate(kind="semantic", content="천우는 Python config를 선호한다")
    for memory in (related, unrelated):
        repo.transition(memory.id, "approved"); repo.transition(memory.id, "active")
    assert ActiveMemoryRetriever(repo).retrieve("Python config는 어떻게 관리해?") == (repo.get(related.id),)
    assert pending.id not in {memory.id for memory in ActiveMemoryRetriever(repo).retrieve("Python config")}

def test_replacement_deprecates_old_memory_only_when_replacement_activates(tmp_path: Path) -> None:
    repo = SqliteMemoryRepository(tmp_path / "memory.sqlite")
    old = repo.add_candidate(kind="preference", content="Python config를 선호한다")
    repo.transition(old.id, "approved"); repo.transition(old.id, "active")
    replacement = repo.replace(old.id, "YAML config를 선호한다")
    assert replacement.status == "candidate" and replacement.supersedes == old.id
    assert repo.get(old.id).status == "active"
    repo.transition(replacement.id, "approved"); repo.transition(replacement.id, "active")
    assert repo.get(old.id).status == "deprecated"
    assert repo.get(replacement.id).status == "active"
