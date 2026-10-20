"""Re-checking one changed finding (the `recheck-changed-findings` change,
#1523; ADR-0059's quote, consent, run protocol).

A `contradiction` or `revision` row whose pages changed is listed with
`fresh=False`, and every write against it answers `Stale("row_inputs_changed")`.
Re-detection is a model call, so the app cannot make the row current for free;
this module lets a person agree to it for ONE row. The quote is for the row's own
pair (a `JudgingQuote` whose work is that single pair), the run judges that pair
and nothing else through the existing detection (`find_contradictions` over a
one-candidate plan, `judge_revisions` over a one-candidate plan), and the row is
then made current or retired. The bundle is never re-detected.

What a run does to the row, once the verdict is stored:

- the finding still holds: the open row is replaced by a fresh one (the old row
  is retired as stale, the new one is pending), exactly as the producers do;
- the finding no longer holds (consistent, uncertain, not actionable): the open
  row is retired as stale, so it leaves To review until a detection pass finds
  it again;
- the pair's pages moved while the model was called: nothing is published, the
  verdict is not stored against content nobody judged, and the row stays as it
  was.

A quote makes no model call. A pair the candidate search no longer keeps, or a
row no longer open, is a quote with nothing to judge: it is never an error.
Like the rest of the judging surface it is OWNER-LOCAL and plain data.
"""

import contextlib
import dataclasses
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from openkos import config, lock
from openkos.application import (
    curate_decisions,
    curate_queue,
    finding_inputs,
    queue_producers,
)
from openkos.application.contradictions_service import contradiction_spec_key
from openkos.application.judging import (
    JudgingKind,
    JudgingObserver,
    JudgingOutcome,
    JudgingPorts,
    JudgingProgress,
    JudgingQuote,
    QuoteNotice,
    QuoteStale,
    QuoteToken,
    RunRefused,
    WorkItem,
    _CeilingClient,
    _Context,
    _context,
    _insecure_key_notice,
    _ObserverGuard,
    _open_workspace,
    _RetryingSection,
    _unattended_notice,
    changed_fields,
)
from openkos.application.runtime import StopToken
from openkos.llm.base import (
    BackendError,
    BackendModelNotFound,
    BackendUnavailable,
)
from openkos.resolution import contradiction
from openkos.state import derived
from openkos.state import pending_queue as pq

FINDING_KINDS: Final = ("contradiction", "revision")
"""The kinds this module quotes and runs: the two whose rows record a finding."""

_RUNNABLE: Final = ("contradiction",)


@dataclass(frozen=True)
class _Target:
    """The pair behind one open row."""

    key: str
    pair: tuple[str, str]
    absorbed: str | None = None
    """A merged-body contradiction's absorbed page; `None` for a typed edge."""


@dataclass(frozen=True)
class _Found:
    """What the LLM-free plan holds for a target: its work item and the
    candidate the judge is handed."""

    item: WorkItem
    spec: contradiction._CandidateSpec
    plan: contradiction.CandidatePlan


def _check_kind(kind: str, decision_key: str) -> None:
    if kind not in _RUNNABLE:
        raise ValueError(f"judging kind {kind!r} is not a kind this surface re-checks")
    if decision_key.partition(":")[0] != kind:
        raise ValueError(f"decision_key must name a {kind} decision")


def _target(
    layout: config.WorkspaceLayout, kind: JudgingKind, decision_key: str
) -> _Target | None:
    """The open row's pair, or `None` when the row is not open or names no pair.
    `decision_key` already names a row of `kind` (`_check_kind`)."""
    row = curate_queue.open_row_by_key(layout, decision_key)
    if row is None:
        return None
    fields = curate_decisions.contradiction_fields(row.payload)
    if fields is None:
        return None
    first, second, absorbed, _rationale, _claims = fields
    return _Target(decision_key, (first, second), absorbed)


# -- contradiction ----------------------------------------------------------------


def _find_contradiction(
    layout: config.WorkspaceLayout,
    context: _Context,
    target: _Target,
    *,
    include_confidential: bool,
    ports: JudgingPorts,
) -> _Found | None:
    """The target's candidate in the engine's own plan, or `None` when the search
    no longer keeps it. Planning is LLM-free, so a quote and a run share it."""
    source = ports.open_proximity(layout.vectors_db_path)
    try:
        graph = ports.build_graph(layout.bundle_dir, candidates=source)
    finally:
        if source is not None:
            source.close()
    with graph as store:
        plan = ports.plan_contradictions(
            layout.bundle_dir,
            store=store,
            include_deprecated=False,
            include_confidential=include_confidential,
            local_exemption=context.local_exemption,
        )
    wanted = (target.pair, target.absorbed)
    for spec in plan.specs:
        if contradiction_spec_key(spec) == wanted:
            rows = finding_inputs.finding_input_digests(layout.bundle_dir, spec)
            item = WorkItem(
                work_key=target.key,
                member_digests=tuple((row.input_ref, row.digest) for row in rows),
            )
            return _Found(item, spec, dataclasses.replace(plan, specs=(spec,)))
    return None


def quote_finding(
    root: Path,
    kind: JudgingKind,
    decision_key: str,
    *,
    include_confidential: bool = False,
    ports: JudgingPorts,
) -> JudgingQuote:
    """Price re-checking the one pair behind the open `decision_key` row, with no
    model call. Raises `JudgingRefused` when `root` is not a readable workspace
    and `ValueError` for a kind this module does not re-check or a key that does
    not name a decision of that kind. A row no longer open, or a pair the search
    no longer keeps, quotes no work."""
    _check_kind(kind, decision_key)
    layout, cfg = _open_workspace(root, "quote")
    context = _context(
        cfg, kind, include_confidential=include_confidential, ports=ports
    )
    binding = context.binding
    target = _target(layout, kind, decision_key)
    found = (
        None
        if target is None
        else _find_contradiction(
            layout,
            context,
            target,
            include_confidential=include_confidential,
            ports=ports,
        )
    )
    work = () if found is None else (found.item,)
    notices: list[QuoteNotice] = [
        QuoteNotice("model", model=binding.model, backend=cfg.backend)
    ]
    unattended = _unattended_notice(layout, cfg, ports.now())
    if unattended is not None:
        notices.append(unattended)
    insecure = _insecure_key_notice(cfg)
    if insecure is not None:
        notices.append(insecure)
    token = QuoteToken(
        binding=binding,
        include_confidential=include_confidential,
        include_uncertain=False,
        work=work,
        calls=len(work),
    )
    return JudgingQuote(
        kind=kind,
        total=0 if target is None else 1,
        served=0,
        uncertain=0,
        to_judge=len(work),
        calls=len(work),
        binding=binding,
        notices=tuple(notices),
        token=token,
    )


# -- the run ------------------------------------------------------------------------


@dataclass
class _Tally:
    judged: int = 0
    persisted: int = 0
    dropped: int = 0
    unpersisted: int = 0
    cause: Literal["workspace_busy", "store_error"] | None = None
    settled: int = 0
    failure: BackendError | None = None
    failed_at: int | None = None


def _publish_contradiction(
    layout: config.WorkspaceLayout,
    target: _Target,
    found: _Found,
    verdict: contradiction.ContradictionVerdict,
    section: Callable[[], contextlib.AbstractContextManager[None]],
) -> None:
    """Replace the open row with the new verdict, or retire it when the
    disagreement no longer holds."""
    conn = derived.open_derived_connection(layout.findings_db_path)
    try:
        if verdict.verdict is contradiction.Verdict.CONTRADICTS:
            proposal = queue_producers.contradiction_proposal(
                verdict,
                found.spec,
                input_digests=finding_inputs.finding_input_digests,
                bundle_dir=layout.bundle_dir,
            )
            queue_producers.publish(
                conn,
                "contradiction",
                [proposal],
                complete=False,
                commit_section=section,
                bundle_dir=layout.bundle_dir,
            )
        else:
            pq.retire_open_by_key(conn, target.key, commit_section=section)
    finally:
        conn.close()


def _run_contradiction(
    layout: config.WorkspaceLayout,
    token: QuoteToken,
    context: _Context,
    item: WorkItem,
    *,
    counted: _CeilingClient,
    ports: JudgingPorts,
    guard: _ObserverGuard,
    tally: _Tally,
    index: int,
    total: int,
) -> None:
    """Judge one quoted pair, store its verdict and publish the row."""
    target = _target(layout, "contradiction", item.work_key)
    found = (
        None
        if target is None
        else _find_contradiction(
            layout,
            context,
            target,
            include_confidential=token.include_confidential,
            ports=ports,
        )
    )
    if target is None or found is None or found.item != item:
        tally.settled += 1
        return
    pinned = finding_inputs.finding_input_digests(layout.bundle_dir, found.spec)

    def on_progress(
        done: int, count: int, verdict: contradiction.ContradictionVerdict
    ) -> None:
        guard.progress(
            JudgingProgress(
                index=index,
                total=total,
                work_key=item.work_key,
                verdict=verdict.verdict.value,
            )
        )

    batch, _pairs = ports.find_contradictions(
        layout.bundle_dir,
        llm=counted,
        include_deprecated=False,
        include_confidential=token.include_confidential,
        local_exemption=context.local_exemption,
        plan=found.plan,
        on_progress=on_progress,
    )
    tally.judged += len(batch.results)
    if batch.failure is not None:
        tally.failure = batch.failure
        tally.failed_at = index
    if not batch.results:
        return
    section = _RetryingSection(ports)
    try:
        stored = finding_inputs.persist_findings(
            layout,
            found.plan,
            batch.results,
            judged_inputs=(pinned,),
            commit_section=section,
        )
        if stored:
            _publish_contradiction(layout, target, found, batch.results[0], section)
    except lock.WorkspaceBusyError:
        tally.unpersisted += 1
        tally.cause = "workspace_busy"
        return
    except sqlite3.Error, OSError:
        tally.unpersisted += 1
        tally.cause = "store_error"
        return
    tally.persisted += stored
    tally.dropped += len(batch.results) - stored


def run_finding(
    root: Path,
    token: QuoteToken | None,
    *,
    stop: StopToken,
    ports: JudgingPorts,
    observer: JudgingObserver | None = None,
) -> JudgingOutcome | QuoteStale | RunRefused:
    """Re-check at most what `token` was quoted for, then make its row current or
    retire it.

    `token=None` is refused before the configuration is read. A moved binding
    field returns `QuoteStale` before any call. A quoted pair whose pages moved
    since the quote, or whose row is no longer open, is `settled_since_quote` and
    is not judged. `stop` is observed before the pair. `BackendUnavailable` and
    `BackendModelNotFound` propagate unchanged; any other backend error ends the
    run as a returned `failure`. Raises `JudgingRefused` when `root` is not a
    readable workspace and `ValueError` for a kind this module does not run."""
    if token is None:
        return RunRefused("no_quote_token")
    kind = token.binding.kind
    if kind not in _RUNNABLE:
        raise ValueError(f"judging kind {kind!r} cannot be re-checked yet")
    layout, cfg = _open_workspace(root, "run")
    context = _context(
        cfg, kind, include_confidential=token.include_confidential, ports=ports
    )
    moved = changed_fields(token.binding, context.binding)
    if moved:
        return QuoteStale(kind, moved)

    guard = _ObserverGuard(observer if observer is not None else JudgingObserver())
    counted = _CeilingClient(context.client, token.calls)
    tally = _Tally()
    status: Literal["completed", "stopped", "failed", "unpersisted"] = "completed"
    reached = 0
    for index, item in enumerate(token.work, start=1):
        if stop.is_set():
            status = "stopped"
            break
        reached = index
        _run_contradiction(
            layout,
            token,
            context,
            item,
            counted=counted,
            ports=ports,
            guard=guard,
            tally=tally,
            index=index,
            total=len(token.work),
        )
        if tally.cause is not None:
            status = "unpersisted"
            break
        failure = tally.failure
        if failure is not None:
            if isinstance(failure, BackendUnavailable | BackendModelNotFound):
                raise failure
            status = "failed"
            break
    return JudgingOutcome(
        kind=kind,
        status=status,
        judged=tally.judged,
        calls=counted.calls,
        persisted=tally.persisted,
        unpersisted=tally.unpersisted,
        unpersisted_cause=tally.cause,
        dropped=tally.dropped,
        deferred=len(token.work) - reached,
        not_in_quote=(),
        settled_since_quote=tally.settled,
        failure=tally.failure,
        failed_at=tally.failed_at,
        observer_failed=guard.failed,
    )
