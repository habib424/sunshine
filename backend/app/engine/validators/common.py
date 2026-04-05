import pandas as pd

from app.engine.registry import register_validator


@register_validator("required_fields")
def required_fields(df: pd.DataFrame, params: dict) -> list[dict]:
    fields = params.get("fields", [])
    issues = []
    for field in fields:
        if field not in df.columns:
            issues.append({
                "severity": "error",
                "row_number": None,
                "column_name": field,
                "message": f"Required column '{field}' is missing",
                "validator_name": "required_fields",
            })
            continue
        null_rows = df[df[field].isna() | (df[field].astype(str).str.strip() == "")]
        for idx in null_rows.index:
            issues.append({
                "severity": "error",
                "row_number": int(idx) + 2,  # +2 for 1-indexed + header row
                "column_name": field,
                "message": f"Required field '{field}' is empty",
                "validator_name": "required_fields",
            })
    return issues


@register_validator("debit_credit_balance")
def debit_credit_balance(df: pd.DataFrame, params: dict) -> list[dict]:
    debit_col = params.get("debit_column", "debit_amount")
    credit_col = params.get("credit_column", "credit_amount")
    tolerance = params.get("tolerance", 0.01)
    issues = []

    if debit_col not in df.columns or credit_col not in df.columns:
        issues.append({
            "severity": "error",
            "row_number": None,
            "column_name": None,
            "message": f"Cannot check balance: missing '{debit_col}' or '{credit_col}' column",
            "validator_name": "debit_credit_balance",
        })
        return issues

    total_debit = pd.to_numeric(df[debit_col], errors="coerce").fillna(0).sum()
    total_credit = pd.to_numeric(df[credit_col], errors="coerce").fillna(0).sum()
    diff = abs(total_debit - total_credit)

    if diff > tolerance:
        issues.append({
            "severity": "error",
            "row_number": None,
            "column_name": None,
            "message": f"Debit/Credit imbalance: debits={total_debit:.2f}, credits={total_credit:.2f}, difference={diff:.2f}",
            "validator_name": "debit_credit_balance",
        })
    return issues


@register_validator("no_duplicates")
def no_duplicates(df: pd.DataFrame, params: dict) -> list[dict]:
    columns = params.get("columns", [])
    issues = []
    existing_cols = [c for c in columns if c in df.columns]
    if not existing_cols:
        return issues

    duplicated = df[df.duplicated(subset=existing_cols, keep=False)]
    for idx in duplicated.index:
        values = {c: str(df.at[idx, c]) for c in existing_cols}
        issues.append({
            "severity": "warning",
            "row_number": int(idx) + 2,
            "column_name": ", ".join(existing_cols),
            "message": f"Duplicate row found: {values}",
            "validator_name": "no_duplicates",
        })
    return issues


@register_validator("numeric_range")
def numeric_range(df: pd.DataFrame, params: dict) -> list[dict]:
    column = params.get("column")
    min_val = params.get("min")
    max_val = params.get("max")
    issues = []

    if column not in df.columns:
        return issues

    numeric = pd.to_numeric(df[column], errors="coerce")
    for idx in df.index:
        val = numeric.at[idx]
        if pd.isna(val):
            continue
        if min_val is not None and val < min_val:
            issues.append({
                "severity": "warning",
                "row_number": int(idx) + 2,
                "column_name": column,
                "message": f"Value {val} is below minimum {min_val}",
                "validator_name": "numeric_range",
            })
        if max_val is not None and val > max_val:
            issues.append({
                "severity": "warning",
                "row_number": int(idx) + 2,
                "column_name": column,
                "message": f"Value {val} is above maximum {max_val}",
                "validator_name": "numeric_range",
            })
    return issues
