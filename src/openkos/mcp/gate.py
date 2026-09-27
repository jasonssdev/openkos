"""The disclosure gate (design Decision 2, ADR-0028): the ONLY module
under `src/openkos/mcp/` that references `openkos.sensitivity`'s
disclosure predicate or its allowed-set sibling. Every tool result is
built through a `disclose_*` function here before it can leave the
process; `mcp/tools.py` never calls `sensitivity` directly, and no other
`mcp` module ever needs to.

This slice adds the shared plumbing every `disclose_*` function needs:
`Snapshot` (the allowed-id set plus the launch-time policy flag, taken
once per tool call) and `take_snapshot`. `finish`, which aggregates
consistency `warnings` and `not_run` entries (design Decision 3), is a
SKELETON here -- it does not yet render `warnings`, because
`application.consistency.Consistency` (slice 5) does not exist yet, so
there is nothing to aggregate. The real `disclose_get`/`disclose_navigate`/
`disclose_pending`/`disclose_query` functions land in slices 5-9 as their
tools do.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from openkos import sensitivity


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


def finish(payload: Mapping[str, object], consistency: object) -> dict[str, object]:
    """Compose a `disclose_*` function's payload with consistency
    `warnings` and aggregated `not_run` entries (design Decision 3).

    Skeleton for this slice: `consistency` is accepted now -- so
    `mcp/tools.py`'s future composition (design Decision 2) can call this
    signature without another edit here -- but typed loosely and not yet
    consulted, since `application.consistency.Consistency` does not exist
    until slice 5. `payload` passes through unchanged. Slice 5 extends
    this to render `{in_flight_write, count}`/`{stale_index, stores}`
    warnings and aggregate document-labelled `NotRun` outcomes into the
    fixed, count-only vocabulary (design Decision 3).
    """
    return dict(payload)
