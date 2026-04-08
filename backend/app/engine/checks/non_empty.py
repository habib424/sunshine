"""Check: required fields are not blank on each line."""

import pandas as pd

from app.engine.checks import register_check, _issue, _safe_str


@register_check("non_empty")
class NonEmptyCheck:
    def execute(self, df: pd.DataFrame, rule: dict, runtime: dict) -> list[dict]:
        columns = rule["params"]["columns"]
        issues: list[dict] = []

        for col in columns:
            if col not in df.columns:
                continue
            series = df[col]
            empty_mask = series.isna() | (series.astype(str).str.strip() == "")
            for idx in df.index[empty_mask]:
                issues.append(
                    _issue(
                        rule,
                        f"Required field '{col}' is empty",
                        entry_id=_safe_str(df.at[idx, "entry_id"]) if "entry_id" in df.columns else None,
                        row_number=int(idx) + 2,
                        column=col,
                    )
                )
        return issues

    def schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "columns": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Columns that must not be blank",
                },
            },
            "required": ["columns"],
        }

    def describe(self, params: dict) -> str:
        cols = params.get("columns", [])
        return f"Checks that columns {cols} are not empty on each row"
