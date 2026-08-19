"""Close at most one unclosed real Winter day using the conversation model."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
import os
from pathlib import Path
import sys

from companion.adapters.day_reflection_interpreter import (
    StructuredChatDayReflectionInterpreter,
)
from companion.adapters.llama_cpp import LlamaCppHttpChatModel
from companion.day_reflection import (
    DayReflectionError,
    DayReflectionRepositoryError,
    DayReflectionService,
    SqliteConversationDayReader,
    SqliteDayReflectionRepository,
)
from companion.reflection import ReflectionRepositoryError
from companion.winter_events import (
    SqliteWinterEventRepository,
    SystemClock,
    WinterEventError,
    WinterEventRepositoryError,
)


def build_parser() -> argparse.ArgumentParser:
    data = Path(os.environ.get("WINTER_DATA_DIR", "data"))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model-url",
        default=os.environ.get("WINTER_LLM_URL", "http://127.0.0.1:18080"),
    )
    parser.add_argument(
        "--conversation-path",
        type=Path,
        default=data / "conversations.sqlite",
    )
    parser.add_argument(
        "--winter-event-path",
        type=Path,
        default=data / "winter_events.sqlite",
    )
    parser.add_argument(
        "--day-reflection-path",
        type=Path,
        default=data / "day_reflections.sqlite",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    clock = SystemClock()
    today = clock.now().date().isoformat()
    try:
        events = SqliteWinterEventRepository(arguments.winter_event_path)
        repository = SqliteDayReflectionRepository(arguments.day_reflection_path)
        closed_dates = {reflection.local_date for reflection in repository.list()}
        pending = tuple(
            local_date
            for local_date in events.list_dates_before(today)
            if local_date not in closed_dates
        )
        if not pending:
            print("Winter day close: no completed unclosed day")
            return 0
        local_date = pending[0]
        result = DayReflectionService(
            SqliteConversationDayReader(arguments.conversation_path),
            events,
            StructuredChatDayReflectionInterpreter(
                LlamaCppHttpChatModel(arguments.model_url)
            ),
            repository,
        ).close(local_date, now=clock.now())
    except (
        DayReflectionError,
        DayReflectionRepositoryError,
        ReflectionRepositoryError,
        WinterEventError,
        WinterEventRepositoryError,
    ) as error:
        print(f"Winter day close unavailable: {error}", file=sys.stderr)
        return 1
    print(
        f"Winter day close: {result.reflection.local_date} "
        f"points={len(result.reflection.points)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
