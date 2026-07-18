"""Unit tests for `bundle/log.py`: rendering and appending to `log.md`."""

from datetime import date

import pytest

from openkos.bundle.log import insert_log_entry, render_log


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
