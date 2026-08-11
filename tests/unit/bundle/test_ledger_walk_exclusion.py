"""EXCLUDE-walk regression guard (design: "Relocate the merge ledger to
`bundle/.state/ledger/`", Decision 3): every inbound-reference/EXCLUDE walk
in this codebase (`bundle/references.py::find_inbound_references`,
`bundle/links.py::find_inbound_link_rewrites`,
`bundle/relations.py::find_inbound_relation_rewrites`,
`bundle/provenance.py::find_inbound_provenance_rewrites`,
`okf._iter_docs` -- and, through it, `state/reindex.py` and `state/fts.py`)
MUST NOT see bytes under `bundle/.state/ledger/` as a document or a
reference source.

This needs -- and got -- ZERO code changes (design "Corrections to the
proposal"): every one of those walks is `sorted(bundle_dir.rglob("*.md"))`
or `okf._iter_docs`'s own `rglob("*.md")`, and the ledger sidecar suffix is
deliberately `.ledger.okf`, never `.md` (`bundle/ledger.py::LEDGER_SUFFIX`).
This test is a LOCK-IN, not a fix: it exists so a future edit to the
sidecar suffix (or to `_iter_docs`'s glob pattern) that reintroduces the
ledger into these walks is caught immediately, rather than silently
resurrecting #550's phantom-reference class."""

from pathlib import Path

from openkos.bundle import ledger
from openkos.model import okf


def _write_concept(path: Path, *, title: str = "Concept") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        okf.dump_frontmatter({"type": "Concept", "title": title}, "Body.\n"),
        encoding="utf-8",
    )


def test_rglob_md_walk_excludes_ledger_sidecar(tmp_path: Path) -> None:
    """`sorted(bundle_dir.rglob("*.md"))` -- the walk every one of the six
    `cli/main.py` EXCLUDE sites builds its snapshot from -- never yields a
    path under `bundle/.state/ledger/`, whether or not a sidecar exists."""
    bundle_dir = tmp_path / "bundle"
    _write_concept(bundle_dir / "concepts" / "survivor.md")

    before = sorted(p.as_posix() for p in bundle_dir.rglob("*.md"))

    ledger.write_entries(
        "concepts/survivor",
        bundle_dir,
        survivor_id="concepts/survivor",
        entries=[
            okf.MergeLedgerEntry(
                schema=okf.MERGE_LEDGER_SCHEMA_V3,
                merged_at="2026-01-01T00:00:00Z",
                absorbed_id="concepts/absorbed",
                absorbed_snapshot=okf.dump_frontmatter(
                    {"type": "Concept", "title": "Absorbed"}, "Absorbed body.\n"
                ),
                survivor_before=okf.dump_frontmatter(
                    {"type": "Concept", "title": "Survivor"}, "Before.\n"
                ),
                index_before="",
                log_before="",
                link_rewrites=[],
                sensitivity_before="private",
                sensitivity_after="private",
                relation_rewrites=[],
                provenance_rewrites=[],
            )
        ],
    )
    ledger_path = ledger.ledger_path_for("concepts/survivor", bundle_dir)
    assert ledger_path.is_file(), "fixture setup: sidecar must actually exist"

    after = sorted(p.as_posix() for p in bundle_dir.rglob("*.md"))

    assert after == before, "the ledger sidecar must never appear in the *.md walk"
    assert ledger_path.as_posix() not in after


def test_iter_docs_excludes_ledger_sidecar(tmp_path: Path) -> None:
    """`okf._iter_docs` -- the shared walk behind `state/reindex.py` and
    `state/fts.py`, in addition to every EXCLUDE-walk caller -- likewise
    never yields a `DocScan` for a `bundle/.state/ledger/` path, and the
    scanned-doc COUNT is identical with and without a sidecar present."""
    bundle_dir = tmp_path / "bundle"
    _write_concept(bundle_dir / "concepts" / "survivor.md")
    _write_concept(bundle_dir / "concepts" / "other.md")

    before_paths = sorted(scan.path.as_posix() for scan in okf._iter_docs(bundle_dir))

    ledger.write_entries(
        "concepts/survivor",
        bundle_dir,
        survivor_id="concepts/survivor",
        entries=[
            okf.MergeLedgerEntry(
                schema=okf.MERGE_LEDGER_SCHEMA_V3,
                merged_at="2026-01-01T00:00:00Z",
                absorbed_id="concepts/absorbed",
                absorbed_snapshot=okf.dump_frontmatter(
                    {"type": "Concept", "title": "Absorbed"}, "Absorbed body.\n"
                ),
                survivor_before=okf.dump_frontmatter(
                    {"type": "Concept", "title": "Survivor"}, "Before.\n"
                ),
                index_before="",
                log_before="",
                link_rewrites=[],
                sensitivity_before="private",
                sensitivity_after="private",
                relation_rewrites=[],
                provenance_rewrites=[],
            )
        ],
    )

    after_paths = sorted(scan.path.as_posix() for scan in okf._iter_docs(bundle_dir))

    assert after_paths == before_paths
    assert len(after_paths) == 2
