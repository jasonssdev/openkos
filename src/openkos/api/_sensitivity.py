"""Who can see a page (#1517): one page's `sensitivity`, set in two phases.

The preview returns a version carrying the page, the level and a digest of
every byte the plan rests on; the apply takes the workspace lock once, plans
again and writes only when the digest still matches. The plan is
`openkos set-sensitivity`'s, including its raise-only propagation from a
`Source` to every page derived from it (ADR-0009).

Owner-local, like the rest of the surface: the preview names the pages a raise
would reach, by id and title, whatever their level.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from openkos.api import _wiring
from openkos.api._workspace import Workspace
from openkos.application import sensitivity_service as service
from openkos.application import sensitivity_undo_service as undo_service

PageSensitivity = Literal["public", "private", "confidential"]
"""Who can see a page: `public` (shareable), `private` (the default: kept in
the library, sent to a cloud model only when one is on) and `confidential`
(never sent anywhere)."""


@dataclass(frozen=True)
class SensitivityRefused:
    """Why the change cannot be made as asked; nothing was written.

    `not_found`: not a valid Concept ID, or no such page. `unchanged`: the
    page's stored value already is the level asked for."""

    reason: Literal["not_found", "unchanged"]


@dataclass(frozen=True)
class SensitivityStale:
    """What the change rests on changed since the preview, or the echoed
    version was altered; nothing was written. Preview again."""

    reason: Literal["content_changed"] = "content_changed"


@dataclass(frozen=True)
class SensitivityVersion:
    """The version a stateless caller applies against: echo it unchanged.
    `content_digest` covers the page, the level and, for a raise of a source
    file's page, every page that could be derived from it."""

    concept_id: str
    level: PageSensitivity
    content_digest: str


@dataclass(frozen=True)
class RaisedPage:
    """A page raised with the one changed, because it was made from it."""

    concept_id: str
    title: str
    before: PageSensitivity
    after: PageSensitivity


@dataclass(frozen=True)
class SensitivityPreview:
    """Setting `concept_id` to `level`. `current` is the page's level now,
    ranked fail-closed (missing is `private`, unrecognised `confidential`).
    `direction` compares the stored value with `level`; `same` rewrites a
    missing or unrecognised value as the level it already reads as.
    `also_raised` is the pages raised with it: only a raise of a page of type
    `Source` has any, and a lowering never lowers another page."""

    concept_id: str
    title: str
    current: PageSensitivity
    level: PageSensitivity
    direction: Literal["raise", "same", "lower"]
    also_raised: tuple[RaisedPage, ...]
    version: SensitivityVersion


@dataclass(frozen=True)
class SensitivityChanged:
    """The level was set. `previous` is the stored value it replaced (`None`
    when there was none). `commit_sha` is `None` when the commit could not be
    made (the write itself landed). `committed_confidential`: the commit holds
    a page explicitly marked confidential."""

    concept_id: str
    level: PageSensitivity
    previous: str | None
    also_raised: tuple[str, ...]
    commit_sha: str | None
    committed_confidential: bool = False


def preview_sensitivity(
    workspace: Workspace, concept_id: str, level: PageSensitivity
) -> SensitivityPreview | SensitivityRefused:
    """What setting `concept_id`'s sensitivity to `level` would do, with the
    version to apply it against.

    An invalid id or a page that does not exist is the returned
    `SensitivityRefused("not_found")`; a stored value that already is `level`,
    `SensitivityRefused("unchanged")`. A level outside `PageSensitivity` raises
    `ValueError`. Writes nothing, takes no lock, builds no client."""
    plan = service.preview_sensitivity(workspace.root, concept_id, level)
    if isinstance(plan, service.SensitivityRefusal):
        return SensitivityRefused(plan.reason)
    return SensitivityPreview(
        concept_id=plan.concept_id,
        title=plan.title,
        current=plan.current,
        level=plan.level,
        direction=plan.direction,
        also_raised=tuple(
            RaisedPage(p.concept_id, p.title, p.before, p.after)
            for p in plan.also_raised
        ),
        version=SensitivityVersion(
            concept_id=plan.concept_id,
            level=plan.level,
            content_digest=plan.content_digest,
        ),
    )


def apply_sensitivity(
    workspace: Workspace,
    version: SensitivityVersion,
    *,
    wait_seconds: float = 0.0,
    on_warning: Callable[[str], None] | None = None,
) -> SensitivityChanged | SensitivityRefused | SensitivityStale:
    """Set the level `version` was previewed for.

    The lock is taken once and held while the plan is made again and written.
    A changed page (or, for a raise of a source file's page, any changed page)
    or an altered version is the returned `SensitivityStale`; a level set
    meanwhile, `SensitivityRefused("unchanged")`; neither writes anything. The
    raised pages, the page and a `log.md` line are written and committed on
    their own; a commit that cannot be made is reported once through
    `on_warning`. No derived store is refreshed."""
    wait = _wiring.validated_wait(wait_seconds)
    notes = _wiring.CommitNotes()
    result = service.apply_sensitivity(
        workspace.root,
        version.concept_id,
        version.level,
        version.content_digest,
        ports=_wiring.decision_ports(
            workspace,
            wait_seconds=wait,
            on_warning=on_warning,
            notes=notes,
            what="the sensitivity change",
        ),
    )
    if isinstance(result, service.SensitivityRefusal):
        return SensitivityRefused(result.reason)
    if isinstance(result, service.SensitivityStale):
        return SensitivityStale()
    return SensitivityChanged(
        concept_id=result.concept_id,
        level=result.level,
        previous=result.previous,
        also_raised=result.also_raised,
        commit_sha=result.commit_sha,
        committed_confidential=notes.confidential,
    )


# -- undo ------------------------------------------------------------------- #


@dataclass(frozen=True)
class SensitivityNotUndoable:
    """Why the change cannot be undone; nothing was written. `not_found`: not a
    valid Concept ID, or no such page. `no_record`: the newest entry about the
    page is not a change that recorded the pages it raised (an older change, a
    change that raised nothing, or one already undone). `changed_since`: the
    page, or a page raised with it, is no longer at the level the change left
    it."""

    reason: Literal["not_found", "no_record", "changed_since"]


@dataclass(frozen=True)
class SensitivityUndoVersion:
    """The version a stateless caller undoes against: echo it unchanged.
    `content_digest` covers the log and every page of the bundle."""

    concept_id: str
    content_digest: str


@dataclass(frozen=True)
class RestoredPage:
    """A derived page raised with the change. `now` is its level and `after` the
    level it ends at: its own before the change, or higher when another page it
    cites requires it (`after == now`: it stays)."""

    concept_id: str
    title: str
    now: PageSensitivity
    after: PageSensitivity


@dataclass(frozen=True)
class SensitivityUndoPreview:
    """Taking back the newest sensitivity change to `concept_id`: the page goes
    from `now` to `restores`, and `pages` are the derived pages raised with it,
    each with the level it ends at, never below what its remaining sources
    require."""

    concept_id: str
    title: str
    now: PageSensitivity
    restores: PageSensitivity
    pages: tuple[RestoredPage, ...]
    version: SensitivityUndoVersion


@dataclass(frozen=True)
class SensitivityUndone:
    """The change was taken back, in one commit. `restored` went back and `kept`
    stayed where they were because another page they cite requires it.
    `commit_sha` is `None` when the commit could not be made (the writes
    landed). `committed_confidential`: the commit holds a page explicitly marked
    confidential."""

    concept_id: str
    level: PageSensitivity
    restored: tuple[str, ...]
    kept: tuple[str, ...]
    commit_sha: str | None
    committed_confidential: bool = False


def preview_undo_sensitivity(
    workspace: Workspace, concept_id: str
) -> SensitivityUndoPreview | SensitivityNotUndoable:
    """What taking back the newest sensitivity change to `concept_id` would do,
    with the version to apply it against.

    The pages raised with the change are read back from the `log.md` line it
    wrote; a change that did not record them is `SensitivityNotUndoable`
    (`no_record`), never guessed at. Writes nothing, takes no lock, builds no
    client."""
    plan = undo_service.preview_undo(workspace.root, concept_id)
    if isinstance(plan, undo_service.UndoRefusal):
        return SensitivityNotUndoable(plan.reason)
    return SensitivityUndoPreview(
        concept_id=plan.concept_id,
        title=plan.title,
        now=plan.now,
        restores=plan.restores,
        pages=tuple(
            RestoredPage(p.concept_id, p.title, p.now, p.after) for p in plan.pages
        ),
        version=SensitivityUndoVersion(plan.concept_id, plan.content_digest),
    )


def apply_undo_sensitivity(
    workspace: Workspace,
    version: SensitivityUndoVersion,
    *,
    wait_seconds: float = 0.0,
    on_warning: Callable[[str], None] | None = None,
) -> SensitivityUndone | SensitivityNotUndoable | SensitivityStale:
    """Take back the change `version` was previewed for.

    The lock is taken once and held while the plan is made again and written. A
    page that changed since the preview or an altered version is the returned
    `SensitivityStale`; a change that can no longer be undone,
    `SensitivityNotUndoable`; neither writes anything. A derived page never ends
    below the level the pages it cites require. The page, the restored pages and
    a `log.md` line are written and committed together; a commit that cannot be
    made is reported once through `on_warning`. No derived store is refreshed."""
    wait = _wiring.validated_wait(wait_seconds)
    notes = _wiring.CommitNotes()
    result = undo_service.apply_undo(
        workspace.root,
        version.concept_id,
        version.content_digest,
        ports=_wiring.decision_ports(
            workspace,
            wait_seconds=wait,
            on_warning=on_warning,
            notes=notes,
            what="the sensitivity undo",
        ),
    )
    if isinstance(result, undo_service.UndoRefusal):
        return SensitivityNotUndoable(result.reason)
    if isinstance(result, undo_service.UndoStale):
        return SensitivityStale()
    return SensitivityUndone(
        concept_id=result.concept_id,
        level=result.level,
        restored=result.restored,
        kept=result.kept,
        commit_sha=result.commit_sha,
        committed_confidential=notes.confidential,
    )
