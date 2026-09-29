"""Unit tests for `openkos.application.revisions` (#1014 piece (a), Phase B
re-plan, Slice P5a): `load_decisions` (+ `resolved_with`), event-date
resolution (`resolve_decision_dates`), and vector coverage
(`read_decision_vectors`).

Exercises the service functions directly against a real (tmp-path)
workspace, mirroring `test_list_service.py`'s posture -- no CLI invocation,
no LLM, no embedder. `read_decision_vectors`'s tests build a REAL
`.openkos/vectors.db` via `vectorstore.open_vector_store`/`upsert`/
`write_model_tag`, the same fixture-building shape
`tests/unit/state/test_vectorstore.py` already uses for `document_vectors`,
rather than a hand-rolled fake -- this is a read seam over that exact
schema, and a fake risks drifting from it."""

import hashlib
from datetime import date
from pathlib import Path

import pytest

from openkos import config
from openkos.application import revisions
from openkos.llm.base import EMBED_DIM
from openkos.model import okf
from openkos.model.relations import RESOLUTION_RELATION_TYPES
from openkos.resolution import decision_revision
from openkos.resolution.decision_revision import DecisionDate
from openkos.state import derived, reindex, revision_findings, vectorstore
from openkos.state.vectorstore import content_hash


def _workspace(tmp_path: Path) -> config.WorkspaceLayout:
    """A workspace root with a real (empty) `bundle/` directory -- mirrors
    `test_list_service.py`'s `_workspace`: the service reads the bundle
    directly, not through a CLI `init`."""
    config.write_config(tmp_path)
    layout = config.WorkspaceLayout(tmp_path)
    layout.bundle_dir.mkdir(parents=True, exist_ok=True)
    return layout


def _write_doc(
    path: Path,
    *,
    type_: str = "Decision",
    title: str | None = "Stub",
    status: str | None = None,
    sensitivity: str | None = "private",
    relations: list[tuple[str, str]] | None = None,
    relations_raw: str | None = None,
    provenance: list[str] | None = None,
    event_date: str | None = None,
    body: str = "Body.",
) -> None:
    """Write a minimal concept `.md` file. `relations` is a list of
    `(target, type)` pairs; `relations_raw` overrides it with a
    hand-written frontmatter line (for the malformed-shape case).
    `provenance`/`event_date` mirror the same frontmatter fields
    `bundle/provenance.py`/`model/okf.py` read."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---", f"type: {type_}"]
    if title is not None:
        lines.append(f"title: {title}")
    if status is not None:
        lines.append(f"status: {status}")
    if sensitivity is not None:
        lines.append(f"sensitivity: {sensitivity}")
    if relations_raw is not None:
        lines.append(relations_raw)
    elif relations is not None:
        lines.append("relations:")
        for target, rel_type in relations:
            lines.append(f"  - target: {target}")
            lines.append(f"    type: {rel_type}")
    if provenance is not None:
        lines.append("provenance:")
        lines.extend(f"  - {entry}" for entry in provenance)
    if event_date is not None:
        lines.append(f"event_date: {event_date}")
    lines.append("---")
    path.write_text("\n".join(lines) + f"\n{body}\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# load_decisions
# ---------------------------------------------------------------------------


def test_load_decisions_excludes_deprecated_confidential_and_bad_relations(
    tmp_path: Path,
) -> None:
    """Design.md Decision 4's three concept-level exclusions: deprecated
    (unconditional), confidential (unless released), and unparseable
    `relations:` (unconditional, and separately counted)."""
    layout = _workspace(tmp_path)
    _write_doc(layout.bundle_dir / "decisions" / "deprecated.md", status="deprecated")
    _write_doc(
        layout.bundle_dir / "decisions" / "confidential.md", sensitivity="confidential"
    )
    _write_doc(
        layout.bundle_dir / "decisions" / "bad-relations.md",
        relations_raw="relations: not-a-list",
    )

    default = revisions.load_decisions(
        layout, include_confidential=False, local_exemption=False
    )
    assert default.decisions == ()
    assert default.bad_relations == 1

    with_flag = revisions.load_decisions(
        layout, include_confidential=True, local_exemption=False
    )
    ids_with_flag = {decision.concept_id for decision in with_flag.decisions}
    assert "decisions/deprecated" not in ids_with_flag  # unconditional exclusion
    assert "decisions/confidential" in ids_with_flag  # released by the flag
    assert "decisions/bad-relations" not in ids_with_flag  # unconditional exclusion
    assert with_flag.bad_relations == 1

    with_exemption = revisions.load_decisions(
        layout, include_confidential=False, local_exemption=True
    )
    ids_with_exemption = {decision.concept_id for decision in with_exemption.decisions}
    assert "decisions/confidential" in ids_with_exemption  # released by the exemption


@pytest.mark.parametrize("relation_type", sorted(RESOLUTION_RELATION_TYPES))
def test_load_decisions_builds_resolved_with_from_relation_frontmatter(
    tmp_path: Path, relation_type: str
) -> None:
    """A `relations:` target under a `RESOLUTION_RELATION_TYPES` member
    populates `resolved_with`; a target under a type OUTSIDE that set
    (`related_to`, on the same Decision) does not (design.md Decision 4)."""
    layout = _workspace(tmp_path)
    _write_doc(
        layout.bundle_dir / "decisions" / "a.md",
        relations=[("decisions/b", relation_type), ("decisions/c", "related_to")],
    )
    _write_doc(layout.bundle_dir / "decisions" / "b.md")
    _write_doc(layout.bundle_dir / "decisions" / "c.md")

    result = revisions.load_decisions(
        layout, include_confidential=False, local_exemption=False
    )

    by_id = {decision.concept_id: decision for decision in result.decisions}
    assert by_id["decisions/a"].resolved_with == frozenset({"decisions/b"})


# ---------------------------------------------------------------------------
# resolve_decision_dates
# ---------------------------------------------------------------------------


def test_resolve_decision_dates_covers_the_date_state_table(tmp_path: Path) -> None:
    """design.md Decision 3's four-case rule, one Decision per case (three
    sub-cases for `missing`): no reached Source -> `none-reached`; a
    reached Source with an absent, or malformed, `event_date`, or a
    dangling reference with no file at all -> `missing`; two reached
    Sources with distinct valid dates -> `multiple`; one valid date,
    reached directly or through an intermediate concept -> `dated`."""
    layout = _workspace(tmp_path)
    bundle = layout.bundle_dir

    _write_doc(bundle / "decisions" / "none.md", provenance=[])

    _write_doc(
        bundle / "decisions" / "missing-absent.md", provenance=["sources/no-date"]
    )
    _write_doc(bundle / "sources" / "no-date.md", type_="Source")

    _write_doc(
        bundle / "decisions" / "missing-malformed.md", provenance=["sources/bad-date"]
    )
    _write_doc(
        bundle / "sources" / "bad-date.md", type_="Source", event_date="not-a-date"
    )

    _write_doc(
        bundle / "decisions" / "missing-dangling.md", provenance=["sources/gone"]
    )
    # `sources/gone.md` is deliberately never created.

    _write_doc(
        bundle / "decisions" / "multiple.md",
        provenance=["sources/d1", "sources/d2"],
    )
    _write_doc(bundle / "sources" / "d1.md", type_="Source", event_date="2026-01-01")
    _write_doc(bundle / "sources" / "d2.md", type_="Source", event_date="2026-02-02")

    _write_doc(bundle / "decisions" / "dated-direct.md", provenance=["sources/d3"])
    _write_doc(bundle / "sources" / "d3.md", type_="Source", event_date="2026-03-03")

    _write_doc(
        bundle / "decisions" / "dated-intermediate.md", provenance=["concepts/mid"]
    )
    _write_doc(
        bundle / "concepts" / "mid.md", type_="Concept", provenance=["sources/d4"]
    )
    _write_doc(bundle / "sources" / "d4.md", type_="Source", event_date="2026-04-04")

    decision_ids = [
        "decisions/none",
        "decisions/missing-absent",
        "decisions/missing-malformed",
        "decisions/missing-dangling",
        "decisions/multiple",
        "decisions/dated-direct",
        "decisions/dated-intermediate",
    ]

    result = revisions.resolve_decision_dates(layout, decision_ids)

    assert result["decisions/none"] == DecisionDate(value=None, state="none-reached")
    assert result["decisions/missing-absent"] == DecisionDate(
        value=None, state="missing"
    )
    assert result["decisions/missing-malformed"] == DecisionDate(
        value=None, state="missing"
    )
    assert result["decisions/missing-dangling"] == DecisionDate(
        value=None, state="missing"
    )
    assert result["decisions/multiple"] == DecisionDate(value=None, state="multiple")
    assert result["decisions/dated-direct"] == DecisionDate(
        value=date(2026, 3, 3), state="dated"
    )
    assert result["decisions/dated-intermediate"] == DecisionDate(
        value=date(2026, 4, 4), state="dated"
    )


# ---------------------------------------------------------------------------
# read_decision_vectors
# ---------------------------------------------------------------------------

_MODEL = "bge-m3"


def test_read_decision_vectors_store_absent_yields_absent_and_creates_no_vectors_db(
    tmp_path: Path,
) -> None:
    """An absent `.openkos/vectors.db` yields `store="absent"`, and the read
    NEVER creates the file or its parent directory as a side effect
    (design.md Decision B1: "a read verb must never create a derived
    store") -- kills `open_vector_store` being called before the
    `vector_store_is_empty` probe."""
    layout = _workspace(tmp_path)
    assert not layout.vectors_db_path.exists()

    coverage = revisions.read_decision_vectors(
        layout, ["decisions/a"], {}, embedding_model=_MODEL
    )

    assert coverage == revisions.VectorCoverage(
        store="absent", vectors={}, missing=frozenset(), stale=frozenset()
    )
    assert not layout.vectors_db_path.exists()
    assert not layout.vectors_db_path.parent.exists()


def test_read_decision_vectors_sqlite_vec_unavailable_yields_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A store that exists and is non-empty, but whose `sqlite-vec` load
    fails when reopened, also degrades to `store="absent"` -- the SAME
    whole-run remedy as an absent store (design.md Decision B1's table)."""
    layout = _workspace(tmp_path)
    with vectorstore.open_vector_store(layout.vectors_db_path) as store:
        store.upsert("decisions/a", [0.1] * EMBED_DIM, "hash-a")
        store.write_model_tag(reindex.embedding_tag(_MODEL))
        store.commit()

    def _raise_vec_unavailable(path: Path) -> vectorstore.VectorStoreDB:
        raise vectorstore.VecUnavailable("sqlite-vec unavailable")

    monkeypatch.setattr(revisions, "open_vector_store", _raise_vec_unavailable)

    coverage = revisions.read_decision_vectors(
        layout, ["decisions/a"], {}, embedding_model=_MODEL
    )

    assert coverage.store == "absent"


@pytest.mark.parametrize("stored_tag", [None, "other-model#chunk-v1"])
def test_read_decision_vectors_model_tag_mismatch_or_missing_yields_model_mismatch(
    tmp_path: Path, stored_tag: str | None
) -> None:
    """A stored `embedding_model` tag of `None` (never written) or one that
    differs from `embedding_tag(cfg.embedding_model)` both yield
    `store="model-mismatch"` -- kills the tag compared without its
    `#chunk-v1` composition suffix."""
    layout = _workspace(tmp_path)
    with vectorstore.open_vector_store(layout.vectors_db_path) as store:
        store.upsert("decisions/a", [0.1] * EMBED_DIM, "hash-a")
        if stored_tag is not None:
            store.write_model_tag(stored_tag)
        store.commit()

    coverage = revisions.read_decision_vectors(
        layout, ["decisions/a"], {}, embedding_model=_MODEL
    )

    assert coverage.store == "model-mismatch"


def test_read_decision_vectors_per_decision_missing_and_stale(tmp_path: Path) -> None:
    """A Decision with no `doc_vectors` row is in `coverage.missing`; a
    Decision whose stored `content_hash` differs from the current file
    bytes' hash is in `coverage.stale`; both are absent from
    `coverage.vectors` -- kills `==` swapped to `!=` on the hash compare."""
    layout = _workspace(tmp_path)
    fresh_bytes = b"the current fresh bytes"
    stale_bytes = b"the current, edited-since bytes"
    with vectorstore.open_vector_store(layout.vectors_db_path) as store:
        store.upsert("decisions/fresh", [0.1] * EMBED_DIM, content_hash(fresh_bytes))
        store.upsert("decisions/stale", [0.2] * EMBED_DIM, "a-now-outdated-hash")
        store.write_model_tag(reindex.embedding_tag(_MODEL))
        store.commit()

    coverage = revisions.read_decision_vectors(
        layout,
        ["decisions/fresh", "decisions/stale", "decisions/never-embedded"],
        {"decisions/fresh": fresh_bytes, "decisions/stale": stale_bytes},
        embedding_model=_MODEL,
    )

    assert coverage.store == "ok"
    assert set(coverage.vectors) == {"decisions/fresh"}
    assert coverage.missing == frozenset({"decisions/never-embedded"})
    assert coverage.stale == frozenset({"decisions/stale"})


def test_read_decision_vectors_confidential_interaction(tmp_path: Path) -> None:
    """A confidential Decision excluded by `load_decisions` (no flag) never
    reaches `read_decision_vectors`'s input at all; made eligible with
    `include_confidential=True` and no stored vector, it lands in
    `coverage.missing` (never embedded on its behalf); with the flag AND a
    current stored vector, it is paired (design.md Decision B2)."""
    layout = _workspace(tmp_path)
    doc_path = layout.bundle_dir / "decisions" / "confidential.md"
    _write_doc(doc_path, sensitivity="confidential")
    current_bytes = doc_path.read_bytes()

    excluded = revisions.load_decisions(
        layout, include_confidential=False, local_exemption=False
    )
    assert excluded.decisions == ()

    included = revisions.load_decisions(
        layout, include_confidential=True, local_exemption=False
    )
    decision_ids = [decision.concept_id for decision in included.decisions]

    # A store that exists and is model-tag-current, but holds no row for
    # this Decision yet -- otherwise the whole run would degrade to
    # `store="absent"` before `missing`/`stale` are ever partitioned.
    with vectorstore.open_vector_store(layout.vectors_db_path) as store:
        store.upsert("decisions/unrelated", [0.9] * EMBED_DIM, "unrelated-hash")
        store.write_model_tag(reindex.embedding_tag(_MODEL))
        store.commit()

    coverage_no_vector = revisions.read_decision_vectors(
        layout,
        decision_ids,
        {"decisions/confidential": current_bytes},
        embedding_model=_MODEL,
    )
    assert coverage_no_vector.missing == frozenset({"decisions/confidential"})
    assert "decisions/confidential" not in coverage_no_vector.vectors

    with vectorstore.open_vector_store(layout.vectors_db_path) as store:
        store.upsert(
            "decisions/confidential", [0.3] * EMBED_DIM, content_hash(current_bytes)
        )
        store.write_model_tag(reindex.embedding_tag(_MODEL))
        store.commit()

    coverage_with_vector = revisions.read_decision_vectors(
        layout,
        decision_ids,
        {"decisions/confidential": current_bytes},
        embedding_model=_MODEL,
    )
    assert "decisions/confidential" in coverage_with_vector.vectors


# ---------------------------------------------------------------------------
# revision_input_digests (Slice P5b, design.md Decision 2)
# ---------------------------------------------------------------------------


def _bundle_snapshot(layout: config.WorkspaceLayout) -> dict[str, str]:
    """Test-local reimplementation of the service's own whole-bundle text
    snapshot -- the `Mapping[str, str]` shape (bundle-relative POSIX path,
    WITH the `.md` suffix, -> decoded text)
    `bundle_provenance.provenance_source_ancestors_many` expects, and the
    exact shape `revision_input_digests`'s `files` parameter takes. Small
    enough to duplicate here rather than reach into the service's own
    private `_bundle_text_snapshot` helper from a test."""
    files: dict[str, str] = {}
    for path in okf.iter_bundle_markdown(layout.bundle_dir):
        if path.name in okf.RESERVED_FILENAMES:
            continue
        files[path.relative_to(layout.bundle_dir).as_posix()] = path.read_text(
            encoding="utf-8"
        )
    return files


def test_revision_input_digests_covers_both_decisions_and_their_reached_sources(
    tmp_path: Path,
) -> None:
    """design.md Decision 2's input-digest table: ordinals 1-2 are each
    Decision's own `content_hash`; 3-4 are `sources-of:<id>` (a digest over
    the SORTED reached-Source id LIST, independent of whether those files
    exist); 5+ are every reached Source's `content_hash`, sorted and
    deduped across both sides -- and a dangling `sources/gone` reference
    (no file behind it) contributes NO content-hash row of its own, while
    still being counted in its side's `sources-of:` id-list digest."""
    layout = _workspace(tmp_path)
    _write_doc(
        layout.bundle_dir / "decisions" / "a.md",
        provenance=["sources/shared", "sources/only-a"],
        body="Body A.",
    )
    _write_doc(
        layout.bundle_dir / "decisions" / "b.md",
        provenance=["sources/shared", "sources/only-b", "sources/gone"],
        body="Body B.",
    )
    _write_doc(layout.bundle_dir / "sources" / "shared.md", type_="Source")
    _write_doc(layout.bundle_dir / "sources" / "only-a.md", type_="Source")
    _write_doc(layout.bundle_dir / "sources" / "only-b.md", type_="Source")
    # `sources/gone.md` is deliberately never created.

    files = _bundle_snapshot(layout)
    digests = revisions.revision_input_digests(
        layout, files, ("decisions/a", "decisions/b")
    )

    # `sources/only-b` is reached ONLY by `decisions/b`'s side -- present in
    # the union only because BOTH sides' reached sets are combined, not just
    # `decisions/a`'s (the ordinal-1 side).
    assert [digest.input_ref for digest in digests] == [
        "decisions/a",
        "decisions/b",
        "sources-of:decisions/a",
        "sources-of:decisions/b",
        "sources/only-a",
        "sources/only-b",
        "sources/shared",
    ]
    assert len(digests) == 7  # gone.md contributes no row of its own

    by_ref = {digest.input_ref: digest.digest for digest in digests}
    assert by_ref["decisions/a"] == content_hash(
        (layout.bundle_dir / "decisions" / "a.md").read_bytes()
    )
    assert by_ref["sources/shared"] == content_hash(
        (layout.bundle_dir / "sources" / "shared.md").read_bytes()
    )
    assert by_ref["sources/only-b"] == content_hash(
        (layout.bundle_dir / "sources" / "only-b.md").read_bytes()
    )
    assert (
        by_ref["sources-of:decisions/a"]
        == hashlib.sha256(
            "\n".join(sorted(["sources/only-a", "sources/shared"])).encode("utf-8")
        ).hexdigest()
    )
    assert (
        by_ref["sources-of:decisions/b"]
        == hashlib.sha256(
            "\n".join(
                sorted(["sources/gone", "sources/only-b", "sources/shared"])
            ).encode("utf-8")
        ).hexdigest()
    )


def test_revision_input_digests_a_missing_source_yields_one_fewer_row_than_if_it_existed(
    tmp_path: Path,
) -> None:
    """The digest tuple genuinely differs in LENGTH depending on whether a
    reached Source's file exists -- proving the row is dropped, not merely
    that its ref string differs. This is what lets a later `is_fresh` strict
    equality check catch an input that BECAME unreadable (P5b.3)."""
    layout = _workspace(tmp_path)
    _write_doc(layout.bundle_dir / "decisions" / "a.md", provenance=["sources/x"])
    _write_doc(layout.bundle_dir / "decisions" / "b.md", provenance=[])

    files = _bundle_snapshot(layout)
    without_source = revisions.revision_input_digests(
        layout, files, ("decisions/a", "decisions/b")
    )

    _write_doc(layout.bundle_dir / "sources" / "x.md", type_="Source")
    files = _bundle_snapshot(layout)
    with_source = revisions.revision_input_digests(
        layout, files, ("decisions/a", "decisions/b")
    )

    assert len(with_source) == len(without_source) + 1
    assert "sources/x" not in {digest.input_ref for digest in without_source}
    assert "sources/x" in {digest.input_ref for digest in with_source}


# ---------------------------------------------------------------------------
# is_fresh (Slice P5b, design.md Decision 2's strict freshness rule)
# ---------------------------------------------------------------------------

_PAIR = ("decisions/a", "decisions/b")


def _finding(
    layout: config.WorkspaceLayout,
    files: dict[str, str],
    *,
    pair_ids: tuple[str, str] = _PAIR,
    prompt_version: str | None = None,
    include_confidential: bool = False,
    digests: tuple[revision_findings.InputDigest, ...] | None = None,
) -> revision_findings.RevisionFinding:
    """One `RevisionFinding` ready to persist. `prompt_version` defaults to
    the REAL `JUDGE_PROMPT_VERSION` and `digests` to the REAL current digest
    tuple (via `revision_input_digests`), so an unmodified call is, by
    construction, exactly the "fresh" case `is_fresh` should accept."""
    return revision_findings.RevisionFinding(
        pair_ids=pair_ids,
        verdict="reaffirms",
        confidence=0.9,
        rationale="stub rationale",
        quotes=(None, None),
        dates=(None, None),
        date_states=("none-reached", "none-reached"),
        include_confidential=include_confidential,
        prompt_version=prompt_version or decision_revision.JUDGE_PROMPT_VERSION,
        input_digests=(
            digests
            if digests is not None
            else revisions.revision_input_digests(layout, files, pair_ids)
        ),
    )


def _record(
    layout: config.WorkspaceLayout, batch: list[revision_findings.RevisionFinding]
) -> None:
    conn = derived.open_derived_connection(layout.findings_db_path)
    try:
        revision_findings.record_revision_findings(conn, batch)
    finally:
        conn.close()


def _read_back(
    layout: config.WorkspaceLayout, pair_ids: tuple[str, str]
) -> revision_findings.RevisionFinding:
    conn = derived.open_derived_connection(layout.findings_db_path)
    try:
        by_pair = {
            row.pair_ids: row for row in revision_findings.open_revision_findings(conn)
        }
    finally:
        conn.close()
    return by_pair[pair_ids]


def test_is_fresh_true_when_prompt_version_confidential_and_digests_all_match(
    tmp_path: Path,
) -> None:
    layout = _workspace(tmp_path)
    _write_doc(layout.bundle_dir / "decisions" / "a.md")
    _write_doc(layout.bundle_dir / "decisions" / "b.md")
    files = _bundle_snapshot(layout)
    _record(layout, [_finding(layout, files, include_confidential=True)])

    stored = _read_back(layout, _PAIR)

    assert revisions.is_fresh(layout, stored, effective_confidential=True) is True


def test_is_fresh_false_for_a_superseded_non_latest_row(tmp_path: Path) -> None:
    """Recording a NEW finding for the same pair REPLACEs the old row
    (`record_revision_findings`'s own contract). The caller's stale
    in-memory copy of the OLD row is therefore no longer the pair's CURRENT
    row, and `is_fresh` must say so rather than trusting the object it was
    handed -- design.md Decision 2's condition 1, "the latest row for its
    pair key"."""
    layout = _workspace(tmp_path)
    _write_doc(layout.bundle_dir / "decisions" / "a.md")
    _write_doc(layout.bundle_dir / "decisions" / "b.md")
    files = _bundle_snapshot(layout)
    old_finding = _finding(layout, files, prompt_version="v-old")
    _record(layout, [old_finding])

    newer_finding = _finding(layout, files, prompt_version="v-old")
    newer_finding = revision_findings.RevisionFinding(
        pair_ids=newer_finding.pair_ids,
        verdict=newer_finding.verdict,
        confidence=0.5,
        rationale="a newer judgment superseded the old one",
        quotes=newer_finding.quotes,
        dates=newer_finding.dates,
        date_states=newer_finding.date_states,
        include_confidential=newer_finding.include_confidential,
        prompt_version=decision_revision.JUDGE_PROMPT_VERSION,
        input_digests=newer_finding.input_digests,
    )
    _record(layout, [newer_finding])

    assert revisions.is_fresh(layout, old_finding) is False
    assert revisions.is_fresh(layout, newer_finding) is True


def test_is_fresh_false_on_prompt_version_mismatch(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    _write_doc(layout.bundle_dir / "decisions" / "a.md")
    _write_doc(layout.bundle_dir / "decisions" / "b.md")
    files = _bundle_snapshot(layout)
    _record(layout, [_finding(layout, files, prompt_version="an-old-prompt-version")])

    stored = _read_back(layout, _PAIR)

    assert revisions.is_fresh(layout, stored) is False


def test_is_fresh_false_on_include_confidential_mismatch(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    _write_doc(layout.bundle_dir / "decisions" / "a.md")
    _write_doc(layout.bundle_dir / "decisions" / "b.md")
    files = _bundle_snapshot(layout)
    _record(layout, [_finding(layout, files, include_confidential=False)])

    stored = _read_back(layout, _PAIR)

    assert revisions.is_fresh(layout, stored, effective_confidential=True) is False
    assert revisions.is_fresh(layout, stored, effective_confidential=False) is True


def test_is_fresh_false_when_a_stored_input_became_unreadable(tmp_path: Path) -> None:
    """A Source reached at record time, later deleted, leaves the STORED
    digest tuple with one more row than the RECOMPUTED one. Strict equality
    catches this immediately -- design.md Decision 2: "an input that cannot
    currently be read must never count as unchanged", explicitly contrasted
    with `findings._is_stale`'s lenient `None`-means-unchanged rule, which
    must NOT apply here."""
    layout = _workspace(tmp_path)
    _write_doc(layout.bundle_dir / "decisions" / "a.md", provenance=["sources/x"])
    _write_doc(layout.bundle_dir / "decisions" / "b.md")
    _write_doc(layout.bundle_dir / "sources" / "x.md", type_="Source")
    files = _bundle_snapshot(layout)
    _record(layout, [_finding(layout, files)])
    (layout.bundle_dir / "sources" / "x.md").unlink()

    stored = _read_back(layout, _PAIR)

    assert revisions.is_fresh(layout, stored) is False


# ---------------------------------------------------------------------------
# plan_revisions (Slice P5b, design.md's Phase B re-plan Data flow)
# ---------------------------------------------------------------------------


def _embed(dim_index: int) -> list[float]:
    """An `EMBED_DIM`-length unit vector with a single `1.0` at
    `dim_index`, else `0.0` -- two vectors sharing a `dim_index` have
    `cosine_similarity` exactly `1.0`; two with DIFFERENT indices have
    exactly `0.0`, cleanly on either side of
    `EMBEDDING_SIMILARITY_THRESHOLD` (0.65) without needing real
    embeddings."""
    vector = [0.0] * EMBED_DIM
    vector[dim_index] = 1.0
    return vector


def _seed_decision_and_vector(
    layout: config.WorkspaceLayout,
    concept_id: str,
    dim_index: int,
    **doc_kwargs: object,
) -> None:
    """Write one Decision `.md` file and seed its CURRENT document vector in
    `.openkos/vectors.db`, as if `openkos reindex` had just run over it --
    the fixture shape every `plan_revisions` test needs so its Decisions are
    both loadable (`load_decisions`) and vector-eligible
    (`read_decision_vectors`)."""
    path = layout.bundle_dir / f"{concept_id}.md"
    _write_doc(path, **doc_kwargs)  # type: ignore[arg-type]
    with vectorstore.open_vector_store(layout.vectors_db_path) as store:
        store.upsert(concept_id, _embed(dim_index), content_hash(path.read_bytes()))
        store.write_model_tag(reindex.embedding_tag(_MODEL))
        store.commit()


def _record_current_finding(
    layout: config.WorkspaceLayout,
    files: dict[str, str],
    pair_ids: tuple[str, str],
) -> None:
    """Persist a finding for `pair_ids` whose stored digests are the REAL
    current ones (via `revision_input_digests`) -- the "this pair was
    already judged and nothing has changed since" fixture shape every
    served-vs-to_judge test starts from."""
    digests = revisions.revision_input_digests(layout, files, pair_ids)
    _record(
        layout,
        [
            revision_findings.RevisionFinding(
                pair_ids=pair_ids,
                verdict="reaffirms",
                confidence=0.9,
                rationale="stub rationale",
                quotes=(None, None),
                dates=(None, None),
                date_states=("none-reached", "none-reached"),
                include_confidential=False,
                prompt_version=decision_revision.JUDGE_PROMPT_VERSION,
                input_digests=digests,
            )
        ],
    )


def test_plan_revisions_serves_unchanged_findings_with_zero_llm_calls(
    tmp_path: Path,
) -> None:
    """A bundle whose Decisions, dates, and vectors are unchanged since the
    last run: the previously-judged pair is served, and NONE appear in
    `to_judge` -- `plan_revisions` itself has no `llm` parameter at all, so
    "zero LLM calls" is an architectural guarantee, not a runtime count."""
    layout = _workspace(tmp_path)
    _seed_decision_and_vector(layout, "decisions/a", 0)
    _seed_decision_and_vector(layout, "decisions/b", 0)

    decisions = revisions.load_decisions(
        layout, include_confidential=False, local_exemption=False
    )
    files = _bundle_snapshot(layout)
    _record_current_finding(layout, files, ("decisions/a", "decisions/b"))

    plan = revisions.plan_revisions(
        layout,
        decisions,
        embedding_model=_MODEL,
        effective_confidential=False,
        fresh=False,
    )

    assert {finding.pair_ids for finding in plan.served} == {
        ("decisions/a", "decisions/b")
    }
    assert plan.to_judge == ()


def test_plan_revisions_edited_decision_body_rejudges_only_its_own_pairs(
    tmp_path: Path,
) -> None:
    """Editing one Decision's body moves ONLY pairs containing it from
    served to `to_judge`; every other persisted finding stays served."""
    layout = _workspace(tmp_path)
    for concept_id in ("decisions/a", "decisions/b", "decisions/c"):
        _seed_decision_and_vector(layout, concept_id, 0, body=f"Body {concept_id}.")

    decisions = revisions.load_decisions(
        layout, include_confidential=False, local_exemption=False
    )
    files = _bundle_snapshot(layout)
    for pair in (
        ("decisions/a", "decisions/b"),
        ("decisions/a", "decisions/c"),
        ("decisions/b", "decisions/c"),
    ):
        _record_current_finding(layout, files, pair)

    # Edit decisions/a's body, then simulate a reindex: re-upsert its
    # vector with the SAME direction but the NEW content hash, so `a` stays
    # a current candidate and only its JUDGE FINDINGS go stale.
    a_path = layout.bundle_dir / "decisions" / "a.md"
    _write_doc(a_path, body="Body decisions/a, edited.")
    with vectorstore.open_vector_store(layout.vectors_db_path) as store:
        store.upsert("decisions/a", _embed(0), content_hash(a_path.read_bytes()))
        store.write_model_tag(reindex.embedding_tag(_MODEL))
        store.commit()

    plan = revisions.plan_revisions(
        layout,
        decisions,
        embedding_model=_MODEL,
        effective_confidential=False,
        fresh=False,
    )

    assert {candidate.pair_ids for candidate in plan.to_judge} == {
        ("decisions/a", "decisions/b"),
        ("decisions/a", "decisions/c"),
    }
    assert {finding.pair_ids for finding in plan.served} == {
        ("decisions/b", "decisions/c")
    }


def test_plan_revisions_edited_source_event_date_rejudges_only_affected_pairs(
    tmp_path: Path,
) -> None:
    """Editing one Source's `event_date` moves ONLY pairs whose Decisions
    reach that Source to `to_judge` -- caught by the `sources-of:<id>`
    digest rows' reached-Source content-hash rows (ordinal 5+), never by
    ordinals 1-2 (neither Decision's own body changes)."""
    layout = _workspace(tmp_path)
    _seed_decision_and_vector(layout, "decisions/a", 0, provenance=["sources/s1"])
    _seed_decision_and_vector(layout, "decisions/b", 0, provenance=["sources/s3"])
    _seed_decision_and_vector(layout, "decisions/c", 1, provenance=["sources/s2"])
    _seed_decision_and_vector(layout, "decisions/d", 1, provenance=["sources/s4"])
    _write_doc(
        layout.bundle_dir / "sources" / "s1.md", type_="Source", event_date="2026-01-01"
    )
    _write_doc(
        layout.bundle_dir / "sources" / "s2.md", type_="Source", event_date="2026-02-02"
    )
    _write_doc(
        layout.bundle_dir / "sources" / "s3.md", type_="Source", event_date="2026-03-03"
    )
    _write_doc(
        layout.bundle_dir / "sources" / "s4.md", type_="Source", event_date="2026-04-04"
    )

    decisions = revisions.load_decisions(
        layout, include_confidential=False, local_exemption=False
    )
    files = _bundle_snapshot(layout)
    _record_current_finding(layout, files, ("decisions/a", "decisions/b"))
    _record_current_finding(layout, files, ("decisions/c", "decisions/d"))

    _write_doc(
        layout.bundle_dir / "sources" / "s1.md", type_="Source", event_date="2026-06-06"
    )

    plan = revisions.plan_revisions(
        layout,
        decisions,
        embedding_model=_MODEL,
        effective_confidential=False,
        fresh=False,
    )

    assert {candidate.pair_ids for candidate in plan.to_judge} == {
        ("decisions/a", "decisions/b")
    }
    assert {finding.pair_ids for finding in plan.served} == {
        ("decisions/c", "decisions/d")
    }


def test_plan_revisions_provenance_path_change_marks_stale(tmp_path: Path) -> None:
    """Rewiring an INTERMEDIATE concept so a Decision now reaches a
    DIFFERENT Source, with neither Decision's own body edited, moves the
    affected pair to `to_judge` -- caught by the `sources-of:<id>` digest
    rows (ordinals 3-4), not by rows 1-2."""
    layout = _workspace(tmp_path)
    _seed_decision_and_vector(layout, "decisions/a", 0, provenance=["concepts/mid"])
    _seed_decision_and_vector(layout, "decisions/b", 0, provenance=["sources/s2"])
    _write_doc(
        layout.bundle_dir / "concepts" / "mid.md",
        type_="Concept",
        provenance=["sources/s1"],
    )
    _write_doc(layout.bundle_dir / "sources" / "s1.md", type_="Source")
    _write_doc(layout.bundle_dir / "sources" / "s2.md", type_="Source")
    _write_doc(layout.bundle_dir / "sources" / "s3.md", type_="Source")

    decisions = revisions.load_decisions(
        layout, include_confidential=False, local_exemption=False
    )
    files = _bundle_snapshot(layout)
    _record_current_finding(layout, files, ("decisions/a", "decisions/b"))

    # Rewire the INTERMEDIATE concept only -- decisions/a.md itself, and
    # its vector, are untouched.
    _write_doc(
        layout.bundle_dir / "concepts" / "mid.md",
        type_="Concept",
        provenance=["sources/s3"],
    )

    plan = revisions.plan_revisions(
        layout,
        decisions,
        embedding_model=_MODEL,
        effective_confidential=False,
        fresh=False,
    )

    assert {candidate.pair_ids for candidate in plan.to_judge} == {
        ("decisions/a", "decisions/b")
    }
    assert plan.served == ()


def test_plan_revisions_fresh_flag_bypasses_serving(tmp_path: Path) -> None:
    """`fresh=True` sends EVERY eligible candidate to `to_judge` regardless
    of persisted findings."""
    layout = _workspace(tmp_path)
    _seed_decision_and_vector(layout, "decisions/a", 0)
    _seed_decision_and_vector(layout, "decisions/b", 0)

    decisions = revisions.load_decisions(
        layout, include_confidential=False, local_exemption=False
    )
    files = _bundle_snapshot(layout)
    _record_current_finding(layout, files, ("decisions/a", "decisions/b"))

    plan = revisions.plan_revisions(
        layout,
        decisions,
        embedding_model=_MODEL,
        effective_confidential=False,
        fresh=True,
    )

    assert plan.served == ()
    assert {candidate.pair_ids for candidate in plan.to_judge} == {
        ("decisions/a", "decisions/b")
    }
