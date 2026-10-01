"""The `merge` use case, as an application service (ADR-0018, issue #1168;
the merge slice of the write-core extraction that began with
`ingest_service.py`).

`merge_concepts` fuses two concepts of the workspace at an explicit `root`:
the workspace gate, id resolution, the ledger-integrity refusals, Phase A
(`lifecycle.prepare_merge`), the confirmation question, the reconciliation
pass, the drift guard, Phase B (`lifecycle.merge_core`) and the auto-commit.
It returns a typed `MergeOutcome` and raises the typed refusals of
`application.write_gate` -- it never prompts, never renders, never reads the
current directory and never raises `typer.Exit`. The CLI `merge` verb is one
adapter over it; `curate`'s Identity stage and `adjudicate --apply` drive the
same write through `commit_merge`, and MVP 4's enqueue rule is meant to be
another caller.

What it does NOT own, and how it reaches each through a parameter instead
(the layering invariant forbids importing `openkos.cli`, `typer`, `rich` or
`openkos.vcs` here):

* the confirmation question -- a `confirm` callback; the service owns only
  WHEN the gate applies (`not policy.auto and prepared.review`);
* every word the user reads -- a `MergeObserver` receives typed data (the
  proposed changes, the merged summary, the commit sha);
* the concrete effects -- `MergePorts` carries the auto-commit, the reset-point
  probe, the reconciliation pass (a model call the adapter wires) and the
  clock, so each stays a substitutable seam.

The post-write derived-index refresh is not computed here either: it is a
once-per-invocation step that a caller such as `curate` batches across many
merges, so the adapter supplies it as `MergePorts.after_commit` and the service
only places it inside the commit section, after the auto-commit.

Locking (ADR-0036): the service holds NO lock while it plans, asks the
confirmation question or runs the reconciliation model call. It enters
`MergePorts.commit_section` only for the commit phase -- re-validate the drift
targets and the read dependencies, write, auto-commit, refresh.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from openkos import config
from openkos.application import commit_phase
from openkos.application import drift as application_drift
from openkos.application import lifecycle as application_lifecycle
from openkos.application.lifecycle import PreparedMerge
from openkos.application.lock_wait import CommitSection
from openkos.application.write_gate import (
    ConfirmCallback,
    DriftDetected,
    Refused,
    require_confirmation,
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class MergePolicy:
    """The caller's choices for one merge."""

    auto: bool = False
    """`--auto`: the confirmation question is not asked. The drift guard still
    runs -- skipping the prompt does not skip the window it stood in."""
    force: bool = False
    """Bypass ONLY the doctor-flagged ledger-integrity refusal; independent of
    `auto`, it never skips the confirmation."""
    no_reconcile: bool = False
    reconcile: bool = False


@dataclass(frozen=True)
class MergePorts:
    """The effects the service sequences but does not own."""

    autocommit: Callable[[Path, Sequence[str], str], str | None]
    """Best-effort commit of the listed workspace-relative paths, returning the
    new commit's abbreviated sha or `None` on every degradation; must never
    raise for a git failure."""

    has_reset_point: Callable[[Path], bool]
    """Whether the workspace has a version-control reset point -- the fact the
    flagged-ledger refusal's remedy depends on."""

    apply_reconciliation: Callable[[Path, PreparedMerge, MergePolicy], PreparedMerge]
    """The post-consent reconciliation pass (#645): returns `prepared`
    unchanged when no pass is planned, otherwise the prepared merge carrying
    the reconciled survivor. Wired by the adapter because it makes a model
    call; it reports its own degrade notices and never raises."""

    clock: Callable[[], datetime] = _utc_now

    commit_section: CommitSection = commit_phase.unlocked_section
    """Entered around the commit phase only -- drift and read-dependency
    re-validation, the write, the auto-commit and `after_commit` -- never around
    the confirmation question or the reconciliation model call (ADR-0036). The
    CLI hands it a section that takes the workspace lock; the default holds
    nothing."""

    after_commit: Callable[[], None] = commit_phase.no_after_commit
    """Runs inside the commit section after the auto-commit: the derived-index
    refresh, which must not race another writer's burst."""


@dataclass(frozen=True)
class MergePreview:
    """Everything the adapter needs to show the proposed changes, before
    anything is written. Data, not text: the adapter owns the wording."""

    prepared: PreparedMerge
    reconcile_planned: bool
    """The reconciliation pass will run after consent (disclosed in the plan
    so the model call is part of what the human approves)."""
    cross_source_same_pair: bool
    """#796: the pair's provenance says it came from different sources."""
    cross_type_concern: str | None
    """#904: the cross-type label to print, when the pair crosses types."""
    index_name: str
    log_name: str


@dataclass(frozen=True)
class MergeSummary:
    """What a finished merge wrote, for the closing line."""

    survivor_canonical: str
    absorbed_canonical: str
    index_name: str
    log_name: str


@dataclass(frozen=True)
class MergeOutcome:
    """What one merge did: the pair and the commit, if one was made."""

    survivor_canonical: str
    absorbed_canonical: str
    commit_sha: str | None
    """The auto-commit's abbreviated sha, `None` when it degraded (#800)."""


class MergeObserver:
    """Receives what a run has to say while it runs. Every method is a no-op,
    so an unattended caller passes nothing and gets silence; an adapter
    overrides the ones it renders."""

    def proposed(self, preview: MergePreview) -> None:
        """The proposed changes, shown before the confirmation question."""

    def merged(self, summary: MergeSummary) -> None:
        """Phase B finished, before the commit."""

    def committed(self, sha: str) -> None:
        """The commit exists (only called with a real sha)."""


def merge_commit_paths(
    prepared: PreparedMerge, merge_result: application_lifecycle.MergeResult
) -> list[str]:
    """The workspace-relative paths one merge's commit holds."""
    return [
        "bundle/index.md",
        "bundle/log.md",
        *(f"bundle/{rel}" for rel in merge_result.touched_files),
        f"bundle/{prepared.survivor_canonical}.md",
        f"bundle/{prepared.absorbed_canonical}.md",
        merge_result.ledger_sidecar_path,
    ]


def merge_commit_message(prepared: PreparedMerge) -> str:
    return (
        f"openkos: merge {prepared.absorbed_canonical} into "
        f"{prepared.survivor_canonical}"
    )


def commit_merge(
    root: Path,
    layout: config.WorkspaceLayout,
    prepared: PreparedMerge,
    *,
    autocommit: Callable[[Path, Sequence[str], str], str | None],
) -> str | None:
    """`merge_core` + the auto-commit for one prepared merge -- the write
    `curate`'s Identity stage and `adjudicate --apply`/`--apply-same` drive
    per accepted pair (issue #137 closing slice). Raises `OSError`/
    `ValueError` straight from `merge_core`, unchanged -- callers decide how
    to report and whether to stop.

    Returns the auto-commit's sha (issue #800) rather than announcing it,
    because this helper is NOT curate-only: `adjudicate` drives it too, and
    #800 scopes the disclosure line to `forget`, `merge` and `curate`."""
    merge_result = application_lifecycle.merge_core(
        layout.bundle_dir,
        layout.bundle_dir / "index.md",
        layout.bundle_dir / "log.md",
        prepared,
    )
    return autocommit(
        root,
        merge_commit_paths(prepared, merge_result),
        merge_commit_message(prepared),
    )


def merge_concepts(
    root: Path,
    survivor_id: str,
    absorbed_id: str,
    policy: MergePolicy,
    *,
    ports: MergePorts,
    observer: MergeObserver | None = None,
    confirm: ConfirmCallback | None = None,
) -> MergeOutcome:
    """Fuse `absorbed_id` into `survivor_id` in the workspace at `root`: the
    first DESTRUCTIVE entity-resolution write (spec: Merge Fuses Two Distinct
    Concept-IDs).

    Phase A (pure, no writes): `root` must be a workspace; both ids resolve
    through `lifecycle.resolve_concept_path` (an absolute id, a `..` segment,
    a reserved basename or a nonexistent concept refuses) to DISTINCT
    concepts; a torn ledger write refuses with no override and a
    doctor-flagged ledger refuses unless `policy.force`;
    `lifecycle.prepare_merge` then builds the entire result in memory (see
    there for the merged survivor, the inbound link/relation/provenance
    rewrites, the catalog and the log). Every refusal is a `Refused` carrying
    the exact text the CLI has always printed.

    The confirmation gate follows the preview: `policy.auto` skips it, else
    the workspace's `review: false` skips it, else `confirm` is asked
    (`ConfirmationDeclined` on no, `ConfirmationUnavailable` when it could not
    be asked or no callback was given). Declining or refusing leaves the
    bundle untouched.

    Past the gate -- and on runs that skip it -- the reconciliation pass runs
    (#645, through `ports.apply_reconciliation`, so the slow model call sits
    inside the window the drift guard re-validates) and the drift guard
    re-reads every path the merge will touch, the absorbed file included (it
    is UNLINKED, so an edit landing on it would be destroyed outright), and
    raises `DriftDetected` (nothing written) if any changed or vanished since
    Phase A read it (#313, #319, #334).

    Phase B (`lifecycle.merge_core`) is not transactional as a whole: a
    failure partway through is a benign, git-recoverable partial result and is
    raised as `Refused`. After the writes: the `merged` notification, the
    auto-commit, and the `committed` notification when a commit exists."""
    obs = observer if observer is not None else MergeObserver()
    layout = config.WorkspaceLayout(root)
    index_path = layout.bundle_dir / "index.md"
    log_path = layout.bundle_dir / "log.md"

    try:
        workspace_reason = config.require_workspace(root)
        if workspace_reason is not None:
            raise Refused(f"openkos merge: refusing to merge -- {workspace_reason}.")

        survivor_path, survivor_canonical = application_lifecycle.resolve_concept_path(
            layout.bundle_dir, survivor_id
        )
        absorbed_path, absorbed_canonical = application_lifecycle.resolve_concept_path(
            layout.bundle_dir, absorbed_id
        )
        if survivor_canonical == absorbed_canonical:
            raise ValueError(
                "survivor and absorbed concept-ids must be distinct, both "
                f"resolved to {survivor_canonical!r}"
            )
    except (OSError, ValueError) as exc:
        raise Refused(f"openkos merge: refusing to merge -- {exc}.") from exc

    torn = application_lifecycle.torn_ledger_refusal(
        layout.bundle_dir, survivor_canonical, "merge"
    )
    if torn is not None:
        raise Refused(torn)
    if not policy.force:
        flagged = application_lifecycle.flagged_ledger_refusal(
            layout.bundle_dir,
            survivor_canonical,
            has_reset_point=lambda: ports.has_reset_point(root),
        )
        if flagged is not None:
            raise Refused(flagged)

    now = ports.clock()

    try:
        prepared = application_lifecycle.prepare_merge(
            layout.bundle_dir,
            index_path,
            log_path,
            survivor_path,
            absorbed_path,
            survivor_canonical,
            absorbed_canonical,
            root,
            now=now,
        )
    except (OSError, ValueError) as exc:
        raise Refused(
            f"openkos merge: failed while preparing the merge -- {exc}."
        ) from exc

    # #645 (ruling: opt-out): the reconciliation pass is planned when the
    # stacked share reaches the threshold, disclosed in the plan -- before the
    # consent gate -- so the model call is part of what the human approves.
    # #796/#904: `merge` is the command `duplicates` and `adjudicate` both name
    # in their closing hints, so the cross-source and cross-type guardrails
    # ride its plan too.
    obs.proposed(
        MergePreview(
            prepared=prepared,
            reconcile_planned=application_lifecycle.reconcile_planned(
                prepared,
                no_reconcile=policy.no_reconcile,
                reconcile=policy.reconcile,
            ),
            cross_source_same_pair=application_lifecycle.cross_source_same_pair(
                layout.bundle_dir, (survivor_canonical, absorbed_canonical)
            ),
            cross_type_concern=application_lifecycle.cross_type_concern(
                layout.bundle_dir, (survivor_canonical, absorbed_canonical)
            ),
            index_name=index_path.name,
            log_name=log_path.name,
        )
    )

    if not policy.auto and prepared.review:
        # #918: the wording comes from the staged request, not a literal here,
        # so an api/mcp adapter driving this gate headlessly reads the same
        # sentence the CLI prints.
        require_confirmation(
            confirm,
            prepared.confirmation.prompt,
            prepared.confirmation.non_tty_refusal,
        )

    # #645: the reconciliation call runs AFTER consent (the plan disclosed it)
    # and BEFORE the drift re-check below. Any failure keeps the stacked body
    # and notices -- the merge itself never fails on an improvement pass.
    prepared = ports.apply_reconciliation(root, prepared, policy)

    # ADR-0036: everything above ran with no workspace lock. The commit phase
    # takes it now. A busy lock propagates as `WorkspaceBusyError` before
    # anything is written (the adapter maps it to exit 3).
    with ports.commit_section():
        # Issue #334: every byte `merge_core` writes below was computed from a
        # pre-prompt read, so re-validate each target now -- after the gate,
        # before the first write. The ABSORBED file is in here too: it is
        # UNLINKED, so an edit landing on it during the prompt would be
        # destroyed outright (#319).
        drift = application_drift.describe_drift(
            layout,
            application_lifecycle.merge_drift_targets(layout, prepared),
            "merge",
            deletes=frozenset({absorbed_path}),
        )
        if drift is not None:
            raise DriftDetected(drift)
        # The documents the plan only READ (the whole-bundle scan that decided
        # which documents reference the absorbed concept) are re-validated too:
        # the whole-verb lock no longer excludes the writer that changed them.
        read_drift = commit_phase.describe_read_drift(
            layout, prepared.read_dependencies, "merge"
        )
        if read_drift is not None:
            raise DriftDetected(read_drift)

        try:
            result = application_lifecycle.merge_core(
                layout.bundle_dir, index_path, log_path, prepared
            )
        except (OSError, ValueError) as exc:
            raise Refused(
                f"openkos merge: failed while writing the merge -- {exc}."
            ) from exc

        obs.merged(
            MergeSummary(
                survivor_canonical=survivor_canonical,
                absorbed_canonical=absorbed_canonical,
                index_name=index_path.name,
                log_name=log_path.name,
            )
        )

        sha = ports.autocommit(
            root, merge_commit_paths(prepared, result), merge_commit_message(prepared)
        )
        # #800: `unmerge` reverses a merge, but only through the ledger and only
        # in last-in-first-out order; the commit is the unconditional way back,
        # so it is named after the success line and only when it exists.
        if sha is not None:
            obs.committed(sha)
        ports.after_commit()

    return MergeOutcome(
        survivor_canonical=survivor_canonical,
        absorbed_canonical=absorbed_canonical,
        commit_sha=sha,
    )
