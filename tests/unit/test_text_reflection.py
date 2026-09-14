"""P4 wiring tests: temporary stores, deterministic extraction, no Docker or model server."""

from io import StringIO
import json
import subprocess

import pytest

from companion import text_host, text_reflection, user_cli
from companion.adapters.sqlite_repository import SqliteConversationRepository
from companion.contracts import ConversationMessage
from companion.identity import CompanionIdentity, JsonIdentityRepository
from companion.memory import SqliteMemoryRepository
from companion.reflection import (
    ReflectionExtraction,
    ReflectionExtractionRequest,
    ReflectionInterpretationError,
    ReflectionInterpreterUnavailableError,
    SqliteReflectionRepository,
)
from companion.turn_understanding import MemoryProposal, SqliteTurnUnderstandingRepository


def identity(data):
    JsonIdentityRepository(data / "identity.json").save(CompanionIdentity(
        "겨울이", "대화 프로그램", ("편안함",), ("정직함",), ("반말",),
        ("없는 일을 꾸미지 않음",), "synthetic-p4",
    ))


def history(data, count):
    repository = SqliteConversationRepository(data / "conversations.sqlite")
    for index in range(1, count + 1):
        repository.append(ConversationMessage("user", f"나는 {index}년차 개발자야"))
    return repository


def candidate(data, index, *, kind="semantic"):
    reflections = SqliteReflectionRepository(data / "reflection.sqlite")
    text = f"나는 {index}년차 개발자야"
    reflections.record_extraction(ReflectionExtractionRequest(index, text), ReflectionExtraction((
        MemoryProposal(kind, f"천우는 {index}년차 개발자다", .95,
                       "candidate_stable", (text,)),
    )))
    return reflections


def command(data):
    return [
        "docker", "run", "--rm", "--name", "winter-text-session-test", "-i",
        "--network", "host", "--read-only", "--user", "1000:1000", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges", "--pids-limit", "128", "--memory", "1g",
        "--tmpfs", "/tmp:rw,nosuid,nodev,size=64m", "--log-opt", "max-size=5m",
        "--log-opt", "max-file=2", "-e", "PYTHONDONTWRITEBYTECODE=1",
        "-e", "PYTHONPATH=/workspace/src:/workspace", "-e", "WINTER_DATA_DIR=/workspace/data",
        "-e", "WINTER_LLM_URL=http://127.0.0.1:18081", "-v", "/project/src:/workspace/src:ro",
        "-v", "/project/configs:/workspace/configs:ro", "-v", f"{data}:/workspace/data:rw",
        "-w", "/workspace", "winter-ai:custom", "python", "-m", "companion.text_child",
        "--model-url", "http://127.0.0.1:18081", "--dialogue-policy", "companion",
    ]


def fake_docker(monkeypatch, *, state="", failure=None):
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        if failure:
            raise failure
        return subprocess.CompletedProcess(argv, 0, stdout=state, stderr="")

    monkeypatch.setattr(text_reflection.subprocess, "run", run)
    return calls


def test_detached_launch_copies_exact_child_configuration_once(tmp_path, monkeypatch):
    history(tmp_path, 3)
    calls = fake_docker(monkeypatch)
    output = StringIO()
    original = command(tmp_path)
    text_reflection.start_after_session(tmp_path, original, output)
    assert len(calls) == 2
    launched = calls[1]
    name = launched[launched.index("--name") + 1]
    assert name.startswith("winter-text-reflect-")
    assert not name.startswith("winter-text-session-")
    # All Docker options and the image must survive, apart from stdin/name and added detach/label.
    expected = original[:original.index("python")]
    expected.remove("-i")
    expected[expected.index("--name") + 1] = name
    assert launched[:2] + launched[5:launched.index("python")] == expected
    assert launched[2] == "-d"
    assert calls[0][4] == "label=" + launched[4]
    assert launched[launched.index("python"):] == [
        "python", "-m", "companion.user_cli", "--model-url", "http://127.0.0.1:18081",
        "--reflect-history", "--analysis-limit", "3", "--reflection-run-log",
    ]
    assert output.getvalue() == (
        "기억 정리를 뒤에서 시작했어 (발화 3개). 다음 대화 시작 때 기억한 걸 알려줄게.\n"
    )
    assert command(tmp_path) == original


@pytest.mark.parametrize("existing", [False, True])
def test_no_pending_messages_means_no_docker_or_output(tmp_path, monkeypatch, existing):
    if existing:
        history(tmp_path, 1)
        candidate(tmp_path, 1)
    calls = fake_docker(monkeypatch)
    output = StringIO()
    text_reflection.start_after_session(tmp_path, command(tmp_path), output)
    assert calls == []
    assert output.getvalue() == ""
    assert not (tmp_path / "reflection_runs.jsonl").exists()


@pytest.mark.parametrize("state", ["running\n", "created\n", "restarting\n", "paused\n"])
def test_same_store_running_container_is_not_duplicated(tmp_path, monkeypatch, state):
    history(tmp_path, 1)
    calls = fake_docker(monkeypatch, state=state)
    output = StringIO()
    text_reflection.start_after_session(tmp_path, command(tmp_path), output)
    assert len(calls) == 1
    assert output.getvalue() == ""


def test_pending_count_matches_has_extraction_and_caps_at_40(tmp_path):
    history(tmp_path, 43)
    refs = candidate(tmp_path, 43)
    refs.record_extraction_failure(42, "fixture")
    assert refs.has_extraction(43) and not refs.has_extraction(42)
    assert text_reflection.pending_count(tmp_path) == 40
    assert text_reflection.pending_count(tmp_path, limit=100) == 42


def host_main(tmp_path, monkeypatch, inputs, *, child_check=None, child_exit=0,
              events=None):
    output = StringIO()
    events = [] if events is None else events

    class Child:
        stdin = StringIO()
        stdout = StringIO('{"kind":"ready","name":"겨울이"}\n')

        def wait(self, timeout):
            events.append("child_exited")
            return child_exit

        def poll(self):
            return child_exit

    def popen(argv, **kwargs):
        events.append("child_started")
        if child_check:
            child_check(output)
        return Child()

    monkeypatch.setattr(text_host.subprocess, "Popen", popen)
    monkeypatch.setattr(text_host, "ProcessWorkerQueue", lambda *a, **kw: None)
    monkeypatch.setattr(text_host.sys, "stdin", StringIO(inputs))
    monkeypatch.setattr(text_host.sys, "stdout", output)
    monkeypatch.setattr(text_host.sys, "argv", [
        "host", "--project-root", str(tmp_path), "--container-name", "winter-text-session-test",
        "--audit-dir", str(tmp_path / "worker_sessions"), "--data-dir", str(tmp_path),
        "--", *command(tmp_path),
    ])
    return text_host.main(), output, events


def test_host_activation_and_notice_precede_real_child_runtime_memory_read(tmp_path, monkeypatch):
    import fcntl

    identity(tmp_path)
    refs = candidate(tmp_path, 1)
    candidate(tmp_path, 2)
    ordered = refs.list(status="candidate")
    calls = fake_docker(monkeypatch)
    monkeypatch.setattr(user_cli, "DATA", tmp_path)
    original_activate = text_host.activate_before_session

    def activate(data, stdout):
        with (data / "worker_sessions" / "terminal.lock").open("a") as lock:
            with pytest.raises(BlockingIOError):
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        original_activate(data, stdout)

    monkeypatch.setattr(text_host, "activate_before_session", activate)

    def child_check(output):
        from companion import text_child
        from companion.application import bootstrap

        original = bootstrap.build_text_runtime
        observed = []

        def build(*args, **kwargs):
            runtime = original(*args, **kwargs)
            observed.extend(runtime.resources.memory_repository.list_active())
            return runtime

        monkeypatch.setattr(text_child, "build_text_runtime", build)
        # No generation: construct the real child's runtime, then immediately close.
        assert text_child.run([], StringIO('{"kind":"close"}\n'), StringIO()) == 0
        assert {item.content for item in observed} == {item.content for item in ordered}
        assert all(item.source == "reflection_auto" for item in observed)
        assert "지난 대화에서 기억한 것 2개:" in output.getvalue()

    code, output, events = host_main(tmp_path, monkeypatch, "/exit\n",
                                     child_check=child_check)
    assert code == 0
    assert events == ["child_started", "child_exited"]
    assert refs.get(ordered[0].id).status == "active"
    assert refs.get(ordered[1].id).status == "active"
    text = output.getvalue()
    assert "후보" not in text and "승인" not in text
    assert text.count("지난 대화에서 기억한 것") == 1
    assert not calls


@pytest.mark.parametrize("answer", ["q", "", "s"])
def test_manual_review_defer_all_preserves_candidates(tmp_path, answer):
    refs = candidate(tmp_path, 1)
    candidate(tmp_path, 2)
    before = refs.list()
    output = StringIO()
    text_reflection.review_before_session(tmp_path, StringIO(answer + "\n"), output)
    assert refs.list() == before
    assert "승인 0, 보관 0, 제외 0, 미룸 2" in output.getvalue()
    assert "후보 2:" not in output.getvalue()


def test_host_no_candidates_starts_child_without_prompt(tmp_path, monkeypatch):
    fake_docker(monkeypatch)
    code, output, events = host_main(tmp_path, monkeypatch, "/exit\n")
    assert code == 0 and events[0] == "child_started"
    assert "후보" not in output.getvalue()
    assert not (tmp_path / "reflection.sqlite").exists()


@pytest.mark.parametrize("failure", [OSError("no docker socket"),
                                     subprocess.CalledProcessError(1, "docker run")])
def test_host_launch_failure_is_logged_after_clean_child_exit(tmp_path, monkeypatch, failure):
    history(tmp_path, 1)
    fake_docker(monkeypatch, failure=failure)
    code, output, events = host_main(tmp_path, monkeypatch, "/exit\n")
    assert code == 0 and events[-1] == "child_exited"
    rows = (tmp_path / "reflection_runs.jsonl").read_text().splitlines()
    assert len(rows) == 1
    entry = json.loads(rows[0])
    assert entry["error"] and entry["started_at"] <= entry["ended_at"]
    assert entry["processed"] == entry["created_candidates"] == 0
    assert "뒤에서 시작했어" not in output.getvalue()


def test_child_failure_does_not_trigger_extraction(tmp_path, monkeypatch):
    history(tmp_path, 1)
    calls = fake_docker(monkeypatch)
    code, _, _ = host_main(tmp_path, monkeypatch, "/exit\n", child_exit=1)
    assert code == 1 and not calls


def test_review_edit_store_and_mistyped_action_are_bounded(tmp_path):
    refs = candidate(tmp_path, 1)
    candidate(tmp_path, 2, kind="relationship")
    candidate(tmp_path, 3)
    ordered = refs.list()
    inputs = []
    for item in ordered:
        inputs.append("a\n" if item.kind == "relationship" else (
            "e\n수정된 개발 경력\n" if item.content == "천우는 1년차 개발자다" else "typo\n"
        ))
    output = StringIO()
    text_reflection.review_before_session(tmp_path, StringIO("".join(inputs)), output)
    assert "승인 1, 보관 1, 제외 0, 미룸 1" in output.getvalue()
    assert [m.content for m in SqliteMemoryRepository(tmp_path / "memories.sqlite").list_active()]
    assert sum(output.getvalue().count(f"후보 {i}:") for i in range(1, 4)) == 3


def test_logged_entry_uses_latest_40_and_preserves_candidate_gate(tmp_path, monkeypatch):
    identity(tmp_path)
    history(tmp_path, 43)
    monkeypatch.setattr(user_cli, "DATA", tmp_path)
    seen = []

    class Interpreter:
        def extract(self, request):
            seen.append(request.message_id)
            if request.message_id == 42:
                raise ReflectionInterpretationError("invalid fixture")
            if request.message_id == 41:
                raise ReflectionInterpreterUnavailableError("unavailable fixture")
            return ReflectionExtraction((MemoryProposal(
                "semantic", f"천우는 {request.message_id}년차 개발자다", .95, "candidate_stable",
                (request.user_text,),
            ),))

    monkeypatch.setattr(text_reflection, "FakeReflectionInterpreter", Interpreter)
    assert user_cli.main(["--backend", "fake", "--reflect-history", "--analysis-limit", "40",
                          "--reflection-run-log"]) == 1
    assert seen == list(range(43, 3, -1))
    row = json.loads((tmp_path / "reflection_runs.jsonl").read_text())
    assert {key: row[key] for key in ("processed", "completed", "failed", "deferred",
                                    "created_candidates", "error")} == {
        "processed": 40, "completed": 38, "failed": 1, "deferred": 1,
        "created_candidates": 38, "error": None,
    }
    assert not SqliteMemoryRepository(tmp_path / "memories.sqlite").list_active()
    assert len(SqliteReflectionRepository(tmp_path / "reflection.sqlite").list(
        status="candidate")) == 38
    turns = SqliteTurnUnderstandingRepository(tmp_path / "turn_understanding.sqlite")
    assert not turns.list(status="completed")
    assert len(turns.list(status="pending")) == 43


def test_logged_entry_records_bootstrap_error(tmp_path, monkeypatch):
    monkeypatch.setattr(user_cli, "DATA", tmp_path)
    assert user_cli.main(["--reflect-history", "--reflection-run-log"]) == 1
    row = json.loads((tmp_path / "reflection_runs.jsonl").read_text())
    assert "Identity" in row["error"] and row["processed"] == 0


def test_candidate_count_uses_readonly_connection(tmp_path, monkeypatch):
    candidate(tmp_path, 1)
    original = text_reflection.sqlite3.connect
    uris = []

    def connect(database, **kwargs):
        assert kwargs["uri"] is True
        uris.append(database)
        return original(database, **kwargs)

    monkeypatch.setattr(text_reflection.sqlite3, "connect", connect)
    assert text_reflection.candidate_count(tmp_path) == 1
    assert len(uris) == 1 and uris[0].endswith("?mode=ro")


def test_host_starts_detached_run_only_after_child_wait(tmp_path, monkeypatch):
    history(tmp_path, 41)
    exited = []
    lifecycle = []
    original_session = text_host.session
    original_start = text_host.start_after_session
    calls = fake_docker(monkeypatch)

    def session(*args, **kwargs):
        result = original_session(*args, **kwargs)
        assert calls == []
        exited.append("closed")
        return result

    def start(data, child_command, stdout):
        assert exited == ["closed"]
        assert lifecycle == ["child_started", "child_exited"]
        original_start(data, child_command, stdout)
        exited.append("detached")

    monkeypatch.setattr(text_host, "session", session)
    monkeypatch.setattr(text_host, "start_after_session", start)
    code, output, events = host_main(tmp_path, monkeypatch, "/exit\n", events=lifecycle)
    assert code == 0 and events == ["child_started", "child_exited"]
    assert exited == ["closed", "detached"]
    assert len(calls) == 2
    assert calls[1][calls[1].index("--analysis-limit") + 1] == "40"
    assert "발화 40개" in output.getvalue()


def test_docker_run_failure_includes_stderr_and_keeps_exit_success(tmp_path, monkeypatch):
    history(tmp_path, 1)
    calls = []

    def docker(argv, **kwargs):
        calls.append(argv)
        if argv[1] == "run":
            raise subprocess.CalledProcessError(125, argv, stderr="Cannot connect to Docker socket")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(text_reflection.subprocess, "run", docker)
    code, _, _ = host_main(tmp_path, monkeypatch, "/exit\n")
    assert code == 0 and len(calls) == 2
    entry = json.loads((tmp_path / "reflection_runs.jsonl").read_text())
    assert "Cannot connect to Docker socket" in entry["error"]


def test_completed_container_does_not_prevent_next_extraction(tmp_path, monkeypatch):
    history(tmp_path, 1)
    calls = fake_docker(monkeypatch, state="exited\n")
    text_reflection.start_after_session(tmp_path, command(tmp_path), StringIO())
    assert len(calls) == 2


def test_explicit_text_url_override_is_used_for_extraction(tmp_path):
    child = command(tmp_path) + ["--model-url", "http://127.0.0.1:19000"]
    detached = text_reflection.detached_command(child, label="test=data", limit=4)
    assert detached[detached.index("--model-url") + 1] == "http://127.0.0.1:19000"


def test_successful_logged_entry_records_once_and_does_not_reextract(tmp_path, monkeypatch):
    identity(tmp_path)
    history(tmp_path, 2)
    monkeypatch.setattr(user_cli, "DATA", tmp_path)
    args = ["--backend", "fake", "--reflect-history", "--analysis-limit", "2",
            "--reflection-run-log"]
    assert user_cli.main(args) == 0
    rows = (tmp_path / "reflection_runs.jsonl").read_text().splitlines()
    assert len(rows) == 1
    entry = json.loads(rows[0])
    assert entry["processed"] == entry["completed"] == 2
    assert entry["failed"] == entry["deferred"] == entry["created_candidates"] == 0
    assert entry["error"] is None
    calls = fake_docker(monkeypatch)
    text_reflection.start_after_session(tmp_path, command(tmp_path), StringIO())
    assert not calls
    assert (tmp_path / "reflection_runs.jsonl").read_text().splitlines() == rows


@pytest.mark.parametrize("edit", ["\n", ""])
def test_empty_edit_is_never_approved_or_reprompted(tmp_path, edit):
    refs = candidate(tmp_path, 1)
    before = refs.list()
    output = StringIO()
    text_reflection.review_before_session(tmp_path, StringIO("e\n" + edit), output)
    assert refs.list() == before
    assert output.getvalue().count("후보 1:") == 1
    assert output.getvalue().count("고친 내용>") == 1
    assert "승인 0, 보관 0, 제외 0, 미룸 1" in output.getvalue()
