"""Byte-identity characterization golden for `openkos next` (mcp-read-surface
Slice 7, design Decision 6: "openkos next stays byte-identical").

`application/next_action.py` and `lint.py` gain structured `subjects`,
`declination_subjects`, and `related_ids` fields in this slice so
`mcp/gate.py` can decide what a `pending` result may disclose without ever
parsing the free-form reason/detail prose. None of that plumbing is read by
`render_lines`, so `openkos next`'s printed stdout must stay byte-for-byte
identical before and after -- this golden is written and confirmed GREEN on
`main`, BEFORE any change to `next_action.py`/`lint.py` lands, and it must
stay green, unchanged, through the rest of this slice.

Every scenario below is built from the exact fixture helpers and expected
wording already pinned, substring-by-substring, across `test_next.py` and
`test_next_action.py` -- this file additionally pins the COMPLETE stdout
stream for a representative matrix (design's Testing Strategy preamble),
mirroring `test_ingest_characterization.py`'s "why a full-stream golden"
reasoning for `ingest`.

No git-identity pinning is needed here (unlike `test_ingest_characterization
.py`'s `_deterministic_git_identity` fixture): `next_cmd` (`cli/main.py`) is
read-only end to end -- it never calls `vcs.git`, never constructs a model
backend, and never writes to the workspace (`test_next.py`'s own
`test_next_never_writes_to_the_workspace`/`test_next_never_constructs_
ollama_client` pin exactly that) -- so there is no machine-dependent stderr
line this golden could observe. The one platform hazard that DOES apply --
directory walk order -- is closed structurally: `okf._iter_docs` walks
`sorted(bundle_dir.rglob("*.md"))` (`model/okf.py`), a deterministic,
locale-independent path sort, not filesystem/OS directory-listing order.

Falsification (design: "a golden that cannot go red is a golden that proves
nothing") was performed manually during apply, not as a permanent test here:
one character was mutated in `next_action.tier_missing_vector_index`'s
reason string, `__pycache__` was purged, `uv run pytest
tests/unit/cli/test_next_golden.py` was run and confirmed RED, then the
mutation was reverted with the exact inverse edit and `__pycache__` purged
again. See the apply-progress record for the exact mutate/revert transcript.
"""

import os
import sqlite3
import unicodedata
from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner

from openkos import config
from openkos.cli.main import app
from openkos.graph import sqlite_graph
from openkos.state import derived, findings, fts

runner = CliRunner()

_STATUS_POINTER = "For everything else, run `openkos status`."


def _init_workspace(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(root)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0, result.stderr


def _seed_vectors_db(root: Path) -> None:
    """Write one `vector_meta` row so the bundle counts as embeddings
    PRESENT (issue #183: "absent OR empty", not merely absent) -- the same
    shape `tests/unit/cli/conftest.py`'s `seed_vectors_db` fixture writes."""
    openkos_dir = root / ".openkos"
    openkos_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(openkos_dir / "vectors.db"))
    try:
        conn.execute("CREATE TABLE vector_meta (id INTEGER PRIMARY KEY, dim INTEGER)")
        conn.execute("INSERT INTO vector_meta (dim) VALUES (1024)")
        conn.commit()
    finally:
        conn.close()


def _write_doc(path: Path, *, doc_type: str = "Concept", title: str = "Stub") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntype: {doc_type}\ntitle: {title}\n---\n# {title}\n",
        encoding="utf-8",
    )


def _write_unparseable_doc(root: Path, name: str = "broken") -> None:
    concepts_dir = root / "bundle" / "concepts"
    concepts_dir.mkdir(parents=True, exist_ok=True)
    (concepts_dir / f"{name}.md").write_text(
        "Just plain text, no frontmatter block.\n", encoding="utf-8"
    )


def _write_unextracted_source(
    root: Path, *, name: str = "notes", resource: str = "raw/notes.txt"
) -> None:
    sources_dir = root / "bundle" / "sources"
    sources_dir.mkdir(parents=True, exist_ok=True)
    resource_line = f"resource: {resource}\n" if resource else ""
    (sources_dir / f"{name}.md").write_text(
        f"---\ntype: Source\ntitle: {name.title()}\n{resource_line}"
        "extraction_status: failed\n---\nBody.\n",
        encoding="utf-8",
    )


def _write_multi_source_uncovered_only_bundle(root: Path) -> None:
    sources_dir = root / "bundle" / "sources"
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
    concepts_dir = root / "bundle" / "concepts"
    concepts_dir.mkdir(parents=True, exist_ok=True)
    (concepts_dir / "from-c.md").write_text(
        "---\ntype: Concept\ntitle: From C\nsensitivity: confidential\n"
        "provenance:\n  - sources/c\n---\nBody.\n",
        encoding="utf-8",
    )


def _break_os_walk(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force `okf._walk_errors` to report exactly one directory-scan error,
    deterministically -- mirrors `test_next.py`'s helper of the same name."""
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


def _record_contradiction(root: Path) -> None:
    layout = config.WorkspaceLayout(root)
    conn = derived.open_derived_connection(layout.findings_db_path)
    try:
        findings.record_findings(
            conn,
            [
                findings.Finding(
                    pair_ids=("concepts/alpha", "concepts/beta"),
                    merged_absorbed_id=None,
                    verdict="contradicts",
                    confidence=0.9,
                    rationale="Stub rationale.",
                    input_digests=(),
                )
            ],
        )
    finally:
        conn.close()


def _lines_to_stdout(lines: list[str]) -> str:
    return "\n".join(lines) + "\n"


def _run_scenario(
    root: Path, monkeypatch: pytest.MonkeyPatch, setup: Callable[[Path], None]
) -> str:
    _init_workspace(root, monkeypatch)
    setup(root)
    result = runner.invoke(app, ["next"])
    assert result.exit_code == 0, result.stderr
    return result.stdout


def test_next_stdout_golden(tmp_path_factory: pytest.TempPathFactory) -> None:
    """Pins `openkos next`'s exact stdout across a representative matrix
    (bootstrap on both branches, missing vector/FTS index, stale indexes,
    an unextracted source with a declination and a skip notice, a
    multi-source-uncovered declination, a duplicate group, a non-NFC name,
    and an open contradiction) -- the regression net for Slice 7's
    `subjects` work, not a RED-first test (design's Testing Strategy)."""

    # -- Bootstrap, branch 1: genuinely empty, the walk completed. --------
    with pytest.MonkeyPatch.context() as mp:
        root = tmp_path_factory.mktemp("bootstrap_empty")
        stdout = _run_scenario(root, mp, lambda _root: None)
    assert stdout == _lines_to_stdout(
        [
            "Run: openkos ingest <path>",
            "This bundle has no documents yet -- ingest your first source "
            "to give it something to index.",
            _STATUS_POINTER,
        ]
    )

    # -- Bootstrap, branch 2: looks empty, but the walk was incomplete. ---
    with pytest.MonkeyPatch.context() as mp:
        root = tmp_path_factory.mktemp("bootstrap_walk_incomplete")
        _init_workspace(root, mp)
        _break_os_walk(mp)
        result = runner.invoke(app, ["next"])
        assert result.exit_code == 0, result.stderr
        stdout = result.stdout
    assert stdout == _lines_to_stdout(
        [
            "Run: openkos status",
            "This bundle looks empty, but at least one directory could "
            "not be read -- check which one before ingesting anything.",
            _STATUS_POINTER,
        ]
    )

    # -- Missing vector index. ---------------------------------------------
    def _missing_vector_index(root: Path) -> None:
        _write_doc(root / "bundle" / "concepts" / "alpha.md", title="Alpha")

    with pytest.MonkeyPatch.context() as mp:
        root = tmp_path_factory.mktemp("missing_vector_index")
        stdout = _run_scenario(root, mp, _missing_vector_index)
    assert stdout == _lines_to_stdout(
        [
            "Run: openkos reindex",
            "Dense retrieval and candidate edges are unavailable -- the "
            "vector index is missing or empty.",
            _STATUS_POINTER,
        ]
    )

    # -- Missing FTS index (vectors present, no `.openkos/fts.db`). --------
    with pytest.MonkeyPatch.context() as mp:
        root = tmp_path_factory.mktemp("missing_fts_index")
        stdout = _run_scenario(root, mp, _seed_vectors_db)
    assert stdout == _lines_to_stdout(
        [
            "Run: openkos reindex",
            "Lexical (full-text) retrieval is unavailable -- the FTS index is missing.",
            _STATUS_POINTER,
        ]
    )

    # -- Stale derived indexes (built, then the bundle changed under them).
    def _stale_indexes(root: Path) -> None:
        _seed_vectors_db(root)
        openkos_dir = root / ".openkos"
        bundle_dir = root / "bundle"
        fts.write_fts_index(openkos_dir / "fts.db", bundle_dir)
        sqlite_graph.write_graph_store(openkos_dir / "graph.db", bundle_dir)
        concepts = bundle_dir / "concepts"
        concepts.mkdir(parents=True, exist_ok=True)
        (concepts / "new.md").write_text(
            "---\ntype: Concept\ntitle: New\ndescription: ''\n---\nbody\n",
            encoding="utf-8",
        )

    with pytest.MonkeyPatch.context() as mp:
        root = tmp_path_factory.mktemp("stale_indexes")
        stdout = _run_scenario(root, mp, _stale_indexes)
    assert stdout == _lines_to_stdout(
        [
            "Run: openkos reindex",
            "Retrieval is answering from indexes older than the bundle (fts, graph).",
            _STATUS_POINTER,
        ]
    )

    # -- Unextracted source: a runnable finding, a declined sibling (no
    # resource), and an unparseable doc surfaced as a skip notice. ---------
    def _unextracted_with_declination_and_skip(root: Path) -> None:
        _seed_vectors_db(root)
        _write_unextracted_source(root, name="broken", resource="")
        _write_unextracted_source(root, name="notes", resource="raw/notes.txt")
        _write_unparseable_doc(root)
        fts.write_fts_index(root / ".openkos" / "fts.db", root / "bundle")

    with pytest.MonkeyPatch.context() as mp:
        root = tmp_path_factory.mktemp("unextracted_declination_skip")
        stdout = _run_scenario(root, mp, _unextracted_with_declination_and_skip)
    assert stdout == _lines_to_stdout(
        [
            "Run: openkos ingest raw/notes.txt",
            "sources/notes: concept extraction failed during ingest — "
            "retry with `openkos ingest raw/notes.txt`",
            "Seen but not recommended -- no runnable command could be derived:",
            "  sources/broken: extraction failed, but the document records "
            "no resource to re-ingest",
            "1 document could not be read and was skipped:",
            "  concepts/broken.md: skipped (unparseable frontmatter)",
            _STATUS_POINTER,
        ]
    )

    # -- multi-source-uncovered: declined loudly, id not spellable. --------
    def _multi_source_uncovered_unspellable(root: Path) -> None:
        _seed_vectors_db(root)
        _write_multi_source_uncovered_only_bundle(root)
        odd_dir = root / "bundle" / "concepts" / "a b"
        odd_dir.mkdir(parents=True, exist_ok=True)
        (odd_dir / "mixed2.md").write_text(
            "---\ntype: Concept\ntitle: Mixed Two\nsensitivity: public\n"
            "provenance:\n  - sources/a\n  - concepts/from-c\n---\nBody.\n",
            encoding="utf-8",
        )
        fts.write_fts_index(root / ".openkos" / "fts.db", root / "bundle")

    with pytest.MonkeyPatch.context() as mp:
        root = tmp_path_factory.mktemp("multi_source_uncovered_unspellable")
        stdout = _run_scenario(root, mp, _multi_source_uncovered_unspellable)
    assert stdout == _lines_to_stdout(
        [
            "No ranked action found in this bundle.",
            "Seen but not recommended -- no runnable command could be derived:",
            "  concepts/a b/mixed2: sensitivity sits below its cited "
            "concepts, but its id is not a runnable argument -- rename "
            "it, or run `openkos set-sensitivity` by hand",
            _STATUS_POINTER,
        ]
    )

    # -- Duplicate group. ----------------------------------------------------
    def _duplicate_groups(root: Path) -> None:
        _seed_vectors_db(root)
        _write_doc(root / "bundle" / "concepts" / "dup-a.md", title="Stoicism")
        _write_doc(root / "bundle" / "concepts" / "dup-b.md", title="STOICISM")
        fts.write_fts_index(root / ".openkos" / "fts.db", root / "bundle")

    with pytest.MonkeyPatch.context() as mp:
        root = tmp_path_factory.mktemp("duplicate_groups")
        stdout = _run_scenario(root, mp, _duplicate_groups)
    assert stdout == _lines_to_stdout(
        [
            "Run: openkos curate",
            "1 candidate group with identical titles is pending review. "
            "Review them first with `openkos duplicates`.",
            _STATUS_POINTER,
        ]
    )

    # -- Non-NFC on-disk name. -----------------------------------------------
    def _non_nfc(root: Path) -> None:
        _seed_vectors_db(root)
        concepts = root / "bundle" / "concepts"
        concepts.mkdir(parents=True, exist_ok=True)
        nfd_cafe = unicodedata.normalize("NFD", "café")
        (concepts / f"{nfd_cafe}.md").write_text(
            "---\ntype: Concept\ntitle: Cafe\n---\nBody.\n", encoding="utf-8"
        )
        fts.write_fts_index(root / ".openkos" / "fts.db", root / "bundle")

    with pytest.MonkeyPatch.context() as mp:
        root = tmp_path_factory.mktemp("non_nfc")
        stdout = _run_scenario(root, mp, _non_nfc)
    assert stdout == _lines_to_stdout(
        [
            "Run: openkos normalize-names",
            "1 on-disk name is not NFC, so the spelling on disk disagrees "
            "with the canonical id. Review them first with `openkos lint`.",
            _STATUS_POINTER,
        ]
    )

    # -- Open contradiction (last tier). -------------------------------------
    def _open_contradiction(root: Path) -> None:
        _seed_vectors_db(root)
        _write_doc(root / "bundle" / "concepts" / "alpha.md", title="Alpha")
        _write_doc(root / "bundle" / "concepts" / "beta.md", title="Beta")
        fts.write_fts_index(root / ".openkos" / "fts.db", root / "bundle")
        _record_contradiction(root)

    with pytest.MonkeyPatch.context() as mp:
        root = tmp_path_factory.mktemp("open_contradiction")
        stdout = _run_scenario(root, mp, _open_contradiction)
    assert stdout == _lines_to_stdout(
        [
            "Run: openkos contradictions",
            "concepts/alpha <-> concepts/beta: an open contradiction "
            "finding is pending review (confidence: 0.90).",
            _STATUS_POINTER,
        ]
    )
