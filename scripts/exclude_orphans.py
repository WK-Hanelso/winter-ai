"""List unanswered greetings read-only; explicitly approved apply targets copies only.

This script does not make the runtime honor exclusions. Never delete source rows.
Use offline DB copies for --apply. Timing evidence identifies candidates, not proof
that the original utterance came from pytest.
"""

from __future__ import annotations

import argparse
from contextlib import ExitStack, closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

ROOT = Path(__file__).resolve().parents[1]
DATABASES = ("conversations.sqlite", "outcomes.sqlite", "turn_understanding.sqlite")


def connect(path: Path, mode: str = "ro") -> sqlite3.Connection:
    connection = sqlite3.connect(path.resolve().as_uri() + f"?mode={mode}", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def timestamp(value: str) -> float:
    # SQLite CURRENT_TIMESTAMP is UTC without an offset; event timestamps have one.
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)  # noqa: UP017 - host Python 3.8
    return parsed.timestamp()


def inspect(conversations: sqlite3.Connection, outcomes: sqlite3.Connection,
            understanding: sqlite3.Connection) -> list[dict]:
    messages = conversations.execute("""
        SELECT id, role, content, created_at,
               LEAD(id) OVER (ORDER BY id) AS next_id,
               LEAD(role) OVER (ORDER BY id) AS next_role
        FROM conversation_messages ORDER BY id
    """).fetchall()
    outcome_rows = outcomes.execute("""
        SELECT id, source, created_at, response_text FROM outcome_turns
        WHERE user_text = '안녕'
    """).fetchall()
    understanding_rows = understanding.execute("""
        SELECT id, source, created_at, status FROM turn_understanding_events
        WHERE user_text = '안녕'
    """).fetchall()
    result = []
    for message in messages:
        if (message["role"] != "user" or message["content"] != "안녕"
                or message["next_role"] == "assistant"):
            continue
        created = timestamp(message["created_at"])
        nearby_outcomes = [dict(row) for row in outcome_rows
                           if abs(timestamp(row["created_at"]) - created) <= 2]
        nearby_understanding = [dict(row) for row in understanding_rows
                                if abs(timestamp(row["created_at"]) - created) <= 2]
        reasons = ["role=user, content=안녕",
                   f"next_id={message['next_id']}, next_role={message['next_role']}",
                   "바로 다음 assistant 행 없음 (전체 대화의 답변 부재를 증명하지 않음)"]
        strong = False
        if len(nearby_outcomes) == len(nearby_understanding) == 1:
            outcome, event = nearby_outcomes[0], nearby_understanding[0]
            strong = (outcome["source"] == event["source"] == "voice"
                      and outcome["response_text"] is None and outcome["id"] == event["id"])
        reasons.append("±2초 유일 매칭, voice/NULL 응답/동일 turn ID 확인" if strong
                       else "매칭 부재·중복·source/응답/turn ID 불일치: 근거 부족")
        # Do not print unrelated response text, only whether one exists.
        for row in nearby_outcomes:
            row["response_is_null"] = row.pop("response_text") is None
        result.append({
            "message_id": message["id"], "created_at": message["created_at"],
            "next_id": message["next_id"], "next_role": message["next_role"],
            "verdict": "제외 후보" if strong else "보류", "reasons": reasons,
            "outcomes": nearby_outcomes, "turn_understanding": nearby_understanding,
        })
    # Same-second greetings can otherwise share the same event as 'unique' evidence.
    for item in result:
        if item["verdict"] != "제외 후보":
            continue
        event_time = timestamp(item["outcomes"][0]["created_at"])
        users = sum(row["role"] == "user" and row["content"] == "안녕"
                    and abs(timestamp(row["created_at"]) - event_time) <= 2
                    for row in messages)
        if users != 1:
            item["verdict"] = "보류"
            item["reasons"].append("같은 outcome이 여러 메시지의 ±2초 창에 들어감")
    return result


def read_candidates(data_dir: Path) -> list[dict]:
    with ExitStack() as stack:
        databases = [stack.enter_context(closing(connect(data_dir / name)))
                     for name in DATABASES]
        return inspect(*databases)


def apply(data_dir: Path, message_ids: list[int]) -> Path:
    original = (ROOT / "data").resolve()
    if data_dir.resolve() == original:
        raise ValueError("--apply requires --data-dir pointing to offline copies")
    for name in DATABASES:
        if (data_dir / name).samefile(original / name):
            raise ValueError(f"Refusing original DB or its symlink/hardlink: {name}")
    if not message_ids or len(message_ids) != len(set(message_ids)):
        raise ValueError("--apply requires unique, explicitly approved --message-id values")
    before = read_candidates(data_dir)
    selected = [item for item in before if item["message_id"] in message_ids]
    if len(selected) != len(message_ids) or any(i["verdict"] != "제외 후보" for i in selected):
        raise ValueError("Every approved ID must still be an unambiguous exclusion candidate")

    backup = ROOT / "data" / "backups" / datetime.now(timezone.utc).strftime(  # noqa: UP017
        "orphans-%Y%m%dT%H%M%S.%fZ"
    )
    backup.mkdir(parents=True, exist_ok=False)
    # Complete all three SQLite snapshots before opening a target for writing.
    for name in DATABASES:
        with closing(connect(data_dir / name)) as source:
            with closing(sqlite3.connect(str(backup / name))) as destination:
                source.backup(destination)
    (backup / "manifest.json").write_text(json.dumps(
        {"data_dir": str(data_dir.resolve()), "selected": selected},
        ensure_ascii=False, indent=2,
    ) + "\n", encoding="utf-8")

    with closing(connect(data_dir / DATABASES[0], "rw")) as database:
        with database:
            database.execute("BEGIN IMMEDIATE")
            with closing(connect(data_dir / DATABASES[1])) as outcomes:
                with closing(connect(data_dir / DATABASES[2])) as understanding:
                    if inspect(database, outcomes, understanding) != before:
                        raise ValueError(
                            "Evidence changed after backup; rerun dry-run on idle copies"
                        )
            database.execute("""
                CREATE TABLE IF NOT EXISTS conversation_context_exclusions (
                    message_id INTEGER PRIMARY KEY,
                    outcome_turn_id TEXT NOT NULL,
                    understanding_event_id TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(message_id) REFERENCES conversation_messages(id)
                )
            """)
            for item in selected:
                database.execute("""
                    INSERT OR IGNORE INTO conversation_context_exclusions
                        (message_id, outcome_turn_id, understanding_event_id, evidence_json)
                    VALUES (?, ?, ?, ?)
                """, (item["message_id"], item["outcomes"][0]["id"],
                      item["turn_understanding"][0]["id"],
                      json.dumps(item, ensure_ascii=False, sort_keys=True)))
    return backup


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="default: list/count only, mode=ro")
    mode.add_argument("--apply", action="store_true", help="requires separate user approval")
    parser.add_argument("--message-id", type=int, action="append", default=[])
    args = parser.parse_args()
    try:
        if args.apply:
            backup = apply(args.data_dir, args.message_id)
            print(f"Exclusion markers written to copy only; backup: {backup}")
        else:
            if args.message_id:
                parser.error("--message-id is only accepted with --apply")
            candidates = read_candidates(args.data_dir)
            print("DRY RUN — all source connections mode=ro; no writes or exclusions applied")
            print(f"data_dir={args.data_dir.resolve()}")
            print("Time: UTC (naive SQLite timestamps treated as UTC), matching window ±2 seconds")
            for item in candidates:
                print(json.dumps(item, ensure_ascii=False, sort_keys=True))
            held = sum(item["verdict"] == "보류" for item in candidates)
            print(f"대상={len(candidates)} 제외 후보={len(candidates) - held} 보류={held}")
            print("제외 후보는 테스트 출처 확정이 아님. --apply는 별도 승인 필요.")
    except (OSError, sqlite3.Error, ValueError) as error:
        parser.exit(1, f"Refused: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
