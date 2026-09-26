"""The bounded revision-history walk (superseded-history-in-query, slice 2a,
design.md Decision 2).

`walk_history` is pure BFS over an INJECTED `read` callable: it never opens a
bundle file itself, so the security property that a gated node's edges are
never followed holds by construction, and the walk is fully testable with a
dict-backed fake reader. `answer.py` (slice 2b) supplies `read` as a
`functools.partial` over the same `_guarded_read` helper the hit loop
already uses, so a hit and a predecessor can never diverge on which gates
apply.

The walk reads `relations:` via `okf.decode_relations`, filtered to
`supersedes`/`revises` and sorted `(0 if supersedes else 1, target)` — a
`ValueError` from a malformed `relations:` field yields `[]`, the same
fail-safe `lifecycle.py:73-76` already uses for the same field.

This module imports only the standard library, `openkos.model.okf`, and
`openkos.event_dates` (a package-root leaf `retrieval` may depend on with no
cycle) — never `openkos.resolution` or `openkos.config`."""

from collections import deque
from collections.abc import Callable, Mapping
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from datetime import date
from typing import Final, Literal, cast

from openkos.event_dates import ResolvedEventDate
from openkos.model import okf

HistoryRole = Literal["superseded", "refined"]
"""The relation a predecessor was reached through: `supersedes` reads as
`"superseded"` (the target is always deprecated, `lifecycle.py:84`);
`revises` reads as `"refined"` (the target may or may not be deprecated
elsewhere)."""

MAX_DEPTH: Final = 3
"""Hops from the retrieved successor a predecessor may be reached at.
A node reached exactly at depth 3 is still attached; depth 4+ is never
enqueued (design.md Decision 2)."""

MAX_BLOCKS: Final = 3
"""Predecessors attached per successor, across the whole chain — not per
depth level (design.md Decision 2)."""

_ROLE_BY_RELATION_TYPE: Final[dict[str, HistoryRole]] = {
    "supersedes": "superseded",
    "revises": "refined",
}


@dataclass(frozen=True)
class Predecessor:
    """One attached history block's source data (design.md Decision 2).

    `holder_id` is the id of the edge's OWN owner — the immediate later
    version that reached this node — never the top-level successor for a
    depth-2+ predecessor. `metadata`/`body` are the predecessor's own
    already-guarded-read frontmatter and text, so the caller (`answer.py`)
    can build its label, date and citation with no further read."""

    concept_id: str
    role: HistoryRole
    holder_id: str
    metadata: dict[str, object]
    body: str


@dataclass(frozen=True)
class HistoryWalk:
    """The walk's result: the attached predecessors, in BFS order, and
    whether the successor's reachable chain continues past what is shown
    (the block cap, the depth bound, or both) — computed with no extra read
    (design.md Decision 2)."""

    predecessors: tuple[Predecessor, ...]
    truncated: bool


ReadFn = Callable[[str], "tuple[dict[str, object], str] | None"]
"""The injected reader: `None` means a gate refused the node or it was
unreadable — a dead end whose own edges are never followed."""


def _history_edges(metadata: Mapping[str, object]) -> list[tuple[str, HistoryRole]]:
    """`supersedes`/`revises` edges off `metadata`, sorted `(0 if
    supersedes else 1, target)` (design.md Decision 2). A malformed
    `relations:` field yields `[]` rather than raising — the same
    fail-safe `lifecycle.py:73-76` uses for the identical field."""
    try:
        relations = okf.decode_relations(dict(metadata))
    except ValueError:
        return []
    edges = [
        (relation.target, _ROLE_BY_RELATION_TYPE[relation.type])
        for relation in relations
        if relation.type in _ROLE_BY_RELATION_TYPE
    ]
    edges.sort(key=lambda edge: (0 if edge[1] == "superseded" else 1, edge[0]))
    return edges


def walk_history(
    successor_id: str,
    successor_metadata: Mapping[str, object],
    *,
    read: ReadFn,
    skip: AbstractSet[str],
) -> HistoryWalk:
    """The exact BFS of design.md Decision 2, in the order a test pins:
    `supersedes` before `revises`, then ascending concept id, level by
    level; a `visited` set guards cycles (self-edges included); a member
    of `skip` is neither attached nor expanded; a refused/unreadable read
    is a dead end whose edges are never followed; attaching stops at
    `MAX_BLOCKS`; expansion stops at `MAX_DEPTH`. `truncated` is set
    whenever the cap cuts off a legitimate (unvisited, unskipped) node, or
    a depth-3 node itself has an unvisited/unskipped outbound edge — both
    computed from edge targets already in hand, with no extra read."""
    visited: set[str] = {successor_id}
    queue: deque[tuple[str, HistoryRole, int, str]] = deque(
        (target, role, 1, successor_id)
        for target, role in _history_edges(successor_metadata)
    )
    attached: list[Predecessor] = []
    truncated = False

    while queue:
        target, role, depth, holder = queue.popleft()
        if target in visited:
            continue
        visited.add(target)
        if target in skip:
            continue
        if len(attached) == MAX_BLOCKS:
            # `target` already passed both guards above, so it is by
            # construction a legitimate node this walk did not show.
            truncated = True
            break
        result = read(target)
        if result is None:
            continue
        metadata, body = result
        attached.append(
            Predecessor(
                concept_id=target,
                role=role,
                holder_id=holder,
                metadata=metadata,
                body=body,
            )
        )
        edges = _history_edges(metadata)
        if depth == MAX_DEPTH:
            if any(
                edge_target not in visited and edge_target not in skip
                for edge_target, _ in edges
            ):
                truncated = True
            continue
        for edge_target, edge_role in edges:
            queue.append((edge_target, edge_role, depth + 1, target))

    return HistoryWalk(predecessors=tuple(attached), truncated=truncated)


def date_phrase(resolved: ResolvedEventDate) -> str:
    """The date phrase for a history label (design.md Decision 5):
    `"event date <D>"` for `dated`, `"event dates <earliest> to <latest>"`
    (ISO, earliest-to-latest) for `multiple`, and `"event date unknown"`
    for `missing`/`none-reached` — never the ingest/retrieval time, only a
    resolved event date or an explicit unknown."""
    if resolved.state == "dated":
        return f"event date {cast(date, resolved.earliest).isoformat()}"
    if resolved.state == "multiple":
        earliest = cast(date, resolved.earliest).isoformat()
        latest = cast(date, resolved.latest).isoformat()
        return f"event dates {earliest} to {latest}"
    return "event date unknown"


def label_note(
    role: HistoryRole, holder_id: str, resolved: ResolvedEventDate, current: bool
) -> str:
    """The history-note suffix appended to a predecessor's label
    (design.md Decision 5): currency is `"still current"` only for a
    `"refined"` predecessor that `current` (not itself deprecated
    elsewhere) says is still current; every other row — `"superseded"`
    (always deprecated, `lifecycle.py:84`) and `"refined"` with `current`
    false — reads `"no longer current"`."""
    currency = "still current" if role == "refined" and current else "no longer current"
    return (
        f" (earlier version, {role} by concept_id: {holder_id}; "
        f"{date_phrase(resolved)}; {currency})"
    )
