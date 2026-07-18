"""Unit tests for `bundle/index.py`: rendering and appending to `index.md`."""

import pytest

from openkos.bundle.index import insert_source_entry, render_index
from openkos.model import okf


def test_render_index_returns_version_frontmatter_and_empty_body() -> None:
    """A fresh `index.md` carries only `okf_version` and an empty body (scenario 2)."""
    metadata, body = okf.load_frontmatter(render_index())

    assert metadata == {"okf_version": "0.1"}
    assert body == ""


def test_insert_source_entry_creates_sources_section_on_fresh_index() -> None:
    """`insert_source_entry` creates `# Sources` on a fresh empty-body index."""
    result = insert_source_entry(
        render_index(),
        title="Call with Maria Salazar",
        slug="call-with-maria-2026-07-14",
        description="Private call correcting the apatheia reading.",
    )

    assert "# Sources" in result
    assert (
        "* [Call with Maria Salazar](/sources/call-with-maria-2026-07-14.md) - "
        "Private call correcting the apatheia reading.\n" in result
    )


def test_insert_source_entry_preserves_frontmatter_verbatim() -> None:
    """The frontmatter block is preserved byte-for-byte, unchanged by the edit."""
    original = render_index()

    result = insert_source_entry(original, title="A", slug="a", description="B")

    frontmatter_block = original.split("---\n", 2)
    expected_frontmatter = "---\n" + frontmatter_block[1] + "---\n"
    assert result.startswith(expected_frontmatter)


def test_insert_source_entry_appends_second_entry_to_existing_sources_section() -> None:
    """A second `insert_source_entry` call appends a new bullet under the same
    `# Sources` section, keeping the first entry (scenario: new entry preserves
    existing catalog)."""
    once = insert_source_entry(
        render_index(), title="First", slug="first", description="First source."
    )

    twice = insert_source_entry(
        once, title="Second", slug="second", description="Second source."
    )

    assert twice.count("# Sources") == 1
    assert "* [First](/sources/first.md) - First source.\n" in twice
    assert "* [Second](/sources/second.md) - Second source.\n" in twice


def test_insert_source_entry_round_trips_existing_sections_byte_for_byte() -> None:
    """An index with `Concepts`/`Decisions`/`People` sections already populated
    keeps them byte-for-byte identical when a new `Sources` entry is added
    (canonical order `[Concepts, Decisions, People, Sources]`)."""
    populated = (
        "---\n"
        'okf_version: "0.1"\n'
        "---\n"
        "\n"
        "# Concepts\n"
        "\n"
        "* [Stoicism](/concepts/stoicism.md) - A school of thought.\n"
        "\n"
        "# Decisions\n"
        "\n"
        "* [Frame the essay](/decisions/frame-the-essay.md) - A choice.\n"
        "\n"
        "# People\n"
        "\n"
        "* [Maria Salazar](/people/maria-salazar.md) - A friend.\n"
    )

    result = insert_source_entry(
        populated,
        title="Reading notes",
        slug="reading-notes",
        description="First pass through the text.",
    )

    assert (
        "# Concepts\n\n* [Stoicism](/concepts/stoicism.md) - A school of thought.\n"
        in result
    )
    assert (
        "# Decisions\n\n"
        "* [Frame the essay](/decisions/frame-the-essay.md) - A choice.\n" in result
    )
    assert (
        "# People\n\n* [Maria Salazar](/people/maria-salazar.md) - A friend.\n"
        in result
    )
    assert result == (
        populated + "\n# Sources\n\n"
        "* [Reading notes](/sources/reading-notes.md) - "
        "First pass through the text.\n"
    )


def test_insert_source_entry_appends_to_existing_sources_section_end() -> None:
    """When `# Sources` already exists with entries and later sections would
    not exist (Sources is last in canonical order), the new bullet is
    appended at the end of that section, not inserted elsewhere."""
    populated = (
        "---\n"
        'okf_version: "0.1"\n'
        "---\n"
        "\n"
        "# Sources\n"
        "\n"
        "* [Existing](/sources/existing.md) - Already there.\n"
    )

    result = insert_source_entry(
        populated, title="New", slug="new", description="Just added."
    )

    assert result == (populated + "* [New](/sources/new.md) - Just added.\n")


def test_insert_source_entry_raises_valueerror_on_missing_frontmatter() -> None:
    """A text that does not start with a `---`-delimited frontmatter block
    raises `ValueError` instead of silently misparsing."""
    with pytest.raises(ValueError, match="frontmatter"):
        insert_source_entry(
            "# Concepts\n\n* [X](/concepts/x.md) - Y.\n",
            title="A",
            slug="a",
            description="B",
        )


def test_insert_source_entry_raises_valueerror_on_malformed_section_chunk() -> None:
    """A `# `-headed chunk with no trailing newline after the header text
    raises `ValueError` instead of silently misparsing."""
    malformed = "---\nokf_version: '0.1'\n---\n\n# Concepts"

    with pytest.raises(ValueError, match="malformed section chunk"):
        insert_source_entry(malformed, title="A", slug="a", description="B")
