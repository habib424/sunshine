"""
YAML rule loader.

Loads validation rule definitions from YAML and produces a RuleSet
that the validator can execute. Files are loaded once and cached.
The API calls reload_rules() after writes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class RuleSet:
    """Parsed, validated rule set from a YAML file."""
    contract: str
    version: int
    grain: str
    canonical_columns: dict[str, str]
    rules: list[dict]
    tolerances: dict[str, float]
    source_path: Path

    @property
    def structural_rules(self) -> list[dict]:
        return [r for r in self.rules if r.get("phase") == "structural" and r.get("enabled", True)]

    @property
    def line_rules(self) -> list[dict]:
        return [r for r in self.rules if r.get("phase") == "line" and r.get("enabled", True)]

    @property
    def group_rules(self) -> list[dict]:
        return [r for r in self.rules if r.get("phase") == "group" and r.get("enabled", True)]

    @property
    def enabled_rules(self) -> list[dict]:
        return [r for r in self.rules if r.get("enabled", True)]

    def get_rule(self, rule_id: str) -> dict | None:
        return next((r for r in self.rules if r["id"] == rule_id), None)

    def to_dict(self) -> dict:
        """Serializable representation for API responses."""
        return {
            "contract": self.contract,
            "version": self.version,
            "grain": self.grain,
            "canonical_columns": self.canonical_columns,
            "rules": self.rules,
            "tolerances": self.tolerances,
        }


_cache: dict[str, RuleSet] = {}
_contracts_dir = Path(__file__).parent


def load_rules(contract_name: str) -> RuleSet:
    """Load and cache rules for a contract from its YAML file."""
    if contract_name in _cache:
        return _cache[contract_name]

    yaml_path = _contracts_dir / f"{contract_name}_rules.yaml"
    if not yaml_path.exists():
        raise FileNotFoundError(
            f"No rules file for contract '{contract_name}' at {yaml_path}"
        )

    with open(yaml_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    # Propagate grain to each rule so check executors can access it
    grain = raw.get("grain", "entry_id")
    for rule in raw.get("rules", []):
        rule.setdefault("grain", grain)

    ruleset = RuleSet(
        contract=raw["contract"],
        version=raw["version"],
        grain=grain,
        canonical_columns=raw.get("canonical_columns", {}),
        rules=raw.get("rules", []),
        tolerances=raw.get("tolerances", {}),
        source_path=yaml_path,
    )

    _cache[contract_name] = ruleset
    return ruleset


def reload_rules(contract_name: str) -> RuleSet:
    """Force-reload rules from disk (used after API writes)."""
    _cache.pop(contract_name, None)
    return load_rules(contract_name)


def save_rules(ruleset: RuleSet) -> None:
    """Write a RuleSet back to its YAML file."""
    data = {
        "contract": ruleset.contract,
        "version": ruleset.version,
        "grain": ruleset.grain,
        "canonical_columns": ruleset.canonical_columns,
        "rules": ruleset.rules,
        "tolerances": ruleset.tolerances,
    }
    # Strip the grain key we injected into individual rules before saving
    for rule in data["rules"]:
        if rule.get("grain") == data["grain"]:
            rule.pop("grain", None)

    tmp = ruleset.source_path.with_suffix(".yaml.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
    tmp.replace(ruleset.source_path)


def list_available_contracts() -> list[str]:
    """Return names of contracts that have YAML rule files."""
    return [
        p.stem.replace("_rules", "")
        for p in _contracts_dir.glob("*_rules.yaml")
    ]
