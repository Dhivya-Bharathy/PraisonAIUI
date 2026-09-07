"""Voice agent HTTP routes."""

from __future__ import annotations

import asyncio
import json
import logging

from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response

from integrations.voice.client import VoiceClient
from integrations.voice.config import VoiceConfigError, load_voice_settings
from integrations.voice.processor import process_voice_webhook
from integrations.voice.store import VoiceCallStore
from integrations.voice.verify import VerificationError, verify_webhook_request

logger = logging.getLogger(__name__)


async def api_voice_config_status(_request: Request) -> JSONResponse:
    try:
        settings = load_voice_settings()
        configured = True
        error = None
    except VoiceConfigError as exc:
        settings = None
        configured = False
        error = str(exc)
    return JSONResponse(
        {
            "configured": configured,
            "error": error,
            "webhook_url": settings.webhook_url if settings else None,
            "default_assistant_id": settings.default_assistant_id if settings else None,
        }
    )


async def api_voice_create_call(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    number = str(body.get("customer_number") or body.get("phone_number") or "").strip()
    if not number:
        return JSONResponse({"error": "customer_number is required"}, status_code=400)
    try:
        settings = load_voice_settings()
        client = VoiceClient(settings)
        result = await asyncio.to_thread(
            client.create_call,
            customer_number=number,
            assistant_id=body.get("assistant_id"),
            phone_number_id=body.get("phone_number_id"),
            metadata=body.get("metadata") if isinstance(body.get("metadata"), dict) else None,
        )
    except VoiceConfigError as exc:
        return JSONResponse({"error": str(exc)}, status_code=503)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=502)
    call_id = result.get("id")
    if call_id:
        VoiceCallStore.upsert_call(str(call_id), status="scheduled", customer_number=number)
    return JSONResponse(result, status_code=201)


async def api_voice_list_calls(_request: Request) -> JSONResponse:
    return JSONResponse({"calls": VoiceCallStore.list_calls()})


async def api_voice_call_detail(request: Request) -> JSONResponse:
    call_id = request.path_params["call_id"]
    record = VoiceCallStore.get_call(call_id)
    if not record:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse(record)


async def webhook_voice(request: Request) -> Response:
    raw = (await request.body()).decode("utf-8")
    try:
        settings = load_voice_settings(require_key=False)
    except VoiceConfigError as exc:
        return JSONResponse({"error": str(exc)}, status_code=503)

    headers = {k: v for k, v in request.headers.items()}
    try:
        verify_webhook_request(secret=settings.server_url_secret, headers=headers)
    except VerificationError:
        return JSONResponse({"error": "invalid secret"}, status_code=401)

    try:
        payload = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    msg = payload.get("message") if isinstance(payload.get("message"), dict) else payload
    event_type = str((msg or {}).get("type") or payload.get("type") or "unknown")

    if event_type in ("tool-calls", "assistant-request", "transfer-destination-request", "knowledge-base-request"):
        result = await asyncio.to_thread(process_voice_webhook, event_type, payload, settings=settings)
        return JSONResponse(result or {})

    asyncio.create_task(asyncio.to_thread(process_voice_webhook, event_type, payload, settings=settings))
    return PlainTextResponse("ok", status_code=200)
