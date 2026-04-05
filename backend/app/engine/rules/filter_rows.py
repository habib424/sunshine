"""
Rule: filter_rows
Remove or keep rows based on conditions.
Supports: equals, not_equals, greater_than, less_than, is_null, is_not_null, is_zero.
"""

import pandas as pd
from app.engine.rules import register_rule


def _eval_condition(series: pd.Series, operator: str, value=None) -> pd.Series:
    if operator == "eq":
        return series == value
    elif operator == "neq":
        return series != value
    elif operator == "gt":
        return pd.to_numeric(series, errors="coerce") > value
    elif operator == "lt":
        return pd.to_numeric(series, errors="coerce") < value
    elif operator == "gte":
        return pd.to_numeric(series, errors="coerce") >= value
    elif operator == "lte":
        return pd.to_numeric(series, errors="coerce") <= value
    elif operator == "is_null":
        return series.isna()
    elif operator == "is_not_null":
        return series.notna()
    elif operator == "is_zero":
        return pd.to_numeric(series, errors="coerce").fillna(0) == 0
    elif operator == "is_not_zero":
        return pd.to_numeric(series, errors="coerce").fillna(0) != 0
    elif operator == "contains":
        return series.astype(str).str.contains(str(value), case=False, na=False)
    else:
        raise ValueError(f"Unknown operator: {operator}")


@register_rule("filter_rows")
class FilterRowsRule:
    def execute(self, df: pd.DataFrame, sheets: dict[str, pd.DataFrame], config: dict) -> pd.DataFrame:
        conditions = config.get("conditions", [])
        logic = config.get("logic", "and")  # "and" or "or"
        action = config.get("action", "remove")  # "remove" or "keep"

        if not conditions:
            return df

        masks = []
        for cond in conditions:
            col = cond.get("column")
            op = cond.get("operator", "eq")
            val = cond.get("value")

            if col not in df.columns:
                masks.append(pd.Series([False] * len(df), index=df.index))
                continue

            masks.append(_eval_condition(df[col], op, val))

        if logic == "and":
            combined = masks[0]
            for m in masks[1:]:
                combined = combined & m
        else:  # "or"
            combined = masks[0]
            for m in masks[1:]:
                combined = combined | m

        if action == "remove":
            return df[~combined].reset_index(drop=True)
        else:  # "keep"
            return df[combined].reset_index(drop=True)

    def schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "conditions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "column": {"type": "string"},
                            "operator": {"type": "string", "enum": ["eq", "neq", "gt", "lt", "gte", "lte", "is_null", "is_not_null", "is_zero", "is_not_zero", "contains"]},
                            "value": {},
                        },
                    },
                },
                "logic": {"type": "string", "enum": ["and", "or"], "description": "How to combine multiple conditions"},
                "action": {"type": "string", "enum": ["remove", "keep"], "description": "Whether to remove or keep matching rows"},
            },
        }

    def describe(self, config: dict) -> str:
        n = len(config.get("conditions", []))
        action = config.get("action", "remove")
        return f"{action.capitalize()} rows matching {n} condition(s)"
