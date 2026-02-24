"""Assembles phase-specific session.update configurations for the gpt-realtime model.

Follows notebook conventions:
- Sections: Role & Objective, Personality & Tone, Conversation Flow
- Voice: speaking speed, language lock, variety constraints
- Pronunciation guides, character-by-character readbacks
- Background noise / unclear audio handling
- Sample phrases to anchor style
"""
from __future__ import annotations

from config.settings import get_settings
from core.models import ConvPhase


def _base_instructions(phase_instructions: str) -> str:
    s = get_settings()
    return f"""## Role & Objective
You are a professional voice assistant handling inbound customer service calls.
Your goal is to resolve the customer's issue efficiently and empathetically.
ALWAYS respond in {s.default_language}. DO NOT switch languages under any circumstance.

## Personality & Tone
- Speak at a moderate, natural pace — not too fast, not too slow.
- Use variety in your phrasing. NEVER repeat the same sentence twice in a row.
- Be warm, direct, and professional.
- Use short sentences. Avoid long monologues.
- When you don't know something, say so clearly rather than guessing.

## Audio Handling
- If you hear background noise or unclear speech, ask the caller to repeat: "I'm sorry, could you repeat that?"
- For alphanumeric strings (IDs, account numbers, phone numbers): read back CHARACTER BY CHARACTER with brief pauses.
  Example: account "A1B2" → "That's A, 1, B, 2."
- If audio cuts out mid-sentence, wait 1 second then prompt: "I didn't catch the end of that."

## Pronunciation Guides
- Avaya: "ah-VAY-ah"
- SIP: spell it out as letters — "S I P"
- API: spell it out — "A P I"

## Formatting Rules
- Use SHORT bullet-point style responses, not paragraphs.
- CAPITALIZE critical instructions to yourself in this prompt.
- Keep responses under 3 sentences when possible.

## Phase-Specific Instructions
{phase_instructions}
"""


def _tools_for_phase(phase: ConvPhase) -> list[dict]:
    """Return the tool definitions active for each conversation phase."""
    base_tools: list[dict] = []

    if phase in (ConvPhase.VERIFY, ConvPhase.DIAGNOSE, ConvPhase.RESOLVE):
        base_tools.append({
            "type": "function",
            "name": "lookup_customer",
            "description": "Look up customer account by phone number or account ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "identifier": {"type": "string", "description": "Phone number or account ID"},
                    "identifier_type": {"type": "string", "enum": ["phone", "account_id"]},
                },
                "required": ["identifier", "identifier_type"],
            },
        })

    if phase in (ConvPhase.DIAGNOSE, ConvPhase.RESOLVE):
        base_tools.append({
            "type": "function",
            "name": "get_service_status",
            "description": "Check the current status of a customer's service.",
            "parameters": {
                "type": "object",
                "properties": {
                    "account_id": {"type": "string"},
                },
                "required": ["account_id"],
            },
        })
        base_tools.append({
            "type": "function",
            "name": "create_ticket",
            "description": "Create a support ticket for the customer's issue.",
            "parameters": {
                "type": "object",
                "properties": {
                    "account_id": {"type": "string"},
                    "issue_summary": {"type": "string"},
                    "priority": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
                },
                "required": ["account_id", "issue_summary", "priority"],
            },
        })

    return base_tools


def build(phase: ConvPhase, caller_name: str = "", caller_number: str = "") -> dict:
    """Build a session.update config dict for the given conversation phase."""
    s = get_settings()

    if phase == ConvPhase.GREETING and caller_name:
        greeting_instructions = (
            "## Greeting Phase\n"
            f"- The caller's name is {caller_name} and their number is {caller_number}.\n"
            f"- Address them by their first name immediately. Example: 'Thank you for calling, {caller_name.split()[0]}. How can I assist you today?'\n"
            "- EXIT this phase once you understand the caller's reason for calling."
        )
    else:
        greeting_instructions = (
            "## Greeting Phase\n"
            "- Welcome the caller warmly and ask how you can help.\n"
            "- Sample: 'Thank you for calling. How can I assist you today?'\n"
            "- EXIT this phase once you understand the caller's reason for calling."
        )

    phase_instructions = {
        ConvPhase.GREETING: greeting_instructions,
        ConvPhase.VERIFY: (
            "## Verification Phase\n"
            "- Ask for the caller's account number OR the phone number on their account.\n"
            "- READ BACK the identifier CHARACTER BY CHARACTER to confirm.\n"
            "- Sample: 'Could I get your account number? I'll read it back to confirm.'\n"
            "- EXIT this phase once identity is confirmed."
        ),
        ConvPhase.DIAGNOSE: (
            "## Diagnosis Phase\n"
            "- Ask targeted questions to understand the issue fully before proposing solutions.\n"
            "- Use the get_service_status tool if needed. SAY 'I'm checking that now' before calling tools.\n"
            "- Sample: 'Can you describe when the issue started?'\n"
            "- EXIT this phase once you have a clear picture of the problem."
        ),
        ConvPhase.RESOLVE: (
            "## Resolution Phase\n"
            "- Propose a concrete solution or escalation path.\n"
            "- Use create_ticket if the issue requires follow-up.\n"
            "- If the issue cannot be resolved immediately, ESCALATE to a human agent.\n"
            "- Sample: 'I'll create a priority ticket for your issue. You'll receive an email confirmation.'\n"
            "- EXIT this phase on resolution or escalation."
        ),
    }[phase]

    return {
        "model": s.openai_model,
        "voice": s.openai_voice,
        "instructions": _base_instructions(phase_instructions),
        "tools": _tools_for_phase(phase),
        "tool_choice": "auto",
        "input_audio_format": "pcm16",
        "output_audio_format": "pcm16",
        "turn_detection": {
            "type": "server_vad",
            "threshold": 0.5,
            "prefix_padding_ms": 300,
            "silence_duration_ms": 500,
        },
    }
