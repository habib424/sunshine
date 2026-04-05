"""Rule catalog: generates descriptions of all rule types for AI context."""

from app.engine.rules import list_rule_types
from app.engine.target_schemas import TARGET_SCHEMAS


def get_catalog_for_ai() -> str:
    rules = list_rule_types()
    lines = ["Available rule types:"]
    for name, info in rules.items():
        lines.append(f"  - {name}: {info['description']}")
    lines.append("")
    lines.append("Target schemas:")
    for name, schema in TARGET_SCHEMAS.items():
        lines.append(f"  - {name}: columns = {schema['columns']}")
    return "\n".join(lines)
