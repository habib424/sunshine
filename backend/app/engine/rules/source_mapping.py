"""
Rule: source_mapping
Reads a specific sheet and prepares it as the initial DataFrame for the pipeline.
"""

import pandas as pd
from app.engine.rules import register_rule


def _int(val, default):
    return int(val) if val is not None else default


@register_rule("source_mapping")
class SourceMappingRule:
    def execute(self, df: pd.DataFrame, sheets: dict[str, pd.DataFrame], config: dict) -> pd.DataFrame:
        sheet_name = config.get("sheet", "Sheet1")
        if sheet_name not in sheets:
            raise ValueError(f"Sheet '{sheet_name}' not found. Available: {list(sheets.keys())}")

        raw = sheets[sheet_name].copy()
        header_row = _int(config.get("header_row"), 0)
        account_col = _int(config.get("account_col"), 0)

        # Set column headers from the specified header row
        headers = raw.iloc[header_row]
        data = raw.iloc[header_row + 1:].copy()
        data.columns = headers

        # Filter out rows where account column is empty
        acct_header = headers.iloc[account_col]
        data = data[pd.notna(data[acct_header])].copy()

        # Optionally select only specific columns
        select_cols = config.get("select_columns")
        if select_cols:
            data = data[[c for c in select_cols if c in data.columns]]

        data = data.reset_index(drop=True)
        return data

    def schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "sheet": {"type": "string", "description": "Sheet name to read from"},
                "header_row": {"type": "integer", "description": "0-indexed row containing column headers"},
                "account_col": {"type": "integer", "description": "0-indexed column with account codes (used to filter empty rows)"},
                "select_columns": {"type": "array", "items": {"type": "string"}, "description": "Optional: only keep these columns"},
            },
            "required": ["sheet"],
        }

    def describe(self, config: dict) -> str:
        sheet = config.get("sheet", "?")
        return f"Read data from sheet '{sheet}'"
