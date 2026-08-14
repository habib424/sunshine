"""Tests for the deterministic open AP -> Light Bills (AP) engine.

The fixture replicates the real "AP Causalens.xlsx": a QuickBooks-style
A/P Aging Detail report grouped by vendor, plus a filled-in Bills (AP)
template sheet whose single row is the worked example for invoice 3428325.

The sign rule under test: a positive open balance is an invoice (bill), a
negative one is a credit note in the same format. Nothing is netted, dropped,
or held back, so the output always ties to the report's own grand total.
"""

from datetime import datetime

import pytest

from app.engine.bills_ap import (
    LIGHT_BILLS_AP_COLUMNS,
    analyze_bills_ap_workbook,
    apply_structured_updates_to_analysis,
    format_bills_ap_analysis_message,
    transform_open_ap_to_light_bills,
)

_HEADER = [
    "Vendor",
    "Transaction Type",
    "Date",
    "Document Number",
    "Due Date",
    "Age",
    "Open Balance",
]

# (vendor group label, [(txn type, date, doc no, due date, age, amount)])
_DEFAULT_GROUPS = [
    (
        "S002 Addison Lee",
        [("Bill", datetime(2026, 7, 31), "3428325", datetime(2026, 7, 31), 13, 319.01)],
    ),
    (
        "S174 Adobe UK",
        [
            ("Bill Payment", datetime(2026, 4, 11), "2241", datetime(2026, 4, 11), 124, -28.64),
            ("Bill Payment", datetime(2026, 5, 11), "2351", datetime(2026, 5, 11), 94, -28.64),
        ],
    ),
    (
        "S464 N2Growth Ltd",
        [
            ("Bill", datetime(2026, 7, 1), "INV-0176", datetime(2026, 7, 17), 27, 7500.0),
            ("Bill Payment", datetime(2026, 7, 21), "3241", datetime(2026, 7, 21), 23, -7500.0),
        ],
    ),
    (
        "S540 NDH Advisory LLC",
        [("Bill", datetime(2026, 7, 22), "May/July 26", datetime(2026, 7, 22), 22, 1307.62)],
    ),
]

_EXAMPLE_ROW = {
    "Vendor": "Addison Lee",
    "Entity": "causaLens",
    "Invoice Number": "3428325",
    "Issue Date": datetime(2026, 7, 31),
    "Due Date": datetime(2026, 7, 31),
    "Currency": "GBP",
    "Description": "Data migration - 3428325",
    "Invoice Currency Amount": 319.01,
    "Local Currency Amount": 319.01,
}


def _ap_workbook(path, *, with_template=True, groups=_DEFAULT_GROUPS, total_amounts=True):
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "AP"

    ws.append(["causaLens"])
    ws.append(["A/P Aging Detail"])
    ws.append(["As of 31 July 2026"])
    ws.append([None])
    ws.append([None])
    ws.append(_HEADER)
    ws.append(["Supplier"])

    grand_total = 0.0
    for label, rows in groups:
        ws.append([label])
        subtotal = 0.0
        for txn_type, date, doc, due, age, amount in rows:
            ws.append([None, txn_type, date, doc, due, age, amount])
            subtotal += amount
        total_cell = round(subtotal, 2) if total_amounts else None
        ws.append([f"Total - {label}", None, None, None, None, None, total_cell])
        grand_total += subtotal
    grand_cell = round(grand_total, 2) if total_amounts else None
    ws.append(["Total - Supplier", None, None, None, None, None, grand_cell])
    ws.append(["Total", None, None, None, None, None, grand_cell])

    if with_template:
        template = wb.create_sheet("Bills (AP)")
        template.append(LIGHT_BILLS_AP_COLUMNS)
        template.append([_EXAMPLE_ROW.get(col) for col in LIGHT_BILLS_AP_COLUMNS])

    wb.save(path)
    return path


def _row_by_invoice(df, invoice_number):
    matches = df[df["Invoice Number"] == invoice_number]
    assert len(matches) == 1, f"expected exactly one row for {invoice_number}"
    return matches.iloc[0]


def test_detects_grouped_report_template_and_facts(tmp_path):
    path = _ap_workbook(tmp_path / "AP example.xlsx")
    analysis = analyze_bills_ap_workbook(path)

    assert analysis.source_sheet == "AP"
    assert analysis.template_sheet == "Bills (AP)"
    assert analysis.layout == "grouped"
    assert analysis.roles["vendor_name"] == "Vendor"
    assert analysis.roles["invoice_number"] == "Document Number"
    assert analysis.roles["open_amount"] == "Open Balance"

    # Facts come from the worked example, not the title block.
    assert analysis.facts["entity"] == "causaLens"
    assert analysis.facts["currency"] == "GBP"
    assert analysis.facts["description_prefix"] == "Data migration"

    assert analysis.detail_rows == 6
    assert analysis.invoice_rows == 3
    assert analysis.credit_note_rows == 3
    assert analysis.invoice_total == pytest.approx(9126.63)
    assert analysis.credit_note_total == pytest.approx(-7557.28)
    assert analysis.source_total == pytest.approx(1569.35)
    assert analysis.reported_total == pytest.approx(1569.35)
    assert analysis.ready
    assert not analysis.questions


def test_every_open_item_keeps_its_sign(tmp_path):
    path = _ap_workbook(tmp_path / "AP example.xlsx")
    analysis = analyze_bills_ap_workbook(path)
    df = transform_open_ap_to_light_bills(path, analysis)

    assert list(df.columns) == LIGHT_BILLS_AP_COLUMNS
    # 6 open items -> 6 rows. Nothing netted, dropped, or held back.
    assert len(df) == 6
    assert df["Invoice Currency Amount"].sum() == pytest.approx(1569.35)

    bill = _row_by_invoice(df, "3428325")
    assert bill["Vendor"] == "Addison Lee"
    assert bill["Vendor ID"] == "S002"
    assert bill["Entity"] == "causaLens"
    assert bill["Issue Date"] == "2026-07-31"
    assert bill["Currency"] == "GBP"
    assert bill["Description"] == "Data migration - 3428325"
    assert bill["Invoice Currency Amount"] == pytest.approx(319.01)
    assert bill["Account"] is None

    # An unapplied payment is a credit note: same format, negative amount.
    credit_note = _row_by_invoice(df, "2241")
    assert credit_note["Vendor"] == "Adobe UK"
    assert credit_note["Invoice Currency Amount"] == pytest.approx(-28.64)
    assert credit_note["Local Currency Amount"] == pytest.approx(-28.64)

    # A vendor whose balance nets to zero still keeps both open items.
    assert _row_by_invoice(df, "INV-0176")["Invoice Currency Amount"] == pytest.approx(7500.0)
    assert _row_by_invoice(df, "3241")["Invoice Currency Amount"] == pytest.approx(-7500.0)

    # Text invoice numbers must never be coerced to numbers or dates.
    assert _row_by_invoice(df, "May/July 26")["Invoice Number"] == "May/July 26"

    message = format_bills_ap_analysis_message(analysis)
    assert "credit note" in message.lower()


def test_subtotals_and_grand_total_are_never_bills(tmp_path):
    path = _ap_workbook(tmp_path / "AP example.xlsx")
    analysis = analyze_bills_ap_workbook(path)
    df = transform_open_ap_to_light_bills(path, analysis)

    assert not any(str(v).lower().startswith("total") for v in df["Vendor"])
    assert not any(str(v).lower() == "supplier" for v in df["Vendor"])
    # Grand total is kept as the reconciliation anchor instead.
    assert analysis.reported_total == pytest.approx(1569.35)


def test_amountless_subtotals_still_detect_grouped_layout(tmp_path):
    # Re-saved workbooks can lose formula caches, leaving "Total - ..." rows
    # with a label and no amount. Those are subtotals, not vendor headers.
    path = _ap_workbook(tmp_path / "AP no cached totals.xlsx", total_amounts=False)
    analysis = analyze_bills_ap_workbook(path)

    assert analysis.layout == "grouped"
    assert analysis.detail_rows == 6
    assert analysis.reported_total is None  # no anchor without cached totals

    df = transform_open_ap_to_light_bills(path, analysis)
    assert len(df) == 6
    assert df["Invoice Currency Amount"].sum() == pytest.approx(1569.35)


def test_entity_comes_from_title_block_without_template(tmp_path):
    path = _ap_workbook(tmp_path / "AP no template.xlsx", with_template=False)
    analysis = analyze_bills_ap_workbook(path)

    assert analysis.facts["entity"] == "causaLens"
    # The report has no currency column and no worked example to take it from.
    assert not analysis.facts.get("currency")
    assert not analysis.ready
    assert any("currency" in q.lower() for q in analysis.questions)


def test_structured_updates_set_currency_and_split(tmp_path):
    path = _ap_workbook(tmp_path / "AP no template.xlsx", with_template=False)
    analysis = analyze_bills_ap_workbook(path)

    updated = apply_structured_updates_to_analysis(
        analysis,
        {"currency": "GBP", "split_vendor_code": "false"},
        path,
    )
    assert updated.facts["currency"] == "GBP"
    assert updated.ready

    df = transform_open_ap_to_light_bills(path, updated)
    row = _row_by_invoice(df, "3428325")
    assert row["Vendor"] == "S002 Addison Lee"
    assert row["Vendor ID"] is None
    assert row["Currency"] == "GBP"


def test_rejects_unknown_structured_update(tmp_path):
    path = _ap_workbook(tmp_path / "AP example.xlsx")
    analysis = analyze_bills_ap_workbook(path)

    with pytest.raises(ValueError, match="Unsupported bills AP updates"):
        apply_structured_updates_to_analysis(analysis, {"posting_date": "2026-07-31"}, path)
