from __future__ import annotations

from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import threading
from typing import cast

import pytest

from companion.voice_latency import SqliteVoiceLatencyRepository
from companion.web import Companion, build_parser, make_handler

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _start_server(
    tmp_path: Path, *, max_request_bytes: int = 16
) -> tuple[ThreadingHTTPServer, threading.Thread]:
    repository = SqliteVoiceLatencyRepository(tmp_path / "voice-latency.sqlite")
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        make_handler(
            cast(Companion, object()),
            repository,
            max_request_bytes=max_request_bytes,
        ),
    )
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    return server, serving


def _raw_post(
    server: ThreadingHTTPServer,
    *,
    content_length: str | None,
) -> tuple[int, dict[str, str]]:
    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=2)
    connection.putrequest("POST", "/turn")
    connection.putheader("Content-Type", "application/json")
    if content_length is not None:
        connection.putheader("Content-Length", content_length)
    connection.endheaders()
    response = connection.getresponse()
    try:
        return response.status, json.loads(response.read())
    finally:
        connection.close()


def test_web_defaults_to_loopback_and_deploy_unit_is_explicit() -> None:
    assert build_parser().parse_args([]).host == "127.0.0.1"

    service = (PROJECT_ROOT / "deploy/systemd/winter-web.service").read_text(encoding="utf-8")
    assert "--host 127.0.0.1" in service
    assert "--host 0.0.0.0" not in service


@pytest.mark.parametrize(
    ("content_length", "expected_status"),
    [
        (None, 411),
        ("not-a-number", 400),
        ("-1", 400),
        ("17", 413),
    ],
)
def test_invalid_or_oversized_body_is_rejected_before_handler(
    tmp_path: Path,
    content_length: str | None,
    expected_status: int,
) -> None:
    server, serving = _start_server(tmp_path, max_request_bytes=16)
    try:
        status, payload = _raw_post(server, content_length=content_length)
    finally:
        server.shutdown()
        server.server_close()
        serving.join(timeout=2)

    assert status == expected_status
    assert payload["error"]


def test_health_body_has_no_extra_protocol_bytes(tmp_path: Path) -> None:
    server, serving = _start_server(tmp_path)
    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=2)
    try:
        connection.request("GET", "/health")
        response = connection.getresponse()
        body = response.read()
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        serving.join(timeout=2)

    assert response.status == 200
    assert body == b"ok"
