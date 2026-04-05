"""
Journal-entry-related intents.

Each intent is a small declarative spec. The engine reads it and decides:
    - which contract to validate against
    - what output to produce (if any)
    - what a successful run looks like

Adding a new JE-adjacent intent (trial balance conversion, reconciliation,
etc.) is a matter of adding an entry to JE_INTENTS, not changing pipeline
code.
"""

JE_INTENTS: dict[str, dict] = {
    "convert_to_light_je": {
        "label": "Convert to Light journal entry format",
        "description": (
            "Transform a source-system journal entry export into the Light.inc "
            "journal entry upload schema. Validates against the Journal Entry "
            "contract before and after the transform."
        ),
        "contract": "journal_entry",
        "action": "convert",
        "output_schema": "light_journal_entry",
        "requires_post_validation": True,
    },
    "validate_je": {
        "label": "Validate a journal entry file",
        "description": (
            "Check that a file complies with the Journal Entry contract. "
            "Produces an issue report but does not transform or export data."
        ),
        "contract": "journal_entry",
        "action": "validate",
        "output_schema": None,
        "requires_post_validation": False,
    },
    "reconcile_je_to_gl": {
        "label": "Reconcile journal entries against a GL extract",
        "description": (
            "Compare a journal entry file to a general ledger extract and "
            "produce a matched / unmatched report. Both files must satisfy "
            "the Journal Entry contract."
        ),
        "contract": "journal_entry",
        "action": "reconcile",
        "output_schema": "reconciliation_report",
        "requires_post_validation": False,
    },
}
