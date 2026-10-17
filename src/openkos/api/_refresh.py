"""Refreshing the decision queue from persisted verdicts, with no model.

The service computes every outcome first, holding no lock, and writes the queue
only inside commit sections that wait at most `wait_seconds` for the workspace
lock. No client is built. The result is API-owned: the daemon's internal tally
type does not cross the boundary.
"""

from dataclasses import dataclass
from typing import Literal

from openkos.api import _wiring
from openkos.api._workspace import Workspace
from openkos.application import lock_wait, queue_producers, queue_refresh


@dataclass(frozen=True)
class KindRefresh:
    """What one refresh did for one kind of decision."""

    kind: Literal["identity", "relation_type", "volatility"]
    inserted: int
    amended: int
    unchanged: int
    replaced: int
    suppressed: int
    retired: int
    complete: bool
    """Whether the pass covered every row of the kind. Only `identity` can say
    so, and only when its report was not truncated; the others are `False`."""

    ambiguous: int = 0
    """`volatility` only: types skipped because the two confidentiality regimes
    held different cached answers."""

    retired_missing: int = 0
    """`identity` and `relation_type` only: open rows retired because a page
    they name is no longer in the bundle, whether or not the pass was complete.
    Counted apart from `retired`."""


@dataclass(frozen=True)
class QueueRefresh:
    """The three per-kind results of one refresh."""

    identity: KindRefresh
    relation_type: KindRefresh
    volatility: KindRefresh
    identity_truncation_notice: str | None
    """The identity report's truncation notice, when it was truncated."""


def _kind(
    kind: Literal["identity", "relation_type", "volatility"],
    result: queue_producers.ProducerResult,
    *,
    ambiguous: int = 0,
) -> KindRefresh:
    return KindRefresh(
        kind=kind,
        inserted=result.inserted,
        amended=result.amended,
        unchanged=result.unchanged,
        replaced=result.replaced,
        suppressed=result.suppressed,
        retired=result.retired,
        complete=result.complete,
        ambiguous=ambiguous,
        retired_missing=result.retired_missing,
    )


def refresh_decision_queue(
    workspace: Workspace, *, wait_seconds: float = 0.0
) -> QueueRefresh:
    """Publish every servable persisted verdict as an open decision row.

    Builds no client and calls no model. The workspace lock is taken only inside
    commit sections, each waiting at most `wait_seconds`. Raises
    `QueueRefreshRefused` when the workspace cannot be read, `WorkspaceBusyError`
    when the lock stays held beyond the wait (rows already written stay, and the
    next refresh converges) and `ValueError` for a `wait_seconds` out of
    range."""
    wait = _wiring.validated_wait(wait_seconds)
    outcome = queue_refresh.refresh_decision_queue(
        workspace.root,
        commit_section=lock_wait.locked_commit_section(
            workspace.root, wait_seconds=wait
        ),
    )
    return QueueRefresh(
        identity=_kind("identity", outcome.identity),
        relation_type=_kind("relation_type", outcome.relation_type),
        volatility=_kind(
            "volatility", outcome.volatility, ambiguous=outcome.volatility_ambiguous
        ),
        identity_truncation_notice=outcome.identity_truncation_notice,
    )
