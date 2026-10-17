"""Undoing a review choice from a stateless client (ADR-0067).

Keep separate, a connection and a how-quickly setting are each undone by a
preview and an apply keyed by what the choice wrote, never by its decision key:
the member set, the edge, the type. A preview plans from the bytes on disk and
returns a content digest; the apply takes the workspace lock once, plans again
from the bytes it will write over, and writes only when the digest is the one
the caller previewed. One write, one commit. No queue row is resolved or
proposed, no derived store is refreshed and no model is reached.
"""

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from openkos import config, fsio
from openkos.application import (
    contradictions_service,
    curate_decisions,
    duplicates_service,
    reconcile_service,
)
from openkos.application import lifecycle as application_lifecycle
from openkos.application import pending as application_pending
from openkos.application.write_gate import Refused, WriteFailed
from openkos.bundle import decisions as bundle_decisions
from openkos.bundle import log as bundle_log
from openkos.model import okf, relations, types

UndoReason = Literal[
    "not_kept_distinct",
    "not_related",
    "tier_unchanged",
    "unknown_type",
    "not_declined",
    "not_reconciled",
]


@dataclass(frozen=True)
class UndoRefusal:
    """Why the undo cannot be planned as asked; nothing was written."""

    reason: UndoReason


@dataclass(frozen=True)
class UndoStale:
    """The bytes the plan rests on changed since the preview, or the echoed key
    was altered; nothing was written."""


def _digest(lines: Sequence[str]) -> str:
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _file_digest(path: Path) -> str:
    """The bytes' sha256, or `-` for a file that cannot be read."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return "-"


# -- keep separate --------------------------------------------------------- #


@dataclass(frozen=True)
class KeptDistinctRuling:
    """A member set a person ruled distinct and has not reopened."""

    member_ids: tuple[str, ...]
    decided_at: str


def _decided_time(stamp: str) -> datetime:
    """`stamp` as an aware time for ordering; unparseable sorts last."""
    try:
        parsed = datetime.fromisoformat(stamp)
    except ValueError:
        return datetime.min.replace(tzinfo=UTC)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def kept_distinct(root: Path) -> tuple[KeptDistinctRuling, ...]:
    """Every declined identity ruling, newest first, then by member set. Reads
    the decisions sidecars only."""
    records = duplicates_service.list_kept_distinct(root)
    by_members = sorted(records, key=lambda record: record.member_ids)
    return tuple(
        KeptDistinctRuling(member_ids=record.member_ids, decided_at=record.decided_at)
        for record in sorted(
            by_members,
            key=lambda record: _decided_time(record.decided_at),
            reverse=True,
        )
    )


@dataclass(frozen=True)
class ReopenPlan:
    """Reopening the ruling over `member_ids` (sorted)."""

    member_ids: tuple[str, ...]
    content_digest: str


def _members(raw: Sequence[str]) -> tuple[str, ...] | None:
    """`raw` canonicalised, deduplicated and sorted, or `None` when an id is
    not a valid Concept ID or fewer than two distinct members remain."""
    try:
        members = tuple(
            sorted({application_lifecycle.canonicalize_concept_id(m) for m in raw})
        )
    except ValueError:
        return None
    return members if len(members) >= 2 else None


def preview_reopen(root: Path, member_ids: Sequence[str]) -> ReopenPlan | UndoRefusal:
    """Plan reopening the declined ruling over exactly `member_ids`.

    The digest covers the member set and the bytes of the sidecar that holds
    the ruling. Writes nothing and takes no lock."""
    layout = config.WorkspaceLayout(root)
    members = _members(member_ids)
    if members is None or not application_pending.is_group_kept_distinct(
        layout, members
    ):
        return UndoRefusal("not_kept_distinct")
    sidecar = bundle_decisions.decisions_path_for(members[0], layout.bundle_dir)
    lines = [f"member\t{member}" for member in members]
    lines.append(f"sidecar\t{_file_digest(sidecar)}")
    return ReopenPlan(member_ids=members, content_digest=_digest(lines))


@dataclass(frozen=True)
class Reopened:
    """The ruling was reopened. `commit_sha` is `None` when the commit could
    not be made (the write itself landed)."""

    member_ids: tuple[str, ...]
    commit_sha: str | None


def apply_reopen(
    root: Path,
    member_ids: Sequence[str],
    content_digest: str,
    *,
    ports: curate_decisions.DecisionPorts,
) -> Reopened | UndoRefusal | UndoStale:
    """Reopen the ruling previewed as `content_digest`, under one lock.

    Plans again inside the commit section; a refusal or a moved digest writes
    nothing. Otherwise the ruling is rewritten with state `open` and the one
    sidecar it lives in is committed."""
    layout = config.WorkspaceLayout(root)
    with ports.commit_section():
        plan = preview_reopen(root, member_ids)
        if isinstance(plan, UndoRefusal):
            return plan
        if plan.content_digest != content_digest:
            return UndoStale()
        try:
            rel_path = duplicates_service.apply_identity_decision(
                layout, plan.member_ids, target_state="open"
            )
        except (OSError, ValueError) as exc:
            raise WriteFailed("reopen", plan.member_ids, str(exc)) from exc
        commit_sha = ports.autocommit(
            root,
            [rel_path],
            f"openkos: reopen identity {'/'.join(plan.member_ids)}",
        )
    return Reopened(member_ids=plan.member_ids, commit_sha=commit_sha)


# -- a declined contradiction ------------------------------------------------- #


@dataclass(frozen=True)
class DeclinedContradictionRecord:
    """A contradiction a person declined and has not reopened."""

    first_id: str
    second_id: str
    merged_absorbed_id: str | None
    decided_at: str


def declined_contradictions(root: Path) -> tuple[DeclinedContradictionRecord, ...]:
    """Every declined contradiction record, newest first, then by pair and
    discriminator. Walks the decisions sidecars as `contradictions --declined`
    does, without its findings join, so no database is opened."""
    bundle_dir = config.WorkspaceLayout(root).bundle_dir
    records: list[DeclinedContradictionRecord] = []
    for path in bundle_decisions.iter_decisions(bundle_dir):
        # Read the WALKED path, never one rebuilt from the sidecar's content.
        for record in bundle_decisions.read_decisions_at(path):
            if record.state == "declined":
                records.append(
                    DeclinedContradictionRecord(
                        first_id=record.pair_ids[0],
                        second_id=record.pair_ids[1],
                        merged_absorbed_id=record.merged_absorbed_id,
                        decided_at=record.decided_at,
                    )
                )
    by_key = sorted(
        records,
        key=lambda r: (r.first_id, r.second_id, r.merged_absorbed_id or ""),
    )
    return tuple(
        sorted(by_key, key=lambda r: _decided_time(r.decided_at), reverse=True)
    )


@dataclass(frozen=True)
class ContradictionReopenPlan:
    """Reopening the declined contradiction over the sorted pair and the
    merged-body discriminator."""

    first_id: str
    second_id: str
    merged_absorbed_id: str | None
    content_digest: str


def _finding_identity(
    first_id: str, second_id: str, merged_absorbed_id: str | None
) -> tuple[tuple[str, str], str | None] | None:
    """The sorted, canonical pair and discriminator, or `None` when an id is not
    a valid Concept ID."""
    try:
        first, second = sorted(
            application_lifecycle.canonicalize_concept_id(concept_id)
            for concept_id in (first_id, second_id)
        )
        merged = (
            None
            if merged_absorbed_id is None
            else application_lifecycle.canonicalize_concept_id(merged_absorbed_id)
        )
    except ValueError:
        return None
    return (first, second), merged


def preview_reopen_contradiction(
    root: Path, first_id: str, second_id: str, merged_absorbed_id: str | None
) -> ContradictionReopenPlan | UndoRefusal:
    """Plan reopening the declined contradiction over exactly this pair (either
    order) and discriminator.

    The digest covers the pair, the discriminator and the bytes of the sidecar
    holding the record. Writes nothing and takes no lock."""
    layout = config.WorkspaceLayout(root)
    identity = _finding_identity(first_id, second_id, merged_absorbed_id)
    if identity is None:
        return UndoRefusal("not_declined")
    pair, merged = identity
    if not application_pending.is_contradiction_declined(layout, pair, merged):
        return UndoRefusal("not_declined")
    sidecar = bundle_decisions.decisions_path_for(pair[0], layout.bundle_dir)
    lines = [
        f"pair\t{pair[0]}\t{pair[1]}",
        f"merged\t{'-' if merged is None else merged}",
        f"sidecar\t{_file_digest(sidecar)}",
    ]
    return ContradictionReopenPlan(
        first_id=pair[0],
        second_id=pair[1],
        merged_absorbed_id=merged,
        content_digest=_digest(lines),
    )


@dataclass(frozen=True)
class ContradictionReopened:
    """The decline was reopened. `commit_sha` is `None` when the commit could
    not be made (the write itself landed)."""

    first_id: str
    second_id: str
    merged_absorbed_id: str | None
    commit_sha: str | None


def apply_reopen_contradiction(
    root: Path,
    first_id: str,
    second_id: str,
    merged_absorbed_id: str | None,
    content_digest: str,
    *,
    ports: curate_decisions.DecisionPorts,
) -> ContradictionReopened | UndoRefusal | UndoStale:
    """Reopen the decline previewed as `content_digest`, under one lock.

    Plans again inside the commit section; a refusal or a moved digest writes
    nothing. Otherwise the record is rewritten with state `open`, as
    `contradictions --reopen` writes it, and its one sidecar is committed."""
    layout = config.WorkspaceLayout(root)
    with ports.commit_section():
        plan = preview_reopen_contradiction(
            root, first_id, second_id, merged_absorbed_id
        )
        if isinstance(plan, UndoRefusal):
            return plan
        if plan.content_digest != content_digest:
            return UndoStale()
        pair = (plan.first_id, plan.second_id)
        try:
            rel_path = contradictions_service.apply_contradiction_decision(
                layout, pair, plan.merged_absorbed_id, target_state="open"
            )
        except (OSError, ValueError) as exc:
            raise WriteFailed("reopen", pair, str(exc)) from exc
        suffix = (
            "" if plan.merged_absorbed_id is None else f" ({plan.merged_absorbed_id})"
        )
        commit_sha = ports.autocommit(
            root,
            [rel_path],
            f"openkos: reopen contradiction {pair[0]} <-> {pair[1]}{suffix}",
        )
    return ContradictionReopened(
        first_id=plan.first_id,
        second_id=plan.second_id,
        merged_absorbed_id=plan.merged_absorbed_id,
        commit_sha=commit_sha,
    )


# -- a connection ---------------------------------------------------------- #


@dataclass(frozen=True)
class UnrelatePlan:
    """Removing the `relation_type` relation from `source_id` to `target_id`.
    `withdraws_deprecation`: the target's deprecated-status export is withdrawn
    too, because nothing else supersedes it."""

    source_id: str
    relation_type: str
    target_id: str
    withdraws_deprecation: bool
    content_digest: str


@dataclass(frozen=True)
class _Unrelating:
    plan: UnrelatePlan
    prepared: application_lifecycle.PreparedUnrelate
    source_path: Path
    target_path: Path


def _plan_unrelate(
    root: Path, source_id: str, relation_type: str, target_id: str
) -> _Unrelating | UndoRefusal:
    """The unrelate plan from the bytes on disk now, as `unrelate` makes it."""
    layout = config.WorkspaceLayout(root)
    try:
        source_path, source = application_lifecycle.resolve_concept_path(
            layout.bundle_dir, source_id
        )
        target_path, target = application_lifecycle.resolve_concept_path(
            layout.bundle_dir, target_id
        )
        rel_type = relations.validate_relation_type(relation_type)
    except ValueError:
        return UndoRefusal("not_related")
    if source == target:
        return UndoRefusal("not_related")
    try:
        prepared = application_lifecycle.prepare_unrelate(
            source_path,
            layout.bundle_dir / "log.md",
            source,
            target,
            rel_type,
            root,
            now=datetime.now(UTC),
            target_path=target_path,
        )
    except ValueError:
        return UndoRefusal("not_related")
    except OSError as exc:
        raise WriteFailed("unrelate", (source, target), str(exc)) from exc
    withdraws = prepared.new_target_text is not None
    lines = [
        f"edge\t{source}\t{rel_type}\t{target}",
        f"source\t{hashlib.sha256(prepared.source_bytes).hexdigest()}",
    ]
    if prepared.target_bytes is not None:
        lines.append(f"target\t{hashlib.sha256(prepared.target_bytes).hexdigest()}")
    plan = UnrelatePlan(
        source_id=source,
        relation_type=rel_type,
        target_id=target,
        withdraws_deprecation=withdraws,
        content_digest=_digest(lines),
    )
    return _Unrelating(plan, prepared, source_path, target_path)


def preview_unrelate(
    root: Path, source_id: str, relation_type: str, target_id: str
) -> UnrelatePlan | UndoRefusal:
    """Plan removing one typed relation, exactly as `unrelate` would.

    The digest covers the edge, the source page's bytes, the target page's
    bytes when its deprecated-status export is withdrawn, and whether it is.
    Writes nothing and takes no lock."""
    planned = _plan_unrelate(root, source_id, relation_type, target_id)
    return planned if isinstance(planned, UndoRefusal) else planned.plan


@dataclass(frozen=True)
class Unrelated:
    """The relation was removed. `commit_sha` is `None` when the commit could
    not be made (the write itself landed)."""

    source_id: str
    relation_type: str
    target_id: str
    commit_sha: str | None


def apply_unrelate(
    root: Path,
    source_id: str,
    relation_type: str,
    target_id: str,
    content_digest: str,
    *,
    ports: curate_decisions.DecisionPorts,
) -> Unrelated | UndoRefusal | UndoStale:
    """Remove the relation previewed as `content_digest`, under one lock.

    Plans again inside the commit section, so the plan and the write see the
    same bytes; a refusal or a moved digest writes nothing. Otherwise the
    source page, the target's withdrawal when planned and the `log.md` line are
    written and committed, as `unrelate` writes them."""
    layout = config.WorkspaceLayout(root)
    with ports.commit_section():
        planned = _plan_unrelate(root, source_id, relation_type, target_id)
        if isinstance(planned, UndoRefusal):
            return planned
        plan = planned.plan
        if plan.content_digest != content_digest:
            return UndoStale()
        try:
            application_lifecycle.unrelate_core(
                planned.source_path,
                layout.bundle_dir / "log.md",
                planned.prepared,
                target_path=planned.target_path,
            )
        except (OSError, ValueError) as exc:
            raise WriteFailed(
                "unrelate", (plan.source_id, plan.target_id), str(exc)
            ) from exc
        paths = [f"bundle/{plan.source_id}.md", "bundle/log.md"]
        if plan.withdraws_deprecation:
            paths.insert(1, f"bundle/{plan.target_id}.md")
        commit_sha = ports.autocommit(
            root,
            paths,
            f"openkos: unrelate {plan.source_id} -> {plan.target_id} "
            f"({plan.relation_type})",
        )
    return Unrelated(
        source_id=plan.source_id,
        relation_type=plan.relation_type,
        target_id=plan.target_id,
        commit_sha=commit_sha,
    )


# -- a reconcile ----------------------------------------------------------------- #

_RESOLUTION_TYPE: dict[str, Literal["reconciled_with", "supersedes", "revises"]] = {
    "symmetric": "reconciled_with",
    "directional": "supersedes",
    "revision": "revises",
}
"""The relation type of each single resolution `reconcile` records."""


@dataclass(frozen=True)
class ReconciledPairRecord:
    """A pair holding one reconcile resolution. `holder_id` and `target_id` are
    `None` for `reconciled_with`, which has no direction."""

    first_id: str
    second_id: str
    relation_type: Literal["reconciled_with", "supersedes", "revises"]
    holder_id: str | None
    target_id: str | None


def _relations_by_page(bundle_dir: Path) -> dict[str, list[okf.Relation]]:
    """Every page's typed relations, keyed by Concept ID. A page whose
    frontmatter or relations cannot be read holds none."""
    pages: dict[str, list[okf.Relation]] = {}
    for path in okf.iter_bundle_markdown(bundle_dir):
        concept_id = okf.concept_id_for(path, bundle_dir)
        try:
            metadata, _ = okf.load_frontmatter(path.read_text(encoding="utf-8"))
            pages[concept_id] = okf.decode_relations(metadata)
        except OSError, ValueError:
            pages[concept_id] = []
    return pages


def _resolution(
    first: str,
    second: str,
    relations_first: list[okf.Relation],
    relations_second: list[okf.Relation],
) -> ReconciledPairRecord | None:
    """The pair's one resolution, classified as `reconcile` classifies it, or
    `None` when it holds none or disagreeing ones."""
    mode, holder = reconcile_service.existing_reconciliation_state(
        relations_a=relations_first,
        relations_b=relations_second,
        canonical_a=first,
        canonical_b=second,
    )
    if mode == "none" or mode == "mixed":
        return None
    target = None if holder is None else (second if holder == first else first)
    return ReconciledPairRecord(
        first_id=first,
        second_id=second,
        relation_type=_RESOLUTION_TYPE[mode],
        holder_id=holder,
        target_id=target,
    )


def reconciled_pairs(root: Path) -> tuple[ReconciledPairRecord, ...]:
    """Every pair of existing pages holding one reconcile resolution, by pair.
    Reads the pages only."""
    pages = _relations_by_page(config.WorkspaceLayout(root).bundle_dir)
    pairs: set[tuple[str, str]] = set()
    for concept_id, page_relations in pages.items():
        for relation in page_relations:
            if (
                relation.type in reconcile_service.MODE_BY_RESOLUTION_TYPE
                and relation.target in pages
                and relation.target != concept_id
            ):
                first, second = sorted((concept_id, relation.target))
                pairs.add((first, second))
    found = (
        _resolution(first, second, pages[first], pages[second])
        for first, second in sorted(pairs)
    )
    return tuple(record for record in found if record is not None)


@dataclass(frozen=True)
class UnreconcilePlan:
    """Undoing the reconcile between `first_id` and `second_id` (sorted): its
    edges, its notes and, for `supersedes`, the out-of-date mark when
    `withdraws_deprecation`. `pages_changed` are the pages whose files change."""

    first_id: str
    second_id: str
    relation_type: Literal["reconciled_with", "supersedes", "revises"]
    holder_id: str | None
    target_id: str | None
    withdraws_deprecation: bool
    pages_changed: tuple[str, ...]
    content_digest: str


@dataclass(frozen=True)
class _Unreconciling:
    plan: UnreconcilePlan
    paths: dict[str, Path]
    new_texts: dict[str, str]
    new_log_text: str


def _resolve_pair(
    layout: config.WorkspaceLayout, first_id: str, second_id: str
) -> dict[str, Path] | None:
    """The two pages by canonical Concept ID, resolved as `reconcile` resolves
    them (two distinct existing files), or `None`."""
    try:
        pair = reconcile_service.resolve_pair(layout, first_id, second_id)
    except Refused:
        return None
    return {pair.canonical_a: pair.path_a, pair.canonical_b: pair.path_b}


def _plan_unreconcile(
    root: Path, first_id: str, second_id: str
) -> _Unreconciling | UndoRefusal:
    """The inverse of the pair's reconcile, from the bytes on disk now."""
    layout = config.WorkspaceLayout(root)
    paths = _resolve_pair(layout, first_id, second_id)
    if paths is None:
        return UndoRefusal("not_reconciled")
    first, second = sorted(paths)
    log_path = layout.bundle_dir / "log.md"
    now = datetime.now(UTC)
    try:
        snapshots = {cid: fsio.snapshot_read(paths[cid]) for cid in (first, second)}
        texts = {cid: text for cid, (_, text) in snapshots.items()}
        page_relations = {
            cid: okf.decode_relations(okf.load_frontmatter(text)[0])
            for cid, text in texts.items()
        }
        resolution = _resolution(
            first, second, page_relations[first], page_relations[second]
        )
        if resolution is None:
            return UndoRefusal("not_reconciled")
        rel_type = resolution.relation_type
        new_texts = dict(texts)
        withdraws = False
        for page, other in ((first, second), (second, first)):
            if not any(
                r.target == other and r.type == rel_type for r in page_relations[page]
            ):
                continue
            prepared = application_lifecycle.prepare_unrelate(
                paths[page],
                log_path,
                page,
                other,
                rel_type,
                root,
                now=now,
                target_path=paths[other],
            )
            new_texts[page] = prepared.new_source_text
            if prepared.new_target_text is not None:
                new_texts[other] = prepared.new_target_text
                withdraws = True
        for page, other in ((first, second), (second, first)):
            metadata, body = okf.load_frontmatter(new_texts[page])
            body, removed = reconcile_service.remove_reconciliation_note(body, other)
            if removed:
                # `reconcile` writes the key on both pages, even with no edge;
                # an emptied list is dropped, as `unrelate` drops it.
                if metadata.get(okf.RELATIONS_KEY) == []:
                    del metadata[okf.RELATIONS_KEY]
                new_texts[page] = okf.dump_frontmatter(metadata, body)
        _, log_text = fsio.snapshot_read(log_path)
    except (OSError, ValueError) as exc:
        raise WriteFailed("unreconcile", (first, second), str(exc)) from exc
    log_line = (
        f"**Unreconcile**: Removed the {rel_type!r} resolution between "
        f"[{first}](/{first}.md) and [{second}](/{second}.md)."
    )
    new_log_text = bundle_log.insert_log_entry(
        log_text, now.astimezone().date(), log_line
    )
    # The resolution is read from the pages, so their bytes cover it.
    lines = [
        *(
            f"page\t{cid}\t{hashlib.sha256(raw).hexdigest()}"
            for cid, (raw, _) in snapshots.items()
        ),
        f"withdraws\t{withdraws}",
    ]
    plan = UnreconcilePlan(
        first_id=first,
        second_id=second,
        relation_type=rel_type,
        holder_id=resolution.holder_id,
        target_id=resolution.target_id,
        withdraws_deprecation=withdraws,
        pages_changed=tuple(
            cid for cid in (first, second) if new_texts[cid] != texts[cid]
        ),
        content_digest=_digest(lines),
    )
    return _Unreconciling(plan, paths, new_texts, new_log_text)


def preview_unreconcile(
    root: Path, first_id: str, second_id: str
) -> UnreconcilePlan | UndoRefusal:
    """Plan undoing the reconcile between two pages, given in either order.

    The digest covers the pair, the resolution, both pages' bytes and whether
    the out-of-date mark is withdrawn. Writes nothing and takes no lock."""
    planned = _plan_unreconcile(root, first_id, second_id)
    return planned if isinstance(planned, UndoRefusal) else planned.plan


@dataclass(frozen=True)
class Unreconciled:
    """The reconcile was undone. `commit_sha` is `None` when the commit could not
    be made (the writes themselves landed)."""

    first_id: str
    second_id: str
    relation_type: Literal["reconciled_with", "supersedes", "revises"]
    commit_sha: str | None


def apply_unreconcile(
    root: Path,
    first_id: str,
    second_id: str,
    content_digest: str,
    *,
    ports: curate_decisions.DecisionPorts,
) -> Unreconciled | UndoRefusal | UndoStale:
    """Undo the reconcile previewed as `content_digest`, under one lock.

    Plans again inside the commit section, so the plan and the write see the
    same bytes; a refusal or a moved digest writes nothing. Otherwise the pages
    that change and a `log.md` line are written and committed together."""
    with ports.commit_section():
        planned = _plan_unreconcile(root, first_id, second_id)
        if isinstance(planned, UndoRefusal):
            return planned
        plan = planned.plan
        if plan.content_digest != content_digest:
            return UndoStale()
        log_path = config.WorkspaceLayout(root).bundle_dir / "log.md"
        try:
            for cid in plan.pages_changed:
                fsio.write_atomic(planned.paths[cid], planned.new_texts[cid])
            fsio.write_atomic(log_path, planned.new_log_text)
        except OSError as exc:
            raise WriteFailed(
                "unreconcile", (plan.first_id, plan.second_id), str(exc)
            ) from exc
        commit_sha = ports.autocommit(
            root,
            [*(f"bundle/{cid}.md" for cid in plan.pages_changed), "bundle/log.md"],
            f"openkos: unreconcile {plan.first_id} <-> {plan.second_id}",
        )
    return Unreconciled(
        first_id=plan.first_id,
        second_id=plan.second_id,
        relation_type=plan.relation_type,
        commit_sha=commit_sha,
    )


# -- how quickly a type goes out of date -------------------------------------- #


@dataclass(frozen=True)
class TierChangePlan:
    """Setting `type_name`'s `type_tiers` entry to `tier`, or removing it when
    `tier` is `None` so the engine's default applies. `configured_tier` is the
    entry now (`None` when there is none): what an undo of this change sets
    back."""

    type_name: str
    tier: str | None
    configured_tier: str | None
    content_digest: str


@dataclass(frozen=True)
class _TierChanging:
    plan: TierChangePlan
    config_bytes: bytes
    new_text: str


def _plan_tier_change(
    root: Path, type_name: str, tier: str | None
) -> _TierChanging | UndoRefusal:
    """The tier change from the bytes of `openkos.yaml` now."""
    if tier is not None and tier not in types.VOLATILITY_TIERS:
        raise ValueError(
            f"{tier!r} is not a volatility tier "
            f"(expected one of {sorted(types.VOLATILITY_TIERS)} or None)"
        )
    if type_name not in {ot.name for ot in types.REGISTRY}:
        return UndoRefusal("unknown_type")
    config_path = config.WorkspaceLayout(root).config_path
    try:
        config_bytes, text = fsio.snapshot_read(config_path)
        configured = config.type_tier_entry(text, type_name)
        new_text = (
            config.clear_type_tier(text, type_name)
            if tier is None
            else config.set_type_tier(text, type_name, tier)
        )
    except (OSError, ValueError) as exc:
        raise WriteFailed("set_tier", (type_name,), str(exc)) from exc
    if configured == tier:
        return UndoRefusal("tier_unchanged")
    lines = [
        f"type\t{type_name}",
        f"tier\t{'-' if tier is None else tier}",
        f"config\t{hashlib.sha256(config_bytes).hexdigest()}",
    ]
    plan = TierChangePlan(
        type_name=type_name,
        tier=tier,
        configured_tier=configured,
        content_digest=_digest(lines),
    )
    return _TierChanging(plan, config_bytes, new_text)


def preview_tier_change(
    root: Path, type_name: str, tier: str | None
) -> TierChangePlan | UndoRefusal:
    """Plan setting `type_name`'s tier, or removing its entry when `tier` is
    `None`, by the same text surgery `set-volatility` uses.

    The digest covers the type, the requested tier and the bytes of
    `openkos.yaml`. A tier outside the vocabulary raises `ValueError`; a shape
    of `openkos.yaml` that cannot be edited raises `WriteFailed`. Writes
    nothing and takes no lock."""
    planned = _plan_tier_change(root, type_name, tier)
    return planned if isinstance(planned, UndoRefusal) else planned.plan


@dataclass(frozen=True)
class TierChanged:
    """The entry was set (`tier`) or removed (`tier` `None`). `commit_sha` is
    `None` when the commit could not be made (the write itself landed)."""

    type_name: str
    tier: str | None
    commit_sha: str | None


_SURFACE = "api"
"""The surface named to the config-commit port, as the decision surface names
itself."""


def apply_tier_change(
    root: Path,
    type_name: str,
    tier: str | None,
    content_digest: str,
    *,
    ports: curate_decisions.DecisionPorts,
) -> TierChanged | UndoRefusal | UndoStale:
    """Set or remove the entry previewed as `content_digest`, under one lock.

    Plans again inside the commit section; a refusal or a moved digest writes
    nothing. An uncommitted edit to the same entry is refused, through the
    config-commit port, as a set-tier decision refuses it (`Refused`).
    Otherwise `openkos.yaml` is written and only this change is committed."""
    config_path = config.WorkspaceLayout(root).config_path
    with ports.commit_section():
        planned = _plan_tier_change(root, type_name, tier)
        if isinstance(planned, UndoRefusal):
            return planned
        plan = planned.plan
        if plan.content_digest != content_digest:
            return UndoStale()
        decision = (
            None
            if ports.plan_config_commit is None
            else ports.plan_config_commit(
                root, _SURFACE, plan.type_name, plan.tier, planned.config_bytes
            )
        )
        if isinstance(decision, curate_decisions.ConfigCommitRefusal):
            raise Refused(decision.message)
        try:
            fsio.write_atomic(config_path, planned.new_text)
        except OSError as exc:
            raise WriteFailed("set_tier", (plan.type_name,), str(exc)) from exc
        message = (
            f"openkos: clear-volatility {plan.type_name}"
            if plan.tier is None
            else f"openkos: set-volatility {plan.type_name} -> {plan.tier}"
        )
        commit_sha = (
            ports.autocommit(root, ["openkos.yaml"], message)
            if ports.autocommit_config_edit is None
            else ports.autocommit_config_edit(root, message, decision)
        )
    return TierChanged(type_name=plan.type_name, tier=plan.tier, commit_sha=commit_sha)
