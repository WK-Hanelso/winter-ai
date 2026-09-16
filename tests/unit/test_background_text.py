from io import StringIO
import json
import os
from pathlib import Path
from queue import Queue
import sqlite3
import subprocess
import sys
import threading

import pytest

from companion.adapters.fake import FakeChatModel
from companion.adapters.sqlite_repository import (
    ConversationRepositoryError,
    SqliteConversationRepository,
)
from companion.contracts import ConversationMessage
from companion.core import CompanionCore
from companion.owned_work import OwnedWorkReceipt, OwnedWorkUpdate
from companion.owned_work_repository import SqliteOwnedWorkRepository
from companion.text_host import session
from companion.text_jobs import ProcessWorkerQueue, run_job
from companion.work_orchestration import WorkRequest
from companion.worker import ReadOnlyWorkerResult, WorkerUnavailableError


def answer():
    return ReadOnlyWorkerResult(
        "CLI는 Docker야. (README.md:3)", ("README.md:3",), (), "codex", "fixture",
    )


def test_exact_job_claim_cannot_execute_another_approved_job(tmp_path):
    repo = SqliteOwnedWorkRepository(tmp_path / "jobs.sqlite", recovery="none")
    repo.enqueue("one", WorkRequest("README 확인"))
    assert run_job(repo, "different", lambda task: pytest.fail("wrong job")) == 1
    assert repo.status("one") == "pending"
    assert run_job(repo, "one", lambda task: answer()) == 0
    assert run_job(repo, "one", lambda task: pytest.fail("duplicate call")) == 1
    assert "README.md:3" in repo.ready()[0].text


def test_failed_recovery_never_resubmits_interrupted_external_work(tmp_path, monkeypatch):
    path = tmp_path / "jobs.sqlite"
    repo = SqliteOwnedWorkRepository(path)
    repo.enqueue("one", WorkRequest("README 확인"))
    repo.claim_pending()
    monkeypatch.setattr("companion.owned_work_repository._process_alive", lambda pid: False)
    recovered = SqliteOwnedWorkRepository(path, recovery="fail")
    assert recovered.status("one") == "failed"
    assert recovered.claim_pending() is None
    assert "자동 재실행하지" in recovered.ready()[0].text


def test_recovery_preserves_live_worker_and_unread_completion(tmp_path):
    path = tmp_path / "jobs.sqlite"
    repo = SqliteOwnedWorkRepository(path)
    repo.enqueue("one", WorkRequest("README 확인"))
    repo.claim_pending()
    assert SqliteOwnedWorkRepository(path, recovery="fail").status("one") == "running"
    repo.complete(OwnedWorkUpdate("one", "completed", "확인했어"))
    recovered = SqliteOwnedWorkRepository(path, recovery="fail")
    assert recovered.ready()[0].text == "확인했어"
    recovered.acknowledge("one")
    assert SqliteOwnedWorkRepository(path, recovery="fail").ready() == ()


def test_reopening_during_worker_startup_keeps_a_live_pending_owner(tmp_path):
    path = tmp_path / "jobs.sqlite"
    repo = SqliteOwnedWorkRepository(path)
    repo.enqueue("one", WorkRequest("README 확인"))
    repo.assign_pending_owner("one", os.getpid())
    assert SqliteOwnedWorkRepository(path, recovery="fail").status("one") == "pending"


def test_child_crash_becomes_a_failed_notification_without_retry(tmp_path):
    children = []

    def spawn(*args, **kwargs):
        process = subprocess.Popen([sys.executable, "-c", "raise SystemExit(1)"])
        children.append(process)
        return process

    queue = ProcessWorkerQueue(tmp_path / "jobs.sqlite", tmp_path, tmp_path, spawn=spawn)
    queue.submit(WorkRequest("Codex로 README 확인해줘"))
    assert children[0].wait(timeout=5) == 1
    assert queue.ready()[0].status == "failed"
    assert queue.ready()[0].status == "failed"
    assert len(children) == 1


def test_queue_only_spawns_a_bounded_approved_task_and_rejects_a_second(tmp_path):
    calls = []
    queue = ProcessWorkerQueue(tmp_path / "jobs.sqlite", tmp_path, tmp_path,
                               spawn=lambda *args, **kwargs: calls.append((args, kwargs)))
    receipt = queue.submit(WorkRequest("Codex로 README 확인해줘"))
    assert queue.repository.status(receipt.id) == "pending"
    with pytest.raises(WorkerUnavailableError):
        queue.submit(WorkRequest("또 확인해줘"))
    assert len(calls) == 1
    command, options = calls[0][0][0], calls[0][1]
    assert "--job-id" in command and receipt.id in command
    assert "Codex로 README 확인해줘" not in command  # Task stays in the local approval queue.
    assert options["start_new_session"] and options["close_fds"]
    assert "WINTER_PRIVATE_TOKEN" not in options["env"]


def test_foreground_notify_deduplicates_after_crash_without_losing_old_records(tmp_path):
    from companion.winter_events import SqliteWinterEventRepository, SystemClock

    path = tmp_path / "conversations.sqlite"
    repo = SqliteConversationRepository(path)
    original = ConversationMessage("user", "기존에 남긴 원문")
    repo.append(original)
    with sqlite3.connect(path) as source, sqlite3.connect(tmp_path / "backup.sqlite") as backup:
        source.backup(backup)
    events = SqliteWinterEventRepository(tmp_path / "events.sqlite")
    core = CompanionCore(FakeChatModel(), repo, winter_event_repository=events, clock=SystemClock())
    update = OwnedWorkUpdate("job-one", "completed", "파일 확인 결과야. (README.md:3)")
    core.record_worker_update(update)
    # Simulate a crash after Core commit but before UI acknowledgement.
    restarted = CompanionCore(FakeChatModel(), SqliteConversationRepository(path),
                               winter_event_repository=events, clock=SystemClock())
    assert restarted.record_worker_update(update) == update.text
    assert repo.list_messages() == (original, ConversationMessage("assistant", update.text))
    assert SqliteConversationRepository(tmp_path / "backup.sqlite").list_messages() == (original,)
    with pytest.raises(ConversationRepositoryError):
        restarted.record_worker_update(OwnedWorkUpdate("job-one", "completed", "forged result"))


def test_detached_result_survives_foreground_restart_without_an_external_api(tmp_path):
    path = tmp_path / "jobs.sqlite"
    repo = SqliteOwnedWorkRepository(path)
    repo.enqueue("one", WorkRequest("README 확인"))
    code = (
        "import sys; from pathlib import Path; "
        "from companion.owned_work_repository import SqliteOwnedWorkRepository; "
        "from companion.text_jobs import run_job; "
        "from companion.worker import ReadOnlyWorkerResult; "
        "r=SqliteOwnedWorkRepository(Path(sys.argv[1]), recovery='none'); "
        "run_job(r, 'one', lambda task: ReadOnlyWorkerResult('완료',(),(),'fixture','fixture'))"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", code, str(path)], start_new_session=True,
        env=dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2] / "src")),
    )
    assert child.wait(timeout=10) == 0
    del repo
    recovered = SqliteOwnedWorkRepository(path, recovery="fail")
    assert recovered.ready()[0].text.endswith("완료")


class QueueTerminal:
    def __init__(self):
        self.lines = Queue()

    def readline(self, limit):
        return self.lines.get(timeout=10)


class FakeCorePipe:
    def __init__(self):
        self.frames = Queue()
        self.frames.put({"kind": "ready", "name": "겨울이"})
        self.turns = []
        self.normal_replied = threading.Event()

    def write(self, line):
        frame = json.loads(line)
        kind = frame["kind"]
        if kind == "turn":
            self.turns.append(frame["text"])
            if "Codex" in frame["text"]:
                self.frames.put({"kind": "reply", "id": frame["id"], "text": "먼저 이야기하자.",
                                 "worker_pending": True})
                self.frames.put({
                    "kind": "worker_request", "id": frame["id"], "task": frame["text"],
                    "why": "실제 파일 확인이 필요해.",
                })
            else:
                self.frames.put({"kind": "reply", "id": frame["id"], "text": "응, 이야기하자."})
                self.normal_replied.set()
        elif kind in {"worker_queued", "worker_declined"}:
            self.frames.put({"kind": "notification", "id": frame["id"], "text": "접수 상태야."})
        elif kind == "worker_update":
            self.frames.put({"kind": "notification", "id": frame["id"], "text": frame["text"]})

    def readline(self, limit):
        return json.dumps(self.frames.get(timeout=10), ensure_ascii=False) + "\n"

    def flush(self):
        pass


def test_host_chats_while_job_runs_and_notifies_without_another_user_input():
    terminal, child, output = QueueTerminal(), FakeCorePipe(), StringIO()
    completed, delivered = threading.Event(), threading.Event()

    class Jobs:
        calls = 0

        def submit(self, request):
            self.calls += 1
            assert not completed.is_set()
            return OwnedWorkReceipt("job-one")

        def ready(self):
            if completed.is_set() and not delivered.is_set():
                return (OwnedWorkUpdate("job-one", "completed", "실제 작업 결과야."),)
            return ()

        def acknowledge(self, update_id):
            assert "실제 작업 결과야." in output.getvalue()
            delivered.set()

    jobs, errors = Jobs(), []

    def run_session():
        try:
            session(child, child, terminal, output, lambda task: pytest.fail("sync path"),
                    scope="공유 소스", background=jobs)
        except Exception as error:
            errors.append(error)

    thread = threading.Thread(target=run_session)
    thread.start()
    terminal.lines.put("Codex로 README 확인해줘\n")
    terminal.lines.put("승인\n")
    terminal.lines.put("작업 중이지만 나랑 이야기하자\n")
    assert child.normal_replied.wait(5)
    assert not completed.is_set() and jobs.calls == 1
    completed.set()
    assert delivered.wait(5)  # No further terminal input was provided.
    terminal.lines.put("/exit\n")
    terminal.lines.put("")
    thread.join(5)
    assert not thread.is_alive() and not errors
