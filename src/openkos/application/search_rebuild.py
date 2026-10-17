"""Rebuilding search from a client with no terminal (#1518).

`search_state` is a model-free read of whether search needs rebuilding: the
stored vectors carry another AI model for search's tag, or the lexical and
graph indexes predate the pages. `rebuild_search` runs the incremental rebuild
`openkos reindex` runs, over the same service (`reindex_service`), with three
things a terminal does not need:

* consent: when the embedding client is not verifiably on this machine, the
  run refuses unless the caller passed `allow_remote` -- checked on the built
  client's own verdict, before any page is sent. Confidential pages are
  withheld from such a client whatever the consent (the reindex rule);
* a stop, observed after each embedded page and therefore always before the
  store write: the vectors are committed once at the end of a run, so a
  stopped run leaves search exactly as it was;
* the lock only around the store writes (ADR-0036), through the service's
  `commit_section` port, so a long embedding run never blocks a write.
"""

import os
import sqlite3
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from openkos import config
from openkos.application import backends, model_settings, reindex_service, setup
from openkos.application import status as application_status
from openkos.application.runtime import StopToken
from openkos.errors import OpenKosError
from openkos.graph import proximity
from openkos.llm.base import BackendHostLocality, Embedder
from openkos.state import reindex as reindex_module
from openkos.state import vectorstore


class SearchUnavailable(OpenKosError):
    """A search store cannot be used or written: the vector or lexical store is
    unusable, or the graph index could not be written."""


@dataclass(frozen=True)
class SearchState:
    model_changed: bool
    stale_indexes: tuple[str, ...]
    needs_rebuild: bool
    sends_to: str | None


def search_state(root: Path, *, environ: Mapping[str, str] = os.environ) -> SearchState:
    """Whether search needs rebuilding, and where a rebuild would send text.

    Builds no client, takes no lock, creates no store and writes nothing.
    Raises `setup.SetupConfigUnreadable` for an unreadable `openkos.yaml`."""
    cfg = setup.read_setup_config(root)
    layout = config.WorkspaceLayout(root)
    stored = vectorstore.read_stored_model_tag(layout.vectors_db_path)
    model_changed = stored is not None and stored != reindex_module.embedding_tag(
        cfg.embedding_model, cfg.backend
    )
    stale = application_status.stale_index_names(layout, reads=("fts", "graph"))
    return SearchState(
        model_changed=model_changed,
        stale_indexes=stale,
        needs_rebuild=model_changed or bool(stale),
        sends_to=model_settings.remote_host(cfg, "embed", environ),
    )


RebuildPhase = Literal["embedding", "finishing"]


@dataclass(frozen=True)
class RebuildProgress:
    phase: RebuildPhase
    done: int
    total: int


class RebuildObserver(Protocol):
    def progress(self, event: RebuildProgress) -> None:
        """One step of the rebuild."""


@dataclass(frozen=True)
class RebuildOutcome:
    embedded: int
    unchanged: int
    removed: int
    skipped: int
    failed: int
    withheld: int
    complete: bool


@dataclass(frozen=True)
class RebuildRefused:
    reason: Literal["remote_not_allowed"]


@dataclass(frozen=True)
class RebuildStopped:
    """The run stopped before writing anything."""


class _Stop(Exception):
    """Unwinds the run from the progress hook, before the store write."""


class _Remote(Exception):
    """The built client is not on this machine and no consent was given."""


@dataclass(frozen=True)
class RebuildPorts:
    embed_client: Callable[[config.Config], Embedder]
    commit_section: Callable[[], AbstractContextManager[None]]


class _Observer:
    def __init__(
        self,
        *,
        allow_remote: bool,
        stop: StopToken,
        observer: RebuildObserver | None,
    ) -> None:
        self._allow_remote = allow_remote
        self._stop = stop
        self._observer = observer

    def _emit(self, event: RebuildProgress) -> None:
        if self._observer is not None:
            self._observer.progress(event)

    def embedder_ready(self, locality: BackendHostLocality, cfg: config.Config) -> None:
        # Before any page is sent: the consent check on the client's own
        # verdict, then the first stop check (the run's admission point).
        if not locality.is_local and not self._allow_remote:
            raise _Remote
        if self._stop.is_set():
            raise _Stop

    def progress_callback(self) -> Callable[[int, int, object], None]:
        def on_progress(done: int, total: int, concept_id: object) -> None:
            self._emit(RebuildProgress("embedding", done, total))
            if self._stop.is_set():
                raise _Stop

        return on_progress

    def vectors_indexed(
        self,
        report: reindex_module.ReindexReport,
        previous_model_tag: str | None,
        cfg: config.Config,
    ) -> None:
        self._emit(RebuildProgress("finishing", 0, 0))


def rebuild_search(
    root: Path,
    *,
    allow_remote: bool,
    stop: StopToken,
    observer: RebuildObserver | None,
    ports: RebuildPorts,
    environ: Mapping[str, str] = os.environ,
) -> RebuildOutcome | RebuildRefused | RebuildStopped:
    """Rebuild the derived stores of the workspace at `root`, incrementally.

    Raises what `reindex_service.reindex_workspace` raises; the caller maps
    its typed refusals."""
    if not allow_remote:
        cfg = setup.read_setup_config(root)
        if model_settings.remote_host(cfg, "embed", environ) is not None:
            return RebuildRefused("remote_not_allowed")
    try:
        outcome = reindex_service.reindex_workspace(
            root,
            force=False,
            ports=reindex_service.ReindexPorts(
                embed_client=ports.embed_client,
                open_vector_store=vectorstore.open_vector_store,
                open_proximity=proximity.open_proximity_source,
                local_exemption=backends.resolve_local_exemption,
                commit_section=ports.commit_section,
            ),
            observer=_Observer(allow_remote=allow_remote, stop=stop, observer=observer),
        )
    except _Remote:
        return RebuildRefused("remote_not_allowed")
    except _Stop:
        return RebuildStopped()
    except sqlite3.Error as exc:
        # The service re-raises a store error that is not lock contention
        # untouched, and a store that is not a database at all.
        raise SearchUnavailable(f"a search store cannot be used: {exc}") from exc
    report = outcome.report
    withheld = report.withheld_confidential
    return RebuildOutcome(
        embedded=report.embedded,
        unchanged=report.cache_hits,
        removed=report.pruned,
        skipped=report.skipped,
        failed=report.embed_failed,
        withheld=withheld,
        complete=report.skipped == 0 and report.embed_failed == 0 and withheld == 0,
    )
