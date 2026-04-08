"""Check: debit/credit columns balance within each group."""

import pandas as pd

from app.engine.checks import register_check, _issue, _safe_str


@register_check("group_balance")
class GroupBalanceCheck:
    def execute(self, df: pd.DataFrame, rule: dict, runtime: dict) -> list[dict]:
        params = rule["params"]
        debit_col = params.get("debit_column", "debit")
        credit_col = params.get("credit_column", "credit")
        tolerance = params.get("tolerance", 0.01)
        grain = rule.get("grain") or runtime.get("grain", "entry_id")

        if grain not in df.columns:
            return []
        if debit_col not in df.columns or credit_col not in df.columns:
            return []

        debit = pd.to_numeric(df[debit_col], errors="coerce").fillna(0)
        credit = pd.to_numeric(df[credit_col], errors="coerce").fillna(0)
        net = debit - credit

        issues: list[dict] = []
        for group_key, group_net in net.groupby(df[grain], dropna=False):
            diff = float(group_net.sum())
            if abs(diff) > tolerance:
                gid = _safe_str(group_key)
                issues.append(
                    _issue(
                        rule,
                        f"Entry '{gid}' is unbalanced by {diff:.4f} (tolerance {tolerance})",
                        entry_id=gid,
                        details={
                            "difference": diff,
                            "tolerance": tolerance,
                            "total_debit": float(debit[df[grain] == group_key].sum()),
                            "total_credit": float(credit[df[grain] == group_key].sum()),
                        },
                    )
                )
        return issues

    def schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "debit_column": {"type": "string", "default": "debit"},
                "credit_column": {"type": "string", "default": "credit"},
                "tolerance": {"type": "number", "default": 0.01, "description": "Maximum allowed imbalance"},
            },
        }

    def describe(self, params: dict) -> str:
        tol = params.get("tolerance", 0.01)
        return f"Checks that debits and credits balance within each entry (tolerance {tol})"
