"""
Rule: map_columns
Rename columns and reorder them to match a target schema.
Optionally adds missing target columns as null.
"""

import pandas as pd
from app.engine.rules import register_rule


@register_rule("map_columns")
class MapColumnsRule:
    def execute(self, df: pd.DataFrame, sheets: dict[str, pd.DataFrame], config: dict) -> pd.DataFrame:
        rename_map = config.get("rename", {})  # {source_col: target_col}
        column_order = config.get("column_order", [])  # final column order
        drop_unmapped = config.get("drop_unmapped", True)

        # Rename
        if rename_map:
            df = df.rename(columns=rename_map)

        # Enforce column order
        if column_order:
            # Add missing columns as None
            for col in column_order:
                if col not in df.columns:
                    df[col] = None

            if drop_unmapped:
                df = df[column_order]
            else:
                extra = [c for c in df.columns if c not in column_order]
                df = df[column_order + extra]

        return df

    def schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "rename": {
                    "type": "object",
                    "description": "Map of current column names → target column names",
                    "additionalProperties": {"type": "string"},
                },
                "column_order": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Final column order (missing columns added as null)",
                },
                "drop_unmapped": {
                    "type": "boolean",
                    "description": "Remove columns not in column_order",
                },
            },
        }

    def describe(self, config: dict) -> str:
        n_rename = len(config.get("rename", {}))
        n_order = len(config.get("column_order", []))
        parts = []
        if n_rename:
            parts.append(f"rename {n_rename} columns")
        if n_order:
            parts.append(f"reorder to {n_order}-column schema")
        return ", ".join(parts) if parts else "Map columns to target schema"
