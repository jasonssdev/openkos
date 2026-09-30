"""The shipped `AGENTS.md` template teaches the shape of `provenance` correctly.

The template is the document an agent reads before it writes into a bundle, and
no test read its prose: it said `provenance` holds "paths relative to the
workspace root" while the ingestion spec, the lint, and the canonical example all
record Concept IDs of Source documents (the `sources/<id>` form).
"""

from pathlib import Path

_TEMPLATE = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "openkos"
    / "templates"
    / "agents.md.template"
)
_SPEC = (
    Path(__file__).resolve().parents[2] / "openspec" / "specs" / "ingestion" / "spec.md"
)


def _provenance_bullet() -> str:
    lines = [
        line
        for line in _TEMPLATE.read_text(encoding="utf-8").splitlines()
        if line.startswith("- **Preserve provenance.**")
    ]
    assert len(lines) == 1
    return lines[0]


def test_template_says_provenance_lists_concept_ids_of_source_documents() -> None:
    bullet = _provenance_bullet()

    assert "Concept IDs of Source documents" in bullet
    assert "`sources/<id>`" in bullet


def test_template_never_teaches_workspace_relative_paths() -> None:
    bullet = _provenance_bullet()

    assert "relative to the workspace root" not in bullet
    assert "never raw paths" in bullet


def test_spec_still_says_concept_ids_never_raw_paths() -> None:
    # The template's wording is pinned to this contract; if the spec moves, this
    # test names the template as the thing to re-check.
    assert "list of Concept IDs (never raw paths)" in " ".join(
        _SPEC.read_text(encoding="utf-8").split()
    )
