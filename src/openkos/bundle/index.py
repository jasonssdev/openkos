"""Renders the bytes of a fresh bundle's root `index.md`, and appends to it."""

import re
from pathlib import PurePosixPath

from openkos.model import okf
from openkos.model.types import CANONICAL_SECTION_ORDER as _CANONICAL_SECTION_ORDER


def render_index() -> str:
    """Render a fresh root `index.md`: OKF version frontmatter, empty body."""
    return okf.dump_frontmatter({"okf_version": okf.OKF_VERSION})


_FRONTMATTER_RE = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)
_SECTION_SPLIT_RE = re.compile(r"\n(?=# )")
_SECTION_HEADER_RE = re.compile(r"\A# (.+)\n")


def _split_frontmatter_verbatim(text: str) -> tuple[str, str]:
    """Split `text` into its frontmatter block (kept byte-for-byte) and body.

    Never re-parses and re-dumps the frontmatter block through
    `dump_frontmatter`/`frontmatter.Post` -- doing so risks reformatting a
    quoting choice like `okf_version: '0.1'` (D2). Raises `ValueError` if
    `text` does not start with a `---`-delimited block.
    """
    match = _FRONTMATTER_RE.match(text)
    if match is None:
        raise ValueError("index.md: missing or malformed frontmatter block")
    return match.group(0), text[match.end() :]


def _section_header(chunk: str) -> str:
    """Return the header text of a `# `-headed section chunk."""
    match = _SECTION_HEADER_RE.match(chunk)
    if match is None:
        raise ValueError(f"index.md: malformed section chunk {chunk!r}")
    return match.group(1)


def _reject_newline(field: str, value: str) -> None:
    """Raise `ValueError` if `value` contains a newline (RISK-1).

    `title`/`slug`/`description` are interpolated verbatim into the
    rendered bullet with no escaping. A value containing a newline followed
    by `# ` or `## ` could forge a section header the next time the file is
    re-parsed. Every one of these fields is inherently single-line for a
    single Source concept, so rejecting is simpler and safer than escaping.
    """
    if "\n" in value or "\r" in value:
        raise ValueError(f"index.md: {field!r} must not contain a newline")


_TITLE_LABEL_DELIMITERS_RE = re.compile(r"[\[\]]")
_SLUG_TARGET_DELIMITERS_RE = re.compile(r"[()]")


def reject_title_link_delimiters(field: str, value: str) -> None:
    r"""Raise `ValueError` if `value` contains `[` or `]` (RISK-1, insert path).

    A `title` is interpolated verbatim into the LABEL span of the bullet
    (`* [{title}](...)`). A `[`/`]` there forges a second link or closes the
    label early, so `_LINK_RE`/`_LABELLED_LINK_RE` then match the FORGED link
    instead of the real one -- the entry becomes invisible to
    `remove_index_entry`/`relabel_index_entry`, and a forged id can delete a
    DIFFERENT entry. This is the untrusted, LLM-derived counterpart of
    `_reject_markdown_link_delimiters` (which guards the operator-supplied
    relabel path): the STRICT insert-time backstop that fails closed for any
    caller. Callers that want to PRESERVE a title carrying brackets (a benign
    filename like `notes[1].txt`, as well as a hostile one) sanitize it first
    with `sanitize_link_label`, which neutralizes the delimiters instead of
    rejecting the whole value.

    Parentheses and backticks are deliberately NOT rejected here: inside the
    label they sit within `[^\]]*` and never reach the target, so a common
    title like ``map() function`` or ``` `useState` `` stays valid --
    rejecting them would be a false positive with no security benefit.
    """
    if _TITLE_LABEL_DELIMITERS_RE.search(value):
        raise ValueError(
            f"index.md: {field!r} must not contain a markdown link label "
            "delimiter ('[' or ']')"
        )


def _reject_slug_link_delimiters(field: str, value: str) -> None:
    """Raise `ValueError` if `value` contains `(` or `)` (RISK-1, insert path).

    A `slug` is interpolated into the link TARGET span
    (`](/{link_dir}/{slug}.md)`). A `(`/`)` there closes or forges the
    target, changing the bullet's resolved identity. Slugs produced by
    `_slugify` can never contain these, so this is defense-in-depth for the
    generic public inserter.
    """
    if _SLUG_TARGET_DELIMITERS_RE.search(value):
        raise ValueError(
            f"index.md: {field!r} must not contain a markdown link target "
            "delimiter ('(' or ')')"
        )


def sanitize_link_label(title: str) -> str:
    r"""Return `title` with markdown link LABEL delimiters (`[`, `]`) replaced
    by their round-paren equivalents, so the value is safe to interpolate
    into a bullet's `[label](...)` span in `index.md`/`log.md`.

    Brackets close or forge the label for the regex-based `_LINK_RE`/
    `_LABELLED_LINK_RE` matchers -- maliciously, via an injected LLM-extracted
    title, or benignly, via a filename like `notes[1].txt`. Parentheses sit
    inside `[^\]]*` and are inert there, so the substitution keeps the entry
    matchable/removable by its id while preserving the visual grouping. This
    is DISPLAY text only: identity is the slug, which `_slugify` derives
    independently, so the substitution never changes which object exists.
    """
    return title.replace("[", "(").replace("]", ")")


# `_CANONICAL_SECTION_ORDER` is now derived from
# `openkos.model.types.REGISTRY` -- see that module for the single source of
# truth.


def insert_index_entry(
    index_text: str,
    *,
    section: str,
    link_dir: str,
    title: str,
    slug: str,
    description: str,
) -> str:
    """Insert a new bullet into `index_text`'s `# {section}` section (D2, #4).

    Generalizes `insert_source_entry` to any of the canonical catalog
    sections. Pure body-only edit: the frontmatter block is split off and
    kept byte-for-byte verbatim, and every existing section round-trips
    byte-for-byte except for the inserted bullet. `# {section}` is located
    if present, and the bullet is appended to it. If absent, a fresh
    `# {section}` chunk is created and inserted at its CANONICAL rank --
    `_CANONICAL_SECTION_ORDER = (Concepts, Entities, Places, Events,
    Procedures, Decisions, People, Organizations, Sources)` -- i.e.
    immediately before the first EXISTING section whose rank is greater, or
    at the end of the body if no such section exists. `Sources` is always
    last in that order, so a fresh `# Sources` section is always appended
    after every other existing section, regardless of which of the other
    eight currently exist -- preserving the historical Sources-last behavior
    byte-identically.
    `title`/`slug`/`description` are each rejected (`ValueError`) if they
    contain a newline (RISK-1) -- see `_reject_newline`. `title` is
    additionally rejected if it carries a markdown link LABEL delimiter
    (`[`/`]`) and `slug` if it carries a link TARGET delimiter (`(`/`)`) --
    see `reject_title_link_delimiters`/`_reject_slug_link_delimiters` -- so an
    untrusted, LLM-derived title can never forge or break the bullet's first
    link. This guard applies to every section, including untrusted object
    fields. `section` MUST be one of the canonical sections, else
    `ValueError` -- there is no defined rank for an unknown section.
    """
    if section not in _CANONICAL_SECTION_ORDER:
        raise ValueError(
            f"section must be one of {list(_CANONICAL_SECTION_ORDER)}, got {section!r}"
        )
    _reject_newline("title", title)
    _reject_newline("slug", slug)
    _reject_newline("description", description)
    reject_title_link_delimiters("title", title)
    _reject_slug_link_delimiters("slug", slug)
    frontmatter_block, body = _split_frontmatter_verbatim(index_text)
    chunks = _SECTION_SPLIT_RE.split(body)
    preamble, section_chunks = chunks[0], chunks[1:]

    bullet = f"* [{title}](/{link_dir}/{slug}.md) - {description}\n"
    headers = [_section_header(chunk) for chunk in section_chunks]

    if section in headers:
        section_index = headers.index(section)
        section_chunks[section_index] = section_chunks[section_index] + bullet
    else:
        target_rank = _CANONICAL_SECTION_ORDER.index(section)
        insert_at = len(section_chunks)
        for i, header in enumerate(headers):
            if (
                header in _CANONICAL_SECTION_ORDER
                and _CANONICAL_SECTION_ORDER.index(header) > target_rank
            ):
                insert_at = i
                break
        section_chunks.insert(insert_at, f"# {section}\n\n{bullet}")

    return (
        frontmatter_block + preamble + "".join(f"\n{chunk}" for chunk in section_chunks)
    )


def insert_source_entry(
    index_text: str, *, title: str, slug: str, description: str
) -> str:
    """Insert a new Source bullet into `index_text`'s `# Sources` section (D2).

    Thin wrapper around `insert_index_entry(section="Sources",
    link_dir="sources", ...)`, kept as a distinct public function so
    `cli/main.py`'s existing call site (`bundle_index.insert_source_entry`)
    keeps working unmodified.
    """
    return insert_index_entry(
        index_text,
        section="Sources",
        link_dir="sources",
        title=title,
        slug=slug,
        description=description,
    )


_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
_BULLET_MARKERS = ("* ", "- ")
_SCHEME_RE = re.compile(r"\A[A-Za-z][A-Za-z0-9+.-]*:")


def _link_identity(target: str) -> str | None:
    """Normalize a raw markdown link target to its bundle-relative identity.

    A deliberately narrower, bundle-local twin of `lint.normalize_link`
    (NOT imported from `lint`, per #922 -- `lint` imports `config` and
    `okf` and is the higher "health" layer; importing it here would invert
    layering). `index.md` always lives at the bundle root, so there is no
    `source_rel_dir` parameter to thread through: a leading `/` (or
    multiple leading `/`s, e.g. `//concepts/foo.md`) and a bare relative
    link all resolve identically. A trailing `#fragment` or a quoted
    ` "title"` suffix is stripped first; an external `scheme:` URL
    (`http:`, `mailto:`, ...), an empty target, or one that escapes the
    bundle root via `..` all normalize to `None` (never a match).

    NOTE: this normalization must stay in lockstep with the BYTES
    re-implementation in `openkos.vcs.git`'s `_FILE_INFO_CALLBACK_SNIPPET`
    (that snippet runs inside `git filter-repo`'s own subprocess, which
    cannot import `openkos`, hence the duplication) -- proven by the parity
    test in `tests/unit/vcs/test_scrub_snippet_parity.py`.
    """
    target = target.split("#", 1)[0].strip()
    if target.endswith('"') and ' "' in target:
        target = target.rsplit(' "', 1)[0].strip()
    if not target:
        return None
    if _SCHEME_RE.match(target):
        return None
    candidate = PurePosixPath(target.lstrip("/"))
    parts: list[str] = []
    for part in candidate.parts:
        if part == "..":
            if not parts:
                return None
            parts.pop()
        else:
            parts.append(part)
    return "/".join(parts).removesuffix(".md")


def remove_index_entry(index_text: str, concept_id: str) -> tuple[str, int]:
    """Drop every bullet whose FIRST markdown link resolves to `concept_id`.

    Generic across all four sections (Sources, Concepts, People, Decisions,
    #922): matching is by resolved LINK IDENTITY, never by section, so no
    section-splitting or `# `-header parsing is needed here (unlike
    `insert_source_entry`). Frontmatter is split off byte-for-byte via
    `_split_frontmatter_verbatim` (raises `ValueError` on malformed
    frontmatter, matching `insert_source_entry`'s contract) and the body is
    walked line by line: a candidate line is one whose stripped text starts
    with a list marker (`* ` or `- ` -- the engine always writes `*`, a
    hand-authored bullet may use `-`); only its FIRST markdown link is
    inspected, so a bullet that merely MENTIONS another concept later in its
    description text is never mistakenly dropped.

    Count semantics: zero matches returns `(index_text, 0)` completely
    UNCHANGED -- not an error, since a file with no catalog entry is drift,
    not a reason to refuse a deletion that is otherwise safe. One match
    drops that line. More than one match (a duplicate catalog entry) drops
    ALL of them, reporting the total count -- leaving any would create a
    dangling reference to the now-deleted file. Only the matched line plus
    its trailing newline is ever removed; every other byte -- blank lines,
    other bullets, empty sections -- round-trips verbatim (no section
    pruning, avoiding any reflow risk).
    """
    frontmatter_block, body = _split_frontmatter_verbatim(index_text)
    lines = body.splitlines(keepends=True)
    kept_lines: list[str] = []
    removed = 0
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith(_BULLET_MARKERS):
            match = _LINK_RE.search(stripped)
            if match is not None and _link_identity(match.group(1)) == concept_id:
                removed += 1
                continue
        kept_lines.append(line)

    if removed == 0:
        return index_text, 0
    return frontmatter_block + "".join(kept_lines), removed


_LABELLED_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")
_LABEL_UNSAFE_CHARS_RE = re.compile(r"[\[\]()`]")


def _reject_markdown_link_delimiters(field: str, value: str) -> None:
    """Raise `ValueError` if `value` contains `[`, `]`, `(`, `)`, or a
    backtick (Slice 2 review finding, corroborated by execution).

    The LABEL span written by `relabel_index_entry` is the one place a
    title is interpolated without becoming the link target or description,
    so it is the one place a slipped-through delimiter lets the REST of the
    bullet be rewritten: `[`/`]` forges a second link, `(`/`)` breaks the
    existing target. Either way `_LINK_RE`/`_LABELLED_LINK_RE` then match
    the FORGED link, not the real one -- verified by execution: the bullet
    becomes invisible to both `relabel_index_entry` and `remove_index_entry`.
    """
    if _LABEL_UNSAFE_CHARS_RE.search(value):
        raise ValueError(
            f"index.md: {field!r} must not contain a markdown link delimiter "
            "('[', ']', '(', ')') or a backtick"
        )


def relabel_index_entry(
    index_text: str, concept_id: str, new_title: str
) -> tuple[str, int]:
    """Rewrite the LABEL of every bullet whose FIRST markdown link resolves to
    `concept_id`, leaving slug, link target, and description untouched.

    `remove_index_entry`'s twin: same frontmatter-splitting discipline, same
    line-walk, same "first link only" candidate rule, same zero/duplicate
    count semantics -- but this REWRITES a span instead of dropping a line.
    Identity is `_link_identity(target) == concept_id`, never a label-text
    match, because the whole point of this function is to correct a label
    that has drifted from the document's actual `title` -- matching on the
    (stale) label itself would never find the bullet that needs fixing.

    Uses a SEPARATE regex, `_LABELLED_LINK_RE`, rather than adding a capture
    group to the module-level `_LINK_RE`: `remove_index_entry` reads
    `_LINK_RE.search(...).group(1)` as the link TARGET and depends on
    `_LINK_RE` having exactly one group, so widening it here would silently
    break that call site.

    Only the label span between `[` and `]` of the bullet's first link is
    replaced; the link target, the ` - ` separator, the description, the
    bullet marker, leading indentation, and the line ending all round-trip
    verbatim. `_reject_newline` and `_reject_markdown_link_delimiters` both
    guard `new_title` (see the latter's own docstring for why the label
    span is the one place that must be escaped). Count semantics mirror
    `remove_index_entry`: zero matches returns `(index_text, 0)` unchanged --
    not an error, since catalog drift is not a reason to refuse an otherwise
    safe relabel; more than one match (a duplicate catalog entry) relabels
    ALL of them, reporting the total, since leaving one stale label behind
    would be worse than the duplicate itself.
    """
    _reject_newline("new_title", new_title)
    _reject_markdown_link_delimiters("new_title", new_title)
    frontmatter_block, body = _split_frontmatter_verbatim(index_text)
    lines = body.splitlines(keepends=True)
    result_lines: list[str] = []
    relabeled = 0
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith(_BULLET_MARKERS):
            match = _LABELLED_LINK_RE.search(stripped)
            if match is not None and _link_identity(match.group(2)) == concept_id:
                start, end = match.span(1)
                stripped = stripped[:start] + new_title + stripped[end:]
                indent = line[: len(line) - len(line.lstrip())]
                line = indent + stripped
                relabeled += 1
        result_lines.append(line)

    if relabeled == 0:
        return index_text, 0
    return frontmatter_block + "".join(result_lines), relabeled
