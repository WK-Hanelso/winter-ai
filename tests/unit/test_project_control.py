from __future__ import annotations

from pathlib import Path

from companion.project_control import main, validate_project_state

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _write_state(root: Path, body: str) -> Path:
    state_path = root / "project-state.toml"
    state_path.write_text(body, encoding="utf-8")
    return state_path


def _valid_state(*, status_documents: str = "[]", marker_sync: bool = False) -> str:
    return f"""
schema_version = 1
current_phase = "foundation_recovery"
active_issue = 112
active_work = ["foundation"]
canonical_documents = ["AGENTS.md"]
status_documents = {status_documents}

[[features]]
id = "foundation"
maturity = "operational"
evidence = ["AGENTS.md"]
dependencies = []
sync_status = {str(marker_sync).lower()}

[[features]]
id = "worker_activation"
maturity = "shadow"
evidence = []
dependencies = ["foundation"]
sync_status = {str(marker_sync).lower()}
"""


def test_valid_project_state_passes(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("policy", encoding="utf-8")
    state_path = _write_state(tmp_path, _valid_state())

    report = validate_project_state(root=tmp_path, state_path=state_path)

    assert report.ok is True
    assert report.errors == ()
    assert report.current_phase == "foundation_recovery"
    assert report.active_issue == 112


def test_repository_project_state_passes() -> None:
    report = validate_project_state(
        root=PROJECT_ROOT,
        state_path=PROJECT_ROOT / "project-state.toml",
    )

    assert report.errors == ()


def test_unknown_maturity_fails_closed(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("policy", encoding="utf-8")
    state_path = _write_state(
        tmp_path,
        _valid_state().replace('maturity = "shadow"', 'maturity = "finished"'),
    )

    report = validate_project_state(root=tmp_path, state_path=state_path)

    assert report.ok is False
    assert any("unknown maturity 'finished'" in error for error in report.errors)


def test_malformed_toml_fails_closed(tmp_path: Path) -> None:
    state_path = _write_state(tmp_path, 'schema_version = "unterminated')

    report = validate_project_state(root=tmp_path, state_path=state_path)

    assert report.ok is False
    assert any("cannot read project state" in error for error in report.errors)


def test_evidence_cannot_escape_project_root(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("policy", encoding="utf-8")
    state_path = _write_state(
        tmp_path,
        _valid_state().replace('evidence = ["AGENTS.md"]', 'evidence = ["../secret"]', 1),
    )

    report = validate_project_state(root=tmp_path, state_path=state_path)

    assert report.ok is False
    assert any("escapes project root" in error for error in report.errors)


def test_operational_feature_requires_existing_evidence(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("policy", encoding="utf-8")
    state_path = _write_state(
        tmp_path,
        _valid_state().replace('evidence = ["AGENTS.md"]', "evidence = []", 1),
    )

    report = validate_project_state(root=tmp_path, state_path=state_path)

    assert report.ok is False
    assert any("operational requires evidence" in error for error in report.errors)


def test_operational_feature_cannot_outrun_dependency(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("policy", encoding="utf-8")
    state_path = _write_state(
        tmp_path,
        _valid_state()
        .replace('maturity = "operational"', 'maturity = "contract"', 1)
        .replace('maturity = "shadow"', 'maturity = "operational"', 1)
        .replace("evidence = []", 'evidence = ["AGENTS.md"]', 1),
    )

    report = validate_project_state(root=tmp_path, state_path=state_path)

    assert report.ok is False
    assert any(
        "requires operational dependency 'foundation'" in error for error in report.errors
    )


def test_active_work_must_name_a_declared_feature(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("policy", encoding="utf-8")
    state_path = _write_state(
        tmp_path,
        _valid_state().replace('active_work = ["foundation"]', 'active_work = ["new_model"]'),
    )

    report = validate_project_state(root=tmp_path, state_path=state_path)

    assert report.ok is False
    assert "active_work names unknown feature: new_model" in report.errors


def test_status_document_must_match_machine_state(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("policy", encoding="utf-8")
    (tmp_path / "README.md").write_text(
        "<!-- winter-project-state: foundation=operational, worker_activation=operational -->\n",
        encoding="utf-8",
    )
    state_path = _write_state(
        tmp_path,
        _valid_state(status_documents='["README.md"]', marker_sync=True),
    )

    report = validate_project_state(root=tmp_path, state_path=state_path)

    assert report.ok is False
    assert any("status marker does not match" in error for error in report.errors)


def test_cli_reports_all_failures_without_model_or_data(tmp_path: Path, capsys: object) -> None:
    state_path = _write_state(tmp_path, _valid_state())

    result = main(["--root", str(tmp_path), "--state", str(state_path)])

    output = capsys.readouterr().out  # type: ignore[attr-defined]
    assert result == 1
    assert "PROJECT CHECK FAILED" in output
    assert "missing canonical document: AGENTS.md" in output
