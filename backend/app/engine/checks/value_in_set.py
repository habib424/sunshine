"""Check: column values belong to an allowed set (currencies, GL accounts, etc.)."""

import pandas as pd

from app.engine.checks import register_check, _issue, _safe_str

# Built-in ISO 4217 currency codes for offline validation.
_DEFAULT_ISO_CURRENCIES = {
    "AED", "AUD", "BRL", "CAD", "CHF", "CNY", "CZK", "DKK", "EUR", "GBP",
    "HKD", "HUF", "IDR", "ILS", "INR", "JPY", "KRW", "MXN", "MYR", "NOK",
    "NZD", "PHP", "PLN", "RON", "RUB", "SAR", "SEK", "SGD", "THB", "TRY",
    "TWD", "USD", "ZAR",
}

_BUILTIN_SETS = {
    "iso_4217": _DEFAULT_ISO_CURRENCIES,
}


@register_check("value_in_set")
class ValueInSetCheck:
    def execute(self, df: pd.DataFrame, rule: dict, runtime: dict) -> list[dict]:
        params = rule["params"]
        column = params["column"]
        if column not in df.columns:
            return []

        normalize = params.get("normalize", None)

        # Build the allowed set from three sources (all optional):
        # 1. Built-in named set (e.g., "iso_4217")
        # 2. Runtime param from caller (e.g., chart_of_accounts passed at call time)
        # 3. Fallback runtime param (e.g., gl_mapping keys)
        allowed: set[str] = set()

        builtin_name = params.get("allowed_set")
        if builtin_name and builtin_name in _BUILTIN_SETS:
            allowed |= _BUILTIN_SETS[builtin_name]

        runtime_key = params.get("runtime_param")
        if runtime_key and runtime_key in runtime:
            allowed |= set(str(v) for v in runtime[runtime_key])

        fallback_key = params.get("fallback_param")
        if fallback_key and fallback_key in runtime:
            allowed |= set(str(k) for k in runtime[fallback_key])

        # If no allowed set was assembled, skip the check silently.
        if not allowed:
            return []

        issues: list[dict] = []
        for idx in df.index:
            raw = df.at[idx, column]
            if pd.isna(raw):
                continue
            code = str(raw).strip()
            if not code:
                continue

            check_val = code.upper() if normalize == "uppercase" else code
            if check_val not in allowed:
                issues.append(
                    _issue(
                        rule,
                        f"Value '{code}' in '{column}' is not in the allowed set",
                        entry_id=_safe_str(df.at[idx, "entry_id"]) if "entry_id" in df.columns else None,
                        row_number=int(idx) + 2,
                        column=column,
                        details={"raw_value": code},
                    )
                )
        return issues

    def schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "column": {"type": "string", "description": "Column to validate"},
                "allowed_set": {
                    "type": ["string", "null"],
                    "description": "Name of a built-in set (e.g., 'iso_4217'), or null",
                },
                "runtime_param": {
                    "type": "string",
                    "description": "Key in runtime params containing allowed values (e.g., 'chart_of_accounts')",
                },
                "fallback_param": {
                    "type": "string",
                    "description": "Fallback key in runtime params (e.g., 'gl_mapping')",
                },
                "normalize": {
                    "type": "string",
                    "enum": ["uppercase", "lowercase"],
                    "description": "Normalize values before comparison",
                },
            },
            "required": ["column"],
        }

    def describe(self, params: dict) -> str:
        col = params.get("column", "?")
        src = params.get("allowed_set") or params.get("runtime_param") or "configured set"
        return f"Checks that '{col}' values are in {src}"
