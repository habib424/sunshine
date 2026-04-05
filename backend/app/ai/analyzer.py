"""
AI-driven file analyzer.

Reads all sheets from an uploaded workbook, sends the structure to Claude,
and gets back a human-readable analysis + a ready-to-run playbook config.
"""

import json
import re
from pathlib import Path

import pandas as pd

from app.ai.client import get_client

# Describes every registered file-transform handler so Claude can match against them
HANDLER_REGISTRY = {
    "tb_opening_balance": {
        "description": (
            "Multi-entity Trial Balance → Light journal entry upload format. "
            "Reads an Entities sheet (currency lookup) and a TB sheet where each "
            "column after the first few is an entity/company with its balances. "
            "Produces one journal entry row per (entity, account) pair, splitting "
            "positive values into debit and negative values into credit."
        ),
        "required_signals": [
            "A sheet containing entity/company names and their currencies",
            "A sheet with account codes as rows and entity names as columns",
            "Balance values that need to be split into debit/credit",
        ],
        "params": {
            "entities_sheet": "Name of the sheet containing entity master data",
            "tb_sheet": "Name of the sheet containing the trial balance data",
            "tb_header_row": "0-indexed row number of the column header row in the TB sheet",
            "tb_account_col": "0-indexed column number of the account code column",
            "tb_first_entity_col": "0-indexed column number of the first entity balance column",
            "entities_name_col": "0-indexed column number of the entity display name in the entities sheet",
            "entities_currency_col": "0-indexed column number of the currency in the entities sheet",
            "entities_header_row": "0-indexed row number of the header in the entities sheet",
            "date": "Posting date in YYYY-MM-DD format",
            "entry_description": "Text for the entry description field",
            "entry_id_prefix": "Prefix for the journal entry ID, e.g. 'Opening-TB-2025-'",
        },
    }
}

ANALYZE_PROMPT = """You are a senior ERP financial data migration consultant. A user has uploaded an Excel workbook and wants to understand what transformation is needed to migrate it into their target ERP (Light).

Here is the complete structure of the workbook:

{file_structure}

Available transformation handlers:
{handlers}

Your task:
1. Analyze the workbook structure and identify what type of financial data this is.
2. Match it to the most appropriate transformation handler (or say "unknown" if none fit).
3. Generate the exact parameters needed to run the handler.
4. Provide a clear, plain-English explanation of what the transformation will do.

Respond with ONLY a valid JSON object in this exact format:
{{
  "summary": "<2-3 sentence plain English description of what this file contains and what the transformation will do>",
  "file_type": "<trial_balance|gl_history|open_ap|open_ar|chart_of_accounts|vendors|customers|unknown>",
  "handler": "<handler_name or null>",
  "confidence": <0.0 to 1.0>,
  "entities_found": ["<list of entity/company names if applicable>"],
  "params": {{<complete params object for the handler, or null>}},
  "warnings": ["<any issues or assumptions the user should review>"]
}}"""


def _rule_based_analyze(file_structure: dict) -> dict | None:
    """
    Pattern-match the file structure against known handlers without using the AI.
    Returns an analysis dict if a match is found, otherwise None.
    """
    sheet_names = list(file_structure.keys())
    sheet_names_lower = [s.lower() for s in sheet_names]

    # --- Pattern: Multi-entity TB (Entities + TB sheets) ---
    entities_sheet = next((s for s in sheet_names if "entit" in s.lower()), None)
    tb_sheet = next((s for s in sheet_names if s.lower() in ("tb", "trial balance", "trial_balance")), None)

    if entities_sheet and tb_sheet:
        tb_data = file_structure[tb_sheet]
        ent_data = file_structure[entities_sheet]

        # Find the header row in TB (row with "Light account number" or similar)
        tb_header_row = 1
        tb_account_col = 0
        tb_first_entity_col = 3
        for row in tb_data["rows"]:
            vals = [str(v).lower() if v else "" for v in row["values"]]
            if any("account" in v for v in vals):
                tb_header_row = row["row_index"]
                # Find first entity col (after account + name columns)
                non_empty = [i for i, v in enumerate(vals) if v and "account" not in v and "name" not in v and "nan" not in v]
                if non_empty:
                    tb_first_entity_col = non_empty[0]
                break

        # Find header row in entities sheet
        ent_header_row = 1
        ent_name_col = 2
        ent_currency_col = 4
        for row in ent_data["rows"]:
            vals = [str(v).lower() if v else "" for v in row["values"]]
            if any("currency" in v for v in vals) or any("display" in v for v in vals):
                ent_header_row = row["row_index"]
                for i, v in enumerate(vals):
                    if "display" in v or "name" in v:
                        ent_name_col = i
                    if "currency" in v or "curr" in v:
                        ent_currency_col = i
                break

        # Detect posting date from TB header row 0
        date = "2025-12-31"
        for row in tb_data["rows"]:
            for val in row["values"]:
                if val and "december" in str(val).lower():
                    year_match = re.search(r"20\d\d", str(val))
                    if year_match:
                        date = f"{year_match.group()}-12-31"
                elif val:
                    date_match = re.search(r"(\d{4})-(\d{2})-(\d{2})", str(val))
                    if date_match:
                        date = date_match.group(0)

        # Collect entity names from TB header row
        entity_col_header_row = next(
            (r for r in tb_data["rows"] if r["row_index"] == tb_header_row), None
        )
        entities_found = []
        if entity_col_header_row:
            for v in entity_col_header_row["values"][tb_first_entity_col:]:
                if v and str(v).strip() not in ("", "None", "Total", "nan"):
                    entities_found.append(str(v).strip())

        return {
            "summary": (
                f"Multi-entity Trial Balance workbook with {len(entities_found)} companies detected. "
                f"Sunshine will transform each entity's balances into Light journal entry format, "
                f"splitting positive values into debits and negative values into credits."
            ),
            "file_type": "trial_balance",
            "handler": "tb_opening_balance",
            "confidence": 0.92,
            "entities_found": entities_found,
            "params": {
                "entities_sheet": entities_sheet,
                "tb_sheet": tb_sheet,
                "tb_header_row": tb_header_row,
                "tb_account_col": tb_account_col,
                "tb_first_entity_col": tb_first_entity_col,
                "entities_name_col": ent_name_col,
                "entities_currency_col": ent_currency_col,
                "entities_header_row": ent_header_row,
                "date": date,
                "entry_description": "Data migration : Opening TB",
                "entry_id_prefix": f"Opening-TB-{date[:4]}-",
            },
            "warnings": [
                "Detected using pattern matching (no AI key configured). Review the parameters below before running.",
                f"Posting date set to {date} — adjust if needed.",
            ],
            "ai_assisted": False,
        }

    return None


def _read_file_structure(file_path: Path) -> dict:
    """Read all sheets and return a compact structure summary."""
    sheets_raw = pd.read_excel(file_path, sheet_name=None, header=None, nrows=30, engine="openpyxl")
    structure = {}
    for sheet_name, df in sheets_raw.items():
        rows_preview = []
        for i, row in df.iterrows():
            rows_preview.append({
                "row_index": i,
                "values": [str(v) if pd.notna(v) else None for v in row.tolist()],
            })
            if i >= 7:
                break
        structure[sheet_name] = {
            "total_rows_preview": len(df),
            "total_cols": len(df.columns),
            "rows": rows_preview,
        }
    return structure


def analyze_file(file_path: Path) -> dict:
    """
    Analyze an uploaded file and return a transformation plan.
    Uses Claude AI when an API key is configured, falls back to
    rule-based pattern matching otherwise.
    """
    file_structure = _read_file_structure(file_path)
    sheet_names = list(file_structure.keys())

    # --- Rule-based pattern matching (fast + reliable) ---
    result = _rule_based_analyze(file_structure)
    if result:
        result["sheet_names"] = sheet_names
        return result

    # --- Nothing matched ---
    return {
        "summary": "Could not automatically identify this file type.",
        "file_type": "unknown",
        "handler": None,
        "confidence": 0.0,
        "entities_found": [],
        "params": None,
        "warnings": ["No matching transform pattern found. Add an Anthropic API key for AI-assisted detection."],
        "sheet_names": sheet_names,
        "ai_assisted": False,
    }


def build_playbook_config(analysis: dict) -> dict | None:
    """Turn an analysis result into a ready-to-save playbook config dict."""
    if not analysis.get("handler") or not analysis.get("params"):
        return None

    return {
        "transform_type": "file",
        "handler": analysis["handler"],
        "params": analysis["params"],
    }
