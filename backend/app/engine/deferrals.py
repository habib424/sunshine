"""
Intent-led deferred cost / deferred revenue migration to Light JE upload.

This module is deliberately deterministic. The assistant may help identify
missing facts, but once roles and assumptions are known, this code produces the
upload file without generated Python.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import pandas as pd


Direction = Literal["cost", "revenue"]


LIGHT_JE_V2_COLUMNS = [
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


DEFERRAL_INTENTS = {
    "migrate_deferrals_to_light_je",
    "migrate_deferred_cost_to_light_je",
    "migrate_deferred_revenue_to_light_je",
}


_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_DATE_IN_TEXT = re.compile(r"(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})")


@dataclass
class DeferralAnalysis:
    direction: Direction
    source_sheets: list[dict[str, Any]] = field(default_factory=list)
    source_sheet: str | None = None
    header_row: int | None = None
    roles: dict[str, str] = field(default_factory=dict)
    role_confidence: dict[str, float] = field(default_factory=dict)
    reference_sheet: str | None = None
    target_example_sheet: str | None = None
    facts: dict[str, Any] = field(default_factory=dict)
    assumptions: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    confidence: float = 0.0
    valid_source_rows: int = 0

    @property
    def ready(self) -> bool:
        required_roles = {"amount", "description", "source_reference"}
        sources_ready = (
            all(required_roles.issubset(set(source.get("roles", {}))) for source in self.source_sheets)
            if self.source_sheets
            else required_roles.issubset(set(self.roles))
        )
        required_facts = {"entity", "currency", "posting_date"}
        has_deferral_account = "deferral_account" in self.roles or "deferral_account" in self.facts
        has_release_account = "release_account" in self.roles or "release_account" in self.facts
        has_release_end = (
            "release_end_date" in self.roles
            or "release_end_date" in self.facts
            or "release_end_offset_years" in self.facts
        )
        return (
            sources_ready
            and required_facts.issubset(set(self.facts))
            and has_deferral_account
            and has_release_account
            and has_release_end
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "direction": self.direction,
            "source_sheets": self.source_sheets,
            "source_sheet": self.source_sheet,
            "header_row": self.header_row,
            "roles": self.roles,
            "role_confidence": self.role_confidence,
            "reference_sheet": self.reference_sheet,
            "target_example_sheet": self.target_example_sheet,
            "facts": {
                k: _serialise_fact(v)
                for k, v in self.facts.items()
            },
            "assumptions": self.assumptions,
            "questions": self.questions,
            "warnings": self.warnings,
            "confidence": self.confidence,
            "valid_source_rows": self.valid_source_rows,
            "ready": self.ready,
        }


def intent_to_direction(intent: str, file_path: Path | None = None) -> Direction:
    if "revenue" in intent:
        return "revenue"
    if "cost" in intent:
        return "cost"
    if file_path is not None:
        try:
            import openpyxl
            workbook = openpyxl.load_workbook(file_path, read_only=True)
            context = f"{file_path.stem} {' '.join(workbook.sheetnames)}".lower()
            workbook.close()
            if "revenue" in context or "income" in context:
                return "revenue"
        except Exception:
            pass
    return "cost"


def analyze_deferral_workbook(file_path: Path, direction: Direction) -> DeferralAnalysis:
    sheets = pd.read_excel(file_path, sheet_name=None, header=None, engine="openpyxl")
    analysis = DeferralAnalysis(direction=direction)

    analysis.target_example_sheet = _find_target_example_sheet(sheets)
    analysis.reference_sheet = _find_reference_sheet(sheets)
    source_candidates = _find_source_sheets_and_roles(sheets, analysis.target_example_sheet, direction)

    if source_candidates:
        analysis.source_sheets = source_candidates
        source_candidate = source_candidates[0]
        analysis.source_sheet = source_candidate["sheet"]
        analysis.header_row = source_candidate["header_row"]
        analysis.roles = source_candidate["roles"]
        analysis.role_confidence = source_candidate["role_confidence"]
        analysis.confidence = source_candidate["confidence"]
        analysis.valid_source_rows = _count_valid_rows(file_path, analysis)
    else:
        analysis.questions.append("I could not identify the source schedule. Which sheet contains the deferred balances?")

    analysis.facts.update(_infer_facts(sheets, analysis, file_path))
    analysis.assumptions.extend(_infer_assumptions(analysis))
    analysis.questions.extend(_conversation_questions(analysis))
    analysis.warnings.extend(_reference_warnings(file_path, analysis))
    return analysis


def apply_user_message_to_analysis(
    analysis: DeferralAnalysis,
    message: str,
    file_path: Path,
) -> DeferralAnalysis:
    lower = message.lower()

    if re.search(r"\b(?:this|migration|direction)\s+(?:is|should be)\s+(?:deferred\s+)?(?:revenue|income)\b", lower) or re.search(r"\bswitch\s+to\s+(?:deferred\s+)?(?:revenue|income)\b", lower):
        analysis.direction = "revenue"
    elif re.search(r"\b(?:this|migration|direction)\s+(?:is|should be)\s+(?:deferred\s+)?(?:cost|prepayment|prepaid)\b", lower) or re.search(r"\bswitch\s+to\s+(?:deferred\s+)?(?:cost|prepayment|prepaid)\b", lower):
        analysis.direction = "cost"

    named_values = _parse_named_values(message)
    named_values.update({
        key: value for key, value in _parse_contextual_accounts(message).items()
        if key not in named_values
    })

    if not named_values.get("entity") and _looks_like_bare_entity_answer(message, analysis):
        named_values["entity"] = message.strip().strip("`'\".,; ")

    posting_text = named_values.get("posting_date")
    if not posting_text and any("posting date" in question.lower() for question in analysis.questions):
        posting_text = message
    posting_date = _parse_any_date(posting_text) if posting_text else None
    if posting_date is not None:
        analysis.facts["posting_date"] = posting_date

    release_start_text = named_values.get("release_start_date")
    release_start = _parse_any_date(release_start_text) if release_start_text else None
    if release_start is not None:
        analysis.facts["release_start_date"] = release_start
        analysis.facts.setdefault("posting_date", release_start)

    currency = _parse_currency(message)
    if currency:
        analysis.facts["currency"] = currency
        analysis.facts.pop("_currency_evidence", None)

    entity = named_values.get("entity")
    if entity:
        analysis.facts["entity"] = entity

    template = named_values.get("release_template")
    if template:
        analysis.facts["release_template"] = template

    prefix = named_values.get("description_prefix")
    if prefix:
        analysis.facts["description_prefix"] = prefix

    for account_fact in ("deferral_account", "release_account"):
        value = named_values.get(account_fact)
        if value:
            account = _numeric_account(value)
            analysis.facts[account_fact] = account if account is not None else value

    release_end_text = named_values.get("release_end_date")
    if release_end_text:
        offset_years = _parse_year_offset(release_end_text)
        release_end_date = _parse_any_date(release_end_text)
        if offset_years is not None:
            analysis.facts["release_end_offset_years"] = offset_years
            analysis.facts.pop("release_end_date", None)
        elif release_end_date is not None:
            analysis.facts["release_end_date"] = release_end_date
            analysis.facts.pop("release_end_offset_years", None)

    # Re-run fact/validation derivations with updated user-provided facts.
    refreshed = analyze_deferral_workbook(file_path, analysis.direction)
    refreshed.facts.update(analysis.facts)
    refreshed.assumptions = _infer_assumptions(refreshed)
    refreshed.questions = _conversation_questions(refreshed)
    refreshed.warnings = _reference_warnings(file_path, refreshed)
    return refreshed


def apply_structured_updates_to_analysis(
    analysis: DeferralAnalysis,
    updates: dict[str, str],
    file_path: Path,
) -> DeferralAnalysis:
    """Apply an already validated AI patch without reparsing natural language."""
    direction = updates.get("direction", analysis.direction)
    if direction not in ("cost", "revenue"):
        raise ValueError("Invalid deferral direction")

    facts = dict(analysis.facts)
    for key, raw_value in updates.items():
        if key == "direction":
            continue
        if key in {"posting_date", "release_start_date", "release_end_date"}:
            facts[key] = datetime.fromisoformat(raw_value)
        elif key == "release_end_offset_years":
            facts[key] = int(raw_value)
        else:
            facts[key] = raw_value

    if "release_start_date" in updates:
        facts.setdefault("posting_date", facts["release_start_date"])
    if "release_end_date" in updates:
        facts.pop("release_end_offset_years", None)
    if "release_end_offset_years" in updates:
        facts.pop("release_end_date", None)
    if "currency" in updates:
        facts.pop("_currency_evidence", None)

    refreshed = analyze_deferral_workbook(file_path, direction)
    refreshed.facts.update(facts)
    refreshed.assumptions = _infer_assumptions(refreshed)
    refreshed.questions = _conversation_questions(refreshed)
    refreshed.warnings = _reference_warnings(file_path, refreshed)
    return refreshed


def transform_deferrals_to_light_je(file_path: Path, analysis: DeferralAnalysis) -> pd.DataFrame:
    if not analysis.ready:
        missing = ", ".join(_missing_items(analysis))
        raise ValueError(f"Deferral migration is missing required information: {missing}")
    source_configs = analysis.source_sheets or ([{
        "sheet": analysis.source_sheet,
        "header_row": analysis.header_row,
        "roles": analysis.roles,
    }] if analysis.source_sheet is not None and analysis.header_row is not None else [])
    if not source_configs:
        raise ValueError("Deferral migration needs at least one source schedule")

    normalized_parts: list[pd.DataFrame] = []
    for source_config in source_configs:
        source = pd.read_excel(
            file_path,
            sheet_name=source_config["sheet"],
            header=source_config["header_row"],
            engine="openpyxl",
        )
        source_rows = _valid_source_rows(source, source_config["roles"])
        normalized = pd.DataFrame(index=source_rows.index)
        for role, column in source_config["roles"].items():
            if column in source_rows.columns:
                normalized[role] = source_rows[column]
        normalized["_source_sheet"] = source_config["sheet"]
        normalized_parts.append(normalized)
    rows = pd.concat(normalized_parts, ignore_index=True, sort=False)
    roles = {column: column for column in rows.columns}

    account_codes = _read_account_codes(file_path, analysis.reference_sheet)
    deferral_mapper = (
        _choose_account_mapper(rows["deferral_account"], account_codes)
        if "deferral_account" in rows.columns
        else None
    )
    release_mapper = (
        _choose_account_mapper(rows["release_account"], account_codes)
        if "release_account" in rows.columns
        else None
    )

    output_rows: list[dict[str, Any]] = []
    for _, row in rows.iterrows():
        amount = _to_float(row["amount"])
        if amount is None or math.isclose(amount, 0.0, abs_tol=0.005):
            continue

        description = _clean_text(row["description"])
        source_reference = _clean_reference(row["source_reference"])
        document_description = f"{analysis.facts.get('description_prefix', 'Data migration deferred ')}{description}"
        document_number = f"{document_description}{source_reference}"

        deferral_account = (
            deferral_mapper(row["deferral_account"])
            if deferral_mapper is not None
            else _numeric_account(analysis.facts["deferral_account"])
        )
        release_account = (
            release_mapper(row["release_account"])
            if release_mapper is not None
            else _numeric_account(analysis.facts["release_account"])
        )
        release_start = analysis.facts.get("release_start_date", analysis.facts["posting_date"])
        release_end = _release_end_date(row, analysis, release_start, roles)

        common = {
            "Entity": analysis.facts["entity"],
            "Document Number": document_number,
            "Currency": analysis.facts["currency"],
            "Posting Date": analysis.facts["posting_date"],
            "Ledger": analysis.facts.get("ledger", "Primary"),
            "Business Partner": None,
            "Entry Description": document_description,
            "Line Description": document_description,
            "Tax Code": None,
            "Departments (line)": None,
        }

        release_template = _release_template(row, analysis, len(output_rows) // 2 + 1, roles)

        if analysis.direction == "cost":
            output_rows.append({
                **common,
                "Account": release_account,
                "Debit": round(amount, 2),
                "Credit": None,
                "Release Template": release_template,
                "Release Start Date": release_start,
                "Release End Date": release_end,
            })
            output_rows.append({
                **common,
                "Account": deferral_account,
                "Debit": None,
                "Credit": round(amount, 2),
                "Release Template": None,
                "Release Start Date": None,
                "Release End Date": None,
            })
        else:
            output_rows.append({
                **common,
                "Account": deferral_account,
                "Debit": round(amount, 2),
                "Credit": None,
                "Release Template": None,
                "Release Start Date": None,
                "Release End Date": None,
            })
            output_rows.append({
                **common,
                "Account": release_account,
                "Debit": None,
                "Credit": round(amount, 2),
                "Release Template": release_template,
                "Release Start Date": release_start,
                "Release End Date": release_end,
            })

    df = pd.DataFrame(output_rows, columns=LIGHT_JE_V2_COLUMNS)
    for column in ("Release Start Date", "Release End Date"):
        df[column] = pd.Series(
            [None if pd.isna(value) else value for value in df[column]],
            dtype=object,
        )
    _validate_output(df, rows, analysis, account_codes, source_amount_column="amount")
    return df


def format_deferral_analysis_message(analysis: DeferralAnalysis) -> str:
    title = "deferred revenue" if analysis.direction == "revenue" else "deferred cost / prepayment"
    lines = [
        f"I treated this as a {title} migration into the Light JE upload layout.",
        "",
    ]

    if analysis.source_sheet:
        lines.append(
            f"Detected source schedule: `{analysis.source_sheet}`, header row {int(analysis.header_row or 0) + 1} "
            f"({round(analysis.confidence * 100)}% confidence)."
        )
        if analysis.valid_source_rows:
            lines.append(f"Detected valid source rows: {analysis.valid_source_rows}.")
    else:
        lines.append("I could not confidently identify the source schedule yet.")

    if analysis.target_example_sheet:
        lines.append(f"I ignored `{analysis.target_example_sheet}` as an output/example sheet.")
    if analysis.reference_sheet:
        lines.append(f"Detected account reference sheet: `{analysis.reference_sheet}`.")

    if analysis.roles:
        lines.append("")
        lines.append("Mapped source roles:")
        for role, column in analysis.roles.items():
            lines.append(f"- `{role}` -> `{column}`")

    if analysis.facts:
        lines.append("")
        lines.append("Facts/assumptions for the run:")
        for key, value in analysis.facts.items():
            lines.append(f"- `{key}`: `{_serialise_fact(value)}`")

    if analysis.assumptions:
        lines.append("")
        lines.append("Assumptions:")
        for item in analysis.assumptions:
            lines.append(f"- {item}")

    if analysis.warnings:
        lines.append("")
        lines.append("Checks to review:")
        for warning in analysis.warnings:
            lines.append(f"- {warning}")

    if analysis.questions:
        lines.append("")
        lines.append("I need/strongly recommend confirming:")
        for question in analysis.questions:
            lines.append(f"- {question}")
        lines.append("")
        lines.append("Reply with the missing facts, for example: `entity is X`, `currency is EUR`, `posting date 2026-03-01`, or `release template is 12 Months - Deferred Revenue`.")
    else:
        lines.append("")
        lines.append("I have enough to run a deterministic migration. Review the assumptions, then click Run Migration or tell me what to adjust.")

    return "\n".join(lines)


def format_deferral_conversation_message(
    analysis: DeferralAnalysis,
    changes: dict[str, Any] | None = None,
    understood: bool = True,
) -> str:
    """Render a concise acknowledgement instead of repeating internal state."""
    labels = {
        "entity": "Entity",
        "currency": "Currency",
        "posting_date": "Posting date",
        "release_start_date": "Release start",
        "release_end_date": "Release end",
        "release_end_offset_years": "Release end rule",
        "release_template": "Release template",
        "deferral_account": "Deferred balance account",
        "release_account": "Revenue / expense account",
        "description_prefix": "Description prefix",
        "direction": "Migration type",
    }
    title = "deferred income" if analysis.direction == "revenue" else "prepaid expenses"
    labels["deferral_account"] = (
        "Deferred-income liability account" if analysis.direction == "revenue" else "Prepaid-expense asset account"
    )
    labels["release_account"] = "Revenue account" if analysis.direction == "revenue" else "Expense account"
    lines: list[str] = []

    if changes is None:
        lines.append(f"I recognized this as a {title} migration.")
    elif changes:
        lines.append("Got it. I updated the migration plan:")
        for key, value in changes.items():
            if key.startswith("_"):
                continue
            display = (
                "1 year after the release start"
                if key == "release_end_offset_years" and value == 1
                else _serialise_fact(value)
            )
            lines.append(f"- {labels.get(key, key.replace('_', ' ').title())}: {display}")
    elif understood:
        lines.append("Got it. That is already reflected in the migration plan.")
    else:
        lines.append("I couldn't apply that instruction confidently, so I left the plan unchanged.")

    if analysis.source_sheets:
        lines.append("")
        if len(analysis.source_sheets) == 1:
            source = analysis.source_sheets[0]
            lines.append(f"I found {source.get('valid_rows', analysis.valid_source_rows)} balances in {source['sheet']}.")
        else:
            lines.append(
                f"I found {analysis.valid_source_rows} balances across "
                f"{len(analysis.source_sheets)} schedules and will include both:"
            )
            for source in analysis.source_sheets:
                row_count = source.get("valid_rows", 0)
                lines.append(f"- {source['sheet']}: {row_count} {'balance' if row_count == 1 else 'balances'}")
    elif not analysis.source_sheet:
        lines.extend(["", "I could not identify a usable deferred-balance schedule yet."])

    if changes is None and analysis.facts.get("currency"):
        lines.append("")
        evidence = analysis.facts.get("_currency_evidence")
        suffix = f" (inferred from {evidence})" if evidence else ""
        lines.append(f"Currency: {analysis.facts['currency']}{suffix}")

    if analysis.questions:
        lines.extend(["", "I still need:"])
        lines.extend(f"- {question}" for question in analysis.questions)
        lines.extend(["", "Answer naturally in one message; you do not need special field names."])
    else:
        lines.extend([
            "",
            "Everything required is set. You can run the migration, or tell me any correction in your own words.",
        ])

    if analysis.warnings:
        lines.extend(["", "Note: " + " ".join(analysis.warnings)])
    return "\n".join(lines)


def _find_target_example_sheet(sheets: dict[str, pd.DataFrame]) -> str | None:
    target_terms = {"entity", "document number", "currency", "posting date", "account", "debit", "credit"}
    for name, df in sheets.items():
        for idx in range(min(10, len(df))):
            row_terms = {_norm(v) for v in df.iloc[idx].dropna().tolist()}
            if len(target_terms & row_terms) >= 5:
                return name
    return None


def _find_reference_sheet(sheets: dict[str, pd.DataFrame]) -> str | None:
    for name, df in sheets.items():
        if len(df) == 0:
            continue
        for idx in range(min(5, len(df))):
            headers = [_norm(v) for v in df.iloc[idx].tolist()]
            if "code" in headers and ("label" in headers or "name" in headers or "account name" in headers):
                return name
    return None


def _find_source_sheets_and_roles(
    sheets: dict[str, pd.DataFrame],
    target_example_sheet: str | None,
    direction: Direction,
) -> list[dict[str, Any]]:
    candidates = []
    for sheet_name, df in sheets.items():
        if sheet_name == target_example_sheet:
            continue
        for row_idx in range(min(20, len(df))):
            roles, role_confidence = _roles_for_header_row(df.iloc[row_idx].tolist(), direction)
            required_hits = {"amount", "description", "source_reference", "deferral_account", "release_account"} & set(roles)
            if len(required_hits) < 3:
                continue
            non_empty = int(df.iloc[row_idx].notna().sum())
            data_score = _data_below_score(df, row_idx)
            score = (len(required_hits) / 5) * 0.75 + min(non_empty / max(len(df.columns), 1), 1.0) * 0.10 + data_score * 0.15
            candidates.append({
                "sheet": sheet_name,
                "header_row": row_idx,
                "roles": roles,
                "role_confidence": role_confidence,
                "confidence": round(score, 3),
            })

    if not candidates:
        return []

    best_by_sheet: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        current = best_by_sheet.get(candidate["sheet"])
        if current is None or candidate["confidence"] > current["confidence"]:
            best_by_sheet[candidate["sheet"]] = candidate

    ranked = sorted(best_by_sheet.values(), key=lambda c: c["confidence"], reverse=True)
    core_roles = {"amount", "description", "source_reference"}
    compatible = [
        candidate for candidate in ranked
        if core_roles.issubset(set(candidate["roles"]))
    ]
    return compatible or ranked[:1]


def _roles_for_header_row(values: list[Any], direction: Direction) -> tuple[dict[str, str], dict[str, float]]:
    scored: list[tuple[str, str, float]] = []
    for value in values:
        if pd.isna(value):
            continue
        header = str(value).strip()
        norm = _norm(header)
        if not norm:
            continue
        for role, score in _score_header_for_roles(norm, direction):
            scored.append((role, header, score))

    roles: dict[str, str] = {}
    confidence: dict[str, float] = {}
    for role, header, score in sorted(scored, key=lambda item: item[2], reverse=True):
        if role in roles:
            continue
        if header in roles.values():
            continue
        roles[role] = header
        confidence[role] = score

    # If there is one generic "Account" plus a P&L account role, treat the
    # generic account as the balance-sheet deferral account.
    if "deferral_account" not in roles:
        generic = [h for role, h, _ in scored if role == "generic_account"]
        if generic and "release_account" in roles:
            roles["deferral_account"] = generic[0]
            confidence["deferral_account"] = 0.72
    roles.pop("generic_account", None)
    confidence.pop("generic_account", None)
    return roles, confidence


def _score_header_for_roles(norm: str, direction: Direction) -> list[tuple[str, float]]:
    matches: list[tuple[str, float]] = []

    def add(role: str, score: float):
        matches.append((role, score))

    if any(term in norm for term in ("remainder amount", "remaining amount", "remaining balance", "open amount", "deferred amount", "deferral amount", "balance amount", "b fwd", "brought forward")):
        add("amount", 0.98)
    elif norm in {"amount", "balance", "value"} or norm.endswith(" amount"):
        add("amount", 0.70)

    if any(term in norm for term in ("description", "line text", "memo", "narration", "text", "details")):
        add("description", 0.95 if norm != "name" else 0.55)
    elif norm in {"name", "label"}:
        add("description", 0.45)

    if any(term in norm for term in ("entry", "document", "doc no", "voucher", "invoice", "inv number", "inv no", "reference", "ref", "transaction id")):
        add("source_reference", 0.90)

    release_terms = ["cost account", "expense account", "p l account", "pl account"]
    if direction == "revenue":
        release_terms.extend(["revenue account", "income account", "sales account"])
    if any(term in norm for term in release_terms):
        add("release_account", 0.98)
    elif any(term in norm for term in ("cost centre account", "nominal account")):
        add("release_account", 0.65)

    deferral_terms = [
        "deferred account",
        "deferral account",
        "balance sheet account",
        "prepaid account",
        "prepayment account",
        "liability account",
        "deferred revenue account",
    ]
    if any(term in norm for term in deferral_terms):
        add("deferral_account", 0.98)
    elif norm in {"account", "account code", "account number", "gl account", "ledger account"}:
        add("generic_account", 0.68)

    if norm == "from" or any(term in norm for term in ("start date", "service start", "period start", "release start")):
        add("release_start_date", 0.82)
    if norm == "to" or any(term in norm for term in ("end date", "service end", "period end", "release end")):
        add("release_end_date", 0.90)

    if any(term in norm for term in ("currency", "ccy")):
        add("currency", 0.80)
    if any(term in norm for term in ("entity", "company", "business unit")):
        add("entity", 0.80)
    if any(term in norm for term in ("business partner", "customer", "vendor", "supplier")):
        add("business_partner", 0.70)
        add("description", 0.72)
    if any(term in norm for term in ("department", "cost center", "cost centre")):
        add("department", 0.70)
    if any(term in norm for term in ("template", "release template")):
        add("release_template", 0.85)

    return matches


def _data_below_score(df: pd.DataFrame, row_idx: int) -> float:
    below = df.iloc[row_idx + 1: row_idx + 6]
    if below.empty:
        return 0.0
    mask = below.notna() & (below.astype(str).apply(lambda col: col.str.strip() != ""))
    return float(mask.values.mean())


def _infer_facts(
    sheets: dict[str, pd.DataFrame],
    analysis: DeferralAnalysis,
    file_path: Path,
) -> dict[str, Any]:
    facts: dict[str, Any] = {"ledger": "Primary"}

    if analysis.source_sheet:
        source_raw = sheets[analysis.source_sheet]
        facts.update(_infer_from_source_banner(source_raw))

    if analysis.reference_sheet:
        ref = _read_headered_sheet_from_raw(sheets[analysis.reference_sheet])
        if "Company Entities" in ref.columns:
            entities = [str(v).strip() for v in ref["Company Entities"].dropna().unique() if str(v).strip()]
            if len(entities) == 1:
                facts.setdefault("entity", entities[0])

    if analysis.roles and analysis.source_sheet and analysis.header_row is not None:
        source = _read_headered_sheet_from_raw(sheets[analysis.source_sheet], analysis.header_row)
        if "currency" in analysis.roles:
            currency_values = source[analysis.roles["currency"]].dropna().astype(str).str.strip()
            currency_values = [v for v in currency_values.unique() if re.fullmatch(r"[A-Za-z]{3}", v)]
            if len(currency_values) == 1:
                facts.setdefault("currency", currency_values[0].upper())
        if "entity" in analysis.roles:
            entity_values = source[analysis.roles["entity"]].dropna().astype(str).str.strip()
            entity_values = [v for v in entity_values.unique() if v]
            if len(entity_values) == 1:
                facts.setdefault("entity", entity_values[0])

    if "currency" not in facts:
        sample_values = " ".join(
            str(value)
            for frame in sheets.values()
            for value in frame.iloc[:10].values.flatten()
            if pd.notna(value)
        )
        context = f"{file_path.stem} {' '.join(sheets.keys())} {sample_values}".lower()
        currency_hints = (
            (r"\buk\b|united kingdom", "GBP", "a UK marker in the workbook"),
            (r"\bdenmark\b|\bdk\b", "DKK", "Denmark in the workbook name"),
            (r"\bsweden\b|\bse\b", "SEK", "Sweden in the workbook name"),
            (r"\bnorway\b|\bno\b", "NOK", "Norway in the workbook name"),
        )
        for pattern, currency, evidence in currency_hints:
            if re.search(pattern, context):
                facts["currency"] = currency
                facts["_currency_evidence"] = evidence
                break
    facts.setdefault("description_prefix", "Data migration deferred ")
    return facts


def _infer_from_source_banner(df: pd.DataFrame) -> dict[str, Any]:
    facts: dict[str, Any] = {}
    for row_idx in range(min(6, len(df))):
        text = " ".join(str(v) for v in df.iloc[row_idx].dropna().tolist() if str(v).strip())
        if not text:
            continue
        if " - " in text and "entity" not in facts and re.search(r"[A-Za-z]", text):
            maybe_entity = text.split(" - ", 1)[1].strip()
            if maybe_entity:
                facts["entity"] = maybe_entity
        if "as of" in text.lower():
            date = _parse_any_date(text)
            if date:
                facts["posting_date"] = date + timedelta(days=1)
    return facts


def _infer_assumptions(analysis: DeferralAnalysis) -> list[str]:
    assumptions = []
    if analysis.facts.get("_currency_evidence"):
        assumptions.append(f"Currency {analysis.facts['currency']} was inferred from {analysis.facts['_currency_evidence']}; you can correct it.")
    if analysis.facts.get("ledger") == "Primary":
        assumptions.append("Ledger is set to Primary.")
    if analysis.facts.get("description_prefix") == "Data migration deferred ":
        assumptions.append("Document and line descriptions use the prefix `Data migration deferred `.")
    if not analysis.facts.get("release_template") and "release_template" not in analysis.roles:
        assumptions.append("No release-template column was found, so release template names will be generated from the detected service period. If Light has exact template names, tell me the template to use.")
    if analysis.direction == "revenue":
        assumptions.append("Deferred revenue direction: debit deferral/liability account and credit revenue account with release fields.")
    else:
        assumptions.append("Deferred cost direction: debit cost/expense account with release fields and credit prepaid/deferral account.")
    return assumptions


def _missing_questions(analysis: DeferralAnalysis) -> list[str]:
    questions = []
    missing_source_roles = [
        role for role in ("amount", "description", "source_reference")
        if role not in analysis.roles
    ]
    if missing_source_roles:
        questions.append(f"Confirm which source columns represent: {', '.join(missing_source_roles)}.")
    missing_configuration = [
        item for item in _missing_items(analysis, role_only=True)
        if item not in missing_source_roles
    ]
    if missing_configuration:
        questions.append(f"Provide: {', '.join(missing_configuration)}.")
    for fact in ("entity", "currency", "posting_date"):
        if fact not in analysis.facts:
            questions.append(f"Provide `{fact}`.")
    return questions


def _conversation_questions(analysis: DeferralAnalysis) -> list[str]:
    """Describe only execution blockers, using accounting language."""
    questions: list[str] = []
    missing = set(_missing_items(analysis, role_only=True))

    source_labels = {
        "amount": "opening balance",
        "description": "customer or description",
        "source_reference": "invoice or source reference",
    }
    missing_source = [source_labels[key] for key in source_labels if key in missing]
    if missing_source:
        questions.append("Which columns contain " + ", ".join(missing_source) + "?")

    needs_deferral = "deferral_account" in missing
    needs_release = "release_account" in missing
    if needs_deferral and needs_release:
        balance_label = "deferred-income liability" if analysis.direction == "revenue" else "prepaid-expense asset"
        pnl_label = "revenue" if analysis.direction == "revenue" else "expense"
        questions.append(f"Which Light {balance_label} account and {pnl_label} account should I use?")
    elif needs_deferral:
        questions.append("Which Light balance-sheet account should hold the deferred balance?")
    elif needs_release:
        questions.append("Which Light revenue or expense account should receive the releases?")

    if "release_end_date" in missing:
        questions.append("When should the release end (a date or a rule such as one year after the start)?")
    if "entity" not in analysis.facts:
        questions.append("Which Light entity should I post to?")
    if "currency" not in analysis.facts:
        questions.append("Which currency should I use?")
    if "posting_date" not in analysis.facts:
        questions.append("What posting date should I use?")
    return questions


def _missing_items(analysis: DeferralAnalysis, role_only: bool = False) -> list[str]:
    required_roles = ["amount", "description", "source_reference"]
    missing = [role for role in required_roles if role not in analysis.roles]
    for role_or_fact in ("deferral_account", "release_account"):
        if role_or_fact not in analysis.roles and role_or_fact not in analysis.facts:
            missing.append(role_or_fact)
    if (
        "release_end_date" not in analysis.roles
        and "release_end_date" not in analysis.facts
        and "release_end_offset_years" not in analysis.facts
    ):
        missing.append("release_end_date")
    if not role_only:
        missing.extend([fact for fact in ("entity", "currency", "posting_date") if fact not in analysis.facts])
    return missing


def _reference_warnings(file_path: Path, analysis: DeferralAnalysis) -> list[str]:
    warnings = []
    if not analysis.ready or not analysis.source_sheet or analysis.header_row is None:
        return warnings
    try:
        source = pd.read_excel(file_path, sheet_name=analysis.source_sheet, header=analysis.header_row, engine="openpyxl")
        rows = _valid_source_rows(source, analysis.roles)
        account_codes = _read_account_codes(file_path, analysis.reference_sheet)
        if account_codes:
            for role in ("deferral_account", "release_account"):
                mapper = _choose_account_mapper(rows[analysis.roles[role]], account_codes)
                missing = sorted({
                    int(mapped)
                    for mapped in (mapper(v) for v in rows[analysis.roles[role]].dropna())
                    if mapped is not None and int(mapped) not in account_codes
                })
                if missing:
                    warnings.append(f"{role} generated account codes not found in the account reference: {missing[:10]}")
        else:
            warnings.append("No account master was found, so account existence cannot be validated.")
    except Exception as exc:
        warnings.append(f"Could not complete account validation yet: {exc}")
    return warnings


def _valid_source_rows(source: pd.DataFrame, roles: dict[str, str]) -> pd.DataFrame:
    amount_col = roles["amount"]
    ref_col = roles["source_reference"]
    df = source.copy()
    df["_amount_numeric"] = df[amount_col].apply(_to_float)
    mask = df["_amount_numeric"].notna() & df[ref_col].notna()
    for role in ("deferral_account", "release_account"):
        if role in roles:
            mask &= df[roles[role]].notna()
    valid = df[mask].copy()
    valid = valid[~valid["_amount_numeric"].apply(lambda v: math.isclose(float(v), 0.0, abs_tol=0.005))]
    return valid.drop(columns=["_amount_numeric"])


def _count_valid_rows(file_path: Path, analysis: DeferralAnalysis) -> int:
    try:
        if analysis.source_sheets:
            total = 0
            for source_config in analysis.source_sheets:
                source = pd.read_excel(
                    file_path,
                    sheet_name=source_config["sheet"],
                    header=source_config["header_row"],
                    engine="openpyxl",
                )
                valid_rows = len(_valid_source_rows(source, source_config["roles"]))
                source_config["valid_rows"] = valid_rows
                total += valid_rows
            return total
        if not analysis.source_sheet or analysis.header_row is None:
            return 0
        source = pd.read_excel(file_path, sheet_name=analysis.source_sheet, header=analysis.header_row, engine="openpyxl")
        return len(_valid_source_rows(source, analysis.roles))
    except Exception:
        return 0


def _read_account_codes(file_path: Path, reference_sheet: str | None) -> set[int]:
    if not reference_sheet:
        return set()
    try:
        df = pd.read_excel(file_path, sheet_name=reference_sheet, header=0, engine="openpyxl")
    except Exception:
        return set()
    code_col = None
    for col in df.columns:
        if _norm(col) in {"code", "account", "account code", "account number", "gl account"}:
            code_col = col
            break
    if code_col is None:
        return set()
    return {
        int(v)
        for v in pd.to_numeric(df[code_col], errors="coerce").dropna().tolist()
    }


def _choose_account_mapper(values: pd.Series, account_codes: set[int]):
    candidates = pd.to_numeric(values, errors="coerce").dropna().astype(int).tolist()
    if not account_codes:
        return lambda value: _numeric_account(value)
    direct_hits = sum(1 for value in candidates if value in account_codes)
    times_ten_hits = sum(1 for value in candidates if value * 10 in account_codes)
    if times_ten_hits > direct_hits:
        return lambda value: _numeric_account(value, multiplier=10)
    return lambda value: _numeric_account(value)


def _numeric_account(value: Any, multiplier: int = 1) -> int | None:
    number = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(number):
        return None
    return int(number) * multiplier


def _release_template(
    row: pd.Series,
    analysis: DeferralAnalysis,
    sequence: int,
    roles: dict[str, str] | None = None,
) -> str | None:
    active_roles = roles or analysis.roles
    if "release_template" in active_roles:
        value = row.get(active_roles["release_template"])
        if pd.notna(value) and str(value).strip():
            return str(value).strip()
    if analysis.facts.get("release_template"):
        return str(analysis.facts["release_template"])

    label = "Deferred Revenue" if analysis.direction == "revenue" else "Prepayment (AP)"
    months = _period_months(row, analysis, active_roles)
    if months is not None:
        return f"{months} Months - {label}"
    return f"{sequence} Months - {label}"


def _period_months(row: pd.Series, analysis: DeferralAnalysis, roles: dict[str, str] | None = None) -> int | None:
    start = None
    active_roles = roles or analysis.roles
    if "release_start_date" in active_roles:
        start = _to_datetime(row[active_roles["release_start_date"]])
    if start is None:
        start = analysis.facts.get("release_start_date")
    if start is None:
        start = analysis.facts.get("posting_date")
    end = _release_end_date(row, analysis, start, active_roles)
    if start is None or end is None:
        return None
    months = (end.year - start.year) * 12 + (end.month - start.month) + 1
    return max(months, 1)


def _validate_output(
    df: pd.DataFrame,
    source_rows: pd.DataFrame,
    analysis: DeferralAnalysis,
    account_codes: set[int],
    source_amount_column: str | None = None,
) -> None:
    debit = pd.to_numeric(df["Debit"], errors="coerce").fillna(0).sum()
    credit = pd.to_numeric(df["Credit"], errors="coerce").fillna(0).sum()
    if not math.isclose(debit, credit, abs_tol=0.01):
        raise ValueError(f"Generated JE is not balanced: debit={debit:.2f}, credit={credit:.2f}")

    source_total = source_rows[source_amount_column or analysis.roles["amount"]].apply(_to_float).dropna().sum()
    if not math.isclose(debit, source_total, abs_tol=0.01):
        raise ValueError(f"Generated total {debit:.2f} does not match source amount total {source_total:.2f}")

    missing_required = []
    required = ["Entity", "Document Number", "Currency", "Posting Date", "Account", "Entry Description", "Line Description"]
    for column in required:
        if df[column].isna().any() or (df[column].astype(str).str.strip() == "").any():
            missing_required.append(column)
    if missing_required:
        raise ValueError(f"Generated output has blank required fields: {missing_required}")

    if account_codes:
        generated_accounts = set(pd.to_numeric(df["Account"], errors="coerce").dropna().astype(int).tolist())
        missing = sorted(generated_accounts - account_codes)
        if missing:
            raise ValueError(f"Generated accounts not found in account reference: {missing[:20]}")


def _read_headered_sheet_from_raw(df: pd.DataFrame, header_row: int = 0) -> pd.DataFrame:
    if len(df) <= header_row:
        return pd.DataFrame()
    headers = df.iloc[header_row].tolist()
    data = df.iloc[header_row + 1:].copy()
    data.columns = headers
    return data


def _norm(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    return _NON_ALNUM.sub(" ", str(value).strip().lower()).strip()


def _clean_text(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).replace("\u200b", "").replace("\xa0", " ").strip()


def _clean_reference(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _to_float(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    text = text.replace(",", "")
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]
    try:
        return float(text)
    except ValueError:
        return None


def _to_datetime(value: Any) -> datetime | None:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, datetime):
        return value
    if hasattr(value, "to_pydatetime"):
        return value.to_pydatetime()
    parsed = pd.to_datetime(value, errors="coerce", dayfirst=True)
    if pd.isna(parsed):
        return None
    return parsed.to_pydatetime()


def _parse_any_date(text: str) -> datetime | None:
    match = _DATE_IN_TEXT.search(text)
    if not match:
        parsed = pd.to_datetime(text, errors="coerce", dayfirst=True)
        return None if pd.isna(parsed) else parsed.to_pydatetime()
    day, month, year = match.groups()
    year_int = int(year)
    if year_int < 100:
        year_int += 2000
    try:
        return datetime(year_int, int(month), int(day))
    except ValueError:
        return None


def _parse_currency(message: str) -> str | None:
    known = {"DKK", "EUR", "USD", "GBP", "SEK", "NOK", "CHF", "CAD", "AUD"}
    for token in re.findall(r"\b[A-Za-z]{3}\b", message):
        upper = token.upper()
        if upper in known:
            return upper
    return None


_MESSAGE_FIELD_ALIASES = {
    "release_template": ("release template", "template"),
    "description_prefix": ("description prefix", "prefix"),
    "deferral_account": (
        "deferral account",
        "liability account",
        "balance sheet account",
        "deferred income account",
        "prepaid account",
    ),
    "release_account": ("release account", "revenue account", "income account", "expense account"),
    "release_start_date": ("release start date", "starting date", "start date"),
    "release_end_date": ("release end date", "ending date", "end date"),
    "posting_date": ("posting date",),
    "currency": ("currency",),
    "entity": ("entity", "company"),
}


def _parse_named_values(message: str) -> dict[str, str]:
    """Extract several labelled facts from one loose, punctuation-free message."""
    aliases = [
        (alias, field)
        for field, field_aliases in _MESSAGE_FIELD_ALIASES.items()
        for alias in field_aliases
    ]
    alias_pattern = "|".join(
        re.escape(alias)
        for alias, _ in sorted(aliases, key=lambda item: len(item[0]), reverse=True)
    )
    matches = list(re.finditer(
        rf"\b({alias_pattern})\b\s*(?:(?:is\s+)?as of\s+|is\s+|[=:]\s*)?",
        message,
        re.IGNORECASE,
    ))
    alias_to_field = {alias.lower(): field for alias, field in aliases}
    values: dict[str, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(message)
        value = message[match.end():end].strip(" \t\r\n,;.`'\"")
        if value:
            values[alias_to_field[match.group(1).lower()]] = value
    return values


def _parse_contextual_accounts(message: str) -> dict[str, str]:
    """Understand account codes placed before or after plain-English labels."""
    contexts = {
        "deferral_account": r"balance\s*sheet|liabilit(?:y|ies)|deferred\s+(?:income|revenue)|prepaid|prepayment",
        "release_account": r"revenue|income|sales|expense|p\s*&\s*l|profit\s+and\s+loss",
    }
    values: dict[str, str] = {}
    for field, context in contexts.items():
        patterns = (
            rf"\b(\d{{3,10}})\b\D{{0,24}}(?:for|as|to)?\s*(?:the\s+)?(?:{context})(?:\s+account)?",
            rf"(?:{context})(?:\s+account)?\D{{0,30}}\b(\d{{3,10}})\b",
        )
        for pattern in patterns:
            match = re.search(pattern, message, re.IGNORECASE)
            if match:
                values[field] = match.group(1)
                break
    return values


def _looks_like_bare_entity_answer(message: str, analysis: DeferralAnalysis) -> bool:
    if not any("which light entity" in question.lower() for question in analysis.questions):
        return False
    text = message.strip().strip("`'\".,; ")
    if not text or len(text.split()) > 6 or re.search(r"\d", text):
        return False
    if _parse_currency(text) or _parse_any_date(text) is not None:
        return False
    disallowed = (
        "account",
        "currency",
        "date",
        "template",
        "year",
        "month",
        "same as",
        "include",
        "exclude",
    )
    return not any(term in text.lower() for term in disallowed)

def _parse_year_offset(text: str) -> int | None:
    match = re.search(r"(?:\+|plus|after|add(?:ing)?)\s*(\d+)\s*years?\b", text, re.IGNORECASE)
    if not match and re.search(r"\b(?:one|a)\s+year\b", text, re.IGNORECASE):
        return 1
    return int(match.group(1)) if match else None


def _add_years(value: datetime, years: int) -> datetime:
    try:
        return value.replace(year=value.year + years)
    except ValueError:
        return value.replace(year=value.year + years, day=28)


def _release_end_date(
    row: pd.Series,
    analysis: DeferralAnalysis,
    release_start: datetime | None,
    roles: dict[str, str] | None = None,
) -> datetime | None:
    active_roles = roles or analysis.roles
    if "release_end_date" in active_roles:
        return _to_datetime(row[active_roles["release_end_date"]])
    if analysis.facts.get("release_end_date"):
        return _to_datetime(analysis.facts["release_end_date"])
    offset_years = analysis.facts.get("release_end_offset_years")
    if release_start is not None and offset_years is not None:
        return _add_years(release_start, int(offset_years))
    return None


def _serialise_fact(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    return str(value)
