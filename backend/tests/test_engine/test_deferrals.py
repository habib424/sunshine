from datetime import datetime

import pandas as pd

from app.engine.deferrals import (
    DeferralAnalysis,
    _add_years,
    _parse_named_values,
    _parse_year_offset,
    _roles_for_header_row,
    analyze_deferral_workbook,
    apply_user_message_to_analysis,
    intent_to_direction,
    transform_deferrals_to_light_je,
)


def test_wide_deferral_schedule_header_is_detected():
    roles, confidence = _roles_for_header_row(
        ["Date", None, "Customer", "INV Number", "b/fwd", datetime(2026, 8, 1)],
        "revenue",
    )

    assert roles["amount"] == "b/fwd"
    assert roles["description"] == "Customer"
    assert roles["source_reference"] == "INV Number"
    assert "release_end_date" not in roles
    assert confidence["amount"] == 0.98


def test_one_loose_message_yields_separate_facts():
    message = (
        "currency is GBP template is Deferred Revenue starting date is as of "
        "01-08-2026 entity causalens, ending date is same as per the date + 1 year"
    )

    values = _parse_named_values(message)

    assert values == {
        "currency": "GBP",
        "release_template": "Deferred Revenue",
        "release_start_date": "01-08-2026",
        "entity": "causalens",
        "release_end_date": "same as per the date + 1 year",
    }
    assert _parse_year_offset(values["release_end_date"]) == 1


def test_constant_accounts_and_relative_end_date_can_make_analysis_ready():
    analysis = DeferralAnalysis(
        direction="revenue",
        roles={
            "amount": "b/fwd",
            "description": "Customer",
            "source_reference": "INV Number",
        },
        facts={
            "entity": "causalens",
            "currency": "GBP",
            "posting_date": datetime(2026, 8, 1),
            "deferral_account": 2200,
            "release_account": 4000,
            "release_end_offset_years": 1,
        },
    )

    assert analysis.ready
    assert _add_years(datetime(2024, 2, 29), 1) == datetime(2025, 2, 28)

def test_conversation_combines_schedules_and_stops_asking_answered_questions(tmp_path):
    workbook_path = tmp_path / "Deferred Income UK.xlsx"
    columns = ["Date", "Region", "Customer", "INV Number", "b/fwd", datetime(2026, 8, 1)]
    main = pd.DataFrame(
        [[datetime(2026, 1, 1), "UK", "Customer A", "INV-1", 100.0, -10.0]],
        columns=columns,
    )
    professional_services = pd.DataFrame(
        [[datetime(2026, 2, 1), "UK", "Customer B", "INV-2", 50.0, -5.0]],
        columns=columns,
    )
    with pd.ExcelWriter(workbook_path, engine="openpyxl") as writer:
        main.to_excel(writer, sheet_name="Deferred Revenue", index=False)
        professional_services.to_excel(writer, sheet_name="Deferred Revenue - PS", index=False)

    direction = intent_to_direction("migrate_deferrals_to_light_je", workbook_path)
    analysis = analyze_deferral_workbook(workbook_path, direction)
    assert direction == "revenue"
    assert [source["sheet"] for source in analysis.source_sheets] == [
        "Deferred Revenue",
        "Deferred Revenue - PS",
    ]
    assert analysis.valid_source_rows == 2
    assert analysis.facts["currency"] == "GBP"

    analysis = apply_user_message_to_analysis(
        analysis,
        "currency is GBP template is Deferred Revenue starting date is as of "
        "01-08-2026 entity causalens, ending date is the start + 1 year",
        workbook_path,
    )
    assert analysis.facts["entity"] == "causalens"
    assert analysis.facts["release_end_offset_years"] == 1
    assert analysis.questions == [
        "Which Light deferred-income liability account and revenue account should I use?"
    ]

    analysis = apply_user_message_to_analysis(
        analysis,
        "Use 2200 for the balance sheet and 4000 for income",
        workbook_path,
    )
    assert analysis.ready
    assert analysis.questions == []
    assert analysis.facts["deferral_account"] == 2200
    assert analysis.facts["release_account"] == 4000

    output = transform_deferrals_to_light_je(workbook_path, analysis)
    assert len(output) == 4
    assert output["Document Number"].nunique() == 2
    assert output["Debit"].fillna(0).sum() == output["Credit"].fillna(0).sum() == 150.0
