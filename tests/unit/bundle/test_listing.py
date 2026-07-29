"""Unit tests for `bundle/listing.py`: the single-pass bundle enumerator and
type-filter vocabulary resolver backing the `list` CLI verb (PR1 of
discover-concept-ids, `openspec/changes/discover-concept-ids/design.md`).

No CLI wiring here -- this module is pure canonical-layer coverage; the CLI
command and its own tests are PR2.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest

from openkos import lifecycle
from openkos.bundle import listing
from openkos.model import okf
from openkos.model.types import REGISTRY


def _write_doc(
    path: Path,
    *,
    type_: str = "Concept",
    title: str | None = "Stub",
    status: str | None = None,
    relations: list[tuple[str, str]] | None = None,
    relations_raw: str | None = None,
    extra_lines: list[str] | None = None,
    body: str = "",
) -> None:
    """Write a minimal concept `.md` file with optional frontmatter fields
    (mirrors `tests/unit/test_lifecycle.py::_write_doc`). `title=None` omits
    the `title:` key entirely; `extra_lines` appends arbitrary raw
    frontmatter lines verbatim (used for `sensitivity:` variants)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---", f"type: {type_}"]
    if title is not None:
        lines.append(f"title: {title}")
    if status is not None:
        lines.append(f"status: {status}")
    if relations_raw is not None:
        lines.append(relations_raw)
    elif relations is not None:
        lines.append("relations:")
        for target, rel_type in relations:
            lines.append(f"  - target: {target}")
            lines.append(f"    type: {rel_type}")
    if extra_lines:
        lines.extend(extra_lines)
    lines.append("---")
    frontmatter = "\n".join(lines) + "\n"
    path.write_text(f"{frontmatter}{body}", encoding="utf-8")


# --- Phase 1: BundleObject field derivation --------------------------------


def test_concept_id_derived_from_path(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "people" / "jane.md")

    rows = listing.list_objects(bundle_dir)

    assert len(rows) == 1
    assert rows[0].concept_id == "people/jane"


def test_link_dir_derived_structurally_from_first_path_segment(
    tmp_path: Path,
) -> None:
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "people" / "jane.md")

    rows = listing.list_objects(bundle_dir)

    assert rows[0].link_dir == "people"


def test_root_level_doc_has_empty_link_dir(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "root.md")

    rows = listing.list_objects(bundle_dir)

    assert rows[0].concept_id == "root"
    assert rows[0].link_dir == ""


def test_title_whitespace_and_newlines_are_collapsed(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "a.md",
        title='"Multi   line\\ntitle"',
    )

    rows = listing.list_objects(bundle_dir)

    assert rows[0].title == "Multi line title"


def test_absent_title_renders_empty_string(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "a.md", title=None)

    rows = listing.list_objects(bundle_dir)

    assert rows[0].title == ""


# --- Phase 2: sensitivity derivation ----------------------------------------


@pytest.mark.parametrize("value", list(okf.SENSITIVITY_ORDER))
def test_valid_sensitivity_member_passes_through(tmp_path: Path, value: str) -> None:
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "a.md", extra_lines=[f"sensitivity: {value}"])

    rows = listing.list_objects(bundle_dir)

    assert rows[0].sensitivity == value


def test_absent_sensitivity_is_unknown(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "a.md")

    rows = listing.list_objects(bundle_dir)

    assert rows[0].sensitivity == "unknown"


def test_blank_sensitivity_is_unknown(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "a.md", extra_lines=['sensitivity: "   "'])

    rows = listing.list_objects(bundle_dir)

    assert rows[0].sensitivity == "unknown"


def test_garbage_sensitivity_is_unknown(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "a.md", extra_lines=["sensitivity: bogus"])

    rows = listing.list_objects(bundle_dir)

    assert rows[0].sensitivity == "unknown"


def test_non_string_sensitivity_is_unknown(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "a.md", extra_lines=["sensitivity: 123"])

    rows = listing.list_objects(bundle_dir)

    assert rows[0].sensitivity == "unknown"


# --- Phase 3: unreadable/unparseable rows are still listed -----------------


def test_unreadable_document_still_produces_a_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `DocScan` with `read_error` set still yields a row -- fail-visible,
    not fail-closed (design D5): id/link_dir come from the path and need no
    successful read; `title=""`, `sensitivity="unknown"`, `readable=False`
    (pattern: `tests/unit/test_sensitivity.py:103-114`)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "broken.md")

    original_iter_docs = okf._iter_docs

    def _raising_iter_docs(target_bundle_dir: Path) -> Iterator[okf.DocScan]:
        for scan in original_iter_docs(target_bundle_dir):
            yield okf.DocScan(
                path=scan.path,
                metadata=None,
                read_error=OSError("simulated unreadable file"),
                parse_error=None,
            )

    monkeypatch.setattr(okf, "_iter_docs", _raising_iter_docs)

    rows = listing.list_objects(bundle_dir)

    assert len(rows) == 1
    row = rows[0]
    assert row.concept_id == "concepts/broken"
    assert row.link_dir == "concepts"
    assert row.title == ""
    assert row.sensitivity == "unknown"
    assert row.readable is False


def test_unparseable_document_still_produces_a_row(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "bundle"
    path = bundle_dir / "concepts" / "malformed.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("---\ntitle: [unterminated\n---\nbody\n", encoding="utf-8")

    rows = listing.list_objects(bundle_dir)

    assert len(rows) == 1
    row = rows[0]
    assert row.concept_id == "concepts/malformed"
    assert row.title == ""
    assert row.sensitivity == "unknown"
    assert row.readable is False


# --- Phase 3: single-walk enforcement ---------------------------------------


def test_list_objects_performs_exactly_one_iter_docs_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-generator counting wrapper (design D3): the call is recorded at
    call time, not at first `next()` -- a `yield from` wrapper would
    silently defer that recording and stop proving anything."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "a.md")

    calls: list[Path] = []
    original = okf._iter_docs

    def _counting_iter_docs(target_bundle_dir: Path) -> Iterator[okf.DocScan]:
        calls.append(target_bundle_dir)
        return original(target_bundle_dir)

    monkeypatch.setattr(okf, "_iter_docs", _counting_iter_docs)

    listing.list_objects(bundle_dir)

    assert len(calls) == 1


# --- Phase 4: status derivation and drift guard -----------------------------


def test_own_status_deprecated_marks_row_deprecated(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "old.md", status="deprecated")

    rows = listing.list_objects(bundle_dir)

    assert rows[0].status == "deprecated"


def test_superseded_target_marked_deprecated_regardless_of_own_status(
    tmp_path: Path,
) -> None:
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "a.md",
        status="active",
        relations=[("concepts/b", "supersedes")],
    )
    _write_doc(bundle_dir / "concepts" / "b.md", status="active")

    rows = {row.concept_id: row for row in listing.list_objects(bundle_dir)}

    assert rows["concepts/a"].status == "active"
    assert rows["concepts/b"].status == "deprecated"


def test_self_superseding_edge_is_dropped_and_stays_active(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "a.md",
        status="active",
        relations=[("concepts/a", "supersedes")],
    )

    rows = listing.list_objects(bundle_dir)

    assert rows[0].status == "active"


def test_supersedes_cycle_marks_all_members_deprecated(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "a.md",
        status="active",
        relations=[("concepts/b", "supersedes")],
    )
    _write_doc(
        bundle_dir / "concepts" / "b.md",
        status="active",
        relations=[("concepts/a", "supersedes")],
    )

    rows = listing.list_objects(bundle_dir)

    assert {row.status for row in rows} == {"deprecated"}


def test_malformed_relations_contributes_no_edges_and_does_not_crash(
    tmp_path: Path,
) -> None:
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "broken.md",
        status="active",
        relations_raw="relations: not-a-list",
    )
    _write_doc(bundle_dir / "concepts" / "fine.md", status="deprecated")

    rows = {row.concept_id: row for row in listing.list_objects(bundle_dir)}

    assert rows["concepts/broken"].status == "active"
    assert rows["concepts/fine"].status == "deprecated"


def test_deprecated_status_drift_guard_against_lifecycle(tmp_path: Path) -> None:
    """`list_objects`'s in-pass status derivation must agree with
    `lifecycle.deprecated_concept_ids`, intersected with the ids that
    actually produced a row -- `lifecycle` can name a supersession target
    that has no file on disk (design D4)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "own.md", status="deprecated")
    _write_doc(
        bundle_dir / "concepts" / "a.md",
        status="active",
        relations=[("concepts/b", "supersedes")],
    )
    _write_doc(bundle_dir / "concepts" / "b.md", status="active")
    _write_doc(
        bundle_dir / "concepts" / "self.md",
        status="active",
        relations=[("concepts/self", "supersedes")],
    )
    _write_doc(
        bundle_dir / "concepts" / "cycle_a.md",
        status="active",
        relations=[("concepts/cycle_b", "supersedes")],
    )
    _write_doc(
        bundle_dir / "concepts" / "cycle_b.md",
        status="active",
        relations=[("concepts/cycle_a", "supersedes")],
    )

    rows = listing.list_objects(bundle_dir)
    row_ids = {row.concept_id for row in rows}
    listing_deprecated = {row.concept_id for row in rows if row.status == "deprecated"}

    assert listing_deprecated == (
        lifecycle.deprecated_concept_ids(bundle_dir) & row_ids
    )


# --- Phase 5: vocabulary resolver -------------------------------------------


_CANONICAL_LINK_DIRS = tuple(ot.link_dir for ot in REGISTRY if ot.link_dir)
_REGISTRY_NAMES = tuple(ot.name for ot in REGISTRY)


@pytest.mark.parametrize("link_dir", _CANONICAL_LINK_DIRS)
def test_resolve_link_dir_accepts_every_canonical_link_dir(link_dir: str) -> None:
    assert listing.resolve_link_dir(link_dir) == link_dir


@pytest.mark.parametrize("name", _REGISTRY_NAMES)
def test_resolve_link_dir_accepts_every_registry_name_alias(name: str) -> None:
    """Includes `Source` -- the case `types.TYPE_TO_LINK_DIR` would silently
    break, since that map is `llm_classifiable`-only and omits it (design
    D7 gotcha)."""
    expected = next(ot.link_dir for ot in REGISTRY if ot.name == name)
    assert listing.resolve_link_dir(name) == expected


def test_resolve_link_dir_rejects_empty_string() -> None:
    assert listing.resolve_link_dir("") is None


@pytest.mark.parametrize("wrong_case", ["People", "person"])
def test_resolve_link_dir_is_case_sensitive(wrong_case: str) -> None:
    assert listing.resolve_link_dir(wrong_case) is None


def test_resolve_link_dir_rejects_unknown_value() -> None:
    assert listing.resolve_link_dir("bogus-type") is None


# --- Phase 6: remaining coverage --------------------------------------------


def test_empty_bundle_returns_empty_list(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()

    rows = listing.list_objects(bundle_dir)

    assert rows == []


def test_rows_are_returned_in_alphabetical_id_order(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "zebra.md")
    _write_doc(bundle_dir / "concepts" / "apple.md")
    _write_doc(bundle_dir / "people" / "jane.md")

    rows = listing.list_objects(bundle_dir)

    assert [row.concept_id for row in rows] == [
        "concepts/apple",
        "concepts/zebra",
        "people/jane",
    ]
