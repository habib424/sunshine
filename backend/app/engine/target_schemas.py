"""
Target output schema definitions.
Each schema describes the expected column structure of an output file type.
"""

TARGET_SCHEMAS = {
    "light_journal_entry": {
        "description": "Light.inc journal entry upload format",
        "columns": [
            "Company",
            "entry id (required)",
            "date (required)",
            "currency (required)",
            "debit (required)",
            "credit (required)",
            "tax amount",
            "account code (required)",
            "tax code",
            "entry description (required)",
            "line description (required)",
            "business partner name (required)",
            "business partner id",
            "local currency rate",
            "group currency rate",
            "accounting release template name",
            "accounting release start date",
            "accounting release end date",
        ],
    },
}


def get_target_schema(name: str) -> dict:
    if name not in TARGET_SCHEMAS:
        raise KeyError(f"Unknown target schema: '{name}'. Available: {list(TARGET_SCHEMAS.keys())}")
    return TARGET_SCHEMAS[name]


def list_target_schemas() -> dict:
    return {k: v["description"] for k, v in TARGET_SCHEMAS.items()}
