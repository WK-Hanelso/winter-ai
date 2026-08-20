"""Foreground-first scheduling for one Local ChatModel runtime."""

from __future__ import annotations

from collections.abc import Iterator
import threading
import time
from typing import Literal, cast

from companion.contracts import ChatRequest, ChatResult
from companion.ports import ChatModel

Priority = Literal["foreground", "background"]


class ForegroundPriorityChatScheduler:
    """Let conversation turns enter before background planning and review."""

    def __init__(
        self,
        model: ChatModel,
        *,
        background_quiet_seconds: float = 6.0,
    ) -> None:
        if background_quiet_seconds < 0:
            raise ValueError("background quiet time must not be negative")
        self._model = model
        self._background_quiet_seconds = background_quiet_seconds
        self._condition = threading.Condition()
        self._active = False
        self._foreground_waiters = 0
        # A fresh process has no conversation to protect yet.  The quiet
        # window starts only after a real foreground turn completes.
        self._last_foreground = float("-inf")

    def foreground(self) -> ChatModel:
        return _PrioritizedChatModel(self, "foreground")

    def background(self) -> ChatModel:
        return _PrioritizedChatModel(self, "background")

    def mark_foreground_activity(self) -> None:
        """Keep queued background work out while the user starts a turn."""
        with self._condition:
            self._last_foreground = time.monotonic()
            self._condition.notify_all()

    def generate(self, priority: Priority, request: ChatRequest) -> ChatResult:
        self._acquire(priority)
        try:
            return self._model.generate(request)
        finally:
            self._release(priority)

    def generate_stream(
        self, priority: Priority, request: ChatRequest
    ) -> Iterator[str]:
        self._acquire(priority)
        try:
            stream = getattr(self._model, "generate_stream", None)
            if stream is None:
                yield self._model.generate(request).text
            else:
                yield from cast(Iterator[str], stream(request))
        finally:
            self._release(priority)

    def _acquire(self, priority: Priority) -> None:
        with self._condition:
            if priority == "foreground":
                self._foreground_waiters += 1
            try:
                while True:
                    quiet_remaining = (
                        self._last_foreground
                        + self._background_quiet_seconds
                        - time.monotonic()
                    )
                    blocked = self._active or (
                        priority == "background"
                        and (
                            self._foreground_waiters > 0
                            or quiet_remaining > 0
                        )
                    )
                    if not blocked:
                        self._active = True
                        return
                    self._condition.wait(
                        timeout=(
                            max(0.001, quiet_remaining)
                            if priority == "background"
                            and not self._active
                            and self._foreground_waiters == 0
                            else None
                        )
                    )
            finally:
                if priority == "foreground":
                    self._foreground_waiters -= 1

    def _release(self, priority: Priority) -> None:
        with self._condition:
            self._active = False
            if priority == "foreground":
                self._last_foreground = time.monotonic()
            self._condition.notify_all()


class _PrioritizedChatModel:
    def __init__(
        self,
        scheduler: ForegroundPriorityChatScheduler,
        priority: Priority,
    ) -> None:
        self._scheduler = scheduler
        self._priority = priority

    def generate(self, request: ChatRequest) -> ChatResult:
        return self._scheduler.generate(self._priority, request)

    def generate_stream(self, request: ChatRequest) -> Iterator[str]:
        yield from self._scheduler.generate_stream(self._priority, request)
