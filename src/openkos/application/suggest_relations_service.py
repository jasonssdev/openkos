"""The `suggest-relations` use case, as an application service (ADR-0018,
issue #1168; the findings slice after `ingest_service` and `reindex_service`).

`suggest_relations` types every untyped body-link edge of the workspace at an
explicit `root` with the model, and `apply_relation_suggestions` walks the
suggestions and writes each accepted relation. Both return typed outcomes and
raise typed `SuggestionRefused` subclasses; neither prompts, renders, reads the
current directory, calls `sys.stdin.isatty()` or raises `typer.Exit`. The CLI
`suggest-relations` verb is one adapter over them; MVP 4's scheduled
maintenance is meant to be another.

What the service does NOT own, and how it reaches each through a parameter
instead (the layering invariant forbids importing `openkos.cli`, `typer`,
`rich` or `openkos.vcs` here):

* every word the user reads -- a `SuggestRelationsObserver` receives typed
  data (the notices, the cost quote, the progress, the summary) and the
  adapter renders it;
* the cost question and each per-item consent question -- the observer
  answers them (`confirm_cost`, `confirm_relate`); the service owns only WHEN
  they apply;
* the concrete effects -- `SuggestRelationsPorts` carries the chat-client
  factory, the proximity-source opener, the graph build, the candidate and
  typing seams, the auto-commit and the derived-store refresh, so each stays a
  substitutable seam and the adapter's own (monkeypatchable) implementations
  stay adapter-side.

This module also owns the persisted-suggestion serve partition, the persist
step and the reassembly (`partition_edge_suggestion_serves`,
`persist_edge_suggestions`, `reassemble_edge_suggestions`), which `curate`'s
Structure stage shares: their advisories reach the caller as a message through
`on_warning`, never as printed text.
"""

from __future__ import annotations

import contextlib
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, NamedTuple, Protocol, cast

from openkos import config, lock
from openkos.application import backends as application_backends
from openkos.application import drift as application_drift
from openkos.application import lifecycle as application_lifecycle
from openkos.application import pending as application_pending
from openkos.application.lock_wait import CommitSection
from openkos.graph import proximity, sqlite_graph
from openkos.graph.base import Edge, GraphStore
from openkos.llm.base import (
    BackendError,
    BackendModelNotFound,
    BackendUnavailable,
    LLMBackend,
)
from openkos.model import okf
from openkos.model.relations import ASYMMETRIC_RELATION_TYPES, validate_relation_type
from openkos.resolution import edge_typing
from openkos.resolution.edge_typing import (
    LEAST_SPECIFIC_RELATION_TYPE,
    EdgeSuggestion,
    EdgeSuggestionBatch,
    corrected_edge_from_rationale,
)
from openkos.state import derived
from openkos.state import edge_suggestions as edge_suggestions_store

DOCTOR_HINT = application_backends.DOCTOR_HINT
"""The remediation clause appended to a `BackendUnavailable` message."""

_VERB = "suggest-relations"
_APPLY_VERB = "suggest-relations --apply"


# -- Typed refusals ---------------------------------------------------------


class SuggestionRefused(Exception):
    """Base of every refusal the suggestion services raise. `message` is the
    complete, user-facing text (the wording the CLI has always printed), so an
    adapter renders it verbatim and maps the TYPE to an exit code. Deliberately
    not an `OSError`/`ValueError`: the services' own `except` clauses must
    never swallow one of these."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NotAWorkspace(SuggestionRefused):
    """`root` is not an OpenKOS workspace."""


class WorkspaceUnreadable(SuggestionRefused):
    """Reading `openkos.yaml` raised `OSError`/`ValueError`."""


class BackendNotReachable(SuggestionRefused):
    """The model backend could not be reached."""


class ModelNotInstalled(SuggestionRefused):
    """The configured model is not installed on the backend."""


class BackendFailed(SuggestionRefused):
    """Any other backend failure."""


class RelateFailed(SuggestionRefused):
    """Preparing or writing one accepted relation raised `OSError`/
    `ValueError`; relations accepted earlier stay committed."""


class DriftDetected(SuggestionRefused):
    """A write target changed on disk after the plan was computed from it;
    nothing more was written. The one refusal a script may retry (exit 3)."""


def workspace_refusal(verb: str, reason: str) -> NotAWorkspace:
    return NotAWorkspace(f"openkos {verb}: refusing to run -- {reason}.")


def unreadable_refusal(verb: str, exc: Exception) -> WorkspaceUnreadable:
    return WorkspaceUnreadable(
        f"openkos {verb}: failed while reading the workspace -- {exc}."
    )


def batch_failure_message(
    context: str,
    failure: BaseException | None,
    model: str,
    cfg: config.Config | None = None,
) -> str:
    """The one stderr line for a partial batch (#441): the same 3-tier
    cause-specific wording the raise-path handlers use, prefixed with `context`
    (how much paid-for work survived). The `isinstance` dispatch mirrors the
    handlers' ORDER: both specific classes subclass `BackendError`, so the
    generic branch must come last or their remediation is lost."""
    if isinstance(failure, BackendUnavailable):
        return (
            f"{context} -- {failure}. {application_backends.start_hint(cfg)}, "
            f"then try again.{DOCTOR_HINT}"
        )
    if isinstance(failure, BackendModelNotFound):
        return (
            f"{context} -- model '{model}' is not installed. "
            f"{application_backends.install_hint(cfg, model)}, then try again."
        )
    return f"{context} -- {failure}."


def relations_batch_failure_message(
    batch: EdgeSuggestionBatch,
    *,
    total: int,
    model: str,
    cfg: config.Config | None = None,
) -> str:
    context = (
        f"openkos {_VERB}: failed after suggesting "
        f"{len(batch.results)} of {total} untyped edge(s)"
    )
    return batch_failure_message(context, batch.failure, model, cfg)


# -- Inputs / ports / observer ----------------------------------------------


@dataclass(frozen=True)
class SuggestRelationsRequest:
    """The caller's choices for one run."""

    include_confidential: bool = False
    fresh: bool = False
    edge_offset: int = 0
    skip_confirmation: bool = False
    """`--auto`: skip the cost question."""


@dataclass(frozen=True)
class CostQuote:
    """What the cost question states (#134, #799): `to_type` edges cost one
    inference each; `served` cost nothing."""

    total: int
    served: int
    to_type: int


class SuggestRelationsObserver(Protocol):
    """The adapter's window onto a run."""

    def walk_incomplete(
        self, bundle_dir: Path, *, include_confidential: bool, local_exemption: bool
    ) -> None:
        """Called once the local exemption is known, before any candidate is
        computed, so the adapter may warn that the bundle scan was incomplete."""

    def workspace_header(self, root: Path) -> None:
        """The candidates are computed; the report begins."""

    def empty_window(self, edge_offset: int) -> None:
        """`--edge-offset` sat at or past the candidate set."""

    def candidate_notices(self, truncation: str | None, quarantine: str | None) -> None:
        """The cap-truncation and unjudged-source notices, each disclosed
        before the spend, only when present."""

    def no_candidates(self, message: str) -> None:
        """Nothing is left to type; `message` says why."""

    def warn(self, message: str) -> None:
        """An advisory the run survives (a persisted-suggestion read or write
        failed)."""

    def serve_split(self, served: int, total: int, fresh: int) -> None:
        """The persisted store was READ: `served` of `total` need no model."""

    def confirm_cost(self, quote: CostQuote) -> bool:
        """State the cost and ask whether to proceed."""

    def edge_progress(self, index: int, count: int, suggestion: EdgeSuggestion) -> None:
        """One edge was typed."""


class ApplyObserver(Protocol):
    """The adapter's window onto the `--apply` walk."""

    def degraded(self, edge: Edge) -> None:
        """No valid type was suggested for `edge`; it is skipped unprompted."""

    def preview(
        self, edge: Edge, suggested_type: str, caveat: str, rationale: str
    ) -> None:
        """One valid suggestion, before the consent question."""

    def confirm_relate(self, edge: Edge, suggested_type: str, caveat: str) -> bool:
        """Ask whether to write this relation."""

    def already_present(self) -> None:
        """The relation already exists; nothing is written."""

    def summary(self, outcome: ApplyOutcome) -> None:
        """The walk finished (before the derived stores are refreshed)."""


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class SuggestRelationsPorts:
    """The run's effects. Required fields are the ones only an adapter can
    supply; the rest default to the real implementation so a second adapter
    need not name them."""

    chat_client: Callable[[config.Config, str | None], LLMBackend]
    zero_state_message: Callable[[config.WorkspaceLayout, GraphStore, bool], str]
    """The three-state message for a zero-candidate outcome, given the store
    already open and whether embeddings are missing."""
    autocommit: Callable[[Path, Sequence[str], str], str | None]
    refresh_derived: Callable[[config.WorkspaceLayout], object]
    resolve_local_exemption: Callable[
        [application_backends.HasLocality, config.Config], bool
    ] = application_backends.resolve_local_exemption
    open_proximity: Callable[[Path], proximity.VectorProximitySource | None] = (
        proximity.open_proximity_source
    )
    build_graph: Callable[..., sqlite_graph.SqliteGraphStore] = sqlite_graph.build_graph
    candidate_edges: Callable[..., list[Edge]] = edge_typing.candidate_edges
    suggest_edge_types: Callable[..., EdgeSuggestionBatch] = (
        edge_typing.suggest_edge_types
    )
    now: Callable[[], datetime] = _utc_now
    commit_section: CommitSection = contextlib.nullcontext
    """Entered around each commit phase -- the persist of the suggestions and
    each accepted relation's prepare, drift check, write and auto-commit --
    and nothing before it: the cost prompt, the model calls and the per-item
    prompts hold no workspace lock (#1137, ADR-0036). An adapter that owns the
    lock passes a section that takes it; the default holds nothing."""


@dataclass(frozen=True)
class SuggestRelationsOutcome:
    """One run's result. `status` is how far it got; `results` are in
    CANDIDATE order (served and fresh suggestions indistinguishable)."""

    status: Literal["empty_window", "no_candidates", "declined", "completed"]
    results: tuple[EdgeSuggestion, ...] = ()
    total: int = 0
    truncation_notice: str | None = None
    next_offset: int | None = None
    failure: BackendError | None = None
    model: str = ""
    batch: EdgeSuggestionBatch | None = None
    cfg: config.Config | None = None
    """The run's config, so a partial-batch message words the backend."""


@dataclass(frozen=True)
class ApplyOutcome:
    applied: int
    skipped: int
    declined: tuple[str, ...]

    @property
    def nothing_to_apply(self) -> bool:
        return self.applied == 0 and self.skipped == 0


# -- The suggestion caveat --------------------------------------------------


def suggestion_caveat(suggested_type: str) -> str:
    """What a suggested type does NOT establish, spelled ONCE (#778).

    Two caveats, one helper, because they are answered at the same moment and
    by the same three surfaces -- `suggest-relations`' listing, its `--apply`
    preview and prompt, and `curate`'s Structure stage.

    - **Asymmetric types** carry #624's direction caveat, reproduced
      byte-for-byte (`docs/testing.md` documents it as the contract).
    - **The least-specific type** carries #802's: "the two are connected, and
      the documents do not support saying how".

    A single `if/elif` is safe because the two classes are disjoint -- the
    least-specific type is symmetric -- and a test pins that."""
    if suggested_type in ASYMMETRIC_RELATION_TYPES:
        return " (direction model-suggested, unverified)"
    if suggested_type == LEAST_SPECIFIC_RELATION_TYPE:
        return " (connected; the documents do not say how)"
    return ""


# -- Persisted suggestions (shared with curate's Structure stage) -----------


class EdgeSuggestionServes(NamedTuple):
    """What `partition_edge_suggestion_serves` answers (#809).

    `store_read` is the third value because the count alone cannot carry the
    difference between "looked, found nothing" and "could not look". Both
    produce an empty `served`, and only one is worth reporting as a split."""

    served: dict[str, EdgeSuggestion]
    to_type: list[Edge]
    store_read: bool


def reassemble_edge_suggestions(
    edges: list[Edge],
    served: dict[str, EdgeSuggestion],
    fresh: Sequence[EdgeSuggestion],
) -> list[EdgeSuggestion]:
    """Rebuild a run's suggestions in CANDIDATE order, merging what the store
    served with what the model just typed (#809).

    A served suggestion wins over a fresh one for the same key; a key can only
    appear in both when the partition served it AND the model typed it anyway,
    which the callers' own flow makes impossible, so the precedence is a
    tiebreak that should never fire.

    Keyed on `result.edge`, deliberately NOT `effective_edge` (#991): `edges`
    is always in CANDIDATE direction, and a direction-corrected fresh result's
    `edge` stays the candidate too -- only `corrected_edge` differs. Keying on
    `effective_edge` would silently drop every corrected suggestion."""
    fresh_by_key = {
        edge_suggestions_store.pair_key_for(
            result.edge.source_id, result.edge.target_id
        ): result
        for result in fresh
    }
    rebuilt: list[EdgeSuggestion] = []
    for edge in edges:
        key = edge_suggestions_store.pair_key_for(edge.source_id, edge.target_id)
        found = served.get(key) or fresh_by_key.get(key)
        if found is not None:
            rebuilt.append(found)
    return rebuilt


def partition_edge_suggestion_serves(
    layout: config.WorkspaceLayout,
    edges: list[Edge],
    *,
    include_confidential: bool,
    on_warning: Callable[[str], None] | None = None,
    surface: str = _VERB,
) -> EdgeSuggestionServes:
    """Split `edges` into suggestions servable from `.openkos/findings.db` and
    the edges that still need a model call (#799).

    An edge is SERVED iff its latest persisted row matches this run's EFFECTIVE
    `include_confidential` bit, carries a digest row for BOTH current endpoints
    and no others, every stored digest equals the endpoint's CURRENT content
    hash, and the stored type still validates. Everything else re-types,
    conservatively -- including a present-but-corrupt store, which degrades to
    one advisory (through `on_warning`; `None` is curate's pricing probe, which
    stays silent) and a full fresh run.

    The key is DIRECTED: half the vocabulary is asymmetric, so `a -> b` and
    `b -> a` are different questions and one must never answer for the other."""
    if not layout.findings_db_path.exists():
        return EdgeSuggestionServes({}, edges, store_read=False)
    try:
        conn = derived.open_derived_connection(layout.findings_db_path)
        try:
            persisted = edge_suggestions_store.open_edge_suggestions(conn)
        finally:
            conn.close()
    except (OSError, sqlite3.Error) as exc:
        if on_warning is not None:
            on_warning(
                f"openkos {surface}: warning -- failed to read persisted "
                f"suggestions ({exc}); typing every edge fresh."
            )
        return EdgeSuggestionServes({}, edges, store_read=False)
    latest: dict[str, edge_suggestions_store.PersistedEdgeSuggestion] = {}
    for row in persisted:
        latest[edge_suggestions_store.pair_key_for(row.source_id, row.target_id)] = row

    current_digest = application_pending.current_finding_digest(layout.bundle_dir)
    served: dict[str, EdgeSuggestion] = {}
    to_type: list[Edge] = []
    for edge in edges:
        key = edge_suggestions_store.pair_key_for(edge.source_id, edge.target_id)
        stored = latest.get(key)
        if stored is None or stored.include_confidential != include_confidential:
            to_type.append(edge)
            continue
        endpoints = {edge.source_id, edge.target_id}
        if {digest.input_ref for digest in stored.input_digests} != endpoints or any(
            current_digest(digest.input_ref) != digest.digest
            for digest in stored.input_digests
        ):
            to_type.append(edge)
            continue
        try:
            validate_relation_type(stored.suggested_type)
        except ValueError:
            to_type.append(edge)
            continue
        # #991 R4: reconstructed from the PERSISTED rationale, never by
        # re-reading the bundle -- a re-read can fail transiently and disagree
        # with the disclosure already frozen at fresh time.
        served[key] = EdgeSuggestion(
            edge=edge,
            suggested_type=stored.suggested_type,
            rationale=stored.rationale,
            corrected_edge=corrected_edge_from_rationale(edge, stored.rationale),
        )
    return EdgeSuggestionServes(served, to_type, store_read=True)


def persist_edge_suggestions(
    layout: config.WorkspaceLayout,
    results: Sequence[EdgeSuggestion],
    *,
    include_confidential: bool,
    on_warning: Callable[[str], None] | None = None,
    surface: str = _VERB,
    judged_digests: Mapping[str, str | None] | None = None,
    commit_section: CommitSection = contextlib.nullcontext,
) -> None:
    """Persist freshly computed edge-typing suggestions (#799), fail-open: a
    failed persist costs one advisory, never the run.

    `judged_digests` is each endpoint's content digest as it stood BEFORE the
    typing call (#1137): that call holds no workspace lock, so an endpoint
    edited or forgotten meanwhile has a different digest now, and its
    suggestion is dropped rather than stored against content nobody typed.
    Without it (curate, which holds the lock throughout) the digests are read
    here, as before. The persist runs inside `commit_section`; a busy
    workspace costs the same advisory a failed persist does.

    Two results are skipped rather than stored. A `suggested_type` of `None` is
    the fail-closed degrade (a FAILURE, not a verdict: caching it would never
    retry). An endpoint with no current digest (unreadable) is skipped because
    a row whose staleness can never be checked would serve forever."""
    if not results:
        return
    try:
        with commit_section():
            current_digest = application_pending.current_finding_digest(
                layout.bundle_dir
            )
            batch: list[edge_suggestions_store.PersistedEdgeSuggestion] = []
            for result in results:
                if result.suggested_type is None:
                    continue
                # `result.edge`, deliberately NOT `effective_edge` (#991): the row is
                # keyed on the CANDIDATE pair -- the question that was asked -- never
                # on a direction-corrected one.
                edge = result.edge
                digests: list[edge_suggestions_store.InputDigest] = []
                for endpoint_id in (edge.source_id, edge.target_id):
                    digest = current_digest(endpoint_id)
                    if digest is None or (
                        judged_digests is not None
                        and judged_digests.get(endpoint_id) != digest
                    ):
                        break
                    digests.append(
                        edge_suggestions_store.InputDigest(
                            input_ref=endpoint_id, digest=digest
                        )
                    )
                else:
                    batch.append(
                        edge_suggestions_store.PersistedEdgeSuggestion(
                            source_id=edge.source_id,
                            target_id=edge.target_id,
                            suggested_type=result.suggested_type,
                            rationale=result.rationale,
                            include_confidential=include_confidential,
                            input_digests=tuple(digests),
                        )
                    )
            if not batch:
                return
            try:
                conn = derived.open_derived_connection(layout.findings_db_path)
                try:
                    edge_suggestions_store.record_edge_suggestions(conn, batch)
                finally:
                    conn.close()
            except (OSError, sqlite3.Error) as exc:
                if on_warning is not None:
                    on_warning(
                        f"openkos {surface}: warning -- failed to persist edge "
                        f"suggestions ({exc}); the next run will re-type them."
                    )
    except lock.WorkspaceBusyError as exc:
        if on_warning is not None:
            on_warning(
                f"openkos {surface}: warning -- the workspace is busy, so the "
                f"edge suggestions were not persisted ({exc}); the next run "
                "will re-type them."
            )


# -- The run ----------------------------------------------------------------


def suggest_relations(
    root: Path,
    request: SuggestRelationsRequest,
    ports: SuggestRelationsPorts,
    observer: SuggestRelationsObserver,
) -> SuggestRelationsOutcome:
    """Type every untyped candidate edge of the workspace at `root`.

    Read-only over the bundle: the one thing it writes is the persisted
    suggestions in `.openkos/findings.db`. The candidates are counted FIRST,
    with no model call, so the cost of the one-inference-per-edge run can be
    quoted and answered before the model is ever contacted (#134); the serve
    partition (#799) runs before the quote, so it states the calls this run
    will ACTUALLY make. A no-model failure comes back INSIDE the returned
    batch (#441) and is carried on the outcome: the completed suggestions are
    never discarded. The raise-path ladder is retained around the call for an
    injected backend that raises outside `llm.chat`'s guarded seam."""
    reason = config.require_workspace(root)
    if reason is not None:
        raise workspace_refusal(_VERB, reason)

    layout = config.WorkspaceLayout(root)
    try:
        cfg = config.read_config(root)
    except (OSError, ValueError) as exc:
        raise unreadable_refusal(_VERB, exc) from exc

    # Built before the candidate count: `candidate_edges` filters on
    # sensitivity, so the confidential local exemption must be resolved from
    # the SAME client the typing run later sends through (#240). Construction
    # performs no I/O.
    llm = ports.chat_client(cfg, "edge_typing")
    local_exemption = ports.resolve_local_exemption(
        cast(application_backends.HasLocality, llm), cfg
    )
    observer.walk_incomplete(
        layout.bundle_dir,
        include_confidential=request.include_confidential,
        local_exemption=local_exemption,
    )

    # graph-projection-reuse (#196): the proximity source is closed as early as
    # possible -- `build_graph` consumes it eagerly -- and the projection is
    # built exactly ONCE per run, threaded via `store=` into both
    # `candidate_edges` and the zero-result state message.
    source = ports.open_proximity(layout.vectors_db_path)
    embeddings_missing = source is None
    try:
        graph = ports.build_graph(
            layout.bundle_dir, candidates=source, candidate_offset=request.edge_offset
        )
    finally:
        if source is not None:
            source.close()

    with graph as store:
        edges = ports.candidate_edges(
            layout.bundle_dir,
            include_confidential=request.include_confidential,
            local_exemption=local_exemption,
            store=store,
        )

        observer.workspace_header(root)
        # #567: an offset at or past the candidate set produced an empty window
        # on purpose -- say so, instead of the zero-candidate message claiming
        # there is nothing untyped at all.
        if request.edge_offset > 0 and not edges:
            observer.empty_window(request.edge_offset)
            return SuggestRelationsOutcome(status="empty_window", model=cfg.model)
        # #378 slice 2: pass 3's cap truncation, never silent -- but restricted
        # to what THIS caller may see, re-derived through the same sensitivity
        # walk `candidate_edges` just ran. Read INSIDE the `with` block, since
        # `store` closes below.
        truncation = edge_typing.candidate_truncation_notice(
            store.candidate_report,
            layout.bundle_dir,
            include_confidential=request.include_confidential,
            local_exemption=local_exemption,
        )
        # #841: the unjudged-source withholding, disclosed beside the cap.
        quarantine = edge_typing.quarantined_candidate_notice(
            store.candidate_report,
            layout.bundle_dir,
            include_confidential=request.include_confidential,
            local_exemption=local_exemption,
        )
        # #567: computed inside the `with` block (the report lives on `store`).
        next_offset = edge_typing.next_candidate_offset(
            store.candidate_report,
            layout.bundle_dir,
            include_confidential=request.include_confidential,
            local_exemption=local_exemption,
        )
        observer.candidate_notices(truncation, quarantine)
        total = len(edges)
        if total == 0:
            observer.no_candidates(
                ports.zero_state_message(layout, store, embeddings_missing)
            )
            return SuggestRelationsOutcome(
                status="no_candidates",
                truncation_notice=truncation,
                next_offset=next_offset,
                model=cfg.model,
            )

    # Everything from here on runs OUTSIDE the `with` block: the minutes-long
    # model run stays out of the store's lifetime.
    #
    # #799: the serve partition runs BEFORE the cost quote. It keys on the
    # EFFECTIVE confidential inclusion (`--include-confidential` OR the
    # verified local-backend exemption, the same disjunction
    # `sensitivity.should_block` applies).
    effective_confidential = request.include_confidential or local_exemption
    served_by_key: dict[str, EdgeSuggestion] = {}
    to_type = edges
    # The split is reported when a store was actually READ (#809), not merely
    # present: a count of zero meaning "could not look" must not be rendered in
    # the words of a count meaning "looked, found nothing".
    store_read = False
    if not request.fresh:
        served_by_key, to_type, store_read = partition_edge_suggestion_serves(
            layout,
            edges,
            include_confidential=effective_confidential,
            on_warning=observer.warn,
        )
    if store_read:
        observer.serve_split(len(served_by_key), total, len(to_type))

    if not request.skip_confirmation and not observer.confirm_cost(
        CostQuote(total=total, served=len(served_by_key), to_type=len(to_type))
    ):
        return SuggestRelationsOutcome(
            status="declined",
            total=total,
            truncation_notice=truncation,
            next_offset=next_offset,
            model=cfg.model,
        )

    # #1137: the typing call below holds no workspace lock, so pin each
    # endpoint's digest BEFORE it -- the persist keeps a suggestion only for
    # content that is still what was typed.
    digest_of = application_pending.current_finding_digest(layout.bundle_dir)
    judged_digests = {
        endpoint_id: digest_of(endpoint_id)
        for edge in to_type
        for endpoint_id in (edge.source_id, edge.target_id)
    }
    try:
        # Still called with an empty `to_type` (a fully-served run): zero edges
        # means zero `llm.chat` calls by construction.
        batch = ports.suggest_edge_types(
            to_type,
            bundle_dir=layout.bundle_dir,
            llm=llm,
            include_confidential=request.include_confidential,
            local_exemption=local_exemption,
            # #812: the same workspace key `curate`'s Structure stage reads --
            # this verb and that stage run one suggester. `None` on a workspace
            # that never set it: the pre-#812 prompt, byte for byte.
            rationale_language=cfg.rationale_language,
            on_progress=observer.edge_progress,
        )
    except BackendUnavailable as exc:
        raise BackendNotReachable(
            f"openkos {_VERB}: failed -- {exc}. "
            f"{application_backends.start_hint(cfg)}, "
            f"then try again.{DOCTOR_HINT}"
        ) from exc
    except BackendModelNotFound as exc:
        raise ModelNotInstalled(
            f"openkos {_VERB}: failed -- model '{cfg.model}' is "
            f"not installed. {application_backends.install_hint(cfg, cfg.model)}, "
            "then try again."
        ) from exc
    # The two specific handlers above MUST precede this generic handler: both
    # subclass `BackendError`, so reordering would silently funnel them into
    # this fallback and lose their actionable remediation messages.
    except BackendError as exc:
        raise BackendFailed(f"openkos {_VERB}: failed -- {exc}.") from exc

    # #799: fresh suggestions persist even on a partial batch (the paid-for work
    # is kept, mirroring #441's own posture), then the run's results are rebuilt
    # in CANDIDATE order so a served suggestion and a fresh one are
    # indistinguishable downstream.
    persist_edge_suggestions(
        layout,
        batch.results,
        include_confidential=effective_confidential,
        on_warning=observer.warn,
        judged_digests=judged_digests,
        commit_section=ports.commit_section,
    )
    results: list[EdgeSuggestion] = (
        reassemble_edge_suggestions(edges, served_by_key, batch.results)
        if served_by_key
        else list(batch.results)
    )
    return SuggestRelationsOutcome(
        status="completed",
        results=tuple(results),
        total=total,
        truncation_notice=truncation,
        next_offset=next_offset,
        failure=batch.failure,
        model=cfg.model,
        batch=batch,
        cfg=cfg,
    )


# -- --apply ----------------------------------------------------------------


def apply_relation_suggestions(
    root: Path,
    results: Sequence[EdgeSuggestion],
    ports: SuggestRelationsPorts,
    observer: ApplyObserver,
) -> ApplyOutcome:
    """The interactive `--apply` walk (#560): per VALID suggestion, preview it,
    ask consent through the observer, and on yes write through the exact
    `prepare_relate` -> drift check -> `relate_core` -> auto-commit sequence
    curate's Structure stage uses -- reused verbatim so the write paths cannot
    drift. A degraded suggestion is skipped without a prompt; an already-present
    relation is skipped without a write; declines are listed after the summary.

    Every byte `relate_core` writes was computed by `prepare_relate` BEFORE the
    prompt, so each accepted item re-validates its targets against the prepared
    baselines strictly after its yes and strictly before its write (the
    #306/#313/#319 drift arc); drift raises `DriftDetected`, prior per-item
    commits remain intact."""
    layout = config.WorkspaceLayout(root)
    log_path = layout.bundle_dir / "log.md"
    now = ports.now()
    applied = 0
    skipped = 0
    declined: list[str] = []

    for result in results:
        # `effective_edge`, not `edge` (#991): this is the WRITE path, so the
        # direction that reaches `prepare_relate` must be the corrected one
        # when the direction-signature check found one.
        edge = result.effective_edge
        if result.suggested_type is None:
            observer.degraded(edge)
            skipped += 1
            continue

        caveat = suggestion_caveat(result.suggested_type)
        observer.preview(edge, result.suggested_type, caveat, result.rationale)
        if not observer.confirm_relate(edge, result.suggested_type, caveat):
            skipped += 1
            declined.append(
                f"{edge.source_id} -> {edge.target_id} [{result.suggested_type}]"
            )
            continue

        # The commit phase (#1137): the model call and the prompt above held
        # no workspace lock, so the relation is prepared from the bytes it
        # will be written over.
        with ports.commit_section():
            source_path = okf.concept_path_for(edge.source_id, layout.bundle_dir)
            target_path = okf.concept_path_for(edge.target_id, layout.bundle_dir)
            try:
                prepared = application_lifecycle.prepare_relate(
                    source_path,
                    log_path,
                    edge.source_id,
                    edge.target_id,
                    result.suggested_type,
                    root,
                    now=now,
                    target_path=target_path,
                )
            except (OSError, ValueError) as exc:
                raise _relate_failed(edge, exc) from exc

            if prepared.already_present:
                observer.already_present()
                skipped += 1
                continue

            drift_baselines = {
                source_path: prepared.source_bytes,
                log_path: prepared.log_bytes,
            }
            if prepared.target_bytes is not None:
                drift_baselines[target_path] = prepared.target_bytes
            drift = application_drift.describe_drift(
                layout, drift_baselines, _APPLY_VERB
            )
            if drift is not None:
                raise DriftDetected(drift)

            try:
                application_lifecycle.relate_core(
                    source_path, log_path, prepared, target_path=target_path
                )
            except (OSError, ValueError) as exc:
                raise _relate_failed(edge, exc) from exc

            apply_commit_paths = [f"bundle/{edge.source_id}.md", "bundle/log.md"]
            if prepared.new_target_text is not None:
                apply_commit_paths.insert(1, f"bundle/{edge.target_id}.md")
            ports.autocommit(
                root,
                apply_commit_paths,
                f"openkos: relate {edge.source_id} -> {edge.target_id} "
                f"({result.suggested_type})",
            )
        applied += 1

    outcome = ApplyOutcome(applied=applied, skipped=skipped, declined=tuple(declined))
    observer.summary(outcome)
    # #640: once per invocation, only when the walk applied at least one
    # relation write; an all-declined walk invalidated nothing.
    if applied:
        ports.refresh_derived(layout)
    return outcome


def _relate_failed(edge: Edge, exc: Exception) -> RelateFailed:
    return RelateFailed(
        f"openkos {_APPLY_VERB}: failed while relating "
        f"{edge.source_id} -> {edge.target_id} -- {exc}."
    )
