# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run the application (starts both servers)
python main.py

# Run all tests
pytest

# Run tests with coverage report
pytest --cov=sip_bridge --cov=core --cov=db --cov=dashboard --cov=config --cov-report=term-missing

# Run a single test file
pytest tests/test_conversation_fsm.py

# Run a single test
pytest tests/test_conversation_fsm.py::test_phase_advances_in_order -v

# Run scenario tests only
pytest tests/test_scenarios.py -v

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
  → maintenance mode / budget / circuit breaker gate
  → CallController.accept() (REST to OpenAI Realtime)
  → SessionManager.connect() (WS to wss://api.openai.com/v1/realtime)
  → ConversationFSM.enter(GREETING) → PromptBuilder.build() → session.update
  → OpenAI events → tool calls → ToolExecutor → DB queries
  → escalation check → SIP REFER if thresholds exceeded
  → teardown: CDR + escalation context written to DB
```

### Key in-memory shared state

- **`core/event_bus.py`** — asyncio pub/sub hub; topics are constants in `core/models.py`. All inter-module communication goes through here.
- **`core/state_store.py`** — in-memory registry for live calls, token usage, logs, health metrics, daily cost accumulator, and maintenance mode flag. Dashboard reads from here. `snapshot()` queries the DB for transcript turns and call events per call: active calls get all turns (no limit); ended/historical calls are capped at **50 turns**. Results returned as `active_call_transcripts` and `active_call_events`. `load_recent_cdrs(limit)` pre-populates `_calls` from the `call_detail_records` table at startup so ended calls survive a process restart.
- **`Call` dataclass** (`core/models.py`) — includes `service_category: str | None = None`, set by TRIAGE when `phase_complete(service_category=...)` is called; persisted to CDR at call end.
- **`SERVICE_CATEGORIES`** (`core/models.py`) — `frozenset({"technical_support", "billing", "sales", "move_transfer", "appointment", "account"})` — the allowed values for `Call.service_category`; used by `ToolExecutor` to validate the argument before writing it to state.

### Conversation FSM (`sip_bridge/conversation_fsm.py`)

Six phases in order: `GREETING → VERIFY → TRIAGE → DIAGNOSE → RESOLVE → WRAP_UP`

- **VERIFY** is skipped when the caller is identified by caller ID at call start (`account_id` already set).
- Each `enter(phase)` call sends a `session.update` to OpenAI with phase-specific prompt and tool set (from `PromptBuilder`). `enter()` reads `call.service_category` and passes it to `prompt_builder.build()` so DIAGNOSE and RESOLVE receive category-specific instructions and tool sets.
- Phase advancement is model-driven via the `phase_complete` tool; a turn-count fallback auto-advances after the phase limit: TRIAGE caps at **2 turns**, DIAGNOSE at **4 turns**, all others at `MAX_TURNS_PER_PHASE` (default 8).
- `_phase_tools_called: set[str]` is reset on every `enter()` call; `record_tool_called(name)` / `is_tool_already_called(name)` expose it to `ToolExecutor` for once-per-phase enforcement (e.g. `get_service_status`).
- `phase_complete` from `WRAP_UP` cleanly ends the session.
- Escalation to a human agent triggers a SIP REFER when `frustration_count >= ESCALATION_FRUSTRATION_LIMIT` (default 3) or `tool_failure_count >= ESCALATION_TOOL_FAILURE_LIMIT` (default 2).

**TRIAGE service category classification:** TRIAGE now requires the model to classify the caller into one of 6 service categories via the `service_category` parameter of `phase_complete`:

| Category | Triggers |
|---|---|
| `technical_support` | Internet/TV/phone outage, connectivity issues, tickets |
| `billing` | Balance inquiry, payment, charge dispute, autopay setup |
| `sales` | Plan upgrade, new service, promotions, pricing |
| `move_transfer` | Moving home, transferring service, cancellation |
| `appointment` | Confirm, cancel, or reschedule technician visits |
| `account` | Contact info update, account access, general account questions |

The category is validated against `SERVICE_CATEGORIES` in `core/models.py` and written to `call.service_category` before `fsm.advance()` is called. Calling `phase_complete` without `service_category` is still valid — the category stays `None` and defaults to `technical_support` behavior. The category persists to the CDR via the `service_category` column in `call_detail_records`.

### Tools available to the model

| Tool | Available from | Category | Purpose |
|---|---|---|---|
| `phase_complete` | All phases | — | Advance to the next phase (or end session from WRAP_UP); accepts optional `service_category` in TRIAGE |
| `escalate_to_agent` | All phases | — | Trigger SIP REFER to human agent |
| `lookup_customer` | VERIFY onward | — | Look up account by verified phone, email, or account ID |
| `get_service_status` | DIAGNOSE onward | technical_support | Fetch services, open incidents, and open support tickets |
| `get_ticket` | DIAGNOSE onward | technical_support | Look up any ticket by ID (including resolved/closed) |
| `get_account_history` | DIAGNOSE onward | technical_support | Return resolved tickets + incidents for repeat-caller context |
| `create_ticket` | DIAGNOSE onward | technical_support | Open a support ticket |
| `update_ticket` | RESOLVE onward | technical_support | Change an existing ticket's status or priority |
| `get_account_balance` | DIAGNOSE onward | billing | Mock-data-backed — returns balance, minimum payment due, due date, last payment (`billing_accounts`/`payments` tables) |
| `get_payment_history` | DIAGNOSE onward | billing | Mock-data-backed — recent payments for the account (`payments` table) |
| `make_payment` | RESOLVE onward | billing | v1 stub — returns feature_pending, escalates to agent |
| `setup_autopay` | RESOLVE onward | billing | v1 stub — returns feature_pending, escalates to agent |
| `get_product_catalog` | DIAGNOSE onward | sales | Mock-data-backed — global plan catalog filtered by account_type when account_id is given (`products` table) |
| `get_promotions` | DIAGNOSE onward | sales | Mock-data-backed — promotions filtered by account_type (`promotions` table) |
| `initiate_upgrade` | RESOLVE onward | sales | v1 stub — returns feature_pending, escalates to agent |
| `get_service_eligibility` | DIAGNOSE onward | move_transfer | Mock-data-backed — zip-prefix lookup against `service_areas`; falls back to "eligible, standard plans" when the address doesn't match a seeded prefix |
| `initiate_service_move` | RESOLVE onward | move_transfer | v1 stub — returns feature_pending, escalates to agent |
| `cancel_service` | RESOLVE onward | move_transfer | v1 stub — returns feature_pending, escalates to agent |
| `get_appointments` | DIAGNOSE onward | appointment | Mock-data-backed — non-cancelled appointments for the account (`appointments` table) |
| `confirm_appointment` | RESOLVE onward | appointment | v1 stub — returns feature_pending, escalates to agent |
| `cancel_appointment` | RESOLVE onward | appointment | v1 stub — returns feature_pending, escalates to agent |
| `reschedule_appointment` | RESOLVE onward | appointment | v1 stub — returns feature_pending, escalates to agent |
| `get_account_details` | DIAGNOSE onward | account | Mock-data-backed — extends `Customer` with `mailing_address`/`preferred_contact_method` |
| `update_contact_info` | RESOLVE onward | account | v1 stub — returns feature_pending, escalates to agent |

Tool calls are handled by `ToolExecutor` in this exact sequence:
1. **Acquire `session_manager.tool_lock`** — per-session `asyncio.Lock` that serialises concurrent handlers. When the model batches multiple function calls in a single response, all fire as concurrent asyncio tasks; without this lock they race on `_response_ready` and both send `response.create`, causing `conversation_already_has_active_response`.
2. **Wait for the triggering response to finish** — `response.function_call_arguments.done` fires before the triggering response sends `response.done`. `wait_for_response_done()` blocks here first to avoid a protocol conflict.
3. **Inject audio preamble** — sends a text item (e.g. "I'm looking up your account now.") and `response.create` so the caller hears something while the DB query runs.
4. **Execute the tool** — dispatched with `TOOL_TIMEOUT_SECONDS` (default 5s) timeout and one transient-error retry.
5. **Wait for the preamble response to finish** — second `wait_for_response_done()` call ensures the preamble audio completes before sending the result.
6. **Return result** — sends `function_call_output` item and a **phase/tool-specific `response.create`** override (see below) so the model receives targeted guidance.

Both `wait_for_response_done()` calls are required. Skipping either causes OpenAI to reject the next `response.create` with `conversation_already_has_active_response`.

**Post-tool `response.create` overrides** (`tool_executor._handle_with_lock`):

| Trigger | Override sent | Why |
|---|---|---|
| `phase_complete` → TRIAGE entry | `instructions`: "Call phase_complete now. Set service_category…" | Forces immediate silent routing |
| `phase_complete` → DIAGNOSE entry | `tool_choice: "required"` only, **no `instructions`** | Per-response `instructions` REPLACES session-level instructions, stripping the `CONFIRMED ACCOUNT` block. Session context inherited intact. |
| `phase_complete` → RESOLVE entry | `instructions`: action-focused, no-repeat | Prevents model repeating DIAGNOSE content; does **not** set `tool_choice: required` so model can speak before calling `create_ticket` |
| `phase_complete` → WRAP_UP entry | `instructions`: "ask Is there anything else? and WAIT" | Prevents proactive tool calls at wrap-up entry |
| `get_service_status` in DIAGNOSE | `instructions`: script each field + "Do NOT ask any question" + `tool_choice: required` | Prevents "Does that sound okay?" stall before `phase_complete` |
| `get_service_status` in WRAP_UP | `instructions`: report tickets then re-ask "anything else?" | Guides model after caller asks about open tickets in wrap-up |
| `create_ticket` (any phase) | `instructions`: read ticket_id back + call `phase_complete` + `tool_choice: required` | Prevents stuck "still working on it" loop |
| `update_ticket` (any phase) | `instructions`: read new status back + call `phase_complete` + `tool_choice: required` | Same pattern as `create_ticket` |

### Session Manager (`sip_bridge/session_manager.py`)

**Caller audio transcription:** `PromptBuilder.build()` includes `"input_audio_transcription": {"model": "whisper-1"}` in every `session.update`. This causes OpenAI to emit `conversation.item.input_audio_transcription.completed` events for each caller utterance. `SessionManager` handles this event and saves/publishes the caller turn identically to agent turns. Without this field, caller speech is never transcribed.

Key internal state per call:
- **`_greeting_triggered`** — SIP calls never fire `session.created` (the session pre-exists). The greeting `response.create` is fired on the first `session.updated` event instead, guarded by this flag to ensure it fires exactly once.
- **`_response_ready` (asyncio.Event)** — Set when idle, cleared when a response is in progress. Cleared synchronously in `send_event()` before any `response.create` is sent (not after receiving the server-side `response.created`) to close the race window where a fast tool lookup would slip through before the event arrived.
- **`_tool_lock` (asyncio.Lock)** — Serialises concurrent tool call handlers. Exposed as the `tool_lock` property. Acquired by `ToolExecutor` before any `wait_for_response_done()` call so that batched function calls never race on `_response_ready`.
- **`wait_for_response_done(timeout)`** — Public method awaited by `ToolExecutor`. Waits for `_response_ready` to be set (i.e. `response.done` received). Resets the event on timeout to prevent deadlocks.

**`_teardown` call lifecycle:** Called at the end of `connect()` regardless of how the session exits. Three paths:
- **TRANSFERRING** — Waits `transfer_hangup_delay_seconds` then sends BYE. After the hangup, re-fetches the call; if it is still `TRANSFERRING`, transitions to `ENDED`, sets `ended_at = now()`, computes `duration_seconds` from `answered_at`, sets `hangup_cause = "transferred"`, publishes `CALL_ENDED`, and saves the CDR. This ensures transferred calls have a duration and appear correctly in the dashboard.
- **ENDED / FAILED** — CDR saved immediately; no state mutation.
- **Any other state** (e.g. RINGING if the call was never answered) — Transitions to `ENDED`, sets `ended_at`, computes duration if `answered_at` is set, sets `hangup_cause = "normal"`, publishes `CALL_ENDED`, saves CDR.

### Agent Behaviour Rules (`sip_bridge/prompt_builder.py`)

The following rules are injected into every `session.update` via `_base_instructions()`:

| Rule | Enforcement |
|---|---|
| Always look up before answering | VERIFY phase mandates `lookup_customer` call before any account info |
| Phone lookup = verified caller ID only | Tool description + `ToolExecutor` code-level check; fabricated numbers rejected before DB is queried |
| No verified phone → ask email or account ID | VERIFY branches: no `caller_number` → ask for email or ACC-XXXNNN |
| Confirmed account_id carried into every phase | When `account_id` is known, each phase instruction starts with `CONFIRMED ACCOUNT: ...`. The model cannot forget or invent a different ID. |
| No re-verification when account is known | TRIAGE explicitly tells the model not to ask for re-verification when account is already confirmed |
| `lookup_customer` not PROACTIVE when account known | `_tools_for_phase(known_caller=True)` switches the tool behaviour tag to `ON DEMAND ONLY`; model will not auto-call with invented identifiers |
| Incidents ≠ tickets | "Incidents vs Support Tickets" section with scripted responses per field; DIAGNOSE phase scripts each separately; explicit prohibition on using the wrong term |
| No service answers in GREETING | GREETING instructions explicitly state no service tools are available; model told to say "Let me check on that for you" and call `phase_complete` immediately if caller asks about service — max 1–2 exchanges |
| No service tools in TRIAGE | `get_service_status` and `create_ticket` removed from TRIAGE's tool list entirely; TRIAGE instructions also explicitly say "DO NOT call get_service_status in this phase" and "DO NOT report whether incidents or tickets exist"; model must classify and advance |
| TRIAGE acknowledges before advancing | If caller already described their issue in GREETING, TRIAGE says "Got it, let me look into that for you." and calls `phase_complete` immediately — does NOT ask "Are you calling about a technical issue?" (jargon that ignores what the caller just said) |
| DIAGNOSE bridge phrase before advancing | After reporting all `get_service_status` results, model says a transitional phrase ("Let me see what I can do about this.") before calling `phase_complete` — gives the caller a natural pause |
| RESOLVE does not repeat DIAGNOSE | RESOLVE instructions explicitly say "DO NOT repeat" what was reported in DIAGNOSE; focuses only on what happens next (ETA, ticket creation, escalation) |
| WRAP_UP does not claim issue is fixed | WRAP_UP says "diagnostic and resolution steps are complete" and warns the model not to say the problem is fixed if a service incident is still active |
| Auto-escalation announces the transfer | Before firing the SIP REFER on threshold-based escalation, the FSM injects a `conversation.item.create` text message: "Let me connect you with one of our agents who can better assist you — please hold." and waits for `response.done` so the caller hears it before the call transfers |
| `create_ticket` ONE-round confirmation protocol | Tool description instructs model to confirm the issue summary ONCE before calling ("ONE round only"). Step 1: ask once. Step 2: when caller says yes, call immediately. CRITICAL: do NOT ask for confirmation a second time. After the tool succeeds, a post-tool `response.create` override in `tool_executor` forces the model to read back the `ticket_id` and call `phase_complete`. |
| `update_ticket` requires confirmation + readback | Tool description requires CONFIRMATION FIRST (confirm the change before calling) and readback after: "Done, your ticket is now [status]" |
| `get_ticket` on demand for named tickets | Available from DIAGNOSE onward; ON DEMAND — triggered only when caller quotes a specific ticket ID; returns any status including resolved |
| `get_account_history` on demand for recurring issues | Available from DIAGNOSE onward; ON DEMAND — triggered only when caller mentions a past or recurring issue |
| Only answer what tools return | "CRITICAL — Never Fabricate" section in base instructions |
| No result → say unable to find | Same section |
| No billing data — redirect to agent (non-billing calls) | "CRITICAL — No Billing Access" block in `_base_instructions()` replaced with a "Billing & Payments" block when `service_category == "billing"`; for all other categories the redirect phrase is retained |
| Out-of-scope deflection | Exact phrase embedded in "Scope" section |
| No persona adoption | Explicit prohibition in role definition |
| Short and conversational | "This is a phone call. Under 2 sentences." |
| No symbols or markdown | Explicit list of prohibited characters/formatting |

`lookup_customer` supports three `identifier_type` values: `phone` (verified caller ID only), `email`, and `account_id` (format `ACC-XXXNNN`). Phone lookups try multiple E.164 normalizations (`+1NNNN`, `NNNN`, stripped leading 1) so format mismatches still resolve.

**Tool synonym normalisation (code-level):** `_dispatch()` in `tool_executor.py` applies `_TOOL_SYNONYMS` before routing. This catches hallucinated tool names the model occasionally invents:

| Hallucinated name | Normalised to |
|---|---|
| `get_support_tickets` | `get_service_status` |
| `get_tickets` | `get_service_status` |
| `get_open_tickets` | `get_service_status` |
| `check_service` | `get_service_status` |
| `get_account_status` | `get_service_status` |
| `lookup_ticket` | `get_ticket` |
| `get_ticket_details` | `get_ticket` |
| `check_ticket` | `get_ticket` |
| `close_ticket` | `update_ticket` |
| `resolve_ticket` | `update_ticket` |

Similarly, `_resolve_lookup_args()` normalises parameter key synonyms for `lookup_customer`: `customer_identifier`, `customer_id`, `account_id`, `phone_number`, `email` are all mapped to `identifier`.

**Phone lookup enforcement (code-level):** Before dispatching `lookup_customer` with `identifier_type='phone'`, `ToolExecutor._dispatch()` fetches the call's `caller_number` from the state store and validates the identifier against `_normalize_phone_candidates(caller_number)`. If there is no verified number or the identifier does not match, the tool returns an error dict immediately — the DB is never queried — and a `WARNING` is logged. The error message instructs the model to ask for email or account ID instead.

`_tools_for_phase(phase, known_caller=False, service_category=None)` — the `known_caller` parameter changes `lookup_customer`'s behaviour tag: `PROACTIVE` when the account is not yet confirmed (VERIFY path), `ON DEMAND ONLY` when it is (post-VERIFY with known caller). The `service_category` parameter controls which category-specific tools are included in DIAGNOSE and RESOLVE: only the tools for the matched category are exposed; all others are withheld. `_PHASE_TOOL_ALLOWLIST` is the security backstop and includes all 16 new stub tools as a superset across DIAGNOSE, RESOLVE, and WRAP_UP. This prevents the model from auto-calling the tool in TRIAGE/DIAGNOSE/RESOLVE and inventing an identifier it does not have.

When `account_id` is known at the start of a phase, `PromptBuilder.build()` prepends `CONFIRMED ACCOUNT: The caller's account_id is <id>. Use ONLY this account_id for all tool calls. DO NOT use any other account_id.` to the TRIAGE, DIAGNOSE, RESOLVE, and WRAP_UP instructions. This prevents the model from inventing a different account_id mid-conversation (e.g. after a tool failure) or asking the caller to re-verify.

TRIAGE phase does **not** have `get_service_status`, `create_ticket`, `get_ticket`, `get_account_history`, or `update_ticket`. All data-access and action tools are available from DIAGNOSE onward only. Removing them from TRIAGE proved more reliable than prompt-only instructions — the model would call them anyway when they were present. TRIAGE's sole job is to classify the issue type into a `service_category` and call `phase_complete`.

**`_diagnose_instructions(service_category, account_context) -> str`** — returns category-specific DIAGNOSE phase instructions (e.g. scripted billing tool usage, appointment listing). Called by `build()` when constructing the DIAGNOSE session update.

**`_resolve_instructions(service_category, account_context) -> str`** — returns category-specific RESOLVE phase instructions (e.g. payment confirmation, appointment rescheduling). Called by `build()` when constructing the RESOLVE session update.

**`build(phase, ..., service_category=None)`** — new `service_category` parameter threaded through to `_tools_for_phase()`, `_diagnose_instructions()`, `_resolve_instructions()`, and `_base_instructions()`.

**`_stub_tool(tool_name)` helper** in `tool_executor.py` — returns `{"status": "feature_pending", "message": "...connects you to an agent."}` for all 16 new category tools. All stub tools also have preamble phrases registered in `_PREAMBLES` so the caller hears a holding message while the stub executes.

**`get_ticket` response shape:** Full ticket fields including `ticket_id`, `issue_summary`, `priority`, `status`, `created_at`, `updated_at`, `resolved_at`. Returns tickets of any status (open, in_progress, resolved, closed) — unlike `get_service_status` which only returns open tickets.

**`update_ticket` behaviour:** Requires `ticket_id`; `status` and `priority` are optional (supply only what is changing). When `status` is set to `resolved` or `closed`, `resolved_at` is stamped automatically. Returns `{ ticket_id, status, priority, updated: true }` on success or `{ status: "error", message: "..." }` on failure. Tool description requires CONFIRMATION FIRST — model must confirm the change with the caller before calling and read back the new status afterward.

**`get_account_history` response shape:** `{ account_id, resolved_tickets[], resolved_incidents[] }`. Each resolved ticket includes `ticket_id`, `issue_summary`, `priority`, `status`, `created_at`, `resolved_at`. Each resolved incident includes `incident_id`, `title`, `severity`, `resolved_at`. Returns up to 10 most recent of each. Tool is ON DEMAND — triggered only when the caller references a past or recurring issue.

**`get_service_status` response shape:** `{ account_id, services[], open_incidents[], open_support_tickets[] }`. `open_incidents` are network/area outages from the `service_incidents` table. `open_support_tickets` are customer-specific tickets from the `support_tickets` table with status != resolved. Returning both in one call ensures the model has the complete picture before speaking.

**Incident vs ticket scripted responses** (in `_base_instructions()` and reinforced in DIAGNOSE):

| Field | Required spoken response |
|---|---|
| `open_incidents` present | "There is a known service incident in your area: [title]. Our team is working on it." |
| `open_support_tickets` present | "You have an open support ticket: [summary]." |
| `open_support_tickets` empty | "You have no open support tickets." |
| Both present | Report incident first, then ticket — as separate items |

The model is explicitly prohibited from calling an incident a "ticket" or a ticket an "incident". The DIAGNOSE phase instructions bullet-script each field (`open_incidents →`, `open_support_tickets →`) so the model has no ambiguity when reading the tool result.

There is no billing tool. Base instructions contain a dedicated "CRITICAL — No Billing Access" section that hardcodes the redirect response: *"I don't have access to billing details, but I can connect you with an agent who can help — would you like me to transfer you?"* TRIAGE, DIAGNOSE, and RESOLVE phases each repeat this intercept for billing mentions so the model cannot fabricate charges or balances at any stage.

### Resilience mechanisms

- **WebSocket reconnection** — up to 3 attempts with `[0.5, 1.0, 2.0]s` backoff; `ws_ping_interval`/`ws_ping_timeout` heartbeat
- **Circuit breaker** — rejects new calls (503) when N reconnect failures occur in a sliding window
- **Maintenance mode** — operator-toggled flag that rejects all new calls with 503
- **Budget hard stop** — rejects new calls when daily cost exceeds `DAILY_BUDGET_USD` (0 = no limit)

### Database

SQLite by default (`openaisip.db`); configurable to PostgreSQL via `DATABASE_URL` in `.env`. SQLAlchemy async ORM. Tables:

| Table | Purpose |
|---|---|
| `customers` | Account records; includes `mailing_address` (nullable) and `preferred_contact_method` |
| `services` | Per-customer service subscriptions |
| `service_incidents` | Open/historical network outages — returned as `open_incidents` by `get_service_status` |
| `support_tickets` | Customer-specific tickets created via `create_ticket` — also returned as `open_support_tickets` by `get_service_status` |
| `billing_accounts` | Current balance/minimum payment/due date per account — read by `get_account_balance` |
| `payments` | Historical payments (masked `method` string, never a real card number) — read by `get_payment_history` and `get_account_balance`'s last-payment field |
| `products` | Global service plan catalog, not customer-specific — read by `get_product_catalog` |
| `promotions` | Promotional offers filtered by `account_type` — read by `get_promotions` |
| `service_areas` | Mock service-availability lookup keyed by a 3-digit zip/postal prefix — read by `get_service_eligibility` |
| `appointments` | Scheduled technician visits — read by `get_appointments` |
| `call_transcripts` | Per-turn transcript (PCI-scrubbed, retained `TRANSCRIPT_RETENTION_DAYS`) |
| `call_events` | Append-only event timeline per call (phase changes, tool calls, WS events) |
| `call_detail_records` | CDR snapshot written at call end (billing fields, token counts, cost); includes nullable `service_category TEXT` column |
| `escalation_contexts` | Warm handoff packet written when `escalate_to_agent` fires; read by agent desktop |

Seed data (15 customers, plus billing/product/promotion/service-area/appointment mock data) loads on startup. Anyone with an already-seeded local `openaisip.db` from before these tables existed needs `python -m db.seed --reset` to pick them up — the seed idempotency check only looks at whether `ACC-JT001` exists.

**Datetime serialization note:** SQLite stores datetimes without timezone info. `_cdr_to_dict()` in `repository.py` uses `_dt_utc()` to append `Z` to all naive datetime strings before returning them via the API or loading them into `StateStore`. This ensures JavaScript always interprets them as UTC regardless of browser timezone, preventing incorrect duration calculations for operators in non-UTC timezones.

**Eastern time display:** All date/time rendering in `calls-panel.js` and `transcript-panel.js` uses `timeZone: 'America/New_York'` with `en-US` locale so times appear in ET regardless of the operator's browser timezone. The calls table appends `ET` to each date/time label. Turn and event timestamps in the transcript panel use `hour12: false` 24-hour format for compactness.

### Configuration

All settings in `config/settings.py` load from `.env`. Copy `.env.example` to start. Key settings:

```
# Core
OPENAI_API_KEY, OPENAI_PROJECT_ID, OPENAI_MODEL, OPENAI_VOICE
WEBHOOK_SECRET              — HMAC-SHA256 Svix signature validation
DASHBOARD_API_KEY           — bearer token for dashboard REST and WebSocket auth

# Conversation
HUMAN_AGENT_SIP_URI         — SIP URI for escalation transfers
ESCALATION_FRUSTRATION_LIMIT   (default 3)
ESCALATION_TOOL_FAILURE_LIMIT  (default 2)
MAX_TURNS_PER_PHASE            (default 8)
FRUSTRATION_KEYWORDS           — comma-separated trigger phrases

# Resilience
WS_PING_INTERVAL / WS_PING_TIMEOUT
CIRCUIT_BREAKER_FAILURE_THRESHOLD / _WINDOW_SECONDS / _COOLDOWN_SECONDS
TOOL_TIMEOUT_SECONDS           (default 5.0)

# Cost / budget
COST_INPUT_AUDIO_PER_1K / COST_OUTPUT_AUDIO_PER_1K
COST_INPUT_TEXT_PER_1K / COST_OUTPUT_TEXT_PER_1K / COST_INPUT_CACHED_PER_1K
DAILY_BUDGET_USD               (default 0 = no limit)

# Warm handoff
HANDOFF_CONTEXT_URL            — optional POST destination for agent desktop integration

# Data retention
TRANSCRIPT_RETENTION_DAYS      (default 90)

# Database
DATABASE_URL                   (default sqlite+aiosqlite:///./openaisip.db)

# Startup history
CDR_HISTORY_LIMIT              (default 20) — CDRs pre-loaded into StateStore on startup so ended calls appear in the dashboard immediately after a restart
```

### Operator Dashboard REST API

| Endpoint | Purpose |
|---|---|
| `GET /api/calls` | List all calls |
| `GET /api/calls/{id}/transcript` | Full transcript for a call |
| `GET /api/tokens/summary` | Global token + cost totals |
| `GET /api/health` | Channel health metrics |
| `GET /api/logs` | Recent log entries |
| `GET/POST /api/operator/maintenance` | Toggle maintenance mode |
| `GET /api/operator/cdrs` | Recent CDRs |
| `GET /api/operator/cdrs/{id}` | Single CDR |
| `GET /api/operator/cost/today` | Daily spend vs budget |
| `GET /api/operator/events/{id}` | Per-call event timeline |
| `GET /api/operator/handoff/{id}` | Escalation context for agent desktop |

### Event Bus Topics

| Topic | Publisher | Subscriber |
|---|---|---|
| `CALL_CREATED` | `webhook_handler` | `ws_manager` |
| `CALL_UPDATED` | `session_manager` (raw OpenAI events), `conversation_fsm` (phase/escalation) | `ws_manager` |
| `CALL_ENDED` | `session_manager` | `ws_manager` |
| `TOKEN_USAGE` | `session_manager` | `ws_manager` |
| `TRANSCRIPT_TURN` | `session_manager._save_transcript_turn()` | `ws_manager` |
| `CALL_EVENT` | `db.repository.emit_call_event()` | `ws_manager` |
| `HEALTH_UPDATE` | `health_collector` | `ws_manager` |
| `LOG_ENTRY` | `core.logger` | `ws_manager` |
| `BUDGET_ALERT` | `state_store.record_token_usage()` | `ws_manager` |

**`CALL_EVENT` publish flow:** `conversation_fsm.enter()`, `tool_executor._handle_with_lock()`, and `session_manager.connect()` all call `db.repository.emit_call_event()`. That function schedules two asyncio tasks: a DB write (`save_call_event`) and a bus publish (`bus.publish(Topic.CALL_EVENT, payload)`). The payload carries `call_id`, `event_type`, `data`, and `timestamp`.

**`TRANSCRIPT_TURN` PCI note:** `session_manager._save_transcript_turn()` calls `repository.save_transcript_turn()`, which applies `_scrub_pii()` before the DB insert and **returns the scrubbed text**. The bus publish uses the returned scrubbed text — not the raw text from OpenAI — so card numbers are redacted in both DB and WebSocket. The `TRANSCRIPT_TURN` payload includes a `timestamp` field (`datetime.now(timezone.utc).isoformat()`) so live turns received via WebSocket show their time in the transcript panel, matching turns loaded from the DB snapshot.

### Operator Dashboard WebSocket

`dashboard/ws_manager.py` subscribes to all topics in `ALL_TOPICS` (including `CALL_EVENT`) and fans every message out to connected browser clients unchanged.

**Snapshot-before-registration ordering** (`ws_manager.connect()`): The snapshot is built and sent to the client BEFORE the client is added to `_connections`. If registration happened first, the broadcast loop would start delivering live events (`CALL_EVENT`, `TRANSCRIPT_TURN`) before the snapshot arrived; the JS SNAPSHOT handler then replaces state, erasing those early events. Snapshot-first guarantees the client receives a clean baseline before any live events flow.

**Snapshot on connect** (`ws_manager.connect()` → `store.snapshot()`):
- Includes `active_call_transcripts: {call_id → turns}` and `active_call_events: {call_id → events}` for all calls (active + recent historical). Active calls receive all transcript turns (`limit=None`); ended calls are capped at **50 turns** to keep the snapshot payload fast and small.
- All transcript/event DB queries run in **parallel** (`asyncio.gather`) — 20 CDR calls issue 40 queries concurrently rather than sequentially, keeping snapshot build time under ~100 ms instead of several seconds.
- `active_calls` contains the 50 most-recent calls in `_calls`, sorted newest-first — includes both live calls and CDR-loaded historical calls.
- Existing fields unchanged: `global_tokens`, `recent_logs`, `channel_health`.

**Live Transcript Panel** (`dashboard/static/js/transcript-panel.js`):
- `TranscriptPanel` class follows the same `handleMessage(msg)` pattern as `CallsPanel`, `TokensPanel`, etc.
- On `SNAPSHOT`: **merges** snapshot turns into the existing `_transcripts` map (keyed by `turn_index`) rather than replacing it. `TRANSCRIPT_TURN` events may arrive before the snapshot — those turns are in the JS map but not yet in the DB when the snapshot queries, so a replace would erase them. Merge preserves early live turns while filling in turns from before the browser connected. **Events are also merged**: snapshot events have a DB `id`; live `CALL_EVENT` bus events don't. The merge preserves live-only events (no `id`) alongside the snapshot events so no events are lost. Stores call metadata in `_callMeta`. `_syncSelector` rebuilds the call dropdown from `snapshot.active_calls` **and re-adds any calls that arrived via `CALL_CREATED` before the snapshot landed** (race guard). **Auto-selects** the first ACTIVE/RINGING/TRANSFERRING call if no call is currently selected (operator sees transcript immediately on dashboard open).
- On `CALL_CREATED`: initialises empty maps for the new call; stores call metadata; **auto-selects the new call** in the dropdown (operator sees live transcript immediately without manual selection); option is **prepended** so new calls always appear at the top, above historical ended calls in the CDR history list.
- On `TRANSCRIPT_TURN`: appends to the call's turn map (keyed by `turn_index` to deduplicate on reconnect).
- On `CALL_EVENT`: appends to the call's event list.
- Renders on a 150 ms debounce timer. Auto-scrolls conversation pane unless operator has scrolled up.
- **Call selector dropdown** shows `caller_number [state] — Mon DD HH:MM` using the stored `_callMeta` to ensure `created_at` is always available even when partial `CALL_UPDATED` events arrive.

**Active Calls table** (`dashboard/static/js/calls-panel.js`):
- Columns: Call ID, **Date / Time** (created_at), From, State, Phase, Duration.
- `CALL_UPDATED` payloads are **merged** into the existing call object (`{ ...existing, ...payload }`) rather than replacing it, so partial updates (e.g. the raw OpenAI event forwarded from `session_manager`) never wipe `created_at` or other stable fields.
- `_formatDateTime`: renders `created_at` in `America/New_York` timezone with `en-US` locale, appending `ET` so the operator always sees Eastern time regardless of their browser locale.
- `_formatDuration`: if `duration_seconds` is null (call still active or CDR not fully written), computes from `answered_at` to `ended_at` (or `Date.now()` for live calls). Naive UTC datetime strings from SQLite are suffixed with `Z` before parsing so JS treats them as UTC regardless of browser timezone. Returns `'—'` if the result is negative (guard for incomplete CDR data). Transferred calls now always have `duration_seconds` set because `_teardown` computes it after the BYE.

### Testing

- **Unit tests** — `tests/test_conversation_fsm.py` (includes auto-escalation spoken-message test and teardown transferred-call duration/CDR test), `test_prompt_builder.py` (tool presence per phase, behaviour tags, billing/incident/ticket guardrails, phase rules, tool synonym map, TRIAGE acknowledgment, DIAGNOSE bridge phrase, RESOLVE no-repeat, WRAP_UP incident caveat, new tool phase availability and behavior tags; **+4 new tests** covering: `create_ticket` ONE-round-only confirmation protocol, double-confirmation prohibition, WRAP_UP tool guidance for `get_service_status` ticket listing, WRAP_UP tool guidance for `get_ticket` named lookup), `test_token_tracker.py`, `test_webhook_handler.py`, `test_ws_manager.py` (snapshot parallel fetch, limit enforcement, transcript merge tests; **+1 new test** covering: snapshot-before-registration ordering — client not in `_connections` while snapshot is sent), `test_repository_events.py` (get_ticket, update_ticket, get_account_history, transcript/event helpers)
- **Scenario tests** — `tests/test_scenarios.py` (16 end-to-end FSM flow scenarios using `ScenarioHarness`: 12 original + 4 new tests covering: DIAGNOSE entry `response.create` has `tool_choice` only and no `instructions` override; DIAGNOSE `get_service_status` result `response.create` prohibits follow-up questions; `create_ticket` result `response.create` forces ticket-ID readback and `phase_complete`; RESOLVE entry `response.create` does NOT have `tool_choice=required` so model can speak before calling `create_ticket`)
- **Harness** — `tests/scenario_harness.py` — mocks all external I/O; captures `session.update` configs and sent events

CI runs on push/PR via `.github/workflows/ci.yml` (Python 3.12.3, pytest, 70% coverage gate). **216 tests** total.
