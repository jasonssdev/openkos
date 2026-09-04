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
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

from openkos import config, source_title
from openkos.bundle import index as bundle_index
from openkos.bundle import log as bundle_log
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


def extraction_retry_due(metadata: Mapping[str, object]) -> bool:
    """Whether a byte-identical re-ingest should still re-run extraction
    (#773): only when the previous run left RETRYABLE DEBT on the Source --
    `extraction_status: failed` (#187, the one status `lint` flags) or a
    judge-degrade `extraction_notice` token (#772's quarantine, whose
    `lint` retry hint names exactly this re-ingest as the remedy).

    Every other state -- markers absent, a deliberate-policy
    `extraction_status` (`no-extractable-text`/`blocked-by-sensitivity`/
    `no-concepts-found`), #585's sole-object disclosure, or #801's
    `objects-without-evidence` -- means the previous extraction ran to its
    intended conclusion, so an unchanged source has nothing to retry and
    the re-ingest skips extraction unless `--re-extract` asks for a
    deliberate redo.

    #801's token is excluded on the same grounds as #585's, and the
    exclusion is load-bearing rather than incidental: both are disclosures
    about output the pipeline produced as designed, not failures of a step.
    A plain re-ingest re-runs the SAME prompt over the SAME bytes, which is
    promised to fix neither -- which is exactly why `lint.check_unevidenced`
    names `--re-extract` instead of a bare re-ingest.

    Moved from `cli/main._extraction_retry_due` verbatim (issue #918 Slice
    3) -- the ONE definition `cli/main._reingest_will_skip` (the batch
    cost-gate predictor, out of scope otherwise) and `converged_reingest`
    below both call, so "the shared predicate" stays true by construction."""
    if metadata.get(okf.EXTRACTION_STATUS_KEY) == okf.EXTRACTION_STATUS_FAILED:
        return True
    # #884: MEMBERSHIP over every recorded token, not equality against the
    # whole value. The key can now hold several conditions, and a run whose
    # judge failed AND that lost a candidate in staging is still
    # retry-worthy for the judge half -- an equality test would have read
    # the multi-token value as "no judge token" and silently stopped
    # offering the retry.
    return bool(
        {
            okf.EXTRACTION_NOTICE_JUDGE_UNAVAILABLE,
            okf.EXTRACTION_NOTICE_JUDGE_EMPTY,
        }
        & set(okf.extraction_notices(metadata))
    )


def carried_extraction_notice(
    metadata: Mapping[str, object],
) -> tuple[okf.ExtractionNotice, ...]:
    """The `extraction_notice` a Source's frontmatter CARRIES, narrowed to
    the closed vocabulary (`okf.EXTRACTION_NOTICE_VALUES`) by matching a
    member rather than casting -- so the returned value is one this build
    can actually spell.

    It FAILS CLOSED: an absent key and an unrecognised value are both
    dropped. Frontmatter is hand-editable, and a Source written by a later
    release may carry a token this one does not know; neither is a reason to
    crash a run that is otherwise writing nothing, and neither may be
    counted under a summary term whose wording promises a vocabulary member.
    Leaving it out is the honest answer -- the Source document itself stays
    the record, and `okf` stays the one place the vocabulary is defined.

    The one caller is `converged_reingest`'s convergent path, which needs
    the PRIOR run's token because the summary term counts what a Source
    carries when the run ends, not what the run stamped (#805, item 1).
    Moved from `cli/main._carried_extraction_notice` verbatim (issue #918
    Slice 3)."""
    return okf.extraction_notices(metadata)


@dataclass(frozen=True)
class ConvergedReingest:
    """A non-`None` `converged_reingest` result -- the #773 convergence
    short-circuit fired (design: "`converged_reingest` replaces the #773
    mid-region `return`"). The adapter maps this to the SAME exit path it
    used before this move: echo the verbatim disclosure line and return
    `_SingleIngestOutcome(regenerated=True, extraction_degraded=False,
    extraction_skipped=True, extraction_notice=carried_notices)`."""

    carried_notices: tuple[okf.ExtractionNotice, ...]


def converged_reingest(
    concept_text: str, *, re_extract: bool
) -> ConvergedReingest | None:
    """The #773 convergence gate: `None` means "fall through to the full
    run"; a `ConvergedReingest` means a byte-identical re-ingest of a Source
    whose previous extraction ran to its intended conclusion has nothing to
    redo -- writing NOTHING (not even a regenerated Source) is what makes
    the promised idempotence true.

    Called only when the adapter's `had_prior_source and concept_text is
    not None` (design: Interfaces/Contracts) -- `concept_text` is the
    single `_snapshot_read` observation the adapter already took, so this
    parses no second read.

    Four policy decisions, each falling through to the full run (design:
    "the gate is three policy decisions... plus the `carried_extraction_
    notice` narrowing -- all pure over a Mapping, all belonging to the
    service"):

    1. `re_extract` -- the deliberate redo always runs extraction again.
    2. Unparseable frontmatter proves nothing about the previous
       extraction (`okf.load_frontmatter` raises `ValueError`).
    3. A pre-#552 legacy Source records no `origin_key` -- the full
       regenerate path is what backfills it (the no-verb self-migration),
       so such a Source takes that path ONCE and every later re-ingest of
       it skips like any other.
    4. Retryable debt (`extraction_retry_due`) -- exactly the retry
       `lint`'s unextracted/unjudged hints name.

    Only when all four clear does this return `ConvergedReingest`, carrying
    the PRIOR run's `carried_extraction_notice` -- not `None`, since the
    Source this run leaves untouched may still carry a disclosure (#805,
    item 1: the summary counts what a Source CARRIES when the run ends, not
    what THIS run stamped)."""
    if re_extract:
        return None
    try:
        prior_metadata, _ = okf.load_frontmatter(concept_text)
    except ValueError:
        # An unparseable prior Source proves nothing about the previous
        # extraction -- fall through to the full run, which is the
        # pre-#773 behavior for every re-ingest.
        return None
    if prior_metadata.get(okf.ORIGIN_KEY_KEY) is None:
        return None
    if extraction_retry_due(prior_metadata):
        return None
    return ConvergedReingest(carried_notices=carried_extraction_notice(prior_metadata))


def _read_source_sensitivity(source_display_path: str, text: str) -> object:
    """Raw `sensitivity` from an EXISTING Source concept, unranked --
    `okf.combine_sensitivity` ranks it fail-closed per ADR-0003.

    Takes the already-decoded `text` rather than reading a path itself
    (#318): the adapter snapshots the file exactly once via
    `_snapshot_read` and passes the decoded text down as `concept_text`.
    `source_display_path` names the source (not the Source document itself
    -- `application/ingest.py` never holds a `Path` to the concept file,
    D2) purely for the error message. Ported from `cli/main.
    _read_source_sensitivity` (issue #918 Slice 3), adapted to a string
    identifier in place of the original's `Path`."""
    try:
        metadata, _ = okf.load_frontmatter(text)
    except Exception as exc:
        # `frontmatter.loads` raises `yaml.YAMLError` on malformed YAML,
        # which is neither `OSError` nor `ValueError` -- translate rather
        # than degrade.
        raise ValueError(
            f"refusing to ingest -- the existing Source for "
            f"'{source_display_path}' could not be parsed to resolve the "
            "sensitivity from its snapshot -- the single read that also "
            f"feeds the title parse and the drift baseline: {exc}"
        ) from exc
    return metadata.get("sensitivity")


def _read_source_title(source_display_path: str, text: str) -> object:
    """Raw `title` from an EXISTING Source concept, read so a re-ingest's
    preview can name a title change instead of overwriting it silently.
    Mirrors `_read_source_sensitivity`'s shape exactly, for `title` rather
    than `sensitivity`. Does NOT make `title` sticky -- the caller uses the
    return value only to decide what the preview SAYS, never what gets
    WRITTEN. Ported from `cli/main._read_source_title` (issue #918 Slice
    3)."""
    try:
        metadata, _ = okf.load_frontmatter(text)
    except Exception as exc:
        raise ValueError(
            f"refusing to ingest -- the existing Source for "
            f"'{source_display_path}' could not be parsed to resolve its "
            f"existing title: {exc}"
        ) from exc
    return metadata.get("title")


@dataclass(frozen=True)
class SourceDocumentPlan:
    """The composed Source document for THIS run, plus the on-disk facts
    the adapter's preview and the #773 gate need (design: Interfaces/
    Contracts). `content` is the FRESH build with `extraction_status=None`
    and no `extraction_notice` -- `compose_catalog_update` owns rebuilding
    it a second time, conditionally, once the staging outcome is known."""

    title: str
    description: str
    resolved_sensitivity: str
    on_disk_sensitivity: object | None
    on_disk_title: object | None
    source_sensitivity: str
    """The resolved `sensitivity` read BACK from `content`'s own rendered
    frontmatter -- the value `stage_derived_objects`' `stamp_sensitivity`
    kwarg needs, guaranteed to equal `resolved_sensitivity` but read back
    rather than assumed, exactly as the pre-move adapter code did."""

    content: str

    raw_content: str | None
    origin_key: str | None
    """Carried so `compose_catalog_update` can rebuild `content` a second
    time with the same inputs, conditionally (design's public interface
    lists `content` as the only rendered artifact; these two are retained
    internally rather than re-threaded through a second parameter list --
    see this change's apply-progress deviations)."""


def compose_source_document(
    *,
    raw_content: str | None,
    source_stem: str,
    source_display_path: str,
    resource: str,
    origin_key: str | None,
    concept_text: str | None,
    cfg: config.Config,
    timestamp: str,
) -> SourceDocumentPlan:
    """Compose this run's Source document from local inputs (design:
    Interfaces/Contracts) -- `concept_text is None` is exactly the
    adapter's `had_prior_source` being `False` (a fresh ingest, or a
    post-`forget` regenerate with no prior document to read back).

    Title derivation (issue #248): a binary/undecodable source
    (`raw_content is None`) and a blank/whitespace-only decoded source
    (`not raw_content.strip()`) never call `source_title.
    derive_source_title` at all; any other `None` result (no usable
    candidate in real content) falls back to `source_titles.titleize
    (source_stem)`. The title is then neutralized against markdown link
    delimiters (`bundle_index.sanitize_link_label`) before it feeds the
    frontmatter, the `# ` heading, and the catalog bullets.

    Re-ingest never lowers a Source's sensitivity (issue #229): when
    `concept_text` is given, `resolved_sensitivity` is the high-water mark
    (`okf.combine_sensitivity`) of the on-disk value and `cfg.
    default_sensitivity`; a concept-absent regenerate (`concept_text is
    None`) resolves directly to `cfg.default_sensitivity` -- `None` must
    never reach `combine_sensitivity`, or a `public` workspace would be
    wrongly raised to `private` (`okf._rank(None)` floors at `private`).

    `origin_key` is stamped as `_RawDestination.origin_key` resolved (#865)
    -- the caller's concern, passed through unchanged.

    Renders nothing (spec: "The service module renders nothing") and calls
    no presentation primitive, matching `stage_derived_objects`."""
    derived_title = (
        None
        if raw_content is None or not raw_content.strip()
        else source_title.derive_source_title(raw_content)
    )
    title = (
        derived_title
        if derived_title is not None
        else source_titles.titleize(source_stem)
    )
    title = bundle_index.sanitize_link_label(title)

    if raw_content is None:
        description = (
            f"Raw source imported from '{source_display_path}' as {resource}; "
            "binary/non-text content could not be embedded, not yet "
            "extracted into concepts."
        )
    else:
        description = (
            f"Raw source imported from '{source_display_path}' as {resource}; "
            "full text embedded verbatim below, not yet extracted into "
            "concepts."
        )

    if concept_text is not None:
        on_disk_sensitivity = _read_source_sensitivity(
            source_display_path, concept_text
        )
        resolved_sensitivity = okf.combine_sensitivity(
            on_disk_sensitivity, cfg.default_sensitivity
        )
        on_disk_title = _read_source_title(source_display_path, concept_text)
    else:
        on_disk_sensitivity = None
        resolved_sensitivity = cfg.default_sensitivity
        on_disk_title = None

    content = okf.build_source_concept(
        title=title,
        description=description,
        resource=resource,
        tags=[],
        timestamp=timestamp,
        sensitivity=resolved_sensitivity,
        provenance=[resource],
        raw_content=raw_content,
        extraction_status=None,
        extraction_notice=(),
        origin_key=origin_key,
    )
    source_metadata, _ = okf.load_frontmatter(content)
    source_sensitivity = str(source_metadata["sensitivity"])

    return SourceDocumentPlan(
        title=title,
        description=description,
        resolved_sensitivity=resolved_sensitivity,
        on_disk_sensitivity=on_disk_sensitivity,
        on_disk_title=on_disk_title,
        source_sensitivity=source_sensitivity,
        content=content,
        raw_content=raw_content,
        origin_key=origin_key,
    )


@dataclass(frozen=True)
class CatalogUpdate:
    """`compose_catalog_update`'s typed result (design: Interfaces/
    Contracts) -- the Source document's final bytes for THIS run, plus the
    extended `index.md`/`log.md` texts covering the Source and every staged
    derived object. The adapter's Phase B write loop consumes these
    unchanged."""

    concept_content: str
    new_index_text: str
    new_log_text: str


def compose_catalog_update(
    *,
    source: SourceDocumentPlan,
    staged: StagedDerivedObjects,
    slug: str,
    resource: str,
    index_text: str,
    log_text: str,
    regenerate: bool,
    timestamp: str,
    entry_date: date,
) -> CatalogUpdate:
    """Own the conditional Source re-render and the derived-plans index/log
    loop (design: Interfaces/Contracts).

    Conditional re-render (design: "The ordering conflict"): `source.
    content` was built with `extraction_status=None` and no notice; ONLY
    when staging produced a `skip_reason` or a `notice` is it rebuilt a
    SECOND time, from scratch, with that marker stamped onto FRESH content
    -- never patched onto the already-built bytes, and never read off disk.
    Both markers are rebuilt for THIS run alone, which is what makes them
    self-clearing (#187's anti-merge rule, inherited unchanged by #585): a
    re-ingest whose extraction now finds a second subject rebuilds without
    the notice, so a stale marker can never outlive the condition it
    described. `skip_reason` and `notices` are mutually exclusive by
    construction (zero objects vs exactly one), so both are always passed
    together rather than branched on, matching the pre-move call site.

    Catalog text (design D3): on a regenerate, the Source's existing
    `index.md` bullet is removed BEFORE it is re-inserted ("dedup before
    insert" -- a no-forget re-ingest already has the bullet, and a bare
    insert would duplicate it; a post-forget regenerate has zero matches,
    leaving `index_text` unchanged). Extends the SAME diff, in staging
    order, with one index bullet and one log entry per staged derived
    object, plus the durable disambiguation audit log entry (#131) when
    `plan.disambiguated_from is not None` -- no second read-modify-write
    round trip, matching "one confirm gate, one preview".

    Renders nothing and calls no presentation primitive."""
    concept_content = source.content
    if staged.skip_reason is not None or staged.notices:
        concept_content = okf.build_source_concept(
            title=source.title,
            description=source.description,
            resource=resource,
            tags=[],
            timestamp=timestamp,
            sensitivity=source.resolved_sensitivity,
            provenance=[resource],
            raw_content=source.raw_content,
            extraction_status=staged.skip_reason,
            extraction_notice=staged.notices,
            origin_key=source.origin_key,
        )

    working_index_text = index_text
    if regenerate:
        working_index_text, _ = bundle_index.remove_index_entry(
            working_index_text, f"sources/{slug}"
        )
        log_line = (
            f"**Re-ingest**: Regenerated [{source.title}](/sources/{slug}.md) "
            f"from existing `{resource}` (identical source, raw copy reused)."
        )
    else:
        log_line = (
            f"**Ingest**: Imported [{source.title}](/sources/{slug}.md) from "
            f"`{resource}`."
        )
    new_index_text = bundle_index.insert_source_entry(
        working_index_text,
        title=source.title,
        slug=slug,
        description=source.description,
    )
    new_log_text = bundle_log.insert_log_entry(log_text, entry_date, log_line)

    for plan in staged.plans:
        new_index_text = bundle_index.insert_index_entry(
            new_index_text,
            section=plan.section,
            link_dir=plan.link_dir,
            title=plan.title,
            slug=plan.slug,
            description=plan.description,
        )
        new_log_text = bundle_log.insert_log_entry(
            new_log_text,
            entry_date,
            f"**Ingest**: Extracted [{plan.title}]"
            f"(/{plan.link_dir}/{plan.slug}.md) ({plan.doc_type}) "
            f"from [{source.title}](/sources/{slug}.md).",
        )
        if plan.disambiguated_from is not None:
            new_log_text = bundle_log.insert_log_entry(
                new_log_text,
                entry_date,
                f"**Disambiguation**: [{plan.title}]"
                f"(/{plan.link_dir}/{plan.slug}.md) from source '{slug}' "
                f"collided with '{plan.disambiguated_from}'; wrote "
                f"distinct concept '{plan.slug}'.",
            )

    return CatalogUpdate(
        concept_content=concept_content,
        new_index_text=new_index_text,
        new_log_text=new_log_text,
    )
