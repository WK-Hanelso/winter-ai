from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import threading
from typing import cast
from urllib.request import Request, urlopen

import pytest

from companion.voice_latency import (
    SqliteVoiceLatencyRepository,
    VoiceLatencyError,
    VoiceLatencySample,
)
from companion.web import Companion, make_handler


def _payload() -> dict[str, str | float]:
    return {
        "trace_id": "phone-turn-1",
        "release_to_stt_ms": 1000,
        "release_to_first_sentence_ms": 2000,
        "release_to_first_audio_ms": 3000,
        "release_to_play_ms": 3100,
        "server_stt_ms": 900,
        "server_first_sentence_ms": 800,
        "server_first_audio_ms": 1800,
    }


def test_phone_metric_post_is_persisted_and_summary_is_readable(tmp_path: Path) -> None:
    repository = SqliteVoiceLatencyRepository(tmp_path / "voice-latency.sqlite")
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        make_handler(cast(Companion, object()), repository),
    )
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        request = Request(
            f"{base_url}/voice-metric",
            data=json.dumps(_payload()).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=2) as response:
            posted = json.loads(response.read())
        with urlopen(f"{base_url}/voice-metrics", timeout=2) as response:
            cache_control = response.headers["Cache-Control"]
            summary = json.loads(response.read())
    finally:
        server.shutdown()
        server.server_close()
        serving.join(timeout=2)

    assert posted["sample_size"] == 1
    assert summary["sample_size"] == 1
    assert summary["gate_ready"] is False
    assert cache_control == "no-store"
    assert repository.list_recent()[0].trace_id == "phone-turn-1"


def test_metric_contract_does_not_accept_conversation_text(tmp_path: Path) -> None:
    SqliteVoiceLatencyRepository(tmp_path / "voice-latency.sqlite")
    payload = _payload()
    payload["transcript"] = "성능 DB에는 들어가면 안 되는 문장"

    with pytest.raises(VoiceLatencyError, match=r"extra=\['transcript'\]"):
        VoiceLatencySample.from_payload(payload)
