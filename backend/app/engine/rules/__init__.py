"""
Composable rule engine for Sunshine transformations.

Each rule is a class registered via @register_rule("type_name").
Rules are executed in order as a pipeline: each receives a DataFrame
(and access to all original sheets) and returns a transformed DataFrame.
"""

from typing import Any, Protocol

import pandas as pd


class RuleExecutor(Protocol):
    """Protocol that all rule executors must implement."""

    def execute(self, df: pd.DataFrame, sheets: dict[str, pd.DataFrame], config: dict) -> pd.DataFrame:
        """Transform the DataFrame according to config. `sheets` provides access to all original sheets."""
        ...

    def schema(self) -> dict:
        """Return JSON Schema for this rule's config (drives frontend form rendering)."""
        ...

    def describe(self, config: dict) -> str:
        """Return a human-readable description of what this rule does with the given config."""
        ...


_registry: dict[str, type] = {}


def register_rule(rule_type: str):
    """Decorator to register a rule executor class."""
    def decorator(cls):
        _registry[rule_type] = cls
        return cls
    return decorator


def get_rule_executor(rule_type: str) -> RuleExecutor:
    if rule_type not in _registry:
        raise KeyError(f"No rule registered: '{rule_type}'. Available: {list(_registry.keys())}")
    return _registry[rule_type]()


def list_rule_types() -> dict[str, dict]:
    """Return all registered rule types with their schemas and descriptions."""
    result = {}
    for name, cls in _registry.items():
        instance = cls()
        result[name] = {
            "type": name,
            "schema": instance.schema(),
            "description": instance.describe({}),
        }
    return result
