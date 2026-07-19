"""OKF (Open Knowledge Format) adapter.

The one seam that knows the on-disk shape of OKF v0.1: frontmatter framing,
reserved filenames, and the conformance rules of §9. Nothing outside this
module parses or emits frontmatter, or reasons about reserved files
(AGENTS.md:41, docs/architecture.md:113).

Rule 3 of §9 (reserved-file structure) is not implemented here; it needs
concept-doc structure the model layer does not have, and is deferred to
`lint` (docs/okf-alignment.md).
"""

import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import frontmatter

OKF_VERSION: Final = "0.1"
"""The OKF version this engine targets and declares, per §11."""

RESERVED_FILENAMES: Final[frozenset[str]] = frozenset({"index.md", "log.md"})
"""§6/§7 give these a fixed structure; §9 rule 1 exempts them from frontmatter."""


def dump_frontmatter(metadata: dict[str, object], body: str = "") -> str:
    """Render `metadata` as a YAML frontmatter block over `body`, per §4.1."""
    post = frontmatter.Post(body)
    post.metadata = metadata
    return frontmatter.dumps(post) + "\n"


def load_frontmatter(text: str) -> tuple[dict[str, object], str]:
    """Parse the frontmatter block and body out of `text`, per §4.1."""
    post = frontmatter.loads(text)
    return post.metadata, post.content


def build_source_concept(
    *,
    title: str,
    description: str,
    resource: str,
    tags: list[str],
    timestamp: str,
    sensitivity: str,
    provenance: list[str],
    raw_content: str | None = None,
) -> str:
    """Build a conformant OKF Source concept document (D4/ingest-source-body D1).

    Plain dict -> `dump_frontmatter`, no pydantic: every field is
    engine-derived from trusted local inputs (workspace config, the source's
    filename, an injected clock, the raw path) rather than untrusted
    structured LLM output, so `check_conformance` (§9 rules 1-2: parseable
    frontmatter, non-empty `type`) is the only gate this slice needs.
    `description` is passed through verbatim -- callers MUST phrase it as an
    honest description of the source's embedding state (embedded verbatim,
    or could not be embedded), never claiming extraction/compilation
    occurred, matching this slice's scope.

    `raw_content` (ingest-source-body D1/D3) renders one of three body
    shapes, each honest about what happened: `raw_content` holding
    non-blank text embeds it verbatim under a `## Source content` heading;
    `None` (a decode failure) renders a short note that the content could
    not be embedded as text; blank/whitespace-only text renders a distinct
    "source is empty" note. All three end with `# Citations`.
    """
    metadata: dict[str, object] = {
        "type": "Source",
        "title": title,
        "description": description,
        "resource": resource,
        "tags": tags,
        "timestamp": timestamp,
        "status": "active",
        "version": 1,
        "freshness": "snapshot",
        "sensitivity": sensitivity,
        "provenance": provenance,
    }
    if raw_content is None:
        section = (
            "_Source content could not be embedded as text "
            "(binary or non-UTF-8); see the linked resource._\n\n"
        )
    elif not raw_content.strip():
        section = "_The source file is empty._\n\n"
    else:
        section = f"## Source content\n\n{raw_content}\n\n"
    body = f"# {title}\n\n{description}\n\n{section}# Citations\n"
    return dump_frontmatter(metadata, body)


_CONCEPT_TYPES: Final[frozenset[str]] = frozenset({"Concept", "Entity"})
"""Closed vocabulary `build_concept` accepts, per the design's Architecture
Decisions ("Vocabulary"); anything else fails closed with `ValueError`."""


def build_concept(
    *,
    type: str,
    title: str,
    description: str,
    body: str,
    provenance: list[str],
    sensitivity: str,
    timestamp: str,
) -> str:
    """Build a conformant OKF Concept/Entity document from LLM-extracted,
    UNTRUSTED fields (design: "Builder validation").

    Unlike `build_source_concept` (whose inputs are engine-derived and
    trusted, so it skips validation -- see its docstring), this builder is
    the fail-closed gate for `extraction.ExtractionResult` data: `type` MUST
    be one of `{"Concept", "Entity"}`; `title`/`description` MUST be non-empty
    after stripping whitespace AND single-line (no embedded newlines, since
    each is a single Markdown/heading line); and `provenance` MUST be
    non-empty (a derived object always cites the Source it came from). Any
    violation raises `ValueError` rather than emitting a non-conformant or
    misleading document.

    `description` is a one-line lede; `body` follows it only when non-blank,
    so a blank body does not duplicate the description paragraph. A `## Related`
    section then backlinks every `provenance` entry -- each a Source concept-id
    path such as `sources/<slug>` -- as the source this object was extracted
    from, bundle-relative per docs/knowledge-object-model.md's link shape.
    `tags` is always `[]`: this slice has no tagging step.
    """
    if type not in _CONCEPT_TYPES:
        raise ValueError(f"type must be one of {sorted(_CONCEPT_TYPES)}, got {type!r}")
    if not title.strip():
        raise ValueError("title must be non-empty")
    if not description.strip():
        raise ValueError("description must be non-empty")
    if "\n" in title or "\r" in title:
        raise ValueError("title must not contain newlines")
    if "\n" in description or "\r" in description:
        raise ValueError("description must not contain newlines")
    if not provenance:
        raise ValueError("provenance must be non-empty for a derived object")

    metadata: dict[str, object] = {
        "type": type,
        "title": title,
        "description": description,
        "tags": [],
        "timestamp": timestamp,
        "status": "active",
        "version": 1,
        "freshness": "snapshot",
        "sensitivity": sensitivity,
        "provenance": provenance,
    }
    related = "\n".join(
        f"- [{ref}](/{ref}.md) — source this was extracted from" for ref in provenance
    )
    # `description` is a one-line lede; append `body` only when it adds content,
    # so a blank-body fallback does not render the description paragraph twice.
    lede = description if not body.strip() else f"{description}\n\n{body}"
    doc_body = f"# {title}\n\n{lede}\n\n## Related\n\n{related}\n"
    return dump_frontmatter(metadata, doc_body)


@dataclass(frozen=True)
class DocScan:
    """One `_iter_docs` result: a non-reserved `.md` file, scanned once.

    Exactly one of `metadata`, `read_error`, or `parse_error` is set (the
    other two are `None`) -- a successfully read AND parsed file has
    `metadata` populated (possibly `{}`) and both errors `None`; a file that
    could not be opened/decoded has `read_error` set and `metadata`/
    `parse_error` `None`; a file that was read but whose frontmatter did not
    parse has `parse_error` set and `metadata`/`read_error` `None`.
    """

    path: Path
    metadata: dict[str, object] | None
    read_error: OSError | UnicodeDecodeError | None
    parse_error: str | None


def _iter_docs(bundle_dir: Path) -> Iterator[DocScan]:
    """Walk every non-reserved `.md` file under `bundle_dir` exactly once (D2).

    `sorted(rglob("*.md"))` is the SAME walk `check_conformance` used before
    this refactor, so both `check_conformance` and `survey_bundle` (Phase 2)
    observe files in identical order. A file that cannot be opened or
    decoded yields a `DocScan` with `read_error` set instead of raising --
    `check_conformance` re-raises it (preserving its documented raise
    contract); `survey_bundle` degrades it to a finding (D3). A file whose
    frontmatter does not parse, or that has no parseable frontmatter block,
    yields `parse_error` set to the SAME message text `check_conformance`
    has always produced for that case.
    """
    for path in sorted(bundle_dir.rglob("*.md")):
        if path.name in RESERVED_FILENAMES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            yield DocScan(path, None, exc, None)
            continue
        try:
            post = frontmatter.loads(text)
        except Exception as exc:  # broad: any parse failure is a rule-1 violation
            yield DocScan(path, None, None, f"no parseable frontmatter ({exc})")
            continue
        if post.handler is None:
            yield DocScan(path, None, None, "no parseable frontmatter")
        else:
            yield DocScan(path, post.metadata, None, None)


@dataclass(frozen=True)
class BundleSurvey:
    """Counts and §9 findings for one `_iter_docs` pass over a bundle (Phase 2/D2).

    `findings` is a SUPERSET of `check_conformance`'s violations: it adds a
    per-file "unreadable" line for a `read_error` (D3), which
    `check_conformance` instead raises, PLUS one "unreadable directory" line
    per subdirectory `_iter_docs`'s `rglob` walk could not descend into (its
    `OSError` is silently swallowed by `scandir()`, per stdlib `glob`
    behavior). A file contributing a finding is counted as NEITHER a source
    nor a concept; an unreadable subdirectory's contents are unknown, so it
    affects no count at all -- only `findings`.
    """

    sources: int
    concepts: int
    findings: list[str]


def _walk_errors(bundle_dir: Path) -> list[OSError]:
    """Collect directory-scan `OSError`s that `_iter_docs`'s `rglob` walk
    would silently swallow, without yielding any file paths.

    `Path.rglob` never surfaces `scandir()` failures on a subdirectory it
    cannot descend into -- the subtree just vanishes from the walk with no
    signal. This walks the SAME tree with `os.walk`'s `onerror` hook solely
    to capture those errors as data (each has `.filename` set to the
    unreadable directory); `_iter_docs` and `check_conformance` are
    untouched and stay byte-identical.
    """
    errors: list[OSError] = []
    for _ in os.walk(bundle_dir, onerror=errors.append):
        pass
    return errors


def survey_bundle(bundle_dir: Path) -> BundleSurvey:
    """Survey `bundle_dir` for source/concept counts and §9-shaped findings (D2/D3).

    Consumes the SAME `_iter_docs` walk `check_conformance` uses, in one
    pass: `type == "Source"` counts as a source, any other non-empty `type`
    counts as a concept, and every read error, parse error, or missing/empty
    `type` becomes a finding instead of a count -- including a per-file read
    error, which `survey_bundle` degrades to a finding rather than raising
    (D3, Q3), unlike `check_conformance`. Directory-scan errors that
    `_iter_docs`'s walk silently drops (see `_walk_errors`) are appended as
    one finding per unreadable directory, sorted by path for determinism, so
    an unscanned subtree is never invisible to a caller reading `findings`
    alone -- it never affects `sources`/`concepts`, since that subtree's
    contents are unknown.
    """
    sources = 0
    concepts = 0
    findings: list[str] = []
    for scan in _iter_docs(bundle_dir):
        if scan.read_error is not None:
            findings.append(f"{scan.path}: unreadable ({scan.read_error})")
        elif scan.parse_error is not None:
            findings.append(f"{scan.path}: {scan.parse_error}")
        else:
            doc_type = (scan.metadata or {}).get("type")
            if not doc_type:
                findings.append(f"{scan.path}: missing non-empty 'type'")
            elif doc_type == "Source":
                sources += 1
            else:
                concepts += 1
    for walk_error in sorted(
        _walk_errors(bundle_dir), key=lambda exc: str(exc.filename)
    ):
        findings.append(f"{walk_error.filename}: unreadable directory ({walk_error})")
    return BundleSurvey(sources, concepts, findings)


def check_conformance(bundle_dir: Path) -> list[str]:
    """Check §9 rules 1-2 against every non-reserved `.md` file under `bundle_dir`.

    An empty list means conformant; a fresh, empty bundle passes vacuously
    because there are no non-reserved `.md` files to violate either rule.
    May raise `OSError` or `UnicodeDecodeError` when a candidate file cannot
    be read or decoded -- those are inspection failures, never reported as
    conformance violations. Consumes the shared `_iter_docs` walk (D2) and
    re-raises `read_error` to preserve this exact contract; output is
    byte-identical to the pre-refactor implementation (regression-guarded by
    `tests/unit/model/test_okf.py::test_check_conformance_round_trip_regression`).
    """
    violations: list[str] = []
    for scan in _iter_docs(bundle_dir):
        if scan.read_error is not None:
            raise scan.read_error
        if scan.parse_error is not None:
            violations.append(f"{scan.path}: {scan.parse_error}")
        elif not (scan.metadata or {}).get("type"):
            violations.append(f"{scan.path}: missing non-empty 'type'")
    return violations
