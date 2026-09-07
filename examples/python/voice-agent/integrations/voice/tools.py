"""Tools callable from voice provider ``tool-calls`` webhook events."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable


def get_current_time() -> dict[str, Any]:
    """Return current UTC time for voice agent demos."""
    now = datetime.now(timezone.utc).isoformat()
    return {"utc": now, "spoken": f"The current UTC time is {now}"}


def echo_message(message: str = "") -> dict[str, Any]:
    """Echo a message back to the caller."""
    text = message.strip() or "nothing"
    return {"message": text, "spoken": f"You said {text}"}


_TOOL_REGISTRY: dict[str, Callable[..., Any]] = {
    "get_current_time": get_current_time,
    "echo_message": echo_message,
}


def register_tool(name: str, fn: Callable[..., Any]) -> None:
    _TOOL_REGISTRY[name] = fn


def execute_tool(name: str, parameters: dict[str, Any] | None) -> str:
    """Run a registered tool and return a JSON string for the provider."""
    fn = _TOOL_REGISTRY.get(name)
    if fn is None:
        return json.dumps({"error": f"Unknown tool: {name}"})
    try:
        result = fn(**(parameters or {}))
        if isinstance(result, str):
            return result
        return json.dumps(result)
    except TypeError:
        try:
            result = fn(parameters or {})
            return json.dumps(result)
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"error": str(exc)})
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error": str(exc)})
