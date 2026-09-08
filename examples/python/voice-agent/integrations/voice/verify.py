"""Verify incoming voice provider webhook requests."""

from __future__ import annotations

import hmac

_SECRET_HEADERS = ("x-voice-webhook-secret", "x-webhook-secret")


class VerificationError(ValueError):
    """Webhook secret verification failed."""


def verify_webhook_request(*, secret: str | None, headers: dict[str, str]) -> None:
    """Validate webhook secret header when ``VOICE_SERVER_URL_SECRET`` is configured."""
    if not secret:
        return
    lowered = {k.lower(): v for k, v in headers.items()}
    incoming = next((lowered[name] for name in _SECRET_HEADERS if lowered.get(name)), None)
    if not incoming or not hmac.compare_digest(incoming, secret):
        raise VerificationError("Invalid or missing webhook secret header")
