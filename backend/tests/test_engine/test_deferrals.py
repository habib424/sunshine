from datetime import datetime

from app.engine.deferrals import (
    DeferralAnalysis,
    _add_years,
    _parse_named_values,
    _parse_year_offset,
    _roles_for_header_row,
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

