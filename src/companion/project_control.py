"""Fail-closed development control checks for the Winter repository."""
from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
import re
import tomllib

MATURITY_ORDER: Mapping[str, int] = {
    "absent": 0,
    "contract": 1,
    "shadow": 2,
    "operational": 3,
    "validated": 4,
}
_FEATURE_ID = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True)
class FeatureState:
    id: str
    maturity: str
    evidence: tuple[str, ...]
    dependencies: tuple[str, ...]
    sync_status: bool


@dataclass(frozen=True)
class ProjectCheckReport:
    errors: tuple[str, ...]
    current_phase: str | None
    active_issue: int | None
    active_work: tuple[str, ...]
    feature_count: int

    @property
    def ok(self) -> bool:
        return not self.errors


def _string_list(value: object, *, field: str, errors: list[str]) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        errors.append(f"{field} must be a list of strings")
        return ()
    return tuple(value)


def _project_path(root: Path, value: str, *, field: str, errors: list[str]) -> Path | None:
    candidate = (root / value).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        errors.append(f"{field} escapes project root: {value}")
        return None
    return candidate


def _load_toml(state_path: Path, errors: list[str]) -> Mapping[str, object]:
    try:
        with state_path.open("rb") as handle:
            loaded = tomllib.load(handle)
    except FileNotFoundError:
        errors.append(f"missing project state: {state_path}")
        return {}
    except (OSError, tomllib.TOMLDecodeError) as error:
        errors.append(f"cannot read project state: {error}")
        return {}
    if not isinstance(loaded, dict):
        errors.append("project state root must be a TOML table")
        return {}
    return loaded


def _parse_features(raw: object, errors: list[str]) -> tuple[FeatureState, ...]:
    if not isinstance(raw, list):
        errors.append("features must be an array of tables")
        return ()

    parsed: list[FeatureState] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        prefix = f"features[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{prefix} must be a table")
            continue
        feature_id = item.get("id")
        maturity = item.get("maturity")
        if not isinstance(feature_id, str) or not _FEATURE_ID.fullmatch(feature_id):
            errors.append(f"{prefix}.id must use lowercase snake_case")
            continue
        if feature_id in seen:
            errors.append(f"duplicate feature id: {feature_id}")
            continue
        seen.add(feature_id)
        if not isinstance(maturity, str):
            errors.append(f"feature '{feature_id}' maturity must be a string")
            maturity = ""
        elif maturity not in MATURITY_ORDER:
            errors.append(f"feature '{feature_id}' has unknown maturity '{maturity}'")
        evidence = _string_list(
            item.get("evidence", []),
            field=f"feature '{feature_id}' evidence",
            errors=errors,
        )
        dependencies = _string_list(
            item.get("dependencies", []),
            field=f"feature '{feature_id}' dependencies",
            errors=errors,
        )
        sync_status = item.get("sync_status", False)
        if not isinstance(sync_status, bool):
            errors.append(f"feature '{feature_id}' sync_status must be true or false")
            sync_status = False
        parsed.append(
            FeatureState(
                id=feature_id,
                maturity=maturity,
                evidence=evidence,
                dependencies=dependencies,
                sync_status=sync_status,
            )
        )
    return tuple(parsed)


def _expected_status_marker(features: Sequence[FeatureState]) -> str:
    synchronized = sorted(
        (feature.id, feature.maturity) for feature in features if feature.sync_status
    )
    claims = ", ".join(f"{feature_id}={maturity}" for feature_id, maturity in synchronized)
    return f"<!-- winter-project-state: {claims} -->"


def validate_project_state(*, root: Path, state_path: Path) -> ProjectCheckReport:
    """Validate state, prerequisite ordering, evidence, and declared documentation claims."""

    root = root.resolve()
    if not state_path.is_absolute():
        state_path = root / state_path
    state_path = state_path.resolve()
    errors: list[str] = []
    raw = _load_toml(state_path, errors)

    if raw.get("schema_version") != 1:
        errors.append("schema_version must be 1")

    phase_value = raw.get("current_phase")
    current_phase = phase_value if isinstance(phase_value, str) and phase_value.strip() else None
    if current_phase is None:
        errors.append("current_phase must be a non-empty string")

    issue_value = raw.get("active_issue")
    active_issue = (
        issue_value if isinstance(issue_value, int) and not isinstance(issue_value, bool) else None
    )
    if active_issue is None or active_issue < 1:
        errors.append("active_issue must be a positive GitHub issue number")

    canonical_documents = _string_list(
        raw.get("canonical_documents", []),
        field="canonical_documents",
        errors=errors,
    )
    status_documents = _string_list(
        raw.get("status_documents", []),
        field="status_documents",
        errors=errors,
    )
    features = _parse_features(raw.get("features", []), errors)
    active_work = _string_list(raw.get("active_work", []), field="active_work", errors=errors)
    if not active_work:
        errors.append("active_work must name at least one declared feature")

    for document in canonical_documents:
        path = _project_path(root, document, field="canonical document", errors=errors)
        if path is not None and not path.is_file():
            errors.append(f"missing canonical document: {document}")

    by_id = {feature.id: feature for feature in features}
    for feature_id in active_work:
        if feature_id not in by_id:
            errors.append(f"active_work names unknown feature: {feature_id}")
    for feature in features:
        maturity_level = MATURITY_ORDER.get(feature.maturity)
        if maturity_level is None:
            continue
        if maturity_level >= MATURITY_ORDER["operational"] and not feature.evidence:
            errors.append(f"feature '{feature.id}' {feature.maturity} requires evidence")
        for evidence in feature.evidence:
            path = _project_path(
                root,
                evidence,
                field=f"feature '{feature.id}' evidence",
                errors=errors,
            )
            if path is not None and not path.exists():
                errors.append(f"feature '{feature.id}' evidence does not exist: {evidence}")
        for dependency_id in feature.dependencies:
            dependency = by_id.get(dependency_id)
            if dependency is None:
                errors.append(f"feature '{feature.id}' has unknown dependency '{dependency_id}'")
                continue
            if maturity_level >= MATURITY_ORDER["operational"] and MATURITY_ORDER.get(
                dependency.maturity, -1
            ) < MATURITY_ORDER["operational"]:
                errors.append(
                    f"feature '{feature.id}' requires operational dependency '{dependency_id}'"
                )

    expected_marker = _expected_status_marker(features)
    if any(feature.sync_status for feature in features):
        if not status_documents:
            errors.append("status_documents is required when sync_status is enabled")
        for document in status_documents:
            path = _project_path(root, document, field="status document", errors=errors)
            if path is None:
                continue
            try:
                contents = path.read_text(encoding="utf-8")
            except OSError as error:
                errors.append(f"cannot read status document '{document}': {error}")
                continue
            if expected_marker not in contents:
                errors.append(f"status marker does not match machine state: {document}")

    return ProjectCheckReport(
        errors=tuple(dict.fromkeys(errors)),
        current_phase=current_phase,
        active_issue=active_issue,
        active_work=active_work,
        feature_count=len(features),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--state", type=Path, default=Path("project-state.toml"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = validate_project_state(root=args.root, state_path=args.state)
    if report.ok:
        print("PROJECT CHECK PASSED")
        print(f"phase: {report.current_phase}")
        print(f"active issue: #{report.active_issue}")
        print(f"active work: {', '.join(report.active_work)}")
        print(f"features: {report.feature_count}")
        return 0

    print("PROJECT CHECK FAILED")
    for error in report.errors:
        print(f"- {error}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
