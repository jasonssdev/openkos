"""The `contradictions` use case, as an application service (ADR-0018, issue
#1168; the findings slice after `ingest_service` and `reindex_service`).

Four operations over the workspace at an explicit `root`, each returning a
typed outcome and raising typed `ContradictionsRefused` subclasses -- none
prompts, renders, reads the current directory, commits, or raises
`typer.Exit`. The CLI `contradictions` verb is one adapter over them; MVP 4's
scheduled maintenance is meant to be another.

* `run_contradictions` -- the LLM check over already-related pairs: plan the
  candidates, serve every digest-fresh persisted verdict without a model call
  (#653), judge the rest, persist the fresh verdicts to `.openkos/findings.db`,
  and return the plan-ordered verdicts with everything the report needs;
* `record_contradiction_decision` -- the `--decline` / `--reopen` writes: one
  decision record under `bundle/.state/decisions/**`, never a concept file, and
  never a read of the findings store;
* `list_declined` -- the `--declined` listing, each record joined to its
  persisted finding when one exists.

What it does NOT own, and how it reaches each through a parameter instead (the
layering invariant forbids importing `openkos.cli`, `typer`, `rich` or
`openkos.vcs` here):

* every word the user reads -- a `ContradictionsObserver` receives the few
  advisories that occur MID-run (before any verdict exists) and the adapter
  renders the returned `ContradictionsOutcome`, notices included, afterwards;
* the concrete effects -- `ContradictionsPorts` carries the chat-client
  factory, the local-exemption resolution, the proximity-source opener, the
  graph builder, the planner, the judge, the findings persistence and the
  digest function, so each stays a substitutable seam and the adapter's own
  (monkeypatchable) implementations stay adapter-side;
* the auto-commit of a decision -- the outcome names the workspace-relative
  path and the caller stages it, because git is an adapter concern;
* the per-pair progress hook -- the observer decides whether there is one (the
  CLI gates it on a TTY).

The run never writes the BUNDLE. Persisting a finding is `.openkos/` derived
state, the same carve-out `curate`'s Contradictions stage holds, and it is
fail-open: a locked or corrupt store degrades to one advisory rather than
discarding verdicts the model was already paid for (#441's posture).
"""

from __future__ import annotations

import dataclasses
import sqlite3
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, cast

from openkos import config
from openkos.application import backends as application_backends
from openkos.application import pending as application_pending
from openkos.bundle import decisions as bundle_decisions
from openkos.graph import proximity
from openkos.graph.base import GraphStore
from openkos.graph.sqlite_graph import SqliteGraphStore
from openkos.llm.base import (
    BackendError,
    BackendModelNotFound,
    BackendUnavailable,
    LLMBackend,
)
from openkos.model import okf
from openkos.resolution.contradiction import (
    CandidatePlan,
    ContradictionBatch,
    ContradictionVerdict,
    contradiction_truncation_notice,
    is_high_confidence_contradiction,
    vacuous_coverage_notice,
)
from openkos.resolution.contradiction import Verdict as ContradictionVerdictValue
from openkos.resolution.edge_typing import (
    candidate_truncation_notice,
    quarantined_candidate_notice,
)
from openkos.state import derived, findings

_VERB = "contradictions"

Warn = Callable[[str], None]
"""Receives one note a `bundle` reader/writer produced; the caller decides how
it is shown."""

SpecKey = tuple[tuple[str, str], str | None]
"""The `(pair_ids, merged_absorbed_id)` identity of one candidate -- the SAME
identity `bundle.decisions.decision_key_for` and the findings store key on."""


# -- Typed refusals ---------------------------------------------------------


class ContradictionsRefused(Exception):
    """Base of every refusal the service raises. `message` is the complete,
    user-facing text (the wording the CLI has always printed), so an adapter
    renders it verbatim and maps the TYPE to an exit code. Deliberately not an
    `OSError`/`ValueError`/`sqlite3.Error`: the service's own `except` clauses
    must never swallow one of these."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NotAWorkspace(ContradictionsRefused):
    """`root` is not an OpenKOS workspace."""


class WorkspaceUnreadable(ContradictionsRefused):
    """Reading `openkos.yaml` raised `OSError`/`ValueError`."""


class BackendNotReachable(ContradictionsRefused):
    """The chat backend could not be reached."""


class ModelNotInstalled(ContradictionsRefused):
    """The configured chat model is not installed on the backend."""


class BackendFailed(ContradictionsRefused):
    """Any other backend failure that has no more specific refusal."""


# -- Inputs / outputs -------------------------------------------------------


class ContradictionsObserver(Protocol):
    """The adapter's window onto a run. Only what happens BEFORE a verdict
    exists is reported here; everything the report shows comes back in the
    `ContradictionsOutcome`."""

    def exemption_resolved(self, local_exemption: bool) -> None:
        """The chat client exists and the confidential local exemption is
        resolved; the adapter may warn that the walk is incomplete."""

    def vacuous_coverage(self, notice: str) -> None:
        """The plan has no typed-edge pairs (#557). Called BEFORE the judging
        loop, so an operator who only needed typed-edge coverage can abort."""

    def persisted_findings_unreadable(self, error: Exception) -> None:
        """`findings.db` is present but could not be read; every candidate is
        judged fresh instead."""

    def progress_callback(self) -> Callable[[int, int, object], None] | None:
        """The per-pair progress hook, or `None` for silence."""

    def persist_failed(self, error: Exception) -> None:
        """Persisting the fresh verdicts failed; they are still returned."""


@dataclass(frozen=True)
class ContradictionsPorts:
    """The effects the service takes from its caller."""

    chat_client: Callable[[config.Config], LLMBackend]
    local_exemption: Callable[[application_backends.HasLocality, config.Config], bool]
    open_proximity: Callable[[Path], proximity.VectorProximitySource | None]
    build_graph: Callable[..., SqliteGraphStore]
    plan_candidates: Callable[..., CandidatePlan]
    find_contradictions: Callable[..., tuple[ContradictionBatch, int]]
    persist_findings: Callable[
        [config.WorkspaceLayout, CandidatePlan, Sequence[ContradictionVerdict]], None
    ]
    finding_input_digests: Callable[[Path, Any], tuple[findings.InputDigest, ...]]
    zero_state_message: Callable[[config.WorkspaceLayout, GraphStore, bool], str]
    """The state-message text for a run with no candidates: the layout, the
    open store and whether embeddings are missing. The wording is shared with
    `suggest-relations`, so it stays adapter-side."""


@dataclass(frozen=True)
class ContradictionsOptions:
    show_all: bool = False
    include_deprecated: bool = False
    include_confidential: bool = False
    fresh: bool = False


@dataclass(frozen=True)
class ContradictionsOutcome:
    """A completed run, with everything the report renders.

    `displayed` is the verdict list after the `--all`/high-confidence filter and
    the declined-finding suppression; `verdicts` is every verdict served or
    judged, in plan order, because the zero-candidate and partial-failure
    reporting describe what was JUDGED, not what was shown. `zero_state` is set
    only when nothing was judged on a clean run, and then `displayed` is empty
    and `truncation_notice` is `None`: the report stops at that message."""

    model: str
    plan: CandidatePlan
    judged_plan: CandidatePlan
    batch: ContradictionBatch
    verdicts: tuple[ContradictionVerdict, ...]
    displayed: tuple[ContradictionVerdict, ...]
    served_count: int
    fresh_count: int
    vacuous_notice: str | None
    candidate_notice: str | None
    quarantine_notice: str | None
    truncation_notice: str | None
    zero_state: str | None


@dataclass(frozen=True)
class ContradictionRuling:
    """A decision that was written: the pair in its canonical (sorted) order,
    the merged-body discriminator, and the workspace-relative path of the
    sidecar for the caller's auto-commit."""

    pair: tuple[str, str]
    merged_absorbed_id: str | None
    rel_path: str


@dataclass(frozen=True)
class DeclinedFinding:
    """One declined record, joined to its persisted finding when one exists."""

    record: bundle_decisions.DecisionRecord
    finding: findings.PersistedFinding | None


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _require_workspace(root: Path) -> config.WorkspaceLayout:
    reason = config.require_workspace(root)
    if reason is not None:
        raise NotAWorkspace(f"openkos {_VERB}: refusing to run -- {reason}.")
    return config.WorkspaceLayout(root)


# -- Decisions --------------------------------------------------------------


def sorted_decision_pair(pair: tuple[str, str]) -> tuple[str, str]:
    """Canonicalize an operator-supplied `--decline`/`--reopen` pair into
    `decision_key_for`'s expected order (sorted `pair_ids`). The caller may
    type either order, but the identity -- and the decisions sidecar it is
    stored under (`pair_ids[0]`'s own file) -- must be stable regardless."""
    pair_a, pair_b = sorted(pair)
    return pair_a, pair_b


def apply_contradiction_decision(
    layout: config.WorkspaceLayout,
    pair: tuple[str, str],
    merged_absorbed_id: str | None,
    *,
    target_state: bundle_decisions.DecisionState,
    on_warning: Warn | None = None,
    clock: Callable[[], datetime] = _utc_now,
) -> str:
    """Write (or update in place) the single decision record identified by
    `pair`/`merged_absorbed_id` to `target_state`, returning the
    workspace-relative decision path for the caller's auto-commit list.

    Never opens `.openkos/findings.db`: decline/reopen never read the findings
    store as a precondition -- a matching findings row is not required either
    way. Any existing record for the SAME `decision_key` is replaced in place
    (idempotent re-decline/re-reopen); every OTHER record already in the owning
    sidecar is preserved, mirroring `write_decisions`'s full-replace contract."""
    pair_ids = sorted_decision_pair(pair)
    key = bundle_decisions.decision_key_for(pair_ids, merged_absorbed_id)
    owner_id = pair_ids[0]
    existing = bundle_decisions.read_decisions(owner_id, layout.bundle_dir)
    records = [record for record in existing if record.decision_key != key]
    records.append(
        bundle_decisions.DecisionRecord(
            decision_key=key,
            pair_ids=pair_ids,
            merged_absorbed_id=merged_absorbed_id,
            state=target_state,
            decided_at=clock().isoformat(),
        )
    )
    path = bundle_decisions.write_decisions(
        owner_id, layout.bundle_dir, records=records, on_warning=on_warning
    )
    return f"bundle/{path.relative_to(layout.bundle_dir).as_posix()}"


def record_contradiction_decision(
    root: Path,
    pair: tuple[str, str],
    merged_absorbed_id: str | None,
    *,
    target_state: bundle_decisions.DecisionState,
    on_warning: Warn | None = None,
    clock: Callable[[], datetime] = _utc_now,
) -> ContradictionRuling:
    """Persist one `--decline` (`"declined"`) or `--reopen` (`"open"`) ruling.
    Short-circuits before any graph build or model client by construction."""
    layout = _require_workspace(root)
    rel_path = apply_contradiction_decision(
        layout,
        pair,
        merged_absorbed_id,
        target_state=target_state,
        on_warning=on_warning,
        clock=clock,
    )
    return ContradictionRuling(
        pair=sorted_decision_pair(pair),
        merged_absorbed_id=merged_absorbed_id,
        rel_path=rel_path,
    )


def _open_findings_by_decision_key(
    layout: config.WorkspaceLayout,
) -> dict[str, findings.PersistedFinding]:
    """Every persisted finding, keyed by the SAME `decision_key_for` identity a
    decision record is keyed on -- a read-time join, used ONLY by the declined
    listing's stale-label lookup."""
    return {
        bundle_decisions.decision_key_for(pf.pair_ids, pf.merged_absorbed_id): pf
        for pf in application_pending.persisted_findings(layout)
    }


def list_declined(root: Path) -> tuple[DeclinedFinding, ...]:
    """Every declined record, ordered by decision key, from
    `bundle.decisions.iter_decisions`'s INCLUDE walk -- never from a fresh model
    judgment. The findings store is opened only when there is something to
    join."""
    layout = _require_workspace(root)
    declined_records: list[bundle_decisions.DecisionRecord] = []
    for decisions_path in bundle_decisions.iter_decisions(layout.bundle_dir):
        metadata, _ = okf.load_frontmatter(decisions_path.read_text(encoding="utf-8"))
        concept_id = metadata.get("concept_id")
        if not isinstance(concept_id, str):
            continue
        # Read the WALKED path, not a path rebuilt from the sidecar's own
        # `concept_id` content (read-side traversal guard).
        for record in bundle_decisions.read_decisions_at(decisions_path):
            if record.state == "declined":
                declined_records.append(record)
    if not declined_records:
        return ()
    findings_by_key = _open_findings_by_decision_key(layout)
    declined_records.sort(key=lambda record: record.decision_key)
    return tuple(
        DeclinedFinding(record, findings_by_key.get(record.decision_key))
        for record in declined_records
    )


# -- The run ----------------------------------------------------------------


def contradiction_spec_key(spec: object) -> SpecKey:
    """The `(pair_ids, merged_absorbed_id)` identity of one candidate spec,
    spelled as a plain tuple so specs and persisted rows can be joined without
    hashing the (unhashable, ledger-entry-carrying) spec itself. `spec` is typed
    loosely because the candidate spec is `resolution.contradiction`'s own
    private shape; only its two identity attributes are read here."""
    merge_entry = getattr(spec, "merge_entry", None)
    absorbed = merge_entry.absorbed_id if merge_entry is not None else None
    return (spec.pair_ids, absorbed)  # type: ignore[attr-defined]


def _partition_persisted_serves(
    layout: config.WorkspaceLayout,
    plan: CandidatePlan,
    *,
    finding_input_digests: Callable[[Path, Any], tuple[findings.InputDigest, ...]],
    observer: ContradictionsObserver,
) -> tuple[dict[SpecKey, ContradictionVerdict], CandidatePlan]:
    """Split `plan` into verdicts servable from `.openkos/findings.db` and the
    candidates that still need a model call (#653).

    A candidate is SERVED iff its latest persisted finding's stored digest rows
    are exactly the rows `finding_input_digests` computes for the pair's CURRENT
    bytes -- the same function that recorded them, so equality means "nothing
    this verdict was computed from has changed". Everything else re-judges,
    conservatively: no persisted row, any digest drift, an unreadable input
    (fewer current rows than stored), a stored verdict value outside the enum --
    or a present-but-corrupt store, which degrades to one advisory and a full
    fresh judge (#685 item 4) rather than crashing before any model spend.
    `consistent` rows serve exactly like `contradicts` rows -- they are what
    proves a pair needs no re-judging (the display filter, not this partition,
    decides what is shown).

    Returns `(served_by_key, judged_plan)`: the served verdicts keyed by
    `contradiction_spec_key`, and a copy of `plan` whose `specs` are only the
    candidates to judge -- totals untouched, so the truncation notice keeps
    describing the ORIGINAL plan."""
    if not layout.findings_db_path.exists():
        return {}, plan
    # Fail OPEN to judging on a present-but-corrupt store: this read runs BEFORE
    # any model spend, so a crash here would cost the whole run to protect a
    # cache. `sqlite3.Error` covers a non-database file; `OSError` an
    # unreadable one.
    try:
        conn = derived.open_derived_connection(layout.findings_db_path)
        try:
            persisted = findings.open_findings(conn)
        finally:
            conn.close()
    except (OSError, sqlite3.Error) as exc:
        observer.persisted_findings_unreadable(exc)
        return {}, plan
    # Insertion order is `record_findings` order: iterating forward and
    # overwriting leaves the LATEST row for each identity -- a pair curate
    # judged twice serves its newest verdict, never a superseded one.
    latest: dict[SpecKey, findings.PersistedFinding] = {}
    for pf in persisted:
        latest[(pf.pair_ids, pf.merged_absorbed_id)] = pf

    served: dict[SpecKey, ContradictionVerdict] = {}
    to_judge = []
    for spec in plan.specs:
        key = contradiction_spec_key(spec)
        row = latest.get(key)
        # Row lookup FIRST: a candidate with no persisted finding is judged
        # without paying the per-pair digest I/O -- the hashes exist only to
        # compare against a stored row.
        if row is None:
            to_judge.append(spec)
            continue
        current = finding_input_digests(layout.bundle_dir, spec)
        if not current or tuple(row.input_digests) != current:
            to_judge.append(spec)
            continue
        try:
            verdict_value = ContradictionVerdictValue(row.verdict)
        except ValueError:
            to_judge.append(spec)
            continue
        served[key] = ContradictionVerdict(
            pair_ids=row.pair_ids,
            verdict=verdict_value,
            confidence=row.confidence,
            rationale=row.rationale,
            conflicting_claims=row.conflicting_claims,
            merged_absorbed_id=row.merged_absorbed_id,
        )
    return served, dataclasses.replace(plan, specs=tuple(to_judge))


def _reassemble(
    plan: CandidatePlan,
    judged_plan: CandidatePlan,
    served_by_key: dict[SpecKey, ContradictionVerdict],
    fresh_verdicts: list[ContradictionVerdict],
) -> list[ContradictionVerdict]:
    """The run's verdicts in the ORIGINAL plan order: a served verdict and a
    fresh one must interleave exactly where their candidates sat, or the
    display order would depend on what happened to be cached. A judged result
    that maps onto no plan spec (an injected finder in tests, or a future
    planner/finder drift) is paid-for work -- appended after the plan-ordered
    ones rather than silently dropped."""
    fresh_by_key = {
        contradiction_spec_key(spec): verdict
        for spec, verdict in zip(judged_plan.specs, fresh_verdicts, strict=False)
    }
    verdicts: list[ContradictionVerdict] = []
    placed: set[int] = set()
    for spec in plan.specs:
        key = contradiction_spec_key(spec)
        if key in served_by_key:
            verdicts.append(served_by_key[key])
        elif key in fresh_by_key:
            verdicts.append(fresh_by_key[key])
            placed.add(id(fresh_by_key[key]))
    verdicts.extend(v for v in fresh_verdicts if id(v) not in placed)
    return verdicts


def run_contradictions(
    root: Path,
    *,
    options: ContradictionsOptions,
    ports: ContradictionsPorts,
    observer: ContradictionsObserver,
) -> ContradictionsOutcome:
    """Run the contradiction check over the workspace at `root`.

    Order matters and is preserved from the CLI it was lifted out of: the
    workspace check, the config read, the client and the exemption, the graph
    built ONCE and held open across planning and judging (graph-projection
    reuse, #196), the vacuous-coverage advisory BEFORE any model spend, the
    serve/judge split, the judge with its ordered error ladder (the specific
    backend refusals BEFORE the generic one, since both subclass
    `BackendError`), the fail-open persistence, and only then the notices that
    read the still-open store."""
    layout = _require_workspace(root)
    try:
        cfg = config.read_config(root)
    except (OSError, ValueError) as exc:
        raise WorkspaceUnreadable(
            f"openkos {_VERB}: failed while reading the workspace -- {exc}."
        ) from exc

    llm = ports.chat_client(cfg)
    local_exemption = ports.local_exemption(
        cast(application_backends.HasLocality, llm), cfg
    )
    observer.exemption_resolved(local_exemption)

    # Source-then-build prologue: the proximity source is closed as early as
    # possible, right after `build_graph` consumes it. The LLM loop runs INSIDE
    # `find_contradictions`, so the store stays open across it: splitting this
    # block would require two builds, which is exactly what #196 removes.
    source = ports.open_proximity(layout.vectors_db_path)
    embeddings_missing = source is None
    try:
        graph: AbstractContextManager[SqliteGraphStore] = ports.build_graph(
            layout.bundle_dir, candidates=source
        )
    finally:
        if source is not None:
            source.close()

    with graph as store:
        # Built here, not inside `find_contradictions`, because the cap-reached
        # line has to name WHICH KIND was truncated (#444) -- and passing it in
        # means that line describes the exact list that was judged.
        plan = ports.plan_candidates(
            layout.bundle_dir,
            store=store,
            include_deprecated=options.include_deprecated,
            include_confidential=options.include_confidential,
            local_exemption=local_exemption,
        )
        # Vacuous-coverage guard (#557): warn BEFORE the judging loop, not
        # after -- the run costs one LLM call per candidate.
        vacuous = vacuous_coverage_notice(plan)
        if vacuous is not None:
            observer.vacuous_coverage(vacuous)
        # #653: serve persisted, digest-fresh findings instead of re-billing the
        # model for verdicts the store already holds. `--fresh` bypasses the
        # store; the truncation/vacuous notices keep describing the ORIGINAL
        # plan either way.
        served_by_key: dict[SpecKey, ContradictionVerdict] = {}
        judged_plan = plan
        if not options.fresh:
            served_by_key, judged_plan = _partition_persisted_serves(
                layout,
                plan,
                finding_input_digests=ports.finding_input_digests,
                observer=observer,
            )
        try:
            batch, _total_pairs = ports.find_contradictions(
                layout.bundle_dir,
                llm=llm,
                include_deprecated=options.include_deprecated,
                include_confidential=options.include_confidential,
                local_exemption=local_exemption,
                store=store,
                plan=judged_plan,
                on_progress=observer.progress_callback(),
            )
        except BackendUnavailable as exc:
            raise BackendNotReachable(
                f"openkos {_VERB}: failed -- {exc}. Start it with "
                f"`ollama serve`, then try again."
                f"{application_backends.DOCTOR_HINT}"
            ) from exc
        except BackendModelNotFound as exc:
            raise ModelNotInstalled(
                f"openkos {_VERB}: failed -- model '{cfg.model}' is not "
                f"installed. Pull it with `ollama pull {cfg.model}`, then try "
                "again."
            ) from exc
        # The two specific handlers above MUST precede this generic handler:
        # both subclass `BackendError`, so reordering would silently funnel them
        # into this fallback and lose their actionable remediation messages.
        except BackendError as exc:
            raise BackendFailed(f"openkos {_VERB}: failed -- {exc}.") from exc

        # #653: freshly judged verdicts persist through `curate`'s EXACT write
        # path, so the next default run serves them -- `.openkos/` derived state
        # only, never a bundle write. Partial batches persist their completed
        # prefix. Fail-open: this write runs AFTER the paid model calls and
        # BEFORE any verdict is displayed, so a locked or corrupt findings.db
        # degrades to one advisory, never a crash that discards the paid-for
        # verdicts (#441's posture).
        fresh_verdicts = list(batch.results)
        if fresh_verdicts:
            try:
                ports.persist_findings(layout, judged_plan, fresh_verdicts)
            except (OSError, sqlite3.Error) as exc:
                observer.persist_failed(exc)

        verdicts = _reassemble(plan, judged_plan, served_by_key, fresh_verdicts)

        # Both notices re-derive their counts through the sensitivity filter,
        # so neither discloses a pre-cap volume that includes a confidential
        # endpoint the judge already excluded. Read INSIDE the `with` block,
        # since `store` closes below.
        candidate_notice = candidate_truncation_notice(
            store.candidate_report,
            layout.bundle_dir,
            include_confidential=options.include_confidential,
            local_exemption=local_exemption,
        )
        # #841: the unjudged-source withholding -- the contradiction engine
        # reads the same candidate projection, so its queue is also smaller than
        # the bundle could produce.
        quarantine_notice = quarantined_candidate_notice(
            store.candidate_report,
            layout.bundle_dir,
            include_confidential=options.include_confidential,
            local_exemption=local_exemption,
        )
        zero_state: str | None = None
        if not verdicts and batch.failure is None:
            # Guarded on a clean run only (#441): a first-candidate failure also
            # carries zero verdicts, and the zero-candidates state message would
            # then claim an empty graph the failure, not the projection,
            # produced.
            zero_state = ports.zero_state_message(layout, store, embeddings_missing)

    truncation_notice: str | None = None
    displayed: list[ContradictionVerdict] = []
    if zero_state is None:
        truncation_notice = contradiction_truncation_notice(plan)
        shown = (
            verdicts
            if options.show_all
            else [v for v in verdicts if is_high_confidence_contradiction(v)]
        )
        # A verdict whose decision_key already carries a `declined` decision is
        # dropped from the DISPLAY list only -- it still counts in `verdicts`
        # for the zero-candidate/partial-failure reporting, which describe what
        # was JUDGED, not what was declined.
        displayed = [
            v
            for v in shown
            if not application_pending.is_contradiction_declined(
                layout, v.pair_ids, v.merged_absorbed_id
            )
        ]

    return ContradictionsOutcome(
        model=cfg.model,
        plan=plan,
        judged_plan=judged_plan,
        batch=batch,
        verdicts=tuple(verdicts),
        displayed=tuple(displayed),
        served_count=len(served_by_key),
        fresh_count=len(fresh_verdicts),
        vacuous_notice=vacuous,
        candidate_notice=candidate_notice,
        quarantine_notice=quarantine_notice,
        truncation_notice=truncation_notice,
        zero_state=zero_state,
    )
