"""Shared retrieval pool-floor helper.

`answer.py` widens a caller-supplied `limit` to at least 10 candidates
before querying either retriever, so a small final `limit` (e.g. `limit=1`)
still gives fusion a reasonable candidate pool to rank from. This module is
the single source of that `max(limit, 10)` floor (design D5, follow-up #2).

It lives in `retrieval/` rather than inside `answer.py` because it was
originally shared with `graph_retrieve.py`, which needed the same floor and
could not import `answer.py` without a cycle. Issue #434 removed that second
caller along with the graph channel, leaving `answer.py` as the only one.
The constant stays named and separate anyway: the floor is a retrieval
CONTRACT (both channels are widened to it before either is queried), and the
pool size is exactly the kind of number a future change has to be able to
find in one place.
"""

POOL_FLOOR = 10
"""The minimum candidate-pool size any retrieval call widens `limit` to."""


def pool_limit(limit: int) -> int:
    """Return `limit` widened to at least `POOL_FLOOR`."""
    return max(limit, POOL_FLOOR)
