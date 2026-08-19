"""Run one synthetic Codex typed-project Worker isolation probe."""

from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from companion.adapters.codex_gateway_worker import CodexTypedProjectWorker
from companion.work_orchestration import WorkerTaskPacket


def _packet() -> WorkerTaskPacket:
    return WorkerTaskPacket(
        schema_version=2,
        objective="README에 적힌 실행 상태와 다음 할 일을 확인한다.",
        desired_outcome="파일의 정확한 줄 근거가 있는 짧은 한국어 요약을 만든다.",
        capabilities=("project.inspect",),
        resources=("project",),
        context=("이것은 개인정보가 없는 합성 격리 probe다.",),
        definition_of_done=("현재 상태와 다음 할 일을 각각 근거와 함께 보고한다.",),
        prohibited_actions=(
            "shell, network, account, write와 project 밖 경로를 사용하지 않는다.",
        ),
        required_report=(
            "summary",
            "evidence with stable ids",
            "claims referencing evidence ids",
            "actions_performed",
            "checks",
            "limitations",
            "unresolved",
        ),
    )


def main() -> int:
    with TemporaryDirectory(prefix="winter-codex-probe-source-") as temporary:
        source = Path(temporary)
        (source / "README.md").write_text(
            "# Synthetic Winter Probe\n"
            "상태: typed gateway shadow 검증 중\n"
            "다음 할 일: 실제 프로젝트 연결 전 사람 검토\n",
            encoding="utf-8",
        )
        result = CodexTypedProjectWorker(source).execute(_packet())
    print(
        json.dumps(
            {
                "worker": result.worker,
                "model": result.model,
                "report": result.report.to_dict(),
                "audit_log": [asdict(item) for item in result.audit_log],
                "evidence_catalog": [
                    asdict(item) for item in result.evidence_catalog
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
