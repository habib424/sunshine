"""Check: all rows in a group share the same value for a column."""

import pandas as pd

from app.engine.checks import register_check, _issue, _safe_str


@register_check("group_consistency")
class GroupConsistencyCheck:
    def execute(self, df: pd.DataFrame, rule: dict, runtime: dict) -> list[dict]:
        params = rule["params"]
        column = params["column"]
        grain = rule.get("grain") or runtime.get("grain", "entry_id")

        if column not in df.columns or grain not in df.columns:
            return []

        issues: list[dict] = []
        for group_key, group in df.groupby(grain, dropna=False):
            # Normalise blanks to NaN so "" and NaN don't register as a split.
            normalised = group[column].where(
                group[column].astype(str).str.strip() != "",
                other=pd.NA,
            )
            distinct = normalised.dropna().unique()
            if len(distinct) > 1:
                gid = _safe_str(group_key)
                issues.append(
                    _issue(
                        rule,
                        f"Entry '{gid}' has {len(distinct)} distinct values for '{column}': {list(distinct)}",
                        entry_id=gid,
                        column=column,
                        details={"distinct_values": [str(v) for v in distinct]},
                    )
                )
        return issues

    def schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "column": {"type": "string", "description": "Column that must be consistent within each group"},
            },
            "required": ["column"],
        }

    def describe(self, params: dict) -> str:
        col = params.get("column", "?")
        return f"Checks that all rows in a group have the same '{col}' value"
