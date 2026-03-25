# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run the application (starts both servers)
python main.py

# Run all tests
pytest

# Run a single test file
pytest tests/test_conversation_fsm.py

# Run a single test
pytest tests/test_conversation_fsm.py::test_phase_advances_in_order -v

# Install dependencies
pip install -r requirements.txt
```

## Architecture

This project is an **OpenAI SIP Bridge** — it receives inbound SIP calls via OpenAI webhooks and connects them to the OpenAI Realtime API for AI-driven voice conversations, with an operator dashboard for monitoring.

Two FastAPI apps run in the same asyncio process via `main.py`:
- **SIP Bridge** (`sip_bridge/app.py`) — port 8000, handles OpenAI webhooks and Realtime WebSocket sessions
- **Dashboard** (`dashboard/app.py`) — port 8001, operator UI with REST API and WebSocket fan-out

### Data flow for an inbound call

```
OpenAI SIP → POST /webhook → webhook_handler.py
  → CallController.accept() (REST to OpenAI Realtime)
  → SessionManager.connect() (WS to wss://api.openai.com/v1/realtime)
  → ConversationFSM.enter(GREETING) → PromptBuilder.build() → session.update
  → OpenAI events → tool calls → ToolExecutor → DB queries
  → escalation check → SIP REFER if thresholds exceeded
```

### Key in-memory shared state

- **`core/event_bus.py`** — asyncio pub/sub hub; topics are constants in `core/models.py`. All inter-module communication goes through here.
- **`core/state_store.py`** — in-memory registry for live calls, token usage, logs, and health metrics. Dashboard reads from here.

### Conversation FSM (`sip_bridge/conversation_fsm.py`)

Four phases in order: `GREETING → VERIFY → DIAGNOSE → RESOLVE`

Each `enter(phase)` call sends a `session.update` to OpenAI with a phase-specific prompt and tool set (from `PromptBuilder`). Escalation to a human agent triggers a SIP REFER when `frustration_count >= 3` or `tool_failure_count >= 2` (configurable via `.env`).

### Tools available to the model

- `lookup_customer` — available from VERIFY phase onward
- `get_service_status` — available from DIAGNOSE phase onward
- `create_ticket` — available from DIAGNOSE phase onward
- `escalate_to_agent` — always available; triggers SIP REFER

Tool calls are handled by `ToolExecutor`, which injects an audio preamble before executing to mask DB latency.

### Database

SQLite by default (`openaisip.db`); configurable to PostgreSQL via `DATABASE_URL` in `.env`. SQLAlchemy async ORM. Tables: `customers`, `services`, `service_incidents`, `support_tickets`. Seed data (15 customers) loads on startup.

### Configuration

All settings in `config/settings.py` load from `.env`. Copy `.env.example` to start. Key settings:
- `OPENAI_API_KEY`, `OPENAI_PROJECT_ID`, `OPENAI_MODEL`, `OPENAI_VOICE`
- `WEBHOOK_SECRET` — HMAC-SHA256 signature validation for OpenAI Svix webhooks
- `DASHBOARD_API_KEY` — bearer token for dashboard REST and WebSocket auth
- `HUMAN_AGENT_SIP_URI` — SIP URI for escalation transfers
- `ESCALATION_FRUSTRATION_LIMIT` (default 3), `ESCALATION_TOOL_FAILURE_LIMIT` (default 2)
