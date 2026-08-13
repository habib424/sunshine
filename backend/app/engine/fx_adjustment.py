"""
Deterministic FX currency adjustment to the Light JE upload layout.

After a migration, Light converts local-currency balances into each bank
account's transaction currency at a default exchange rate, so the booked
account-currency amount can differ from the real bank balance. This intent
reads the booked account-currency balances (from the trial balance extract)
and the real bank balances (provided by the customer), and posts the
difference to a bank clearing account with the Local and Group Currency FX
Rates overridden to 0 — aligning the transaction-currency amount without
touching the local or group currency amounts.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd


FX_ADJUSTMENT_INTENT = "fx_currency_adjustment"
FX_ADJUSTMENT_INTENTS = {FX_ADJUSTMENT_INTENT}


LIGHT_FX_COLUMNS = [
    "Entity",
    "Document Number",
    "Currency",
    "Posting Date",
    "Ledger",
    "Business Partner",
    "Entry Description",
    "Light account",
    "Debit",
    "Credit",
    "Line Description",
    "Tax Code",
    "Local Currency FX Rate",
    "Group Currency FX Rate",
    "Release Template",
    "Release Start Date",
    "Release End Date",
    "Departments (line)",
]

_KNOWN_CURRENCIES = {
    "SEK", "EUR", "USD", "GBP", "NOK", "DKK", "CHF", "JPY", "AUD", "CAD",
    "PLN", "CZK", "HUF", "CNY", "HKD", "SGD", "INR", "AED", "ZAR", "NZD",
    "RON", "BGN", "ISK", "TRY", "MXN", "BRL", "KRW", "THB", "MYR", "IDR",
}

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_ACCOUNT_RE = re.compile(r"^\s*(\d{4,8})(?:\.0+)?\s*$")
_ENTITY_IN_HEADER_RE = re.compile(r"\(([^)]+)\)")
_CURRENCY_SUFFIX_RE = re.compile(r"([A-Z]{3})(?=,|\s|$)")
_AMOUNT_TEXT_RE = re.compile(r"[-+]?[\d\s '.,]*\d")
_DATE_RE = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})|(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})")

_TOLERANCE = 0.01


@dataclass
class FXAdjustmentRow:
    account: str
    description: str
    currency: str
    booked_currency_amount: float
    real_bank_balance: float

    @property
    def difference(self) -> float:
        return round(self.real_bank_balance - self.booked_currency_amount, 2)

    def to_dict(self) -> dict[str, Any]:
        return {
            "account": self.account,
            "description": self.description,
            "currency": self.currency,
            "booked_currency_amount": self.booked_currency_amount,
            "real_bank_balance": self.real_bank_balance,
            "difference": self.difference,
        }


@dataclass
class FXAdjustmentAnalysis:
    source_sheet: str | None = None
    header_row: int | None = None
    roles: dict[str, str] = field(default_factory=dict)
    facts: dict[str, Any] = field(default_factory=dict)
    adjustments: list[FXAdjustmentRow] = field(default_factory=list)
    aligned: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    confidence: float = 0.0

    @property
    def ready(self) -> bool:
        return not _missing_items(self)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_sheet": self.source_sheet,
            "header_row": self.header_row,
            "roles": self.roles,
            "facts": self.facts,
            "adjustments": [row.to_dict() for row in self.adjustments],
            "aligned": self.aligned,
            "skipped": self.skipped,
            "assumptions": self.assumptions,
            "questions": self.questions,
            "warnings": self.warnings,
            "confidence": self.confidence,
            "ready": self.ready,
        }


def analyze_fx_workbook(file_path: Path) -> FXAdjustmentAnalysis:
    sheets = pd.read_excel(file_path, sheet_name=None, header=None, engine="openpyxl")
    analysis = FXAdjustmentAnalysis()

    source = _find_source_sheet(sheets)
    if not source:
        analysis.questions.append(
            "Which sheet contains the bank accounts with booked balances and real bank balances?"
        )
        return analysis

    analysis.source_sheet = source["sheet"]
    analysis.header_row = source["header_row"]
    analysis.roles = source["roles"]
    analysis.confidence = source["confidence"]

    entity = _entity_from_headers(source["header_values"])
    if entity:
        analysis.facts["entity"] = entity

    _collect_rows(sheets[analysis.source_sheet], source, analysis)

    analysis.facts.setdefault("ledger", "Primary")
    _finalise(analysis)
    return analysis


def _finalise(analysis: FXAdjustmentAnalysis) -> None:
    """Re-derive everything that depends on the facts being settled."""
    local = analysis.facts.get("local_currency") or _infer_local_currency(analysis)
    if local:
        analysis.facts.setdefault("local_currency", local)

    # An FX-rate-0 entry cannot fix a difference on a local-currency account —
    # only the transaction-currency amount moves. Park those rows instead.
    if local:
        keep: list[FXAdjustmentRow] = []
        for row in analysis.adjustments:
            if row.currency == local:
                warning = (
                    f"`{row.account}` {row.description} is a {row.currency} (local currency) "
                    f"account with a difference of {row.difference:,.2f} — an FX-rate-0 "
                    "adjustment cannot fix a local currency difference, so it was skipped."
                )
                if warning not in analysis.warnings:
                    analysis.warnings.append(warning)
                analysis.skipped.append({
                    "account": row.account,
                    "description": row.description,
                    "reason": "difference is in the local currency",
                })
            else:
                keep.append(row)
        analysis.adjustments = keep

    analysis.assumptions = _infer_assumptions(analysis)
    analysis.questions = _missing_questions(analysis)


def apply_user_message_to_analysis(
    analysis: FXAdjustmentAnalysis,
    message: str,
    file_path: Path,
) -> FXAdjustmentAnalysis:
    facts = dict(analysis.facts)

    entity = _parse_named_value(message, ("entity", "company"))
    if entity:
        facts["entity"] = entity

    ledger = _parse_named_value(message, ("ledger",))
    if ledger:
        facts["ledger"] = ledger

    clearing = _parse_account_value(message, ("clearing account", "bank clearing account", "offset account"))
    if clearing:
        facts["clearing_account"] = clearing

    local_currency = _parse_named_value(message, ("local currency",))
    if local_currency and re.fullmatch(r"[A-Za-z]{3}", local_currency.strip()):
        facts["local_currency"] = local_currency.strip().upper()

    document_year = _parse_named_value(message, ("document year", "doc year"))
    if document_year and re.fullmatch(r"\d{4}", document_year.strip()):
        facts["document_year"] = document_year.strip()

    posting_date = _parse_any_date(message)
    if posting_date:
        facts["posting_date"] = posting_date

    refreshed = analyze_fx_workbook(file_path)
    refreshed.facts.update({k: v for k, v in facts.items() if v not in (None, "")})
    _finalise(refreshed)
    return refreshed


def format_fx_analysis_message(analysis: FXAdjustmentAnalysis) -> str:
    lines = ["I found an FX currency adjustment layout."]

    if analysis.source_sheet:
        lines.append(
            f"- Source sheet: `{analysis.source_sheet}` with header row {analysis.header_row}."
        )
    if analysis.roles:
        role_bits = [f"`{source}` -> {role}" for role, source in sorted(analysis.roles.items())]
        lines.append("- Column roles: " + ", ".join(role_bits))

    if analysis.adjustments:
        lines.append("\nAccounts needing adjustment (real bank balance vs booked currency amount):")
        for row in analysis.adjustments:
            sign = "+" if row.difference >= 0 else ""
            lines.append(
                f"- `{row.account}` {row.description} ({row.currency}): "
                f"booked {row.booked_currency_amount:,.2f} -> bank {row.real_bank_balance:,.2f}, "
                f"adjustment {sign}{row.difference:,.2f} {row.currency}"
            )
    if analysis.aligned:
        accounts = ", ".join(f"`{item['account']}`" for item in analysis.aligned)
        lines.append(f"\nAlready aligned (no adjustment needed): {accounts}")
    if analysis.skipped:
        lines.append("\nSkipped rows:")
        for item in analysis.skipped:
            lines.append(f"- `{item['account']}` {item.get('description', '')}: {item['reason']}")

    if analysis.assumptions:
        lines.append("\nAssumptions:")
        lines.extend(f"- {item}" for item in analysis.assumptions)

    if analysis.warnings:
        lines.append("\nWarnings:")
        lines.extend(f"- {item}" for item in analysis.warnings)

    if analysis.questions:
        lines.append("\nI need this before I can run it:")
        lines.extend(f"- {item}" for item in analysis.questions)
        lines.append("\nReply with the missing fact, for example: `clearing account is 900300`.")
    else:
        lines.append(
            "\nReady to run. Each adjustment posts to the bank account and the clearing account "
            "with Local and Group Currency FX Rate = 0, so only the transaction-currency amount moves."
        )

    return "\n".join(lines)


def transform_fx_adjustments(file_path: Path, analysis: FXAdjustmentAnalysis) -> pd.DataFrame:
    missing = _missing_items(analysis)
    if missing:
        raise ValueError(f"FX adjustment is missing required information: {', '.join(missing)}")

    entity = str(analysis.facts["entity"])
    clearing_account = str(analysis.facts["clearing_account"])
    ledger = str(analysis.facts.get("ledger", "Primary"))
    posting_date = _format_date(str(analysis.facts["posting_date"]))
    document_year = str(
        analysis.facts.get("document_year")
        or _year_of(str(analysis.facts["posting_date"]))
    )

    output_rows: list[dict[str, Any]] = []
    by_currency: dict[str, list[FXAdjustmentRow]] = {}
    for row in analysis.adjustments:
        by_currency.setdefault(row.currency, []).append(row)

    for currency in sorted(by_currency):
        document_number = f"{entity}-{document_year}-FXJournal {currency}"
        description = f"FX currency adjustment {currency}"
        for row in by_currency[currency]:
            amount = abs(row.difference)
            bank_is_debit = row.difference > 0
            output_rows.append(_line(
                entity, document_number, currency, posting_date, ledger, description,
                row.account,
                debit=amount if bank_is_debit else "",
                credit="" if bank_is_debit else amount,
            ))
            output_rows.append(_line(
                entity, document_number, currency, posting_date, ledger, description,
                clearing_account,
                debit="" if bank_is_debit else amount,
                credit=amount if bank_is_debit else "",
            ))

    return pd.DataFrame(output_rows, columns=LIGHT_FX_COLUMNS)


def _line(
    entity: str,
    document_number: str,
    currency: str,
    posting_date: str,
    ledger: str,
    description: str,
    account: str,
    debit: Any,
    credit: Any,
) -> dict[str, Any]:
    return {
        "Entity": entity,
        "Document Number": document_number,
        "Currency": currency,
        "Posting Date": posting_date,
        "Ledger": ledger,
        "Business Partner": "",
        "Entry Description": description,
        "Light account": account,
        "Debit": debit,
        "Credit": credit,
        "Line Description": description,
        "Tax Code": "",
        "Local Currency FX Rate": 0,
        "Group Currency FX Rate": 0,
        "Release Template": "",
        "Release Start Date": "",
        "Release End Date": "",
        "Departments (line)": "",
    }


# ---------------------------------------------------------------------------
# Sheet and row detection


_ROLE_MATCHERS: list[tuple[str, tuple[str, ...]]] = [
    # Order matters: more specific prefixes first.
    ("balance_currency", ("balance in account currency", "balance in currency", "currency balance", "saldo i valuta")),
    ("real_balance", ("real bank balance", "actual bank balance", "bank balance", "statement balance", "real balance")),
    ("account", ("light account", "account code", "gl account", "account", "konto")),
    ("description", ("description", "account name", "benamning", "name")),
    ("balance_local", ("balance", "saldo")),
]


def _find_source_sheet(sheets: dict[str, pd.DataFrame]) -> dict | None:
    best: dict | None = None
    for name, df in sheets.items():
        for row_idx in range(min(10, len(df))):
            values = df.iloc[row_idx].tolist()
            roles: dict[str, str] = {}
            role_columns: dict[str, int] = {}
            for col_idx, value in enumerate(values):
                if value is None or (isinstance(value, float) and pd.isna(value)):
                    continue
                text = str(value).strip()
                if not text:
                    continue
                normalised = _normalise(text)
                for role, prefixes in _ROLE_MATCHERS:
                    if role in roles:
                        continue
                    if any(normalised.startswith(_normalise(p)) for p in prefixes):
                        roles[role] = text
                        role_columns[role] = col_idx
                        break

            required = {"account", "balance_currency", "real_balance"}
            hits = len(required & set(roles))
            if hits < 3:
                continue
            confidence = round(0.6 + 0.1 * len(roles), 2)
            candidate = {
                "sheet": name,
                "header_row": row_idx,
                "roles": roles,
                "role_columns": role_columns,
                "header_values": [str(v) for v in values if pd.notna(v)],
                "confidence": min(confidence, 1.0),
            }
            if best is None or candidate["confidence"] > best["confidence"]:
                best = candidate
    return best


def _collect_rows(df: pd.DataFrame, source: dict, analysis: FXAdjustmentAnalysis) -> None:
    cols = source["role_columns"]

    for idx in range(source["header_row"] + 1, len(df)):
        raw = df.iloc[idx]
        account = _clean_account(raw.iloc[cols["account"]] if cols.get("account") is not None else None)
        if not account:
            continue
        description = _clean_text(raw.iloc[cols["description"]]) if "description" in cols else ""
        booked = _parse_amount(raw.iloc[cols["balance_currency"]])
        real_raw = raw.iloc[cols["real_balance"]] if "real_balance" in cols else None

        if booked is None:
            analysis.skipped.append({
                "account": account,
                "description": description,
                "reason": "no booked account-currency balance found",
            })
            continue

        currency = _currency_of_row(description, real_raw)
        real = _parse_amount(real_raw)

        if real is None:
            analysis.skipped.append({
                "account": account,
                "description": description,
                "reason": "no real bank balance provided",
            })
            continue

        if currency is None:
            analysis.skipped.append({
                "account": account,
                "description": description,
                "reason": "could not detect the account currency",
            })
            continue

        difference = round(real - booked, 2)
        if abs(difference) <= _TOLERANCE:
            analysis.aligned.append({
                "account": account,
                "description": description,
                "currency": currency,
            })
            continue

        analysis.adjustments.append(FXAdjustmentRow(
            account=account,
            description=description,
            currency=currency,
            booked_currency_amount=booked,
            real_bank_balance=real,
        ))


def _entity_from_headers(header_values: list[str]) -> str | None:
    for value in header_values:
        if _normalise(value).startswith("balance"):
            match = _ENTITY_IN_HEADER_RE.search(value)
            if match:
                return match.group(1).strip()
    return None


def _infer_local_currency(analysis: FXAdjustmentAnalysis) -> str | None:
    # Aligned rows where booked equals real are usually local-currency accounts,
    # but the most reliable signal is the most common currency among all rows.
    counts: dict[str, int] = {}
    for item in analysis.aligned:
        currency = item.get("currency")
        if currency:
            counts[currency] = counts.get(currency, 0) + 1
    if counts:
        return max(counts, key=counts.get)
    return None


def _infer_assumptions(analysis: FXAdjustmentAnalysis) -> list[str]:
    assumptions = [
        "Each adjustment posts the difference between the real bank balance and the booked "
        "account-currency amount, with Local and Group Currency FX Rate = 0 so the local and "
        "group currency amounts stay unchanged.",
        f"Ledger is `{analysis.facts.get('ledger', 'Primary')}`.",
    ]
    if analysis.facts.get("entity"):
        assumptions.append(f"Entity `{analysis.facts['entity']}` was read from the balance column header.")
    if analysis.facts.get("posting_date") and not analysis.facts.get("document_year"):
        assumptions.append(
            "Document numbers use the posting-date year "
            "(override with e.g. `document year is 2025`)."
        )
    return assumptions


def _missing_questions(analysis: FXAdjustmentAnalysis) -> list[str]:
    questions = []
    for item in _missing_items(analysis):
        if item == "source_sheet":
            questions.append("Which sheet contains the bank accounts with booked and real balances?")
        elif item == "entity":
            questions.append("Which Light entity should this FX adjustment use?")
        elif item == "posting_date":
            questions.append("What posting date should the adjustment entries use?")
        elif item == "clearing_account":
            questions.append("Which bank clearing account should receive the offset lines?")
        elif item == "adjustments":
            questions.append(
                "No adjustable differences were found — provide the real bank balances "
                "for the FX accounts (or check the skipped rows above)."
            )
        else:
            questions.append(f"How should I fill `{item}`?")
    return questions


def _missing_items(analysis: FXAdjustmentAnalysis) -> list[str]:
    missing: list[str] = []
    if not analysis.source_sheet or analysis.header_row is None:
        missing.append("source_sheet")
    if not analysis.facts.get("entity"):
        missing.append("entity")
    if not analysis.facts.get("posting_date"):
        missing.append("posting_date")
    if not analysis.facts.get("clearing_account"):
        missing.append("clearing_account")
    if analysis.source_sheet and not analysis.adjustments:
        missing.append("adjustments")
    return missing


# ---------------------------------------------------------------------------
# Parsing helpers


def _normalise(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode("ascii")
    return _NON_ALNUM.sub(" ", text.lower()).strip()


def _clean_text(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).strip()


def _clean_account(value: Any) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, (int, float)) and not pd.isna(value):
        if float(value).is_integer():
            return str(int(value))
        return None
    match = _ACCOUNT_RE.match(str(value))
    return match.group(1) if match else None


def _currency_of_row(description: str, real_raw: Any) -> str | None:
    # Prefer the currency named in the real bank balance text ("Nordea EUR: ...").
    if isinstance(real_raw, str):
        for token in re.findall(r"\b([A-Z]{3})\b", real_raw):
            if token in _KNOWN_CURRENCIES:
                return token
    for match in _CURRENCY_SUFFIX_RE.finditer(description or ""):
        if match.group(1) in _KNOWN_CURRENCIES:
            return match.group(1)
    return None


def _parse_amount(value: Any) -> float | None:
    """Parse a number from a cell that may be numeric or European-formatted text.

    Handles "272 420,54", "105 400,00", "1.234.567,89", "1,234,567.89",
    and labelled text like "Nordea EUR: 272 420,54".
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, (int, float)):
        return float(value)

    text = str(value)
    matches = _AMOUNT_TEXT_RE.findall(text)
    if not matches:
        return None
    candidate = matches[-1].strip()
    candidate = candidate.replace(" ", "").replace(" ", "").replace("'", "")
    if not candidate or not re.search(r"\d", candidate):
        return None

    has_comma = "," in candidate
    has_dot = "." in candidate
    if has_comma and has_dot:
        # The rightmost separator is the decimal separator.
        if candidate.rfind(",") > candidate.rfind("."):
            candidate = candidate.replace(".", "").replace(",", ".")
        else:
            candidate = candidate.replace(",", "")
    elif has_comma:
        head, _, tail = candidate.rpartition(",")
        if len(tail) in (1, 2):
            candidate = head.replace(",", "") + "." + tail
        else:
            candidate = candidate.replace(",", "")

    try:
        return float(candidate)
    except ValueError:
        return None


def _parse_named_value(message: str, names: tuple[str, ...]) -> str | None:
    for name in names:
        pattern = rf"{re.escape(name)}\s*(?:is|=|:)\s*([^\n,;.]+)"
        match = re.search(pattern, message, flags=re.IGNORECASE)
        if match:
            value = match.group(1).strip().strip("`'\"")
            if value:
                return value
    return None


def _parse_account_value(message: str, names: tuple[str, ...]) -> str | None:
    value = _parse_named_value(message, names)
    if not value:
        return None
    match = re.search(r"(\d{4,8})", value)
    return match.group(1) if match else None


def _parse_any_date(message: str) -> str | None:
    match = _DATE_RE.search(message)
    if not match:
        return None
    if match.group(1):
        year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
    else:
        day, month, year = int(match.group(4)), int(match.group(5)), int(match.group(6))
        if year < 100:
            year += 2000
    try:
        return datetime(year, month, day).strftime("%Y-%m-%d")
    except ValueError:
        return None


def _format_date(value: str) -> str:
    try:
        return datetime.strptime(value, "%Y-%m-%d").strftime("%d-%m-%Y")
    except ValueError:
        return value


def _year_of(value: str) -> str:
    try:
        return str(datetime.strptime(value, "%Y-%m-%d").year)
    except ValueError:
        return str(datetime.now().year)
