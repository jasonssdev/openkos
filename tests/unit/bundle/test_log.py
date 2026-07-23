"""Unit tests for `bundle/log.py`: rendering and appending to `log.md`."""

from datetime import date

import pytest

from openkos.bundle import index as bundle_index
from openkos.bundle.log import (
    LogEntry,
    insert_log_entry,
    read_recent_entries,
    remove_log_entry,
    render_log,
)


def test_render_log_has_no_frontmatter() -> None:
    """`log.md` is a reserved file with no frontmatter block (scenario 3)."""
    text = render_log(date(2026, 7, 16))

    assert not text.startswith("---")


def test_render_log_has_heading_dated_section_and_initialization_bullet() -> None:
    """The exact shape scenario 3 requires: heading, dated section, bullet."""
    text = render_log(date(2026, 7, 16))

    assert "# Directory Update Log" in text
    assert "## 2026-07-16" in text
    assert (
        "* **Initialization**: Created the bundle structure and the root "
        "[index](/index.md)." in text
    )


def test_insert_log_entry_creates_todays_section_at_top_when_absent() -> None:
    """`insert_log_entry` creates today's `## YYYY-MM-DD` section at the top
    of the log when it does not yet exist."""
    fresh = render_log(date(2026, 7, 5))

    result = insert_log_entry(
        fresh,
        date(2026, 7, 14),
        "**Creation**: Compiled [Call with Maria](/sources/call.md).",
    )

    assert result.index("## 2026-07-14") < result.index("## 2026-07-05")
    assert "* **Creation**: Compiled [Call with Maria](/sources/call.md).\n" in result


def test_insert_log_entry_prepends_within_existing_todays_section() -> None:
    """When today's section already exists, the new bullet is PREPENDED
    ahead of prior entries in that section, and prior entries remain
    unchanged."""
    log_text = (
        "# Directory Update Log\n"
        "\n"
        "## 2026-07-14\n"
        "\n"
        "* **Creation**: First entry of the day.\n"
    )

    result = insert_log_entry(
        log_text, date(2026, 7, 14), "**Creation**: Second entry of the day."
    )

    assert result == (
        "# Directory Update Log\n"
        "\n"
        "## 2026-07-14\n"
        "\n"
        "* **Creation**: Second entry of the day.\n"
        "* **Creation**: First entry of the day.\n"
    )


def test_insert_log_entry_leaves_prior_dated_sections_unchanged() -> None:
    """Prior dated sections stay byte-for-byte identical when a new entry is
    added to a different (new) day (scenario: prior entries unchanged)."""
    log_text = (
        "# Directory Update Log\n"
        "\n"
        "## 2026-07-05\n"
        "\n"
        "* **Creation**: Compiled the reading notes.\n"
        "* **Initialization**: Created the bundle structure and the root "
        "[index](/index.md).\n"
    )

    result = insert_log_entry(
        log_text, date(2026, 7, 14), "**Creation**: Compiled the call notes."
    )

    assert (
        "## 2026-07-05\n"
        "\n"
        "* **Creation**: Compiled the reading notes.\n"
        "* **Initialization**: Created the bundle structure and the root "
        "[index](/index.md).\n" in result
    )
    assert result == (
        "# Directory Update Log\n"
        "\n"
        "## 2026-07-14\n"
        "\n"
        "* **Creation**: Compiled the call notes.\n"
        "\n"
        "## 2026-07-05\n"
        "\n"
        "* **Creation**: Compiled the reading notes.\n"
        "* **Initialization**: Created the bundle structure and the root "
        "[index](/index.md).\n"
    )


def test_insert_log_entry_raises_valueerror_on_malformed_section_chunk() -> None:
    """A `## `-headed chunk with no blank line after the header raises
    `ValueError` instead of silently misparsing."""
    malformed = "# Directory Update Log\n\n## 2026-07-05\n* no blank line above\n"

    with pytest.raises(ValueError, match="malformed section chunk"):
        insert_log_entry(malformed, date(2026, 7, 14), "**Creation**: New.")


def test_read_recent_entries_flattens_newest_first_across_sections() -> None:
    """`read_recent_entries` flattens bullets across `## YYYY-MM-DD` sections
    newest-first, with NO sort -- the log is already newest-first by
    construction (`insert_log_entry` prepends), so this walks top-down (D4)."""
    log_text = (
        "# Directory Update Log\n"
        "\n"
        "## 2026-07-16\n"
        "\n"
        "* Bullet A\n"
        "\n"
        "## 2026-07-14\n"
        "\n"
        "* Bullet C\n"
    )

    entries = read_recent_entries(log_text, limit=5)

    assert entries == [
        LogEntry("2026-07-16", "Bullet A"),
        LogEntry("2026-07-14", "Bullet C"),
    ]


def test_read_recent_entries_stops_at_limit() -> None:
    """`read_recent_entries` stops flattening once `limit` entries are
    collected, even when more remain in the log."""
    log_text = (
        "# Directory Update Log\n"
        "\n"
        "## 2026-07-16\n"
        "\n"
        "* Bullet A\n"
        "* Bullet B\n"
        "\n"
        "## 2026-07-14\n"
        "\n"
        "* Bullet C\n"
    )

    entries = read_recent_entries(log_text, limit=2)

    assert entries == [
        LogEntry("2026-07-16", "Bullet A"),
        LogEntry("2026-07-16", "Bullet B"),
    ]


def test_read_recent_entries_stops_at_limit_mid_section() -> None:
    """`read_recent_entries` stops mid-section (not only at a section
    boundary) once `limit` is reached, leaving a later bullet in the SAME
    section unread."""
    log_text = (
        "# Directory Update Log\n"
        "\n"
        "## 2026-07-16\n"
        "\n"
        "* Bullet A\n"
        "* Bullet B\n"
        "* Bullet C\n"
    )

    entries = read_recent_entries(log_text, limit=2)

    assert entries == [
        LogEntry("2026-07-16", "Bullet A"),
        LogEntry("2026-07-16", "Bullet B"),
    ]


def test_read_recent_entries_preserves_multi_bullet_same_day_order() -> None:
    """Multiple bullets within the SAME day's section keep their on-disk
    order (top-to-bottom, no sort)."""
    log_text = (
        "# Directory Update Log\n"
        "\n"
        "## 2026-07-16\n"
        "\n"
        "* Bullet A\n"
        "* Bullet B\n"
        "* Bullet C\n"
    )

    entries = read_recent_entries(log_text, limit=5)

    assert [entry.text for entry in entries] == ["Bullet A", "Bullet B", "Bullet C"]
    assert all(entry.date == "2026-07-16" for entry in entries)


def test_read_recent_entries_skips_non_bullet_lines_within_section() -> None:
    """A non-`"* "`-prefixed line inside a section's body (e.g. a stray blank
    line) is skipped rather than treated as an entry."""
    log_text = "# Directory Update Log\n\n## 2026-07-16\n\n\n* Bullet A\n"

    entries = read_recent_entries(log_text, limit=5)

    assert entries == [LogEntry("2026-07-16", "Bullet A")]


def test_read_recent_entries_malformed_section_chunk_raises_valueerror() -> None:
    """A `## `-headed chunk with no blank line after the header raises
    `ValueError`, matching `insert_log_entry`'s malformed-chunk contract."""
    malformed = "# Directory Update Log\n\n## 2026-07-05\n* no blank line above\n"

    with pytest.raises(ValueError, match="malformed section chunk"):
        read_recent_entries(malformed, limit=5)


def test_read_recent_entries_empty_log_body_returns_empty_list() -> None:
    """A log with a header but no dated sections yet returns `[]` (scenario:
    empty log)."""
    assert read_recent_entries("# Directory Update Log\n", limit=5) == []


@pytest.mark.parametrize("newline", ["\n", "\r"])
def test_insert_log_entry_rejects_newline_in_entry(newline: str) -> None:
    """A newline in `entry` is REJECTED, not interpolated (RISK-2):
    unescaped, a value like `"evil\\n## 2099-01-01"` could forge a new
    dated section the next time `log.md` is parsed. A single log entry is
    always single-line, so rejecting is simpler and safer than escaping."""
    with pytest.raises(ValueError, match="newline"):
        insert_log_entry(
            render_log(date(2026, 7, 16)),
            date(2026, 7, 16),
            f"evil{newline}## 2099-01-01",
        )


# --- remove_log_entry (Slice 2: live log.md tombstone cleanup) -------------


def test_remove_log_entry_drops_bullet_matching_first_link() -> None:
    """`remove_log_entry` drops a bullet whose FIRST markdown link resolves
    to `concept_id`."""
    log_text = (
        "# Directory Update Log\n"
        "\n"
        "## 2026-07-16\n"
        "\n"
        "* [Reading notes](/sources/reading-notes.md) - Ingested.\n"
        "* [Sibling](/sources/sibling.md) - A surviving sibling.\n"
    )

    result, removed = remove_log_entry(log_text, "sources/reading-notes")

    assert removed == 1
    assert "[Reading notes]" not in result
    assert "[Sibling]" in result


def test_remove_log_entry_drops_tombstone_matching_anchor() -> None:
    """`remove_log_entry` drops a `forget`-style tombstone line whose
    `(id: <x>)` anchor equals `concept_id`, matching the real tombstone
    format `**Tombstone** (HH:MM:SSZ): Removed [<title>](/<id>.md)
    (id: <id>).`."""
    log_text = (
        "# Directory Update Log\n"
        "\n"
        "## 2026-07-16\n"
        "\n"
        "* **Tombstone** (12:00:00Z): Removed [Reading notes]"
        "(/sources/reading-notes.md) (id: sources/reading-notes).\n"
    )

    result, removed = remove_log_entry(log_text, "sources/reading-notes")

    assert removed == 1
    assert "Tombstone" not in result


def test_remove_log_entry_zero_matches_returns_unchanged() -> None:
    """A `concept_id` with no matching bullet returns `(log_text, 0)`
    UNCHANGED -- not an error."""
    log_text = (
        "# Directory Update Log\n"
        "\n"
        "## 2026-07-16\n"
        "\n"
        "* [Reading notes](/sources/reading-notes.md) - Ingested.\n"
    )

    result, removed = remove_log_entry(log_text, "sources/nonexistent")

    assert removed == 0
    assert result == log_text


def test_remove_log_entry_prose_mention_and_sibling_survive_untouched() -> None:
    """A surviving sibling's bullet AND a log line that merely MENTIONS the
    target id in prose (not as its own first link) are left byte-identical
    when `remove_log_entry` is called for an unrelated concept id (mirrors
    the collision-safety guarantee at the pure-function level)."""
    log_text = (
        "# Directory Update Log\n"
        "\n"
        "## 2026-07-16\n"
        "\n"
        "* [Sibling](/concepts/sibling.md) - A surviving sibling.\n"
        "* Reviewed provenance touching concepts/target during an audit.\n"
    )

    result, removed = remove_log_entry(log_text, "concepts/target")

    assert removed == 0
    assert result == log_text


def test_remove_log_entry_does_not_match_non_first_link_on_the_line() -> None:
    """Only the FIRST markdown link on a bullet line is the match
    candidate -- a bullet whose description mentions another concept must
    not be dropped when that OTHER concept is the target."""
    log_text = (
        "# Directory Update Log\n"
        "\n"
        "## 2026-07-16\n"
        "\n"
        "* [Stoicism](/concepts/stoicism.md) - See also "
        "[Epictetus](/people/epictetus.md).\n"
    )

    result, removed = remove_log_entry(log_text, "people/epictetus")

    assert removed == 0
    assert result == log_text


def test_remove_log_entry_reuses_bundle_index_matcher_not_a_fork() -> None:
    """`remove_log_entry` REUSES (imports, never re-implements)
    `bundle.index`'s `_LINK_RE`, `_BULLET_MARKERS`, and `_link_identity` --
    proven by object IDENTITY (`is`), since `from ... import name` binds a
    separate reference in `log.py`'s namespace that a same-named
    re-implementation could otherwise satisfy by coincidence; `is` proves
    it is the EXACT SAME object, i.e. one matcher, never a diverging fork."""
    from openkos.bundle import log as bundle_log

    assert getattr(bundle_log, "_link_identity") is bundle_index._link_identity  # noqa: B009
    assert getattr(bundle_log, "_LINK_RE") is bundle_index._LINK_RE  # noqa: B009
    assert getattr(bundle_log, "_BULLET_MARKERS") is bundle_index._BULLET_MARKERS  # noqa: B009
