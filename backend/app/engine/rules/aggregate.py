"""
Rule: aggregate
Group rows by key columns and aggregate numeric columns (sum, first, etc.).
Useful for combining duplicate account codes into a single row.
"""

import pandas as pd
from app.engine.rules import register_rule


@register_rule("aggregate")
class AggregateRule:
    def execute(self, df: pd.DataFrame, sheets: dict[str, pd.DataFrame], config: dict) -> pd.DataFrame:
        group_by = config.get("group_by", [])
        agg_rules = config.get("aggregations", {})  # {col: "sum"|"first"|"last"|"count"|"mean"}
        default_agg = config.get("default_aggregation", "first")

        if not group_by:
            return df

        # Build aggregation dict
        agg_dict = {}
        for col in df.columns:
            if col in group_by:
                continue
            agg_dict[col] = agg_rules.get(col, default_agg)

        return df.groupby(group_by, as_index=False).agg(agg_dict).reset_index(drop=True)

    def schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "group_by": {"type": "array", "items": {"type": "string"}, "description": "Columns to group by"},
                "aggregations": {"type": "object", "description": "Column → aggregation function (sum, first, last, count, mean)"},
                "default_aggregation": {"type": "string", "enum": ["sum", "first", "last", "count", "mean"]},
            },
            "required": ["group_by"],
        }

    def describe(self, config: dict) -> str:
        group_by = config.get("group_by", [])
        return f"Aggregate rows by {', '.join(group_by)}"
