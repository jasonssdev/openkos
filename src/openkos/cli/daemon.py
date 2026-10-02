"""`openkos daemon [--once]`: the unattended engine's foreground verb (MVP 4,
job-runtime, ADR-0037).

This module is the CLI-layer composition root for the runner
(`application/runner.py`). The runner is synchronous and imports no `cli`, `vcs`,
`typer` or `rich`, so every effect it cannot own arrives here as a port:

- **Signals.** SIGTERM and SIGINT set the runner's `StopToken`, and do nothing
  else. The handler is one lock-free attribute store: a handler that logged, or
  called `threading.Event.set()`, could block on a lock the interrupted thread
  already holds. A second signal is therefore idempotent, and a write burst in
  progress is never interrupted (the runner checks the flag between units and
  immediately before a burst, never inside one).
- **Logging.** `logsetup.configure_logging("daemon", root=...)`: a rotating file
  in the per-user log directory, never inside the workspace.
- **Git.** The four `vcs.git` functions the runner's commit-retry job uses.
- **The watch job.** `watch_ports()` builds each inbox file's `IngestPorts` around
  the commit section the watch hands it, with the budgeted run's counted chat
  client. Its post-commit step is a no-op: the watch job runs the incremental
  refresh once, after its last import, so a batch pays for one refresh and the
  imports are searchable without waiting for the maintenance job.
- **The advisor stages.** Contradictions, duplicates (identity), relation typing,
  volatility and revisions, each computed through its application service with
  `max_calls=ctx.budget.remaining` and a chat client counted by the pass's
  budget, then enqueued through `application/queue_producers.py`. No stage
  merges, relates, reconciles or sets anything: a pass writes only
  `.openkos/findings.db`.

The verb is SELF-LOCKING: it never holds the workspace lock for its own
lifetime. Each stage enters the runner's commit section only to write, so an idle
daemon blocks no person's verb.

`cli.main` is imported lazily, inside the functions that need its helpers
(`_chat_client`, `_embed_client`, ...), for the reason `cli/curate.py` states:
`main` registers this command, so a module-scope import back would be circular.
Those helpers are reached by attribute at call time, so a test that patches
`openkos.cli.main.<name>` keeps intercepting.
"""

import dataclasses
import logging
import signal
import types
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import typer

from openkos import config, logsetup, resolution
from openkos.application import (
    contradictions_service,
    duplicates_service,
    reindex_service,
    revisions,
    watch_notify,
)
from openkos.application import ingest_service as ingest_svc
from openkos.application import pending as application_pending
from openkos.application import queue_producers as producers
from openkos.application import suggest_relations_service as relations_service
from openkos.application import suggest_volatility_service as volatility_service
from openkos.application.lock_wait import CommitSection
from openkos.application.runner import (
    AdvisorStage,
    JobResult,
    RunnerPorts,
    StageContext,
    StageResult,
    maintenance_due,
    run_due_jobs,
)
from openkos.application.runtime import StopToken
from openkos.application.watch import BudgetedRun, WatchPorts
from openkos.cli import observability, output
from openkos.graph import sqlite_graph
from openkos.resolution import contradiction, edge_typing, volatility_typing
from openkos.state.vectorstore import open_vector_store
from openkos.vcs import git as vcs_git

log = logging.getLogger(__name__)

POLL_CEILING_SECONDS = 10
"""The idle loop wakes at least this often (and at `quiet_seconds` when that is
shorter), so a stop request and a newly due job are noticed promptly."""

COMMIT_RETRY_PAUSE_SECONDS = 300
"""After a commit-retry that did not complete, the loop idles at least this long
before trying again, so a workspace that cannot commit (no identity, no
repository) does not write a job record every poll."""

WAKE_COALESCE_SECONDS = 1.0
"""After a native change signal ends an idle wait early, the loop pauses this
long before running jobs, so a burst of OS events costs one pass, not many."""

_STOP_SIGNALS = (signal.SIGTERM, signal.SIGINT)


# -- observers: the services' "window onto a run", with only TTY-gated stderr -----


class _DaemonReindexObserver:
    def embedder_ready(self, locality: object, cfg: config.Config) -> None:
        return None

    def progress_callback(self) -> Callable[[int, int, object], None] | None:
        return observability.progress_callback("daemon", "embedding doc")

    def vectors_indexed(
        self, report: object, previous_model_tag: str | None, cfg: config.Config
    ) -> None:
        return None


class _DaemonContradictionsObserver:
    def exemption_resolved(self, local_exemption: bool) -> None:
        return None

    def vacuous_coverage(self, notice: str) -> None:
        return None

    def persisted_findings_unreadable(self, error: Exception) -> None:
        log.warning("persisted findings unreadable (%s)", type(error).__name__)

    def progress_callback(self) -> Callable[[int, int, object], None] | None:
        return observability.progress_callback("daemon", "checking pair")

    def persist_failed(self, error: Exception) -> None:
        log.warning("contradiction findings not persisted (%s)", type(error).__name__)


class _DaemonRelationsObserver:
    def __init__(self) -> None:
        self._progress: Callable[[int, int, object], None] | None = None
        self._progress_resolved = False

    def walk_incomplete(
        self, bundle_dir: Path, *, include_confidential: bool, local_exemption: bool
    ) -> None:
        return None

    def workspace_header(self, root: Path) -> None:
        return None

    def empty_window(self, edge_offset: int) -> None:
        return None

    def candidate_notices(self, truncation: str | None, quarantine: str | None) -> None:
        return None

    def no_candidates(self, message: str) -> None:
        return None

    def warn(self, message: str) -> None:
        log.warning("suggest-relations advisory")

    def serve_split(self, served: int, total: int, fresh: int) -> None:
        return None

    def confirm_cost(self, quote: relations_service.CostQuote) -> bool:
        return True  # the budget bounds the spend; no human is attached

    def edge_progress(self, index: int, count: int, suggestion: object) -> None:
        if self._progress is None and not self._progress_resolved:
            self._progress = observability.progress_callback("daemon", "untyped edge")
            self._progress_resolved = True
        if self._progress is not None:
            self._progress(index, count, suggestion)


class _DaemonVolatilityObserver:
    def walk_incomplete(
        self, bundle_dir: Path, *, include_confidential: bool, local_exemption: bool
    ) -> None:
        return None

    def progress_callback(self) -> Callable[[int, int, object], None] | None:
        return observability.progress_callback("daemon", "suggesting type")


class _DaemonRevisionsObserver:
    def started(self) -> None:
        return None

    def truncation_notice(self, notice: str) -> None:
        return None

    def cost_gate(self, plan: revisions.RevisionPlan) -> None:
        return None

    def confirm_judging(self) -> revisions.ConfirmationAnswer:
        return "proceed"  # the budget bounds the spend; no human is attached

    def progress_callback(self) -> Callable[[int, int, object], None] | None:
        return observability.progress_callback("daemon", "judging pair")


# -- the incremental refresh --------------------------------------------------------


def _refresh_derived(root: Path) -> object:
    """The incremental derived refresh (`reindex` without `--force`): the
    content-hash cache keeps it to what changed. Raises `ReindexRefused`."""
    from openkos.cli import main as cli_main

    return reindex_service.reindex_workspace(
        root,
        force=False,
        ports=reindex_service.ReindexPorts(
            embed_client=lambda cfg: cli_main._embed_client(cfg),
            open_vector_store=open_vector_store,
            open_proximity=lambda path: cli_main._open_proximity_or_degrade(path),
            local_exemption=lambda client, cfg: cli_main._resolve_local_exemption(
                client, cfg
            ),
        ),
        observer=_DaemonReindexObserver(),
    )


# -- the advisor stages -------------------------------------------------------------


def _counted(ctx: StageContext, build: Callable[..., Any]) -> Callable[..., Any]:
    """A service's `chat_client` port, counted into this pass's one budget."""
    return ctx.budget.wrap_chat_client(build)


def _raise_failure(failure: BaseException | None) -> None:
    """A stage that enqueued what it completed then surfaces the backend failure
    that cut it short, so the job records `failed` rather than `completed`."""
    if failure is not None:
        raise failure


def _identity_stage(ctx: StageContext) -> StageResult:

    report = duplicates_service.report_duplicates(
        ctx.root,
        include_deprecated=False,
        find_candidates_report=lambda *a, **k: resolution.find_candidates_report(
            *a, **k
        ),
    )
    # #1226: a group the model judged DIFFERENT (verdict still servable) is
    # settled; leaving it out lets the complete pass retire its open row.
    judged_different = application_pending.judged_different_groups(ctx.layout)
    report = dataclasses.replace(
        report,
        groups=tuple(
            group
            for group in report.groups
            if tuple(sorted(group.member_ids)) not in judged_different
        ),
    )
    producers.enqueue_identity(
        ctx.queue(),
        report,
        current_digest=application_pending.current_finding_digest(
            ctx.layout.bundle_dir
        ),
        bundle_dir=ctx.layout.bundle_dir,
        commit_section=ctx.commit_section,
    )
    return StageResult()


def _relations_stage(ctx: StageContext) -> StageResult:
    from openkos.cli import main as cli_main

    outcome = relations_service.suggest_relations(
        ctx.root,
        relations_service.SuggestRelationsRequest(
            skip_confirmation=True, max_calls=ctx.budget.remaining
        ),
        relations_service.SuggestRelationsPorts(
            chat_client=_counted(
                ctx, lambda cfg, task: cli_main._chat_client(cfg, task=task)
            ),
            zero_state_message=cli_main._relations_zero_state_message,
            autocommit=lambda root, paths, message: None,
            refresh_derived=lambda layout: None,
            resolve_local_exemption=lambda client, cfg: (
                cli_main._resolve_local_exemption(client, cfg)
            ),
            open_proximity=lambda path: cli_main._open_proximity_or_degrade(path),
            build_graph=lambda *a, **k: sqlite_graph.build_graph(*a, **k),
            candidate_edges=lambda *a, **k: edge_typing.candidate_edges(*a, **k),
            suggest_edge_types=lambda *a, **k: edge_typing.suggest_edge_types(*a, **k),
            commit_section=ctx.commit_section,
        ),
        _DaemonRelationsObserver(),
    )
    producers.enqueue_relations(
        ctx.queue(),
        outcome,
        current_digest=application_pending.current_finding_digest(
            ctx.layout.bundle_dir
        ),
        bundle_dir=ctx.layout.bundle_dir,
        commit_section=ctx.commit_section,
    )
    _raise_failure(outcome.failure)
    return StageResult(deferred_by_bound=outcome.deferred_by_bound)


def _volatility_stage(ctx: StageContext) -> StageResult:
    from openkos.cli import main as cli_main

    outcome = volatility_service.suggest_volatility_tiers(
        ctx.root,
        volatility_service.VolatilityRequest(max_calls=ctx.budget.remaining),
        volatility_service.VolatilityPorts(
            chat_client=_counted(
                ctx, lambda cfg, task: cli_main._chat_client(cfg, task=task)
            ),
            resolve_local_exemption=lambda client, cfg: (
                cli_main._resolve_local_exemption(client, cfg)
            ),
            suggest_volatility=lambda *a, **k: volatility_typing.suggest_volatility(
                *a, **k
            ),
        ),
        _DaemonVolatilityObserver(),
    )
    producers.enqueue_volatility(
        ctx.queue(),
        outcome,
        bundle_dir=ctx.layout.bundle_dir,
        commit_section=ctx.commit_section,
    )
    _raise_failure(outcome.failure)
    return StageResult(deferred_by_bound=outcome.deferred_by_bound)


def _contradictions_stage(ctx: StageContext) -> StageResult:
    from openkos.cli import curate as curate_module
    from openkos.cli import main as cli_main

    outcome = contradictions_service.run_contradictions(
        ctx.root,
        options=contradictions_service.ContradictionsOptions(
            max_calls=ctx.budget.remaining
        ),
        ports=contradictions_service.ContradictionsPorts(
            chat_client=_counted(
                ctx, lambda cfg: cli_main._chat_client(cfg, task="contradiction")
            ),
            local_exemption=lambda client, cfg: cli_main._resolve_local_exemption(
                client, cfg
            ),
            open_proximity=lambda path: cli_main._open_proximity_or_degrade(path),
            build_graph=lambda *a, **k: sqlite_graph.build_graph(*a, **k),
            plan_candidates=lambda *a, **k: contradiction.plan_candidates(*a, **k),
            find_contradictions=lambda *a, **k: contradiction.find_contradictions(
                *a, **k
            ),
            persist_findings=curate_module.persist_findings,
            finding_input_digests=curate_module.finding_input_digests,
            zero_state_message=lambda layout, store, embeddings_missing: "",
        ),
        observer=_DaemonContradictionsObserver(),
    )
    producers.enqueue_contradictions(
        ctx.queue(),
        outcome,
        input_digests=curate_module.finding_input_digests,
        bundle_dir=ctx.layout.bundle_dir,
        commit_section=ctx.commit_section,
    )
    _raise_failure(outcome.batch.failure)
    return StageResult(deferred_by_bound=outcome.deferred_by_bound)


def _revisions_stage(ctx: StageContext) -> StageResult:
    from openkos.cli import main as cli_main

    run = revisions.run_revisions(
        ctx.root,
        revisions.RevisionsRequest(
            skip_confirmation=True, max_calls=ctx.budget.remaining
        ),
        revisions.RevisionsPorts(
            chat_client=_counted(
                ctx, lambda cfg, task: cli_main._chat_client(cfg, task=task)
            ),
            resolve_local_exemption=lambda client, cfg: (
                cli_main._resolve_local_exemption(client, cfg)
            ),
        ),
        _DaemonRevisionsObserver(),
    )
    if run.status != "completed" or run.report is None:
        # Nothing was judged (no decisions, no usable vectors): an absent result
        # cannot tell "no finding" from "not reached", so retire nothing.
        return StageResult()
    report = run.report
    producers.enqueue_revisions(
        ctx.queue(),
        revisions.actionable_revision_findings(ctx.layout),
        complete=producers.revisions_run_complete(report.plan, report.outcome),
        bundle_dir=ctx.layout.bundle_dir,
        commit_section=ctx.commit_section,
    )
    _raise_failure(report.outcome.failure)
    return StageResult(deferred_by_bound=report.outcome.deferred_by_bound)


def production_stages() -> tuple[AdvisorStage, ...]:
    """The five advisor stages, in curate's order: identity, relation typing,
    volatility, contradictions, decision revisions. Only identity (a pure bundle
    walk) needs no model."""
    return (
        AdvisorStage("identity", _identity_stage, uses_model=False),
        AdvisorStage("suggest-relations", _relations_stage),
        AdvisorStage("suggest-volatility", _volatility_stage),
        AdvisorStage("contradictions", _contradictions_stage),
        AdvisorStage("revisions", _revisions_stage),
    )


def _ingest_ports(
    section: CommitSection, run_budget: BudgetedRun
) -> ingest_svc.IngestPorts:
    """One inbox file's ingest effects: the extraction client counted into the
    budgeted run, the engine's auto-commit, and no post-commit embedding (the
    watch job refreshes the derived indexes once per job, after its imports)."""
    from openkos.cli import main as cli_main

    return ingest_svc.IngestPorts(
        chat_client=run_budget.wrap_chat_client(
            lambda cfg: cli_main._chat_client(cfg, task="extraction")
        ),
        autocommit=lambda root, paths, message: cli_main._autocommit(
            root, paths, message
        ),
        after_commit=lambda layout, cfg: None,
        commit_section=section,
    )


def _notify_stderr(message: str) -> None:
    typer.echo(message, err=True)


def watch_ports() -> WatchPorts:
    return WatchPorts(ingest_ports=_ingest_ports, notify=_notify_stderr)


def production_ports(root: Path) -> RunnerPorts:
    """The runner's ports wired to the real effects: the four git functions, the
    incremental refresh, the inbox watch and the production advisor stages."""
    return RunnerPorts(
        refresh_derived=_refresh_derived,
        commit_paths=vcs_git.commit_paths,
        paths_dirty=vcs_git.paths_dirty,
        repo_root=vcs_git.repo_root,
        has_git_identity=vcs_git.has_git_identity,
        advisor_stages=production_stages(),
        watch=watch_ports(),
    )


# -- the verb -----------------------------------------------------------------------


def _install_stop_handlers(stop: StopToken) -> dict[int, Any]:
    def handler(signum: int, frame: types.FrameType | None) -> None:
        stop.set()  # one lock-free store; nothing else is safe here

    return {int(sig): signal.signal(sig, handler) for sig in _STOP_SIGNALS}


def _restore_handlers(previous: dict[int, Any]) -> None:
    for signum, handler in previous.items():
        signal.signal(signum, handler)


def _report(result: JobResult) -> None:
    detail = f" ({result.detail_code})" if result.detail_code else ""
    note = "" if result.recorded else " [outcome not recorded]"
    typer.echo(
        f"openkos daemon: {result.kind}: {result.outcome}{detail} -- "
        f"calls={result.chat_calls} done={result.units_done} "
        f"deferred={result.units_deferred}{note}"
    )


def _idle_seconds(
    unattended: config.UnattendedConfig, results: Sequence[JobResult]
) -> float:
    poll = float(min(unattended.quiet_seconds, POLL_CEILING_SECONDS))
    stuck = any(
        r.kind == "commit-retry" and r.outcome in ("commit_failed", "busy")
        for r in results
    )
    return max(poll, COMMIT_RETRY_PAUSE_SECONDS) if stuck else poll


def _idle(
    token: StopToken,
    seconds: float,
    notifier: watch_notify.ChangeNotifier | None,
) -> None:
    """The loop's idle wait. With a native notifier it ends early on a change
    signal; the pass that follows is the ordinary polling one, and `seconds`
    stays the safety-net interval, so a missed event delays, never loses."""
    if notifier is None:
        token.wait(seconds)
        return
    woke = False

    def wake() -> bool:
        nonlocal woke
        woke = woke or notifier.consume()
        return woke

    token.wait(seconds, wake=wake)
    if woke and not token.is_set():
        token.wait(WAKE_COALESCE_SECONDS)
        notifier.consume()


class _Announcer:
    """The runner's `announce` port: one TTY-gated `openkos daemon: ...` line on
    stderr. A line identical to the one just printed is dropped, so an idling
    loop that keeps finding the same unsettled file says it once, not every
    poll; `reset` lets the same line print again after a job reported."""

    def __init__(self) -> None:
        self._last: str | None = None

    def __call__(self, message: str) -> None:
        if message == self._last:
            return
        self._last = message
        observability.stage_notice("daemon", message)

    def reset(self) -> None:
        self._last = None


def _open_notifier(
    unattended: config.UnattendedConfig, *, once: bool
) -> watch_notify.ChangeNotifier | None:
    """The optional native wake-up, or `None` (polling alone). A `--once` run
    never idles, so it never starts one. A requested backend that cannot start
    is warned about on stderr and in the log, then polling carries on."""
    if once:
        return None
    opened = watch_notify.open_notifier(unattended.watch_backend, unattended.inbox)
    if opened.warning is not None:
        log.warning("native watch backend unavailable; polling")
        output.notice(
            f"openkos daemon: {opened.warning}", kind="warning", verb="daemon"
        )
    return opened.notifier


def serve(
    root: Path,
    *,
    once: bool,
    ports: RunnerPorts | None = None,
    stop: StopToken | None = None,
    install_signals: bool = True,
) -> int:
    """Run the daemon for the workspace at `root`; the process exit code.

    `--once` runs every due job in order and returns. Otherwise the loop runs the
    due jobs, then idles on `StopToken.wait` (a lock-free poll, interruptible by
    a signal) until a stop is requested. A stop returns 0.
    """
    reason = config.require_workspace(root)
    if reason is not None:
        typer.echo(f"openkos daemon: refusing to run -- {reason}.", err=True)
        return 1
    try:
        cfg = config.read_config(root)
    except (OSError, ValueError) as exc:
        typer.echo(
            f"openkos daemon: failed while reading the workspace -- {exc}.",
            err=True,
        )
        return 1

    token = stop if stop is not None else StopToken()
    notifier: watch_notify.ChangeNotifier | None = None
    previous = _install_stop_handlers(token) if install_signals else {}
    logsetup.configure_logging("daemon", root=root)
    try:
        wired = ports if ports is not None else production_ports(root)
        announcer = _Announcer()
        wired = dataclasses.replace(wired, announce=announcer)
        log.info("daemon started (once=%s)", once)
        notifier = _open_notifier(cfg.unattended, once=once)
        while True:
            due = maintenance_due(root, cfg.unattended, wired.now())
            results = run_due_jobs(
                root,
                unattended=cfg.unattended,
                stop=token,
                ports=wired,
                maintenance_due=due,
            )
            for result in results:
                _report(result)
            if results:
                announcer.reset()
            if once or token.is_set():
                break
            _idle(token, _idle_seconds(cfg.unattended, results), notifier)
            if token.is_set():
                break
        if token.is_set():
            log.info("daemon stopped on request")
            typer.echo("openkos daemon: stopped.")
        else:
            log.info("daemon finished (once=%s)", once)
        return 0
    except Exception as exc:  # noqa: BLE001 -- logged by type; never a traceback to a log
        log.error("daemon failed (%s)", type(exc).__name__)
        typer.echo(f"openkos daemon: failed -- {type(exc).__name__}.", err=True)
        return 1
    finally:
        if notifier is not None:
            notifier.close()
        _restore_handlers(previous)
        logsetup.reset_logging()
