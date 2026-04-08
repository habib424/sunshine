"""Check: required columns exist in the DataFrame."""

import pandas as pd

from app.engine.checks import register_check, _issue


@register_check("required_columns")
class RequiredColumnsCheck:
    def execute(self, df: pd.DataFrame, rule: dict, runtime: dict) -> list[dict]:
        columns = rule["params"]["columns"]
        missing = [c for c in columns if c not in df.columns]
        return [
            _issue(
                rule,
                f"Required column '{col}' is missing from the file",
                column=col,
                details={"expected_column": col},
            )
            for col in missing
        ]

    def schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "columns": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of column names that must exist",
                },
            },
            "required": ["columns"],
        }

    def describe(self, params: dict) -> str:
        cols = params.get("columns", [])
        return f"Checks that columns {cols} exist in the file"
