from pathlib import Path

from companion.project_baseline import build_project_baseline


def test_baseline_is_deterministic_and_excludes_private_runtime_data(
    tmp_path: Path,
) -> None:
    source = tmp_path / "project"
    (source / "src").mkdir(parents=True)
    (source / "data").mkdir()
    (source / ".git").mkdir()
    (source / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    (source / "README.md").write_text("project\n", encoding="utf-8")
    (source / "data" / "conversation.sqlite").write_bytes(b"private")
    (source / ".env").write_text("TOKEN=secret\n", encoding="utf-8")
    (source / ".git" / "config").write_text("private\n", encoding="utf-8")

    first = build_project_baseline(source)
    second = build_project_baseline(source)

    assert first == second
    assert tuple(item.path for item in first.files) == ("README.md", "src/app.py")
    assert "private" not in repr(first)
    assert "secret" not in repr(first)


def test_baseline_changes_for_safe_source_but_not_private_data(tmp_path: Path) -> None:
    source = tmp_path / "project"
    target = source / "src" / "app.py"
    private = source / "data" / "conversation.sqlite"
    target.parent.mkdir(parents=True)
    private.parent.mkdir(parents=True)
    target.write_text("VALUE = 1\n", encoding="utf-8")
    private.write_bytes(b"one")
    initial = build_project_baseline(source)

    private.write_bytes(b"two")
    assert build_project_baseline(source) == initial

    target.write_text("VALUE = 2\n", encoding="utf-8")
    assert build_project_baseline(source).manifest_sha256 != initial.manifest_sha256


def test_baseline_includes_root_build_and_compose_files(tmp_path: Path) -> None:
    source = tmp_path / "project"
    source.mkdir()
    (source / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    (source / "Dockerfile.denoise").write_text("FROM scratch\n", encoding="utf-8")
    (source / "compose.vc-server.yaml").write_text("services: {}\n", encoding="utf-8")

    baseline = build_project_baseline(source)

    assert tuple(item.path for item in baseline.files) == (
        "Dockerfile",
        "Dockerfile.denoise",
        "compose.vc-server.yaml",
    )
