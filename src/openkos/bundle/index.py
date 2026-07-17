"""Renders the bytes of a fresh bundle's root `index.md`."""

from openkos.model import okf


def render_index() -> str:
    """Render a fresh root `index.md`: OKF version frontmatter, empty body."""
    return okf.dump_frontmatter({"okf_version": okf.OKF_VERSION})
