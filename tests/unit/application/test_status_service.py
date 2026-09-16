"""Direct unit tests for `openkos.application.status`: the `status`
command's read core, extracted out of `cli/main.py` (issue #995, PR 3 of
MVP 3's "prerequisite zero" -- the read verbs need an application service
before an MCP adapter can be a thin layer instead of a second
implementation).

Mirrors `test_query_service.py`/`test_pending.py`'s posture: these exercise
`build_status_report` directly against a real (tmp-path) workspace, never a
CLI invocation. `tests/unit/cli/test_status.py` stays the black-box,
rendered-output contract; this file is the one that can see the RAW facts
the CLI adapter later formats, in particular the three-way `recent_entries`
state a rendered string collapses into two visible branches ("unavailable"
vs "No activity recorded yet.").
"""

from pathlib import Path

import pytest

from openkos import config
from openkos.application import status as status_service
from openkos.state import derived, findings


def _workspace(tmp_path: Path) -> config.WorkspaceLayout:
    """A workspace root with a real (empty) `bundle/` directory -- unlike
    `test_pending.py`/`test_query_service.py`'s `_workspace`, `status`
    reads `bundle/log.md` directly (not through a CLI `init`), so the
    directory must exist before `build_status_report` is called on it."""
    config.write_config(tmp_path)
    layout = config.WorkspaceLayout(tmp_path)
    layout.bundle_dir.mkdir(parents=True, exist_ok=True)
    return layout


# --- recent_entries: the three-way state ---


def test_recent_entries_is_none_when_log_is_missing(tmp_path: Path) -> None:
    """No `log.md` on disk at all raises `FileNotFoundError` (an `OSError`
    subclass) from the `read_text` call -- caught the same way a malformed
    `log.md` is, degrading to `None` rather than propagating (D5, lenient
    degrade). `None` is a DISTINCT state from `()` (empty log): "the log
    could not be read/parsed" versus "the log has nothing in it yet", and
    the CLI adapter renders each with different wording.

    Exercises `read_status_overview`, not `build_status_report`:
    `recent_entries` moved there in the partial-output-regression fix
    (review findings R3-partial-output-on-read-failure /
    R4-status-no-partial-output) -- it is one of the two cheap,
    already-guarded reads the CLI renders BEFORE calling
    `build_status_report`."""
    layout = _workspace(tmp_path)

    overview = status_service.read_status_overview(layout)

    assert overview.recent_entries is None


def test_recent_entries_is_none_when_log_is_malformed(tmp_path: Path) -> None:
    """A `## ` section header with no blank line after it makes
    `bundle_log.read_recent_entries` raise `ValueError`
    (`_SECTION_HEADER_RE` not matching the chunk) -- caught the same
    `except (OSError, ValueError)` as the missing-file case above, and
    degrading to the same `None`."""
    layout = _workspace(tmp_path)
    (layout.bundle_dir / "log.md").write_text(
        "# Directory Update Log\n\n## 2026-07-16\n* no blank line above\n",
        encoding="utf-8",
    )

    overview = status_service.read_status_overview(layout)

    assert overview.recent_entries is None


def test_recent_entries_is_empty_tuple_when_log_has_no_sections(
    tmp_path: Path,
) -> None:
    """A readable `log.md` with a header but no dated sections yet is the
    genuinely-empty state: `read_recent_entries` returns `[]`, which this
    module reports as `()` -- distinct from the unreadable `None` above,
    even though both render as "nothing to show" -- the CLI adapter's
    branch selects different wording for each."""
    layout = _workspace(tmp_path)
    (layout.bundle_dir / "log.md").write_text(
        "# Directory Update Log\n", encoding="utf-8"
    )

    overview = status_service.read_status_overview(layout)

    assert overview.recent_entries == ()


def test_recent_entries_is_populated_when_log_has_entries(tmp_path: Path) -> None:
    """A readable `log.md` with dated bullets round-trips through
    `bundle_log.read_recent_entries` into a non-empty tuple, newest-first."""
    layout = _workspace(tmp_path)
    (layout.bundle_dir / "log.md").write_text(
        "# Directory Update Log\n\n## 2026-07-16\n\n* Did a thing.\n",
        encoding="utf-8",
    )

    overview = status_service.read_status_overview(layout)

    assert overview.recent_entries is not None
    assert len(overview.recent_entries) == 1
    assert overview.recent_entries[0].date == "2026-07-16"
    assert overview.recent_entries[0].text == "Did a thing."


# --- edge_summary is None exactly when vectors_missing ---


def test_edge_summary_is_none_when_vectors_missing(tmp_path: Path) -> None:
    """A fresh workspace has no `vectors.db` on disk: `vectors_missing` is
    `True`, and the graph projection is never even opened, so
    `edge_summary` stays `None` -- the CLI adapter's own gate ("only when
    `vectors.db` is non-empty") must have a raw fact to gate on, not a
    tuple it has to reinterpret."""
    layout = _workspace(tmp_path)

    report = status_service.build_status_report(layout)

    assert report.vectors_missing is True
    assert report.edge_summary is None


def test_edge_summary_is_computed_when_vectors_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When `vectors_missing` is `False`, `build_status_report` builds the
    graph projection and reports a real `(total, typed)` tuple -- `(0, 0)`
    over an empty bundle, still a TUPLE, never `None`. `vector_store_is_empty`
    is monkeypatched directly (rather than writing a real, non-empty
    `vectors.db`) because `graph_edge_summary`/`build_graph` operate over
    `bundle_dir` on disk, entirely independent of the vector store -- the
    only thing this test needs to force is the boolean gate."""
    layout = _workspace(tmp_path)
    monkeypatch.setattr(status_service, "vector_store_is_empty", lambda path: False)

    report = status_service.build_status_report(layout)

    assert report.vectors_missing is False
    assert isinstance(report.edge_summary, tuple)
    assert report.edge_summary == (0, 0)


# --- has_eligible_docs ---


def test_has_eligible_docs_false_on_empty_bundle(tmp_path: Path) -> None:
    """An empty bundle has no lint-eligible docs -- `has_eligible_docs` is
    `False`, the fact that gates the CLI's missing-vector-index
    needs-attention line (#386: reindexing an empty bundle is meaningless)."""
    layout = _workspace(tmp_path)

    report = status_service.build_status_report(layout)

    assert report.has_eligible_docs is False


def test_has_eligible_docs_true_when_a_concept_exists(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    concepts_dir = layout.bundle_dir / "concepts"
    concepts_dir.mkdir()
    (concepts_dir / "a.md").write_text(
        "---\ntype: Concept\ntitle: A\n---\nBody.\n", encoding="utf-8"
    )

    report = status_service.build_status_report(layout)

    assert report.has_eligible_docs is True


# --- no writes, no files created ---


def test_build_status_report_creates_no_files(tmp_path: Path) -> None:
    """`status` is read-only, Phase-A only -- `build_status_report` must
    never create `.openkos/findings.db`, `vectors.db`, or any other derived
    store as a side effect of reading. Mirrors
    `test_persisted_findings_absent_db_answers_empty_and_creates_nothing`'s
    guard in `test_pending.py`, extended to the whole report."""
    layout = _workspace(tmp_path)
    before = {p for p in tmp_path.rglob("*") if p.is_file()}

    status_service.build_status_report(layout)

    after = {p for p in tmp_path.rglob("*") if p.is_file()}
    assert after == before
    assert not layout.findings_db_path.exists()
    assert not layout.vectors_db_path.exists()


# --- promoted read predicates: contradiction_finding_counts ---


def test_contradiction_finding_counts_empty_workspace_is_zero_zero(
    tmp_path: Path,
) -> None:
    layout = _workspace(tmp_path)

    assert status_service.contradiction_finding_counts(layout) == (0, 0)


def _record_finding(
    layout: config.WorkspaceLayout,
    *,
    pair_ids: tuple[str, str],
    verdict: str = "contradicts",
    confidence: float = 0.91,
    input_digests: tuple[findings.InputDigest, ...] = (),
) -> None:
    """Persist one contradiction finding directly via
    `state.findings.record_findings` -- same posture as
    `tests/unit/cli/test_status.py::_record_finding`: no CLI writer, no LLM
    call. `verdict` defaults to the lowercase `Verdict.CONTRADICTS.value`
    shape curate's `_persist_findings` actually writes, matching the
    persisted-enum-value comparison `is_high_confidence_finding` applies."""
    conn = derived.open_derived_connection(layout.findings_db_path)
    try:
        findings.record_findings(
            conn,
            [
                findings.Finding(
                    pair_ids=pair_ids,
                    merged_absorbed_id=None,
                    verdict=verdict,
                    confidence=confidence,
                    rationale="Stub rationale.",
                    input_digests=input_digests,
                )
            ],
        )
    finally:
        conn.close()


def test_contradiction_finding_counts_distinguishes_open_from_stale(
    tmp_path: Path,
) -> None:
    """`(open, stale)` are two INDEPENDENT counts, not one total split two
    ways -- the previous version of this test only ever asserted `(0, 0)`,
    which a swapped return `(stale_count, open_count)` would still satisfy
    (review finding R3-service-tests-only-exercise-zero-paths). Two open
    findings plus one finding whose recorded input digest no longer
    matches the concept's current bytes (`beta`'s real content hash was
    never `"0"*64`) proves the two positions are not interchangeable: were
    they swapped, this would assert `(1, 2)` and fail."""
    layout = _workspace(tmp_path)
    concepts_dir = layout.bundle_dir / "concepts"
    concepts_dir.mkdir()
    for name in ("alpha", "beta", "gamma", "delta"):
        (concepts_dir / f"{name}.md").write_text(
            f"---\ntype: Concept\ntitle: {name.title()}\n---\nBody.\n",
            encoding="utf-8",
        )
    _record_finding(layout, pair_ids=("concepts/alpha", "concepts/beta"))
    _record_finding(layout, pair_ids=("concepts/gamma", "concepts/delta"))
    _record_finding(
        layout,
        pair_ids=("concepts/alpha", "concepts/gamma"),
        input_digests=(findings.InputDigest("concepts/beta", "0" * 64),),
    )

    assert status_service.contradiction_finding_counts(layout) == (2, 1)


# --- promoted read predicates: stale_index_names ---


def test_stale_index_names_absent_stores_report_nothing(tmp_path: Path) -> None:
    """Absence is deliberately NOT staleness (`stale_index_names`'s own
    contract, carried over verbatim from `cli.main._stale_index_names`): a
    freshly initialized workspace has no derived store at all, so a
    `reads` declaration naming both checkable stores still reports `()`."""
    layout = _workspace(tmp_path)

    assert status_service.stale_index_names(layout, reads=("fts", "graph")) == ()


def test_stale_index_names_reports_a_store_predating_the_manifest_hash(
    tmp_path: Path,
) -> None:
    """`stale_index_names` returns a non-empty subset when a declared store
    genuinely IS stale, not only the empty tuple -- the previous version of
    this suite never exercised that branch (review finding
    R3-service-tests-only-exercise-zero-paths). A present-but-empty
    `fts.db` has no `meta` table, so `stale_derived_stores`'s own contract
    ("Unreadable/corrupt store: reported, never raised") names it stale
    without raising."""
    layout = _workspace(tmp_path)
    layout.openkos_dir.mkdir(parents=True, exist_ok=True)
    layout.fts_db_path.write_bytes(b"")

    assert status_service.stale_index_names(layout, reads=("fts", "graph")) == ("fts",)


def test_stale_index_names_respects_the_reads_declaration(tmp_path: Path) -> None:
    """A name outside `reads` is never checked, even if a store by that
    name exists on disk AND is genuinely stale -- `reads` is the caller's
    declaration of what its OWN answer depends on (#436).

    The previous version of this test ran against a workspace with NO
    derived store at all, so the `if not stores: return ()` early return in
    `stale_index_names` satisfied the assertion without the intersection
    filter this docstring describes ever running (review finding
    R2-reads-docstring-overclaims). A present, stale `fts.db` -- which
    `test_stale_index_names_reports_a_store_predating_the_manifest_hash`
    above proves WOULD be reported if `fts` were declared -- makes the
    exclusion below a real assertion about the filter, not a vacuous one
    about an empty disk."""
    layout = _workspace(tmp_path)
    layout.openkos_dir.mkdir(parents=True, exist_ok=True)
    layout.fts_db_path.write_bytes(b"")

    assert status_service.stale_index_names(layout, reads=("graph",)) == ()
    assert status_service.stale_index_names(layout, reads=()) == ()


# --- StatusReport is frozen (tuples, not lists) ---


def test_status_report_fields_are_tuples_not_lists(tmp_path: Path) -> None:
    """The dataclass is frozen, so every collection field must itself be
    immutable -- a `list` field on a frozen dataclass is still mutable in
    place, which would defeat the point of freezing it.

    Nine collection fields, all guaranteed non-empty-or-not `tuple`s
    regardless of workspace state. `recent_entries` (now on the separate
    `StatusOverview`, since the partial-output-regression fix) and
    `edge_summary` are declared `... | None` instead, so they are covered
    by their own dedicated tests below/above rather than here (review
    finding R2-frozen-tuple-check-incomplete: both were previously omitted
    from any immutability check at all)."""
    layout = _workspace(tmp_path)

    report = status_service.build_status_report(layout)

    assert isinstance(report.dangling, tuple)
    assert isinstance(report.unextracted, tuple)
    assert isinstance(report.unjudged, tuple)
    assert isinstance(report.unevidenced, tuple)
    assert isinstance(report.staging_dropped, tuple)
    assert isinstance(report.sensitivity_findings, tuple)
    assert isinstance(report.dangling_provenance, tuple)
    assert isinstance(report.unbacked_provenance, tuple)
    assert isinstance(report.stale_indexes, tuple)


def test_status_overview_recent_entries_is_a_tuple_not_a_list(tmp_path: Path) -> None:
    """`StatusOverview.recent_entries` is declared `tuple[...] | None` --
    the frozen-fields sweep above only covers `StatusReport`'s nine
    always-tuple collections, and neither `recent_entries` nor
    `edge_summary` (both `| None`, and therefore the likeliest to be
    rebuilt from a mutable value without anyone noticing) had ANY
    immutability check before this (review finding
    R2-frozen-tuple-check-incomplete). A populated log proves the
    non-`None` branch really is a `tuple`, not just the `None` default,
    which an `is not None` check alone would not catch."""
    layout = _workspace(tmp_path)
    (layout.bundle_dir / "log.md").write_text(
        "# Directory Update Log\n\n## 2026-07-16\n\n* Did a thing.\n",
        encoding="utf-8",
    )

    overview = status_service.read_status_overview(layout)

    assert overview.recent_entries is not None
    assert isinstance(overview.recent_entries, tuple)
