"""The `reindex` use case, as an application service (ADR-0018, issue #1168;
the second extraction after `ingest_service`).

`reindex_workspace` rebuilds the three derived stores -- `vectors.db`,
`fts.db` and `graph.db` -- from the compiled bundle of the workspace at an
explicit `root`. It returns a typed `ReindexOutcome` and raises typed
`ReindexRefused` subclasses; it never prompts, never renders, never reads the
current directory, and never raises `typer.Exit`. The CLI `reindex` verb is
one adapter over it; MVP 4's scheduled maintenance is meant to be another.

What it does NOT own, and how it reaches each through a parameter instead (the
layering invariant forbids importing `openkos.cli`, `typer`, `rich` or
`openkos.vcs` here):

* every word the user reads -- a `ReindexObserver` receives the typed
  report (and the two advisory hooks) and the adapter renders it. The
  summary reaches the observer BEFORE the graph write, never after: a graph
  failure must not swallow what `vectors.db`/`fts.db` already durably did;
* the concrete effects -- `ReindexPorts` carries the embedding-client
  factory, the vector-store opener, the proximity-source opener and the
  local-exemption resolution, so each stays a substitutable seam and the
  adapter's own (monkeypatchable) implementations stay adapter-side;
* the per-doc progress hook -- the observer decides whether there is one
  (the CLI gates it on a TTY).

The vectors/FTS orchestrator (`state.reindex.reindex`) owns the bundle walk,
the content-hash cache, the prune pass and the embedding-model gate; the graph
gate (`sqlite_graph.reindex_graph`) is called separately because the canonical
layer must not import `openkos.graph` -- this service is the seam that ties
the two together so one call still writes all three stores.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

from openkos import config
from openkos.application import backends as application_backends
from openkos.graph import proximity, sqlite_graph
from openkos.llm.base import (
    BackendDiagnostics,
    BackendEmbeddingDimensionMismatch,
    BackendError,
    BackendHostLocality,
    BackendModelNotFound,
    BackendUnavailable,
    Embedder,
)
from openkos.state import derived
from openkos.state import reindex as reindex_module
from openkos.state.fts import FtsUnavailable
from openkos.state.vectorstore import VectorStoreDB, VecUnavailable

LOCK_CONTENTION_TEMPLATE = (
    "openkos {command}: failed -- another OpenKOS process is using the "
    "workspace's derived stores; re-running is safe, so try again once it "
    "finishes."
)
"""The uniform derived-store contention message: `reindex`'s two error ladders
and the CLI's workspace-lock guard for every other verb all format it, so a
busy store always reads identically whichever store hit the busy timeout. It
names the verb the user ran and never a specific concurrent one."""

_VERB = "reindex"


# -- Typed refusals ---------------------------------------------------------


class ReindexRefused(Exception):
    """Base of every refusal the service raises. `message` is the complete,
    user-facing text (the wording the CLI has always printed), so an adapter
    renders it verbatim and maps the TYPE to an exit code. Deliberately not an
    `OSError`/`ValueError`/`sqlite3.Error`: the service's own `except`
    clauses must never swallow one of these."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NotAWorkspace(ReindexRefused):
    """`root` is not an OpenKOS workspace."""


class WorkspaceUnreadable(ReindexRefused):
    """Reading `openkos.yaml` raised `OSError`/`ValueError`."""


class BackendNotReachable(ReindexRefused):
    """The embedding backend could not be reached."""


class EmbeddingModelMissing(ReindexRefused):
    """The configured embedding model is not installed on the backend."""


class EmbeddingDimensionChanged(ReindexRefused):
    """The configured embedding model no longer yields `EMBED_DIM`-dimensional
    vectors. Permanent: the model must be restored in `openkos.yaml`."""


class IndexStoreUnavailable(ReindexRefused):
    """`sqlite-vec` or `fts5` is unusable, or any other backend failure that
    has no more specific refusal of its own."""


class LockContention(ReindexRefused):
    """Another process held a store's write lock past `busy_timeout`."""


class GraphWriteFailed(ReindexRefused):
    """Writing `graph.db` failed after `vectors.db`/`fts.db` had succeeded."""


# -- Inputs / outputs -------------------------------------------------------


class ReindexObserver(Protocol):
    """The adapter's window onto a run. Every method is optional in spirit:
    an adapter with nothing to show implements them as no-ops."""

    def embedder_ready(self, locality: BackendHostLocality, cfg: config.Config) -> None:
        """The embedding client exists; the adapter may warn that its host is
        not this machine. Called before any document is sent."""

    def progress_callback(self) -> Callable[[int, int, object], None] | None:
        """The per-document embedding progress hook, or `None` for silence."""

    def vectors_indexed(
        self,
        report: reindex_module.ReindexReport,
        previous_model_tag: str | None,
        cfg: config.Config,
    ) -> None:
        """`vectors.db`/`fts.db` are committed. Called BEFORE the graph write,
        with the model tag that was stored before this run overwrote it."""


@dataclass(frozen=True)
class ReindexPorts:
    """The effects the service takes from its caller."""

    embed_client: Callable[[config.Config], Embedder]
    open_vector_store: Callable[[Path], AbstractContextManager[VectorStoreDB]]
    open_proximity: Callable[[Path], proximity.VectorProximitySource | None]
    local_exemption: Callable[[application_backends.HasLocality, config.Config], bool]


@dataclass(frozen=True)
class ReindexOutcome:
    """A completed run: what the vectors/FTS pass did, and the model tag that
    was stored before it."""

    report: reindex_module.ReindexReport
    previous_model_tag: str | None


def _lock_contention() -> LockContention:
    return LockContention(LOCK_CONTENTION_TEMPLATE.format(command=_VERB))


def reindex_workspace(
    root: Path,
    *,
    force: bool,
    ports: ReindexPorts,
    observer: ReindexObserver,
) -> ReindexOutcome:
    """Rebuild the derived stores of the workspace at `root`.

    Order matters and is preserved from the CLI it was lifted out of: the
    workspace check, the config read, the vectors/FTS pass (with its ordered
    error ladder -- the specific backend refusals BEFORE the generic one,
    since both specific ones subclass `BackendError`), then the observer's
    summary, then the graph write with its own ladder. A non-lock
    `sqlite3.OperationalError` from the first pass is re-raised untouched.
    """
    reason = config.require_workspace(root)
    if reason is not None:
        raise NotAWorkspace(f"openkos {_VERB}: refusing to run -- {reason}.")

    layout = config.WorkspaceLayout(root)
    try:
        cfg = config.read_config(root)
    except (OSError, ValueError) as exc:
        raise WorkspaceUnreadable(
            f"openkos {_VERB}: failed while reading the workspace -- {exc}."
        ) from exc

    embedder = ports.embed_client(cfg)
    embedder_locality = cast(BackendDiagnostics, embedder)
    observer.embedder_ready(embedder_locality.locality, cfg)
    try:
        with ports.open_vector_store(layout.vectors_db_path) as db:
            # Captured BEFORE the call: `reindex()` may overwrite the stored
            # tag by the time it returns, and the summary names the OLD one.
            previous_model_tag = db.read_model_tag()
            report = reindex_module.reindex(
                layout.bundle_dir,
                db,
                embedder,
                force=force,
                fts_db_path=layout.fts_db_path,
                model_tag=cfg.embedding_model,
                embedding_backend=cfg.backend,
                on_progress=observer.progress_callback(),
                local_exemption=ports.local_exemption(embedder_locality, cfg),
            )
    except BackendUnavailable as exc:
        raise BackendNotReachable(
            f"openkos {_VERB}: failed -- {exc}. Start it with `ollama serve`, "
            f"then try again.{application_backends.DOCTOR_HINT}"
        ) from exc
    except BackendModelNotFound as exc:
        raise EmbeddingModelMissing(
            f"openkos {_VERB}: failed -- embedding model "
            f"'{cfg.embedding_model}' is not installed. Pull it with "
            f"`ollama pull {cfg.embedding_model}`, then try again."
        ) from exc
    # A PERMANENT misconfiguration with a concrete remediation; it subclasses
    # `BackendError`, so it must precede the generic clause below.
    except BackendEmbeddingDimensionMismatch as exc:
        raise EmbeddingDimensionChanged(
            f"openkos {_VERB}: failed -- {exc} Restore the working "
            "'embedding_model' value in openkos.yaml, then run `openkos "
            "reindex` again."
        ) from exc
    # A lock-contention error can surface at any write surface inside the
    # `with` (store open, upsert/commit, FTS's BEGIN IMMEDIATE); a non-lock
    # `OperationalError` is deliberately re-raised, not swallowed.
    except sqlite3.OperationalError as exc:
        if derived.is_lock_contention(exc):
            raise _lock_contention() from exc
        raise
    except (VecUnavailable, FtsUnavailable, BackendError) as exc:
        raise IndexStoreUnavailable(f"openkos {_VERB}: failed -- {exc}.") from exc

    # Reported BEFORE the graph write: `report` already reflects durably
    # committed work, and a graph failure must not hide it.
    observer.vectors_indexed(report, previous_model_tag, cfg)

    # graph.db is written by a SEPARATE call (canonical-layer code cannot
    # import `openkos.graph`), with its own ladder: its only failure mode is a
    # bare `sqlite3.Error`, and a locked graph.db reads exactly like the
    # other stores' lock contention.
    try:
        with_candidates = ports.open_proximity(layout.vectors_db_path)
        try:
            sqlite_graph.reindex_graph(
                layout.bundle_dir,
                layout.graph_db_path,
                force=force,
                candidates=with_candidates,
            )
        finally:
            if with_candidates is not None:
                with_candidates.close()
    except sqlite3.Error as exc:
        if isinstance(exc, sqlite3.OperationalError) and derived.is_lock_contention(
            exc
        ):
            raise _lock_contention() from exc
        raise GraphWriteFailed(
            f"openkos {_VERB}: failed while writing the graph index -- {exc}."
        ) from exc

    return ReindexOutcome(report=report, previous_model_tag=previous_model_tag)
