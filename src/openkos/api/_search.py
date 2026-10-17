"""Rebuilding search through the API (#1518): whether search needs rebuilding,
read without a model, and the rebuild itself, with consent, progress and a
stop.

The rebuild is the incremental `openkos reindex`: an unchanged page costs no
model call, and every page is embedded again when the AI model for search
changed. It sends page text to the configured AI model for search; when that
model is not on this machine it runs only with `allow_remote`, and never sends
a confidential page there. The workspace lock is held only while the stores
are written.
"""

from dataclasses import dataclass
from typing import Literal, Protocol

from openkos.api import _wiring
from openkos.api._workspace import Workspace, WorkspaceUnreadable
from openkos.application import lock_wait, reindex_service, setup
from openkos.application import search_rebuild as service
from openkos.application.runtime import StopToken
from openkos.application.search_rebuild import SearchUnavailable
from openkos.lock import WorkspaceBusyError

RebuildPhase = Literal["embedding", "finishing"]
"""`embedding`: pages are being sent to the AI model for search. `finishing`:
the lexical and graph indexes are being written."""

DEFAULT_REBUILD_WAIT_SECONDS = 30.0
"""How long the store write waits for the workspace lock by default."""


@dataclass(frozen=True)
class SearchIndex:
    """Whether search needs rebuilding.

    `model_changed`: the stored vectors were made by another AI model for
    search (or another backend). `stale_indexes`: the derived indexes whose
    contents predate the pages, by name. `needs_rebuild`: either holds.
    `sends_to`: the redacted host a rebuild would send page text to, when it
    is not this machine; `None` when it is."""

    model_changed: bool
    stale_indexes: tuple[str, ...]
    needs_rebuild: bool
    sends_to: str | None


@dataclass(frozen=True)
class RebuildProgress:
    """One step: during `embedding`, `done` of `total` pages to embed; during
    `finishing`, both are 0."""

    phase: RebuildPhase
    done: int
    total: int


class RebuildObserver(Protocol):
    """Receives the rebuild's progress, on the rebuilding thread."""

    def progress(self, event: RebuildProgress) -> None:
        """One step of the rebuild."""


@dataclass(frozen=True)
class RebuildOutcome:
    """A finished rebuild. `embedded`: pages sent and stored; `unchanged`:
    pages already current; `removed`: vectors of pages that no longer exist;
    `skipped`: pages that could not be read; `failed`: pages the AI model
    could not embed (a later rebuild tries them again); `withheld`:
    confidential pages not sent to an AI model that is not on this machine.
    `complete` is true exactly when nothing was skipped, failed or withheld;
    otherwise the stored vectors keep their old model's tag. After a model
    change `search_index` still reports `model_changed` unless `withheld` was
    the only thing left, which a rebuild could not send anyway."""

    embedded: int
    unchanged: int
    removed: int
    skipped: int
    failed: int
    withheld: int
    complete: bool


@dataclass(frozen=True)
class RebuildRefused:
    """`remote_not_allowed`: the AI model for search is not on this machine and
    `allow_remote` was false. Nothing was sent or written."""

    reason: Literal["remote_not_allowed"]


@dataclass(frozen=True)
class RebuildStopped:
    """The rebuild stopped before writing anything; search is as it was."""


def search_index(workspace: Workspace) -> SearchIndex:
    """Whether search needs rebuilding, and where a rebuild sends page text.

    Builds no client, takes no lock, creates no store and writes nothing.
    Raises `WorkspaceUnreadable` when `openkos.yaml` cannot be read."""
    try:
        state = service.search_state(workspace.root)
    except setup.SetupConfigUnreadable as exc:
        raise WorkspaceUnreadable(workspace.root, exc.reason) from exc
    return SearchIndex(
        model_changed=state.model_changed,
        stale_indexes=state.stale_indexes,
        needs_rebuild=state.needs_rebuild,
        sends_to=state.sends_to,
    )


class _Relay:
    def __init__(self, observer: RebuildObserver) -> None:
        self._observer = observer

    def progress(self, event: service.RebuildProgress) -> None:
        self._observer.progress(RebuildProgress(event.phase, event.done, event.total))


def rebuild_search(
    workspace: Workspace,
    *,
    allow_remote: bool = False,
    stop: StopToken | None = None,
    observer: RebuildObserver | None = None,
    wait_seconds: float = DEFAULT_REBUILD_WAIT_SECONDS,
) -> RebuildOutcome | RebuildRefused | RebuildStopped:
    """Rebuild search, sending only the pages that changed (all of them after
    a change of the AI model for search).

    Returns `RebuildRefused("remote_not_allowed")` before anything is sent when
    the AI model for search is not on this machine and `allow_remote` is
    false. A stop is observed after each page and always before the stores are
    written: a stopped rebuild returns `RebuildStopped` and writes nothing.
    The lock is taken only for the store writes, waiting at most
    `wait_seconds`; a busy workspace or store raises `WorkspaceBusyError`.
    Raises `BackendUnavailable`, `BackendModelNotFound` or
    `BackendEmbeddingDimensionMismatch` for the AI model, `SearchUnavailable`
    for a store, `WorkspaceUnreadable` for `openkos.yaml`, and `ValueError` for
    a `wait_seconds` outside `[0, 3600]`."""
    wait = _wiring.validated_wait(wait_seconds)
    try:
        result = service.rebuild_search(
            workspace.root,
            allow_remote=allow_remote,
            stop=stop if stop is not None else StopToken(),
            observer=_Relay(observer) if observer is not None else None,
            ports=service.RebuildPorts(
                embed_client=_wiring.default_embedder,
                commit_section=lock_wait.locked_commit_section(
                    workspace.root, wait_seconds=wait
                ),
            ),
        )
    except setup.SetupConfigUnreadable as exc:
        raise WorkspaceUnreadable(workspace.root, exc.reason) from exc
    except reindex_service.ReindexRefused as exc:
        raise _mapped(workspace, exc) from exc
    if isinstance(result, service.RebuildRefused):
        return RebuildRefused(result.reason)
    if isinstance(result, service.RebuildStopped):
        return RebuildStopped()
    return RebuildOutcome(
        embedded=result.embedded,
        unchanged=result.unchanged,
        removed=result.removed,
        skipped=result.skipped,
        failed=result.failed,
        withheld=result.withheld,
        complete=result.complete,
    )


def _mapped(workspace: Workspace, exc: reindex_service.ReindexRefused) -> Exception:
    """The API's own error for a reindex refusal: the backend error that caused
    it, the busy workspace, the unreadable workspace, or `SearchUnavailable`."""
    cause = exc.__cause__
    if isinstance(
        exc,
        reindex_service.BackendNotReachable
        | reindex_service.EmbeddingModelMissing
        | reindex_service.EmbeddingDimensionChanged,
    ) and isinstance(cause, Exception):
        return cause
    if isinstance(exc, reindex_service.LockContention):
        return WorkspaceBusyError(
            "another OpenKOS process is using the search stores; try again"
        )
    if isinstance(
        exc, reindex_service.WorkspaceUnreadable | reindex_service.NotAWorkspace
    ):
        return WorkspaceUnreadable(workspace.root, exc.message)
    return SearchUnavailable(exc.message)
