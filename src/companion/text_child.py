"""One persistent Shared Core behind a trusted Host terminal supervisor."""

import sys
from typing import TextIO

from companion.adapters.fake import AdapterUnavailableError
from companion.adapters.stdio_worker import StdioApprovedWorker
from companion.application.bootstrap import build_text_runtime
from companion.cli.__main__ import text_runtime_config
from companion.dialogue.schedule_dialogue import ScheduleOutputError
from companion.owned_work import OwnedWorkUpdate
from companion.session_wire import SessionProtocolError, receive, send, string
from companion.user_cli import build_parser, configure_persistent_args
from companion.worker import WorkerApprovalDeclined, WorkerQueued, WorkerUnavailableError


def run(argv: list[str], stdin: TextIO, stdout: TextIO) -> int:
    args = configure_persistent_args(build_parser().parse_args(argv))
    # Provider construction is exclusively a Host responsibility.
    args.codex_worker = args.winter_work_canary = False
    if args.backend != "local" or args.prompt is not None:
        raise SessionProtocolError("supervised session requires interactive local mode")
    worker = StdioApprovedWorker(stdin, stdout)
    runtime = build_text_runtime(text_runtime_config(args), read_only_worker=worker)
    identity = runtime.resources.identity
    try:
        send(stdout, {"kind": "ready", "name": identity.name if identity else "겨울이"})
        while (frame := receive(stdin)) is not None:
            if frame == {"kind": "close"}:
                return 0
            if frame.get("kind") == "worker_update":
                if set(frame) != {"kind", "id", "status", "text"}:
                    raise SessionProtocolError("invalid Worker notification")
                update_id = string(frame["id"], limit=80)
                status = string(frame["status"], limit=20)
                if status not in {"completed", "partial", "failed"}:
                    raise SessionProtocolError("invalid Worker notification status")
                text = runtime.core.record_worker_update(OwnedWorkUpdate(
                    update_id, status, string(frame["text"], limit=64_000),
                ))
                send(stdout, {"kind": "notification", "id": update_id, "text": text})
                continue
            if set(frame) != {"kind", "id", "text"} or frame["kind"] != "turn":
                raise SessionProtocolError("expected user turn")
            turn_id = string(frame["id"], limit=80)
            text = string(frame["text"])
            worker.turn_id, worker.used = turn_id, False

            def partial(delta: str, *, current_turn_id: str = turn_id) -> None:
                # A delta may consist solely of a space or newline. Split large
                # pieces to retain the existing string and frame size bounds.
                for offset in range(0, len(delta), 8192):
                    part = string(delta[offset:offset + 8192], allow_whitespace=True)
                    send(stdout, {"kind": "partial", "id": current_turn_id, "text": part})

            try:
                response = runtime.core.respond_to_text_streaming(
                    text, on_sentence=lambda _: None, on_partial=partial,
                )
                reply: dict[str, object] = {"kind": "reply", "id": turn_id, "text": response.text}
                if response.worker_request is not None:
                    reply["worker_pending"] = True
                send(stdout, reply)
                if response.worker_request is not None:
                    # The normal answer is already reviewed, displayed and persisted.
                    # Approval disposition is a notification, never another model answer.
                    status = "partial"
                    try:
                        result = worker.execute(response.worker_request)
                        notice = f"코덱스 확인 결과가 왔어. {result.answer}"
                        if result.limitations:
                            notice += "\n확인 범위: " + " ".join(result.limitations)
                        status = "partial" if result.limitations else "completed"
                    except WorkerApprovalDeclined:
                        notice = "코덱스 요청을 취소했어."
                    except WorkerQueued:
                        notice = "코덱스 요청을 접수했어. 결과가 오면 알려줄게."
                    except WorkerUnavailableError:
                        status, notice = "failed", "코덱스 요청을 처리하지 못했어."
                    notice = runtime.core.record_worker_update(OwnedWorkUpdate(
                        f"{turn_id}:submission", status, notice,
                    ))
                    send(stdout, {"kind": "notification", "id": turn_id, "text": notice})
            except (AdapterUnavailableError, ScheduleOutputError):
                send(stdout, {
                    "kind": "error", "id": turn_id,
                    "text": "답변을 완성하지 못했어. 입력은 보존했고 다시 이야기할 수 있어.",
                })
    finally:
        runtime.close()
    return 0


def main() -> int:
    try:
        return run(sys.argv[1:], sys.stdin, sys.stdout)
    except (OSError, RuntimeError, ValueError):
        print("텍스트 세션 연결 또는 저장소 초기화에 실패했습니다.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
