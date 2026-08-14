"""Strict AI interpretation for deterministic migration conversations.

The model is used only to understand the user's language.  It must return a
small, validated patch; the accounting engines remain responsible for
re-detection, readiness and execution.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.ai.client import get_client
from app.engine.deferrals import DEFERRAL_INTENTS
from app.engine.fx_adjustment import FX_ADJUSTMENT_INTENTS
from app.engine.invoices_ar import OPEN_AR_INTENTS
from app.engine.open_ap import OPEN_AP_INTENTS


_TOOL_NAME = "apply_intent_instruction"

_DEFERRAL_FIELDS = (
    "direction",
    "entity",
    "currency",
    "posting_date",
    "release_start_date",
    "release_end_date",
    "release_end_offset_years",
    "release_template",
    "description_prefix",
    "deferral_account",
    "release_account",
)
_OPEN_AP_JE_FIELDS = (
    "entity",
    "ledger",
    "local_currency",
    "posting_date",
    "ap_account",
    "clearing_account",
)
_OPEN_AP_BILLS_FIELDS = (
    "entity",
    "currency",
    "description_prefix",
    "split_vendor_code",
)
_OPEN_AR_FIELDS = (
    "entity",
    "currency",
    "description_prefix",
    "product",
    "split_customer_code",
    "include_po_number",
)
_FX_FIELDS = (
    "entity",
    "ledger",
    "local_currency",
    "posting_date",
    "document_year",
    "clearing_account",
)

_FIELD_DESCRIPTIONS = {
    "direction": "revenue for deferred income, or cost for prepaid expense",
    "entity": "exact Light entity/company name",
    "ledger": "exact ledger name",
    "currency": "three-letter ISO transaction currency",
    "local_currency": "three-letter ISO local currency",
    "posting_date": "ISO date YYYY-MM-DD",
    "release_start_date": "ISO date YYYY-MM-DD",
    "release_end_date": "ISO date YYYY-MM-DD",
    "release_end_offset_years": "whole number of years after the release start",
    "release_template": "exact Light release-template name",
    "description_prefix": "exact description prefix",
    "deferral_account": "deferred-income liability or prepaid-asset account code",
    "release_account": "revenue or expense account code",
    "ap_account": "accounts-payable liability account code",
    "clearing_account": "clearing/offset account code",
    "document_year": "four-digit document year",
    "split_vendor_code": "true or false; whether a leading vendor code is split from its name",
    "product": "exact Light product name used on every invoice line",
    "split_customer_code": "true or false; whether a leading customer code is split from its name",
    "include_po_number": "true or false; whether customer PO references are copied into PO Number",
}

_CANONICAL_LABELS = {
    "entity": "entity",
    "ledger": "ledger",
    "currency": "currency",
    "local_currency": "local currency",
    "posting_date": "posting date",
    "release_start_date": "release start date",
    "release_end_date": "release end date",
    "release_template": "release template",
    "description_prefix": "description prefix",
    "deferral_account": "deferral account",
    "release_account": "release account",
    "ap_account": "AP account",
    "clearing_account": "clearing account",
    "document_year": "document year",
    "product": "product",
}


class _Update(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str = Field(min_length=1, max_length=64)
    value: str = Field(min_length=1, max_length=300)


class _ToolPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    understood: bool
    acknowledgement: str = Field(min_length=1, max_length=600)
    updates: list[_Update] = Field(default_factory=list, max_length=20)
    clarification_needed: bool = False
    clarification_question: str = Field(default="", max_length=300)


@dataclass(frozen=True)
class IntentInterpretation:
    understood: bool
    acknowledgement: str
    updates: dict[str, str]
    clarification_question: str | None = None
    rejected_updates: tuple[str, ...] = ()


class AIInterpretationError(RuntimeError):
    """Raised when an instruction was not successfully evaluated by AI."""


def allowed_fields(intent: str, mode: str | None = None) -> tuple[str, ...]:
    if intent in DEFERRAL_INTENTS:
        return _DEFERRAL_FIELDS
    if intent in OPEN_AP_INTENTS:
        return _OPEN_AP_BILLS_FIELDS if mode == "bills" else _OPEN_AP_JE_FIELDS
    if intent in OPEN_AR_INTENTS:
        return _OPEN_AR_FIELDS
    if intent in FX_ADJUSTMENT_INTENTS:
        return _FX_FIELDS
    return ()


def interpret_intent_instruction(
    *,
    intent: str,
    user_message: str,
    state: dict[str, Any],
    mode: str | None = None,
    initial: bool = False,
) -> IntentInterpretation:
    """Ask Claude for a strict, allow-listed interpretation of one message."""
    fields = allowed_fields(intent, mode)
    if not fields:
        raise AIInterpretationError(f"No structured AI contract exists for intent '{intent}'")

    descriptions = "\n".join(f"- {name}: {_FIELD_DESCRIPTIONS[name]}" for name in fields)
    system = f"""You are Sunshine's language-understanding layer for an ERP migration.
You interpret language; deterministic code applies and validates the result.

Allowed update fields for this intent:
{descriptions}

Rules:
- Use the required tool and only the allowed fields.
- Extract only changes the user explicitly states or unambiguously confirms.
- Never invent account codes, entity names, currencies, dates, templates, sheets, or mappings.
- Normalize currencies to uppercase ISO codes and absolute dates to YYYY-MM-DD.
- Use release_end_offset_years for relative rules such as "one year after the start".
- For account descriptions, use the accounting meaning and current direction to choose the right allowed account field.
- Corrections override the current value (for example, "not EUR, use GBP" means GBP).
- If this is an initial, Sunshine-generated task message, return no updates. Summarize the detected state in the acknowledgement.
- The acknowledgement must say what you understood, without claiming that a change has already been applied.
- Do not repeat every outstanding question in the acknowledgement; the application adds verified blockers afterwards.
- If language is materially ambiguous, set clarification_needed and ask one concise question. Do not guess.
"""

    payload = {
        "intent": intent,
        "mode": mode,
        "initial_sunshine_message": initial,
        "message": user_message,
        "verified_current_state": state,
    }
    tool = {
        "name": _TOOL_NAME,
        "strict": True,
        "description": "Return the structured meaning of the user's migration instruction.",
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "understood",
                "acknowledgement",
                "updates",
                "clarification_needed",
                "clarification_question",
            ],
            "properties": {
                "understood": {"type": "boolean"},
                "acknowledgement": {"type": "string", "maxLength": 600},
                # The strict tool grammar rejects `maxItems` on arrays;
                # _ToolPayload enforces the size limit on the response instead.
                "updates": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["field", "value"],
                        "properties": {
                            "field": {"type": "string", "enum": list(fields)},
                            "value": {"type": "string", "maxLength": 300},
                        },
                    },
                },
                "clarification_needed": {"type": "boolean"},
                "clarification_question": {"type": "string", "maxLength": 300},
            },
        },
    }

    try:
        response = get_client().messages.create(
            model="claude-sonnet-5",
            max_tokens=1200,
            system=system,
            messages=[{"role": "user", "content": json.dumps(payload, default=str)}],
            tools=[tool],
            tool_choice={
                "type": "tool",
                "name": _TOOL_NAME,
                "disable_parallel_tool_use": True,
            },
        )
    except Exception as exc:  # surface failure; never silently bypass AI
        raise AIInterpretationError("The AI service could not evaluate the instruction") from exc

    tool_blocks = [block for block in response.content if getattr(block, "type", None) == "tool_use"]
    if getattr(response, "stop_reason", None) != "tool_use" or len(tool_blocks) != 1:
        raise AIInterpretationError("The AI did not return exactly one structured interpretation")
    tool_block = tool_blocks[0]
    if getattr(tool_block, "name", None) != _TOOL_NAME:
        raise AIInterpretationError("The AI returned the wrong structured tool")
    raw_payload: Any | None = getattr(tool_block, "input", None)
    if raw_payload is None:
        raise AIInterpretationError("The AI did not return a structured interpretation")

    try:
        parsed = _ToolPayload.model_validate(raw_payload)
    except Exception as exc:
        raise AIInterpretationError("The AI returned an invalid structured interpretation") from exc

    accepted: dict[str, str] = {}
    rejected: list[str] = []
    for update in parsed.updates:
        if update.field not in fields:
            rejected.append(f"Unsupported field: {update.field}")
            continue
        value = _normalise_value(update.field, update.value)
        if value is None:
            rejected.append(f"Invalid value for {update.field}")
            continue
        accepted[update.field] = value

    if rejected:
        raise AIInterpretationError("The AI returned an invalid update: " + "; ".join(rejected))

    clarification = parsed.clarification_question.strip() if parsed.clarification_needed else ""
    if initial or not parsed.understood or clarification:
        accepted = {}
    return IntentInterpretation(
        understood=parsed.understood,
        acknowledgement=parsed.acknowledgement.strip(),
        updates=accepted,
        clarification_question=clarification or None,
    )


def canonical_instruction(
    interpretation: IntentInterpretation,
    *,
    intent: str,
    mode: str | None = None,
) -> str:
    """Translate the validated patch into the engines' narrow update grammar."""
    parts: list[str] = []
    for field, value in interpretation.updates.items():
        clean = _clean_for_parser(value)
        if field == "direction":
            direction = "deferred revenue" if clean == "revenue" else "deferred cost"
            parts.append(f"this migration is {direction}")
        elif field == "release_end_offset_years":
            parts.append(f"release end date is +{clean} years")
        elif field == "split_vendor_code":
            if clean == "false":
                parts.append("do not split the vendor code")
            # The bills engine defaults to splitting; true needs no override.
        elif field == "split_customer_code":
            if clean == "false":
                parts.append("do not split the customer code")
            # The invoices engine defaults to splitting; true needs no override.
        elif field == "include_po_number":
            if clean == "false":
                parts.append("leave the PO number blank")
            # PO references are carried by default; true needs no override.
        else:
            label = _CANONICAL_LABELS.get(field)
            if label:
                parts.append(f"{label} is {clean}")
    return "; ".join(parts)


def _normalise_value(field: str, raw: str) -> str | None:
    value = raw.strip().strip("`'\"")
    if not value:
        return None
    if field in {"currency", "local_currency"}:
        value = value.upper()
        return value if re.fullmatch(r"[A-Z]{3}", value) else None
    if field in {"deferral_account", "release_account", "ap_account", "clearing_account"}:
        compact = re.sub(r"[\s.-]", "", value)
        return compact if re.fullmatch(r"\d{3,10}", compact) else None
    if field in {"posting_date", "release_start_date", "release_end_date"}:
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError:
            return None
    if field == "release_end_offset_years":
        match = re.fullmatch(r"\+?(\d{1,2})", value)
        return str(int(match.group(1))) if match and int(match.group(1)) > 0 else None
    if field == "document_year":
        return value if re.fullmatch(r"\d{4}", value) else None
    if field == "direction":
        lowered = value.lower()
        return lowered if lowered in {"revenue", "cost"} else None
    if field in {"split_vendor_code", "split_customer_code", "include_po_number"}:
        lowered = value.lower()
        return lowered if lowered in {"true", "false"} else None
    return value[:300]


def _clean_for_parser(value: str) -> str:
    return re.sub(r"[\r\n;,]+", " ", value).strip()

