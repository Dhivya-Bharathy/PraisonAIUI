# Voice Agent example — PraisonAIUI

Phone and web voice agents using an external telephony/voice platform for STT/TTS/calls and PraisonAI for tools + dashboard.

## Quick start

```powershell
cd examples/python/voice-agent
copy .env.example .env
# Fill VOICE_API_BASE, VOICE_PRIVATE_API_KEY, VOICE_ASSISTANT_ID, etc.

# Fully automatic (app + tunnel + provider config):
.\setup_all.ps1

# Or step by step:
.\start_dev.ps1
```

Dashboard: http://127.0.0.1:8001

## Provider setup

1. Create an **assistant** with tools `get_current_time` and `echo_message`
2. Set **webhook URL** to `{PUBLIC_API_BASE_URL}/webhooks/voice`
3. Set **webhook secret header** `X-Voice-Webhook-Secret` → same as `VOICE_SERVER_URL_SECRET` in `.env`
4. Add a phone number and copy `VOICE_PHONE_NUMBER_ID`

Or run `py -3.13 setup_voice_provider.py` after `start_dev.ps1`.

## API

| Route | Method | Purpose |
|-------|--------|---------|
| `/api/voice/calls` | POST | Outbound call `{ "customer_number": "+1..." }` |
| `/api/voice/calls` | GET | List stored calls |
| `/api/voice/calls/{id}` | GET | Call detail + transcript |
| `/webhooks/voice` | POST | Provider webhooks (tool-calls, transcript, end-of-call) |
| `/api/voice/config` | GET | Config status |

## Architecture

External platform handles voice pipeline (phone, STT, TTS, turn-taking). This app handles:

- **tool-calls** → PraisonAI-registered tools (`integrations/voice/tools.py`)
- **transcript** / **end-of-call-report** → SQLite store + dashboard pages
- **assistant-request** → returns `VOICE_ASSISTANT_ID`

Same pattern as `examples/python/meeting-agent` (Recall webhooks).

## Tests

```bash
pytest tests/unit/test_voice_agent.py -v
```
