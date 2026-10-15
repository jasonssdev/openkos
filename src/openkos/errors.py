"""The catchable root of every typed refusal the public API documents.

This module is a leaf: it imports nothing from `openkos`, so the lock, the
backend contract and every application module may subclass the root without a
layering edge.
"""


class OpenKosError(Exception):
    """Root of every typed refusal the public API documents.

    Catch-compatible: a family that adopts it keeps every base it already had,
    so an existing `except` clause or `isinstance` check keeps matching."""
