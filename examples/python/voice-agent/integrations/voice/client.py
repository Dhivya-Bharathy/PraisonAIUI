"""Minimal voice provider REST client."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from integrations.voice.config import VoiceSettings

logger = logging.getLogger(__name__)


class VoiceAPIError(RuntimeError):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class VoiceClient:
    """HTTP client for voice provider calls and assistants."""

    def __init__(self, settings: VoiceSettings):
        self._settings = settings

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._settings.api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self._settings.api_base.rstrip('/')}{path}"
        with httpx.Client(timeout=30.0) as client:
            response = client.request(method, url, headers=self._headers(), json=json_body)
        if response.status_code >= 400:
            raise VoiceAPIError(response.status_code, response.text[:500] or response.reason_phrase)
        if not response.content:
            return {}
        data = response.json()
        return data if isinstance(data, dict) else {"data": data}

    def create_call(
        self,
        *,
        customer_number: str,
        assistant_id: str | None = None,
        phone_number_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        assistant_id = assistant_id or self._settings.default_assistant_id
        phone_number_id = phone_number_id or self._settings.default_phone_number_id
        if not assistant_id:
            raise VoiceAPIError(0, "assistant_id is required (set VOICE_ASSISTANT_ID or pass explicitly)")
        if not phone_number_id:
            raise VoiceAPIError(0, "phone_number_id is required (set VOICE_PHONE_NUMBER_ID or pass explicitly)")
        body: dict[str, Any] = {
            "assistantId": assistant_id,
            "phoneNumberId": phone_number_id,
            "customer": {"number": customer_number},
        }
        if metadata:
            body["metadata"] = metadata
        return self._request("POST", "/call", json_body=body)

    def get_call(self, call_id: str) -> dict[str, Any]:
        return self._request("GET", f"/call/{call_id}")
