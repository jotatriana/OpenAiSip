"""Handles tool/function calls from the OpenAI Realtime API.

Injects an audio preamble before executing each tool to mask latency.
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

# Map tool names to a natural spoken preamble
_PREAMBLES: dict[str, str] = {
    "lookup_customer": "I'm looking up your account now.",
    "get_service_status": "I'm checking your service status.",
    "create_ticket": "I'm creating a support ticket for you now.",
}

_DEFAULT_PREAMBLE = "I'm checking that now."


async def handle(
    session_manager: Any,
    call_id: str,
    response_id: str,
    item_id: str,
    tool_name: str,
    tool_args: dict,
) -> None:
    """Execute a tool call: inject audio preamble, run the tool, return result."""
    preamble = _PREAMBLES.get(tool_name, _DEFAULT_PREAMBLE)

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

    # Execute the actual tool
    try:
        result = await _dispatch(tool_name, tool_args, call_id=call_id)
        output = str(result)
    except Exception as exc:
        log.warning("Tool %s failed: %s", tool_name, exc, extra={"call_id": call_id})
        output = f"Tool error: {exc}"

        # Increment tool failure counter for escalation tracking
        from core.state_store import store
        call = await store.get_call(call_id)
        if call:
            call.tool_failure_count += 1
            await store.update_call(call)

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


async def _dispatch(tool_name: str, args: dict, call_id: str = "") -> Any:
    """Route tool calls to their implementations."""
    if tool_name == "lookup_customer":
        return await _lookup_customer(args["identifier"], args["identifier_type"])
    elif tool_name == "get_service_status":
        return await _get_service_status(args["account_id"])
    elif tool_name == "create_ticket":
        return await _create_ticket(args["account_id"], args["issue_summary"], args["priority"], call_id=call_id)
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
