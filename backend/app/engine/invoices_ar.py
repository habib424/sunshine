"""
Deterministic open AR upload to the Light "Invoices (AR)" invoice layout.

The mirror image of `bills_ap.py`: instead of vendor bills, this engine
produces open customer invoices — one row per outstanding AR invoice in
Light's Invoices (AR) upload template.

The sources seen so far are QuickBooks/Xero-style "A/R Aging Detail" reports,
which are *hierarchical* rather than tabular:

    causaLens                                      <- title block
    causaLens : causaLens US                       <- QB "company : entity"
    A/R Aging Detail
    As of 31 July 2026

    Customer | Transaction Type | Date | Document Number | P.O. No. | Due Date | Age | Open Balance
    C117 Syneos Health, LLC                        <- customer group header
          | Invoice | 2026-05-12 | INV360 | PO_... | ... | 375000.00   <- detail row, Customer blank
    Total - C117 Syneos Health, LLC |      | 750000.00   <- subtotal
    ...
    Total                           |      | 913650.00   <- grand total (conservation anchor)

The same three traps as the AP aging report apply: the customer lives on a
group-header row, subtotals and grand totals must be excluded or the migration
double-counts (the grand total is kept as a reconciliation anchor), and
`Payment` / `Credit Memo` rows carry negative balances. Every open item keeps
its sign: a positive open balance becomes an invoice, a negative one becomes a
credit note in the same format. Nothing is netted, dropped, or held back, so
the output ties exactly to the report's own grand total.

When the workbook also contains a filled-in Invoices (AR) template sheet, it
is treated as a worked example and read for facts (entity, currency,
description wording, product) rather than mistaken for the source data. Each
output invoice carries one line — Product, Quantity 1, Unit Price equal to the
open balance — matching the worked example.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from app.engine.bills_ap import (
    _DATE_IN_TEXT_RE,
    _TOTAL_RE,
    _VENDOR_CODE_RE as _CUSTOMER_CODE_RE,
    _ZERO_TOL,
    _column_index,
    _count_data_rows,
    _description_prefix,
    _fmt_amount,
    _grand_total,
    _is_blank,
    _looks_numeric,
)
from app.engine.open_ap import (
    _clean_reference,
    _clean_text,
    _normalise,
    _to_date,
    _to_float,
    _word_boundary_match,
)


OPEN_AR_INTENT = "upload_open_ar_to_light_ar"
OPEN_AR_INTENTS = {OPEN_AR_INTENT}


LIGHT_INVOICES_AR_COLUMNS = [
    "Entity",
    "Invoice Status",
    "Customer",
    "Customer ID",
    "Invoice Number",
    "Invoice Date",
    "Due Date",
    "Currency",
    "Payment Type",
    "Payment To",
    "Invoice Template",
    "Net Terms",
    "PO Number",
    "Description",
    "Gross/Net",
    "Invoice Currency Amount",
    "Local Currency Amount",
    "Product",
    "Quantity",
    "Unit Price",
    "Product Name Override",
    "Tax Code",
    "Account",
    "Tax Amount Override",
    "Billing Start",
    "Billing End",
    "Accrual Template",
    "Accrual Start Date",
    "Accrual End Date",
]

_DEFAULT_DESCRIPTION_PREFIX = "Data migration"

_ROLE_KEYWORDS: dict[str, list[str]] = {
    "invoice_number": [
        "document number",
        "document no",
        "doc number",
        "invoice number",
        "invoice nr",
        "invoice no",
        "reference",
        "fakturanummer",
        "fakturanr",
    ],
    "customer_name": [
        "customer",
        "customer name",
        "client",
        "client name",
        "kund",
        "kundnamn",
        "debtor",
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
    "po_number": [
        "p o no",
        "po no",
        "po number",
        "purchase order",
        "customer po",
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
    r"aging|ageing|a\s*/?\s*r\b|accounts receivable|as of|report|detail|summary|unpaid|open items",
    re.IGNORECASE,
)


@dataclass
class InvoicesARAnalysis:
    source_sheet: str | None = None
    header_row: int | None = None
    layout: str = "flat"  # "flat" | "grouped"
    roles: dict[str, str] = field(default_factory=dict)
    role_confidence: dict[str, float] = field(default_factory=dict)
    template_sheet: str | None = None
    facts: dict[str, Any] = field(default_factory=dict)
    # Open-item summary by sign, for the analysis message and audit trail.
    credit_note_customers: list[dict[str, Any]] = field(default_factory=list)
    split_example: dict[str, str] | None = None
    missing_date_customers: list[str] = field(default_factory=list)
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
            "credit_note_customers": self.credit_note_customers,
            "split_example": self.split_example,
            "missing_date_customers": self.missing_date_customers,
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


def analyze_invoices_ar_workbook(file_path: Path) -> InvoicesARAnalysis:
    sheets = pd.read_excel(file_path, sheet_name=None, header=None, engine="openpyxl")
    analysis = InvoicesARAnalysis()

    template = _find_template_sheet(sheets)
    if template:
        analysis.template_sheet = template["sheet"]

    source = _find_source_sheet(sheets, analysis.template_sheet)
    if not source:
        analysis.questions = ["Which sheet contains the open AR invoice detail?"]
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


def apply_structured_updates_to_analysis(
    analysis: InvoicesARAnalysis,
    updates: dict[str, str],
    file_path: Path,
) -> InvoicesARAnalysis:
    """Apply an already validated AI patch without reparsing natural language."""
    allowed = {
        "entity",
        "currency",
        "description_prefix",
        "product",
        "split_customer_code",
        "include_po_number",
    }
    unknown = set(updates) - allowed
    if unknown:
        raise ValueError(f"Unsupported invoices AR updates: {sorted(unknown)}")

    facts = dict(analysis.facts)
    facts.update(updates)
    for flag in ("split_customer_code", "include_po_number"):
        if flag in facts and isinstance(facts[flag], str):
            facts[flag] = facts[flag].lower() == "true"

    refreshed = analyze_invoices_ar_workbook(file_path)
    refreshed.facts.update({key: value for key, value in facts.items() if value not in (None, "")})
    sheets = pd.read_excel(file_path, sheet_name=None, header=None, engine="openpyxl")
    _summarize_open_items(sheets, refreshed)
    _refresh_derived(refreshed)
    return refreshed


def transform_open_ar_to_light_invoices(
    file_path: Path,
    analysis: InvoicesARAnalysis,
) -> pd.DataFrame:
    missing = _missing_items(analysis)
    if missing:
        raise ValueError(
            f"Invoices (AR) upload is missing required information: {', '.join(missing)}"
        )

    sheets = pd.read_excel(file_path, sheet_name=None, header=None, engine="openpyxl")
    read = _read_detail_rows(sheets, analysis)

    # Every open item keeps its sign: positive = invoice, negative = credit
    # note. Rows stay in report order for auditability.
    rows = [
        _output_row(detail, analysis)
        for detail in read["details"]
        if abs(detail.amount) > _ZERO_TOL
    ]

    frame = pd.DataFrame(rows, columns=LIGHT_INVOICES_AR_COLUMNS)
    # Blank cells must reach the export as None, never NaN, so they land in
    # Excel as genuinely empty cells.
    return frame.astype(object).where(frame.notna(), None)


def format_invoices_ar_analysis_message(analysis: InvoicesARAnalysis) -> str:
    lines = ["I found an A/R aging detail report to load as Light customer invoices."]

    if analysis.source_sheet:
        shape = "grouped by customer" if analysis.layout == "grouped" else "one row per invoice"
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
    if analysis.credit_note_customers:
        bits = [
            f"{c['customer_name']} ({c['count']} item(s), {_fmt_amount(c['total'])})"
            for c in analysis.credit_note_customers
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
        lines.append("\nReply with the missing fact, for example: `currency is USD`.")
    else:
        lines.append(
            "\nReady to run. I will keep the original sheets and append the Invoices (AR) "
            "upload as a new working sheet."
        )

    return "\n".join(lines)


# --------------------------------------------------------------------------
# Sheet + layout detection
# --------------------------------------------------------------------------


def _find_template_sheet(sheets: dict[str, pd.DataFrame]) -> dict | None:
    """Find an Invoices (AR) target template sheet (possibly with an example row)."""
    target = {_normalise(c) for c in LIGHT_INVOICES_AR_COLUMNS}
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
    # The volume term is worth 0.30 on its own, so require real role evidence
    # too, or any long non-AR sheet reads as an AR ledger just for being long.
    if not best or best["confidence"] < 0.45 or _required_role_hits(best["roles"]) < 2:
        return None
    best["layout"] = _detect_grouping(df, best["header_row"], best["roles"])
    return best


def _required_role_hits(roles: dict[str, str]) -> int:
    """How many of the four things an AR invoice row needs were identified."""
    return sum(
        1
        for group in (
            ("invoice_number",),
            ("customer_name",),
            ("invoice_date", "due_date"),
            ("open_amount", "open_amount_local", "open_amount_txn", "amount"),
        )
        if any(role in roles for role in group)
    )


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

    role_score = _required_role_hits(roles) / 4
    bonus = len(set(roles) & {"currency", "transaction_type", "due_date", "age", "po_number"}) / 6
    label_ratio = labelish / non_empty

    # Data volume is the term that stops a 1-row target template from beating
    # the real ledger just because its header labels are tidier.
    volume = min(_count_data_rows(df, row_idx) / 10.0, 1.0)

    score = (
        (0.50 * role_score)
        + (0.12 * bonus)
        + (0.08 * label_ratio)
        + (0.30 * volume)
    )
    return round(min(score, 1.0), 3), roles, role_confidence


def _detect_grouping(df: pd.DataFrame, header_row: int, roles: dict[str, str]) -> str:
    """Decide whether customer sits on its own group row instead of every row."""
    customer_idx = _column_index(df, header_row, roles.get("customer_name"))
    if customer_idx is None:
        return "flat"

    group_rows = 0
    detail_rows = 0
    for idx in range(header_row + 1, len(df)):
        values = df.iloc[idx].tolist()
        label = _clean_text(values[customer_idx]) if customer_idx < len(values) else ""
        others = [v for i, v in enumerate(values) if i != customer_idx and not _is_blank(v)]
        if not label and len(others) >= 2:
            detail_rows += 1
        elif label and not others and not _TOTAL_RE.match(label):
            # An amount-less "Total - ..." row (formula cache stripped on
            # save) is a subtotal, not a customer header; counting it as a
            # group flips real grouped reports to "flat".
            group_rows += 1

    # One group header is enough evidence: an aging report with a single
    # customer is still grouped, and reading it as flat drops every detail
    # row because detail rows carry a blank customer cell. A truly flat
    # sheet scores zero on both counters, so this cannot misfire there —
    # and grouped reading keeps per-row customer cells anyway.
    if group_rows >= 1 and detail_rows >= group_rows:
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


# --------------------------------------------------------------------------
# Reading detail rows
# --------------------------------------------------------------------------


@dataclass
class _Detail:
    customer_raw: str
    customer_id: str
    customer_name: str
    txn_type: str
    invoice_number: str
    issue_date: str | None
    due_date: str | None
    po_number: str
    # Local-books open balance: the conservation basis, because the report's
    # subtotals and grand total are stated in the entity's local currency.
    amount: float
    # Transaction-currency amount and per-row currency, when the report
    # carries them (multi-currency AR).
    txn_amount: float | None = None
    row_currency: str = ""


def _read_detail_rows(
    sheets: dict[str, pd.DataFrame],
    analysis: InvoicesARAnalysis,
) -> dict[str, Any]:
    """Walk the source sheet, returning detail rows plus any reported totals."""
    df = sheets[analysis.source_sheet]
    header_row = analysis.header_row or 0
    roles = analysis.roles

    idx = {
        role: _column_index(df, header_row, header) for role, header in roles.items()
    }
    customer_idx = idx.get("customer_name")
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
    split_code = analysis.facts.get("split_customer_code", True)

    details: list[_Detail] = []
    totals: list[tuple[str, float]] = []
    current_customer = ""

    for row_idx in range(header_row + 1, len(df)):
        values = df.iloc[row_idx].tolist()
        if all(_is_blank(v) for v in values):
            continue

        customer_cell = (
            _clean_text(values[customer_idx])
            if customer_idx is not None and customer_idx < len(values)
            else ""
        )
        others = [
            v
            for i, v in enumerate(values)
            if i != customer_idx and not _is_blank(v)
        ]

        # Subtotal / grand total rows: "Total", "Total - C117 Syneos Health, LLC".
        if customer_cell and _TOTAL_RE.match(customer_cell):
            amount = _to_float(values[amount_idx]) if amount_idx is not None else None
            if amount is None:
                amount = next((_to_float(v) for v in others if _to_float(v) is not None), None)
            if amount is not None:
                totals.append((customer_cell, amount))
            continue

        # Group header row: a label alone on the row. Section headers such as
        # "Customer" are naturally superseded because the next group header
        # overwrites them before any detail row is read.
        if grouped and customer_cell and not others:
            current_customer = customer_cell
            continue

        customer_raw = customer_cell or (current_customer if grouped else "")
        if not customer_raw:
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
        po_number = (
            _clean_reference(values[idx["po_number"]])
            if idx.get("po_number") is not None
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

        customer_id, customer_name = _split_customer(customer_raw, split_code)
        details.append(
            _Detail(
                customer_raw=customer_raw,
                customer_id=customer_id,
                customer_name=customer_name,
                txn_type=txn_type,
                invoice_number=invoice_number,
                issue_date=issue_date,
                due_date=due_date,
                po_number=po_number,
                amount=round(float(amount), 2),
                txn_amount=round(float(txn_amount), 2) if txn_amount is not None else None,
                row_currency=row_currency,
            )
        )

    return {"details": details, "totals": totals}


def _split_customer(customer_raw: str, split_code: bool) -> tuple[str, str]:
    if not split_code:
        return "", customer_raw
    match = _CUSTOMER_CODE_RE.match(customer_raw)
    if match:
        return match.group(1), match.group(2).strip()
    return "", customer_raw


# --------------------------------------------------------------------------
# Open-item summary
# --------------------------------------------------------------------------


def _summarize_open_items(sheets: dict[str, pd.DataFrame], analysis: InvoicesARAnalysis) -> None:
    """Classify every open item by sign and reconcile against report totals.

    Positive open balances are invoices; negative open balances — `Payment`
    and `Credit Memo` rows — become credit notes with a negative amount.
    Nothing is netted, dropped, or held back, so the output always ties to
    the report's own grand total.
    """
    if not analysis.source_sheet:
        return
    try:
        read = _read_detail_rows(sheets, analysis)
    except Exception:
        analysis.warnings.append("Could not read the AR detail rows from the source sheet.")
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

    by_customer: dict[str, dict[str, Any]] = {}
    for d in credit_notes:
        entry = by_customer.setdefault(
            d.customer_raw, {"customer_name": d.customer_name, "count": 0, "total": 0.0}
        )
        entry["count"] += 1
        entry["total"] = round(entry["total"] + d.amount, 2)
    analysis.credit_note_customers = [by_customer[key] for key in sorted(by_customer)]

    analysis.split_example = next(
        (
            {"raw": d.customer_raw, "id": d.customer_id, "name": d.customer_name}
            for d in details
            if d.customer_id
        ),
        None,
    )
    analysis.missing_date_customers = sorted(
        {d.customer_name for d in details if abs(d.amount) > _ZERO_TOL and not d.issue_date}
    )


# --------------------------------------------------------------------------
# Facts
# --------------------------------------------------------------------------


def _infer_facts(
    sheets: dict[str, pd.DataFrame],
    analysis: InvoicesARAnalysis,
    template: dict | None,
) -> dict[str, Any]:
    facts: dict[str, Any] = {
        "description_prefix": _DEFAULT_DESCRIPTION_PREFIX,
        "split_customer_code": True,
        "include_po_number": True,
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
        for key in ("entity", "currency", "description_prefix", "product"):
            if example.get(key):
                facts[key] = example[key]

    if not facts.get("currency"):
        currency = _currency_from_source(sheets, analysis)
        if currency:
            facts["currency"] = currency

    facts.setdefault("product", facts["description_prefix"])
    return facts


def _read_title_block(
    sheets: dict[str, pd.DataFrame], analysis: InvoicesARAnalysis
) -> dict[str, Any]:
    """Read entity name and 'As of' date from the rows above the header.

    QuickBooks titles a multi-entity report "company : entity"
    ("causaLens : causaLens US"); the leaf entity on the right is the one the
    report is about, so it wins over a bare company-name row.
    """
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
        if ":" in text:
            parts = [part.strip() for part in text.split(":") if part.strip()]
            if len(parts) >= 2:
                out["entity"] = parts[-1]
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

        product_idx = headers.get("product")
        if product_idx is not None and product_idx < len(values):
            product = _clean_text(values[product_idx])
            if product:
                out["product"] = product
        break
    return out


def _currency_from_source(
    sheets: dict[str, pd.DataFrame], analysis: InvoicesARAnalysis
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


def _output_row(detail: _Detail, analysis: InvoicesARAnalysis) -> dict[str, Any]:
    invoice_number = detail.invoice_number
    prefix = analysis.facts.get("description_prefix", _DEFAULT_DESCRIPTION_PREFIX)
    description = f"{prefix} - {invoice_number}" if invoice_number else prefix
    include_po = analysis.facts.get("include_po_number", True)

    row = {col: None for col in LIGHT_INVOICES_AR_COLUMNS}
    row.update(
        {
            "Entity": analysis.facts.get("entity"),
            # These are the outstanding items as of the report date.
            "Invoice Status": "Open",
            "Customer": detail.customer_name,
            "Customer ID": detail.customer_id or None,
            # Kept as text: invoice numbers like "May/July 26" and "2026-13"
            # must never be coerced to a number or a date.
            "Invoice Number": invoice_number or None,
            "Invoice Date": detail.issue_date,
            "Due Date": detail.due_date or detail.issue_date,
            # Per-row currency wins over the single report-level fact.
            "Currency": detail.row_currency or analysis.facts.get("currency"),
            "PO Number": (detail.po_number or None) if include_po else None,
            "Description": description,
            # The sign carries the document type: a positive amount is an
            # invoice, a negative amount is a credit note. When the report
            # has a transaction-currency column, that amount goes to Invoice
            # Currency Amount (and prices the line); the local open balance
            # always goes to Local Currency Amount.
            "Invoice Currency Amount": (
                detail.txn_amount if detail.txn_amount is not None else detail.amount
            ),
            "Local Currency Amount": detail.amount,
            # One line per item, matching the worked example: the open
            # balance as a single unit-priced product line, in the
            # document's own currency.
            "Product": analysis.facts.get("product", prefix),
            "Quantity": 1,
            "Unit Price": (
                detail.txn_amount if detail.txn_amount is not None else detail.amount
            ),
        }
    )
    return row


# --------------------------------------------------------------------------
# Readiness, assumptions, warnings
# --------------------------------------------------------------------------


def _missing_items(analysis: InvoicesARAnalysis) -> list[str]:
    missing: list[str] = []
    if not analysis.source_sheet or analysis.header_row is None:
        missing.append("source_sheet")
    if "invoice_number" not in analysis.roles:
        missing.append("invoice_number")
    if "customer_name" not in analysis.roles:
        missing.append("customer_name")
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


def _refresh_derived(analysis: InvoicesARAnalysis) -> None:
    analysis.assumptions = _infer_assumptions(analysis)
    analysis.questions = _missing_questions(analysis)
    analysis.warnings = _infer_warnings(analysis)


def _infer_assumptions(analysis: InvoicesARAnalysis) -> list[str]:
    facts = analysis.facts
    assumptions = [
        "One row per open item, at its open balance as of the report date — not the "
        "original document amount.",
        "A positive amount is an invoice; a negative amount is a credit note in the same "
        "format. Payments, credit memos and zero-balance customers are never netted or "
        "dropped, so the upload ties to the report total.",
        f"Each row gets Invoice Status `Open` and one line: Product "
        f"`{facts.get('product', _DEFAULT_DESCRIPTION_PREFIX)}`, Quantity 1, Unit Price equal "
        "to the open balance — matching the worked example.",
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
            f"Currency Amount and Unit Price from `{analysis.roles['open_amount_txn']}` and "
            f"Local Currency Amount from `{local_header}`."
        )
    else:
        assumptions.append(
            f"Entity `{facts.get('entity', '?')}` and currency `{facts.get('currency', '?')}` "
            "apply to every row; the report has no per-row entity or currency."
        )
        assumptions.append(
            "Invoice currency amount equals local currency amount, so no FX rate is applied."
        )
    if facts.get("split_customer_code", True) and analysis.split_example:
        example = analysis.split_example
        assumptions.append(
            f"Customer strings are split into code and name "
            f"(`{example['raw']}` -> Customer ID `{example['id']}`, "
            f"Customer `{example['name']}`). Say `do not split the customer code` "
            "to keep the full string in Customer instead."
        )
    if facts.get("include_po_number", True) and "po_number" in analysis.roles:
        assumptions.append(
            f"Customer PO references from `{analysis.roles['po_number']}` are carried into "
            "`PO Number`. Say `leave the PO number blank` to drop them."
        )
    assumptions.append(
        "Tax, account, billing and accrual columns are left blank, matching the worked "
        "example — Light derives them from the product and entity settings."
    )
    return assumptions


def _missing_questions(analysis: InvoicesARAnalysis) -> list[str]:
    questions = []
    for item in _missing_items(analysis):
        if item == "entity":
            questions.append("Which Light entity should these invoices belong to?")
        elif item == "currency":
            questions.append(
                "Which currency are these invoices in? The report has no currency column."
            )
        elif item == "source_sheet":
            questions.append("Which sheet contains the open AR invoice detail?")
        elif item == "amount":
            questions.append("Which column holds the open balance to migrate?")
        elif item == "open_items":
            questions.append(
                "No open AR rows could be read from the source sheet. Which rows hold "
                "the open invoices and credit notes?"
            )
        else:
            questions.append(f"How should I fill `{item}`?")
    return questions


def _infer_warnings(analysis: InvoicesARAnalysis) -> list[str]:
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

    if analysis.missing_date_customers:
        warnings.append(
            "Rows with no readable invoice date for: "
            + ", ".join(analysis.missing_date_customers[:5])
            + "."
        )
    return warnings
