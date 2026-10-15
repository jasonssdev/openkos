"""Judging as an application service (the `judging-application-service` change,
S2b and S2c; ADR-0059).

Judging is the model-backed step that turns an unjudged item into one a human
can decide. The surface is a quote, consent, run protocol: a caller asks for an
LLM-free `JudgingQuote`, the human consents to it by handing back its
`QuoteToken`, and `run_judging` judges at most what was quoted. Identity,
relation type and volatility are the kinds it covers; relation type and
volatility run the existing `suggest_relations_service` and
`suggest_volatility_service` once per chunk (see `_run_relations` and
`_run_volatility`).

The surface is plain data. It never prompts, prints, reads a terminal or raises
`typer.Exit`; everything a client renders reaches it as returned data or as
observer events. It is OWNER-LOCAL (a quote carries work keys and the serve
helpers read stored rationales), so no disclosing adapter may import it. It
ends at persisted verdicts and amended queue rows: it never merges.

A quote makes no model call and costs nothing. The one question that looks like
it needs the model -- which groups would make NO call, because no member is
readable or every member is confidential -- is answered by the leaf judge
itself, run over a recording stand-in that is handed no model: the rule lives
in `resolution/adjudication.py` alone and cannot drift from this module.

A run re-derives the binding and returns `QuoteStale` when a field of it moved;
a moved work set is never a refusal, it is the ceiling's job (a quoted item no
longer needed is `settled_since_quote`, new work is `not_in_quote`, and neither
is judged). It judges one group per chunk and persists each chunk, in its own
commit section and with no lock held across a model call, before the next one
starts. The ceiling client refuses the call that would pass the quote's `calls`.

Stopping. A `StopToken` is observed between chunks and nothing preempts a
model call (ADR-0021), so a stop takes effect no later than the end of the chunk
in flight: the worst-case latency is that chunk's model calls times the
configured per-call `chat_timeout` (600 s by default) -- one call for an
identity chunk of one group, `RELATION_CHUNK_SIZE` calls (4) for a relation chunk,
`VOLATILITY_CHUNK_SIZE` calls (1) for a volatility chunk of one concept type --
so at the default `chat_timeout` of 600 s the bound is 600 s for identity and
volatility and 2400 s for relation type -- plus one persist, whose wait is
bounded by the commit section the caller supplies. Each volatility chunk
re-invokes the service, whose LLM-free concept-type sample was measured at 3 ms
(8 concepts) to 109 ms (800 concepts) per invocation, so one type per chunk
costs a stop no more than that on top of the one call. A stopped run reports what it did not reach
as `deferred`; what was persisted stays.

A busy workspace is retried (`PERSIST_ATTEMPTS`, `PERSIST_BACKOFF_SECONDS`;
both UNMEASURED placeholders, see the constants) and then reported as
`unpersisted`, after which nothing further is spent. `BackendUnavailable` and
`BackendModelNotFound` propagate unchanged once the completed chunks are
persisted; the counts of those chunks reach a caller that catches them through
the observer's `chunk_persisted` events. Any other backend error ends the run
as a returned `failure`.
"""

import contextlib
import dataclasses
import hashlib
import sqlite3
import time
from collections.abc import Callable, Collection, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final, Literal, cast

from openkos import config, lock
from openkos.application import backends as application_backends
from openkos.application import identity_adjudication
from openkos.application import pending as application_pending
from openkos.application import suggest_relations_service as relations
from openkos.application import suggest_volatility_service as volatility
from openkos.application.lock_wait import CommitSection
from openkos.application.runtime import StopToken
from openkos.graph import proximity, sqlite_graph
from openkos.graph.base import Edge
from openkos.llm.base import (
    BackendError,
    BackendModelNotFound,
    BackendUnavailable,
    LLMBackend,
    Message,
)
from openkos.resolution import adjudication as leaf
from openkos.resolution import edge_typing, volatility_typing
from openkos.resolution.candidates import (
    CandidateGroup,
    CandidateGroupReport,
    find_candidates_report,
)
from openkos.state import adjudications as adjudications_store
from openkos.state import edge_suggestions as edge_suggestions_store
from openkos.state import jobs

JudgingKind = Literal["identity", "relation_type", "volatility"]
"""What a quote is about. The quote carries its kind, so a later kind is
additive."""

ChangedField = Literal["model", "backend", "locality", "confidential", "rubric"]
"""The binding fields `QuoteStale` names when they moved."""

_KINDS: Final = ("identity", "relation_type", "volatility")
"""The kinds this surface quotes and runs."""

_NUL = "\x00"


class JudgingRefused(Exception):
    """A quote cannot be made at all: the root is not a workspace or its
    configuration cannot be read. Raised, never printed."""


@dataclass(frozen=True)
class QuoteBinding:
    """What consent was given to. A change of any field is `QuoteStale`."""

    kind: JudgingKind
    model: str
    """`config.resolve_task_model(cfg, task)`."""

    backend: str
    """`cfg.backend`."""

    is_local: bool
    """The client's own locality verdict (construction only, no call)."""

    effective_confidential: bool
    """`include_confidential` or the resolved local exemption."""

    rubric_digest: str | None
    """The judgment rubric's digest for a kind that has one, else `None`."""

    def digest(self) -> str:
        """Opaque sha256 hex of every field, sorted and NUL-separated."""
        return _digest(
            [
                f"kind={self.kind}",
                f"model={self.model}",
                f"backend={self.backend}",
                f"is_local={int(self.is_local)}",
                f"effective_confidential={int(self.effective_confidential)}",
                f"rubric_digest={self.rubric_digest}",
            ]
        )


def changed_fields(
    consented: QuoteBinding, current: QuoteBinding
) -> tuple[ChangedField, ...]:
    """Every field of `consented` that `current` no longer matches, in a fixed
    order. The work set is NOT part of the comparison: a moved work set is the
    run's ceiling to enforce, never a reason to refuse the whole run."""
    moved: list[ChangedField] = []
    if consented.model != current.model:
        moved.append("model")
    if consented.backend != current.backend:
        moved.append("backend")
    if consented.is_local != current.is_local:
        moved.append("locality")
    if consented.effective_confidential != current.effective_confidential:
        moved.append("confidential")
    if consented.rubric_digest != current.rubric_digest:
        moved.append("rubric")
    return tuple(moved)


@dataclass(frozen=True)
class QuoteStale:
    """A token whose binding no longer matches the current state. Returned,
    never raised, by a run before any client call (S2c)."""

    kind: JudgingKind
    changed: tuple[ChangedField, ...]


@dataclass(frozen=True)
class WorkItem:
    work_key: str
    """Identity: `adjudications_store.group_key_for(member_ids)`."""

    member_digests: tuple[tuple[str, str], ...]
    """Sorted `(concept id, sha256 of its current bytes)`; an unreadable member
    carries `""`."""


@dataclass(frozen=True)
class QuoteToken:
    """The context consent was given for. It holds no time: a token is stale
    only when a binding field moved, decided by resolved values and digests."""

    binding: QuoteBinding
    include_confidential: bool
    """The caller's request bit, re-resolved at run."""

    include_uncertain: bool
    work: tuple[WorkItem, ...]
    """The ceiling, in quote order."""

    calls: int
    """An upper bound on chat calls; a zero-call group is in `work` and adds
    nothing here."""

    def digest(self) -> str:
        """Opaque sha256 hex for transport, covering every field, sorted and
        NUL-separated. Nothing parses it back in this change."""
        parts = [
            f"binding={self.binding.digest()}",
            f"include_confidential={int(self.include_confidential)}",
            f"include_uncertain={int(self.include_uncertain)}",
            f"calls={self.calls}",
        ]
        parts += [
            f"work={item.work_key}"
            + _NUL
            + _NUL.join(f"{ref}:{digest}" for ref, digest in item.member_digests)
            for item in self.work
        ]
        return _digest(parts)


@dataclass(frozen=True)
class QuoteNotice:
    code: Literal[
        "model", "truncated", "quarantined", "rubric_rejudge", "unattended_active"
    ]
    count: int | None = None
    """truncated: groups shown; rubric_rejudge: groups."""

    produced: int | None = None
    """truncated only: how many groups the corpus produced before the cap."""

    state: Literal["active", "unknown"] | None = None
    """unattended_active only."""

    model: str | None = None
    """model only: the resolved task model."""

    backend: str | None = None
    """model only: `cfg.backend`."""

    text: str | None = None
    """relation_type `truncated` and `quarantined`: the relations service's own
    notice, already restricted to what the caller may see. It carries no count
    because the service derives the visible counts through the sensitivity walk
    and renders them; this surface does not repeat that derivation."""


@dataclass(frozen=True)
class JudgingQuote:
    kind: JudgingKind
    total: int
    """Candidate groups considered, kept-distinct ones already dropped."""

    served: int
    """Persisted answers served with no call: decided (SAME, DIFFERENT) verdicts
    for identity, a servable stored suggestion for relation_type."""

    uncertain: int
    """Servable UNCERTAIN verdicts. In `to_judge` only when opted in."""

    to_judge: int
    calls: int
    """An upper bound; the run never exceeds it."""

    binding: QuoteBinding
    notices: tuple[QuoteNotice, ...]
    token: QuoteToken


@dataclass(frozen=True)
class JudgingProgress:
    index: int
    total: int
    work_key: str
    verdict: str
    """`same`, `different` or `uncertain`; never the rationale."""


class JudgingObserver:
    """Receives a run's events (S2c). Every method is a no-op."""

    def progress(self, event: JudgingProgress) -> None:
        return None

    def chunk_persisted(self, persisted: int) -> None:
        return None


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class JudgingPorts:
    """The effects a quote and a run reach. Only the chat-client factory is
    required; the rest default to the real implementation."""

    chat_client: Callable[[config.Config, str | None], LLMBackend]
    resolve_local_exemption: Callable[
        [application_backends.HasLocality, config.Config], bool
    ] = application_backends.resolve_local_exemption
    commit_section: CommitSection = contextlib.nullcontext
    find_candidates_report: Callable[..., CandidateGroupReport] = find_candidates_report
    adjudicate_candidates: Callable[..., leaf.AdjudicationBatch] = (
        leaf.adjudicate_candidates
    )
    sleep: Callable[[float], None] = time.sleep
    """The retry backoff's wait (S2c); a quote never sleeps."""

    open_proximity: Callable[[Path], proximity.VectorProximitySource | None] = (
        proximity.open_proximity_source
    )
    build_graph: Callable[..., sqlite_graph.SqliteGraphStore] = sqlite_graph.build_graph
    candidate_edges: Callable[..., list[Edge]] = edge_typing.candidate_edges
    suggest_edge_types: Callable[..., edge_typing.EdgeSuggestionBatch] = (
        edge_typing.suggest_edge_types
    )
    """The relation_type kind's seams, the same four the relations service has;
    each defaults to the real implementation."""

    plan_volatility_types: Callable[..., tuple[volatility_typing.PlannedType, ...]] = (
        volatility_typing.plan_types
    )
    suggest_volatility: Callable[..., volatility_typing.TierSuggestionBatch] = (
        volatility_typing.suggest_volatility
    )
    """The volatility kind's seams: the LLM-free planning a quote and a run
    partition by, and the typing the volatility service drives."""

    now: Callable[[], datetime] = _utc_now


def _digest(parts: Sequence[str]) -> str:
    return hashlib.sha256(_NUL.join(sorted(parts)).encode("utf-8")).hexdigest()


class _CallRecorder:
    """Stands in for the model when the quote asks the leaf which groups would
    make a call. It is handed to the leaf in place of a client, answers every
    prompt with an empty reply and counts them; no model is reached."""

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages: Sequence[Message]) -> str:
        self.calls += 1
        return ""


def _calling_groups(
    groups: Sequence[CandidateGroup],
    *,
    layout: config.WorkspaceLayout,
    ports: JudgingPorts,
    include_confidential: bool,
    local_exemption: bool,
) -> set[str]:
    """The work keys of the groups a run would spend a call on: the leaf's own
    determination, read off a recording stand-in. A group with no readable
    member (or only blocked ones) short-circuits in the leaf and calls nothing."""
    if not groups:
        return set()
    recorder = _CallRecorder()
    calling: set[str] = set()
    seen = 0

    def on_progress(index: int, total: int, result: leaf.AdjudicatedCandidate) -> None:
        nonlocal seen
        if recorder.calls > seen:
            calling.add(adjudications_store.group_key_for(result.candidate.member_ids))
            seen = recorder.calls

    ports.adjudicate_candidates(
        list(groups),
        bundle_dir=layout.bundle_dir,
        llm=cast(LLMBackend, recorder),
        include_confidential=include_confidential,
        local_exemption=local_exemption,
        on_progress=on_progress,
    )
    return calling


def _unattended_notice(
    layout: config.WorkspaceLayout, cfg: config.Config, now: datetime
) -> QuoteNotice | None:
    """The unattended engine's recent activity, from `.openkos/jobs.db` opened
    read-only (so the file is never created): an open job, or the newest job
    started within `maintenance_interval_seconds`. An absent or schema-less
    record yields no notice; an unreadable one yields `state="unknown"`, since
    an informational notice errs toward disclosure."""
    try:
        conn = jobs.open_jobs_read_only(layout.jobs_db_path)
    except jobs.JobsStoreUnreadableError:
        return QuoteNotice("unattended_active", state="unknown")
    if conn is None:
        return None
    try:
        newest = jobs.last_job(conn)
    except sqlite3.Error:
        return QuoteNotice("unattended_active", state="unknown")
    finally:
        conn.close()
    if newest is None:
        return None
    if newest.ended_at is None:
        return QuoteNotice("unattended_active", state="active")
    try:
        started = datetime.strptime(newest.started_at, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=UTC
        )
    except ValueError:
        return QuoteNotice("unattended_active", state="unknown")
    window = timedelta(seconds=cfg.unattended.maintenance_interval_seconds)
    if now - started <= window:
        return QuoteNotice("unattended_active", state="active")
    return None


def _work_item(
    group: CandidateGroup, current_digest: Callable[[str], str | None]
) -> WorkItem:
    return WorkItem(
        work_key=adjudications_store.group_key_for(group.member_ids),
        member_digests=tuple(
            (member, current_digest(member) or "")
            for member in sorted(group.member_ids)
        ),
    )


@dataclass(frozen=True)
class _Context:
    """What a quote or a run resolves from the configuration before it looks at
    any work: the client (built, never called), its binding and the two
    confidential bits."""

    client: LLMBackend
    binding: QuoteBinding
    local_exemption: bool


def _open_workspace(
    root: Path, verb: str
) -> tuple[config.WorkspaceLayout, config.Config]:
    reason = config.require_workspace(root)
    if reason is not None:
        raise JudgingRefused(f"openkos judging: refusing to {verb} -- {reason}.")
    try:
        cfg = config.read_config(root)
    except (OSError, ValueError) as exc:
        raise JudgingRefused(
            f"openkos judging: failed while reading the workspace -- {exc}."
        ) from exc
    return config.WorkspaceLayout(root), cfg


def _context(
    cfg: config.Config,
    kind: JudgingKind,
    *,
    include_confidential: bool,
    ports: JudgingPorts,
) -> _Context:
    """Build the client once (construction only, no call) and bind what consent
    covers. Both the quote and the run derive the binding here, so what a token
    was given for and what a run compares it with can never be computed two
    ways."""
    # The literal task name, at the call: `test_chat_timeout_wiring` reads it
    # off the AST so a refactor cannot silently drop the `models:` override.
    if kind == "relation_type":
        client = ports.chat_client(cfg, "edge_typing")
        task = "edge_typing"
    elif kind == "volatility":
        client = ports.chat_client(cfg, "volatility_typing")
        task = "volatility_typing"
    else:
        client = ports.chat_client(cfg, "adjudication")
        task = "adjudication"
    local_exemption = ports.resolve_local_exemption(
        cast(application_backends.HasLocality, client), cfg
    )
    binding = QuoteBinding(
        kind=kind,
        model=config.resolve_task_model(cfg, task),
        backend=cfg.backend,
        is_local=cast(application_backends.HasLocality, client).locality.is_local,
        effective_confidential=include_confidential or local_exemption,
        rubric_digest=leaf.rubric_digest() if kind == "identity" else None,
    )
    return _Context(client, binding, local_exemption)


@dataclass(frozen=True)
class _Partition:
    groups: list[CandidateGroup]
    """The candidate groups, kept-distinct ones already dropped."""

    serves: identity_adjudication.AdjudicationServes
    decided: set[str]
    uncertain: set[str]
    fresh: set[str]

    def needed(self, *, include_uncertain: bool) -> list[CandidateGroup]:
        """The groups a run would judge, in candidate order."""
        wanted = self.fresh | self.uncertain if include_uncertain else self.fresh
        return [
            group
            for group in self.groups
            if adjudications_store.group_key_for(group.member_ids) in wanted
        ]


def _partition_identity(
    layout: config.WorkspaceLayout,
    candidates: Sequence[CandidateGroup],
    *,
    effective: bool,
) -> _Partition:
    """The LLM-free split a quote and a run both make: drop what a human ruled
    distinct, then separate verdicts served as decided, served as uncertain and
    still to judge."""
    groups = [
        group
        for group in candidates
        if not application_pending.is_group_kept_distinct(layout, group.member_ids)
    ]
    serves = identity_adjudication.partition_adjudication_serves(
        layout,
        groups,
        include_confidential=effective,
        on_warning=None,
        surface="judging",
    )
    decided = {
        key
        for key, verdict in serves.served.items()
        if verdict.verdict != leaf.Verdict.UNCERTAIN
    }
    return _Partition(
        groups=groups,
        serves=serves,
        decided=decided,
        uncertain=set(serves.served) - decided,
        fresh={
            adjudications_store.group_key_for(g.member_ids) for g in serves.to_judge
        },
    )


# -- relation_type ------------------------------------------------------------ #
#
# The unit judged is the unit the shipped relations service uses: one untyped,
# DIRECTED edge, one chat call. A quote and a run reach that service rather than
# restate it: the quote asks it to price and declines, the run re-invokes it per
# chunk with a bound and a candidate port narrowed to the quoted edges.


class _RelationsObserver:
    """The relations service's observer, reduced to what judging needs: it keeps
    the two candidate notices the service renders, declines the cost question
    (a quote spends nothing) and forwards each typed edge to `on_progress`."""

    def __init__(
        self,
        *,
        on_progress: Callable[[int, int, edge_typing.EdgeSuggestion], None]
        | None = None,
    ) -> None:
        self.truncation: str | None = None
        self.quarantine: str | None = None
        self._on_progress = on_progress

    def walk_incomplete(
        self, bundle_dir: Path, *, include_confidential: bool, local_exemption: bool
    ) -> None:
        return None

    def workspace_header(self, root: Path) -> None:
        return None

    def empty_window(self, edge_offset: int) -> None:
        return None

    def candidate_notices(self, truncation: str | None, quarantine: str | None) -> None:
        self.truncation = truncation
        self.quarantine = quarantine

    def no_candidates(self, message: str) -> None:
        return None

    def warn(self, message: str) -> None:
        return None

    def serve_split(self, served: int, total: int, fresh: int) -> None:
        return None

    def confirm_cost(self, quote: relations.CostQuote) -> bool:
        return False

    def edge_progress(
        self, index: int, count: int, suggestion: edge_typing.EdgeSuggestion
    ) -> None:
        if self._on_progress is not None:
            self._on_progress(index, count, suggestion)


def _relations_ports(
    context: _Context,
    ports: JudgingPorts,
    *,
    client: LLMBackend,
    candidate_edges: Callable[..., list[Edge]],
    commit_section: CommitSection = contextlib.nullcontext,
    suggest_edge_types: Callable[..., edge_typing.EdgeSuggestionBatch] | None = None,
) -> relations.SuggestRelationsPorts:
    """The relations service's ports over this surface's. The client and the
    local exemption are the ones the binding was derived from, so the service
    cannot resolve either a second way; the adapter-side effects (the cost
    question's zero-state wording, the auto-commit, the derived refresh) belong
    to `--apply` and the CLI and are inert here."""
    return relations.SuggestRelationsPorts(
        chat_client=lambda cfg, task: client,
        zero_state_message=lambda layout, store, embeddings_missing: "",
        autocommit=lambda root, paths, message: None,
        refresh_derived=lambda layout: None,
        resolve_local_exemption=lambda client, cfg: context.local_exemption,
        open_proximity=ports.open_proximity,
        build_graph=ports.build_graph,
        candidate_edges=candidate_edges,
        suggest_edge_types=(
            ports.suggest_edge_types
            if suggest_edge_types is None
            else suggest_edge_types
        ),
        commit_section=commit_section,
    )


@dataclass(frozen=True)
class _Walk:
    edges: list[Edge]
    """Every candidate edge the service walked, in candidate order."""

    truncation: str | None
    quarantine: str | None


def _walk_relations(
    root: Path,
    context: _Context,
    *,
    include_confidential: bool,
    ports: JudgingPorts,
) -> _Walk:
    """The LLM-free candidate walk, run by the service itself: it is asked to
    price and its observer declines, so no model is reached and nothing is
    written. The candidate edges are read off the service's own candidate port."""
    seen: list[Edge] = []

    def capture(bundle_dir: Path, **kwargs: object) -> list[Edge]:
        edges = ports.candidate_edges(bundle_dir, **kwargs)
        seen[:] = edges
        return edges

    observer = _RelationsObserver()
    relations.suggest_relations(
        root,
        relations.SuggestRelationsRequest(include_confidential=include_confidential),
        _relations_ports(
            context, ports, client=context.client, candidate_edges=capture
        ),
        observer,
    )
    return _Walk(list(seen), observer.truncation, observer.quarantine)


def _edge_item(edge: Edge, current_digest: Callable[[str], str | None]) -> WorkItem:
    return WorkItem(
        work_key=edge_suggestions_store.pair_key_for(edge.source_id, edge.target_id),
        member_digests=tuple(
            (endpoint, current_digest(endpoint) or "")
            for endpoint in sorted({edge.source_id, edge.target_id})
        ),
    )


def _partition_relations(
    layout: config.WorkspaceLayout,
    edges: list[Edge],
    *,
    effective: bool,
) -> relations.EdgeSuggestionServes:
    """The LLM-free serve split the relations service itself makes (the same
    function), so a quote and a run agree with it about what a model must type."""
    return relations.partition_edge_suggestion_serves(
        layout,
        edges,
        include_confidential=effective,
        on_warning=None,
        surface="judging",
    )


def _quote_relations(
    root: Path,
    layout: config.WorkspaceLayout,
    cfg: config.Config,
    context: _Context,
    *,
    include_confidential: bool,
    ports: JudgingPorts,
) -> JudgingQuote:
    binding = context.binding
    walk = _walk_relations(
        root, context, include_confidential=include_confidential, ports=ports
    )
    serves = _partition_relations(
        layout, walk.edges, effective=binding.effective_confidential
    )
    current_digest = application_pending.current_finding_digest(layout.bundle_dir)
    work = tuple(_edge_item(edge, current_digest) for edge in serves.to_type)

    notices: list[QuoteNotice] = [
        QuoteNotice("model", model=binding.model, backend=cfg.backend)
    ]
    if walk.truncation is not None:
        notices.append(QuoteNotice("truncated", text=walk.truncation))
    if walk.quarantine is not None:
        notices.append(QuoteNotice("quarantined", text=walk.quarantine))
    unattended = _unattended_notice(layout, cfg, ports.now())
    if unattended is not None:
        notices.append(unattended)

    # Every edge costs exactly one chat call and none short-circuits, so the
    # bound is the work itself.
    token = QuoteToken(
        binding=binding,
        include_confidential=include_confidential,
        include_uncertain=False,
        work=work,
        calls=len(work),
    )
    return JudgingQuote(
        kind="relation_type",
        total=len(walk.edges),
        served=len(serves.served),
        uncertain=0,
        to_judge=len(work),
        calls=len(work),
        binding=binding,
        notices=tuple(notices),
        token=token,
    )


def _volatility_plan(
    layout: config.WorkspaceLayout,
    cfg: config.Config,
    context: _Context,
    *,
    include_confidential: bool,
    ports: JudgingPorts,
    only_types: Collection[str] | None = None,
) -> tuple[volatility_typing.PlannedType, ...]:
    """The LLM-free plan the volatility service itself makes: the same leaf
    function over the same arguments (the model that keys the serve lookup, the
    workspace's tier overrides and rationale language, the locality verdict the
    binding was derived from), so a quote and a run agree with it about what a
    model must be asked."""
    return ports.plan_volatility_types(
        layout.bundle_dir,
        include_confidential=include_confidential,
        local_exemption=context.local_exemption,
        rationale_language=cfg.rationale_language,
        served=volatility.cached_answers(layout, context.binding.model, cfg.type_tiers),
        type_tiers=cfg.type_tiers or None,
        only_types=only_types,
    )


def _volatility_item(
    planned: volatility_typing.PlannedType,
    current_digest: Callable[[str], str | None],
) -> WorkItem:
    return WorkItem(
        work_key=planned.type_name,
        member_digests=tuple(
            (ref, current_digest(ref) or "") for ref in sorted(planned.input_refs)
        ),
    )


def _quote_volatility(
    layout: config.WorkspaceLayout,
    cfg: config.Config,
    context: _Context,
    *,
    include_confidential: bool,
    ports: JudgingPorts,
) -> JudgingQuote:
    """The volatility quote, built from the leaf's own plan because the volatility
    service states no `CostQuote`. Every type the model must still be asked costs
    exactly one chat call and none short-circuits, so the bound is the work."""
    binding = context.binding
    plan = _volatility_plan(
        layout, cfg, context, include_confidential=include_confidential, ports=ports
    )
    current_digest = application_pending.current_finding_digest(layout.bundle_dir)
    work = tuple(_volatility_item(p, current_digest) for p in plan if not p.answered)
    notices: list[QuoteNotice] = [
        QuoteNotice("model", model=binding.model, backend=cfg.backend)
    ]
    unattended = _unattended_notice(layout, cfg, ports.now())
    if unattended is not None:
        notices.append(unattended)
    token = QuoteToken(
        binding=binding,
        include_confidential=include_confidential,
        include_uncertain=False,
        work=work,
        calls=len(work),
    )
    return JudgingQuote(
        kind="volatility",
        total=len(plan),
        served=sum(1 for p in plan if p.answered),
        uncertain=0,
        to_judge=len(work),
        calls=len(work),
        binding=binding,
        notices=tuple(notices),
        token=token,
    )


def quote_judging(
    root: Path,
    kind: JudgingKind,
    *,
    include_confidential: bool = False,
    include_uncertain: bool = False,
    ports: JudgingPorts,
) -> JudgingQuote:
    """Price judging for `kind` at the workspace `root`, with no model call.

    Builds the client only to read its locality and the model tag, drops the
    groups a human already ruled distinct, partitions the rest under the
    effective confidential bit and reports what a run would judge and at most
    how many calls it would make. Judged-uncertain items are left out of
    `to_judge` and `calls` unless `include_uncertain`. Raises `JudgingRefused`
    when `root` is not a readable workspace and `ValueError` for a kind this
    surface does not know. `include_uncertain` is an identity notion: a
    relation_type or volatility quote ignores it."""
    if kind not in _KINDS:
        raise ValueError(f"judging kind {kind!r} is not a kind this surface quotes")
    layout, cfg = _open_workspace(root, "quote")
    context = _context(
        cfg, kind, include_confidential=include_confidential, ports=ports
    )
    if kind == "relation_type":
        return _quote_relations(
            root,
            layout,
            cfg,
            context,
            include_confidential=include_confidential,
            ports=ports,
        )
    if kind == "volatility":
        return _quote_volatility(
            layout, cfg, context, include_confidential=include_confidential, ports=ports
        )
    binding = context.binding
    local_exemption = context.local_exemption

    report = ports.find_candidates_report(layout.bundle_dir)
    partition = _partition_identity(
        layout, report.groups, effective=binding.effective_confidential
    )
    groups = partition.groups
    serves = partition.serves
    uncertain = partition.uncertain
    needed = partition.needed(include_uncertain=include_uncertain)

    calling = _calling_groups(
        needed,
        layout=layout,
        ports=ports,
        include_confidential=include_confidential,
        local_exemption=local_exemption,
    )
    current_digest = application_pending.current_finding_digest(layout.bundle_dir)
    work = tuple(_work_item(group, current_digest) for group in needed)
    calls = len(calling)

    notices: list[QuoteNotice] = [
        QuoteNotice("model", model=binding.model, backend=cfg.backend)
    ]
    if report.produced > report.retained:
        notices.append(
            QuoteNotice("truncated", count=report.retained, produced=report.produced)
        )
    if serves.rubric_stale:
        notices.append(QuoteNotice("rubric_rejudge", count=serves.rubric_stale))
    unattended = _unattended_notice(layout, cfg, ports.now())
    if unattended is not None:
        notices.append(unattended)

    token = QuoteToken(
        binding=binding,
        include_confidential=include_confidential,
        include_uncertain=include_uncertain,
        work=work,
        calls=calls,
    )
    return JudgingQuote(
        kind=kind,
        total=len(groups),
        served=len(partition.decided),
        uncertain=len(uncertain),
        to_judge=len(needed),
        calls=calls,
        binding=binding,
        notices=tuple(notices),
        token=token,
    )


IDENTITY_CHUNK_SIZE: Final = 1
"""Groups judged per chunk: one chat call, then one persist. A stop is observed
between chunks, so a larger chunk would multiply the worst-case stop latency
without saving anything a call costs."""


RELATION_CHUNK_SIZE: Final = 4
"""Edges typed per chunk for `relation_type`: each chunk re-invokes the existing
relations service, so each one repeats its LLM-free walk (graph build, candidate
edges, the persisted-suggestion read and an endpoint hash per filtered edge) and
then persists. Measured (S3a.1, macOS, stub chat that answers instantly, so only
the walk and the persist are timed):

    bundle (concepts / candidate edges)   walk only   one chunk, 8 filtered edges
    good-life-demo (8 / 14)                  3.5 ms        6.9 ms
    synthetic (50 / 150)                     8.0 ms        9.8 ms
    synthetic (200 / 600)                   30.0 ms       32.3 ms
    synthetic (800 / 2400)                 121.8 ms      126.4 ms
    synthetic (800 / 2400, store holds
      2400 rows, `fresh=False`)                           279.3 ms

The walk grows about linearly with the bundle (about 0.15 ms per concept, and
about 0.35 ms per concept once the store is read and hashed), so per-chunk
invocation is workable: against even a 5 s model call, four edges per chunk keep
the largest measured overhead at about 1.4 % of the chunk's model time, where
one edge per chunk would be 5.6 % and eight would still be under 1 % but double
the stop latency. The size is therefore set by latency, not overhead: the
smallest size that keeps the overhead under 5 % of a 5 s call at 800 concepts
is two, and four leaves a margin for a bundle about four times larger. Worst-case
stop latency is `RELATION_CHUNK_SIZE` model calls times the per-call
`chat_timeout` (4 x 600 s by default) plus one persist; a typical local call
makes it about a minute and a half. The measurement script is not part of the
repository; the numbers above are its output."""

VOLATILITY_CHUNK_SIZE: Final = 1
"""Concept types asked per chunk for `volatility`. The
volatility service has no graph walk, only a concept-type sample over the bundle:
measured with an instantly answering stub, one invocation costs 3.3 ms on the
good-life-demo, 7.0 ms at 50 concepts, 27 ms at 200 and 109 ms at 800, and an
invocation that makes no call costs the same, so the overhead is the walk and not
the call. A workspace has a few dozen concept types at most, so one type per
chunk is at most a few dozen invocations (about 3 s of walk at 800 concepts)
against a model call of seconds, and it gives the same worst-case stop latency
as identity: one `chat_timeout` (600 s by default) plus one persist."""

_CHUNK_SIZES: Final = {
    "identity": IDENTITY_CHUNK_SIZE,
    "relation_type": RELATION_CHUNK_SIZE,
    "volatility": VOLATILITY_CHUNK_SIZE,
}
"""The default chunk size per runnable kind."""


PERSIST_BACKOFF_SECONDS: Final = (1.0, 2.0)
PERSIST_ATTEMPTS: Final = len(PERSIST_BACKOFF_SECONDS) + 1
"""How long the run sleeps between attempts to persist a chunk into a busy
workspace (through `ports.sleep`, with no lock held), and so how many attempts
it makes: one more than there are waits. UNMEASURED: both are placeholders, not
tuned values.

What was measured (S2c.1, macOS, 60 persists of one chunk through the real lock
and `locked_commit_section`): the persist holds the lock for about 1 ms (median
1.0, p95 1.2, max 4.3), a contender waiting on a held lock acquires it within
0.05-0.09 s of the release, and `wait_seconds=0` refuses in under a millisecond.
That bounds this surface's own contribution to contention, not a competitor's:
the hold time of the daemon's or an ingest's commit section (a git autocommit
inside it) was not measured, so whether 1 s then 2 s outlasts a real contender
is unknown. The sections the app passes may also wait on their own
(`locked_commit_section(wait_seconds=...)`); these attempts bound the service,
that wait bounds each attempt."""


@dataclass(frozen=True)
class RunRefused:
    """No model call was made and nothing was written."""

    reason: Literal["no_quote_token"]


@dataclass(frozen=True)
class JudgingOutcome:
    kind: JudgingKind
    status: Literal["completed", "stopped", "failed", "unpersisted"]
    judged: int
    """Results produced, zero-call degrades included."""

    calls: int
    """Chat calls made; never more than the token's."""

    persisted: int
    unpersisted: int
    """Verdicts that were paid for and could not be stored."""

    unpersisted_cause: Literal["workspace_busy", "store_error"] | None
    dropped: int
    """Verdicts not stored on purpose: a member was edited, raised or forgotten
    during the call, or could not be read. `judged == persisted + dropped +
    unpersisted`."""

    deferred: int
    """Quoted items the run did not reach."""

    not_in_quote: tuple[str, ...]
    """Work keys needed now that the quote did not cover; never judged."""

    settled_since_quote: int
    failure: BackendError | None = None
    failed_at: int | None = None
    """The 1-based run position of the item a backend error ended the run at."""

    observer_failed: bool = False


class _CeilingClient:
    """Counts the chat calls a run makes and refuses the one that would pass
    the quote's ceiling, so an excess is a defect found before the spend, not
    after it."""

    def __init__(self, inner: LLMBackend, limit: int) -> None:
        self._inner = inner
        self._limit = limit
        self.calls = 0

    def chat(self, messages: Sequence[Message]) -> str:
        if self.calls >= self._limit:
            raise AssertionError(
                f"a model call past the quote's ceiling of {self._limit} was refused"
            )
        self.calls += 1
        return self._inner.chat(messages)


class _ObserverGuard:
    """Every observer call goes through here. The observer is the client's
    callback: its failure must not cost paid work, so the first exception is
    swallowed, recorded, and the observer is not called again."""

    def __init__(self, observer: JudgingObserver) -> None:
        self._observer = observer
        self.failed = False

    def progress(self, event: JudgingProgress) -> None:
        if self.failed:
            return
        try:
            self._observer.progress(event)
        except Exception:  # noqa: BLE001 -- an observer is the client's callback; its failure must not cost paid work
            self.failed = True

    def chunk_persisted(self, persisted: int) -> None:
        if self.failed:
            return
        try:
            self._observer.chunk_persisted(persisted)
        except Exception:  # noqa: BLE001 -- an observer is the client's callback; its failure must not cost paid work
            self.failed = True


def _persist_chunk(
    layout: config.WorkspaceLayout,
    results: Sequence[leaf.AdjudicatedCandidate],
    *,
    effective: bool,
    pinned: Mapping[str, str | None],
    ports: JudgingPorts,
) -> identity_adjudication.PersistResult:
    """Persist one chunk, retrying a busy workspace up to `PERSIST_ATTEMPTS`
    times with the backoff between attempts. The lock is held only inside an
    attempt, never while sleeping."""

    def attempt() -> identity_adjudication.PersistResult:
        return identity_adjudication.persist_adjudications(
            layout,
            results,
            include_confidential=effective,
            judged_digests=pinned,
            commit_section=ports.commit_section,
            write_through=True,
        )

    result = attempt()
    for backoff in PERSIST_BACKOFF_SECONDS:
        if result.busy is None:
            return result
        ports.sleep(backoff)
        result = attempt()
    return result


def run_judging(
    root: Path,
    token: QuoteToken | None,
    *,
    stop: StopToken,
    ports: JudgingPorts,
    observer: JudgingObserver | None = None,
    chunk_size: int | None = None,
) -> JudgingOutcome | QuoteStale | RunRefused:
    """Judge at most what `token` was quoted for, persisting each chunk.

    `token=None` is refused before the configuration is read. A moved binding
    field returns `QuoteStale` before any call. Otherwise the work is
    re-partitioned LLM-free and only the quoted items still needed are judged,
    in quote order; each chunk is judged with no lock held and persisted in its
    own commit section before the next begins. `stop` is observed between
    chunks. `chunk_size` defaults per kind (`IDENTITY_CHUNK_SIZE`,
    `RELATION_CHUNK_SIZE`, `VOLATILITY_CHUNK_SIZE`). Raises `JudgingRefused` when `root` is not a readable
    workspace and `ValueError` for a `chunk_size` below one or a kind this surface
    does not run."""
    if token is None:
        return RunRefused("no_quote_token")
    kind = token.binding.kind
    if kind not in _CHUNK_SIZES:
        raise ValueError(f"judging kind {kind!r} cannot be run yet")
    size = _CHUNK_SIZES[kind] if chunk_size is None else chunk_size
    if size < 1:
        raise ValueError(f"chunk_size must be at least 1, got {size}")
    layout, cfg = _open_workspace(root, "run")
    context = _context(
        cfg,
        kind,
        include_confidential=token.include_confidential,
        ports=ports,
    )
    moved = changed_fields(token.binding, context.binding)
    if moved:
        return QuoteStale(kind, moved)

    guard = _ObserverGuard(observer if observer is not None else JudgingObserver())
    if kind == "relation_type":
        return _run_relations(
            root, layout, token, context, size=size, stop=stop, ports=ports, guard=guard
        )
    if kind == "volatility":
        return _run_volatility(
            root,
            layout,
            cfg,
            token,
            context,
            size=size,
            stop=stop,
            ports=ports,
            guard=guard,
        )
    return _run_identity(
        layout, token, context, size=size, stop=stop, ports=ports, guard=guard
    )


def _run_identity(
    layout: config.WorkspaceLayout,
    token: QuoteToken,
    context: _Context,
    *,
    size: int,
    stop: StopToken,
    ports: JudgingPorts,
    guard: _ObserverGuard,
) -> JudgingOutcome:
    """The identity run: one group per chunk by default, the leaf judge called
    once per chunk and the verdicts persisted, with write-through, before the
    next chunk."""
    effective = context.binding.effective_confidential
    report = ports.find_candidates_report(layout.bundle_dir)
    partition = _partition_identity(layout, report.groups, effective=effective)
    needed = partition.needed(include_uncertain=token.include_uncertain)
    digest_of = application_pending.current_finding_digest(layout.bundle_dir)
    now_items = {_work_item(group, digest_of): group for group in needed}
    quoted = set(token.work)
    judge = [now_items[item] for item in token.work if item in now_items]
    not_in_quote = tuple(item.work_key for item in now_items if item not in quoted)
    settled = sum(1 for item in token.work if item not in now_items)

    counted = _CeilingClient(context.client, token.calls)
    judged = persisted = dropped = unpersisted = 0
    failure: BackendError | None = None
    failed_at: int | None = None
    cause: Literal["workspace_busy", "store_error"] | None = None
    status: Literal["completed", "stopped", "failed", "unpersisted"] = "completed"
    for start in range(0, len(judge), size):
        if stop.is_set():
            status = "stopped"
            break
        chunk = judge[start : start + size]
        current = application_pending.current_finding_digest(layout.bundle_dir)
        pinned = {
            member: current(member) for group in chunk for member in group.member_ids
        }

        def on_progress(
            index: int,
            total: int,
            result: leaf.AdjudicatedCandidate,
            *,
            before: int = judged,
        ) -> None:
            guard.progress(
                JudgingProgress(
                    index=before + index,
                    total=len(judge),
                    work_key=adjudications_store.group_key_for(
                        result.candidate.member_ids
                    ),
                    verdict=result.verdict.value,
                )
            )

        batch = ports.adjudicate_candidates(
            chunk,
            bundle_dir=layout.bundle_dir,
            llm=cast(LLMBackend, counted),
            include_confidential=token.include_confidential,
            local_exemption=context.local_exemption,
            on_progress=on_progress,
        )
        before = judged
        judged += len(batch.results)
        failure = batch.failure
        if failure is not None:
            failed_at = before + (batch.failed_index or 1)
        result = _persist_chunk(
            layout, batch.results, effective=effective, pinned=pinned, ports=ports
        )
        dropped += result.dropped
        if result.busy is not None or result.store_error is not None:
            cause = "workspace_busy" if result.busy is not None else "store_error"
            unpersisted = len(batch.results) - result.dropped
            status = "unpersisted"
            break
        persisted += result.persisted
        if batch.results:
            guard.chunk_persisted(persisted)
        if failure is not None:
            if isinstance(failure, BackendUnavailable | BackendModelNotFound):
                raise failure
            status = "failed"
            break
    return JudgingOutcome(
        kind=token.binding.kind,
        status=status,
        judged=judged,
        calls=counted.calls,
        persisted=persisted,
        unpersisted=unpersisted,
        unpersisted_cause=cause,
        dropped=dropped,
        deferred=len(judge) - (judged if failed_at is None else failed_at),
        not_in_quote=not_in_quote,
        settled_since_quote=settled,
        failure=failure,
        failed_at=failed_at,
        observer_failed=guard.failed,
    )


class _RetryingSection:
    """The commit section handed to the relations service, which enters it once
    per persist and treats a busy workspace as a fail-open advisory. Entering
    retries a busy workspace up to `PERSIST_ATTEMPTS` times with the backoff
    between attempts, holding no lock while it sleeps, exactly as identity's
    persist does; when every attempt is busy it records that and raises the busy
    error for the service to absorb, so the run can report why nothing was kept."""

    def __init__(self, ports: JudgingPorts) -> None:
        self._ports = ports
        self.busy = False

    @contextlib.contextmanager
    def __call__(self) -> Iterator[None]:
        stack = contextlib.ExitStack()
        for backoff in PERSIST_BACKOFF_SECONDS:
            try:
                stack.enter_context(self._ports.commit_section())
                break
            except lock.WorkspaceBusyError:
                self._ports.sleep(backoff)
        else:
            try:
                stack.enter_context(self._ports.commit_section())
            except lock.WorkspaceBusyError:
                self.busy = True
                raise
        with stack:
            yield


class _ChunkTyping:
    """The two ports one relations invocation is given. `candidate_edges` narrows
    the walk to the quoted edges still to do, so the service never types an edge
    the quote did not cover. `suggest_edge_types` pins each endpoint's digest as
    the typing begins, so a suggestion about content edited during the call can be
    told from one the store failed to keep (the pin follows the service's own by
    microseconds, not by a model call)."""

    def __init__(
        self,
        layout: config.WorkspaceLayout,
        ports: JudgingPorts,
        remaining: Sequence[Edge],
    ) -> None:
        self._layout = layout
        self._ports = ports
        self._remaining = {
            edge_suggestions_store.pair_key_for(edge.source_id, edge.target_id)
            for edge in remaining
        }
        self.pinned: dict[str, str | None] = {}

    def candidate_edges(self, bundle_dir: Path, **kwargs: object) -> list[Edge]:
        return [
            edge
            for edge in self._ports.candidate_edges(bundle_dir, **kwargs)
            if edge_suggestions_store.pair_key_for(edge.source_id, edge.target_id)
            in self._remaining
        ]

    def suggest_edge_types(
        self, to_type: Sequence[Edge], **kwargs: object
    ) -> edge_typing.EdgeSuggestionBatch:
        current = application_pending.current_finding_digest(self._layout.bundle_dir)
        for edge in to_type:
            for endpoint in (edge.source_id, edge.target_id):
                self.pinned[endpoint] = current(endpoint)
        return self._ports.suggest_edge_types(to_type, **kwargs)


def _progress_hook(
    guard: _ObserverGuard, *, before: int, total: int
) -> Callable[[int, int, edge_typing.EdgeSuggestion], None]:
    """The service's per-edge progress rebased to the run: the index continues
    from `before` and the total is the run's, not the invocation's. Verdict
    only, never the rationale; an edge the model could not type reports
    `degraded`."""

    def on_progress(
        index: int, count: int, suggestion: edge_typing.EdgeSuggestion
    ) -> None:
        guard.progress(
            JudgingProgress(
                index=before + index,
                total=total,
                work_key=edge_suggestions_store.pair_key_for(
                    suggestion.edge.source_id, suggestion.edge.target_id
                ),
                verdict=suggestion.suggested_type or "degraded",
            )
        )

    return on_progress


@dataclass(frozen=True)
class _ChunkStored:
    """What became of one relations chunk's fresh suggestions. Each suggestion is
    in exactly one bucket, so `persisted + dropped + unpersisted` is the number
    the model produced."""

    persisted: int
    dropped: int
    unpersisted: int


def _classify_chunk(
    layout: config.WorkspaceLayout,
    fresh: Sequence[edge_typing.EdgeSuggestion],
    *,
    pinned: Mapping[str, str | None],
    effective: bool,
) -> _ChunkStored:
    """Read the outcome of the service's own persist off the store, not off its
    advisory text. A suggestion with no type is a failure rather than an answer
    and is never stored; one whose endpoints changed since they were pinned
    before the call is about content nobody typed and the service drops it; the
    rest are stored if and only if the store now serves them."""
    current = application_pending.current_finding_digest(layout.bundle_dir)
    storable: list[edge_typing.EdgeSuggestion] = []
    dropped = 0
    for suggestion in fresh:
        edge = suggestion.edge
        if suggestion.suggested_type is None or any(
            pinned.get(endpoint) != current(endpoint)
            for endpoint in (edge.source_id, edge.target_id)
        ):
            dropped += 1
        else:
            storable.append(suggestion)
    served = _partition_relations(
        layout, [suggestion.edge for suggestion in storable], effective=effective
    ).served
    persisted = len(served)
    return _ChunkStored(persisted, dropped, len(storable) - persisted)


def _run_relations(
    root: Path,
    layout: config.WorkspaceLayout,
    token: QuoteToken,
    context: _Context,
    *,
    size: int,
    stop: StopToken,
    ports: JudgingPorts,
    guard: _ObserverGuard,
) -> JudgingOutcome:
    """The relation_type run: the relations service, re-invoked once per chunk.

    Each invocation is bounded to `size` model calls and walks only the quoted
    edges still to do, so what the service types, persists and serves is its own;
    this run adds the ceiling, the stop between chunks, the retry of a busy
    workspace and the typed outcome around it. The service re-walks the bundle
    on every invocation (measured, see `RELATION_CHUNK_SIZE`)."""
    effective = context.binding.effective_confidential
    walk = _walk_relations(
        root, context, include_confidential=token.include_confidential, ports=ports
    )
    serves = _partition_relations(layout, walk.edges, effective=effective)
    digest_of = application_pending.current_finding_digest(layout.bundle_dir)
    now_items = {_edge_item(edge, digest_of): edge for edge in serves.to_type}
    quoted = set(token.work)
    judge = [now_items[item] for item in token.work if item in now_items]
    not_in_quote = tuple(item.work_key for item in now_items if item not in quoted)
    settled = sum(1 for item in token.work if item not in now_items)

    counted = _CeilingClient(context.client, token.calls)
    section = _RetryingSection(ports)
    judged = persisted = dropped = unpersisted = vanished = 0
    failure: BackendError | None = None
    failed_at: int | None = None
    cause: Literal["workspace_busy", "store_error"] | None = None
    status: Literal["completed", "stopped", "failed", "unpersisted"] = "completed"
    for start in range(0, len(judge), size):
        if stop.is_set():
            status = "stopped"
            break
        chunk = _ChunkTyping(layout, ports, judge[start:])
        outcome = relations.suggest_relations(
            root,
            relations.SuggestRelationsRequest(
                include_confidential=token.include_confidential,
                skip_confirmation=True,
                max_calls=size,
            ),
            _relations_ports(
                context,
                ports,
                client=cast(LLMBackend, counted),
                candidate_edges=chunk.candidate_edges,
                commit_section=section,
                suggest_edge_types=chunk.suggest_edge_types,
            ),
            _RelationsObserver(
                on_progress=_progress_hook(guard, before=judged, total=len(judge))
            ),
        )
        batch = outcome.batch
        if batch is None:
            # Nothing the run still owed is a candidate any more: those items
            # were settled elsewhere, not left undone.
            vanished = len(judge) - judged
            break
        before = judged
        judged += len(batch.results)
        failure = batch.failure
        if failure is not None:
            failed_at = before + (batch.failed_index or 1)
        kept = _classify_chunk(
            layout, batch.results, pinned=chunk.pinned, effective=effective
        )
        dropped += kept.dropped
        if kept.unpersisted:
            cause = "workspace_busy" if section.busy else "store_error"
            unpersisted = kept.unpersisted
            status = "unpersisted"
            break
        persisted += kept.persisted
        if batch.results:
            guard.chunk_persisted(persisted)
        if failure is not None:
            if isinstance(failure, BackendUnavailable | BackendModelNotFound):
                raise failure
            status = "failed"
            break
    reached = judged if failed_at is None else failed_at
    return JudgingOutcome(
        kind="relation_type",
        status=status,
        judged=judged,
        calls=counted.calls,
        persisted=persisted,
        unpersisted=unpersisted,
        unpersisted_cause=cause,
        dropped=dropped,
        deferred=len(judge) - reached - vanished,
        not_in_quote=not_in_quote,
        settled_since_quote=settled + vanished,
        failure=failure,
        failed_at=failed_at,
        observer_failed=guard.failed,
    )


class _VolatilityChunk:
    """The typing port one volatility invocation is given. It narrows the
    service's own leaf to the types of the chunk (`only_types`), so the service
    never asks a type the quote did not cover, and it keeps the persist of the
    service honest: the digest of every concept a prompt carried is pinned
    before the call and re-read after it, and an answer about a concept edited,
    raised or forgotten in between is removed from what the service persists
    (the leaf keys a stored answer on the exact prompt, so such a row would
    never be served and would only keep a forgotten concept's text)."""

    def __init__(
        self,
        layout: config.WorkspaceLayout,
        ports: JudgingPorts,
        chunk: Sequence[WorkItem],
    ) -> None:
        self._layout = layout
        self._ports = ports
        self.types = {item.work_key for item in chunk}
        refs = {ref for item in chunk for ref, _digest in item.member_digests}
        current = application_pending.current_finding_digest(layout.bundle_dir)
        self._pinned = {ref: current(ref) for ref in refs}
        self.stale: set[str] = set()
        self.computed: dict[str, volatility_typing.ComputedTier] = {}
        """Every type the model was asked, before the stale ones were withheld."""

    def suggest_volatility(
        self, bundle_dir: Path, **kwargs: object
    ) -> volatility_typing.TierSuggestionBatch:
        batch = self._ports.suggest_volatility(
            bundle_dir, only_types=self.types, **kwargs
        )
        self.computed = dict(batch.computed)
        current = application_pending.current_finding_digest(self._layout.bundle_dir)
        kept: dict[str, volatility_typing.ComputedTier] = {}
        for type_name, computed in batch.computed.items():
            if any(
                self._pinned.get(ref) != current(ref) for ref in computed.input_refs
            ):
                self.stale.add(type_name)
            else:
                kept[type_name] = computed
        return dataclasses.replace(batch, computed=kept)


class _VolatilityObserver:
    """The volatility service's observer, reduced to forwarding each typed
    type to `on_progress`."""

    def __init__(
        self,
        *,
        on_progress: Callable[[int, int, volatility_typing.TierSuggestion], None],
    ) -> None:
        self._on_progress = on_progress

    def walk_incomplete(
        self, bundle_dir: Path, *, include_confidential: bool, local_exemption: bool
    ) -> None:
        return None

    def progress_callback(
        self,
    ) -> Callable[[int, int, volatility_typing.TierSuggestion], None]:
        return self._on_progress


def _volatility_progress_hook(
    guard: _ObserverGuard, *, before: int, total: int
) -> Callable[[int, int, volatility_typing.TierSuggestion], None]:
    """The service's per-type progress rebased to the run: the index continues
    from `before` and the total is the run's, not the invocation's. Verdict
    only, never the rationale; a type the model could not tier reports
    `degraded`."""

    def on_progress(
        index: int, count: int, suggestion: volatility_typing.TierSuggestion
    ) -> None:
        guard.progress(
            JudgingProgress(
                index=before + index,
                total=total,
                work_key=suggestion.type_name,
                verdict=suggestion.suggested_tier or "degraded",
            )
        )

    return on_progress


def _classify_volatility_chunk(
    layout: config.WorkspaceLayout,
    cfg: config.Config,
    model: str,
    fresh: Sequence[volatility_typing.TierSuggestion],
    computed: Mapping[str, volatility_typing.ComputedTier],
    *,
    stale: set[str],
) -> _ChunkStored:
    """Read the outcome of the service's own persist off the store, not off its
    advisory log line. An answer with no tier is a failure rather than an answer
    and is never stored; one about a concept that changed during the call was
    withheld from the persist (`_VolatilityChunk`); the rest are stored if and
    only if the store now serves the exact question they answered."""
    served = volatility.cached_answers(layout, model, cfg.type_tiers)
    persisted = dropped = unpersisted = 0
    for suggestion in fresh:
        if suggestion.suggested_tier is None or suggestion.type_name in stale:
            dropped += 1
        elif served(suggestion.type_name, computed[suggestion.type_name].prompt_digest):
            persisted += 1
        else:
            unpersisted += 1
    return _ChunkStored(persisted, dropped, unpersisted)


def _run_volatility(
    root: Path,
    layout: config.WorkspaceLayout,
    cfg: config.Config,
    token: QuoteToken,
    context: _Context,
    *,
    size: int,
    stop: StopToken,
    ports: JudgingPorts,
    guard: _ObserverGuard,
) -> JudgingOutcome:
    """The volatility run: the volatility service, re-invoked once per chunk.

    Each invocation is bound to `size` model calls and is narrowed to the types
    of its chunk, so what the service asks, caches and persists is its own; this
    run adds the ceiling, the stop between chunks, the retry of a busy workspace
    and the typed outcome around it. The service re-walks the bundle on every
    invocation (measured, see `VOLATILITY_CHUNK_SIZE`)."""
    plan = _volatility_plan(
        layout,
        cfg,
        context,
        include_confidential=token.include_confidential,
        ports=ports,
    )
    digest_of = application_pending.current_finding_digest(layout.bundle_dir)
    now_items = [_volatility_item(p, digest_of) for p in plan if not p.answered]
    now_set = set(now_items)
    quoted = set(token.work)
    judge = [item for item in token.work if item in now_set]
    not_in_quote = tuple(item.work_key for item in now_items if item not in quoted)
    settled = sum(1 for item in token.work if item not in now_set)

    counted = _CeilingClient(context.client, token.calls)
    section = _RetryingSection(ports)
    judged = persisted = dropped = unpersisted = vanished = 0
    failure: BackendError | None = None
    failed_at: int | None = None
    cause: Literal["workspace_busy", "store_error"] | None = None
    status: Literal["completed", "stopped", "failed", "unpersisted"] = "completed"
    for start in range(0, len(judge), size):
        if stop.is_set():
            status = "stopped"
            break
        chunk_items = judge[start : start + size]
        chunk = _VolatilityChunk(layout, ports, chunk_items)
        outcome = volatility.suggest_volatility_tiers(
            root,
            volatility.VolatilityRequest(
                include_confidential=token.include_confidential,
                max_calls=size,
                use_cache=True,
            ),
            volatility.VolatilityPorts(
                chat_client=lambda cfg_, task: cast(LLMBackend, counted),
                resolve_local_exemption=lambda client, cfg_: context.local_exemption,
                suggest_volatility=chunk.suggest_volatility,
                commit_section=section,
            ),
            _VolatilityObserver(
                on_progress=_volatility_progress_hook(
                    guard, before=judged, total=len(judge)
                )
            ),
        )
        batch = outcome.batch
        before = judged + vanished
        fresh = [r for r in batch.results if r.type_name in chunk.computed]
        # A hit the store served by now was answered elsewhere, and a type that
        # stopped being a question is gone: both are settled, not left undone.
        if batch.failure is None:
            vanished += len(chunk_items) - len(fresh)
        else:
            vanished += len(batch.results) - len(fresh)
        judged += len(fresh)
        failure = batch.failure
        if failure is not None:
            failed_at = before + (batch.failed_index or 1)
        kept = _classify_volatility_chunk(
            layout,
            cfg,
            context.binding.model,
            fresh,
            chunk.computed,
            stale=chunk.stale,
        )
        dropped += kept.dropped
        if kept.unpersisted:
            cause = "workspace_busy" if section.busy else "store_error"
            unpersisted = kept.unpersisted
            status = "unpersisted"
            break
        persisted += kept.persisted
        if fresh:
            guard.chunk_persisted(persisted)
        if failure is not None:
            if isinstance(failure, BackendUnavailable | BackendModelNotFound):
                raise failure
            status = "failed"
            break
    reached = judged + vanished if failed_at is None else failed_at
    return JudgingOutcome(
        kind="volatility",
        status=status,
        judged=judged,
        calls=counted.calls,
        persisted=persisted,
        unpersisted=unpersisted,
        unpersisted_cause=cause,
        dropped=dropped,
        deferred=len(judge) - reached,
        not_in_quote=not_in_quote,
        settled_since_quote=settled + vanished,
        failure=failure,
        failed_at=failed_at,
        observer_failed=guard.failed,
    )
