"""The ingest bounded-context application service (ADR-0018), issue #918
(design: `openspec/changes/ingest-application-service/design.md`).

Slice 1 seeded this module with `DerivedPlan` and the collision-detection
helpers, moved verbatim from `cli/main.py`. Slice 2 moves
`stage_derived_objects` itself -- de-presented (design: "the service
returns typed disclosure data; the adapter owns every word"): every
`typer.echo` call that used to live inside the function body is replaced by
a field on the returned `StagedDerivedObjects` (or a `StagingDrop` entry),
and the adapter (`cli/main.py::_ingest_single`, via
`_render_staged_derived_objects`) renders the exact same wording, in the
exact same order, from that typed data.

`OllamaError` is never caught here (design: "the backend exception
propagates; the adapter catches `OllamaError`") -- catching it would
require importing `openkos.llm.ollama`, a CONCRETE backend, which
`tests/unit/application/test_layering.py` forbids. It propagates to the
adapter, which supplies `skip_reason="failed"` itself. Progress reporting is
injected (`on_progress`), never owned: no `rich.Console` spinner and no
`observability.phase_callback` call live here; the adapter builds both and
forwards `on_progress` unchanged to whichever extractor `union_judge`
selected.

This is the second module in the `application/` layer, following the
shipped `application/query.py`. Like it, this module imports nothing from
`openkos.cli`, `typer`, or `rich`, and binds no concrete LLM backend --
enforced by `tests/unit/application/test_layering.py`, generalized in this
change to scan every module under this directory rather than a single
hardcoded path.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from openkos import config
from openkos.bundle import index as bundle_index
from openkos.bundle import source_titles
from openkos.extraction.concept import (
    ExtractionReport,
    ProgressHook,
    extract_concept,
    extract_concept_union,
)
from openkos.llm.base import LLMBackend
from openkos.model import okf
from openkos.model.types import TYPE_TO_LINK_DIR, TYPE_TO_SECTION
from openkos.sensitivity import blocks_llm_send


@dataclass(frozen=True)
class DerivedPlan:
    """One validated derived object staged for Phase B write -- one entry
    per item in the list `stage_derived_objects` returns. The list itself,
    not this dataclass, carries the zero-to-N cardinality (design: bounded
    multi-object contract, D4); `[]` means every candidate was declined,
    dropped, or skipped, and `ingest` degrades to Source-only for this
    batch."""

    doc_type: str
    section: str
    link_dir: str
    slug: str
    title: str
    description: str
    path: Path
    content: str
    disambiguated_from: str | None = None
    """The original, colliding slug this plan was disambiguated away from
    -- `None` for the ordinary (no-collision) case. Set only when a
    foreign-source collision redirected this candidate to `<slug>-N`
    (design: Disambiguation loop, #131); Phase B uses it to emit the one
    audit `insert_log_entry` call for a disambiguated write."""

    type_alternative: str | None = None
    """The runner-up type the model also weighed (#401), or `None` when the
    classification was clear. Carried on the plan so the CALLER can
    aggregate one summary line per run (#566) -- the per-candidate stderr
    line fired on ~100% of extracted objects in real sessions and carried
    no signal. The durable record stays in the document's
    `type_alternative` frontmatter key, written by `build_concept` above,
    independent of this field."""

    sensitivity: str = ""
    """This plan's resolved birth-time `sensitivity` (issue #669, design
    D3) -- `config.type_birth_sensitivity`'s return value, already folded
    into `content`'s frontmatter above. Carried on the plan (rather than
    re-parsed from `content`) so the caller can build the run-summary
    advisory's `(type, resolved_level)` pairs the same way it already
    builds `alternative_pairs`."""

    type_floor_raised: bool = False
    """`True` when this object's resolved `sensitivity` is strictly above
    `stamp_sensitivity` because of the per-type offset mapping (issue #669,
    design D3) -- `resolved != base`, the same shape #569 already uses at
    `plan.sensitivity != cfg.default_sensitivity`. `False` on the common
    path (no offset configured for this type, or `base` already at or
    above the floor-plus-offset)."""


def collision_family(link_dir: Path, base_slug: str) -> list[Path]:
    """Return every file in `link_dir` belonging to `base_slug`'s collision
    family -- `<base_slug>.md` itself and every `<base_slug>-N.md` (N a
    positive integer) -- sorted ascending by `N` (the bare base slug sorts
    first). Matched via a REGEX anchored on the full filename stem
    (`^{base}(-\\d+)?$`), NEVER a glob, so an unrelated sibling like
    `<base>-word.md` never joins the family (design: Collision loop
    mechanics; #131).

    Both sides are NFC-normalized before matching (#414). `_slugify` emits
    NFC, but HFS+ (and some SMB mounts) rewrite a filename to NFD on write
    while APFS preserves whatever spelling it is handed, so `glob` can
    legitimately return the NFD stem of a file created under the NFC slug.
    Matching the raw stems would miss it -- while `derived_path.exists()`,
    which is normalization-INSENSITIVE on macOS, still reports True. The
    caller would then read an EMPTY family, misread the slug as belonging to
    a foreign source, and disambiguate to `<slug>-2` on every re-ingest
    until `write_exclusive` raised `FileExistsError`. Unreachable while
    slugs were ASCII (ASCII has no NFD form); reachable now that they carry
    accents.
    """
    if not link_dir.is_dir():
        return []
    base = unicodedata.normalize("NFC", base_slug)
    pattern = re.compile(rf"^{re.escape(base)}(?:-(\d+))?$")
    members: list[tuple[int, Path]] = []
    for path in link_dir.glob("*.md"):
        match = pattern.match(unicodedata.normalize("NFC", path.stem))
        if match is None:
            continue
        suffix_n = int(match.group(1)) if match.group(1) else 0
        members.append((suffix_n, path))
    members.sort(key=lambda item: item[0])
    return [path for _, path in members]


def family_owns_source(family: list[Path], source_slug: str) -> bool:
    """`True` if ANY member of `family` already carries THIS ingest's
    `sources/<source_slug>` provenance key -- the sole idempotency
    guarantee that a re-ingest never spawns a new disambiguated slug,
    including for a `<slug>-N` this source previously won (design:
    Idempotency Predicate; #131). A member whose frontmatter fails to read
    or parse is skipped, never raised -- the scan degrades per member,
    mirroring `okf._iter_docs`'s broad parse-failure tolerance."""
    provenance_key = f"sources/{source_slug}"
    for path in family:
        try:
            text = path.read_text(encoding="utf-8")
            metadata, _ = okf.load_frontmatter(text)
        except (OSError, UnicodeDecodeError):
            continue
        except Exception:  # noqa: S112 -- broad: malformed frontmatter degrades, never crashes
            continue
        provenance = metadata.get("provenance")
        if isinstance(provenance, list) and provenance_key in provenance:
            return True
    return False


def first_free_disambiguated_slug(
    family: list[Path], base_slug: str, reserved: set[str]
) -> str:
    """First free `<base_slug>-N` (N starting at 2) that is neither already
    on disk (a stem present in `family`) nor already claimed by an earlier
    candidate in THIS batch (`reserved`) -- deterministic, ascending scan
    (design: Collision loop mechanics -- batch-local `seen_slugs` guard;
    #131).

    On-disk stems are NFC-normalized before the comparison, for the same
    reason `collision_family` normalizes (#414): an NFD `<base>-2.md` must
    still count as taken, or this would hand back a name that already
    exists."""
    taken = {unicodedata.normalize("NFC", path.stem) for path in family} | reserved
    n = 2
    while f"{base_slug}-{n}" in taken:
        n += 1
    return f"{base_slug}-{n}"


DropKind = Literal[
    "empty-slug",
    "in-batch-collision",
    "already-exists",
    "disambiguated",
    "build-failed",
]
"""The closed vocabulary for `StagingDrop.kind` (design: Interfaces/
Contracts) -- one entry per per-candidate staging decision
`stage_derived_objects`' loop makes that is NOT a plain successful stage.
`"disambiguated"` is not a LOSS (the candidate is staged, at a redirected
slug) but is still a `StagingDrop`, because the adapter must render its own
distinct wording -- the same reason #884's `lost_in_staging` counter does
NOT increment for it (see that field's docstring below)."""


@dataclass(frozen=True)
class StagingDrop:
    """One per-candidate staging decision `stage_derived_objects`' loop
    reports (design: Interfaces/Contracts) -- moved out of the four/five
    `typer.echo` call sites the loop used to carry, one entry per candidate
    in loop order (`StagedDerivedObjects.drops`' own docstring: "the render
    order"). The adapter's `_render_staging_drop` maps `kind` back to the
    exact original wording."""

    kind: DropKind
    slug: str
    """The slug the decision was about -- `""` for `"empty-slug"` (there is
    no slug; that is exactly why the candidate was dropped), the ORIGINAL
    (pre-disambiguation) slug for `"disambiguated"`, else the candidate's
    resolved slug."""

    disambiguated_to: str | None = None
    """The slug this candidate was redirected to -- set only for
    `"disambiguated"`."""

    error: str | None = None
    """`str(exc)` from the `ValueError` `okf.build_concept` raised -- set
    only for `"build-failed"`."""


@dataclass(frozen=True)
class StagedDerivedObjects:
    """One `stage_derived_objects` call's typed result (design: Interfaces/
    Contracts) -- everything the pre-Slice-2 function used to render via
    `typer.echo`, now returned as data. The adapter
    (`cli/main.py::_render_staged_derived_objects`) renders every field, in
    the same order the function used to echo it."""

    plans: tuple[DerivedPlan, ...]
    skip_reason: okf.ExtractionStatus | None
    """Why this batch produced zero derived objects -- `None` on the
    healthy path (`plans` non-empty). See `stage_derived_objects`'s
    docstring for the full four-token vocabulary and the deliberate fifth
    state (`plans == () and skip_reason is None`)."""

    notices: tuple[okf.ExtractionNotice, ...]
    """The `okf.ExtractionNotice` tokens this batch's Source frontmatter
    should carry (issue #585/#843/#884) -- distinct from the STRING notices
    the 14 `_*_notice(report)` helpers render; those stay in `cli/main.py`
    and are called by the adapter directly over `report`."""

    report: ExtractionReport | None
    """`None` on the two pre-extraction degrades (`no-extractable-text`,
    `blocked-by-sensitivity`) -- the extractor was never called, so there is
    no report to carry. Non-`None` on every other path, including
    `no-concepts-found` (extraction ran, produced nothing)."""

    drops: tuple[StagingDrop, ...]
    """Every per-candidate staging decision, in loop order -- the render
    order."""

    lost_in_staging: int
    """Count of candidates that LOST content in staging -- `"empty-slug"`
    and `"build-failed"` only (issue #843/#884). `"in-batch-collision"`,
    `"already-exists"`, and `"disambiguated"` are deliberately excluded: in
    each of those the content is (or ends up) on disk under some slug, so
    nothing extracted was actually lost."""


def stage_derived_objects(
    *,
    raw_content: str | None,
    source_title: str,
    source_slug: str,
    workspace_floor: str,
    stamp_sensitivity: str,
    timestamp: str,
    bundle_dir: Path,
    llm: LLMBackend,
    cfg: config.Config,
    include_confidential: bool = False,
    union_judge: bool = False,
    on_progress: ProgressHook | None = None,
) -> StagedDerivedObjects:
    """Attempt LLM extraction of zero or more distinct derived objects from
    the source's decoded text, and stage each validated candidate for Phase
    B (the adapter owns slug/path derivation... no -- `ingest` owns the
    actual write; this function computes the COMPLETE, already-deduped
    write set, exactly as `_stage_derived_objects` did before this move
    (issue #918 Slice 2, design: Technical Approach / Interfaces).

    De-presented (design: "the service returns typed disclosure data; the
    adapter owns every word"): every `typer.echo` this function's
    predecessor called is now either a `StagedDerivedObjects` field or a
    `StagingDrop` entry. This function calls no presentation primitive, no
    `rich.Console`, and constructs no spinner; `on_progress` is forwarded
    UNCHANGED to whichever extractor `union_judge` selected (design: "the
    spinner stays in the CLI").

    `OllamaError` (from `llm.chat`, inside `extract_concept`/
    `extract_concept_union`) is NEVER caught here -- it propagates to the
    caller. Catching it would require importing a CONCRETE LLM backend
    (`openkos.llm.ollama`), which `test_application_modules_bind_no_concrete_llm_backend`
    forbids; the adapter's own `except OllamaError` supplies
    `skip_reason="failed"` (design: "the backend exception propagates; the
    adapter catches `OllamaError`").

    Returns `skip_reason="no-extractable-text"` -- always a Source-only
    degrade for this batch -- when `raw_content` is `None` or blank (a
    binary/undecodable or empty source has no text to extract from, so the
    LLM is never called, and `report` stays `None`); returns
    `skip_reason="blocked-by-sensitivity"` when the workspace floor blocks
    the LLM send (`report` stays `None`); returns
    `skip_reason="no-concepts-found"` when `extract_concept`/
    `extract_concept_union` itself returns `[]` (`report` is set --
    extraction DID run). `plans == () and skip_reason is None` is also
    possible (every candidate dropped individually below) -- that state
    deliberately carries no `skip_reason` (design: Sequence, "a real,
    deliberate state").

    Each item in a non-empty extractor result is staged independently, in
    reply order, per the pinned Phase A sequence: (1) derive a slug from the
    title -- an empty slug (a title made only of characters `slugify`
    strips) drops just that candidate (`"empty-slug"`); (2) an in-batch
    collision guard -- a slug already claimed by an EARLIER candidate in
    this SAME reply keeps the first and drops the later one
    (`"in-batch-collision"`); (3) `derived_path.exists()` -- a slug already
    on disk for THIS source is a create-only no-op (`"already-exists"`); for
    a FOREIGN source it disambiguates to the first free numeric suffix and
    is STAGED, not dropped (`"disambiguated"`); (4) `okf.build_concept` --
    untrusted LLM fields that slipped past the extractor's own validation
    can still fail `build_concept`'s stricter gate (`"build-failed"`).

    `notices` (issue #585/#843/#884) carries the SAME `okf.ExtractionNotice`
    computation the pre-move function carried in its persisted-marker
    branches -- judge degrade, sole-object-restates, unevidenced-titles, and
    (appended after the staging loop) candidates-dropped-in-staging when
    `lost_in_staging` is non-zero. These are the PERSISTED tokens (a
    Source's `extraction_notice` frontmatter key), distinct from the STRING
    notices the 14 `_*_notice(report)` helpers render to stderr -- those
    stay in `cli/main.py` and read `report` directly.
    """
    if raw_content is None or not raw_content.strip():
        return StagedDerivedObjects(
            plans=(),
            skip_reason="no-extractable-text",
            notices=(),
            report=None,
            drops=(),
            lost_in_staging=0,
        )

    if not include_confidential and blocks_llm_send(workspace_floor):
        return StagedDerivedObjects(
            plans=(),
            skip_reason="blocked-by-sensitivity",
            notices=(),
            report=None,
            drops=(),
            lost_in_staging=0,
        )

    extractor = extract_concept_union if union_judge else extract_concept
    # `OllamaError` propagates unswallowed -- see docstring above.
    outcome = extractor(
        raw_content,
        source_title=source_title,
        llm=llm,
        on_progress=on_progress,
        concurrent=cfg.concurrent_extraction,
    )

    extractions = outcome.objects
    report = outcome.report

    # #772/#884: same precedence and "append, never substitute" rule the
    # pre-move function carried -- see this module's `notices` field
    # docstring above.
    notices: list[okf.ExtractionNotice] = []
    if report.judge_status == "failed":
        notices.append(okf.EXTRACTION_NOTICE_JUDGE_UNAVAILABLE)
    elif report.judge_status == "empty":
        notices.append(okf.EXTRACTION_NOTICE_JUDGE_EMPTY)
    if report.sole_object_restates_source:
        notices.append(okf.EXTRACTION_NOTICE_SOLE_OBJECT_RESTATES)
    if report.unevidenced_titles:
        notices.append(okf.EXTRACTION_NOTICE_OBJECTS_WITHOUT_EVIDENCE)

    if not extractions:
        return StagedDerivedObjects(
            plans=(),
            skip_reason="no-concepts-found",
            notices=tuple(notices),
            report=report,
            drops=(),
            lost_in_staging=0,
        )

    plans: list[DerivedPlan] = []
    drops: list[StagingDrop] = []
    seen_slugs: set[str] = set()
    lost_in_staging = 0
    for extraction in extractions:
        derived_slug = source_titles.slugify(extraction.title)
        if not derived_slug:
            drops.append(StagingDrop(kind="empty-slug", slug=""))
            lost_in_staging += 1
            continue

        if derived_slug in seen_slugs:
            # NOT counted as a staging loss (#884) -- see `lost_in_staging`'s
            # docstring above.
            drops.append(StagingDrop(kind="in-batch-collision", slug=derived_slug))
            continue

        # An LLM-extracted title carrying a markdown link delimiter (`[`/`]`)
        # would forge or break the catalog bullet's first link in
        # `index.md`/`log.md`. Neutralize the delimiters rather than drop the
        # candidate, so a benign bracketed title (e.g. `Array[0]`) is
        # preserved while the injection is defused.
        safe_title = bundle_index.sanitize_link_label(extraction.title)

        link_dir = TYPE_TO_LINK_DIR[extraction.type]
        section = TYPE_TO_SECTION[extraction.type]
        link_dir_path = bundle_dir / link_dir
        derived_path = link_dir_path / f"{derived_slug}.md"
        original_slug: str | None = None
        if derived_path.exists():
            # A slug already on disk. Distinguish WHO owns it (design:
            # Idempotency Predicate, #131): scan the whole `<slug>`/
            # `<slug>-N` collision family for THIS ingest's own provenance
            # key before deciding.
            family = collision_family(link_dir_path, derived_slug)
            if family_owns_source(family, source_slug):
                # Same-source collision -- create-only no-op (design D5).
                drops.append(StagingDrop(kind="already-exists", slug=derived_slug))
                continue
            # Foreign-source collision -- disambiguate rather than drop.
            original_slug = derived_slug
            derived_slug = first_free_disambiguated_slug(
                family, original_slug, seen_slugs
            )
            derived_path = link_dir_path / f"{derived_slug}.md"
            drops.append(
                StagingDrop(
                    kind="disambiguated",
                    slug=original_slug,
                    disambiguated_to=derived_slug,
                )
            )

        # Per-type sensitivity default (issue #669, design D3): the offset
        # applies to the CONFIG FLOOR, never to `stamp_sensitivity` itself.
        resolved_sensitivity = config.type_birth_sensitivity(
            cfg, extraction.type, stamp_sensitivity
        )
        try:
            content = okf.build_concept(
                type=extraction.type,
                title=safe_title,
                description=extraction.description,
                body=extraction.body,
                provenance=[f"sources/{source_slug}"],
                sensitivity=resolved_sensitivity,
                timestamp=timestamp,
                type_alternative=extraction.type_alternative,
            )
        except ValueError as exc:
            drops.append(
                StagingDrop(kind="build-failed", slug=derived_slug, error=str(exc))
            )
            lost_in_staging += 1
            continue

        seen_slugs.add(derived_slug)
        plans.append(
            DerivedPlan(
                doc_type=extraction.type,
                section=section,
                link_dir=link_dir,
                slug=derived_slug,
                title=safe_title,
                description=extraction.description,
                path=derived_path,
                content=content,
                disambiguated_from=original_slug,
                type_alternative=extraction.type_alternative,
                sensitivity=resolved_sensitivity,
                type_floor_raised=(resolved_sensitivity != stamp_sensitivity),
            )
        )

    if lost_in_staging:
        # #884: APPENDED, never substituted -- see `notices`' docstring above.
        notices.append(okf.EXTRACTION_NOTICE_CANDIDATES_DROPPED)

    return StagedDerivedObjects(
        plans=tuple(plans),
        skip_reason=None,
        notices=tuple(notices),
        report=report,
        drops=tuple(drops),
        lost_in_staging=lost_in_staging,
    )
