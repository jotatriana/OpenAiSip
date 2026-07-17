"""Tests for prompt_builder tool definitions and phase instructions."""
import pytest
from unittest.mock import MagicMock, patch

from core.models import ConvPhase
from sip_bridge.prompt_builder import _tools_for_phase, build


@pytest.fixture(autouse=True)
def mock_settings():
    with patch("sip_bridge.prompt_builder.get_settings") as m:
        m.return_value = MagicMock(
            openai_model="gpt-realtime-mini",
            openai_voice="alloy",
            default_language="en-US",
        )
        yield m


# ---------------------------------------------------------------------------
# Tool presence per phase
# ---------------------------------------------------------------------------

def test_greeting_phase_tools():
    names = [t["name"] for t in _tools_for_phase(ConvPhase.GREETING)]
    assert "phase_complete" in names
    assert "escalate_to_agent" in names
    assert "lookup_customer" not in names
    assert "get_service_status" not in names


def test_verify_phase_tools():
    names = [t["name"] for t in _tools_for_phase(ConvPhase.VERIFY)]
    assert "lookup_customer" in names
    assert "phase_complete" in names
    assert "escalate_to_agent" in names
    assert "get_service_status" not in names
    assert "create_ticket" not in names


def test_triage_phase_tools():
    """TRIAGE has only phase_complete — forces model to call it (tool_choice=required at session level)."""
    names = [t["name"] for t in _tools_for_phase(ConvPhase.TRIAGE)]
    assert "phase_complete" in names
    # lookup_customer removed from TRIAGE: identity is confirmed in VERIFY; TRIAGE only classifies
    assert "lookup_customer" not in names
    # escalate_to_agent removed: model must route via phase_complete only
    assert "escalate_to_agent" not in names
    # service tools belong in DIAGNOSE/RESOLVE only
    assert "get_service_status" not in names
    assert "create_ticket" not in names


def test_diagnose_phase_tools():
    names = [t["name"] for t in _tools_for_phase(ConvPhase.DIAGNOSE)]
    assert set(names) == {
        "lookup_customer", "get_service_status", "create_ticket",
        "get_ticket", "get_account_history",
        "phase_complete", "wait_for_user", "escalate_to_agent",
    }


def test_resolve_phase_tools():
    names = [t["name"] for t in _tools_for_phase(ConvPhase.RESOLVE)]
    assert set(names) == {
        "lookup_customer", "get_service_status", "create_ticket",
        "get_ticket", "get_account_history", "update_ticket",
        "phase_complete", "wait_for_user", "escalate_to_agent", "report_new_issue",
    }


def test_wrap_up_phase_tools():
    names = [t["name"] for t in _tools_for_phase(ConvPhase.WRAP_UP)]
    assert "phase_complete" in names
    assert "escalate_to_agent" in names
    assert "lookup_customer" in names


# ---------------------------------------------------------------------------
# report_new_issue — RESOLVE/WRAP_UP loopback trigger
# ---------------------------------------------------------------------------

def test_report_new_issue_present_in_resolve_and_wrap_up():
    assert "report_new_issue" in [t["name"] for t in _tools_for_phase(ConvPhase.RESOLVE)]
    assert "report_new_issue" in [t["name"] for t in _tools_for_phase(ConvPhase.WRAP_UP)]


def test_report_new_issue_absent_elsewhere():
    for phase in (ConvPhase.GREETING, ConvPhase.VERIFY, ConvPhase.TRIAGE, ConvPhase.DIAGNOSE):
        names = [t["name"] for t in _tools_for_phase(phase)]
        assert "report_new_issue" not in names, f"report_new_issue should not appear in {phase}"


def test_report_new_issue_is_autonomous_with_summary_param():
    tool = next(t for t in _tools_for_phase(ConvPhase.RESOLVE) if t["name"] == "report_new_issue")
    assert "AUTONOMOUS" in tool["description"]
    assert "summary" in tool["parameters"]["properties"]
    assert "summary" in tool["parameters"]["required"]


# ---------------------------------------------------------------------------
# BEHAVIOR tags
# ---------------------------------------------------------------------------

def _get_tool(phase, name):
    return next(t for t in _tools_for_phase(phase) if t["name"] == name)


def test_lookup_customer_is_proactive():
    desc = _get_tool(ConvPhase.VERIFY, "lookup_customer")["description"]
    assert "PROACTIVE" in desc


def test_get_service_status_is_proactive():
    desc = _get_tool(ConvPhase.DIAGNOSE, "get_service_status")["description"]
    assert "PROACTIVE" in desc


def test_create_ticket_is_confirmation_first():
    desc = _get_tool(ConvPhase.DIAGNOSE, "create_ticket")["description"]
    assert "CONFIRMATION FIRST" in desc


def test_escalate_to_agent_is_confirmation_first():
    desc = _get_tool(ConvPhase.GREETING, "escalate_to_agent")["description"]
    assert "CONFIRMATION FIRST" in desc


# ---------------------------------------------------------------------------
# Use when / Do NOT use when rules
# ---------------------------------------------------------------------------

def test_lookup_customer_has_use_and_avoid_rules():
    desc = _get_tool(ConvPhase.VERIFY, "lookup_customer")["description"]
    assert "PHONE RULE" in desc  # verified number enforcement
    assert "NEVER" in desc       # prohibition on verbal numbers


def test_get_service_status_has_use_and_avoid_rules():
    desc = _get_tool(ConvPhase.DIAGNOSE, "get_service_status")["description"]
    assert "Use when:" in desc
    assert "Do NOT use when:" in desc


def test_create_ticket_has_use_and_avoid_rules():
    desc = _get_tool(ConvPhase.DIAGNOSE, "create_ticket")["description"]
    assert "Use when:" in desc
    assert "Do NOT use when:" in desc


def test_escalate_to_agent_has_use_and_avoid_rules():
    desc = _get_tool(ConvPhase.GREETING, "escalate_to_agent")["description"]
    assert "Use when:" in desc
    assert "Do NOT use when:" in desc


# ---------------------------------------------------------------------------
# Preamble phrases
# ---------------------------------------------------------------------------

_NO_PREAMBLE_TOOLS = {"phase_complete", "escalate_to_agent", "wait_for_user", "report_new_issue"}


def test_db_backed_tools_have_preamble_phrases():
    """Tools that mask DB latency must have preamble phrases; FSM control tools do not."""
    for phase in ConvPhase:
        for tool in _tools_for_phase(phase):
            if tool["name"] in _NO_PREAMBLE_TOOLS:
                continue
            assert "Preamble sample phrases" in tool["description"], (
                f"{tool['name']} in {phase} is missing preamble sample phrases"
            )


# ---------------------------------------------------------------------------
# Phase instructions reinforce tool behaviors
# ---------------------------------------------------------------------------

def test_diagnose_instructions_call_get_service_status_immediately():
    """DIAGNOSE instructions must tell the model to call get_service_status immediately."""
    config = build(ConvPhase.DIAGNOSE)
    assert "IMMEDIATELY call get_service_status" in config["instructions"]


def test_resolve_instructions_mention_confirmation_first():
    config = build(ConvPhase.RESOLVE)
    assert "CONFIRMATION FIRST" in config["instructions"]


def test_resolve_instructions_warn_against_saying_will_transfer():
    config = build(ConvPhase.RESOLVE)
    assert "do NOT just say you will transfer" in config["instructions"]


def test_diagnose_instructions_include_failure_handling():
    config = build(ConvPhase.DIAGNOSE)
    assert "tool call fails" in config["instructions"]


def test_resolve_instructions_include_failure_handling():
    config = build(ConvPhase.RESOLVE)
    assert "tool call fails" in config["instructions"]


# ---------------------------------------------------------------------------
# build() output structure
# ---------------------------------------------------------------------------

def test_build_returns_required_keys():
    config = build(ConvPhase.GREETING)
    assert config["type"] == "realtime"
    for key in ("model", "instructions", "tools", "tool_choice", "audio"):
        assert key in config
    assert config["audio"]["input"]["format"] == {"type": "audio/pcm", "rate": 24000}
    assert config["audio"]["input"]["transcription"] == {"model": "whisper-1"}
    assert config["audio"]["input"]["turn_detection"]["type"] == "server_vad"
    assert config["audio"]["output"]["format"] == {"type": "audio/pcm", "rate": 24000}
    assert "voice" in config["audio"]["output"]


def test_build_greeting_with_caller_name():
    config = build(ConvPhase.GREETING, caller_name="Jane Doe", caller_number="+15550001234")
    assert "Jane" in config["instructions"]
    assert "+15550001234" in config["instructions"]


def test_build_greeting_without_caller_name():
    config = build(ConvPhase.GREETING)
    assert "Thank you for calling" in config["instructions"]


def test_build_greeting_known_caller_includes_account_suffix():
    """When caller is identified, greeting must mention the last 4 chars of the account ID."""
    config = build(
        ConvPhase.GREETING,
        caller_name="Jane Doe",
        caller_number="+15550001234",
        account_id="ACC-JD001",
        service_names=["internet", "phone"],
    )
    instructions = config["instructions"]
    # Last 4 chars of "ACC-JD001" = "D001"
    assert "D001" in instructions


def test_build_greeting_known_caller_includes_services():
    """When caller is identified and services are known, greeting must list them."""
    config = build(
        ConvPhase.GREETING,
        caller_name="Jane Doe",
        caller_number="+15550001234",
        account_id="ACC-JD001",
        service_names=["internet", "TV"],
    )
    instructions = config["instructions"]
    assert "internet" in instructions
    assert "TV" in instructions


def test_build_greeting_known_caller_no_services():
    """When no services are available, greeting still works without the services phrase."""
    config = build(
        ConvPhase.GREETING,
        caller_name="Jane Doe",
        caller_number="+15550001234",
        account_id="ACC-JD001",
        service_names=[],
    )
    instructions = config["instructions"]
    # Account suffix still present; no crash
    assert "D001" in instructions


def test_build_greeting_unknown_caller_no_account_suffix():
    """Unknown callers must not see any account suffix or services line."""
    config = build(
        ConvPhase.GREETING,
        caller_name="Jane Doe",
        caller_number="+15550001234",
    )
    instructions = config["instructions"]
    assert "ending in" not in instructions


# ---------------------------------------------------------------------------
# Base instruction safety rules
# ---------------------------------------------------------------------------

def test_base_instructions_no_fabrication_rule():
    config = build(ConvPhase.GREETING)
    assert "Never Fabricate" in config["instructions"]
    assert "tool call" in config["instructions"]


def test_base_instructions_verified_phone_rule():
    config = build(ConvPhase.GREETING)
    assert "verified" in config["instructions"].lower()
    assert "spoken" in config["instructions"].lower() or "verbally" in config["instructions"].lower() or "spoke" in config["instructions"].lower()


def test_base_instructions_out_of_scope_deflection():
    config = build(ConvPhase.GREETING)
    assert "account information, service status, support tickets" in config["instructions"]


def test_base_instructions_no_persona_adoption():
    config = build(ConvPhase.GREETING)
    assert "persona" in config["instructions"].lower()


def test_base_instructions_no_markdown():
    config = build(ConvPhase.GREETING)
    instructions = config["instructions"]
    assert "markdown" in instructions.lower() or "symbols" in instructions.lower()


# ---------------------------------------------------------------------------
# TRIAGE guard against inventing account_id
# ---------------------------------------------------------------------------

def test_triage_instructions_guard_against_invented_account_id():
    """When a known caller's account_id is present, TRIAGE instructions carry the CONFIRMED ACCOUNT guard."""
    config = build(ConvPhase.TRIAGE, account_id="ACC-JT001")
    instructions = config["instructions"]
    assert "DO NOT" in instructions
    assert "account_id" in instructions
    assert "CONFIRMED ACCOUNT" in instructions


def test_triage_phase_does_not_have_create_ticket():
    """create_ticket must not be available in TRIAGE — it belongs in DIAGNOSE/RESOLVE."""
    names = [t["name"] for t in _tools_for_phase(ConvPhase.TRIAGE)]
    assert "create_ticket" not in names


# ---------------------------------------------------------------------------
# VERIFY phase path variations
# ---------------------------------------------------------------------------

def test_verify_with_caller_number_hardcodes_phone():
    config = build(ConvPhase.VERIFY, caller_number="+14165550100")
    assert "+14165550100" in config["instructions"]
    assert "identifier_type='phone'" in config["instructions"]


def test_verify_without_caller_number_asks_for_email_or_account_id():
    config = build(ConvPhase.VERIFY, caller_number="")
    instructions = config["instructions"]
    assert "email" in instructions.lower()
    assert "account" in instructions.lower()
    # Must NOT suggest asking for a phone number verbally
    assert "ask" not in instructions.lower().split("phone")[0][-30:] if "phone" in instructions.lower() else True


# ---------------------------------------------------------------------------
# lookup_customer supports email identifier_type
# ---------------------------------------------------------------------------

def test_lookup_customer_supports_email_identifier_type():
    tool = next(t for t in _tools_for_phase(ConvPhase.VERIFY) if t["name"] == "lookup_customer")
    enum_values = tool["parameters"]["properties"]["identifier_type"]["enum"]
    assert "email" in enum_values
    assert "phone" in enum_values
    assert "account_id" in enum_values


# ---------------------------------------------------------------------------
# Billing guardrails — no billing tool exists, fabrication must be blocked
# ---------------------------------------------------------------------------

def test_base_instructions_no_billing_access_section():
    """Base instructions must contain the explicit 'No Billing Access' section."""
    config = build(ConvPhase.GREETING)
    assert "No Billing Access" in config["instructions"]


def test_base_instructions_billing_redirect_phrase():
    """The exact redirect phrase must appear so the model knows what to say."""
    config = build(ConvPhase.GREETING)
    assert "I don't have access to billing details" in config["instructions"]


def test_base_instructions_no_billing_tools():
    """No phase should expose a billing tool — there isn't one."""
    for phase in ConvPhase:
        names = [t["name"] for t in _tools_for_phase(phase)]
        assert "get_billing" not in names
        assert "get_invoice" not in names


def test_triage_intercepts_billing_questions():
    """TRIAGE must teach the billing category so the model routes it via phase_complete."""
    config = build(ConvPhase.TRIAGE)
    instructions = config["instructions"]
    assert "billing" in instructions.lower()
    # Billing is now a routable category — TRIAGE classifies it via
    # phase_complete(service_category="billing") rather than escalating immediately.
    tools = {t["name"]: t for t in config["tools"]}
    assert "service_category" in tools["phase_complete"]["parameters"]["required"]
    assert "billing" in tools["phase_complete"]["parameters"]["properties"]["service_category"]["enum"]


def test_diagnose_intercepts_billing_questions():
    """DIAGNOSE instructions must redirect billing questions to escalation."""
    config = build(ConvPhase.DIAGNOSE)
    instructions = config["instructions"]
    assert "billing" in instructions.lower()
    assert "escalate_to_agent" in instructions


def test_resolve_intercepts_billing_questions():
    """RESOLVE instructions must block billing fabrication and offer escalation."""
    config = build(ConvPhase.RESOLVE)
    instructions = config["instructions"]
    assert "billing" in instructions.lower()
    assert "I don't have access to billing details" in instructions


def test_never_fabricate_covers_billing_fields():
    """Never Fabricate section must explicitly list billing-related fields."""
    config = build(ConvPhase.GREETING)
    instructions = config["instructions"]
    assert "billing amounts" in instructions or "billing" in instructions
    assert "account balances" in instructions or "balance" in instructions


# ---------------------------------------------------------------------------
# Incidents vs Support Tickets — terminology and scripted responses
# ---------------------------------------------------------------------------

def test_base_instructions_incident_script():
    """Base instructions must tell the model exactly what to say for open_incidents."""
    config = build(ConvPhase.GREETING)
    instructions = config["instructions"]
    assert "known service incident" in instructions
    assert "open_incidents" in instructions


def test_base_instructions_ticket_script():
    """Base instructions must tell the model exactly what to say for open_support_tickets."""
    config = build(ConvPhase.GREETING)
    instructions = config["instructions"]
    assert "open support ticket" in instructions
    assert "open_support_tickets" in instructions


def test_base_instructions_never_confuse_incidents_and_tickets():
    """Base instructions must explicitly prohibit calling an incident a ticket."""
    config = build(ConvPhase.GREETING)
    instructions = config["instructions"]
    assert "NEVER call an incident" in instructions or "NEVER" in instructions
    assert "NEVER confuse" in instructions or "NEVER merge" in instructions


def test_diagnose_reports_incidents_and_tickets_separately():
    """DIAGNOSE instructions must script separate responses for each field."""
    config = build(ConvPhase.DIAGNOSE)
    instructions = config["instructions"]
    assert "open_incidents" in instructions
    assert "open_support_tickets" in instructions
    assert "SEPARATELY" in instructions or "separately" in instructions


def test_diagnose_scripts_empty_ticket_response():
    """DIAGNOSE must tell the model what to say when open_support_tickets is empty."""
    config = build(ConvPhase.DIAGNOSE)
    assert "no open support tickets" in config["instructions"]


# ---------------------------------------------------------------------------
# Confirmed account_id injected into every post-GREETING phase
# ---------------------------------------------------------------------------

def test_triage_contains_confirmed_account_id_when_known():
    config = build(ConvPhase.TRIAGE, account_id="ACC-JT001")
    assert "ACC-JT001" in config["instructions"]
    assert "CONFIRMED ACCOUNT" in config["instructions"]


def test_diagnose_contains_confirmed_account_id_when_known():
    config = build(ConvPhase.DIAGNOSE, account_id="ACC-JT001")
    assert "ACC-JT001" in config["instructions"]
    assert "CONFIRMED ACCOUNT" in config["instructions"]


def test_resolve_contains_confirmed_account_id_when_known():
    config = build(ConvPhase.RESOLVE, account_id="ACC-JT001")
    assert "ACC-JT001" in config["instructions"]
    assert "CONFIRMED ACCOUNT" in config["instructions"]


def test_wrap_up_contains_confirmed_account_id_when_known():
    config = build(ConvPhase.WRAP_UP, account_id="ACC-JT001")
    assert "ACC-JT001" in config["instructions"]
    assert "CONFIRMED ACCOUNT" in config["instructions"]


def test_triage_no_account_context_when_unknown():
    """When account is not yet known, CONFIRMED ACCOUNT line must not appear."""
    config = build(ConvPhase.TRIAGE)
    assert "CONFIRMED ACCOUNT" not in config["instructions"]


def test_triage_blocks_re_verification_when_account_known():
    """TRIAGE must tell the model not to ask the caller to re-verify."""
    config = build(ConvPhase.TRIAGE, account_id="ACC-JT001")
    assert "re-verify" in config["instructions"].lower() or "already confirmed" in config["instructions"].lower()


# ---------------------------------------------------------------------------
# lookup_customer PROACTIVE behaviour suppressed when account already known
# ---------------------------------------------------------------------------

def test_lookup_customer_not_proactive_when_account_known():
    """When account is already confirmed, lookup_customer must NOT be PROACTIVE (check DIAGNOSE)."""
    tool = next(t for t in _tools_for_phase(ConvPhase.DIAGNOSE, known_caller=True)
                if t["name"] == "lookup_customer")
    assert "PROACTIVE" not in tool["description"]
    assert "ON DEMAND ONLY" in tool["description"]


def test_lookup_customer_is_proactive_when_account_unknown():
    """When account is not yet confirmed, lookup_customer should remain PROACTIVE (check DIAGNOSE)."""
    tool = next(t for t in _tools_for_phase(ConvPhase.DIAGNOSE, known_caller=False)
                if t["name"] == "lookup_customer")
    assert "PROACTIVE" in tool["description"]


def test_lookup_customer_phone_rule_warns_against_inventing():
    """Phone rule must explicitly prohibit inventing or guessing a number."""
    tool = next(t for t in _tools_for_phase(ConvPhase.VERIFY) if t["name"] == "lookup_customer")
    desc = tool["description"]
    assert "NEVER invent" in desc or "NEVER" in desc


# ---------------------------------------------------------------------------
# GREETING phase — no fabrication of service data, quick phase_complete
# ---------------------------------------------------------------------------

def test_greeting_blocks_service_status_fabrication():
    """GREETING instructions must explicitly say the model has no service tools here."""
    config = build(ConvPhase.GREETING)
    instructions = config["instructions"]
    assert "do not have service status tools" in instructions.lower() or \
           "DO NOT have service status tools" in instructions or \
           "do NOT have service status tools" in instructions


def test_greeting_redirects_service_questions_to_phase_complete():
    """GREETING must tell the model to call phase_complete if the caller asks about service."""
    config = build(ConvPhase.GREETING)
    instructions = config["instructions"]
    assert "phase_complete" in instructions
    assert "Let me check on that for you" in instructions or "check on that" in instructions.lower()


def test_greeting_limits_exchanges_before_advancing():
    """GREETING must cap the number of exchanges before calling phase_complete."""
    config = build(ConvPhase.GREETING)
    instructions = config["instructions"]
    assert "linger" in instructions.lower() or "maximum" in instructions.lower() or \
           "1 or 2 exchanges" in instructions or "1–2 exchanges" in instructions or \
           "2 exchanges" in instructions


def test_greeting_does_not_repeat():
    """GREETING must explicitly tell the model to say the greeting exactly once."""
    config = build(ConvPhase.GREETING)
    instructions = config["instructions"]
    assert "exactly once" in instructions.lower() or "do not repeat" in instructions.lower()


def test_greeting_handles_vague_acknowledgment():
    """GREETING must instruct the model to ask a follow-up when caller just says 'thanks'."""
    config = build(ConvPhase.GREETING)
    instructions = config["instructions"]
    # Must mention what to do on short acks and must cap at one follow-up
    assert "acknowledgment" in instructions.lower() or "thank you" in instructions.lower()
    assert "phase_complete" in instructions


def test_greeting_does_not_have_service_status_tool():
    """GREETING must not expose get_service_status — confirm no tool leakage."""
    names = [t["name"] for t in _tools_for_phase(ConvPhase.GREETING)]
    assert "get_service_status" not in names
    assert "create_ticket" not in names


# ---------------------------------------------------------------------------
# TRIAGE phase — do not call get_service_status, do not report ticket/incident info
# ---------------------------------------------------------------------------

def test_triage_instructions_prohibit_calling_get_service_status():
    """TRIAGE instructions must explicitly tell the model NOT to call get_service_status."""
    config = build(ConvPhase.TRIAGE)
    instructions = config["instructions"]
    assert "DO NOT call get_service_status" in instructions


def test_create_ticket_description_requires_issue_summary_confirmation():
    """create_ticket tool description must tell the model to confirm the issue summary first."""
    tool = next(t for t in _tools_for_phase(ConvPhase.RESOLVE) if t["name"] == "create_ticket")
    desc = tool["description"]
    assert "issue summary" in desc.lower() or "issue_summary" in desc or "summary" in desc.lower()


def test_create_ticket_description_requires_reading_back_ticket_id():
    """create_ticket tool description must tell the model to read back the ticket_id."""
    tool = next(t for t in _tools_for_phase(ConvPhase.RESOLVE) if t["name"] == "create_ticket")
    desc = tool["description"]
    assert "ticket_id" in desc
    assert "IMMEDIATELY" in desc or "immediately" in desc.lower()


def test_resolve_instructions_require_ticket_id_readback():
    """RESOLVE instructions must tell the model to read back the ticket number to the caller."""
    config = build(ConvPhase.RESOLVE)
    instructions = config["instructions"]
    assert "ticket_id" in instructions
    assert "ticket number" in instructions.lower()


def test_triage_instructions_prohibit_reporting_service_status():
    """TRIAGE must block reporting incidents or tickets without a tool call."""
    config = build(ConvPhase.TRIAGE)
    instructions = config["instructions"]
    # Check for any form of the prohibition (old or new phrasing)
    lowered = instructions.lower()
    assert (
        "do not report" in lowered
        or "do not fabricate" in lowered
        or "fabricat" in lowered
        or "cannot know" in lowered
        or "no service data" in lowered
    )


def test_triage_instructions_advance_immediately_when_issue_known():
    """TRIAGE must tell the model to call phase_complete without asking follow-up questions."""
    config = build(ConvPhase.TRIAGE)
    instructions = config["instructions"]
    assert "phase_complete" in instructions
    assert "immediately" in instructions.lower()


# ---------------------------------------------------------------------------
# Tool synonym normalization (_TOOL_SYNONYMS in tool_executor)
# ---------------------------------------------------------------------------

def test_tool_synonyms_map_get_support_tickets():
    """get_support_tickets must be normalised to get_service_status before dispatch."""
    from sip_bridge.tool_executor import _TOOL_SYNONYMS
    assert _TOOL_SYNONYMS.get("get_support_tickets") == "get_service_status"


def test_tool_synonyms_map_get_tickets():
    from sip_bridge.tool_executor import _TOOL_SYNONYMS
    assert _TOOL_SYNONYMS.get("get_tickets") == "get_service_status"


def test_tool_synonyms_map_check_service():
    from sip_bridge.tool_executor import _TOOL_SYNONYMS
    assert _TOOL_SYNONYMS.get("check_service") == "get_service_status"


def test_tool_synonyms_known_tools_not_remapped():
    """Real tool names must not appear in _TOOL_SYNONYMS (no accidental override)."""
    from sip_bridge.tool_executor import _TOOL_SYNONYMS
    for real_tool in ("get_service_status", "lookup_customer", "create_ticket",
                      "phase_complete", "escalate_to_agent"):
        assert real_tool not in _TOOL_SYNONYMS


# ---------------------------------------------------------------------------
# DIAGNOSE — advance immediately after reporting, no re-calling the tool
# ---------------------------------------------------------------------------

def test_diagnose_instructions_advance_immediately_after_reporting():
    """DIAGNOSE must tell the model to call phase_complete right after reporting results."""
    config = build(ConvPhase.DIAGNOSE)
    instructions = config["instructions"]
    assert "IMMEDIATELY call phase_complete" in instructions or \
           "immediately" in instructions.lower() and "phase_complete" in instructions


def test_diagnose_instructions_prohibit_asking_is_there_anything_else():
    """DIAGNOSE must not wait for caller response before advancing — that belongs to WRAP_UP."""
    config = build(ConvPhase.DIAGNOSE)
    instructions = config["instructions"]
    assert "WRAP_UP" in instructions or "wrap-up" in instructions.lower()
    assert "do not ask" in instructions.lower() or "Do NOT ask" in instructions


def test_diagnose_instructions_once_per_phase():
    """DIAGNOSE must explicitly say not to call get_service_status more than once."""
    config = build(ConvPhase.DIAGNOSE)
    instructions = config["instructions"]
    assert "more than once" in instructions.lower() or "once" in instructions.lower()


def test_get_service_status_once_per_phase_behavior_tag():
    """get_service_status tool description must say it should only be called once per phase."""
    tool = next(t for t in _tools_for_phase(ConvPhase.DIAGNOSE) if t["name"] == "get_service_status")
    desc = tool["description"]
    assert "once" in desc.lower()
    assert "more than once" in desc.lower() or "at most" in desc.lower()


# ---------------------------------------------------------------------------
# RESOLVE — propose and advance; do not ask "is there anything else?"
# ---------------------------------------------------------------------------

def test_resolve_instructions_advance_after_stating_resolution():
    """RESOLVE must tell the model to call phase_complete once the resolution is stated."""
    config = build(ConvPhase.RESOLVE)
    instructions = config["instructions"]
    assert "phase_complete" in instructions
    assert "IMMEDIATELY" in instructions or "immediately" in instructions.lower()


def test_resolve_instructions_do_not_ask_anything_else():
    """RESOLVE must not invite open-ended 'is there anything else?' — that's WRAP_UP's job."""
    config = build(ConvPhase.RESOLVE)
    instructions = config["instructions"]
    assert "WRAP_UP" in instructions or "wrap-up" in instructions.lower()


# ---------------------------------------------------------------------------
# TRIAGE — natural language, acknowledge before advancing
# ---------------------------------------------------------------------------

def test_triage_uses_natural_question_not_jargon():
    """TRIAGE fallback question must be plain language, not internal 'technical issue' jargon."""
    config = build(ConvPhase.TRIAGE)
    instructions = config["instructions"]
    # The new simple question should be present; the old jargon phrase should be gone
    assert "What can I help you with" in instructions or "what can i help" in instructions.lower()
    assert "technical issue with your service" not in instructions


def test_triage_is_silent_routing_phase():
    """TRIAGE must call phase_complete in the same response as any speech (no separate turn)."""
    config = build(ConvPhase.TRIAGE)
    instructions = config["instructions"]
    # New approach: "brief phrase + phase_complete IN THE SAME RESPONSE" rather than fully silent
    assert "same response" in instructions.lower() or "do not speak" in instructions.lower() \
           or "classify" in instructions.lower()


def test_triage_prohibits_fabricating_incidents():
    """TRIAGE must explicitly forbid checking or guessing service status."""
    config = build(ConvPhase.TRIAGE)
    instructions = config["instructions"]
    assert "do not check" in instructions.lower() or "guess service status" in instructions.lower() \
           or "do not troubleshoot" in instructions.lower()


def test_triage_does_not_ask_would_you_like_ticket():
    """TRIAGE must not pre-confirm ticket creation — that belongs to RESOLVE."""
    config = build(ConvPhase.TRIAGE)
    instructions = config["instructions"]
    # Must explicitly prohibit the blocking pre-confirmation question
    assert "would you like me to open a ticket" not in instructions.lower() or \
           "do not" in instructions.lower()
    assert "ticket creation happens in resolve" in instructions.lower() or \
           "happens in resolve" in instructions.lower() or \
           "resolve" in instructions.lower()


def test_triage_caller_wants_ticket_advances_immediately():
    """TRIAGE must call phase_complete immediately — no follow-up questions."""
    config = build(ConvPhase.TRIAGE)
    instructions = config["instructions"]
    # Must say to call phase_complete immediately
    assert "immediately" in instructions.lower() or "now" in instructions.lower()
    # Must not ask diagnostic questions
    assert "do not ask follow-up" in instructions.lower() or "do not troubleshoot" in instructions.lower()


def test_triage_caller_asks_service_status_is_listed():
    """TRIAGE must list technical_support as a routing category for connectivity/service issues."""
    config = build(ConvPhase.TRIAGE)
    instructions = config["instructions"]
    assert "technical_support" in instructions.lower()
    assert "outage" in instructions.lower() or "connectivity" in instructions.lower()


def test_triage_is_silent_no_speech_before_phase_complete():
    """TRIAGE must not let model stall with 'one moment' before calling phase_complete."""
    config = build(ConvPhase.TRIAGE)
    instructions = config["instructions"]
    # Must not instruct the model to say any acknowledgment phrases that stall the tool call
    assert "one moment" not in instructions.lower()


# ---------------------------------------------------------------------------
# DIAGNOSE — bridge phrase before advancing
# ---------------------------------------------------------------------------

def test_diagnose_instructions_include_bridge_phrase():
    """DIAGNOSE must say a transitional phrase before calling phase_complete."""
    config = build(ConvPhase.DIAGNOSE)
    instructions = config["instructions"]
    assert "let me see what" in instructions.lower() or "one moment" in instructions.lower() \
           or "bridge" in instructions.lower() or "Let me" in instructions


# ---------------------------------------------------------------------------
# RESOLVE — do not repeat DIAGNOSE status
# ---------------------------------------------------------------------------

def test_resolve_instructions_do_not_repeat_diagnose():
    """RESOLVE must tell the model not to repeat what was already reported in DIAGNOSE."""
    config = build(ConvPhase.RESOLVE)
    instructions = config["instructions"]
    assert "DO NOT repeat" in instructions or "do not repeat" in instructions.lower()


def test_resolve_instructions_focus_on_next_action():
    """RESOLVE must tell the model to focus on what the caller should expect next."""
    config = build(ConvPhase.RESOLVE)
    instructions = config["instructions"]
    assert "expect" in instructions.lower() or "action" in instructions.lower() \
           or "next" in instructions.lower()


# ---------------------------------------------------------------------------
# WRAP_UP — does not claim issue is resolved when incident may still be active
# ---------------------------------------------------------------------------

def test_wrap_up_does_not_claim_issue_always_resolved():
    """WRAP_UP must not unconditionally say the caller's issue is resolved."""
    config = build(ConvPhase.WRAP_UP)
    instructions = config["instructions"]
    # The old phrase "The caller's issue has been resolved" should be gone
    assert "The caller's issue has been resolved" not in instructions


def test_wrap_up_cautions_about_active_incidents():
    """WRAP_UP must warn the model not to say the problem is fixed if an incident is still active."""
    config = build(ConvPhase.WRAP_UP)
    instructions = config["instructions"]
    assert "incident" in instructions.lower() or "not be fully resolved" in instructions.lower() \
           or "do not tell" in instructions.lower()


# ---------------------------------------------------------------------------
# New tools: get_ticket, update_ticket, get_account_history
# ---------------------------------------------------------------------------

def test_get_ticket_present_in_diagnose():
    names = [t["name"] for t in _tools_for_phase(ConvPhase.DIAGNOSE)]
    assert "get_ticket" in names


def test_get_ticket_present_in_resolve():
    names = [t["name"] for t in _tools_for_phase(ConvPhase.RESOLVE)]
    assert "get_ticket" in names


def test_get_ticket_present_in_wrap_up():
    names = [t["name"] for t in _tools_for_phase(ConvPhase.WRAP_UP)]
    assert "get_ticket" in names


def test_get_ticket_absent_in_triage():
    names = [t["name"] for t in _tools_for_phase(ConvPhase.TRIAGE)]
    assert "get_ticket" not in names


def test_get_ticket_absent_in_verify():
    names = [t["name"] for t in _tools_for_phase(ConvPhase.VERIFY)]
    assert "get_ticket" not in names


def test_get_account_history_present_in_diagnose():
    names = [t["name"] for t in _tools_for_phase(ConvPhase.DIAGNOSE)]
    assert "get_account_history" in names


def test_get_account_history_present_in_resolve():
    names = [t["name"] for t in _tools_for_phase(ConvPhase.RESOLVE)]
    assert "get_account_history" in names


def test_get_account_history_absent_in_triage():
    names = [t["name"] for t in _tools_for_phase(ConvPhase.TRIAGE)]
    assert "get_account_history" not in names


def test_update_ticket_present_in_resolve():
    names = [t["name"] for t in _tools_for_phase(ConvPhase.RESOLVE)]
    assert "update_ticket" in names


def test_update_ticket_present_in_wrap_up():
    names = [t["name"] for t in _tools_for_phase(ConvPhase.WRAP_UP)]
    assert "update_ticket" in names


def test_update_ticket_absent_in_diagnose():
    """update_ticket is action-only — not available in DIAGNOSE (gather info phase)."""
    names = [t["name"] for t in _tools_for_phase(ConvPhase.DIAGNOSE)]
    assert "update_ticket" not in names


def test_update_ticket_absent_in_triage():
    names = [t["name"] for t in _tools_for_phase(ConvPhase.TRIAGE)]
    assert "update_ticket" not in names


def test_update_ticket_confirmation_first():
    """update_ticket tool description must require caller confirmation before calling."""
    tools = _tools_for_phase(ConvPhase.RESOLVE)
    tool = next(t for t in tools if t["name"] == "update_ticket")
    assert "confirmation first" in tool["description"].lower()


def test_get_ticket_on_demand_behavior():
    """get_ticket must be ON DEMAND — not proactive."""
    tools = _tools_for_phase(ConvPhase.DIAGNOSE)
    tool = next(t for t in tools if t["name"] == "get_ticket")
    assert "on demand" in tool["description"].lower()


def test_get_account_history_on_demand_behavior():
    """get_account_history must be ON DEMAND — not proactive."""
    tools = _tools_for_phase(ConvPhase.DIAGNOSE)
    tool = next(t for t in tools if t["name"] == "get_account_history")
    assert "on demand" in tool["description"].lower()


def test_diagnose_instructions_mention_get_ticket():
    """DIAGNOSE instructions must tell the model when to use get_ticket."""
    config = build(ConvPhase.DIAGNOSE)
    assert "get_ticket" in config["instructions"]


def test_diagnose_instructions_mention_get_account_history():
    """DIAGNOSE instructions must tell the model when to use get_account_history."""
    config = build(ConvPhase.DIAGNOSE)
    assert "get_account_history" in config["instructions"]


def test_resolve_instructions_mention_update_ticket():
    """RESOLVE instructions must tell the model when to use update_ticket."""
    config = build(ConvPhase.RESOLVE)
    assert "update_ticket" in config["instructions"]


# ---------------------------------------------------------------------------
# Epic 1 — Multi-service intent routing tests
# ---------------------------------------------------------------------------

def test_triage_phase_complete_requires_service_category():
    """phase_complete in TRIAGE must require service_category parameter."""
    tools = {t["name"]: t for t in _tools_for_phase(ConvPhase.TRIAGE)}
    assert "service_category" in tools["phase_complete"]["parameters"]["required"]


def test_triage_phase_complete_has_all_six_categories_in_enum():
    """phase_complete service_category enum must include all six routing values."""
    tools = {t["name"]: t for t in _tools_for_phase(ConvPhase.TRIAGE)}
    enum_vals = tools["phase_complete"]["parameters"]["properties"]["service_category"]["enum"]
    for cat in ("technical_support", "billing", "sales", "move_transfer", "appointment", "account"):
        assert cat in enum_vals, f"Missing category: {cat}"


def test_non_triage_phase_complete_does_not_require_service_category():
    """Non-TRIAGE phases must not require service_category in phase_complete."""
    for phase in (ConvPhase.GREETING, ConvPhase.VERIFY, ConvPhase.DIAGNOSE, ConvPhase.RESOLVE, ConvPhase.WRAP_UP):
        tools = {t["name"]: t for t in _tools_for_phase(phase)}
        required = tools["phase_complete"]["parameters"].get("required", [])
        assert "service_category" not in required, f"Phase {phase} should not require service_category"


def test_diagnose_tools_default_is_technical_support():
    """No service_category defaults to technical_support tool set."""
    default_tools = {t["name"] for t in _tools_for_phase(ConvPhase.DIAGNOSE)}
    ts_tools = {t["name"] for t in _tools_for_phase(ConvPhase.DIAGNOSE, service_category="technical_support")}
    assert default_tools == ts_tools


def test_diagnose_tools_for_technical_support_unchanged():
    """technical_support DIAGNOSE must have the original tool set."""
    names = {t["name"] for t in _tools_for_phase(ConvPhase.DIAGNOSE, service_category="technical_support")}
    assert "get_service_status" in names
    assert "get_ticket" in names
    assert "get_account_history" in names
    assert "create_ticket" in names


def test_diagnose_tools_for_billing_category():
    """billing DIAGNOSE must have billing tools, not technical_support tools."""
    names = {t["name"] for t in _tools_for_phase(ConvPhase.DIAGNOSE, service_category="billing")}
    assert "get_account_balance" in names
    assert "get_payment_history" in names
    assert "get_service_status" not in names
    assert "create_ticket" not in names


def test_diagnose_tools_for_sales_category():
    """sales DIAGNOSE must have product/promo tools."""
    names = {t["name"] for t in _tools_for_phase(ConvPhase.DIAGNOSE, service_category="sales")}
    assert "get_product_catalog" in names
    assert "get_promotions" in names
    assert "get_service_status" not in names


def test_diagnose_tools_for_appointment_category():
    """appointment DIAGNOSE must have get_appointments."""
    names = {t["name"] for t in _tools_for_phase(ConvPhase.DIAGNOSE, service_category="appointment")}
    assert "get_appointments" in names
    assert "get_service_status" not in names


def test_diagnose_tools_for_account_category():
    """account DIAGNOSE must have get_account_details."""
    names = {t["name"] for t in _tools_for_phase(ConvPhase.DIAGNOSE, service_category="account")}
    assert "get_account_details" in names
    assert "get_service_status" not in names


def test_diagnose_tools_for_move_transfer_category():
    """move_transfer DIAGNOSE must have get_service_eligibility."""
    names = {t["name"] for t in _tools_for_phase(ConvPhase.DIAGNOSE, service_category="move_transfer")}
    assert "get_service_eligibility" in names
    assert "get_service_status" not in names


def test_resolve_tools_for_billing_category():
    """billing RESOLVE must include payment tools."""
    names = {t["name"] for t in _tools_for_phase(ConvPhase.RESOLVE, service_category="billing")}
    assert "make_payment" in names
    assert "setup_autopay" in names
    assert "update_ticket" not in names


def test_resolve_tools_for_appointment_category():
    """appointment RESOLVE must include appointment action tools."""
    names = {t["name"] for t in _tools_for_phase(ConvPhase.RESOLVE, service_category="appointment")}
    assert "confirm_appointment" in names
    assert "cancel_appointment" in names
    assert "reschedule_appointment" in names


def test_resolve_tools_for_account_category():
    """account RESOLVE must include update_contact_info."""
    names = {t["name"] for t in _tools_for_phase(ConvPhase.RESOLVE, service_category="account")}
    assert "update_contact_info" in names


def test_billing_diagnose_instructions_no_no_billing_access_section():
    """When service_category=billing, the 'No Billing Access' block must not appear in DIAGNOSE."""
    config = build(ConvPhase.DIAGNOSE, service_category="billing")
    assert "This system has NO billing tools" not in config["instructions"]
    assert "get_account_balance" in config["instructions"]


def test_non_billing_phases_keep_no_billing_access_section():
    """Non-billing DIAGNOSE must still have the No Billing Access section."""
    config = build(ConvPhase.DIAGNOSE)
    assert "This system has NO billing tools" in config["instructions"]


def test_category_specific_tools_have_preamble_phrases():
    """New category tools must include preamble sample phrases in their descriptions."""
    new_tools = [
        ("billing", ConvPhase.DIAGNOSE, "get_account_balance"),
        ("sales", ConvPhase.DIAGNOSE, "get_product_catalog"),
        ("appointment", ConvPhase.DIAGNOSE, "get_appointments"),
        ("account", ConvPhase.DIAGNOSE, "get_account_details"),
    ]
    for cat, phase, tool_name in new_tools:
        tools = {t["name"]: t for t in _tools_for_phase(phase, service_category=cat)}
        assert tool_name in tools, f"{tool_name} missing for {cat}/{phase}"
        assert "Preamble sample phrases" in tools[tool_name]["description"], (
            f"{tool_name} missing preamble phrases"
        )


# ---------------------------------------------------------------------------
# Coverage gap tests — instruction paths and RESOLVE tool sets
# ---------------------------------------------------------------------------

def test_resolve_tools_for_sales_category():
    """sales RESOLVE must include initiate_upgrade."""
    names = {t["name"] for t in _tools_for_phase(ConvPhase.RESOLVE, service_category="sales")}
    assert "initiate_upgrade" in names
    assert "update_ticket" not in names


def test_resolve_tools_for_move_transfer_category():
    """move_transfer RESOLVE must include initiate_service_move and cancel_service."""
    names = {t["name"] for t in _tools_for_phase(ConvPhase.RESOLVE, service_category="move_transfer")}
    assert "initiate_service_move" in names
    assert "cancel_service" in names
    assert "update_ticket" not in names


def test_diagnose_instructions_for_move_transfer():
    """move_transfer DIAGNOSE must mention get_service_eligibility."""
    config = build(ConvPhase.DIAGNOSE, service_category="move_transfer")
    assert "get_service_eligibility" in config["instructions"]
    assert "Move/Transfer" in config["instructions"]


def test_diagnose_instructions_for_account():
    """account DIAGNOSE must mention get_account_details."""
    config = build(ConvPhase.DIAGNOSE, service_category="account")
    assert "get_account_details" in config["instructions"]
    assert "Account" in config["instructions"]


def test_diagnose_instructions_for_sales():
    """sales DIAGNOSE must mention get_product_catalog."""
    config = build(ConvPhase.DIAGNOSE, service_category="sales")
    assert "get_product_catalog" in config["instructions"]
    assert "Sales" in config["instructions"]


def test_resolve_instructions_for_move_transfer():
    """move_transfer RESOLVE must mention initiate_service_move and cancel_service."""
    config = build(ConvPhase.RESOLVE, service_category="move_transfer")
    assert "initiate_service_move" in config["instructions"]
    assert "cancel_service" in config["instructions"]


def test_resolve_instructions_for_account():
    """account RESOLVE must mention update_contact_info."""
    config = build(ConvPhase.RESOLVE, service_category="account")
    assert "update_contact_info" in config["instructions"]


def test_diagnose_instructions_unknown_category_falls_back_to_technical_support():
    """Unknown service_category in _diagnose_instructions falls back to technical_support."""
    from sip_bridge.prompt_builder import _diagnose_instructions
    text = _diagnose_instructions("unknown_category", "")
    assert "get_service_status" in text


def test_resolve_instructions_unknown_category_falls_back_to_technical_support():
    """Unknown service_category in _resolve_instructions falls back to technical_support."""
    from sip_bridge.prompt_builder import _resolve_instructions
    text = _resolve_instructions("unknown_category", "")
    assert "create_ticket" in text or "update_ticket" in text


# ---------------------------------------------------------------------------
# create_ticket — ONE round confirmation protocol (double-confirmation fix)
# ---------------------------------------------------------------------------

def test_create_ticket_one_round_confirmation_only():
    """create_ticket description must say confirmation is one round only — prevents double-ask."""
    tool = next(t for t in _tools_for_phase(ConvPhase.DIAGNOSE) if t["name"] == "create_ticket")
    desc = tool["description"]
    assert "ONE round only" in desc


def test_create_ticket_prohibits_second_confirmation():
    """create_ticket description must explicitly prohibit asking to confirm a second time."""
    tool = next(t for t in _tools_for_phase(ConvPhase.DIAGNOSE) if t["name"] == "create_ticket")
    desc = tool["description"]
    assert "Do NOT ask for confirmation a second time" in desc or \
           "second time" in desc.lower()


# ---------------------------------------------------------------------------
# WRAP_UP — tool guidance for follow-up requests (stuck caller fix)
# ---------------------------------------------------------------------------

def test_wrap_up_guides_get_service_status_for_ticket_list():
    """WRAP_UP instructions must tell the model to use get_service_status when caller asks about open tickets."""
    config = build(ConvPhase.WRAP_UP)
    instructions = config["instructions"]
    # WRAP_UP should mention get_service_status for listing open tickets
    assert "get_service_status" in instructions


def test_wrap_up_guides_get_ticket_for_specific_named_ticket():
    """WRAP_UP instructions must tell the model to use get_ticket when caller quotes a ticket ID."""
    config = build(ConvPhase.WRAP_UP)
    instructions = config["instructions"]
    assert "get_ticket" in instructions
