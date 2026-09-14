"""Session-bound reflection wiring; extraction and approval policies stay in their services."""

from __future__ import annotations

import argparse
from contextlib import closing
from dataclasses import asdict
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
from typing import TextIO
from uuid import uuid4

from companion.memory import Memory, SqliteMemoryRepository
from companion.reflection import (
    ConversationHistoryBackfill,
    ConversationReflectionService,
    FakeReflectionInterpreter,
    SqliteConversationHistoryReader,
    SqliteReflectionRepository,
)
from companion.reflection_review import run_memory_candidate_review
from companion.turn_understanding import SqliteTurnUnderstandingRepository


def _readonly(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)


def candidate_count(data: Path) -> int:
    path = data / "reflection.sqlite"
    if not path.exists():
        return 0
    with closing(_readonly(path)) as connection:
        return int(connection.execute(
            "SELECT COUNT(*) FROM reflection_candidates WHERE status = 'candidate'",
        ).fetchone()[0])


def review_before_session(data: Path, stdin: TextIO, stdout: TextIO) -> None:
    count = candidate_count(data)
    if count:
        run_memory_candidate_review(
            SqliteReflectionRepository(data / "reflection.sqlite"),
            SqliteMemoryRepository(data / "memories.sqlite"),
            limit=count, stdin=stdin, stdout=stdout, session_start=True,
        )


def activate_before_session(data: Path, stdout: TextIO) -> None:
    """Activate under the Host lock, then notify without input.

    Durable reflection candidates use the existing stable/candidate_stable scopes.
    """
    if not candidate_count(data):
        return
    reflections = SqliteReflectionRepository(data / "reflection.sqlite")
    eligible = tuple(
        candidate for candidate in reflections.list(status="candidate")
        if (candidate.is_current_state and candidate.confidence >= 0.9)
        or (not candidate.is_current_state and candidate.can_activate_as_memory
            and candidate.confidence >= 0.85)
    )
    if not eligible:
        return
    memories = SqliteMemoryRepository(data / "memories.sqlite")
    known_ids = {memory.id for memory in memories.list_active()}
    activated: list[Memory] = []
    for candidate in eligible:
        accepted = reflections.approve(candidate.id, memories, source="reflection_auto")
        if accepted.memory_id is not None and accepted.memory_id not in known_ids:
            activated.append(memories.get(accepted.memory_id))
            known_ids.add(accepted.memory_id)
    if activated:
        preview = " / ".join(
            f'“{memory.content[:40]}{"…" if len(memory.content) > 40 else ""}”'
            for memory in activated[:3]
        )
        extra = f" 외 {len(activated) - 3}개" if len(activated) > 3 else ""
        preview = " ".join(preview.splitlines())
        print(f"지난 대화에서 기억한 것 {len(activated)}개: {preview}{extra}",
              file=stdout, flush=True)


def pending_count(data: Path, *, limit: int = 40) -> int:
    """Read-only equivalent of has_extraction; failed extractions remain eligible."""
    history = data / "conversations.sqlite"
    if not history.exists():
        return 0
    with closing(_readonly(history)) as connection:
        reflection = data / "reflection.sqlite"
        condition = ""
        if reflection.exists():
            connection.execute("ATTACH DATABASE ? AS reflection",
                               (reflection.resolve().as_uri() + "?mode=ro",))
            condition = """AND NOT EXISTS (
                SELECT 1 FROM reflection.reflection_extractions AS extraction
                WHERE extraction.message_id = message.id AND extraction.status = 'completed'
            )"""
        return len(connection.execute(
            f"SELECT id FROM conversation_messages AS message WHERE role = 'user' {condition} "
            "ORDER BY id DESC LIMIT ?", (limit,),
        ).fetchall())


def record_run(data: Path, started_at: str, *, error: str | None = None,
               counts: dict[str, int] | None = None) -> None:
    result: dict[str, object] = dict.fromkeys(
        ("processed", "completed", "failed", "deferred", "created_candidates"), 0,
    )
    if counts is not None:
        result.update({key: counts[key] for key in result})
    result.update(started_at=started_at, ended_at=datetime.now(UTC).isoformat(), error=error)
    data.mkdir(parents=True, exist_ok=True)
    with (data / "reflection_runs.jsonl").open("a", encoding="utf-8") as output:
        output.write(json.dumps(result, ensure_ascii=False) + "\n")


def detached_command(child_command: list[str], *, label: str, limit: int) -> list[str]:
    """Reuse the launcher's exact image, mounts, network and resource restrictions."""
    module = child_command.index("companion.text_child")
    if child_command[:2] != ["docker", "run"] or child_command[module - 2:module] != [
        "python", "-m",
    ]:
        raise ValueError("unsupported text child command")
    prefix = child_command[:module - 2]
    name = prefix.index("--name")
    prefix[name + 1] = "winter-text-reflect-" + uuid4().hex
    prefix.remove("-i")
    # Read the same effective URL as the child, including explicit user overrides.
    from companion.user_cli import build_parser

    args = build_parser().parse_args(child_command[module + 1:])
    return [*prefix[:2], "-d", "--label", label, *prefix[2:],
            "python", "-m", "companion.user_cli", "--model-url", args.model_url,
            "--reflect-history", "--analysis-limit", str(limit), "--reflection-run-log"]


def start_after_session(data: Path, child_command: list[str], stdout: TextIO) -> None:
    """Called under the Host terminal lock, only after a clean child exit."""
    started_at = datetime.now(UTC).isoformat()
    try:
        count = pending_count(data)
        if not count:
            return
        digest = hashlib.sha256(str(data.resolve()).encode()).hexdigest()
        label = "winter.text-reflection-data=" + digest
        existing = subprocess.run(
            ["docker", "ps", "-a", "--filter", "label=" + label, "--format", "{{.State}}"],
            capture_output=True, text=True, timeout=5, check=True,
        )
        if any(state in {"created", "running", "restarting", "paused", "removing"}
               for state in existing.stdout.splitlines()):
            return
        subprocess.run(detached_command(child_command, label=label, limit=count),
                       capture_output=True, text=True, timeout=5, check=True)
    except (OSError, RuntimeError, ValueError, sqlite3.Error, subprocess.SubprocessError) as error:
        detail = f"{type(error).__name__}: {error}"
        if isinstance(error, subprocess.CalledProcessError) and error.stderr:
            detail += f": {error.stderr.strip()}"
        try:
            record_run(data, started_at, error=detail)
        except OSError:
            # Even an unwritable audit destination must not turn /exit into a failure.
            print("기억 정리 시작 오류를 기록하지 못했어.", file=stdout, flush=True)
        return
    print(f"기억 정리를 뒤에서 시작했어 (발화 {count}개). 다음 대화 시작 때 기억한 걸 알려줄게.",
          file=stdout, flush=True)


def run_logged_reflection(data: Path, args: argparse.Namespace) -> int:
    """Detached user_cli entry: same backfill/extractor, no maintenance/model server startup."""
    from companion.adapters.reflection_interpreter import StructuredChatReflectionInterpreter
    from companion.cli.__main__ import build_chat_model

    started_at = datetime.now(UTC).isoformat()
    counts = None
    error = None
    try:
        if not (data / "identity.json").exists():
            raise ValueError("Winter Identity is missing")
        history = SqliteConversationHistoryReader(data / "conversations.sqlite")
        turns = SqliteTurnUnderstandingRepository(data / "turn_understanding.sqlite")
        reflections = SqliteReflectionRepository(data / "reflection.sqlite")
        memories = SqliteMemoryRepository(data / "memories.sqlite")
        ConversationHistoryBackfill(history, turns).run()
        interpreter = (FakeReflectionInterpreter() if args.backend == "fake"
                       else StructuredChatReflectionInterpreter(build_chat_model(args)))
        result = ConversationReflectionService(
            history, reflections, interpreter, memories,
        ).process_pending(limit=min(40, args.analysis_limit))
        counts = asdict(result)
        return 1 if result.failed else 0
    except Exception as failure:  # noqa: BLE001 - detached failures need a durable run record
        error = f"{type(failure).__name__}: {failure}"
        return 1
    finally:
        record_run(data, started_at, error=error, counts=counts)
