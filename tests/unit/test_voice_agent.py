"""Unit tests for Voice Agent integration.

The example lives in ``examples/python/voice-agent`` and ships its own
``integrations.voice`` package. The wider test-suite also owns a top-level
``integrations`` package (``tests/unit/integrations``), so importing the
example's modules requires temporarily giving the example's directory
priority on ``sys.path`` and restoring the original ``integrations`` package
afterwards — otherwise the cached test package shadows ``integrations.voice``.
"""

from __future__ import annotations

import contextlib
import importlib
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_ROOT = Path(__file__).resolve().parents[2] / "examples" / "python" / "voice-agent"

_INTEGRATIONS_PREFIX = ("integrations", "integrations.")


@contextlib.contextmanager
def _voice_modules():
    """Import the example's ``integrations.voice`` package in isolation.

    Saves and clears any already-imported ``integrations*`` modules, puts the
    example directory first on ``sys.path``, then restores the previous state
    on exit so unrelated tests keep their own ``integrations`` package.
    """
    saved = {
        name: mod
        for name, mod in list(sys.modules.items())
        if name == "integrations" or name.startswith("integrations.")
    }
    for name in saved:
        del sys.modules[name]
    sys.path.insert(0, str(_ROOT))
    try:
        yield importlib.import_module
    finally:
        with contextlib.suppress(ValueError):
            sys.path.remove(str(_ROOT))
        for name in [
            name
            for name in list(sys.modules)
            if name == "integrations" or name.startswith("integrations.")
        ]:
            del sys.modules[name]
        sys.modules.update(saved)


@pytest.fixture(autouse=True)
def voice_env(monkeypatch, tmp_path):
    monkeypatch.setenv("VOICE_API_KEY", "test-voice-key")
    monkeypatch.setenv("VOICE_API_BASE", "https://voice-provider.example.test")
    monkeypatch.setenv("VOICE_SERVER_URL_SECRET", "secret-123")
    monkeypatch.setenv("PUBLIC_API_BASE_URL", "https://voice-dev.example.test")
    monkeypatch.setenv("PRAISONAI_VOICE_DIR", str(tmp_path))


def test_execute_tool_get_current_time():
    with _voice_modules() as imp:
        tools = imp("integrations.voice.tools")
        raw = tools.execute_tool("get_current_time", {})
    assert "utc" in raw


def test_tool_calls_webhook_response():
    with _voice_modules() as imp:
        processor = imp("integrations.voice.processor")
        config = imp("integrations.voice.config")
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


def test_assistant_request_returns_default_assistant(monkeypatch):
    monkeypatch.setenv("VOICE_ASSISTANT_ID", "asst-123")
    with _voice_modules() as imp:
        processor = imp("integrations.voice.processor")
        config = imp("integrations.voice.config")
        settings = config.load_voice_settings()
        result = processor.process_voice_webhook(
            "assistant-request",
            {"message": {"type": "assistant-request", "call": {"id": "c1"}}},
            settings=settings,
        )
    assert result == {"assistantId": "asst-123"}


def test_transcript_appends_final_lines():
    with _voice_modules() as imp:
        processor = imp("integrations.voice.processor")
        config = imp("integrations.voice.config")
        store = imp("integrations.voice.store")
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


def test_transcript_preserves_live_status():
    """A transcript/conversation event must not reset an in-progress call to 'unknown'."""
    with _voice_modules() as imp:
        processor = imp("integrations.voice.processor")
        config = imp("integrations.voice.config")
        store = imp("integrations.voice.store")
        settings = config.load_voice_settings()
        processor.process_voice_webhook(
            "status-update",
            {"message": {"type": "status-update", "call": {"id": "call-s1"}, "status": "in-progress"}},
            settings=settings,
        )
        processor.process_voice_webhook(
            "transcript",
            {
                "message": {
                    "type": "transcript",
                    "call": {"id": "call-s1"},
                    "role": "user",
                    "transcriptType": "final",
                    "transcript": "hello there",
                }
            },
            settings=settings,
        )
        record = store.VoiceCallStore.get_call("call-s1")
    assert record["status"] == "in-progress"
    assert "user: hello there" in record["transcript"]


def test_status_updates_are_not_collapsed_by_idempotency_key():
    """Distinct status transitions must each be applied, not dropped as duplicates.

    A large static field (``call``) sorts before ``status``; a truncated JSON key
    would collapse successive updates to the same prefix and freeze the dashboard
    status on the first value.
    """
    with _voice_modules() as imp:
        processor = imp("integrations.voice.processor")
        config = imp("integrations.voice.config")
        store = imp("integrations.voice.store")
        settings = config.load_voice_settings()
        big_call = {
            "id": "call-cascade",
            "orgId": "org-" * 40,
            "assistantId": "asst-" * 40,
            "customer": {"number": "+15551234567"},
        }

        def status_event(status):
            return {"message": {"call": big_call, "status": status, "type": "status-update"}}

        for status in ("ringing", "in-progress", "ended"):
            processor.process_voice_webhook("status-update", status_event(status), settings=settings)
        record = store.VoiceCallStore.get_call("call-cascade")
    assert record["status"] == "ended"


def test_verify_secret_header():
    with _voice_modules() as imp:
        verify = imp("integrations.voice.verify")
        verify.verify_webhook_request(
            secret="secret-123",
            headers={"x-voice-webhook-secret": "secret-123"},
        )
        with pytest.raises(verify.VerificationError):
            verify.verify_webhook_request(
                secret="secret-123", headers={"x-voice-webhook-secret": "wrong"}
            )


def test_create_call_request_body(monkeypatch):
    monkeypatch.setenv("VOICE_ASSISTANT_ID", "asst-1")
    monkeypatch.setenv("VOICE_PHONE_NUMBER_ID", "pn-1")
    with _voice_modules() as imp:
        client_mod = imp("integrations.voice.client")
        config_mod = imp("integrations.voice.config")
        settings = config_mod.load_voice_settings()
        client = client_mod.VoiceClient(settings)
        client._request = MagicMock(return_value={"id": "call-1"})  # noqa: SLF001
        client.create_call(customer_number="+15551234567")
        body = client._request.call_args.kwargs["json_body"]  # noqa: SLF001
    assert body["assistantId"] == "asst-1"
    assert body["customer"]["number"] == "+15551234567"
