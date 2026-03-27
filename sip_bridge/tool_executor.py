"""Handles tool/function calls from the OpenAI Realtime API.

Injects an audio preamble before executing each tool to mask latency.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

log = logging.getLogger(__name__)

# Map tool names to a natural spoken preamble
_PREAMBLES: dict[str, str] = {
    "lookup_customer": "I'm looking up your account now.",
    "get_service_status": "I'm checking your service status.",
    "create_ticket": "I'm creating a support ticket for you now.",
    "get_ticket": "I'm pulling up that ticket now.",
    "update_ticket": "I'm updating that ticket for you now.",
    "get_account_history": "I'm pulling up your account history.",
    "escalate_to_agent": "I'm transferring you to a live agent now. Please hold.",
}

_DEFAULT_PREAMBLE = "I'm checking that now."


async def handle(
    session_manager: Any,
    call_id: str,
    response_id: str,
    item_id: str,
    tool_name: str,
    tool_args: dict,
    fsm: Any = None,
) -> None:
    """Execute a tool call: inject audio preamble, run the tool, return result."""
    # phase_complete drives the FSM directly — no DB call, no preamble needed
    if tool_name == "phase_complete":
        if fsm:
            await fsm.advance()
        await session_manager.send_event({
            "type": "conversation.item.create",
            "item": {
                "type": "function_call_output",
                "call_id": item_id,
                "output": "Phase advanced.",
            },
        })
        await session_manager.send_event({"type": "response.create"})
        log.debug("phase_complete processed", extra={"call_id": call_id})
        return

    # Acquire the per-session tool lock before touching the response-ready event.
    # When the model batches multiple function calls in one response they all fire
    # concurrently as asyncio tasks; without this lock they race on _response_ready
    # and both send response.create, causing "conversation_already_has_active_response".
    async with session_manager.tool_lock:
        await _handle_with_lock(session_manager, call_id, response_id, item_id, tool_name, tool_args, fsm)


async def _handle_with_lock(
    session_manager: Any,
    call_id: str,
    response_id: str,
    item_id: str,
    tool_name: str,
    tool_args: dict,
    fsm: Any,
) -> None:
    """Inner handler that runs while holding session_manager.tool_lock."""
    from config.settings import get_settings
    preamble = _PREAMBLES.get(tool_name, _DEFAULT_PREAMBLE)

    # The tool was triggered by response.function_call_arguments.done, which fires
    # BEFORE the triggering response sends response.done. Wait for that response to
    # finish before injecting the preamble, otherwise OpenAI rejects the preamble
    # response.create with "conversation_already_has_active_response".
    await session_manager.wait_for_response_done(timeout=10.0)

    # Inject audio preamble to mask latency
    await session_manager.send_event({
        "type": "conversation.item.create",
        "item": {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": preamble}],
        },
    })
    await session_manager.send_event({"type": "response.create"})

    # Execute the actual tool with timeout and transient-error retry
    log.info("Tool call: %s args=%s", tool_name, tool_args, extra={"call_id": call_id})
    succeeded = False
    try:
        result = await asyncio.wait_for(
            _dispatch_with_retry(tool_name, tool_args, call_id=call_id),
            timeout=get_settings().tool_timeout_seconds,
        )
        output = str(result)
        succeeded = True
        log.info("Tool %s result: %s", tool_name, output, extra={"call_id": call_id})
        from db.repository import emit_call_event, EVENT_TOOL_CALLED
        emit_call_event(call_id, EVENT_TOOL_CALLED, {"tool": tool_name})
    except asyncio.TimeoutError:
        timeout = get_settings().tool_timeout_seconds
        log.warning("Tool %s timed out after %.1fs", tool_name, timeout, extra={"call_id": call_id})
        output = "Tool timed out. I wasn't able to retrieve that information in time."
        await _increment_tool_failure(call_id)
        from db.repository import emit_call_event, EVENT_TOOL_FAILED
        emit_call_event(call_id, EVENT_TOOL_FAILED, {"tool": tool_name, "reason": "timeout"})
    except Exception as exc:
        log.warning("Tool %s failed: %s", tool_name, exc, extra={"call_id": call_id})
        output = f"Tool error: {exc}"
        await _increment_tool_failure(call_id)
        from db.repository import emit_call_event, EVENT_TOOL_FAILED
        emit_call_event(call_id, EVENT_TOOL_FAILED, {"tool": tool_name, "reason": str(exc)})

    # Check escalation thresholds after every tool execution.
    # escalate_to_agent manages its own transfer path, so skip it there.
    if fsm and tool_name != "escalate_to_agent":
        escalated = await fsm.check_escalation()
        if escalated:
            # Session is closing — skip sending the tool result to avoid
            # a wait_for_response_done timeout on a session that won't respond.
            return

    # For escalation the call is transferring — close the WebSocket so the agent
    # leaves the call after the SIP REFER is sent.
    if tool_name == "escalate_to_agent" and succeeded:
        log.debug("Tool %s completed, closing session", tool_name, extra={"call_id": call_id})
        await session_manager.close()
        return

    # Wait for the preamble response to finish before sending the tool result.
    # Without this, OpenAI rejects the response.create with
    # "conversation_already_has_active_response".
    await session_manager.wait_for_response_done(timeout=15.0)

    # Return tool result to the model
    await session_manager.send_event({
        "type": "conversation.item.create",
        "item": {
            "type": "function_call_output",
            "call_id": item_id,
            "output": output,
        },
    })
    await session_manager.send_event({"type": "response.create"})
    log.debug("Tool %s completed", tool_name, extra={"call_id": call_id})


async def _dispatch_with_retry(tool_name: str, args: dict, call_id: str = "") -> Any:
    """Dispatch tool with one retry on transient DB errors."""
    try:
        return await _dispatch(tool_name, args, call_id=call_id)
    except Exception as exc:
        if _is_transient_error(exc):
            log.warning("Transient error on %s, retrying: %s", tool_name, exc, extra={"call_id": call_id})
            await asyncio.sleep(0.5)
            return await _dispatch(tool_name, args, call_id=call_id)
        raise


def _is_transient_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(kw in msg for kw in ("connection", "timeout", "temporarily unavailable", "database is locked"))


async def _increment_tool_failure(call_id: str) -> None:
    from core.state_store import store
    call = await store.get_call(call_id)
    if call:
        call.tool_failure_count += 1
        await store.update_call(call)


def _resolve_lookup_args(args: dict) -> tuple[str, str]:
    """Normalize lookup_customer args — the model sometimes uses synonym key names."""
    identifier = (
        args.get("identifier")
        or args.get("customer_identifier")
        or args.get("customer_id")
        or args.get("account_id")
        or args.get("phone_number")
        or args.get("email")
        or ""
    )
    identifier_type = args.get("identifier_type", "")
    # Infer type from key name when the model omits identifier_type
    if not identifier_type:
        if args.get("email") or (identifier and "@" in identifier):
            identifier_type = "email"
        elif args.get("phone_number") or (identifier and identifier.startswith("+")):
            identifier_type = "phone"
        else:
            identifier_type = "account_id"
    if not identifier:
        raise ValueError(f"lookup_customer called with no recognizable identifier in args: {args}")
    return identifier, identifier_type


_TOOL_SYNONYMS: dict[str, str] = {
    # The model occasionally invents these alternative names
    "get_support_tickets": "get_service_status",
    "get_tickets":         "get_service_status",
    "get_open_tickets":    "get_service_status",
    "check_service":       "get_service_status",
    "get_account_status":  "get_service_status",
    # get_ticket synonyms
    "lookup_ticket":       "get_ticket",
    "get_ticket_details":  "get_ticket",
    "check_ticket":        "get_ticket",
    # update_ticket synonyms
    "close_ticket":        "update_ticket",
    "resolve_ticket":      "update_ticket",
}

# Tools allowed per phase — defence-in-depth guard against the model calling
# tools that were not included in the current session.update config.
_PHASE_TOOL_ALLOWLIST: dict[str, set[str]] = {
    "GREETING": {"phase_complete", "escalate_to_agent"},
    "VERIFY":   {"phase_complete", "escalate_to_agent", "lookup_customer"},
    "TRIAGE":   {"phase_complete", "escalate_to_agent", "lookup_customer"},
    "DIAGNOSE": {"phase_complete", "escalate_to_agent", "lookup_customer",
                 "get_service_status", "get_ticket", "get_account_history", "create_ticket"},
    "RESOLVE":  {"phase_complete", "escalate_to_agent", "lookup_customer",
                 "get_service_status", "get_ticket", "get_account_history", "create_ticket", "update_ticket"},
    "WRAP_UP":  {"phase_complete", "escalate_to_agent", "lookup_customer",
                 "get_service_status", "get_ticket", "get_account_history", "create_ticket", "update_ticket"},
}


async def _dispatch(tool_name: str, args: dict, call_id: str = "") -> Any:
    """Route tool calls to their implementations."""
    tool_name = _TOOL_SYNONYMS.get(tool_name, tool_name)

    # Phase guard — reject tools that are not allowed in the current phase.
    # The model occasionally calls tools from a previous phase due to batching
    # or session.update timing. Returning an error is safer than executing.
    if call_id:
        from core.state_store import store
        _call = await store.get_call(call_id)
        if _call and _call.phase:
            allowed = _PHASE_TOOL_ALLOWLIST.get(_call.phase.value, set())
            if tool_name not in allowed:
                log.warning(
                    "Phase guard: tool '%s' blocked in phase %s",
                    tool_name, _call.phase.value,
                    extra={"call_id": call_id},
                )
                return {
                    "status": "error",
                    "message": (
                        f"Tool '{tool_name}' is not available in the current phase ({_call.phase.value}). "
                        "Call phase_complete to advance to the next phase first."
                    ),
                }
    if tool_name == "lookup_customer":
        identifier, identifier_type = _resolve_lookup_args(args)
        # Hard-reject phone lookups that use an identifier the model invented rather
        # than the verified caller ID.  The model sometimes fabricates a plausible-
        # looking E.164 number (e.g. +12025550123) when it cannot find the real one.
        if identifier_type == "phone":
            from core.state_store import store
            call = await store.get_call(call_id)
            verified = call.caller_number if call else ""
            if not verified:
                return {
                    "status": "error",
                    "message": (
                        "Phone lookup rejected: no verified caller ID is available for this session. "
                        "Ask the caller for their email address or account ID instead."
                    ),
                }
            from db.repository import _normalize_phone_candidates
            if identifier not in _normalize_phone_candidates(verified):
                log.warning(
                    "Blocked phone lookup with non-verified number %s (verified: %s)",
                    identifier, verified,
                    extra={"call_id": call_id},
                )
                return {
                    "status": "error",
                    "message": (
                        f"Phone lookup rejected: '{identifier}' is not the verified caller ID. "
                        f"Only the verified caller ID may be used for phone lookups. "
                        "Ask the caller for their email address or account ID instead."
                    ),
                }
        return await _lookup_customer(identifier, identifier_type)
    elif tool_name == "get_service_status":
        return await _get_service_status(args["account_id"])
    elif tool_name == "create_ticket":
        return await _create_ticket(args["account_id"], args["issue_summary"], args["priority"], call_id=call_id)
    elif tool_name == "get_ticket":
        result = await _get_ticket(args["ticket_id"])
        if result is None:
            return {"status": "not_found", "ticket_id": args["ticket_id"]}
        return result
    elif tool_name == "update_ticket":
        return await _update_ticket(args["ticket_id"], args.get("status"), args.get("priority"))
    elif tool_name == "get_account_history":
        return await _get_account_history(args["account_id"])
    elif tool_name == "escalate_to_agent":
        return await _escalate_to_agent(call_id, args.get("reason", ""))
    else:
        raise ValueError(f"Unknown tool: {tool_name}")


# ── Tool implementations ───────────────────────────────────────────────────────

async def _lookup_customer(identifier: str, identifier_type: str) -> dict:
    from db import repository
    result = await repository.find_customer(identifier, identifier_type)
    if result is None:
        return {"status": "not_found", "identifier": identifier}
    return {"status": "found", **result}


async def _get_service_status(account_id: str) -> dict:
    from db import repository
    return await repository.get_service_status(account_id)


async def _create_ticket(account_id: str, issue_summary: str, priority: str, call_id: str = "") -> dict:
    from db import repository
    return await repository.create_ticket(account_id, issue_summary, priority, call_id=call_id)


async def _get_ticket(ticket_id: str) -> dict | None:
    from db import repository
    return await repository.get_ticket(ticket_id)


async def _update_ticket(ticket_id: str, status: str | None, priority: str | None) -> dict:
    from db import repository
    return await repository.update_ticket(ticket_id, status=status, priority=priority)


async def _get_account_history(account_id: str) -> dict:
    from db import repository
    return await repository.get_account_history(account_id)


async def _escalate_to_agent(call_id: str, reason: str) -> dict:
    from config.settings import get_settings
    from sip_bridge import call_controller
    s = get_settings()
    log.info("Escalating to human agent: %s", reason, extra={"call_id": call_id})

    # Write warm-handoff context to DB (best-effort; don't let it block the REFER)
    asyncio.create_task(_write_handoff_context(call_id, reason))

    await call_controller.refer(call_id, s.human_agent_sip_uri)
    return {"status": "transferring", "target": s.human_agent_sip_uri, "reason": reason}


async def _write_handoff_context(call_id: str, reason: str) -> None:
    """Persist escalation context and optionally POST it to a webhook."""
    from config.settings import get_settings
    from core.state_store import store
    from db import repository

    try:
        call = await store.get_call(call_id)
        phase = call.phase.value if call and call.phase else None
        await repository.save_escalation_context(
            call_id=call_id,
            sip_call_id=call.sip_call_id if call else "",
            account_id=call.account_id if call else "",
            caller_name=call.caller_name if call else "",
            caller_number=call.caller_number if call else "",
            phase_at_escalation=phase,
            escalation_reason=reason,
            frustration_count=call.frustration_count if call else 0,
            tool_failure_count=call.tool_failure_count if call else 0,
        )
        log.debug("Handoff context saved", extra={"call_id": call_id})
    except Exception as exc:
        log.warning("Failed to save handoff context: %s", exc, extra={"call_id": call_id})
        return

    # Optional real-time delivery to agent desktop via webhook
    s = get_settings()
    if s.handoff_context_url:
        try:
            import httpx
            context = await repository.get_escalation_context(call_id)
            async with httpx.AsyncClient(timeout=5.0) as client:
                await client.post(s.handoff_context_url, json=context)
            log.debug("Handoff context POSTed to %s", s.handoff_context_url, extra={"call_id": call_id})
        except Exception as exc:
            log.warning("Handoff webhook POST failed: %s", exc, extra={"call_id": call_id})
