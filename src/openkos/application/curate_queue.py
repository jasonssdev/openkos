"""What `curate` reads from, and enqueues into, the pending-work queue (#1141,
ADR-0037, `mvp4-unattended-foundations` unit 4.6).

`curate` is a human-facing path: when the queue exists, each stage serves the
OPEN rows of its kind in place of the model work the row already holds, and
enqueues what it computed that holds no open row. Resolution is not here: a row
leaves the open states when the shared write core performs the write it proposes
(`application.queue_resolution`), so `curate` never resolves a row itself, and a
row it presented but neither wrote nor declined simply stays `pending`.

**Serving is per candidate, never per row.** A stage still derives its candidates
from the bundle under its own sensitivity filter (a cheap, model-free walk); a
row only answers for a candidate that walk produced. A row naming an endpoint the
run excludes is therefore never shown, and a row whose inputs no longer match the
bundle is not served (its producer's next complete pass retires it as `stale`).

**Derived and best-effort.** The queue is a derived cache. An absent queue is
today's behaviour exactly -- nothing is read and nothing is created -- and a
queue that cannot be read serves nothing. A failed enqueue costs the row, never
the run.
"""

import json
import sqlite3
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass

from openkos import config, lock
from openkos.application import pending as application_pending
from openkos.application import queue_producers
from openkos.application.lock_wait import CommitSection
from openkos.model import types
from openkos.model.relations import validate_relation_type
from openkos.resolution.adjudication import AdjudicatedCandidate
from openkos.resolution.candidates import CandidateGroup
from openkos.resolution.contradiction import (
    ContradictionVerdict,
    _CandidateSpec,
)
from openkos.resolution.contradiction import Verdict as ContradictionKind
from openkos.resolution.edge_typing import EdgeSuggestion
from openkos.resolution.volatility_typing import TierSuggestion
from openkos.state import derived
from openkos.state import pending_queue as pq


@dataclass(frozen=True)
class OpenRow:
    """One open row, its payload decoded and its input digests read."""

    item: pq.PendingItem
    payload: dict[str, object]
    inputs: tuple[pq.InputDigest, ...]


def _decode(payload: str) -> dict[str, object]:
    try:
        decoded = json.loads(payload)
    except ValueError:
        return {}
    return decoded if isinstance(decoded, dict) else {}


def queue_present(layout: config.WorkspaceLayout) -> bool:
    """Whether the queue has ever been computed here. `False` for an absent or
    unreadable `findings.db`: an unreadable queue serves nothing. The file is
    opened through the derived-store opener, like every other `findings.db`
    reader `curate` uses, so closing it leaves no `-wal`/`-shm` behind; nothing
    is written and an absent file is never created."""
    try:
        if not layout.findings_db_path.is_file():
            return False
        conn = derived.open_derived_connection(layout.findings_db_path)
    except (sqlite3.Error, OSError):
        return False
    try:
        return pq.queue_exists(conn)
    except sqlite3.Error:
        return False
    finally:
        conn.close()


def read_open_rows(layout: config.WorkspaceLayout, kind: str) -> tuple[OpenRow, ...]:
    """The open rows of `kind`, read-only (no lock, no write, no creation).
    Empty for an absent or unreadable queue."""
    if not queue_present(layout):
        return ()
    try:
        conn = derived.open_derived_connection(layout.findings_db_path)
    except (sqlite3.Error, OSError):
        return ()
    try:
        return tuple(
            OpenRow(
                item=item,
                payload=_decode(item.payload),
                inputs=pq.item_input_digests(conn, item.id),
            )
            for item in pq.open_items(conn, kind=kind)
        )
    except sqlite3.Error:
        return ()
    finally:
        conn.close()


def _fresh(
    row: OpenRow,
    current_digest: Callable[[str], str | None],
    required: Iterable[str],
) -> bool:
    """Every input the row was computed from, and every concept it names, still
    hashes to what was recorded. A row with no recorded digest for a concept it
    names cannot be told stale, so it is not served."""
    recorded = {entry.input_ref: entry.digest for entry in row.inputs}
    for concept_id in required:
        if concept_id not in recorded:
            return False
    return all(current_digest(ref) == digest for ref, digest in recorded.items())


# -- identity ---------------------------------------------------------------


def identity_verdicts(
    layout: config.WorkspaceLayout,
) -> dict[tuple[str, ...], tuple[str, float, str]]:
    """Fresh open identity rows that carry a verdict: sorted member ids to
    `(verdict value, confidence, rationale)`."""
    current = application_pending.current_finding_digest(layout.bundle_dir)
    served: dict[tuple[str, ...], tuple[str, float, str]] = {}
    for row in read_open_rows(layout, "identity"):
        verdict = row.payload.get("adjudication")
        if not isinstance(verdict, dict) or not _fresh(row, current, row.item.targets):
            continue
        value, confidence, rationale = (
            verdict.get("verdict"),
            verdict.get("confidence"),
            verdict.get("rationale"),
        )
        if (
            isinstance(value, str)
            and isinstance(confidence, int | float)
            and not isinstance(confidence, bool)
            and isinstance(rationale, str)
        ):
            served[tuple(sorted(row.item.targets))] = (
                value,
                float(confidence),
                rationale,
            )
    return served


# -- relation types -----------------------------------------------------------


@dataclass(frozen=True)
class RelationRow:
    suggested_type: str
    rationale: str
    effective_source_id: str
    effective_target_id: str


def relation_suggestions(
    layout: config.WorkspaceLayout,
) -> dict[tuple[str, str], RelationRow]:
    """Fresh open relation rows, keyed on the candidate edge `(source, target)`."""
    current = application_pending.current_finding_digest(layout.bundle_dir)
    served: dict[tuple[str, str], RelationRow] = {}
    for row in read_open_rows(layout, "relation_type"):
        payload = row.payload
        fields = [
            payload.get(name)
            for name in (
                "source_id",
                "target_id",
                "effective_source_id",
                "effective_target_id",
                "suggested_type",
                "rationale",
            )
        ]
        if not all(isinstance(value, str) for value in fields):
            continue
        source, target, eff_source, eff_target, suggested, rationale = (
            str(value) for value in fields
        )
        if not _fresh(row, current, (source, target)):
            continue
        try:
            suggested = validate_relation_type(suggested)
        except ValueError:
            continue
        served[(source, target)] = RelationRow(
            suggested, rationale, eff_source, eff_target
        )
    return served


# -- volatility ---------------------------------------------------------------


def volatility_suggestions(
    layout: config.WorkspaceLayout,
) -> dict[str, TierSuggestion]:
    """Fresh open volatility rows, keyed on type. A row is fresh while the
    default it was computed against is still the type's default."""
    served: dict[str, TierSuggestion] = {}
    for row in read_open_rows(layout, "volatility"):
        payload = row.payload
        type_name = payload.get("type_name")
        default = payload.get("current_default")
        tier = payload.get("suggested_tier")
        rationale = payload.get("rationale")
        if not (
            isinstance(type_name, str)
            and isinstance(default, str)
            and isinstance(tier, str)
            and isinstance(rationale, str)
        ):
            continue
        if default != types.TYPE_TO_DEFAULT_VOLATILITY.get(type_name, ""):
            continue
        if tier not in types.VOLATILITY_TIERS:
            continue
        served[type_name] = TierSuggestion(
            type_name=type_name,
            current_default=default,
            suggested_tier=tier,
            rationale=rationale,
        )
    return served


# -- enqueue ------------------------------------------------------------------


def _unchanged_since_pinned(
    pinned: Mapping[str, str | None],
    current: Callable[[str], str | None],
    concept_ids: Iterable[str],
) -> bool:
    """Whether every pinned input still hashes to what it did when the model was
    asked. A verdict about content edited while no lock was held is not a
    proposal about the content now on disk, so it is not enqueued. An input
    that was never pinned (served from a store that checked it) passes."""
    return all(
        pinned.get(concept_id, current(concept_id)) == current(concept_id)
        for concept_id in concept_ids
    )


def _enqueue_missing(
    layout: config.WorkspaceLayout,
    proposals: Sequence[pq.Proposal],
    *,
    commit_section: CommitSection,
) -> None:
    """Upsert each proposal that holds no open row. Does nothing when the queue
    does not exist (the first producer pass creates it), and never raises for a
    store problem: the queue is derived, and the run it rode on already did its
    work."""
    if not proposals or not queue_present(layout):
        return
    try:
        conn = derived.open_derived_connection(layout.findings_db_path)
    except (sqlite3.Error, OSError):
        return
    try:
        open_keys = {item.decision_key for item in pq.open_items(conn)}
        for proposal in proposals:
            if proposal.decision_key in open_keys:
                continue
            pq.upsert_proposal(
                conn,
                proposal,
                commit_section=commit_section,
                bundle_dir=layout.bundle_dir,
            )
    except (sqlite3.Error, OSError, lock.WorkspaceBusyError):
        return
    finally:
        conn.close()


def enqueue_identity(
    layout: config.WorkspaceLayout,
    groups: Sequence[CandidateGroup],
    results: Sequence[AdjudicatedCandidate],
    *,
    pinned: Mapping[str, str | None],
    commit_section: CommitSection,
) -> None:
    """Every candidate group of the run that holds no open row, with its verdict
    when it has one. `pinned` is each judged member's digest from before the
    model call."""
    current = application_pending.current_finding_digest(layout.bundle_dir)
    by_members = {tuple(sorted(r.candidate.member_ids)): r for r in results}
    proposals = [
        proposal
        for group in groups
        if _unchanged_since_pinned(pinned, current, group.member_ids)
        and (
            proposal := queue_producers.identity_group_proposal(
                group,
                current_digest=current,
                result=by_members.get(tuple(sorted(group.member_ids))),
            )
        )
        is not None
    ]
    _enqueue_missing(layout, proposals, commit_section=commit_section)


def enqueue_relations(
    layout: config.WorkspaceLayout,
    suggestions: Sequence[EdgeSuggestion],
    *,
    pinned: Mapping[str, str | None],
    commit_section: CommitSection,
) -> None:
    """Every typed suggestion of the run that holds no open row. `pinned` is
    each typed endpoint's digest from before the model call."""
    current = application_pending.current_finding_digest(layout.bundle_dir)
    proposals = [
        proposal
        for suggestion in suggestions
        if _unchanged_since_pinned(
            pinned, current, (suggestion.edge.source_id, suggestion.edge.target_id)
        )
        and (
            proposal := queue_producers.relation_suggestion_proposal(
                suggestion, current_digest=current
            )
        )
        is not None
    ]
    _enqueue_missing(layout, proposals, commit_section=commit_section)


def enqueue_volatility(
    layout: config.WorkspaceLayout,
    suggestions: Sequence[TierSuggestion],
    *,
    commit_section: CommitSection,
) -> None:
    proposals = [
        proposal
        for suggestion in suggestions
        if (proposal := queue_producers.volatility_suggestion_proposal(suggestion))
        is not None
    ]
    _enqueue_missing(layout, proposals, commit_section=commit_section)


def enqueue_contradictions(
    layout: config.WorkspaceLayout,
    pairs: Sequence[tuple[ContradictionVerdict, _CandidateSpec | None]],
    *,
    input_digests: queue_producers.InputDigestsFor,
    commit_section: CommitSection,
) -> None:
    """Every `CONTRADICTS` verdict that holds no open row. Report-only: a
    contradiction is enqueued here and never resolved, applied or written."""
    proposals = [
        queue_producers.contradiction_proposal(
            verdict,
            spec,
            input_digests=input_digests,
            bundle_dir=layout.bundle_dir,
        )
        for verdict, spec in pairs
        if verdict.verdict is ContradictionKind.CONTRADICTS
    ]
    _enqueue_missing(layout, proposals, commit_section=commit_section)
