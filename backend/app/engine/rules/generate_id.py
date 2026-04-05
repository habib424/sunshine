"""
Rule: generate_id
Create a column with values generated from a template pattern.
Supports {column_name} placeholders that are replaced with row values.
"""

import pandas as pd
from app.engine.rules import register_rule


@register_rule("generate_id")
class GenerateIdRule:
    def execute(self, df: pd.DataFrame, sheets: dict[str, pd.DataFrame], config: dict) -> pd.DataFrame:
        target_col = config.get("target_column", "entry_id")
        pattern = config.get("pattern", "{Company}")

        def _render(row):
            result = pattern
            for col in df.columns:
                placeholder = "{" + str(col) + "}"
                if placeholder in result:
                    result = result.replace(placeholder, str(row.get(col, "")))
            return result

        df[target_col] = df.apply(_render, axis=1)
        return df

    def schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "target_column": {"type": "string", "description": "Column name to write the generated ID"},
                "pattern": {"type": "string", "description": "Template pattern with {column_name} placeholders, e.g. 'Opening-TB-2025-{Company}'"},
            },
            "required": ["target_column", "pattern"],
        }

    def describe(self, config: dict) -> str:
        pattern = config.get("pattern", "?")
        return f"Generate IDs from pattern: {pattern}"
