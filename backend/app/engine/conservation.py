"""
Generic conservation engine.

Every transformation must preserve declared properties of the source data.
This module provides a single mechanism that works for any file type:

    1. snapshot(df, declarations) → captures the declared property values
       from the source DataFrame at ingest time.
    2. compare(snapshot, output_df, declarations) → re-computes properties
       on the output and returns issues for any that differ beyond tolerance.

The declarations live in the intent spec (not here), keeping this module
file-type-agnostic. Adding a new file type means adding conservation
declarations to that type's intent — no code changes here.

Supported conservation types:
    sum     — sum of a numeric column must be preserved
    count   — row count (or unique value count) must be preserved
    set     — the set of distinct values in a column must be preserved
    hash    — a hash of all cell values must be preserved (strictest)

Issue codes emitted:
    CONSERVATION-SUM-MISMATCH
    CONSERVATION-COUNT-MISMATCH
    CONSERVATION-SET-MISMATCH
    CONSERVATION-HASH-MISMATCH
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

import pandas as pd


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------

@dataclass
class ConservationSnapshot:
    """Captured property values from the source DataFrame."""
    values: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Serialisable representation for logging / storage."""
        result = {}
        for key, val in self.values.items():
            if isinstance(val, set):
                result[key] = {"type": "set", "count": len(val), "sample": sorted(list(val))[:10]}
            elif isinstance(val, float):
                result[key] = {"type": "number", "value": round(val, 6)}
            else:
                result[key] = {"type": type(val).__name__, "value": val}
        return result


def _key(decl: dict) -> str:
    """Stable dict key for a conservation declaration."""
    return f"{decl['type']}:{decl['field']}"


def _compute_property(df: pd.DataFrame, decl: dict) -> Any:
    """Compute a single conservation property from a DataFrame."""
    prop_type = decl["type"]
    col = decl["field"]

    if prop_type == "sum":
        if col not in df.columns:
            return 0.0
        return float(pd.to_numeric(df[col], errors="coerce").fillna(0).sum())

    elif prop_type == "count":
        if col == "rows":
            return len(df)
        if col not in df.columns:
            return 0
        return int(df[col].nunique())

    elif prop_type == "set":
        if col not in df.columns:
            return set()
        return set(str(v) for v in df[col].dropna().unique())

    elif prop_type == "hash":
        if col == "all_values":
            blob = df.to_csv(index=False).encode("utf-8")
        elif col in df.columns:
            blob = df[col].astype(str).str.cat(sep="\n").encode("utf-8")
        else:
            blob = b""
        return hashlib.sha256(blob).hexdigest()[:32]

    else:
        raise ValueError(f"Unknown conservation type: {prop_type}")


def snapshot(df: pd.DataFrame, declarations: list[dict]) -> ConservationSnapshot:
    """
    Capture conservation property values from a source DataFrame.

    Call this at ingest time, before any transformation runs.
    The returned snapshot is passed to `compare()` after the transform.
    """
    snap = ConservationSnapshot()
    for decl in declarations:
        snap.values[_key(decl)] = _compute_property(df, decl)
    return snap


# ---------------------------------------------------------------------------
# Compare
# ---------------------------------------------------------------------------

def _issue(
    issue_code: str,
    severity: str,
    message: str,
    details: dict,
) -> dict:
    return {
        "issue_code": issue_code,
        "severity": severity,
        "scope": "file",
        "entry_id": None,
        "row_number": None,
        "column": details.get("field"),
        "message": message,
        "details": details,
    }


def compare(
    snap: ConservationSnapshot,
    output_df: pd.DataFrame,
    declarations: list[dict],
) -> list[dict]:
    """
    Re-compute conservation properties on the output and compare against
    the source snapshot. Returns a list of issues for any violations.

    This is called after the transform, before export. If any issues are
    returned with severity "error", the export must be blocked.
    """
    issues: list[dict] = []

    for decl in declarations:
        key = _key(decl)
        tolerance = decl.get("tolerance", 0.01)
        source_val = snap.values.get(key)
        output_val = _compute_property(output_df, decl)

        prop_type = decl["type"]
        col = decl["field"]

        if prop_type == "sum":
            diff = output_val - source_val
            if abs(diff) > tolerance:
                issues.append(_issue(
                    "CONSERVATION-SUM-MISMATCH",
                    "error",
                    (
                        f"Column '{col}' total changed: "
                        f"source={source_val:,.4f}, "
                        f"output={output_val:,.4f}, "
                        f"difference={diff:+,.4f} "
                        f"(tolerance {tolerance})"
                    ),
                    {
                        "field": col,
                        "source_value": source_val,
                        "output_value": output_val,
                        "difference": diff,
                        "tolerance": tolerance,
                    },
                ))

        elif prop_type == "count":
            if source_val != output_val:
                diff = output_val - source_val
                issues.append(_issue(
                    "CONSERVATION-COUNT-MISMATCH",
                    "error",
                    (
                        f"{'Row count' if col == 'rows' else f'Distinct count of {col}'} "
                        f"changed: source={source_val}, output={output_val} "
                        f"(diff {diff:+d})"
                    ),
                    {
                        "field": col,
                        "source_value": source_val,
                        "output_value": output_val,
                        "difference": diff,
                    },
                ))

        elif prop_type == "set":
            lost = source_val - output_val
            gained = output_val - source_val
            if lost or gained:
                parts = []
                if lost:
                    sample = sorted(list(lost))[:5]
                    parts.append(
                        f"{len(lost)} value(s) lost (sample: {sample})"
                    )
                if gained:
                    sample = sorted(list(gained))[:5]
                    parts.append(
                        f"{len(gained)} value(s) gained (sample: {sample})"
                    )
                issues.append(_issue(
                    "CONSERVATION-SET-MISMATCH",
                    "error",
                    f"Set of '{col}' values changed: {'; '.join(parts)}",
                    {
                        "field": col,
                        "lost_count": len(lost),
                        "gained_count": len(gained),
                        "lost_sample": sorted(list(lost))[:10],
                        "gained_sample": sorted(list(gained))[:10],
                    },
                ))

        elif prop_type == "hash":
            if source_val != output_val:
                issues.append(_issue(
                    "CONSERVATION-HASH-MISMATCH",
                    "error",
                    f"Content hash of '{col}' changed from {source_val} to {output_val}",
                    {
                        "field": col,
                        "source_hash": source_val,
                        "output_hash": output_val,
                    },
                ))

    return issues
