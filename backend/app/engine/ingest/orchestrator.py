"""
Ingest orchestrator.

Single entry point for turning an uploaded file + a declared intent into
a canonicalised DataFrame ready for the validator. Ties together:

    1. Fingerprint the file (stable shape hash).
    2. Look up a confirmed layout for (intent, fingerprint) in the store.
       If present, apply it directly — fully deterministic.
    3. Otherwise run the deterministic layout detector and return its
       proposal plus a confidence score.
    4. The caller (UI layer) decides whether to auto-accept (high
       confidence), ask the user to confirm (medium), or escalate to an
       AI-assisted proposal (low). This module never calls the AI itself.

Return shape is explicit about the outcome so the caller can branch:

    status = "applied_cached"    confidence >= threshold, used store entry
           | "applied_detected"  no cache, detector confidence high enough to proceed
           | "needs_confirmation" detector produced something, but user must confirm
           | "needs_help"         detector could not produce anything usable
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pandas as pd

from app.engine.contracts import get_contract
from app.engine.ingest import fingerprint as fp_mod
from app.engine.ingest import layout as layout_mod
from app.engine.ingest import layout_store
from app.engine.intents import get_intent


# Confidence thresholds. Kept here rather than per-call so the whole
# product has one consistent answer to "how sure is sure enough?".
AUTO_APPLY_THRESHOLD = 0.85
CONFIRM_THRESHOLD = 0.50


IngestStatus = Literal[
    "applied_cached",
    "applied_detected",
    "needs_confirmation",
    "needs_help",
]


@dataclass
class IngestResult:
    status: IngestStatus
    intent: str
    fingerprint: str
    layout: dict
    dataframe: pd.DataFrame | None
    confidence: float
    unresolved: list[str]
    source: str  # "cache" | "detector" | "none"

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "intent": self.intent,
            "fingerprint": self.fingerprint,
            "layout": self.layout,
            "confidence": self.confidence,
            "unresolved": self.unresolved,
            "source": self.source,
            "rows": 0 if self.dataframe is None else int(len(self.dataframe)),
            "canonical_columns": [] if self.dataframe is None else list(self.dataframe.columns),
        }


def ingest(file_path: Path, intent: str) -> IngestResult:
    """
    Run the deterministic ingest pipeline for a file under a declared intent.

    Never calls an AI. If the detector can't produce a usable layout,
    the caller gets a `needs_help` result and can decide whether to invoke
    the AI assistant to propose one.
    """
    intent_spec = get_intent(intent)
    # Validate contract exists early so a typo'd intent fails fast.
    get_contract(intent_spec["contract"])

    sheets = pd.read_excel(file_path, sheet_name=None, header=None, engine="openpyxl")

    # Detection has to run before we can fingerprint, because the fingerprint
    # is derived from the detected header row — that's what keeps the hash
    # stable across monthly reruns whose banner rows contain the period.
    layout = layout_mod.detect_layout_from_sheets(sheets)
    fingerprint = fp_mod.fingerprint_from_layout(sheets, layout)

    # 1. Cache hit path. The stored layout takes precedence over what the
    # detector just produced: the user confirmed the cached version once,
    # and that confirmation is the source of truth.
    cached = layout_store.get_layout(intent, fingerprint)
    if cached is not None:
        cached_layout = cached["layout"]
        df = layout_mod.apply_layout(file_path, cached_layout)
        return IngestResult(
            status="applied_cached",
            intent=intent,
            fingerprint=fingerprint,
            layout=cached_layout,
            dataframe=df,
            confidence=1.0,  # a confirmed layout is, by definition, certain
            unresolved=[],
            source="cache",
        )

    # 2. Detection path — use what we already computed.
    confidence = float(layout.get("confidence", 0.0))

    if confidence >= AUTO_APPLY_THRESHOLD and not layout.get("missing_required"):
        df = layout_mod.apply_layout(file_path, layout)
        return IngestResult(
            status="applied_detected",
            intent=intent,
            fingerprint=fingerprint,
            layout=layout,
            dataframe=df,
            confidence=confidence,
            unresolved=layout.get("unresolved", []),
            source="detector",
        )

    if confidence >= CONFIRM_THRESHOLD and layout.get("sheet") is not None:
        return IngestResult(
            status="needs_confirmation",
            intent=intent,
            fingerprint=fingerprint,
            layout=layout,
            dataframe=None,
            confidence=confidence,
            unresolved=layout.get("unresolved", []),
            source="detector",
        )

    return IngestResult(
        status="needs_help",
        intent=intent,
        fingerprint=fingerprint,
        layout=layout,
        dataframe=None,
        confidence=confidence,
        unresolved=layout.get("unresolved", [])
        or ["Detector could not identify a header row with sufficient confidence"],
        source="none" if layout.get("sheet") is None else "detector",
    )


def confirm_layout(
    file_path: Path,
    intent: str,
    layout: dict,
    *,
    confirmed_by: str = "user",
    notes: str = "",
) -> IngestResult:
    """
    Persist a (possibly edited) layout as the confirmed one for the file's
    fingerprint + intent, then apply it and return a ready-to-validate result.

    This is the call the UI makes after the user accepts a proposed layout
    from `needs_confirmation` or `needs_help`.
    """
    intent_spec = get_intent(intent)
    get_contract(intent_spec["contract"])

    sheets = pd.read_excel(file_path, sheet_name=None, header=None, engine="openpyxl")
    fingerprint = fp_mod.fingerprint_from_layout(sheets, layout)

    layout_store.save_layout(
        intent,
        fingerprint,
        layout,
        confirmed_by=confirmed_by,
        notes=notes,
    )
    df = layout_mod.apply_layout(file_path, layout)
    return IngestResult(
        status="applied_cached",
        intent=intent,
        fingerprint=fingerprint,
        layout=layout,
        dataframe=df,
        confidence=1.0,
        unresolved=[],
        source="cache",
    )
