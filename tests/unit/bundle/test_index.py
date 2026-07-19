"""Unit tests for `bundle/index.py`: rendering and appending to `index.md`."""

import pytest

from openkos.bundle.index import (
    insert_index_entry,
    insert_source_entry,
    remove_index_entry,
    render_index,
)
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
    (canonical order `[Concepts, Decisions, People, Organizations, Sources]`)."""
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


def test_insert_source_entry_stays_last_when_entities_section_present() -> None:
    """`insert_source_entry` still appends `# Sources` after EVERY other
    canonical section, including a populated `# Entities` section (canonical
    order `[Concepts, Entities, Decisions, People, Organizations,
    Sources]`)."""
    populated = (
        "---\n"
        'okf_version: "0.1"\n'
        "---\n"
        "\n"
        "# Concepts\n"
        "\n"
        "* [Stoicism](/concepts/stoicism.md) - A school of thought.\n"
        "\n"
        "# Entities\n"
        "\n"
        "* [Zettelkasten](/entities/zettelkasten.md) - A note-taking tool.\n"
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

    assert result == (
        populated + "\n# Sources\n\n"
        "* [Reading notes](/sources/reading-notes.md) - "
        "First pass through the text.\n"
    )


def test_insert_index_entry_creates_concepts_section_on_fresh_index() -> None:
    """`insert_index_entry(section="Concepts", link_dir="concepts")` creates a
    fresh `# Concepts` section on an empty-body index."""
    result = insert_index_entry(
        render_index(),
        section="Concepts",
        link_dir="concepts",
        title="Stoicism",
        slug="stoicism",
        description="A school of Hellenistic philosophy.",
    )

    assert "# Concepts" in result
    assert (
        "* [Stoicism](/concepts/stoicism.md) - "
        "A school of Hellenistic philosophy.\n" in result
    )


def test_insert_index_entry_appends_to_existing_concepts_section() -> None:
    """A second `insert_index_entry` call for `Concepts` appends a new
    bullet under the same section, keeping the first entry."""
    once = insert_index_entry(
        render_index(),
        section="Concepts",
        link_dir="concepts",
        title="Stoicism",
        slug="stoicism",
        description="First concept.",
    )

    twice = insert_index_entry(
        once,
        section="Concepts",
        link_dir="concepts",
        title="Apatheia",
        slug="apatheia",
        description="Second concept.",
    )

    assert twice.count("# Concepts") == 1
    assert "* [Stoicism](/concepts/stoicism.md) - First concept.\n" in twice
    assert "* [Apatheia](/concepts/apatheia.md) - Second concept.\n" in twice


def test_insert_index_entry_places_entities_between_concepts_and_decisions() -> None:
    """A fresh `# Entities` section is inserted at its canonical rank --
    after `# Concepts`, before `# Decisions` -- when both neighbors already
    exist (canonical order `[Concepts, Entities, Decisions, People,
    Organizations, Sources]`)."""
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
    )

    result = insert_index_entry(
        populated,
        section="Entities",
        link_dir="entities",
        title="Zettelkasten",
        slug="zettelkasten",
        description="A note-taking tool.",
    )

    assert result == (
        "---\n"
        'okf_version: "0.1"\n'
        "---\n"
        "\n"
        "# Concepts\n"
        "\n"
        "* [Stoicism](/concepts/stoicism.md) - A school of thought.\n"
        "\n"
        "# Entities\n"
        "\n"
        "* [Zettelkasten](/entities/zettelkasten.md) - A note-taking tool.\n"
        "\n"
        "# Decisions\n"
        "\n"
        "* [Frame the essay](/decisions/frame-the-essay.md) - A choice.\n"
    )


def test_insert_index_entry_places_entities_after_concepts_when_last_section() -> None:
    """`# Entities` is appended right after `# Concepts` when `Concepts` is
    currently the last (and only) existing section."""
    populated = (
        "---\n"
        'okf_version: "0.1"\n'
        "---\n"
        "\n"
        "# Concepts\n"
        "\n"
        "* [Stoicism](/concepts/stoicism.md) - A school of thought.\n"
    )

    result = insert_index_entry(
        populated,
        section="Entities",
        link_dir="entities",
        title="Zettelkasten",
        slug="zettelkasten",
        description="A note-taking tool.",
    )

    assert result == (
        populated + "\n# Entities\n\n"
        "* [Zettelkasten](/entities/zettelkasten.md) - A note-taking tool.\n"
    )


def test_insert_index_entry_places_entities_before_people_when_concepts_absent() -> (
    None
):
    """`# Entities` is inserted before `# People` even when `# Concepts` and
    `# Decisions` are both absent (canonical ordering holds regardless of
    which OTHER sections currently exist)."""
    populated = (
        "---\n"
        'okf_version: "0.1"\n'
        "---\n"
        "\n"
        "# People\n"
        "\n"
        "* [Maria Salazar](/people/maria-salazar.md) - A friend.\n"
        "\n"
        "# Sources\n"
        "\n"
        "* [Reading notes](/sources/reading-notes.md) - First pass.\n"
    )

    result = insert_index_entry(
        populated,
        section="Entities",
        link_dir="entities",
        title="Zettelkasten",
        slug="zettelkasten",
        description="A note-taking tool.",
    )

    assert result == (
        "---\n"
        'okf_version: "0.1"\n'
        "---\n"
        "\n"
        "# Entities\n"
        "\n"
        "* [Zettelkasten](/entities/zettelkasten.md) - A note-taking tool.\n"
        "\n"
        "# People\n"
        "\n"
        "* [Maria Salazar](/people/maria-salazar.md) - A friend.\n"
        "\n"
        "# Sources\n"
        "\n"
        "* [Reading notes](/sources/reading-notes.md) - First pass.\n"
    )


def test_insert_index_entry_full_canonical_order_from_scratch() -> None:
    """Inserting one entry per section in a DELIBERATELY SHUFFLED call order
    (Sources, People, Concepts, Decisions, Entities, Organizations) still
    yields the canonical `[Concepts, Entities, Decisions, People,
    Organizations, Sources]` section order in the final rendered index."""
    text = render_index()
    text = insert_index_entry(
        text,
        section="Sources",
        link_dir="sources",
        title="S",
        slug="s",
        description="s",
    )
    text = insert_index_entry(
        text, section="People", link_dir="people", title="P", slug="p", description="p"
    )
    text = insert_index_entry(
        text,
        section="Concepts",
        link_dir="concepts",
        title="C",
        slug="c",
        description="c",
    )
    text = insert_index_entry(
        text,
        section="Decisions",
        link_dir="decisions",
        title="D",
        slug="d",
        description="d",
    )
    text = insert_index_entry(
        text,
        section="Entities",
        link_dir="entities",
        title="E",
        slug="e",
        description="e",
    )
    text = insert_index_entry(
        text,
        section="Organizations",
        link_dir="organizations",
        title="O",
        slug="o",
        description="o",
    )

    headers_in_order = [line[2:] for line in text.splitlines() if line.startswith("# ")]
    assert headers_in_order == [
        "Concepts",
        "Entities",
        "Decisions",
        "People",
        "Organizations",
        "Sources",
    ]


def test_insert_index_entry_places_places_between_entities_and_decisions() -> None:
    """A fresh `# Places` section is inserted at its canonical rank -- after
    `# Entities`, before `# Decisions` -- when both neighbors already exist
    (canonical order `[Concepts, Entities, Places, Decisions, People,
    Organizations, Sources]`, spec: "Fresh Places section inserted at
    canonical rank")."""
    populated = (
        "---\n"
        'okf_version: "0.1"\n'
        "---\n"
        "\n"
        "# Concepts\n"
        "\n"
        "* [Stoicism](/concepts/stoicism.md) - A school of thought.\n"
        "\n"
        "# Entities\n"
        "\n"
        "* [Zettelkasten](/entities/zettelkasten.md) - A note-taking tool.\n"
        "\n"
        "# Decisions\n"
        "\n"
        "* [Frame the essay](/decisions/frame-the-essay.md) - A choice.\n"
        "\n"
        "# People\n"
        "\n"
        "* [Maria Salazar](/people/maria-salazar.md) - A friend.\n"
    )

    result = insert_index_entry(
        populated,
        section="Places",
        link_dir="places",
        title="Yellowstone National Park",
        slug="yellowstone-national-park",
        description="A national park in the western United States.",
    )

    assert result == (
        "---\n"
        'okf_version: "0.1"\n'
        "---\n"
        "\n"
        "# Concepts\n"
        "\n"
        "* [Stoicism](/concepts/stoicism.md) - A school of thought.\n"
        "\n"
        "# Entities\n"
        "\n"
        "* [Zettelkasten](/entities/zettelkasten.md) - A note-taking tool.\n"
        "\n"
        "# Places\n"
        "\n"
        "* [Yellowstone National Park](/places/yellowstone-national-park.md) - "
        "A national park in the western United States.\n"
        "\n"
        "# Decisions\n"
        "\n"
        "* [Frame the essay](/decisions/frame-the-essay.md) - A choice.\n"
        "\n"
        "# People\n"
        "\n"
        "* [Maria Salazar](/people/maria-salazar.md) - A friend.\n"
    )


def test_insert_index_entry_places_places_leaves_other_sections_byte_identical() -> (
    None
):
    """With all 7 canonical sections already present, inserting a second
    bullet into `# Places` round-trips every OTHER section's bullet
    byte-for-byte, and relative section order is unchanged (spec: "Existing
    sections keep byte-identical order")."""
    text = render_index()
    for section, link_dir in (
        ("Concepts", "concepts"),
        ("Entities", "entities"),
        ("Places", "places"),
        ("Decisions", "decisions"),
        ("People", "people"),
        ("Organizations", "organizations"),
        ("Sources", "sources"),
    ):
        text = insert_index_entry(
            text,
            section=section,
            link_dir=link_dir,
            title=f"First {section}",
            slug=f"first-{section.lower()}",
            description="First entry.",
        )
    other_section_bullets = [
        "* [First Concepts](/concepts/first-concepts.md) - First entry.\n",
        "* [First Entities](/entities/first-entities.md) - First entry.\n",
        "* [First Decisions](/decisions/first-decisions.md) - First entry.\n",
        "* [First People](/people/first-people.md) - First entry.\n",
        "* [First Organizations](/organizations/first-organizations.md) - "
        "First entry.\n",
        "* [First Sources](/sources/first-sources.md) - First entry.\n",
    ]
    for bullet in other_section_bullets:
        assert bullet in text

    result = insert_index_entry(
        text,
        section="Places",
        link_dir="places",
        title="Second Places",
        slug="second-places",
        description="Second entry.",
    )

    headers_in_order = [
        line[2:] for line in result.splitlines() if line.startswith("# ")
    ]
    assert headers_in_order == [
        "Concepts",
        "Entities",
        "Places",
        "Decisions",
        "People",
        "Organizations",
        "Sources",
    ]
    for bullet in other_section_bullets:
        assert bullet in result
    assert "* [First Places](/places/first-places.md) - First entry.\n" in result
    assert "* [Second Places](/places/second-places.md) - Second entry.\n" in result


def test_insert_index_entry_places_project_between_decisions_and_people() -> None:
    """A fresh `# Projects` section is inserted at its canonical rank --
    after `# Decisions`, before `# People` -- when both neighbors already
    exist (canonical order `[..., Decisions, Projects, People, ...]`, spec:
    "Decision and Project Route to Dedicated Catalog Sections")."""
    populated = (
        "---\n"
        'okf_version: "0.1"\n'
        "---\n"
        "\n"
        "# Decisions\n"
        "\n"
        "* [Frame the essay](/decisions/frame-the-essay.md) - A choice.\n"
        "\n"
        "# People\n"
        "\n"
        "* [Maria Salazar](/people/maria-salazar.md) - A friend.\n"
    )

    result = insert_index_entry(
        populated,
        section="Projects",
        link_dir="projects",
        title="Stoicism Essay Series",
        slug="stoicism-essay-series",
        description="An ongoing series of essays on Stoic practice.",
    )

    assert result == (
        "---\n"
        'okf_version: "0.1"\n'
        "---\n"
        "\n"
        "# Decisions\n"
        "\n"
        "* [Frame the essay](/decisions/frame-the-essay.md) - A choice.\n"
        "\n"
        "# Projects\n"
        "\n"
        "* [Stoicism Essay Series](/projects/stoicism-essay-series.md) - "
        "An ongoing series of essays on Stoic practice.\n"
        "\n"
        "# People\n"
        "\n"
        "* [Maria Salazar](/people/maria-salazar.md) - A friend.\n"
    )


def test_insert_index_entry_places_events_and_procedures_between_places_and_decisions() -> (
    None
):
    """Fresh `# Events` and `# Procedures` sections are inserted at their
    canonical rank -- after `# Places`, before `# Decisions` -- when both
    neighbors already exist (canonical order `[Concepts, Entities, Places,
    Events, Procedures, Decisions, Projects, People, Organizations,
    Sources]`, spec: "Event and Procedure Route to Dedicated Catalog
    Sections")."""
    populated = (
        "---\n"
        'okf_version: "0.1"\n'
        "---\n"
        "\n"
        "# Places\n"
        "\n"
        "* [Yellowstone](/places/yellowstone.md) - A national park.\n"
        "\n"
        "# Decisions\n"
        "\n"
        "* [Frame the essay](/decisions/frame-the-essay.md) - A choice.\n"
    )

    with_event = insert_index_entry(
        populated,
        section="Events",
        link_dir="events",
        title="Stoicon 2026",
        slug="stoicon-2026",
        description="An annual Stoicism conference.",
    )

    assert with_event == (
        "---\n"
        'okf_version: "0.1"\n'
        "---\n"
        "\n"
        "# Places\n"
        "\n"
        "* [Yellowstone](/places/yellowstone.md) - A national park.\n"
        "\n"
        "# Events\n"
        "\n"
        "* [Stoicon 2026](/events/stoicon-2026.md) - "
        "An annual Stoicism conference.\n"
        "\n"
        "# Decisions\n"
        "\n"
        "* [Frame the essay](/decisions/frame-the-essay.md) - A choice.\n"
    )

    with_procedure = insert_index_entry(
        with_event,
        section="Procedures",
        link_dir="procedures",
        title="Morning Journaling Routine",
        slug="morning-journaling-routine",
        description="A repeatable daily reflection practice.",
    )

    headers_in_order = [
        line[2:] for line in with_procedure.splitlines() if line.startswith("# ")
    ]
    assert headers_in_order == ["Places", "Events", "Procedures", "Decisions"]
    assert (
        "* [Morning Journaling Routine](/procedures/morning-journaling-routine.md) - "
        "A repeatable daily reflection practice.\n" in with_procedure
    )


def test_insert_index_entry_places_events_and_procedures_leaves_other_sections_byte_identical() -> (
    None
):
    """With all 9 canonical sections already present, inserting a second
    bullet into `# Events`/`# Procedures` round-trips every OTHER section's
    bullet byte-for-byte, and relative section order is unchanged (spec:
    "Existing sections keep byte-identical order")."""
    text = render_index()
    for section, link_dir in (
        ("Concepts", "concepts"),
        ("Entities", "entities"),
        ("Places", "places"),
        ("Events", "events"),
        ("Procedures", "procedures"),
        ("Decisions", "decisions"),
        ("People", "people"),
        ("Organizations", "organizations"),
        ("Sources", "sources"),
    ):
        text = insert_index_entry(
            text,
            section=section,
            link_dir=link_dir,
            title=f"First {section}",
            slug=f"first-{section.lower()}",
            description="First entry.",
        )
    other_section_bullets = [
        "* [First Concepts](/concepts/first-concepts.md) - First entry.\n",
        "* [First Entities](/entities/first-entities.md) - First entry.\n",
        "* [First Places](/places/first-places.md) - First entry.\n",
        "* [First Decisions](/decisions/first-decisions.md) - First entry.\n",
        "* [First People](/people/first-people.md) - First entry.\n",
        "* [First Organizations](/organizations/first-organizations.md) - "
        "First entry.\n",
        "* [First Sources](/sources/first-sources.md) - First entry.\n",
    ]
    for bullet in other_section_bullets:
        assert bullet in text

    result = insert_index_entry(
        text,
        section="Events",
        link_dir="events",
        title="Second Events",
        slug="second-events",
        description="Second entry.",
    )
    result = insert_index_entry(
        result,
        section="Procedures",
        link_dir="procedures",
        title="Second Procedures",
        slug="second-procedures",
        description="Second entry.",
    )

    headers_in_order = [
        line[2:] for line in result.splitlines() if line.startswith("# ")
    ]
    assert headers_in_order == [
        "Concepts",
        "Entities",
        "Places",
        "Events",
        "Procedures",
        "Decisions",
        "People",
        "Organizations",
        "Sources",
    ]
    for bullet in other_section_bullets:
        assert bullet in result
    assert "* [First Events](/events/first-events.md) - First entry.\n" in result
    assert "* [Second Events](/events/second-events.md) - Second entry.\n" in result
    assert (
        "* [First Procedures](/procedures/first-procedures.md) - First entry.\n"
        in result
    )
    assert (
        "* [Second Procedures](/procedures/second-procedures.md) - Second entry.\n"
        in result
    )


@pytest.mark.parametrize("field", ["title", "slug", "description"])
@pytest.mark.parametrize("newline", ["\n", "\r"])
def test_insert_index_entry_rejects_newline_in_interpolated_field(
    field: str, newline: str
) -> None:
    """The generalized inserter still guards `title`/`slug`/`description`
    against newline-forgery (RISK-1) for a non-Sources section, so untrusted
    LLM-derived text cannot forge a section header."""
    kwargs = {"title": "A", "slug": "a", "description": "B"}
    kwargs[field] = f"evil{newline}# Forged Section"

    with pytest.raises(ValueError, match="newline"):
        insert_index_entry(
            render_index(), section="Concepts", link_dir="concepts", **kwargs
        )


def test_insert_index_entry_rejects_unknown_section() -> None:
    """A `section` outside the canonical order has no defined rank, so the
    inserter fails closed with a clear `ValueError` rather than a cryptic
    tuple.index lookup error."""
    with pytest.raises(ValueError, match="section must be one of"):
        insert_index_entry(
            render_index(),
            section="Notes",
            link_dir="notes",
            title="A",
            slug="a",
            description="B",
        )


def test_insert_source_entry_delegates_to_insert_index_entry() -> None:
    """`insert_source_entry` is a thin `section="Sources", link_dir="sources"`
    wrapper around `insert_index_entry` -- both produce byte-identical
    output, and `main.py`'s call site keeps working unmodified."""
    via_wrapper = insert_source_entry(
        render_index(), title="A", slug="a", description="B"
    )
    via_generalized = insert_index_entry(
        render_index(),
        section="Sources",
        link_dir="sources",
        title="A",
        slug="a",
        description="B",
    )

    assert via_wrapper == via_generalized


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
