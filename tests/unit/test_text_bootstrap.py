"""Text startup contracts use only isolated synthetic stores and explicit fakes."""

from argparse import Namespace
from dataclasses import fields, replace
from io import StringIO
import subprocess
import sys

from configs.dialogue.context import HIGH_MESSAGES
import pytest

from companion.adapters.fake import AdapterUnavailableError, FakeChatModel
from companion.application import bootstrap
from companion.application.bootstrap import (
    TextRuntimeConfig,
    TextRuntimeError,
    build_text_resources,
    build_text_runtime,
)
from companion.cli import __main__ as cli
from companion.contracts import ChatResult, ConversationMessage
from companion.identity import CompanionIdentity, JsonIdentityRepository


def test_python_config_and_cli_options_have_identical_runtime_inputs(tmp_path):
    config = TextRuntimeConfig(
        backend="fake", conversation_db=tmp_path / "conversation.sqlite",
        model_url="http://unused", context_max_messages=7, context_max_characters=500,
        interactive=False, project_root=tmp_path,
    )
    options = {item.name: getattr(config, item.name) for item in fields(config)}
    options.pop("interactive")
    assert cli.text_runtime_config(Namespace(**options, prompt="hello")) == config


def test_text_startup_does_not_import_speech_devices_or_models():
    # Fresh interpreter: other tests may already have imported voice modules.
    result = subprocess.run(
        [sys.executable, "-c", """
import sys
from companion.application.bootstrap import TextRuntimeConfig, build_text_runtime
runtime = build_text_runtime(TextRuntimeConfig(backend='fake'))
assert runtime.core.respond_to_text('hello').text == 'fake: hello'
assert runtime.close()
# Shared prosody metadata (voice_profile) is part of Core, not a speech runtime.
for name in sys.modules:
    assert not name.startswith(('companion.voice_cli', 'companion.voice_pipeline',
                                'companion.adapters.cosyvoice',
                                'companion.adapters.sensevoice',
                                'torch', 'sounddevice', 'whisper',
                                'cosyvoice', 'faster_whisper')), name
"""], capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr


def test_history_inspection_never_constructs_model_or_worker(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        pytest.fail("inspection must not construct model or worker")

    monkeypatch.setattr(bootstrap, "build_chat_model", forbidden)
    monkeypatch.setattr(bootstrap, "CodexReadOnlyWorker", forbidden)
    monkeypatch.setattr(bootstrap, "WinterOwnedProjectCanary", forbidden)
    config = TextRuntimeConfig(conversation_db=tmp_path / "conversation.sqlite")
    resources = build_text_resources(config)
    resources.repository.append(ConversationMessage("user", "이전 대화"))
    output = StringIO()
    assert cli.run(Namespace(
        backend="local", prompt=None, conversation_db=config.conversation_db,
        show_history=True, codex_worker=True, winter_work_canary=True,
    ), stdout=output) == 0
    assert output.getvalue() == "user> 이전 대화\n"


@pytest.mark.parametrize("option,label", [
    ("conversation_db", "Conversation storage"),
    ("turn_understanding_db", "Turn understanding"),
    ("outcome_db", "Outcome storage"),
    ("reflection_db", "Reflection storage"),
    ("identity_path", "Companion identity"),
    ("memory_db", "Memory"),
    ("capability_db", "Capability registry"),
    ("improvement_db", "Improvement ledger"),
    ("belief_db", "Belief storage"),
    ("winter_event_db", "Winter lived continuity"),
    ("day_reflection_db", "Winter day reflection"),
    ("dialogue_state_db", "Dialogue state"),
])
def test_invalid_store_fails_explicitly_before_model(option, label, tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("model must not be constructed after storage failure")

    monkeypatch.setattr(bootstrap, "build_chat_model", forbidden)
    config = replace(TextRuntimeConfig(backend="fake"), **{option: tmp_path})
    with pytest.raises(TextRuntimeError, match=f"{label} unavailable:") as error:
        build_text_runtime(config)
    assert error.value.__cause__ is not None


def test_invalid_style_and_unknown_backend_are_not_silent_fakes():
    with pytest.raises(TextRuntimeError, match="Verbal style unavailable:"):
        build_text_runtime(TextRuntimeConfig(verbal_style="invalid-profile"))
    with pytest.raises(TextRuntimeError, match="Unknown chat backend"):
        build_text_runtime(TextRuntimeConfig(backend="typo"))


def test_local_model_failure_propagates_without_fake_fallback(monkeypatch):
    class UnavailableModel:
        def generate(self, request):
            raise AdapterUnavailableError("synthetic model failure")

    def forbidden():
        pytest.fail("fake must not be constructed as fallback")

    monkeypatch.setattr(bootstrap, "FakeChatModel", forbidden)
    monkeypatch.setattr(bootstrap, "LlamaCppHttpChatModel", lambda **kw: UnavailableModel())
    output = StringIO()
    assert cli.run(Namespace(backend="local", prompt="hello"), stdout=output) == 1
    assert output.getvalue() == "Companion unavailable: synthetic model failure\n"


def test_restart_preserves_identity_memory_and_original_evidence(tmp_path):
    identity = CompanionIdentity(
        "겨울이", "synthetic companion", ("차분함",), ("정직함",),
        ("함께 대화한다",), ("근거를 지어내지 않는다",), "test-v1",
    )
    config = TextRuntimeConfig(
        backend="fake", identity_path=tmp_path / "identity.json",
        conversation_db=tmp_path / "conversation.sqlite",
        memory_db=tmp_path / "memory.sqlite",
    )
    JsonIdentityRepository(config.identity_path).save(identity)
    identity_bytes = config.identity_path.read_bytes()
    first = build_text_runtime(config)
    response = first.core.respond_to_text("기억해. Python config를 선호해")
    memory_id = response.memory_candidate_ids[0]
    memory = first.resources.memory_repository.get(memory_id)
    messages = first.resources.repository.list_messages()
    assert memory.status == "active"
    assert memory.evidence_ref is not None
    assert first.close()

    class RecordingModel:
        def generate(self, request):
            self.request = request
            return ChatResult("알겠어.")

    model = RecordingModel()
    second = build_text_runtime(config, chat_model=model)
    assert second.resources.memory_repository.get(memory_id) == memory
    assert second.resources.repository.list_messages() == messages
    assert second.resources.identity == identity
    second.core.respond_to_text("내가 선호하는 config가 뭐지?")
    context = "\n".join(item.content for item in model.request.messages)
    assert identity.system_message() in context
    assert "Python config" in context
    assert config.identity_path.read_bytes() == identity_bytes
    assert second.close()


@pytest.mark.parametrize("interactive", [False, True])
def test_canary_retains_sync_or_background_selection(tmp_path, interactive):
    runtime = build_text_runtime(TextRuntimeConfig(
        backend="fake", project_root=tmp_path, interactive=interactive,
        winter_work_canary=True, owned_work_db=tmp_path / "work.sqlite",
    ))
    assert (runtime.task_inbox is not None) == interactive
    assert runtime.close()


def test_workers_are_explicit_opt_in(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("worker must not be initialized without opt-in")

    monkeypatch.setattr(bootstrap, "CodexReadOnlyWorker", forbidden)
    monkeypatch.setattr(bootstrap, "WinterOwnedProjectCanary", forbidden)
    assert build_text_runtime(TextRuntimeConfig(backend="fake")).close()


def test_invalid_background_job_store_is_reported(tmp_path):
    with pytest.raises(TextRuntimeError, match="Winter owned work unavailable:"):
        build_text_runtime(TextRuntimeConfig(
            backend="fake", winter_work_canary=True, owned_work_db=tmp_path,
        ))


def test_runtime_close_is_called_after_model_error(monkeypatch):
    class UnavailableModel:
        def generate(self, request):
            raise AdapterUnavailableError("synthetic failure")

    runtime = build_text_runtime(TextRuntimeConfig(), chat_model=UnavailableModel())
    calls = []
    monkeypatch.setattr(cli, "build_text_runtime", lambda *a, **kw: runtime)
    monkeypatch.setattr(bootstrap.TextRuntime, "close", lambda self: calls.append(self) or True)
    assert cli.run(Namespace(backend="local", prompt="hello"), stdout=StringIO()) == 1
    assert calls == [runtime]


@pytest.mark.parametrize("input_text", ["/exit\n", "", "hello\n/exit\n"])
def test_cli_always_calls_runtime_close(monkeypatch, input_text):
    runtime = build_text_runtime(TextRuntimeConfig(backend="fake"), chat_model=FakeChatModel())
    calls = []
    monkeypatch.setattr(cli, "build_text_runtime", lambda *a, **kw: runtime)
    monkeypatch.setattr(bootstrap.TextRuntime, "close", lambda self: calls.append(self) or True)
    assert cli.run(Namespace(backend="fake", prompt=None),
                   stdin=StringIO(input_text), stdout=StringIO()) == 0
    assert calls == [runtime]


def test_close_reports_pending_work_without_cancelling_it(monkeypatch):
    runtime = build_text_runtime(TextRuntimeConfig(backend="fake", winter_work_canary=True))
    timeouts = []
    monkeypatch.setattr(runtime.task_inbox, "wait_for_idle",
                        lambda timeout: timeouts.append(timeout) or False)
    assert not runtime.close()
    assert not runtime.close(timeout=0.01)
    assert timeouts == [0.0, 0.01]


def test_lean_examples_are_opt_in_and_keep_shared_evidence(monkeypatch):
    from configs.dialogue import companion_examples, companion_examples_lean

    class Capture:
        def generate(self, request):
            self.request = request
            return ChatResult("응.")

    for option, examples in ((None, companion_examples), ("lean", companion_examples_lean)):
        monkeypatch.delenv("WINTER_COMPANION_EXAMPLES", raising=False)
        if option:
            monkeypatch.setenv("WINTER_COMPANION_EXAMPLES", option)
        model = Capture()
        runtime = build_text_runtime(TextRuntimeConfig(
            dialogue_policy="companion", interactive=False,
        ), chat_model=model)
        runtime.resources.repository.append(ConversationMessage("user", "실제 대화 근거"))
        runtime.core.respond_to_text("안녕")
        messages = model.request.messages
        assert all(ConversationMessage("user", user) in messages for user, _ in examples.EXAMPLES)
        assert ConversationMessage("user", "실제 대화 근거") in messages
        assert model.request.max_tokens == (384 if option else 640)
        if option:
            assert ConversationMessage("system", companion_examples_lean.INSTRUCTION) in messages
            assert ConversationMessage("user", companion_examples.EXAMPLES[0][0]) not in messages
        assert runtime.close()


def test_context_message_environment_override_reaches_core(monkeypatch):
    monkeypatch.delenv("WINTER_CONTEXT_MAX_MESSAGES", raising=False)
    assert TextRuntimeConfig().context_max_messages == 12
    assert TextRuntimeConfig(context_max_messages=7).context_max_messages == 7
    for low in (7, HIGH_MESSAGES, 30):
        monkeypatch.setenv("WINTER_CONTEXT_MAX_MESSAGES", str(low))
        config = TextRuntimeConfig(backend="fake", context_max_messages=12)
        assert config.context_max_messages == low
        runtime = build_text_runtime(config)
        try:
            builder = runtime.core._context_builder
            high = max(low, HIGH_MESSAGES)
            assert builder.message_limit == high + 1  # overflow sentinel
            history = tuple(ConversationMessage("user", f"turn-{i}") for i in range(high * 2 + 1))
            # Fresh runtime starts at low, grows to high, then collapses to low.
            assert builder.build(history[:high + 1]) == history[high + 1 - low:high + 1]
            start = high + 1 - low
            for end in range(high + 2, start + high + 1):
                assert builder.build(history[:end][-builder.message_limit:]) == history[start:end]
            end = start + high + 1
            assert builder.build(history[:end][-builder.message_limit:]) == history[end - low:end]
        finally:
            assert runtime.close()
    monkeypatch.setenv("WINTER_CONTEXT_MAX_MESSAGES", "0")
    with pytest.raises(ValueError, match="must be positive"):
        TextRuntimeConfig()
