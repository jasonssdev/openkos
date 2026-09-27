"""Unit tests for `openkos.application.next_action`'s open-contradiction tier
(pending-work design, Decision 6): the last tier in `_TIERS`, wired directly
to `.openkos/findings.db` and `bundle/.state/decisions/**` -- the two stores
Slice A/B1/B2 already ship, joined at read time by `decision_key_for`.

Follows `test_next.py`'s pattern: `_init_workspace` builds a bare workspace
via the real `init` command, `seed_vectors_db` marks embeddings present so
tier 1 never fires, and every fixture writes a persisted finding directly
via `state.findings.record_findings`/`bundle.decisions.write_decisions` --
no CLI writer is exercised here, mirroring Slice B1's own test posture."""

import ast
from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner

from openkos import config
from openkos.application import next_action
from openkos.bundle import decisions as bundle_decisions
from openkos.cli.main import app
from openkos.state import derived, findings

runner = CliRunner()


@pytest.fixture(autouse=True)
def _fts_index_present_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """#553 ranked a missing-FTS-index tier above the contradiction tier
    under test here; these fixtures never build an `fts.db`, so the signal
    reports present by default -- the same convention `seed_vectors_db`
    already applies to tier 1 (see `test_next.py`'s fixture of the same
    name)."""
    monkeypatch.setattr(
        "openkos.application.next_action.fts_index_present", lambda _path: True
    )


def _init_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0


def _record_finding(
    tmp_path: Path,
    *,
    pair_ids: tuple[str, str] = ("concepts/alpha", "concepts/beta"),
    merged_absorbed_id: str | None = None,
    verdict: str = "contradicts",
    confidence: float = 0.91,
    input_digests: tuple[findings.InputDigest, ...] = (),
) -> None:
    """Persist one finding directly via `state.findings.record_findings`
    (no CLI writer needed -- mirrors Slice A/B1's own test posture).

    `verdict` defaults to the lowercase `Verdict.CONTRADICTS.value` shape
    curate's `_persist_findings` actually writes (`verdict.verdict.value`)
    -- NOT the uppercase display rendering -- because #639's verdict filter
    compares against the persisted enum value, so a helper writing
    `"CONTRADICTS"` would silently test a shape the store never contains."""
    layout = config.WorkspaceLayout(tmp_path)
    conn = derived.open_derived_connection(layout.findings_db_path)
    try:
        findings.record_findings(
            conn,
            [
                findings.Finding(
                    pair_ids=pair_ids,
                    merged_absorbed_id=merged_absorbed_id,
                    verdict=verdict,
                    confidence=confidence,
                    rationale="Stub rationale.",
                    input_digests=input_digests,
                )
            ],
        )
    finally:
        conn.close()


def _decline(
    tmp_path: Path,
    *,
    pair_ids: tuple[str, str],
    merged_absorbed_id: str | None = None,
) -> None:
    """Write a `declined` decision record directly via
    `bundle.decisions.write_decisions` (no CLI writer needed)."""
    from datetime import UTC, datetime

    bundle_dir = tmp_path / "bundle"
    key = bundle_decisions.decision_key_for(pair_ids, merged_absorbed_id)
    bundle_decisions.write_decisions(
        pair_ids[0],
        bundle_dir,
        records=[
            bundle_decisions.DecisionRecord(
                decision_key=key,
                pair_ids=pair_ids,
                merged_absorbed_id=merged_absorbed_id,
                state="declined",
                decided_at=datetime.now(UTC).isoformat(),
            )
        ],
    )


def _write_concept(path: Path, *, title: str, body: str = "Body.") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntype: Concept\ntitle: {title}\n---\n{body}\n", encoding="utf-8"
    )


def test_open_contradiction_is_ranked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    """One open, non-stale, non-declined contradiction finding, with every
    higher-ranked tier clean, is what `next` recommends acting on
    (pending-work spec, Scenario "An open contradiction is ranked")."""
    _init_workspace(tmp_path, monkeypatch)
    seed_vectors_db(tmp_path)
    _write_concept(tmp_path / "bundle" / "concepts" / "alpha.md", title="Alpha")
    _write_concept(tmp_path / "bundle" / "concepts" / "beta.md", title="Beta")
    _record_finding(tmp_path)

    result = runner.invoke(app, ["next"])

    assert result.exit_code == 0
    assert "openkos contradictions" in result.stdout


def test_stale_or_declined_only_yields_none_action(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    """A bundle whose only persisted findings are stale or declined MUST
    yield `action is None`, and the rendered output MUST still carry
    `_STATUS_POINTER` and MUST NOT assert the bundle is clean (pending-work
    spec, Scenario "An unranked finding does not become a false
    all-clear"; design Decision 6 -- the honesty guard)."""
    _init_workspace(tmp_path, monkeypatch)
    seed_vectors_db(tmp_path)
    _write_concept(tmp_path / "bundle" / "concepts" / "alpha.md", title="Alpha")
    _write_concept(tmp_path / "bundle" / "concepts" / "beta.md", title="Beta")
    _write_concept(tmp_path / "bundle" / "concepts" / "gamma.md", title="Gamma")
    _write_concept(tmp_path / "bundle" / "concepts" / "delta.md", title="Delta")

    # Stale: stored digest deliberately does not match "alpha"'s current bytes.
    _record_finding(
        tmp_path,
        pair_ids=("concepts/alpha", "concepts/beta"),
        input_digests=(findings.InputDigest("concepts/alpha", "0" * 64),),
    )
    # Declined: an explicit operator decision hides it.
    _record_finding(tmp_path, pair_ids=("concepts/gamma", "concepts/delta"))
    _decline(tmp_path, pair_ids=("concepts/gamma", "concepts/delta"))

    result = next_action.next_action(config.WorkspaceLayout(tmp_path))

    assert result.action is None

    result_cli = runner.invoke(app, ["next"])
    assert result_cli.exit_code == 0
    assert next_action._STATUS_POINTER in result_cli.stdout
    assert "openkos contradictions" not in result_cli.stdout
    assert "clean" not in result_cli.stdout.lower()


def test_consistent_finding_is_not_ranked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    """A persisted `consistent` verdict -- curate records EVERY judged
    pair, not only contradictions -- is not open contradiction work, so the
    tier MUST NOT fire on it even at high confidence (#639: before the
    verdict filter, `next` told operators to review pairs already judged
    consistent, and nothing could clear the recommendation)."""
    _init_workspace(tmp_path, monkeypatch)
    seed_vectors_db(tmp_path)
    _write_concept(tmp_path / "bundle" / "concepts" / "alpha.md", title="Alpha")
    _write_concept(tmp_path / "bundle" / "concepts" / "beta.md", title="Beta")
    _record_finding(tmp_path, verdict="consistent", confidence=0.95)

    result = next_action.next_action(config.WorkspaceLayout(tmp_path))

    assert result.action is None

    result_cli = runner.invoke(app, ["next"])
    assert result_cli.exit_code == 0
    assert "openkos contradictions" not in result_cli.stdout


def test_low_confidence_contradiction_is_not_ranked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    """A `contradicts` verdict below `is_high_confidence_finding`'s 0.7
    threshold is not surfaced by `contradictions` or offered by
    `reconcile --from-findings`, so `next` ranking it would recommend work
    no other command shows (#639: same shared predicate, same cutoff)."""
    _init_workspace(tmp_path, monkeypatch)
    seed_vectors_db(tmp_path)
    _write_concept(tmp_path / "bundle" / "concepts" / "alpha.md", title="Alpha")
    _write_concept(tmp_path / "bundle" / "concepts" / "beta.md", title="Beta")
    _record_finding(tmp_path, verdict="contradicts", confidence=0.5)

    result = next_action.next_action(config.WorkspaceLayout(tmp_path))

    assert result.action is None


def test_contradiction_reason_line_names_a_contradiction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    """A high-confidence `contradicts` finding still fires the tier, and
    the reason line reads "an open contradiction finding" -- fixed wording,
    not the raw verdict value, because after #639's filter the verdict here
    is always `contradicts` and interpolating it would print the lowercase
    enum value ("an open contradicts finding")."""
    _init_workspace(tmp_path, monkeypatch)
    seed_vectors_db(tmp_path)
    _write_concept(tmp_path / "bundle" / "concepts" / "alpha.md", title="Alpha")
    _write_concept(tmp_path / "bundle" / "concepts" / "beta.md", title="Beta")
    _record_finding(tmp_path, verdict="contradicts", confidence=0.9)

    result_cli = runner.invoke(app, ["next"])

    assert result_cli.exit_code == 0
    assert "openkos contradictions" in result_cli.stdout
    assert (
        "an open contradiction finding is pending review (confidence: 0.90)"
        in result_cli.stdout
    )


def test_mixed_verdicts_rank_only_the_contradiction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    """With a `consistent` finding (0.95) and a `contradicts` finding (0.9)
    both persisted, the tier fires ON THE CONTRADICTION: the consistent
    pair is filtered out even though it sorts first and carries the higher
    confidence (#639)."""
    _init_workspace(tmp_path, monkeypatch)
    seed_vectors_db(tmp_path)
    _write_concept(tmp_path / "bundle" / "concepts" / "alpha.md", title="Alpha")
    _write_concept(tmp_path / "bundle" / "concepts" / "beta.md", title="Beta")
    _write_concept(tmp_path / "bundle" / "concepts" / "gamma.md", title="Gamma")
    _write_concept(tmp_path / "bundle" / "concepts" / "delta.md", title="Delta")
    _record_finding(
        tmp_path,
        pair_ids=("concepts/alpha", "concepts/beta"),
        verdict="consistent",
        confidence=0.95,
    )
    _record_finding(
        tmp_path,
        pair_ids=("concepts/gamma", "concepts/delta"),
        verdict="contradicts",
        confidence=0.9,
    )

    result_cli = runner.invoke(app, ["next"])

    assert result_cli.exit_code == 0
    assert "concepts/gamma <-> concepts/delta" in result_cli.stdout
    assert "concepts/alpha" not in result_cli.stdout


# --- mcp-read-surface Slice 7: structured `subjects` (design Decision 6) --


def test_every_next_action_and_declination_call_declares_subjects() -> None:
    """An AST scan of `next_action.py` asserts every `NextAction(` call site
    passes `subjects=` explicitly (never relying on the `None` default), and
    every `record_declination(` call passes `subjects=` explicitly (design
    Decision 6) -- so a new tier landing later without declaring `subjects=`
    fails this test rather than silently defaulting to `None` (undeclared,
    which `gate.disclose_pending` must withhold)."""
    repo_root = Path(__file__).resolve().parents[3]
    module_path = repo_root / "src" / "openkos" / "application" / "next_action.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"))

    missing: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name: str | None = None
        if isinstance(func, ast.Name):
            name = func.id
        elif isinstance(func, ast.Attribute):
            name = func.attr
        if name not in ("NextAction", "record_declination"):
            continue
        kw_names = {kw.arg for kw in node.keywords}
        if "subjects" not in kw_names:
            missing.append(f"{name}() at line {node.lineno}")

    assert not missing, f"call sites missing explicit subjects=: {missing}"


def _write_below_source_sensitivity_bundle(tmp_path: Path) -> None:
    sources_dir = tmp_path / "bundle" / "sources"
    sources_dir.mkdir(parents=True, exist_ok=True)
    (sources_dir / "a.md").write_text(
        "---\ntype: Source\ntitle: A\nresource: raw/a.txt\n"
        "sensitivity: confidential\n---\nBody.\n",
        encoding="utf-8",
    )
    concepts_dir = tmp_path / "bundle" / "concepts"
    concepts_dir.mkdir(parents=True, exist_ok=True)
    (concepts_dir / "derived.md").write_text(
        "---\ntype: Concept\ntitle: Derived\nsensitivity: public\n"
        "provenance:\n  - sources/a\n---\nBody.\n",
        encoding="utf-8",
    )


def _write_multi_source_uncovered_only_bundle(tmp_path: Path) -> None:
    sources_dir = tmp_path / "bundle" / "sources"
    sources_dir.mkdir(parents=True, exist_ok=True)
    (sources_dir / "a.md").write_text(
        "---\ntype: Source\ntitle: A\nresource: raw/a.txt\n"
        "sensitivity: public\n---\nBody.\n",
        encoding="utf-8",
    )
    (sources_dir / "c.md").write_text(
        "---\ntype: Source\ntitle: C\nresource: raw/c.txt\n"
        "sensitivity: confidential\n---\nBody.\n",
        encoding="utf-8",
    )
    concepts_dir = tmp_path / "bundle" / "concepts"
    concepts_dir.mkdir(parents=True, exist_ok=True)
    (concepts_dir / "from-c.md").write_text(
        "---\ntype: Concept\ntitle: From C\nsensitivity: confidential\n"
        "provenance:\n  - sources/c\n---\nBody.\n",
        encoding="utf-8",
    )
    (concepts_dir / "mixed.md").write_text(
        "---\ntype: Concept\ntitle: Mixed\nsensitivity: public\n"
        "provenance:\n  - sources/a\n  - concepts/from-c\n---\nBody.\n",
        encoding="utf-8",
    )


def _write_unextracted_source(
    tmp_path: Path, *, name: str = "notes", resource: str = "raw/notes.txt"
) -> None:
    sources_dir = tmp_path / "bundle" / "sources"
    sources_dir.mkdir(parents=True, exist_ok=True)
    resource_line = f"resource: {resource}\n" if resource else ""
    (sources_dir / f"{name}.md").write_text(
        f"---\ntype: Source\ntitle: {name.title()}\n{resource_line}"
        "extraction_status: failed\n---\nBody.\n",
        encoding="utf-8",
    )


def _write_unjudged_source(
    tmp_path: Path,
    *,
    name: str = "notes",
    resource: str = "raw/notes.txt",
    notice: str = "judge-selection-unavailable",
) -> None:
    sources_dir = tmp_path / "bundle" / "sources"
    sources_dir.mkdir(parents=True, exist_ok=True)
    resource_line = f"resource: {resource}\n" if resource else ""
    (sources_dir / f"{name}.md").write_text(
        f"---\ntype: Source\ntitle: {name.title()}\n{resource_line}"
        f"extraction_notice: {notice}\n---\nBody.\n",
        encoding="utf-8",
    )


def _write_doc(path: Path, *, title: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntype: Concept\ntitle: {title}\n---\n# {title}\n", encoding="utf-8"
    )


def test_per_tier_subjects_match_design_table(
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One assertion per tier from design Decision 6's table: bootstrap,
    missing-vector-index, missing-FTS, stale-indexes, duplicate-groups, and
    non-NFC give `subjects=()`; unextracted and unjudged (action and both
    declinations) give `(finding.concept_id,)`; below-source-sensitivity
    gives `(finding.concept_id, *finding.related_ids)`;
    multi-source-uncovered (action and declination) gives
    `(finding.concept_id, *finding.related_ids)`; open contradictions gives
    `finding.pair_ids`; `declination_subjects` stays index-aligned with
    `declinations`."""
    monkeypatch.setattr(
        "openkos.application.next_action.fts_index_present", lambda _path: True
    )

    # -- bootstrap: `subjects=()` --
    root = tmp_path_factory.mktemp("subjects_bootstrap")
    _init_workspace(root, monkeypatch)
    result = next_action.next_action(config.WorkspaceLayout(root))
    assert result.action is not None
    assert result.action.subjects == ()

    # -- missing vector index: `subjects=()` --
    root = tmp_path_factory.mktemp("subjects_missing_vector")
    _init_workspace(root, monkeypatch)
    _write_doc(root / "bundle" / "concepts" / "alpha.md", title="Alpha")
    result = next_action.next_action(config.WorkspaceLayout(root))
    assert result.action is not None
    assert result.action.command == "openkos reindex"
    assert result.action.subjects == ()

    # -- missing FTS index: `subjects=()` --
    root = tmp_path_factory.mktemp("subjects_missing_fts")
    _init_workspace(root, monkeypatch)
    monkeypatch.setattr(
        "openkos.application.next_action.vector_store_is_empty", lambda _path: False
    )
    monkeypatch.setattr(
        "openkos.application.next_action.fts_index_present", lambda _path: False
    )
    result = next_action.next_action(config.WorkspaceLayout(root))
    monkeypatch.setattr(
        "openkos.application.next_action.fts_index_present", lambda _path: True
    )
    assert result.action is not None
    assert result.action.command == "openkos reindex"
    assert "FTS" in result.action.reason
    assert result.action.subjects == ()

    # -- stale indexes: `subjects=()` --
    root = tmp_path_factory.mktemp("subjects_stale")
    _init_workspace(root, monkeypatch)
    from openkos.graph import sqlite_graph
    from openkos.state import fts as fts_module

    bundle_dir = root / "bundle"
    fts_module.write_fts_index(root / ".openkos" / "fts.db", bundle_dir)
    sqlite_graph.write_graph_store(root / ".openkos" / "graph.db", bundle_dir)
    (bundle_dir / "concepts").mkdir(parents=True, exist_ok=True)
    (bundle_dir / "concepts" / "new.md").write_text(
        "---\ntype: Concept\ntitle: New\n---\nBody.\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        "openkos.application.next_action.vector_store_is_empty", lambda _path: False
    )
    result = next_action.next_action(config.WorkspaceLayout(root))
    assert result.action is not None
    assert result.action.command == "openkos reindex"
    assert "older than the bundle" in result.action.reason
    assert result.action.subjects == ()

    # -- unextracted: action `(concept_id,)`, declination `(concept_id,)` --
    root = tmp_path_factory.mktemp("subjects_unextracted")
    _init_workspace(root, monkeypatch)
    _write_unextracted_source(root, name="broken", resource="")
    _write_unextracted_source(root, name="notes", resource="raw/notes.txt")
    result = next_action.next_action(config.WorkspaceLayout(root))
    assert result.action is not None
    assert result.action.command == "openkos ingest raw/notes.txt"
    assert result.action.subjects == ("sources/notes",)
    assert len(result.declination_subjects) == len(result.declinations)
    assert result.declination_subjects[0] == ("sources/broken",)

    # -- unjudged: action `(concept_id,)`, declination `(concept_id,)` --
    root = tmp_path_factory.mktemp("subjects_unjudged")
    _init_workspace(root, monkeypatch)
    _write_unjudged_source(root, name="broken", resource="")
    _write_unjudged_source(root, name="notes", resource="raw/notes.txt")
    result = next_action.next_action(config.WorkspaceLayout(root))
    assert result.action is not None
    assert result.action.command == "openkos ingest raw/notes.txt"
    assert result.action.subjects == ("sources/notes",)
    assert len(result.declination_subjects) == len(result.declinations)
    assert result.declination_subjects[0] == ("sources/broken",)

    # -- below-source-sensitivity: `(concept_id, *related_ids)` --
    root = tmp_path_factory.mktemp("subjects_below_source")
    _init_workspace(root, monkeypatch)
    _write_below_source_sensitivity_bundle(root)
    result = next_action.next_action(config.WorkspaceLayout(root))
    assert result.action is not None
    assert result.action.command == "openkos backfill-sensitivity"
    assert result.action.subjects == ("concepts/derived", "sources/a")

    # -- multi-source-uncovered: action + declination both
    # `(concept_id, *related_ids)` --
    root = tmp_path_factory.mktemp("subjects_multi_source")
    _init_workspace(root, monkeypatch)
    _write_multi_source_uncovered_only_bundle(root)
    odd_dir = root / "bundle" / "concepts" / "a b"
    odd_dir.mkdir(parents=True, exist_ok=True)
    (odd_dir / "early.md").write_text(
        "---\ntype: Concept\ntitle: Early\nsensitivity: public\n"
        "provenance:\n  - sources/a\n  - concepts/from-c\n---\nBody.\n",
        encoding="utf-8",
    )
    result = next_action.next_action(config.WorkspaceLayout(root))
    assert result.action is not None
    assert (
        result.action.command == "openkos set-sensitivity concepts/mixed confidential"
    )
    assert result.action.subjects == (
        "concepts/mixed",
        "sources/a",
        "concepts/from-c",
    )
    assert len(result.declination_subjects) == len(result.declinations)
    assert result.declination_subjects[0] == (
        "concepts/a b/early",
        "sources/a",
        "concepts/from-c",
    )

    # -- duplicate groups: `subjects=()` --
    root = tmp_path_factory.mktemp("subjects_duplicates")
    _init_workspace(root, monkeypatch)
    _write_doc(root / "bundle" / "concepts" / "dup-a.md", title="Stoicism")
    _write_doc(root / "bundle" / "concepts" / "dup-b.md", title="STOICISM")
    result = next_action.next_action(config.WorkspaceLayout(root))
    assert result.action is not None
    assert result.action.command == "openkos curate"
    assert result.action.subjects == ()

    # -- non-NFC: `subjects=()` --
    root = tmp_path_factory.mktemp("subjects_non_nfc")
    _init_workspace(root, monkeypatch)
    import unicodedata

    nfd_cafe = unicodedata.normalize("NFD", "café")
    (root / "bundle" / "concepts").mkdir(parents=True, exist_ok=True)
    (root / "bundle" / "concepts" / f"{nfd_cafe}.md").write_text(
        "---\ntype: Concept\ntitle: Cafe\n---\nBody.\n", encoding="utf-8"
    )
    result = next_action.next_action(config.WorkspaceLayout(root))
    assert result.action is not None
    assert result.action.command == "openkos normalize-names"
    assert result.action.subjects == ()

    # -- open contradictions: `finding.pair_ids` --
    root = tmp_path_factory.mktemp("subjects_contradiction")
    _init_workspace(root, monkeypatch)
    _write_doc(root / "bundle" / "concepts" / "alpha.md", title="Alpha")
    _write_doc(root / "bundle" / "concepts" / "beta.md", title="Beta")
    _record_finding(root)
    result = next_action.next_action(config.WorkspaceLayout(root))
    assert result.action is not None
    assert result.action.command == "openkos contradictions"
    assert result.action.subjects == ("concepts/alpha", "concepts/beta")
