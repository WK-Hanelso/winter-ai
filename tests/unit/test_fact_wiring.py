"""P5 internal CLI and notices: temporary state, no live model or Docker."""

from dataclasses import asdict
from io import StringIO
import json
import sqlite3

import pytest

from companion import text_host, text_reflection, user_cli
from companion.adapters.sqlite_repository import SqliteConversationRepository
from companion.contracts import ConversationMessage
from companion.fact_extraction import Fact, FactExtractionRequest, SqliteFactExtractionRepository


def history(data, count):
    repository = SqliteConversationRepository(data / "conversations.sqlite")
    for index in range(count):
        repository.append(ConversationMessage("user", f"나는 경력 {index}인 개발자야"))
    (data / "identity.json").write_text("{}")  # Internal extraction only checks identity presence.


def store(data, index, *, confidence=.95, source="extracted", content=None):
    repository = SqliteFactExtractionRepository(data / "memories.sqlite")
    content = content or f"천우는 경력{index} 개발자다."
    request = FactExtractionRequest(index, content)
    repository.store(request, (Fact("semantic", content, confidence, "durable", content),),
                     total=1, rejected=0)
    if source != "extracted":
        with sqlite3.connect(data / "memories.sqlite") as connection:
            connection.execute("UPDATE memories SET source = ? WHERE evidence_ref = ?",
                               (source, f"conversation:{index}"))
    return repository


@pytest.mark.parametrize("count", [0, 1, 5])
def test_notice_new_active_only_once_and_updates_watermark(tmp_path, count):
    SqliteFactExtractionRepository(tmp_path / "memories.sqlite")
    for index in range(1, count + 1):
        store(tmp_path, index)
    store(tmp_path, 100, confidence=.5)
    store(tmp_path, 101, source="reflection_auto")
    store(tmp_path, 102, source="user_explicit")
    output = StringIO()
    text_reflection.notify_before_session(tmp_path, output)
    text = output.getvalue()
    if count:
        assert text.startswith(f"지난 대화에서 기억한 것 {count}개:")
        assert len(text.splitlines()) == 1
        assert "경력1 개발자" in text
        if count > 3:
            assert text.endswith(" 외 2개\n")
    else:
        assert text == ""
    assert "경력100" not in text and "경력101" not in text and "경력102" not in text
    first = json.loads((tmp_path / "fact_notice.json").read_text())["last_notified_at"]
    repeat = StringIO()
    text_reflection.notify_before_session(tmp_path, repeat)
    assert repeat.getvalue() == ""
    assert json.loads((tmp_path / "fact_notice.json").read_text())["last_notified_at"] >= first
    store(tmp_path, 200)
    next_notice = StringIO()
    text_reflection.notify_before_session(tmp_path, next_notice)
    assert next_notice.getvalue() == '지난 대화에서 기억한 것 1개: “천우는 경력200 개발자다.”\n'
    assert not (tmp_path / "fact_notice.tmp").exists()


def test_notice_keeps_one_line_and_forget_is_excluded(tmp_path):
    repo = store(tmp_path, 1, content="첫 번째\n내용" + "가" * 50)
    store(tmp_path, 2)
    repo.memories.forget_explicit("경력2")
    output = StringIO()
    text_reflection.notify_before_session(tmp_path, output)
    assert len(output.getvalue().splitlines()) == 1
    assert "…" in output.getvalue() and "경력2" not in output.getvalue()


def test_notice_snapshot_waits_for_inflight_fact_commit(tmp_path, monkeypatch):
    # BEGIN IMMEDIATE must precede cutoff acquisition, preventing older uncommitted
    # extraction timestamps from being hidden behind the notice watermark.
    store(tmp_path, 1)
    statements = []
    connect = sqlite3.connect

    def traced(*args, **kwargs):
        connection = connect(*args, **kwargs)
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(text_reflection.sqlite3, "connect", traced)
    text_reflection.notify_before_session(tmp_path, StringIO())
    assert statements[0] == "BEGIN IMMEDIATE"
    assert statements[1].startswith("SELECT content")


def test_host_uses_new_notice_entry():
    assert text_host.notify_before_session is text_reflection.notify_before_session


def test_cli_all_oldest_first_partial_failure_resume_and_run_log(tmp_path, monkeypatch, capsys):
    history(tmp_path, 43)
    monkeypatch.setattr(user_cli, "DATA", tmp_path)
    seen = []
    fail = {2}

    class Extractor:
        def extract(self, request):
            seen.append(request.message_id)
            if request.message_id in fail:
                raise RuntimeError("synthetic extraction failure")
            return (Fact("semantic", f"천우는 경력{request.message_id} 개발자다.", .95,
                         "durable", request.user_text),)

        def verify(self, request, facts):
            return (request.message_id != 3,)

    monkeypatch.setattr(text_reflection, "FakeFactExtractor", Extractor)
    args = ["--backend", "fake", "--extract-facts", "--run-log"]
    assert user_cli.main(args + ["--limit", "4"]) == 1
    assert seen == [1, 2, 3, 4]
    assert "기억 정리 진행: 4/4" in capsys.readouterr().out
    fail.clear()
    seen.clear()
    assert user_cli.main(args + ["--all"]) == 0
    assert seen == [2, *range(5, 44)]
    assert "기억 정리 진행: 40/40" in capsys.readouterr().out
    log = [json.loads(line) for line in (tmp_path / "fact_runs.jsonl").read_text().splitlines()]
    assert {key: log[0][key] for key in ("processed", "stored", "rejected", "failed")} == {
        "processed": 4, "stored": 2, "rejected": 1, "failed": 1,
    }
    assert log[1]["processed"] == log[1]["stored"] == 40
    assert all(row["started_at"] <= row["ended_at"] and row["error"] is None for row in log)
    assert text_reflection.pending_count(tmp_path) == 0
    seen.clear()
    assert user_cli.main(args + ["--all"]) == 0
    assert seen == []
    assert not (tmp_path / "reflection.sqlite").exists()
    assert not (tmp_path / "reflection_runs.jsonl").exists()
    assert not (tmp_path / "turn_understanding.sqlite").exists()


def test_cli_all_has_no_forty_cap_and_does_not_require_run_log(tmp_path, monkeypatch, capsys):
    history(tmp_path, 43)
    monkeypatch.setattr(user_cli, "DATA", tmp_path)
    assert user_cli.main(["--backend", "fake", "--extract-facts", "--all"]) == 0
    assert "기억 정리 진행: 43/43" in capsys.readouterr().out
    assert len(SqliteFactExtractionRepository(tmp_path / "memories.sqlite").completed_ids()) == 43
    assert not (tmp_path / "fact_runs.jsonl").exists()


def test_cli_logs_missing_identity_without_creating_memory_db(tmp_path, monkeypatch):
    monkeypatch.setattr(user_cli, "DATA", tmp_path)
    assert user_cli.main(["--extract-facts", "--all", "--run-log"]) == 1
    row = json.loads((tmp_path / "fact_runs.jsonl").read_text())
    assert "Identity" in row["error"] and row["processed"] == 0
    assert row["lexical_rejected"] == row["lexical_demoted"] == 0
    assert not (tmp_path / "memories.sqlite").exists()


def test_p51_run_log_counts_lexical_rejections_and_demotions(tmp_path, monkeypatch):
    history(tmp_path, 0)
    SqliteConversationRepository(tmp_path / "conversations.sqlite").append(
        ConversationMessage("user", "6년차 개발자. 연구원"),
    )
    monkeypatch.setattr(user_cli, "DATA", tmp_path)

    class Extractor:
        def extract(self, request):
            return (
                Fact("semantic", "천우는 6년차 개발자다.", .95, "durable", "6년차 개발자"),
                Fact("semantic", "천우는 선임 연구원이다.", .95, "durable", "연구원"),
                Fact("semantic", "천우는 선임 데이터베이스 연구원이다.", .95, "durable", "연구원"),
            )

        def verify(self, request, facts):
            assert len(facts) == 2 and facts[1].confidence == .55
            return (True, True)

    monkeypatch.setattr(text_reflection, "FakeFactExtractor", Extractor)
    args = ["--backend", "fake", "--extract-facts", "--all", "--run-log"]
    assert user_cli.main(args) == 0
    row = json.loads((tmp_path / "fact_runs.jsonl").read_text())
    assert {key: row[key] for key in (
        "processed", "stored", "rejected", "failed", "lexical_rejected", "lexical_demoted",
    )} == dict(processed=1, stored=2, rejected=1, failed=0, lexical_rejected=1, lexical_demoted=1)
    # A completed-message rerun has fresh zero counters and no duplicate work.
    assert user_cli.main(args) == 0
    rerun = json.loads((tmp_path / "fact_runs.jsonl").read_text().splitlines()[-1])
    assert rerun["processed"] == rerun["lexical_rejected"] == rerun["lexical_demoted"] == 0


def test_cli_invalid_limit_and_conflicting_scope_do_not_extract(tmp_path, monkeypatch):
    history(tmp_path, 1)
    monkeypatch.setattr(user_cli, "DATA", tmp_path)
    assert user_cli.main(["--extract-facts", "--limit", "0", "--run-log"]) == 1
    assert not (tmp_path / "memories.sqlite").exists()
    with pytest.raises(SystemExit):
        user_cli.main(["--extract-facts", "--limit", "3", "--all"])


def test_pending_count_missing_table_readonly_and_version_aware(tmp_path):
    history(tmp_path, 2)
    assert text_reflection.pending_count(tmp_path) == 2
    repository = store(tmp_path, 1)
    before = [asdict(m) for m in repository.memories.list()]
    assert text_reflection.pending_count(tmp_path) == 1
    assert [asdict(m) for m in repository.memories.list()] == before
    with sqlite3.connect(tmp_path / "memories.sqlite") as connection:
        connection.execute("UPDATE fact_extractions SET version = 0")
    assert text_reflection.pending_count(tmp_path) == 2
    with sqlite3.connect(tmp_path / "memories.sqlite") as connection:
        connection.execute("DROP TABLE fact_extractions")
    assert text_reflection.pending_count(tmp_path) == 2


def test_memories_entry_shows_extracted_candidates_and_preserves_legacy_review(
    tmp_path, monkeypatch, capsys,
):
    history(tmp_path, 1)
    repo = store(tmp_path, 1, confidence=.59)
    store(tmp_path, 2)
    before = repo.memories.list()
    captured = []
    monkeypatch.setattr(user_cli, "DATA", tmp_path)
    monkeypatch.setattr(user_cli, "run", lambda args: captured.append(args) or 0)
    assert user_cli.main(["--backend", "fake", "--review-memory-candidates"]) == 0
    assert captured[0].review_memory_candidates is True
    assert capsys.readouterr().out == (
        "[candidate/semantic] 천우는 경력1 개발자다. (확신도 0.59)\n"
    )
    assert repo.memories.list() == before
