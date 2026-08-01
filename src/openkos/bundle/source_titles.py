"""Pure core for `openkos backfill-source-titles` (design D1; spec: "Body
First-Line Safety Property", "Exactly Two Byte-Level Edits Per Staged
Source").

`bundle/` is where pure `Mapping[str, str]` snapshot resolvers live
(alongside `provenance`, `links`, `relations`, `references`, `listing`).
Imports `openkos.model.okf` only -- no `pathlib.Path`, no I/O, no
derived-layer import (`retrieval`, `graph`, `memory`), per AGENTS.md's
`bundle` -> `model` layering rule.

`titleize` is `cli/main.py`'s former `_titleize`, promoted here so
`ingest` and this backfill share exactly ONE implementation -- a
"narrower local twin" duplicate would silently misclassify a title the
moment the two diverged (design D1).
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import PurePosixPath

import yaml

from openkos import source_title
from openkos.model import okf

_TITLE_SEPARATOR_RE = re.compile(r"[-_]+")


def titleize(stem: str) -> str:
    """Turn a filename stem into a human-readable title: `-`/`_` -> spaces.

    Moved verbatim from `cli/main.py`'s `_titleize`; `cli/main.py` now
    delegates to this function rather than duplicating it.
    """
    return _TITLE_SEPARATOR_RE.sub(" ", stem).strip()


_FRONTMATTER_RE = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)
_TOP_LEVEL_TITLE_RE = re.compile(r"^title:([ \t]*.*)$", re.MULTILINE)


def _split_frontmatter_verbatim(text: str) -> tuple[str, str]:
    """Split off the frontmatter block byte-for-byte, mirroring
    `bundle/index.py:_split_frontmatter_verbatim` (`index.py:20-31`,
    design D2 there): never re-dump it, since that risks reformatting a
    quoting choice. A deliberate separate copy, not an import, to avoid
    cross-module private coupling. Raises `ValueError` if `text` lacks a
    `---`-delimited block.
    """
    match = _FRONTMATTER_RE.match(text)
    if match is None:
        raise ValueError("Source document: missing or malformed frontmatter block")
    return match.group(0), text[match.end() :]


def _patch_title_line(block: str, *, current_title: str, new_title: str) -> str:
    """Replace ONLY the top-level `title:` line in `block`, verbatim
    otherwise. Fails closed unless `title:` is a single self-contained
    scalar: a block scalar, anchor/alias, multi-line value, or anything not
    round-tripping to `current_title` alone all refuse. The replacement
    comes from `okf.dump_frontmatter`, so quoting is never hand-rolled.
    """
    matches = list(_TOP_LEVEL_TITLE_RE.finditer(block))
    if len(matches) != 1:
        raise ValueError(f"'title:' line count is {len(matches)}, expected exactly 1")
    match = matches[0]
    value = match.group(1)
    stripped = value.strip()
    if not stripped or stripped[0] in "|>&*":
        raise ValueError(f"'title:' is not a rewritable scalar: {match.group(0)!r}")
    try:
        parsed = yaml.safe_load(f"title:{value}\n")
    except yaml.YAMLError as exc:
        raise ValueError(f"'title:' did not parse: {match.group(0)!r}") from exc
    if not isinstance(parsed, dict) or parsed.get("title") != current_title:
        raise ValueError(f"'title:' does not round-trip: {match.group(0)!r}")
    # A trailing YAML comment lives on the line but not in the parsed value,
    # so re-emitting the line would silently DELETE it -- a third byte-level
    # edit, exactly what this function exists to prevent. Refuse instead of
    # guessing where the comment ends. ` #` inside the parsed value is not a
    # comment (it was quoted), so it must not trigger the refusal.
    if " #" in value and " #" not in current_title:
        raise ValueError(f"'title:' carries a trailing comment: {match.group(0)!r}")

    new_line = "\n".join(
        okf.dump_frontmatter({"title": new_title}, "").split("\n")[1:-2]
    )
    return block[: match.start()] + new_line + block[match.end() :]


def retitle_document(text: str, *, current_title: str, new_title: str) -> str:
    """Rewrite a Source document's `title` and its first body line, and
    nothing else (spec: "Exactly Two Byte-Level Edits Per Staged Source").

    Validates semantically via `okf.load_frontmatter` (parsed `title` must
    equal `current_title`) but writes surgically via
    `_split_frontmatter_verbatim`/`_patch_title_line`, never a full
    `dump_frontmatter` re-dump -- mirrors `bundle/index.py`'s precedent
    (`index.py:20-31`, design D2): re-dumping risks reformatting keys,
    ordering, or a value's on-disk type. Verified against this repo's own
    shipped Source: a re-dump re-sorted keys, flattened an inline `tags`
    sequence into a block one, and turned an ISO-8601 `Z` timestamp into a
    `datetime` on reload. The body is also preserved verbatim except its
    first line, so trailing whitespace a re-dump would drop now survives.

    Raises `ValueError` when `metadata["title"]` != `current_title`, when
    the first body line != `f"# {current_title}"` (spec: "Body First-Line
    Safety Property"), or when `title:` is not a scalar
    `_patch_title_line` can safely rewrite.
    """
    metadata, _ = okf.load_frontmatter(text)
    on_disk_title = metadata.get("title")
    if on_disk_title != current_title:
        raise ValueError(
            f"expected on-disk title {current_title!r}, found {on_disk_title!r}"
        )

    frontmatter_block, body = _split_frontmatter_verbatim(text)
    new_block = _patch_title_line(
        frontmatter_block, current_title=current_title, new_title=new_title
    )

    # `load_frontmatter` returns `content.strip()`: preserve the leading
    # whitespace run it would drop, rather than discarding it (point 7).
    stripped_body = body.lstrip()
    leading_ws = body[: len(body) - len(stripped_body)]
    first_line_raw, sep, rest = stripped_body.partition("\n")
    trailing_cr = "\r" if first_line_raw.endswith("\r") else ""
    first_line = first_line_raw[:-1] if trailing_cr else first_line_raw
    expected = f"# {current_title}"
    if first_line != expected:
        raise ValueError(f"expected first body line {expected!r}, found {first_line!r}")

    new_body = leading_ws + f"# {new_title}{trailing_cr}" + sep + rest
    return new_block + new_body


@dataclass(frozen=True)
class SourceCandidate:
    """`scan_source_titles`'s three result buckets (design D3)."""

    concept_id: str
    current_title: str
    resource: str
    # The candidate's own bundle document text, for `retitle_document`.
    # Excluded from equality/repr: bulk payload, not identity.
    document_text: str = field(default="", compare=False, repr=False)


@dataclass(frozen=True)
class SkippedSource:
    concept_id: str
    current_title: str
    reason: str


@dataclass(frozen=True)
class WarnedSource:
    concept_id: str
    reason: str


@dataclass(frozen=True)
class ScanResult:
    candidates: tuple[SourceCandidate, ...]
    skipped: tuple[SkippedSource, ...]
    warned: tuple[WarnedSource, ...]


def _resource_reason(resource: object) -> str | None:
    """Warned reason for a malformed `resource`, `None` if well-formed
    (design D2): `resource-missing` vs `resource-malformed`."""
    if resource is None or not isinstance(resource, str):
        return "resource-missing"
    posix = PurePosixPath(resource)
    if (
        "\\" in resource
        or posix.is_absolute()
        or ".." in posix.parts
        or not resource.startswith("raw/")
        or len(posix.parts) != 2
    ):
        return "resource-malformed"
    return None


def scan_source_titles(files: Mapping[str, str]) -> ScanResult:
    """Pure classifier (design D2/D3): malformed `resource` -> `warned`;
    curated title -> `skipped`; otherwise -> `candidates`."""
    candidates: list[SourceCandidate] = []
    skipped: list[SkippedSource] = []
    warned: list[WarnedSource] = []

    for path in sorted(files):
        metadata: dict[str, object] | None
        try:
            metadata, _ = okf.load_frontmatter(files[path])
        except Exception:  # broad: malformed frontmatter is skipped rather
            # than surfaced, mirroring `provenance._source_levels`
            metadata = None
        if metadata is None or metadata.get("type") != "Source":
            continue

        concept_id = path.removesuffix(".md")
        current_title = str(metadata.get("title"))
        resource = metadata.get("resource")

        reason = _resource_reason(resource)
        if reason is not None:
            warned.append(WarnedSource(concept_id=concept_id, reason=reason))
            continue

        if not isinstance(resource, str):
            continue  # unreachable: `_resource_reason` already excludes this
        stem = PurePosixPath(resource).stem
        if current_title != titleize(stem):
            skipped.append(
                SkippedSource(
                    concept_id=concept_id, current_title=current_title, reason="curated"
                )
            )
            continue

        candidates.append(
            SourceCandidate(
                concept_id=concept_id,
                current_title=current_title,
                resource=resource,
                document_text=files[path],
            )
        )

    return ScanResult(
        candidates=tuple(sorted(candidates, key=lambda c: c.concept_id)),
        skipped=tuple(sorted(skipped, key=lambda s: s.concept_id)),
        warned=tuple(sorted(warned, key=lambda w: w.concept_id)),
    )


@dataclass(frozen=True)
class SourceRetitle:
    """A staged Source: new title + full rewritten document (design D3)."""

    concept_id: str
    current_title: str
    new_title: str
    content: str


@dataclass(frozen=True)
class SourceTitleBackfill:
    staged: tuple[SourceRetitle, ...]
    skipped: tuple[SkippedSource, ...]
    warned: tuple[WarnedSource, ...]


def resolve_source_title_backfill(
    scan: ScanResult, raw_texts: Mapping[str, str | None]
) -> SourceTitleBackfill:
    """Re-derive each candidate's title from injected `raw/` text and stage
    a `retitle_document`-rewritten document (design D2/D3). `raw_texts` is
    keyed by `resource`: a missing key is `raw-unreadable`, an explicit
    `None` is `raw-undecodable`. `scan`'s own `skipped`/`warned` pass
    through; every bucket is sorted by `concept_id`."""
    staged: list[SourceRetitle] = []
    skipped: list[SkippedSource] = list(scan.skipped)
    warned: list[WarnedSource] = list(scan.warned)

    def skip(cid: str, title: str, reason: str) -> None:
        skipped.append(SkippedSource(cid, title, reason))

    def warn(cid: str, reason: str) -> None:
        warned.append(WarnedSource(concept_id=cid, reason=reason))

    for c in scan.candidates:
        if c.resource not in raw_texts:
            warn(c.concept_id, "raw-unreadable")
            continue
        raw_text = raw_texts[c.resource]
        if raw_text is None:
            warn(c.concept_id, "raw-undecodable")
            continue
        if not raw_text.strip():
            skip(c.concept_id, c.current_title, "empty-raw-source")
            continue

        new_title = source_title.derive_source_title(raw_text)
        if new_title is None:
            skip(c.concept_id, c.current_title, "no-derivable-title")
            continue
        if new_title == c.current_title:
            skip(c.concept_id, c.current_title, "already-current")
            continue

        try:
            content = retitle_document(
                c.document_text, current_title=c.current_title, new_title=new_title
            )
        except ValueError:
            warn(c.concept_id, "heading-mismatch")
            continue

        staged.append(
            SourceRetitle(
                concept_id=c.concept_id,
                current_title=c.current_title,
                new_title=new_title,
                content=content,
            )
        )

    return SourceTitleBackfill(
        staged=tuple(sorted(staged, key=lambda s: s.concept_id)),
        skipped=tuple(sorted(skipped, key=lambda s: s.concept_id)),
        warned=tuple(sorted(warned, key=lambda w: w.concept_id)),
    )
