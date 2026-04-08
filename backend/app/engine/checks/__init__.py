"""
Validation check type registry.

Each check type is a class registered via @register_check("type_name").
Check types implement the logic for a category of validation (e.g.,
"group_consistency", "value_in_set"). The YAML rules define WHICH checks
to run with WHAT parameters — identical to how transform RuleExecutors
work with their JSON configs.

Protocol:
    execute(df, rule, runtime) -> list[dict]   # run the check, return issues
    schema() -> dict                           # JSON Schema for params (drives UI)
    describe(params) -> str                    # human-readable summary
"""

from __future__ import annotations

from typing import Protocol

import pandas as pd


class CheckExecutor(Protocol):
    def execute(self, df: pd.DataFrame, rule: dict, runtime: dict) -> list[dict]: ...
    def schema(self) -> dict: ...
    def describe(self, params: dict) -> str: ...


_registry: dict[str, type] = {}


def register_check(check_type: str):
    """Decorator to register a check executor class."""
    def decorator(cls):
        _registry[check_type] = cls
        return cls
    return decorator


def get_check_executor(check_type: str) -> CheckExecutor:
    if check_type not in _registry:
        raise KeyError(
            f"No check registered: '{check_type}'. "
            f"Available: {list(_registry.keys())}"
        )
    return _registry[check_type]()


def list_check_types() -> dict[str, dict]:
    """Return all registered check types with their schemas."""
    result = {}
    for name, cls in _registry.items():
        instance = cls()
        result[name] = {
            "type": name,
            "schema": instance.schema(),
            "description": instance.describe({}),
        }
    return result


def _issue(
    rule: dict,
    message: str,
    *,
    entry_id: str | None = None,
    row_number: int | None = None,
    column: str | None = None,
    details: dict | None = None,
) -> dict:
    """Build a standard issue dict from a YAML rule + check-specific data."""
    return {
        "issue_code": rule["issue_code"],
        "severity": rule.get("severity", "error"),
        "scope": rule.get("scope", "line"),
        "entry_id": entry_id,
        "row_number": row_number,
        "column": column,
        "message": message,
        "details": details or {},
    }


def _safe_str(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    return str(value)


# Import check implementations to trigger registration
import app.engine.checks.required_columns  # noqa: F401, E402
import app.engine.checks.non_empty  # noqa: F401, E402
import app.engine.checks.at_least_one  # noqa: F401, E402
import app.engine.checks.parseable_date  # noqa: F401, E402
import app.engine.checks.value_in_set  # noqa: F401, E402
import app.engine.checks.group_consistency  # noqa: F401, E402
import app.engine.checks.group_balance  # noqa: F401, E402
