"""Unit tests for `model/okf.py`'s `migrate_document` (okf-v02-migration,
issue #1064, Phase 4): the pure, bytes-in/bytes-out per-document migration
from OKF v0.1 shape to v0.2 shape -- design.md Decision 8's rule table (R1
`generated`, R2 `status`, R3 `sources`, R4 `# Citations`), plus the
idempotency, builder-equivalence, and commutation properties design.md's
Testing Strategy requires.

Nothing calls `migrate_document` yet (Phase 6, `repair`, is the first
caller) -- this file is the whole test surface for the function."""

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from openkos.bundle import links as bundle_links
from openkos.bundle import provenance as bundle_provenance
from openkos.bundle import relations as bundle_relations
from openkos.model import okf

_FIXTURES_PATH = Path(__file__).resolve().parent / "fixtures" / "okf_v01_documents.json"


def _load_v01_fixtures() -> dict[str, dict[str, object]]:
    return json.loads(_FIXTURES_PATH.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def _migrated_text(result: object, original: str) -> str:
    """The resulting TEXT of a `MigrationResult`: `Migrated.text`, or the
    caller's OWN original input for `Unchanged` -- which carries no `text`
    field by design (design.md Decision 8: "the input bytes are returned
    untouched", proven structurally by task 4.13, not by a byte comparison
    here)."""
    if isinstance(result, okf.Migrated):
        return result.text
    if isinstance(result, okf.Unchanged):
        return original
    raise AssertionError(f"expected Migrated or Unchanged, got {result!r}")


# -- R1: `generated` (tasks 4.1-4.4) ------------------------------------------


def test_migrate_document_r1_generated_from_scalar_timestamp() -> None:
    """A quoted `timestamp` scalar and no `generated` migrates to
    `generated: {by: openkos/legacy, at: <the same value>}`, `timestamp`
    removed (okf-format-migration: "A v0.1 concept is rewritten to v0.2
    shape", the `generated` half)."""
    text = okf.dump_frontmatter(
        {"type": "Concept", "title": "Stoicism", "timestamp": "2026-07-14T09:00:00Z"},
        "# Stoicism\n",
    )

    result = okf.migrate_document(text)

    assert isinstance(result, okf.Migrated)
    assert result.changes.generated is True
    metadata, _ = okf.load_frontmatter(result.text)
    assert metadata["generated"] == {
        "by": okf.LEGACY_ACTOR,
        "at": "2026-07-14T09:00:00Z",
    }
    assert "timestamp" not in metadata


def test_migrate_document_r1_preserves_unquoted_timestamp_source_text() -> None:
    """A BARE, UNQUOTED `timestamp` (which PyYAML resolves to a `datetime`
    on load) migrates so `generated.at`'s SOURCE TEXT is the exact original
    unquoted string -- obtained via `yaml.compose`, never via
    `datetime.isoformat()` (which would rewrite `Z` as `+00:00`; design.md
    Decision 5's hard part)."""
    text = (
        "---\n"
        "type: Source\n"
        "title: Call Notes\n"
        "timestamp: 2026-07-14T09:00:00Z\n"
        "---\n"
        "Body.\n"
    )

    result = okf.migrate_document(text)

    assert isinstance(result, okf.Migrated)
    metadata, _ = okf.load_frontmatter(result.text)
    generated = metadata["generated"]
    assert isinstance(generated, dict)
    # The naive-conversion failure mode this test kills: a plain
    # `str(datetime.fromisoformat(...))` would produce
    # "2026-07-14 09:00:00+00:00" -- not the original `Z`-suffixed text.
    assert generated["at"] == "2026-07-14T09:00:00Z"
    assert generated["by"] == okf.LEGACY_ACTOR


@pytest.mark.parametrize(
    ("metadata_extra", "expect_refused"),
    [
        pytest.param(
            {"generated": {"by": "openkos/legacy", "at": "2020-01-01T00:00:00Z"}},
            False,
            id="generated_present_no_timestamp",
        ),
        pytest.param(
            {
                "generated": {"by": "openkos/legacy", "at": "2020-01-01T00:00:00Z"},
                "timestamp": "2020-06-01T00:00:00Z",
            },
            False,
            id="generated_present_with_leftover_timestamp",
        ),
        pytest.param({}, False, id="neither_key_present"),
        pytest.param(
            {"timestamp": {"at": "2020-01-01T00:00:00Z"}},
            True,
            id="timestamp_is_a_mapping",
        ),
        pytest.param(
            {"timestamp": ["2020-01-01T00:00:00Z"]},
            True,
            id="timestamp_is_a_list",
        ),
    ],
)
def test_migrate_document_r1_noop_and_refusal_cases(
    metadata_extra: dict[str, object], expect_refused: bool
) -> None:
    """`generated` present (with or without a leftover `timestamp`), or
    neither key present, is a no-op; a non-scalar `timestamp` (mapping or
    list) refuses rather than guessing (design.md Decision 8's R1 table)."""
    metadata: dict[str, object] = {
        "type": "Concept",
        "title": "X",
        "description": "Y",
        **metadata_extra,
    }
    text = okf.dump_frontmatter(metadata, "Body.\n")

    result = okf.migrate_document(text)

    if expect_refused:
        assert result == okf.Refused("timestamp is not a scalar")
    else:
        assert isinstance(result, okf.Unchanged)


def test_migrate_document_unparseable_or_missing_frontmatter_refuses() -> None:
    """No frontmatter block, or a block whose YAML fails to parse, refuses
    rather than crashing or guessing."""
    no_frontmatter = "# Just a document\n\nNo frontmatter block at all.\n"
    assert okf.migrate_document(no_frontmatter) == okf.Refused(
        "unparseable frontmatter"
    )

    malformed_yaml = "---\ntitle: [unterminated\n---\nBody.\n"
    assert okf.migrate_document(malformed_yaml) == okf.Refused(
        "unparseable frontmatter"
    )


# -- R2: `status` (task 4.5) --------------------------------------------------


@pytest.mark.parametrize(
    ("status", "expect_change"),
    [
        ("active", True),
        ("stable", False),
        ("draft", False),
        ("deprecated", False),
        ("an-unknown-value", False),
        (None, False),
    ],
)
def test_migrate_document_r2_status_active_to_stable(
    status: str | None, expect_change: bool
) -> None:
    """`status: active` migrates to `status: stable` in place; every other
    value -- including absent -- is left untouched (design.md Decision
    8's R2 rule, exact-match only)."""
    metadata: dict[str, object] = {
        "type": "Concept",
        "title": "X",
        "generated": {"by": "openkos/legacy", "at": "2020-01-01T00:00:00Z"},
    }
    if status is not None:
        metadata["status"] = status
    text = okf.dump_frontmatter(metadata, "Body.\n")

    result = okf.migrate_document(text)

    if expect_change:
        assert isinstance(result, okf.Migrated)
        assert result.changes.status is True
        new_metadata, _ = okf.load_frontmatter(result.text)
        assert new_metadata["status"] == "stable"
    else:
        assert isinstance(result, okf.Unchanged)


# -- R3: `sources` (task 4.6) --------------------------------------------------


def test_migrate_document_r3_sources_set_or_unchanged() -> None:
    """A `sources` projection that differs from the current value (absent
    included) is set via the refresh/insert rule; a document whose `sources`
    already matches the projection is left byte-unchanged by R3 (design.md
    Decision 8's R3 rule)."""
    base_metadata: dict[str, object] = {
        "type": "Concept",
        "title": "X",
        "generated": {"by": "openkos/legacy", "at": "2020-01-01T00:00:00Z"},
        "provenance": ["sources/foo"],
    }

    missing_sources = okf.dump_frontmatter(dict(base_metadata), "Body.\n")
    result = okf.migrate_document(missing_sources)
    assert isinstance(result, okf.Migrated)
    assert result.changes.sources is True
    new_metadata, _ = okf.load_frontmatter(result.text)
    assert new_metadata["sources"] == [
        {"id": "sources/foo", "resource": "/sources/foo.md"}
    ]

    already_matching = okf.dump_frontmatter(
        {
            **base_metadata,
            "sources": [{"id": "sources/foo", "resource": "/sources/foo.md"}],
        },
        "Body.\n",
    )
    result2 = okf.migrate_document(already_matching)
    assert isinstance(result2, okf.Unchanged)


# -- R4: `# Citations` (tasks 4.8-4.10) ---------------------------------------


def test_migrate_document_r4_removes_bare_trailing_citations_heading() -> None:
    """A `type: Source` document whose body ends with a bare `# Citations`
    heading (only whitespace after it) has that heading and the blank line
    before it removed, body ending in exactly one `\\n` (okf-format-
    migration: "A bare empty Citations heading is removed")."""
    text = okf.dump_frontmatter(
        {
            "type": "Source",
            "title": "X",
            "generated": {"by": "openkos/legacy", "at": "2020-01-01T00:00:00Z"},
        },
        "# X\n\nSome content.\n\n# Citations\n",
    )

    result = okf.migrate_document(text)

    assert isinstance(result, okf.Migrated)
    assert result.changes.citations_removed is True
    assert result.changes.legacy_citations is False
    _, body = okf.load_frontmatter(result.text)
    assert body == "# X\n\nSome content."
    assert result.text.endswith("Some content.\n")
    assert "# Citations" not in result.text


def test_migrate_document_reports_legacy_citations_without_converting() -> None:
    """A `# Citations` section carrying non-empty hand-authored content --
    including a NON-trailing bare heading -- is left byte-unchanged
    (`Unchanged`, no rewrite), and `legacy_citations` is `True` (okf-format-
    migration: "A non-empty Citations section survives migration")."""
    non_empty_trailing = okf.dump_frontmatter(
        {
            "type": "Source",
            "title": "X",
            "generated": {"by": "openkos/legacy", "at": "2020-01-01T00:00:00Z"},
        },
        "# X\n\nBody text.\n\n# Citations\n\n[1] A hand-written citation.\n",
    )
    result = okf.migrate_document(non_empty_trailing)
    assert isinstance(result, okf.Unchanged)
    assert result.legacy_citations is True

    bare_but_not_trailing = okf.dump_frontmatter(
        {
            "type": "Source",
            "title": "X",
            "generated": {"by": "openkos/legacy", "at": "2020-01-01T00:00:00Z"},
        },
        "# X\n\n# Citations\n\n## Another section\n\nMore content after.\n",
    )
    result2 = okf.migrate_document(bare_but_not_trailing)
    assert isinstance(result2, okf.Unchanged)
    assert result2.legacy_citations is True


def test_migrate_document_unchanged_returns_input_bytes_untouched() -> None:
    """A hand-formatted, already-v0.2 document -- unusual spacing/quoting --
    is never cosmetically re-serialized when no rule fires: `Unchanged`
    carries NO `text` field at all (design.md Decision 8), so the caller
    keeps its own input untouched by construction. Detection is exactly
    "would migration change the bytes"."""
    text = (
        "---\n"
        'title:   "Stoicism"\n'
        "type: Concept\n"
        "generated:\n"
        "  by: openkos/0.3.0\n"
        "  at: '2026-01-01T00:00:00Z'\n"
        "status: stable\n"
        "---\n"
        "Body.\n"
    )

    result = okf.migrate_document(text)

    assert isinstance(result, okf.Unchanged)
    assert result.legacy_citations is False


# -- Properties (tasks 4.13-4.17) ---------------------------------------------


@pytest.mark.parametrize("fixture_name", sorted(_load_v01_fixtures()))
def test_migrate_document_is_idempotent(fixture_name: str) -> None:
    """`migrate(migrate(x)) == migrate(x)`, compared on the resulting text
    (or both `Unchanged` on the second pass), over every entry of the frozen
    v0.1 fixture (okf-format-migration's idempotency requirement at the
    per-document level)."""
    fixture = _load_v01_fixtures()[fixture_name]
    original = fixture["text"]
    assert isinstance(original, str)

    once = okf.migrate_document(original)
    once_text = _migrated_text(once, original)

    twice = okf.migrate_document(once_text)

    assert (
        isinstance(twice, okf.Unchanged)
        or _migrated_text(twice, once_text) == once_text
    )


_BUILDERS: dict[str, Callable[..., str]] = {
    "build_concept": okf.build_concept,
    "build_source_concept": okf.build_source_concept,
}


@pytest.mark.parametrize("fixture_name", sorted(_load_v01_fixtures()))
def test_migrate_document_equivalent_to_new_builders(fixture_name: str) -> None:
    """`migrate_document(old_builder_output) == new_builder_output(same
    args, generated=Generated(by=LEGACY_ACTOR, at=<the old fixture's
    timestamp>))`, for every entry of the frozen v0.1 fixture (design.md's
    builder-equivalence property)."""
    fixture = _load_v01_fixtures()[fixture_name]
    original = fixture["text"]
    assert isinstance(original, str)
    builder_name = fixture["builder"]
    assert isinstance(builder_name, str)
    builder = _BUILDERS[builder_name]
    raw_args = fixture["args"]
    assert isinstance(raw_args, dict)
    args: dict[str, object] = dict(raw_args)
    timestamp = args.pop("timestamp")
    assert isinstance(timestamp, str)
    args["generated"] = okf.Generated(by=okf.LEGACY_ACTOR, at=timestamp)

    migrated = okf.migrate_document(original)
    migrated_text = _migrated_text(migrated, original)
    expected = builder(**args)

    assert migrated_text == expected


def _third_party_provenance_doc() -> str:
    return (
        "---\n"
        "type: Concept\n"
        "title: Third Party\n"
        "description: cites the absorbed concept\n"
        "timestamp: '2026-01-01T00:00:00Z'\n"
        "status: active\n"
        "provenance:\n"
        "- concepts/absorbed\n"
        "---\n"
        "Body.\n"
    )


def _third_party_relation_doc() -> str:
    return (
        "---\n"
        "type: Concept\n"
        "title: Third Party Rel\n"
        "description: relates to the absorbed concept\n"
        "timestamp: '2026-01-01T00:00:00Z'\n"
        "status: active\n"
        "relations:\n"
        "- target: concepts/absorbed\n"
        "  type: related-to\n"
        "---\n"
        "Body.\n"
    )


def _third_party_link_doc() -> str:
    return (
        "---\n"
        "type: Concept\n"
        "title: Third Party Link\n"
        "description: links to the absorbed concept\n"
        "timestamp: '2026-01-01T00:00:00Z'\n"
        "status: active\n"
        "---\n"
        "See [here](/concepts/absorbed.md) for more.\n"
    )


def test_migrate_document_commutes_with_provenance_and_relation_rewrites() -> None:
    """`migrate_document(apply_X(s)) == apply_X(migrate_document(s))` for
    `apply_provenance_rewrites`, `apply_relation_rewrites`, and
    `apply_link_rewrites` -- design.md's commutation property, load-bearing
    for Phase 5's link-offset math. Rewrites are built via the REAL bundle
    scan functions (`find_inbound_provenance_rewrites`/
    `find_inbound_relation_rewrites`/`find_inbound_link_rewrites`), never
    hand-constructed rewrite records."""
    provenance_doc = _third_party_provenance_doc()
    provenance_rewrites = bundle_provenance.find_inbound_provenance_rewrites(
        {"concepts/third-party.md": provenance_doc},
        absorbed_id="concepts/absorbed",
        survivor_id="concepts/survivor",
    )

    def apply_provenance(text: str) -> str:
        return bundle_provenance.apply_provenance_rewrites(
            text,
            file="concepts/third-party.md",
            survivor_id="concepts/survivor",
            absorbed_id="concepts/absorbed",
            rewrites=provenance_rewrites,
        )

    left = _migrated_text(
        okf.migrate_document(apply_provenance(provenance_doc)),
        apply_provenance(provenance_doc),
    )
    right = apply_provenance(
        _migrated_text(okf.migrate_document(provenance_doc), provenance_doc)
    )
    assert left == right

    relation_doc = _third_party_relation_doc()
    relation_rewrites = bundle_relations.find_inbound_relation_rewrites(
        {"concepts/third-party-rel.md": relation_doc},
        absorbed_id="concepts/absorbed",
        survivor_id="concepts/survivor",
    )

    def apply_relation(text: str) -> str:
        return bundle_relations.apply_relation_rewrites(
            text,
            file="concepts/third-party-rel.md",
            survivor_id="concepts/survivor",
            absorbed_id="concepts/absorbed",
            rewrites=relation_rewrites,
        )

    left_r = _migrated_text(
        okf.migrate_document(apply_relation(relation_doc)),
        apply_relation(relation_doc),
    )
    right_r = apply_relation(
        _migrated_text(okf.migrate_document(relation_doc), relation_doc)
    )
    assert left_r == right_r

    link_doc = _third_party_link_doc()
    link_rewrites = bundle_links.find_inbound_link_rewrites(
        {"concepts/third-party-link.md": link_doc},
        absorbed_id="concepts/absorbed",
        survivor_id="concepts/survivor",
    )

    def apply_link(text: str) -> str:
        return bundle_links.apply_link_rewrites(
            text, file="concepts/third-party-link.md", rewrites=link_rewrites
        )

    left_l = _migrated_text(
        okf.migrate_document(apply_link(link_doc)), apply_link(link_doc)
    )
    right_l = apply_link(_migrated_text(okf.migrate_document(link_doc), link_doc))
    assert left_l == right_l
