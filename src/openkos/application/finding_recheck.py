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

import dataclasses
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal

from openkos import config, lock
from openkos.application import (
    curate_decisions,
    curate_queue,
    finding_inputs,
    queue_producers,
    revisions,
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
from openkos.state import derived, findings
from openkos.state import pending_queue as pq

FINDING_KINDS: Final = ("contradiction", "revision")
"""The kinds this module quotes and runs: the two whose rows record a finding."""


@dataclass(frozen=True)
class _Target:
    """The pair behind one open row."""

    key: str
    pair: tuple[str, str]
    absorbed: str | None = None
    """A merged-body contradiction's absorbed page; `None` for a typed edge."""


@dataclass(frozen=True)
class _Found:
    """What the LLM-free plan holds for a target: its work item, and the plan
    narrowed to that one candidate, which is all the judge is ever handed."""

    item: WorkItem
    plan: Any
    """A `contradiction.CandidatePlan` or a `revisions.RevisionPlan`."""


def _check_kind(kind: str, decision_key: str) -> None:
    if kind not in FINDING_KINDS:
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
    if kind == "revision":
        pair = row.payload.get("pair_ids")
        if not (
            isinstance(pair, list)
            and len(pair) == 2
            and all(isinstance(member, str) and member for member in pair)
        ):
            return None
        first, second = sorted(str(member) for member in pair)
        return _Target(decision_key, (first, second))
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
            return _Found(item, dataclasses.replace(plan, specs=(spec,)))
    return None


def _find_revision(
    layout: config.WorkspaceLayout,
    cfg: config.Config,
    context: _Context,
    target: _Target,
    *,
    ports: JudgingPorts,
) -> _Found | None:
    """The target's candidate in the revisions plan, or `None` when the plan does
    not hold it. The plan reads the vectors the last reindex stored and embeds
    nothing, so a decision edited since then has no vector and no candidate until
    it is reindexed."""
    effective = context.binding.effective_confidential
    decisions = revisions.load_decisions(
        layout, include_confidential=effective, local_exemption=context.local_exemption
    )
    if not decisions.decisions:
        return None
    plan = ports.plan_revisions(
        layout,
        decisions,
        embedding_model=cfg.embedding_model,
        effective_confidential=effective,
        fresh=True,
        backend=cfg.backend,
    )
    if plan.coverage.store != "ok":
        return None
    for candidate in plan.candidate_plan.candidates:
        if candidate.pair_ids == target.pair:
            rows = revisions.current_input_digests(layout, target.pair)
            item = WorkItem(
                work_key=target.key,
                member_digests=tuple((row.input_ref, row.digest) for row in rows),
            )
            return _Found(item, dataclasses.replace(plan, to_judge=(candidate,)))
    return None


def _find(
    layout: config.WorkspaceLayout,
    cfg: config.Config,
    context: _Context,
    kind: JudgingKind,
    target: _Target,
    *,
    include_confidential: bool,
    ports: JudgingPorts,
) -> _Found | None:
    if kind == "revision":
        return _find_revision(layout, cfg, context, target, ports=ports)
    return _find_contradiction(
        layout,
        context,
        target,
        include_confidential=include_confidential,
        ports=ports,
    )


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
        else _find(
            layout,
            cfg,
            context,
            kind,
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


@dataclass(frozen=True)
class _Run:
    """What judging one quoted pair needs: the run's resolved state, the ceiling
    client and the tally the pair's outcome is added to."""

    layout: config.WorkspaceLayout
    cfg: config.Config
    token: QuoteToken
    context: _Context
    counted: _CeilingClient
    ports: JudgingPorts
    guard: _ObserverGuard
    tally: _Tally
    index: int
    total: int

    def progress(self, key: str, verdict: str) -> None:
        self.guard.progress(
            JudgingProgress(
                index=self.index, total=self.total, work_key=key, verdict=verdict
            )
        )

    def fail(self, failure: BackendError | None) -> None:
        if failure is not None:
            self.tally.failure = failure
            self.tally.failed_at = self.index

    def store(self, stored: Callable[[_RetryingSection], int], judged: int) -> None:
        """Run the write that stores and publishes a verdict, in commit sections
        that retry a busy workspace, and add its outcome to the tally. `stored`
        returns how many verdicts it stored; a verdict it did not store was
        dropped on purpose."""
        section = _RetryingSection(self.ports)
        try:
            count = stored(section)
        except lock.WorkspaceBusyError:
            self.tally.unpersisted += judged
            self.tally.cause = "workspace_busy"
            return
        except sqlite3.Error, OSError:
            self.tally.unpersisted += judged
            self.tally.cause = "store_error"
            return
        self.tally.persisted += count
        self.tally.dropped += judged - count


def _publish_contradiction(
    layout: config.WorkspaceLayout,
    target: _Target,
    spec: contradiction._CandidateSpec,
    verdict: contradiction.ContradictionVerdict,
    pinned: tuple[findings.InputDigest, ...],
    section: _RetryingSection,
) -> None:
    """Replace the open row with the new verdict, or retire it when the
    disagreement no longer holds."""
    conn = derived.open_derived_connection(layout.findings_db_path)
    try:
        if verdict.verdict is contradiction.Verdict.CONTRADICTS:
            proposal = queue_producers.contradiction_proposal(
                verdict,
                spec,
                # The digests the pair was judged on, not a fresh read: a page
                # edited since would otherwise stamp the row current.
                input_digests=lambda _bundle_dir, _spec: pinned,
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


def _run_contradiction(run: _Run, item: WorkItem) -> None:
    """Judge one quoted pair, store its verdict and publish the row."""
    layout, token = run.layout, run.token
    target = _target(layout, "contradiction", item.work_key)
    found = (
        None
        if target is None
        else _find_contradiction(
            layout,
            run.context,
            target,
            include_confidential=token.include_confidential,
            ports=run.ports,
        )
    )
    if target is None or found is None or found.item != item:
        run.tally.settled += 1
        return
    [spec] = found.plan.specs
    pinned = finding_inputs.finding_input_digests(layout.bundle_dir, spec)
    batch, _pairs = run.ports.find_contradictions(
        layout.bundle_dir,
        llm=run.counted,
        include_deprecated=False,
        include_confidential=token.include_confidential,
        local_exemption=run.context.local_exemption,
        plan=found.plan,
        on_progress=lambda done, count, verdict: run.progress(
            item.work_key, verdict.verdict.value
        ),
    )
    run.tally.judged += len(batch.results)
    run.fail(batch.failure)
    if not batch.results:
        return

    def store(section: _RetryingSection) -> int:
        stored = finding_inputs.persist_findings(
            layout,
            found.plan,
            batch.results,
            judged_inputs=(pinned,),
            commit_section=section,
        )
        if stored:
            _publish_contradiction(
                layout, target, spec, batch.results[0], pinned, section
            )
        return stored

    run.store(store, len(batch.results))


def _publish_revision(
    layout: config.WorkspaceLayout, target: _Target, section: _RetryingSection
) -> None:
    """Replace the open row with the pair's persisted finding when it is still
    fresh and actionable, or retire it when the revision no longer holds."""
    held = [
        finding
        for finding in revisions.actionable_revision_findings(layout)
        if finding.pair_ids == target.pair
    ]
    conn = derived.open_derived_connection(layout.findings_db_path)
    try:
        if held:
            queue_producers.publish(
                conn,
                "revision",
                queue_producers.revision_proposals(held),
                complete=False,
                commit_section=section,
                bundle_dir=layout.bundle_dir,
            )
        else:
            pq.retire_open_by_key(conn, target.key, commit_section=section)
    finally:
        conn.close()


def _run_revision(run: _Run, item: WorkItem) -> None:
    """Judge one quoted pair, which stores its finding, and publish the row."""
    layout = run.layout
    target = _target(layout, "revision", item.work_key)
    found = (
        None
        if target is None
        else _find_revision(layout, run.cfg, run.context, target, ports=run.ports)
    )
    if target is None or found is None or found.item != item:
        run.tally.settled += 1
        return
    outcome = run.ports.judge_revisions(
        layout,
        found.plan,
        llm=run.counted,
        effective_confidential=run.context.binding.effective_confidential,
        on_progress=lambda done, count, verdict: run.progress(
            item.work_key, verdict.verdict.value
        ),
    )
    run.tally.judged += len(outcome.results)
    run.fail(outcome.failure)
    if not outcome.results:
        return

    def store(section: _RetryingSection) -> int:
        # A malformed verdict is not stored, and one about pages that moved
        # during the call is not a verdict about the pages now on disk.
        if outcome.results[0].malformed or not revisions.recorded_inputs_fresh(
            layout, target.pair, item.member_digests
        ):
            return 0
        _publish_revision(layout, target, section)
        return 1

    run.store(store, len(outcome.results))


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
    if kind not in FINDING_KINDS:
        raise ValueError(f"judging kind {kind!r} is not a kind this surface re-checks")
    layout, cfg = _open_workspace(root, "run")
    context = _context(
        cfg, kind, include_confidential=token.include_confidential, ports=ports
    )
    for item in token.work:
        # The token is echoed over the wire, not signed: a forged work key naming
        # another kind's row must not be judged, replaced or retired here.
        _check_kind(kind, item.work_key)
    moved = changed_fields(token.binding, context.binding)
    if moved:
        return QuoteStale(kind, moved)

    guard = _ObserverGuard(observer if observer is not None else JudgingObserver())
    counted = _CeilingClient(context.client, token.calls)
    tally = _Tally()
    judge = _run_revision if kind == "revision" else _run_contradiction
    status: Literal["completed", "stopped", "failed", "unpersisted"] = "completed"
    reached = 0
    for index, item in enumerate(token.work, start=1):
        if stop.is_set():
            status = "stopped"
            break
        reached = index
        run = _Run(
            layout,
            cfg,
            token,
            context,
            counted,
            ports,
            guard,
            tally,
            index,
            len(token.work),
        )
        judge(run, item)
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
