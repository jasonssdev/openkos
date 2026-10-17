"""Undoing a review choice from a stateless client (ADR-0067).

Keep separate, a connection and a how-quickly setting are each undone by a
preview and an apply keyed by what the choice wrote, never by its decision key:
the member set, the edge, the type. A preview plans from the bytes on disk and
returns a content digest; the apply takes the workspace lock once, plans again
from the bytes it will write over, and writes only when the digest is the one
the caller previewed. One write, one commit. No queue row is resolved or
proposed, no derived store is refreshed and no model is reached.
"""

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from openkos import config
from openkos.application import curate_decisions, duplicates_service
from openkos.application import lifecycle as application_lifecycle
from openkos.application import pending as application_pending
from openkos.application.write_gate import WriteFailed
from openkos.bundle import decisions as bundle_decisions

UndoReason = Literal[
    "not_kept_distinct", "not_related", "tier_unchanged", "unknown_type"
]


@dataclass(frozen=True)
class UndoRefusal:
    """Why the undo cannot be planned as asked; nothing was written."""

    reason: UndoReason


@dataclass(frozen=True)
class UndoStale:
    """The bytes the plan rests on changed since the preview, or the echoed key
    was altered; nothing was written."""


def _digest(lines: Sequence[str]) -> str:
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _file_digest(path: Path) -> str:
    """The bytes' sha256, or `-` for a file that cannot be read."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return "-"


# -- keep separate --------------------------------------------------------- #


@dataclass(frozen=True)
class KeptDistinctRuling:
    """A member set a person ruled distinct and has not reopened."""

    member_ids: tuple[str, ...]
    decided_at: str


def _decided_time(stamp: str) -> datetime:
    """`stamp` as an aware time for ordering; unparseable sorts last."""
    try:
        parsed = datetime.fromisoformat(stamp)
    except ValueError:
        return datetime.min.replace(tzinfo=UTC)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def kept_distinct(root: Path) -> tuple[KeptDistinctRuling, ...]:
    """Every declined identity ruling, newest first, then by member set. Reads
    the decisions sidecars only."""
    records = duplicates_service.list_kept_distinct(root)
    by_members = sorted(records, key=lambda record: record.member_ids)
    return tuple(
        KeptDistinctRuling(member_ids=record.member_ids, decided_at=record.decided_at)
        for record in sorted(
            by_members,
            key=lambda record: _decided_time(record.decided_at),
            reverse=True,
        )
    )


@dataclass(frozen=True)
class ReopenPlan:
    """Reopening the ruling over `member_ids` (sorted)."""

    member_ids: tuple[str, ...]
    content_digest: str


def _members(raw: Sequence[str]) -> tuple[str, ...] | None:
    """`raw` canonicalised, deduplicated and sorted, or `None` when an id is
    not a valid Concept ID or fewer than two distinct members remain."""
    try:
        members = tuple(
            sorted({application_lifecycle.canonicalize_concept_id(m) for m in raw})
        )
    except ValueError:
        return None
    return members if len(members) >= 2 else None


def preview_reopen(root: Path, member_ids: Sequence[str]) -> ReopenPlan | UndoRefusal:
    """Plan reopening the declined ruling over exactly `member_ids`.

    The digest covers the member set and the bytes of the sidecar that holds
    the ruling. Writes nothing and takes no lock."""
    layout = config.WorkspaceLayout(root)
    members = _members(member_ids)
    if members is None or not application_pending.is_group_kept_distinct(
        layout, members
    ):
        return UndoRefusal("not_kept_distinct")
    sidecar = bundle_decisions.decisions_path_for(members[0], layout.bundle_dir)
    lines = [f"member\t{member}" for member in members]
    lines.append(f"sidecar\t{_file_digest(sidecar)}")
    return ReopenPlan(member_ids=members, content_digest=_digest(lines))


@dataclass(frozen=True)
class Reopened:
    """The ruling was reopened. `commit_sha` is `None` when the commit could
    not be made (the write itself landed)."""

    member_ids: tuple[str, ...]
    commit_sha: str | None


def apply_reopen(
    root: Path,
    member_ids: Sequence[str],
    content_digest: str,
    *,
    ports: curate_decisions.DecisionPorts,
) -> Reopened | UndoRefusal | UndoStale:
    """Reopen the ruling previewed as `content_digest`, under one lock.

    Plans again inside the commit section; a refusal or a moved digest writes
    nothing. Otherwise the ruling is rewritten with state `open` and the one
    sidecar it lives in is committed."""
    layout = config.WorkspaceLayout(root)
    with ports.commit_section():
        plan = preview_reopen(root, member_ids)
        if isinstance(plan, UndoRefusal):
            return plan
        if plan.content_digest != content_digest:
            return UndoStale()
        try:
            rel_path = duplicates_service.apply_identity_decision(
                layout, plan.member_ids, target_state="open"
            )
        except (OSError, ValueError) as exc:
            raise WriteFailed("reopen", plan.member_ids, str(exc)) from exc
        commit_sha = ports.autocommit(
            root,
            [rel_path],
            f"openkos: reopen identity {'/'.join(plan.member_ids)}",
        )
    return Reopened(member_ids=plan.member_ids, commit_sha=commit_sha)
