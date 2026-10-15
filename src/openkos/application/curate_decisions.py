"""The curation decision surface: the apply half of a `curate` decision as
plain data (the `curate-decisions-as-data` change, ADR-0053).

This module is INTERNAL and OWNER-LOCAL. It is not a published API, and a
decision payload may derive from confidential concepts, so no `mcp/` or
`api/` adapter may import it (a boundary guard lands with the list). It is
a narrow synchronous service in the `merge_service` mould (ADR-0018): it
never prompts, never prints, never reads a TTY and never raises
`typer.Exit`. Every outcome is a returned typed result or a raised typed
refusal from `write_gate`; the adapter owns the wording, the exit code and
the commit disclosure.

Effects the service sequences but does not own arrive through
`DecisionPorts` (the auto-commit, the commit section, the warning sink) and
`DecisionObserver` (the lines that print INSIDE a commit section). Library
calls go through the module attribute (`duplicates_service.<name>`), never a
`from ... import`, so an existing patch of the target keeps working.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Literal, Protocol

from openkos import config
from openkos.application import (
    catalog_delta,
    commit_phase,
    curate_queue,
    duplicates_service,
    merge_service,
    pending_queue_report,
)
from openkos.application import drift as application_drift
from openkos.application import lifecycle as application_lifecycle
from openkos.application import pending as application_pending
from openkos.application.lock_wait import CommitSection
from openkos.application.write_gate import DriftDetected, Refused, WriteFailed
from openkos.graph.base import Edge
from openkos.model import okf
from openkos.model import types as model_types
from openkos.model.relations import ASYMMETRIC_RELATION_TYPES, validate_relation_type
from openkos.resolution.adjudication import Verdict
from openkos.resolution.candidates import CandidateGroup, Tier
from openkos.resolution.edge_typing import EdgeSuggestion
from openkos.state import pending_queue as pq


class Action(StrEnum):
    """What a client may do with one open decision."""

    MERGE = "merge"
    KEEP_DISTINCT = "keep_distinct"
    SKIP = "skip"
    RELATE = "relate"
    RELATE_REVERSED = "relate_reversed"
    SET_TIER = "set_tier"


@dataclass(frozen=True)
class DecisionVersion:
    """The version a stateless caller commits against.

    `payload_digest` proves the queue row, its proposal and its recorded
    inputs are unchanged; `content_digest` proves the bytes the human saw are
    the bytes that would be written. `payload_digest` is `None` when no queue
    row backs the item, which the token path rejects. `decision_key` is the
    handle: a findings row id is never used, because the cache is derived."""

    decision_key: str
    payload_digest: str | None
    content_digest: str

    def token(self) -> str:
        """An opaque hex digest of the three components, for transport.
        Parsing a token back is deferred to the API stage."""
        parts = (
            self.decision_key,
            "-" if self.payload_digest is None else f"+{self.payload_digest}",
            self.content_digest,
        )
        return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Applied:
    """The action took effect. `commit_sha` is `None` when the auto-commit
    degraded (the write itself still landed)."""

    decision_key: str | None
    action: Action
    commit_sha: str | None


@dataclass(frozen=True)
class NotApplied:
    """The action was not taken, and that is not an error: the walk goes on.
    A record failure is fail-open and carries its error in `detail`."""

    action: Action
    reason: Literal[
        "skipped", "member_missing", "record_failed", "not_mergeable", "not_offered"
    ]
    detail: str | None = None


@dataclass(frozen=True)
class Stale:
    """The version token no longer matches; nothing was written."""

    decision_key: str
    reason: Literal[
        "content_changed", "row_resolved", "row_superseded", "row_inputs_changed"
    ]


ApplyOutcome = Applied | NotApplied | Stale


class ConfigCommitPlan(Protocol):
    """How a config edit is committed when `openkos.yaml` carries uncommitted
    edits (#1330). OPAQUE to this module: the adapter's port makes it and the
    adapter's commit port consumes it; the service only carries it between
    the two."""


@dataclass(frozen=True)
class ConfigCommitRefusal:
    """The adapter's refusal to commit a config edit on its own. `message` is
    the complete text the adapter composed; the service raises it as a
    `write_gate.Refused` and prints nothing."""

    message: str


def _ignore_warning(message: str) -> None:
    """The default warning sink: drop it."""


def _write_identity_ruling(
    layout: config.WorkspaceLayout,
    member_ids: tuple[str, ...],
    on_warning: Callable[[str], None],
) -> str:
    """The default ruling writer: the shared service writer, reached through
    its module attribute so a patch of it still takes effect."""
    return duplicates_service.apply_identity_decision(
        layout, member_ids, target_state="declined", on_warning=on_warning
    )


def _keep_prepared(
    root: Path, prepared: application_lifecycle.PreparedMerge
) -> application_lifecycle.PreparedMerge:
    """The default reconciliation port: leave the plan as previewed."""
    return prepared


@dataclass(frozen=True)
class DecisionPorts:
    """The effects the service sequences but does not own."""

    autocommit: Callable[[Path, Sequence[str], str], str | None]
    """Best-effort commit of the listed workspace-relative paths, returning the
    abbreviated sha or `None` on every degradation. Bound late by the adapter."""

    commit_section: CommitSection = commit_phase.unlocked_section
    """Entered around the commit phase only, never around a prompt (ADR-0036).
    The default holds nothing."""

    on_warning: Callable[[str], None] = _ignore_warning
    """Where a note a library reader returned is sent."""

    record_identity_ruling: Callable[
        [config.WorkspaceLayout, tuple[str, ...], Callable[[str], None]], str
    ] = _write_identity_ruling
    """Writes the keep-distinct ruling and returns its workspace-relative
    path. A port, not a direct call, so an adapter that already routes this
    write through its own seam keeps doing so."""

    reconcile: Callable[
        [Path, application_lifecycle.PreparedMerge], application_lifecycle.PreparedMerge
    ] = _keep_prepared
    """The body-reconciliation pass of an identity merge: the only place a
    model may be reached. Called only when the preview reports one planned,
    after the caller chose to commit and before the commit section opens."""

    plan_config_commit: (
        Callable[
            [Path, str, str, str, bytes], ConfigCommitPlan | ConfigCommitRefusal | None
        ]
        | None
    ) = None
    """Decides, from the working tree and git, how a config edit is committed:
    `(root, surface, concept_type, tier, baseline)` to a plan, a refusal, or
    `None` when the ordinary path-scoped commit is right. It reads git, so it
    stays adapter-side. When unset, the commit is always the ordinary one."""

    autocommit_config_edit: (
        Callable[[Path, str, ConfigCommitPlan | None], str | None] | None
    ) = None
    """Commits the config edit, honoring the plan, and returns the abbreviated
    sha or `None`. When unset, `autocommit` commits `openkos.yaml` alone."""


class DecisionObserver:
    """The lines that print INSIDE a commit section, before further work in
    the same sequence. Every method is a no-op; an adapter overrides the ones
    it renders. A result that ends the sequence is returned instead."""

    def merge_member_missing(self, survivor: str, absorbed: str) -> None:
        """A merge member vanished between the prompt and the section."""

    def relate_endpoint_missing(self, source: str, target: str) -> None:
        """A relation endpoint vanished between the prompt and the section."""

    def keep_distinct_unrecorded(self, member_ids: tuple[str, ...], error: str) -> None:
        """The keep-distinct ruling could not be recorded (fail-open)."""


def identity_decision_key(member_ids: Sequence[str]) -> str:
    """The stored queue key of an identity decision over `member_ids`."""
    return pq.stored_decision_key("identity", pq.identity_key(member_ids))


def skip_decision(decision_key: str) -> NotApplied:
    """Skip one decision: write nothing, record nothing. The queue row, if
    any, stays `pending` and the next run offers it again."""
    return NotApplied(Action.SKIP, "skipped")


def _all_present(layout: config.WorkspaceLayout, concept_ids: Sequence[str]) -> bool:
    return all(
        okf.concept_path_for(concept_id, layout.bundle_dir).exists()
        for concept_id in concept_ids
    )


def record_keep_distinct(
    root: Path,
    layout: config.WorkspaceLayout,
    member_ids: Sequence[str],
    *,
    ports: DecisionPorts,
) -> Applied | NotApplied:
    """Persist a human "keep distinct" ruling (#797) and commit it.

    No commit section and no presence check: `adjudicate --apply` calls this
    without either, and `commit_keep_distinct` adds both for `curate`. A write
    error is RETURNED as `NotApplied("record_failed")`, never raised: the
    decline already took effect (no merge happened), so the worst case is the
    group being offered again, and losing the operator's place to persist a
    suppression would be the larger harm. The commit subject keeps the member
    order the caller gave."""
    members = tuple(member_ids)
    try:
        rel_path = ports.record_identity_ruling(layout, members, ports.on_warning)
        commit_sha = ports.autocommit(
            root, [rel_path], f"openkos: keep distinct {'/'.join(members)}"
        )
    except (OSError, ValueError) as exc:
        return NotApplied(Action.KEEP_DISTINCT, "record_failed", str(exc))
    return Applied(identity_decision_key(members), Action.KEEP_DISTINCT, commit_sha)


def commit_keep_distinct(
    root: Path,
    layout: config.WorkspaceLayout,
    member_ids: Sequence[str],
    *,
    ports: DecisionPorts,
    observer: DecisionObserver,
) -> Applied | NotApplied:
    """`record_keep_distinct` inside one commit section, after a presence check.

    The prompt held no lock, so a member forgotten meanwhile has no ruling to
    record -- a row naming it would outlive the concept it is about. That case
    is a SILENT `NotApplied("member_missing")`: no observer call and nothing
    written. A record failure notifies the observer inside the section, then
    is returned."""
    members = tuple(member_ids)
    with ports.commit_section():
        return _keep_distinct_in_section(root, layout, members, ports, observer)


def _keep_distinct_in_section(
    root: Path,
    layout: config.WorkspaceLayout,
    members: tuple[str, ...],
    ports: DecisionPorts,
    observer: DecisionObserver,
) -> Applied | NotApplied:
    if not _all_present(layout, members):
        return NotApplied(Action.KEEP_DISTINCT, "member_missing")
    outcome = record_keep_distinct(root, layout, members, ports=ports)
    if isinstance(outcome, NotApplied):
        observer.keep_distinct_unrecorded(members, outcome.detail or "")
    return outcome


@dataclass(frozen=True)
class KeepDistinctDecisionPreview:
    """What a keep-distinct ruling rests on: the members it is about. It has
    no plan to compute, so its version covers the members' current bytes."""

    member_ids: tuple[str, ...]
    decision_key: str


def _keep_distinct_content_lines(
    layout: config.WorkspaceLayout, member_ids: Sequence[str]
) -> list[str]:
    """One `"member\t<id>\t<sha256>"` line per member (`-` for one that does
    not resolve), so a member edited or forgotten moves the version."""
    lines = []
    for member_id in sorted(member_ids):
        path = okf.concept_path_for(member_id, layout.bundle_dir)
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            digest = "-"
        lines.append(f"member\t{member_id}\t{digest}")
    return lines


def commit_keep_distinct_decision(
    root: Path,
    layout: config.WorkspaceLayout,
    preview: KeepDistinctDecisionPreview,
    *,
    ports: DecisionPorts,
    observer: DecisionObserver,
    expected: DecisionVersion,
) -> ApplyOutcome:
    """`commit_keep_distinct` against a version token.

    A member edited since the token was issued returns
    `Stale("content_changed")` before the section; inside the section the open
    row is re-read by `decision_key` (`row_resolved`, `row_superseded`,
    `row_inputs_changed`) before anything is written."""
    refreshed = version_of(preview, layout=layout, payload_digest=None)
    if refreshed.content_digest != expected.content_digest:
        return Stale(expected.decision_key, "content_changed")
    with ports.commit_section():
        if stale := _stale_row(layout, expected) or _stale_inputs(layout, expected):
            return stale
        return _keep_distinct_in_section(
            root, layout, preview.member_ids, ports, observer
        )


# -- identity merge -------------------------------------------------------- #


@dataclass(frozen=True)
class MergeDecisionPreview:
    """What committing one identity merge would do, computed without writing.

    `prepared` is the pure plan and the drift baselines; `reconcile_planned`
    says whether the commit would call the reconciliation port (the one
    predicate, `lifecycle.reconcile_planned`). `group`, `no_reconcile` and
    `reconcile` are how this preview was made, kept so a token commit can
    re-preview from current bytes under the same inputs."""

    prepared: application_lifecycle.PreparedMerge
    member_ids: tuple[str, ...]
    decision_key: str
    reconcile_planned: bool
    cross_source_same_pair: bool
    cross_type_concern: str | None
    survivor_criterion: str
    group: CandidateGroup
    no_reconcile: bool
    reconcile: bool


def preview_merge(
    root: Path,
    layout: config.WorkspaceLayout,
    group: CandidateGroup,
    *,
    verdict: Verdict | None,
    ordered_pair: tuple[str, str],
    survivor_criterion: str | None = None,
    no_reconcile: bool,
    reconcile: bool,
) -> MergeDecisionPreview | NotApplied:
    """Preview merging `group`'s pair, survivor first as pinned by `ordered_pair`.

    Writes nothing and calls no model. Only a persisted SAME judgment is
    mergeable: `verdict` of `None`, DIFFERENT or UNCERTAIN is the RETURNED
    `NotApplied("not_mergeable")`, never a raise. A member that no longer
    resolves is `NotApplied("member_missing")`; a plan that cannot be built
    raises `WriteFailed`. `survivor_criterion` is the caller's, when it already
    stated one; otherwise it is derived."""
    if verdict is not Verdict.SAME:
        return NotApplied(Action.MERGE, "not_mergeable")
    survivor_id, absorbed_id = ordered_pair
    try:
        prepared = application_lifecycle.prepare_one_merge(
            root,
            layout,
            layout.bundle_dir / "index.md",
            layout.bundle_dir / "log.md",
            group,
            ordered_pair=ordered_pair,
        )
    except (OSError, ValueError) as exc:
        raise WriteFailed("merge", (survivor_id, absorbed_id), str(exc)) from exc
    if prepared is None:
        return NotApplied(Action.MERGE, "member_missing")
    if survivor_criterion is None:
        survivor_criterion = application_lifecycle.ordered_merge_pair(
            layout.bundle_dir, group.member_ids
        )[2]
    return MergeDecisionPreview(
        prepared=prepared,
        member_ids=tuple(group.member_ids),
        decision_key=identity_decision_key(group.member_ids),
        reconcile_planned=application_lifecycle.reconcile_planned(
            prepared, no_reconcile=no_reconcile, reconcile=reconcile
        ),
        cross_source_same_pair=application_lifecycle.cross_source_same_pair(
            layout.bundle_dir, group.member_ids
        ),
        cross_type_concern=application_lifecycle.cross_type_concern(
            layout.bundle_dir,
            (prepared.survivor_canonical, prepared.absorbed_canonical),
        ),
        survivor_criterion=survivor_criterion,
        group=group,
        no_reconcile=no_reconcile,
        reconcile=reconcile,
    )


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _relative(layout: config.WorkspaceLayout, path: Path) -> str:
    """`path` relative to the bundle, or as given when it lies outside it."""
    try:
        return path.relative_to(layout.bundle_dir).as_posix()
    except ValueError:
        return str(path)


def _merge_content_lines(
    layout: config.WorkspaceLayout, prepared: application_lifecycle.PreparedMerge
) -> list[str]:
    """One `"<role>\t<path>\t<sha256>"` line per document the plan rests on:
    every write target (the absorbed file as `delete`), every document the
    scan only read, and the name of every document the scan could see (so a
    document appearing or vanishing moves the digest too)."""
    absorbed_path = layout.bundle_dir / f"{prepared.absorbed_canonical}.md"
    lines = [
        f"{'delete' if path == absorbed_path else 'write'}\t"
        f"{_relative(layout, path)}\t{_sha256(data)}"
        for path, data in application_lifecycle.merge_drift_targets(
            layout, prepared, include_catalog=False
        ).items()
    ]
    dependencies = prepared.read_dependencies
    lines.extend(
        f"read\t{_relative(layout, path)}\t{_sha256(data)}"
        for path, data in dependencies.present.items()
    )
    lines.extend(
        f"document\t{_relative(layout, path)}\t-"
        for path in dependencies.documents or ()
    )
    return sorted(lines)


def _relate_content_lines(
    prepared: application_lifecycle.PreparedRelate,
) -> list[str]:
    """One `"write\t<path>\t<sha256>"` line per document the relate plan will
    write and guards: the source, and the target when a deprecated-status
    export is planned. `log.md` is re-composed over its current bytes at
    commit, never guarded, so it is not part of the version."""
    baselines = {f"{prepared.source_canonical}.md": prepared.source_bytes}
    if prepared.target_bytes is not None:
        baselines[f"{prepared.target_canonical}.md"] = prepared.target_bytes
    return sorted(f"write\t{name}\t{_sha256(data)}" for name, data in baselines.items())


def _tier_content_lines(preview: TierDecisionPreview) -> list[str]:
    """The proposed tier and the digest of the configuration it would replace.
    A tier item carries no input digests, so the bytes `openkos.yaml` holds
    now stand for the currently effective tier: another verb moving it moves
    the version."""
    return sorted(
        [
            f"tier\t{preview.type_name}\t{preview.tier}",
            f"config\t{_sha256(preview.prepared.config_bytes)}",
        ]
    )


def version_of(
    preview: (
        MergeDecisionPreview
        | RelateDecisionPreview
        | TierDecisionPreview
        | KeepDistinctDecisionPreview
    ),
    *,
    layout: config.WorkspaceLayout,
    payload_digest: str | None,
) -> DecisionVersion:
    """The version a stateless caller commits `preview` against.

    `payload_digest` is the open queue row's, or `None` when no row backs the
    item. The CLI never calls this, so its walk pays no hashing cost."""
    if isinstance(preview, RelateDecisionPreview):
        lines = _relate_content_lines(preview.prepared)
    elif isinstance(preview, TierDecisionPreview):
        lines = _tier_content_lines(preview)
    elif isinstance(preview, KeepDistinctDecisionPreview):
        lines = _keep_distinct_content_lines(layout, preview.member_ids)
    else:
        lines = _merge_content_lines(layout, preview.prepared)
    return DecisionVersion(
        decision_key=preview.decision_key,
        payload_digest=payload_digest,
        content_digest=_sha256("\n".join(lines).encode("utf-8")),
    )


def _repreview(
    root: Path, layout: config.WorkspaceLayout, preview: MergeDecisionPreview
) -> MergeDecisionPreview | NotApplied:
    """The same merge, re-planned from the bytes on disk now."""
    return preview_merge(
        root,
        layout,
        preview.group,
        verdict=Verdict.SAME,
        ordered_pair=(
            preview.prepared.survivor_canonical,
            preview.prepared.absorbed_canonical,
        ),
        survivor_criterion=preview.survivor_criterion,
        no_reconcile=preview.no_reconcile,
        reconcile=preview.reconcile,
    )


def _stale_row(
    layout: config.WorkspaceLayout, expected: DecisionVersion
) -> Stale | None:
    """Whether the queue row `expected` was issued for is still the open one
    and unchanged (`row_resolved`, `row_superseded`). Called INSIDE the commit
    section, where every queue writer also enters, so the check and the write
    cannot interleave with a producer."""
    row = (
        None
        if expected.payload_digest is None
        else curate_queue.open_row_by_key(layout, expected.decision_key)
    )
    if row is None:
        return Stale(expected.decision_key, "row_resolved")
    if row.item.payload_digest != expected.payload_digest:
        return Stale(expected.decision_key, "row_superseded")
    return None


def _stale_inputs(
    layout: config.WorkspaceLayout, expected: DecisionVersion
) -> Stale | None:
    """Whether the open row `expected` was issued for was computed from inputs
    that have changed since (`row_inputs_changed`). Called INSIDE the section,
    right before the write and AFTER the drift guards: a document that moved
    after the token was checked is a race, raised as drift, so what reaches
    this check is a row that was already stale when the token was issued."""
    row = curate_queue.open_row_by_key(layout, expected.decision_key)
    if row is None:
        return Stale(expected.decision_key, "row_resolved")
    if not _inputs_fresh(layout, row):
        return Stale(expected.decision_key, "row_inputs_changed")
    return None


def commit_merge_decision(
    root: Path,
    layout: config.WorkspaceLayout,
    preview: MergeDecisionPreview,
    *,
    ports: DecisionPorts,
    observer: DecisionObserver,
    surface: str,
    expected: DecisionVersion | None = None,
) -> ApplyOutcome:
    """Commit one previewed identity merge.

    With `expected=None` (the CLI) the drift guard compares against the
    baselines read at preview, so nothing is re-previewed. With a token, the
    merge is re-previewed from current bytes first; a changed
    `content_digest` returns `Stale("content_changed")` before any model call
    or section, and inside the section the open row is re-read by
    `decision_key` (`row_resolved`, `row_superseded`).

    Order: the reconciliation port (only when planned), then the commit
    section, inside which -- in `curate`'s order, not `merge_concepts'` --
    members present, target drift, catalog recompose, read drift, then the
    write and its auto-commit. A vanished member is RETURNED
    (`NotApplied("member_missing")`, announced through the observer inside the
    section); a drift of any kind is RAISED as `DriftDetected`, whose message
    names the check and carries `surface`; a failing write is `WriteFailed`.
    Every failed check writes nothing."""
    if expected is not None:
        refreshed = _repreview(root, layout, preview)
        if isinstance(refreshed, NotApplied):
            return refreshed
        if (
            version_of(refreshed, layout=layout, payload_digest=None).content_digest
            != expected.content_digest
        ):
            return Stale(expected.decision_key, "content_changed")
        preview = refreshed

    prepared = preview.prepared
    if preview.reconcile_planned:
        prepared = ports.reconcile(root, prepared)

    absorbed_path = layout.bundle_dir / f"{prepared.absorbed_canonical}.md"
    survivor_path = layout.bundle_dir / f"{prepared.survivor_canonical}.md"
    with ports.commit_section():
        if expected is not None and (stale := _stale_row(layout, expected)):
            return stale
        if not (survivor_path.exists() and absorbed_path.exists()):
            observer.merge_member_missing(
                prepared.survivor_canonical, prepared.absorbed_canonical
            )
            return NotApplied(Action.MERGE, "member_missing")
        # `index.md`/`log.md` are re-composed over their current bytes below,
        # not guarded: every verb appends to them.
        drift = application_drift.describe_drift(
            layout,
            application_lifecycle.merge_drift_targets(
                layout, prepared, include_catalog=False
            ),
            surface,
            deletes=frozenset({absorbed_path}),
        )
        if drift is not None:
            raise DriftDetected(drift)
        try:
            prepared = application_lifecycle.recompose_merge_catalog(
                layout, prepared, verb=surface
            )
        except catalog_delta.CatalogRecomposeError as exc:
            raise DriftDetected(str(exc)) from exc
        read_drift = commit_phase.describe_read_drift(
            layout, prepared.read_dependencies, surface
        )
        if read_drift is not None:
            raise DriftDetected(read_drift)
        if expected is not None and (stale := _stale_inputs(layout, expected)):
            return stale
        try:
            commit_sha = merge_service.commit_merge(
                root, layout, prepared, autocommit=ports.autocommit
            )
        except (OSError, ValueError) as exc:
            raise WriteFailed(
                "merge",
                (prepared.survivor_canonical, prepared.absorbed_canonical),
                str(exc),
            ) from exc
    return Applied(preview.decision_key, Action.MERGE, commit_sha)


# -- relate ---------------------------------------------------------------- #


@dataclass(frozen=True)
class RelateDecisionPreview:
    """What committing one relation would do, computed without writing.

    `source_id` and `target_id` are the edge AS WRITTEN: already swapped when
    `reverse`. `decision_key` is the same for either direction (the queue key
    is the unordered edge). `suggestion`, `reverse` and `now` are how this
    preview was made, kept so a token commit can re-preview from current
    bytes."""

    prepared: application_lifecycle.PreparedRelate
    source_id: str
    target_id: str
    relation_type: str
    decision_key: str
    suggestion: EdgeSuggestion
    reverse: bool
    now: datetime

    @property
    def action(self) -> Action:
        return Action.RELATE_REVERSED if self.reverse else Action.RELATE


def preview_relate(
    root: Path,
    layout: config.WorkspaceLayout,
    suggestion: EdgeSuggestion,
    *,
    reverse: bool,
    now: datetime,
) -> RelateDecisionPreview | NotApplied:
    """Preview relating `suggestion`'s effective edge, swapped when `reverse`.

    Writes nothing and calls no model. `now` is the caller's, once per stage,
    so every entry of a run carries one date. A suggestion with no valid type
    is `NotApplied("not_offered")`; an endpoint that no longer resolves is
    `NotApplied("member_missing")`; a plan that cannot be built raises
    `WriteFailed`."""
    action = Action.RELATE_REVERSED if reverse else Action.RELATE
    relation_type = suggestion.suggested_type
    if relation_type is None:
        return NotApplied(action, "not_offered")
    edge = suggestion.effective_edge
    source_id, target_id = (
        (edge.target_id, edge.source_id)
        if reverse
        else (edge.source_id, edge.target_id)
    )
    if not _all_present(layout, (source_id, target_id)):
        return NotApplied(action, "member_missing")
    try:
        prepared = application_lifecycle.prepare_relate(
            okf.concept_path_for(source_id, layout.bundle_dir),
            layout.bundle_dir / "log.md",
            source_id,
            target_id,
            relation_type,
            root,
            now=now,
            target_path=okf.concept_path_for(target_id, layout.bundle_dir),
        )
    except (OSError, ValueError) as exc:
        raise WriteFailed("relate", (source_id, target_id), str(exc)) from exc
    return RelateDecisionPreview(
        prepared=prepared,
        source_id=source_id,
        target_id=target_id,
        relation_type=relation_type,
        decision_key=pq.stored_decision_key(
            "relation_type", pq.relation_type_key(source_id, target_id)
        ),
        suggestion=suggestion,
        reverse=reverse,
        now=now,
    )


def commit_relate_decision(
    root: Path,
    layout: config.WorkspaceLayout,
    preview: RelateDecisionPreview,
    *,
    ports: DecisionPorts,
    observer: DecisionObserver,
    surface: str,
    expected: DecisionVersion | None = None,
) -> ApplyOutcome:
    """Commit one previewed relation.

    With `expected=None` (the CLI) the drift guard compares against the
    baselines read at preview. With a token, the relation is re-previewed
    from current bytes first (`Stale("content_changed")` before the section),
    and inside the section the open row is re-read by `decision_key`
    (`row_resolved`, `row_superseded`).

    Inside ONE commit section, in order: endpoints present (a vanished one is
    announced through the observer and RETURNED as
    `NotApplied("member_missing")`), endpoint drift, the `log.md` recompose
    (both RAISED as `DriftDetected`), then the write through
    `lifecycle.relate_core` and its auto-commit. A failing write is
    `WriteFailed`. Every failed check writes nothing."""
    if expected is not None:
        refreshed = preview_relate(
            root, layout, preview.suggestion, reverse=preview.reverse, now=preview.now
        )
        if isinstance(refreshed, NotApplied):
            return refreshed
        if (
            version_of(refreshed, layout=layout, payload_digest=None).content_digest
            != expected.content_digest
        ):
            return Stale(expected.decision_key, "content_changed")
        preview = refreshed

    prepared = preview.prepared
    source_path = okf.concept_path_for(preview.source_id, layout.bundle_dir)
    target_path = okf.concept_path_for(preview.target_id, layout.bundle_dir)
    log_path = layout.bundle_dir / "log.md"
    with ports.commit_section():
        if expected is not None and (stale := _stale_row(layout, expected)):
            return stale
        if not _all_present(layout, (preview.source_id, preview.target_id)):
            observer.relate_endpoint_missing(preview.source_id, preview.target_id)
            return NotApplied(preview.action, "member_missing")
        # `log.md` is re-composed below, not guarded: another verb's entry
        # appended since the plan was composed is kept.
        baselines = {source_path: prepared.source_bytes}
        if prepared.target_bytes is not None:
            baselines[target_path] = prepared.target_bytes
        drift = application_drift.describe_drift(layout, baselines, surface)
        if drift is not None:
            raise DriftDetected(drift)
        try:
            prepared = application_lifecycle.recompose_relate_log(
                log_path, prepared, verb=surface
            )
        except catalog_delta.CatalogRecomposeError as exc:
            raise DriftDetected(str(exc)) from exc
        if expected is not None and (stale := _stale_inputs(layout, expected)):
            return stale
        try:
            application_lifecycle.relate_core(
                source_path, log_path, prepared, target_path=target_path
            )
        except (OSError, ValueError) as exc:
            raise WriteFailed(
                "relate", (preview.source_id, preview.target_id), str(exc)
            ) from exc
        paths = [f"bundle/{preview.source_id}.md", "bundle/log.md"]
        if prepared.new_target_text is not None:
            paths.insert(1, f"bundle/{preview.target_id}.md")
        commit_sha = ports.autocommit(
            root,
            paths,
            f"openkos: relate {preview.source_id} -> {preview.target_id} "
            f"({preview.relation_type})",
        )
    return Applied(preview.decision_key, preview.action, commit_sha)


# -- set tier -------------------------------------------------------------- #


@dataclass(frozen=True)
class TierDecisionPreview:
    """What setting one concept type's volatility tier would do, computed
    without writing. `prepared.config_bytes` is the drift baseline and, with
    the proposed tier, the content of the version."""

    prepared: application_lifecycle.PreparedSetVolatility
    type_name: str
    tier: str
    decision_key: str


def preview_set_tier(
    layout: config.WorkspaceLayout, type_name: str, tier: str
) -> TierDecisionPreview:
    """Preview setting `type_name` to `tier` in `openkos.yaml`.

    Writes nothing and calls no model. A configuration shape that cannot be
    edited raises `WriteFailed("set_tier", ...)`."""
    try:
        prepared = application_lifecycle.prepare_set_volatility(
            layout.config_path, type_name, tier
        )
    except (OSError, ValueError) as exc:
        raise WriteFailed("set_tier", (type_name,), str(exc)) from exc
    return TierDecisionPreview(
        prepared=prepared,
        type_name=type_name,
        tier=tier,
        decision_key=pq.stored_decision_key("volatility", pq.volatility_key(type_name)),
    )


def commit_tier_decision(
    root: Path,
    layout: config.WorkspaceLayout,
    preview: TierDecisionPreview,
    *,
    ports: DecisionPorts,
    surface: str,
    expected: DecisionVersion | None = None,
) -> ApplyOutcome:
    """Commit one previewed tier change.

    With `expected=None` (the CLI) the drift guard compares against the bytes
    read at preview. With a token, the change is re-previewed from current
    bytes first (`Stale("content_changed")` before the section, which is what
    an effective tier moved elsewhere looks like), and inside the section the
    open row is re-read by `decision_key` (`row_resolved`, `row_superseded`).

    Inside ONE commit section, in order: config drift (RAISED as
    `DriftDetected`), the config-commit decision (a refusal is RAISED as
    `Refused` carrying the adapter's text), then the write through
    `lifecycle.set_volatility_core` (a failure is `WriteFailed`) and the
    config commit. Every failed check writes nothing. The sha, if any, is on
    the returned `Applied`."""
    if expected is not None:
        refreshed = preview_set_tier(layout, preview.type_name, preview.tier)
        if (
            version_of(refreshed, layout=layout, payload_digest=None).content_digest
            != expected.content_digest
        ):
            return Stale(expected.decision_key, "content_changed")
        preview = refreshed

    prepared = preview.prepared
    message = f"openkos: set-volatility {preview.type_name} -> {preview.tier}"
    with ports.commit_section():
        if expected is not None and (stale := _stale_row(layout, expected)):
            return stale
        drift = application_drift.describe_drift(
            layout, {layout.config_path: prepared.config_bytes}, surface
        )
        if drift is not None:
            raise DriftDetected(drift)
        if expected is not None and (stale := _stale_inputs(layout, expected)):
            return stale
        decision = (
            None
            if ports.plan_config_commit is None
            else ports.plan_config_commit(
                root, surface, preview.type_name, preview.tier, prepared.config_bytes
            )
        )
        if isinstance(decision, ConfigCommitRefusal):
            raise Refused(decision.message)
        try:
            application_lifecycle.set_volatility_core(layout.config_path, prepared)
        except (OSError, ValueError) as exc:
            raise WriteFailed("set_tier", (preview.type_name,), str(exc)) from exc
        commit_sha = (
            ports.autocommit(root, ["openkos.yaml"], message)
            if ports.autocommit_config_edit is None
            else ports.autocommit_config_edit(root, message, decision)
        )
    return Applied(preview.decision_key, Action.SET_TIER, commit_sha)


# -- the list -------------------------------------------------------------- #

DecisionKind = Literal[
    "identity",
    "relation_type",
    "volatility",
    "contradiction",
    "revision",
    "watch_refusal",
]


@dataclass(frozen=True)
class WithheldAction:
    """An action the item's kind could offer but this item does not, and why."""

    action: Action
    reason: Literal[
        "pending_evaluation",
        "judged_different",
        "judged_uncertain",
        "cross_type",
        "more_than_two_members",
        "no_valid_suggestion",
    ]


@dataclass(frozen=True)
class DecisionItem:
    """One open decision, as data a client can render and act on.

    OWNER-LOCAL: `payload` is the stored proposal, which can derive from
    confidential concepts. Nothing here ranks by, or presents, the
    adjudicator's confidence: it travels inside `payload` as stored."""

    decision_key: str
    kind: DecisionKind
    targets: tuple[str, ...]
    fresh: bool | None
    """`False` when the row was computed from inputs that have changed since.
    A stale item is listed, never hidden, and an apply against it is `Stale`.
    `None` means freshness was NOT computed for this kind (one that carries no
    actions here, whose input refs may be synthetic); it never means fresh."""
    evaluation: Literal[
        "judged_same",
        "judged_different",
        "judged_uncertain",
        "pending_evaluation",
        "not_applicable",
    ]
    actions: tuple[Action, ...]
    withheld: tuple[WithheldAction, ...]
    resolves_via: str
    """Where the item resolves when it carries no action (and the command that
    resolves it for one that does)."""
    payload: Mapping[str, object]
    payload_digest: str


_ACTIONABLE_KINDS = frozenset({"identity", "relation_type", "volatility"})

_CONTRADICTION_RESOLVES = (
    "openkos contradictions --decline, openkos reconcile or openkos unmerge"
)


def _inputs_fresh(layout: config.WorkspaceLayout, row: curate_queue.OpenRow) -> bool:
    """The list's freshness predicate for `row`, by kind: recorded inputs
    still hash as recorded (identity, relation_type), the proposed-against tier
    is still the effective one (volatility). The other kinds take no action
    and are never asked: the list reports their freshness as not computed."""
    kind = row.item.kind
    if kind in ("identity", "relation_type"):
        return curate_queue.row_is_fresh(
            row,
            application_pending.current_finding_digest(layout.bundle_dir),
            row.item.targets,
        )
    if kind == "volatility":
        return curate_queue.volatility_row_is_fresh(layout, row)
    return True


def _strings(payload: Mapping[str, object], *names: str) -> tuple[str, ...] | None:
    values = [payload.get(name) for name in names]
    if not all(isinstance(value, str) for value in values):
        return None
    return tuple(str(value) for value in values)


def relation_fields(
    payload: Mapping[str, object],
) -> tuple[str, str, str, str, str, str] | None:
    """`(source, target, effective source, effective target, type, rationale)`
    of a relation row, or `None` when the row does not carry a usable type."""
    fields = _strings(
        payload,
        "source_id",
        "target_id",
        "effective_source_id",
        "effective_target_id",
        "suggested_type",
        "rationale",
    )
    if fields is None:
        return None
    try:
        suggested = validate_relation_type(fields[4])
    except ValueError:
        return None
    return (fields[0], fields[1], fields[2], fields[3], suggested, fields[5])


def _tier_fields(payload: Mapping[str, object]) -> tuple[str, str] | None:
    """`(type name, proposed tier)` of a volatility row, or `None`."""
    fields = _strings(payload, "type_name", "suggested_tier")
    if fields is None or fields[1] not in model_types.VOLATILITY_TIERS:
        return None
    return fields[0], fields[1]


def tier_detail_fields(
    payload: Mapping[str, object],
) -> tuple[str, str, str, str] | None:
    """`(type name, current default, proposed tier, rationale)` of a volatility
    row, or `None` when the row does not carry a usable proposal."""
    fields = _strings(
        payload, "type_name", "current_default", "suggested_tier", "rationale"
    )
    if fields is None or fields[2] not in model_types.VOLATILITY_TIERS:
        return None
    return fields[0], fields[1], fields[2], fields[3]


def _members_of(
    payload: Mapping[str, object], targets: tuple[str, ...]
) -> tuple[str, ...]:
    listed = payload.get("member_ids")
    if isinstance(listed, list) and all(isinstance(m, str) for m in listed):
        return tuple(str(m) for m in listed)
    return targets


def identity_detail_fields(
    payload: Mapping[str, object], targets: tuple[str, ...]
) -> tuple[tuple[str, ...], str, str, str | None, str | None] | None:
    """`(members, type, tier, verdict, rationale)` of an identity row, or
    `None` when the row does not carry a usable group.

    `members` falls back to the row's `targets` exactly as the actions do. The
    verdict and rationale are `None` for an unjudged row or one whose stored
    verdict is not one of the three known. The adjudicator's confidence is
    deliberately not read: nothing presents it."""
    okf_type = payload.get("okf_type")
    if not isinstance(okf_type, str):
        return None
    try:
        tier = Tier(str(payload.get("tier")))
    except ValueError:
        return None
    adjudication = payload.get("adjudication")
    verdict: str | None = None
    rationale: str | None = None
    if isinstance(adjudication, dict):
        stored = adjudication.get("verdict")
        if stored in {v.value for v in Verdict}:
            verdict = str(stored)
            said = adjudication.get("rationale")
            rationale = said if isinstance(said, str) else None
    return _members_of(payload, targets), okf_type, tier.value, verdict, rationale


def _identity_members(row: curate_queue.OpenRow) -> tuple[str, ...]:
    return _members_of(row.payload, row.item.targets)


def _identity_actions(
    layout: config.WorkspaceLayout, row: curate_queue.OpenRow
) -> tuple[str, tuple[Action, ...], tuple[WithheldAction, ...]]:
    adjudication = row.payload.get("adjudication")
    verdict = adjudication.get("verdict") if isinstance(adjudication, dict) else None
    members = _identity_members(row)
    reason: (
        Literal[
            "pending_evaluation",
            "judged_different",
            "judged_uncertain",
            "cross_type",
            "more_than_two_members",
        ]
        | None
    )
    if verdict == Verdict.SAME.value:
        evaluation = "judged_same"
        if len(members) != 2:
            reason = "more_than_two_members"
        elif application_lifecycle.cross_type_concern(layout.bundle_dir, members):
            reason = "cross_type"
        else:
            reason = None
    elif verdict == Verdict.DIFFERENT.value:
        evaluation, reason = "judged_different", "judged_different"
    elif verdict == Verdict.UNCERTAIN.value:
        # A judgment the model declined to make (a malformed reply degrades to
        # it too): not a group still waiting for evaluation.
        evaluation, reason = "judged_uncertain", "judged_uncertain"
    else:
        evaluation, reason = "pending_evaluation", "pending_evaluation"
    if reason is None:
        return evaluation, (Action.MERGE, Action.KEEP_DISTINCT, Action.SKIP), ()
    return (
        evaluation,
        (Action.KEEP_DISTINCT, Action.SKIP),
        (WithheldAction(Action.MERGE, reason),),
    )


def _actions_of(
    layout: config.WorkspaceLayout, row: curate_queue.OpenRow
) -> tuple[str, tuple[Action, ...], tuple[WithheldAction, ...]]:
    """`(evaluation, actions, withheld)` for one open row, by kind."""
    kind = row.item.kind
    if kind == "identity":
        return _identity_actions(layout, row)
    if kind == "relation_type":
        fields = relation_fields(row.payload)
        if fields is None:
            return (
                "not_applicable",
                (),
                (WithheldAction(Action.RELATE, "no_valid_suggestion"),),
            )
        suggested = fields[4]
        actions: tuple[Action, ...]
        if suggested == "supersedes":
            return "not_applicable", (), ()
        if suggested in ASYMMETRIC_RELATION_TYPES:
            actions = (Action.RELATE, Action.RELATE_REVERSED, Action.SKIP)
        else:
            actions = (Action.RELATE, Action.SKIP)
        return "not_applicable", actions, ()
    if kind == "volatility":
        if _tier_fields(row.payload) is None:
            return (
                "not_applicable",
                (),
                (WithheldAction(Action.SET_TIER, "no_valid_suggestion"),),
            )
        return "not_applicable", (Action.SET_TIER, Action.SKIP), ()
    return "not_applicable", (), ()


def _configured_inbox(layout: config.WorkspaceLayout) -> Path | None:
    try:
        return config.read_config(layout.root).unattended.inbox
    except OSError, ValueError:
        return None


def _item_of(
    layout: config.WorkspaceLayout, row: curate_queue.OpenRow, inbox: Path | None
) -> DecisionItem:
    evaluation, actions, withheld = _actions_of(layout, row)
    kind = row.item.kind
    return DecisionItem(
        decision_key=row.item.decision_key,
        kind=kind,  # type: ignore[arg-type]
        targets=row.item.targets,
        fresh=_inputs_fresh(layout, row) if kind in _ACTIONABLE_KINDS else None,
        evaluation=evaluation,  # type: ignore[arg-type]
        actions=actions,
        withheld=withheld,
        resolves_via=(
            _CONTRADICTION_RESOLVES
            if kind == "contradiction"
            else pending_queue_report.row_resolving_command(
                row.item, inbox, layout.bundle_dir
            )
        ),
        payload=MappingProxyType(row.payload),
        payload_digest=row.item.payload_digest,
    )


def list_open_decisions(layout: config.WorkspaceLayout) -> tuple[DecisionItem, ...]:
    """Every open decision, read from the pending-work queue.

    LLM-free, lock-free and write-free: it constructs no model client, takes
    no lock and creates nothing, so an unreachable backend or an absent queue
    does not matter. Order is deterministic: `pq.KINDS` order, then the
    queue's own order within a kind. A stale row is listed with
    `fresh=False`, never omitted. OWNER-LOCAL, like every `DecisionItem`."""
    inbox = _configured_inbox(layout)
    return tuple(
        _item_of(layout, row, inbox)
        for kind in pq.KINDS
        for row in curate_queue.read_open_rows(layout, kind)
    )


# -- preview and apply by token --------------------------------------------- #


@dataclass(frozen=True)
class DecisionPolicy:
    """The inputs a re-preview needs that the token does not carry. A caller
    holding only the token and the `decision_key` commits with the defaults.
    None of the three enters the token, so a different policy never makes a
    token `Stale`."""

    no_reconcile: bool = False
    reconcile: bool = False
    now: datetime | None = None
    """`None`: read once per call from the clock."""


DecisionPreviewBody = (
    MergeDecisionPreview
    | RelateDecisionPreview
    | TierDecisionPreview
    | KeepDistinctDecisionPreview
)


@dataclass(frozen=True)
class DecisionPreview:
    """What an action would do, and the version to commit it against."""

    preview: DecisionPreviewBody
    version: DecisionVersion


def _refusal(item: DecisionItem, action: Action) -> NotApplied | None:
    """Why `action` is not one this item takes, or `None` when it is."""
    if action in item.actions:
        return None
    if any(w.action is action and action is Action.MERGE for w in item.withheld):
        return NotApplied(action, "not_mergeable")
    return NotApplied(action, "not_offered")


def _group_of(row: curate_queue.OpenRow) -> CandidateGroup:
    """The candidate group a merge preview needs, rebuilt from the row's own
    payload (no detection runs)."""
    members = tuple(sorted(_identity_members(row)))
    types = row.payload.get("member_types")
    member_types = (
        tuple(str(t) for t in types)
        if isinstance(types, list) and len(types) == len(members)
        else ()
    )
    try:
        tier = Tier(str(row.payload.get("tier")))
    except ValueError:
        tier = Tier.HIGH
    return CandidateGroup(
        okf_type=str(row.payload.get("okf_type") or ""),
        member_ids=members,
        tier=tier,
        trigger=str(row.payload.get("trigger") or ""),
        member_types=member_types,
    )


def _build_preview(
    root: Path,
    layout: config.WorkspaceLayout,
    row: curate_queue.OpenRow,
    action: Action,
    policy: DecisionPolicy,
) -> DecisionPreviewBody | NotApplied:
    """The preview for `action` over the open `row`: member ids, relation
    endpoints and type, and proposed tier all come from the row's payload."""
    if action is Action.MERGE:
        group = _group_of(row)
        survivor, absorbed, criterion = application_lifecycle.ordered_merge_pair(
            layout.bundle_dir, group.member_ids
        )
        return preview_merge(
            root,
            layout,
            group,
            verdict=Verdict.SAME,
            ordered_pair=(survivor, absorbed),
            survivor_criterion=criterion,
            no_reconcile=policy.no_reconcile,
            reconcile=policy.reconcile,
        )
    if action in (Action.RELATE, Action.RELATE_REVERSED):
        fields = relation_fields(row.payload)
        if fields is None:
            return NotApplied(action, "not_offered")
        source, target, eff_source, eff_target, suggested, rationale = fields
        edge, effective = Edge(source, target), Edge(eff_source, eff_target)
        return preview_relate(
            root,
            layout,
            EdgeSuggestion(
                edge=edge,
                suggested_type=suggested,
                rationale=rationale,
                corrected_edge=None if effective == edge else effective,
            ),
            reverse=action is Action.RELATE_REVERSED,
            now=policy.now or datetime.now(UTC),
        )
    if action is Action.SET_TIER:
        tier_fields = _tier_fields(row.payload)
        if tier_fields is None:
            return NotApplied(action, "not_offered")
        return preview_set_tier(layout, *tier_fields)
    members = tuple(sorted(_identity_members(row)))
    return KeepDistinctDecisionPreview(members, row.item.decision_key)


def preview_decision(
    root: Path,
    layout: config.WorkspaceLayout,
    decision_key: str,
    action: Action,
    *,
    policy: DecisionPolicy = DecisionPolicy(),  # noqa: B008 -- frozen, shared
) -> DecisionPreview | NotApplied | Stale:
    """What `action` would do to the open decision `decision_key`, with the
    version to commit it against. Writes nothing, takes no lock, calls no
    model. A key with no open row is `Stale("row_resolved")`; an action the item
    does not take is `NotApplied` (`not_mergeable` for a withheld merge,
    `not_offered` otherwise); skip has no plan, so it is
    `NotApplied("skipped")`."""
    row = curate_queue.open_row_by_key(layout, decision_key)
    if row is None:
        return Stale(decision_key, "row_resolved")
    item = _item_of(layout, row, None)
    if (refusal := _refusal(item, action)) is not None:
        return refusal
    if action is Action.SKIP:
        return skip_decision(decision_key)
    built = _build_preview(root, layout, row, action, policy)
    if isinstance(built, NotApplied):
        return built
    return DecisionPreview(
        built,
        version_of(built, layout=layout, payload_digest=row.item.payload_digest),
    )


def apply_decision(
    root: Path,
    layout: config.WorkspaceLayout,
    version: DecisionVersion,
    action: Action,
    *,
    policy: DecisionPolicy = DecisionPolicy(),  # noqa: B008 -- frozen, shared
    ports: DecisionPorts,
    observer: DecisionObserver,
    surface: str,
) -> ApplyOutcome:
    """Apply `action` to the decision `version` was issued for, against that
    version, holding no state but the token.

    The item is re-listed by `version.decision_key`. A merge the item withholds
    is `NotApplied("not_mergeable")` and any other action outside its set is
    `NotApplied("not_offered")`; skip writes nothing and returns
    `NotApplied("skipped")`, leaving the row `pending`. Otherwise the action is
    re-previewed from current bytes and committed on the token path of its
    kind, which returns `Stale` (and writes nothing) when the version no longer
    holds. A decision with no open row is `Stale("row_resolved")`."""
    row = curate_queue.open_row_by_key(layout, version.decision_key)
    if row is None:
        return Stale(version.decision_key, "row_resolved")
    if (refusal := _refusal(_item_of(layout, row, None), action)) is not None:
        return refusal
    if action is Action.SKIP:
        return skip_decision(version.decision_key)
    built = _build_preview(root, layout, row, action, policy)
    if isinstance(built, NotApplied):
        return built
    if isinstance(built, MergeDecisionPreview):
        return commit_merge_decision(
            root,
            layout,
            built,
            ports=ports,
            observer=observer,
            surface=surface,
            expected=version,
        )
    if isinstance(built, RelateDecisionPreview):
        return commit_relate_decision(
            root,
            layout,
            built,
            ports=ports,
            observer=observer,
            surface=surface,
            expected=version,
        )
    if isinstance(built, TierDecisionPreview):
        return commit_tier_decision(
            root, layout, built, ports=ports, surface=surface, expected=version
        )
    return commit_keep_distinct_decision(
        root, layout, built, ports=ports, observer=observer, expected=version
    )


# -- consent as data ------------------------------------------------------- #


class BulkEligibility(StrEnum):
    """Whether `--accept` (or `review: false`) may settle a kind's decisions
    without asking per item."""

    NEVER = "never"
    SYMMETRIC_ONLY = "symmetric_only"
    ALWAYS = "always"


BULK_ELIGIBILITY: Mapping[DecisionKind, BulkEligibility] = MappingProxyType(
    {
        # An identity merge is destructive and judged by a model: it is asked
        # one group at a time, never accepted in bulk. This row is the
        # data-level barrier; `item_consent` also refuses it by name.
        "identity": BulkEligibility.NEVER,
        # Asymmetric types ask per item (#624); `related_to` is bulk (#1222).
        "relation_type": BulkEligibility.SYMMETRIC_ONLY,
        "volatility": BulkEligibility.ALWAYS,
        "contradiction": BulkEligibility.NEVER,
        "revision": BulkEligibility.NEVER,
        "watch_refusal": BulkEligibility.NEVER,
    }
)


def spend_consent(
    *, auto: bool, interactive: Callable[[], bool]
) -> Literal["granted", "ask", "refused"]:
    """The model-spend decision of one LLM-costing stage. `--auto` consents
    outright, without consulting the terminal; otherwise a terminal can be
    asked and a pipe has no channel to ask on. `interactive` is a thunk so the
    terminal is read only on the branch that needs it."""
    if auto:
        return "granted"
    return "ask" if interactive() else "refused"


def write_walk_consent(
    *,
    writes: bool,
    interactive: Callable[[], bool],
    accepted: bool,
    unattended_identity: bool,
) -> Literal["walk", "refused"]:
    """Whether a stage may begin its per-item write walk once its spend was
    granted. `--auto` consents to spend, never to a per-item write, so a
    writing stage on a pipe is refused unless naming it in `--accept` (or the
    unattended identity pass) supplied the missing consent. Only a writing
    stage reads the terminal."""
    if not writes:
        return "walk"
    if interactive() or accepted or unattended_identity:
        return "walk"
    return "refused"


def item_consent(
    kind: DecisionKind,
    *,
    accepted: bool,
    asymmetric: bool,
    interactive: Callable[[], bool],
) -> Literal["bulk", "ask", "withheld"]:
    """How one item's write consent is obtained: `bulk` (taken from an
    accepted stage), `ask` (a per-item prompt) or `withheld` (an accepted
    stage's exempt item on a pipe, where there is no channel to ask on).

    `asymmetric` marks an item exempt from bulk acceptance: an asymmetric
    relation whose direction is unverified (#624), or any suggestion that
    asserts nothing specific (#508). It exempts under every non-`NEVER` row, so
    the table decides which KINDS may be accepted in bulk and the flag which
    ITEMS of an accepted kind still ask.

    Identity is `ask` for every input, whatever the table or the flags say. The
    terminal is read only for an accepted-but-exempt item."""
    if kind == "identity" or not accepted:
        return "ask"
    if BULK_ELIGIBILITY[kind] is not BulkEligibility.NEVER and not asymmetric:
        return "bulk"
    return "ask" if interactive() else "withheld"
