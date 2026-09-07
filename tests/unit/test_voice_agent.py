"""Unit tests for Voice Agent integration."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_ROOT = Path(__file__).resolve().parents[2] / "examples" / "python" / "voice-agent"
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def voice_env(monkeypatch, tmp_path):
    monkeypatch.setenv("VOICE_API_KEY", "test-voice-key")
    monkeypatch.setenv("VOICE_API_BASE", "https://voice-provider.example.test")
    monkeypatch.setenv("VOICE_SERVER_URL_SECRET", "secret-123")
    monkeypatch.setenv("PUBLIC_API_BASE_URL", "https://voice-dev.example.test")
    monkeypatch.setenv("PRAISONAI_VOICE_DIR", str(tmp_path))


def test_execute_tool_get_current_time():
    tools = _load("voice_tools", _ROOT / "integrations" / "voice" / "tools.py")
    raw = tools.execute_tool("get_current_time", {})
    assert "utc" in raw


def test_tool_calls_webhook_response():
    processor = _load("voice_processor", _ROOT / "integrations" / "voice" / "processor.py")
    config = _load("voice_config", _ROOT / "integrations" / "voice" / "config.py")
    settings = config.load_voice_settings()
    payload = {
        "message": {
            "type": "tool-calls",
            "call": {"id": "call-1", "customer": {"number": "+15551234567"}},
            "toolCallList": [
                {"id": "tc-1", "name": "echo_message", "parameters": {"message": "hello"}}
            ],
        }
    }
    result = processor.process_voice_webhook("tool-calls", payload, settings=settings)
    assert result["results"][0]["name"] == "echo_message"
    assert "hello" in result["results"][0]["result"]


def test_assistant_request_returns_default_assistant():
    processor = _load("voice_processor2", _ROOT / "integrations" / "voice" / "processor.py")
    config = _load("voice_config2", _ROOT / "integrations" / "voice" / "config.py")
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("VOICE_ASSISTANT_ID", "asst-123")
    settings = config.load_voice_settings()
    result = processor.process_voice_webhook(
        "assistant-request",
        {"message": {"type": "assistant-request", "call": {"id": "c1"}}},
        settings=settings,
    )
    assert result == {"assistantId": "asst-123"}
    monkeypatch.undo()


def test_transcript_appends_final_lines():
    processor = _load("voice_processor3", _ROOT / "integrations" / "voice" / "processor.py")
    config = _load("voice_config3", _ROOT / "integrations" / "voice" / "config.py")
    store = _load("voice_store", _ROOT / "integrations" / "voice" / "store.py")
    settings = config.load_voice_settings()
    payload = {
        "message": {
            "type": "transcript",
            "call": {"id": "call-t1"},
            "role": "user",
            "transcriptType": "final",
            "transcript": "I need the time",
        }
    }
    processor.process_voice_webhook("transcript", payload, settings=settings)
    record = store.VoiceCallStore.get_call("call-t1")
    assert "user: I need the time" in record["transcript"]


def test_verify_secret_header():
    verify = _load("voice_verify", _ROOT / "integrations" / "voice" / "verify.py")
    verify.verify_webhook_request(
        secret="secret-123",
        headers={"x-voice-webhook-secret": "secret-123"},
    )
    with pytest.raises(verify.VerificationError):
        verify.verify_webhook_request(secret="secret-123", headers={"x-voice-webhook-secret": "wrong"})


def test_create_call_request_body():
    client_mod = _load("voice_client", _ROOT / "integrations" / "voice" / "client.py")
    config_mod = _load("voice_config4", _ROOT / "integrations" / "voice" / "config.py")
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("VOICE_ASSISTANT_ID", "asst-1")
    monkeypatch.setenv("VOICE_PHONE_NUMBER_ID", "pn-1")
    settings = config_mod.load_voice_settings()
    client = client_mod.VoiceClient(settings)
    client._request = MagicMock(return_value={"id": "call-1"})  # noqa: SLF001
    client.create_call(customer_number="+15551234567")
    body = client._request.call_args.kwargs["json_body"]  # noqa: SLF001
    assert body["assistantId"] == "asst-1"
    assert body["customer"]["number"] == "+15551234567"
    monkeypatch.undo()
