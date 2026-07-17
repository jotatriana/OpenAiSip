# OpenAI SIP Bridge

An AI-powered inbound call handler that connects OpenAI's Realtime API to a SIP telephony system. Incoming calls are answered by a GPT voice assistant that greets callers by name, guides them through a structured 6-phase conversation, executes back-end tools against a real database, and escalates to a human agent when needed — with a complete audit trail and real-time operator dashboard.

## Architecture

```
Caller → SIP Provider → POST /webhooks/sip
                              │
                    sip_bridge (port 8000)
                    ┌─────────┴──────────┐
             webhook_handler        circuit breaker
             maintenance mode       budget gate
                              │
                    OpenAI Realtime API (WSS)
                    ┌─────────┴──────────┐
               ConvFSM (6 phases)    ToolExecutor
                                          │
                              SQLite / PostgreSQL
                     ┌────────────────────┼────────────────────┐
               customers             call_transcripts      call_events
               services              call_detail_records   escalation_contexts
               incidents/tickets
                              │
                    In-process EventBus
                              │
                    dashboard (port 8001)
                              │
                    Operator Browser (WebSocket)
```

Both servers run in the **same process** so they share a single in-memory `EventBus` and `StateStore` — no external message broker required.

## Features

- **Inbound SIP call handling** via OpenAI Realtime webhooks (Svix HMAC-SHA256 signature verification)
- **Caller ID personalisation** — display name and E.164 number parsed from SIP `From` header; caller greeted by name and pre-looked-up against the DB
- **6-phase conversation FSM**: Greeting → Verify → Triage → Diagnose → Resolve → Wrap-Up
  - VERIFY is skipped automatically when the caller is identified by caller ID
  - Model-driven phase transitions via `phase_complete` tool; 8-turn auto-advance safety net (TRIAGE capped at 2 turns, DIAGNOSE at 4 turns)
- **Realtime 2.0 prompting alignment** (`gpt-realtime-2.1`) — `session.update` sets `reasoning.effort` per phase (`minimal` for GREETING/TRIAGE/WRAP_UP, `low` for VERIFY/RESOLVE, `medium` for DIAGNOSE) so the model reasons only as deeply as each phase actually needs; base instructions include `## Reasoning` and `## Verbosity` sections per the model's prompting guide
- **Service category classification** — TRIAGE requires the model to classify every caller into one of 6 categories via `phase_complete(service_category=...)`: `technical_support`, `billing`, `sales`, `move_transfer`, `appointment`, `account`; category is validated, written to `Call.service_category`, persisted to the CDR, and used to select the correct tool set for DIAGNOSE and RESOLVE
- **Category-specific tool routing** — DIAGNOSE and RESOLVE expose only the tools for the matched category; unrelated tools are withheld entirely; `_PHASE_TOOL_ALLOWLIST` is the security backstop preventing cross-category tool calls
- **Live database tool calls** — `lookup_customer` (by verified phone, email, or account ID), `get_service_status` (returns services, open incidents, and open support tickets), `create_ticket`, `get_ticket` (look up any ticket by ID including resolved), `update_ticket` (change status/priority with caller confirmation), `get_account_history` (resolved tickets + incidents for repeat-caller context) — all backed by SQLAlchemy async ORM (SQLite / PostgreSQL); ticket creation requires ONE-round issue summary confirmation and reads back the `ticket_id` immediately after
- **16 v1 stub tools** for non-technical categories — `get_account_balance`, `get_payment_history`, `make_payment`, `setup_autopay` (billing); `get_product_catalog`, `get_promotions`, `initiate_upgrade` (sales); `get_service_eligibility`, `initiate_service_move`, `cancel_service` (move/transfer); `get_appointments`, `confirm_appointment`, `cancel_appointment`, `reschedule_appointment` (appointment); `get_account_details`, `update_contact_info` (account) — all return `feature_pending` and auto-escalate to a live agent; each has a preamble phrase so the caller hears a holding message
- **Post-tool `response.create` overrides** — targeted per-phase/per-tool instruction overrides injected after each tool result to prevent common model failure modes (DIAGNOSE entry forces `tool_choice: "required"` with no instructions override to preserve session context; TRIAGE forces silent routing; RESOLVE prevents repeating DIAGNOSE; WRAP_UP prevents proactive tool calls; `get_service_status`, `create_ticket`, and `update_ticket` each have custom scripting overrides)
- **Incident vs ticket terminology enforced** — base instructions define the distinction with explicit scripted responses for each field (`open_incidents` → "known service incident in your area", `open_support_tickets` → "open support ticket"); DIAGNOSE phase scripts both fields separately; model is explicitly prohibited from calling an incident a ticket or vice versa
- **Billing guardrail** — for non-billing calls, every phase explicitly redirects billing questions to a live agent with a fixed phrase; for billing-category calls, the guardrail is replaced with a "Billing & Payments" section that enables the billing stub tools, preventing the model from fabricating charges or balances
- **Confirmed account context** — when a caller is identified by caller ID, the confirmed `account_id` is injected into every phase's instructions; the model can never invent a different account ID or ask the caller to re-verify
- **Phone lookup hard enforcement** — `ToolExecutor` validates every `lookup_customer` phone call against the session's verified caller ID before touching the DB; fabricated numbers are rejected with a log warning and the model is told to ask for email or account ID instead
- **`lookup_customer` PROACTIVE suppressed when account known** — `_tools_for_phase()` switches the tool's behaviour tag from `PROACTIVE` to `ON DEMAND ONLY` once the account is confirmed, preventing the model from auto-calling it with invented identifiers
- **Concurrent tool call serialisation** — per-session `asyncio.Lock` ensures that when the model batches multiple function calls in one response, they execute one at a time and never race on the shared `_response_ready` event
- **Native tool-call preambles** — `gpt-realtime-2` speaks its own preamble (steered by "Preamble sample phrases" in each tool description) in the same turn it calls a tool; `ToolExecutor` waits for that triggering response to finish, then dispatches the tool immediately — no separate scripted preamble turn, no double-speaking
- **`wait_for_user` no-op tool** — available in every phase (including TRIAGE); the model calls it for audio not addressed to it (silence, background noise, hold music, side conversation) and the turn ends without a spoken reply, instead of guessing or saying "I didn't catch that"
- **Automatic escalation** via SIP REFER when frustration or tool-failure thresholds are exceeded
- **Warm handoff context** — structured briefing packet written to DB (and optionally POSTed to agent desktop webhook) when a call escalates
- **WebSocket resilience** — 3-attempt reconnection with exponential backoff; configurable ping/pong heartbeat
- **Circuit breaker** — automatically rejects new calls when repeated WS failures indicate a degraded upstream
- **Maintenance mode** — operator toggle to pause new calls (REST API)
- **Token usage tracking** per call and globally with per-type breakdown (text, audio, cached)
- **Cost tracking** — configurable per-token pricing (defaults in `config/settings.py` match `gpt-realtime-2.1` — update `COST_*_PER_1K` in `.env` if you switch models); daily spend accumulator with budget alert and hard stop
- **Caller audio transcription** — `audio.input.transcription: {model: "whisper-1"}` enabled in every `session.update`; caller turns appear in the live transcript panel alongside agent turns
- **Transcript persistence** — every spoken turn saved to DB with PCI scrubbing (regex card-number redaction); scrubbed text published to dashboard WebSocket; configurable retention
- **CDR persistence** — call detail record written at call end with billing fields, token counts, and cost
- **Per-call event timeline** — append-only log of phase transitions, tool calls, WS reconnects, and escalations; published to dashboard in real time via `CALL_EVENT` WebSocket messages
- **Operator dashboard** with live WebSocket feed, active call table (Call ID, Date/Time, From, State, Phase, Duration), live transcript panel (conversation + event timeline per call), token/cost stats, channel health, CDR browser, and log stream
- **Live transcript panel** — new incoming calls are auto-selected in the transcript dropdown so operators see the live transcript immediately; new calls appear at the top of the selector above historical CDR entries; snapshot delivers all turns for active calls and up to 50 turns for historical calls; real-time `TRANSCRIPT_TURN` events include timestamps
- **Eastern time display** — all date/time fields in the Active Calls table and transcript panel render in `America/New_York` timezone regardless of the operator's browser locale; calls table labels include `ET` suffix
- **Transferred call duration** — calls that end via SIP REFER escalation have `ended_at`, `duration_seconds`, and `hangup_cause="transferred"` set after the BYE so their duration shows correctly in the dashboard and their CDR is saved
- **CDR history on startup** — the last `CDR_HISTORY_LIMIT` (default 20) ended calls are pre-loaded from the DB into memory at startup so the dashboard shows historical calls immediately after a restart without waiting for new calls
- **Bearer token auth** on all dashboard REST and WebSocket endpoints
- **GitHub Actions CI** — automated test run with coverage gate on every push/PR

## Project Structure

```
OpenAiSip/
├── main.py                        # Entrypoint — starts both servers, inits DB, retention loop
├── config/
│   └── settings.py                # Pydantic settings loaded from .env
├── core/
│   ├── models.py                  # Pydantic models (Call, Session, TokenUsage, …)
│   ├── event_bus.py               # In-process asyncio pub/sub hub
│   ├── state_store.py             # In-memory state: calls, tokens, health, cost, maintenance mode
│   └── logger.py                  # Structured logging → EventBus
├── db/
│   ├── engine.py                  # Async SQLAlchemy engine + init_db()
│   ├── models.py                  # ORM tables (8 tables — see Database section)
│   ├── repository.py              # Async query/write functions + call event helper
│   └── seed.py                    # 15-customer sample dataset
├── sip_bridge/
│   ├── app.py                     # FastAPI app — POST /webhooks/sip
│   ├── webhook_handler.py         # Svix verification, gate checks, call creation
│   ├── call_controller.py         # OpenAI Realtime REST client (accept/reject/refer/hangup)
│   ├── session_manager.py         # Per-call WS event loop, reconnection, transcript capture
│   ├── conversation_fsm.py        # 6-phase FSM with escalation and turn-limit safety net
│   ├── prompt_builder.py          # Phase-specific session.update configs
│   └── tool_executor.py           # Tool dispatch, wait_for_user, timeout, retry, handoff context
├── dashboard/
│   ├── app.py                     # FastAPI app — REST API + WebSocket /ws/events
│   ├── auth.py                    # Bearer token auth (HTTP + WebSocket)
│   ├── ws_manager.py              # WebSocket fan-out hub + snapshot on connect
│   ├── health_collector.py        # Background health poll → EventBus
│   └── routes/
│       ├── calls.py               # GET /api/calls, /api/calls/{id}/transcript
│       ├── tokens.py              # GET /api/tokens
│       ├── health.py              # GET /api/health
│       ├── logs.py                # GET /api/logs
│       ├── config.py              # GET /api/config
│       └── operator.py            # Maintenance, CDRs, cost, event timeline, handoff context
├── tests/
│   ├── scenario_harness.py        # ScenarioHarness — full FSM test rig, all I/O mocked
│   ├── test_scenarios.py          # 16 end-to-end scenario tests
│   ├── test_conversation_fsm.py
│   ├── test_prompt_builder.py
│   ├── test_token_tracker.py
│   ├── test_webhook_handler.py
│   ├── test_ws_manager.py
│   └── test_repository_events.py
└── .github/
    └── workflows/
        └── ci.yml                 # GitHub Actions — Python 3.12, pytest, 70% coverage gate
```

## Setup

### 1. Create virtual environment and install dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure environment

Copy `.env.example` to `.env` and fill in your values:

```env
# OpenAI
OPENAI_API_KEY=sk-...
OPENAI_PROJECT_ID=proj_...
OPENAI_MODEL=gpt-realtime-2.1
OPENAI_VOICE=alloy

# Webhook (from OpenAI SIP project settings)
WEBHOOK_SECRET=whsec_...

# Dashboard
DASHBOARD_API_KEY=your-secret-key-here

# Conversation behaviour (optional — these are defaults)
HUMAN_AGENT_SIP_URI=sip:queue@avaya.internal
ESCALATION_FRUSTRATION_LIMIT=3
ESCALATION_TOOL_FAILURE_LIMIT=2
MAX_TURNS_PER_PHASE=8

# Cost tracking (optional — 0 means no limit)
DAILY_BUDGET_USD=0.0

# Warm handoff (optional — leave empty to skip webhook delivery)
HANDOFF_CONTEXT_URL=

# Database (defaults to local SQLite)
DATABASE_URL=sqlite+aiosqlite:///./openaisip.db

# Dashboard history — CDRs pre-loaded on startup (0 to disable)
CDR_HISTORY_LIMIT=20
```

### 3. Run

```bash
python main.py
```

On startup this will:
1. Create all database tables (if they don't exist)
2. Seed sample customer data (skipped if already present)
3. Pre-load the last `CDR_HISTORY_LIMIT` (default 20) CDRs into memory so the dashboard shows recent call history immediately
4. Start both servers in the same process
5. Start the daily transcript retention cleanup loop

| Service | URL |
|---|---|
| SIP Bridge (webhook receiver) | `http://0.0.0.0:8000` |
| Operator Dashboard | `http://0.0.0.0:8001` |

> **Important:** Do not start the two apps as separate `uvicorn` processes. They must share the same process to use the in-process EventBus.

## Conversation Flow

```
GREETING   →  Greet caller by name (account suffix + services for known callers); understand reason for call (1–2 exchanges max; no service tools)
VERIFY     →  Confirm identity via lookup_customer (skipped if caller ID matched)
TRIAGE     →  Classify caller into one of 6 service categories; call phase_complete(service_category=…); max 2 turns; no service/ticket/billing tools
DIAGNOSE   →  Call the category's primary diagnostic tool once; report findings; look up specific tickets or history on demand; say bridge phrase; advance (max 4 turns)
RESOLVE    →  State next action (no repeating DIAGNOSE findings); create/update tickets or escalate; advance to WRAP_UP
WRAP_UP    →  Ask if anything else; handle further issues with full tool set; thank caller; close call gracefully
```

Each phase sends a `session.update` to the OpenAI Realtime API with phase-specific instructions and a scoped set of tools. The model calls `phase_complete` to advance; if it stalls, the FSM auto-advances after the phase turn limit (TRIAGE: 2, DIAGNOSE: 4, all others: 8).

### Phase details

**GREETING** — Always the entry phase. Tools: `phase_complete`, `wait_for_user`, `escalate_to_agent` only — no `lookup_customer`, no service/ticket tools. Known callers (matched by SIP caller ID) are greeted by first name with their account suffix and service names; unknown callers get a generic greeting. Explicitly forbidden from stating anything about incidents, outages, or tickets here, even if asked — it says "let me check on that" and advances. Capped at ~2 exchanges by prompt instruction. `phase_complete` → VERIFY, but `advance()` skips VERIFY entirely when `account_id` is already known, jumping straight to TRIAGE.

**VERIFY** — Confirms identity. Known callers get a light confirmation only ("am I speaking with the account holder?"), no tool call. Unknown callers: if a verified SIP caller-ID number exists, `lookup_customer(identifier_type="phone")` is called with that number (never a number the caller speaks aloud); otherwise the model asks for email or account ID and reads it back character-by-character before submitting. Tools: `lookup_customer`, `phase_complete`, `wait_for_user`, `escalate_to_agent`. → TRIAGE. Entirely skipped for known callers.

**TRIAGE** — Classifies the issue into exactly one of the 6 `service_category` values, nothing else. Tools: only `phase_complete` (+ `wait_for_user`) — no `lookup_customer`, no service tools, no `escalate_to_agent`. `tool_choice` is forced to `"required"` at the session level so the model cannot generate speech-only turns, and the phase is hard-capped at 2 turns in code (`conversation_fsm.py`'s `_TRIAGE_MAX_TURNS`). `phase_complete` requires `service_category`, validated against `SERVICE_CATEGORIES`, written to `Call.service_category`, and that value determines which tools appear in DIAGNOSE/RESOLVE/WRAP_UP. Always → DIAGNOSE.

**DIAGNOSE** — Calls the matched category's primary read tool and reports findings. Hard-capped at 4 turns. Instructed to call the primary tool immediately, no clarifying questions first (except move_transfer, which needs the new address). After reporting, says a brief bridge phrase, then calls `phase_complete` — never asks "anything else?" here (that's WRAP_UP's job). `tool_choice: "required"` is forced on entry via a post-`phase_complete` override to guarantee the tool actually fires. Reasoning effort: `medium` — the one phase with genuine multi-step reasoning. → RESOLVE.

**RESOLVE** — Takes action on what DIAGNOSE found; explicitly told not to repeat it. `technical_support` uses `create_ticket` (confirm once, read back `ticket_id`) or `update_ticket`; other categories' write/action tools are still `feature_pending` stubs, so the model tells the caller and calls `escalate_to_agent`. `tool_choice` is *not* forced here, so the model can ask a confirmation question and wait for the answer before calling a write tool. Reasoning effort: `low`. → WRAP_UP.

**WRAP_UP** — Confirms nothing else is needed, closes cleanly. Warns the model not to claim the issue is "fixed" if a service incident might still be active. The full category tool set stays available for follow-ups (e.g. "what tickets do I have" → `get_service_status`), then it re-asks "anything else?" before calling `phase_complete`. Reasoning effort: `minimal`. `phase_complete` from WRAP_UP doesn't move to another phase — it's the one case that ends the call (`_end_session()`).

**Backward loopback (RESOLVE/WRAP_UP → TRIAGE)** — the `report_new_issue` tool lets the model send the call back to TRIAGE when the caller raises a different or unrelated issue (e.g. a billing question during a technical support call). `ToolExecutor` calls `fsm.transition(ConvPhase.TRIAGE, reason=summary)` instead of the normal forward `advance()`; the underlying `ConversationFSM.transition(target, reason)` method supports jumping to any phase and is also exercised directly by `tests/test_conversation_fsm.py::test_backward_transition`. The loopback always lands on TRIAGE rather than jumping straight back to DIAGNOSE — the new issue might belong to a different `service_category` entirely, so it's cheaper and more reliable to let TRIAGE's already-tested 2-turn classification pick the right category than to have the model guess whether it's "the same" issue.

> **Realtime session-config schema:** `prompt_builder.build()` produces `{"type": "realtime", "model", "instructions", "tools", "tool_choice", "reasoning": {"effort": ...}, "audio": {"input": {...}, "output": {...}}}`. Audio format fields are objects, not strings — `{"type": "audio/pcm", "rate": 24000}`, not `"pcm16"`. This same dict is sent as-is both as the REST body to `POST /v1/realtime/calls/{call_id}/accept` and nested under `{"type": "session.update", "session": ...}` for every WS phase transition. Requests to both the REST accept endpoint and the WebSocket use only `Authorization: Bearer <key>` — do **not** add an `OpenAI-Beta: realtime=v1` header; it routes the request to a stale API path whose call registry the WebSocket layer can't see, producing a `call_id_not_found` 404 on connect even though `/accept` returns 200.
>
> **`conversation.item.create` content type:** assistant-authored `message` items (e.g. the pre-transfer escalation announcement in `conversation_fsm.py`) must use `content: [{"type": "output_text", "text": ...}]`. The old `"type": "text"` is rejected with `invalid_value` once the item is actually processed — the surrounding `conversation.item.create`/`response.create` calls still return success, so the failure only shows up as an `error` event a beat later in the WS event stream.

### Tool availability by phase

Tools available in DIAGNOSE/RESOLVE depend on the `service_category` set during TRIAGE. Only the tools for the matched category are exposed; unrelated tools are withheld.

| Tool | Category | GREETING | VERIFY | TRIAGE | DIAGNOSE | RESOLVE | WRAP_UP |
|---|---|:-:|:-:|:-:|:-:|:-:|:-:|
| `phase_complete` | — | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `wait_for_user` | — | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `escalate_to_agent` | — | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `report_new_issue` | — | — | — | — | — | ✓ | ✓ |
| `lookup_customer` | — | — | ✓ | — | ✓ | ✓ | ✓ |
| `get_service_status` | technical_support | — | — | — | ✓ | ✓ | ✓ |
| `get_ticket` | technical_support | — | — | — | ✓ | ✓ | ✓ |
| `get_account_history` | technical_support | — | — | — | ✓ | ✓ | ✓ |
| `create_ticket` | technical_support | — | — | — | ✓ | ✓ | ✓ |
| `update_ticket` | technical_support | — | — | — | — | ✓ | ✓ |
| `get_account_balance` | billing | — | — | — | ✓ | — | — |
| `get_payment_history` | billing | — | — | — | ✓ | — | — |
| `make_payment` | billing | — | — | — | — | ✓ | — |
| `setup_autopay` | billing | — | — | — | — | ✓ | — |
| `get_product_catalog` | sales | — | — | — | ✓ | — | — |
| `get_promotions` | sales | — | — | — | ✓ | — | — |
| `initiate_upgrade` | sales | — | — | — | — | ✓ | — |
| `get_service_eligibility` | move_transfer | — | — | — | ✓ | — | — |
| `initiate_service_move` | move_transfer | — | — | — | — | ✓ | — |
| `cancel_service` | move_transfer | — | — | — | — | ✓ | — |
| `get_appointments` | appointment | — | — | — | ✓ | — | — |
| `confirm_appointment` | appointment | — | — | — | — | ✓ | — |
| `cancel_appointment` | appointment | — | — | — | — | ✓ | — |
| `reschedule_appointment` | appointment | — | — | — | — | ✓ | — |
| `get_account_details` | account | — | — | — | ✓ | — | — |
| `update_contact_info` | account | — | — | — | — | ✓ | — |

> Note: `lookup_customer` is not listed in TRIAGE above because it is removed from TRIAGE's tool set in the current implementation (account should already be confirmed before TRIAGE, or VERIFY handles it). The 7 read tools for the non-technical-support categories (`get_account_balance`, `get_payment_history`, `get_product_catalog`, `get_promotions`, `get_service_eligibility`, `get_appointments`, `get_account_details`) are mock-data-backed and return real data. The 9 write/action tools (`make_payment`, `setup_autopay`, `initiate_upgrade`, `initiate_service_move`, `cancel_service`, `confirm_appointment`, `cancel_appointment`, `reschedule_appointment`, `update_contact_info`) are still v1 stubs that return `feature_pending` and auto-escalate.

### Escalation

A call is transferred to `HUMAN_AGENT_SIP_URI` via SIP REFER when either threshold is crossed:

| Trigger | Default threshold |
|---|---|
| Caller frustration signals | 3 |
| Consecutive tool failures | 2 |

When escalation fires, an `EscalationContext` record is written to the DB containing the caller's identity, phase at escalation, reason, frustration/failure counts, and the last 10 transcript turns. If `HANDOFF_CONTEXT_URL` is set, this context is also POSTed in real time to the agent desktop system.

## Database

### Schema

| Table | Purpose |
|---|---|
| `customers` | Account records — looked up by verified phone, email, or account ID |
| `services` | Per-customer service subscriptions (internet, phone, TV, mobile) |
| `service_incidents` | Open or historical outages linked to a customer/service |
| `support_tickets` | Tickets created during calls |
| `call_transcripts` | Per-turn transcript; PCI-scrubbed; purged after `TRANSCRIPT_RETENTION_DAYS` |
| `call_events` | Append-only event timeline per call |
| `call_detail_records` | CDR snapshot written at call end; includes `service_category` column |
| `escalation_contexts` | Warm handoff packet for agent desktop |

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

15 customers, 33 services, 6 open incidents, and 5 pre-existing tickets covering a realistic range of scenarios.

## Operator Dashboard

Open `http://<host>:8001` in a browser. On first visit a login overlay prompts for `DASHBOARD_API_KEY`. The key is saved to `localStorage` for subsequent visits.

### REST API

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/calls` | List all calls (active + recent) |
| GET | `/api/calls/{id}` | Single call detail |
| GET | `/api/calls/{id}/transcript` | Full per-turn transcript |
| POST | `/api/calls/{id}/hangup` | Force-hang up a call |
| GET | `/api/tokens/summary` | Global token + cost totals |
| GET | `/api/tokens/{id}` | Per-call token totals |
| GET | `/api/health` | Channel health snapshot |
| GET | `/api/logs` | Recent log buffer |
| GET | `/api/operator/maintenance` | Get maintenance mode state |
| POST | `/api/operator/maintenance?enabled=true` | Toggle maintenance mode |
| GET | `/api/operator/cdrs?limit=100` | Recent CDRs |
| GET | `/api/operator/cdrs/{id}` | Single CDR |
| GET | `/api/operator/cost/today` | Daily spend vs budget |
| GET | `/api/operator/events/{id}` | Per-call event timeline |
| GET | `/api/operator/handoff/{id}` | Escalation context for agent desktop |

### WebSocket events

Connect to `ws://<host>:8001/ws/events?token=<DASHBOARD_API_KEY>`. A snapshot of current state is sent on connect. Subsequent messages are JSON with a `type` field:

| Type | Payload |
|---|---|
| `SNAPSHOT` | Full state snapshot on connect — includes `active_call_transcripts` (all turns for active calls; last 50 for ended calls) and `active_call_events` (same limits) |
| `CALL_CREATED` | New inbound call |
| `CALL_UPDATED` | State/phase change |
| `CALL_ENDED` | Call finished |
| `TOKEN_USAGE` | Per-response token breakdown |
| `TRANSCRIPT_TURN` | Single transcript turn (PCI-scrubbed text, role, phase, turn_index) |
| `CALL_EVENT` | Per-call event (phase_entered, tool_called, tool_failed, ws_reconnected, escalated, …) |
| `HEALTH_UPDATE` | Channel health metrics |
| `LOG_ENTRY` | Structured log message |
| `BUDGET_ALERT` | Daily budget threshold crossed |

## Resilience

| Mechanism | Behaviour |
|---|---|
| WS reconnection | Up to 3 attempts, 0.5 / 1.0 / 2.0 s backoff. Re-enters current FSM phase on reconnect. |
| Circuit breaker | Rejects new calls with 503 after N failures in a sliding window. Auto-resets after cooldown. |
| Maintenance mode | `POST /api/operator/maintenance?enabled=true` pauses all new calls until disabled. |
| Budget hard stop | Rejects new calls when `daily_cost_usd >= DAILY_BUDGET_USD` (if set). |
| Tool timeout | 5 s default; one retry on transient DB errors. |
| Turn-limit safety | Auto-advances FSM phase after the phase limit if `phase_complete` is never called (TRIAGE: 2 turns, DIAGNOSE: 4 turns, all others: 8 turns). |

## Testing

```bash
pytest                        # all tests
pytest tests/test_scenarios.py -v   # scenario tests only
pytest --cov=sip_bridge --cov=core --cov=db --cov-report=term-missing
```

**218 tests** across unit tests and 16 end-to-end scenario tests. CI runs automatically on push/PR via GitHub Actions.

## Security

A full OWASP Top 10 (2025) audit was conducted on 2026-06-08. The report and automated scanner output are in `audit/2026-06-08/`. Summary:

| Finding | Severity | Status |
|---|---|---|
| SEV-001: Stored XSS in calls table via SIP `From` header | High | ✅ Fixed — `_esc()` wraps all dynamic fields in `calls-panel.js` |
| SEV-002: Webhook replay — no timestamp validation | Medium | ✅ Fixed — ±5-minute freshness check in `webhook_handler.py` |
| SEV-003: Unpinned dependencies / no lockfile | Medium | ⚠️ Partial — `idna`, `mako`, `python-dotenv`, `pytest` patched; `starlette`/`fastapi` major bump pending; lockfile not yet committed |
| SEV-004: Webhook verification fails open when secret is empty | Low | ✅ Fixed — raises `HTTP 500` if `WEBHOOK_SECRET` is unset |
| SEV-005: Non-constant-time API-key comparison | Low | ✅ Fixed — `hmac.compare_digest` used in `dashboard/auth.py` |
| SEV-006: Dashboard served over plain HTTP, bound to all interfaces | Low | ⚠️ Open — use a TLS-terminating reverse proxy in production; bind dashboard to `127.0.0.1` |
| SEV-007: `ngrok.yml` not git-ignored | Low | ✅ Fixed — added to `.gitignore` |
| SEV-008: Verbose exception text surfaced to model/logs | Info | ⚠️ Open — low risk; tighten in a future pass |
| SEV-009: Best-effort-only PCI/PII scrubbing in transcripts | Info | ⚠️ Open — production deployments should use a certified PII service and encrypt the transcript store at rest |

### Production deployment checklist

- Set `WEBHOOK_SECRET` (required — server rejects requests if unset)
- Terminate TLS in front of both services with a reverse proxy (nginx, Caddy, etc.)
- Bind the dashboard (`port 8001`) to `127.0.0.1` or a private interface; only the SIP webhook (`port 8000`) needs to be public
- Run `pip-audit` and pin exact dependency versions with a lockfile (`pip-compile` or `uv`) before deploying
- Rotate any `ngrok.yml` authtoken that may have existed before the file was git-ignored

## Health Check

```
GET /health   →  {"status": "ok"}   (SIP bridge, port 8000)
```
