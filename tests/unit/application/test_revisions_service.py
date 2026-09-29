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

from datetime import date
from pathlib import Path

import pytest

from openkos import config
from openkos.application import revisions
from openkos.llm.base import EMBED_DIM
from openkos.model.relations import RESOLUTION_RELATION_TYPES
from openkos.resolution.decision_revision import DecisionDate
from openkos.state import reindex, vectorstore
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
