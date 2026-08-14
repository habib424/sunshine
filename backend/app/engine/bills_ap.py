"""
Deterministic open AP upload to the Light "Bills (AP)" bill-document layout.

Unlike `open_ap.py`, which posts AP opening balances as debit/credit journal
lines, this engine produces actual bill documents: one row per outstanding
vendor invoice in Light's Bills (AP) upload template.

The sources seen so far are QuickBooks/Xero-style "A/P Aging Detail" reports,
which are *hierarchical* rather than tabular:

    causaLens                                      <- entity, title block
    A/P Aging Detail
    As of 31 July 2026

    Vendor | Transaction Type | Date | Document Number | Due Date | Age | Open Balance
    Supplier                                       <- section header
    S002 Addison Lee                               <- vendor group header
          | Bill | 2026-07-31 | 3428325 | ... | 319.01     <- detail row, Vendor blank
    Total - S002 Addison Lee |                     | 319.01  <- subtotal
    ...
    Total - Supplier | 53178.08
    Total            | 53178.08                    <- grand total (conservation anchor)

Three things follow from that shape and drive this module:

1. The vendor lives on a group header row, so detail rows have an empty vendor
   cell. A flat reader that requires vendor-per-row drops every row.
2. Section headers, per-vendor subtotals and grand totals must be excluded or
   the migration double-counts. The grand total is kept as a reconciliation
   anchor instead.
3. `Bill Payment` and credit rows carry negative open balances. Every open
   item keeps its sign: a positive open balance becomes an invoice (bill), a
   negative one becomes a credit note in the same format. Nothing is netted,
   dropped, or held back, so the output ties exactly to the report's own
   grand total.

When the workbook also contains a filled-in Bills (AP) template sheet, it is
treated as a worked example and read for facts (entity, currency, description
wording) rather than mistaken for the source data.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from app.engine.open_ap import (
    _clean_reference,
    _clean_text,
    _normalise,
    _to_date,
    _to_float,
    _word_boundary_match,
)


LIGHT_BILLS_AP_COLUMNS = [
    "Vendor",
    "Vendor ID",
    "Entity",
    "Invoice Number",
    "Issue Date",
    "Due Date",
    "Currency",
    "Pay From",
    "Payment Date",
    "Description",
    "Lines With Tax",
    "Invoice Currency Amount",
    "Local Currency Amount",
    "Line Description",
    "Line Amount",
    "Tax Code",
    "Account",
    "Line Tax Amount",
    "Amortization Template",
    "Amortization Start Date",
    "Amortization End Date",
]

# Amounts below this are treated as zero when netting and when deciding whether
# a residual balance is worth migrating.
_ZERO_TOL = 0.005

_DEFAULT_DESCRIPTION_PREFIX = "Data migration"

_ROLE_KEYWORDS: dict[str, list[str]] = {
    "invoice_number": [
        "document number",
        "document no",
        "doc number",
        "invoice number",
        "invoice nr",
        "invoice no",
        "bill number",
        "bill no",
        "reference",
        "fakturanummer",
        "fakturanr",
    ],
    "vendor_name": [
        "vendor",
        "vendor name",
        "supplier",
        "supplier name",
        "leverantor",
        "payee",
    ],
    "transaction_type": [
        "transaction type",
        "txn type",
        "type",
    ],
    "due_date": [
        "due date",
        "forfdat",
        "forfallodatum",
        "forfallsdatum",
    ],
    "invoice_date": [
        "invoice date",
        "issue date",
        "document date",
        "transaction date",
        "faktdat",
        "fakturadatum",
        "date",
    ],
    "open_amount": [
        "open balance",
        "outstanding balance",
        "open amount",
        "remaining amount",
        "balance",
        "saldo",
    ],
    # Multi-currency reports carry two amount columns: the entity's local
    # books amount and the document's own transaction-currency amount.
    "open_amount_local": [
        "local currency open amount",
        "local open amount",
        "local currency amount",
        "local amount",
        "open balance local",
        "local balance",
    ],
    "open_amount_txn": [
        "invoice currency amount",
        "transaction currency amount",
        "foreign currency amount",
        "currency amount",
        "foreign amount",
        "amount in currency",
    ],
    "amount": [
        "amount",
        "belopp",
    ],
    "currency": [
        "currency",
        "valuta",
    ],
    "age": [
        "age",
        "days overdue",
    ],
}

# Report furniture that must never be read as an entity name.
_TITLE_NOISE = re.compile(
    r"aging|ageing|a\s*/?\s*p\b|accounts payable|as of|report|detail|summary|unpaid|open items",
    re.IGNORECASE,
)

# "S002 Addison Lee" -> ("S002", "Addison Lee"). Deliberately strict: a letter
# prefix of 1-3 chars followed by digits, or a pure digit code of 3+ digits.
_VENDOR_CODE_RE = re.compile(r"^([A-Za-z]{1,3}\d{2,8}|\d{3,8})[\s\-:]+(.+)$")

_TOTAL_RE = re.compile(r"^total\b", re.IGNORECASE)
_DATE_IN_TEXT_RE = re.compile(
    r"(\d{4})-(\d{1,2})-(\d{1,2})|(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})"
)


@dataclass
class BillsAPAnalysis:
    source_sheet: str | None = None
    header_row: int | None = None
    layout: str = "flat"  # "flat" | "grouped"
    roles: dict[str, str] = field(default_factory=dict)
    role_confidence: dict[str, float] = field(default_factory=dict)
    template_sheet: str | None = None
    facts: dict[str, Any] = field(default_factory=dict)
    # Open-item summary by sign, for the analysis message and audit trail.
    credit_note_vendors: list[dict[str, Any]] = field(default_factory=list)
    split_example: dict[str, str] | None = None
    missing_date_vendors: list[str] = field(default_factory=list)
    source_total: float | None = None
    reported_total: float | None = None
    invoice_total: float | None = None
    credit_note_total: float | None = None
    assumptions: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    confidence: float = 0.0
    detail_rows: int = 0
    invoice_rows: int = 0
    credit_note_rows: int = 0
    zero_rows: int = 0

    @property
    def ready(self) -> bool:
        return not _missing_items(self)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_sheet": self.source_sheet,
            "header_row": self.header_row,
            "layout": self.layout,
            "roles": self.roles,
            "role_confidence": self.role_confidence,
            "template_sheet": self.template_sheet,
            "facts": self.facts,
            "credit_note_vendors": self.credit_note_vendors,
            "split_example": self.split_example,
            "missing_date_vendors": self.missing_date_vendors,
            "source_total": self.source_total,
            "reported_total": self.reported_total,
            "invoice_total": self.invoice_total,
            "credit_note_total": self.credit_note_total,
            "assumptions": self.assumptions,
            "questions": self.questions,
            "warnings": self.warnings,
            "confidence": self.confidence,
            "detail_rows": self.detail_rows,
            "invoice_rows": self.invoice_rows,
            "credit_note_rows": self.credit_note_rows,
            "zero_rows": self.zero_rows,
            "ready": self.ready,
        }


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------


def analyze_bills_ap_workbook(file_path: Path) -> BillsAPAnalysis:
    sheets = pd.read_excel(file_path, sheet_name=None, header=None, engine="openpyxl")
    analysis = BillsAPAnalysis()

    template = _find_template_sheet(sheets)
    if template:
        analysis.template_sheet = template["sheet"]

    source = _find_source_sheet(sheets, analysis.template_sheet)
    if not source:
        analysis.questions = ["Which sheet contains the open AP invoice detail?"]
        return analysis

    analysis.source_sheet = source["sheet"]
    analysis.header_row = source["header_row"]
    analysis.layout = source["layout"]
    analysis.roles = source["roles"]
    analysis.role_confidence = source["role_confidence"]
    analysis.confidence = source["confidence"]

    analysis.facts = _infer_facts(sheets, analysis, template)
    _summarize_open_items(sheets, analysis)
    _refresh_derived(analysis)
    return analysis


def apply_user_message_to_analysis(
    analysis: BillsAPAnalysis,
    message: str,
    file_path: Path,
) -> BillsAPAnalysis:
    """Fold facts stated in chat ("entity is causaLens") into the analysis."""
    overrides: dict[str, Any] = {}

    entity = _parse_named_value(message, ("entity", "company"))
    if entity:
        overrides["entity"] = entity

    currency = _parse_named_value(message, ("currency", "local currency"))
    if currency and re.fullmatch(r"[A-Za-z]{3}", currency.strip()):
        overrides["currency"] = currency.strip().upper()

    description = _parse_named_value(message, ("description", "description prefix"))
    if description:
        overrides["description_prefix"] = description

    if _mentions_disable_vendor_id(message):
        overrides["split_vendor_code"] = False

    refreshed = analyze_bills_ap_workbook(file_path)
    refreshed.facts.update({k: v for k, v in overrides.items() if v not in (None, "")})
    # The summary depends on facts only for labelling, but re-run so the
    # analysis message and the transform always agree.
    sheets = pd.read_excel(file_path, sheet_name=None, header=None, engine="openpyxl")
    _summarize_open_items(sheets, refreshed)
    _refresh_derived(refreshed)
    return refreshed


def apply_structured_updates_to_analysis(
    analysis: BillsAPAnalysis,
    updates: dict[str, str],
    file_path: Path,
) -> BillsAPAnalysis:
    """Apply an already validated AI patch without reparsing natural language."""
    allowed = {"entity", "currency", "description_prefix", "split_vendor_code"}
    unknown = set(updates) - allowed
    if unknown:
        raise ValueError(f"Unsupported bills AP updates: {sorted(unknown)}")

    facts = dict(analysis.facts)
    facts.update(updates)
    if "split_vendor_code" in facts and isinstance(facts["split_vendor_code"], str):
        facts["split_vendor_code"] = facts["split_vendor_code"].lower() == "true"

    refreshed = analyze_bills_ap_workbook(file_path)
    refreshed.facts.update({key: value for key, value in facts.items() if value not in (None, "")})
    sheets = pd.read_excel(file_path, sheet_name=None, header=None, engine="openpyxl")
    _summarize_open_items(sheets, refreshed)
    _refresh_derived(refreshed)
    return refreshed


def transform_open_ap_to_light_bills(
    file_path: Path,
    analysis: BillsAPAnalysis,
) -> pd.DataFrame:
    missing = _missing_items(analysis)
    if missing:
        raise ValueError(
            f"Bills (AP) upload is missing required information: {', '.join(missing)}"
        )

    sheets = pd.read_excel(file_path, sheet_name=None, header=None, engine="openpyxl")
    read = _read_detail_rows(sheets, analysis)

    # Every open item keeps its sign: positive = invoice (bill), negative =
    # credit note. Rows stay in report order for auditability.
    rows = [
        _output_row(detail, analysis)
        for detail in read["details"]
        if abs(detail.amount) > _ZERO_TOL
    ]

    frame = pd.DataFrame(rows, columns=LIGHT_BILLS_AP_COLUMNS)
    # Blank cells must reach the export as None, never NaN, so they land in
    # Excel as genuinely empty cells.
    return frame.astype(object).where(frame.notna(), None)


def format_bills_ap_analysis_message(analysis: BillsAPAnalysis) -> str:
    lines = ["I found an A/P aging detail report to load as Light bills."]

    if analysis.source_sheet:
        shape = "grouped by vendor" if analysis.layout == "grouped" else "one row per invoice"
        lines.append(
            f"- Source sheet: `{analysis.source_sheet}`, header row {analysis.header_row}, "
            f"{shape}, {analysis.detail_rows} detail rows."
        )
    if analysis.roles:
        role_bits = [f"`{src}` -> {role}" for role, src in sorted(analysis.roles.items())]
        lines.append("- Column roles: " + ", ".join(role_bits))
    if analysis.template_sheet:
        lines.append(
            f"- Worked example: `{analysis.template_sheet}`. I took the target layout and "
            "the entity/currency/description wording from it."
        )

    if analysis.invoice_rows or analysis.credit_note_rows:
        lines.append(
            f"\n{analysis.detail_rows} open items become {analysis.invoice_rows} invoices "
            f"({_fmt_amount(analysis.invoice_total)}) and {analysis.credit_note_rows} credit "
            f"notes ({_fmt_amount(analysis.credit_note_total)}), net "
            f"{_fmt_amount(analysis.source_total)} "
            f"{analysis.facts.get('currency', '')}".rstrip()
            + "."
        )
    if analysis.credit_note_vendors:
        bits = [
            f"{v['vendor_name']} ({v['count']} item(s), {_fmt_amount(v['total'])})"
            for v in analysis.credit_note_vendors
        ]
        lines.append(
            "- Credit notes stay in the same format with a negative amount: "
            + ", ".join(bits)
            + "."
        )

    if analysis.reported_total is not None:
        lines.append(
            f"- Reconciliation: invoices {_fmt_amount(analysis.invoice_total)} + credit notes "
            f"{_fmt_amount(analysis.credit_note_total)} = {_fmt_amount(analysis.source_total)}; "
            f"report grand total {_fmt_amount(analysis.reported_total)}."
        )

    if analysis.assumptions:
        lines.append("\nAssumptions:")
        lines.extend(f"- {item}" for item in analysis.assumptions)

    if analysis.warnings:
        lines.append("\nWarnings:")
        lines.extend(f"- {item}" for item in analysis.warnings)

    if analysis.questions:
        lines.append("\nI need this before I can run it:")
        lines.extend(f"- {item}" for item in analysis.questions)
        lines.append("\nReply with the missing fact, for example: `currency is GBP`.")
    else:
        lines.append(
            "\nReady to run. I will keep the original sheets and append the Bills (AP) upload "
            "as a new working sheet."
        )

    return "\n".join(lines)


# --------------------------------------------------------------------------
# Sheet + layout detection
# --------------------------------------------------------------------------


def _find_template_sheet(sheets: dict[str, pd.DataFrame]) -> dict | None:
    """Find a Bills (AP) target template sheet (possibly with an example row)."""
    target = {_normalise(c) for c in LIGHT_BILLS_AP_COLUMNS}
    for name, df in sheets.items():
        for row_idx in range(min(10, len(df))):
            values = {_normalise(v) for v in df.iloc[row_idx].tolist() if pd.notna(v)}
            values.discard("")
            if len(target & values) >= 8:
                return {"sheet": name, "header_row": row_idx}
    return None


def _find_source_sheet(
    sheets: dict[str, pd.DataFrame],
    template_sheet: str | None,
) -> dict | None:
    candidates: list[dict] = []
    for name, df in sheets.items():
        if name == template_sheet:
            continue  # the target template is a worked example, never the source
        candidate = _detect_layout(name, df)
        if candidate:
            candidates.append(candidate)
    if not candidates:
        return None
    return max(candidates, key=lambda c: c["confidence"])


def _detect_layout(sheet_name: str, df: pd.DataFrame, max_rows: int = 15) -> dict | None:
    best: dict | None = None
    for row_idx in range(min(max_rows, len(df))):
        score, roles, role_confidence = _score_header_row(df, row_idx)
        if best is None or score > best["confidence"]:
            best = {
                "sheet": sheet_name,
                "header_row": row_idx,
                "roles": roles,
                "role_confidence": role_confidence,
                "confidence": score,
            }
    if not best or best["confidence"] < 0.45:
        return None
    best["layout"] = _detect_grouping(df, best["header_row"], best["roles"])
    return best


def _score_header_row(
    df: pd.DataFrame, row_idx: int
) -> tuple[float, dict[str, str], dict[str, float]]:
    row = df.iloc[row_idx].tolist()
    roles: dict[str, str] = {}
    role_confidence: dict[str, float] = {}
    non_empty = 0
    labelish = 0

    for value in row:
        if _is_blank(value):
            continue
        non_empty += 1
        if not _looks_numeric(value):
            labelish += 1
        match = _match_role(_normalise(value), set(roles))
        if match:
            role, score = match
            roles[role] = str(value)
            role_confidence[role] = score

    if non_empty == 0:
        return 0.0, {}, {}

    required_hits = sum(
        1
        for group in (
            ("invoice_number",),
            ("vendor_name",),
            ("invoice_date", "due_date"),
            ("open_amount", "open_amount_local", "open_amount_txn", "amount"),
        )
        if any(role in roles for role in group)
    )
    role_score = required_hits / 4
    bonus = len(set(roles) & {"currency", "transaction_type", "due_date", "age"}) / 6
    label_ratio = labelish / non_empty

    # Data volume is the term that stops a 1-row target template from beating
    # the real ledger just because its header labels are tidier.
    data_rows = _count_data_rows(df, row_idx)
    volume = min(data_rows / 10.0, 1.0)

    score = (
        (0.50 * role_score)
        + (0.12 * bonus)
        + (0.08 * label_ratio)
        + (0.30 * volume)
    )
    return round(min(score, 1.0), 3), roles, role_confidence


def _count_data_rows(df: pd.DataFrame, header_row: int) -> int:
    """Rows below the header carrying at least two populated cells."""
    count = 0
    for idx in range(header_row + 1, len(df)):
        populated = sum(1 for v in df.iloc[idx].tolist() if not _is_blank(v))
        if populated >= 2:
            count += 1
    return count


def _detect_grouping(df: pd.DataFrame, header_row: int, roles: dict[str, str]) -> str:
    """Decide whether vendor sits on its own group row instead of every row."""
    vendor_idx = _column_index(df, header_row, roles.get("vendor_name"))
    if vendor_idx is None:
        return "flat"

    group_rows = 0
    detail_rows = 0
    for idx in range(header_row + 1, len(df)):
        values = df.iloc[idx].tolist()
        label = _clean_text(values[vendor_idx]) if vendor_idx < len(values) else ""
        others = [v for i, v in enumerate(values) if i != vendor_idx and not _is_blank(v)]
        if not label and len(others) >= 2:
            detail_rows += 1
        elif label and not others and not _TOTAL_RE.match(label):
            # An amount-less "Total - ..." row (formula cache stripped on
            # save) is a subtotal, not a vendor header; counting it as a
            # group flips real grouped reports to "flat".
            group_rows += 1

    if group_rows >= 2 and detail_rows >= group_rows:
        return "grouped"
    return "flat"


def _match_role(header: str, claimed: set[str]) -> tuple[str, float] | None:
    candidates: list[tuple[int, str, float]] = []
    squashed_header = header.replace(" ", "")
    for role, keywords in _ROLE_KEYWORDS.items():
        if role in claimed:
            continue
        for keyword in keywords:
            norm = _normalise(keyword)
            if header == norm:
                candidates.append((len(norm), role, 1.0))
            elif norm.replace(" ", "") == squashed_header:
                # Fused headers like "Local  CURRENCYopen amount" squash to
                # the same letters as the keyword.
                candidates.append((len(norm), role, 0.95))
            elif _word_boundary_match(norm, header):
                candidates.append((len(norm), role, 0.9))
    if not candidates:
        return None
    _, role, score = max(candidates, key=lambda c: (c[0], c[2]))
    return role, score


def _column_index(df: pd.DataFrame, header_row: int, header: str | None) -> int | None:
    if header is None:
        return None
    target = _normalise(header)
    for idx, value in enumerate(df.iloc[header_row].tolist()):
        if _normalise(value) == target:
            return idx
    return None


# --------------------------------------------------------------------------
# Reading detail rows
# --------------------------------------------------------------------------


@dataclass
class _Detail:
    vendor_raw: str
    vendor_id: str
    vendor_name: str
    txn_type: str
    invoice_number: str
    issue_date: str | None
    due_date: str | None
    # Local-books open balance: the conservation basis, because the report's
    # subtotals and grand total are stated in the entity's local currency.
    amount: float
    # Transaction-currency amount and per-row currency, when the report
    # carries them (multi-currency AP).
    txn_amount: float | None = None
    row_currency: str = ""


def _read_detail_rows(
    sheets: dict[str, pd.DataFrame],
    analysis: BillsAPAnalysis,
) -> dict[str, Any]:
    """Walk the source sheet, returning detail rows plus any reported totals."""
    df = sheets[analysis.source_sheet]
    header_row = analysis.header_row or 0
    roles = analysis.roles

    idx = {
        role: _column_index(df, header_row, header) for role, header in roles.items()
    }
    vendor_idx = idx.get("vendor_name")
    # Local open balance first: it is the conservation basis the report's own
    # subtotals and grand total are stated in.
    amount_idx = idx.get("open_amount_local")
    if amount_idx is None:
        amount_idx = idx.get("open_amount")
    if amount_idx is None:
        amount_idx = idx.get("amount")
    txn_idx = idx.get("open_amount_txn")
    currency_idx = idx.get("currency")

    grouped = analysis.layout == "grouped"
    split_code = analysis.facts.get("split_vendor_code", True)

    details: list[_Detail] = []
    totals: list[tuple[str, float]] = []
    current_vendor = ""

    for row_idx in range(header_row + 1, len(df)):
        values = df.iloc[row_idx].tolist()
        if all(_is_blank(v) for v in values):
            continue

        vendor_cell = (
            _clean_text(values[vendor_idx])
            if vendor_idx is not None and vendor_idx < len(values)
            else ""
        )
        others = [
            v
            for i, v in enumerate(values)
            if i != vendor_idx and not _is_blank(v)
        ]

        # Subtotal / grand total rows: "Total", "Total - Supplier", "Total - S002 ...".
        if vendor_cell and _TOTAL_RE.match(vendor_cell):
            amount = _to_float(values[amount_idx]) if amount_idx is not None else None
            if amount is None:
                amount = next((_to_float(v) for v in others if _to_float(v) is not None), None)
            if amount is not None:
                totals.append((vendor_cell, amount))
            continue

        # Group header row: a label alone on the row. Section headers such as
        # "Supplier" are naturally superseded because the next group header
        # overwrites them before any detail row is read.
        if grouped and vendor_cell and not others:
            current_vendor = vendor_cell
            continue

        vendor_raw = vendor_cell or (current_vendor if grouped else "")
        if not vendor_raw:
            continue

        amount = _to_float(values[amount_idx]) if amount_idx is not None else None
        if amount is None:
            continue

        invoice_number = (
            _clean_reference(values[idx["invoice_number"]])
            if idx.get("invoice_number") is not None
            else ""
        )
        issue_date = (
            _to_date(values[idx["invoice_date"]])
            if idx.get("invoice_date") is not None
            else None
        )
        due_date = (
            _to_date(values[idx["due_date"]]) if idx.get("due_date") is not None else None
        )
        txn_type = (
            _clean_text(values[idx["transaction_type"]])
            if idx.get("transaction_type") is not None
            else ""
        )

        txn_amount = (
            _to_float(values[txn_idx])
            if txn_idx is not None and txn_idx < len(values)
            else None
        )
        row_currency = (
            _clean_text(values[currency_idx]).upper()
            if currency_idx is not None and currency_idx < len(values)
            else ""
        )
        if not re.fullmatch(r"[A-Z]{3}", row_currency):
            row_currency = ""

        vendor_id, vendor_name = _split_vendor(vendor_raw, split_code)
        details.append(
            _Detail(
                vendor_raw=vendor_raw,
                vendor_id=vendor_id,
                vendor_name=vendor_name,
                txn_type=txn_type,
                invoice_number=invoice_number,
                issue_date=issue_date,
                due_date=due_date,
                amount=round(float(amount), 2),
                txn_amount=round(float(txn_amount), 2) if txn_amount is not None else None,
                row_currency=row_currency,
            )
        )

    return {"details": details, "totals": totals}


def _split_vendor(vendor_raw: str, split_code: bool) -> tuple[str, str]:
    if not split_code:
        return "", vendor_raw
    match = _VENDOR_CODE_RE.match(vendor_raw)
    if match:
        return match.group(1), match.group(2).strip()
    return "", vendor_raw


# --------------------------------------------------------------------------
# Open-item summary
# --------------------------------------------------------------------------


def _summarize_open_items(sheets: dict[str, pd.DataFrame], analysis: BillsAPAnalysis) -> None:
    """Classify every open item by sign and reconcile against report totals.

    Positive open balances are invoices (bills); negative open balances —
    `Bill Payment` rows and vendor credits — become credit notes with a
    negative amount. Nothing is netted, dropped, or held back, so the output
    always ties to the report's own grand total.
    """
    if not analysis.source_sheet:
        return
    try:
        read = _read_detail_rows(sheets, analysis)
    except Exception:
        analysis.warnings.append("Could not read the AP detail rows from the source sheet.")
        return

    details: list[_Detail] = read["details"]
    invoices = [d for d in details if d.amount > _ZERO_TOL]
    credit_notes = [d for d in details if d.amount < -_ZERO_TOL]

    analysis.detail_rows = len(details)
    analysis.invoice_rows = len(invoices)
    analysis.credit_note_rows = len(credit_notes)
    analysis.zero_rows = len(details) - len(invoices) - len(credit_notes)
    analysis.invoice_total = round(sum(d.amount for d in invoices), 2)
    analysis.credit_note_total = round(sum(d.amount for d in credit_notes), 2)
    analysis.source_total = round(sum(d.amount for d in details), 2)
    analysis.reported_total = _grand_total(read["totals"])

    by_vendor: dict[str, dict[str, Any]] = {}
    for d in credit_notes:
        entry = by_vendor.setdefault(
            d.vendor_raw, {"vendor_name": d.vendor_name, "count": 0, "total": 0.0}
        )
        entry["count"] += 1
        entry["total"] = round(entry["total"] + d.amount, 2)
    analysis.credit_note_vendors = [by_vendor[key] for key in sorted(by_vendor)]

    analysis.split_example = next(
        (
            {"raw": d.vendor_raw, "id": d.vendor_id, "name": d.vendor_name}
            for d in details
            if d.vendor_id
        ),
        None,
    )
    analysis.missing_date_vendors = sorted(
        {d.vendor_name for d in details if abs(d.amount) > _ZERO_TOL and not d.issue_date}
    )


def _grand_total(totals: list[tuple[str, float]]) -> float | None:
    """Pick the report's own grand total.

    Only a bare "Total" row or a section total ("Total - Supplier",
    "Total - Customer") is a real anchor. A per-vendor subtotal must never be
    used as a fallback: anchoring on the last vendor's subtotal makes every
    report without a grand-total row "fail" reconciliation.
    """
    if not totals:
        return None
    for label, amount in reversed(totals):
        if _normalise(label) in ("total", "total supplier", "total customer"):
            return round(amount, 2)
    return None


# --------------------------------------------------------------------------
# Facts
# --------------------------------------------------------------------------


def _infer_facts(
    sheets: dict[str, pd.DataFrame],
    analysis: BillsAPAnalysis,
    template: dict | None,
) -> dict[str, Any]:
    facts: dict[str, Any] = {
        "description_prefix": _DEFAULT_DESCRIPTION_PREFIX,
        "split_vendor_code": True,
    }

    title = _read_title_block(sheets, analysis)
    if title.get("entity"):
        facts["entity"] = title["entity"]
    if title.get("as_of"):
        facts["as_of_date"] = title["as_of"]

    # A filled-in target template is a worked example: it is the most reliable
    # statement of what the user wants, so it wins over the title block.
    if template:
        example = _read_template_example(sheets, template)
        for key in ("entity", "currency", "description_prefix"):
            if example.get(key):
                facts[key] = example[key]

    if not facts.get("currency"):
        currency = _currency_from_source(sheets, analysis)
        if currency:
            facts["currency"] = currency

    return facts


def _read_title_block(
    sheets: dict[str, pd.DataFrame], analysis: BillsAPAnalysis
) -> dict[str, Any]:
    """Read entity name and 'As of' date from the rows above the header."""
    out: dict[str, Any] = {}
    if not analysis.source_sheet or not analysis.header_row:
        return out
    df = sheets[analysis.source_sheet]

    for row_idx in range(min(analysis.header_row, len(df))):
        values = [v for v in df.iloc[row_idx].tolist() if not _is_blank(v)]
        if len(values) != 1:
            continue
        text = _clean_text(values[0])
        if not text:
            continue
        if re.match(r"^as of\b", text, re.IGNORECASE):
            out.setdefault("as_of", _to_date(re.sub(r"^as of\s*", "", text, flags=re.I)))
            continue
        if _TITLE_NOISE.search(text) or _DATE_IN_TEXT_RE.search(text):
            continue
        if _looks_numeric(text):
            continue
        out.setdefault("entity", text)
    return out


def _read_template_example(
    sheets: dict[str, pd.DataFrame], template: dict
) -> dict[str, Any]:
    """Pull facts from the first populated row of the target template sheet."""
    out: dict[str, Any] = {}
    df = sheets[template["sheet"]]
    header_row = template["header_row"]
    headers = {
        _normalise(v): i for i, v in enumerate(df.iloc[header_row].tolist()) if not _is_blank(v)
    }

    for row_idx in range(header_row + 1, len(df)):
        values = df.iloc[row_idx].tolist()
        if all(_is_blank(v) for v in values):
            continue

        entity_idx = headers.get("entity")
        if entity_idx is not None and entity_idx < len(values):
            entity = _clean_text(values[entity_idx])
            if entity:
                out["entity"] = entity

        currency_idx = headers.get("currency")
        if currency_idx is not None and currency_idx < len(values):
            currency = _clean_text(values[currency_idx]).upper()
            if re.fullmatch(r"[A-Z]{3}", currency):
                out["currency"] = currency

        desc_idx = headers.get("description")
        if desc_idx is not None and desc_idx < len(values):
            prefix = _description_prefix(_clean_text(values[desc_idx]))
            if prefix:
                out["description_prefix"] = prefix
        break
    return out


def _description_prefix(description: str) -> str | None:
    """"Data migration - 3428325" -> "Data migration"."""
    if not description:
        return None
    match = re.match(r"^(.*?)\s*-\s*\S+$", description)
    if match and match.group(1).strip():
        return match.group(1).strip()
    return description


def _currency_from_source(
    sheets: dict[str, pd.DataFrame], analysis: BillsAPAnalysis
) -> str | None:
    """Read a single consistent currency code from a source currency column."""
    header = analysis.roles.get("currency")
    if header is None:
        return None
    df = sheets[analysis.source_sheet]
    col = _column_index(df, analysis.header_row or 0, header)
    if col is None:
        return None
    codes = set()
    for row_idx in range((analysis.header_row or 0) + 1, len(df)):
        values = df.iloc[row_idx].tolist()
        if col >= len(values):
            continue
        text = _clean_text(values[col]).upper()
        if re.fullmatch(r"[A-Z]{3}", text):
            codes.add(text)
    return codes.pop() if len(codes) == 1 else None


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------


def _output_row(detail: _Detail, analysis: BillsAPAnalysis) -> dict[str, Any]:
    invoice_number = detail.invoice_number
    prefix = analysis.facts.get("description_prefix", _DEFAULT_DESCRIPTION_PREFIX)
    description = f"{prefix} - {invoice_number}" if invoice_number else prefix

    row = {col: None for col in LIGHT_BILLS_AP_COLUMNS}
    row.update(
        {
            "Vendor": detail.vendor_name,
            "Vendor ID": detail.vendor_id or None,
            "Entity": analysis.facts.get("entity"),
            # Kept as text: invoice numbers like "May/July 26" and "2026-13"
            # must never be coerced to a number or a date.
            "Invoice Number": invoice_number or None,
            "Issue Date": detail.issue_date,
            "Due Date": detail.due_date or detail.issue_date,
            # Per-row currency wins over the single report-level fact.
            "Currency": detail.row_currency or analysis.facts.get("currency"),
            "Description": description,
            # The sign carries the document type: a positive amount is an
            # invoice (bill), a negative amount is a credit note. When the
            # report has a transaction-currency column, that amount goes to
            # Invoice Currency Amount; the local open balance always goes to
            # Local Currency Amount.
            "Invoice Currency Amount": (
                detail.txn_amount if detail.txn_amount is not None else detail.amount
            ),
            "Local Currency Amount": detail.amount,
        }
    )
    return row


# --------------------------------------------------------------------------
# Readiness, assumptions, warnings
# --------------------------------------------------------------------------


def _missing_items(analysis: BillsAPAnalysis) -> list[str]:
    missing: list[str] = []
    if not analysis.source_sheet or analysis.header_row is None:
        missing.append("source_sheet")
    if "invoice_number" not in analysis.roles:
        missing.append("invoice_number")
    if "vendor_name" not in analysis.roles:
        missing.append("vendor_name")
    if not any(
        r in analysis.roles
        for r in ("open_amount", "open_amount_local", "open_amount_txn", "amount")
    ):
        missing.append("amount")
    if not analysis.facts.get("entity"):
        missing.append("entity")
    # A per-row currency column answers the currency question by itself.
    if not analysis.facts.get("currency") and "currency" not in analysis.roles:
        missing.append("currency")
    if analysis.source_sheet and analysis.invoice_rows + analysis.credit_note_rows == 0:
        missing.append("open_items")
    return missing


def _refresh_derived(analysis: BillsAPAnalysis) -> None:
    analysis.assumptions = _infer_assumptions(analysis)
    analysis.questions = _missing_questions(analysis)
    analysis.warnings = _infer_warnings(analysis)


def _infer_assumptions(analysis: BillsAPAnalysis) -> list[str]:
    facts = analysis.facts
    assumptions = [
        "One row per open item, at its open balance as of the report date — not the "
        "original document amount.",
        "A positive amount is an invoice; a negative amount is a credit note in the same "
        "format. Payments, credits and zero-balance vendors are never netted or dropped, "
        "so the upload ties to the report total.",
    ]
    if "open_amount_txn" in analysis.roles:
        local_header = (
            analysis.roles.get("open_amount_local")
            or analysis.roles.get("open_amount")
            or analysis.roles.get("amount")
        )
        assumptions.append(
            f"Entity `{facts.get('entity', '?')}` applies to every row; each row keeps its "
            f"own currency from `{analysis.roles.get('currency', '?')}`, with Invoice "
            f"Currency Amount from `{analysis.roles['open_amount_txn']}` and Local Currency "
            f"Amount from `{local_header}`."
        )
    else:
        assumptions.append(
            f"Entity `{facts.get('entity', '?')}` and currency `{facts.get('currency', '?')}` "
            "apply to every row; the report has no per-row entity or currency."
        )
        assumptions.append(
            "Invoice currency amount equals local currency amount, so no FX rate is applied."
        )
    if facts.get("split_vendor_code", True) and analysis.split_example:
        example = analysis.split_example
        assumptions.append(
            f"Vendor strings are split into code and name "
            f"(`{example['raw']}` -> Vendor ID `{example['id']}`, "
            f"Vendor `{example['name']}`). Say `do not split vendor code` to "
            "keep the full string in Vendor instead."
        )
    assumptions.append(
        "Line-level columns (Account, Line Amount, Tax Code, amortization) are left blank, "
        "matching the worked example — these are header-level migration bills."
    )
    return assumptions


def _missing_questions(analysis: BillsAPAnalysis) -> list[str]:
    questions = []
    for item in _missing_items(analysis):
        if item == "entity":
            questions.append("Which Light entity should these bills belong to?")
        elif item == "currency":
            questions.append(
                "Which currency are these bills in? The report has no currency column."
            )
        elif item == "source_sheet":
            questions.append("Which sheet contains the open AP invoice detail?")
        elif item == "amount":
            questions.append("Which column holds the open balance to migrate?")
        elif item == "open_items":
            questions.append(
                "No open AP rows could be read from the source sheet. Which rows hold "
                "the open invoices and credit notes?"
            )
        else:
            questions.append(f"How should I fill `{item}`?")
    return questions


def _infer_warnings(analysis: BillsAPAnalysis) -> list[str]:
    warnings: list[str] = []

    if analysis.reported_total is not None and analysis.source_total is not None:
        if abs(analysis.reported_total - analysis.source_total) > 0.01:
            warnings.append(
                f"Detail rows sum to {_fmt_amount(analysis.source_total)} but the report's "
                f"own grand total is {_fmt_amount(analysis.reported_total)}. Some rows may "
                "not have been read."
            )

    accounted = round((analysis.invoice_total or 0) + (analysis.credit_note_total or 0), 2)
    if analysis.source_total is not None and abs(accounted - analysis.source_total) > 0.01:
        warnings.append(
            f"Invoices plus credit notes ({_fmt_amount(accounted)}) do not tie to the "
            f"detail total ({_fmt_amount(analysis.source_total)})."
        )

    if analysis.zero_rows:
        warnings.append(
            f"{analysis.zero_rows} open item(s) with a zero amount were skipped."
        )

    if analysis.missing_date_vendors:
        warnings.append(
            "Rows with no readable issue date for: "
            + ", ".join(analysis.missing_date_vendors[:5])
            + "."
        )
    return warnings


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------


def _mentions_disable_vendor_id(message: str) -> bool:
    return bool(
        re.search(
            r"(do\s*not|don'?t|no)\s+(split|separate)\s+(the\s+)?vendor",
            message,
            re.IGNORECASE,
        )
    )


def _parse_named_value(message: str, names: tuple[str, ...]) -> str | None:
    for name in names:
        pattern = rf"\b{re.escape(name)}\b\s*(?:is|=|:)\s*([^\n,;]+)"
        match = re.search(pattern, message, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip().strip("`'\"")
    return None


def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and pd.isna(value):
        return True
    return str(value).strip() == ""


def _looks_numeric(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return True
    try:
        float(str(value).replace(",", ""))
        return True
    except ValueError:
        return False


def _fmt_amount(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:,.2f}"
