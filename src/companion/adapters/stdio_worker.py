"""Worker port in Docker; it has neither provider credentials nor project tools."""

from typing import TextIO

from companion.session_wire import SessionProtocolError, receive, send, string
from companion.worker import (
    ReadOnlyWorkerRequest,
    ReadOnlyWorkerResult,
    WorkerApprovalDeclined,
    WorkerQueued,
    WorkerUnavailableError,
)


class StdioApprovedWorker:
    def __init__(self, stdin: TextIO, stdout: TextIO) -> None:
        self._stdin = stdin
        self._stdout = stdout
        self.turn_id = ""
        self.used = False

    def execute(self, request: ReadOnlyWorkerRequest) -> ReadOnlyWorkerResult:
        if self.used or not self.turn_id:
            raise WorkerUnavailableError("no active approved task channel")
        self.used = True
        send(self._stdout, {"kind": "worker_request", "id": self.turn_id, "task": request.task,
                            "why": request.why})
        reply = receive(self._stdin)
        if reply is None or reply.get("id") != self.turn_id:
            raise WorkerUnavailableError("worker result does not match the current turn")
        if reply.get("kind") == "worker_declined":
            raise WorkerApprovalDeclined("external execution declined")
        if reply.get("kind") == "worker_queued" and set(reply) == {"kind", "id", "job_id"}:
            string(reply["job_id"], limit=80)
            raise WorkerQueued("approved job accepted")
        if reply.get("kind") == "worker_error":
            raise WorkerUnavailableError("external worker failed")
        if set(reply) != {"kind", "id", "result"} or reply["kind"] != "worker_result":
            raise SessionProtocolError("invalid worker result frame")
        result = reply["result"]
        if not isinstance(result, dict) or set(result) != {
            "answer", "evidence", "limitations", "worker", "model",
        }:
            raise SessionProtocolError("invalid worker result fields")
        arrays = []
        for field in ("evidence", "limitations"):
            values = result[field]
            if not isinstance(values, list) or len(values) > 64:
                raise SessionProtocolError("invalid worker evidence/limitations")
            arrays.append(tuple(string(value) for value in values))
        return ReadOnlyWorkerResult(
            answer=string(result["answer"], limit=32_000), evidence=arrays[0],
            limitations=arrays[1], worker=string(result["worker"], limit=80),
            model=string(result["model"], limit=120),
        )
