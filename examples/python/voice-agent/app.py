"""Voice Agent — PraisonAIUI example with telephony webhooks.

  - Outbound calls     → POST /api/voice/calls
  - Provider webhooks  → POST /webhooks/voice (tool-calls, transcript, end-of-call)
  - Dashboard          → Calls list + call detail + chat

Run:
    cd examples/python/voice-agent
    pip install httpx
    copy .env.example .env
    .\\start_dev.ps1
    python app.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

from starlette.routing import Route
from voice_routes import (
    api_voice_call_detail,
    api_voice_config_status,
    api_voice_create_call,
    api_voice_list_calls,
    webhook_voice,
)

import praisonaiui as aiui
from praisonaiui.server import create_app

_EXAMPLE_DIR = Path(__file__).resolve().parent
if str(_EXAMPLE_DIR) not in sys.path:
    sys.path.insert(0, str(_EXAMPLE_DIR))


def _load_local_env() -> None:
    env_path = _EXAMPLE_DIR / ".env"
    if not env_path.is_file():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if key and key not in os.environ:
            os.environ[key] = value


_load_local_env()

aiui.set_pages(["chat", "voice-calls", "call-detail", "config"])
aiui.set_style("dashboard")

_agent: Any | None = None


def get_agent():
    global _agent
    if _agent is not None:
        return _agent
    from integrations.voice.tools import echo_message, get_current_time, register_tool
    from praisonaiagents import Agent

    register_tool("get_current_time", get_current_time)
    register_tool("echo_message", echo_message)

    _agent = Agent(
        name="Voice Assistant",
        instructions=(
            "You help with voice and phone agent demos. "
            "Use tools when the caller asks for the time or wants a message echoed."
        ),
        model="gpt-4o-mini",
        tools=[get_current_time, echo_message],
    )
    from praisonaiagents.escalation.loop_guard import LoopGuard, LoopGuardConfig

    max_turn_sec = float(os.getenv("VOICE_AGENT_LOOP_GUARD_MAX_SEC", "600"))
    _agent._loop_guard = LoopGuard(
        LoopGuardConfig(enabled=True, max_time_per_turn=max_turn_sec)
    )
    return _agent


def _demo_tool_reply(text: str) -> str | None:
    """Fast path for demo tool queries — no LLM required."""
    from integrations.voice.tools import echo_message, get_current_time

    lower = text.lower().strip()
    if any(p in lower for p in ("what time", "current time", "tell me the time", "time is it")):
        return str(get_current_time()["spoken"])
    if lower.startswith("echo "):
        return str(echo_message(text[5:])["spoken"])
    if lower.startswith("repeat "):
        return str(echo_message(text[7:])["spoken"])
    return None


@aiui.reply
async def on_message(message: str):
    """Chat with the voice demo agent."""
    text = str(message).strip()
    if not text:
        return

    demo = _demo_tool_reply(text)
    if demo is not None:
        await aiui.say(demo)
        return

    if not os.getenv("OPENAI_API_KEY"):
        await aiui.say(
            "Set `OPENAI_API_KEY` for open-ended chat. "
            'Demo without API key: "What time is it?" or "Echo hello world".'
        )
        return
    await aiui.think("Running voice assistant...")
    agent = get_agent()
    response = await asyncio.to_thread(agent.chat, text)
    await aiui.say(str(response))


def _list_calls() -> list[dict[str, Any]]:
    from integrations.voice.store import VoiceCallStore

    rows = VoiceCallStore.list_calls()
    return [
        {
            "id": r["call_id"],
            "status": r["status"],
            "customer_number": r.get("customer_number") or "—",
            "transcript_preview": (r.get("transcript") or "")[:120],
            "updated_at": r.get("updated_at") or "",
        }
        for r in rows
    ]


def _pick_call(calls: list[dict[str, Any]]) -> dict[str, Any]:
    live = next((c for c in calls if "progress" in (c.get("status") or "").lower()), None)
    return live or (calls[0] if calls else {"id": "—", "status": "none", "customer_number": "—", "transcript_preview": ""})


@aiui.page("voice-calls", title="Voice calls", icon="📞", group="Voice", order=1)
async def voice_calls_page():
    calls = _list_calls()
    rows = [[c["id"], c["customer_number"], c["status"], c["updated_at"]] for c in calls]
    return aiui.layout(
        [
            aiui.text("Outbound and inbound voice calls via telephony provider"),
            aiui.table(
                headers=["Call ID", "Customer", "Status", "Updated"],
                rows=rows or [["—", "—", "—", "—"]],
            ),
            aiui.alert(
                "Start a call: POST /api/voice/calls with {\"customer_number\": \"+1...\"}. "
                "Set provider webhook URL to {PUBLIC_API_BASE_URL}/webhooks/voice",
                variant="info",
                title="Setup",
            ),
        ]
    )


@aiui.page("call-detail", title="Call detail", icon="🎙️", group="Voice", order=2)
async def call_detail_page():
    from integrations.voice.store import VoiceCallStore

    calls = _list_calls()
    picked = _pick_call(calls)
    record = VoiceCallStore.get_call(picked["id"]) if picked.get("id") and picked["id"] != "—" else None
    transcript = (record or {}).get("transcript") or "(empty)"
    summary = (record or {}).get("summary") or "(not available yet)"
    status = (record or {}).get("status") or picked.get("status") or "unknown"
    return aiui.layout(
        [
            aiui.text(f"Call {picked.get('id', '—')}"),
            aiui.badge(status, variant="default"),
            aiui.tabs(
                [
                    {"label": "Transcript", "children": [aiui.code_block(transcript, language="text")]},
                    {"label": "Summary", "children": [aiui.text(summary)]},
                ]
            ),
        ]
    )


app = create_app()
app.routes[0:0] = [
    Route("/api/voice/calls", api_voice_create_call, methods=["POST"]),
    Route("/api/voice/calls", api_voice_list_calls, methods=["GET"]),
    Route("/api/voice/calls/{call_id}", api_voice_call_detail, methods=["GET"]),
    Route("/api/voice/config", api_voice_config_status, methods=["GET"]),
    Route("/webhooks/voice", webhook_voice, methods=["POST"]),
]

if __name__ == "__main__":
    import uvicorn

    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("VOICE_AGENT_PORT", "8001"))
    uvicorn.run(app, host=host, port=port)
