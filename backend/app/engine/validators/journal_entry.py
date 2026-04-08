"""
Journal Entry validators.

Each validator checks one invariant of the Journal Entry contract and emits
issues keyed by a stable issue code. Issues returned by this module all share
the same shape:

    {
        "issue_code":    str,   # stable, from contracts.journal_entry.ISSUE_CODES
        "severity":      "error" | "warning",
        "scope":         "file" | "entry" | "line",
        "entry_id":      str | None,
        "row_number":    int | None,    # 1-indexed data row, no header offset
        "column":        str | None,    # canonical column name
        "message":       str,           # human-readable explanation
        "details":       dict,          # structured payload for the resolver
    }

The resolver keys on (issue_code, entry_id) or (issue_code, row_number) to
look up learned resolutions, so those fields must be populated whenever the
scope allows it.

All validators operate on a DataFrame whose columns have already been
normalised to the canonical column names from the contract. Upstream mapping
is responsible for that normalisation; these validators never guess.
"""

from __future__ import annotations

import pandas as pd

from app.engine.contracts.journal_entry import (
    CANONICAL_COLUMNS,
    JOURNAL_ENTRY_CONTRACT,
)
from app.engine.registry import register_validator


# ISO 4217 is long; we keep a small built-in set for offline validation and
# let callers inject a fuller list via params if they want stricter checks.
_DEFAULT_ISO_CURRENCIES = {
    "AED", "AUD", "BRL", "CAD", "CHF", "CNY", "CZK", "DKK", "EUR", "GBP",
    "HKD", "HUF", "IDR", "ILS", "INR", "JPY", "KRW", "MXN", "MYR", "NOK",
    "NZD", "PHP", "PLN", "RON", "RUB", "SAR", "SEK", "SGD", "THB", "TRY",
    "TWD", "USD", "ZAR",
}


def _issue(
    issue_code: str,
    severity: str,
    scope: str,
    message: str,
    *,
    entry_id: str | None = None,
    row_number: int | None = None,
    column: str | None = None,
    details: dict | None = None,
) -> dict:
    return {
        "issue_code": issue_code,
        "severity": severity,
        "scope": scope,
        "entry_id": entry_id,
        "row_number": row_number,
        "column": column,
        "message": message,
        "details": details or {},
    }


def _missing_required_columns(df: pd.DataFrame) -> list[dict]:
    required = JOURNAL_ENTRY_CONTRACT["required_columns"]
    missing = [c for c in required if c not in df.columns]
    return [
        _issue(
            "JE-MISSING-FIELD",
            "error",
            "file",
            f"Required canonical column '{col}' is missing from the mapped file",
            column=col,
            details={"expected_column": col, "role": CANONICAL_COLUMNS.get(col, "")},
        )
        for col in missing
    ]


def _check_empty_fields(df: pd.DataFrame) -> list[dict]:
    required = JOURNAL_ENTRY_CONTRACT["required_columns"]
    issues: list[dict] = []
    for col in required:
        if col not in df.columns:
            continue
        series = df[col]
        empty_mask = series.isna() | (series.astype(str).str.strip() == "")
        for idx in df.index[empty_mask]:
            issues.append(
                _issue(
                    "JE-EMPTY-FIELD",
                    "error",
                    "line",
                    f"Required field '{col}' is empty",
                    entry_id=_safe_str(df.at[idx, "entry_id"]) if "entry_id" in df.columns else None,
                    row_number=int(idx) + 2,
                    column=col,
                )
            )
    return issues


def _check_amounts(df: pd.DataFrame) -> list[dict]:
    """Check that each line has at least one of debit or credit populated."""
    amount_cols = JOURNAL_ENTRY_CONTRACT.get("amount_columns", ["debit", "credit"])
    present = [c for c in amount_cols if c in df.columns]
    if not present:
        return []

    issues: list[dict] = []
    for idx in df.index:
        has_amount = False
        for col in present:
            val = df.at[idx, col]
            if pd.notna(val):
                try:
                    if float(val) != 0:
                        has_amount = True
                        break
                except (ValueError, TypeError):
                    pass
        if not has_amount:
            issues.append(
                _issue(
                    "JE-NO-AMOUNT",
                    "error",
                    "line",
                    "Line has neither debit nor credit amount",
                    entry_id=_safe_str(df.at[idx, "entry_id"]) if "entry_id" in df.columns else None,
                    row_number=int(idx) + 2,
                    column="debit/credit",
                )
            )
    return issues


def _check_per_group_consistency(df: pd.DataFrame) -> list[dict]:
    """
    For each group of lines sharing an entry_id, verify that the columns
    listed in per_group_invariants hold a single distinct value.
    """
    if "entry_id" not in df.columns:
        return []

    issues: list[dict] = []
    invariants = [
        inv for inv in JOURNAL_ENTRY_CONTRACT["per_group_invariants"]
        if inv["column"] is not None
    ]

    for entry_id, group in df.groupby("entry_id", dropna=False):
        eid = _safe_str(entry_id)
        for inv in invariants:
            col = inv["column"]
            if col not in df.columns:
                continue
            # Normalise blanks to NaN so "" and NaN don't register as a split.
            normalised = group[col].where(
                group[col].astype(str).str.strip() != "",
                other=pd.NA,
            )
            distinct = normalised.dropna().unique()
            if len(distinct) > 1:
                issues.append(
                    _issue(
                        inv["issue_code"],
                        "error",
                        "entry",
                        f"Entry '{eid}' has {len(distinct)} distinct values for '{col}': {list(distinct)}",
                        entry_id=eid,
                        column=col,
                        details={"distinct_values": [str(v) for v in distinct]},
                    )
                )
    return issues


def _check_balance(df: pd.DataFrame) -> list[dict]:
    if "entry_id" not in df.columns:
        return []
    if "debit" not in df.columns or "credit" not in df.columns:
        return []

    tolerance = JOURNAL_ENTRY_CONTRACT["tolerances"]["balance"]
    debit = pd.to_numeric(df["debit"], errors="coerce").fillna(0)
    credit = pd.to_numeric(df["credit"], errors="coerce").fillna(0)
    net = debit - credit

    issues: list[dict] = []
    for entry_id, group_net in net.groupby(df["entry_id"], dropna=False):
        diff = float(group_net.sum())
        if abs(diff) > tolerance:
            eid = _safe_str(entry_id)
            issues.append(
                _issue(
                    "JE-UNBALANCED",
                    "error",
                    "entry",
                    f"Entry '{eid}' is unbalanced by {diff:.4f} (tolerance {tolerance})",
                    entry_id=eid,
                    details={
                        "difference": diff,
                        "tolerance": tolerance,
                        "total_debit": float(debit[df["entry_id"] == entry_id].sum()),
                        "total_credit": float(credit[df["entry_id"] == entry_id].sum()),
                    },
                )
            )
    return issues


def _check_dates(df: pd.DataFrame) -> list[dict]:
    if "date" not in df.columns:
        return []
    parsed = pd.to_datetime(df["date"], errors="coerce")
    bad_mask = parsed.isna() & df["date"].notna() & (df["date"].astype(str).str.strip() != "")
    issues: list[dict] = []
    for idx in df.index[bad_mask]:
        issues.append(
            _issue(
                "JE-INVALID-DATE",
                "error",
                "line",
                f"Date value '{df.at[idx, 'date']}' cannot be parsed",
                entry_id=_safe_str(df.at[idx, "entry_id"]) if "entry_id" in df.columns else None,
                row_number=int(idx) + 2,
                column="date",
                details={"raw_value": _safe_str(df.at[idx, "date"])},
            )
        )
    return issues


def _check_currencies(df: pd.DataFrame, allowed: set[str]) -> list[dict]:
    if "currency" not in df.columns:
        return []
    issues: list[dict] = []
    for idx in df.index:
        raw = df.at[idx, "currency"]
        if pd.isna(raw):
            continue
        code = str(raw).strip().upper()
        if not code:
            continue
        if code not in allowed:
            issues.append(
                _issue(
                    "JE-INVALID-CURRENCY",
                    "error",
                    "line",
                    f"Currency code '{code}' is not in the allowed ISO 4217 set",
                    entry_id=_safe_str(df.at[idx, "entry_id"]) if "entry_id" in df.columns else None,
                    row_number=int(idx) + 2,
                    column="currency",
                    details={"raw_value": code},
                )
            )
    return issues


def _check_gl_accounts(df: pd.DataFrame, coa: set[str], mapping: dict[str, str]) -> list[dict]:
    if "gl_account" not in df.columns:
        return []
    # If the caller provided neither a COA nor a mapping, we can't check
    # membership. Stay silent rather than flag every line.
    if not coa and not mapping:
        return []

    issues: list[dict] = []
    for idx in df.index:
        raw = df.at[idx, "gl_account"]
        if pd.isna(raw):
            continue
        code = str(raw).strip()
        if not code:
            continue
        if code in coa or code in mapping:
            continue
        issues.append(
            _issue(
                "JE-UNKNOWN-GL",
                "error",
                "line",
                f"GL account '{code}' is not in the chart of accounts or mapping table",
                entry_id=_safe_str(df.at[idx, "entry_id"]) if "entry_id" in df.columns else None,
                row_number=int(idx) + 2,
                column="gl_account",
                details={"account_code": code},
            )
        )
    return issues


def _safe_str(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    return str(value)


@register_validator("journal_entry_contract")
def journal_entry_contract(df: pd.DataFrame, params: dict) -> list[dict]:
    """
    Run Journal Entry validation checks loaded from YAML.

    The function signature is unchanged from the original. Internally,
    it loads rules from journal_entry_rules.yaml and dispatches to
    registered check executors. The YAML defines WHICH checks to run
    with WHAT parameters; the Python check types implement HOW.

    Params:
        chart_of_accounts: iterable of valid GL account codes. Optional.
        gl_mapping: dict of source-code -> target-code. Optional.
        allowed_currencies: iterable of ISO 4217 codes. Optional.
    """
    from app.engine.contracts.loader import load_rules
    from app.engine.checks import get_check_executor

    ruleset = load_rules("journal_entry")

    # Build runtime context from caller params (same keys as before).
    runtime = {
        "grain": ruleset.grain,
        "chart_of_accounts": set(str(c) for c in params.get("chart_of_accounts") or []),
        "gl_mapping": {str(k): str(v) for k, v in (params.get("gl_mapping") or {}).items()},
        "allowed_currencies": set(
            str(c).upper() for c in (params.get("allowed_currencies") or _DEFAULT_ISO_CURRENCIES)
        ),
    }

    issues: list[dict] = []

    # Phase 1: structural (short-circuit on failure)
    for rule in ruleset.structural_rules:
        executor = get_check_executor(rule["check_type"])
        found = executor.execute(df, rule, runtime)
        issues.extend(found)
    if issues:
        return issues

    # Phase 2: line-level checks
    for rule in ruleset.line_rules:
        executor = get_check_executor(rule["check_type"])
        issues.extend(executor.execute(df, rule, runtime))

    # Phase 3: group-level checks
    for rule in ruleset.group_rules:
        executor = get_check_executor(rule["check_type"])
        issues.extend(executor.execute(df, rule, runtime))

    return issues
