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
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Final, Literal
from urllib.parse import quote, unquote

from openkos.model.okf import LinkRewrite, rename_foreign_segment

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


WITHHELD_LABEL: Final = "[withheld]"
"""What a link into a withheld object becomes in an exported body
(okf-export, #1301, ADR-0048): the target and the label both go, because an
engine-written label is the withheld object's title."""

_ROOT_RESERVED_IDS: Final = frozenset({"index", "log"})
"""Bundle-root reserved files an export always writes, so a link to them
never dangles and names no withheld object."""

_SCHEME_RE: Final = re.compile(r"\A[A-Za-z][A-Za-z0-9+.-]*:")

_INLINE_LINK_RE: Final = re.compile(
    r"!?\[((?:[^\[\]]|\[[^\[\]]*\])*)\]"
    r"\(\s*(<[^>]*>|[^)\s]+)(?:\s+(?:\"[^\"]*\"|'[^']*'))?\s*\)"
)
"""An inline link or image, `[label](target "title")`, the target optionally
in angle brackets. Group 2 is the raw target."""

_REFERENCE_DEFINITION_RE: Final = re.compile(
    r"\A {0,3}\[([^\]^][^\]]*)\]:\s*(<[^>]*>|\S+)(?:\s+.*)?\Z"
)
"""A reference-style definition line, `[ref]: target`. A label starting with
`^` is a footnote, not a link, and does not match."""

_FULL_REFERENCE_RE: Final = re.compile(r"!?\[((?:[^\[\]]|\[[^\[\]]*\])*)\]\[([^\]]*)\]")
_SHORTCUT_REFERENCE_RE: Final = re.compile(r"!?\[([^\[\]^][^\[\]]*)\](?![\[(:])")


@dataclass(frozen=True)
class LinkTarget:
    """How one markdown link destination resolves, in the frame of the
    document that carries it (okf-import, #1314).

    `kind` is `empty` (nothing), `anchor` (an in-page `#fragment` only),
    `external` (a `scheme:` URL) or `path`. For a `path`: `path` is the
    bundle-relative path with its suffix kept (`.md` or not), percent-escapes
    decoded, dot segments removed per RFC 3986 section 5.2.4 and any `..`
    above the root CLAMPED away (`escaped` records that one was); `absolute`
    says the destination named the root (`/...`); `suffix` is the raw
    `#fragment`. A `?query` stays part of the last segment, which is how
    every engine reader sees it."""

    kind: Literal["empty", "anchor", "external", "path"]
    path: str = ""
    suffix: str = ""
    escaped: bool = False
    absolute: bool = False


def _strip_angle(target: str) -> str:
    target = target.strip()
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1].strip()
    return target


def resolve_link_target(target: str, *, file_id: str) -> LinkTarget:
    """Resolve the raw markdown link destination `target`, written in the
    document `file_id`. A leading `/` is bundle-relative (any number of
    them); anything else is relative to the document's own directory. The
    one shared core of `_bundle_target_id` and of the import rewrite and its
    proof."""
    target = _strip_angle(target)
    base, hash_mark, fragment = target.partition("#")
    suffix = hash_mark + fragment
    base = unquote(base)
    if not base:
        return LinkTarget("anchor" if suffix else "empty", suffix=suffix)
    if _SCHEME_RE.match(base):
        return LinkTarget("external", suffix=suffix)
    absolute = base.startswith("/")
    if absolute:
        candidate = PurePosixPath(base.lstrip("/"))
    else:
        candidate = PurePosixPath(file_id).parent / base
    parts: list[str] = []
    escaped = False
    for part in candidate.parts:
        if part in ("", "."):
            continue
        if part == "..":
            if parts:
                parts.pop()
            else:
                escaped = True
        else:
            parts.append(part)
    return LinkTarget(
        "path", "/".join(parts), suffix=suffix, escaped=escaped, absolute=absolute
    )


def _bundle_target_id(target: str, *, file_id: str) -> str | None:
    """The concept id a markdown link `target` in document `file_id` points
    at, or `None` when it points at nothing that could be a concept: an
    external `scheme:` URL, a pure `#anchor`, a path that is not `.md`, or a
    path that escapes the bundle root. A leading `/` is bundle-relative;
    anything else is relative to the document's own directory."""
    resolved = resolve_link_target(target, file_id=file_id)
    if resolved.kind != "path" or resolved.escaped:
        return None
    if not resolved.path.endswith(".md"):
        return None
    return resolved.path.removesuffix(".md")


def _withholds(target: str, *, file_id: str, exported: AbstractSet[str]) -> bool:
    concept_id = _bundle_target_id(target, file_id=file_id)
    return (
        concept_id is not None
        and concept_id not in exported
        and concept_id not in _ROOT_RESERVED_IDS
    )


def withhold_links(body: str, *, file_id: str, exported: AbstractSet[str]) -> str:
    """`body` with every link into a concept outside `exported` replaced by
    `WITHHELD_LABEL` (okf-export, #1301, ADR-0048).

    Covers inline links and images in the bundle-relative and relative
    forms, and reference-style links: a definition into a withheld object
    is removed, and every use of its reference label becomes
    `WITHHELD_LABEL`. Links to exported concepts, to the bundle-root
    `index.md`/`log.md`, to external URLs, to anchors and to non-`.md`
    paths are kept. Text outside links is kept as written. A line inside a
    fenced code block is never edited: it is text, not a link, and the
    export's byte scan is what guards it.

    Returns the SAME `body` object when nothing changed. Unlike
    `find_inbound_link_rewrites`, this is a one-way export transform with no
    reversal; it does not change what `merge` or `forget` match."""
    safe_lines = _iter_safe_lines(body)
    withheld_refs: set[str] = set()
    kept: list[tuple[str, bool]] = []
    for line, is_safe in safe_lines:
        if is_safe:
            definition = _REFERENCE_DEFINITION_RE.match(line)
            if definition is not None and _withholds(
                definition.group(2), file_id=file_id, exported=exported
            ):
                withheld_refs.add(definition.group(1).strip().casefold())
                continue
        kept.append((line, is_safe))

    def inline(match: re.Match[str]) -> str:
        if _withholds(match.group(2), file_id=file_id, exported=exported):
            return WITHHELD_LABEL
        return match.group(0)

    def full_reference(match: re.Match[str]) -> str:
        ref = (match.group(2) or match.group(1)).strip().casefold()
        return WITHHELD_LABEL if ref in withheld_refs else match.group(0)

    def shortcut_reference(match: re.Match[str]) -> str:
        ref = match.group(1).strip().casefold()
        return WITHHELD_LABEL if ref in withheld_refs else match.group(0)

    out: list[str] = []
    for line, is_safe in kept:
        if is_safe:
            line = _INLINE_LINK_RE.sub(inline, line)
            if withheld_refs:
                line = _FULL_REFERENCE_RE.sub(full_reference, line)
                line = _SHORTCUT_REFERENCE_RE.sub(shortcut_reference, line)
        out.append(line)
    result = "\n".join(out)
    return body if result == body else result


# --- okf-import (#1314): rewrite a foreign body into its namespace -----------
#
# The engine has several independent link readers and they disagree: the
# graph and `_LINK_RE` read absolute `.md` targets only (the graph across
# lines, `_LINK_RE` per line), lint reads any target up to the first `)`
# (spaces allowed, extension-less, no unquoting), `_bundle_target_id` unquotes
# and `<>`-strips, and the index reader is root-relative. A rewriter built on
# any ONE of them misses a form another resolves, and a missed form is a
# foreign link resolving to an unrelated LOCAL document. Every reader needs
# the two characters `](` (inline, image) or a definition `]:`, so the
# rewrite anchors on the delimiter and scans the whole body, with no line
# split and no fence mask; the proof then re-reads the OUTPUT the way each
# reader does.

_SITE_RE: Final = re.compile(r"\](?:\(|:)")
"""A pointer-site delimiter: `](` opens an inline link or image destination,
`]:` a reference definition's."""

_TARGET_RUN_RE: Final = re.compile(r"[^)\s]*")
_TARGET_RUN_NO_HASH_RE: Final = re.compile(r"[^)\s#]*")
_LOOSE_DESTINATION_RE: Final = re.compile(r"\s*(<[^>]*>|[^)\s]+)")
_LOOSE_DEFINITION_RE: Final = re.compile(r"\s*(<[^>]*>|\S+)")
_HTML_ABSOLUTE_ATTRIBUTE_RE: Final = re.compile(
    r"""\b(?:href|src)\s*=\s*["']?\s*/""", re.IGNORECASE
)
_PERCENT_ENCODED_CHARS_RE: Final = re.compile(r"[\s()<>\"\\#%\[\]\x00-\x1f]")


@dataclass(frozen=True)
class PointerSite:
    """One place a markdown body points at something: `destination` is
    `body[start:end]`, read as CommonMark reads it; `origin` is the index
    just after the `](` / `]:` delimiter."""

    kind: Literal["inline", "definition"]
    origin: int
    start: int
    end: int
    destination: str


@dataclass(frozen=True)
class NamespacedBody:
    """A body rewritten into an import namespace: the new `text`, how many
    destinations changed and how many of those were clamped from above the
    foreign root, and whether the body carries a bundle-absolute raw HTML
    `href`/`src` (left as written: no engine reader resolves it)."""

    text: str
    links_rewritten: int
    links_clamped: int
    html_links: bool


def _skip_gap(body: str, pos: int) -> int:
    """Skip spaces and tabs and at most one line ending (CommonMark)."""
    size = len(body)
    while pos < size and body[pos] in " \t":
        pos += 1
    if pos < size and body[pos] in "\r\n":
        pos += 2 if body.startswith("\r\n", pos) else 1
        while pos < size and body[pos] in " \t":
            pos += 1
    return pos


def _read_destination(body: str, pos: int, *, inline: bool) -> int:
    """The end of the destination starting at `pos`: `<...>` (no line ending,
    no inner `<`), else a run of non-whitespace; for an inline link the run
    holds balanced parentheses and ends at the unbalanced `)`."""
    size = len(body)
    if body.startswith("<", pos):
        index = pos + 1
        while index < size:
            char = body[index]
            if char == "\\" and index + 1 < size:
                index += 2
                continue
            if char == ">":
                return index + 1
            if char in "<\r\n":
                break
            index += 1
    depth = 0
    index = pos
    while index < size:
        char = body[index]
        if char == "\\" and index + 1 < size and not body[index + 1].isspace():
            index += 2
            continue
        if char.isspace():
            break
        if inline:
            if char == "(":
                depth += 1
            elif char == ")":
                if depth == 0:
                    break
                depth -= 1
        index += 1
    return index


def _scan_sites(body: str) -> list[PointerSite]:
    """Every delimiter of `body` with the destination CommonMark reads after
    it, which may be empty."""
    sites: list[PointerSite] = []
    for match in _SITE_RE.finditer(body):
        inline = match.group(0) == "]("
        start = _skip_gap(body, match.end())
        end = _read_destination(body, start, inline=inline)
        sites.append(
            PointerSite(
                "inline" if inline else "definition",
                match.end(),
                start,
                end,
                body[start:end],
            )
        )
    return sites


def pointer_sites(body: str) -> list[PointerSite]:
    """Every pointer site of `body`, in document order: each `](` (inline
    link and image) and each definition `]:`. Anchored on the delimiter over
    the WHOLE body: no line split and no fence mask, because lint and the
    graph read links across lines and lint does not mask fences. A delimiter
    with no destination is not a site (the proof still reads it: see
    `namespace_link_violations`)."""
    return [site for site in _scan_sites(body) if site.end > site.start]


def _is_byte_preserving(inner: str) -> bool:
    """Whether `/imports/<ns>` may be inserted before `inner` verbatim: a
    single leading `/`, no percent-escape and no empty or dot segment."""
    base = inner.partition("#")[0]
    if not base.startswith("/") or base.startswith("//") or "%" in base:
        return False
    segments = base[1:].split("/")
    if segments[-1] == "":
        segments = segments[:-1]
    return not any(segment in ("", ".", "..") for segment in segments)


def _rename_path(path: str) -> str:
    """`path` with every segment renamed as the import renames a name with
    whitespace (`okf.rename_foreign_segment`). A `?query` on the last segment
    is not part of any name (a foreign name cannot hold a `?`), so it is kept
    as written."""
    segments = path.split("/")
    head, mark, query = segments[-1].partition("?")
    renamed = [rename_foreign_segment(segment) for segment in segments[:-1]]
    renamed.append(rename_foreign_segment(head) + mark + query)
    return "/".join(renamed)


def _renamed_own_path(inner: str) -> str | None:
    """The path the destination `inner` itself names (decoded, no `#fragment`),
    renamed, or `None` when no segment it names holds whitespace. Only what the
    link spells counts: a relative link inherits the whitespace of its
    document's directory, and that directory moves with the document."""
    base = unquote(_strip_angle(inner).partition("#")[0])
    renamed = _rename_path(base)
    return None if renamed == base else renamed


def _quote_path(path: str) -> str:
    return _PERCENT_ENCODED_CHARS_RE.sub(
        lambda found: quote(found.group(), safe=""), path
    )


def _namespaced_destination(
    destination: str, *, foreign_id: str, prefix: str
) -> tuple[str, bool] | None:
    """`(new destination, clamped)` for a destination that must change, or
    `None` when it stays as written."""
    angle = len(destination) >= 2 and destination[0] == "<" and destination[-1] == ">"
    inner = destination[1:-1] if angle else destination
    resolved = resolve_link_target(inner, file_id=foreign_id)
    if resolved.kind != "path":
        return None
    own = _renamed_own_path(inner)
    if not (resolved.absolute or resolved.escaped):
        if own is None:
            return None
        new_inner = f"{_quote_path(own)}{resolved.suffix}"
    elif (
        own is None
        and resolved.absolute
        and not resolved.escaped
        and _is_byte_preserving(inner)
    ):
        new_inner = f"/{prefix}{inner}"
    else:
        quoted = _quote_path(_rename_path(resolved.path))
        new_inner = f"/{prefix}/{quoted}{resolved.suffix}"
    return (f"<{new_inner}>" if angle else new_inner), resolved.escaped


def rewrite_links_into_namespace(
    body: str, *, foreign_id: str, prefix: str
) -> NamespacedBody:
    """`body`, the text of the foreign document `foreign_id`, with every
    pointer that points into the foreign bundle rewritten to resolve to the
    same document under `prefix` (`imports/<ns>`).

    A destination that is empty, an `#anchor`, a `scheme:` URL or relative
    and inside the foreign root is left as written. A relative one climbing
    above the root is replaced by `/<prefix>/<path clamped per RFC 3986
    section 5.2.4>`. An absolute one gets `/<prefix>` inserted byte for byte
    when its path is canonical, otherwise it is replaced by the clamped,
    re-quoted path. Fragment, query, title and every other byte are kept;
    fenced and inline code are rewritten too (over-rewriting an example is
    visible and harmless, leaving one out is not provable). Raw HTML
    attributes and `[[wiki]]` links are not pointer sites."""
    out: list[str] = []
    cursor = 0
    rewritten = 0
    clamped = 0
    for site in pointer_sites(body):
        if site.start < cursor:
            continue  # nested in an earlier destination: the proof judges it
        changed = _namespaced_destination(
            site.destination, foreign_id=foreign_id, prefix=prefix
        )
        if changed is None:
            continue
        new_destination, was_clamped = changed
        out.append(body[cursor : site.start])
        out.append(new_destination)
        cursor = site.end
        rewritten += 1
        clamped += int(was_clamped)
    out.append(body[cursor:])
    return NamespacedBody(
        "".join(out),
        rewritten,
        clamped,
        _HTML_ABSOLUTE_ATTRIBUTE_RE.search(body) is not None,
    )


def _site_readings(body: str, site: PointerSite) -> list[str]:
    """Every string some engine reader can take as the destination of
    `site`."""
    readings = [site.destination]  # CommonMark
    if site.kind == "inline":
        close = body.find(")", site.origin)
        if close != -1:
            first_paren = body[site.origin : close]  # lint, index: `[^)]+`
            readings.append(first_paren)
            titled = first_paren.split("#", 1)[0].strip()
            if titled.endswith('"') and ' "' in titled:
                readings.append(titled.rsplit(' "', 1)[0].strip())  # title strip
        run = _TARGET_RUN_RE.match(body, site.origin)
        if run is not None:
            readings.append(run.group())  # reconciliation: `[^)\s]+`
        run = _TARGET_RUN_NO_HASH_RE.match(body, site.origin)
        if run is not None:
            readings.append(run.group())  # graph, links: `[^)\s#]+`
        loose = _LOOSE_DESTINATION_RE.match(body, site.origin)
        if loose is not None:
            readings.append(loose.group(1))  # `_INLINE_LINK_RE`
    else:
        loose = _LOOSE_DEFINITION_RE.match(body, site.origin)
        if loose is not None:
            readings.append(loose.group(1))  # `_REFERENCE_DEFINITION_RE`
    return readings


def _outside_namespace(
    reading: str, *, concept_id: str, prefix: str, decode: bool, unangle: bool
) -> str | None:
    """Why `reading`, resolved from the document `concept_id` the way one
    reader would, leaves `prefix`, or `None` when it stays inside (or points
    at nothing: empty, an anchor, a `scheme:` URL)."""
    target = reading.strip()
    if unangle and target.startswith("<") and target.endswith(">"):
        target = target[1:-1].strip()
    target = target.split("#", 1)[0]
    if decode:
        target = unquote(target)
    target = target.strip()
    if not target or _SCHEME_RE.match(target):
        return None
    if target.startswith("/"):
        candidate = PurePosixPath(target.lstrip("/"))
    else:
        candidate = PurePosixPath(concept_id).parent / target
    parts: list[str] = []
    for part in candidate.parts:
        if part in ("", "."):
            continue
        if part == "..":
            if not parts:
                return "climbs out of the bundle"
            parts.pop()
        else:
            parts.append(part)
    resolved = "/".join(parts)
    if resolved == prefix or resolved.startswith(f"{prefix}/"):
        return None
    return f"resolves to `{resolved}`"


def namespace_link_violations(body: str, *, concept_id: str, prefix: str) -> list[str]:
    """The proof: every way a pointer of `body` (the adopted text of the
    local document `concept_id`) can leave `prefix`, empty when none can.

    Every pointer site of the OUTPUT is re-read in the LOCAL frame under
    every reading an engine reader can take (CommonMark, up to the first
    `)`, up to the first whitespace, after lint's ` "title"` strip), each
    raw and percent-decoded, each with and without `<>` stripped. A
    destination that is external, an anchor, empty, or a path under
    `prefix/` passes; anything else (a link left at `/concepts/x.md`, a
    relative link climbing out of the bundle) is a violation naming the
    destination."""
    violations: list[str] = []
    for site in _scan_sites(body):
        for reading in _site_readings(body, site):
            for decode in (False, True):
                for unangle in (False, True):
                    why = _outside_namespace(
                        reading,
                        concept_id=concept_id,
                        prefix=prefix,
                        decode=decode,
                        unangle=unangle,
                    )
                    if why is not None:
                        message = (
                            f"{concept_id}: `{reading.strip()}` {why}, "
                            f"outside {prefix}/"
                        )
                        if message not in violations:
                            violations.append(message)
    return violations
