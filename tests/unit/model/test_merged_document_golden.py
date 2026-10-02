"""Byte-exact goldens for `okf.build_merged_document`.

Pinned BEFORE the field-union core was factored out for attach-at-ingest,
so the refactor has an oracle: merge output must not change by one byte.
"""

import pytest

from openkos.model import okf

CASES: dict[str, tuple[dict[str, object], str, dict[str, object], str]] = {
    "basic_union": (
        {
            "type": "Concept",
            "title": "A",
            "description": "d",
            "tags": ["x", "y"],
            "status": "stable",
            "version": 1,
            "freshness": "snapshot",
            "sensitivity": "public",
            "provenance": ["sources/a"],
            "sources": [{"id": "sources/a"}],
            "generated": {"by": "openkos/1", "at": "2026-01-01T00:00:00Z"},
        },
        "# A\n\nbody one\n\n## Related\n\n- [sources/a](/sources/a.md) - src\n",
        {
            "type": "Concept",
            "title": "A2",
            "description": "d2",
            "tags": ["y", "z"],
            "status": "stable",
            "version": 1,
            "freshness": "timeless",
            "sensitivity": "confidential",
            "provenance": ["sources/b", "sources/a"],
            "generated": {"by": "openkos/1", "at": "2026-02-01T00:00:00Z"},
            "type_alternative": "Entity",
            "event_date": "2026-01-01",
            "extra": "fill",
        },
        "# A2\n\n## Heading\n\nbody two\n",
    ),
    "legacy_timestamp": (
        {
            "type": "Concept",
            "title": "A",
            "tags": [],
            "status": "active",
            "sensitivity": "private",
            "provenance": ["sources/a"],
            "timestamp": "2026-03-01T00:00:00Z",
            "freshness": "snapshot",
        },
        "# A\n\nx\n",
        {
            "type": "Concept",
            "title": "B",
            "tags": [],
            "sensitivity": "public",
            "provenance": ["sources/b"],
            "generated": {"by": "openkos/1", "at": "2026-01-01T00:00:00Z"},
            "freshness": "timeless",
        },
        "# B\n\ny\n",
    ),
    "relations": (
        {
            "type": "Concept",
            "title": "A",
            "sensitivity": "public",
            "provenance": ["sources/a"],
            "relations": [
                {"target": "concepts/z", "type": "related_to"},
                {"target": "concepts/b", "type": "related_to"},
            ],
            "generated": {"by": "o", "at": "2026-01-01T00:00:00Z"},
        },
        "# A\n",
        {
            "type": "Concept",
            "title": "B",
            "sensitivity": "public",
            "provenance": ["sources/b"],
            "relations": [
                {"target": "concepts/a", "type": "related_to"},
                {"target": "concepts/q", "type": "references"},
            ],
            "generated": {"by": "o", "at": "2026-01-01T00:00:00Z"},
        },
        "# B\n\nb body\n",
    ),
}

GOLDEN = {
    "basic_union": "---\ndescription: d\nextra: fill\nfreshness: timeless\ngenerated:\n  at: '2026-02-01T00:00:00Z'\n  by: openkos/1\nprovenance:\n- sources/a\n- sources/b\nsensitivity: confidential\nsources:\n- id: sources/a\n  resource: /sources/a.md\n- id: sources/b\n  resource: /sources/b.md\nstatus: stable\ntags:\n- x\n- y\n- z\ntitle: A\ntype: Concept\nversion: 1\n---\n\n# A\n\nbody one\n\n## Related\n\n- [sources/a](/sources/a.md) - src\n\n## Merged content (concepts/b)\n\n### A2\n\n#### Heading\n\nbody two\n",
    "legacy_timestamp": "---\nfreshness: snapshot\ngenerated:\n  at: '2026-03-01T00:00:00Z'\n  by: openkos/legacy\nprovenance:\n- sources/a\n- sources/b\nsensitivity: private\nsources:\n- id: sources/a\n  resource: /sources/a.md\n- id: sources/b\n  resource: /sources/b.md\nstatus: stable\ntags: []\ntitle: A\ntype: Concept\n---\n\n# A\n\nx\n\n## Merged content (concepts/b)\n\n### B\n\ny\n",
    "relations": "---\nfreshness: null\ngenerated:\n  at: '2026-01-01T00:00:00Z'\n  by: o\nprovenance:\n- sources/a\n- sources/b\nrelations:\n- target: concepts/q\n  type: references\n- target: concepts/z\n  type: related_to\nsensitivity: public\nsources:\n- id: sources/a\n  resource: /sources/a.md\n- id: sources/b\n  resource: /sources/b.md\ntitle: A\ntype: Concept\n---\n\n# A\n\n## Merged content (concepts/b)\n\n### B\n\nb body\n",
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_merged_document_is_byte_identical(name: str) -> None:
    survivor_meta, survivor_body, absorbed_meta, absorbed_body = CASES[name]
    meta, body = okf.build_merged_document(
        survivor_meta,
        survivor_body,
        absorbed_meta,
        absorbed_body,
        "concepts/b",
        "concepts/a",
    )
    assert okf.dump_frontmatter(meta, body) == GOLDEN[name]
