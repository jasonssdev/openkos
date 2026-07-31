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

from openkos import source_title
from openkos.model import okf

_TITLE_SEPARATOR_RE = re.compile(r"[-_]+")


def titleize(stem: str) -> str:
    """Turn a filename stem into a human-readable title: `-`/`_` -> spaces.

    Moved verbatim from `cli/main.py`'s `_titleize`; `cli/main.py` now
    delegates to this function rather than duplicating it.
    """
    return _TITLE_SEPARATOR_RE.sub(" ", stem).strip()


def retitle_document(text: str, *, current_title: str, new_title: str) -> str:
    """Rewrite a Source document's `title` and its first body line, and
    nothing else (spec: "Exactly Two Byte-Level Edits Per Staged Source").

    Raises `ValueError` when `metadata["title"]` does not equal
    `current_title`, or when the body's first line does not read exactly
    `f"# {current_title}"` (spec: "Body First-Line Safety Property").
    Neither check is skippable: a hand-edited body MUST be refused, never
    overwritten.

    No separate CRLF handling is needed here (design D4's stated caveat):
    `load_frontmatter` delegates to `python-frontmatter`'s `parse`, which
    unconditionally replaces every `\\r\\n` with `\\n` in `text` BEFORE
    splitting it into lines -- so `body.split("\\n")[0]` can never itself
    end in a bare `\\r` by construction. There is nothing left to strip or
    re-attach; `dump_frontmatter`'s output is `\\n`-terminated throughout,
    matching every other engine-written Source.
    """
    metadata, body = okf.load_frontmatter(text)
    on_disk_title = metadata.get("title")
    if on_disk_title != current_title:
        raise ValueError(
            f"expected on-disk title {current_title!r}, found {on_disk_title!r}"
        )

    lines = body.split("\n")
    first_line = lines[0] if lines else ""
    expected = f"# {current_title}"
    if first_line != expected:
        raise ValueError(
            f"Source {current_title!r}: expected first body line "
            f"{expected!r}, found {first_line!r}"
        )

    lines[0] = f"# {new_title}"
    new_body = "\n".join(lines)

    new_metadata = dict(metadata)
    new_metadata["title"] = new_title
    return okf.dump_frontmatter(new_metadata, new_body)


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
