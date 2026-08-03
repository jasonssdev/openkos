"""Unit tests for the `status` CLI command: Phase-A-only, read-only overview.

`status` is the first read command (design.md's Technical Approach): no
Phase B, no confirm gate, no `--auto`. It composes `config.require_workspace`
(exit gate), `okf.survey_bundle` (counts + §9 findings from one disk scan),
and `bundle.log.read_recent_entries` (recent activity, lenient-degrading),
then renders three sections via `typer.echo`. Exit 0 on every successful
read; the ONLY non-zero path is an absent/unreadable workspace.
"""

import sqlite3
from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner

from openkos import lint as lint_check
from openkos.cli.main import app
from openkos.graph import sqlite_graph
from openkos.llm.base import EMBED_DIM
from tests.unit.cli.conftest import snapshot_bytes as _snapshot

runner = CliRunner()


def _init_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0


class _OfflineOllama:
    """Serves both halves of `OllamaClient` without a network.

    `ingest` builds a real client for `_embed_after_ingest` (#183), so any
    test that invokes `ingest` and then asserts on embedding-dependent
    output MUST patch this seam -- the convention every test in
    `test_ingest.py` already follows. Without it the assertion silently
    becomes "is an Ollama server reachable on this machine", which passes
    for a developer running one and fails in CI."""

    def chat(self, messages: object) -> str:
        return '{"extract": false}'

    def embed(self, texts: "list[str]") -> "list[list[float]]":
        return [[1.0] + [0.0] * (EMBED_DIM - 1) for _ in texts]


def test_status_refuses_when_not_a_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A directory that is not an initialized workspace exits non-zero, with
    the shared `require_workspace` reason under a `status`-specific prefix,
    and no raw traceback (spec: Workspace Presence Check)."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["status"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert result.stderr == (
        "openkos status: refusing to run -- no OpenKOS workspace found in "
        "this directory (run 'openkos init' first).\n"
    )
    assert "Traceback" not in result.stderr


def test_status_refuses_cleanly_when_workspace_files_are_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A permission-denied `bundle/index.md` makes `Path.is_file()` raise
    `PermissionError` instead of swallowing it to `False`. `status` must
    still refuse cleanly (exit 1, D1's shared gate, no raw traceback) --
    never let the `OSError` propagate out of `require_workspace` uncaught."""
    _init_workspace(tmp_path, monkeypatch)

    original_is_file = Path.is_file

    def fake_is_file(self: Path) -> bool:
        if self.name == "index.md":
            raise PermissionError(13, "Permission denied", str(self))
        return original_is_file(self)

    monkeypatch.setattr(Path, "is_file", fake_is_file)

    result = runner.invoke(app, ["status"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert "Traceback" not in result.stderr
    assert result.stderr.startswith("openkos status: refusing to run -- ")
    assert "could not be read" in result.stderr


def test_status_fresh_bundle_empty_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A freshly initialized, empty bundle reports `Sources: 0`,
    `Concepts: 0`, the recent-activity empty-state text, and exits 0 (spec:
    Freshly initialized empty bundle). `log.md`'s `Initialization` bullet
    means "no activity recorded yet" is the true empty-log-body case, so
    this asserts the actual init entry appears instead. A fresh `init`
    never creates `.openkos/vectors.db`, so "needs attention" is NOT empty
    -- it always surfaces the #142 missing-vector-index line until the
    first `openkos reindex`."""
    _init_workspace(tmp_path, monkeypatch)

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert "Sources:  0" in result.stdout
    assert "Concepts: 0" in result.stdout
    assert "vectors.db" in result.stdout
    assert "openkos reindex" in result.stdout


def test_status_healthy_bundle_full_render_has_three_sections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A healthy workspace with an ingested source renders all three
    sections and exits 0 (spec: Healthy bundle with sources, Healthy bundle
    shows recent activity).

    Updated by #183: `ingest` now embeds what it wrote, so `vectors.db`
    exists and carries the Source's own vector by the time `status` runs.
    The #142 missing-vector-index line is therefore GONE -- its absence is
    the assertion now. This test previously documented "`ingest` never
    creates it", which was the starving behavior the issue set out to
    fix."""
    _init_workspace(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient", lambda *a, **k: _OfflineOllama()
    )
    source = tmp_path / "notes.txt"
    source.write_text("Some raw notes.", encoding="utf-8")
    ingest_result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])
    assert ingest_result.exit_code == 0

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert "Bundle contents:" in result.stdout
    assert "Recent activity:" in result.stdout
    assert "Needs attention:" in result.stdout
    assert "Sources:  1" in result.stdout
    assert "Concepts: 0" in result.stdout
    assert "Ingest" in result.stdout
    assert (tmp_path / ".openkos" / "vectors.db").exists()
    assert "vectors.db" not in result.stdout
    assert "Nothing needs attention." in result.stdout


def test_status_breaks_down_non_concept_types_instead_of_folding_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bundle with a Procedure alongside Concepts reports a distinct
    `Procedures:` line and a `Concepts:` count that excludes it -- never the
    old two-bucket conflation that folded every non-Source type into
    "Concepts" (issue #133)."""
    _init_workspace(tmp_path, monkeypatch)
    concepts_dir = tmp_path / "bundle" / "concepts"
    concepts_dir.mkdir()
    (concepts_dir / "alpha.md").write_text(
        "---\ntype: Concept\ntitle: Alpha\n---\nBody.\n", encoding="utf-8"
    )
    (concepts_dir / "beta.md").write_text(
        "---\ntype: Concept\ntitle: Beta\n---\nBody.\n", encoding="utf-8"
    )
    procedures_dir = tmp_path / "bundle" / "procedures"
    procedures_dir.mkdir()
    (procedures_dir / "setup.md").write_text(
        "---\ntype: Procedure\ntitle: Setup\n---\nBody.\n", encoding="utf-8"
    )

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert "Concepts:" in result.stdout
    assert "Procedures:" in result.stdout
    concepts_line = next(
        line for line in result.stdout.splitlines() if "Concepts:" in line
    )
    procedures_line = next(
        line for line in result.stdout.splitlines() if "Procedures:" in line
    )
    assert concepts_line.split(":", 1)[1].strip() == "2"
    assert procedures_line.split(":", 1)[1].strip() == "1"


def test_status_counts_reflect_disk_scan_not_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A source file present on disk but NOT reflected in `index.md` (as
    after an interrupted `ingest`) is still counted -- disk is the truth,
    never `index.md` (spec: Catalog drift -- disk is the truth)."""
    _init_workspace(tmp_path, monkeypatch)
    sources_dir = tmp_path / "bundle" / "sources"
    sources_dir.mkdir()
    (sources_dir / "orphan.md").write_text(
        "---\ntype: Source\ntitle: Orphan\n---\nBody.\n", encoding="utf-8"
    )

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert "Sources:  1" in result.stdout
    index_text = (tmp_path / "bundle" / "index.md").read_text(encoding="utf-8")
    assert "orphan" not in index_text


def test_status_empty_log_body_shows_no_activity_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `log.md` with a header but no dated sections yet reports "No
    activity recorded yet." (spec: Empty log). A fresh `init` always writes
    an `Initialization` bullet, so this is an edge case reached only when
    `log.md`'s activity section is genuinely empty."""
    _init_workspace(tmp_path, monkeypatch)
    log_path = tmp_path / "bundle" / "log.md"
    log_path.write_text("# Directory Update Log\n", encoding="utf-8")

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert "No activity recorded yet." in result.stdout


def test_status_malformed_log_degrades_and_still_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A malformed `log.md` degrades to a notice under "Recent activity" and
    the command still exits 0 -- counts and findings are unaffected (spec:
    Q2/D5, lenient degrade). No `.openkos/vectors.db` exists yet, so "needs
    attention" surfaces the #142 missing-vector-index line."""
    _init_workspace(tmp_path, monkeypatch)
    log_path = tmp_path / "bundle" / "log.md"
    log_path.write_text(
        "# Directory Update Log\n\n## 2026-07-16\n* no blank line above\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert "Recent activity unavailable" in result.stdout
    assert "Sources:  0" in result.stdout
    assert "vectors.db" in result.stdout


def test_status_conformance_violation_is_surfaced_but_non_fatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A concept file with a missing `type` field is listed under "Needs
    attention" and the command still exits 0 (spec: Conformance violation
    is surfaced but non-fatal)."""
    _init_workspace(tmp_path, monkeypatch)
    concepts_dir = tmp_path / "bundle" / "concepts"
    concepts_dir.mkdir()
    (concepts_dir / "orphan.md").write_text(
        "---\ntitle: no type here\n---\nBody.\n", encoding="utf-8"
    )

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert "missing non-empty 'type'" in result.stdout


# --- purge-transactional-cleanup #141/#142: Needs-attention wiring --------


def test_status_surfaces_dangling_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A concept whose `relations:` target names an id absent from disk is
    listed under "needs attention", and `status` still exits 0 (status
    spec: "Dangling reference is surfaced under needs attention")."""
    _init_workspace(tmp_path, monkeypatch)
    concepts_dir = tmp_path / "bundle" / "concepts"
    concepts_dir.mkdir()
    (concepts_dir / "stoicism.md").write_text(
        "---\ntype: Concept\ntitle: Stoicism\n"
        "relations:\n"
        "  - target: concepts/ghost\n"
        "    type: relates-to\n"
        "---\nBody.\n",
        encoding="utf-8",
    )
    index_path = tmp_path / "bundle" / "index.md"
    index_path.write_text(
        index_path.read_text(encoding="utf-8")
        + "\n# Concepts\n\n* [Stoicism](/concepts/stoicism.md) - test fixture.\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert "concepts/stoicism: " in result.stdout
    assert "concepts/ghost" in result.stdout


def test_status_no_dangling_references_no_new_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bundle where every `relations:` target resolves to a concept id
    present on disk adds no dangling-reference entry under "needs
    attention" (status spec: "No dangling references, no new needs-
    attention entries")."""
    _init_workspace(tmp_path, monkeypatch)
    concepts_dir = tmp_path / "bundle" / "concepts"
    concepts_dir.mkdir()
    (concepts_dir / "stoicism.md").write_text(
        "---\ntype: Concept\ntitle: Stoicism\n"
        "relations:\n"
        "  - target: concepts/epicureanism\n"
        "    type: relates-to\n"
        "---\nBody.\n",
        encoding="utf-8",
    )
    (concepts_dir / "epicureanism.md").write_text(
        "---\ntype: Concept\ntitle: Epicureanism\n---\nBody.\n", encoding="utf-8"
    )
    index_path = tmp_path / "bundle" / "index.md"
    index_path.write_text(
        index_path.read_text(encoding="utf-8") + "\n# Concepts\n\n"
        "* [Stoicism](/concepts/stoicism.md) - test fixture.\n"
        "* [Epicureanism](/concepts/epicureanism.md) - test fixture.\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert "references missing concept" not in result.stdout


# --- issue #187: unextracted-source fold-in (no-fifth-walk guard) ---------


def test_status_lists_unextracted_under_needs_attention(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Source with `extraction_status: failed` is listed under "needs
    attention", naming the same retry command `lint` computes, and `status`
    still exits 0 (status spec: "Unextracted source surfaced under needs
    attention")."""
    _init_workspace(tmp_path, monkeypatch)
    sources_dir = tmp_path / "bundle" / "sources"
    sources_dir.mkdir()
    (sources_dir / "notes.md").write_text(
        "---\ntype: Source\ntitle: Notes\nresource: raw/notes.txt\n"
        "extraction_status: failed\n---\nBody.\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert "openkos ingest raw/notes.txt" in result.stdout


def test_status_blocked_by_sensitivity_never_in_retry_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bundle containing only a Source with `extraction_status:
    blocked-by-sensitivity` adds no unextracted-source entry under "needs
    attention" -- a deliberate policy outcome, never debt (status spec:
    "blocked-by-sensitivity never appears in the retry prompt")."""
    _init_workspace(tmp_path, monkeypatch)
    sources_dir = tmp_path / "bundle" / "sources"
    sources_dir.mkdir()
    (sources_dir / "notes.md").write_text(
        "---\ntype: Source\ntitle: Notes\nresource: raw/notes.txt\n"
        "extraction_status: blocked-by-sensitivity\n---\nBody.\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert "openkos ingest" not in result.stdout


def test_status_unextracted_reuses_the_single_collect_docs_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`status` calls `lint_check.collect_docs` exactly ONCE -- the
    unextracted-source check reuses the SAME in-memory `docs` list already
    bound for dangling-reference findings, never a second walk (status
    spec: "No new bundle walk is introduced"; design's no-fifth-walk
    guard). A plain-function counting wrapper is used, not a `yield from`
    generator -- a generator would record the call at first `next()`
    rather than at call time, hiding a real regression."""
    _init_workspace(tmp_path, monkeypatch)
    sources_dir = tmp_path / "bundle" / "sources"
    sources_dir.mkdir()
    (sources_dir / "notes.md").write_text(
        "---\ntype: Source\ntitle: Notes\nresource: raw/notes.txt\n"
        "extraction_status: failed\n---\nBody.\n",
        encoding="utf-8",
    )
    calls = {"n": 0}
    real = lint_check.collect_docs

    def _counting_collect_docs(
        bundle_dir: Path,
    ) -> tuple[list[lint_check.LintDoc], list[str]]:
        calls["n"] += 1
        return real(bundle_dir)

    monkeypatch.setattr(
        "openkos.cli.main.lint_check.collect_docs", _counting_collect_docs
    )

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert calls["n"] == 1
    assert "openkos ingest raw/notes.txt" in result.stdout


# --- issue #231 (PR2): below-source-sensitivity / multi-source-uncovered ---


def test_status_lists_below_source_sensitivity_under_needs_attention(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A descendant the `backfill-sensitivity` sweep would raise is listed
    under "needs attention", labeled `below-source-sensitivity` distinctly,
    and `status` still exits 0 (design D3; #231)."""
    _init_workspace(tmp_path, monkeypatch)
    sources_dir = tmp_path / "bundle" / "sources"
    sources_dir.mkdir()
    (sources_dir / "a.md").write_text(
        "---\ntype: Source\ntitle: A\nresource: raw/a.txt\n"
        "sensitivity: confidential\n---\nBody.\n",
        encoding="utf-8",
    )
    concepts_dir = tmp_path / "bundle" / "concepts"
    concepts_dir.mkdir()
    (concepts_dir / "derived.md").write_text(
        "---\ntype: Concept\ntitle: Derived\nsensitivity: public\n"
        "provenance:\n  - sources/a\n---\nBody.\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    section = result.stdout.split("Needs attention:", 1)[1]
    assert "concepts/derived: " in section
    assert "below-source-sensitivity" in section


def test_status_marks_multi_source_uncovered_as_not_covered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A doc that is a member of no single-Source closure is listed under
    "needs attention", labeled `multi-source-uncovered` and explicitly
    marked as not covered by `backfill-sensitivity`, and `status` still
    exits 0 (design D3; #231)."""
    _init_workspace(tmp_path, monkeypatch)
    sources_dir = tmp_path / "bundle" / "sources"
    sources_dir.mkdir()
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
    concepts_dir.mkdir()
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

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    section = result.stdout.split("Needs attention:", 1)[1]
    assert "concepts/mixed: " in section
    assert "multi-source-uncovered" in section
    assert "not covered by" in section


# --- issue #257: dangling-provenance ---


def test_status_lists_dangling_provenance_under_needs_attention(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A concept whose `provenance` cites an id that resolves to no bundle
    concept is listed under "needs attention", labeled `dangling-provenance`
    distinctly, while a Source's own raw `resource` entry never fires
    (design D8's trap), and `status` still exits 0 (issue #257)."""
    _init_workspace(tmp_path, monkeypatch)
    sources_dir = tmp_path / "bundle" / "sources"
    sources_dir.mkdir()
    (sources_dir / "a.md").write_text(
        "---\ntype: Source\ntitle: A\nresource: raw/a.txt\n"
        "provenance:\n  - raw/a.txt\n---\nBody.\n",
        encoding="utf-8",
    )
    concepts_dir = tmp_path / "bundle" / "concepts"
    concepts_dir.mkdir()
    (concepts_dir / "derived.md").write_text(
        "---\ntype: Concept\ntitle: Derived\n"
        "provenance:\n  - concepts/does-not-exist\n---\nBody.\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    section = result.stdout.split("Needs attention:", 1)[1]
    assert "concepts/derived: " in section
    assert "dangling-provenance" in section
    assert "concepts/does-not-exist" in section
    assert "backfill-sensitivity" in section
    # The Source's own raw `resource` entry is excluded -- never a finding
    # SUBJECT here (it may not appear as a rendered "<id>: " prefix).
    assert "sources/a: " not in section


def test_status_below_source_reuses_the_single_collect_docs_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`status` calls `lint_check.collect_docs` exactly ONCE even with the
    below-source-sensitivity / multi-source-uncovered checks wired in --
    they reuse the SAME in-memory `docs` list already bound for
    dangling-reference/unextracted findings (design D3: "No new bundle walk
    is introduced")."""
    _init_workspace(tmp_path, monkeypatch)
    sources_dir = tmp_path / "bundle" / "sources"
    sources_dir.mkdir()
    (sources_dir / "a.md").write_text(
        "---\ntype: Source\ntitle: A\nresource: raw/a.txt\n"
        "sensitivity: confidential\n---\nBody.\n",
        encoding="utf-8",
    )
    concepts_dir = tmp_path / "bundle" / "concepts"
    concepts_dir.mkdir()
    (concepts_dir / "derived.md").write_text(
        "---\ntype: Concept\ntitle: Derived\nsensitivity: public\n"
        "provenance:\n  - sources/a\n---\nBody.\n",
        encoding="utf-8",
    )
    calls = {"n": 0}
    real = lint_check.collect_docs

    def _counting_collect_docs(
        bundle_dir: Path,
    ) -> tuple[list[lint_check.LintDoc], list[str]]:
        calls["n"] += 1
        return real(bundle_dir)

    monkeypatch.setattr(
        "openkos.cli.main.lint_check.collect_docs", _counting_collect_docs
    )

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert calls["n"] == 1
    assert "concepts/derived: " in result.stdout


def test_status_surfaces_missing_vectors_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An absent `.openkos/vectors.db` is listed under "needs attention"
    with an `openkos reindex` instruction, and `status` still exits 0
    (status spec: "Missing vectors.db is surfaced"). It must NOT also
    print "Nothing needs attention." -- the two lines are contradictory
    (issue #183 Fix 2 follow-up)."""
    _init_workspace(tmp_path, monkeypatch)

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert "vectors.db" in result.stdout
    assert "openkos reindex" in result.stdout
    assert "Nothing needs attention." not in result.stdout


def test_status_present_vectors_db_produces_no_missing_index_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    """A present `.openkos/vectors.db` produces no missing-vector-index
    entry under "needs attention" (status spec: "Present vectors.db
    produces no vector-index entry"). A fresh, concept-free bundle still
    surfaces the state-1 "no concept relationships yet" line (issue #183),
    so "needs attention" is no longer empty -- but it is state 1, distinct
    from the embeddings-missing state."""
    _init_workspace(tmp_path, monkeypatch)
    seed_vectors_db(tmp_path)

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert "vectors.db" not in result.stdout
    assert "No concept relationships yet." in result.stdout


# --- concept-edge-seeding #183: three-state concept-to-concept summary ----


def test_status_state1_no_edges_reports_distinct_message_from_state3(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    """State 1 (zero concept-to-concept edges, `vectors.db` present) reports
    "No concept relationships yet." -- distinct from the state-3
    embeddings-missing message (specs/status: "Empty graph reports no
    concept relationships")."""
    _init_workspace(tmp_path, monkeypatch)
    seed_vectors_db(tmp_path)

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert "No concept relationships yet." in result.stdout
    assert "candidate edges are not computable" not in result.stdout.lower()


def test_status_state2_edges_present_reports_counts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    """State 2 (concept-to-concept edges exist, some typed) reports the
    total edge count and the typed subset (specs/status: "Edges present
    reports counts")."""
    _init_workspace(tmp_path, monkeypatch)
    seed_vectors_db(tmp_path)
    concepts_dir = tmp_path / "bundle" / "concepts"
    concepts_dir.mkdir(parents=True, exist_ok=True)
    (concepts_dir / "a.md").write_text(
        "---\ntype: Concept\ntitle: A\n---\nSee also [B](/concepts/b.md).\n",
        encoding="utf-8",
    )
    (concepts_dir / "b.md").write_text(
        "---\ntype: Concept\ntitle: B\n---\nBody.\n", encoding="utf-8"
    )

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert "1 concept-to-concept edge(s) (0 typed)." in result.stdout


def test_status_builds_the_graph_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    """`status` calls `build_graph` exactly once per invocation (#195 --
    plumbing pins today's already-single-call behavior as a regression
    guard, not a new speedup: `status` has no redundant build to remove)."""
    _init_workspace(tmp_path, monkeypatch)
    seed_vectors_db(tmp_path)
    concepts_dir = tmp_path / "bundle" / "concepts"
    concepts_dir.mkdir(parents=True, exist_ok=True)
    (concepts_dir / "a.md").write_text(
        "---\ntype: Concept\ntitle: A\n---\nSee also [B](/concepts/b.md).\n",
        encoding="utf-8",
    )
    (concepts_dir / "b.md").write_text(
        "---\ntype: Concept\ntitle: B\n---\nBody.\n", encoding="utf-8"
    )

    calls: list[Path] = []
    real = sqlite_graph.build_graph

    def _counting_build_graph(
        bundle_dir: Path, *, candidates: sqlite_graph.CandidateSource | None = None
    ) -> sqlite_graph.SqliteGraphStore:
        calls.append(bundle_dir)
        return real(bundle_dir, candidates=candidates)

    monkeypatch.setattr("openkos.cli.main.build_graph", _counting_build_graph)
    monkeypatch.setattr("openkos.graph.summary.build_graph", _counting_build_graph)

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert len(calls) == 1
    assert "1 concept-to-concept edge(s) (0 typed)." in result.stdout


def test_status_state3_missing_vectors_db_names_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """State 3 (`vectors.db` absent) reports that candidate edges are not
    computable yet, using a message distinct from the state-1 "no concept
    relationships yet" line (specs/status: "Missing embeddings reports a
    distinct not-computable state")."""
    _init_workspace(tmp_path, monkeypatch)

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert "candidate edges" in result.stdout.lower()
    assert "No concept relationships yet." not in result.stdout


def test_status_state3_empty_vectors_db_names_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """State 3 also fires for an existing but EMPTY `vectors.db` (zero
    `vector_meta` rows), not merely an absent one -- `vector_store_is_empty`
    keys on "absent OR empty" (issue #183)."""
    _init_workspace(tmp_path, monkeypatch)
    openkos_dir = tmp_path / ".openkos"
    openkos_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(openkos_dir / "vectors.db"))
    conn.execute(
        "CREATE TABLE vector_meta (concept_id TEXT PRIMARY KEY, content_hash TEXT NOT NULL)"
    )
    conn.commit()
    conn.close()

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert "candidate edges" in result.stdout.lower()
    assert "No concept relationships yet." not in result.stdout


def test_status_healthy_workspace_reports_nothing_needs_attention(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    """The edge-count summary is a purely INFORMATIONAL line, never a
    `needs_attention` entry (spec: "or an adjacent informational line") --
    a genuinely healthy workspace (no survey findings, no dangling refs, no
    conformance issues) must still be able to print "Nothing needs
    attention." even though the informational line is also printed (issue
    #183 Fix 2 -- this branch was previously unreachable)."""
    _init_workspace(tmp_path, monkeypatch)
    seed_vectors_db(tmp_path)

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert "Nothing needs attention." in result.stdout


def test_status_state3_wins_even_with_existing_concept_edges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """State 3 is checked FIRST: an absent `vectors.db` reports the
    embeddings-missing message even when concept-to-concept edges already
    exist in the graph -- it wins over whatever the edge count would
    otherwise say (issue #183, both call sites' documented ordering)."""
    _init_workspace(tmp_path, monkeypatch)
    concepts_dir = tmp_path / "bundle" / "concepts"
    concepts_dir.mkdir(parents=True, exist_ok=True)
    (concepts_dir / "a.md").write_text(
        "---\ntype: Concept\ntitle: A\n---\nSee also [B](/concepts/b.md).\n",
        encoding="utf-8",
    )
    (concepts_dir / "b.md").write_text(
        "---\ntype: Concept\ntitle: B\n---\nBody.\n", encoding="utf-8"
    )

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert "candidate edges" in result.stdout.lower()
    assert "concept-to-concept edge(s)" not in result.stdout


def test_status_rejects_json_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No `--json` or other structured output mode is offered (spec:
    Read-Only and Human-Readable Only)."""
    _init_workspace(tmp_path, monkeypatch)

    result = runner.invoke(app, ["status", "--json"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)


def test_status_never_writes_to_the_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No file under the workspace is created, modified, or deleted on any
    run -- healthy, empty, or with findings (spec: No mutation on any
    run)."""
    _init_workspace(tmp_path, monkeypatch)
    concepts_dir = tmp_path / "bundle" / "concepts"
    concepts_dir.mkdir()
    (concepts_dir / "orphan.md").write_text(
        "---\ntitle: no type here\n---\nBody.\n", encoding="utf-8"
    )
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert _snapshot(tmp_path) == before


# --- status-surfaces-pending-duplicates #186: fourth needs-attention source


def _write_doc(path: Path, *, doc_type: str = "Concept", title: str = "Stub") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntype: {doc_type}\ntitle: {title}\n---\n# {title}\n",
        encoding="utf-8",
    )


def test_status_surfaces_exact_title_duplicate_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An exact-title-match candidate group (Tier.HIGH) is surfaced under
    "needs attention", naming `openkos duplicates` as the next step (status
    spec: "Exact-title duplicate groups are surfaced")."""
    _init_workspace(tmp_path, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Stoicism")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="STOICISM")

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert "1 candidate group" in result.stdout
    assert "openkos duplicates" in result.stdout
    assert "Nothing needs attention." not in result.stdout


def test_status_duplicate_line_has_no_tier_labels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The duplicate-groups line never prints `HIGH`, `LOW`, `exact`, or
    `near` (status spec: "MUST NOT use the words HIGH, LOW, exact, or
    near")."""
    _init_workspace(tmp_path, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Stoicism")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="STOICISM")

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    duplicate_line = next(
        line for line in result.stdout.splitlines() if "candidate group" in line
    )
    assert "HIGH" not in duplicate_line
    assert "LOW" not in duplicate_line
    assert "exact" not in duplicate_line
    assert "near" not in duplicate_line


def test_status_near_match_only_duplicates_still_all_clear(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    """A near-match-only candidate group (Tier.LOW, no exact-title-match
    group) does NOT surface a duplicate-groups entry, and `status` still
    prints "Nothing needs attention." (status spec: "Only near-match groups
    still means nothing needs attention"). This is the sole pin on the
    HIGH-only decision and the sole cover for the tier filter's false arm --
    it must not be folded into any other test."""
    _init_workspace(tmp_path, monkeypatch)
    seed_vectors_db(tmp_path)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Stoicism")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="Stoic Philosophy")

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert "candidate group" not in result.stdout
    assert "Nothing needs attention." in result.stdout


def test_status_no_duplicate_groups_no_new_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    """A fresh bundle with no near/exact-title candidate groups adds no
    duplicate-groups entry, and `status` still prints "Nothing needs
    attention." (status spec: "No duplicate groups"; covers the
    `if exact_title_groups:` false arm)."""
    _init_workspace(tmp_path, monkeypatch)
    seed_vectors_db(tmp_path)

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert "candidate group" not in result.stdout
    assert "Nothing needs attention." in result.stdout


def test_status_deprecated_only_duplicate_group_excluded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    """An exact-title-match group whose members are ALL deprecated is
    excluded by `find_candidates`'s default `include_deprecated=False`, so
    no duplicate-groups entry appears (status spec: "Deprecated-only
    duplicate group is excluded by default")."""
    _init_workspace(tmp_path, monkeypatch)
    seed_vectors_db(tmp_path)
    (tmp_path / "bundle" / "concepts").mkdir(parents=True, exist_ok=True)
    (tmp_path / "bundle" / "concepts" / "a.md").write_text(
        "---\ntype: Concept\ntitle: Stoicism\nstatus: deprecated\n---\n# Stoicism\n",
        encoding="utf-8",
    )
    (tmp_path / "bundle" / "concepts" / "b.md").write_text(
        "---\ntype: Concept\ntitle: STOICISM\nstatus: deprecated\n---\n# STOICISM\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert "candidate group" not in result.stdout


def test_status_duplicate_line_plural_wording(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two distinct exact-title-match groups pluralize the count (status
    spec: "MUST report the exact-title group count with correct
    singular/plural wording")."""
    _init_workspace(tmp_path, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Stoicism")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="STOICISM")
    _write_doc(tmp_path / "bundle" / "concepts" / "c.md", title="Epicureanism")
    _write_doc(tmp_path / "bundle" / "concepts" / "d.md", title="EPICUREANISM")

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert "2 candidate groups" in result.stdout
