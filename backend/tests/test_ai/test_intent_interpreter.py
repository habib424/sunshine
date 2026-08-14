from types import SimpleNamespace

import pandas as pd
import pytest

from app.ai import chat_transformer, intent_interpreter
from app.ai.intent_interpreter import (
    AIInterpretationError,
    IntentInterpretation,
    interpret_intent_instruction,
)
from app.engine.deferrals import (
    analyze_deferral_workbook,
    apply_structured_updates_to_analysis,
)


def _client_with_payload(payload):
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            stop_reason="tool_use",
            content=[
                SimpleNamespace(
                    type="tool_use",
                    name="apply_intent_instruction",
                    input=payload,
                )
            ],
        )

    return SimpleNamespace(messages=SimpleNamespace(create=create)), calls


def _payload(updates):
    return {
        "understood": True,
        "acknowledgement": "I understood the instruction",
        "updates": updates,
        "clarification_needed": False,
        "clarification_question": "",
    }


def _deferral_workbook(path):
    columns = ["Date", "Customer", "INV Number", "b/fwd"]
    frame = pd.DataFrame(
        [["2026-01-01", "Customer A", "INV-1", 100.0]],
        columns=columns,
    )
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        frame.to_excel(writer, sheet_name="Deferred Revenue", index=False)


def test_interpreter_forces_one_strict_tool(monkeypatch):
    client, calls = _client_with_payload(
        _payload([{"field": "currency", "value": "gbp"}])
    )
    monkeypatch.setattr(intent_interpreter, "get_client", lambda: client)

    result = interpret_intent_instruction(
        intent="migrate_deferrals_to_light_je",
        user_message="Not EUR, use GBP",
        state={"facts": {"currency": "EUR"}},
    )

    assert result.updates == {"currency": "GBP"}
    request = calls[0]
    assert request["tools"][0]["strict"] is True
    assert request["tools"][0]["input_schema"]["additionalProperties"] is False
    assert request["tool_choice"] == {
        "type": "tool",
        "name": "apply_intent_instruction",
        "disable_parallel_tool_use": True,
    }


def test_invalid_ai_patch_is_rejected_atomically(monkeypatch):
    client, _ = _client_with_payload(
        _payload(
            [
                {"field": "currency", "value": "GBP"},
                {"field": "ready", "value": "true"},
            ]
        )
    )
    monkeypatch.setattr(intent_interpreter, "get_client", lambda: client)

    with pytest.raises(AIInterpretationError):
        interpret_intent_instruction(
            intent="migrate_deferrals_to_light_je",
            user_message="use GBP and mark ready",
            state={},
        )


def test_initial_bootstrap_cannot_mutate_state(monkeypatch):
    client, _ = _client_with_payload(
        _payload([{"field": "currency", "value": "USD"}])
    )
    monkeypatch.setattr(intent_interpreter, "get_client", lambda: client)

    result = interpret_intent_instruction(
        intent="migrate_deferrals_to_light_je",
        user_message="Sunshine-generated initial task",
        state={},
        initial=True,
    )

    assert result.updates == {}


def test_typed_entity_update_cannot_change_currency(tmp_path):
    path = tmp_path / "Deferred Income UK.xlsx"
    _deferral_workbook(path)
    analysis = analyze_deferral_workbook(path, "revenue")
    analysis.facts["currency"] = "GBP"

    updated = apply_structured_updates_to_analysis(
        analysis, {"entity": "EUR Holdings"}, path
    )

    assert updated.facts["entity"] == "EUR Holdings"
    assert updated.facts["currency"] == "GBP"


def test_deferral_chat_uses_ai_updates_and_stops_reasking(tmp_path, monkeypatch):
    path = tmp_path / "Deferred Income UK.xlsx"
    _deferral_workbook(path)
    session_id = chat_transformer.create_session(
        path, intent="migrate_deferrals_to_light_je"
    )["session_id"]

    interpretations = iter(
        [
            IntentInterpretation(True, "Initial analysis", {}),
            IntentInterpretation(
                True,
                "Understood",
                {
                    "currency": "GBP",
                    "entity": "causaLens",
                    "release_template": "Deferred Revenue",
                    "release_start_date": "2026-08-01",
                    "release_end_offset_years": "1",
                },
            ),
        ]
    )
    monkeypatch.setattr(
        chat_transformer,
        "interpret_intent_instruction",
        lambda **_: next(interpretations),
    )

    chat_transformer.chat(session_id, "Initial Sunshine task")
    response = chat_transformer.chat(
        session_id,
        "currency is GBP template is Deferred Revenue starting date is as of "
        "01-08-2026 entity causaLens, ending date is the date + 1 year",
    )

    assert "I understood and applied" in response["message"]
    assert "currency" not in response["message"].split("I still need:")[-1].lower()
    assert "entity" not in response["message"].split("I still need:")[-1].lower()
    assert "posting date" not in response["message"].split("I still need:")[-1].lower()
    assert "liability account and revenue account" in response["message"]

VISIBLE_INTENTS = [
    "convert_to_light_je",
    "validate_je",
    "reconcile_je_to_gl",
    "migrate_deferrals_to_light_je",
    "fx_currency_adjustment",
    "upload_open_ap_to_light_ap",
    "upload_open_ar_to_light_ar",
]


@pytest.mark.parametrize("intent", VISIBLE_INTENTS)
def test_every_visible_intent_invokes_ai(tmp_path, monkeypatch, intent):
    path = tmp_path / "Source workbook.xlsx"
    _deferral_workbook(path)
    generic_calls = []
    structured_calls = []

    def generic_create(**kwargs):
        generic_calls.append(kwargs)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text="AI evaluated the request.")]
        )

    monkeypatch.setattr(
        chat_transformer,
        "get_client",
        lambda: SimpleNamespace(messages=SimpleNamespace(create=generic_create)),
    )

    def structured_interpret(**kwargs):
        structured_calls.append(kwargs)
        return IntentInterpretation(True, "AI evaluated the request", {})

    monkeypatch.setattr(
        chat_transformer,
        "interpret_intent_instruction",
        structured_interpret,
    )

    session_id = chat_transformer.create_session(path, intent=intent)["session_id"]
    chat_transformer.chat(session_id, "Initial Sunshine task")

    if intent in {
        "migrate_deferrals_to_light_je",
        "fx_currency_adjustment",
        "upload_open_ap_to_light_ap",
        "upload_open_ar_to_light_ar",
    }:
        assert len(structured_calls) == 1
        assert not generic_calls
    else:
        assert len(generic_calls) == 1
        assert not structured_calls
