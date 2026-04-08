"""Check: date values can be parsed."""

import pandas as pd

from app.engine.checks import register_check, _issue, _safe_str


@register_check("parseable_date")
class ParseableDateCheck:
    def execute(self, df: pd.DataFrame, rule: dict, runtime: dict) -> list[dict]:
        column = rule["params"]["column"]
        if column not in df.columns:
            return []

        parsed = pd.to_datetime(df[column], errors="coerce")
        # Only flag rows that have a value but it can't be parsed.
        # Blank/NaN dates are handled by the non_empty check.
        has_value = df[column].notna() & (df[column].astype(str).str.strip() != "")
        bad_mask = parsed.isna() & has_value

        issues: list[dict] = []
        for idx in df.index[bad_mask]:
            issues.append(
                _issue(
                    rule,
                    f"Date value '{df.at[idx, column]}' cannot be parsed",
                    entry_id=_safe_str(df.at[idx, "entry_id"]) if "entry_id" in df.columns else None,
                    row_number=int(idx) + 2,
                    column=column,
                    details={"raw_value": _safe_str(df.at[idx, column])},
                )
            )
        return issues

    def schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "column": {"type": "string", "description": "Column containing date values"},
            },
            "required": ["column"],
        }

    def describe(self, params: dict) -> str:
        col = params.get("column", "?")
        return f"Checks that values in '{col}' can be parsed as dates"
