"""Undoing a review choice (ADR-0067): keep separate is reopened, and the
rulings it can be reopened from are listed.

An undo is keyed by what the choice wrote, never by a decision key: the queue
row was resolved when the choice was applied, and the CLI writes the same files
with no row. The preview returns a version carrying that key and a digest of
every byte the plan rests on; the apply takes the workspace lock once, plans
again and writes only when the digest still matches.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal

from openkos.api import _wiring
from openkos.api._workspace import Workspace
from openkos.application import undo_service


@dataclass(frozen=True)
class NotUndoable:
    """Why the undo cannot be made as asked; nothing was written.

    `not_kept_distinct`: no declined ruling over exactly these members.
    `not_related`: the source page holds no such typed relation, or a page no
    longer exists. `tier_unchanged`: the type's setting already is the one
    asked for. `unknown_type`: not a concept type the engine knows."""

    reason: Literal[
        "not_kept_distinct", "not_related", "tier_unchanged", "unknown_type"
    ]


@dataclass(frozen=True)
class UndoStale:
    """What the undo rests on changed since the preview, or the echoed version
    was altered; nothing was written. Preview again."""

    reason: Literal["content_changed"] = "content_changed"


def _refusal(refusal: undo_service.UndoRefusal) -> NotUndoable:
    return NotUndoable(reason=refusal.reason)


# -- keep separate ------------------------------------------------------------ #


@dataclass(frozen=True)
class KeptDistinct:
    """Pages a person said are different things, and when."""

    member_ids: tuple[str, ...]
    """Sorted."""
    decided_at: str
    """When the ruling was recorded (ISO 8601)."""


@dataclass(frozen=True)
class ReopenVersion:
    """The version a stateless caller reopens a ruling against: echo it
    unchanged. `content_digest` covers the members and the sidecar holding the
    ruling."""

    member_ids: tuple[str, ...]
    content_digest: str


@dataclass(frozen=True)
class ReopenPreview:
    """Reopening the ruling over `member_ids`: they may be offered as the same
    thing again."""

    member_ids: tuple[str, ...]
    version: ReopenVersion


@dataclass(frozen=True)
class Reopened:
    """The ruling was reopened. `commit_sha` is `None` when its commit could not
    be made (the write itself landed)."""

    member_ids: tuple[str, ...]
    commit_sha: str | None


def kept_distinct(workspace: Workspace) -> tuple[KeptDistinct, ...]:
    """Every member set a person ruled distinct and has not reopened, newest
    first. Reads the decisions sidecars only: takes no lock, builds no client
    and writes nothing."""
    return tuple(
        KeptDistinct(member_ids=ruling.member_ids, decided_at=ruling.decided_at)
        for ruling in undo_service.kept_distinct(workspace.root)
    )


def preview_reopen(
    workspace: Workspace, member_ids: Sequence[str]
) -> ReopenPreview | NotUndoable:
    """What reopening the ruling over exactly `member_ids` would do, with the
    version to apply it against. Members are matched as a set, in any order.
    Writes nothing, takes no lock, builds no client."""
    plan = undo_service.preview_reopen(workspace.root, member_ids)
    if isinstance(plan, undo_service.UndoRefusal):
        return _refusal(plan)
    return ReopenPreview(
        member_ids=plan.member_ids,
        version=ReopenVersion(
            member_ids=plan.member_ids, content_digest=plan.content_digest
        ),
    )


def apply_reopen(
    workspace: Workspace,
    version: ReopenVersion,
    *,
    wait_seconds: float = 0.0,
    on_warning: Callable[[str], None] | None = None,
) -> Reopened | NotUndoable | UndoStale:
    """Reopen the ruling `version` was previewed for.

    The workspace lock is taken once, waiting at most `wait_seconds` (0 to
    3600; beyond it `WorkspaceBusyError` is raised and nothing is written), and
    held while the plan is made again and written. A changed ruling or version
    is the returned `UndoStale`, a ruling no longer kept distinct the returned
    `NotUndoable`; neither writes anything. The ruling's sidecar is committed
    on its own; a commit that cannot be made is reported once through
    `on_warning`. No decision is put back in the queue: a refresh does that."""
    wait = _wiring.validated_wait(wait_seconds)
    result = undo_service.apply_reopen(
        workspace.root,
        version.member_ids,
        version.content_digest,
        ports=_wiring.decision_ports(
            workspace, wait_seconds=wait, on_warning=on_warning, what="the undo"
        ),
    )
    if isinstance(result, undo_service.UndoRefusal):
        return _refusal(result)
    if isinstance(result, undo_service.UndoStale):
        return UndoStale()
    return Reopened(member_ids=result.member_ids, commit_sha=result.commit_sha)


# -- a connection ------------------------------------------------------------- #


@dataclass(frozen=True)
class UnrelateVersion:
    """The version a stateless caller removes a relation against: echo it
    unchanged. `content_digest` covers the edge and the pages the removal
    rewrites."""

    source_id: str
    relation_type: str
    target_id: str
    content_digest: str


@dataclass(frozen=True)
class UnrelatePreview:
    """Removing the `relation_type` relation from `source_id` to `target_id`.
    `withdraws_deprecation`: the target stops being marked superseded, because
    nothing else supersedes it."""

    source_id: str
    relation_type: str
    target_id: str
    withdraws_deprecation: bool
    version: UnrelateVersion


@dataclass(frozen=True)
class Unrelated:
    """The relation was removed. `commit_sha` is `None` when its commit could
    not be made (the write itself landed); `committed_confidential` is true when
    the commit holds a concept explicitly marked confidential."""

    source_id: str
    relation_type: str
    target_id: str
    commit_sha: str | None
    committed_confidential: bool = False


def preview_unrelate(
    workspace: Workspace, source_id: str, relation_type: str, target_id: str
) -> UnrelatePreview | NotUndoable:
    """What removing one typed relation would do, exactly as `openkos unrelate`
    plans it, with the version to apply it against. A relation that is not
    there, a page that no longer exists, an invalid id or a blank type is the
    returned `NotUndoable("not_related")`. Writes nothing, takes no lock,
    builds no client."""
    plan = undo_service.preview_unrelate(
        workspace.root, source_id, relation_type, target_id
    )
    if isinstance(plan, undo_service.UndoRefusal):
        return _refusal(plan)
    return UnrelatePreview(
        source_id=plan.source_id,
        relation_type=plan.relation_type,
        target_id=plan.target_id,
        withdraws_deprecation=plan.withdraws_deprecation,
        version=UnrelateVersion(
            source_id=plan.source_id,
            relation_type=plan.relation_type,
            target_id=plan.target_id,
            content_digest=plan.content_digest,
        ),
    )


def apply_unrelate(
    workspace: Workspace,
    version: UnrelateVersion,
    *,
    wait_seconds: float = 0.0,
    on_warning: Callable[[str], None] | None = None,
) -> Unrelated | NotUndoable | UndoStale:
    """Remove the relation `version` was previewed for.

    The lock is taken once, as `apply_reopen` takes it, and held while the plan
    is made again and written. A changed page or version is the returned
    `UndoStale`, a relation no longer there the returned `NotUndoable`; neither
    writes anything. The source page, the target's withdrawal when planned and
    a `log.md` line are written and committed on their own; a commit that cannot
    be made is reported once through `on_warning`."""
    wait = _wiring.validated_wait(wait_seconds)
    notes = _wiring.CommitNotes()
    result = undo_service.apply_unrelate(
        workspace.root,
        version.source_id,
        version.relation_type,
        version.target_id,
        version.content_digest,
        ports=_wiring.decision_ports(
            workspace,
            wait_seconds=wait,
            on_warning=on_warning,
            notes=notes,
            what="the undo",
        ),
    )
    if isinstance(result, undo_service.UndoRefusal):
        return _refusal(result)
    if isinstance(result, undo_service.UndoStale):
        return UndoStale()
    return Unrelated(
        source_id=result.source_id,
        relation_type=result.relation_type,
        target_id=result.target_id,
        commit_sha=result.commit_sha,
        committed_confidential=notes.confidential,
    )
