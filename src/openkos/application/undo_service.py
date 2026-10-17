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
from openkos.model import relations

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


# -- a connection ---------------------------------------------------------- #


@dataclass(frozen=True)
class UnrelatePlan:
    """Removing the `relation_type` relation from `source_id` to `target_id`.
    `withdraws_deprecation`: the target's deprecated-status export is withdrawn
    too, because nothing else supersedes it."""

    source_id: str
    relation_type: str
    target_id: str
    withdraws_deprecation: bool
    content_digest: str


@dataclass(frozen=True)
class _Unrelating:
    plan: UnrelatePlan
    prepared: application_lifecycle.PreparedUnrelate
    source_path: Path
    target_path: Path


def _plan_unrelate(
    root: Path, source_id: str, relation_type: str, target_id: str
) -> _Unrelating | UndoRefusal:
    """The unrelate plan from the bytes on disk now, as `unrelate` makes it."""
    layout = config.WorkspaceLayout(root)
    try:
        source_path, source = application_lifecycle.resolve_concept_path(
            layout.bundle_dir, source_id
        )
        target_path, target = application_lifecycle.resolve_concept_path(
            layout.bundle_dir, target_id
        )
        rel_type = relations.validate_relation_type(relation_type)
    except ValueError:
        return UndoRefusal("not_related")
    if source == target:
        return UndoRefusal("not_related")
    try:
        prepared = application_lifecycle.prepare_unrelate(
            source_path,
            layout.bundle_dir / "log.md",
            source,
            target,
            rel_type,
            root,
            now=datetime.now(UTC),
            target_path=target_path,
        )
    except ValueError:
        return UndoRefusal("not_related")
    except OSError as exc:
        raise WriteFailed("unrelate", (source, target), str(exc)) from exc
    withdraws = prepared.new_target_text is not None
    lines = [
        f"edge\t{source}\t{rel_type}\t{target}",
        f"source\t{hashlib.sha256(prepared.source_bytes).hexdigest()}",
    ]
    if prepared.target_bytes is not None:
        lines.append(f"target\t{hashlib.sha256(prepared.target_bytes).hexdigest()}")
    plan = UnrelatePlan(
        source_id=source,
        relation_type=rel_type,
        target_id=target,
        withdraws_deprecation=withdraws,
        content_digest=_digest(lines),
    )
    return _Unrelating(plan, prepared, source_path, target_path)


def preview_unrelate(
    root: Path, source_id: str, relation_type: str, target_id: str
) -> UnrelatePlan | UndoRefusal:
    """Plan removing one typed relation, exactly as `unrelate` would.

    The digest covers the edge, the source page's bytes, the target page's
    bytes when its deprecated-status export is withdrawn, and whether it is.
    Writes nothing and takes no lock."""
    planned = _plan_unrelate(root, source_id, relation_type, target_id)
    return planned if isinstance(planned, UndoRefusal) else planned.plan


@dataclass(frozen=True)
class Unrelated:
    """The relation was removed. `commit_sha` is `None` when the commit could
    not be made (the write itself landed)."""

    source_id: str
    relation_type: str
    target_id: str
    commit_sha: str | None


def apply_unrelate(
    root: Path,
    source_id: str,
    relation_type: str,
    target_id: str,
    content_digest: str,
    *,
    ports: curate_decisions.DecisionPorts,
) -> Unrelated | UndoRefusal | UndoStale:
    """Remove the relation previewed as `content_digest`, under one lock.

    Plans again inside the commit section, so the plan and the write see the
    same bytes; a refusal or a moved digest writes nothing. Otherwise the
    source page, the target's withdrawal when planned and the `log.md` line are
    written and committed, as `unrelate` writes them."""
    layout = config.WorkspaceLayout(root)
    with ports.commit_section():
        planned = _plan_unrelate(root, source_id, relation_type, target_id)
        if isinstance(planned, UndoRefusal):
            return planned
        plan = planned.plan
        if plan.content_digest != content_digest:
            return UndoStale()
        try:
            application_lifecycle.unrelate_core(
                planned.source_path,
                layout.bundle_dir / "log.md",
                planned.prepared,
                target_path=planned.target_path,
            )
        except (OSError, ValueError) as exc:
            raise WriteFailed(
                "unrelate", (plan.source_id, plan.target_id), str(exc)
            ) from exc
        paths = [f"bundle/{plan.source_id}.md", "bundle/log.md"]
        if plan.withdraws_deprecation:
            paths.insert(1, f"bundle/{plan.target_id}.md")
        commit_sha = ports.autocommit(
            root,
            paths,
            f"openkos: unrelate {plan.source_id} -> {plan.target_id} "
            f"({plan.relation_type})",
        )
    return Unrelated(
        source_id=plan.source_id,
        relation_type=plan.relation_type,
        target_id=plan.target_id,
        commit_sha=commit_sha,
    )
