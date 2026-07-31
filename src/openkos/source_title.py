"""Derive a Source's `title` from its decoded raw content (issue #248).

`derive_source_title` is PURE and IDEMPOTENT: it depends only on its
`raw_content` argument -- no clock, no filesystem, no locale, no randomness,
no `openkos` imports. This is what lets a byte-identical re-ingest of the
same raw file produce a byte-identical Source document, including its
derived `title` (see `tests/unit/cli/test_ingest.py`'s idempotence pin).

`None` is NOT an error. It means "no usable candidate was found in this
content" -- a normal, expected outcome. This function MUST NOT raise, and it
MUST NOT know about filenames, slugs, or `_titleize`; the caller (`ingest`)
owns the `_titleize(src.stem)` fallback.

Precedence, evaluated as ONE linear pass over `raw_content.split("\\n")`:

1. The first ATX H1 (`# ` line) that is not inside a fenced code block.
2. Otherwise, the first line that is TITLE-PLAUSIBLE (see
   `_is_title_plausible`).
3. Otherwise, `None`.

NO CASCADE: if an H1 is found but fails normalization/validation, this
function returns `None` DIRECTLY. It does NOT fall through to re-evaluate
rule 2 on some other line -- the proposal's fallback chain treats a failed
(1) the same as an absent (1), but this function itself must not blur "why"
an H1 failed by silently trying a different rule.

NOT A GENERAL SANITISER: this function REJECTS candidates that fail
validation; it never escapes, truncates, or rewrites them.
`okf.build_source_concept` gains no new guarantee from this module and still
receives untrusted-by-contract input on the fallback (slug) path.

Fence tracking (the three-line state machine below, `_FENCE_MARKERS`) is a
deliberate, intentional DUPLICATE of `bundle/links.py:50-74`'s
`_mask_fenced_code_blocks`, itself a documented duplicate of
`graph/sqlite_graph.py`'s copy (issue #922's `_link_identity` precedent: a
narrower local twin kept separate rather than importing across layers or
extracting a shared module for a two-branch primitive). What is duplicated
is precisely bounded: the marker tuple and the "the same 3-char marker
closes the fence it opened" rule -- nothing else is shared with `links.py`.

The leading-frontmatter probe (`_frontmatter_end`) is FENCE-BLIND: it
matches `python-frontmatter`'s own behavior, so a `---` line inside a fenced
code block within the first few lines of a document can close a frontmatter
scan early. This is a named, accepted inaccuracy, not an oversight.

Naming caveat (issue #248): the settled name for the rule this module
implements is broader than "headings" -- rule 2 accepts any title-plausible
PLAIN line, not just markdown headings -- so `derive_source_title` is named
for its OUTCOME (a title), not for the ATX-heading mechanism alone.
"""

import re
from typing import Final

_ATX_H1_RE: Final = re.compile(r"^# (.*)$")
"""Matches an ATX H1 line: a single leading `# ` followed by the heading
text (which may itself be empty or whitespace-only; that is rejected later
by the shared validator's empty-after-normalization check)."""

_ATX_CLOSING_RE: Final = re.compile(r" #+$")
"""Strips a trailing ATX closing `#` sequence, e.g. `Title #` -> `Title`.

Applied only AFTER whitespace collapse (so interior whitespace is exactly
one space) and only to headings (never to a rule-2 plain line, where a
trailing `#` is ordinary content, not heading syntax). The required space
BEFORE the `#` run is what protects a legitimate trailing `#` that is part
of the title itself: `Grade A#` and `C# vs F#` have no space before their
final `#`, so they are left untouched; only `Title #` (space then `#`) is
stripped.
"""

_FORBIDDEN_IN_TITLE: Final = re.compile(r"[\x00-\x1f\x7f\[\]()`*_<>|]")
"""Characters a normalized title candidate MUST NOT contain (rejected,
never escaped or truncated). Follows `lint._UNSPELLABLE_IN_SPAN`'s shape
(`lint.py:603-622`): one compiled class, each member justified individually.

- `[` `]` `(` `)` -- would silently break the `[title](/path.md)` bullet
  link `index.py`/`log.py` render for every Source; only `\\n`/`\\r` raise
  there today, so these four would corrupt a bullet without erroring.
- `` ` `` -- opens an inline code span in the Source's own `# {title}` H1
  (`okf.py:217`) and in every bullet; `lint._UNSPELLABLE_IN_SPAN` already
  rejects it for `resource` for the same reason.
- `*` `_` -- markdown inline emphasis; the title would render as emphasis
  instead of as its own literal text.
- `<` `>` -- `<` opens raw HTML or an autolink; `>` reads as a blockquote
  prefix, and both read as prompt structure in
  `extraction/concept.py:189`'s unbounded `SOURCE TITLE:` interpolation.
- `|` -- a markdown table pipe would split any future table cell the title
  lands in.
- `\\x00-\\x1f` -- every C0 control character, which INCLUDES `\\n` and
  `\\r` -- the only two characters `index.py`/`log.py` reject by RAISING
  today, so admitting them here would turn a cosmetic rendering problem
  into a hard ingest failure at ingest time. Python's `str.split()` (step 1
  of normalization) already classifies `\\x09-\\x0d` (TAB, LF, VT, FF, CR)
  and `\\x1c-\\x1f` (FS/GS/RS/US) as whitespace and destroys them before
  this class ever runs -- those code points are UNREACHABLE by construction
  here; say so rather than imply a live rejection that never actually
  fires. The rest of the range (`\\x00-\\x08`, `\\x0e-\\x1b`) IS reachable
  and is what this class actually rejects.
- `\\x7f` DEL -- a control character no normalization step removes and that
  renders as nothing visible; excluding it would let an invisible byte into
  the title silently.

Deliberately NOT rejected (stated so nobody over-corrects this class
later): `#`, `&`, `"`, `'`, `:` (mid-string; a trailing `:` is rejected by
the title-plausible predicate, not this class), `-`, and all non-ASCII
text. Both of this repo's shipped example first lines contain an em dash
(`—`); a greedier class would regress the repo's own flagship corpus.
"""

_TITLE_MAX_CHARS: Final = 120
"""Maximum candidate length, measured on the FINAL normalized string (after
whitespace collapse and, for headings, closing-`#` strip) -- never on the
raw pre-normalization text. A heading padded with 200 spaces is not
rejected for length; only its actual content is measured."""

_FENCE_MARKERS: Final = ("```", "~~~")
"""The two fence marker strings tracked by the walk below. See the module
docstring's fence-duplication note: identical bounded duplicate of
`bundle/links.py`'s `_FENCE_MARKERS`, not an import."""

_BLOCK_SYNTAX_PREFIXES: Final = ("-", "*", ">", "#", "|", *_FENCE_MARKERS)
"""Markdown block-syntax prefixes that disqualify a rule-2 plain line from
being title-plausible (proposal: "does not begin with markdown block
syntax"). `#` is included because a line starting with `#` that reaches
rule 2 was NOT a matched ATX H1 (rule 1 already claims those), so it is
some other heading level (`##`, `###`, ...) -- still block syntax, not
plain prose."""

_TERMINAL_PUNCTUATION: Final = (".", ",", ";", ":")
"""Trailing punctuation that disqualifies a rule-2 plain line: it reads as
the end of a sentence within a paragraph, not a standalone title."""


def _frontmatter_end(lines: list[str]) -> int:
    """Return the index of the first line AFTER a leading YAML frontmatter
    block, or `0` if there is none.

    A bounded probe, not a fold into the main walk: it only ever matters for
    a PREFIX of the document (`lines[0] == "---"`), so giving the main loop
    a "still in frontmatter" mode flag -- one that can only ever be true at
    the very start -- would be strictly less readable than this one named
    call. Skipped as frontmatter ONLY when a closing `---` line exists LATER
    in the file; otherwise `lines[0]` is ordinary content and this returns
    `0`, letting the main walk evaluate it like any other line.
    """
    if not lines or lines[0] != "---":
        return 0
    for index in range(1, len(lines)):
        if lines[index] == "---":
            return index + 1
    return 0


def _is_title_plausible(text: str, next_line: str | None) -> bool:
    """Rule 2's predicate, evaluated on the NORMALIZED candidate `text` plus
    the raw next physical line (`None` at end-of-file).

    ALL must hold: non-empty (already guaranteed by the caller via
    `_normalize_and_validate`'s empty check happening after this in the
    fixed pipeline -- see that function); followed by a blank line or EOF;
    does not end in `.`, `,`, `;`, or `:`; does not begin with markdown
    block syntax.
    """
    if next_line is not None and next_line.strip() != "":
        return False
    if text.endswith(_TERMINAL_PUNCTUATION):
        return False
    return not text.startswith(_BLOCK_SYNTAX_PREFIXES)


def _normalize_and_validate(candidate: str, *, is_heading: bool) -> str | None:
    """Shared normalize-then-validate pipeline, run in this FIXED order
    (the order is load-bearing, see design.md):

    1. Collapse/strip: `" ".join(candidate.split())` collapses every
       whitespace run (including a trailing CRLF `\\r`) to one space and
       strips both ends in one call. Doing this FIRST is what makes a CRLF
       source safe: reversing this with the forbidden-character check would
       reject every CRLF source for its `\\r`.
    2. Heading-only: strip a trailing ATX closing `#` sequence.
    3. Reject if empty (a whitespace-only heading collapses to `""`).
    4. Reject if longer than `_TITLE_MAX_CHARS`, measured on this final
       string.
    5. Reject if `_FORBIDDEN_IN_TITLE` matches anywhere in this final
       string.

    Returns the normalized string on success, `None` on any rejection.
    """
    text = " ".join(candidate.split())
    if is_heading:
        text = _ATX_CLOSING_RE.sub("", text)
    if not text:
        return None
    if len(text) > _TITLE_MAX_CHARS:
        return None
    if _FORBIDDEN_IN_TITLE.search(text):
        return None
    return text


def derive_source_title(raw_content: str) -> str | None:
    """Derive a Source title from `raw_content`, or `None` if no usable
    candidate exists. See the module docstring for the full contract
    (purity, `None`-is-not-an-error, no-cascade, fence duplication).
    """
    lines = raw_content.split("\n")
    start = _frontmatter_end(lines)

    fence_marker: str | None = None
    first_body_index: int | None = None

    for index in range(start, len(lines)):
        line = lines[index]
        stripped = line.lstrip()
        opens_or_closes = stripped.startswith(_FENCE_MARKERS)

        if fence_marker is not None:
            if opens_or_closes and stripped[:3] == fence_marker:
                fence_marker = None
            continue

        if opens_or_closes:
            fence_marker = stripped[:3]
            continue

        if line.strip() == "":
            continue

        match = _ATX_H1_RE.match(line)
        if match:
            return _normalize_and_validate(match.group(1), is_heading=True)

        if first_body_index is None:
            first_body_index = index

    if first_body_index is None:
        return None

    next_line = (
        lines[first_body_index + 1] if first_body_index + 1 < len(lines) else None
    )
    normalized = " ".join(lines[first_body_index].split())
    if not _is_title_plausible(normalized, next_line):
        return None
    return _normalize_and_validate(lines[first_body_index], is_heading=False)
