"""The `reconcile` use case, as an application service (ADR-0018, issue
#1168; the reconcile slice of the write-core extraction that began with
`ingest_service.py`).

`reconcile` records how a human resolved a contradiction between two concepts
as additive typed edges plus hidden-anchored `## Reconciliation` body notes --
a symmetric `reconciled_with`, a directed `supersedes` (`--winner`) or a
directed `revises` (`--revision`). This module owns the whole write:

* `reconcile_concepts` -- the two-id form: the workspace gate, request
  validation, pair resolution, the workspace config and one transaction;
* `reconcile_pair` -- ONE pair's complete transaction (Phase A in-memory
  build, the at-most-one-resolution conflict gate, preview, confirmation,
  drift re-validation, Phase B additive writes, auto-commit), which the CLI's
  `--from-findings` walks (#567, #1014) call once per accepted finding so
  every door shares ONE write path;
* the pure pair machinery (`resolve_pair_member`, the resolution-state
  classifier, the note and anchor builders).

It returns typed outcomes and raises the typed refusals of
`application.write_gate`; it never prompts, renders, reads the current
directory or raises `typer.Exit`. What it reaches through parameters instead
(the layering invariant forbids `openkos.cli`, `typer`, `rich` and
`openkos.vcs` here): the confirmation question (`confirm`), every word the
user reads (`ReconcileObserver`) and the concrete effects (`ReconcilePorts`:
the auto-commit, the snapshot read and the clock). The post-write
derived-index refresh stays the adapter's, because the batch walk refreshes
once for all its pairs, never per pair (#655).
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from openkos import config, fsio
from openkos.application import drift as application_drift
from openkos.application import lifecycle as application_lifecycle
from openkos.application.lock_wait import CommitSection
from openkos.application.write_gate import (
    ConfirmCallback,
    DriftDetected,
    Refused,
    require_confirmation,
)
from openkos.bundle import log as bundle_log
from openkos.model import okf

_RECONCILE_ANCHOR_TEMPLATE = "<!-- okos:reconcile target={target} role={role} -->"
"""Hidden HTML-comment anchor keyed on the counterpart concept-id (design:
Interfaces / Contracts). `reconcile`'s idempotency check
(`_reconcile_anchor_present`) matches on `target=<id>` alone, ignoring
`role` and the note's heading level, so ANY prior anchor for that
counterpart -- however it got there -- suppresses a re-append."""

_RECONCILE_ANCHOR_RE = re.compile(r"<!-- okos:reconcile target=(\S+) role=(\w+) -->")


def _reconcile_anchor_present(body: str, counterpart_id: str) -> bool:
    """Return whether `body` already carries a `## Reconciliation` anchor
    referencing `counterpart_id` (any role) -- `reconcile`'s idempotency
    gate: a repeated call for the same pair never re-appends a duplicate
    note (spec: Idempotent Re-run)."""
    return any(
        match.group(1) == counterpart_id
        for match in _RECONCILE_ANCHOR_RE.finditer(body)
    )


ReconcileRole = Literal["reconciled", "supersedes", "superseded", "revises", "revised"]


def _reconcile_sentence(role: ReconcileRole, counterpart_id: str, date_str: str) -> str:
    """One human-readable sentence for a `## Reconciliation` note, per
    `role` (design: Interfaces / Contracts) -- `reconciled` (symmetric,
    both coexist), `supersedes` (this concept wins), `superseded` (hidden
    from retrieval as of this edge; deprecated-status-export, issue #1075,
    also exports this onto the concept's own `status` unless a
    human-authored value blocks it), `revises` (this concept refines its
    counterpart; both remain current), or `revised` (the mirror role on the
    refined counterpart). `role` is a closed `Literal`, and any other value
    raises defensively (rather than silently falling through to the
    "superseded" sentence) so a typo can never mislabel a note."""
    link = f"[{counterpart_id}](/{counterpart_id}.md)"
    if role == "reconciled":
        return f"Reconciled with {link} on {date_str} (both coexist)."
    if role == "supersedes":
        return f"Supersedes {link} as of {date_str} (this concept wins)."
    if role == "superseded":
        return f"Superseded by {link} as of {date_str} (hidden from retrieval)."
    if role == "revises":
        return f"Revises {link} as of {date_str} (refinement; both remain current)."
    if role == "revised":
        return f"Revised by {link} as of {date_str} (refinement; both remain current)."
    raise ValueError(f"unexpected reconciliation role {role!r}")


def _reconciliation_note(
    *, counterpart_id: str, role: ReconcileRole, date_str: str
) -> str:
    """Build one full `## Reconciliation` body note: an h2 heading (chosen
    over `#` to avoid a second top-level heading alongside the concept's own
    title, design note), the hidden anchor keyed on `counterpart_id`, and
    one sentence linking to the counterpart."""
    anchor = _RECONCILE_ANCHOR_TEMPLATE.format(target=counterpart_id, role=role)
    sentence = _reconcile_sentence(role, counterpart_id, date_str)
    return f"## Reconciliation\n{anchor}\n{sentence}\n"


def _append_reconciliation_note(body: str, note: str) -> str:
    """Append `note` to `body` as a new trailing section, additive-only --
    never overwrites existing content (mirrors
    `okf.build_merged_document`'s body-append separator math)."""
    new_body = body.rstrip("\n") + "\n\n" + note
    if not new_body.endswith("\n"):
        new_body += "\n"
    return new_body


def _add_relation_if_absent(
    relations: list[okf.Relation], new_relation: okf.Relation
) -> tuple[list[okf.Relation], bool]:
    """Append `new_relation` to `relations` unless an identical
    `(target, type)` pair is already present, mirroring `relate`'s
    idempotent dedup (task 2.3). Returns the possibly-extended list and
    whether an entry was actually added."""
    already_present = any(
        relation.target == new_relation.target and relation.type == new_relation.type
        for relation in relations
    )
    if already_present:
        return relations, False
    return [*relations, new_relation], True


ResolutionMode = Literal["none", "symmetric", "directional", "revision", "mixed"]
RequestedMode = Literal["symmetric", "directional", "revision"]

MODE_BY_RESOLUTION_TYPE: dict[str, RequestedMode] = {
    "reconciled_with": "symmetric",
    "supersedes": "directional",
    "revises": "revision",
}
"""The mode `reconcile`'s classifier assigns to each `RESOLUTION_RELATION_
TYPES` member (design Decision 2). Keyed by that shared constant rather than
hand-listed a second time, so a type added to one and not the other becomes
`test_mode_and_role_tables_cover_every_resolution_type`'s failing assertion
instead of a `KeyError` at classify time."""

DIRECTED_ROLES: dict[str, tuple[ReconcileRole, ReconcileRole]] = {
    "supersedes": ("supersedes", "superseded"),
    "revises": ("revises", "revised"),
}
"""The (holder role, target role) pair for each DIRECTED resolution type --
`reconciled_with` has no entry here, since a symmetric edge has no holder
(design Decision 4)."""


def _existing_reconciliation_state(
    *,
    relations_a: list[okf.Relation],
    relations_b: list[okf.Relation],
    canonical_a: str,
    canonical_b: str,
) -> tuple[ResolutionMode, str | None]:
    """Classify the pair's EXISTING reconciliation state from
    already-loaded (pre-mutation) relations -- the CRITICAL refuse-on-conflict
    gate (fix: a mode-switch re-run must never add a second, contradictory
    reconciliation resolution). One table-driven pass (design Decision 2)
    collects the set of `(mode, holder)` pairs any `RESOLUTION_RELATION_TYPES`
    edge between `{a, b}` implies -- a symmetric `reconciled_with` always
    contributes `(symmetric, None)` regardless of which side holds it (so a
    ONE-SIDED `reconciled_with` still classifies as `symmetric`, not
    `mixed`), while `supersedes`/`revises` contribute `(mode, <holder>)`.

    Returns `("none", None)` when the pair carries no prior reconciliation,
    the single collected `(mode, holder)` when exactly one kind of edge (in
    at most one direction) is present, or `("mixed", None)` when the pair
    carries more than one -- disagreeing resolutions only a hand edit can
    produce, which this classifier refuses to rank by precedence."""
    found: set[tuple[RequestedMode, str | None]] = set()
    for relation in relations_a:
        if relation.target == canonical_b and relation.type in MODE_BY_RESOLUTION_TYPE:
            mode = MODE_BY_RESOLUTION_TYPE[relation.type]
            found.add((mode, None if mode == "symmetric" else canonical_a))
    for relation in relations_b:
        if relation.target == canonical_a and relation.type in MODE_BY_RESOLUTION_TYPE:
            mode = MODE_BY_RESOLUTION_TYPE[relation.type]
            found.add((mode, None if mode == "symmetric" else canonical_b))

    if not found:
        return "none", None
    if len(found) == 1:
        (mode, holder) = next(iter(found))
        return mode, holder
    return "mixed", None


def _reconciliation_state_description(mode: ResolutionMode, holder: str | None) -> str:
    """Human-readable description of an existing reconciliation state, for
    the refuse-on-conflict error message."""
    if mode == "directional":
        return f"a directional reconciliation ({holder!r} supersedes its counterpart)"
    if mode == "revision":
        return f"a revision ({holder!r} revises its counterpart; both remain current)"
    if mode == "mixed":
        return (
            "conflicting resolutions (more than one 'supersedes', 'revises' "
            "or 'reconciled_with' edge between the pair, and they disagree)"
        )
    return "a symmetric reconciliation ('reconciled_with')"


def resolve_pair_member(
    layout: config.WorkspaceLayout,
    flag: str,
    value: str,
    canonical_a: str,
    canonical_b: str,
) -> tuple[str, str]:
    """Resolve `value` (an id passed to `flag`, e.g. `--winner` or
    `--revision`) to `(holder, counterpart)`, where `holder` is EXACTLY one
    of `canonical_a`/`canonical_b` (design Decision 5 step 3). `value` is
    resolved via `application_lifecycle.resolve_concept_path` first -- an
    absolute id, a `..` segment, a reserved basename, or a nonexistent
    concept refuses there, byte-identical to how `--winner` already refused
    before this helper existed. Only once `value` resolves to a REAL concept
    that is not a pair member does this raise its own message, shared by
    both flags so their validation cannot drift apart by copy-paste."""
    _, resolved = application_lifecycle.resolve_concept_path(layout.bundle_dir, value)
    if resolved == canonical_a:
        return canonical_a, canonical_b
    if resolved == canonical_b:
        return canonical_b, canonical_a
    raise ValueError(
        f"{flag} {value!r} must resolve to one of the pair "
        f"({canonical_a!r}, {canonical_b!r}), got {resolved!r}"
    )


@dataclass(frozen=True)
class PairRequest:
    """One resolved pair a reconciliation will be recorded on."""

    path_a: Path
    canonical_a: str
    path_b: Path
    canonical_b: str
    holder_canonical: str | None = None
    """`None` means a SYMMETRIC request (`edge_type` is then ignored);
    otherwise the pair member that gets the outbound edge."""
    target_canonical: str | None = None
    """The holder's counterpart; `None` exactly when `holder_canonical` is."""
    edge_type: Literal["supersedes", "revises"] = "supersedes"
    """Which directed resolution (design Decision 4)."""


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class ReconcilePorts:
    """The effects the service sequences but does not own."""

    autocommit: Callable[[Path, Sequence[str], str], object]
    """Best-effort commit of the listed workspace-relative paths; must never
    raise for a git failure (it degrades to a warning)."""

    snapshot_read: Callable[[Path], tuple[bytes, str]] = fsio.snapshot_read
    """ONE observation of a file: its bytes (the drift baseline) and decoded
    text (the plan's input), taken together (#318)."""

    clock: Callable[[], datetime] = _utc_now

    commit_section: CommitSection = contextlib.nullcontext
    """Entered around the commit phase -- the drift re-validation, the writes
    and the auto-commit -- and nothing before it: Phase A and the confirmation
    prompt hold no workspace lock (#1137, ADR-0036). An adapter that owns the
    lock passes a section that takes it; the default holds nothing."""


@dataclass(frozen=True)
class ReconcilePreview:
    """Everything the adapter needs to show one pair's proposed changes,
    before anything is written. Data, not text: the adapter owns the wording."""

    pair: PairRequest
    edge_added_a: bool
    edge_added_b: bool
    note_added_a: bool
    note_added_b: bool
    status_outcome: okf.ExportOutcome | None
    """The deprecated-status export outcome on the superseded side (#1075),
    or `None` when this run exports nothing."""
    target_is_a: bool
    """Which side `status_outcome` is about."""
    log_name: str


@dataclass(frozen=True)
class ReconcileWritten:
    """What a finished pair wrote, for the closing line."""

    pair: PairRequest
    log_name: str


@dataclass(frozen=True)
class ReconcileOutcome:
    """What one pair's reconciliation did."""

    changed: bool
    """Whether a concept document CHANGED (#655): an edge added or a note
    appended on either side. `False` is the idempotent no-change re-run,
    which writes only the log entry -- `log.md` is a catalog file no derived
    index reads, so a caller's write-time refresh keys on this signal."""


class ReconcileObserver:
    """Receives what a run has to say while it runs. Every method is a no-op,
    so an unattended caller passes nothing and gets silence; an adapter
    overrides the ones it renders."""

    def proposed(self, preview: ReconcilePreview) -> None:
        """One pair's proposed changes, shown before the confirmation."""

    def written(self, written: ReconcileWritten) -> None:
        """Phase B finished, before the commit."""


NOT_A_TTY_REFUSAL = (
    "openkos reconcile: refusing to write without confirmation -- "
    "stdin is not a TTY; re-run with --auto."
)
CONFIRM_PROMPT = "Proceed with these changes?"


def check_workspace(root: Path) -> None:
    """Refuse unless `root` is an OpenKOS workspace."""
    workspace_reason = config.require_workspace(root)
    if workspace_reason is not None:
        raise Refused(
            f"openkos reconcile: refusing to reconcile -- {workspace_reason}."
        )


def validate_request(
    *,
    id_a: str | None,
    id_b: str | None,
    winner: str | None,
    revision: str | None,
    from_findings: bool,
    auto: bool,
) -> None:
    """The argument-shape gates, before any concept is resolved. Raises
    `Refused` carrying the complete text."""
    try:
        if from_findings:
            if (
                id_a is not None
                or id_b is not None
                or winner is not None
                or revision is not None
                or auto
            ):
                raise ValueError(
                    "--from-findings takes no concept ids, no --winner, no "
                    "--revision, and no --auto; use the two-id form for a "
                    "directional, revision, or unattended reconciliation"
                )
        elif winner is not None and revision is not None:
            raise ValueError(
                "--winner and --revision are mutually exclusive: a "
                "reconciliation is either a reversal (--winner) or a "
                "refinement (--revision), never both"
            )
        elif id_a is None or id_b is None:
            raise ValueError(
                "two concept ids are required (or pass --from-findings to "
                "walk the persisted open findings)"
            )
    except ValueError as exc:
        raise Refused(f"openkos reconcile: refusing to reconcile -- {exc}.") from exc


def resolve_pair(
    layout: config.WorkspaceLayout,
    id_a: str,
    id_b: str,
    *,
    winner: str | None = None,
    revision: str | None = None,
) -> PairRequest:
    """Resolve the two ids (and `--winner`/`--revision`) to a `PairRequest`:
    every id through `lifecycle.resolve_concept_path` (an absolute id, a `..`
    segment, a reserved basename or a nonexistent concept refuses), to
    DISTINCT files -- a hardlink alias or a case-insensitive second spelling
    of one file counts as the same file -- and the holder to exactly one of
    the pair."""
    try:
        path_a, canonical_a = application_lifecycle.resolve_concept_path(
            layout.bundle_dir, id_a
        )
        path_b, canonical_b = application_lifecycle.resolve_concept_path(
            layout.bundle_dir, id_b
        )
        if canonical_a == canonical_b:
            raise ValueError(
                f"id_a and id_b must be distinct, both resolved to {canonical_a!r}"
            )
        # Distinct STRINGS are not distinct FILES (#324): on a
        # case-insensitive filesystem (macOS default) `foo` and `Foo` are
        # two canonical ids for ONE file -- and a symlink aliases one under
        # any name on any filesystem. The drift guard cannot catch this
        # either: both keys snapshot the same identical bytes (no drift),
        # and Phase B's second `write_atomic` over the same inode then
        # silently discards the first document's edge and note. `samefile`
        # compares device+inode -- after `resolve_concept_path` proved both
        # exist, so error precedence is preserved -- and is naturally False
        # for genuinely distinct files on case-sensitive hosts. The string
        # check above stays: it is cheap and gives the literal self-pair its
        # clearer message.
        if path_a.samefile(path_b):
            raise ValueError(
                f"id_a and id_b must be distinct, {canonical_a!r} and "
                f"{canonical_b!r} resolve to the same file on this filesystem"
            )

        holder_canonical: str | None = None
        target_canonical: str | None = None
        edge_type: Literal["supersedes", "revises"] = "supersedes"
        if winner is not None:
            holder_canonical, target_canonical = resolve_pair_member(
                layout, "--winner", winner, canonical_a, canonical_b
            )
        elif revision is not None:
            holder_canonical, target_canonical = resolve_pair_member(
                layout, "--revision", revision, canonical_a, canonical_b
            )
            edge_type = "revises"
    except (OSError, ValueError) as exc:
        raise Refused(f"openkos reconcile: refusing to reconcile -- {exc}.") from exc
    return PairRequest(
        path_a=path_a,
        canonical_a=canonical_a,
        path_b=path_b,
        canonical_b=canonical_b,
        holder_canonical=holder_canonical,
        target_canonical=target_canonical,
        edge_type=edge_type,
    )


def read_workspace_config(root: Path) -> config.Config:
    """The workspace config, or the `Refused` the two-id form has always
    raised for an unreadable one."""
    try:
        return config.read_config(root)
    except (OSError, ValueError) as exc:
        raise Refused(
            f"openkos reconcile: failed while preparing the reconcile -- {exc}."
        ) from exc


def reconcile_concepts(
    root: Path,
    id_a: str | None,
    id_b: str | None,
    *,
    winner: str | None = None,
    revision: str | None = None,
    auto: bool = False,
    ports: ReconcilePorts,
    observer: ReconcileObserver | None = None,
    confirm: ConfirmCallback | None = None,
) -> ReconcileOutcome:
    """The two-id form: record a reconciliation between `id_a` and `id_b` in
    the workspace at `root` -- symmetric by default, directed by `winner`
    (`supersedes`) or `revision` (`revises`). The workspace gate, request
    validation, pair resolution and the config read are each a `Refused`
    carrying the exact text the CLI has always printed; `reconcile_pair`
    documents the transaction."""
    check_workspace(root)
    validate_request(
        id_a=id_a,
        id_b=id_b,
        winner=winner,
        revision=revision,
        from_findings=False,
        auto=auto,
    )
    if id_a is None or id_b is None:  # pragma: no cover -- validated above
        raise Refused("openkos reconcile: refusing to reconcile -- two ids needed.")
    layout = config.WorkspaceLayout(root)
    pair = resolve_pair(layout, id_a, id_b, winner=winner, revision=revision)
    cfg = read_workspace_config(root)
    return reconcile_pair(
        root,
        cfg,
        pair,
        auto=auto,
        ports=ports,
        observer=observer,
        confirm=confirm,
    )


@dataclass(frozen=True)
class _PreparedPair:
    """Phase A's complete result for one pair."""

    bytes_a: bytes
    bytes_b: bytes
    log_bytes: bytes
    new_text_a: str
    new_text_b: str
    new_log_text: str
    preview: ReconcilePreview
    changed: bool


def _prepare_pair(
    layout: config.WorkspaceLayout,
    pair: PairRequest,
    ports: ReconcilePorts,
) -> _PreparedPair:
    """Phase A: no writes. Builds every byte Phase B will write, in memory,
    from one observation of each input."""
    path_a, canonical_a = pair.path_a, pair.canonical_a
    path_b, canonical_b = pair.path_b, pair.canonical_b
    holder_canonical = pair.holder_canonical
    target_canonical = pair.target_canonical
    edge_type = pair.edge_type
    log_path = layout.bundle_dir / "log.md"

    now = ports.clock()
    today = now.astimezone().date()
    date_str = today.isoformat()

    try:
        # One `snapshot_read` observation per target: the decoded text
        # feeds the parsers below, the raw bytes feed
        # the drift guard (issues #306, #313, #318).
        bytes_a, text_a = ports.snapshot_read(path_a)
        bytes_b, text_b = ports.snapshot_read(path_b)
        log_bytes, log_text = ports.snapshot_read(log_path)

        metadata_a, body_a = okf.load_frontmatter(text_a)
        metadata_b, body_b = okf.load_frontmatter(text_b)
        relations_a = okf.decode_relations(metadata_a)
        relations_b = okf.decode_relations(metadata_b)

        # CRITICAL refuse-on-conflict gate (before ANY edge is computed or
        # written): a pair may carry AT MOST ONE reconciliation resolution
        # written by `reconcile`. Compare the pair's EXISTING state to the
        # one requested by THIS invocation -- an unrelated (`"none"`) prior
        # state proceeds as a fresh write, an IDENTICAL prior state falls
        # through to the ordinary idempotent no-op path below, but a
        # DIFFERENT prior state (mode switch, or opposite `--winner`) is
        # refused here, with zero writes -- this is what prevents a 2nd
        # `supersedes` edge from coexisting with a stale `reconciled_with`
        # edge (or a 2nd, opposite-direction `supersedes` edge), and
        # prevents the `## Reconciliation` note from going stale relative
        # to frontmatter (the note-append gate below is anchor-keyed on
        # `target` alone and blind to `role`, so it cannot itself repair a
        # mismatched note on a later run).
        existing_mode, existing_holder = _existing_reconciliation_state(
            relations_a=relations_a,
            relations_b=relations_b,
            canonical_a=canonical_a,
            canonical_b=canonical_b,
        )
        requested_mode: RequestedMode = (
            MODE_BY_RESOLUTION_TYPE[edge_type]
            if holder_canonical is not None
            else "symmetric"
        )
        if existing_mode != "none" and (
            existing_mode != requested_mode or existing_holder != holder_canonical
        ):
            description = _reconciliation_state_description(
                existing_mode, existing_holder
            )
            raise ValueError(
                f"concepts {canonical_a!r} and {canonical_b!r} are already "
                f"reconciled as {description}; reconcile will not overwrite "
                "an existing resolution. To change it, edit the concepts "
                "manually or revert with git, then re-run"
            )

        edge_added_a = False
        edge_added_b = False
        role_a: ReconcileRole
        role_b: ReconcileRole
        if holder_canonical is None:
            relations_a, edge_added_a = _add_relation_if_absent(
                relations_a, okf.Relation(target=canonical_b, type="reconciled_with")
            )
            relations_b, edge_added_b = _add_relation_if_absent(
                relations_b, okf.Relation(target=canonical_a, type="reconciled_with")
            )
            role_a, role_b = "reconciled", "reconciled"
        else:
            holder_role, target_role = DIRECTED_ROLES[edge_type]
            if holder_canonical == canonical_a:
                relations_a, edge_added_a = _add_relation_if_absent(
                    relations_a, okf.Relation(target=canonical_b, type=edge_type)
                )
                role_a, role_b = holder_role, target_role
            else:
                relations_b, edge_added_b = _add_relation_if_absent(
                    relations_b, okf.Relation(target=canonical_a, type=edge_type)
                )
                role_a, role_b = target_role, holder_role

        note_added_a = False
        if not _reconcile_anchor_present(body_a, canonical_b):
            body_a = _append_reconciliation_note(
                body_a,
                _reconciliation_note(
                    counterpart_id=canonical_b, role=role_a, date_str=date_str
                ),
            )
            note_added_a = True

        note_added_b = False
        if not _reconcile_anchor_present(body_b, canonical_a):
            body_b = _append_reconciliation_note(
                body_b,
                _reconciliation_note(
                    counterpart_id=canonical_a, role=role_b, date_str=date_str
                ),
            )
            note_added_b = True

        # deprecated-status-export (issue #1075, design Decision 5): a
        # directed `supersedes` edge that was just ADDED (never on a
        # symmetric/`revises` reconcile, and never on an idempotent
        # no-edge re-run) exports the counterpart's status in this SAME
        # Phase B write. `edge_type == "revises"` and the idempotent case
        # both leave `status_outcome` `None`, writing nothing.
        status_outcome: okf.ExportOutcome | None = None
        target_is_a = False
        if holder_canonical is not None and edge_type == "supersedes":
            edge_added = (
                edge_added_a if holder_canonical == canonical_a else edge_added_b
            )
            if edge_added:
                target_is_a = holder_canonical != canonical_a
                if target_is_a:
                    decision = okf.project_deprecation_export(
                        metadata_a, superseded=True
                    )
                    metadata_a = decision.metadata
                else:
                    decision = okf.project_deprecation_export(
                        metadata_b, superseded=True
                    )
                    metadata_b = decision.metadata
                status_outcome = decision.outcome

        metadata_a[okf.RELATIONS_KEY] = okf.encode_relations(relations_a)
        metadata_b[okf.RELATIONS_KEY] = okf.encode_relations(relations_b)
        new_text_a = okf.dump_frontmatter(metadata_a, body_a)
        new_text_b = okf.dump_frontmatter(metadata_b, body_b)

        changed = edge_added_a or edge_added_b or note_added_a or note_added_b
        if not changed:
            log_line = (
                f"**Reconcile**: [{canonical_a}](/{canonical_a}.md) and "
                f"[{canonical_b}](/{canonical_b}.md) are already reconciled; "
                "no change."
            )
        elif holder_canonical is None:
            log_line = (
                "**Reconcile**: Recorded a symmetric 'reconciled_with' "
                f"between [{canonical_a}](/{canonical_a}.md) and "
                f"[{canonical_b}](/{canonical_b}.md)."
            )
        elif edge_type == "supersedes":
            log_line = (
                f"**Reconcile**: [{holder_canonical}](/{holder_canonical}.md) "
                f"supersedes [{target_canonical}](/{target_canonical}.md) "
                "(recorded 'supersedes')."
            )
        else:
            log_line = (
                f"**Reconcile**: [{holder_canonical}](/{holder_canonical}.md) "
                f"revises [{target_canonical}](/{target_canonical}.md) "
                "(recorded 'revises'; both remain current)."
            )
        new_log_text = bundle_log.insert_log_entry(log_text, today, log_line)
    except (OSError, ValueError) as exc:
        raise Refused(
            f"openkos reconcile: failed while preparing the reconcile -- {exc}."
        ) from exc

    return _PreparedPair(
        bytes_a=bytes_a,
        bytes_b=bytes_b,
        log_bytes=log_bytes,
        new_text_a=new_text_a,
        new_text_b=new_text_b,
        new_log_text=new_log_text,
        preview=ReconcilePreview(
            pair=pair,
            edge_added_a=edge_added_a,
            edge_added_b=edge_added_b,
            note_added_a=note_added_a,
            note_added_b=note_added_b,
            status_outcome=status_outcome,
            target_is_a=target_is_a,
            log_name=log_path.name,
        ),
        changed=changed,
    )


def reconcile_pair(
    root: Path,
    cfg: config.Config,
    pair: PairRequest,
    *,
    auto: bool,
    announce_preview: bool = True,
    ports: ReconcilePorts,
    observer: ReconcileObserver | None = None,
    confirm: ConfirmCallback | None = None,
) -> ReconcileOutcome:
    """One pair's complete reconcile transaction -- Phase A in-memory build,
    conflict gate, preview, confirmation, drift re-validation, Phase B
    additive writes, and auto-commit -- so the two-id form and the CLI's
    `--from-findings` walks (#567) share the SAME write path instead of a
    second implementation. `announce_preview=False` suppresses only the
    `proposed` notification (the batch walk collects consent from the finding
    context before calling); every gate below still runs. A `Refused` is
    raised for a prepare/conflict/write failure and `DriftDetected` for
    post-consent target drift.

    `pair.holder_canonical is None` means a SYMMETRIC request (`edge_type` is
    then ignored); otherwise `holder_canonical` is the pair member that gets
    the outbound edge and `target_canonical` its counterpart, with
    `edge_type` naming which directed resolution (`"supersedes"` or
    `"revises"`, design Decision 4).

    The outcome's `changed` says whether this run CHANGED a concept document
    (#655): an edge added or a note appended on either side. `False` is the
    idempotent no-change re-run, which writes only the log entry."""
    obs = observer if observer is not None else ReconcileObserver()
    layout = config.WorkspaceLayout(root)
    log_path = layout.bundle_dir / "log.md"
    canonical_a, canonical_b = pair.canonical_a, pair.canonical_b
    holder_canonical = pair.holder_canonical
    target_canonical = pair.target_canonical
    edge_type = pair.edge_type

    prepared = _prepare_pair(layout, pair, ports)

    if announce_preview:
        obs.proposed(prepared.preview)

    if not auto and cfg.review:
        require_confirmation(confirm, CONFIRM_PROMPT, NOT_A_TTY_REFUSAL)

    with ports.commit_section():
        # Issue #313: every byte below was computed from a pre-prompt read, so
        # re-validate each target now -- after the gate, before the first write --
        # and unconditionally, because `--auto` and `review: false` skip the
        # prompt but not the window it stood in.
        drift = application_drift.describe_drift(
            layout,
            {
                pair.path_a: prepared.bytes_a,
                pair.path_b: prepared.bytes_b,
                log_path: prepared.log_bytes,
            },
            "reconcile",
        )
        if drift is not None:
            raise DriftDetected(drift)

        try:
            fsio.write_atomic(pair.path_a, prepared.new_text_a)
            fsio.write_atomic(pair.path_b, prepared.new_text_b)
            fsio.write_atomic(log_path, prepared.new_log_text)
        except (OSError, ValueError) as exc:
            raise Refused(
                f"openkos reconcile: failed while writing the reconcile -- {exc}."
            ) from exc

        obs.written(ReconcileWritten(pair=pair, log_name=log_path.name))

        if holder_canonical is None:
            reconcile_message = f"openkos: reconcile {canonical_a} <-> {canonical_b}"
        elif edge_type == "supersedes":
            reconcile_message = (
                f"openkos: reconcile {holder_canonical} supersedes {target_canonical}"
            )
        else:
            reconcile_message = (
                f"openkos: reconcile {holder_canonical} revises {target_canonical}"
            )
        ports.autocommit(
            root,
            [f"bundle/{canonical_a}.md", f"bundle/{canonical_b}.md", "bundle/log.md"],
            reconcile_message,
        )

    return ReconcileOutcome(changed=prepared.changed)
