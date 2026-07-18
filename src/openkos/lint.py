"""Read-only bundle health check: stale inline stamps and orphan pages.

`lint` is the SECOND read command, mirroring `status`'s Phase-A-only shape
(design.md's Technical Approach): a pure read/validate, no writes, no
confirm. It reuses `okf._iter_docs` for the single `rglob` walk and
`okf.load_frontmatter` to split bodies, but keeps its OWN vocabulary
(`LintDoc`/`LintFinding`/`LintReport`), fully separate from
`okf.BundleSurvey`/`check_conformance` -- lint reports OpenKOS's opinion
about knowledge *health*, not OKF's verdict about *validity* (docs/cli.md).

The clock and the freshness window are always injected by the caller
(`cli/main.py::lint`); this module never calls `datetime.now()`, so every
function here is deterministic and testable with fixed inputs.
"""

import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path, PurePosixPath

from openkos import config
from openkos.model import okf


@dataclass(frozen=True)
class LintDoc:
    """One collected bundle doc: its path, identity, containing directory, and body."""

    path: Path
    identity: str
    """Bundle-relative POSIX path with the trailing `.md` stripped (Q1):
    the canonical form every markdown link normalizes to."""
    rel_dir: str
    """`identity`'s parent directory (`""` for a top-level doc), used to
    resolve a plain-relative link found in this doc's body."""
    body: str
    """The doc's text after its frontmatter block (`okf.load_frontmatter`)."""


@dataclass(frozen=True)
class LintFinding:
    """One lint finding: a flat, warning-level signal (no error/warning tiers)."""

    kind: str
    """`"stale"` or `"orphan"`."""
    path: str
    """The finding's bundle-relative `.md` path, for display."""
    detail: str
    """Human-readable detail text, rendered verbatim after `path`."""


@dataclass(frozen=True)
class LintReport:
    """The full result of one `lint` run: stale-stamp and orphan findings, plus notices."""

    stale: list[LintFinding]
    orphans: list[LintFinding]
    notices: list[str]


def collect_docs(bundle_dir: Path) -> tuple[list[LintDoc], list[str]]:
    """Collect every readable, parseable, non-reserved doc under `bundle_dir`.

    Wraps `okf._iter_docs` for the single walk (D2). A `read_error`/
    `parse_error` doc is excluded from `docs` but surfaced as a skip
    notice, so it never reads as a false-clean scan. The body re-read
    (`okf.load_frontmatter`, keeping `okf.py` byte-unchanged) is guarded
    too: a TOCTOU failure there is also skipped with a notice. Returns
    `(docs, skip_notices)` in walk order.
    """
    docs: list[LintDoc] = []
    skip_notices: list[str] = []
    for scan in okf._iter_docs(bundle_dir):
        identity = scan.path.relative_to(bundle_dir).as_posix().removesuffix(".md")
        if scan.read_error is not None:
            skip_notices.append(f"{identity}.md: skipped (unreadable)")
            continue
        if scan.parse_error is not None:
            skip_notices.append(f"{identity}.md: skipped (unparseable frontmatter)")
            continue
        rel_dir = str(PurePosixPath(identity).parent)
        if rel_dir == ".":
            rel_dir = ""
        try:
            text = scan.path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            skip_notices.append(f"{identity}.md: skipped (unreadable)")
            continue
        try:
            _, body = okf.load_frontmatter(text)
        except Exception:  # broad: a concurrent edit can corrupt frontmatter mid-scan
            skip_notices.append(f"{identity}.md: skipped (unparseable frontmatter)")
            continue
        docs.append(
            LintDoc(path=scan.path, identity=identity, rel_dir=rel_dir, body=body)
        )
    return docs, skip_notices


_WINDOW_RE = re.compile(r"\A(\d+)([dw])\Z")


def parse_window(raw: object) -> timedelta:
    """Parse the `<N>d`/`<N>w` freshness-window grammar (Q4).

    `raw` is typed `object`: a non-`str` value (e.g. an int/bool from
    user-edited `openkos.yaml`) raises `ValueError` immediately, like an
    unparseable string, instead of an uncaught `AttributeError`.

    `N` is a positive integer; `w` multiplies by 7 (a week). Surrounding
    whitespace is tolerated. Raises `ValueError` on a zero, negative, or
    otherwise unparseable value -- never returns a non-positive `timedelta`.
    """
    if not isinstance(raw, str):
        raise ValueError(f"freshness_window: invalid duration {raw!r}")
    match = _WINDOW_RE.match(raw.strip())
    if match is None:
        raise ValueError(f"freshness_window: invalid duration {raw!r}")
    count = int(match.group(1))
    if count <= 0:
        raise ValueError(f"freshness_window: duration must be positive, got {raw!r}")
    days = count * 7 if match.group(2) == "w" else count
    return timedelta(days=days)


def resolve_window(raw: object) -> tuple[timedelta, str | None]:
    """Resolve `raw` to a `(window, notice)` pair, never raising (Q4).

    `raw` is typed `object`, matching `parse_window`: a non-`str` value
    falls back exactly like an unparseable string.

    A valid `raw` resolves to `(parse_window(raw), None)`. An unparseable,
    zero, or negative `raw` falls back to `config.DEFAULT_FRESHNESS_WINDOW`
    and returns a notice describing the fallback -- `lint` degrades on bad
    config instead of crashing, matching the read-only-never-fail contract
    every `lint` scan honors.
    """
    try:
        return parse_window(raw), None
    except ValueError:
        notice = (
            f"openkos lint: freshness_window '{raw}' is not a valid duration; "
            f"using default {config.DEFAULT_FRESHNESS_WINDOW}."
        )
        return parse_window(config.DEFAULT_FRESHNESS_WINDOW), notice


_STAMP_RE = re.compile(r"\(as of (\d{4}-\d{2}-\d{2})\)")


def check_stale_stamps(
    docs: list[LintDoc], *, today: date, window: timedelta
) -> list[LintFinding]:
    """Flag any inline `(as of YYYY-MM-DD)` stamp older than `window` (Q5).

    Reads only inline body text, never the `freshness` field (a
    `freshness: snapshot` concept carries no `(as of ...)` stamp by design,
    so a pure-ingest bundle produces zero findings here). `_STAMP_RE`
    shape-matches, then `date(y, m, d)` is attempted in a `try`/`except
    ValueError` -- a non-date like `2026-13-45` is silently skipped, never
    flagged, never crashes (MVP-1 lenient). A stamp is stale iff `today -
    stamp > window` (an exact-boundary stamp is NOT stale). One finding is
    produced per unique `(identity, stamp text)` pair, so a stamp repeated
    verbatim within one body never double-counts. `today` and `window` are
    always injected -- this function never calls `datetime.now()`.
    """
    findings: list[LintFinding] = []
    seen: set[tuple[str, str]] = set()
    for doc in docs:
        for stamp_text in _STAMP_RE.findall(doc.body):
            key = (doc.identity, stamp_text)
            if key in seen:
                continue
            year, month, day = (int(part) for part in stamp_text.split("-"))
            try:
                stamp_date = date(year, month, day)
            except ValueError:
                continue
            seen.add(key)
            age_days = (today - stamp_date).days
            if age_days > window.days:
                findings.append(
                    LintFinding(
                        kind="stale",
                        path=f"{doc.identity}.md",
                        detail=(
                            f"(as of {stamp_text}) is {age_days} days old "
                            f"(window {window.days}d)"
                        ),
                    )
                )
    return findings


_SCHEME_RE = re.compile(r"\A[A-Za-z][A-Za-z0-9+.-]*:")


def normalize_link(target: str, source_rel_dir: str) -> str | None:
    """Normalize a raw markdown link target to its canonical bundle identity (Q1).

    One identity unifies every link FORM OKF tolerates for the same doc:
    `/concepts/x.md`, `concepts/x.md`, `./x.md`, `../concepts/x.md`, and the
    extension-less `concepts/x` all resolve to `"concepts/x"` -- matching
    `LintDoc.identity` exactly, so a link-form choice never produces a false
    orphan. A trailing `#fragment` or ` "title"` (markdown link title
    syntax) is stripped first. An external `scheme:` URL (`http:`,
    `https:`, `mailto:`, ...) is not a bundle link and normalizes to
    `None`, as does a link that resolves to nothing (a pure in-page anchor)
    or that escapes the bundle root via `..`.
    """
    target = target.split("#", 1)[0].strip()
    if target.endswith('"') and ' "' in target:
        target = target.rsplit(' "', 1)[0].strip()
    if not target:
        return None
    if _SCHEME_RE.match(target):
        return None
    candidate = (
        PurePosixPath(target.lstrip("/"))
        if target.startswith("/")
        else PurePosixPath(source_rel_dir) / target
    )
    parts: list[str] = []
    for part in candidate.parts:
        if part == "..":
            if not parts:
                return None
            parts.pop()
        else:
            parts.append(part)
    return "/".join(parts).removesuffix(".md")


_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


def check_orphans(docs: list[LintDoc], *, index_text: str) -> list[LintFinding]:
    """Flag any doc not referenced by a markdown link from `index.md` or
    another doc's body (Q2/Q3).

    The referenced-set is built from links scanned in `index_text` PLUS
    every collected doc's body -- `log.md` is structurally EXCLUDED (there
    is no `log_text` parameter here), since it links every logged doc and
    would otherwise nullify orphan detection entirely. Treatment is
    UNIFORM across doc types: a `type: Source` doc is orphan-able exactly
    like a concept (Q3) -- `ingest` already catalogs every Source in
    `index.md`'s `# Sources`, so a properly ingested Source is inherently
    referenced. Each link target is resolved via `normalize_link` against
    its linking doc's directory (`""` for `index.md`, `doc.rel_dir` for a
    doc body), so every OKF-tolerated link form counts equally. A doc's
    link to ITSELF is excluded: the contract is "referenced by ANOTHER
    doc", so a self-link must not hide an otherwise-orphan doc.
    """
    referenced: set[str] = set()
    for target in _LINK_RE.findall(index_text):
        identity = normalize_link(target, "")
        if identity is not None:
            referenced.add(identity)
    for doc in docs:
        for target in _LINK_RE.findall(doc.body):
            identity = normalize_link(target, doc.rel_dir)
            if identity is not None and identity != doc.identity:
                referenced.add(identity)
    return [
        LintFinding(
            kind="orphan",
            path=f"{doc.identity}.md",
            detail="not referenced by index.md or any concept",
        )
        for doc in docs
        if doc.identity not in referenced
    ]
