"""
Intents describe what the user wants to do with an uploaded file.

Intent is the first-class declared input to the pipeline. Everything
downstream — which contract to apply, what "done" means, what the output
looks like — is derived from the intent, never guessed.

Each intent binds to:
    - a contract (the rules the file must satisfy, from app.engine.contracts)
    - an action ("convert", "validate", "reconcile", ...) that decides the
      shape of the output
    - an output_schema (only meaningful for actions that produce a new file)

The registry is intentionally small and closed. New intents are added here
deliberately, not inferred at runtime.
"""

from app.engine.intents.journal_entry_intents import JE_INTENTS

INTENTS: dict[str, dict] = {
    **JE_INTENTS,
}


def get_intent(name: str) -> dict:
    if name not in INTENTS:
        raise KeyError(
            f"Unknown intent '{name}'. Available: {list(INTENTS.keys())}"
        )
    return INTENTS[name]


def list_intents() -> list[dict]:
    """Return intent metadata suitable for a UI dropdown or classifier."""
    return [
        {
            "name": name,
            "label": spec["label"],
            "description": spec["description"],
            "contract": spec["contract"],
            "action": spec["action"],
        }
        for name, spec in INTENTS.items()
        if not spec.get("hidden", False)
    ]
