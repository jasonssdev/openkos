"""Renders the bytes of a fresh bundle's `log.md`."""

from datetime import date


def render_log(today: date) -> str:
    """Render a fresh `log.md`: heading, dated section, Initialization entry.

    `today` is a parameter rather than an internal `date.today()` call, so
    output is deterministic and testable; callers pass a timezone-aware
    timestamp's date (Ruff DTZ), per design's one testability injection.
    `log.md` is a reserved file and carries no frontmatter (§6/§7).
    """
    return (
        "# Directory Update Log\n"
        "\n"
        f"## {today.isoformat()}\n"
        "\n"
        "* **Initialization**: Created the bundle structure and the root "
        "[index](/index.md).\n"
    )
