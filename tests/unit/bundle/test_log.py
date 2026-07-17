"""Unit tests for `render_log`: the bytes of a fresh bundle's `log.md`."""

from datetime import date

from openkos.bundle.log import render_log


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
