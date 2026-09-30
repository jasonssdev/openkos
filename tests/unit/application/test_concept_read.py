"""Direct unit tests for `openkos.application.concept_read` (mcp-read-surface
slice 5, design Decision 4): `get`'s read core, exercised directly against a
real (tmp-path) workspace, never through the MCP tool or the CLI.

Mirrors `test_list_service.py`'s posture: no Typer runner, no MCP server --
the whole point of the application layer (ADR-0018) is that `read_concept`
is reachable and testable on its own.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from openkos import config, read_outcome
from openkos.application import concept_read
from openkos.model import okf


def _workspace(tmp_path: Path) -> config.WorkspaceLayout:
    config.write_config(tmp_path)
    layout = config.WorkspaceLayout(tmp_path)
    layout.bundle_dir.mkdir(parents=True, exist_ok=True)
    return layout


def _write_doc(path: Path, *, frontmatter_lines: list[str], body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "---\n" + "\n".join(frontmatter_lines) + "\n---\n" + body
    path.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# 5.1: only the curated field set ever comes back
# ---------------------------------------------------------------------------


def test_read_concept_curated_fields_only(tmp_path: Path) -> None:
    """An extra frontmatter key (`secret_ids`) is absent from the returned
    record entirely -- confirmed via `dataclasses.fields`, not just unused
    access. The body is the text as written, including a mention of a
    separate confidential concept BY NAME (the gate never touches prose --
    mcp's "A non-confidential note mentioning a confidential one is shown
    whole", P1)."""
    layout = _workspace(tmp_path)
    _write_doc(
        layout.bundle_dir / "concepts" / "pub.md",
        frontmatter_lines=[
            "type: Concept",
            "title: Public Note",
            "description: A one-line summary.",
            "sensitivity: public",
            "secret_ids:",
            "  - concepts/hidden",
        ],
        body="This note mentions concepts/hidden-secret by name.\n",
    )

    record = concept_read.read_concept(layout, "concepts/pub")

    assert isinstance(record, concept_read.ConceptRecord)
    field_names = {f.name for f in dataclasses.fields(record)}
    assert field_names == {
        "concept_id",
        "sensitivity",
        "type",
        "title",
        "description",
        "status",
        "body",
        "relations",
        "provenance",
        "not_run",
    }
    assert not hasattr(record, "secret_ids")
    assert record.concept_id == "concepts/pub"
    assert record.title == "Public Note"
    assert record.description == "A one-line summary."
    assert record.type == "Concept"
    assert record.sensitivity == "public"
    assert record.status == "stable"
    assert "concepts/hidden-secret" in record.body
    assert record.not_run == ()


def test_read_concept_never_discloses_source_frontmatter(tmp_path: Path) -> None:
    """design.md Decision 9 (preserve-source-frontmatter, issue #1062):
    `get`'s curated field set (`ConceptRecord`) never carries
    `source_frontmatter` -- no new MCP egress for the incoming mapping.
    Task 2.21. PRECONDITION: assert the on-disk document DOES carry the
    key, so an absent field never makes this pass vacuously for the wrong
    reason (a doc that never had the key in the first place)."""
    layout = _workspace(tmp_path)
    doc_path = layout.bundle_dir / "sources" / "notes.md"
    _write_doc(
        doc_path,
        frontmatter_lines=[
            "type: Source",
            "title: Notes",
            "description: A one-line summary.",
            "sensitivity: public",
            "source_frontmatter:",
            "  author: Jane",
        ],
        body="Body text.\n",
    )

    # PRECONDITION: the on-disk document actually carries the key.
    on_disk_metadata, _ = okf.load_frontmatter(doc_path.read_text(encoding="utf-8"))
    assert on_disk_metadata[okf.SOURCE_FRONTMATTER_KEY] == {"author": "Jane"}

    record = concept_read.read_concept(layout, "sources/notes")

    assert isinstance(record, concept_read.ConceptRecord)
    field_names = {f.name for f in dataclasses.fields(record)}
    assert okf.SOURCE_FRONTMATTER_KEY not in field_names
    assert not hasattr(record, okf.SOURCE_FRONTMATTER_KEY)


def test_concept_record_status_is_stable_not_active(tmp_path: Path) -> None:
    """A live (non-deprecated) concept's `ConceptRecord.status` reads
    `"stable"`, never `"active"` (okf-v02-migration design.md Decision 7,
    same display-vocabulary switch as `bundle/listing.py`)."""
    layout = _workspace(tmp_path)
    _write_doc(
        layout.bundle_dir / "concepts" / "pub.md",
        frontmatter_lines=[
            "type: Concept",
            "title: Public Note",
            "description: A one-line summary.",
            "sensitivity: public",
            "status: active",
        ],
        body="Body text.\n",
    )

    record = concept_read.read_concept(layout, "concepts/pub")

    assert isinstance(record, concept_read.ConceptRecord)
    assert record.status == "stable"


# ---------------------------------------------------------------------------
# 5.2: path-traversal refusals -- no read is attempted
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "concept_id",
    [
        "../../etc/passwd",
        "/etc/passwd",
        "index",
        "area/secret",
        "..\\..\\x",
        "C:\\x",
        "a:b",
    ],
)
def test_read_concept_path_traversal_refusals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, concept_id: str
) -> None:
    """Every case raises `ConceptNotFound`, and no read is ever attempted
    (a read spy on `Path.read_text` never fires)."""
    layout = _workspace(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    victim = outside / "secret.md"
    victim.write_text(
        okf.dump_frontmatter({"type": "Concept", "title": "Secret"}, "# Secret\n"),
        encoding="utf-8",
    )
    (layout.bundle_dir / "area").symlink_to(outside)

    def _spy_read_text(self: Path, *args: object, **kwargs: object) -> str:
        raise AssertionError(f"no read should have been attempted for {self}")

    monkeypatch.setattr(Path, "read_text", _spy_read_text)

    with pytest.raises(concept_read.ConceptNotFound):
        concept_read.read_concept(layout, concept_id)


# ---------------------------------------------------------------------------
# 5.3: unreadable content and malformed relations are data, not exceptions
# ---------------------------------------------------------------------------


def test_read_concept_unreadable_and_malformed(tmp_path: Path) -> None:
    """Invalid-UTF-8 bytes produce `UnreadableConcept`, not an exception.
    Malformed `relations:` (a `ValueError` from `okf.decode_relations`)
    produces `relations=()` plus a `NotRun("relations", ...)` entry, with
    the rest of the record intact."""
    layout = _workspace(tmp_path)
    broken = layout.bundle_dir / "concepts" / "broken.md"
    broken.parent.mkdir(parents=True, exist_ok=True)
    broken.write_bytes(b"---\ntype: Concept\ntitle: Broken\n---\n\xff\xfe garbage\n")

    unreadable = concept_read.read_concept(layout, "concepts/broken")
    assert unreadable == concept_read.UnreadableConcept(concept_id="concepts/broken")

    # A genuine frontmatter PARSE failure (not just unreadable bytes) --
    # same fixture `test_okf.py::test_check_conformance...` uses to trigger
    # a real `yaml.parser.ParserError` from `frontmatter.loads`.
    unparseable = layout.bundle_dir / "concepts" / "unparseable.md"
    unparseable.write_text("---\ntype: [unclosed\n---\nBody.\n", encoding="utf-8")

    unreadable_parse = concept_read.read_concept(layout, "concepts/unparseable")
    assert unreadable_parse == concept_read.UnreadableConcept(
        concept_id="concepts/unparseable"
    )

    _write_doc(
        layout.bundle_dir / "concepts" / "bad-relations.md",
        frontmatter_lines=[
            "type: Concept",
            "title: Bad Relations",
            "sensitivity: public",
            "relations: not-a-list",
        ],
        body="Body text.\n",
    )

    record = concept_read.read_concept(layout, "concepts/bad-relations")
    assert isinstance(record, concept_read.ConceptRecord)
    assert record.relations == ()
    assert record.title == "Bad Relations"
    assert record.body == "Body text."
    assert len(record.not_run) == 1
    entry = record.not_run[0]
    assert isinstance(entry, read_outcome.NotRun)
    assert entry.label == "relations"


# ---------------------------------------------------------------------------
# 6.1: concept_neighbors returns both directions, typed and untyped
# ---------------------------------------------------------------------------


def test_concept_neighbors_both_directions(tmp_path: Path) -> None:
    """A concept with an outbound typed relation, an outbound untyped link,
    and an INBOUND edge from another concept -- `concept_neighbors` returns
    all three, each with the correct `direction` (`"out"`/`"in"`) and
    `relation_type` (`None` for untyped, the type string for typed,
    `"derived_from"` for a provenance-derived edge); a missing concept id
    raises `ConceptNotFound`."""
    layout = _workspace(tmp_path)
    _write_doc(
        layout.bundle_dir / "concepts" / "target.md",
        frontmatter_lines=[
            "type: Concept",
            "title: Target",
            "sensitivity: public",
            "relations:",
            "  - target: concepts/typed-out",
            "    type: related_to",
        ],
        body="See [Untyped Out](/concepts/untyped-out.md) for more.\n",
    )
    _write_doc(
        layout.bundle_dir / "concepts" / "typed-out.md",
        frontmatter_lines=["type: Concept", "title: Typed Out", "sensitivity: public"],
        body="An outbound typed-relation target.\n",
    )
    _write_doc(
        layout.bundle_dir / "concepts" / "untyped-out.md",
        frontmatter_lines=[
            "type: Concept",
            "title: Untyped Out",
            "sensitivity: public",
        ],
        body="An outbound untyped-link target.\n",
    )
    _write_doc(
        layout.bundle_dir / "concepts" / "inbound.md",
        frontmatter_lines=[
            "type: Concept",
            "title: Inbound",
            "sensitivity: public",
            "relations:",
            "  - target: concepts/target",
            "    type: related_to",
        ],
        body="Points back at the target concept.\n",
    )

    neighborhood = concept_read.concept_neighbors(layout, "concepts/target")

    assert neighborhood.concept_id == "concepts/target"
    assert neighborhood.skipped_count == 0
    assert neighborhood.neighbors == (
        concept_read.Neighbor(
            concept_id="concepts/inbound", direction="in", relation_type="related_to"
        ),
        concept_read.Neighbor(
            concept_id="concepts/typed-out",
            direction="out",
            relation_type="related_to",
        ),
        concept_read.Neighbor(
            concept_id="concepts/untyped-out", direction="out", relation_type=None
        ),
    )

    with pytest.raises(concept_read.ConceptNotFound):
        concept_read.concept_neighbors(layout, "concepts/does-not-exist")
