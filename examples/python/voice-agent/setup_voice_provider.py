"""Configure voice provider assistant: tools + webhook URL + auth header.

Usage:
    cd examples/python/voice-agent
    py -3.13 setup_voice_provider.py
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any

import httpx

_ROOT = Path(__file__).resolve().parent
ENV_PATH = _ROOT / ".env"
WEBHOOK_SECRET_HEADER = "X-Voice-Webhook-Secret"


def _load_env() -> None:
    if not ENV_PATH.is_file():
        return
    for raw in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if key and key not in os.environ:
            os.environ[key] = value


def _write_env_value(key: str, value: str) -> None:
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.is_file() else []
    updated = False
    new_lines: list[str] = []
    for line in lines:
        if line.strip().startswith(f"{key}="):
            new_lines.append(f"{key}={value}")
            updated = True
        else:
            new_lines.append(line)
    if not updated:
        new_lines.append(f"{key}={value}")
    ENV_PATH.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    os.environ[key] = value


def _api_key() -> str:
    return os.getenv("VOICE_PRIVATE_API_KEY", "").strip() or os.getenv("VOICE_API_KEY", "").strip()


def _api_base() -> str:
    return os.getenv("VOICE_API_BASE", "").strip().rstrip("/")


def _request(
    method: str,
    path: str,
    *,
    api_key: str,
    api_base: str,
    body: dict | None = None,
) -> dict | list | None:
    url = f"{api_base}{path}"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    response = httpx.request(method, url, headers=headers, json=body, timeout=30.0)
    if response.status_code >= 400:
        print(f"ERROR {method} {path} -> {response.status_code}")
        print(response.text[:1500])
        return None
    if not response.content:
        return {}
    return response.json()


def _tool_specs() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "get_current_time",
                "description": "Returns the current UTC time when the caller asks for the time.",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
            "async": False,
        },
        {
            "type": "function",
            "function": {
                "name": "echo_message",
                "description": "Repeats back a message the caller provides.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "message": {
                            "type": "string",
                            "description": "The message to repeat back to the caller.",
                        }
                    },
                    "required": ["message"],
                },
            },
            "async": False,
        },
    ]


def ensure_server_secret() -> str:
    secret = os.getenv("VOICE_SERVER_URL_SECRET", "").strip()
    if not secret:
        secret = str(uuid.uuid4())
        _write_env_value("VOICE_SERVER_URL_SECRET", secret)
        print("Generated VOICE_SERVER_URL_SECRET")
    return secret


def ensure_tools(api_key: str, api_base: str, assistant_id: str) -> list[str]:
    existing = _request("GET", "/tool", api_key=api_key, api_base=api_base)
    by_name: dict[str, str] = {}
    if isinstance(existing, list):
        for item in existing:
            fn = item.get("function") if isinstance(item, dict) else None
            if isinstance(fn, dict) and fn.get("name") and item.get("id"):
                by_name[str(fn["name"])] = str(item["id"])

    tool_ids: list[str] = []
    for spec in _tool_specs():
        name = spec["function"]["name"]
        if name in by_name:
            tool_id = by_name[name]
            print(f"Reusing tool {name}: {tool_id}")
        else:
            created = _request("POST", "/tool", api_key=api_key, api_base=api_base, body=spec)
            if not isinstance(created, dict) or not created.get("id"):
                raise RuntimeError(f"Failed to create tool {name}")
            tool_id = str(created["id"])
            print(f"Created tool {name}: {tool_id}")
        tool_ids.append(tool_id)

    assistant = _request("GET", f"/assistant/{assistant_id}", api_key=api_key, api_base=api_base)
    if not isinstance(assistant, dict):
        raise RuntimeError("Could not load assistant")

    model = dict(assistant.get("model") or {})
    if not model.get("provider"):
        raise RuntimeError("Assistant has no model.provider — configure a model in the provider dashboard first")

    existing_ids = [str(x) for x in (model.get("toolIds") or [])]
    model["toolIds"] = list(dict.fromkeys(existing_ids + tool_ids))

    messages = list(model.get("messages") or [])
    system_hint = (
        "You are a helpful voice assistant. When the caller asks for the time, "
        "call get_current_time. When they ask you to repeat something, call echo_message."
    )
    if not messages:
        model["messages"] = [{"role": "system", "content": system_hint}]
    else:
        for msg in messages:
            if isinstance(msg, dict) and msg.get("role") == "system":
                content = str(msg.get("content") or "")
                if "get_current_time" not in content:
                    msg["content"] = f"{content.rstrip()}\n\n{system_hint}"
                break
        else:
            messages.insert(0, {"role": "system", "content": system_hint})
        model["messages"] = messages

    patched = _request(
        "PATCH",
        f"/assistant/{assistant_id}",
        api_key=api_key,
        api_base=api_base,
        body={"model": model},
    )
    if patched is None:
        raise RuntimeError("Failed to attach tools to assistant")

    print("Attached tool IDs:", model["toolIds"])
    return tool_ids


def ensure_webhook(api_key: str, api_base: str, assistant_id: str, webhook_url: str, secret: str) -> None:
    assistant = _request("GET", f"/assistant/{assistant_id}", api_key=api_key, api_base=api_base)
    if not isinstance(assistant, dict):
        raise RuntimeError("Could not load assistant for webhook setup")

    server = dict(assistant.get("server") or {})
    server["url"] = webhook_url.rstrip("/")
    server["headers"] = {WEBHOOK_SECRET_HEADER: secret}
    server.pop("credentialId", None)
    server.pop("secret", None)

    patched = _request(
        "PATCH",
        f"/assistant/{assistant_id}",
        api_key=api_key,
        api_base=api_base,
        body={"server": server},
    )
    if patched is None:
        raise RuntimeError("Failed to configure assistant server URL")

    print(f"Server URL: {webhook_url}")
    print(f"Webhook auth header: {WEBHOOK_SECRET_HEADER}")


def main() -> int:
    _load_env()
    api_key = _api_key()
    api_base = _api_base()
    assistant_id = os.getenv("VOICE_ASSISTANT_ID", "").strip()
    public_base = os.getenv("PUBLIC_API_BASE_URL", "").strip().rstrip("/")

    if not api_key:
        print("Set VOICE_PRIVATE_API_KEY in .env")
        return 1
    if not api_base:
        print("Set VOICE_API_BASE in .env")
        return 1
    if not assistant_id:
        print("Set VOICE_ASSISTANT_ID in .env")
        return 1
    if not public_base:
        print("Set PUBLIC_API_BASE_URL in .env (run .\\start_dev.ps1 first)")
        return 1

    secret = ensure_server_secret()
    webhook_url = f"{public_base}/webhooks/voice"

    try:
        ensure_tools(api_key, api_base, assistant_id)
        ensure_webhook(api_key, api_base, assistant_id, webhook_url, secret)
    except RuntimeError as exc:
        print(f"Setup failed: {exc}")
        return 1

    print("Voice provider auto-setup complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
