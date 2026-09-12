"""Bounded local pipe protocol. Model output is never parsed as a control message."""

import json
from typing import TextIO

MAX_FRAME = 262_144


class SessionProtocolError(RuntimeError):
    pass


def receive(stream: TextIO) -> dict[str, object] | None:
    line = stream.readline(MAX_FRAME + 1)
    if not line:
        return None
    if len(line) > MAX_FRAME or not line.endswith("\n"):
        raise SessionProtocolError("invalid session frame size")
    try:
        value = json.loads(line)
    except ValueError as error:
        raise SessionProtocolError("invalid session JSON") from error
    if not isinstance(value, dict):
        raise SessionProtocolError("session frame must be an object")
    return value


def send(stream: TextIO, value: dict[str, object]) -> None:
    line = json.dumps(value, ensure_ascii=False) + "\n"
    if len(line) > MAX_FRAME:
        raise SessionProtocolError("session frame exceeds size limit")
    stream.write(line)
    stream.flush()


def string(value: object, *, limit: int = 8192) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise SessionProtocolError("invalid session text")
    return value
