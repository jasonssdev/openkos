"""`openkos.api.read_page` (`review-screen` change, design D1): a page as the
desktop app shows it -- a curated field set, never a frontmatter passthrough."""

from pathlib import Path

import pytest

from openkos.api import Page, PageSource, Workspace, read_page
from tests.unit.api import support
from tests.unit.application.curation_support import make_workspace
from tests.unit.cli.commit_phase_support import write_doc

PERSON = "people/marguerite-osei"
SOURCE = "sources/allotment-volunteers"


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = make_workspace(tmp_path, monkeypatch)
    write_doc(
        root,
        SOURCE,
        {
            "type": "Source",
            "title": "Allotment volunteers",
            "resource": "raw/allotment-volunteers.md",
            "generated": {"by": "openkos/0.3", "at": "2026-10-12T09:00:00Z"},
        },
        "## Source content\n\nRaw text.\n",
    )
    write_doc(
        root,
        PERSON,
        {
            "type": "Person",
            "title": "Marguerite  Osei",
            "sensitivity": "private",
            "provenance": [SOURCE],
            "extra": "never returned",
        },
        "# Marguerite Osei\n\nRuns the *allotment* rota with [Stoicism](/concepts/stoicism.md) [1].\n",
    )
    support.forbid_model_clients(monkeypatch, "a page read")
    return root


def _read(root: Path, concept_id: str) -> Page | None:
    return read_page(Workspace.open(root), concept_id)


def test_a_page_is_read_with_its_sources(root: Path) -> None:
    before = support.snapshot(root)

    page = _read(root, PERSON)

    assert page == Page(
        concept_id=PERSON,
        title="Marguerite Osei",
        okf_type="Person",
        excerpt="Runs the allotment rota with Stoicism.",
        sensitivity="private",
        sources=(
            PageSource(
                source_id=SOURCE,
                file_name="allotment-volunteers.md",
                added_at="2026-10-12T09:00:00Z",
            ),
        ),
    )
    assert support.snapshot(root) == before


def test_a_leading_heading_is_dropped_only_when_it_is_the_title(root: Path) -> None:
    write_doc(
        root, "concepts/x", {"type": "Concept", "title": "X"}, "# Other\n\nText.\n"
    )

    page = _read(root, "concepts/x")

    assert page is not None
    assert page.excerpt == "Other Text."


def test_a_long_excerpt_is_cut_at_a_word_boundary(root: Path) -> None:
    body = " ".join(["word"] * 100)  # 499 characters
    write_doc(root, "concepts/long", {"type": "Concept", "title": "Long"}, body)

    page = _read(root, "concepts/long")

    assert page is not None
    assert page.excerpt.endswith("word…")
    assert len(page.excerpt) <= 281
    assert page.excerpt == " ".join(["word"] * 56) + "…"


def test_an_excerpt_at_the_limit_is_not_cut(root: Path) -> None:
    body = "a" * 280
    write_doc(root, "concepts/edge", {"type": "Concept", "title": "Edge"}, body)

    page = _read(root, "concepts/edge")

    assert page is not None
    assert page.excerpt == body


def test_a_cut_on_a_space_keeps_the_last_whole_word(root: Path) -> None:
    kept = "x" * 275 + " abcd"  # 280 characters, then a space
    write_doc(root, "concepts/cut", {"type": "Concept", "title": "Cut"}, kept + " tail")

    page = _read(root, "concepts/cut")

    assert page is not None
    assert page.excerpt == kept + "…"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, "private"),
        ("", "private"),
        ("public", "public"),
        (" confidential ", "confidential"),
        ("secret", "confidential"),
        (3, "confidential"),
    ],
)
def test_sensitivity_fails_closed(root: Path, raw: object, expected: str) -> None:
    metadata: dict[str, object] = {"type": "Concept", "title": "S"}
    if raw is not None:
        metadata["sensitivity"] = raw
    write_doc(root, "concepts/s", metadata)

    page = _read(root, "concepts/s")

    assert page is not None
    assert page.sensitivity == expected


def test_only_source_documents_with_a_raw_file_are_listed(root: Path) -> None:
    write_doc(
        root,
        "concepts/other",
        {"type": "Concept", "title": "Other", "resource": "raw/other.md"},
    )
    write_doc(
        root,
        "sources/remote",
        {"type": "Source", "title": "Remote", "resource": "https://example.com/x"},
    )
    write_doc(
        root,
        "sources/undated",
        {"type": "Source", "title": "Undated", "resource": "raw/undated.txt"},
    )
    write_doc(
        root,
        "concepts/derived",
        {
            "type": "Concept",
            "title": "Derived",
            "provenance": [
                "concepts/other",
                "sources/remote",
                "sources/missing",
                "sources/undated",
                SOURCE,
            ],
        },
    )

    page = _read(root, "concepts/derived")

    assert page is not None
    assert [s.source_id for s in page.sources] == ["sources/undated", SOURCE]
    assert page.sources[0] == PageSource("sources/undated", "undated.txt", None)


def test_a_page_without_a_string_type_has_none(root: Path) -> None:
    write_doc(root, "concepts/t", {"type": ["Person"], "title": "T"})

    page = _read(root, "concepts/t")

    assert page is not None
    assert page.okf_type is None


@pytest.mark.parametrize("concept_id", ["../outside", "people/nobody", "/etc/passwd"])
def test_an_invalid_or_missing_id_is_none(root: Path, concept_id: str) -> None:
    before = support.snapshot(root)

    assert _read(root, concept_id) is None
    assert support.snapshot(root) == before


def test_an_unparseable_page_is_none(root: Path) -> None:
    path = root / "bundle" / "concepts" / "broken.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("---\ntitle: [unclosed\n---\nBody.\n", encoding="utf-8")

    assert _read(root, "concepts/broken") is None


def test_an_unquoted_added_timestamp_is_read_as_iso_text(root: Path) -> None:
    path = root / "bundle" / "sources" / "bare.md"
    path.write_text(
        "---\ntype: Source\ntitle: Bare\nresource: raw/bare.md\n"
        "generated:\n  at: 2026-10-12T09:00:00Z\n---\nText.\n",
        encoding="utf-8",
    )
    write_doc(
        root,
        "concepts/from-bare",
        {"type": "Concept", "title": "From bare", "provenance": ["sources/bare"]},
    )

    page = _read(root, "concepts/from-bare")

    assert page is not None
    assert page.sources == (
        PageSource("sources/bare", "bare.md", "2026-10-12T09:00:00+00:00"),
    )
