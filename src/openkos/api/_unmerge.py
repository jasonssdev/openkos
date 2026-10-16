"""Undoing a merge (ADR-0064): the merge history and the two-phase unmerge.

An unmerge is not a queue decision: nothing proposes it and it has no decision
key. What identifies it is the survivor's merge ledger, so the preview is keyed
by the survivor and the absorbed id, and the version it returns carries both
with a digest of every byte the plan rests on.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from openkos.api import _wiring
from openkos.api._workspace import Workspace
from openkos.application import unmerge_service


@dataclass(frozen=True)
class MergeRecord:
    """One merge recorded on a concept: `survivor_id` absorbed `absorbed_id`."""

    survivor_id: str
    absorbed_id: str
    absorbed_title: str | None
    """The absorbed document's title as it was when merged, or `None` when it
    cannot be read."""
    merged_at: str
    """When the merge was made, as the ledger recorded it (ISO 8601)."""


@dataclass(frozen=True)
class UnmergeStep:
    """One single-step unmerge of a plan: `absorbed_id` is restored beside
    `survivor_id`, both to their bytes before that merge."""

    survivor_id: str
    absorbed_id: str
    absorbed_title: str | None
    merged_at: str
    files_rewritten: int
    """The other documents whose links, relations or provenance the step
    restores."""
    survivor_edits_unverifiable: bool
    """The merge predates the survivor-edit check: an edit made to the survivor
    since cannot be detected, and the restore would discard it."""
    restores_catalog_snapshot: bool
    """The merge recorded whole-file `index.md`/`log.md` snapshots, so the
    restore discards any later change to them."""


@dataclass(frozen=True)
class UnmergeVersion:
    """The version a stateless caller applies an unmerge against: echo it
    unchanged. `content_digest` covers the ledger entries being reversed, every
    file the steps restore, rewrite or recreate, and `discard_survivor_edits`,
    so a changed bundle or an altered field is stale."""

    survivor_id: str
    absorbed_id: str
    content_digest: str
    discard_survivor_edits: bool = False


@dataclass(frozen=True)
class UnmergePreview:
    """What undoing a merge would do: every step in the order it runs, the
    survivor's newer merges first, down to and including the named one."""

    survivor_id: str
    absorbed_id: str
    steps: tuple[UnmergeStep, ...]
    survivor_edits_discarded: bool
    """The first step discards an edit made to the survivor since its merge;
    only possible when the preview was asked to."""
    version: UnmergeVersion


@dataclass(frozen=True)
class NotUnmergeable:
    """Why this merge cannot be undone as asked; nothing was written.

    `survivor_missing`: the survivor was itself merged into `absorbed_into`
    (when a ledger records it), which is undone first. `not_merged`: nothing of
    `absorbed_id` is recorded on the survivor. `survivor_edited`: the survivor
    was edited since the merge, and restoring it would discard that edit."""

    survivor_id: str
    absorbed_id: str
    reason: Literal["survivor_missing", "not_merged", "survivor_edited"]
    absorbed_into: str | None = None


@dataclass(frozen=True)
class UnmergeStale:
    """The bundle or the merge ledger changed since the preview, or the echoed
    version was altered; nothing was written. Preview again."""

    survivor_id: str
    absorbed_id: str
    reason: Literal["content_changed"] = "content_changed"


@dataclass(frozen=True)
class Unmerged:
    """The unmerge took effect: `restored_ids` are back beside the survivor, in
    the order they were restored, each step committed on its own.
    `commit_shas[i]` is `None` when that step's commit could not be made (the
    restore itself still landed). `committed_confidential` is true when a
    commit holds a concept explicitly marked confidential."""

    survivor_id: str
    restored_ids: tuple[str, ...]
    commit_shas: tuple[str | None, ...]
    committed_confidential: bool = False


def merge_history(workspace: Workspace, concept_id: str) -> tuple[MergeRecord, ...]:
    """What was merged into `concept_id`, newest first.

    Reads the concept's merge ledger only: takes no lock, builds no client and
    writes nothing. An id that names no merged concept lists nothing."""
    return tuple(
        MergeRecord(
            survivor_id=entry.survivor_id,
            absorbed_id=entry.absorbed_id,
            absorbed_title=entry.absorbed_title,
            merged_at=entry.merged_at,
        )
        for entry in unmerge_service.merge_history(workspace.root, concept_id)
    )


def preview_unmerge(
    workspace: Workspace,
    survivor_id: str,
    absorbed_id: str,
    *,
    discard_survivor_edits: bool = False,
) -> UnmergePreview | NotUnmergeable:
    """What undoing the merge of `absorbed_id` into `survivor_id` would do, with
    the version to apply it against.

    The plan undoes the survivor's merges newest first, down to and including
    that one; when it is the newest, the plan is that one step. Writes nothing,
    takes no lock, builds no client. A cause a client can word is the returned
    `NotUnmergeable`; any other refusal raises `Refused`.
    `discard_survivor_edits` lets the first step restore a survivor that was
    edited since its merge, discarding the edit; it is carried in the version."""
    result = unmerge_service.preview_unwind(
        workspace.root,
        survivor_id,
        absorbed_id,
        discard_survivor_edits=discard_survivor_edits,
    )
    if isinstance(result, unmerge_service.UnwindRefusal):
        return NotUnmergeable(
            survivor_id=survivor_id,
            absorbed_id=absorbed_id,
            reason=result.reason,
            absorbed_into=result.absorbed_into,
        )
    return UnmergePreview(
        survivor_id=result.survivor_id,
        absorbed_id=result.absorbed_id,
        steps=tuple(
            UnmergeStep(
                survivor_id=result.survivor_id,
                absorbed_id=step.absorbed_id,
                absorbed_title=step.absorbed_title,
                merged_at=step.merged_at,
                files_rewritten=step.files_rewritten,
                survivor_edits_unverifiable=step.survivor_edits_unverifiable,
                restores_catalog_snapshot=step.restores_catalog_snapshot,
            )
            for step in result.steps
        ),
        survivor_edits_discarded=result.survivor_edits_discarded,
        version=UnmergeVersion(
            survivor_id=result.survivor_id,
            absorbed_id=result.absorbed_id,
            content_digest=result.content_digest,
            discard_survivor_edits=result.discard_survivor_edits,
        ),
    )


def apply_unmerge(
    workspace: Workspace,
    version: UnmergeVersion,
    *,
    wait_seconds: float = 0.0,
    on_warning: Callable[[str], None] | None = None,
) -> Unmerged | NotUnmergeable | UnmergeStale:
    """Perform the unmerge `version` was previewed for, every step of it.

    The workspace lock is taken once, waiting at most `wait_seconds` (0 to
    3600; beyond it `WorkspaceBusyError` is raised and nothing is written), and
    held while the plan is made again from current bytes and every step runs.
    A changed bundle, ledger or version is the returned `UnmergeStale`, and a
    cause `preview_unmerge` would report the returned `NotUnmergeable`; neither
    writes anything. Each step restores both documents to their bytes before
    that merge and is committed on its own, only the paths it wrote; a commit
    that cannot be made is reported once through `on_warning`.

    A first step that fails raises its own refusal (`Refused` or
    `DriftDetected`), with nothing written. A later one raises
    `UnmergeInterrupted` (a `WriteFailed`): the completed steps are kept and
    committed. The derived indexes are not refreshed."""
    wait = _wiring.validated_wait(wait_seconds)
    notes = _wiring.CommitNotes()
    result = unmerge_service.unwind_to_version(
        workspace.root,
        version.survivor_id,
        version.absorbed_id,
        content_digest=version.content_digest,
        discard_survivor_edits=version.discard_survivor_edits,
        ports=_wiring.unmerge_ports(
            workspace, wait_seconds=wait, on_warning=on_warning, notes=notes
        ),
    )
    if isinstance(result, unmerge_service.UnwindRefusal):
        return NotUnmergeable(
            survivor_id=version.survivor_id,
            absorbed_id=version.absorbed_id,
            reason=result.reason,
            absorbed_into=result.absorbed_into,
        )
    if isinstance(result, unmerge_service.UnwindStale):
        return UnmergeStale(
            survivor_id=version.survivor_id, absorbed_id=version.absorbed_id
        )
    return Unmerged(
        survivor_id=result.survivor_id,
        restored_ids=result.restored_ids,
        commit_shas=result.commit_shas,
        committed_confidential=notes.confidential,
    )
