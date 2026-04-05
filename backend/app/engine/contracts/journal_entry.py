"""
Journal Entry contract.

Defines the invariants that any valid journal entry file must satisfy,
regardless of source system. Validators in
app.engine.validators.journal_entry check these invariants and emit issues
keyed by the stable issue codes below.

Issue codes are the stable identity of a kind of problem. Resolutions are
learned and cached against these codes, so renaming a code invalidates the
learned rules store for that code. Treat them as a public API.
"""

# Stable issue codes. Do not rename once in use; add new ones instead.
ISSUE_CODES = {
    "JE-MISSING-FIELD":     "A required column is absent from the file",
    "JE-EMPTY-FIELD":       "A required field is empty on a given line",
    "JE-MIXED-DATE":        "Lines of the same entry have different dates",
    "JE-MIXED-CURRENCY":    "Lines of the same entry have different currencies",
    "JE-MIXED-PARTNER":     "Lines of the same entry have different business partners",
    "JE-MIXED-DESCRIPTION": "Lines of the same entry have different entry descriptions",
    "JE-UNBALANCED":        "Sum of debits and credits for an entry does not net to zero",
    "JE-UNKNOWN-GL":        "A GL account is not in the chart of accounts or mapping table",
    "JE-INVALID-DATE":      "A date value cannot be parsed",
    "JE-INVALID-CURRENCY":  "A currency code is not a recognised ISO 4217 code",
}


# Canonical column roles. The ingest / mapping layer is responsible for
# producing a DataFrame whose columns are named exactly like this before
# validation runs. This decouples validators from the source system's
# column naming.
CANONICAL_COLUMNS = {
    "entry_id":             "Groups lines belonging to the same journal entry",
    "date":                 "Posting date of the line",
    "currency":             "ISO 4217 currency code",
    "debit":                "Debit amount (numeric, >= 0)",
    "credit":               "Credit amount (numeric, >= 0)",
    "gl_account":           "General ledger account code",
    "business_partner":     "Business partner name or id",
    "entry_description":    "Description applying to the whole entry",
    "line_description":     "Description applying to a single line",
}


JOURNAL_ENTRY_CONTRACT = {
    "name": "journal_entry",
    "version": 1,
    "grain": "entry_id",
    "canonical_columns": CANONICAL_COLUMNS,
    "required_columns": [
        "entry_id",
        "date",
        "currency",
        "debit",
        "credit",
        "gl_account",
    ],
    "per_group_invariants": [
        # Each (issue_code, column) pair the validator will check.
        {"issue_code": "JE-MIXED-DATE",        "column": "date"},
        {"issue_code": "JE-MIXED-CURRENCY",    "column": "currency"},
        {"issue_code": "JE-MIXED-PARTNER",     "column": "business_partner"},
        {"issue_code": "JE-MIXED-DESCRIPTION", "column": "entry_description"},
        {"issue_code": "JE-UNBALANCED",        "column": None},
    ],
    "per_line_invariants": [
        {"issue_code": "JE-EMPTY-FIELD",      "column": None},
        {"issue_code": "JE-INVALID-DATE",     "column": "date"},
        {"issue_code": "JE-INVALID-CURRENCY", "column": "currency"},
        {"issue_code": "JE-UNKNOWN-GL",       "column": "gl_account"},
    ],
    "tolerances": {
        "balance": 0.01,
    },
    "issue_codes": ISSUE_CODES,
}
