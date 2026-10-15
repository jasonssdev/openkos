"""Refresh the decision queue from persisted verdicts, with no model (#1391).

Judging writes its verdicts into the adjudication, edge-suggestion and
volatility stores; the pending-work queue is filled by the unattended
producers. `refresh_decision_queue` closes the gap without a daemon pass: it
recomputes the open `identity`, `relation_type` and `volatility` rows from what
is already persisted, so a freshly judged item becomes a listable decision.

**Model-free.** No client is built, no backend is resolved and no model is
called; this module never imports the client builders. It writes only the
pending-work queue -- never `bundle/` and never a verdict store.

**Compute, then commit.** All three outcomes are computed first, holding no
lock. The queue is opened and written only afterwards, and every write -- the
schema creation included -- happens inside the `commit_section` the caller
supplied, the same port the producers use. A busy section propagates unchanged;
rows already upserted stay, and the next refresh converges.

**Retirement.** Only `identity` can prove completeness from persisted state: its
pass is complete exactly when the report is not truncated. `relation_type` and
`volatility` insert and amend only; their retirement stays the unattended
pass's job. An event-driven row is never retired here.
"""

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from openkos import config
from openkos.application import identity_stage, queue_producers
from openkos.application import pending as application_pending
from openkos.application import suggest_relations_service as relations_service
from openkos.application import suggest_volatility_service as volatility_service
from openkos.application.curate_queue import queue_present
from openkos.application.lock_wait import CommitSection
from openkos.errors import OpenKosError
from openkos.resolution import find_candidates_report as _find_candidates_report
from openkos.resolution.candidates import CandidateGroupReport
from openkos.state import derived
from openkos.state import pending_queue as pq


class QueueRefreshRefused(OpenKosError):
    """`root` is not a readable workspace. Nothing was written."""


@dataclass(frozen=True)
class RefreshOutcome:
    """What one refresh did, per kind."""

    identity: queue_producers.ProducerResult
    relation_type: queue_producers.ProducerResult
    volatility: queue_producers.ProducerResult
    volatility_ambiguous: int
    """Types skipped because the two confidentiality regimes held different
    cached answers; the engine never guesses between them."""
    identity_truncation_notice: str | None


def _default_find_candidates_report(*args: object, **kwargs: object) -> object:
    # Late-bound, so a patch of `openkos.resolution.find_candidates_report`
    # keeps intercepting.
    return _find_candidates_report(*args, **kwargs)  # type: ignore[arg-type]


def _nothing(kind: str, *, complete: bool) -> queue_producers.ProducerResult:
    return queue_producers.ProducerResult(
        kind=kind,
        inserted=0,
        unchanged=0,
        replaced=0,
        suppressed=0,
        retired=0,
        complete=complete,
    )


def refresh_decision_queue(
    root: Path,
    *,
    commit_section: CommitSection,
    clock: Callable[[], datetime] | None = None,
    find_candidates_report: Callable[
        ..., CandidateGroupReport
    ] = _default_find_candidates_report,  # type: ignore[assignment]
) -> RefreshOutcome:
    """Publish every servable persisted verdict as an open row of its kind.

    Raises `QueueRefreshRefused` when `root` is not a readable workspace, before
    anything is written. A `WorkspaceBusyError` from `commit_section` propagates
    unchanged."""
    reason = config.require_workspace(root)
    if reason is not None:
        raise QueueRefreshRefused(f"cannot refresh the decision queue: {reason}.")
    layout = config.WorkspaceLayout(root)
    try:
        cfg = config.read_config(root)
    except (OSError, ValueError) as exc:
        raise QueueRefreshRefused(
            f"cannot refresh the decision queue: openkos.yaml could not be read ({exc})."
        ) from exc

    # -- compute: no lock, no client, no write ------------------------------------
    report = identity_stage.identity_report(
        root, layout, find_candidates_report=find_candidates_report
    )
    suggestions = relations_service.stored_edge_suggestions(layout)
    stored = volatility_service.stored_volatility_answers(layout, cfg)
    current_digest = application_pending.current_finding_digest(layout.bundle_dir)
    identity_complete = report.truncation_notice is None

    nothing_to_publish = (
        not queue_producers.identity_proposals(report, current_digest=current_digest)
        and not any(
            queue_producers.relation_suggestion_proposal(
                suggestion, current_digest=current_digest
            )
            for suggestion in suggestions
        )
        and not any(
            queue_producers.volatility_suggestion_proposal(answer)
            for answer in stored.answers
        )
    )
    if nothing_to_publish and not queue_present(layout):
        return RefreshOutcome(
            identity=_nothing("identity", complete=identity_complete),
            relation_type=_nothing("relation_type", complete=False),
            volatility=_nothing("volatility", complete=False),
            volatility_ambiguous=stored.ambiguous,
            identity_truncation_notice=report.truncation_notice,
        )

    # -- commit: every write inside a commit section --------------------------------
    conn = derived.open_derived_connection(layout.findings_db_path)
    try:
        _ensure_queue(conn, commit_section)
        identity = identity_stage.enqueue_identity_report(
            conn, layout, report, commit_section=commit_section, clock=clock
        )
        relation_type = queue_producers.enqueue_stored_relations(
            conn,
            suggestions,
            current_digest=current_digest,
            bundle_dir=layout.bundle_dir,
            commit_section=commit_section,
            clock=clock,
        )
        volatility = queue_producers.enqueue_stored_volatility(
            conn,
            stored.answers,
            bundle_dir=layout.bundle_dir,
            commit_section=commit_section,
            clock=clock,
        )
    finally:
        conn.close()
    return RefreshOutcome(
        identity=identity,
        relation_type=relation_type,
        volatility=volatility,
        volatility_ambiguous=stored.ambiguous,
        identity_truncation_notice=report.truncation_notice,
    )


def _ensure_queue(conn: sqlite3.Connection, commit_section: CommitSection) -> None:
    """Create the queue tables, inside one commit section, when they are absent
    (stricter than the runner's unlocked `ensure_schema`: a refresh writes only
    under the lock)."""
    if pq.queue_exists(conn):
        return
    with commit_section():
        pq.ensure_schema(conn)
