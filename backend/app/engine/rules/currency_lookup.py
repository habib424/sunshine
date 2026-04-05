"""
Rule: currency_lookup
Joins a currency code to each row by matching an entity/company name
against a lookup sheet. Supports fuzzy name matching.
"""

import pandas as pd
from app.engine.rules import register_rule


def _int(val, default):
    return int(val) if val is not None else default


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


def _build_currency_map(lookup_df: pd.DataFrame, header_row: int, name_col: int, currency_col: int) -> dict[str, str]:
    data = lookup_df.iloc[header_row:]
    currency_map = {}
    for _, row in data.iterrows():
        name = str(row.iloc[name_col]).strip() if pd.notna(row.iloc[name_col]) else ""
        currency = str(row.iloc[currency_col]).strip() if pd.notna(row.iloc[currency_col]) else ""
        if name and name != "nan" and currency and currency != "nan":
            currency_map[name] = currency
    return currency_map


def _resolve(entity_name: str, currency_map: dict[str, str], default: str) -> str:
    if entity_name in currency_map:
        return currency_map[entity_name]
    norm = _normalize(entity_name)
    for k, v in currency_map.items():
        if _normalize(k) == norm:
            return v
    # Word overlap fallback
    words = set(entity_name.lower().split())
    best, best_score = default, 0
    for k, v in currency_map.items():
        score = len(words & set(k.lower().split()))
        if score > best_score:
            best_score, best = score, v
    return best


@register_rule("currency_lookup")
class CurrencyLookupRule:
    def execute(self, df: pd.DataFrame, sheets: dict[str, pd.DataFrame], config: dict) -> pd.DataFrame:
        lookup_sheet = config.get("lookup_sheet", "Entities")
        entity_col = config.get("entity_column", "Company")
        target_col = config.get("target_column", "currency")
        default_currency = config.get("default_currency", "GBP")
        name_col = _int(config.get("lookup_name_col"), 2)
        currency_col = _int(config.get("lookup_currency_col"), 4)
        header_row = _int(config.get("lookup_header_row"), 1)

        if lookup_sheet not in sheets:
            # No lookup sheet → fill with default
            df[target_col] = default_currency
            return df

        currency_map = _build_currency_map(sheets[lookup_sheet], header_row, name_col, currency_col)

        if entity_col in df.columns:
            df[target_col] = df[entity_col].apply(lambda x: _resolve(str(x), currency_map, default_currency))
        else:
            df[target_col] = default_currency

        return df

    def schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "lookup_sheet": {"type": "string", "description": "Sheet containing entity-to-currency mapping"},
                "entity_column": {"type": "string", "description": "Column in the data that contains entity names to match"},
                "target_column": {"type": "string", "description": "Column name to write the currency code into"},
                "default_currency": {"type": "string", "description": "Fallback currency if no match found"},
                "lookup_name_col": {"type": "integer", "description": "0-indexed column in lookup sheet for entity names"},
                "lookup_currency_col": {"type": "integer", "description": "0-indexed column in lookup sheet for currency codes"},
                "lookup_header_row": {"type": "integer", "description": "0-indexed header row in lookup sheet"},
            },
        }

    def describe(self, config: dict) -> str:
        sheet = config.get("lookup_sheet", "Entities")
        return f"Look up currency from '{sheet}' sheet by matching entity names"
