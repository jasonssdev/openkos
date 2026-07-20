"""Unit tests for inbound-link rewrite/reverse primitives (`bundle/links.py`).

`find_inbound_link_rewrites` is pure scan-only (spec: Inbound-Link Rewrite):
it reads bundle-relative text bodies already in memory and returns the
rewrites a later unit (U4) would apply -- it never writes anything.
`apply_link_rewrites` is pure text-in/text-out and MUST be bounded to
exactly one occurrence per recorded `LinkRewrite`, never a blind
replace-all (design's Bounded reversal note) -- this is what lets a
coincidental pre-existing identical `[y](/survivor.md)` link elsewhere in
the same file survive untouched, and what lets MULTIPLE occurrences of the
identical target each get rewritten in turn. `reverse_link_rewrites`
reverts EXACTLY at each rewrite's recorded `offset` -- the positional
disambiguator that tells apart a freshly-rewritten `](/survivor.md)`
occurrence from a coincidental pre-existing one sharing the same target,
which a target-string-only reverse cannot do.
"""

import pytest

from openkos.bundle import links
from openkos.model.okf import LinkRewrite


def test_find_inbound_link_rewrites_basic_match() -> None:
    """Requirement: Inbound-Link Rewrite -- a bundle-relative link to the
    absorbed id is rewritten to point at the survivor. `offset` is the
    position of `/concepts/absorbed.md` in the ORIGINAL text -- for a
    file's first (and only) occurrence, that's exactly where `new_link`
    will begin post-rewrite, since nothing before it changes length."""
    text = "See [Stoicism](/concepts/absorbed.md) for more.\n"
    files = {"concepts/other.md": text}

    rewrites = links.find_inbound_link_rewrites(
        files, absorbed_id="concepts/absorbed", survivor_id="concepts/survivor"
    )

    assert rewrites == [
        LinkRewrite(
            file="concepts/other.md",
            old_link="/concepts/absorbed.md",
            new_link="/concepts/survivor.md",
            offset=text.index("/concepts/absorbed.md"),
        )
    ]


def test_find_inbound_link_rewrites_preserves_anchor() -> None:
    """The `#anchor` suffix is preserved verbatim on both sides of the
    rewrite (spec: Inbound-Link Rewrite -- anchor preserved)."""
    text = "[link](/concepts/absorbed.md#section-1)\n"
    files = {"concepts/other.md": text}

    rewrites = links.find_inbound_link_rewrites(
        files, absorbed_id="concepts/absorbed", survivor_id="concepts/survivor"
    )

    assert rewrites == [
        LinkRewrite(
            file="concepts/other.md",
            old_link="/concepts/absorbed.md#section-1",
            new_link="/concepts/survivor.md#section-1",
            offset=text.index("/concepts/absorbed.md#section-1"),
        )
    ]


def test_find_inbound_link_rewrites_skips_fenced_code_block() -> None:
    """A link inside a fenced code block MUST NOT be rewritten (spec:
    Inbound-Link Rewrite -- fence-masked code-block links MUST NOT be
    rewritten)."""
    files = {
        "concepts/other.md": (
            "prose before\n```\n[link](/concepts/absorbed.md)\n```\nprose after\n"
        )
    }

    rewrites = links.find_inbound_link_rewrites(
        files, absorbed_id="concepts/absorbed", survivor_id="concepts/survivor"
    )

    assert rewrites == []


def test_find_inbound_link_rewrites_ignores_non_matching_link() -> None:
    """A link to an unrelated concept-id is left out of the result."""
    files = {"concepts/other.md": "[link](/concepts/unrelated.md)\n"}

    rewrites = links.find_inbound_link_rewrites(
        files, absorbed_id="concepts/absorbed", survivor_id="concepts/survivor"
    )

    assert rewrites == []


def test_find_inbound_link_rewrites_across_multiple_files() -> None:
    """The scan walks every file in the mapping, aggregating matches. Each
    file's `offset` is independent -- computed against THAT file's own
    resulting text, not a cross-file running total."""
    files = {
        "concepts/a.md": "[a](/concepts/absorbed.md)\n",
        "concepts/b.md": "[b](/concepts/absorbed.md#s)\nunrelated text\n",
        "concepts/c.md": "no links here\n",
    }

    rewrites = links.find_inbound_link_rewrites(
        files, absorbed_id="concepts/absorbed", survivor_id="concepts/survivor"
    )

    assert rewrites == [
        LinkRewrite(
            file="concepts/a.md",
            old_link="/concepts/absorbed.md",
            new_link="/concepts/survivor.md",
            offset=files["concepts/a.md"].index("/concepts/absorbed.md"),
        ),
        LinkRewrite(
            file="concepts/b.md",
            old_link="/concepts/absorbed.md#s",
            new_link="/concepts/survivor.md#s",
            offset=files["concepts/b.md"].index("/concepts/absorbed.md#s"),
        ),
    ]


def test_apply_link_rewrites_rewrites_recorded_occurrence() -> None:
    """`apply_link_rewrites` bounded-substitutes the recorded target only.
    (`offset` is unused by `apply_link_rewrites` itself -- only
    `reverse_link_rewrites` consumes it -- but is computed correctly here
    for consistency with how `find_inbound_link_rewrites` would produce
    it.)"""
    text = "See [Stoicism](/concepts/absorbed.md) for more.\n"
    rewrite = LinkRewrite(
        file="concepts/other.md",
        old_link="/concepts/absorbed.md",
        new_link="/concepts/survivor.md",
        offset=text.index("/concepts/absorbed.md"),
    )

    result = links.apply_link_rewrites(
        text, file="concepts/other.md", rewrites=[rewrite]
    )

    assert result == "See [Stoicism](/concepts/survivor.md) for more.\n"


def test_apply_link_rewrites_preserves_anchor() -> None:
    text = "[link](/concepts/absorbed.md#section-1)\n"
    rewrite = LinkRewrite(
        file="concepts/other.md",
        old_link="/concepts/absorbed.md#section-1",
        new_link="/concepts/survivor.md#section-1",
        offset=text.index("/concepts/absorbed.md#section-1"),
    )

    result = links.apply_link_rewrites(
        text, file="concepts/other.md", rewrites=[rewrite]
    )

    assert result == "[link](/concepts/survivor.md#section-1)\n"


def test_apply_link_rewrites_does_not_touch_preexisting_survivor_link() -> None:
    """Bounded rewrite: a coincidental pre-existing `[y](/survivor.md)` link
    elsewhere in the same file is NEVER altered by the rewrite of a
    different, recorded occurrence (design's Bounded reversal note)."""
    text = (
        "[absorbed link](/concepts/absorbed.md)\n"
        "[pre-existing survivor link](/concepts/survivor.md)\n"
    )
    rewrite = LinkRewrite(
        file="concepts/other.md",
        old_link="/concepts/absorbed.md",
        new_link="/concepts/survivor.md",
        offset=text.index("/concepts/absorbed.md"),
    )

    result = links.apply_link_rewrites(
        text, file="concepts/other.md", rewrites=[rewrite]
    )

    assert result == (
        "[absorbed link](/concepts/survivor.md)\n"
        "[pre-existing survivor link](/concepts/survivor.md)\n"
    )


def test_apply_link_rewrites_ignores_rewrites_for_other_files() -> None:
    """A rewrite recorded for a different `file` is not applied here --
    callers may pass the full ledger's `link_rewrites` list without
    pre-filtering by file."""
    text = "[link](/concepts/absorbed.md)\n"
    rewrite = LinkRewrite(
        file="concepts/unrelated.md",
        old_link="/concepts/absorbed.md",
        new_link="/concepts/survivor.md",
        offset=0,  # irrelevant -- filtered out by the file mismatch
    )

    result = links.apply_link_rewrites(
        text, file="concepts/other.md", rewrites=[rewrite]
    )

    assert result == text


def test_apply_link_rewrites_skips_fenced_code_block() -> None:
    """Even a (spuriously) recorded rewrite is never applied inside a
    fenced code block -- the same fence-mask boundary the scan honors."""
    text = "prose\n```\n[link](/concepts/absorbed.md)\n```\n"
    rewrite = LinkRewrite(
        file="concepts/other.md",
        old_link="/concepts/absorbed.md",
        new_link="/concepts/survivor.md",
        offset=text.index("/concepts/absorbed.md"),
    )

    result = links.apply_link_rewrites(
        text, file="concepts/other.md", rewrites=[rewrite]
    )

    assert result == text


def test_apply_link_rewrites_raises_when_old_link_absent() -> None:
    """Fail-closed: applying a rewrite whose `old_link` is not present in
    the text raises rather than silently no-op or corrupting text."""
    text = "no matching link here\n"
    rewrite = LinkRewrite(
        file="concepts/other.md",
        old_link="/concepts/absorbed.md",
        new_link="/concepts/survivor.md",
        offset=0,  # irrelevant -- old_link is absent, so apply raises first
    )

    with pytest.raises(ValueError, match="old_link"):
        links.apply_link_rewrites(text, file="concepts/other.md", rewrites=[rewrite])


def test_reverse_link_rewrites_round_trips_with_apply() -> None:
    """apply then reverse restores the ORIGINAL text byte-for-byte (spec:
    Unmerge Achieves Round-Trip Parity, as applied to link rewrites). A
    fenced occurrence of the SAME target is included to prove it survives
    both directions untouched. `offset` is the position `new_link` will
    occupy in the rewritten (post-apply) text -- computed from the EXPECTED
    rewritten text rather than hardcoded, so the fixture stays correct if
    the surrounding prose ever changes."""
    original = (
        "[absorbed link](/concepts/absorbed.md)\n"
        "prose\n```\n[fenced link](/concepts/absorbed.md)\n```\n"
    )
    expected_rewritten = (
        "[absorbed link](/concepts/survivor.md)\n"
        "prose\n```\n[fenced link](/concepts/absorbed.md)\n```\n"
    )
    rewrite = LinkRewrite(
        file="concepts/other.md",
        old_link="/concepts/absorbed.md",
        new_link="/concepts/survivor.md",
        offset=expected_rewritten.index("/concepts/survivor.md"),
    )

    rewritten = links.apply_link_rewrites(
        original, file="concepts/other.md", rewrites=[rewrite]
    )
    assert rewritten == expected_rewritten

    restored = links.reverse_link_rewrites(
        rewritten, file="concepts/other.md", rewrites=[rewrite]
    )

    assert restored == original


def test_reverse_link_rewrites_degrades_cleanly_when_new_link_absent() -> None:
    """Idempotence/safety: if the file changed since the merge (the
    recorded `new_link` is no longer present AT THE RECORDED OFFSET), reverse
    MUST fail closed rather than corrupt the text."""
    text = "the file no longer has that link at all\n"
    rewrite = LinkRewrite(
        file="concepts/other.md",
        old_link="/concepts/absorbed.md",
        new_link="/concepts/survivor.md",
        offset=0,
    )

    with pytest.raises(ValueError, match="new_link"):
        links.reverse_link_rewrites(text, file="concepts/other.md", rewrites=[rewrite])


def test_reverse_link_rewrites_ignores_rewrites_for_other_files() -> None:
    """Symmetric to `apply_link_rewrites`: a rewrite recorded for a
    different `file` is not reversed here either."""
    text = "[link](/concepts/survivor.md)\n"
    rewrite = LinkRewrite(
        file="concepts/unrelated.md",
        old_link="/concepts/absorbed.md",
        new_link="/concepts/survivor.md",
        offset=0,  # irrelevant -- filtered out by the file mismatch
    )

    result = links.reverse_link_rewrites(
        text, file="concepts/other.md", rewrites=[rewrite]
    )

    assert result == text


# -- Reversibility gap fix: exact-offset reverse (closes ambiguity before
# U5 byte-parity) ------------------------------------------------------


def test_reverse_link_rewrites_restores_byte_parity_when_file_also_has_a_preexisting_survivor_link() -> (
    None
):
    """Round-trip byte-parity (spec: Unmerge Achieves Round-Trip Parity) when
    a file links to BOTH the absorbed id AND, pre-existing, the survivor id:
    after merge there are TWO `](/concepts/survivor.md)` occurrences in the
    file -- one freshly rewritten, one already there before the merge. A
    target-only reverse cannot tell them apart and may flip the WRONG
    (pre-existing) one. `reverse_link_rewrites` MUST restore the file
    byte-for-byte: the pre-existing survivor link untouched, only the
    rewritten one restored."""
    file = "concepts/other.md"
    original = (
        "[absorbed link](/concepts/absorbed.md)\n"
        "[pre-existing survivor link](/concepts/survivor.md)\n"
    )

    rewrites = links.find_inbound_link_rewrites(
        {file: original},
        absorbed_id="concepts/absorbed",
        survivor_id="concepts/survivor",
    )
    rewritten = links.apply_link_rewrites(original, file=file, rewrites=rewrites)
    restored = links.reverse_link_rewrites(rewritten, file=file, rewrites=rewrites)

    assert restored == original


def test_apply_and_reverse_round_trip_multiple_absorbed_occurrences_right_to_left() -> (
    None
):
    """Multiple rewritten occurrences of the SAME absorbed id in one file
    (spec: Inbound-Link Rewrite) are all rewritten by `apply_link_rewrites`
    and all reversed exactly by `reverse_link_rewrites` -- applied
    RIGHT-TO-LEFT (descending recorded `offset`) so earlier offsets stay
    valid as later reversions change length, since `old-name`/`new` differ
    in length here -- byte-parity round-trip."""
    file = "concepts/other.md"
    original = "[a](/concepts/old-name.md)\nprose\n[b](/concepts/old-name.md)\n"

    rewrites = links.find_inbound_link_rewrites(
        {file: original}, absorbed_id="concepts/old-name", survivor_id="concepts/new"
    )
    assert len(rewrites) == 2

    rewritten = links.apply_link_rewrites(original, file=file, rewrites=rewrites)
    assert rewritten == "[a](/concepts/new.md)\nprose\n[b](/concepts/new.md)\n"

    restored = links.reverse_link_rewrites(rewritten, file=file, rewrites=rewrites)

    assert restored == original
