"""The `unmerge` use cases, as an application service (ADR-0018, issue
#1168; the unmerge slice of the write-core extraction that began with
`ingest_service.py`).

Two entry points over the same single step:

* `unmerge_concept` reverses the survivor's most recent merge (the LIFO tail
  of its `merged_from` ledger): the workspace gate, id resolution, the
  torn-ledger refusal, then ONE step -- Phase A (`lifecycle.prepare_unmerge`),
  the confirmation question, the drift guard, Phase B
  (`lifecycle.unmerge_core`) and the auto-commit.
* `unwind_merges` (`--to`) unwinds the ledger tail-first down to and including
  the entry that absorbed a named id: one preview and ONE confirmation for the
  whole plan, then the same complete step per entry with the confirmation
  short-circuited, stopping at the first failure.

Both return typed outcomes and raise the typed refusals of
`application.write_gate`; neither prompts, renders, reads the current
directory or raises `typer.Exit`. The CLI `unmerge` verb is one adapter over
them. What they reach through parameters instead (the layering invariant
forbids `openkos.cli`, `typer`, `rich` and `openkos.vcs` here): the
confirmation question (`confirm`), every word the user reads
(`UnmergeObserver`) and the concrete effects (`UnmergePorts`: the auto-commit
and the clock). The post-write derived-index refresh stays the adapter's,
placed once after a whole unwind rather than per step.

Each step is NOT transactional with the next: a chain stopping at step N
leaves steps 1..N-1 completed and a consistent, git-recoverable bundle.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from openkos import config
from openkos.application import catalog_delta, commit_phase
from openkos.application import drift as application_drift
from openkos.application import lifecycle as application_lifecycle
from openkos.application.consent import boolean_confirmation
from openkos.application.lifecycle import PreparedUnmerge
from openkos.application.lock_wait import CommitSection
from openkos.application.write_gate import (
    ConfirmCallback,
    DriftDetected,
    Refused,
    WriteRefused,
    require_confirmation,
)
from openkos.bundle import ledger as bundle_ledger
from openkos.bundle import merge as bundle_merge
from openkos.model import okf


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class UnmergePolicy:
    """The caller's choices for one unmerge."""

    auto: bool = False
    """`--auto`: the confirmation question is not asked. The drift guard still
    runs -- skipping the prompt does not skip the window it stood in."""
    discard_survivor_edits: bool = False
    """#1110: proceed even though the survivor's current bytes no longer match
    what the merge wrote. Independent of `auto`; it bypasses ONLY that one
    refusal."""


@dataclass(frozen=True)
class UnmergePorts:
    """The effects the service sequences but does not own."""

    autocommit: Callable[[Path, Sequence[str], str], str | None]
    """Best-effort commit of the listed workspace-relative paths; must never
    raise for a git failure (it degrades to a warning). Returns the commit's
    sha, or `None` when there is no commit to name (#1334 item 3)."""

    clock: Callable[[], datetime] = _utc_now

    commit_section: CommitSection = commit_phase.unlocked_section
    """Entered around each step's commit phase only (ADR-0036): never around
    the confirmation question. The CLI hands it a section that takes the
    workspace lock; the default holds nothing."""

    after_commit: Callable[[], None] = commit_phase.no_after_commit
    """Runs inside the commit section after each step's auto-commit: the
    derived-index refresh."""


@dataclass(frozen=True)
class UnmergePreview:
    """Everything the adapter needs to show one step's proposed changes,
    before anything is written. Data, not text: the adapter owns the wording."""

    prepared: PreparedUnmerge
    survivor_canonical: str
    absorbed_canonical: str
    index_name: str
    log_name: str


@dataclass(frozen=True)
class UnmergeSummary:
    """What a finished step wrote, for the closing line."""

    survivor_canonical: str
    absorbed_canonical: str
    index_name: str
    log_name: str


@dataclass(frozen=True)
class UnwindStep:
    """One step of a `--to` plan: the entry's absorbed id and the preview
    lines derived from the ledger entry alone (no disk reads)."""

    absorbed_id: str
    preview_lines: tuple[str, ...]


@dataclass(frozen=True)
class UnwindPlan:
    """The whole `--to` plan, newest merge first."""

    survivor_canonical: str
    steps: tuple[UnwindStep, ...]


@dataclass(frozen=True)
class UnmergeOutcome:
    """What one single-step unmerge restored."""

    survivor_canonical: str
    absorbed_canonical: str


@dataclass(frozen=True)
class UnwindOutcome:
    """What a completed `--to` unwind restored."""

    survivor_canonical: str
    absorbed_ids: tuple[str, ...]
    """The restored ids, in execution order."""


class UnwindStopped(WriteRefused):
    """A `--to` unwind stopped at a failing step; earlier steps completed and
    are NOT rolled back. `message` is the progress line the adapter prints
    AFTER the failing step's own refusal (`cause`), whose type also decides
    the exit code."""

    def __init__(
        self,
        cause: WriteRefused,
        *,
        step_number: int,
        total: int,
        absorbed_id: str,
    ) -> None:
        completed = step_number - 1
        if completed:
            progress = (
                f"steps 1..{completed} completed and left a consistent "
                "bundle (git-recoverable); completed steps are not "
                "rolled back"
            )
        else:
            progress = "no earlier steps had completed"
        super().__init__(
            f"openkos unmerge: --to unwind stopped at step {step_number} "
            f"of {total} (restore '{absorbed_id}') -- {progress}."
        )
        self.cause = cause
        self.step_number = step_number
        self.total = total
        self.absorbed_id = absorbed_id


class UnmergeObserver:
    """Receives what a run has to say while it runs. Every method is a no-op,
    so an unattended caller passes nothing and gets silence; an adapter
    overrides the ones it renders."""

    def proposed(self, preview: UnmergePreview) -> None:
        """One step's proposed changes, shown before the confirmation."""

    def restored(self, summary: UnmergeSummary) -> None:
        """One step's Phase B finished, before the commit."""

    def committed(self, sha: str) -> None:
        """One step's auto-commit landed as `sha`; never called when the
        commit degraded, so an observer never names a commit that does not
        exist."""

    def unwind_planned(self, plan: UnwindPlan) -> None:
        """The whole `--to` plan, shown before its single confirmation."""

    def step_starting(self, step_number: int, total: int, absorbed_id: str) -> None:
        """A `--to` step is about to run."""


@dataclass(frozen=True)
class _Request:
    layout: config.WorkspaceLayout
    survivor_path: Path
    survivor_canonical: str
    target_canonical: str
    cfg: config.Config
    now: datetime


def _open_request(
    root: Path, survivor_id: str, target_input: str, ports: UnmergePorts
) -> _Request:
    """The gates both forms share, before any step: the workspace, the
    survivor's existence (with the reverse-provenance hint naming the
    absorber when it was itself absorbed, #562), the target's canonical id,
    the torn-ledger refusal, and the workspace config."""
    layout = config.WorkspaceLayout(root)

    try:
        workspace_reason = config.require_workspace(root)
        if workspace_reason is not None:
            raise Refused(
                f"openkos unmerge: refusing to unmerge -- {workspace_reason}."
            )

        survivor_canonical = application_lifecycle.canonicalize_concept_id(survivor_id)
        survivor_path = okf.concept_path_for(survivor_canonical, layout.bundle_dir)
        if not survivor_path.is_file():
            message = f"concept '{survivor_id}' does not exist"
            absorber = bundle_ledger.find_absorber(
                survivor_canonical, layout.bundle_dir
            )
            if absorber is not None:
                message += (
                    f". It was absorbed into '{absorber}'; run "
                    f"`openkos unmerge {absorber} {survivor_canonical}` first "
                    "to restore it"
                )
            raise ValueError(message)
        target_canonical = application_lifecycle.canonicalize_concept_id(target_input)
    except (OSError, ValueError) as exc:
        raise Refused(f"openkos unmerge: refusing to unmerge -- {exc}.") from exc

    torn = application_lifecycle.torn_ledger_refusal(
        layout.bundle_dir, survivor_canonical, "unmerge"
    )
    if torn is not None:
        raise Refused(torn)

    now = ports.clock()

    try:
        cfg = config.read_config(root)
    except (OSError, ValueError) as exc:
        raise Refused(
            f"openkos unmerge: failed while preparing the unmerge -- {exc}."
        ) from exc

    return _Request(
        layout=layout,
        survivor_path=survivor_path,
        survivor_canonical=survivor_canonical,
        target_canonical=target_canonical,
        cfg=cfg,
        now=now,
    )


def unmerge_concept(
    root: Path,
    survivor_id: str,
    absorbed_id: str,
    policy: UnmergePolicy,
    *,
    ports: UnmergePorts,
    observer: UnmergeObserver | None = None,
    confirm: ConfirmCallback | None = None,
) -> UnmergeOutcome:
    """Reverse the most recent merge on `survivor_id` in the workspace at
    `root`, restoring both concept files to byte parity with their pre-merge
    state (spec: Unmerge Achieves Round-Trip Parity).

    LIFO-ENFORCED: `absorbed_id` must be the tail entry's `absorbed_id`, else
    the step refuses (`lifecycle.prepare_unmerge`, with the unwind sequence
    named when the id is buried deeper). See `_unmerge_step` for the step's
    phases and their refusals."""
    obs = observer if observer is not None else UnmergeObserver()
    request = _open_request(root, survivor_id, absorbed_id, ports)
    _unmerge_step(
        root,
        request,
        request.target_canonical,
        policy,
        ports=ports,
        obs=obs,
        confirm=confirm,
        confirmed=False,
    )
    return UnmergeOutcome(
        survivor_canonical=request.survivor_canonical,
        absorbed_canonical=request.target_canonical,
    )


def unwind_merges(
    root: Path,
    survivor_id: str,
    to_absorbed_id: str,
    policy: UnmergePolicy,
    *,
    ports: UnmergePorts,
    observer: UnmergeObserver | None = None,
    confirm: ConfirmCallback | None = None,
) -> UnwindOutcome:
    """Unwind `survivor_id`'s merge ledger tail-first, one FULL single-step
    unmerge per entry, down to AND INCLUDING the entry that absorbed
    `to_absorbed_id` (`bundle.merge.plan_unwind_sequence`, issue #562).

    The whole plan -- one block per step in execution order -- is announced
    BEFORE one single confirmation with the same precedence as the single
    step: `policy.auto` skips it; otherwise the workspace's `review: false`
    skips it; otherwise `confirm` is asked once for the whole plan.

    Execution is a sequential loop over the complete step, each recomputed
    from CURRENT disk state with every fail-closed check included and the
    confirmation short-circuited. Deliberately NOT a whole-chain in-memory
    composition: every intermediate state after a completed step is a
    consistent bundle, so `plan_unmerge`'s LIFO-tail safety argument holds
    unchanged at each step. A failing step raises `UnwindStopped` carrying its
    own refusal as `cause`; completed steps are not rolled back."""
    obs = observer if observer is not None else UnmergeObserver()
    request = _open_request(root, survivor_id, to_absorbed_id, ports)
    layout = request.layout
    survivor_canonical = request.survivor_canonical

    try:
        entries = bundle_ledger.read_entries(survivor_canonical, layout.bundle_dir)
        sequence = bundle_merge.plan_unwind_sequence(
            survivor_id=survivor_canonical,
            to_absorbed_id=request.target_canonical,
            entries=entries,
        )
    except (OSError, ValueError) as exc:
        raise Refused(
            f"openkos unmerge: failed while preparing the unmerge -- {exc}."
        ) from exc

    obs.unwind_planned(
        UnwindPlan(
            survivor_canonical=survivor_canonical,
            steps=tuple(
                UnwindStep(
                    absorbed_id=entry.absorbed_id,
                    preview_lines=tuple(
                        application_lifecycle.unwind_step_preview_lines(
                            entry, survivor_canonical
                        )
                    ),
                )
                for entry in sequence
            ),
        )
    )

    if not policy.auto and request.cfg.review:
        chain_confirmation = boolean_confirmation("unmerge")
        require_confirmation(
            confirm, chain_confirmation.prompt, chain_confirmation.non_tty_refusal
        )

    total = len(sequence)
    for step_number, entry in enumerate(sequence, start=1):
        obs.step_starting(step_number, total, entry.absorbed_id)
        try:
            _unmerge_step(
                root,
                request,
                entry.absorbed_id,
                policy,
                ports=ports,
                obs=obs,
                confirm=confirm,
                confirmed=True,
            )
        except WriteRefused as exc:
            raise UnwindStopped(
                exc,
                step_number=step_number,
                total=total,
                absorbed_id=entry.absorbed_id,
            ) from exc

    return UnwindOutcome(
        survivor_canonical=survivor_canonical,
        absorbed_ids=tuple(entry.absorbed_id for entry in sequence),
    )


def _unmerge_step(
    root: Path,
    request: _Request,
    absorbed_canonical: str,
    policy: UnmergePolicy,
    *,
    ports: UnmergePorts,
    obs: UnmergeObserver,
    confirm: ConfirmCallback | None,
    confirmed: bool,
) -> None:
    """ONE complete single-step unmerge -- the preview / confirmation /
    drift-guard machinery both forms share (issue #562), Phase A and Phase B
    delegated to `lifecycle.prepare_unmerge`/`unmerge_core` (#918 Slice S2b).
    The classic form calls it once with `confirmed=False`; the unwind loop
    calls it once per ledger entry with `confirmed=True`, because the WHOLE
    plan was already confirmed at its single gate -- `confirmed`
    short-circuits the question exactly like `policy.auto` does, and
    everything AFTER the gate (the drift guard included) runs identically on
    every path.

    Phase A (pure, no writes; `lifecycle.prepare_unmerge`): reads the
    survivor's `merged_from` ledger and computes the entire restoration in
    memory -- the restored survivor, the restored absorbed document, the
    restored `index.md`/`log.md` and every reversed inbound link, relation
    and provenance rewrite -- failing closed (`ValueError`) on a collision at
    the absorbed path, on drift in a rewrite file, and (#1110) on a survivor
    whose bytes no longer match what the merge wrote unless
    `policy.discard_survivor_edits`. Any such failure is a `Refused` carrying
    the exact text the CLI has always printed.

    The confirmation gate follows the preview: skipped by `confirmed` or
    `policy.auto`, else by the workspace's `review: false`, else `confirm` is
    asked. Past it -- and on runs that skip it -- the drift guard re-reads
    `index.md`, `log.md`, the survivor and every rewritten third-party file
    and raises `DriftDetected` (nothing written) if any changed or vanished
    since Phase A read it (#306, #313, #319). The refusal carries a CUSTOM
    remedy (#328) because `unmerge` is the one guarded verb whose re-run is
    not a safe recovery: a re-run restores the pre-merge snapshots over
    `index.md`/`log.md`/the survivor, overwriting the protected edit.

    Phase B (`lifecycle.unmerge_core`) is not transactional as a whole; a
    failure partway through is a benign, git-recoverable partial result,
    raised as `Refused`. After the writes: the `restored` notification and the
    auto-commit."""
    layout = request.layout
    survivor_canonical = request.survivor_canonical
    index_path = layout.bundle_dir / "index.md"
    log_path = layout.bundle_dir / "log.md"

    try:
        prepared = application_lifecycle.prepare_unmerge(
            root,
            layout,
            request.survivor_path,
            survivor_canonical,
            absorbed_canonical,
            now=request.now,
            cfg=request.cfg,
            discard_survivor_edits=policy.discard_survivor_edits,
        )
    except (OSError, ValueError) as exc:
        raise Refused(
            f"openkos unmerge: failed while preparing the unmerge -- {exc}."
        ) from exc

    obs.proposed(
        UnmergePreview(
            prepared=prepared,
            survivor_canonical=survivor_canonical,
            absorbed_canonical=absorbed_canonical,
            index_name=index_path.name,
            log_name=log_path.name,
        )
    )

    if not confirmed and not policy.auto and prepared.review:
        require_confirmation(
            confirm,
            prepared.confirmation.prompt,
            prepared.confirmation.non_tty_refusal,
        )

    # ADR-0036: the plan and the question above ran with no workspace lock;
    # the commit phase takes it now (a busy lock propagates as
    # `WorkspaceBusyError` before anything is written).
    with ports.commit_section():
        # A V5 (delta) entry's reversal is re-composed over the catalog's
        # current bytes below; a snapshot entry restores whole files and so
        # keeps refusing when either catalog file moved.
        recomposable = prepared.catalog_edit is not None
        drift = application_drift.describe_drift(
            layout,
            {
                **(
                    {}
                    if recomposable
                    else {
                        index_path: prepared.index_bytes,
                        log_path: prepared.log_bytes,
                    }
                ),
                request.survivor_path: prepared.survivor_bytes,
                **{
                    layout.bundle_dir / rel: data
                    for rel, data in prepared.rewrite_bytes.items()
                },
            },
            "unmerge",
            remedy=(
                "Copy your edit somewhere safe before re-running: a re-run "
                "restores the pre-merge snapshots over index.md, log.md, and "
                "the survivor (overwriting the edit), and keeps refusing on an "
                "edited rewrite file until that edit is reverted."
            ),
            hint=application_lifecycle.okf_v02_migration_hint(index_path),
        )
        if drift is not None:
            raise DriftDetected(drift)
        # What the plan only READ (bystander relations, the free absorbed
        # path): a re-run recomputes over them, so the default advice applies.
        read_drift = commit_phase.describe_read_drift(
            layout, prepared.read_dependencies, "unmerge"
        )
        if read_drift is not None:
            raise DriftDetected(read_drift)
        if recomposable:
            try:
                prepared = application_lifecycle.recompose_unmerge_catalog(
                    layout, prepared
                )
            except catalog_delta.CatalogRecomposeError as exc:
                raise DriftDetected(str(exc)) from exc

        try:
            result = application_lifecycle.unmerge_core(layout, prepared)
        except (OSError, ValueError) as exc:
            raise Refused(
                f"openkos unmerge: failed while writing the unmerge -- {exc}."
            ) from exc

        obs.restored(
            UnmergeSummary(
                survivor_canonical=survivor_canonical,
                absorbed_canonical=absorbed_canonical,
                index_name=index_path.name,
                log_name=log_path.name,
            )
        )

        sha = ports.autocommit(
            root, result.committed_paths, f"openkos: unmerge {absorbed_canonical}"
        )
        if sha is not None:
            obs.committed(sha)
        ports.after_commit()
