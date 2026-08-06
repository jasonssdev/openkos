"""Unit tests for `retrieval/pool.py`: the shared pool-floor helper.

Names the `max(limit, 10)` "pool floor" once (follow-up #2) so a future
change to the floor value has exactly one place to edit. It was extracted
because `answer.py` and the since-removed `graph_retrieve.py` both needed it
(issue #434 left `answer.py` as the only caller); the constant stays named
rather than being inlined back, because the floor is a retrieval contract --
FTS and dense are both widened to it before either is queried.
"""

from openkos.retrieval import pool


def test_pool_limit_returns_floor_when_limit_is_below_it() -> None:
    """A `limit` below the floor is raised to `POOL_FLOOR`."""
    assert pool.pool_limit(1) == pool.POOL_FLOOR
    assert pool.pool_limit(1) == 10


def test_pool_limit_returns_limit_when_limit_exceeds_the_floor() -> None:
    """A `limit` above the floor passes through unchanged."""
    assert pool.pool_limit(25) == 25


def test_pool_limit_returns_floor_when_limit_equals_the_floor() -> None:
    """A `limit` exactly at the floor returns the floor, unchanged (the
    boundary case: neither branch of `max` should misfire)."""
    assert pool.pool_limit(pool.POOL_FLOOR) == pool.POOL_FLOOR
