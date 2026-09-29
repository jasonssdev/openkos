"""The `revisions` application service (#1014 piece (a), Phase B re-plan,
design.md's "Phase B re-plan (2026-09-28)" section).

This slice (P5a) builds the read-only front half only: `load_decisions`
(the concept-level exclusions and `resolved_with` -- design.md Decision 4),
`resolve_decision_dates` (the event-date resolution table -- design.md
Decision 3), and `read_decision_vectors` (candidate vectors come ONLY from
`.openkos/vectors.db`, never from an embed call at detection time --
design.md Decision B1). Nothing here calls an LLM or an embedder, and
nothing here writes to the bundle or to `.openkos/`: `read_decision_vectors`
is a pure READ over whatever `state/reindex.py` already wrote, and it never
creates the vector store as a side effect of checking it (Decision B1).

Candidate generation itself (`plan_revision_candidates`) and the judge
(`judge_pairs`) stay in the Phase A leaf, `resolution/decision_revision.py`
-- this module composes them with bundle/state reads, it does not
reimplement them (ADR-0018: narrow synchronous use-case services under
`application/`, no `engine.py`).

Later Phase B slices (P5b, P6) extend this same module with input-digest
freshness checking, candidate planning, and judging; they are out of scope
for this file as it stands after P5a."""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Literal

from openkos import config, lifecycle, sensitivity
from openkos.bundle import provenance as bundle_provenance
from openkos.model import okf
from openkos.model.relations import RESOLUTION_RELATION_TYPES
from openkos.resolution.decision_revision import DecisionDate
from openkos.state import reindex
from openkos.state.vectorstore import (
    VecUnavailable,
    content_hash,
    open_vector_store,
    vector_store_is_empty,
)

_DECISION_TYPE = "Decision"
"""The `type:` frontmatter value `load_decisions` scopes its walk to -- the
one string literal every consumer of `type` in this codebase spells inline
(e.g. `okf.survey_bundle`'s `"Source"` check); there is no shared
`model.types` constant for it to import instead."""


@dataclass(frozen=True)
class Decision:
    """One surviving Decision, as `load_decisions` sees it -- before dates,
    vectors, or candidate scoring are attached (those are later P5a/P5b
    steps over `decision_ids`, not fields of this record).

    `resolved_with` is already filtered to `RESOLUTION_RELATION_TYPES`
    (`supersedes`/`reconciled_with`/`revises`) -- the Phase A leaf's
    `DecisionInput.resolved_with` field this record eventually feeds reads
    only membership, never `openkos.model.relations` itself (design.md
    Decision 4)."""

    concept_id: str
    resolved_with: frozenset[str]


@dataclass(frozen=True)
class DecisionSet:
    """The result of one `load_decisions` run: every surviving Decision,
    plus the one exclusion design.md requires a dedicated count for.

    Deprecated and confidential exclusions are not counted here -- they are
    reported, if at all, from `lifecycle.deprecated_concept_ids`/
    `sensitivity.sensitive_concept_ids`'s own set sizes at the call site,
    the same way every other bundle-wide exclusion in this codebase is
    surfaced. `bad_relations` is different: it is a per-Decision parse
    outcome `load_decisions` alone observes while walking the bundle, so it
    has nowhere else to live (design.md Decision 4: "reported as a
    count")."""

    decisions: tuple[Decision, ...]
    bad_relations: int
    """Decisions whose `relations:` frontmatter failed `okf.decode_relations`
    (fail-closed -- design.md Decision 4: "their resolution state cannot be
    known"). Excluded from `decisions` unconditionally, the same as a
    deprecated Decision -- there is no flag that admits one."""


def load_decisions(
    layout: config.WorkspaceLayout,
    *,
    include_confidential: bool,
    local_exemption: bool,
) -> DecisionSet:
    """Walk the bundle once for every `type: Decision` document, applying
    design.md Decision 4's three concept-level exclusions BEFORE any
    candidate input is built: deprecated (always -- there is no
    `--include-deprecated` flag, because a superseded Decision is already
    resolved), confidential (unless `include_confidential` or
    `local_exemption` releases it), and a Decision whose `relations:`
    frontmatter fails to parse (always -- see `DecisionSet.bad_relations`).

    A Decision that fails to read or parse at all (`scan.read_error`/
    `scan.parse_error` set) contributes nothing -- it is not a `Decision` by
    construction, since its `type` could not even be read; this mirrors
    `lifecycle.deprecated_concept_ids`/`sensitivity.sensitive_concept_ids`'s
    own fail-safe skip, not their fail-CLOSED one (there is no `type` to
    exclude the concept BY yet)."""
    deprecated = lifecycle.deprecated_concept_ids(layout.bundle_dir)
    confidential = sensitivity.sensitive_concept_ids(
        layout.bundle_dir,
        include_confidential=include_confidential,
        local_exemption=local_exemption,
    )

    decisions: list[Decision] = []
    bad_relations = 0
    for scan in okf._iter_docs(layout.bundle_dir):
        if scan.read_error is not None or scan.parse_error is not None:
            continue
        metadata = scan.metadata or {}
        if metadata.get("type") != _DECISION_TYPE:
            continue
        concept_id = okf.concept_id_for(scan.path, layout.bundle_dir)
        if concept_id in deprecated or concept_id in confidential:
            continue
        try:
            relations = okf.decode_relations(metadata)
        except ValueError:
            bad_relations += 1
            continue
        resolved_with = frozenset(
            relation.target
            for relation in relations
            if relation.type in RESOLUTION_RELATION_TYPES
        )
        decisions.append(Decision(concept_id=concept_id, resolved_with=resolved_with))

    return DecisionSet(decisions=tuple(decisions), bad_relations=bad_relations)


def resolve_decision_dates(
    layout: config.WorkspaceLayout, decision_ids: Collection[str]
) -> dict[str, DecisionDate]:
    """Resolve each id's `DecisionDate` via `provenance_source_ancestors_many`
    (P4.2, one parse for every id) + `okf.read_event_date`, applying
    design.md Decision 3's four-case rule exactly, checked in order:

    1. No reached Source at all: `"none-reached"`.
    2. Any reached Source has no file, cannot be parsed, or its
       `event_date` is absent/malformed (`okf.read_event_date(...).value is
       None`): `"missing"` -- a single unusable Source taints the whole
       resolution, per the rule's own literal wording ("ANY reached
       Source ... missing"), never silently outvoted by the others.
    3. More than one distinct value among the (now all-valid) reached
       dates: `"multiple"`.
    4. Otherwise: `"dated"`, with the single agreed value.

    Builds its own `files` snapshot the same way `list_service.
    list_provenance_sources` does (one `okf.iter_bundle_markdown` walk, an
    unreadable file simply contributing no entry) -- a Source with no file
    behind it is then indistinguishable, at the lookup below, from a
    dangling reference the walk never reached, which is exactly case 2's
    "no file" branch."""
    files: dict[str, str] = {}
    for path in okf.iter_bundle_markdown(layout.bundle_dir):
        if path.name in okf.RESERVED_FILENAMES:
            continue
        rel = path.relative_to(layout.bundle_dir).as_posix()
        try:
            files[rel] = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

    ancestors_by_id = bundle_provenance.provenance_source_ancestors_many(
        files, object_ids=decision_ids
    )
    return {
        decision_id: _resolve_one_decision_date(files, ancestors_by_id[decision_id])
        for decision_id in decision_ids
    }


def _resolve_one_decision_date(
    files: Mapping[str, str], source_ids: Sequence[str]
) -> DecisionDate:
    """One id's share of `resolve_decision_dates`'s four-case rule -- see
    that function's docstring for the case table. `source_ids` is the
    already-computed `sources/`-prefixed ancestor list for this one id."""
    if not source_ids:
        return DecisionDate(value=None, state="none-reached")

    values: set[date] = set()
    for source_id in source_ids:
        text = files.get(f"{source_id}.md")
        if text is None:
            return DecisionDate(value=None, state="missing")
        try:
            metadata, _ = okf.load_frontmatter(text)
        except Exception:  # broad: any parse failure makes this Source unusable
            return DecisionDate(value=None, state="missing")
        stored = okf.read_event_date(metadata)
        if stored.value is None:
            return DecisionDate(value=None, state="missing")
        values.add(stored.value)

    if len(values) > 1:
        return DecisionDate(value=None, state="multiple")
    (only,) = values
    return DecisionDate(value=only, state="dated")


VectorStoreState = Literal["ok", "absent", "model-mismatch"]
"""design.md Decision B1's three whole-run vector-store states. `"absent"`
covers BOTH an absent store and a present-but-empty one -- both key on the
same `vector_store_is_empty` predicate, and the report renders the same
"run `openkos reindex`" remedy for either."""


@dataclass(frozen=True)
class VectorCoverage:
    """One `read_decision_vectors` run's result (design.md Decision B1)."""

    store: VectorStoreState
    vectors: Mapping[str, Sequence[float]]
    """Current vectors only, keyed by concept id -- an id in `missing` or
    `stale` is never a key here."""
    missing: frozenset[str]
    """No `doc_vectors` row at all: never embedded, or withheld by
    reindex's own confidential egress gate."""
    stale: frozenset[str]
    """A `doc_vectors` row exists, but its `content_hash` no longer matches
    the Decision's current file bytes -- edited since the last reindex."""


_ABSENT_COVERAGE = VectorCoverage(
    store="absent", vectors={}, missing=frozenset(), stale=frozenset()
)
"""The shared zero-coverage result for the two "no usable store at all"
branches (store absent/empty, or `sqlite-vec` unavailable) -- one literal
instance so the two branches cannot drift on a stray field."""

_MODEL_MISMATCH_COVERAGE = VectorCoverage(
    store="model-mismatch", vectors={}, missing=frozenset(), stale=frozenset()
)
"""The shared zero-coverage result for a stored `embedding_model` tag that
is absent or differs from the currently configured model -- a DISTINCT
whole-run state from `_ABSENT_COVERAGE` (design.md Decision B1's table:
different message, same remedy)."""


def read_decision_vectors(
    layout: config.WorkspaceLayout,
    decision_ids: Collection[str],
    files: Mapping[str, bytes],
    *,
    embedding_model: str,
) -> VectorCoverage:
    """Read each eligible Decision's document vector from `.openkos/
    vectors.db`'s `doc_vectors` table -- this function never embeds
    anything (design.md Decision B1: "`revisions` makes zero embedding
    calls").

    `files` maps each id in `decision_ids` to its CURRENT raw file bytes,
    for the per-Decision `content_hash` freshness compare below; an id
    absent from `files` (its file could not be read) is treated the same as
    a hash mismatch -- `stale`, never silently `vectors` -- because
    freshness cannot be confirmed either way.

    Probes with `vector_store_is_empty` BEFORE ever calling
    `open_vector_store`: that probe reads over a plain read-only
    connection, so an absent store is never created as a side effect of
    this READ (design.md Decision B1: "a read verb must never create a
    derived store"). `open_vector_store` is reached only once the store is
    known to already exist and hold at least one embedded concept."""
    if vector_store_is_empty(layout.vectors_db_path):
        return _ABSENT_COVERAGE
    try:
        store = open_vector_store(layout.vectors_db_path)
    except VecUnavailable:
        return _ABSENT_COVERAGE

    try:
        if store.read_model_tag() != reindex.embedding_tag(embedding_model):
            return _MODEL_MISMATCH_COVERAGE
        stored = store.document_vectors(decision_ids)
    finally:
        store.close()

    vectors: dict[str, Sequence[float]] = {}
    missing: set[str] = set()
    stale: set[str] = set()
    for concept_id in decision_ids:
        record = stored.get(concept_id)
        if record is None:
            missing.add(concept_id)
            continue
        current_bytes = files.get(concept_id)
        if current_bytes is None or record.content_hash != content_hash(current_bytes):
            stale.add(concept_id)
            continue
        vectors[concept_id] = record.vector

    return VectorCoverage(
        store="ok",
        vectors=vectors,
        missing=frozenset(missing),
        stale=frozenset(stale),
    )
