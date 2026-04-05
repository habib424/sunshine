"""
Analyzer v2: returns composable rules instead of handler+params.
Rule-based fallback for TB files. AI path can be added later.
"""

import re
import uuid
from pathlib import Path

import pandas as pd

from app.engine.target_schemas import TARGET_SCHEMAS


def _read_file_structure(file_path: Path) -> dict:
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


def _rid():
    return f"r_{uuid.uuid4().hex[:6]}"


def analyze_file_v2(file_path: Path) -> dict:
    """Analyze file and return composable rules instead of handler+params."""
    structure = _read_file_structure(file_path)
    sheet_names = list(structure.keys())

    # Detect TB pattern: Entities + TB sheets
    entities_sheet = next((s for s in sheet_names if "entit" in s.lower()), None)
    tb_sheet = next((s for s in sheet_names if s.lower() in ("tb", "trial balance", "trial_balance")), None)

    if not (entities_sheet and tb_sheet):
        return {
            "summary": "Could not identify a known transformation pattern.",
            "goal": "unknown",
            "rules": [],
            "entities_found": [],
            "sheet_names": sheet_names,
            "confidence": 0.0,
        }

    tb_data = structure[tb_sheet]
    ent_data = structure[entities_sheet]

    # Find TB header row (row with "account" keyword)
    tb_header_row = 1
    account_col_name = "Light account number"
    first_entity_col = 3
    for row in tb_data["rows"]:
        vals = [str(v).lower() if v else "" for v in row["values"]]
        if any("account" in v for v in vals):
            tb_header_row = row["row_index"]
            for i, v in enumerate(vals):
                if "account" in v:
                    account_col_name = row["values"][i]
                    break
            # Find first entity col (after metadata columns)
            non_meta = [i for i, v in enumerate(vals) if v and "account" not in v and "name" not in v and "nan" not in v and v.strip() != ""]
            if non_meta:
                first_entity_col = non_meta[0]
            break

    # Collect entity names
    header_row_data = next((r for r in tb_data["rows"] if r["row_index"] == tb_header_row), None)
    entities_found = []
    if header_row_data:
        for v in header_row_data["values"][first_entity_col:]:
            if v and str(v).strip() not in ("", "None", "Total", "nan"):
                entities_found.append(str(v).strip())

    # Detect entities sheet layout
    ent_name_col = 2
    ent_currency_col = 4
    ent_header_row = 1
    for row in ent_data["rows"]:
        vals = [str(v).lower() if v else "" for v in row["values"]]
        if any("currency" in v for v in vals) or any("display" in v or "name" in v for v in vals):
            ent_header_row = row["row_index"]
            for i, v in enumerate(vals):
                if "display" in v or ("name" in v and "legal" not in v):
                    ent_name_col = i
                if "currency" in v:
                    ent_currency_col = i
            break

    # Detect date
    date = "2025-12-31"
    for row in tb_data["rows"]:
        for val in row["values"]:
            if val and "december" in str(val).lower():
                year_match = re.search(r"20\d\d", str(val))
                if year_match:
                    date = f"{year_match.group()}-12-31"

    # Build the rules
    target_cols = TARGET_SCHEMAS["light_journal_entry"]["columns"]

    rules = [
        {
            "id": _rid(), "type": "source_mapping",
            "label": f"Read '{tb_sheet}' sheet",
            "description": f"Load data from the {tb_sheet} sheet, using row {tb_header_row} as headers",
            "enabled": True, "ai_suggested": True,
            "config": {"sheet": tb_sheet, "header_row": tb_header_row, "account_col": 0},
        },
        {
            "id": _rid(), "type": "unpivot_entities",
            "label": f"Expand {len(entities_found)} entity columns into rows",
            "description": "Pivot each entity column into a separate row per account, creating Company/account_code/value columns",
            "enabled": True, "ai_suggested": True,
            "config": {
                "account_col_name": account_col_name,
                "first_entity_col_index": first_entity_col,
            },
        },
        {
            "id": _rid(), "type": "currency_lookup",
            "label": f"Look up currency from '{entities_sheet}'",
            "description": "Match each entity name to its currency code using fuzzy name matching",
            "enabled": True, "ai_suggested": True,
            "config": {
                "lookup_sheet": entities_sheet,
                "entity_column": "Company",
                "target_column": "currency (required)",
                "default_currency": "GBP",
                "lookup_name_col": ent_name_col,
                "lookup_currency_col": ent_currency_col,
                "lookup_header_row": ent_header_row,
            },
        },
        {
            "id": _rid(), "type": "debit_credit_split",
            "label": "Split into debit/credit",
            "description": "Positive balances become debits, negative balances become credits",
            "enabled": True, "ai_suggested": True,
            "config": {
                "source_column": "value",
                "debit_column": "debit (required)",
                "credit_column": "credit (required)",
                "drop_source": True,
            },
        },
        {
            "id": _rid(), "type": "filter_rows",
            "label": "Remove zero-balance lines",
            "description": "Remove rows where both debit and credit are empty (zero balance accounts)",
            "enabled": False, "ai_suggested": True,
            "config": {
                "conditions": [
                    {"column": "debit (required)", "operator": "is_null"},
                    {"column": "credit (required)", "operator": "is_null"},
                ],
                "logic": "and",
                "action": "remove",
            },
        },
        {
            "id": _rid(), "type": "set_constant",
            "label": "Set posting date & description",
            "description": f"Set date to {date}, entry description, tax amount, and other constant fields",
            "enabled": True, "ai_suggested": True,
            "config": {"assignments": {
                "date (required)": date,
                "entry description (required)": "Data migration : Opening TB",
                "tax amount": 0,
                "tax code": None,
                "line description (required)": None,
                "business partner name (required)": None,
                "business partner id": None,
                "local currency rate": None,
                "group currency rate": None,
                "accounting release template name": None,
                "accounting release start date": None,
                "accounting release end date": None,
            }},
        },
        {
            "id": _rid(), "type": "generate_id",
            "label": "Generate entry IDs",
            "description": f"Create entry IDs using pattern: Opening-TB-{date[:4]}-{{Company}}",
            "enabled": True, "ai_suggested": True,
            "config": {
                "target_column": "entry id (required)",
                "pattern": f"Opening-TB-{date[:4]}-{{Company}}",
            },
        },
        {
            "id": _rid(), "type": "map_columns",
            "label": "Map to Light journal format",
            "description": "Rename and reorder columns to match the Light.inc 18-column journal entry schema",
            "enabled": True, "ai_suggested": True,
            "config": {
                "rename": {"account_code": "account code (required)"},
                "column_order": target_cols,
                "drop_unmapped": True,
            },
        },
    ]

    return {
        "summary": f"Multi-entity Trial Balance with {len(entities_found)} companies. "
                   f"Will pivot entity columns into rows, look up currencies, split debit/credit, "
                   f"and format as Light journal entries.",
        "goal": "journal_entry",
        "target_schema": "light_journal_entry",
        "rules": rules,
        "entities_found": entities_found,
        "sheet_names": sheet_names,
        "confidence": 0.92,
    }
