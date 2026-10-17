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

import dataclasses
import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

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
    UnmergeInterrupted,
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
    keep_distinct: bool = False
    """#1334 item 9: after each step, record the permanent keep-distinct
    ruling for the pair that step restored (through `ports.record_distinct`),
    so the judge stops proposing it again. Opt-in: whether an unmerge MEANS
    "these are distinct" is the operator's call."""


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

    record_distinct: Callable[[Path, str, str], None] = lambda _root, _a, _b: None
    """Records the keep-distinct ruling for `(survivor, absorbed)`; runs only
    under `policy.keep_distinct`, after the step's unmerge commit. It owns its
    own failure handling: a failure must never undo the completed unmerge."""

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
        if policy.keep_distinct:
            ports.record_distinct(root, survivor_canonical, absorbed_canonical)
        ports.after_commit()


# -- the stateless path (`unmerge-api`, ADR-0064) ------------------------------ #
#
# A caller with no terminal and no memory between calls (`openkos.api`) undoes a
# merge in two phases: `preview_unwind` plans the unwind and digests every byte
# the plan rests on, and the apply re-plans under the lock and refuses a moved
# digest. Nothing here prompts; the version is the consent.


@dataclass(frozen=True)
class MergeHistoryEntry:
    """One merge recorded on a survivor's ledger."""

    survivor_id: str
    absorbed_id: str
    absorbed_title: str | None
    """The absorbed document's `title` as the ledger recorded it, or `None`
    when it cannot be read (a redacted snapshot included)."""
    merged_at: str


def _recorded_title(entry: okf.MergeLedgerEntry) -> str | None:
    try:
        metadata, _body = okf.load_frontmatter(entry.absorbed_snapshot)
    except ValueError:
        return None
    title = metadata.get("title")
    return title if isinstance(title, str) and title.strip() else None


def merge_history(root: Path, concept_id: str) -> tuple[MergeHistoryEntry, ...]:
    """Every merge recorded on `concept_id`'s committed ledger, newest first.

    Reads only the ledger: no lock, no write. An id that is not a valid Concept
    ID, or names no ledger, lists nothing; a ledger that cannot be read is a
    `Refused`."""
    layout = config.WorkspaceLayout(root)
    try:
        canonical = application_lifecycle.canonicalize_concept_id(concept_id)
    except ValueError:
        return ()
    try:
        entries = bundle_ledger.read_entries(canonical, layout.bundle_dir)
    except (OSError, ValueError) as exc:
        raise Refused(
            f"openkos unmerge: the merge ledger of '{canonical}' cannot be read "
            f"-- {exc}."
        ) from exc
    return tuple(
        MergeHistoryEntry(
            survivor_id=canonical,
            absorbed_id=entry.absorbed_id,
            absorbed_title=_recorded_title(entry),
            merged_at=entry.merged_at,
        )
        for entry in reversed(entries)
    )


@dataclass(frozen=True)
class MergedConcept:
    """A page whose ledger records at least one merge, with its history."""

    concept_id: str
    merges: tuple[MergeHistoryEntry, ...]
    """`merge_history` for the page: newest first, never empty."""


_OLDEST = datetime.min.replace(tzinfo=UTC)


def _merged_time(stamp: str) -> datetime:
    """`stamp` as an aware time, for ordering; an unparseable one sorts last
    and a naive one is read as UTC."""
    try:
        parsed = datetime.fromisoformat(stamp)
    except ValueError:
        return _OLDEST
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def order_merged(pages: Sequence[MergedConcept]) -> tuple[MergedConcept, ...]:
    """`pages` by their newest merge, newest first, then by Concept ID."""
    by_id = sorted(pages, key=lambda page: page.concept_id)
    return tuple(
        sorted(
            by_id, key=lambda page: _merged_time(page.merges[0].merged_at), reverse=True
        )
    )


def merged_concepts(root: Path) -> tuple[MergedConcept, ...]:
    """Every existing page whose committed ledger records a merge, newest
    combine first (design D1).

    Walks the ledger sidecars and reads each through `merge_history`, so the
    records are exactly the per-page read's. The Concept ID comes from the
    walked path, never from the sidecar's own `survivor_id`. A ledger whose
    page no longer exists (an absorbed survivor's) is left out: its merges are
    undone after the merge that absorbed it. A ledger that cannot be read is
    skipped, as every bundle-wide ledger walk skips one. Reads only: no lock,
    no write."""
    layout = config.WorkspaceLayout(root)
    ledger_root = bundle_ledger.ledger_root(layout.bundle_dir)
    pages: list[MergedConcept] = []
    for path in bundle_ledger.iter_ledgers(layout.bundle_dir):
        relative = path.relative_to(ledger_root).as_posix()
        concept_id = relative.removesuffix(bundle_ledger.LEDGER_SUFFIX)
        if not okf.concept_path_for(concept_id, layout.bundle_dir).is_file():
            continue
        try:
            merges = merge_history(root, concept_id)
        except Refused:
            continue
        if merges:
            pages.append(MergedConcept(concept_id=concept_id, merges=merges))
    return order_merged(pages)


@dataclass(frozen=True)
class UnwindStepSummary:
    """One step of a stateless unwind, as data derived from its ledger entry."""

    absorbed_id: str
    absorbed_title: str | None
    merged_at: str
    files_rewritten: int
    """The other documents this step restores or rewrites."""
    survivor_edits_unverifiable: bool
    """The entry predates the survivor-edit check (#1110): an edit made to the
    survivor since this merge cannot be detected, and would be discarded."""
    restores_catalog_snapshot: bool
    """The entry restores whole-file `index.md`/`log.md` snapshots, discarding
    any later change to them, instead of reversing only its own edit."""


@dataclass(frozen=True)
class UnwindRefusal:
    """Why a stateless unwind cannot be planned; nothing was written."""

    reason: Literal["survivor_missing", "not_merged", "survivor_edited"]
    absorbed_into: str | None = None
    """For `survivor_missing`: the concept whose ledger records absorbing the
    survivor, when one does."""


@dataclass(frozen=True)
class UnwindPreview:
    """A planned unwind, newest merge first, and the digest of what it rests on."""

    survivor_id: str
    absorbed_id: str
    steps: tuple[UnwindStepSummary, ...]
    discard_survivor_edits: bool
    survivor_edits_discarded: bool
    """The first step would discard an edit made to the survivor since its
    merge; only possible when `discard_survivor_edits` is set."""
    content_digest: str


def _rewritten_files(entry: okf.MergeLedgerEntry) -> list[str]:
    return sorted(
        {rewrite.file for rewrite in entry.link_rewrites}
        | {rewrite.file for rewrite in entry.relation_rewrites}
        | {rewrite.file for rewrite in entry.provenance_rewrites}
    )


def _sha_or_dash(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return "-"


def _bundle_relative(layout: config.WorkspaceLayout, path: Path) -> str:
    try:
        return path.relative_to(layout.bundle_dir).as_posix()
    except ValueError:
        return str(path)


def _unwind_digest(
    layout: config.WorkspaceLayout,
    survivor_id: str,
    absorbed_id: str,
    first: PreparedUnmerge,
    *,
    discard_survivor_edits: bool,
) -> str:
    """sha-256 over sorted lines naming every input of the plan (design D2):
    the target and the caller's choice, the ledger sidecar (every step's entry
    lives in it, so the sequence follows from it and the target), and the first
    step's baselines: the survivor, its rewrite files, the catalog when the
    entry restores snapshots, and every other bundle document, which its read
    dependencies capture for the supersession walk. So a file a later step
    rewrites, and a path a later step recreates, are covered without a line of
    their own. The time is not an input: an unchanged bundle digests the same."""

    def sha(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    bundle = layout.bundle_dir
    lines = [
        f"target\t{survivor_id}\t{absorbed_id}",
        f"discard\t{'true' if discard_survivor_edits else 'false'}",
        f"ledger\t{_sha_or_dash(bundle_ledger.ledger_path_for(survivor_id, bundle))}",
        f"survivor\t{sha(first.survivor_bytes)}",
    ]
    if first.catalog_edit is None:
        # A snapshot entry restores whole files, so the catalog is an input.
        lines.append(f"catalog\tindex.md\t{sha(first.index_bytes)}")
        lines.append(f"catalog\tlog.md\t{sha(first.log_bytes)}")
    lines.extend(
        f"rewrite\t{rel}\t{sha(data)}" for rel, data in first.rewrite_bytes.items()
    )
    dependencies = first.read_dependencies
    lines.extend(
        f"read\t{_bundle_relative(layout, path)}\t{sha(data)}"
        for path, data in dependencies.present.items()
    )
    return sha("\n".join(sorted(lines)).encode("utf-8"))


def _step_summary(entry: okf.MergeLedgerEntry) -> UnwindStepSummary:
    return UnwindStepSummary(
        absorbed_id=entry.absorbed_id,
        absorbed_title=_recorded_title(entry),
        merged_at=entry.merged_at,
        files_rewritten=len(_rewritten_files(entry)),
        survivor_edits_unverifiable=not entry.survivor_after_sha256,
        restores_catalog_snapshot=entry.schema != okf.MERGE_LEDGER_SCHEMA_V5,
    )


def preview_unwind(
    root: Path,
    survivor_id: str,
    absorbed_id: str,
    *,
    discard_survivor_edits: bool = False,
    clock: Callable[[], datetime] = _utc_now,
) -> UnwindPreview | UnwindRefusal:
    """Plan undoing `survivor_id`'s merges, newest first, down to and including
    the one that absorbed `absorbed_id`; naming the newest is one step.

    Pure: no lock, no write. Returns `UnwindRefusal` for the three causes a
    client words (design D4): the survivor's document is gone, nothing of that
    id is recorded on it, or restoring the first step would discard an edit
    made since its merge and the caller did not opt in. Later steps' own checks
    run at apply. Every other refusal is raised as `Refused`, in the CLI's
    words."""
    layout = config.WorkspaceLayout(root)
    try:
        survivor_canonical = application_lifecycle.canonicalize_concept_id(survivor_id)
        target_canonical = application_lifecycle.canonicalize_concept_id(absorbed_id)
    except ValueError as exc:
        raise Refused(f"openkos unmerge: refusing to unmerge -- {exc}.") from exc
    workspace_reason = config.require_workspace(root)
    if workspace_reason is not None:
        raise Refused(f"openkos unmerge: refusing to unmerge -- {workspace_reason}.")
    if not okf.concept_path_for(survivor_canonical, layout.bundle_dir).is_file():
        return UnwindRefusal(
            "survivor_missing",
            absorbed_into=bundle_ledger.find_absorber(
                survivor_canonical, layout.bundle_dir
            ),
        )
    request = _open_request(
        root, survivor_id, absorbed_id, UnmergePorts(autocommit=_no_commit, clock=clock)
    )
    try:
        entries = bundle_ledger.read_entries(survivor_canonical, layout.bundle_dir)
    except (OSError, ValueError) as exc:
        raise Refused(
            f"openkos unmerge: failed while preparing the unmerge -- {exc}."
        ) from exc
    if all(entry.absorbed_id != target_canonical for entry in entries):
        return UnwindRefusal("not_merged")
    try:
        sequence = bundle_merge.plan_unwind_sequence(
            survivor_id=survivor_canonical,
            to_absorbed_id=target_canonical,
            entries=entries,
        )
        # Prepared with the override on, so an edit is REPORTED rather than
        # raised: the refusal below is then decided from a flag, never from
        # error text.
        first = application_lifecycle.prepare_unmerge(
            root,
            layout,
            request.survivor_path,
            survivor_canonical,
            sequence[0].absorbed_id,
            now=request.now,
            cfg=request.cfg,
            discard_survivor_edits=True,
        )
    except (OSError, ValueError) as exc:
        raise Refused(
            f"openkos unmerge: failed while preparing the unmerge -- {exc}."
        ) from exc
    if first.survivor_edits_discarded and not discard_survivor_edits:
        return UnwindRefusal("survivor_edited")
    return UnwindPreview(
        survivor_id=survivor_canonical,
        absorbed_id=target_canonical,
        steps=tuple(_step_summary(entry) for entry in sequence),
        discard_survivor_edits=discard_survivor_edits,
        survivor_edits_discarded=first.survivor_edits_discarded,
        content_digest=_unwind_digest(
            layout,
            survivor_canonical,
            target_canonical,
            first,
            discard_survivor_edits=discard_survivor_edits,
        ),
    )


def _no_commit(root: Path, paths: Sequence[str], message: str) -> str | None:
    """The auto-commit port of a plan that never writes."""
    return None


@dataclass(frozen=True)
class UnwindStale:
    """The bundle or the ledger changed since the preview; nothing was written."""

    reason: Literal["content_changed"] = "content_changed"


@dataclass(frozen=True)
class UnwindApplied:
    """What a stateless unwind restored, in the order it ran, and each step's
    commit (`None` for one that degraded)."""

    survivor_id: str
    restored_ids: tuple[str, ...]
    commit_shas: tuple[str | None, ...]


def unwind_to_version(
    root: Path,
    survivor_id: str,
    absorbed_id: str,
    *,
    content_digest: str,
    discard_survivor_edits: bool,
    ports: UnmergePorts,
) -> UnwindApplied | UnwindRefusal | UnwindStale:
    """Perform the unwind a `preview_unwind` digested as `content_digest`.

    Inside ONE entry of `ports.commit_section` (design D3): the unwind is
    planned again from current bytes, a refusal is returned as is and a moved
    digest is `UnwindStale`, neither writing anything; then `unwind_merges`
    runs every step with the confirmation satisfied by the version (the caller
    previewed exactly this plan), each step committed on its own and none
    taking the section again. A first step that fails raises its own refusal,
    with nothing written; a later one raises `UnmergeInterrupted`, keeping the
    completed steps."""
    with ports.commit_section():
        preview = preview_unwind(
            root,
            survivor_id,
            absorbed_id,
            discard_survivor_edits=discard_survivor_edits,
            clock=ports.clock,
        )
        if isinstance(preview, UnwindRefusal):
            return preview
        if preview.content_digest != content_digest:
            return UnwindStale()
        shas: list[str | None] = []

        def autocommit(root: Path, paths: Sequence[str], message: str) -> str | None:
            sha = ports.autocommit(root, paths, message)
            shas.append(sha)
            return sha

        try:
            unwind_merges(
                root,
                preview.survivor_id,
                preview.absorbed_id,
                UnmergePolicy(auto=True, discard_survivor_edits=discard_survivor_edits),
                ports=dataclasses.replace(
                    ports,
                    autocommit=autocommit,
                    commit_section=commit_phase.unlocked_section,
                ),
            )
        except UnwindStopped as exc:
            if exc.step_number == 1:
                raise exc.cause from exc
            raise UnmergeInterrupted(
                preview.survivor_id,
                tuple(
                    step.absorbed_id for step in preview.steps[: exc.step_number - 1]
                ),
                tuple(shas),
                exc.absorbed_id,
                exc.cause.message,
            ) from exc
    return UnwindApplied(
        survivor_id=preview.survivor_id,
        restored_ids=tuple(step.absorbed_id for step in preview.steps),
        commit_shas=tuple(shas),
    )
