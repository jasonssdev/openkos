"""Inbound-link rewrite/reverse primitives for `merge`/`unmerge` (spec:
Inbound-Link Rewrite; ADR-0002's `link_rewrites`).

`find_inbound_link_rewrites` is the pure Phase-A SCAN: given every bundle
file's text already in memory, it finds bundle-relative markdown links
pointing at an absorbed concept-id and returns the `okf.LinkRewrite` records
a later unit (U4) would apply -- no file is read or written here, and no
CLI wiring happens in this module. Each returned record also carries the
`offset` where its `new_link` will begin in that file's post-merge text
(see `okf.LinkRewrite`'s docstring). `apply_link_rewrites` is pure
text-in/text-out and is BOUNDED to exactly one occurrence per recorded
`LinkRewrite` -- never a blind replace-all -- so a coincidental
pre-existing identical `[y](/survivor-id.md)` link elsewhere in the same
file is never touched by a rewrite of a *different* recorded occurrence,
and multiple recorded occurrences of the SAME target are each consumed in
turn. `reverse_link_rewrites` is the exact inverse `unmerge` (U5) needs for
round-trip parity -- made unambiguous by reverting at the recorded
`offset` rather than searching for `new_link`'s target string, which
cannot by itself distinguish a rewritten occurrence from a coincidental
pre-existing one sharing the same target.

`_LINK_RE`/`_mask_fenced_code_blocks` are a deliberate, intentional
DUPLICATE of `graph/sqlite_graph.py`'s copies (same bundle-relative
`[text](/….md)` link shape, same "blank out fenced lines" mask), not an
import -- the canonical layer (`openkos.model`, `openkos.bundle`,
`openkos.state`) MUST NOT import the derived `openkos.graph` package. This
mirrors `bundle/index.py`'s `_link_identity` precedent (#922: a narrower
bundle-local twin of a higher layer's link helper, kept separate rather
than inverting layering). Unlike `sqlite_graph.py`'s copy, `_LINK_RE` here
captures the `#anchor` suffix in its own group so it can be preserved
verbatim across a rewrite, rather than discarded.
"""

import re
from collections.abc import Mapping
from typing import Final

from openkos.model.okf import LinkRewrite

_LINK_RE: Final = re.compile(r"\[[^\]]*\]\(/([^)\s#]+\.md)(#[^)\s]*)?\)")
"""A bundle-relative `[text](/….md)` markdown link, per
`docs/knowledge-object-model.md`'s link shape. Group 1 is the `.md`-suffixed
bundle-relative path (the concept id once `.md` is stripped); group 2 is the
optional `#anchor` suffix INCLUDING its leading `#`, captured (not
discarded) so a rewrite can preserve it verbatim."""

_FENCE_MARKERS: Final = ("```", "~~~")


def _mask_fenced_code_blocks(body: str) -> str:
    """Blank out every line inside a fenced code block (fence markers
    included), keeping every other line byte-identical -- same algorithm as
    `graph/sqlite_graph.py`'s copy (intentionally duplicated, see module
    docstring). A masked line differs from the original at the SAME index,
    which is what lets `_iter_safe_lines` below detect "this line is inside
    a fence" without any character-offset bookkeeping.
    """
    lines = body.split("\n")
    masked: list[str] = []
    fence_marker: str | None = None
    for line in lines:
        stripped = line.lstrip()
        opens_or_closes = stripped.startswith(_FENCE_MARKERS)
        if fence_marker is None:
            if opens_or_closes:
                fence_marker = stripped[:3]
                masked.append("")
            else:
                masked.append(line)
        else:
            if opens_or_closes and stripped[:3] == fence_marker:
                fence_marker = None
            masked.append("")
    return "\n".join(masked)


def _iter_safe_lines(text: str) -> list[tuple[str, bool]]:
    """Split `text` into `(line, is_safe)` pairs, where `is_safe` is False
    for every line inside a fenced code block. Line-level (not
    character-offset) granularity keeps this immune to the mask's blanked
    lines having a different length than the original."""
    lines = text.split("\n")
    masked_lines = _mask_fenced_code_blocks(text).split("\n")
    return [
        (line, line == masked) for line, masked in zip(lines, masked_lines, strict=True)
    ]


def find_inbound_link_rewrites(
    files: Mapping[str, str], *, absorbed_id: str, survivor_id: str
) -> list[LinkRewrite]:
    """Pure Phase-A scan: find every bundle-relative markdown link, across
    every file in `files` (bundle-relative path -> full text already in
    memory), that resolves to `absorbed_id`, and return the `LinkRewrite`
    each one needs so a later unit can repoint it at `survivor_id`. A link
    inside a fenced code block is never matched (spec: Inbound-Link Rewrite
    -- fence-masked code-block links MUST NOT be rewritten). No file is
    read from disk and none is written here; `files` iteration order
    determines result order, and matches within a file are in line order.

    Each returned `LinkRewrite.offset` is the character offset, WITHIN THAT
    FILE's own resulting (post-merge) text, where this occurrence's
    `new_link` will begin once `apply_link_rewrites` substitutes it --
    computed by walking the same text `apply_link_rewrites` will produce
    (unchanged prefix, then either the original target's length for a
    non-matching link or `new_link`'s length for a rewritten one, then the
    closing `)`, one line at a time, joined by `"\\n"`) without actually
    building it. This is the positional disambiguator `reverse_link_rewrites`
    needs (see `LinkRewrite.offset`'s docstring); it is independent across
    files, since each file's rewrites are only ever applied to that file's
    own text.
    """
    rewrites: list[LinkRewrite] = []
    for file, text in files.items():
        safe_lines = _iter_safe_lines(text)
        offset = 0
        for index, (line, is_safe) in enumerate(safe_lines):
            if is_safe:
                last_end = 0
                for match in _LINK_RE.finditer(line):
                    path, anchor = match.group(1), match.group(2) or ""
                    concept_id = path.removesuffix(".md")
                    # The literal "/" preceding group 1 starts the target
                    # text; the target ends at the anchor (if present) or
                    # the path otherwise -- the closing ")" always follows.
                    target_start = match.start(1) - 1
                    target_end = match.end(2) if match.group(2) else match.end(1)

                    offset += len(line[last_end:target_start])
                    if concept_id == absorbed_id:
                        new_link = f"/{survivor_id}.md{anchor}"
                        rewrites.append(
                            LinkRewrite(
                                file=file,
                                old_link=f"/{path}{anchor}",
                                new_link=new_link,
                                offset=offset,
                            )
                        )
                        offset += len(new_link)
                    else:
                        offset += target_end - target_start
                    offset += len(line[target_end : match.end()])
                    last_end = match.end()
                offset += len(line[last_end:])
            else:
                offset += len(line)
            if index != len(safe_lines) - 1:
                offset += 1  # the "\n" `apply_link_rewrites` joins lines with
    return rewrites


def _substitute_target(
    text: str, *, old_target: str, new_target: str, missing_error: str
) -> str:
    """Shared bounded-substitution core for `apply_link_rewrites`: replace
    `](old_target)` with `](new_target)` at exactly ONE occurrence -- the
    first found in document (top-to-bottom) order among SAFE (non-fenced)
    lines -- leaving fenced lines and every OTHER occurrence (including any
    later occurrence of the SAME target) byte-identical. Matching on the
    closing `]( ... )` pair (rather than a blind substring search) means a
    target that only appears as plain prose text -- never inside a real
    link -- is never touched either.

    Bounding to exactly one occurrence per call is what lets a file with
    MULTIPLE occurrences of the identical `old_target` (e.g. two separate
    links to the same absorbed id) be rewritten correctly: `apply_link_rewrites`
    calls this once per recorded `LinkRewrite`, in the SAME top-to-bottom
    order `find_inbound_link_rewrites` recorded them in, so each call
    consumes the next remaining occurrence in turn -- never all of them at
    once, which would starve a later identical rewrite of any occurrence
    left to substitute.

    Two distinct "not found" outcomes are handled differently, so a
    (defensively passed) rewrite whose only occurrence lives inside a
    fenced code block degrades to a silent no-op rather than an error --
    it was never eligible for rewriting in the first place, so leaving it
    alone is correct, not a drift signal:

    - `old_target` present nowhere at all (safe or fenced) -- the file
      genuinely changed since the rewrite was recorded. Raises
      `ValueError` so the caller fails closed instead of corrupting text.
    - `old_target` present only inside a fenced code block -- never
      eligible for rewriting; returns `text` unchanged, no error.
    """
    pattern = re.compile(r"\]\(" + re.escape(old_target) + r"\)")
    replacement = f"]({new_target})"

    out_lines: list[str] = []
    found_anywhere = False
    substituted = False
    for line, is_safe in _iter_safe_lines(text):
        if pattern.search(line):
            found_anywhere = True
        if is_safe and not substituted and pattern.search(line):
            substituted = True
            out_lines.append(pattern.sub(replacement, line, count=1))
        else:
            out_lines.append(line)

    if not found_anywhere:
        raise ValueError(
            f"{missing_error}: no occurrence of link target "
            f"{old_target!r} found in text"
        )
    if not substituted:
        return text
    return "\n".join(out_lines)


def apply_link_rewrites(text: str, *, file: str, rewrites: list[LinkRewrite]) -> str:
    """Pure: apply every rewrite in `rewrites` whose `.file == file` to
    `text`, bounded to the recorded `{old_link, new_link}` occurrence(s)
    only. Rewrites recorded for a DIFFERENT file are ignored, so a caller
    may pass a merge ledger entry's full `link_rewrites` list without
    pre-filtering by file. Raises `ValueError` if a rewrite's `old_link` is
    not found on an unfenced line (fail-closed; see `_substitute_target`).
    """
    result = text
    for rewrite in rewrites:
        if rewrite.file != file:
            continue
        result = _substitute_target(
            result,
            old_target=rewrite.old_link,
            new_target=rewrite.new_link,
            missing_error="cannot apply link rewrite: old_link",
        )
    return result


def reverse_link_rewrites(text: str, *, file: str, rewrites: list[LinkRewrite]) -> str:
    """Pure inverse of `apply_link_rewrites`, made EXACT by the recorded
    `offset`: for every rewrite whose `.file == file` (others ignored),
    restore `old_link` in place of `new_link` at PRECISELY the recorded
    character offset -- never by searching for `new_link`'s target string.

    This is the fix for a real ambiguity a target-only reverse cannot
    resolve: when a file links to BOTH the absorbed and survivor concepts,
    the post-merge text has TWO occurrences shaped like `](/survivor.md)`
    (one just rewritten, one coincidentally pre-existing) -- a target-string
    search cannot tell them apart and may revert the wrong one, corrupting
    the pre-existing link and breaking byte-parity. Reversing at the exact
    recorded `offset` removes the ambiguity: each recorded rewrite reverts
    only its own occurrence, regardless of what else in the file happens to
    share its target string.

    Before substituting, the bytes at `offset` are verified to still be the
    recorded `new_link` -- an immediate-unmerge byte-parity assumption. If
    they are not (the file changed since the merge), this degrades cleanly
    with a `ValueError` rather than corrupting the text (spec: Unmerge
    Achieves Round-Trip Parity's idempotence/safety contract) -- the same
    fail-closed contract `apply_link_rewrites`'s absent-target case already
    established.

    When a file has multiple recorded rewrites, they are reverted
    RIGHT-TO-LEFT (descending `offset`) so an earlier offset stays valid
    even after a later (higher-offset) reversion changes the text's length.
    """
    file_rewrites = sorted(
        (rw for rw in rewrites if rw.file == file),
        key=lambda rw: rw.offset,
        reverse=True,
    )
    for rewrite in file_rewrites:
        end = rewrite.offset + len(rewrite.new_link)
        if text[rewrite.offset : end] != rewrite.new_link:
            raise ValueError(
                "cannot reverse link rewrite: new_link "
                f"{rewrite.new_link!r} not found at recorded offset "
                f"{rewrite.offset} in text"
            )
        text = text[: rewrite.offset] + rewrite.old_link + text[end:]
    return text
