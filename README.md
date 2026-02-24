# OpenAI SIP Bridge

An AI-powered inbound call handler that connects OpenAI's Realtime API to a SIP telephony system. Incoming calls are answered by a GPT-4o voice assistant that greets callers by name, guides them through a structured conversation, executes back-end tools against a real database, and escalates to a human agent when needed. A real-time operator dashboard provides live visibility into active calls, token usage, channel health, and logs.

## Architecture

```
Caller → SIP Provider → POST /webhooks/sip
                              │
                    sip_bridge (port 8000)
                              │
                    OpenAI Realtime API (WSS)
                         │         │
                   ConvFSM      ToolExecutor
                                    │
                              SQLite / PostgreSQL
                              (customers, services,
                               incidents, tickets)
                              │
                    In-process EventBus
                              │
                    dashboard (port 8001)
                              │
                    Operator Browser (WebSocket)
```

Both the SIP bridge and the dashboard run in the **same process** so they share a single in-memory `EventBus` and `StateStore`. All real-time events (calls, tokens, logs, health) flow directly to the dashboard without any external message broker.

## Features

- **Inbound SIP call handling** via OpenAI Realtime webhooks (Svix signature verification)
- **Caller ID personalisation** — display name and E.164 number parsed from SIP `From` header; AI greets caller by first name
- **4-phase conversation FSM**: Greeting → Verify → Diagnose → Resolve
- **Live database tool calls** — `lookup_customer`, `get_service_status`, `create_ticket` backed by SQLAlchemy async + SQLite/PostgreSQL
- **Audio preambles** injected before each tool call to mask DB latency
- **Automatic escalation** via SIP REFER when frustration or tool-failure thresholds are exceeded
- **Token usage tracking** per call and globally (text, audio, cached breakdowns)
- **Operator dashboard** with live WebSocket feed, active call table, token stats, channel health, and log stream
- **Bearer token auth** on all dashboard REST and WebSocket endpoints

## Project Structure

```
OpenAiSip/
├── main.py                        # Entrypoint — starts both servers in one process, inits DB
├── config/
│   └── settings.py                # Pydantic settings loaded from .env
├── core/
│   ├── models.py                  # Pydantic models (Call, Session, TokenUsage, …)
│   ├── event_bus.py               # In-process asyncio pub/sub hub
│   ├── state_store.py             # In-memory async state registry
│   └── logger.py                  # Structured logging → EventBus
├── db/
│   ├── engine.py                  # Async SQLAlchemy engine + init_db()
│   ├── models.py                  # ORM tables (customers, services, incidents, tickets)
│   ├── repository.py              # Async query functions used by tool_executor
│   └── seed.py                    # Sample data for local testing
├── sip_bridge/
│   ├── app.py                     # FastAPI app — POST /webhooks/sip
│   ├── webhook_handler.py         # Svix verification, From-header parsing, call creation
│   ├── call_controller.py         # OpenAI Realtime REST client (accept/reject/refer/hangup)
│   ├── session_manager.py         # Per-call WebSocket event loop to OpenAI Realtime
│   ├── conversation_fsm.py        # Phase state machine (GREETING→VERIFY→DIAGNOSE→RESOLVE)
│   ├── prompt_builder.py          # Phase-specific session.update configs + caller personalisation
│   └── tool_executor.py           # Tool call handler with audio preambles + DB dispatch
└── dashboard/
    ├── app.py                     # FastAPI app — REST API + WebSocket /ws/events
    ├── auth.py                    # Bearer token auth (HTTP + WebSocket)
    ├── ws_manager.py              # WebSocket fan-out hub + snapshot on connect
    ├── health_collector.py        # Background health poll → EventBus
    ├── routes/
    │   ├── calls.py               # GET /api/calls
    │   ├── tokens.py              # GET /api/tokens
    │   ├── health.py              # GET /api/health
    │   ├── logs.py                # GET /api/logs
    │   └── config.py              # GET /api/config (exposes reconnect params)
    └── static/                    # Vanilla JS single-page dashboard
        ├── index.html
        ├── css/
        │   ├── theme.css          # CSS variables (light/dark theme)
        │   └── dashboard.css      # Layout, components, login overlay
        └── js/
            ├── ws-client.js       # ReconnectingWS with exponential backoff FSM
            ├── dashboard.js       # Main controller — login, WS init, theme
            ├── calls-panel.js
            ├── tokens-panel.js
            ├── health-panel.js
            └── logs-panel.js
```

## Setup

### 1. Create virtual environment and install dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure environment

Create a `.env` file in the project root:

```env
# OpenAI
OPENAI_API_KEY=sk-...
OPENAI_PROJECT_ID=proj_...
OPENAI_MODEL=gpt-4o-realtime-preview
OPENAI_VOICE=alloy

# Webhook (from OpenAI SIP project settings)
WEBHOOK_SECRET=whsec_...

# Database (defaults to local SQLite — change for PostgreSQL)
DATABASE_URL=sqlite+aiosqlite:///./openaisip.db

# Dashboard
DASHBOARD_API_KEY=your-secret-key-here

# Conversation behaviour (optional — these are defaults)
DEFAULT_LANGUAGE=en-US
ESCALATION_FRUSTRATION_LIMIT=3
ESCALATION_TOOL_FAILURE_LIMIT=2
HUMAN_AGENT_SIP_URI=sip:queue@avaya.internal
```

### 3. Run

```bash
python main.py
```

On startup this will:
1. Create all database tables (if they don't exist)
2. Seed sample customer data (skipped if already present)
3. Start both servers in the same process

| Service | URL |
|---|---|
| SIP Bridge (webhook receiver) | `http://0.0.0.0:8000` |
| Operator Dashboard | `http://0.0.0.0:8001` |

> **Important:** Do not start the two apps as separate `uvicorn` processes. They must share the same process to use the in-process EventBus.

## Dashboard

Open `http://<host>:8001` in a browser. On the first visit a login overlay prompts for the `DASHBOARD_API_KEY`. The key is saved to `localStorage` so subsequent visits connect automatically.

To reset the key:
```javascript
localStorage.removeItem('dashboard_api_key'); location.reload();
```

The dashboard shows:
- **Channel Health** — SIP registration state, active calls, WS sessions, setup latency, error rate
- **Token Usage** — global totals broken down by input/output/audio/cached tokens
- **Active Calls** — call ID, caller, state, conversation phase, duration (live)
- **Live Logs** — filterable by level (DEBUG / INFO / WARNING / ERROR)

## Conversation Flow

```
GREETING   →  Greet caller by first name (from SIP CallerID), understand reason for call
VERIFY     →  Confirm identity via lookup_customer (phone or account ID)
DIAGNOSE   →  Gather issue details, check service status via get_service_status
RESOLVE    →  Propose solution, open ticket via create_ticket, or escalate to human
```

Each phase sends a `session.update` to the OpenAI Realtime API with phase-specific instructions and a scoped set of tools.

### Escalation

A call is transferred to `HUMAN_AGENT_SIP_URI` via SIP REFER when either threshold is crossed:

| Trigger | Default threshold |
|---|---|
| Caller frustration signals | 3 |
| Consecutive tool failures | 2 |

## Database

### Schema

| Table | Purpose |
|---|---|
| `customers` | Account records — looked up by phone number or account ID |
| `services` | Per-customer service subscriptions (internet, phone, TV, mobile) |
| `service_incidents` | Open or historical outages linked to a customer/service |
| `support_tickets` | Tickets created during calls, linked to `call_id` |

### Tools

| Tool | Phase | DB operation |
|---|---|---|
| `lookup_customer` | VERIFY + | `SELECT` from `customers` by phone or account_id |
| `get_service_status` | DIAGNOSE + | `SELECT` services + open incidents for account |
| `create_ticket` | RESOLVE | `INSERT` into `support_tickets` with linked `call_id` |

### Switching to PostgreSQL

Change one line in `.env`:
```env
DATABASE_URL=postgresql+asyncpg://user:pass@host/dbname
```

No code changes required. Install the driver:
```bash
pip install asyncpg
```

### Seed data

The seed script runs automatically on startup. To run it manually:
```bash
python -m db.seed            # skip if data already present
python -m db.seed --reset    # clear all rows and re-seed
```

The seed loads **15 customers**, **33 services**, **6 open incidents**, and **5 pre-existing tickets** covering a realistic range of scenarios:

| Account | Name | Type | Status |
|---|---|---|---|
| ACC-JT001 | Julio Triana | Residential | Active |
| ACC-SM002 | Sarah Mitchell | Residential | Active |
| ACC-RG003 | Robert Garcia | Business | Active |
| ACC-LP004 | Linda Park | Residential | Suspended |
| ACC-DW005 | David Williams | Residential | Active |
| ACC-AC006 | Angela Chen | Business | Active |
| ACC-MK007 | Michael Kim | Residential | Active |
| ACC-FB008 | Fatima Bello | Residential | Active |
| ACC-TR009 | Thomas Rivera | Business | Active |
| ACC-NO010 | Natalie Okafor | Residential | Active |
| ACC-JL011 | James Lee | Residential | Active |
| ACC-PV012 | Patricia Vasquez | Business | Active |
| ACC-CN013 | Carlos Nguyen | Residential | Cancelled |
| ACC-EH014 | Emily Hassan | Residential | Active |
| ACC-BT015 | Brian Thompson | Business | Active |

**Open incidents in seed data:**

| Account | Incident | Severity |
|---|---|---|
| ACC-JT001 | Intermittent internet drops in area | High |
| ACC-RG003 | Complete internet outage — node failure | Critical |
| ACC-AC006 | Business phone lines dropping calls | Medium |
| ACC-NO010 | Premium TV channel pixelation | Low |
| ACC-DW005 | Scheduled maintenance notice | Low |
| ACC-FB008 | Slow speeds during peak hours | Medium |

## Webhook Endpoint

```
POST /webhooks/sip
```

Accepts Svix-signed webhook events from OpenAI. Currently handles:

- `realtime.call.incoming` — parses SIP headers, creates call record, accepts the call, opens a Realtime WebSocket session

Set this URL in your OpenAI SIP project settings. The `WEBHOOK_SECRET` must match the signing secret shown there.

## Health Check

```
GET /health   →  {"status": "ok"}   (SIP bridge, port 8000)
```
