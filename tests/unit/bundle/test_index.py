"""Unit tests for `render_index`: the bytes of a fresh bundle's `index.md`."""

from openkos.bundle.index import render_index
from openkos.model import okf


def test_render_index_returns_version_frontmatter_and_empty_body() -> None:
    """A fresh `index.md` carries only `okf_version` and an empty body (scenario 2)."""
    metadata, body = okf.load_frontmatter(render_index())

    assert metadata == {"okf_version": "0.1"}
    assert body == ""
