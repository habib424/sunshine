"""
Rule: unpivot_entities
Pivots entity/company columns into rows. Turns a wide TB (accounts as rows,
entities as columns) into a long format (one row per entity-account pair).
"""

import pandas as pd
from app.engine.rules import register_rule


def _int(val, default):
    return int(val) if val is not None else default


@register_rule("unpivot_entities")
class UnpivotEntitiesRule:
    def execute(self, df: pd.DataFrame, sheets: dict[str, pd.DataFrame], config: dict) -> pd.DataFrame:
        account_col = config.get("account_col_name")
        entity_columns = config.get("entity_columns")  # list of column names
        extra_cols = config.get("keep_columns", [])  # other columns to preserve

        if not account_col:
            raise ValueError("unpivot_entities requires 'account_col_name'")

        # Auto-detect entity columns if not specified
        if not entity_columns:
            first_entity_idx = _int(config.get("first_entity_col_index"), 3)
            all_cols = list(df.columns)
            entity_columns = []
            for col in all_cols[first_entity_idx:]:
                col_str = str(col).strip()
                if col_str in ("", "Total", "nan") or pd.isna(col):
                    break
                entity_columns.append(col)

        if not entity_columns:
            raise ValueError("No entity columns found to unpivot")

        rows = []
        for _, row in df.iterrows():
            account = row[account_col]
            extras = {c: row.get(c) for c in extra_cols if c in df.columns}

            for entity_col in entity_columns:
                try:
                    value = float(row[entity_col]) if pd.notna(row[entity_col]) else 0.0
                except (ValueError, TypeError):
                    value = 0.0

                rows.append({
                    "Company": str(entity_col).strip(),
                    "account_code": account,
                    "value": value,
                    **extras,
                })

        return pd.DataFrame(rows)

    def schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "account_col_name": {"type": "string", "description": "Column name containing account codes"},
                "entity_columns": {"type": "array", "items": {"type": "string"}, "description": "List of column names to unpivot (auto-detected if empty)"},
                "first_entity_col_index": {"type": "integer", "description": "0-indexed column index where entity columns start (for auto-detection)"},
                "keep_columns": {"type": "array", "items": {"type": "string"}, "description": "Additional columns to preserve in each row"},
            },
            "required": ["account_col_name"],
        }

    def describe(self, config: dict) -> str:
        n = len(config.get("entity_columns", []))
        return f"Expand {n or 'auto-detected'} entity columns into rows (one row per entity-account pair)"
