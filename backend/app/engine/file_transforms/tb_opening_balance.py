"""
File-level transform: multi-entity Trial Balance → Light journal entry upload format.

Reads an Excel workbook with:
  - An entities tab: Company Code, Display name, Local Currency
  - A TB tab: rows = accounts, columns = entity names + balances
  - Optionally a template tab (ignored — we generate the output directly)

Produces one journal entry row per (entity, account) pair.
"""

from pathlib import Path

import pandas as pd

from app.engine.file_transforms import register_file_transform


def _normalize(name: str) -> str:
    return (
        name.lower()
        .replace(" ltd", " limited")
        .replace(" inc.", "")
        .replace(" inc", "")
        .replace(".", "")
        .replace(",", "")
        .strip()
    )


def _build_currency_map(entities_df: pd.DataFrame, config: dict) -> dict[str, str]:
    """Build {entity display name -> currency} from the entities sheet."""
    def _int(val, default):
        return int(val) if val is not None else default

    name_col = _int(config.get("entities_name_col"), 2)
    curr_col = _int(config.get("entities_currency_col"), 4)
    header_row = _int(config.get("entities_header_row"), 1)

    data = entities_df.iloc[header_row:]
    currency_map = {}
    for _, row in data.iterrows():
        name = str(row.iloc[name_col]).strip() if pd.notna(row.iloc[name_col]) else ""
        currency = str(row.iloc[curr_col]).strip() if pd.notna(row.iloc[curr_col]) else "GBP"
        if name and name != "nan":
            currency_map[name] = currency
    return currency_map


def _resolve_currency(entity_name: str, currency_map: dict[str, str]) -> str:
    if entity_name in currency_map:
        return currency_map[entity_name]
    norm = _normalize(entity_name)
    for k, v in currency_map.items():
        if _normalize(k) == norm:
            return v
    # Word-overlap fallback
    words = set(entity_name.lower().split())
    best, best_score = "GBP", 0
    for k, v in currency_map.items():
        score = len(words & set(k.lower().split()))
        if score > best_score:
            best_score, best = score, v
    return best


@register_file_transform("tb_opening_balance")
def tb_opening_balance(sheets: dict[str, pd.DataFrame], config: dict) -> pd.DataFrame:
    """
    config keys:
      entities_sheet      str   name of the entities tab
      tb_sheet            str   name of the TB tab
      tb_header_row       int   0-indexed row that contains column headers in the TB tab
      tb_first_entity_col int   0-indexed first column that holds entity balances
      tb_last_entity_col  int   0-indexed last entity column (exclusive); None = auto-detect
      tb_account_col      int   0-indexed column for Light account number
      date                str   posting date, e.g. "2025-12-31"
      entry_description   str   text for entry description field
      entry_id_prefix     str   prefix for entry id, e.g. "Opening-TB-2025-"
      entities_name_col   int   0-indexed column in entities sheet for display name
      entities_currency_col int 0-indexed column in entities sheet for currency
      entities_header_row int   0-indexed row index of header in entities sheet
    """
    def _safe(val, default):
        return int(val) if val is not None else default

    entities_sheet = config.get("entities_sheet") or "Entities"
    tb_sheet = config.get("tb_sheet") or "TB"
    tb_header_row = _safe(config.get("tb_header_row"), 1)
    tb_first_entity_col = _safe(config.get("tb_first_entity_col"), 3)
    tb_account_col = _safe(config.get("tb_account_col"), 0)
    date = str(config.get("date") or "2025-12-31")
    entry_description = str(config.get("entry_description") or "Data migration : Opening TB")
    entry_id_prefix = str(config.get("entry_id_prefix") or "Opening-TB-2025-")

    if entities_sheet not in sheets:
        raise ValueError(f"Sheet '{entities_sheet}' not found. Available: {list(sheets.keys())}")
    if tb_sheet not in sheets:
        raise ValueError(f"Sheet '{tb_sheet}' not found. Available: {list(sheets.keys())}")

    currency_map = _build_currency_map(sheets[entities_sheet], config)

    tb = sheets[tb_sheet]
    headers = tb.iloc[tb_header_row]

    # Auto-detect entity columns: from first_entity_col until "Total" or empty
    last_entity_col = int(config["tb_last_entity_col"]) if config.get("tb_last_entity_col") is not None else None
    entity_cols = []
    for i in range(tb_first_entity_col, len(headers)):
        val = headers.iloc[i]
        if pd.isna(val) or str(val).strip() in ("", "Total"):
            if last_entity_col is None:
                break
        if last_entity_col is not None and i >= last_entity_col:
            break
        name = str(val).strip()
        if name and name != "nan":
            entity_cols.append((i, name))

    # Data rows: skip header rows, drop rows with no account code
    data = tb.iloc[tb_header_row + 1:].copy()
    data = data[pd.notna(data.iloc[:, tb_account_col])]

    output_columns = [
        "Company",
        "entry id (required)",
        "date (required)",
        "currency (required)",
        "debit (required)",
        "credit (required)",
        "tax amount",
        "account code (required)",
        "tax code",
        "entry description (required)",
        "line description (required)",
        "business partner name (required)",
        "business partner id",
        "local currency rate",
        "group currency rate",
        "accounting release template name",
        "accounting release start date",
        "accounting release end date",
    ]

    rows = []
    for col_idx, entity_name in entity_cols:
        currency = _resolve_currency(entity_name, currency_map)
        entry_id = f"{entry_id_prefix}{entity_name}"

        for _, row in data.iterrows():
            account_code = row.iloc[tb_account_col]
            try:
                value = float(row.iloc[col_idx]) if pd.notna(row.iloc[col_idx]) else 0.0
            except (ValueError, TypeError):
                value = 0.0

            debit = value if value > 0 else None
            credit = abs(value) if value < 0 else None

            rows.append([
                entity_name,
                entry_id,
                date,
                currency,
                debit,
                credit,
                0,
                int(account_code),
                None,
                entry_description,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
            ])

    return pd.DataFrame(rows, columns=output_columns)
