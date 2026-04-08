"""Check: at least one of a set of columns is populated per line."""

import pandas as pd

from app.engine.checks import register_check, _issue, _safe_str


@register_check("at_least_one_populated")
class AtLeastOnePopulatedCheck:
    def execute(self, df: pd.DataFrame, rule: dict, runtime: dict) -> list[dict]:
        columns = rule["params"]["columns"]
        nonzero = rule["params"].get("nonzero", False)
        present = [c for c in columns if c in df.columns]
        if not present:
            return []

        issues: list[dict] = []
        for idx in df.index:
            has_value = False
            for col in present:
                val = df.at[idx, col]
                if pd.notna(val):
                    if nonzero:
                        try:
                            if float(val) != 0:
                                has_value = True
                                break
                        except (ValueError, TypeError):
                            pass
                    else:
                        if str(val).strip():
                            has_value = True
                            break
            if not has_value:
                issues.append(
                    _issue(
                        rule,
                        f"Line has none of [{', '.join(present)}] populated",
                        entry_id=_safe_str(df.at[idx, "entry_id"]) if "entry_id" in df.columns else None,
                        row_number=int(idx) + 2,
                        column="/".join(present),
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
                    "description": "At least one of these columns must be populated",
                },
                "nonzero": {
                    "type": "boolean",
                    "description": "If true, zero values are treated as empty",
                    "default": False,
                },
            },
            "required": ["columns"],
        }

    def describe(self, params: dict) -> str:
        cols = params.get("columns", [])
        return f"Checks that at least one of {cols} is populated per row"
