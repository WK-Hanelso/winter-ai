from pathlib import Path
import pytest
from companion.memory import MemoryRepositoryError, SqliteMemoryRepository

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
