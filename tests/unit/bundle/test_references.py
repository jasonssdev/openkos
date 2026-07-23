"""Unit tests for the detect-only inbound-reference helper
(`bundle/references.py`), used by `forget`'s Phase A detection (spec:
"Inbound Reference Detection").

`find_inbound_references` is a thin detect-only wrapper around
`bundle/links.py::find_inbound_link_rewrites` and
`bundle/relations.py::find_inbound_relation_rewrites`: it reuses both
scanners' pure Phase-A logic, discards their rewrite payloads, and keeps
only presence + kind (+ the actual relation `type`, recovered by decoding
each matched file's snapshot -- a `RelationRewrite` alone carries no type).
"""

from openkos.bundle import references
from openkos.model import okf


def _doc(metadata: dict[str, object], body: str = "Body.") -> str:
    base: dict[str, object] = {"type": "Concept"}
    base.update(metadata)
    return okf.dump_frontmatter(base, body)


def test_find_inbound_references_detects_link() -> None:
    """Requirement: Inbound Reference Detection -- a bundle-relative
    markdown link to the target id surfaces as a `kind="link"` record."""
    files = {"concepts/other.md": "See [Target](/concepts/target.md).\n"}

    refs = references.find_inbound_references(files, target_id="concepts/target")

    assert refs == [
        references.InboundReference(
            referrer_id="concepts/other", kind="link", relation_type=None
        )
    ]


def test_find_inbound_references_detects_typed_relation() -> None:
    """A typed `relations:` entry targeting the target id surfaces as a
    `kind="relation"` record carrying its actual type."""
    text = _doc({"relations": [{"target": "concepts/target", "type": "depends_on"}]})
    files = {"concepts/other.md": text}

    refs = references.find_inbound_references(files, target_id="concepts/target")

    assert refs == [
        references.InboundReference(
            referrer_id="concepts/other", kind="relation", relation_type="depends_on"
        )
    ]


def test_find_inbound_references_link_and_relation_from_same_referrer() -> None:
    """A referrer holding BOTH a link and a typed relation to the target
    produces two separate records, one per reference kind."""
    text = _doc(
        {"relations": [{"target": "concepts/target", "type": "supersedes"}]},
        "See [Target](/concepts/target.md).",
    )
    files = {"concepts/other.md": text}

    refs = references.find_inbound_references(files, target_id="concepts/target")

    assert len(refs) == 2
    assert (
        references.InboundReference(
            referrer_id="concepts/other", kind="link", relation_type=None
        )
        in refs
    )
    assert (
        references.InboundReference(
            referrer_id="concepts/other", kind="relation", relation_type="supersedes"
        )
        in refs
    )


def test_find_inbound_references_none_found() -> None:
    """No inbound link or relation anywhere -> empty list."""
    files = {"concepts/other.md": _doc({}, "Nothing here.")}

    refs = references.find_inbound_references(files, target_id="concepts/target")

    assert refs == []


def test_find_inbound_references_ignores_fenced_code_block_link() -> None:
    """A link inside a fenced code block is never a real reference (fence
    mask carried over from `find_inbound_link_rewrites`; spec:
    Inbound-Link Rewrite's fence-masking, reused here for detection)."""
    files = {
        "concepts/other.md": (
            "prose\n```\n[Target](/concepts/target.md)\n```\nmore prose\n"
        )
    }

    refs = references.find_inbound_references(files, target_id="concepts/target")

    assert refs == []


def test_find_inbound_references_detects_unverifiable_referrer_mentioning_target() -> (
    None
):
    """A file whose frontmatter fails to parse (malformed YAML) is reported
    as `kind="unverifiable"` when its RAW text mentions the target's
    canonical id -- it might reference the target, and `find_inbound_
    relation_rewrites` alone would silently `continue` past it (the CRITICAL
    fail-open this backstop closes; spec: "Inbound Reference Detection" MUST
    enumerate every inbound typed relation, even from an unparseable file
    that could plausibly reference the target)."""
    malformed = (
        "---\n"
        "type: Concept\n"
        "title: Bad\n"
        "relations: [target: concepts/target, type: depends_on\n"
        "---\n\n"
        "Body.\n"
    )
    files = {"concepts/other.md": malformed}

    refs = references.find_inbound_references(files, target_id="concepts/target")

    assert refs == [
        references.InboundReference(
            referrer_id="concepts/other", kind="unverifiable", relation_type=None
        )
    ]


def test_find_inbound_references_ignores_unverifiable_referrer_not_mentioning_target() -> (
    None
):
    """A file whose frontmatter fails to parse but whose raw text never
    mentions the target's canonical id is NOT reported -- the proportionate
    rule: an unrelated malformed file elsewhere in the bundle must never
    block an unrelated forget (spec: "Inbound Reference Detection")."""
    malformed = (
        "---\n"
        "type: Concept\n"
        "title: Bad\n"
        "relations: [target: concepts/unrelated, type: depends_on\n"
        "---\n\n"
        "Body.\n"
    )
    files = {"concepts/other.md": malformed}

    refs = references.find_inbound_references(files, target_id="concepts/target")

    assert refs == []


def test_find_inbound_references_excludes_self_reference() -> None:
    """A self-referencing link/relation living in the TARGET's own file is
    excluded from results -- defense-in-depth on top of the documented
    caller contract that `forget` never includes the target's own file in
    `files` in the first place (design: "Caller EXCLUDES target's own file
    from files -> self-refs never count")."""
    text = _doc(
        {"relations": [{"target": "concepts/target", "type": "related_to"}]},
        "Self-link: [me](/concepts/target.md).",
    )
    files = {"concepts/target.md": text}

    refs = references.find_inbound_references(files, target_id="concepts/target")

    assert refs == []
