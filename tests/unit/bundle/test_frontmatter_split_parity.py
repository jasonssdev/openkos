"""PARITY test (okf-codec-seam WU1, design D4.1): proves `bundle.index` and
`bundle.source_titles`'s two independent `_split_frontmatter_verbatim`
copies split IDENTICAL `(block, body)` for the same inputs, and refuse an
absent frontmatter block with their own FULL, distinguishing message.

This test's power as a divergence detector exists ONLY on the pre-move
tree (design D4): once both wrappers delegate to one shared function
(WU2), an implementation divergence between them becomes structurally
impossible, and this test can then detect only a *label* divergence. It is
therefore landed here, in WU1, against the two pre-move copies, and its
falsification is recorded in the apply-progress transcript (design D4,
task 1.6) rather than re-derived here.
"""

import re

import pytest

from openkos.bundle import index as index_module
from openkos.bundle import source_titles as source_titles_module

# Shared corpus (design D4.1): each scenario isolates one framing property
# that the split must preserve byte-for-byte. Mirrored verbatim in
# `tests/unit/model/fixtures/okf_framing_goldens.json`'s "text" field per
# scenario (tests/unit/model/test_okf_framing_characterization.py) -- the
# two files pin the SAME corpus from two different angles: identical
# outputs across both copies here, and recorded exact bytes there.
FRONTMATTER_FRAMING_SCENARIOS: dict[str, str] = {
    "crlf_body": "---\nokf_version: 0.1\n---\nLine one\r\nLine two\r\n",
    "quoted_okf_version": "---\nokf_version: '0.1'\n---\nBody.\n",
    "triple_dash_in_body": "---\nokf_version: 0.1\n---\nBefore\n---\nAfter\n",
    "no_trailing_newline": "---\nokf_version: 0.1\n---\nBody without trailing newline",
    "empty_body": "---\nokf_version: 0.1\n---\n",
    "non_ascii": "---\nokf_version: 0.1\n---\nÀlgo en español: café, 中文, emoji 🎉\n",
    # Not in design D4.1's original 6-item list; added during apply (task
    # 1.6's falsification transcript) because none of the other scenarios
    # exercise `re.DOTALL`: a single-line frontmatter body never needs `.`
    # to cross an embedded `\n`, so dropping `re.DOTALL` from either
    # `_FRONTMATTER_RE` silently stayed GREEN against the original corpus.
    # Multi-line frontmatter content is exactly what `re.DOTALL` is FOR.
    "multiline_frontmatter": (
        "---\nokf_version: 0.1\nauthor: Jane\ntags: [a, b]\n---\nBody.\n"
    ),
}

FRONTMATTER_ABSENT_TEXT = "No frontmatter here.\nJust body text.\n"
"""No `---`-delimited block at all -- both callables must refuse, each with
its own operator-facing prefix (design D1's "Caller passes a wrong label"
row, guarded by the full-string assertions below)."""


@pytest.mark.parametrize("scenario", sorted(FRONTMATTER_FRAMING_SCENARIOS))
def test_both_copies_split_identically(scenario: str) -> None:
    """`index.py` and `source_titles.py`'s independent copies must agree,
    byte-for-byte, on `(block, body)` for every corpus scenario."""
    text = FRONTMATTER_FRAMING_SCENARIOS[scenario]

    index_result = index_module._split_frontmatter_verbatim(text)
    source_result = source_titles_module._split_frontmatter_verbatim(text)

    assert index_result == source_result
    block, body = index_result
    assert block + body == text


@pytest.mark.parametrize(
    ("callable_", "expected_label"),
    [
        (index_module._split_frontmatter_verbatim, "index.md"),
        (source_titles_module._split_frontmatter_verbatim, "Source document"),
    ],
    ids=["index", "source_titles"],
)
def test_absent_block_refuses_with_full_distinguishing_message(
    callable_: object, expected_label: str
) -> None:
    """Each copy's refusal carries its OWN full prefix, not the shared half
    -- the exact regression design D1 names as "Caller passes a wrong
    label" and design's proposal risk table names as "the only
    operator-visible difference" between the two failure paths."""
    with pytest.raises(
        ValueError, match=re.escape(f"{expected_label}: missing or malformed")
    ) as exc_info:
        callable_(FRONTMATTER_ABSENT_TEXT)  # type: ignore[operator]

    assert (
        str(exc_info.value)
        == f"{expected_label}: missing or malformed frontmatter block"
    )
