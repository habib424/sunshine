"""
Deterministic open AP upload to the Light AP upload layout.

The source files seen so far are Fortnox-style AP ledgers with invoice, vendor,
voucher, currency, amount, and remaining balance columns. If the workbook also
contains a Light Posting reference sheet, we reuse matching posting lines. If
not, we create balanced opening AP entries using a configurable AP liability
account and clearing account.

Two source shapes are supported:

*flat* -- one row per invoice, every column populated on every row. This is the
Fortnox export the module was originally built for.

*grouped* -- QuickBooks/Xero-style "A/P Aging Detail" reports, which are
hierarchical rather than tabular:

    Vendor | Transaction Type | Date | Document Number | Due Date | Age | Open Balance
    Supplier                                       <- section header
    S002 Addison Lee                               <- vendor group header
          | Bill | 2026-07-31 | 3428325 | ... | 319.01   <- detail row, Vendor blank
    Total - S002 Addison Lee |              | 319.01     <- subtotal
    ...
    Total                    |              | 53178.08   <- grand total

The vendor lives on its own group-header row, so detail rows carry an empty
vendor cell; a reader that requires a vendor on every row drops the whole
report and keeps only the subtotals. Section headers, per-vendor subtotals and
the grand total must be skipped or the migration double-counts.

Selecting the source sheet has the same trap: a workbook often ships the Light
target template alongside the data, and a tidy 1-row template header will
outscore a real ledger whose header sits below a title block unless data volume
counts and known Light layouts are demoted.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from app.engine.target_schemas import TARGET_SCHEMAS


OPEN_AP_INTENT = "upload_open_ap_to_light_ap"
OPEN_AP_INTENTS = {OPEN_AP_INTENT}


LIGHT_OPEN_AP_COLUMNS = [
    "Entity",
    "Document Number",
    "Currency",
    "Posting Date",
    "Ledger",
    "Business Partner",
    "Entry Description",
    "Account",
    "Debit",
    "Credit",
    "Line Description",
    "Tax Code",
    "Release Template",
    "Release Start Date",
    "Release End Date",
    "Departments (line)",
]


_ROLE_KEYWORDS: dict[str, list[str]] = {
    "open_amount_local": [
        "saldo i sek",
        "balance in sek",
        "open amount in sek",
        "remaining amount in sek",
        "local open amount",
    ],
    "amount_local": [
        "belopp i sek",
        "amount in sek",
        "local amount",
        "amount local",
    ],
    "invoice_number": [
        "fortnox invoice nr",
        "invoice number",
        "invoice nr",
        "invoice no",
        "fakturanummer",
        "fakturanr",
        # Aging reports label the invoice reference "Document Number".
        "document number",
        "document no",
        "doc number",
        "bill number",
        "bill no",
    ],
    "vendor_name": [
        "leverantor",
        "supplier",
        "vendor",
        "vendor name",
        "supplier name",
    ],
    "invoice_date": [
        "faktdat",
        "fakturadatum",
        "invoice date",
        "document date",
        "date",
    ],
    "due_date": [
        "forfdat",
        "forfallodatum",
        "forfallsdatum",
        "due date",
    ],
    "voucher_number": [
        "vernr",
        "voucher",
        "voucher number",
        "voucher no",
        "verifikation",
    ],
    "currency": [
        "valuta",
        "currency",
        "currency code",
    ],
    "open_amount": [
        "saldo",
        "balance",
        "open amount",
        "remaining amount",
    ],
    "amount": [
        "belopp",
        "amount",
    ],
}


_LIGHT_HEADER_SYNONYMS: dict[str, str] = {
    "entity": "Entity",
    "document number": "Document Number",
    "currency": "Currency",
    "posting date": "Posting Date",
    "ledger": "Ledger",
    "business partner": "Business Partner",
    "entry description": "Entry Description",
    "account": "Account",
    "debit": "Debit",
    "credit": "Credit",
    "line description": "Line Description",
    "tax code": "Tax Code",
    "release template": "Release Template",
    "release start date": "Release Start Date",
    "release end date": "Release End Date",
    "departments line": "Departments (line)",
    "kostnadsstalle": "Departments (line)",
}


_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_ACCOUNT_RE = re.compile(r"\b(\d{4,8})\b")
_DATE_RE = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})|(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})")

# "Total", "Total - Supplier", "Total - S002 Addison Lee".
_TOTAL_RE = re.compile(r"^total\b", re.IGNORECASE)


@dataclass
class OpenAPAnalysis:
    source_sheet: str | None = None
    header_row: int | None = None
    layout: str = "flat"  # "flat" | "grouped"
    roles: dict[str, str] = field(default_factory=dict)
    role_confidence: dict[str, float] = field(default_factory=dict)
    reference_sheet: str | None = None
    reference_header_row: int | None = None
    facts: dict[str, Any] = field(default_factory=dict)
    assumptions: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    confidence: float = 0.0
    valid_source_rows: int = 0

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
            "reference_sheet": self.reference_sheet,
            "reference_header_row": self.reference_header_row,
            "facts": self.facts,
            "assumptions": self.assumptions,
            "questions": self.questions,
            "warnings": self.warnings,
            "confidence": self.confidence,
            "valid_source_rows": self.valid_source_rows,
            "ready": self.ready,
        }


def analyze_open_ap_workbook(file_path: Path) -> OpenAPAnalysis:
    sheets = pd.read_excel(file_path, sheet_name=None, header=None, engine="openpyxl")
    analysis = OpenAPAnalysis()

    ref = _find_light_posting_sheet(sheets)
    if ref:
        analysis.reference_sheet = ref["sheet"]
        analysis.reference_header_row = ref["header_row"]

    source = _find_source_sheet_and_roles(sheets, analysis.reference_sheet)
    if source:
        analysis.source_sheet = source["sheet"]
        analysis.header_row = source["header_row"]
        analysis.layout = source["layout"]
        analysis.roles = source["roles"]
        analysis.role_confidence = source["role_confidence"]
        analysis.confidence = source["confidence"]
        analysis.valid_source_rows = _count_valid_rows(file_path, analysis)
    else:
        analysis.questions.append("Which sheet contains the open AP invoice ledger?")

    analysis.facts.update(_infer_facts(file_path, analysis))
    analysis.assumptions = _infer_assumptions(analysis)
    analysis.questions = _missing_questions(analysis)
    analysis.warnings = _infer_warnings(file_path, analysis)
    return analysis


def apply_user_message_to_analysis(
    analysis: OpenAPAnalysis,
    message: str,
    file_path: Path,
) -> OpenAPAnalysis:
    facts = dict(analysis.facts)

    entity = _parse_named_value(message, ("entity", "company"))
    if entity:
        facts["entity"] = entity

    ledger = _parse_named_value(message, ("ledger",))
    if ledger:
        facts["ledger"] = ledger

    local_currency = _parse_named_value(message, ("local currency", "currency"))
    if local_currency and re.fullmatch(r"[A-Za-z]{3}", local_currency.strip()):
        facts["local_currency"] = local_currency.strip().upper()

    ap_account = _parse_account(message, ("ap account", "accounts payable account", "liability account"))
    if ap_account:
        facts["ap_account"] = ap_account

    clearing_account = _parse_account(message, ("clearing account", "offset account", "debit account"))
    if clearing_account:
        facts["clearing_account"] = clearing_account

    posting_date = _parse_any_date(message)
    if posting_date:
        facts["posting_date"] = posting_date

    refreshed = analyze_open_ap_workbook(file_path)
    refreshed.facts.update({k: v for k, v in facts.items() if v not in (None, "")})
    refreshed.assumptions = _infer_assumptions(refreshed)
    refreshed.questions = _missing_questions(refreshed)
    refreshed.warnings = _infer_warnings(file_path, refreshed)
    return refreshed


def transform_open_ap_to_light_ap(file_path: Path, analysis: OpenAPAnalysis) -> pd.DataFrame:
    missing = _missing_items(analysis)
    if missing:
        raise ValueError(f"Open AP upload is missing required information: {', '.join(missing)}")
    if analysis.source_sheet is None or analysis.header_row is None:
        raise ValueError("Open AP upload needs a source sheet and header row")

    rows = _read_source_rows(file_path, analysis)
    reference = _read_reference_postings(file_path, analysis)

    output_rows: list[dict[str, Any]] = []
    for _, row in rows.iterrows():
        invoice_number = _clean_reference(row[analysis.roles["invoice_number"]])
        vendor_name = _clean_text(row[analysis.roles["vendor_name"]])
        voucher = _clean_text(row[analysis.roles.get("voucher_number")]) if analysis.roles.get("voucher_number") else ""
        amount = _source_amount(row, analysis.roles)
        if amount is None or math.isclose(amount, 0.0, abs_tol=0.005):
            continue

        ref_lines = _find_reference_lines(reference, invoice_number, voucher)
        if not ref_lines.empty:
            output_rows.extend(_reference_output_rows(ref_lines, analysis, amount))
            continue

        output_rows.extend(_fallback_output_rows(row, analysis, invoice_number, vendor_name, voucher, amount))

    return pd.DataFrame(output_rows, columns=LIGHT_OPEN_AP_COLUMNS)


def format_open_ap_analysis_message(analysis: OpenAPAnalysis) -> str:
    lines = ["I found an open AP upload layout."]

    if analysis.source_sheet:
        shape = "grouped by vendor" if analysis.layout == "grouped" else "one row per invoice"
        lines.append(
            f"- Source sheet: `{analysis.source_sheet}` with header row {analysis.header_row}, "
            f"{shape}, and {analysis.valid_source_rows} invoice rows."
        )
    if analysis.roles:
        role_bits = [f"`{source}` -> {role}" for role, source in sorted(analysis.roles.items())]
        lines.append("- Column roles: " + ", ".join(role_bits))
    if analysis.reference_sheet:
        lines.append(
            f"- Light Posting reference: `{analysis.reference_sheet}`. Matching invoices will reuse those posting lines."
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
        lines.append("\nReply with the missing fact, for example: `entity is Grasp Research AB`.")
    else:
        lines.append("\nReady to run. I will append a Light AP upload sheet to the original workbook.")

    return "\n".join(lines)


def _find_source_sheet_and_roles(sheets: dict[str, pd.DataFrame], reference_sheet: str | None) -> dict | None:
    template_sheets = _find_template_sheets(sheets)
    candidates: list[dict] = []
    for name, df in sheets.items():
        if name == reference_sheet:
            continue
        candidate = _detect_ap_sheet_layout(name, df)
        if candidate:
            candidate["is_template"] = name in template_sheets
            candidates.append(candidate)
    if not candidates:
        return None

    # A sheet in one of Light's own upload layouts is the target template or a
    # worked example, not the source ledger. It only wins when the workbook
    # offers nothing else, because an AP export already delivered in Light's
    # layout is still a legitimate source.
    preferred = [c for c in candidates if not c["is_template"]] or candidates
    return max(preferred, key=lambda c: c["confidence"])


def _find_template_sheets(sheets: dict[str, pd.DataFrame]) -> set[str]:
    """Sheets whose header row is one of Light's own upload layouts."""
    layouts = [
        {_normalise(col) for col in schema["columns"]}
        for name, schema in TARGET_SCHEMAS.items()
        if name.startswith("light_")
    ]
    found: set[str] = set()
    for name, df in sheets.items():
        for row_idx in range(min(10, len(df))):
            values = {_normalise(v) for v in df.iloc[row_idx].tolist() if not _is_blank(v)}
            values.discard("")
            if any(len(layout & values) >= 8 for layout in layouts):
                found.add(name)
                break
    return found


def _detect_ap_sheet_layout(sheet_name: str, df: pd.DataFrame, max_rows: int = 15) -> dict | None:
    best: dict | None = None
    for row_idx in range(min(max_rows, len(df))):
        score, roles, role_confidence = _score_ap_header_row(df, row_idx)
        if best is None or score > best["confidence"]:
            best = {
                "sheet": sheet_name,
                "header_row": row_idx,
                "roles": roles,
                "role_confidence": role_confidence,
                "confidence": score,
            }
    # The volume term below is worth 0.30 on its own, so score alone would let
    # any densely populated sheet clear the threshold. Require real role
    # evidence too, or a per-entity summary or an FX journal reads as an AP
    # ledger just for being long.
    if not best or best["confidence"] < 0.45 or _required_role_hits(best["roles"]) < 2:
        return None
    best["layout"] = _detect_grouping(df, best["header_row"], best["roles"])
    return best


def _required_role_hits(roles: dict[str, str]) -> int:
    """How many of the four things an AP invoice row needs were identified."""
    return sum(
        1
        for role_group in (
            ("invoice_number",),
            ("vendor_name",),
            ("invoice_date", "due_date"),
            ("open_amount_local", "amount_local", "open_amount", "amount"),
        )
        if any(role in roles for role in role_group)
    )


def _score_ap_header_row(df: pd.DataFrame, row_idx: int) -> tuple[float, dict[str, str], dict[str, float]]:
    row = df.iloc[row_idx].tolist()
    roles: dict[str, str] = {}
    role_confidence: dict[str, float] = {}
    non_empty = 0
    labelish = 0

    for value in row:
        if _is_blank(value):
            continue
        non_empty += 1
        header = _normalise(value)
        if not _looks_numeric(value):
            labelish += 1
        match = _match_role(header, set(roles))
        if match:
            role, score = match
            roles[role] = str(value)
            role_confidence[role] = score

    if non_empty == 0:
        return 0.0, {}, {}

    role_score = _required_role_hits(roles) / 4
    bonus_roles = len(set(roles) & {"currency", "voucher_number", "due_date"}) / 6
    label_ratio = labelish / non_empty

    # Data volume is the term that stops a 1-row target template from beating
    # the real ledger just because its header labels are tidier.
    volume = min(_count_data_rows(df, row_idx) / 10.0, 1.0)

    score = (0.50 * role_score) + (0.12 * bonus_roles) + (0.08 * label_ratio) + (0.30 * volume)
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


def _column_index(df: pd.DataFrame, header_row: int, header: str | None) -> int | None:
    if header is None:
        return None
    target = _normalise(header)
    for idx, value in enumerate(df.iloc[header_row].tolist()):
        if _normalise(value) == target:
            return idx
    return None


def _match_role(header: str, claimed_roles: set[str]) -> tuple[str, float] | None:
    candidates: list[tuple[int, str, float]] = []
    for role, keywords in _ROLE_KEYWORDS.items():
        if role in claimed_roles:
            continue
        for keyword in keywords:
            norm_keyword = _normalise(keyword)
            if header == norm_keyword:
                candidates.append((len(norm_keyword), role, 1.0))
            elif _word_boundary_match(norm_keyword, header):
                candidates.append((len(norm_keyword), role, 0.9))
    if not candidates:
        return None
    _, role, score = max(candidates, key=lambda c: (c[0], c[2]))
    return role, score


def _find_light_posting_sheet(sheets: dict[str, pd.DataFrame]) -> dict | None:
    required = {"entity", "document number", "posting date", "account", "debit", "credit"}
    for name, df in sheets.items():
        for row_idx in range(min(10, len(df))):
            values = {_normalise(v) for v in df.iloc[row_idx].tolist() if pd.notna(v)}
            canonical = {_LIGHT_HEADER_SYNONYMS.get(v, v) for v in values}
            canonical_norm = {_normalise(v) for v in canonical}
            if len(required & canonical_norm) >= 5:
                return {"sheet": name, "header_row": row_idx}
    return None


def _infer_facts(file_path: Path, analysis: OpenAPAnalysis) -> dict[str, Any]:
    facts: dict[str, Any] = {
        "ledger": "Primary",
        "ap_account": "221101",
        "clearing_account": "111500",
    }

    if analysis.reference_sheet is not None:
        ref = _read_reference_postings(file_path, analysis)
        if not ref.empty:
            for col, fact in (("Entity", "entity"), ("Ledger", "ledger"), ("Currency", "local_currency")):
                if col in ref.columns:
                    value = _first_non_empty(ref[col])
                    if value:
                        facts[fact] = value
            ap_account = _most_common_credit_account(ref)
            if ap_account:
                facts["ap_account"] = ap_account
    else:
        local_currency = _infer_local_currency_from_roles(analysis.roles)
        if local_currency:
            facts["local_currency"] = local_currency

    facts.setdefault("local_currency", _infer_local_currency_from_roles(analysis.roles) or "SEK")
    return facts


def _infer_assumptions(analysis: OpenAPAnalysis) -> list[str]:
    assumptions = [
        f"Target currency is `{analysis.facts.get('local_currency', 'SEK')}` because the AP export has local-currency balance columns.",
        "Posting date uses the source invoice date unless you provide a migration posting date.",
    ]
    if not analysis.reference_sheet:
        assumptions.append(
            "No Light Posting reference sheet was found, so unmatched invoices will be balanced with "
            f"AP account `{analysis.facts.get('ap_account', '221101')}` and clearing account "
            f"`{analysis.facts.get('clearing_account', '111500')}`."
        )
    return assumptions


def _missing_questions(analysis: OpenAPAnalysis) -> list[str]:
    questions = []
    for item in _missing_items(analysis):
        if item == "entity":
            questions.append("Which Light entity should these AP opening entries use?")
        elif item == "amount":
            questions.append("Which source column contains the open AP amount to migrate?")
        elif item == "source_sheet":
            questions.append("Which sheet contains the open AP invoice ledger?")
        else:
            questions.append(f"How should I fill `{item}`?")
    return questions


def _missing_items(analysis: OpenAPAnalysis) -> list[str]:
    missing: list[str] = []
    if not analysis.source_sheet or analysis.header_row is None:
        missing.append("source_sheet")
    if "invoice_number" not in analysis.roles:
        missing.append("invoice_number")
    if "vendor_name" not in analysis.roles:
        missing.append("vendor_name")
    if not any(role in analysis.roles for role in ("invoice_date", "due_date")):
        missing.append("invoice_date")
    if not any(role in analysis.roles for role in ("open_amount_local", "amount_local", "open_amount", "amount")):
        missing.append("amount")
    if not analysis.facts.get("entity"):
        missing.append("entity")
    return missing


def _infer_warnings(file_path: Path, analysis: OpenAPAnalysis) -> list[str]:
    warnings: list[str] = []
    if analysis.reference_sheet:
        try:
            ref = _read_reference_postings(file_path, analysis)
            rows = _read_source_rows(file_path, analysis) if analysis.source_sheet else pd.DataFrame()
            missing_matches = 0
            for _, row in rows.iterrows():
                invoice = _clean_reference(row[analysis.roles["invoice_number"]])
                voucher = _clean_text(row[analysis.roles.get("voucher_number")]) if analysis.roles.get("voucher_number") else ""
                if not _find_reference_lines(ref, invoice, voucher):
                    missing_matches += 1
            if missing_matches:
                warnings.append(
                    f"{missing_matches} invoice(s) did not match the Light Posting reference and will use fallback accounts."
                )
        except Exception:
            warnings.append("Could not verify every invoice against the Light Posting reference sheet.")
    return warnings


def _read_source_rows(file_path: Path, analysis: OpenAPAnalysis) -> pd.DataFrame:
    if analysis.layout == "grouped":
        raw = _read_grouped_source_rows(file_path, analysis)
    else:
        raw = _read_flat_source_rows(file_path, analysis)
    return _filter_source_rows(raw, analysis).reset_index(drop=True)


def _read_flat_source_rows(file_path: Path, analysis: OpenAPAnalysis) -> pd.DataFrame:
    raw = pd.read_excel(
        file_path,
        sheet_name=analysis.source_sheet,
        header=analysis.header_row,
        engine="openpyxl",
    )
    return raw.dropna(how="all").copy()


def _read_grouped_source_rows(file_path: Path, analysis: OpenAPAnalysis) -> pd.DataFrame:
    """Flatten a hierarchical aging report into one row per invoice.

    The vendor is carried down from its group-header row onto every detail row
    beneath it. Section headers, per-vendor subtotals and the grand total are
    dropped: their amounts are already present in the detail rows, so keeping
    them would double-count the migration.
    """
    df = pd.read_excel(
        file_path,
        sheet_name=analysis.source_sheet,
        header=None,
        engine="openpyxl",
    )
    header_row = analysis.header_row or 0
    columns = _mangle_headers(df.iloc[header_row].tolist())
    vendor_idx = _column_index(df, header_row, analysis.roles.get("vendor_name"))

    records: list[list[Any]] = []
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
        others = [v for i, v in enumerate(values) if i != vendor_idx and not _is_blank(v)]

        # Subtotal / grand total rows: "Total", "Total - Supplier", "Total - S002 ...".
        if vendor_cell and _TOTAL_RE.match(vendor_cell):
            continue

        # Group header row: a label alone on the row. Section headers such as
        # "Supplier" are naturally superseded because the next group header
        # overwrites them before any detail row is read.
        if vendor_cell and not others:
            current_vendor = vendor_cell
            continue

        vendor = vendor_cell or current_vendor
        if not vendor:
            continue

        record = list(values[: len(columns)])
        record += [None] * (len(columns) - len(record))
        if vendor_idx is not None and vendor_idx < len(record):
            record[vendor_idx] = vendor
        records.append(record)

    return pd.DataFrame(records, columns=columns)


def _mangle_headers(row: list[Any]) -> list[str]:
    """Header labels as `pd.read_excel` would name them, so roles still match."""
    columns: list[str] = []
    seen: dict[str, int] = {}
    for idx, value in enumerate(row):
        name = f"Unnamed: {idx}" if _is_blank(value) else str(value)
        if name in seen:
            seen[name] += 1
            name = f"{name}.{seen[name]}"
        else:
            seen[name] = 0
        columns.append(name)
    return columns


def _filter_source_rows(raw: pd.DataFrame, analysis: OpenAPAnalysis) -> pd.DataFrame:
    required_cols = [analysis.roles[r] for r in ("invoice_number", "vendor_name") if r in analysis.roles]
    for col in required_cols:
        if col not in raw.columns:
            continue
        raw = raw[raw[col].notna() & (raw[col].astype(str).str.strip() != "")]

    amount_col = _amount_role_column(analysis.roles)
    if amount_col and amount_col in raw.columns:
        numeric = raw[amount_col].apply(_to_float)
        raw = raw[numeric.notna() & (numeric.abs() > 0.005)]

    return raw


def _count_valid_rows(file_path: Path, analysis: OpenAPAnalysis) -> int:
    try:
        return int(len(_read_source_rows(file_path, analysis)))
    except Exception:
        return 0


def _read_reference_postings(file_path: Path, analysis: OpenAPAnalysis) -> pd.DataFrame:
    if analysis.reference_sheet is None:
        return pd.DataFrame(columns=LIGHT_OPEN_AP_COLUMNS)
    header_row = analysis.reference_header_row if analysis.reference_header_row is not None else 0
    ref = pd.read_excel(file_path, sheet_name=analysis.reference_sheet, header=header_row, engine="openpyxl")
    rename: dict[Any, str] = {}
    for col in ref.columns:
        normalized = _normalise(col)
        target = _LIGHT_HEADER_SYNONYMS.get(normalized)
        if target:
            rename[col] = target
    ref = ref.rename(columns=rename)
    for col in LIGHT_OPEN_AP_COLUMNS:
        if col not in ref.columns:
            ref[col] = None
    return ref[LIGHT_OPEN_AP_COLUMNS].dropna(how="all").reset_index(drop=True)


def _find_reference_lines(ref: pd.DataFrame, invoice_number: str, voucher: str) -> pd.DataFrame:
    if ref.empty:
        return ref

    invoice_number = _clean_reference(invoice_number)
    voucher_key = _voucher_key(voucher)

    if voucher_key:
        doc_mask = ref["Document Number"].astype(str).apply(lambda v: _voucher_key(v).endswith(voucher_key))
        matches = ref[doc_mask]
        if not matches.empty:
            return _invoice_reference_only(matches, invoice_number)

    if invoice_number:
        desc = (
            ref["Entry Description"].fillna("").astype(str)
            + " "
            + ref["Line Description"].fillna("").astype(str)
        )
        inv_mask = desc.apply(lambda v: _description_has_invoice(v, invoice_number))
        matches = ref[inv_mask]
        if not matches.empty:
            return _invoice_reference_only(matches, invoice_number)

    return ref.iloc[0:0]


def _invoice_reference_only(matches: pd.DataFrame, invoice_number: str) -> pd.DataFrame:
    desc = (
        matches["Entry Description"].fillna("").astype(str)
        + " "
        + matches["Line Description"].fillna("").astype(str)
    ).apply(_normalise)
    invoice_mask = desc.str.contains("levfakt|vendor invoice|supplier invoice|invoice", regex=True)
    if invoice_number:
        invoice_mask = invoice_mask | desc.apply(lambda text: _description_has_invoice(text, invoice_number))
    filtered = matches[invoice_mask]
    return filtered if not filtered.empty else matches


def _reference_output_rows(ref_lines: pd.DataFrame, analysis: OpenAPAnalysis, source_amount: float) -> list[dict[str, Any]]:
    lines = ref_lines.copy()
    ratio = _reference_scale_ratio(lines, analysis, source_amount)
    rows: list[dict[str, Any]] = []

    for _, ref_row in lines.iterrows():
        out = {col: ref_row.get(col) for col in LIGHT_OPEN_AP_COLUMNS}
        out["Entity"] = analysis.facts["entity"]
        out["Ledger"] = analysis.facts.get("ledger") or out.get("Ledger") or "Primary"
        out["Currency"] = analysis.facts.get("local_currency") or out.get("Currency") or "SEK"
        if analysis.facts.get("posting_date"):
            out["Posting Date"] = analysis.facts["posting_date"]
        out["Debit"] = _scaled_amount(out.get("Debit"), ratio)
        out["Credit"] = _scaled_amount(out.get("Credit"), ratio)
        rows.append(_blank_nan(out))
    return rows


def _reference_scale_ratio(ref_lines: pd.DataFrame, analysis: OpenAPAnalysis, source_amount: float) -> float:
    ap_account = str(analysis.facts.get("ap_account", "221101"))
    account_text = ref_lines["Account"].apply(lambda v: str(v).split(".")[0] if pd.notna(v) else "")
    ap_lines = ref_lines[account_text == ap_account]
    if ap_lines.empty:
        return 1.0
    credit = ap_lines["Credit"].apply(_to_float).fillna(0).sum()
    debit = ap_lines["Debit"].apply(_to_float).fillna(0).sum()
    ref_amount = float(credit - debit)
    if math.isclose(ref_amount, 0.0, abs_tol=0.005):
        return 1.0
    if math.isclose(ref_amount, source_amount, abs_tol=0.01):
        return 1.0
    return source_amount / ref_amount


def _fallback_output_rows(
    source_row: pd.Series,
    analysis: OpenAPAnalysis,
    invoice_number: str,
    vendor_name: str,
    voucher: str,
    amount: float,
) -> list[dict[str, Any]]:
    posting_date = analysis.facts.get("posting_date") or _source_date(source_row, analysis.roles)
    document_number = _document_number(voucher, posting_date, invoice_number)
    line_description = f"Levfakt {vendor_name} ({invoice_number})".strip()
    entry_description = f"Data migration - {line_description}"

    ap_account = analysis.facts.get("ap_account", "221101")
    clearing_account = analysis.facts.get("clearing_account", "111500")
    common = {
        "Entity": analysis.facts["entity"],
        "Document Number": document_number,
        "Currency": analysis.facts.get("local_currency", "SEK"),
        "Posting Date": posting_date,
        "Ledger": analysis.facts.get("ledger", "Primary"),
        "Business Partner": None,
        "Entry Description": entry_description,
        "Line Description": line_description,
        "Tax Code": None,
        "Release Template": None,
        "Release Start Date": None,
        "Release End Date": None,
        "Departments (line)": None,
    }

    amount = round(float(amount), 2)
    if amount >= 0:
        ap_line = {**common, "Account": ap_account, "Debit": None, "Credit": amount}
        offset_line = {**common, "Account": clearing_account, "Debit": amount, "Credit": None}
    else:
        abs_amount = abs(amount)
        ap_line = {**common, "Account": ap_account, "Debit": abs_amount, "Credit": None}
        offset_line = {**common, "Account": clearing_account, "Debit": None, "Credit": abs_amount}

    return [_blank_nan(ap_line), _blank_nan(offset_line)]


def _source_amount(row: pd.Series, roles: dict[str, str]) -> float | None:
    col = _amount_role_column(roles)
    if not col:
        return None
    return _to_float(row.get(col))


def _amount_role_column(roles: dict[str, str]) -> str | None:
    for role in ("open_amount_local", "amount_local", "open_amount", "amount"):
        if role in roles:
            return roles[role]
    return None


def _source_date(row: pd.Series, roles: dict[str, str]) -> str:
    for role in ("invoice_date", "due_date"):
        if role in roles:
            parsed = _to_date(row.get(roles[role]))
            if parsed:
                return parsed
    return datetime.utcnow().date().isoformat()


def _document_number(voucher: str, posting_date: str, invoice_number: str) -> str:
    voucher = _clean_text(voucher)
    if voucher:
        suffix = re.sub(r"\s+", "-", voucher.strip().upper())
        year = str(posting_date)[:4] if posting_date else ""
        return f"{year}-{suffix}" if year and not suffix.startswith(year) else suffix
    return f"AP-{invoice_number}"


def _most_common_credit_account(ref: pd.DataFrame) -> str | None:
    if ref.empty or "Account" not in ref.columns:
        return None
    credits = ref[ref["Credit"].apply(lambda v: (_to_float(v) or 0) > 0)]
    accounts = [str(v).split(".")[0] for v in credits["Account"].dropna().tolist()]
    if not accounts:
        return None
    return Counter(accounts).most_common(1)[0][0]


def _infer_local_currency_from_roles(roles: dict[str, str]) -> str | None:
    for role in ("open_amount_local", "amount_local"):
        header = roles.get(role, "")
        match = re.search(r"\b([A-Z]{3})\b", str(header).upper())
        if match:
            return match.group(1)
        if "sek" in _normalise(header):
            return "SEK"
    return None


def _parse_named_value(message: str, names: tuple[str, ...]) -> str | None:
    for name in names:
        pattern = rf"\b{re.escape(name)}\b\s*(?:is|=|:)\s*([^\n,;]+)"
        match = re.search(pattern, message, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip().strip("`'\"")
    return None


def _parse_account(message: str, names: tuple[str, ...]) -> str | None:
    for name in names:
        pattern = rf"\b{re.escape(name)}\b\s*(?:is|=|:)?\s*(\d{{4,8}})"
        match = re.search(pattern, message, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def _parse_any_date(message: str) -> str | None:
    match = _DATE_RE.search(message)
    if not match:
        return None
    return _to_date(match.group(0))


def _description_has_invoice(text: str, invoice_number: str) -> bool:
    if not invoice_number:
        return False
    norm_text = _normalise(text)
    norm_invoice = _normalise(invoice_number)
    return bool(norm_invoice and re.search(rf"(^|\s|\(){re.escape(norm_invoice)}($|\s|\))", norm_text))


def _voucher_key(value: Any) -> str:
    text = _normalise(value)
    if not text:
        return ""
    parts = text.split()
    if len(parts) >= 2 and parts[-1].isdigit():
        return f"{parts[-2]}{parts[-1]}"
    return "".join(parts[-2:]) if len(parts) > 1 else text.replace(" ", "")


def _scaled_amount(value: Any, ratio: float) -> float | None:
    num = _to_float(value)
    if num is None or math.isclose(num, 0.0, abs_tol=0.005):
        return None
    return round(float(num) * ratio, 2)


def _to_float(value: Any) -> float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none"}:
        return None
    text = text.replace("\u00a0", "").replace(" ", "").replace("$", "").replace("€", "")
    if "," in text and "." in text:
        text = text.replace(",", "")
    elif "," in text:
        text = text.replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def _to_date(value: Any) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, pd.Timestamp):
        return value.date().isoformat()
    if isinstance(value, datetime):
        return value.date().isoformat()

    numeric = _to_float(value)
    if numeric is not None and 20000 <= numeric <= 80000:
        parsed = pd.to_datetime(numeric, unit="D", origin="1899-12-30", errors="coerce")
    else:
        parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.date().isoformat()


def _clean_text(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none"} else text


def _clean_reference(value: Any) -> str:
    text = _clean_text(value)
    if re.fullmatch(r"\d+\.0", text):
        return text[:-2]
    return text


def _normalise(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("„", "a").replace("”", "o").replace("‚", "e")
    return _NON_ALNUM.sub(" ", text.lower()).strip()


def _word_boundary_match(keyword: str, text: str) -> bool:
    kw_words = keyword.split()
    text_words = text.split()
    if not kw_words:
        return False
    for i in range(len(text_words) - len(kw_words) + 1):
        if text_words[i : i + len(kw_words)] == kw_words:
            return True
    return False


def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and pd.isna(value):
        return True
    return str(value).strip() == ""


def _looks_numeric(value: Any) -> bool:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return True
    try:
        float(str(value).replace(",", ""))
        return True
    except ValueError:
        return False


def _first_non_empty(series: pd.Series) -> Any | None:
    for value in series:
        text = _clean_text(value)
        if text:
            return text
    return None


def _blank_nan(row: dict[str, Any]) -> dict[str, Any]:
    cleaned = {}
    for col in LIGHT_OPEN_AP_COLUMNS:
        value = row.get(col)
        if value is None or (isinstance(value, float) and pd.isna(value)):
            cleaned[col] = None
        else:
            cleaned[col] = value
    return cleaned
