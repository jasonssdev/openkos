"""Unit tests for `retrieval/pool.py`: the shared pool-floor helper.

DRYs the `max(limit, 10)` "pool floor" duplicated across `answer.py`/
`graph_retrieve.py` (follow-up #2) into one named constant + function, so a
future change to the floor value has exactly one place to edit.
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
