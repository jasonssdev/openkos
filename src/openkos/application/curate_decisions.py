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
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal

from openkos import config
from openkos.application import (
    catalog_delta,
    commit_phase,
    curate_queue,
    duplicates_service,
    merge_service,
)
from openkos.application import drift as application_drift
from openkos.application import lifecycle as application_lifecycle
from openkos.application.lock_wait import CommitSection
from openkos.application.write_gate import DriftDetected, WriteFailed
from openkos.model import okf
from openkos.resolution.adjudication import Verdict
from openkos.resolution.candidates import CandidateGroup
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
        if not _all_present(layout, members):
            return NotApplied(Action.KEEP_DISTINCT, "member_missing")
        outcome = record_keep_distinct(root, layout, members, ports=ports)
        if isinstance(outcome, NotApplied):
            observer.keep_distinct_unrecorded(members, outcome.detail or "")
        return outcome


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


def version_of(
    preview: MergeDecisionPreview | RelateDecisionPreview,
    *,
    layout: config.WorkspaceLayout,
    payload_digest: str | None,
) -> DecisionVersion:
    """The version a stateless caller commits `preview` against.

    `payload_digest` is the open queue row's, or `None` when no row backs the
    item. The CLI never calls this, so its walk pays no hashing cost."""
    lines = (
        _relate_content_lines(preview.prepared)
        if isinstance(preview, RelateDecisionPreview)
        else _merge_content_lines(layout, preview.prepared)
    )
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
    """Whether the queue row `expected` was issued for is still the open one.
    Called INSIDE the commit section, where every queue writer also enters,
    so the check and the write cannot interleave with a producer."""
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
