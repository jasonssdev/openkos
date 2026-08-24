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
import unicodedata
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path, PurePosixPath
from typing import Final

from openkos import config, fsio
from openkos.bundle import provenance as bundle_provenance
from openkos.model import okf, types
from openkos.model import relations as relation_vocabulary


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
    freshness: str
    """The doc's frontmatter `freshness` field, or `""` if absent
    (ingest-source-body D4). `check_stale_stamps` skips any doc whose
    `freshness` is `"snapshot"` -- such docs (as produced by `openkos
    ingest`) embed verbatim source text that MAY coincidentally contain an
    `(as of ...)`-shaped string that is not a maintained freshness stamp."""

    type: str
    """The doc's frontmatter `type` field, or `""` if absent
    (freshness-lint-v1). Feeds `window_for_doc`'s per-type default
    volatility lookup."""

    volatility: str
    """The doc's frontmatter `volatility` field, or `""` if absent
    (freshness-lint-v1, `concept-volatility` spec). Absent-by-default:
    `openkos ingest` never emits this key, so `""` is the overwhelmingly
    common case, meaning resolution falls through to the per-type default.
    Distinct from `freshness`, which stays a binary snapshot/non-snapshot
    skip flag, never a volatility signal."""

    relations: tuple[str, ...] = ()
    """This doc's `relations:` frontmatter targets, decoded via
    `okf.decode_relations` (purge-transactional-cleanup #141). Each entry is
    already the canonical, `.md`-stripped bundle-relative concept id --
    byte-identical to `LintDoc.identity`'s shape, no normalization needed
    (unlike a body markdown link, which must go through `normalize_link`).
    Defaults to `()` so every pre-existing construction/test fixture that
    omits it is unaffected. Feeds `check_dangling_targets` as one of its two
    outbound-reference sources."""

    engine_owned_relations: tuple[tuple[str, str], ...] = ()
    """This doc's `relations:` entries whose `type` is engine-owned, as
    `(type, target)` pairs in frontmatter order (issue #421).

    A NARROWING of `relations`, not a duplicate of it: `relations` keeps
    every target and drops every type, which is all `check_dangling_targets`
    needs; this keeps the type, but only for
    `relations.ENGINE_OWNED_RELATION_TYPES` -- today exactly
    `derived_from`. The filter is applied in `collect_docs` against the
    registry rather than against a literal, so a second engine-derived type
    is FOLLOWED here with no edit to this module (issue #421's third open
    question). Feeds `check_unbacked_provenance`.

    Types outside that set are deliberately absent: an open-vocabulary
    relation (`related_to`, `references`, ...) carries no provenance
    meaning, so its target has no reason to appear in `provenance:` and
    checking it would flag every typed edge in every bundle."""

    extraction_status: str = ""
    """The doc's frontmatter `extraction_status` field, or `""` if absent
    (issue #187). Only a Source can carry this key
    (`okf.build_source_concept`); every other doc type simply defaults to
    `""`, same as `freshness`/`type`/`volatility`. Feeds
    `check_unextracted`, which matches ONLY the literal `"failed"` value --
    any other token, including an unrecognized one, is ignored fail-silent
    (design's write-side-typed/read-side-fail-silent policy)."""

    extraction_notice: str = ""
    """The doc's frontmatter `extraction_notice` field, or `""` if absent
    (issue #772). Only a Source can carry this key
    (`okf.build_source_concept`); every other doc type defaults to `""`,
    same as `extraction_status`. Feeds `check_unjudged`, which matches ONLY
    the two judge-degrade tokens -- #585's `sole-object-restates-source`
    and any unrecognized token are ignored fail-silent (the same
    write-side-typed/read-side-fail-silent policy `extraction_status`
    documents) -- `check_unevidenced` (#801), which matches ONLY
    `objects-without-evidence` and ignores the rest the same way -- and
    `check_staging_dropped` (#843), which matches ONLY
    `candidates-dropped-in-staging`, again the same way. One field, three
    readers, disjoint by construction: a Source carries at most one token,
    and the checks answer different questions about it."""

    resource: str = ""
    """The doc's frontmatter `resource` field, or `""` if absent (issue
    #187). `openkos ingest` sets this to `raw/<name>` on every Source; it
    is the input `check_unextracted` names in its retry command detail."""

    sensitivity: str = ""
    """The doc's frontmatter `sensitivity` field, or `""` if absent
    (#231, PR2). Defaulted like `extraction_status`/`resource` (#187):
    `tests/unit/resolution/test_volatility_typing.py:612` constructs
    `LintDoc` with only the seven non-defaulted fields above. Feeds
    `check_below_source_sensitivity`'s `okf.combine_sensitivity` comparison
    -- a missing/blank value ranks fail-closed (ADR-0003), never crashes."""

    provenance: tuple[str, ...] = ()
    """This doc's `provenance:` frontmatter list, `.md`-stripped to the
    same canonical shape as `identity`/`relations` (#231, PR2). Defaults to
    `()` for the same reason `relations` does. Feeds
    `check_below_source_sensitivity`'s closure-membership computation via
    `bundle.provenance.provenance_closure` -- never rendered into
    write-ready content (design D2: `LintDoc` keeps `body` only, not the
    full file text `resolve_source_raises` would need)."""


@dataclass(frozen=True)
class LintFinding:
    """One lint finding: a flat, warning-level signal (no error/warning tiers)."""

    kind: str
    """`"stale"`, `"orphan"`, `"dangling"`, `"unextracted"`,
    `"unjudged"`, `"unevidenced"`, `"below-source-sensitivity"`,
    `"multi-source-uncovered"`, `"dangling-provenance"`,
    `"unbacked-provenance"`, or `"non-nfc-name"`."""
    path: str
    """The finding's bundle-relative `.md` path -- except for
    `"non-nfc-name"` (#474), where it may name a directory or a non-`.md`
    file: that kind reports an on-disk ENTRY, not a concept object, so
    `path`, not `concept_id`, is the honest spelling there (the same
    reasoning the `concept_id` docstring gives for skip notices)."""
    detail: str
    """Human-readable detail text, rendered verbatim after the subject."""

    remediation: str = ""
    """The exact runnable command that RESOLVES this finding, or `""` when
    the kind has none to offer (issue #693).

    Engine-computed, never lifted out of `detail`. That distinction is the
    point of the field, not a stylistic preference: `detail` interpolates
    values the bundle's own documents control -- a raw `sensitivity` string,
    cited concept ids -- so a document can plant a backtick span spelling a
    plausible command with the wrong argument. `cli/next_action.py` already
    carries two hard-won guards against exactly that (#274, and the trap-1
    filter). A field the engine computes is not forgeable from frontmatter,
    so a consumer can trust it without re-deriving those guards.

    Opt-in per kind rather than retrofitted onto all nine: today only
    `multi-source-uncovered` populates it, because that is the kind #693 is
    about -- `status` listed it as actionable while `next` had no runnable
    command to offer for it. `below-source-sensitivity` keeps its existing
    detail-parsing tier untouched. A later kind earns this field when it has
    a consumer, not on principle."""

    @property
    def concept_id(self) -> str:
        """`path` minus its `.md` extension -- the OKF Concept ID (SPEC §2),
        which is how `list`, `set-sensitivity`, and every other verb spell
        this object. Prefer this over `path` when DISPLAYING a finding, so
        one object never reads two ways depending on the verb (issue #247).

        Exact, never a guess: every construction site in this module builds
        `path` as `f"{doc.identity}.md"`, and `doc.identity` IS the concept
        id. Skip notices are deliberately NOT covered -- they name a file
        that failed to become an object, so a path is the honest spelling
        there. `"non-nfc-name"` findings (#474) are not covered either, for
        the same reason: their `path` names an on-disk entry -- possibly a
        directory or a non-`.md` file -- not a concept object, so
        displaying them goes through `path`, never this property."""
        return self.path.removesuffix(".md")


@dataclass(frozen=True)
class LintReport:
    """The full result of one `lint` run: stale-stamp, orphan, and
    dangling-reference findings, plus notices."""

    stale: list[LintFinding]
    orphans: list[LintFinding]
    dangling: list[LintFinding] = field(default_factory=list)
    unextracted: list[LintFinding] = field(default_factory=list)
    unjudged: list[LintFinding] = field(default_factory=list)
    """`"unjudged"` findings (#772): a Source whose `extraction_notice`
    carries a judge-degrade token, meaning its derived objects were stored
    without quality selection -- retryable debt, rendered under its own
    `Unjudged extractions:` section (see `check_unjudged`)."""
    unevidenced: list[LintFinding] = field(default_factory=list)
    """`"unevidenced"` findings (#801): a Source whose `extraction_notice`
    carries `objects-without-evidence`, meaning at least one derived object
    it stored quotes no line of it and therefore cannot support a citation
    -- rendered under its own `Unevidenced objects:` section (see
    `check_unevidenced`).

    Its own field rather than a widening of `unjudged`, for the reason that
    check's docstring gives: the two answer different questions and have
    different repairs, and one shared list would leave `lint` unable to
    render them apart."""
    staging_dropped: list[LintFinding] = field(default_factory=list)
    """`"staging-dropped"` findings (#843): a Source whose
    `extraction_notice` carries `candidates-dropped-in-staging`, meaning
    extraction produced at least one candidate the run could not store --
    the bundle may under-represent the source -- rendered under its own
    `Staging-dropped candidates:` section (see `check_staging_dropped`)."""
    below_source: list[LintFinding] = field(default_factory=list)
    """`"below-source-sensitivity"` findings (#231, PR2): a descendant
    inside exactly one `type: Source` closure whose `sensitivity` differs
    from `okf.combine_sensitivity(descendant, source_level)` -- exactly
    what `backfill-sensitivity` would stage as a write."""
    multi_source_uncovered: list[LintFinding] = field(default_factory=list)
    """`"multi-source-uncovered"` findings (#231, PR2): a doc with
    non-empty `provenance` that is a member of no single-Source closure and
    whose `sensitivity` sits below the high-water-mark of its cited
    concepts' levels -- explicitly NOT covered by `backfill-sensitivity`."""
    dangling_provenance: list[LintFinding] = field(default_factory=list)
    """`"dangling-provenance"` findings (#257): a `provenance:` entry that
    resolves to no collected doc AND is not the doc's own raw `resource`
    value -- the outbound-reference family `check_dangling_targets` never
    scans, with the sensitivity consequence that earned the dedicated kind
    (see `check_dangling_provenance`)."""
    unbacked_provenance: list[LintFinding] = field(default_factory=list)
    """`"unbacked-provenance"` findings (#421): a `relations:` entry whose
    type is engine-owned (`derived_from`) naming a target the SAME
    document's `provenance:` does not record -- a provenance claim no
    recorded provenance backs. The graph projection synthesizes
    `derived_from` from `provenance:` (#135), so once written such an edge
    is indistinguishable downstream from a synthesized one; this finding is
    the only surface that still tells them apart (see
    `check_unbacked_provenance`)."""
    non_nfc: list[LintFinding] = field(default_factory=list)
    """`"non-nfc-name"` findings (#474): an on-disk name (file OR
    directory) under the bundle that is not NFC, with the NFC rename as
    remediation. Concept ids are canonically NFC (`okf.concept_id_for`,
    #430) and `okf.concept_path_for` resolves an NFC id against a
    decomposed on-disk spelling TOLERANTLY -- its docstring promises that
    a caller who wants to know a bundle carries decomposed names should
    ask `lint`; this field is where that answer lands (see
    `check_non_nfc_names`)."""
    state_dir_markdown: list[LintFinding] = field(default_factory=list)
    """`"state-dir-markdown"` findings (task 3.6, safety net for design
    Decision 3's EXCLUDE/INCLUDE separation): a `.md` file found under
    `bundle/.state/`, where only non-`.md` derived-state sidecars (the
    merge ledger) belong -- see `check_state_dir_contains_no_markdown`."""
    notices: list[str] = field(default_factory=list)


def collect_docs(bundle_dir: Path) -> tuple[list[LintDoc], list[str]]:
    """Collect every readable, parseable, non-reserved doc under `bundle_dir`.

    Wraps `okf._iter_docs` for the single walk (D2). A `read_error`/
    `parse_error` doc is excluded from `docs` but surfaced as a skip
    notice, so it never reads as a false-clean scan. The body re-read
    (`okf.load_frontmatter`, keeping `okf.py` byte-unchanged) is guarded
    too: a TOCTOU failure there is also skipped with a notice. `relations:`
    is decoded via `okf.decode_relations` (purge-transactional-cleanup
    #141); a corrupt `relations:` value (e.g. hand-edited to a non-list)
    raises `ValueError` there, caught here and surfaced as a skip notice --
    read-only-never-fail, matching every other guard in this function.
    `provenance:` follows the SAME convention (#231, PR2): an absent key is
    valid and decodes to an empty tuple with no notice, but a present
    non-list value (e.g. hand-edited to a scalar) is skipped with a notice
    rather than coerced to empty -- a silent coercion would hide the doc
    from every provenance-based check and read as a false clean scan.
    Returns `(docs, skip_notices)` in walk order.
    """
    docs: list[LintDoc] = []
    skip_notices: list[str] = []
    for scan in okf._iter_docs(bundle_dir):
        identity = okf.concept_id_for(scan.path, bundle_dir)
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
            metadata, body = okf.load_frontmatter(text)
        except Exception:  # broad: a concurrent edit can corrupt frontmatter mid-scan
            skip_notices.append(f"{identity}.md: skipped (unparseable frontmatter)")
            continue
        try:
            decoded_relations = okf.decode_relations(metadata)
        except ValueError:
            skip_notices.append(f"{identity}.md: skipped (invalid relations)")
            continue
        relations = tuple(relation.target for relation in decoded_relations)
        # #421: the SAME already-decoded list, filtered against the registry
        # rather than a literal `derived_from`, so a second engine-derived
        # type is followed here with no edit. No second `decode_relations`
        # call and no second read -- one decode, two projections.
        engine_owned_relations = tuple(
            (relation.type, relation.target)
            for relation in decoded_relations
            if relation.type in relation_vocabulary.ENGINE_OWNED_RELATION_TYPES
        )
        raw_provenance = metadata.get("provenance")
        if raw_provenance is None:
            provenance: tuple[str, ...] = ()
        elif isinstance(raw_provenance, list):
            provenance = tuple(
                str(entry).removesuffix(".md") for entry in raw_provenance
            )
        else:
            skip_notices.append(f"{identity}.md: skipped (invalid provenance)")
            continue
        docs.append(
            LintDoc(
                path=scan.path,
                identity=identity,
                rel_dir=rel_dir,
                body=body,
                freshness=str(metadata.get("freshness", "")),
                type=str(metadata.get("type", "")),
                volatility=str(metadata.get("volatility", "")),
                relations=relations,
                engine_owned_relations=engine_owned_relations,
                extraction_status=str(metadata.get("extraction_status", "")),
                extraction_notice=str(metadata.get("extraction_notice", "")),
                resource=str(metadata.get("resource", "")),
                sensitivity=str(metadata.get("sensitivity", "")),
                provenance=provenance,
            )
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


@dataclass(frozen=True)
class VolatilityWindows:
    """The three resolved stale-stamp windows `window_for_doc` picks from
    (freshness-lint-v1, design: "Data-model change"). `static` has no
    window value here -- a `static`-tier doc always resolves to `None`
    (never flagged) in `window_for_doc`, never reading this dataclass."""

    slow: timedelta
    volatile: timedelta
    fallback: timedelta
    """Global `freshness_window` fallback, used when a doc's type is
    unresolvable (unknown or absent) AND it carries no per-concept
    `volatility` override -- the same window MVP-1 applied uniformly."""

    type_tiers: dict[str, str] = field(default_factory=dict)
    """`type_tiers` config override map (freshness-suggest-windows,
    `concept-volatility` spec ADDED requirement), already guarded to a
    mapping by `resolve_windows` (mirroring the `volatility_windows`
    non-mapping guard). Read by `window_for_doc`'s new precedence step,
    which sits between the per-concept `volatility` override and the
    per-type registry default: a `type_tiers[doc.type]` value that is a
    `str` member of `types.VOLATILITY_TIERS` overrides the registry
    default; an unknown `doc.type` key, a non-`str` value (e.g. a
    list/dict from a hand-edited `openkos.yaml` -- unhashable against the
    `VOLATILITY_TIERS` frozenset), or an invalid tier value is ignored --
    `.get` never raises on a missing key, and an `isinstance(str)` guard
    runs BEFORE the `VOLATILITY_TIERS` membership check so a non-`str`
    value never reaches (and never raises against) that `frozenset`
    `in`/`not in` test. Defaults to `{}` so every construction that omits
    it (including every pre-existing test fixture) reproduces exact S1
    precedence unchanged."""


def resolve_windows(cfg: config.Config) -> tuple[VolatilityWindows, list[str]]:
    """Resolve `cfg.volatility_windows`/`cfg.freshness_window` to a
    `(VolatilityWindows, notices)` pair, never raising (freshness-lint-v1,
    load-bearing).

    Precedence per tier: a present `volatility_windows[tier]` value wins;
    an absent key falls to `config.DEFAULT_VOLATILITY_WINDOWS[tier]`. Each
    resolved raw value -- including the `slow`/`volatile` defaults
    themselves -- is parsed via `resolve_window` (Q4), which ALREADY
    degrades a malformed/non-string value to `config.DEFAULT_FRESHNESS_WINDOW`
    with a notice, so a malformed per-tier override never raises here
    either; the fallback tier resolves the SAME way `lint`'s CLI has always
    resolved `cfg.freshness_window`. `cfg.volatility_windows` not being a
    mapping at all (`None`, a list, a scalar -- e.g. from hand-edited
    `openkos.yaml`) is treated as an empty map, so every tier falls to its
    packaged default rather than raising an `AttributeError` on `.get`.
    `cfg.type_tiers` (freshness-suggest-windows) gets the SAME non-mapping
    guard and is threaded onto the result verbatim -- validating individual
    entries (unknown type key, invalid tier value) is `window_for_doc`'s
    job, not this function's.
    """
    # `cfg.volatility_windows`/`cfg.type_tiers` are typed `dict[str, str]`,
    # but a hand-edited `openkos.yaml` can hold a non-mapping at runtime
    # (e.g. a list or a bare scalar) -- widen to `object` first so the
    # `isinstance` runtime guards below are not mypy-redundant against the
    # (merely aspirational) static type.
    raw_windows: object = cfg.volatility_windows
    raw_map = raw_windows if isinstance(raw_windows, dict) else {}
    raw_type_tiers: object = cfg.type_tiers
    type_tiers_map = raw_type_tiers if isinstance(raw_type_tiers, dict) else {}
    notices: list[str] = []
    slow_window, slow_notice = resolve_window(
        raw_map.get("slow", config.DEFAULT_VOLATILITY_WINDOWS["slow"])
    )
    volatile_window, volatile_notice = resolve_window(
        raw_map.get("volatile", config.DEFAULT_VOLATILITY_WINDOWS["volatile"])
    )
    fallback_window, fallback_notice = resolve_window(cfg.freshness_window)
    for notice in (slow_notice, volatile_notice, fallback_notice):
        if notice is not None:
            notices.append(notice)
    windows = VolatilityWindows(
        slow=slow_window,
        volatile=volatile_window,
        fallback=fallback_window,
        type_tiers=type_tiers_map,
    )
    return windows, notices


def window_for_doc(doc: "LintDoc", windows: VolatilityWindows) -> timedelta | None:
    """Resolve `doc`'s stale-stamp window, or `None` if it must never be
    flagged (freshness-lint-v1 + freshness-suggest-windows, load-bearing
    precedence).

    Tier precedence: (1) `doc.volatility.strip()`, if a member of
    `types.VOLATILITY_TIERS` -- the per-concept override always wins; (2)
    else `windows.type_tiers.get(doc.type)`, if `doc.type` is itself a
    REGISTERED type (a key of `types.TYPE_TO_DEFAULT_VOLATILITY`) AND the
    looked-up value is a `str` member of `types.VOLATILITY_TIERS` -- the
    config `type_tiers` override (freshness-suggest-windows,
    `concept-volatility` spec ADDED requirement: an entry is ignored if
    its type name is unknown, its tier value is invalid, OR its tier
    value is not even a `str` -- e.g. a hand-edited `openkos.yaml` holding
    a list/dict/int/bool/None -- so all three conditions are checked here,
    not just tier-value validity); (3) else
    `types.TYPE_TO_DEFAULT_VOLATILITY.get(doc.type)` -- the per-type
    registry default; (4) else the global fallback tier. An unknown or
    absent `volatility` value degrades silently to step 2, an unregistered
    `doc.type`, a non-`str` `type_tiers` value (list/dict/int/bool/None,
    including unhashable ones that would otherwise raise `TypeError`
    against the `VOLATILITY_TIERS` frozenset), or an invalid tier value in
    `type_tiers` degrades silently to step 3, and an unknown or absent
    `type` degrades silently to step 4 -- none of these degrade paths ever
    raises. `static` (reached via
    override, `type_tiers`, or type default) resolves to `None`;
    `slow`/`volatile` resolve to their `windows` entry; the fallback tier
    resolves to `windows.fallback`.
    """
    tier = doc.volatility.strip()
    if (
        tier not in types.VOLATILITY_TIERS
        and doc.type in types.TYPE_TO_DEFAULT_VOLATILITY
    ):
        # `windows.type_tiers` is typed `dict[str, str]`, but a hand-edited
        # `openkos.yaml` can hold a non-string value (unhashable list/dict,
        # or a hashable int/bool/None) at runtime -- widen to `object`
        # first so the `isinstance` guard below is not mypy-redundant
        # against the (merely aspirational) static type, mirroring
        # `resolve_windows`'s same widen-then-guard pattern above.
        candidate_tier: object = windows.type_tiers.get(doc.type, "")
        # Guard with `isinstance` BEFORE the `VOLATILITY_TIERS` membership
        # check below, since `in`/`not in` against a `frozenset` raises
        # `TypeError` on an unhashable value instead of degrading. A
        # non-`str` candidate degrades to `""`, which falls through to the
        # registry default exactly like any other invalid tier value.
        tier = candidate_tier if isinstance(candidate_tier, str) else ""
    if tier not in types.VOLATILITY_TIERS:
        tier = types.TYPE_TO_DEFAULT_VOLATILITY.get(doc.type, "")
    if tier == "static":
        return None
    if tier == "slow":
        return windows.slow
    if tier == "volatile":
        return windows.volatile
    return windows.fallback


_STAMP_RE = re.compile(r"\(as of (\d{4}-\d{2}-\d{2})\)")


def check_stale_stamps(
    docs: list[LintDoc], *, today: date, windows: VolatilityWindows
) -> list[LintFinding]:
    """Flag any inline `(as of YYYY-MM-DD)` stamp older than its doc's
    volatility-resolved window (Q5, freshness-lint-v1).

    Reads only inline body text, EXCEPT that any doc whose `freshness` is
    `"snapshot"` is skipped entirely (ingest-source-body D4): such docs (as
    produced by `openkos ingest`) embed verbatim source text that MAY
    coincidentally contain an `(as of ...)`-shaped string, and that text is
    not a maintained freshness stamp. Each surviving doc's window is
    resolved via `window_for_doc(doc, windows)` -- a `None` result means
    `static` tier (by override or type default): the doc is skipped
    entirely, regardless of stamp age. `_STAMP_RE` shape-matches, then
    `date(y, m, d)` is attempted in a `try`/`except ValueError` -- a
    non-date like `2026-13-45` is silently skipped, never flagged, never
    crashes (MVP-1 lenient). A stamp is stale iff `today - stamp >
    resolved_window` (an exact-boundary stamp is NOT stale). One finding is
    produced per unique `(identity, stamp text)` pair, so a stamp repeated
    verbatim within one body never double-counts. `today` and `windows` are
    always injected -- this function never calls `datetime.now()`.
    """
    findings: list[LintFinding] = []
    seen: set[tuple[str, str]] = set()
    for doc in docs:
        if doc.freshness == "snapshot":
            continue
        window = window_for_doc(doc, windows)
        if window is None:
            continue
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


def check_dangling_targets(docs: list[LintDoc]) -> list[LintFinding]:
    """Flag each OUTBOUND reference naming a concept id absent from `docs`
    (purge-transactional-cleanup #141, mirroring `check_orphans`'s inbound
    scan the opposite direction).

    An outbound reference is either (a) a `relations:` target
    (`doc.relations`, already canonical -- `okf.decode_relations`/
    `Relation.target` strips `.md` and normalizes the path shape, identical
    to `LintDoc.identity`'s form, so no further normalization is needed), or
    (b) a body markdown bundle link, resolved via the SAME `normalize_link`
    resolver `check_orphans` uses against `doc.rel_dir`. A link that
    normalizes to `None` (external `scheme:` URL, pure in-page anchor, or
    one that escapes the bundle root) or that resolves to the referring
    doc's OWN identity (a self-link/self-relation) is never flagged -- a
    self-reference always resolves to a doc that, by definition, exists.

    The existence set is `{d.identity for d in docs}` (exactly the doc set
    `collect_docs` returned). One finding is produced per unique
    `(referring doc, missing target)` pair, in the doc's own relations-then-
    body-links order, so a target referenced twice from the same doc never
    double-counts. This scan is READ-ONLY and NON-GATING: it never writes
    and its findings never affect any caller's exit code."""
    existing = {doc.identity for doc in docs}
    findings: list[LintFinding] = []
    for doc in docs:
        missing: list[str] = []
        seen: set[str] = set()
        for target in doc.relations:
            if target != doc.identity and target not in existing and target not in seen:
                seen.add(target)
                missing.append(target)
        for raw_target in _LINK_RE.findall(doc.body):
            identity = normalize_link(raw_target, doc.rel_dir)
            if identity is None or identity == doc.identity:
                continue
            if identity not in existing and identity not in seen:
                seen.add(identity)
                missing.append(identity)
        for target in missing:
            findings.append(
                LintFinding(
                    kind="dangling",
                    path=f"{doc.identity}.md",
                    detail=f"references missing concept '{target}'",
                )
            )
    return findings


def check_dangling_provenance(docs: list[LintDoc]) -> list[LintFinding]:
    """Flag each `provenance:` entry naming an id absent from `docs` (issue
    #257), walking `doc.provenance` the way `check_dangling_targets` walks
    `doc.relations` -- the one outbound-reference family that scan never
    reads, so a doc whose provenance cites a mistyped or since-removed id
    was previously silent in every check.

    The signature takes ONLY `docs` -- no `bundle_dir` parameter -- the
    SAME structural no-fifth-walk guard `check_dangling_targets`/
    `check_unextracted`/`check_below_source_sensitivity` follow: a function
    that never receives a directory is incapable of opening a walk.

    A doc's OWN raw `resource` entry never fires. Every Source is built
    with `provenance=[resource]` (`cli/main.py`, ingest), and a raw
    resource path (`raw/<name>`) never normalizes to a bundle id, so
    without this exclusion the check would report every Source in every
    bundle on every run -- the exact trap design D8 names for
    `bundle.provenance.resolve_backfill_raises`, which deliberately does
    not call `find_unresolvable_provenance` for the same reason. The
    discriminator is `doc.resource`, matched against BOTH the raw value
    and its `.md`-stripped form, because `collect_docs` strips `.md` off
    every provenance entry: a Source ingested from a markdown file carries
    `provenance=("raw/notes",)` against `resource="raw/notes.md"`. The
    exclusion is per-ENTRY, never per-doc: any other non-resolving entry
    on the same doc still fires.

    The detail carries the sensitivity consequence that justified the
    dedicated kind: a dangling entry excludes the doc FAIL-CLOSED from
    every Source's provenance closure (`provenance_closure`'s subset
    rule), so `backfill-sensitivity` will never raise it and
    `set-sensitivity` cannot cascade to it -- it may still sit below its
    Source, and this finding is the only surface reporting that. The
    detail names both commands only to RULE THEM OUT; `openkos next` is
    protected from it by filtering on `finding.kind` BEFORE extracting any
    backtick span (`cli/next_action.py`, the same explicit gate
    `multi-source-uncovered`'s detail relies on).

    The existence set is `{d.identity for d in docs}`, and one finding is
    produced per unique `(citing doc, missing entry)` pair in the doc's
    own provenance order -- the SAME membership approach and
    one-finding-per-unique-pair contract `check_dangling_targets` pins.
    This scan is READ-ONLY and NON-GATING: it never writes and its
    findings never affect any caller's exit code."""
    existing = {doc.identity for doc in docs}
    findings: list[LintFinding] = []
    for doc in docs:
        own_resource: set[str] = set()
        if doc.resource:
            own_resource = {doc.resource, doc.resource.removesuffix(".md")}
        seen: set[str] = set()
        for target in doc.provenance:
            if target in own_resource:
                continue
            if target in existing or target in seen:
                continue
            seen.add(target)
            findings.append(
                LintFinding(
                    kind="dangling-provenance",
                    path=f"{doc.identity}.md",
                    detail=(
                        f"provenance cites missing concept '{target}' — "
                        "unreachable from any Source's provenance closure, so "
                        "`openkos backfill-sensitivity` will never raise it "
                        "and `openkos set-sensitivity` cannot cascade to it"
                    ),
                )
            )
    return findings


def check_unbacked_provenance(docs: list[LintDoc]) -> list[LintFinding]:
    """Flag each ENGINE-OWNED `relations:` entry whose target the SAME
    document's `provenance:` does not record (issue #421) -- a provenance
    claim no recorded provenance backs.

    `derived_from` MEANS provenance -- "this object was compiled from that
    source" -- and it is the guarantee behind citations and behind
    sensitivity propagation under the high-water-mark rule. The graph
    projection SYNTHESIZES it from each document's `provenance:` frontmatter
    (`graph/sqlite_graph.py`, #135), so a hand-written or model-suggested
    `derived_from` lands in the same graph, with the same type string, as
    the synthesized ones. Nothing downstream can tell the two apart -- which
    is what makes this silent corruption rather than a visible mistake, and
    it is why the projection itself cannot be the detector: by the time it
    reads the edge, the distinction is already gone. This check is the only
    place the two are still separable, because it reads the document, where
    `relations:` and `provenance:` are still distinct fields.

    #380/#418 closed the INGRESS (`SUGGESTABLE_RELATION_TYPES` withholds the
    type from the suggester, and `_parse_reply` refuses it anyway); this is
    the DETECTION half, for the claims already on disk.

    PURE AND DETERMINISTIC, deliberately: no LLM, no clock, no config. The
    signature takes ONLY `docs` -- no `bundle_dir` -- the SAME structural
    no-fifth-walk guard `check_dangling_targets`/`check_unextracted`/
    `check_below_source_sensitivity`/`check_dangling_provenance` follow: a
    function that never receives a directory is incapable of opening a
    walk. A provenance-integrity check that depended on a model would not
    be a check.

    The subject set is `relations.ENGINE_OWNED_RELATION_TYPES`, applied in
    `collect_docs` when `LintDoc.engine_owned_relations` is built, never a
    literal `derived_from` written here. Today that set has exactly one
    member, so this check has exactly one subject; if the engine ever
    derives a second type, this check FOLLOWS it rather than being
    re-written (issue #421). The `kind` string stays type-agnostic for the
    same reason -- the offending type is named in the DETAIL, where a human
    reads it, not in the kind, where a second member would make it a lie.

    A target that IS in `provenance:` is never flagged: that entry states
    exactly what the projection would synthesize anyway, so it is redundant
    at worst, never false. Comparison is by exact id: both fields are
    already canonical, `.md`-stripped bundle ids by the time they reach
    `LintDoc` (`okf.decode_relations`/`Relation.target` for one,
    `collect_docs`'s strip for the other), so no normalization is needed --
    the same equality `check_dangling_targets` relies on.

    A document with an EMPTY `provenance:` is checked like any other, not
    skipped: nothing backs an engine-owned claim there, which is the
    maximal case of this defect rather than an exemption. Existence of the
    target is NOT tested and is not this check's business --
    `check_dangling_targets` already reports a `relations:` target absent
    from the bundle, and a claim can be perfectly unbacked while pointing
    at a document that exists (both real occurrences in #421 target live
    Concepts).

    REPORT ONLY. The finding names the citing document, the relation type,
    the offending target, and the provenance the document does record --
    enough to judge the claim without reopening the file -- and it names NO
    command: removing a human-accepted `relations:` entry is a destructive
    edit no read-only verb may make, and no repair verb exists for it. The
    detail deliberately contains no backtick-spanned `openkos <verb>`
    command at all, so `cli/next_action.py`'s `_command_from_detail` has
    nothing to extract even if this kind were ever passed to it -- a
    stronger guard than `multi-source-uncovered`'s, which relies on its
    consumer's `finding.kind` filter.

    One finding per unique `(citing doc, type, target)` triple, in doc order
    then each doc's own `relations:` order -- the SAME
    one-finding-per-unique-pair contract `check_dangling_targets` and
    `check_dangling_provenance` pin. This scan is READ-ONLY and NON-GATING:
    it never writes and its findings never affect any caller's exit code."""
    findings: list[LintFinding] = []
    for doc in docs:
        backing = set(doc.provenance)
        recorded = (
            ", ".join(repr(entry) for entry in doc.provenance)
            if doc.provenance
            else "none recorded"
        )
        seen: set[tuple[str, str]] = set()
        for rel_type, target in doc.engine_owned_relations:
            if target in backing or (rel_type, target) in seen:
                continue
            seen.add((rel_type, target))
            findings.append(
                LintFinding(
                    kind="unbacked-provenance",
                    path=f"{doc.identity}.md",
                    detail=(
                        f"relations: asserts {rel_type} '{target}', which this "
                        f"document's own provenance does not record "
                        f"(provenance: {recorded}) — an unbacked provenance "
                        "claim: the graph projection synthesizes "
                        f"{rel_type} from provenance, so this edge is "
                        "indistinguishable from a real one once projected. "
                        "Nothing removes it automatically; judge it and edit "
                        "the document yourself"
                    ),
                )
            )
    return findings


_UNSPELLABLE_IN_SPAN = re.compile(r"[`\r\n]")
"""The characters a `resource` value MUST NOT carry for its retry command to
be spelled inside a single-line backtick span (issue #285).

Exactly three, each for a stated reason: a BACKTICK closes the span early,
leaving a well-formed `openkos ingest <plain path>` that names a DIFFERENT
file than the finding is about (issue #274); a CR or an LF splits the
single-line `LintFinding.detail` across lines, truncating it the same way.
All three are legal POSIX filename characters, so `resource` -- the raw,
unsanitised basename `ingest` copied to disk (see
`okf.build_source_concept`'s contract) -- really can carry them.

Deliberately NARROW, and NOT `cli/next_action.py`'s `_SAFE_ARGUMENT`. That
regex answers "is this runnable exactly as printed" and rejects far more
(spaces, semicolons, quotes, a leading `-`). This one answers only "can this
value be spelled unambiguously inside one backtick span", which every other
character does survive. Widening this set would decline hints for filenames
this module can name perfectly well -- and it would NOT make the hint safer,
because `next` corroborates the extracted command against the document's own
`resource` before printing it."""

_SAFE_COMMAND_ARGUMENT = re.compile(r"\A(?!-)[\w./-]+\Z")
"""Whether a concept id may be printed as a command argument (issue #693).

Deliberately IDENTICAL to `cli/next_action.py:_SAFE_ARGUMENT`, and a separate
copy rather than an import for the direction of the dependency: `lint` is a
leaf that `cli` reads, never the other way round. The two answer the same
question -- "is this runnable exactly as printed" -- and `LintFinding.
remediation` is a runnable command, so it must clear the same bar the
consumer would apply.

A concept id that does not clear it yields an EMPTY `remediation` rather
than an unrunnable one. The finding itself still fires: the problem is real,
and only the one-line shortcut is unavailable."""


def check_unextracted(docs: list[LintDoc]) -> list[LintFinding]:
    """Flag each Source whose extraction was skipped for a RETRYABLE reason
    (issue #187).

    Matches ONLY `doc.extraction_status == "failed"` -- the other three
    `okf.ExtractionStatus` values (`no-extractable-text`,
    `blocked-by-sensitivity`, `no-concepts-found`), and any unrecognized,
    out-of-vocabulary token, are ignored fail-silent (design's
    write-side-typed/read-side-fail-silent policy). `blocked-by-sensitivity`
    in particular is a deliberate policy outcome, never debt, and MUST NEVER
    appear as something to retry.

    The signature takes ONLY `docs` -- no `bundle_dir` parameter, exactly
    like `check_dangling_targets`. This is the STRUCTURAL no-fifth-walk
    guard (design: "structural, not procedural"): a function that never
    receives a directory is incapable of opening a walk, so a future edit
    cannot add one without changing this pinned signature.

    The finding's detail has THREE outcomes, not two:

    1. `resource` present and spellable -> the literal retry command built
       from the Source's own value (`openkos ingest <resource>`).
    2. `resource` empty -> a generic re-ingest hint naming the bare verb.
    3. `resource` present but UNSPELLABLE (`_UNSPELLABLE_IN_SPAN`) -> no
       command at all, and a hint naming the repair instead.

    Outcome 3 exists because `resource` is the raw, unsanitised basename of
    the ingested file (`okf.build_source_concept`'s contract, issue #285):
    it can legally carry a backtick, which closes the span below early and
    leaves a WELL-FORMED `openkos ingest <plain path>` naming a DIFFERENT
    file than this finding is about -- issue #274, printed by `openkos next`
    against a real bundle. A CR/LF breaks the single-line detail the same
    way. Declining costs nothing: the finding's `path`/`concept_id` still
    locates the document. The declining hint MUST NOT echo the raw value
    back, either -- reprinting the string this function just refused to
    trust reintroduces the defect one line lower, which is the same
    reasoning `next_action._tier_unextracted_source` records for its own
    declinations (#276).

    THAT COMMAND IS READ BY ANOTHER MODULE (issue #278). `openkos next`
    prints tier 2's recommendation by scanning this detail's backtick spans
    and taking the one that is `openkos ingest <path>`
    (`cli/next_action.py`, `_command_from_detail`). The backticks are a data
    boundary here, not decoration.

    What that permits and what it forbids, precisely:

    - Rewording the prose around the command is SAFE, including adding
      backticked text before it -- the extractor scans every span and
      matches by verb, never by position.
    - Splitting the command across spans (`` `openkos` `ingest x` ``) or
      dropping its backticks BREAKS tier 2, and breaks it SILENTLY: `next`
      falls through and prints a lower-ranked recommendation instead. No
      exception, no failing assertion in this module.

    `tests/unit/test_lint_command_spans.py` pins the span from this side so
    the break surfaces here rather than only in `test_next.py`.
    """
    findings: list[LintFinding] = []
    for doc in docs:
        if doc.extraction_status != okf.EXTRACTION_STATUS_FAILED:
            continue
        findings.append(
            LintFinding(
                kind="unextracted",
                path=f"{doc.identity}.md",
                detail=(
                    "concept extraction failed during ingest — "
                    f"{_ingest_retry_hint(doc)}"
                ),
            )
        )
    return findings


def _ingest_retry_hint(doc: LintDoc) -> str:
    """The three-outcome re-ingest hint `check_unextracted` documents,
    shared verbatim with `check_unjudged` (#772) so the two retryable-debt
    kinds cannot drift apart on how they spell the remedy.

    Outcome 3 -- a present but UNSPELLABLE `resource` (#285) -- emits NO
    command at all, not even a bare `openkos ingest` span (which would
    collide with the empty-`resource` fallback and make two different
    repairs indistinguishable), and never echoes the refused value back
    (#274's defect one line lower). The finding's `path`/`concept_id`
    still locates the document."""
    if doc.resource and _UNSPELLABLE_IN_SPAN.search(doc.resource):
        return (
            "this source's raw filename cannot be spelled inside a "
            "command; rename the raw file and re-ingest it"
        )
    if doc.resource:
        return f"retry with `openkos ingest {doc.resource}`"
    return "re-run `openkos ingest` on this source's raw file"


_UNJUDGED_NOTICE_CAUSES: Final = {
    "judge-selection-unavailable": "judge unavailable",
    "judge-selection-empty": "judge reply matched no candidate",
}
"""Spelled as literals rather than `okf.EXTRACTION_NOTICE_*` so a typo in
either module fails a test instead of silently agreeing with itself --
the same reasoning `check_unextracted` gives for matching one literal.
The values are the human-readable cause each token records; keeping them
distinct on this surface preserves the same failed/empty split #754
established in `ingest`'s terminal notices."""


def check_unjudged(docs: list[LintDoc]) -> list[LintFinding]:
    """Flag each Source whose derived objects were stored WITHOUT judge
    selection (issue #772).

    Matches ONLY the two judge-degrade `extraction_notice` tokens
    (`_UNJUDGED_NOTICE_CAUSES`); #585's `sole-object-restates-source` --
    an honest disclosure, never debt -- and any unrecognized,
    out-of-vocabulary token are ignored fail-silent, exactly the
    write-side-typed/read-side-fail-silent policy `check_unextracted`
    follows for `extraction_status`.

    This is the read half of #772's fail-open-but-quarantine design: the
    write half (`ingest` stamping the token) is only a quarantine if some
    later surface is guaranteed to look. `lint` and `status` both render
    these findings, so an unjudged extraction can no longer be admitted
    silently and forgotten.

    Same structural no-fifth-walk guard as every sibling: the signature
    takes ONLY `docs`, so this function is incapable of opening a walk.

    The detail reuses `_ingest_retry_hint` -- including #285's declining
    third outcome -- because the remedy is the same re-ingest
    `check_unextracted` names: a re-run whose judge answers replaces the
    unfiltered set with a selected one."""
    findings: list[LintFinding] = []
    for doc in docs:
        cause = _UNJUDGED_NOTICE_CAUSES.get(doc.extraction_notice)
        if cause is None:
            continue
        findings.append(
            LintFinding(
                kind="unjudged",
                path=f"{doc.identity}.md",
                detail=(
                    "derived objects were stored without judge selection "
                    f"during ingest ({cause}) — {_ingest_retry_hint(doc)}"
                ),
            )
        )
    return findings


_UNEVIDENCED_NOTICE: Final = "objects-without-evidence"
"""Spelled as a literal rather than
`okf.EXTRACTION_NOTICE_OBJECTS_WITHOUT_EVIDENCE`, for exactly the reason
`_UNJUDGED_NOTICE_CAUSES` and `check_unextracted` give for theirs: a typo
in either module then fails a test instead of silently agreeing with
itself, which a shared constant cannot do."""


def check_unevidenced(docs: list[LintDoc]) -> list[LintFinding]:
    """Flag each Source that stored a derived object carrying NO line
    quoted from it (issue #801).

    Matches ONLY `objects-without-evidence`. Both judge-degrade tokens,
    #585's `sole-object-restates-source`, and any unrecognized,
    out-of-vocabulary token are ignored fail-silent -- the same
    write-side-typed/read-side-fail-silent policy `check_unextracted`
    follows for `extraction_status`.

    A NEW kind (`"unevidenced"`), deliberately not folded into
    `check_unjudged`'s `_UNJUDGED_NOTICE_CAUSES`. The two answer different
    questions and have different repairs: an unjudged extraction means no
    quality selection ran over the set, and a re-ingest whose judge answers
    fixes it; this means some object the run stored quotes nothing from its
    source, which a judge cannot repair because the judge already kept it.
    Merging distinct signals into one key is a defect in this repo, not a
    tidying (`extraction_notice` is itself a separate key from
    `extraction_status` for the same reason).

    Same structural no-fifth-walk guard as every sibling: the signature
    takes ONLY `docs`, so this function is incapable of opening a walk.

    The detail deliberately does NOT reuse `_ingest_retry_hint`, which is
    the one place this check departs from `check_unjudged`'s shape. That
    hint spells a plain `openkos ingest <resource>`, and on an unchanged
    source that command SKIPS extraction entirely (#773's convergence
    short-circuit) -- this token is excluded from
    `cli/main._extraction_retry_due` on purpose, because it is a disclosure
    rather than retryable debt. Printing a command that provably does
    nothing is worse than printing none, so the detail names the defect,
    points at the source the reader can check it against, and names
    `--re-extract` as the flag that actually forces a redo. The flag is
    named WITHOUT the resource for the reason #274/#285 established: a
    doc-controlled value interpolated into a backtick span is forgeable,
    and the finding's `concept_id` already locates the document.
    """
    findings: list[LintFinding] = []
    for doc in docs:
        if doc.extraction_notice != _UNEVIDENCED_NOTICE:
            continue
        findings.append(
            LintFinding(
                kind="unevidenced",
                path=f"{doc.identity}.md",
                detail=(
                    "a derived object was stored with no line quoted from "
                    "this source — it cannot support a citation; check the "
                    "derived objects against the source, and re-ingest with "
                    "--re-extract to redo extraction"
                ),
            )
        )
    return findings


_STAGING_DROP_NOTICE: Final = "candidates-dropped-in-staging"
"""Spelled as a literal rather than
`okf.EXTRACTION_NOTICE_CANDIDATES_DROPPED`, for exactly the reason
`_UNEVIDENCED_NOTICE` and `_UNJUDGED_NOTICE_CAUSES` give for theirs: a
typo in either module then fails a test instead of silently agreeing with
itself, which a shared constant cannot do."""


def check_staging_dropped(docs: list[LintDoc]) -> list[LintFinding]:
    """Flag each Source that lost at least one extracted candidate while
    staging (issue #843).

    Matches ONLY `candidates-dropped-in-staging`. Both judge-degrade
    tokens, #585's `sole-object-restates-source`, #801's
    `objects-without-evidence`, and any unrecognized, out-of-vocabulary
    token are ignored fail-silent -- the same write-side-typed/
    read-side-fail-silent policy every sibling follows.

    A NEW kind (`"staging-dropped"`), deliberately not folded into any
    sibling. The judge tokens mean no quality selection ran over the
    stored set; #801's means a stored object quotes nothing; this one
    means the bundle stores LESS than extraction produced -- content was
    lost, not degraded -- and nothing about that loss survived the
    terminal until the marker did.

    Same structural no-fifth-walk guard as every sibling: the signature
    takes ONLY `docs`, so this function is incapable of opening a walk.

    The detail follows `check_unevidenced`'s shape, not
    `_ingest_retry_hint`'s, and for its exact reason: the token is
    excluded from `cli/main._extraction_retry_due` on purpose (a staging
    drop is a property of the specific sample, and re-running the same
    prompt over the same bytes is promised to fix nothing), so a plain
    re-ingest of an unchanged source SKIPS extraction entirely (#773) and
    naming it would print a command that provably does nothing. The
    detail names `--re-extract`, the flag that actually forces a redo,
    WITHOUT the resource (#274/#285: a doc-controlled value interpolated
    into a backtick span is forgeable, and the finding's `concept_id`
    already locates the document)."""
    findings: list[LintFinding] = []
    for doc in docs:
        if doc.extraction_notice != _STAGING_DROP_NOTICE:
            continue
        findings.append(
            LintFinding(
                kind="staging-dropped",
                path=f"{doc.identity}.md",
                detail=(
                    "at least one extracted candidate was dropped while "
                    "staging, so the bundle may under-represent this "
                    "source; re-ingest with --re-extract to redo extraction"
                ),
            )
        )
    return findings


def check_below_source_sensitivity(docs: list[LintDoc]) -> list[LintFinding]:
    """Flag descendants a `backfill-sensitivity` sweep would raise, and docs
    its per-Source closure basis cannot reach (#231, PR2, design D2/D3).

    Takes ONLY `docs` -- no `bundle_dir` parameter -- the SAME structural
    no-fifth-walk guard `check_dangling_targets`/`check_unextracted` follow
    (`lint.py:556-560`): a function that never receives a directory is
    incapable of opening a walk. Reuses the SAME closure algorithm and rank
    comparator the sweep uses,
    `bundle.provenance.provenance_closure` plus `okf.combine_sensitivity`
    -- NEVER `bundle.provenance.resolve_source_raises`, which returns
    `okf.DescendantRaise.content`, requiring the full file text plus a
    metadata dict that `LintDoc` does not keep (design D2; `LintDoc` keeps
    `body` only).

    Both finding kinds share one basis: CLOSURE MEMBERSHIP, computed once
    from every `type: Source` doc's `provenance_closure` over a map built
    from `LintDoc.provenance` alone.

    - `"below-source-sensitivity"`: `doc.identity` is in the closure of
      EXACTLY ONE Source root, and `okf.combine_sensitivity(doc.sensitivity,
      source.sensitivity)` differs from `doc.sensitivity` -- the identical
      test the sweep uses to stage a write, so a missing, blank, or
      unrecognized `sensitivity` is ranked fail-closed (ADR-0003) and IS
      flagged even though it does not "strictly rank below".
    - `"multi-source-uncovered"`: `doc.provenance` is non-empty, every cited
      id resolves to a doc in `docs`, `doc.identity` is a member of NO
      single-Source closure, and `doc.sensitivity` sits strictly below the
      high-water-mark of its cited concepts' levels (folded via repeated
      `okf.combine_sensitivity`, never `okf._rank` -- ADR-0003 keeps that
      helper private). Its detail names the descendant, its current level,
      and every cited concept id with that concept's level.

      SINCE ISSUE #697 the sweep DOES repair this document (ADR-0016:
      `resolve_cited_high_water_raises` folds the same high-water mark this
      check computes), so the detail no longer marks it as uncovered. The
      finding kind survives the change because it still answers a different
      question from `below-source-sensitivity` -- which single-Source closure
      a document belongs to is what decides whether `set-sensitivity <source>`
      alone would have reached it -- and because the two remedies differ in
      blast radius: one document versus the whole bundle.

    A doc citing 2+ concepts that all fall inside ONE Source's closure is a
    member of that single closure and is therefore
    `"below-source-sensitivity"`, never `"multi-source-uncovered"` (design
    D3 exclusion -- `query --save`'s two-output rule writes such docs
    routinely). A doc citing any unresolvable id falls into NEITHER
    category, because the sweep cannot reach it either.

    That last case is reported by `check_dangling_provenance` (issue #257),
    not here: `check_dangling_targets` does not close the gap either -- it
    scans `doc.relations` and the body's markdown links only, never
    `doc.provenance`. Such a doc may still sit below its Source, which is
    exactly the consequence the `dangling-provenance` detail names.

    BOTH DETAILS ARE READ BY ANOTHER MODULE (issue #278). `openkos next`
    prints tier 3's recommendation by scanning the `below-source-sensitivity`
    detail's backtick spans for `openkos backfill-sensitivity`
    (`cli/next_action.py`, `_command_from_detail`), so keeping that command
    inside ONE span is a contract, not a style choice -- splitting it or
    dropping its backticks stops tier 3 firing, silently.

    The `multi-source-uncovered` detail names the same command only to RULE
    IT OUT, and `next` is protected from it by filtering on `finding.kind`
    BEFORE extracting anything -- not by the sentence's negation, which no
    regex reads. So the safety of that wording rests entirely on the kind
    filter: this detail may keep naming the command it excludes, but a NEW
    finding kind whose detail names a command it does not endorse would
    need the same explicit gate on the consuming side.
    """
    docs_by_id = {doc.identity: doc for doc in docs}
    provenance_by_id = {
        doc.identity: frozenset(doc.provenance) for doc in docs if doc.provenance
    }
    sources = [doc for doc in docs if doc.type == "Source"]

    # `membership[descendant_id]` collects every Source id whose closure
    # contains `descendant_id` (the root itself excluded from its own
    # closure, matching `resolve_source_raises`'s "a root is never its own
    # descendant" rule -- design D6).
    membership: dict[str, set[str]] = {}
    for source in sources:
        closure = bundle_provenance.provenance_closure(
            provenance_by_id, root_ids={source.identity}
        )
        for member_id in closure:
            if member_id == source.identity:
                continue
            membership.setdefault(member_id, set()).add(source.identity)

    findings: list[LintFinding] = []

    for member_id in sorted(membership):
        source_ids = membership[member_id]
        if len(source_ids) != 1:
            continue  # not a member of exactly one Source's closure
        doc = docs_by_id[member_id]
        (source_id,) = source_ids
        source_level = docs_by_id[source_id].sensitivity
        new_level = okf.combine_sensitivity(doc.sensitivity, source_level)
        if new_level == doc.sensitivity:
            continue  # already covered -- nothing the sweep would stage
        findings.append(
            LintFinding(
                kind="below-source-sensitivity",
                path=f"{doc.identity}.md",
                detail=(
                    f"sensitivity {doc.sensitivity!r} is below Source "
                    f"'{source_id}' ({source_level!r}); `openkos "
                    f"backfill-sensitivity` would raise it to {new_level!r}"
                ),
            )
        )

    for doc in docs:
        if not doc.provenance:
            continue
        source_ids = membership.get(doc.identity, set())
        if len(source_ids) == 1:
            continue  # covered by below-source-sensitivity above

        cited_levels: list[tuple[str, str]] = []
        for cited_id in doc.provenance:
            cited_doc = docs_by_id.get(cited_id)
            if cited_doc is None:
                cited_levels = []
                break
            cited_levels.append((cited_id, cited_doc.sensitivity))
        if not cited_levels:
            continue  # an unresolvable cite falls into neither category

        high_water = cited_levels[0][1]
        for _, level in cited_levels[1:]:
            high_water = okf.combine_sensitivity(high_water, level)
        if okf.combine_sensitivity(doc.sensitivity, high_water) == doc.sensitivity:
            continue  # already at or above the high-water-mark

        cited_detail = ", ".join(
            f"{cited_id!r} ({level!r})" for cited_id, level in cited_levels
        )
        # The level the document should be AT: the high-water-mark across
        # every cite, not the first one listed. `combine_sensitivity` never
        # lowers, so this is also the level `set-sensitivity` will accept
        # without `--allow-downgrade`.
        resolved_level = okf.combine_sensitivity(doc.sensitivity, high_water)
        # ONE predicate decides both the detail's command span and the
        # `remediation` field, so the two can never disagree about whether
        # this identity may be spelled as a command (#693).
        #
        # It has to cover the SPAN, not just the field. `doc.identity` comes
        # from the on-disk path and a backtick is a legal POSIX filename
        # character, so an unguarded interpolation closes the span early and
        # leaves a well-formed `openkos set-sensitivity <some other id>
        # <level>` behind -- issue #274's defect exactly, one field over,
        # and `next` echoes this detail verbatim as its reason line.
        # `_SAFE_COMMAND_ARGUMENT` already excludes a backtick (it admits
        # only `[\w./-]`), so the same check serves both.
        spellable = bool(_SAFE_COMMAND_ARGUMENT.fullmatch(doc.identity))
        remediation = (
            f"openkos set-sensitivity {doc.identity} {resolved_level}"
            if spellable
            else ""
        )
        # Exactly ONE runnable command is ever spelled in a backtick span,
        # and it is the one that works. The sweep is still named -- it is why
        # this finding exists at all -- but as prose, not as a command: a
        # negated command sitting on `next`'s reason line in copy-paste shape
        # is the trap `cli/next_action.py`'s trap-1 filter was built to keep
        # off the screen.
        remedy_clause = (
            f"resolve it with `{remediation}`"
            if spellable
            else (
                f"resolve it by raising the level to {resolved_level!r}, but "
                f"this id cannot be spelled as a command argument -- rename "
                f"it first"
            )
        )
        findings.append(
            LintFinding(
                kind="multi-source-uncovered",
                path=f"{doc.identity}.md",
                detail=(
                    f"sensitivity {doc.sensitivity!r} is below the high-water "
                    f"mark of what it cites (member of no single Source's "
                    f"closure); {remedy_clause}, or repair every such "
                    f"document at once with the backfill-sensitivity sweep; "
                    f"cites: {cited_detail}"
                ),
                remediation=remediation,
            )
        )

    return findings


@dataclass(frozen=True)
class NonNfcEntry:
    """One on-disk entry (file OR directory) under `bundle_dir` whose OWN
    name is not NFC (issue #474 part 2, design D1) -- the richer sibling
    `LintFinding` cannot carry, because `LintFinding.path` is already
    NFC-normalized and so, by construction, cannot locate the raw entry
    on a byte-exact filesystem. `check_non_nfc_names` projects this into
    `LintFinding`s for `lint`; `normalize-names` (`cli/main.py`) consumes
    it directly to build its rename plan."""

    path: Path
    """Raw, byte-exact on-disk `Path` -- what `LintFinding.path`'s
    NFC-normalized spelling cannot carry."""
    raw_name: str
    """`path.name` exactly as the filesystem returned it."""
    nfc_name: str
    """`unicodedata.normalize("NFC", raw_name)`."""
    rel_posix: str
    """NFC bundle-relative POSIX path -- identical to the `LintFinding`
    this entry projects to, and to `okf`'s canonical id spelling (#247)."""
    depth: int
    """`len(path.relative_to(bundle_dir).parts)` -- `normalize-names`'
    `(-depth, rel_posix)` deepest-first apply-order sort key (design D4)."""
    is_dir: bool
    """`Path.is_dir()`-derived -- stat-based, so it FOLLOWS symlinks
    (never lstat); safe because every consumer gates on `is_symlink`
    first, so a symlink's target-derived value is never acted on.
    `False` on `OSError` (read-only-never-fail)."""
    is_symlink: bool
    """`lstat`-derived; `False` on `OSError` (read-only-never-fail)."""


def scan_non_nfc_entries(bundle_dir: Path) -> list[NonNfcEntry]:
    """The SINGLE definition of "offending entry" shared by `openkos
    lint`'s `non-nfc-name` finding and `openkos normalize-names` (issue
    #474 part 2, design D1) -- `check_non_nfc_names` below is a thin
    projection of this scan's result into `LintFinding`s, and
    `normalize-names` (`cli/main.py`) consumes this scan directly for its
    richer raw-`Path`-carrying plan. One walk, one definition: the day
    this scan's notion of "offending" changes, both callers agree by
    construction, never by drifting independently.

    Concept ids are canonically NFC: `okf.concept_id_for` normalizes on
    the way in (#430), so a decomposed on-disk spelling and its NFC id
    can never be byte-equal -- the drift a macOS-created bundle (HFS+
    forced NFD; APFS preserves whatever it is given) syncs onto every
    other platform. `okf.concept_path_for` absorbs that drift TOLERANTLY,
    resolving the NFC id against the decomposed file, and its docstring
    ends by promising that a caller who wants to KNOW should ask `lint`.

    This walk takes `bundle_dir` and pulls `bundle_dir.rglob("*")` one
    entry at a time -- deliberately NOT the `collect_docs` walk, and
    deliberately NOT a violation of design D3's no-fifth-walk guard: that
    guard protects the read+parse walk (every `docs`-consuming check is
    structurally incapable of opening one), while this walk reads NAMES
    ONLY and never opens a single file. It also CANNOT ride on
    `collect_docs`: that walk only surfaces readable, parseable `.md`
    docs, and a decomposed name on a directory, a non-`.md` file, or an
    unreadable doc is exactly what this scan must still see.

    The test is each entry's OWN name -- `path.name !=
    unicodedata.normalize("NFC", path.name)` -- never the full path, so a
    decomposed DIRECTORY produces exactly ONE entry, not one per
    descendant: one rename fixes the whole subtree.

    `is_dir`/`is_symlink` are stat'd ONLY for entries that already failed
    the NFC test, so a clean bundle pays exactly what it paid before this
    scan existed (design D1).

    Each `next()` on the walk is guarded INDIVIDUALLY, so a broken walk
    (a directory deleted mid-scan, a permission wall) degrades to the
    entries collected so far -- read-only-never-fail, matching every
    other guard in this module. The generator is deliberately never fed
    through `sorted(...)`: that would consume the WHOLE walk before the
    first entry exists, so a mid-walk OSError would discard everything
    already seen and render a silently empty, false-clean report (review
    R4-001). Determinism comes from sorting the RESULT by `rel_posix`
    after the walk instead. This scan is READ-ONLY and NON-GATING: it
    never writes and its result never affects any caller's exit code."""
    entries: list[NonNfcEntry] = []
    walk = bundle_dir.rglob("*")
    while True:
        try:
            path = next(walk)
        except StopIteration:
            break
        except OSError:
            break  # a broken walk degrades to the entries collected so far
        nfc_name = unicodedata.normalize("NFC", path.name)
        if path.name == nfc_name:
            continue
        rel = path.relative_to(bundle_dir)
        try:
            is_symlink = path.is_symlink()
        except OSError:
            is_symlink = False
        try:
            is_dir = path.is_dir()
        except OSError:
            is_dir = False
        entries.append(
            NonNfcEntry(
                path=path,
                raw_name=path.name,
                nfc_name=nfc_name,
                rel_posix=unicodedata.normalize("NFC", rel.as_posix()),
                depth=len(rel.parts),
                is_dir=is_dir,
                is_symlink=is_symlink,
            )
        )
    entries.sort(key=lambda entry: entry.rel_posix)
    return entries


def scan_stranded_rename_temps(bundle_dir: Path) -> list[Path]:
    """Names-only walk for entries whose name starts with
    `fsio.RENAME_TEMP_PREFIX` -- `normalize-names`-only helper (issue #474
    part 2, design D3), never called by `lint`. A temp can be stranded
    by a hard kill strictly between `fsio.rename_two_step`'s two
    `os.rename` calls, or by a double fault in its guard or hop-2 branch
    where the best-effort (suppressed) restore also fails (PR #492) --
    every single-fault failure path there restores the original name.
    A double fault in that primitive's post-rename VERIFICATION branch
    leaves no temp for this scan to find (issue #495): hop 2 already
    succeeded, so the entry sits at its final spelling and surfaces
    through `scan_non_nfc_entries` instead, if at all. This
    scan lets the NEXT run report it
    (never touch it: auto-deleting would be data loss, auto-renaming
    would be a guess).

    Deliberately a SECOND names-only walk rather than folded into
    `scan_non_nfc_entries`'s return value: `lint` would then pay for, and
    have to unpack, a signal it never reports (design D3)."""
    stranded: list[Path] = []
    walk = bundle_dir.rglob("*")
    while True:
        try:
            path = next(walk)
        except StopIteration:
            break
        except OSError:
            break  # read-only-never-fail, matching every other guard here
        if path.name.startswith(fsio.RENAME_TEMP_PREFIX):
            stranded.append(path)
    stranded.sort()
    return stranded


def scan_markdown_under_state_dir(bundle_dir: Path) -> list[Path]:
    """Names-only walk for any `*.md` file under `bundle_dir/.state/`
    (`okf.STATE_DIRNAME`) -- the safety net for the EXCLUDE/INCLUDE
    separation (design: "Relocate the merge ledger to `bundle/.state/
    ledger/`", Decision 3): the merge-ledger sidecar store's whole
    portability rationale depends on its files NEVER matching `*.md`, so
    every EXCLUDE walk (`rglob("*.md")`) structurally skips it with zero
    code at each site. A future author who reintroduces a `.md` file under
    `.state/` (by hand, or via a suffix change) would silently defeat that
    guarantee -- this scan enforces it is never true, rather than merely
    documented.

    Deliberately a SEPARATE, NAMES-ONLY walk (mirrors
    `scan_non_nfc_entries`'s own rationale): `collect_docs`/`_iter_docs`
    never descends into `.state/` at all (by the very same `*.md` glob this
    scan exists to police), so there is no shared walk to reuse."""
    state_dir = bundle_dir / okf.STATE_DIRNAME
    if not state_dir.is_dir():
        return []
    return sorted(state_dir.rglob("*.md"))


def check_state_dir_contains_no_markdown(bundle_dir: Path) -> list[LintFinding]:
    """Flag every `*.md` file found under `bundle_dir/.state/` (task 3.6):
    a `.state/` subtree exists ONLY for non-`.md` derived-state sidecars
    (the merge ledger today); any `.md` file there would silently defeat
    the ledger's zero-code EXCLUDE-walk guarantee (see
    `scan_markdown_under_state_dir`). Read-only and NON-GATING, matching
    every other `lint` finding -- `lint` never writes and never fails the
    run; the detail names the concrete risk rather than an abstract rule
    violation."""
    return [
        LintFinding(
            kind="state-dir-markdown",
            path=path.relative_to(bundle_dir).as_posix(),
            detail=(
                f"'{path.relative_to(bundle_dir).as_posix()}' is a `.md` "
                "file under `bundle/.state/` -- every inbound-reference/"
                "EXCLUDE walk in this codebase relies on `.state/` never "
                "matching `*.md`; move or rename this file so it does not"
            ),
        )
        for path in scan_markdown_under_state_dir(bundle_dir)
    ]


def check_non_nfc_names(bundle_dir: Path) -> list[LintFinding]:
    """Flag every on-disk name (file OR directory) under `bundle_dir` that
    is not NFC, with the NFC rename as remediation (issue #474).

    A thin, 1:1 projection of `scan_non_nfc_entries` (design D1): `lint`
    itself never writes (spec: Read-Only and Human-Readable Only), but the
    rename is no longer the human's shell problem: `openkos
    normalize-names` (#474 part 2) performs it, consuming this function's
    own `scan_non_nfc_entries`, so detection and migration can never
    disagree about what an offending entry is.

    `path` is the NFC-normalized bundle-relative POSIX path -- the
    canonical spelling, matching how every other verb spells the object
    (#247) -- and it may name a directory or a non-`.md` file, which is
    why the CLI renders this kind via `finding.path`, never
    `finding.concept_id`. The detail carries BOTH spellings: the raw
    on-disk name through `ascii(...)`, because the NFD/NFC difference is
    invisible in rendered text and the escaped combining mark is the only
    way a human can SEE it, and the NFC rename target as remediation.

    `scan_non_nfc_entries` already sorts by `rel_posix` (identical to this
    projection's `path`), so no second sort is needed here -- the
    `findings.sort(key=...)` this function used to run is now redundant
    and has been dropped. This projection is READ-ONLY and NON-GATING: it
    never writes and its findings never affect any caller's exit code."""
    return [
        LintFinding(
            kind="non-nfc-name",
            path=entry.rel_posix,
            detail=(
                f"on-disk name {entry.raw_name!a} is not NFC -- "
                f"run `openkos normalize-names` to rename it to "
                f"{entry.nfc_name!r} so the spelling on disk matches the "
                f"canonical id (#430)"
            ),
        )
        for entry in scan_non_nfc_entries(bundle_dir)
    ]
