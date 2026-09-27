"""The disclosure gate (design Decision 2, ADR-0028): the ONLY module
under `src/openkos/mcp/` that references `openkos.sensitivity`'s
disclosure predicate or its allowed-set sibling. Every tool result is
built through a `disclose_*` function here before it can leave the
process; `mcp/tools.py` never calls `sensitivity` directly, and no other
`mcp` module ever needs to.

`Snapshot`/`take_snapshot` are the shared plumbing every `disclose_*`
function needs: the allowed-id set plus the launch-time policy flag,
taken once per tool call. `finish` aggregates a `disclose_*` function's
raw `not_run` entries with `application.consistency.Consistency`'s own,
renders both into the fixed, count-only vocabulary (design Decision 3),
and renders `warnings` from the consistency check (design Decision 11).

Slice 5 added `disclose_get` (design Decision 4), the first real
`disclose_*` function; this slice adds `disclose_navigate` (design
Decision 5). `disclose_pending`/`disclose_query` land in slices 7-9 as
their tools do.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, cast

from openkos import read_outcome, sensitivity
from openkos.application import concept_read, list_service
from openkos.application import consistency as application_consistency
from openkos.model import okf


@dataclass(frozen=True)
class Snapshot:
    """The set of concept ids a tool result may disclose right now,
    captured once per tool call (design Decision 2: taken AFTER a tool's
    `run`, so an object raised to confidential mid-read is still caught).

    `expose_confidential` travels alongside `allowed` so a `disclose_*`
    function can re-check a freshly read sensitivity value against the
    SAME launch-time policy the snapshot was built under -- the `get`
    conjunction (design Decision 4) needs both the snapshot's allowed set
    AND the target's own just-read label, without a second parameter.
    """

    allowed: frozenset[str]
    expose_confidential: bool

    def discloses(self, concept_id: str) -> bool:
        return concept_id in self.allowed


def take_snapshot(bundle_dir: Path, *, expose_confidential: bool) -> Snapshot:
    """Walk the bundle once and capture which ids may be disclosed right
    now."""
    allowed = sensitivity.disclosable_concept_ids(
        bundle_dir, expose_confidential=expose_confidential
    )
    return Snapshot(allowed=allowed, expose_confidential=expose_confidential)


_IN_FLIGHT_WRITE: Final = "in_flight_write"
_STALE_INDEX: Final = "stale_index"
_CONCEPT_READ: Final = "concept_read"
_RELATIONS: Final = "relations"
_PROVENANCE_WALK: Final = "provenance_walk"
_GRAPH_BUILD: Final = "graph_build"

_NOT_RUN_LABELS: Final = frozenset(
    {
        _IN_FLIGHT_WRITE,
        _STALE_INDEX,
        _CONCEPT_READ,
        _RELATIONS,
        _PROVENANCE_WALK,
        _GRAPH_BUILD,
    }
)
"""design Decision 3's fixed `not_run` label vocabulary, in full -- kept as
one named constant even though `_render_not_run` (below) only routes THREE
of these six through its own single-entry table. `stale_index` never
actually produces a `not_run` entry (`application.consistency.
read_consistency`'s staleness check never raises); `graph_build` (slice
6's `navigate`) is built directly from a count, never from raw `NotRun`
outcomes. This constant documents the complete vocabulary design Decision 3
names, independent of which labels this slice's aggregation touches."""

_GRAPH_BUILD_REASON_RE: Final = re.compile(r"[1-9][0-9]* edges? could not be included")
"""The EXACT count-only shape `disclose_navigate` builds for a `graph_build`
entry (`"<n> edge(s) could not be included"`). `_render_not_run` fullmatches
every `graph_build` entry's `reason` against this before forwarding it --
the label alone is NOT a trust boundary, since `finish` also composes
`NotRun`s a *service* could emit under the same label vocabulary. A reason
that does not fullmatch (a document path, `str(exc)`, anything else) is
therefore treated as unrecognized and folded into the aggregate bucket
instead, exactly like any other untrusted entry -- fail-closed on shape,
never on label or producer."""

_PASSTHROUGH_REASON_PATTERNS: Final[Mapping[str, re.Pattern[str]]] = {
    _GRAPH_BUILD: _GRAPH_BUILD_REASON_RE,
}
"""Labels whose `reason` legitimately varies per call (so a single
`_FIXED_REASONS` string cannot represent it) but is still safe to forward
ONCE VALIDATED against a fixed shape -- `graph_build` (slice 6's
`navigate`) is the only member today. Unlike `_SINGLE_ENTRY_LABELS` (label
kept, reason always replaced by a `_FIXED_REASONS` lookup) or the
aggregate bucket (folded into one `provenance_walk` entry), a passthrough
label's reason is kept verbatim ONLY when it fullmatches its pattern;
otherwise it degrades to the aggregate bucket, same as an unrecognized
label."""

_SINGLE_ENTRY_LABELS: Final = frozenset({_IN_FLIGHT_WRITE, _CONCEPT_READ, _RELATIONS})
"""Labels a service already emits directly and correctly (never a document
path): `finish` keeps the label and only replaces the `reason` text, since
these are single-occurrence checks per tool call. Any OTHER label --
`list_provenance_sources`'s document-path labels (`list_service.py:303`),
or a genuinely unrecognized one -- is aggregated into one `provenance_walk`
entry instead (design Decision 3: "an unknown label is aggregated too").
`graph_build` (slice 6's `navigate`) is deliberately NOT included here: its
`not_run` entry is built directly from a count, not from raw `NotRun`
outcomes routed through this aggregation."""

_FIXED_REASONS: Final[Mapping[str, str]] = {
    _IN_FLIGHT_WRITE: "the in-flight write check could not run",
    _CONCEPT_READ: "the concept could not be read",
    _RELATIONS: "the concept's relations could not be read",
}

_IN_FLIGHT_WRITE_WARNING: Final = (
    "a write may be in flight; this result may reflect a partial state"
)
_STALE_INDEX_WARNING: Final = "a derived store predates the current bundle"


def _render_not_run(
    entries: tuple[read_outcome.NotRun, ...],
) -> list[dict[str, object]]:
    """Render raw `NotRun` outcomes into the fixed, count-only vocabulary
    (design Decision 3). Every `reason` is EITHER a hardcoded string (never
    the raw entry's own `reason`, which may be `str(exc)` or carry a
    document path) OR, for a passthrough label, the entry's own `reason` --
    but ONLY after it fullmatches that label's fixed shape
    (`_PASSTHROUGH_REASON_PATTERNS`); an entry whose label claims to be
    `graph_build` but whose `reason` does NOT match (a document path,
    exception text, anything else) is treated as unrecognized and folded
    into the aggregate bucket instead, exactly like a genuinely unknown
    label -- the label alone is never trusted as a boundary, since `finish`
    composes entries a service could produce under the SAME label
    vocabulary. Order follows the fixed vocabulary, not arrival order, so
    the rendered list is deterministic."""
    single: dict[str, dict[str, object]] = {}
    passthrough: list[dict[str, object]] = []
    aggregate_count = 0
    for entry in entries:
        pattern = _PASSTHROUGH_REASON_PATTERNS.get(entry.label)
        if entry.label in _SINGLE_ENTRY_LABELS:
            single[entry.label] = {
                "label": entry.label,
                "reason": _FIXED_REASONS[entry.label],
            }
        elif pattern is not None and pattern.fullmatch(entry.reason):
            passthrough.append({"label": entry.label, "reason": entry.reason})
        else:
            aggregate_count += 1

    rendered: list[dict[str, object]] = []
    for label in (_IN_FLIGHT_WRITE, _CONCEPT_READ, _RELATIONS):
        if label in single:
            rendered.append(single[label])
    rendered.extend(passthrough)
    if aggregate_count:
        noun = "document" if aggregate_count == 1 else "documents"
        rendered.append(
            {
                "label": _PROVENANCE_WALK,
                "reason": f"{aggregate_count} {noun} could not be read",
            }
        )
    return rendered


def finish(
    payload: Mapping[str, object], consistency: application_consistency.Consistency
) -> dict[str, object]:
    """Compose a `disclose_*` function's payload with consistency
    `warnings` and aggregated `not_run` entries (design Decision 3).

    `payload["not_run"]` (when present) is a tuple of RAW `read_outcome.
    NotRun` outcomes a `disclose_*` function collected -- not yet rendered
    wire shape. This combines them with `consistency.not_run` (its own raw
    `in_flight_write` failure, if any) and renders the union through the
    fixed vocabulary. `warnings` is built fresh here: `in_flight_write`
    when `consistency.in_flight_writes` is a positive count, `stale_index`
    when `consistency.stale_stores` names at least one store -- never both
    derived from the SAME field, since a scan that could not run
    (`in_flight_writes is None`) produces a `not_run` entry instead of a
    warning.
    """
    result = dict(payload)

    raw_not_run = cast("tuple[read_outcome.NotRun, ...]", payload.get("not_run") or ())
    result["not_run"] = _render_not_run(raw_not_run + consistency.not_run)

    warnings: list[dict[str, object]] = []
    if (
        isinstance(consistency.in_flight_writes, int)
        and consistency.in_flight_writes > 0
    ):
        warnings.append(
            {
                "code": _IN_FLIGHT_WRITE,
                "count": consistency.in_flight_writes,
                "message": _IN_FLIGHT_WRITE_WARNING,
            }
        )
    if consistency.stale_stores:
        warnings.append(
            {
                "code": _STALE_INDEX,
                "stores": list(consistency.stale_stores),
                "message": _STALE_INDEX_WARNING,
            }
        )
    result["warnings"] = warnings

    return result


@dataclass(frozen=True)
class GetRaw:
    """`get`'s `run()` result, bundling everything `disclose_get` needs:
    the resolved target's own read AND its provenance-ancestor walk,
    executed together (design Decision 2 -- the snapshot is taken AFTER
    both, so an object raised to confidential mid-read is still caught)."""

    target_id: str
    record: concept_read.ConceptRecord | concept_read.UnreadableConcept
    sources: list_service.ProvenanceSources


def _echoed_sensitivity(value: object) -> str:
    """Normalize a raw `sensitivity` value for disclosure: echoed only when
    it is a recognized `okf.SENSITIVITY_ORDER` member, else `"unknown"` --
    the same normalization `bundle.listing.list_objects` already applies to
    `BundleObject.sensitivity` (design Decision 4)."""
    return value if value in okf.SENSITIVITY_ORDER else "unknown"


def disclose_get(raw: object, snapshot: Snapshot) -> dict[str, object]:
    """Build `get`'s disclosure-safe payload (design Decision 4's table).

    `raw` is typed `object` to match `Tool.disclose`'s contravariant
    signature (every tool's `disclose` shares one field type); it is always
    a `GetRaw` at runtime, since `mcp/tools.py` only ever pairs `get`'s
    `run` (which returns one) with this function.

    The gate requires BOTH `snapshot.discloses(target_id)` (the bundle-wide
    walk `take_snapshot` already did) AND, for a readable target, `not
    blocks_disclosure(record.sensitivity, ...)` (a fresh re-check of the
    SAME read `run()` already performed) -- an object raised to
    confidential between the walk and this call is still withheld, and an
    object the walk missed is not disclosed just because a later read says
    it is fine.
    """
    raw = cast(GetRaw, raw)
    if not snapshot.discloses(raw.target_id):
        return {"concept": None, "withheld": 1, "not_run": ()}

    record = raw.record
    if isinstance(record, concept_read.UnreadableConcept):
        return {
            "concept": None,
            "withheld": 0,
            "not_run": (
                read_outcome.NotRun(
                    label=_CONCEPT_READ, reason=_FIXED_REASONS[_CONCEPT_READ]
                ),
            ),
        }

    if sensitivity.blocks_disclosure(
        record.sensitivity, expose_confidential=snapshot.expose_confidential
    ):
        return {"concept": None, "withheld": 1, "not_run": ()}

    withheld = 0
    not_run: list[read_outcome.NotRun] = list(record.not_run)

    relations: list[dict[str, object]] = []
    for relation in record.relations:
        if snapshot.discloses(relation.target):
            relations.append({"target": relation.target, "type": relation.type})
        else:
            withheld += 1

    provenance: list[str] = []
    for provenance_id in record.provenance:
        if snapshot.discloses(provenance_id):
            provenance.append(provenance_id)
        else:
            withheld += 1

    rows_by_id = {row.concept_id: row for row in raw.sources.rows}
    source_ancestors: list[dict[str, object]] = []
    for ancestor_id in raw.sources.ancestors:
        if not snapshot.discloses(ancestor_id):
            withheld += 1
            continue
        row = rows_by_id.get(ancestor_id)
        source_ancestors.append(
            {
                "id": ancestor_id,
                "title": row.title if row is not None else "",
                "sensitivity": row.sensitivity if row is not None else "unknown",
            }
        )
    not_run.extend(raw.sources.not_run)

    concept: dict[str, object] = {
        "id": record.concept_id,
        "type": record.type,
        "title": record.title,
        "description": record.description,
        "sensitivity": _echoed_sensitivity(record.sensitivity),
        "status": record.status,
        "body": record.body,
        "relations": relations,
        "provenance": provenance,
        "source_ancestors": source_ancestors,
    }
    return {"concept": concept, "withheld": withheld, "not_run": tuple(not_run)}


def disclose_navigate(raw: object, snapshot: Snapshot) -> dict[str, object]:
    """Build `navigate`'s disclosure-safe payload (design Decision 5).

    `raw` is typed `object` to match `Tool.disclose`'s contravariant
    signature; it is always a `concept_read.Neighborhood` at runtime, since
    `mcp/tools.py` only ever pairs `navigate`'s `run` (which returns one)
    with this function.

    The target itself must be disclosable, else `concept_id: null,
    withheld: 1` with no neighbors listed at all -- the graph projection is
    sensitivity-blind by construction (`graph/base.py`), so there is no
    second, freshly-read label to re-check the way `get`'s conjunction
    does; `snapshot.discloses` is the only check for a neighbor too, in
    EITHER direction: an inbound edge is filtered exactly like an outbound
    one (the proposal's "both ends disclosable" rule). `raw.skipped_count`
    becomes a `graph_build` `not_run` entry built directly from the count
    -- `finish`'s `_render_not_run` forwards its `reason` verbatim only
    after re-validating it against `_PASSTHROUGH_REASON_PATTERNS`'s fixed
    shape (this module's own docstring): the label alone is not trusted,
    so this entry is safe only because its `reason` is ALSO built in the
    exact shape that validation expects, never because this function is
    the one that built it."""
    raw = cast(concept_read.Neighborhood, raw)
    if not snapshot.discloses(raw.concept_id):
        return {"concept_id": None, "withheld": 1, "neighbors": [], "not_run": ()}

    withheld = 0
    neighbors: list[dict[str, object]] = []
    for neighbor in raw.neighbors:
        if snapshot.discloses(neighbor.concept_id):
            neighbors.append(
                {
                    "id": neighbor.concept_id,
                    "direction": neighbor.direction,
                    "relation": neighbor.relation_type,
                }
            )
        else:
            withheld += 1

    not_run: tuple[read_outcome.NotRun, ...] = ()
    if raw.skipped_count > 0:
        noun = "edge" if raw.skipped_count == 1 else "edges"
        not_run = (
            read_outcome.NotRun(
                label=_GRAPH_BUILD,
                reason=f"{raw.skipped_count} {noun} could not be included",
            ),
        )

    return {
        "concept_id": raw.concept_id,
        "neighbors": neighbors,
        "withheld": withheld,
        "not_run": not_run,
    }
