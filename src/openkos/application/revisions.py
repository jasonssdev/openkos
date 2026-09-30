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

This slice (P5b) extends it with `revision_input_digests` (design.md
Decision 2's input-digest table), `is_fresh` (Decision 2's strict
freshness rule -- deliberately NOT `findings._is_stale`'s lenient
`None`-means-unchanged rule, because a revision finding can lead to a
bundle write), and `plan_revisions` (the zero-LLM served/to_judge split,
"Phase B re-plan" Data flow).

This slice (P6) adds `judge_revisions` (judges `plan.to_judge` through the
Phase A leaf's `judge_pairs`, persisting only non-malformed verdicts) and
`actionable_revision_findings` (the strict-freshness, actionable-only read
`reconcile --from-findings`, Phase B S8/S9, will consume). `judge_revisions`
is also where design.md Decision B2's "the flag releases only the judge's
chat send, never an embed" rule is enforced: `_load_doc` (a module-local
copy of `contradiction._load_doc`'s sensitivity re-check, design.md
Decision 5) re-verifies each Decision's sensitivity independently of
`load_decisions`'s own upstream exclusion, walk-independent and
fail-closed, before that body ever reaches `llm.chat`."""

import hashlib
import sqlite3
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal, Protocol, cast

from openkos import config, lifecycle, sensitivity
from openkos.application import backends as application_backends
from openkos.bundle import provenance as bundle_provenance
from openkos.llm.base import (
    BackendError,
    BackendModelNotFound,
    BackendUnavailable,
    LLMBackend,
)
from openkos.model import okf
from openkos.model.relations import RESOLUTION_RELATION_TYPES
from openkos.resolution import decision_revision
from openkos.resolution.decision_revision import DecisionDate
from openkos.state import derived, reindex, revision_findings
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

    Builds its `files` snapshot via `_bundle_text_snapshot` (one
    `okf.iter_bundle_markdown` walk, an unreadable file simply contributing
    no entry) -- a Source with no file behind it is then indistinguishable,
    at the lookup below, from a dangling reference the walk never reached,
    which is exactly case 2's "no file" branch."""
    files = _bundle_text_snapshot(layout)
    ancestors_by_id = bundle_provenance.provenance_source_ancestors_many(
        files, object_ids=decision_ids
    )
    return {
        decision_id: _resolve_one_decision_date(files, ancestors_by_id[decision_id])
        for decision_id in decision_ids
    }


def _bundle_text_snapshot(layout: config.WorkspaceLayout) -> dict[str, str]:
    """One whole-bundle read of every markdown file's decoded text, keyed by
    bundle-relative POSIX path (INCLUDING the `.md` suffix) -- the
    `Mapping[str, str]` shape `bundle_provenance.provenance_source_ancestors_many`/
    `_parse_provenance_by_id` expect. Extracted from `resolve_decision_dates`'s
    original inline block (P5a) so `resolve_decision_dates`,
    `revision_input_digests`'s callers, and `is_fresh` share ONE reader
    instead of three separate walks (P5b). An unreadable file (bad encoding,
    a race with a concurrent delete) simply contributes no entry -- the same
    degrade-not-crash posture `list_service.list_provenance_sources` and
    this function's prior inline form both took; behavior is unchanged by
    the extraction."""
    files: dict[str, str] = {}
    for path in okf.iter_bundle_markdown(layout.bundle_dir):
        if path.name in okf.RESERVED_FILENAMES:
            continue
        rel = path.relative_to(layout.bundle_dir).as_posix()
        try:
            files[rel] = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
    return files


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
        except okf.FrontmatterError:  # any parse failure makes this Source unusable
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
    backend: str = config.DEFAULT_BACKEND,
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

    `backend` (issue #1057 Phase 11) defaults to `config.DEFAULT_BACKEND`
    (`"ollama"`), preserving every pre-Phase-11 caller's behavior
    byte-identically; it is passed straight through to
    `reindex.embedding_tag(embedding_model, backend)` for the stored-tag
    comparison, so a backend switch reads as a `model-mismatch` exactly
    like a model-name change would.

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
        if store.read_model_tag() != reindex.embedding_tag(embedding_model, backend):
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


_SOURCES_OF_PREFIX = "sources-of:"
"""Duplicated verbatim from `state.revision_findings._SOURCES_OF_PREFIX`
(kept private there): this module builds the `input_ref` strings that
module's own sweep matches against, so the literal must agree exactly.
There is no shared public constant to import instead without widening that
already-shipped module's API outside this slice's scope (design.md
Decision 2)."""


def revision_input_digests(
    layout: config.WorkspaceLayout,
    files: Mapping[str, str],
    pair_ids: tuple[str, str],
) -> tuple[revision_findings.InputDigest, ...]:
    """design.md Decision 2's input-digest table for one candidate pair, in
    the documented ordinal order:

    1, 2. `pair_ids` sorted (so the row order is always
       `(smaller_id, larger_id)`, matching `state.revision_findings`'
       `pair_id_0 < pair_id_1` invariant) -- `content_hash` of each
       Decision's raw file bytes, read fresh from disk (as
       `cli.curate.finding_input_digests` does; NOT decoded through `files`,
       so this row is byte-exact even if `files` was built with universal
       newline translation).
    3, 4. `sources-of:<id>` for each side -- sha256 of the `"\\n"`-joined
       SORTED reached-Source id list, over the id list itself (never over
       file bytes), so this row moves when provenance PATH changes even if
       every file it currently names is unreadable.
    5+. every reached Source id of EITHER side, sorted and deduped --
       `content_hash` of that Source's raw file bytes. A dangling
       reference (no file behind it) contributes NO row here at all, so a
       stored tuple computed while that file existed is a different LENGTH
       from a freshly-recomputed one after it is deleted -- exactly the
       "an unreadable input must never count as unchanged" property
       `is_fresh`'s strict equality depends on.

    `files` is used ONLY for the provenance walk
    (`bundle_provenance.provenance_source_ancestors_many`) -- the
    bundle-relative-path-keyed `Mapping[str, str]` snapshot
    `_bundle_text_snapshot` builds, reusable across many calls (one per
    candidate pair) without re-walking the bundle each time."""
    id_0, id_1 = sorted(pair_ids)
    ancestors_by_id = bundle_provenance.provenance_source_ancestors_many(
        files, object_ids=(id_0, id_1)
    )

    digests: list[revision_findings.InputDigest] = []
    for decision_id in (id_0, id_1):
        try:
            raw_bytes = okf.concept_path_for(
                decision_id, layout.bundle_dir
            ).read_bytes()
        except OSError:
            continue
        digests.append(
            revision_findings.InputDigest(decision_id, content_hash(raw_bytes))
        )

    reached_by_id = {
        decision_id: ancestors_by_id.get(decision_id, [])
        for decision_id in (id_0, id_1)
    }
    for decision_id in (id_0, id_1):
        joined = "\n".join(sorted(reached_by_id[decision_id]))
        digests.append(
            revision_findings.InputDigest(
                f"{_SOURCES_OF_PREFIX}{decision_id}",
                hashlib.sha256(joined.encode("utf-8")).hexdigest(),
            )
        )

    all_reached = sorted(set(reached_by_id[id_0]) | set(reached_by_id[id_1]))
    for source_id in all_reached:
        try:
            raw_bytes = okf.concept_path_for(source_id, layout.bundle_dir).read_bytes()
        except OSError:
            continue
        digests.append(
            revision_findings.InputDigest(source_id, content_hash(raw_bytes))
        )

    return tuple(digests)


def is_fresh(
    layout: config.WorkspaceLayout,
    finding: revision_findings.RevisionFinding,
    *,
    effective_confidential: bool | None = None,
) -> bool:
    """design.md Decision 2's strict freshness rule, checked in order --
    ALL four must hold, never the lenient `None`-means-unchanged rule
    `findings._is_stale` uses elsewhere in this codebase (a revision finding
    can lead to a bundle write via `reconcile --from-findings`, so an input
    that cannot currently be read must never count as unchanged):

    1. `finding` IS the pair's CURRENT row -- re-read from
       `.openkos/findings.db` and compared by full equality, so a caller's
       stale in-memory copy of a row `record_revision_findings` has since
       REPLACEd is correctly rejected, never trusted just because it was
       handed in.
    2. `finding.prompt_version == decision_revision.JUDGE_PROMPT_VERSION`.
    3. `finding.include_confidential == effective_confidential`, UNLESS
       `effective_confidential` is `None` -- the caller's explicit
       opt-out of this one dimension (every real `plan_revisions` call
       passes its own resolved `--include-confidential OR local_exemption`
       value here).
    4. The recomputed current digest tuple
       (`revision_input_digests`, over a fresh `_bundle_text_snapshot`)
       equals `finding.input_digests` EXACTLY -- strict tuple equality, so
       one fewer current row (an input that became unreadable) can never
       tie with a shorter stored tuple by coincidence.

    An absent `.openkos/findings.db`, or a present-but-unreadable one,
    degrades to `False` (nothing to compare against, or a corrupt store --
    fail toward re-judging, never toward silently serving a stale verdict),
    mirroring `_partition_persisted_serves`'s fail-open-to-judging posture
    (`main.py:13333-13353`) and never CREATING the store as a side effect of
    this read (`derived.open_derived_connection` is only reached once
    `findings_db_path.exists()` is already true)."""
    if not layout.findings_db_path.exists():
        return False
    try:
        conn = derived.open_derived_connection(layout.findings_db_path)
        try:
            persisted = revision_findings.open_revision_findings(conn)
        finally:
            conn.close()
    except (OSError, sqlite3.Error):
        return False

    latest_by_pair: dict[tuple[str, str], revision_findings.RevisionFinding] = {}
    for row in persisted:
        latest_by_pair[row.pair_ids] = row
    current = latest_by_pair.get(finding.pair_ids)
    if current != finding:
        return False
    if current.prompt_version != decision_revision.JUDGE_PROMPT_VERSION:
        return False
    if (
        effective_confidential is not None
        and current.include_confidential != effective_confidential
    ):
        return False

    files = _bundle_text_snapshot(layout)
    recomputed = revision_input_digests(layout, files, finding.pair_ids)
    return recomputed == current.input_digests


@dataclass(frozen=True)
class RevisionPlan:
    """One `plan_revisions` run's result (design.md's Phase B re-plan
    Interfaces: "`RevisionPlan` carries coverage, candidate plan, served
    findings, to_judge")."""

    coverage: VectorCoverage
    candidate_plan: decision_revision.RevisionCandidatePlan
    served: tuple[revision_findings.RevisionFinding, ...]
    """Persisted findings whose pair is still a candidate AND still fresh --
    P6's report renders these directly, with no further judging."""
    to_judge: tuple[decision_revision.RevisionCandidate, ...]
    """Candidates needing a judge call: no persisted finding at all, a
    stale one, or every candidate when `fresh=True`."""


def plan_revisions(
    layout: config.WorkspaceLayout,
    decisions: DecisionSet,
    *,
    embedding_model: str,
    effective_confidential: bool,
    fresh: bool,
    backend: str = config.DEFAULT_BACKEND,
) -> RevisionPlan:
    """The zero-LLM, zero-embed planning step (design.md's Phase B re-plan
    Data flow): reads current vectors (`read_decision_vectors`), builds each
    surviving Decision's `DecisionInput` (subject always `None` -- design.md
    Decision B3 dropped the production subject pass), calls the Phase A leaf
    `decision_revision.plan_revision_candidates`, then partitions the
    result into `served`/`to_judge` via `is_fresh` against
    `.openkos/findings.db`'s persisted rows -- UNLESS `fresh=True`, which
    sends every candidate straight to `to_judge` with `served=()`.

    Reads `.openkos/findings.db` at most once here to build the
    pair-keyed lookup driving which findings even get an `is_fresh` check;
    `is_fresh` itself independently re-reads the store per finding to
    confirm it is still the CURRENT row (its own condition 1) -- one
    intentional redundant read per served candidate, accepted for this
    slice rather than widening `is_fresh`'s signature with a pre-fetched
    cache parameter the design does not name.

    `backend` (issue #1057 Phase 11) defaults to `config.DEFAULT_BACKEND`,
    forwarded straight through to `read_decision_vectors`."""
    decision_ids = [decision.concept_id for decision in decisions.decisions]

    current_bytes: dict[str, bytes] = {}
    for concept_id in decision_ids:
        try:
            current_bytes[concept_id] = okf.concept_path_for(
                concept_id, layout.bundle_dir
            ).read_bytes()
        except OSError:
            continue

    coverage = read_decision_vectors(
        layout,
        decision_ids,
        current_bytes,
        embedding_model=embedding_model,
        backend=backend,
    )

    files = _bundle_text_snapshot(layout)
    ancestors_by_id = bundle_provenance.provenance_source_ancestors_many(
        files, object_ids=decision_ids
    )
    decision_inputs = [
        decision_revision.DecisionInput(
            concept_id=decision.concept_id,
            subject=None,
            source_ids=frozenset(ancestors_by_id.get(decision.concept_id, [])),
            resolved_with=decision.resolved_with,
        )
        for decision in decisions.decisions
    ]
    candidate_plan = decision_revision.plan_revision_candidates(
        decision_inputs, coverage.vectors
    )

    if fresh:
        return RevisionPlan(
            coverage=coverage,
            candidate_plan=candidate_plan,
            served=(),
            to_judge=candidate_plan.candidates,
        )

    persisted_by_pair: dict[tuple[str, str], revision_findings.RevisionFinding] = {}
    if layout.findings_db_path.exists():
        try:
            conn = derived.open_derived_connection(layout.findings_db_path)
            try:
                persisted = revision_findings.open_revision_findings(conn)
            finally:
                conn.close()
            for row in persisted:
                persisted_by_pair[row.pair_ids] = row
        except (OSError, sqlite3.Error):
            persisted_by_pair = {}

    served: list[revision_findings.RevisionFinding] = []
    to_judge: list[decision_revision.RevisionCandidate] = []
    for candidate in candidate_plan.candidates:
        finding = persisted_by_pair.get(candidate.pair_ids)
        if finding is not None and is_fresh(
            layout, finding, effective_confidential=effective_confidential
        ):
            served.append(finding)
        else:
            to_judge.append(candidate)

    return RevisionPlan(
        coverage=coverage,
        candidate_plan=candidate_plan,
        served=tuple(served),
        to_judge=tuple(to_judge),
    )


def _load_doc(
    layout: config.WorkspaceLayout,
    concept_id: str,
    *,
    effective_confidential: bool,
) -> tuple[str, str]:
    """Module-local copy of `contradiction._load_doc`'s sensitivity
    re-check (`contradiction.py:428-482`; design.md Decision 5: "The
    service loads each body with a module-local copy of `_load_doc`'s
    sensitivity re-check ... walk-independent and fail-closed"), applied
    here to the judge's own body load. Enforces design.md Decision B2's
    "the flag releases only the judge's chat send" rule AT THIS LAYER,
    not only via `load_decisions`'s upstream exclusion: a Decision the
    upstream `sensitivity.sensitive_concept_ids` walk silently missed (an
    unlistable subtree) is still degraded to `(concept_id, "")` here and
    never reaches `llm.chat`.

    `effective_confidential` is passed as `should_block`'s
    `include_confidential` -- already `--include-confidential OR
    local_exemption` (this module's single resolved flag, per
    `plan_revisions`/`is_fresh`), and `should_block` itself treats
    `include_confidential`/`local_exemption` as a disjunction, so passing
    the pre-OR'd value through one of the two parameters is equivalent to
    passing the original two.

    Returns `(title, body)`; an unreadable/unparseable document, or one
    `sensitivity.should_block` degrades, returns `(concept_id, "")` rather
    than raising or skipping the pair -- the caller always gets something
    to build a `JudgeSide` from."""
    try:
        text = okf.concept_path_for(concept_id, layout.bundle_dir).read_text(
            encoding="utf-8"
        )
    except (OSError, UnicodeDecodeError):
        return concept_id, ""
    try:
        metadata, body = okf.load_frontmatter(text)
    except okf.FrontmatterError:  # any parse failure degrades this doc, never raises
        return concept_id, ""
    if sensitivity.should_block(metadata, include_confidential=effective_confidential):
        return concept_id, ""
    title = str(metadata.get("title") or "") or concept_id
    return title, body


@dataclass(frozen=True)
class RevisionOutcome:
    """One `judge_revisions` run's result. `results` holds EVERY judged
    `RevisionVerdict` in `plan.to_judge` order, including a `malformed=True`
    one (Phase B, S7/S8's `--all` view shows it, counted as "N malformed"
    -- design.md Decision 6) -- mirroring `decision_revision.RevisionBatch`'s
    own contract, since `judge_revisions` adds persistence on top of
    `judge_pairs` rather than replacing its shape. `failure`/`failed_index`
    surface `RevisionBatch`'s own partial-batch contract unchanged: a raised
    backend error (`OllamaError` at runtime -- `llm.ollama`'s concrete
    subclass of this module's own `BackendError`, ADR-0018 D1: an
    `application/` module imports only `llm.base`, never a concrete backend
    module) stops judging and is carried here instead of propagating, the
    same "already-paid-for prefix survives" guarantee `judge_pairs` already
    gives, so a caller inspecting `RevisionOutcome` sees exactly what
    `judge_pairs` would have told it directly."""

    results: tuple[decision_revision.RevisionVerdict, ...]
    failure: BackendError | None = None
    failed_index: int | None = None


def _revision_finding_from_verdict(
    layout: config.WorkspaceLayout,
    files: Mapping[str, str],
    verdict: decision_revision.RevisionVerdict,
    *,
    effective_confidential: bool,
) -> revision_findings.RevisionFinding:
    """One judged (non-malformed) `RevisionVerdict`'s durable shape, ready
    for `record_revision_findings` -- design.md Decision 2's persisted
    columns. `verdict.pair_ids`/`verdict.dates` are already aligned (sorted
    `concept_id` order, `_sorted_pair`'s contract in the Phase A leaf), so
    no re-sorting happens here; `record_revision_findings` itself sorts
    defensively regardless."""
    date_0, date_1 = verdict.dates
    dates: tuple[str | None, str | None] = (
        date_0.value.isoformat() if date_0.value is not None else None,
        date_1.value.isoformat() if date_1.value is not None else None,
    )
    date_states = (date_0.state, date_1.state)
    return revision_findings.RevisionFinding(
        pair_ids=verdict.pair_ids,
        verdict=verdict.verdict.value,
        confidence=verdict.confidence,
        rationale=verdict.rationale,
        quotes=verdict.quotes,
        dates=dates,
        date_states=date_states,
        include_confidential=effective_confidential,
        prompt_version=decision_revision.JUDGE_PROMPT_VERSION,
        input_digests=revision_input_digests(layout, files, verdict.pair_ids),
    )


def judge_revisions(
    layout: config.WorkspaceLayout,
    plan: RevisionPlan,
    *,
    llm: LLMBackend,
    effective_confidential: bool,
    on_progress: Callable[[int, int, decision_revision.RevisionVerdict], None]
    | None = None,
) -> RevisionOutcome:
    """Judge every `plan.to_judge` candidate through the Phase A leaf's
    `judge_pairs` (design.md's Phase B re-plan Data flow: "judge_revisions
    -> pair_direction -> build_judge_messages -> llm.chat -> parse ->
    persist"), then persist every non-malformed `RevisionVerdict` via
    `record_revision_findings` (P3.4) -- a malformed verdict is never
    persisted, so it is re-judged next run (design.md Decision 6).

    `judge_pairs` never raises: a mid-batch `OllamaError` stops the loop and
    returns the completed prefix in `RevisionBatch.results` instead of
    propagating (its own documented contract). Because of that, persisting
    once, after `judge_pairs` returns, over whatever `results` it produced
    already gives the "already-judged pairs survive a later failure"
    property design.md asks for -- there is no separate result the caller
    could lose by not persisting incrementally mid-loop; `record_revision_
    findings` itself commits once per call regardless.

    Builds each `JudgeSide` from `resolve_decision_dates` (P5a.4, the same
    `DecisionDate`s `plan_revisions` already resolved once) and `_load_doc`
    (this module's own sensitivity re-check) over the concept ids named by
    `plan.to_judge` -- never over `plan.served`'s pairs, which need no
    judge call at all."""
    decision_ids = sorted(
        {concept_id for candidate in plan.to_judge for concept_id in candidate.pair_ids}
    )
    dates = resolve_decision_dates(layout, decision_ids)
    sides: dict[str, decision_revision.JudgeSide] = {}
    for concept_id in decision_ids:
        title, body = _load_doc(
            layout, concept_id, effective_confidential=effective_confidential
        )
        sides[concept_id] = decision_revision.JudgeSide(
            concept_id=concept_id, title=title, body=body, date=dates[concept_id]
        )

    pairs = [
        (sides[id_a], sides[id_b])
        for id_a, id_b in (candidate.pair_ids for candidate in plan.to_judge)
    ]
    batch = decision_revision.judge_pairs(pairs, llm=llm, on_progress=on_progress)

    files = _bundle_text_snapshot(layout)
    to_persist = [
        _revision_finding_from_verdict(
            layout, files, verdict, effective_confidential=effective_confidential
        )
        for verdict in batch.results
        if not verdict.malformed
    ]
    if to_persist:
        conn = derived.open_derived_connection(layout.findings_db_path)
        try:
            revision_findings.record_revision_findings(conn, to_persist)
        finally:
            conn.close()

    return RevisionOutcome(
        results=tuple(batch.results),
        failure=batch.failure,
        failed_index=batch.failed_index,
    )


def actionable_revision_findings(
    layout: config.WorkspaceLayout,
) -> tuple[revision_findings.RevisionFinding, ...]:
    """The latest persisted row per pair, kept only when it is BOTH still
    fresh (`is_fresh`, with `effective_confidential=None` -- this reader has
    no per-call confidential flag of its own, so it opts out of that one
    freshness dimension, `is_fresh`'s own documented escape) and actionable
    (`decision_revision.is_actionable_revision`, Phase A leaf) -- the read
    `reconcile --from-findings` (Phase B, S8/S9) will drive its walk from.

    An absent `.openkos/findings.db`, or a present-but-unreadable one,
    degrades to `()` -- nothing to read, not an error -- mirroring
    `is_fresh`'s own degrade-to-`False`-never-raise posture for the same
    store. Results are sorted by `pair_ids` for a deterministic order; the
    underlying store carries no ordering a caller should rely on beyond
    that."""
    if not layout.findings_db_path.exists():
        return ()
    try:
        conn = derived.open_derived_connection(layout.findings_db_path)
        try:
            persisted = revision_findings.open_revision_findings(conn)
        finally:
            conn.close()
    except (OSError, sqlite3.Error):
        return ()

    latest_by_pair: dict[tuple[str, str], revision_findings.RevisionFinding] = {}
    for row in persisted:
        latest_by_pair[row.pair_ids] = row

    results = [
        finding
        for finding in latest_by_pair.values()
        if is_fresh(layout, finding)
        and decision_revision.is_actionable_revision(
            finding.verdict, finding.confidence, finding.quotes[0], finding.quotes[1]
        )
    ]
    return tuple(sorted(results, key=lambda finding: finding.pair_ids))


# -- The `revisions` run ----------------------------------------------------
#
# The use case `openkos revisions` is an adapter over (issue #1168): the
# front half (`load_decisions`, `plan_revisions`) and the judging half
# (`judge_revisions`) above, sequenced with the one cost gate between them.
# `run_revisions` takes an explicit `root`, returns a typed `RevisionsRun` and
# raises typed `RevisionsRefused` subclasses; it never prompts, renders, reads
# the current directory, calls `sys.stdin.isatty()` or raises `typer.Exit`.

EXPERIMENTAL_NOTICE = (
    "openkos revisions: experimental -- detection quality is unmeasured on "
    "real bundles; review every finding before applying it with 'openkos "
    "reconcile --from-findings'."
)
"""design.md's Phase B re-plan, Decision B4: stated once, on every non-refused
run -- the whole point is that this detector has only been measured against a
synthetic harness fixture (`evals/decision_revisions/`), never against a real
bundle."""

NO_VECTORS_MESSAGE = (
    "openkos revisions: no document embeddings found -- run 'openkos reindex' first."
)
MODEL_MISMATCH_MESSAGE = (
    "openkos revisions: vectors.db was embedded with a different embedding "
    "model or scheme than 'embedding_model' -- run 'openkos reindex' first."
)
"""design.md Decision B1's two whole-run vector-store degrade messages:
`revisions` never embeds, so a Decision's document vector comes ONLY from
`.openkos/vectors.db` as written by `openkos reindex`. Both cases make zero LLM
calls, state their remedy, and are not failures -- the store is simply not built
for the currently configured model."""

_DOCTOR_HINT = " Or run `openkos doctor` to diagnose the environment."
_VERB = "revisions"


class RevisionsRefused(Exception):
    """Base of every refusal `run_revisions` raises. `message` is the complete,
    user-facing text, so an adapter renders it verbatim and maps the TYPE to an
    exit code."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NotAWorkspace(RevisionsRefused):
    """`root` is not an OpenKOS workspace."""


class WorkspaceUnreadable(RevisionsRefused):
    """Reading `openkos.yaml` raised `OSError`/`ValueError`."""


class ConfirmationUnavailable(RevisionsRefused):
    """The cost question was required and could not be asked (stdin is not a
    TTY and `--auto` was not passed); nothing was judged."""


@dataclass(frozen=True)
class RevisionsRequest:
    skip_confirmation: bool = False
    """`--auto`: skip the pair-judgment question."""
    include_confidential: bool = False
    fresh: bool = False


ConfirmationAnswer = Literal["proceed", "declined", "unavailable"]


class RevisionsObserver(Protocol):
    """The adapter's window onto a run."""

    def started(self) -> None:
        """The workspace was accepted; the run is about to start."""

    def truncation_notice(self, notice: str) -> None:
        """Candidates were dropped by the cap; stated BEFORE the cost gate so an
        operator learns it before consenting to the spend."""

    def cost_gate(self, plan: RevisionPlan) -> None:
        """State the exact judge-call count. Called whenever at least one pair
        is left to judge, even under `--auto`."""

    def confirm_judging(self) -> ConfirmationAnswer:
        """Ask whether to proceed. `"unavailable"` means it could not be asked."""

    def progress_callback(
        self,
    ) -> Callable[[int, int, decision_revision.RevisionVerdict], None] | None:
        """The per-pair judging progress hook, or `None` for silence."""


@dataclass(frozen=True)
class RevisionsPorts:
    chat_client: Callable[[config.Config, str | None], LLMBackend]
    resolve_local_exemption: Callable[
        [application_backends.HasLocality, config.Config], bool
    ] = application_backends.resolve_local_exemption
    truncation_notice: Callable[
        [decision_revision.RevisionCandidatePlan], str | None
    ] = decision_revision.revision_truncation_notice


@dataclass(frozen=True)
class RevisionsReport:
    """What a judged run holds: the decisions it considered, the plan it
    judged and the outcome -- everything `revisions_report` renders from."""

    decisions: DecisionSet
    plan: RevisionPlan
    outcome: RevisionOutcome


@dataclass(frozen=True)
class RevisionsRun:
    """One `run_revisions` result. `status` is how far it got; `report` is set
    exactly when `status == "completed"`."""

    status: Literal[
        "no_decisions", "vectors_absent", "model_mismatch", "declined", "completed"
    ]
    model: str
    report: RevisionsReport | None = None


def revisions_batch_failure_message(
    outcome: RevisionOutcome, *, total: int, model: str
) -> str:
    """One line for a partial `RevisionOutcome` (#441 precedent): the same
    3-tier cause-specific wording the sibling verbs use, prefixed with how much
    paid-for judging survived. `total` is `len(plan.to_judge)` -- the
    judged-pair budget this run actually paid for, never the full candidate plan
    (served pairs cost nothing and cannot fail)."""
    failure = outcome.failure
    context = (
        f"openkos {_VERB}: failed after judging {len(outcome.results)} "
        f"of {total} planned pair(s)"
    )
    if isinstance(failure, BackendUnavailable):
        return (
            f"{context} -- {failure}. Start it with `ollama serve`, then "
            f"try again.{_DOCTOR_HINT}"
        )
    if isinstance(failure, BackendModelNotFound):
        return (
            f"{context} -- model '{model}' is not installed. Pull it with "
            f"`ollama pull {model}`, then try again."
        )
    return f"{context} -- {failure}."


def run_revisions(
    root: Path,
    request: RevisionsRequest,
    ports: RevisionsPorts,
    observer: RevisionsObserver,
) -> RevisionsRun:
    """Detect Decisions that a later Decision reverses, refines or reaffirms in
    the workspace at `root`. Read-only over the bundle: the one thing it writes
    is `.openkos/findings.db`.

    Candidate pairs are blocked by embedding similarity over each eligible
    Decision's document vector, read directly from `.openkos/vectors.db` -- this
    makes NO embedding call, ever (Decision B1). The ONE cost gate (Decision B4)
    fires only when there is at least one candidate pair left to judge."""
    reason = config.require_workspace(root)
    if reason is not None:
        raise NotAWorkspace(f"openkos {_VERB}: refusing to run -- {reason}.")

    layout = config.WorkspaceLayout(root)
    try:
        cfg = config.read_config(root)
    except (OSError, ValueError) as exc:
        raise WorkspaceUnreadable(
            f"openkos {_VERB}: failed while reading the workspace -- {exc}."
        ) from exc

    observer.started()

    llm = ports.chat_client(cfg, None)
    local_exemption = ports.resolve_local_exemption(
        cast(application_backends.HasLocality, llm), cfg
    )
    # design.md Decision B2: the flag (or the local exemption) releases only the
    # judge's `llm.chat` send of a confidential Decision's body -- it never
    # authorizes an embedding call, which this run never makes at all.
    effective_confidential = request.include_confidential or local_exemption

    decisions = load_decisions(
        layout,
        include_confidential=effective_confidential,
        local_exemption=local_exemption,
    )
    if not decisions.decisions:
        return RevisionsRun(status="no_decisions", model=cfg.model)

    plan = plan_revisions(
        layout,
        decisions,
        embedding_model=cfg.embedding_model,
        effective_confidential=effective_confidential,
        fresh=request.fresh,
        backend=cfg.backend,
    )

    # design.md Decision B1's table: a whole-run vector-store degrade makes zero
    # LLM calls -- there is no candidate plan worth judging, so neither the gate
    # nor the judge is ever reached.
    if plan.coverage.store == "absent":
        return RevisionsRun(status="vectors_absent", model=cfg.model)
    if plan.coverage.store == "model-mismatch":
        return RevisionsRun(status="model_mismatch", model=cfg.model)

    # #378 precedent: stated BEFORE the gate, so an operator learns candidates
    # were dropped before consenting to the spend.
    notice = ports.truncation_notice(plan.candidate_plan)
    if notice is not None:
        observer.truncation_notice(notice)

    # Decision B4: the one remaining cost gate, stated (even under `--auto`)
    # whenever there is at least one pair left to judge -- a gate whose count is
    # zero states nothing and asks nothing.
    if plan.to_judge:
        observer.cost_gate(plan)
        if not request.skip_confirmation:
            answer = observer.confirm_judging()
            if answer == "unavailable":
                raise ConfirmationUnavailable(
                    f"openkos {_VERB}: refusing to spend model calls "
                    "without confirmation -- stdin is not a TTY; re-run "
                    "with --auto."
                )
            if answer == "declined":
                return RevisionsRun(status="declined", model=cfg.model)

    outcome = judge_revisions(
        layout,
        plan,
        llm=llm,
        effective_confidential=effective_confidential,
        on_progress=observer.progress_callback(),
    )
    return RevisionsRun(
        status="completed",
        model=cfg.model,
        report=RevisionsReport(decisions=decisions, plan=plan, outcome=outcome),
    )
