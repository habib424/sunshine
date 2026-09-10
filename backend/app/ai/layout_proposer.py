"""AI fallback for source-layout detection.

The deterministic analyzers recognize common header wordings with keyword
rules. Real-world exports vary endlessly ("Balance as at Migration date",
"Itamised breakdown", ...), so when the rules find nothing the engine asks
the AI to read the raw rows and propose the layout: which sheet, which
header row, and which header text plays which role.

The proposal is data, not code. Every proposed header is validated against
the actual cells of the proposed header row; anything that doesn't match is
dropped. The deterministic engine then runs exactly as if the keywords had
matched — the AI only names columns, it never computes amounts.

Proposals are cached per (file, mtime, spec) for the process lifetime so
the repeated re-analysis the chat flow performs never re-calls the AI.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pandas as pd

from app.ai.client import get_client

logger = logging.getLogger("sunshine.layout_proposer")

_TOOL_NAME = "propose_source_layout"

# (path, mtime_ns, spec_key) -> proposal dict or None (None = AI answered
# but nothing usable; transient AI errors are NOT cached).
_cache: dict[tuple[str, int, str], dict | None] = {}


def propose_layout(
    file_path: Path,
    sheets: dict[str, pd.DataFrame],
    task: str,
    roles: dict[str, str],
    required_roles: list[str] | None = None,
    preferred_sheet: str | None = None,
    max_rows: int = 12,
    max_cols: int = 20,
) -> dict | None:
    """Ask the AI to locate the source table. Returns a validated proposal:

        {"sheet": str, "header_row": int, "roles": {role: exact header text}}

    or None when nothing usable was proposed. `roles` maps role name ->
    description shown to the model; `required_roles` (default: all `roles`
    keys) must all be present after validation, else None.
    """
    candidates = {
        name: df for name, df in sheets.items()
        if not preferred_sheet or preferred_sheet not in sheets or name == preferred_sheet
    }
    if not candidates:
        return None

    spec_key = json.dumps([task, sorted(roles), sorted(required_roles or []), preferred_sheet or ""])
    try:
        cache_key = (str(file_path), file_path.stat().st_mtime_ns, spec_key)
    except OSError:
        cache_key = None
    if cache_key is not None and cache_key in _cache:
        return _cache[cache_key]

    preview = {
        name: [
            ["" if pd.isna(v) else str(v)[:60] for v in df.iloc[i].tolist()[:max_cols]]
            for i in range(min(max_rows, len(df)))
        ]
        for name, df in candidates.items()
    }

    tool = {
        "name": _TOOL_NAME,
        "description": (
            "Report where the source table lives in the workbook and which "
            "header plays which role. Copy header texts EXACTLY as they "
            "appear in the rows, including spacing and typos."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "found": {
                    "type": "boolean",
                    "description": "false when no sheet contains a usable source table",
                },
                "sheet": {"type": "string", "description": "sheet name containing the source table"},
                "header_row": {
                    "type": "integer",
                    "description": "0-based row index of the header row within the preview rows",
                },
                "roles": {
                    "type": "object",
                    "description": "role name -> the EXACT header cell text for that role",
                    "properties": {
                        role: {"type": "string", "description": desc} for role, desc in roles.items()
                    },
                    "additionalProperties": False,
                },
            },
            "required": ["found"],
        },
    }

    payload = {
        "task": task,
        "sheets_first_rows": preview,
        "notes": (
            "Rows are shown 0-indexed per sheet. Only map a role when a "
            "column for it clearly exists; leave unclear roles out."
        ),
    }

    try:
        response = get_client().messages.create(
            model="claude-sonnet-5",
            max_tokens=1200,
            thinking={"type": "disabled"},
            system=(
                "You locate tabular source data inside messy accounting "
                "spreadsheets. You only report structure; you never invent "
                "headers that are not present in the provided rows."
            ),
            messages=[{"role": "user", "content": json.dumps(payload, default=str)}],
            tools=[tool],
            tool_choice={
                "type": "tool",
                "name": _TOOL_NAME,
                "disable_parallel_tool_use": True,
            },
        )
    except Exception as exc:
        logger.warning("AI layout proposal failed: %s", exc)
        return None  # transient — not cached, a later refresh may retry

    proposal = _validate(response, candidates, roles, required_roles or list(roles))
    if cache_key is not None:
        _cache[cache_key] = proposal
    return proposal


def complete_roles(
    file_path: Path,
    df: pd.DataFrame,
    sheet: str,
    header_row: int,
    known_roles: dict[str, str],
    wanted_roles: dict[str, str],
    task: str,
    max_rows: int = 10,
    max_cols: int = 24,
) -> dict[str, str]:
    """Gap-fill: keyword detection already located the table; ask the AI to
    map the roles the keywords missed onto the remaining headers.

    Returns {role: exact header text} for newly mapped roles only (possibly
    empty). Headers already claimed by `known_roles` are never reassigned.
    """
    if not wanted_roles:
        return {}

    spec_key = json.dumps(["complete", task, sheet, header_row, sorted(wanted_roles), sorted(known_roles.items())])
    try:
        cache_key = (str(file_path), file_path.stat().st_mtime_ns, spec_key)
    except OSError:
        cache_key = None
    if cache_key is not None and cache_key in _cache:
        return dict(_cache[cache_key] or {})

    start = max(0, header_row - 2)
    preview = [
        ["" if pd.isna(v) else str(v)[:60] for v in df.iloc[i].tolist()[:max_cols]]
        for i in range(start, min(start + max_rows, len(df)))
    ]

    tool = {
        "name": _TOOL_NAME,
        "description": (
            "Map the listed roles onto header cells of the given header row. "
            "Copy header texts EXACTLY as they appear, including spacing and "
            "typos. Leave a role out when no column matches it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "roles": {
                    "type": "object",
                    "description": "role name -> the EXACT header cell text for that role",
                    "properties": {
                        role: {"type": "string", "description": desc}
                        for role, desc in wanted_roles.items()
                    },
                    "additionalProperties": False,
                },
            },
            "required": ["roles"],
        },
    }

    payload = {
        "task": task,
        "sheet": sheet,
        "header_row_index_in_preview": header_row - start,
        "rows": preview,
        "already_mapped": known_roles,
        "notes": (
            "Only map the missing roles listed in the tool schema, using "
            "headers from the indicated header row that are not already "
            "mapped. Leave unclear roles out."
        ),
    }

    try:
        response = get_client().messages.create(
            model="claude-sonnet-5",
            max_tokens=800,
            thinking={"type": "disabled"},
            system=(
                "You map spreadsheet column headers to semantic roles for an "
                "accounting migration. You only use headers that are present "
                "in the provided rows; you never invent header text."
            ),
            messages=[{"role": "user", "content": json.dumps(payload, default=str)}],
            tools=[tool],
            tool_choice={
                "type": "tool",
                "name": _TOOL_NAME,
                "disable_parallel_tool_use": True,
            },
        )
    except Exception as exc:
        logger.warning("AI role completion failed: %s", exc)
        return {}

    tool_use = next((b for b in response.content if b.type == "tool_use"), None)
    proposed = (tool_use.input or {}).get("roles", {}) if tool_use is not None else {}

    header_cells = {
        str(v).strip(): str(v)
        for v in df.iloc[header_row].tolist()
        if v is not None and not (isinstance(v, float) and pd.isna(v)) and str(v).strip()
    }
    taken = set(known_roles.values())

    validated: dict[str, str] = {}
    for role, header in proposed.items():
        if role not in wanted_roles or not isinstance(header, str):
            continue
        matched = header_cells.get(header.strip())
        if matched is not None and matched not in taken and matched not in validated.values():
            validated[role] = matched

    if cache_key is not None:
        _cache[cache_key] = validated
    return dict(validated)


def _validate(
    response: Any,
    sheets: dict[str, pd.DataFrame],
    roles: dict[str, str],
    required_roles: list[str],
) -> dict | None:
    tool_use = next((b for b in response.content if b.type == "tool_use"), None)
    if tool_use is None:
        return None
    data = tool_use.input or {}
    if not data.get("found"):
        return None

    sheet = data.get("sheet")
    header_row = data.get("header_row")
    if sheet not in sheets or not isinstance(header_row, int):
        return None
    df = sheets[sheet]
    if not (0 <= header_row < len(df)):
        return None

    header_cells = {
        str(v).strip(): str(v)
        for v in df.iloc[header_row].tolist()
        if v is not None and not (isinstance(v, float) and pd.isna(v)) and str(v).strip()
    }

    validated: dict[str, str] = {}
    for role, header in (data.get("roles") or {}).items():
        if role not in roles or not isinstance(header, str):
            continue
        matched = header_cells.get(header.strip())
        if matched is not None and matched not in validated.values():
            validated[role] = matched

    if any(role not in validated for role in required_roles):
        logger.info(
            "AI layout proposal for sheet %r rejected: missing %s",
            sheet,
            [r for r in required_roles if r not in validated],
        )
        return None

    return {"sheet": sheet, "header_row": header_row, "roles": validated}
