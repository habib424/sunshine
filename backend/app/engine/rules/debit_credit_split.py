"""
Rule: debit_credit_split
Splits a single value column into separate debit and credit columns.
Positive → debit, Negative → credit (absolute value), Zero → both None.
"""

import pandas as pd
from app.engine.rules import register_rule


@register_rule("debit_credit_split")
class DebitCreditSplitRule:
    def execute(self, df: pd.DataFrame, sheets: dict[str, pd.DataFrame], config: dict) -> pd.DataFrame:
        source_col = config.get("source_column", "value")
        debit_col = config.get("debit_column", "debit (required)")
        credit_col = config.get("credit_column", "credit (required)")

        if source_col not in df.columns:
            df[debit_col] = None
            df[credit_col] = None
            return df

        values = pd.to_numeric(df[source_col], errors="coerce").fillna(0)
        df[debit_col] = values.apply(lambda v: v if v > 0 else None)
        df[credit_col] = values.apply(lambda v: abs(v) if v < 0 else None)

        # Optionally drop the source column
        if config.get("drop_source", True):
            df = df.drop(columns=[source_col], errors="ignore")

        return df

    def schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "source_column": {"type": "string", "description": "Column containing the value to split"},
                "debit_column": {"type": "string", "description": "Column name for debit values"},
                "credit_column": {"type": "string", "description": "Column name for credit values"},
                "drop_source": {"type": "boolean", "description": "Remove the original value column after splitting"},
            },
        }

    def describe(self, config: dict) -> str:
        return "Split values into debit (positive) and credit (negative) columns"
