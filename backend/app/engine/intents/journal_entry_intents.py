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
        # Conservation: properties the transform must preserve from source
        # to output. The engine snapshots these at ingest, re-checks after
        # the transform, and blocks export on any violation.
        # These are NOT JE-specific concepts — the conservation engine is
        # generic. Other file types declare their own conserved properties.
        "conserve": [
            {"type": "sum",   "field": "debit",    "tolerance": 0.01},
            {"type": "sum",   "field": "credit",   "tolerance": 0.01},
            {"type": "count", "field": "rows"},
            {"type": "set",   "field": "entry_id"},
        ],
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
        # Validation-only: no transform, so nothing to conserve.
        "conserve": [],
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
        "conserve": [
            {"type": "count", "field": "rows"},
        ],
    },
    "migrate_deferred_cost_to_light_je": {
        "label": "Migrate deferred costs / prepayments to Light JE",
        "description": (
            "Read a deferred cost or prepayment schedule in whatever source "
            "layout is provided, identify the accounting facts needed, and "
            "produce the Light journal entry upload with release accounting."
        ),
        "contract": "journal_entry",
        "action": "convert",
        "output_schema": "light_journal_entry_v2",
        "requires_post_validation": False,
        "conserve": [
            {"type": "sum", "field": "amount", "tolerance": 0.01},
        ],
    },
    "migrate_deferred_revenue_to_light_je": {
        "label": "Migrate deferred revenue to Light JE",
        "description": (
            "Read a deferred revenue schedule in whatever source layout is "
            "provided, identify the accounting facts needed, and produce the "
            "Light journal entry upload with release accounting."
        ),
        "contract": "journal_entry",
        "action": "convert",
        "output_schema": "light_journal_entry_v2",
        "requires_post_validation": False,
        "conserve": [
            {"type": "sum", "field": "amount", "tolerance": 0.01},
        ],
    },
    "fx_currency_adjustment": {
        "label": "Post FX currency adjustments",
        "description": (
            "Align booked account-currency amounts with real bank balances after "
            "a migration. Posts each difference against the bank clearing account "
            "with the local and group currency FX rates overridden to 0, so only "
            "the transaction-currency amount changes."
        ),
        "contract": "journal_entry",
        "action": "convert",
        "output_schema": "light_fx_adjustment",
        "requires_post_validation": False,
        "conserve": [
            {"type": "sum", "field": "amount", "tolerance": 0.01},
        ],
    },
    "upload_open_ap_to_light_ap": {
        "label": "Upload open AP to Light",
        "description": (
            "Read an open accounts payable export or aging report and produce "
            "the upload that brings open AP into Light: one Light bill per "
            "outstanding vendor invoice with payments netted per vendor, or "
            "opening JE lines reused from a Light Posting reference sheet when "
            "the workbook carries one."
        ),
        "contract": "journal_entry",
        "action": "convert",
        "output_schema": "light_bills_ap_upload",
        "requires_post_validation": False,
        "conserve": [
            {"type": "sum", "field": "amount", "tolerance": 0.01},
        ],
    },
}
