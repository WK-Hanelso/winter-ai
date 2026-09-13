"""Host-owned terminal and per-task approval; the shared Companion runs in Docker."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
import json
import os
from pathlib import Path
from queue import Empty, Queue
import select
import subprocess
import sys
import threading
from typing import TextIO, cast
from uuid import uuid4

from companion.adapters.codex_gateway_worker import CodexTypedProjectWorker
from companion.session_wire import SessionProtocolError, receive, send, string
from companion.text_jobs import ProcessWorkerQueue
from companion.text_reflection import review_before_session, start_after_session
from companion.work_orchestration import WorkerTaskPacket, WorkRequest
from companion.worker import ReadOnlyWorkerResult, WorkerUnavailableError
from companion.worker_gateway import GatewayWorkerResult


def project_packet(task: str) -> WorkerTaskPacket:
    """Permissions are fixed here, never inferred from model output or task text."""
    return WorkerTaskPacket(
        schema_version=2, objective=task,
        desired_outcome="요청에 답하는 간결한 한국어 설명과 실제 파일 근거",
        capabilities=("project.inspect",), resources=("project",), context=(),
        definition_of_done=("읽은 파일의 근거로 답하고 미확인 사항을 구분한다.",),
        prohibited_actions=("파일 수정, shell, network, 개인정보 및 인증정보 접근 금지",),
        required_report=("summary", "evidence"),
    )


def checked_answer(result: GatewayWorkerResult) -> ReadOnlyWorkerResult:
    # The adapter validates IDs against actual reads. Unconstrained summary is not evidence.
    locators = {item.evidence_id: item.locator for item in result.report.evidence}
    lines = [
        f"{claim.content} ({', '.join(locators[key] for key in claim.evidence_ids)})"
        for claim in result.report.claims if claim.evidence_ids
    ]
    if not lines:
        raise WorkerUnavailableError("근거에 연결된 Worker 답변이 없습니다.")
    return ReadOnlyWorkerResult(
        answer="\n".join(lines), evidence=tuple(locators.values()),
        limitations=result.report.limitations + result.report.unresolved,
        worker=result.worker, model=result.model,
    )


def audited_execution(
    worker: CodexTypedProjectWorker, task: str, audit_dir: Path,
) -> ReadOnlyWorkerResult:
    """Persist approval before execution; never auto-retry an interrupted external task."""
    audit_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = audit_dir / f"{uuid4().hex}.jsonl"
    with path.open("x", encoding="utf-8") as output:
        os.fchmod(output.fileno(), 0o600)

        def record(kind: str, payload: dict[str, object]) -> None:
            output.write(json.dumps({"kind": kind, "at": datetime.now(UTC).isoformat(),
                                     **payload}, ensure_ascii=False) + "\n")
            output.flush()
            os.fsync(output.fileno())

        packet = project_packet(task)
        record("approved", {"packet": packet.to_dict()})
        try:
            result = worker.execute(packet)
            answer = checked_answer(result)
        except (WorkerUnavailableError, OSError, ValueError):
            record("failed", {"status": "no_accepted_result"})
            raise
        record("completed", {"result": asdict(answer), "report": result.report.to_dict(),
                             "gateway_audit": [asdict(item) for item in result.audit_log]})
        return answer


def session(
    child_in: TextIO, child_out: TextIO, terminal_in: TextIO, terminal_out: TextIO,
    execute: Callable[[str], ReadOnlyWorkerResult], *, scope: str,
    background: ProcessWorkerQueue | None = None,
) -> int:
    """One input owner prevents approval prompts from racing Docker's stdin."""
    def display(text: str) -> None:
        print(text, file=terminal_out, flush=True)

    ready = receive(child_out)
    if ready is None or set(ready) != {"kind", "name"} or ready["kind"] != "ready":
        raise SessionProtocolError("Core did not become ready")
    name = string(ready["name"], limit=80)
    display(f"{name} 준비됐어. /exit로 종료할 수 있어.")
    display("Codex에 프로젝트 확인을 부탁하면, 외부로 보낼 내용을 먼저 확인할게.")
    lines: Queue[str | BaseException] = Queue(maxsize=16)

    def read_terminal() -> None:
        try:
            while True:
                line = terminal_in.readline(8194)
                lines.put(line)
                if not line:
                    return
        except Exception as error:  # noqa: BLE001 - transport failures go to the input owner
            lines.put(error)

    terminal_fd: int | None = None
    if background is not None:
        try:
            terminal_fd = terminal_in.fileno()
        except (AttributeError, OSError, ValueError):
            pass
    if background is not None and terminal_fd is None:
        threading.Thread(target=read_terminal, daemon=True, name="winter-terminal-input").start()
    pending_input = b""
    input_eof = False

    def notify_results() -> bool:
        if background is None:
            return False
        updates = background.ready()
        for update in updates:
            send(child_in, {"kind": "worker_update", "id": update.id,
                            "status": update.status, "text": update.text})
            reply = receive(child_out)
            if reply != {"kind": "notification", "id": update.id, "text": update.text}:
                raise SessionProtocolError("notification was not persisted by the Core")
            display(f"\n{name}> {update.text}")
            background.acknowledge(update.id)
        return bool(updates)

    def input_line(*, notifications: bool) -> str:
        nonlocal pending_input, input_eof
        if background is None:
            return terminal_in.readline(8194)
        while True:
            if terminal_fd is not None:
                if b"\n" in pending_input or input_eof:
                    line, separator, pending_input = pending_input.partition(b"\n")
                    return (line + separator).decode("utf-8")
                if select.select([terminal_fd], [], [], 0.2)[0]:
                    part = os.read(terminal_fd, 4096)
                    pending_input += part
                    input_eof = not part
                    if len(pending_input) > 32776:
                        raise SessionProtocolError("terminal input exceeds byte limit")
                    continue
                if notifications and notify_results():
                    print("You> ", end="", file=terminal_out, flush=True)
                continue
            try:
                value = lines.get(timeout=0.2)
                if isinstance(value, BaseException):
                    raise SessionProtocolError("terminal input failed") from value
                return value
            except Empty:
                if notifications and notify_results():
                    print("You> ", end="", file=terminal_out, flush=True)

    while True:
        notify_results()
        print("You> ", end="", file=terminal_out, flush=True)
        line = input_line(notifications=True)
        if not line or line.strip() in {"/exit", "/quit"}:
            send(child_in, {"kind": "close"})
            return 0
        if len(line) > 8192:
            raise SessionProtocolError("user input exceeds limit")
        task = line.strip()
        if not task:
            continue
        turn_id = uuid4().hex
        send(child_in, {"kind": "turn", "id": turn_id, "text": task})
        requested = False
        while True:
            frame = receive(child_out)
            if frame is None or frame.get("id") != turn_id:
                raise SessionProtocolError("Core response does not match current turn")
            kind = frame.get("kind")
            if kind in {"reply", "error"} and set(frame) == {"kind", "id", "text"}:
                display(f"{name}> {string(frame['text'], limit=64_000)}")
                break
            if (kind != "worker_request" or set(frame) != {"kind", "id", "task"}
                    or frame["task"] != task or requested):
                raise SessionProtocolError("unbound or duplicate Worker request")
            requested = True
            display(f"[외부 Codex / 읽기 전용]\n보낼 요청: {task}\n읽기 범위: {scope}")
            display("대화 이력·기억·Identity·Reference·.env는 보내지 않아. 코드 수정도 하지 않아.")
            print("이 요청과 범위의 외부 전송을 승인하려면 '승인' 입력 (나머지는 취소)> ",
                  end="", file=terminal_out, flush=True)
            if input_line(notifications=False).strip() != "승인":
                send(child_in, {"kind": "worker_declined", "id": turn_id})
                continue
            if background is not None:
                try:
                    receipt = background.submit(WorkRequest(task))
                    send(child_in, {"kind": "worker_queued", "id": turn_id,
                                    "job_id": receipt.id})
                except (WorkerUnavailableError, OSError, ValueError):
                    display("접수하지 못했어. 진행 중인 작업이나 요청 길이(800자)를 확인해줘.")
                    send(child_in, {"kind": "worker_error", "id": turn_id})
                continue
            display("Codex가 읽기 전용으로 확인 중이야. 이 초안은 결과가 올 때까지 기다려.")
            try:
                result = execute(task)
                send(child_in, {"kind": "worker_result", "id": turn_id,
                                "result": asdict(result)})
            except (WorkerUnavailableError, OSError, ValueError):
                display("Worker 실행 또는 근거 검증에 실패했어. 성공한 결과로 기록하지 않을게.")
                send(child_in, {"kind": "worker_error", "id": turn_id})


def main() -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--container-name", required=True)
    parser.add_argument("--audit-dir", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--codex-executable", default="codex")
    parser.add_argument("--codex-model", default="gpt-5.6-sol")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command or not args.container_name.startswith("winter-text-session-"):
        parser.error("a dedicated Docker child is required")
    worker = CodexTypedProjectWorker(
        args.project_root, executable=args.codex_executable, model=args.codex_model,
        allowed_roots=("README.md", "pyproject.toml", "src", "tests"),
    )
    args.audit_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    # One UI owns delivery acknowledgements; detached approved jobs do not hold this lock.
    import fcntl

    session_lock = (args.audit_dir / "terminal.lock").open("a")
    try:
        fcntl.flock(session_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        session_lock.close()
        print("같은 기록의 텍스트 대화창이 이미 열려 있습니다.", file=sys.stderr)
        return 1
    jobs = ProcessWorkerQueue(
        args.audit_dir / "jobs.sqlite", args.project_root, args.audit_dir,
        executable=args.codex_executable, model=args.codex_model,
    )
    process: subprocess.Popen[str] | None = None
    try:
        data = args.data_dir or args.audit_dir.parent
        review_before_session(data, sys.stdin, sys.stdout)
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   text=True, encoding="utf-8", bufsize=1)
        assert process.stdin is not None and process.stdout is not None
        result = session(cast(TextIO, process.stdin), cast(TextIO, process.stdout),
                         sys.stdin, sys.stdout,
                         lambda task: audited_execution(worker, task, args.audit_dir),
                         scope=f"{args.project_root}: README.md, pyproject.toml, src/, tests/",
                         background=jobs)
        process.stdin.close()
        if process.wait(timeout=10) != 0:
            return 1
        start_after_session(data, command, sys.stdout)
        return result
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError, KeyboardInterrupt):
        print("세션 연결이 종료됐습니다. 완료되지 않은 Worker 결과를 성공으로 처리하지 않습니다.",
              file=sys.stderr)
        return 1
    finally:
        session_lock.close()
        if process is not None:
            if process.poll() is None:
                try:
                    subprocess.run(["docker", "stop", "--time", "2", args.container_name],
                                   capture_output=True, timeout=10, check=False)
                except (OSError, subprocess.SubprocessError):
                    pass
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
            for stream in (process.stdin, process.stdout):
                if stream is not None:
                    stream.close()


if __name__ == "__main__":
    raise SystemExit(main())
