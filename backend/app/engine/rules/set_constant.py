"""
Rule: set_constant
Set one or more columns to constant values.
"""

import pandas as pd
from app.engine.rules import register_rule


@register_rule("set_constant")
class SetConstantRule:
    def execute(self, df: pd.DataFrame, sheets: dict[str, pd.DataFrame], config: dict) -> pd.DataFrame:
        assignments = config.get("assignments", {})
        for col_name, value in assignments.items():
            df[col_name] = value
        return df

    def schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "assignments": {
                    "type": "object",
                    "description": "Map of column name → constant value to set",
                    "additionalProperties": True,
                },
            },
            "required": ["assignments"],
        }

    def describe(self, config: dict) -> str:
        assignments = config.get("assignments", {})
        cols = list(assignments.keys())
        if len(cols) <= 3:
            return f"Set {', '.join(cols)} to constant values"
        return f"Set {len(cols)} columns to constant values"
