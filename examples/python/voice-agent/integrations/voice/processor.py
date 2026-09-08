"""Process voice provider webhook events."""

from __future__ import annotations

import json
import logging
from typing import Any

from integrations.voice.config import VoiceSettings
from integrations.voice.store import VoiceCallStore, webhook_event_key
from integrations.voice.tools import execute_tool

logger = logging.getLogger(__name__)


def _message(payload: dict[str, Any]) -> dict[str, Any]:
    msg = payload.get("message")
    return msg if isinstance(msg, dict) else payload


def _call_id(payload: dict[str, Any]) -> str | None:
    msg = _message(payload)
    call = msg.get("call")
    if isinstance(call, dict) and call.get("id"):
        return str(call["id"])
    return None


def _customer_number(payload: dict[str, Any]) -> str | None:
    msg = _message(payload)
    call = msg.get("call") if isinstance(msg.get("call"), dict) else {}
    customer = call.get("customer") if isinstance(call, dict) else {}
    if isinstance(customer, dict) and customer.get("number"):
        return str(customer["number"])
    return None


def process_voice_webhook(
    event_type: str, payload: dict[str, Any], *, settings: VoiceSettings
) -> dict[str, Any] | None:
    """Handle a verified provider webhook POST. Returns response body when required."""
    event_key = webhook_event_key(event_type, payload)
    if event_type not in ("tool-calls", "assistant-request", "transfer-destination-request", "knowledge-base-request"):
        if not VoiceCallStore.mark_event_processed(event_key, event_type):
            logger.info("Skipping duplicate voice event %s", event_type)
            return None

    msg = _message(payload)
    call_id = _call_id(payload)

    if event_type == "assistant-request":
        assistant_id = settings.default_assistant_id
        if not assistant_id:
            return {"error": "No assistant configured on server"}
        return {"assistantId": assistant_id}

    if event_type == "tool-calls" and call_id:
        tool_calls = msg.get("toolCallList") or []
        results = []
        for item in tool_calls:
            if not isinstance(item, dict):
                continue
            tool_call_id = str(item.get("id") or "")
            name = str(item.get("name") or "")
            params = item.get("parameters") if isinstance(item.get("parameters"), dict) else {}
            result = execute_tool(name, params)
            results.append({"toolCallId": tool_call_id, "name": name, "result": result})
        VoiceCallStore.upsert_call(call_id, status="in-progress", customer_number=_customer_number(payload))
        return {"results": results}

    if event_type == "status-update" and call_id:
        status = str(msg.get("status") or "unknown")
        VoiceCallStore.upsert_call(call_id, status=status, customer_number=_customer_number(payload))
        return None

    if event_type == "transcript" and call_id:
        role = str(msg.get("role") or "unknown")
        text = str(msg.get("transcript") or "").strip()
        if text and str(msg.get("transcriptType") or "final") == "final":
            VoiceCallStore.append_transcript_line(call_id, f"{role}: {text}")
        return None

    if event_type == "end-of-call-report" and call_id:
        artifact = msg.get("artifact") if isinstance(msg.get("artifact"), dict) else {}
        transcript = artifact.get("transcript") if isinstance(artifact, dict) else None
        summary = str(msg.get("summary") or "")
        ended = str(msg.get("endedReason") or "ended")
        VoiceCallStore.upsert_call(
            call_id,
            status=f"ended ({ended})",
            customer_number=_customer_number(payload),
            transcript=str(transcript) if transcript else None,
            summary=summary or None,
            metadata={"artifact": artifact},
        )
        return None

    if event_type == "conversation-update" and call_id:
        messages = msg.get("messages") or []
        lines = []
        for item in messages:
            if not isinstance(item, dict):
                continue
            role = item.get("role") or "unknown"
            text = item.get("message") or item.get("content") or ""
            if text:
                lines.append(f"{role}: {text}")
        if lines:
            VoiceCallStore.upsert_call(
                call_id, transcript="\n".join(lines), customer_number=_customer_number(payload)
            )
        return None

    logger.debug("Unhandled voice event %s: %s", event_type, json.dumps(payload)[:300])
    return None
