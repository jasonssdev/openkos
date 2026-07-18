"""Unit tests for `bundle/index.py`: rendering and appending to `index.md`."""

import pytest

from openkos.bundle.index import insert_source_entry, remove_index_entry, render_index
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


@pytest.mark.parametrize("field", ["title", "slug", "description"])
@pytest.mark.parametrize("newline", ["\n", "\r"])
def test_insert_source_entry_rejects_newline_in_interpolated_field(
    field: str, newline: str
) -> None:
    """A newline in `title`/`slug`/`description` is REJECTED, not
    interpolated (RISK-1): unescaped, a value like
    `"evil\\n# Forged Section"` could forge a section header the next time
    `index.md` is parsed. A single Source concept's title/slug/description
    is always single-line, so rejecting is simpler and safer than escaping."""
    kwargs = {"title": "A", "slug": "a", "description": "B"}
    kwargs[field] = f"evil{newline}# Forged Section"

    with pytest.raises(ValueError, match="newline"):
        insert_source_entry(render_index(), **kwargs)


_POPULATED_INDEX = (
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
    "\n"
    "# Sources\n"
    "\n"
    "* [Reading notes](/sources/reading-notes.md) - First pass through the text.\n"
)


@pytest.mark.parametrize(
    ("concept_id", "surviving_fragment"),
    [
        ("sources/reading-notes", "[Stoicism]"),
        ("concepts/stoicism", "[Reading notes]"),
        ("people/maria-salazar", "[Frame the essay]"),
        ("decisions/frame-the-essay", "[Maria Salazar]"),
    ],
)
def test_remove_index_entry_drops_matching_bullet_from_any_section(
    concept_id: str, surviving_fragment: str
) -> None:
    """`remove_index_entry` drops the matching bullet regardless of which of
    the four sections (Sources, Concepts, People, Decisions) it lives in --
    matching is by resolved link identity, not by section (#922)."""
    result, removed = remove_index_entry(_POPULATED_INDEX, concept_id)

    assert removed == 1
    assert concept_id.split("/")[-1] not in result or surviving_fragment in result
    assert surviving_fragment in result


def test_remove_index_entry_removes_exactly_the_matching_bullet_line() -> None:
    """Only the matched bullet line (plus its trailing newline) is removed;
    every other byte -- blank lines, other bullets -- is preserved verbatim."""
    result, removed = remove_index_entry(_POPULATED_INDEX, "sources/reading-notes")

    assert removed == 1
    expected = _POPULATED_INDEX.replace(
        "* [Reading notes](/sources/reading-notes.md) - First pass through the text.\n",
        "",
    )
    assert result == expected


@pytest.mark.parametrize(
    "link_target",
    [
        "/sources/reading-notes.md",
        "sources/reading-notes.md",
        "sources/reading-notes",
    ],
)
def test_remove_index_entry_matches_every_link_form(link_target: str) -> None:
    """Leading-slash, no-leading-slash, and extension-less link forms all
    normalize to the same identity and match `concept_id`."""
    index_text = (
        "---\n"
        'okf_version: "0.1"\n'
        "---\n"
        "\n"
        "# Sources\n"
        "\n"
        f"* [Reading notes]({link_target}) - First pass through the text.\n"
    )

    result, removed = remove_index_entry(index_text, "sources/reading-notes")

    assert removed == 1
    assert "[Reading notes]" not in result


def test_remove_index_entry_matches_link_with_trailing_fragment_and_title() -> None:
    """A trailing `#fragment` or quoted-title suffix is stripped before
    matching, mirroring `lint.normalize_link`'s narrower bundle-local twin."""
    index_text = (
        "---\n"
        'okf_version: "0.1"\n'
        "---\n"
        "\n"
        "# Sources\n"
        "\n"
        '* [Reading notes](/sources/reading-notes.md#intro "Reading notes") - '
        "First pass through the text.\n"
    )

    result, removed = remove_index_entry(index_text, "sources/reading-notes")

    assert removed == 1
    assert "[Reading notes]" not in result


def test_remove_index_entry_zero_matches_returns_unchanged() -> None:
    """A `concept_id` with no matching bullet returns `(index_text, 0)`
    UNCHANGED -- no unrelated bullet is dropped, and this is not an error
    (a file with no catalog entry is drift; deletion is still safe)."""
    result, removed = remove_index_entry(_POPULATED_INDEX, "sources/nonexistent")

    assert removed == 0
    assert result == _POPULATED_INDEX


def test_remove_index_entry_drops_all_duplicate_matches() -> None:
    """More than one bullet resolving to the same `concept_id` (a duplicate
    catalog entry) drops ALL of them and reports the total count."""
    index_text = (
        "---\n"
        'okf_version: "0.1"\n'
        "---\n"
        "\n"
        "# Sources\n"
        "\n"
        "* [Reading notes](/sources/reading-notes.md) - First pass.\n"
        "* [Reading notes again](/sources/reading-notes.md) - Duplicate entry.\n"
    )

    result, removed = remove_index_entry(index_text, "sources/reading-notes")

    assert removed == 2
    assert "[Reading notes]" not in result
    assert "[Reading notes again]" not in result


def test_remove_index_entry_preserves_frontmatter_verbatim() -> None:
    """The frontmatter block is preserved byte-for-byte across a removal."""
    result, removed = remove_index_entry(_POPULATED_INDEX, "sources/reading-notes")

    assert removed == 1
    frontmatter_block = _POPULATED_INDEX.split("---\n", 2)
    expected_frontmatter = "---\n" + frontmatter_block[1] + "---\n"
    assert result.startswith(expected_frontmatter)


def test_remove_index_entry_raises_valueerror_on_malformed_frontmatter() -> None:
    """A text that does not start with a `---`-delimited frontmatter block
    raises `ValueError`, reusing `_split_frontmatter_verbatim`'s contract."""
    with pytest.raises(ValueError, match="frontmatter"):
        remove_index_entry("# Concepts\n\n* [X](/concepts/x.md) - Y.\n", "concepts/x")


def test_remove_index_entry_accepts_hyphen_bullet_marker() -> None:
    """A hand-authored bullet using `- ` (not the engine's `* `) still
    matches -- both list markers are accepted (D2)."""
    index_text = (
        "---\n"
        'okf_version: "0.1"\n'
        "---\n"
        "\n"
        "# Concepts\n"
        "\n"
        "- [Stoicism](/concepts/stoicism.md) - A school of thought.\n"
    )

    result, removed = remove_index_entry(index_text, "concepts/stoicism")

    assert removed == 1
    assert "[Stoicism]" not in result


def test_remove_index_entry_does_not_match_non_first_link_on_the_line() -> None:
    """Only the FIRST markdown link on a bullet line is the match candidate --
    a bullet whose description happens to mention another concept must not
    be dropped when that OTHER concept is forgotten."""
    index_text = (
        "---\n"
        'okf_version: "0.1"\n'
        "---\n"
        "\n"
        "# Concepts\n"
        "\n"
        "* [Stoicism](/concepts/stoicism.md) - See also "
        "[Epictetus](/people/epictetus.md).\n"
    )

    result, removed = remove_index_entry(index_text, "people/epictetus")

    assert removed == 0
    assert result == index_text
