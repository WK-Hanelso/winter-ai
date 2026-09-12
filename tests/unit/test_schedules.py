"""Synthetic schedule commands only; every database lives under pytest tmp_path."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
import json
import sqlite3
from threading import Barrier

import pytest

from companion.schedules import (
    KST,
    ScheduleCommand,
    ScheduleRepository,
    ScheduleService,
    resolve_date,
    resolve_date_range,
    resolve_time,
)

NOW = datetime(2026, 9, 14, 9, tzinfo=KST)
CUE = "일정 저장"
SOURCE = f"합성 검증용 {CUE} 요청"


@dataclass
class FixedClock:
    value: datetime = NOW
    calls: int = 0

    def now(self) -> datetime:
        self.calls += 1
        return self.value


@pytest.fixture
def service(tmp_path):
    return ScheduleService(ScheduleRepository(tmp_path / "knowledge.sqlite"), FixedClock())


def create_command(command_id="create-1", **fields):
    return ScheduleCommand(
        operation="create", command_id=command_id, source_message_id=1, cue=CUE,
        expected_revision=0, fields={"title": "합성 일정", "date": "내일", **fields},
    )


def edit_command(created, operation="update", command_id="edit-1", **fields):
    return ScheduleCommand(
        operation=operation, command_id=command_id, source_message_id=2, cue=CUE,
        expected_revision=created.revision,
        target={"schedule_id": created.schedule_id}, fields=fields,
    )


def query_command(**fields):
    return ScheduleCommand(
        operation="query", command_id="query-1", source_message_id=3, cue=CUE,
        target={"title_hint": "합성"}, fields=fields,
    )


def execute(service, command, **kwargs):
    return service.execute(command, source_text=SOURCE, **kwargs)


def db_snapshot(service):
    with sqlite3.connect(service.repository.path) as connection:
        return (
            connection.execute("SELECT * FROM schedules ORDER BY id").fetchall(),
            connection.execute("SELECT * FROM change_history ORDER BY command_id").fetchall(),
        )


def test_create_move_cancel_query_and_previous_revisions_survive_restart(service):
    created = execute(service, create_command(time="오후 3시", place="동쪽", participants="검증자"))
    assert created.status == "applied" and created.revision == 1
    original = created.schedules[0]
    assert (original.date, original.time, original.status) == ("2026-09-15", "15:00", "planned")
    assert original.created_at == "2026-09-14T00:00:00+00:00"
    service.clock.value = datetime(2026, 9, 14, 10, tzinfo=KST)
    moved = execute(service, edit_command(created, date="금요일"))
    assert moved.status == "applied" and moved.revision == 2
    assert moved.changed_fields == {"date": "2026-09-18", "status": "changed"}
    current = moved.schedules[0]
    assert (current.time, current.place, current.participants_text) == ("15:00", "동쪽", "검증자")
    assert current.created_at == original.created_at and current.updated_at != original.updated_at
    assert current.source_message_id == 2
    cancelled = execute(service, edit_command(moved, "cancel", "cancel-1"))
    assert cancelled.revision == 3 and cancelled.schedules[0].status == "cancelled"
    snapshot = db_snapshot(service)
    restarted = ScheduleService(ScheduleRepository(service.repository.path), FixedClock())
    result = execute(restarted, query_command(date_from="2026-09-18", date_to="2026-09-18"))
    assert result.status == "applied" and result.schedules == cancelled.schedules
    assert execute(restarted, query_command(), active_only=True).schedules == ()
    assert restarted.repository.get(original.id) == cancelled.schedules[0]
    assert restarted.repository.get_revision(original.id, 1) == original
    assert restarted.repository.get_revision(original.id, 2) == current
    assert restarted.repository.get_revision(original.id, 3) == cancelled.schedules[0]
    assert restarted.repository.get_revision(original.id, 4) is None
    history = restarted.repository.history(original.id)
    assert [(h["before_revision"], h["after_revision"]) for h in history] == [
        (0, 1), (1, 2), (2, 3),
    ]
    assert [h["source_message_id"] for h in history] == [1, 2, 2]
    change = json.loads(history[1]["change_json"])
    assert change["before"] == original.__dict__ and change["after"] == current.__dict__
    assert change["command"]["request"]["fields"] == {"date": "금요일"}
    assert change["command"]["normalized_fields"] == {"date": "2026-09-18"}
    assert change["patch"] == moved.changed_fields
    assert SOURCE not in history[1]["change_json"]
    assert db_snapshot(restarted) == snapshot


@pytest.mark.parametrize("operation", ["create", "update", "cancel"])
def test_retry_after_midnight_and_restart_returns_original_receipt(service, operation):
    command = create_command()
    created = execute(service, command)
    if operation != "create":
        fields = {"date": "모레"} if operation == "update" else {}
        command = edit_command(created, operation, **fields)
    applied = execute(service, command)
    # Later changes must not alter the receipt replayed for an earlier command.
    if operation != "cancel":
        execute(service, edit_command(applied, command_id="later", place="서쪽"))
    before = db_snapshot(service)
    clock = FixedClock(datetime(2026, 10, 1, tzinfo=KST))
    restarted = ScheduleService(ScheduleRepository(service.repository.path), clock)
    assert execute(restarted, command) == applied
    assert clock.calls == 0
    assert db_snapshot(restarted) == before
    conflict = execute(restarted, replace(command, cue="검증용 일정"))
    assert conflict.status == "rejected" and conflict.reason == "command_id_conflict"
    assert db_snapshot(restarted) == before


@pytest.mark.parametrize("replacement", [
    {"fields": {"title": "다른 제목", "date": "내일"}},
    {"source_message_id": 9}, {"original_source_message_id": 8},
    {"received_at": NOW},
])
def test_same_id_different_command_is_rejected(service, replacement):
    command = create_command()
    execute(service, command)
    before = db_snapshot(service)
    result = execute(service, replace(command, **replacement))
    assert result.reason == "command_id_conflict" and result.status == "rejected"
    assert db_snapshot(service) == before


@pytest.mark.parametrize("value", [
    "2026-02-30", "2025-02-29", "2026-13-01", "0000-01-01", "2026-2-30",
])
def test_invalid_calendar_date_rejected_without_writes(service, value):
    result = execute(service, create_command(date=value))
    assert (result.status, result.reason) == ("rejected", "invalid_date")
    assert db_snapshot(service) == ([], [])


def test_revision_conflict_rejected_with_latest_candidate_and_no_overwrite(service):
    created = execute(service, create_command())
    moved = execute(service, edit_command(created, date="모레"))
    before = db_snapshot(service)
    result = execute(service, edit_command(created, command_id="stale", title="다른 제목"))
    assert (result.status, result.reason) == ("rejected", "revision_conflict")
    assert result.schedules == moved.schedules and result.revision == 2
    assert db_snapshot(service) == before


@pytest.mark.parametrize("operation", ["create", "update", "cancel"])
def test_history_insert_failure_rolls_back_current_row_and_history(service, operation):
    command = create_command()
    if operation != "create":
        created = execute(service, command)
        fields = {"title": "변경"} if operation == "update" else {}
        command = edit_command(created, operation, **fields)
    before = db_snapshot(service)
    with sqlite3.connect(service.repository.path) as connection:
        connection.execute("""
            CREATE TRIGGER fail_history BEFORE INSERT ON change_history
            BEGIN SELECT RAISE(ABORT, 'injected history failure'); END
        """)
    failed = execute(service, command)
    assert (failed.status, failed.reason) == ("failed", "database_error")
    assert db_snapshot(service) == before
    with sqlite3.connect(service.repository.path) as connection:
        connection.execute("DROP TRIGGER fail_history")
    assert execute(service, command).status == "applied"


def test_failure_after_history_insert_also_rolls_back(service, monkeypatch):
    persist = service.repository._persist

    def fail_after_insert(*args):
        persist(*args)
        raise sqlite3.OperationalError("after both writes")

    monkeypatch.setattr(service.repository, "_persist", fail_after_insert)
    result = execute(service, create_command())
    assert result.status == "failed"
    assert db_snapshot(service) == ([], [])


def test_commit_ack_failure_recovers_applied_receipt(service, monkeypatch):
    transaction = service.repository.transaction

    @contextmanager
    def lost_ack():
        with transaction() as connection:
            yield connection
        raise sqlite3.OperationalError("commit acknowledgement lost")

    monkeypatch.setattr(service.repository, "transaction", lost_ack)
    result = execute(service, create_command())
    assert result.status == "applied"
    assert service.repository.get(result.schedule_id) == result.schedules[0]
    assert len(service.repository.history(result.schedule_id)) == 1


def test_unavailable_database_reports_commit_unconfirmed(service, monkeypatch):
    @contextmanager
    def unavailable():
        raise sqlite3.OperationalError("cannot connect")
        yield

    monkeypatch.setattr(service.repository, "_connection", unavailable)
    assert execute(service, create_command()).reason == "commit_unconfirmed"
    assert execute(service, query_command()).reason == "database_error"


@pytest.mark.parametrize("operation", ["update", "cancel"])
def test_ambiguous_target_lists_candidates_without_picking_one(service, operation):
    first = execute(service, create_command())
    second = execute(service, create_command("create-2", date="모레"))
    fields = {"place": "남쪽"} if operation == "update" else {}
    command = replace(edit_command(first, operation, **fields), target={"title_hint": "합성"})
    before = db_snapshot(service)
    result = execute(service, command)
    assert result.status == "needs_clarification" and result.reason == "ambiguous_target"
    assert {s.id for s in result.schedules} == {first.schedule_id, second.schedule_id}
    assert result.schedule_id is None and result.clarification
    assert db_snapshot(service) == before
    # Unresolved commands are not consumed; the same ID can be clarified without a pending table.
    clarified = execute(service, replace(command, target={"schedule_id": second.schedule_id},
                                        original_source_message_id=1))
    assert clarified.status == "applied"
    payload = json.loads(service.repository.history(second.schedule_id)[-1]["change_json"])
    assert payload["command"]["request"]["original_source_message_id"] == 1
    assert service.repository.get(first.schedule_id).revision == 1


@pytest.mark.parametrize("target", [
    {"schedule_id": "missing"}, {"title_hint": "없는 제목"},
    {"schedule_id": "actual", "title_hint": "다른 제목"},
])
def test_zero_or_inconsistent_target_is_rejected(service, target):
    created = execute(service, create_command())
    if target.get("schedule_id") == "actual":
        target = {**target, "schedule_id": created.schedule_id}
    before = db_snapshot(service)
    result = execute(service, replace(edit_command(created, place="남쪽"), target=target))
    assert (result.status, result.reason) == ("rejected", "target_not_found")
    assert db_snapshot(service) == before


def test_omitted_time_stays_sql_null_through_other_updates(service):
    created = execute(service, create_command())
    moved = execute(
        service, edit_command(created, date="모레", place="북쪽", participants="동행자")
    )
    assert moved.status == "applied" and moved.schedules[0].time is None
    with sqlite3.connect(service.repository.path) as connection:
        assert connection.execute("SELECT time IS NULL FROM schedules").fetchone() == (1,)
    changed = execute(
        service, edit_command(moved, command_id="time", time="오후 4시", title="새 제목")
    )
    assert changed.changed_fields == {"time": "16:00", "title": "새 제목"}
    cleared = execute(service, edit_command(changed, command_id="clear", time=None, place=None,
                                           participants=None))
    assert cleared.status == "applied"
    assert cleared.schedules[0].title == "새 제목"
    assert cleared.schedules[0].time is cleared.schedules[0].place is None
    assert cleared.schedules[0].participants_text is None


def test_noop_and_cancelled_mutations_do_not_create_revisions(service):
    created = execute(service, create_command())
    before = db_snapshot(service)
    noop = execute(service, edit_command(created, title=" 합성 일정 "))
    assert noop.reason == "no_change" and noop.status == "rejected"
    assert db_snapshot(service) == before
    cancelled = execute(service, edit_command(created, "cancel", "cancel"))
    before = db_snapshot(service)
    repeated = execute(service, edit_command(cancelled, "cancel", "cancel-again"))
    assert repeated.reason == "already_cancelled"
    assert execute(service, edit_command(cancelled, title="복구")).reason == "already_cancelled"
    assert db_snapshot(service) == before


@pytest.mark.parametrize(("expression", "expected"), [
    ("오늘", date(2026, 9, 14)), ("내일", date(2026, 9, 15)), ("모레", date(2026, 9, 16)),
    ("금요일", date(2026, 9, 18)), ("이번 주 금요일", date(2026, 9, 18)),
    ("다음 주 금요일", date(2026, 9, 25)), ("이번 주말", None), ("주말쯤", None),
    ("9월 18일", None), ("2028-02-29", date(2028, 2, 29)),
])
def test_pure_relative_date_resolution(expression, expected):
    assert resolve_date(expression, NOW) == expected


def test_weekend_range_and_kst_utc_boundary():
    assert resolve_date_range("이번 주말", NOW) == (date(2026, 9, 19), date(2026, 9, 20))
    sunday = datetime(2026, 9, 20, 22, tzinfo=KST)
    assert resolve_date_range("이번 주말", sunday) == (date(2026, 9, 19), date(2026, 9, 20))
    assert resolve_date("다음 주 금요일", sunday) == date(2026, 9, 25)
    utc = datetime(2026, 9, 13, 15, tzinfo=UTC)
    assert resolve_date("내일", utc) == date(2026, 9, 15)
    friday = datetime(2026, 9, 18, tzinfo=KST)
    assert resolve_date("금요일", friday) is None
    assert resolve_date("내일", datetime(2026, 12, 31, tzinfo=KST)) == date(2027, 1, 1)
    with pytest.raises(ValueError, match="timezone-aware"):
        resolve_date("내일", datetime(2026, 9, 14))


@pytest.mark.parametrize("expression", ["이번 주말", "주말쯤", "9월 18일"])
def test_ambiguous_date_needs_clarification_without_writes(service, expression):
    result = execute(service, create_command(date=expression))
    assert result.status == "needs_clarification" and result.reason == "ambiguous_date"
    assert db_snapshot(service) == ([], [])


def test_past_weekday_not_automatically_advanced(service):
    service.clock.value = datetime(2026, 9, 20, tzinfo=KST)
    result = execute(service, create_command(date="이번 주 금요일"))
    assert result.status == "needs_clarification" and result.reason == "past_date"
    assert db_snapshot(service) == ([], [])


def test_query_weekend_inclusive_title_literal_and_active_state(service):
    friday = execute(service, create_command(date="2026-09-18"))
    saturday = execute(service, create_command("sat", date="2026-09-19"))
    sunday = execute(service, create_command("sun", date="2026-09-20"))
    execute(service, create_command("mon", date="2026-09-21"))
    execute(service, edit_command(sunday, "cancel", "cancel-sun"))
    query = query_command(date_from="이번 주말", date_to="이번 주말")
    before = db_snapshot(service)
    result = execute(service, query)
    assert [s.id for s in result.schedules] == [saturday.schedule_id, sunday.schedule_id]
    assert [s.status for s in result.schedules] == ["planned", "cancelled"]
    assert len(execute(service, query, active_only=True).schedules) == 1
    assert execute(service, replace(query_command(), target={"title_hint": "%"})).schedules == ()
    injection = replace(query_command(), target={"title_hint": "' OR 1=1 --"})
    assert execute(service, injection).schedules == ()
    assert db_snapshot(service) == before
    execute(service, edit_command(friday, command_id="rename", title="별개"))
    assert len(execute(service, query_command()).schedules) == 3  # query ID reads current data


@pytest.mark.parametrize("fields", [
    {"date_from": "내일"}, {"date_from": None, "date_to": "내일"},
    {"date_from": "2026-09-20", "date_to": "2026-09-19"},
])
def test_invalid_query_range_rejected(service, fields):
    result = execute(service, query_command(**fields))
    assert result.status == "rejected" and result.reason == "invalid_date_range"


@pytest.mark.parametrize(("text", "expected"), [
    ("00:00", "00:00"), ("23:59", "23:59"), ("오전 12시", "00:00"),
    ("오후 12시", "12:00"), ("오후 3시 5분", "15:05"), ("3시", None),
    ("저녁쯤", None), ("24:00", None), ("12:60", None),
])
def test_time_resolution(text, expected):
    assert resolve_time(text) == expected


@pytest.mark.parametrize(
    ("text", "status"), [("3시", "needs_clarification"), ("24:00", "rejected")]
)
def test_unresolved_time_is_not_silently_dropped(service, text, status):
    assert execute(service, create_command(time=text)).status == status
    assert db_snapshot(service) == ([], [])


@pytest.mark.parametrize("replacement", [
    {"source_message_id": 0}, {"source_message_id": None}, {"source_message_id": True},
    {"original_source_message_id": -1}, {"cue": "없는 근거"}, {"cue": "일"},
    {"expected_revision": True}, {"expected_revision": 1}, {"command_id": " "},
    {"fields": {"title": None, "date": "내일"}}, {"fields": {"title": " ", "date": "내일"}},
    {"fields": {"date": 32}}, {"fields": {"status": "planned"}},
    {"target": {"schedule_id": "any"}}, {"received_at": NOW.replace(tzinfo=None)},
])
def test_invalid_commands_do_not_write(service, replacement):
    result = execute(service, replace(create_command(), **replacement))
    assert result.status == "rejected"
    assert db_snapshot(service) == ([], [])


@pytest.mark.parametrize("fields", [{"title": "제목"}, {"date": "내일"}])
def test_missing_create_required_fields_needs_clarification(service, fields):
    result = execute(service, replace(create_command(), fields=fields))
    assert result.status == "needs_clarification"
    assert db_snapshot(service) == ([], [])


def test_explicit_received_time_and_source_provenance(service):
    command = replace(create_command(), received_at=datetime(2026, 9, 13, 15, tzinfo=UTC),
                      original_source_message_id=7)
    result = execute(service, command)
    assert result.schedules[0].date == "2026-09-15" and service.clock.calls == 0
    payload = json.loads(service.repository.history(result.schedule_id)[0]["change_json"])
    assert payload["command"]["received_at"] == "2026-09-13T15:00:00+00:00"
    assert payload["command"]["request"]["original_source_message_id"] == 7
    assert execute(service, command) == result


def test_repository_query_rejects_partial_reversed_or_non_iso_ranges(service):
    for filters in (
        {"date_from": "2026-09-15"},
        {"date_from": "2026-09-16", "date_to": "2026-09-15"},
        {"date_from": "20260915", "date_to": "20260915"},
    ):
        with pytest.raises(ValueError):
            service.repository.query(**filters)
    assert db_snapshot(service) == ([], [])


def test_schema_has_exactly_two_tables_and_unique_command_ids(service):
    created = execute(service, create_command())
    with sqlite3.connect(service.repository.path) as connection:
        tables = connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        assert {r[0] for r in tables} == {
            "schedules", "change_history",
        }
        assert connection.execute("PRAGMA user_version").fetchone() == (0,)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("INSERT INTO change_history SELECT * FROM change_history")
    assert len(service.repository.history(created.schedule_id)) == 1


@pytest.mark.parametrize("same_command", [True, False])
def test_concurrent_writers_serialize_revision_and_idempotency(service, same_command):
    created = execute(service, create_command())
    command = edit_command(created, place="북쪽")
    other = command if same_command else replace(
        command, command_id="racer", fields={"place": "남쪽"},
    )
    barrier = Barrier(2)

    def run(command):
        sibling = ScheduleService(ScheduleRepository(service.repository.path), FixedClock())
        barrier.wait(timeout=5)
        return execute(sibling, command)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, (command, other)))
    expected = ["applied", "applied"] if same_command else ["applied", "rejected"]
    assert sorted(r.status for r in results) == expected
    if same_command:
        assert results[0] == results[1]
    else:
        assert next(r for r in results if r.status == "rejected").reason == "revision_conflict"
    assert service.repository.get(created.schedule_id).revision == 2
    assert len(service.repository.history(created.schedule_id)) == 2
