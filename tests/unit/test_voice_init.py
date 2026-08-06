from companion.voice.init import SERVICES, wait_until_ready


class CapturingNotifier:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def notify_ready(self, message: str) -> None:
        self.messages.append(message)


def test_init_emits_one_final_ready_notification() -> None:
    output: list[str] = []

    assert wait_until_ready(request=lambda url: True, sleep=lambda seconds: None, write=output.append)

    assert output[0] == "Initializing local Companion services..."
    assert output[-1] == "Companion is ready. You can start a text or voice conversation."
    assert [line for line in output if line.startswith("[ready]")] == [
        "[ready] LLM", "[ready] STT", "[ready] TTS"
    ]


def test_init_times_out_with_pending_service_names() -> None:
    output: list[str] = []

    assert not wait_until_ready(timeout_seconds=0, request=lambda url: False, write=output.append)

    assert output[-1] == "Companion unavailable: timed out waiting for LLM, STT, TTS"


def test_init_emits_ready_event_through_the_selected_notifier() -> None:
    notifier = CapturingNotifier()

    assert wait_until_ready(request=lambda url: True, sleep=lambda seconds: None, write=lambda line: None, notifier=notifier)

    assert notifier.messages == ["Companion is ready. You can start a text or voice conversation."]
