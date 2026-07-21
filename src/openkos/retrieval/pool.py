"""Shared retrieval pool-floor helper.

`answer.py` and `graph_retrieve.py` both widen a caller-supplied `limit` to
at least 10 candidates before querying a retriever, so a small final
`limit` (e.g. `limit=1`) still gives fusion/PPR a reasonable candidate pool
to rank from. This module is the single source of that `max(limit, 10)`
floor (design D5, follow-up #2), living in `retrieval/` rather than
`answer.py` or `graph_retrieve.py` specifically so BOTH can depend on it
without either importing the other -- `answer.py` already imports
`graph_retrieve`, so `graph_retrieve` importing FROM `answer.py` would be a
cycle; a shared leaf module has no such constraint.
"""

POOL_FLOOR = 10
"""The minimum candidate-pool size any retrieval call widens `limit` to."""


def pool_limit(limit: int) -> int:
    """Return `limit` widened to at least `POOL_FLOOR`."""
    return max(limit, POOL_FLOOR)
