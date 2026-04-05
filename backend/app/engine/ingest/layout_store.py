"""
Layout store: persisted map of source fingerprint -> confirmed layout.

This is the "memory" half of the deterministic ingest layer. The first
time a new kind of file is uploaded, the detector may propose a layout
with imperfect confidence and the user confirms or edits it. That
confirmation is written here, keyed by (intent, fingerprint). Subsequent
uploads of the same kind of file reuse the confirmed layout without any
AI involvement — which is what makes the pipeline deterministic across
repeat runs.

Format: a single JSON file at backend/data/layouts.json. Plain JSON is
plenty for this scale (tens of layouts, not millions) and makes the
learned state trivially inspectable and diff-able.

File shape:
    {
        "version": 1,
        "entries": {
            "<intent>::<fingerprint>": {
                "intent": str,
                "fingerprint": str,
                "layout": { ... same shape as detect_layout returns ... },
                "confirmed_by": str,
                "confirmed_at": str,     # ISO 8601
                "notes": str,
            },
            ...
        }
    }
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock


_DEFAULT_STORE_PATH = Path(__file__).resolve().parents[3] / "data" / "layouts.json"
_LOCK = Lock()


def _store_path() -> Path:
    path = _DEFAULT_STORE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _load() -> dict:
    path = _store_path()
    if not path.exists():
        return {"version": 1, "entries": {}}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        # Corrupt or unreadable: don't crash the pipeline, start fresh.
        # The old file is left in place for the operator to inspect.
        return {"version": 1, "entries": {}}
    if "entries" not in data:
        data["entries"] = {}
    return data


def _save(data: dict) -> None:
    path = _store_path()
    tmp = path.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    tmp.replace(path)


def _key(intent: str, fingerprint: str) -> str:
    return f"{intent}::{fingerprint}"


def get_layout(intent: str, fingerprint: str) -> dict | None:
    """Return a confirmed layout entry, or None if none is stored."""
    with _LOCK:
        data = _load()
        return data["entries"].get(_key(intent, fingerprint))


def save_layout(
    intent: str,
    fingerprint: str,
    layout: dict,
    *,
    confirmed_by: str = "user",
    notes: str = "",
) -> dict:
    """Persist a confirmed layout. Returns the stored entry."""
    entry = {
        "intent": intent,
        "fingerprint": fingerprint,
        "layout": layout,
        "confirmed_by": confirmed_by,
        "confirmed_at": datetime.now(timezone.utc).isoformat(),
        "notes": notes,
    }
    with _LOCK:
        data = _load()
        data["entries"][_key(intent, fingerprint)] = entry
        _save(data)
    return entry


def delete_layout(intent: str, fingerprint: str) -> bool:
    """Remove a stored layout. Returns True if something was removed."""
    with _LOCK:
        data = _load()
        removed = data["entries"].pop(_key(intent, fingerprint), None)
        if removed is not None:
            _save(data)
            return True
        return False


def list_layouts() -> list[dict]:
    """Return every stored entry, for inspection or UI display."""
    with _LOCK:
        data = _load()
        return list(data["entries"].values())
