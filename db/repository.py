"""Async query functions used by the tool executor."""
from __future__ import annotations

import asyncio
import json
import re
import random
import string
from datetime import datetime, timezone

from sqlalchemy import select

from db.engine import AsyncSessionLocal
from db.models import CallDetailRecord, CallEvent, CallTranscript, Customer, EscalationContext, ServiceIncident, SupportTicket

# ── Call event type constants ──────────────────────────────────────────────────
EVENT_CALL_CREATED = "call_created"
EVENT_CALL_ANSWERED = "call_answered"
EVENT_CALL_ENDED = "call_ended"
EVENT_CALL_REJECTED = "call_rejected"
EVENT_PHASE_ENTERED = "phase_entered"
EVENT_TOOL_CALLED = "tool_called"
EVENT_TOOL_FAILED = "tool_failed"
EVENT_ESCALATED = "escalated"
EVENT_WS_RECONNECTED = "ws_reconnected"
EVENT_WS_FAILED = "ws_failed"

# Pre-compiled regex for basic PCI scrubbing (16-digit card numbers with optional separators).
# NOTE: This is best-effort only. A production deployment needs a certified PII service.
_CARD_RE = re.compile(r'\b\d{4}[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{4}\b')


def _scrub_pii(text: str) -> str:
    return _CARD_RE.sub('[REDACTED]', text)


# ── Call event timeline ───────────────────────────────────────────────────────

async def save_call_event(call_id: str, event_type: str, data: dict | None = None) -> None:
    """Append one event to the call timeline. Fire-and-forget safe."""
    async with AsyncSessionLocal() as session:
        event = CallEvent(
            call_id=call_id,
            event_type=event_type,
            data=json.dumps(data or {}),
        )
        session.add(event)
        await session.commit()


def emit_call_event(call_id: str, event_type: str, data: dict | None = None) -> None:
    """Schedule a non-blocking call event write. Safe to call from sync or async context."""
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(save_call_event(call_id, event_type, data))
        # Publish to event bus for real-time dashboard delivery
        from core.event_bus import bus
        from core.models import Topic
        payload = {
            "call_id": call_id,
            "event_type": event_type,
            "data": data or {},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        loop.create_task(bus.publish(Topic.CALL_EVENT, payload))
    except RuntimeError:
        pass  # No running event loop — skip (e.g. during testing outside async context)


async def get_call_events(call_id: str, limit: int | None = None) -> list[dict]:
    """Return events for a call in chronological order. Pass limit for the most recent N."""
    async with AsyncSessionLocal() as session:
        if limit is not None:
            stmt = (
                select(CallEvent)
                .where(CallEvent.call_id == call_id)
                .order_by(CallEvent.created_at.desc(), CallEvent.id.desc())
                .limit(limit)
            )
        else:
            stmt = (
                select(CallEvent)
                .where(CallEvent.call_id == call_id)
                .order_by(CallEvent.created_at, CallEvent.id)
            )
        result = await session.execute(stmt)
        rows = result.scalars().all()
        if limit is not None:
            rows = list(reversed(rows))  # restore chronological order
        return [
            {
                "id": e.id,
                "event_type": e.event_type,
                "data": json.loads(e.data),
                "timestamp": e.created_at.isoformat() if e.created_at else None,
            }
            for e in rows
        ]


def _normalize_phone_candidates(raw: str) -> list[str]:
    """Return the E.164 variants to try for a phone lookup."""
    digits = re.sub(r"\D", "", raw)
    candidates = [raw]  # always try as-is first
    # Try with +1 prefix if it looks like a 10-digit North-American number
    if len(digits) == 10:
        candidates.append(f"+1{digits}")
        candidates.append(digits)
    # Try stripping leading 1 if 11 digits starting with 1
    elif len(digits) == 11 and digits.startswith("1"):
        candidates.append(f"+{digits}")
        candidates.append(digits[1:])
    return list(dict.fromkeys(candidates))  # deduplicate preserving order


async def find_customer(identifier: str, identifier_type: str) -> dict | None:
    """Look up a customer by phone number or account ID.

    Returns a dict with account details, or None if not found.
    Phone lookup tries multiple E.164 normalizations so +1/no-+1 mismatches still resolve.
    """
    async with AsyncSessionLocal() as session:
        if identifier_type == "phone":
            candidates = _normalize_phone_candidates(identifier)
            from sqlalchemy import or_
            stmt = select(Customer).where(
                or_(*[Customer.phone_number == c for c in candidates])
            )
        elif identifier_type == "email":
            stmt = select(Customer).where(Customer.email == identifier)
        else:
            stmt = select(Customer).where(Customer.account_id == identifier)

        result = await session.execute(stmt)
        customer = result.scalar_one_or_none()

        if customer is None:
            return None

        return {
            "account_id": customer.account_id,
            "full_name": customer.full_name,
            "phone_number": customer.phone_number,
            "email": customer.email or "",
            "account_type": customer.account_type,
            "account_status": customer.account_status,
        }


async def get_service_status(account_id: str) -> dict:
    """Return all services, open incidents, and open support tickets for an account."""
    async with AsyncSessionLocal() as session:
        # Verify account exists
        customer = await session.get(Customer, account_id)
        if customer is None:
            return {"error": f"Account {account_id} not found"}

        services = [
            {
                "service_type": svc.service_type,
                "plan_name": svc.plan_name,
                "status": svc.status,
            }
            for svc in customer.services
        ]

        incident_stmt = (
            select(ServiceIncident)
            .where(
                ServiceIncident.account_id == account_id,
                ServiceIncident.status != "resolved",
            )
            .order_by(ServiceIncident.created_at.desc())
        )
        incident_result = await session.execute(incident_stmt)
        open_incidents = incident_result.scalars().all()

        incidents = [
            {
                "incident_id": inc.incident_id,
                "title": inc.title,
                "description": inc.description,
                "severity": inc.severity,
                "status": inc.status,
                "opened_at": inc.created_at.isoformat(),
            }
            for inc in open_incidents
        ]

        ticket_stmt = (
            select(SupportTicket)
            .where(
                SupportTicket.account_id == account_id,
                SupportTicket.status != "resolved",
            )
            .order_by(SupportTicket.created_at.desc())
        )
        ticket_result = await session.execute(ticket_stmt)
        open_tickets = ticket_result.scalars().all()

        tickets = [
            {
                "ticket_id": tkt.ticket_id,
                "issue_summary": tkt.issue_summary,
                "priority": tkt.priority,
                "status": tkt.status,
                "created_at": tkt.created_at.isoformat(),
            }
            for tkt in open_tickets
        ]

        return {
            "account_id": account_id,
            "services": services,
            "open_incidents": incidents,
            "open_support_tickets": tickets,
        }


async def save_transcript_turn(
    call_id: str,
    turn_index: int,
    role: str,
    text: str,
    phase: str | None = None,
) -> str:
    """Persist one transcript turn. Text is scrubbed for obvious PCI data before insert.

    Returns the scrubbed text so callers can publish it downstream without re-scrubbing.
    """
    scrubbed = _scrub_pii(text)
    async with AsyncSessionLocal() as session:
        turn = CallTranscript(
            call_id=call_id,
            turn_index=turn_index,
            role=role,
            text=scrubbed,
            phase=phase,
        )
        session.add(turn)
        await session.commit()
    return scrubbed


async def get_transcript(call_id: str, limit: int | None = None) -> list[dict]:
    """Return turns for a call in chronological order. Pass limit for the most recent N."""
    async with AsyncSessionLocal() as session:
        if limit is not None:
            stmt = (
                select(CallTranscript)
                .where(CallTranscript.call_id == call_id)
                .order_by(CallTranscript.turn_index.desc())
                .limit(limit)
            )
        else:
            stmt = (
                select(CallTranscript)
                .where(CallTranscript.call_id == call_id)
                .order_by(CallTranscript.turn_index)
            )
        result = await session.execute(stmt)
        turns = result.scalars().all()
        if limit is not None:
            turns = list(reversed(turns))  # restore chronological order
        return [
            {
                "turn_index": t.turn_index,
                "role": t.role,
                "text": t.text,
                "phase": t.phase,
                "timestamp": t.created_at.isoformat() if t.created_at else None,
            }
            for t in turns
        ]


async def cleanup_old_transcripts(retention_days: int) -> int:
    """Delete transcript turns older than retention_days. Returns rows deleted."""
    from datetime import timedelta
    from sqlalchemy import delete
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=retention_days)
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            delete(CallTranscript).where(CallTranscript.created_at < cutoff)
        )
        await session.commit()
        return result.rowcount


async def create_ticket(
    account_id: str,
    issue_summary: str,
    priority: str,
    call_id: str = "",
) -> dict:
    """Insert a new support ticket and return its ID."""
    async with AsyncSessionLocal() as session:
        ticket_id = "TKT-" + "".join(random.choices(string.digits, k=8))
        ticket = SupportTicket(
            ticket_id=ticket_id,
            account_id=account_id,
            call_id=call_id or None,
            issue_summary=issue_summary,
            priority=priority,
            status="open",
            created_by="ai",
        )
        session.add(ticket)
        await session.commit()

        return {
            "ticket_id": ticket_id,
            "account_id": account_id,
            "priority": priority,
            "status": "created",
        }


async def get_ticket(ticket_id: str) -> dict | None:
    """Return a specific support ticket by ID, regardless of status. None if not found."""
    async with AsyncSessionLocal() as session:
        ticket = await session.get(SupportTicket, ticket_id)
        if ticket is None:
            return None
        return {
            "ticket_id": ticket.ticket_id,
            "account_id": ticket.account_id,
            "issue_summary": ticket.issue_summary,
            "priority": ticket.priority,
            "status": ticket.status,
            "created_by": ticket.created_by,
            "created_at": ticket.created_at.isoformat() if ticket.created_at else None,
            "updated_at": ticket.updated_at.isoformat() if ticket.updated_at else None,
            "resolved_at": ticket.resolved_at.isoformat() if ticket.resolved_at else None,
        }


async def update_ticket(
    ticket_id: str,
    status: str | None = None,
    priority: str | None = None,
) -> dict:
    """Update a support ticket's status and/or priority. Returns updated fields."""
    if not status and not priority:
        return {"status": "error", "message": "No fields provided to update."}
    async with AsyncSessionLocal() as session:
        ticket = await session.get(SupportTicket, ticket_id)
        if ticket is None:
            return {"status": "error", "message": f"Ticket {ticket_id} not found."}
        if status:
            ticket.status = status
            if status in ("resolved", "closed"):
                ticket.resolved_at = datetime.now(timezone.utc).replace(tzinfo=None)
        if priority:
            ticket.priority = priority
        await session.commit()
        return {
            "ticket_id": ticket_id,
            "status": ticket.status,
            "priority": ticket.priority,
            "updated": True,
        }


async def get_account_history(account_id: str, limit: int = 10) -> dict:
    """Return resolved/closed tickets and resolved incidents for an account (historical view)."""
    async with AsyncSessionLocal() as session:
        customer = await session.get(Customer, account_id)
        if customer is None:
            return {"error": f"Account {account_id} not found"}

        ticket_stmt = (
            select(SupportTicket)
            .where(
                SupportTicket.account_id == account_id,
                SupportTicket.status.in_(["resolved", "closed"]),
            )
            .order_by(SupportTicket.updated_at.desc())
            .limit(limit)
        )
        ticket_result = await session.execute(ticket_stmt)
        resolved_tickets = [
            {
                "ticket_id": t.ticket_id,
                "issue_summary": t.issue_summary,
                "priority": t.priority,
                "status": t.status,
                "created_at": t.created_at.isoformat() if t.created_at else None,
                "resolved_at": t.resolved_at.isoformat() if t.resolved_at else None,
            }
            for t in ticket_result.scalars().all()
        ]

        incident_stmt = (
            select(ServiceIncident)
            .where(
                ServiceIncident.account_id == account_id,
                ServiceIncident.status == "resolved",
            )
            .order_by(ServiceIncident.resolved_at.desc())
            .limit(limit)
        )
        incident_result = await session.execute(incident_stmt)
        resolved_incidents = [
            {
                "incident_id": inc.incident_id,
                "title": inc.title,
                "severity": inc.severity,
                "resolved_at": inc.resolved_at.isoformat() if inc.resolved_at else None,
            }
            for inc in incident_result.scalars().all()
        ]

        return {
            "account_id": account_id,
            "resolved_tickets": resolved_tickets,
            "resolved_incidents": resolved_incidents,
        }


async def save_escalation_context(
    call_id: str,
    sip_call_id: str,
    account_id: str,
    caller_name: str,
    caller_number: str,
    phase_at_escalation: str | None,
    escalation_reason: str,
    frustration_count: int,
    tool_failure_count: int,
    recent_turns: int = 10,
) -> None:
    """Persist escalation context and recent transcript for warm handoff."""
    # Fetch the last `recent_turns` transcript turns
    recent: list[dict] = []
    async with AsyncSessionLocal() as session:
        stmt = (
            select(CallTranscript)
            .where(CallTranscript.call_id == call_id)
            .order_by(CallTranscript.turn_index.desc())
            .limit(recent_turns)
        )
        result = await session.execute(stmt)
        turns = list(reversed(result.scalars().all()))
        recent = [
            {"role": t.role, "text": t.text, "phase": t.phase}
            for t in turns
        ]

    async with AsyncSessionLocal() as session:
        record = EscalationContext(
            call_id=call_id,
            sip_call_id=sip_call_id,
            account_id=account_id,
            caller_name=caller_name,
            caller_number=caller_number,
            phase_at_escalation=phase_at_escalation,
            escalation_reason=_scrub_pii(escalation_reason),
            frustration_count=frustration_count,
            tool_failure_count=tool_failure_count,
            recent_transcript=json.dumps(recent),
        )
        await session.merge(record)
        await session.commit()


async def get_escalation_context(call_id: str) -> dict | None:
    """Return escalation context for a call, or None if not found."""
    async with AsyncSessionLocal() as session:
        record = await session.get(EscalationContext, call_id)
        if record is None:
            return None
        return {
            "call_id": record.call_id,
            "sip_call_id": record.sip_call_id,
            "account_id": record.account_id,
            "caller_name": record.caller_name,
            "caller_number": record.caller_number,
            "phase_at_escalation": record.phase_at_escalation,
            "escalation_reason": record.escalation_reason,
            "frustration_count": record.frustration_count,
            "tool_failure_count": record.tool_failure_count,
            "recent_transcript": json.loads(record.recent_transcript),
            "created_at": record.created_at.isoformat() if record.created_at else None,
        }


async def save_cdr(cdr_data: dict) -> None:
    """Upsert a CallDetailRecord. Called at call end from session_manager._teardown."""
    async with AsyncSessionLocal() as session:
        record = CallDetailRecord(**cdr_data)
        await session.merge(record)
        await session.commit()


async def get_cdr(call_id: str) -> dict | None:
    """Return CDR for a specific call, or None if not found."""
    async with AsyncSessionLocal() as session:
        record = await session.get(CallDetailRecord, call_id)
        if record is None:
            return None
        return _cdr_to_dict(record)


async def get_recent_cdrs(limit: int = 100) -> list[dict]:
    """Return the most recent CDRs ordered by created_at desc."""
    async with AsyncSessionLocal() as session:
        stmt = (
            select(CallDetailRecord)
            .order_by(CallDetailRecord.created_at.desc())
            .limit(limit)
        )
        result = await session.execute(stmt)
        return [_cdr_to_dict(r) for r in result.scalars().all()]


async def get_daily_cost_from_db(target_date: "date | None" = None) -> float:
    """Sum cost_usd from CDRs for target_date (UTC). Defaults to today."""
    from datetime import date, timedelta
    if target_date is None:
        target_date = datetime.now(timezone.utc).date()
    day_start = datetime(target_date.year, target_date.month, target_date.day)
    day_end = day_start + timedelta(days=1)
    async with AsyncSessionLocal() as session:
        from sqlalchemy import func as sqlfunc
        result = await session.execute(
            select(sqlfunc.coalesce(sqlfunc.sum(CallDetailRecord.cost_usd), 0.0))
            .where(
                CallDetailRecord.created_at >= day_start,
                CallDetailRecord.created_at < day_end,
            )
        )
        return float(result.scalar())


def _dt_utc(dt: datetime | None) -> str | None:
    """Serialize a datetime to ISO 8601, always with a UTC 'Z' suffix.

    SQLite stores naive datetimes (no tzinfo). Without the suffix, JavaScript
    treats the string as local time instead of UTC, causing wrong durations for
    users in timezones that differ from the server.
    """
    if dt is None:
        return None
    s = dt.isoformat()
    # Already has offset (+HH:MM or Z) — leave it alone
    if "+" in s or s.endswith("Z"):
        return s
    return s + "Z"


def _cdr_to_dict(record: CallDetailRecord) -> dict:
    return {
        "call_id": record.call_id,
        "sip_call_id": record.sip_call_id,
        "from_uri": record.from_uri,
        "to_uri": record.to_uri,
        "caller_number": record.caller_number,
        "account_id": record.account_id,
        "state": record.state,
        "phase_at_end": record.phase_at_end,
        "hangup_cause": record.hangup_cause,
        "escalated": bool(record.escalated),
        "frustration_count": record.frustration_count,
        "tool_failure_count": record.tool_failure_count,
        "total_tokens": record.total_tokens,
        "input_tokens": record.input_tokens,
        "output_tokens": record.output_tokens,
        "input_audio_tokens": record.input_audio_tokens,
        "output_audio_tokens": record.output_audio_tokens,
        "cost_usd": record.cost_usd,
        "duration_seconds": record.duration_seconds,
        "answered_at": _dt_utc(record.answered_at),
        "ended_at": _dt_utc(record.ended_at),
        "created_at": _dt_utc(record.created_at),
    }
