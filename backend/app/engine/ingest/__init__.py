"""
Deterministic ingest: turning a raw uploaded file into a normalised
DataFrame plus metadata about how that normalisation was done.

Responsibilities:
    - fingerprint: a stable hash of a source file's shape, used as the
      cache key for learned layouts and resolutions.
    - layout: detect sheet, header row, and canonical column roles, with
      a confidence score. Always deterministic; never calls the AI.
    - layout_store: persisted map of fingerprint -> confirmed layout.

The AI only enters the picture at the intent/layout boundary when the
deterministic detector's confidence is low, and even then it produces a
structured layout descriptor for the user to confirm — not code.
"""
