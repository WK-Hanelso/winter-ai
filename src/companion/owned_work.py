"""One bounded, read-only Winter-owned repository diagnosis canary."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import threading
from uuid import uuid4

from companion.ports import EvidenceWorker, OwnedWorkExecutor, WinterExecutive
from companion.project_baseline import ProjectBaselineError, build_project_baseline
from companion.work_orchestration import WorkRequest, build_worker_task
from companion.worker import (
    ReadOnlyWorkerResult,
    WorkerUnavailableError,
)


@dataclass(frozen=True)
class ReadOnlyProjectCanaryPolicy:
    """Open only an obvious project-inspection candidate; never grant authority."""

    project_markers: tuple[str, ...] = (
        "코드",
        "저장소",
        "repository",
        "repo",
        "readme",
        "테스트",
        "test",
        "latency",
        "응답 지연",
        "소스",
    )
    inspection_markers: tuple[str, ...] = (
        "진단",
        "분석",
        "확인",
        "검토",
        "조사",
        "찾",
        "원인",
        "비교",
        "추천",
    )
    write_markers: tuple[str, ...] = (
        "수정해",
        "고쳐",
        "변경해",
        "삭제해",
        "배포해",
        "적용해",
        "커밋해",
        "push",
        "푸시해",
    )
    private_markers: tuple[str, ...] = (
        "내 기억",
        "대화 원문",
        "대화 기록",
        "identity",
        "정체성",
        "계정",
        "비밀번호",
        "credential",
        "human reference",
        "음성 원본",
    )

    def route(self, text: str) -> WorkRequest | None:
        normalized = " ".join(text.lower().split())
        if not normalized or len(normalized) > 800:
            return None
        if any(marker in normalized for marker in self.write_markers):
            return None
        if any(marker in normalized for marker in self.private_markers):
            return None
        if not any(marker in normalized for marker in self.project_markers):
            return None
        if not any(marker in normalized for marker in self.inspection_markers):
            return None
        return WorkRequest(user_text=text.strip())


class WinterOwnedProjectCanary:
    """Plan, inspect and review one project task without modifying its source."""

    def __init__(
        self,
        source_root: Path,
        executive: WinterExecutive,
        worker: EvidenceWorker,
    ) -> None:
        self._source_root = source_root.resolve()
        self._executive = executive
        self._worker = worker

    def execute(self, request: WorkRequest) -> ReadOnlyWorkerResult:
        try:
            before = build_project_baseline(self._source_root)
        except ProjectBaselineError as error:
            raise WorkerUnavailableError(
                "읽기 전용 project 기준선을 만들지 못했습니다."
            ) from error
        brief = self._executive.create_brief(request)
        if not brief.ready_for_worker:
            raise WorkerUnavailableError(
                "겨울이가 요청을 안전한 읽기 작업으로 정리하지 못했습니다."
            )
        packet = build_worker_task(brief)
        worker_result = self._worker.execute(packet)
        try:
            after = build_project_baseline(self._source_root)
        except ProjectBaselineError as error:
            raise WorkerUnavailableError(
                "읽기 전용 project 기준선을 다시 확인하지 못했습니다."
            ) from error
        if after != before:
            raise WorkerUnavailableError(
                "읽기 전용 Worker 실행 중 project source가 달라졌습니다."
            )
        decision = self._executive.review(brief, worker_result.report)
        answer = decision.final_response or decision.judgment
        if not answer.strip():
            raise WorkerUnavailableError(
                "겨울이가 Worker 근거에서 최종 판단을 만들지 못했습니다."
            )
        catalog = {
            item.evidence_id: item.locator
            for item in worker_result.evidence_catalog
        }
        evidence = tuple(
            catalog[evidence_id]
            for evidence_id in decision.evidence_used
            if evidence_id in catalog
        )
        limitations = tuple(
            dict.fromkeys(
                worker_result.report.limitations
                + worker_result.report.unresolved
                + decision.unresolved
            )
        )
        if decision.next_action == "retry":
            limitations += ("추가 Worker 실행은 자동으로 반복하지 않았어.",)
        return ReadOnlyWorkerResult(
            answer=answer,
            evidence=evidence,
            limitations=limitations,
            worker=worker_result.worker,
            model=worker_result.model,
        )


@dataclass(frozen=True)
class OwnedWorkReceipt:
    id: str


@dataclass(frozen=True)
class OwnedWorkUpdate:
    id: str
    status: str
    text: str
    evidence: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()


class AsyncOwnedWorkCoordinator:
    """Run one slow read-only task without holding the conversation turn open."""

    def __init__(
        self,
        executor: OwnedWorkExecutor,
        *,
        completion_sink: Callable[[OwnedWorkUpdate], None] | None = None,
    ) -> None:
        self._executor = executor
        self._completion_sink = completion_sink
        self._lock = threading.Lock()
        self._active_id: str | None = None
        self._updates: dict[str, OwnedWorkUpdate] = {}
        self._threads: dict[str, threading.Thread] = {}

    def submit(self, request: WorkRequest) -> OwnedWorkReceipt:
        with self._lock:
            if self._active_id is not None:
                raise WorkerUnavailableError(
                    "이미 확인 중인 작업이 있습니다."
                )
            if len(self._updates) >= 10:
                raise WorkerUnavailableError(
                    "아직 전달하지 못한 작업 결과가 너무 많습니다."
                )
            work_id = str(uuid4())
            self._active_id = work_id
            thread = threading.Thread(
                target=self._run,
                args=(work_id, request),
                daemon=True,
                name=f"winter-owned-work-{work_id[:8]}",
            )
            self._threads[work_id] = thread
            thread.start()
        return OwnedWorkReceipt(work_id)

    def ready(self) -> tuple[OwnedWorkUpdate, ...]:
        with self._lock:
            return tuple(self._updates.values())

    def acknowledge(self, update_id: str) -> None:
        with self._lock:
            self._updates.pop(update_id, None)
            self._threads.pop(update_id, None)

    def wait_for_idle(self, timeout: float | None = None) -> bool:
        with self._lock:
            active_id = self._active_id
            thread = self._threads.get(active_id) if active_id is not None else None
        if thread is not None:
            thread.join(timeout=timeout)
        with self._lock:
            return self._active_id is None

    def _run(self, work_id: str, request: WorkRequest) -> None:
        try:
            result = self._executor.execute(request)
            text = _owned_work_answer(result)
            update = OwnedWorkUpdate(
                id=work_id,
                status="partial" if result.limitations else "completed",
                text=text,
                evidence=result.evidence,
                limitations=result.limitations,
            )
        except Exception:  # noqa: BLE001 - a background job must always release its slot
            update = OwnedWorkUpdate(
                id=work_id,
                status="failed",
                text=(
                    "확인을 끝내지 못했어. 확인한 척하지 않을게. "
                    "지금 대화는 그대로 이어갈 수 있어."
                ),
            )
        if self._completion_sink is not None:
            try:
                self._completion_sink(update)
            except Exception:  # noqa: BLE001 - delivery remains visible in the inbox
                update = OwnedWorkUpdate(
                    id=update.id,
                    status=update.status,
                    text=update.text,
                    evidence=update.evidence,
                    limitations=update.limitations
                    + ("대화 기록에는 저장하지 못했어.",),
                )
        with self._lock:
            self._updates[work_id] = update
            self._active_id = None


def _owned_work_answer(result: ReadOnlyWorkerResult) -> str:
    answer = result.answer.strip()
    if result.limitations:
        answer += " 다만 " + result.limitations[0]
    return answer
