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
3. `Bill Payment` rows carry negative open balances and are not linked to the
   bill they pay, so payments are netted per vendor and applied oldest-first.
   Vendors that net to zero have nothing open and are dropped; vendors with a
   negative net hold a vendor credit and are reported, never emitted as a
   negative bill.

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
    "amount": [
        "invoice currency amount",
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
    # Per-vendor netting outcome, for the analysis message and audit trail.
    bill_vendors: list[dict[str, Any]] = field(default_factory=list)
    credit_vendors: list[dict[str, Any]] = field(default_factory=list)
    settled_vendors: list[dict[str, Any]] = field(default_factory=list)
    source_total: float | None = None
    reported_total: float | None = None
    bill_total: float | None = None
    credit_total: float | None = None
    assumptions: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    confidence: float = 0.0
    detail_rows: int = 0
    bill_rows: int = 0

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
            "bill_vendors": self.bill_vendors,
            "credit_vendors": self.credit_vendors,
            "settled_vendors": self.settled_vendors,
            "source_total": self.source_total,
            "reported_total": self.reported_total,
            "bill_total": self.bill_total,
            "credit_total": self.credit_total,
            "assumptions": self.assumptions,
            "questions": self.questions,
            "warnings": self.warnings,
            "confidence": self.confidence,
            "detail_rows": self.detail_rows,
            "bill_rows": self.bill_rows,
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
    _apply_netting(sheets, analysis)
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
    # Netting output depends on facts only for labelling, but re-run so the
    # analysis message and the transform always agree.
    sheets = pd.read_excel(file_path, sheet_name=None, header=None, engine="openpyxl")
    _apply_netting(sheets, refreshed)
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
    details = _read_detail_rows(sheets, analysis)
    plan = _net_by_vendor(details)

    rows: list[dict[str, Any]] = []
    for vendor in plan["bill_vendors"]:
        for bill in vendor["bills"]:
            rows.append(_output_row(vendor, bill, analysis))

    return pd.DataFrame(rows, columns=LIGHT_BILLS_AP_COLUMNS)


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

    if analysis.bill_vendors:
        lines.append(
            f"\nNetting {analysis.detail_rows} rows per vendor gives "
            f"{analysis.bill_rows} bills across {len(analysis.bill_vendors)} vendors, "
            f"totalling {_fmt_amount(analysis.bill_total)} "
            f"{analysis.facts.get('currency', '')}".rstrip()
            + "."
        )
    if analysis.settled_vendors:
        names = ", ".join(v["vendor_name"] for v in analysis.settled_vendors)
        lines.append(
            f"- Dropped {len(analysis.settled_vendors)} vendor(s) that net to zero "
            f"(paid in full, nothing open): {names}."
        )
    if analysis.credit_vendors:
        bits = [
            f"{v['vendor_name']} ({_fmt_amount(v['net'])})" for v in analysis.credit_vendors
        ]
        lines.append(
            f"- Held back {len(analysis.credit_vendors)} vendor(s) in a credit position — "
            f"these are unapplied payments, not bills, so I will not emit them: "
            + ", ".join(bits)
            + "."
        )

    if analysis.reported_total is not None:
        lines.append(
            f"- Reconciliation: report grand total {_fmt_amount(analysis.reported_total)}; "
            f"detail rows sum to {_fmt_amount(analysis.source_total)}; "
            f"bills {_fmt_amount(analysis.bill_total)} + credits "
            f"{_fmt_amount(analysis.credit_total)} = "
            f"{_fmt_amount((analysis.bill_total or 0) + (analysis.credit_total or 0))}."
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
            ("open_amount", "amount"),
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
        vendor_blank = _is_blank(values[vendor_idx]) if vendor_idx < len(values) else True
        others = [v for i, v in enumerate(values) if i != vendor_idx and not _is_blank(v)]
        if vendor_blank and len(others) >= 2:
            detail_rows += 1
        elif not vendor_blank and not others:
            group_rows += 1

    if group_rows >= 2 and detail_rows >= group_rows:
        return "grouped"
    return "flat"


def _match_role(header: str, claimed: set[str]) -> tuple[str, float] | None:
    candidates: list[tuple[int, str, float]] = []
    for role, keywords in _ROLE_KEYWORDS.items():
        if role in claimed:
            continue
        for keyword in keywords:
            norm = _normalise(keyword)
            if header == norm:
                candidates.append((len(norm), role, 1.0))
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
    amount: float


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
    amount_idx = idx.get("open_amount")
    if amount_idx is None:
        amount_idx = idx.get("amount")

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
# Per-vendor netting
# --------------------------------------------------------------------------


def _net_by_vendor(read: dict[str, Any]) -> dict[str, Any]:
    """Net payments against bills per vendor.

    An aging report gives no link between a Bill Payment and the bill it
    settles, so payments are pooled per vendor and applied oldest bill first.
    Vendors netting to zero are dropped; vendors netting negative hold a
    credit and are reported rather than emitted as negative bills.
    """
    details: list[_Detail] = read["details"]

    by_vendor: dict[str, list[_Detail]] = {}
    for row in details:
        by_vendor.setdefault(row.vendor_raw, []).append(row)

    bill_vendors: list[dict[str, Any]] = []
    credit_vendors: list[dict[str, Any]] = []
    settled_vendors: list[dict[str, Any]] = []

    for vendor_raw, rows in by_vendor.items():
        net = round(sum(r.amount for r in rows), 2)
        summary = {
            "vendor_raw": vendor_raw,
            "vendor_id": rows[0].vendor_id,
            "vendor_name": rows[0].vendor_name,
            "net": net,
            "row_count": len(rows),
        }

        if abs(net) < _ZERO_TOL:
            settled_vendors.append(summary)
            continue
        if net < 0:
            credit_vendors.append(summary)
            continue

        positives = [r for r in rows if r.amount > _ZERO_TOL]
        credit_pool = round(sum(-r.amount for r in rows if r.amount < -_ZERO_TOL), 2)

        # Oldest first: an unlinked payment settles the longest-outstanding bill.
        positives.sort(key=lambda r: (r.issue_date or "", r.invoice_number))

        bills: list[dict[str, Any]] = []
        for row in positives:
            applied = min(credit_pool, row.amount) if credit_pool > _ZERO_TOL else 0.0
            remaining = round(row.amount - applied, 2)
            credit_pool = round(credit_pool - applied, 2)
            if remaining <= _ZERO_TOL:
                continue
            bills.append(
                {
                    "invoice_number": row.invoice_number,
                    "issue_date": row.issue_date,
                    "due_date": row.due_date,
                    "amount": remaining,
                    "gross_amount": row.amount,
                    "payment_applied": round(applied, 2),
                    "txn_type": row.txn_type,
                }
            )

        summary["bills"] = bills
        summary["bill_total"] = round(sum(b["amount"] for b in bills), 2)
        bill_vendors.append(summary)

    bill_vendors.sort(key=lambda v: v["vendor_raw"])
    credit_vendors.sort(key=lambda v: v["vendor_raw"])
    settled_vendors.sort(key=lambda v: v["vendor_raw"])

    return {
        "bill_vendors": bill_vendors,
        "credit_vendors": credit_vendors,
        "settled_vendors": settled_vendors,
        "source_total": round(sum(r.amount for r in details), 2),
        "totals": read["totals"],
        "detail_rows": len(details),
    }


def _apply_netting(sheets: dict[str, pd.DataFrame], analysis: BillsAPAnalysis) -> None:
    if not analysis.source_sheet:
        return
    try:
        read = _read_detail_rows(sheets, analysis)
    except Exception:
        analysis.warnings.append("Could not read the AP detail rows from the source sheet.")
        return

    plan = _net_by_vendor(read)
    analysis.bill_vendors = plan["bill_vendors"]
    analysis.credit_vendors = plan["credit_vendors"]
    analysis.settled_vendors = plan["settled_vendors"]
    analysis.detail_rows = plan["detail_rows"]
    analysis.source_total = plan["source_total"]
    analysis.bill_total = round(sum(v["bill_total"] for v in plan["bill_vendors"]), 2)
    analysis.credit_total = round(sum(v["net"] for v in plan["credit_vendors"]), 2)
    analysis.bill_rows = sum(len(v["bills"]) for v in plan["bill_vendors"])
    analysis.reported_total = _grand_total(plan["totals"])


def _grand_total(totals: list[tuple[str, float]]) -> float | None:
    """Pick the report's own grand total, preferring a bare "Total" row."""
    if not totals:
        return None
    for label, amount in reversed(totals):
        if _normalise(label) == "total":
            return round(amount, 2)
    return round(totals[-1][1], 2)


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


def _output_row(
    vendor: dict[str, Any],
    bill: dict[str, Any],
    analysis: BillsAPAnalysis,
) -> dict[str, Any]:
    invoice_number = bill["invoice_number"]
    prefix = analysis.facts.get("description_prefix", _DEFAULT_DESCRIPTION_PREFIX)
    description = f"{prefix} - {invoice_number}" if invoice_number else prefix
    amount = bill["amount"]

    row = {col: None for col in LIGHT_BILLS_AP_COLUMNS}
    row.update(
        {
            "Vendor": vendor["vendor_name"],
            "Vendor ID": vendor["vendor_id"] or None,
            "Entity": analysis.facts.get("entity"),
            # Kept as text: invoice numbers like "May/July 26" and "2026-13"
            # must never be coerced to a number or a date.
            "Invoice Number": invoice_number or None,
            "Issue Date": bill["issue_date"],
            "Due Date": bill["due_date"] or bill["issue_date"],
            "Currency": analysis.facts.get("currency"),
            "Description": description,
            "Invoice Currency Amount": amount,
            # The aging report carries a single currency, so the invoice
            # currency is the entity's local currency and no rate applies.
            "Local Currency Amount": amount,
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
    if not any(r in analysis.roles for r in ("open_amount", "amount")):
        missing.append("amount")
    if not analysis.facts.get("entity"):
        missing.append("entity")
    if not analysis.facts.get("currency"):
        missing.append("currency")
    if analysis.source_sheet and analysis.bill_rows == 0:
        missing.append("bills")
    return missing


def _refresh_derived(analysis: BillsAPAnalysis) -> None:
    analysis.assumptions = _infer_assumptions(analysis)
    analysis.questions = _missing_questions(analysis)
    analysis.warnings = _infer_warnings(analysis)


def _infer_assumptions(analysis: BillsAPAnalysis) -> list[str]:
    facts = analysis.facts
    assumptions = [
        "One bill per outstanding invoice, at its open balance as of the report date — "
        "not the original invoice amount.",
        f"Entity `{facts.get('entity', '?')}` and currency `{facts.get('currency', '?')}` "
        "apply to every bill; the report has no per-row entity or currency.",
        "Invoice currency amount equals local currency amount, so no FX rate is applied.",
    ]
    if facts.get("split_vendor_code", True) and any(
        v["vendor_id"] for v in analysis.bill_vendors
    ):
        example = next((v for v in analysis.bill_vendors if v["vendor_id"]), None)
        if example:
            assumptions.append(
                f"Vendor strings are split into code and name "
                f"(`{example['vendor_raw']}` -> Vendor ID `{example['vendor_id']}`, "
                f"Vendor `{example['vendor_name']}`). Say `do not split vendor code` to "
                "keep the full string in Vendor instead."
            )
    if any(b["payment_applied"] > _ZERO_TOL for v in analysis.bill_vendors for b in v["bills"]):
        assumptions.append(
            "Payments are not linked to invoices in an aging report, so they are pooled per "
            "vendor and applied to the oldest bill first."
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
        elif item == "bills":
            questions.append(
                "No open bills survived netting — every vendor is settled or in credit. "
                "Should I migrate gross bill rows without applying payments?"
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

    accounted = round((analysis.bill_total or 0) + (analysis.credit_total or 0), 2)
    if analysis.source_total is not None and abs(accounted - analysis.source_total) > 0.01:
        warnings.append(
            f"Bills plus credits ({_fmt_amount(accounted)}) do not tie to the detail total "
            f"({_fmt_amount(analysis.source_total)})."
        )

    if analysis.credit_vendors:
        warnings.append(
            f"{_fmt_amount(abs(analysis.credit_total or 0))} of unapplied vendor payments is "
            "excluded from this upload and needs to be posted separately as vendor credits."
        )

    missing_dates = [
        v["vendor_name"]
        for v in analysis.bill_vendors
        for b in v["bills"]
        if not b["issue_date"]
    ]
    if missing_dates:
        warnings.append(
            f"{len(missing_dates)} bill(s) have no readable issue date: "
            + ", ".join(sorted(set(missing_dates))[:5])
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
