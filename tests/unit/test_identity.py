from pathlib import Path
import pytest
from companion.identity import CompanionIdentity, IdentityRepositoryError, JsonIdentityRepository

def sample() -> CompanionIdentity:
    return CompanionIdentity("Winter", "local companion", ("calm",), ("honesty",), ("respect agency",), ("no impersonation",), "1")

def test_identity_json_round_trip(tmp_path: Path) -> None:
    repository = JsonIdentityRepository(tmp_path / "identity.json")
    repository.save(sample())
    assert repository.load() == sample()

def test_identity_rejects_empty_persona(tmp_path: Path) -> None:
    with pytest.raises(IdentityRepositoryError, match="non-empty"):
        JsonIdentityRepository(tmp_path / "identity.json").save(CompanionIdentity("x", "role", (), ("v",), ("p",), ("b",), "1"))
