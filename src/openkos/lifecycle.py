"""Canonical-layer effective-status predicate (status-aware-retrieval,
MVP-3 gap #8 · S1).

`deprecated_concept_ids` is the ONE shared predicate every retrieval input
(FTS/vector/graph) and candidate-load surface (contradiction detection,
adjudication) filters against before fusion/candidate emission — see
`openspec/changes/status-aware-retrieval/design.md`. It imports only
`openkos.model.okf`, the canonical `openkos.bundle.provenance` closure and
stdlib (never `state`, `retrieval` or `graph`), a package-root leaf like
`lint.py`/`config.py`:
both `retrieval/` and `resolution/` depend on it with no cycle and no
retrieval<->resolution coupling.

A concept is effective-deprecated iff its own `status` frontmatter field
equals `"deprecated"`, OR it is the TARGET of ANY other concept's outbound
`supersedes` edge, OR it is a PROVENANCE ORPHAN of a superseded Source
(`provenance_orphans`: its whole provenance is a superseded Source). Self-`supersedes` edges (source == target) are dropped
before set-building, so they never mark a concept deprecated — that is the
only exemption this predicate makes.

**R2 (PINNED, fail-safe)**: there is no reciprocal-cancellation or
cycle-detection step of any kind — the predicate never inspects graph
structure beyond a single edge's own source/target. It simply deprecates
every non-self target it sees. A side effect of that simplicity is that ANY
`supersedes` cycle — a mutual 2-cycle (A -> B, B -> A), a 3-cycle, a longer
cycle, or a cycle with an extra chord edge — ends up with every one of its
members deprecated, since each member is the target of at least one
in-cycle edge. Contradictory or cyclic supersession is treated as
unresolved and hidden rather than guessed at.
"""

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from openkos.bundle import provenance as bundle_provenance
from openkos.model import okf


class _HasConceptId(Protocol):
    """Structural type for anything `filter_hits` can filter: `FtsHit`,
    `VecHit`, and `GraphHit` all expose `.concept_id`, but this module never
    imports them directly (that would pull `retrieval`/`state` into a
    canonical-layer leaf).

    Declared as a READ-ONLY `@property`, not a plain `concept_id: str`
    attribute (Phase 2 correction): every real hit type is a frozen
    dataclass, so mypy `--strict` treats their `concept_id` field as
    read-only. A plain attribute in a `Protocol` requires BOTH a getter and
    a setter to be structurally satisfied, which a frozen dataclass field
    never provides -- `mypy --strict` rejected `FtsHit`/`VecHit`/`GraphHit`
    as `H` until this was narrowed to a read-only property (caught wiring
    `retrieval/answer.py`, PR1's own single-file `mypy` check never
    exercised this against a real concrete hit type)."""

    @property
    def concept_id(self) -> str: ...


def _superseded_ids(supersedes: set[tuple[str, str]]) -> frozenset[str]:
    """The shared edge rule (R2, fail-safe): every non-self `supersedes`
    TARGET, cycles included, with no reciprocal-cancellation exemption. The
    ONE place this computation is made, so `deprecated_concept_ids` and the
    deprecated-status export (`superseded_from_metadata`) can never disagree
    about who is superseded (design: 'the predicate and the export can
    never disagree')."""
    return frozenset(target for source, target in supersedes if target != source)


def provenance_ids(meta: Mapping[str, object]) -> frozenset[str] | None:
    """One document's normalized `provenance` ids, or `None` when the field
    is absent or not a list (such a document is never swept: fail safe, the
    direction that hides nothing). Same `.md`-stripping the `forget` closure
    keys on, so the predicate and `forget` compare identical ids."""
    raw = meta.get("provenance")
    if not isinstance(raw, list):
        return None
    return frozenset(bundle_provenance.normalize_provenance_id(str(e)) for e in raw)


def provenance_orphans(
    provenance_by_id: Mapping[str, frozenset[str]],
    superseded_ids: Collection[str],
) -> frozenset[str]:
    """Concepts whose ENTIRE provenance is, directly or through other such
    concepts, a superseded Source (retire-superseded-sources).

    The ONE place the rule is computed: `deprecated_concept_ids` and
    `bundle.listing.list_objects` both call it. Roots are only the superseded
    ids under `sources/` -- a superseded `Decision` does not propagate to the
    Insights citing it. The closure is `forget --scope source`'s own
    (`bundle.provenance.provenance_closure`), so its non-empty-provenance
    guard keeps a concept with no recorded provenance, or with any live
    entry, out of the result. The roots themselves are excluded: a superseded
    Source is already deprecated by the edge rule. Pure; writes nothing."""
    roots = frozenset(sid for sid in superseded_ids if sid.startswith("sources/"))
    if not roots:
        return frozenset()
    closure = bundle_provenance.provenance_closure(provenance_by_id, root_ids=roots)
    return frozenset(closure) - roots


@dataclass(frozen=True)
class SupersededSet:
    """One `superseded_from_metadata`/`superseded_concept_ids` result
    (deprecated-status-export, issue #1075).

    `ids` is the edge-derived superseded set (the same rule
    `deprecated_concept_ids` folds into its own result). `complete` is
    `False` when any document contributed no data because it failed to
    read/parse or carried malformed `relations:` -- an unreadable document
    may hold the only edge that supersedes some concept, so a caller MUST
    NOT withdraw a deprecated-status export on an incomplete walk (spec:
    'Withdrawal Requires A Complete Edge Walk'). `unreadable` names every
    concept id that made the walk incomplete, sorted for determinism."""

    ids: frozenset[str]
    complete: bool
    unreadable: tuple[str, ...]


def superseded_from_metadata(
    docs: Mapping[str, Mapping[str, object] | None],
) -> SupersededSet:
    """Compute a `SupersededSet` from an already-held mapping of concept id
    to its frontmatter metadata (deprecated-status-export, issue #1075).

    Built for a WRITER's post-write view: the metadata it already holds for
    every document in the bundle (no extra walk), with its own planned
    texts substituted and deleted documents removed. A `None` value marks a
    concept whose document could not be read/parsed -- it contributes no
    edges and makes the result incomplete. A concept whose `relations:` is
    present but malformed (fails `okf.decode_relations`) contributes no
    edges for ITSELF either, and also makes the result incomplete, since a
    real `supersedes` edge it might have carried cannot be trusted absent."""
    supersedes: set[tuple[str, str]] = set()
    unreadable: list[str] = []
    for cid, meta in docs.items():
        if meta is None:
            unreadable.append(cid)
            continue
        try:
            relations = okf.decode_relations(dict(meta))
        except ValueError:
            unreadable.append(cid)
            continue
        for relation in relations:
            if relation.type == "supersedes":
                supersedes.add((cid, relation.target))

    return SupersededSet(
        ids=_superseded_ids(supersedes),
        complete=not unreadable,
        unreadable=tuple(sorted(unreadable)),
    )


def superseded_concept_ids(bundle_dir: Path) -> SupersededSet:
    """`superseded_from_metadata` over one fresh `okf._iter_docs` walk of
    `bundle_dir` (deprecated-status-export, issue #1075) -- the disk-backed
    counterpart callers use when they hold no in-memory planned view of
    their own (e.g. `lint`, `repair`)."""
    docs: dict[str, Mapping[str, object] | None] = {}
    for scan in okf._iter_docs(bundle_dir):
        cid = okf.concept_id_for(scan.path, bundle_dir)
        if scan.read_error is not None or scan.parse_error is not None:
            docs[cid] = None
        else:
            docs[cid] = scan.metadata or {}
    return superseded_from_metadata(docs)


def deprecated_concept_ids(bundle_dir: Path) -> frozenset[str]:
    """Compute the set of effective-deprecated concept ids for `bundle_dir`
    in one `okf._iter_docs` walk.

    A concept id is included when its own `status` is `"deprecated"`, or
    when it is the target of another concept's `supersedes` edge (self-refs
    dropped, no other exemption — see module docstring for the R2 fail-safe
    rule). A document that fails to read/parse, or whose `relations:` field
    is malformed, contributes no status/edges for itself and is otherwise
    skipped (fail-safe: never raises)."""
    status_by_id: dict[str, str] = {}
    provenance_by_id: dict[str, frozenset[str]] = {}
    supersedes: set[tuple[str, str]] = set()  # (source, target), source != target
    for scan in okf._iter_docs(bundle_dir):
        if scan.read_error is not None or scan.parse_error is not None:
            continue
        cid = okf.concept_id_for(scan.path, bundle_dir)
        meta = scan.metadata or {}
        status_by_id[cid] = "deprecated" if okf.declares_deprecated(meta) else ""
        provenance = provenance_ids(meta)
        if provenance is not None:
            provenance_by_id[cid] = provenance
        try:
            relations = okf.decode_relations(meta)
        except ValueError:
            relations = []  # malformed relations: no edges, no crash
        for relation in relations:
            if relation.type == "supersedes" and relation.target != cid:
                supersedes.add((cid, relation.target))

    # Fail-safe rule (R2): any non-self supersedes target is deprecated,
    # with no reciprocal-cancellation or cycle-length exemption — every
    # member of any supersedes cycle (mutual pair or longer) is hidden.
    # Shared with `superseded_from_metadata` via `_superseded_ids` so the
    # predicate and the deprecated-status export can never disagree.
    superseded = _superseded_ids(supersedes)
    own_deprecated = {
        cid for cid, status in status_by_id.items() if status == "deprecated"
    }
    return frozenset(
        own_deprecated | superseded | provenance_orphans(provenance_by_id, superseded)
    )


def filter_hits[H: _HasConceptId](hits: list[H], deprecated: frozenset[str]) -> list[H]:
    """Drop every hit whose `.concept_id` is in `deprecated`, preserving the
    relative order of the remaining hits.

    Generic over any `.concept_id`-bearing hit type (`FtsHit`, `VecHit`,
    `GraphHit`) so every retrieval seam reuses this one filter (design:
    "one generic filter_hits ... applied at every seam")."""
    return [hit for hit in hits if hit.concept_id not in deprecated]
