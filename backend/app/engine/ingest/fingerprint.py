"""
Source fingerprint.

A fingerprint is a stable identifier for a *kind* of file — a shape, not
a specific upload. Two SAP GL exports from January and February should
produce the same fingerprint; a SAP export and an Oracle export should
produce different ones.

Fingerprints are computed from header text, not from arbitrary top-of-file
rows. That matters in practice: most source-system exports prepend a
period banner ("Period: 2026-01") above the real headers, and hashing
those banners would break cache hits across reporting periods. The hash
therefore takes a *layout hint* that tells it which row on which sheet is
the real header — which is exactly what the layout detector produces.

The fingerprint is the cache key for:
    - confirmed layouts in layout_store
    - learned resolutions in the resolution store (next slice)
"""

from __future__ import annotations

import hashlib
import re

import pandas as pd


_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _normalise(text) -> str:
    """Lowercase, strip, collapse non-alphanumeric characters to a single dash."""
    if text is None:
        return ""
    if isinstance(text, float) and pd.isna(text):
        return ""
    lowered = str(text).strip().lower()
    return _NON_ALNUM.sub("-", lowered).strip("-")


def _hash(parts: list[str]) -> str:
    blob = "\n".join(parts).encode("utf-8")
    digest = hashlib.sha256(blob).hexdigest()[:24]
    return f"v1:{digest}"


def fingerprint_from_layout(sheets: dict[str, pd.DataFrame], layout: dict) -> str:
    """
    Compute a fingerprint from a detected/confirmed layout.

    Only the detected sheet name and its header row cells feed the hash.
    Everything else — banner rows above the header, data rows below, cell
    values in other sheets — is ignored. That keeps the hash stable across
    months of the same report while still distinguishing different source
    systems or report types (different headers, different sheet name).

    Falls back to a skeleton hash if the layout is incomplete; that path
    exists so the orchestrator can still cache "unknown layouts" if it
    ever needs to, but in the normal flow the layout is always provided.
    """
    sheet_name = layout.get("sheet") if layout else None
    header_row = layout.get("header_row") if layout else None

    if sheet_name is None or header_row is None or sheet_name not in sheets:
        return _skeleton_fingerprint(sheets)

    df = sheets[sheet_name]
    if header_row >= len(df):
        return _skeleton_fingerprint(sheets)

    header_cells = df.iloc[header_row].tolist()
    normalised = [_normalise(c) for c in header_cells]
    # Trim trailing empty header slots so "Foo | Bar |    " and "Foo | Bar" hash identically.
    while normalised and normalised[-1] == "":
        normalised.pop()

    parts = [
        f"sheet:{_normalise(sheet_name)}",
        "headers:" + "|".join(normalised),
    ]
    return _hash(parts)


def _skeleton_fingerprint(sheets: dict[str, pd.DataFrame]) -> str:
    """
    Last-resort fingerprint based on sheet names and the first non-empty
    row of each sheet. Used only when no layout has been detected yet.
    Less stable than fingerprint_from_layout but better than nothing.
    """
    parts: list[str] = []
    for sheet_name in sorted(sheets.keys()):
        parts.append(f"sheet:{_normalise(sheet_name)}")
        df = sheets[sheet_name]
        first_non_empty = None
        for i in range(min(20, len(df))):
            row = [_normalise(c) for c in df.iloc[i].tolist()]
            if any(cell for cell in row):
                first_non_empty = row
                break
        if first_non_empty is not None:
            while first_non_empty and first_non_empty[-1] == "":
                first_non_empty.pop()
            parts.append("first:" + "|".join(first_non_empty))
    return _hash(parts)
