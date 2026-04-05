"""
Deterministic layout detection.

Given an uploaded workbook, the detector picks:
    - the sheet most likely to contain the journal entry data
    - the header row within that sheet
    - a mapping from source column names to canonical contract columns

Every decision carries a numeric confidence in [0.0, 1.0]. The orchestrator
decides, based on that confidence, whether to proceed silently, ask the
user to confirm, or escalate to an AI-assisted layout proposal.

The detector itself NEVER calls an AI. It is pure, deterministic, and
idempotent. Same file, same output, always.

Returned layout shape:
    {
        "sheet": str,
        "header_row": int,                       # 0-indexed
        "column_roles": dict[str, str],          # source_col -> canonical_col
        "unmapped_columns": list[str],           # source columns with no role
        "missing_required": list[str],           # contract-required roles not found
        "confidence": float,                     # 0.0 - 1.0
        "unresolved": list[str],                 # human-readable reasons confidence < 1.0
    }
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from app.engine.contracts.journal_entry import (
    CANONICAL_COLUMNS,
    JOURNAL_ENTRY_CONTRACT,
)


# Keyword dictionary mapping canonical roles to candidate header fragments.
# Ordered by specificity: longer, more distinctive phrases come first so they
# win ties against shorter generic ones.
_ROLE_KEYWORDS: dict[str, list[str]] = {
    "entry_id": [
        "journal entry id", "journal entry no", "entry id", "entry no",
        "doc number", "document no", "document number", "je number",
        "voucher no", "voucher",
    ],
    "date": [
        "posting date", "document date", "doc date", "entry date",
        "transaction date", "date",
    ],
    "currency": [
        "document currency", "transaction currency",
        "currency code", "currency", "curr", "ccy",
    ],
    "debit": [
        "debit amount", "dr amount", "debit", "dr",
    ],
    "credit": [
        "credit amount", "cr amount", "credit", "cr",
    ],
    "gl_account": [
        "gl account", "general ledger account", "account code",
        "account number", "account no", "ledger account", "account",
    ],
    "business_partner": [
        "business partner name", "business partner", "partner name",
        "counterparty", "customer", "supplier", "vendor", "partner",
    ],
    "entry_description": [
        "header text", "entry description", "je description",
        "document header text", "journal description",
    ],
    "line_description": [
        "line description", "line text", "item text", "line item text",
    ],
}


_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _normalise_header(text) -> str:
    if text is None or (isinstance(text, float) and pd.isna(text)):
        return ""
    return _NON_ALNUM.sub(" ", str(text).strip().lower()).strip()


def _match_role(header_text: str) -> str | None:
    """Match a normalised header to a canonical role, or return None."""
    if not header_text:
        return None
    # Exact normalised match first, then substring match.
    # We search the most specific keyword lists first so, e.g., "document
    # currency" wins over a plain "currency" when both are plausible.
    for role, keywords in _ROLE_KEYWORDS.items():
        for kw in keywords:
            if header_text == kw:
                return role
    for role, keywords in _ROLE_KEYWORDS.items():
        for kw in keywords:
            if kw in header_text:
                return role
    return None


def _score_header_row(df: pd.DataFrame, row_idx: int) -> tuple[float, list[str], dict[str, str]]:
    """
    Return a score for row `row_idx` being the header row of `df`.

    The score blends:
        - fraction of non-empty cells in the row
        - whether the cells look like labels (text, not numbers)
        - how many of them match canonical roles
        - whether the rows below look like data (non-empty, stable shape)
    """
    if row_idx >= len(df):
        return 0.0, [], {}

    row = df.iloc[row_idx].tolist()
    non_empty = [c for c in row if c is not None and not (isinstance(c, float) and pd.isna(c)) and str(c).strip() != ""]
    if not non_empty:
        return 0.0, [], {}

    fill_ratio = len(non_empty) / max(len(row), 1)

    # Labels shouldn't be pure numbers. A row of numbers is data, not headers.
    numeric_count = 0
    for c in non_empty:
        if isinstance(c, (int, float)) and not isinstance(c, bool):
            numeric_count += 1
        else:
            # Try parsing as number; strings that are clearly numeric count.
            try:
                float(str(c).replace(",", ""))
                numeric_count += 1
            except ValueError:
                pass
    label_ratio = 1.0 - (numeric_count / len(non_empty))

    # Role match ratio: how many headers resolve to a canonical role?
    roles: dict[str, str] = {}
    raw_headers: list[str] = []
    for c in row:
        header_text = _normalise_header(c)
        raw_headers.append(str(c) if c is not None and not (isinstance(c, float) and pd.isna(c)) else "")
        if not header_text:
            continue
        role = _match_role(header_text)
        if role and str(c) not in roles:
            # Preserve first occurrence of each source column
            roles[str(c)] = role
    distinct_roles = set(roles.values())
    role_match_ratio = len(distinct_roles) / max(len(JOURNAL_ENTRY_CONTRACT["required_columns"]), 1)
    role_match_ratio = min(role_match_ratio, 1.0)

    # Data-below check: rows after this one should be non-empty more often than not.
    below = df.iloc[row_idx + 1 : row_idx + 6]
    if len(below) == 0:
        data_below_ratio = 0.0
    else:
        non_null_mask = below.notna() & (below.astype(str).apply(lambda s: s.str.strip() != ""))
        data_below_ratio = float(non_null_mask.values.mean())

    # Weighted blend. Role matching dominates because it's the strongest
    # signal that this row is actually a JE header. The others are tiebreakers.
    score = (
        0.55 * role_match_ratio
        + 0.20 * label_ratio
        + 0.15 * fill_ratio
        + 0.10 * data_below_ratio
    )
    return score, raw_headers, roles


def _detect_sheet_layout(sheet_name: str, df: pd.DataFrame, max_rows: int = 15) -> dict | None:
    """Try every plausible header row in a sheet and return the best candidate."""
    best: dict | None = None
    for row_idx in range(min(max_rows, len(df))):
        score, raw_headers, roles = _score_header_row(df, row_idx)
        if best is None or score > best["score"]:
            best = {
                "sheet": sheet_name,
                "header_row": row_idx,
                "score": score,
                "raw_headers": raw_headers,
                "column_roles": roles,
            }
    return best


def detect_layout(file_path: Path) -> dict:
    """
    Run layout detection against a workbook and return the best candidate
    layout plus its confidence score and unresolved issues.
    """
    sheets = pd.read_excel(file_path, sheet_name=None, header=None, engine="openpyxl")
    return detect_layout_from_sheets(sheets)


def detect_layout_from_sheets(sheets: dict[str, pd.DataFrame]) -> dict:
    candidates: list[dict] = []
    for name, df in sheets.items():
        cand = _detect_sheet_layout(name, df)
        if cand:
            candidates.append(cand)

    if not candidates:
        return {
            "sheet": None,
            "header_row": None,
            "column_roles": {},
            "unmapped_columns": [],
            "missing_required": list(JOURNAL_ENTRY_CONTRACT["required_columns"]),
            "confidence": 0.0,
            "unresolved": ["Workbook contains no readable sheets"],
        }

    best = max(candidates, key=lambda c: c["score"])
    raw_headers = best["raw_headers"]
    column_roles = best["column_roles"]

    unmapped = [
        h for h in raw_headers
        if h and h not in column_roles
    ]
    mapped_roles = set(column_roles.values())
    missing_required = [
        r for r in JOURNAL_ENTRY_CONTRACT["required_columns"]
        if r not in mapped_roles
    ]

    unresolved: list[str] = []
    if missing_required:
        unresolved.append(
            f"Could not map required canonical columns: {missing_required}"
        )
    if unmapped:
        unresolved.append(
            f"Source columns with no matched role: {unmapped}"
        )

    # Confidence is primarily the row score, penalised if required roles
    # are missing. A layout that can't satisfy the contract should never
    # report high confidence regardless of how "header-like" the row looks.
    confidence = best["score"]
    if missing_required:
        penalty = len(missing_required) / max(
            len(JOURNAL_ENTRY_CONTRACT["required_columns"]), 1
        )
        confidence = max(0.0, confidence * (1 - penalty))

    return {
        "sheet": best["sheet"],
        "header_row": best["header_row"],
        "column_roles": column_roles,
        "unmapped_columns": unmapped,
        "missing_required": missing_required,
        "confidence": round(confidence, 3),
        "unresolved": unresolved,
    }


def apply_layout(file_path: Path, layout: dict) -> pd.DataFrame:
    """
    Read the file using a (confirmed) layout and return a DataFrame whose
    columns are renamed to the canonical contract names.

    Columns without a role are dropped from the canonicalised view but
    remain available if the caller wants to re-read the raw sheet.
    """
    if not layout.get("sheet") or layout.get("header_row") is None:
        raise ValueError("Layout is incomplete: sheet or header_row missing")

    raw = pd.read_excel(
        file_path,
        sheet_name=layout["sheet"],
        header=layout["header_row"],
        engine="openpyxl",
    )

    renamed = raw.rename(columns=layout["column_roles"])
    keep = [c for c in renamed.columns if c in CANONICAL_COLUMNS]
    # Drop rows that are entirely empty after canonicalisation — those are
    # almost always footer rows like "Total" or blank spacers below the data.
    canonical_df = renamed[keep].copy()
    canonical_df = canonical_df.dropna(how="all").reset_index(drop=True)
    return canonical_df
