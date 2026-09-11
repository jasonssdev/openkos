"""The lifecycle bounded-context application service (ADR-0018, issue
#918, design: `openspec/changes/lifecycle-application-service/design.md`).

Composes the orchestration around five already-mutating verbs -- `merge`,
`unmerge`, `forget`, `purge`, and `adjudicate --apply`/`--apply-same` --
into typed Phase A (validate/preview) / confirm / Phase B (write) callables
usable by any adapter without importing `openkos.cli`. Workspace layout,
configuration, and any needed `LLMBackend` arrive as parameters, so this
module binds no concrete backend and performs no interactive I/O (spec:
Non-CLI Callable Lifecycle Composition). It is the third and last artifact
in the `application/` layer, following the shipped `application/query.py`
and `application/ingest.py`.

Slice 1 (S1, issue #918) seeds this module with the merge core, moved
verbatim from `cli/main.py` (design: Technical Approach, "the confirmation
gate as the one new seam"): `StackedBodyReport`, `PreparedMerge`,
`MergeResult`, `prepare_merge`, `merge_core`, `merge_drift_targets`, plus
the shared id-resolution helpers `canonicalize_concept_id`/
`resolve_concept_path` every later slice's Phase A also needs. `merge`'s
own confirmation gate stayed inline in the CLI adapter for this slice;
S3/S4/S5 wired `forget`/`purge`/`adjudicate --apply-same`, and the
follow-on that closed #918's "expressed as data" goal for the two verbs it
names wired `merge` and `unmerge` too -- every `Prepared*`/`*Plan` in this
module now carries a `confirmation`.

Slice 2a (S2a, issue #918) added `unmerge`'s write-only Phase B --
`PreparedUnmerge`, `UnmergeResult`, `unmerge_core` -- relocated verbatim
from `_execute_single_unmerge`'s former inline tail (design C2: that
439-line function is Phase A, preview, confirm gate, drift guard, AND
Phase B in one body, too large for one slice, so only the write-only tail
moved there). `PreparedUnmerge` was deliberately PARTIAL that slice -- it
carried only the write inputs `unmerge_core` needs, not yet a full Phase-A
result.

Slice 2b (S2b, issue #918) completes the split: `prepare_unmerge` and
`unwind_step_preview_lines` relocate verbatim from `_execute_single_
unmerge`'s former Phase-A body and `main.py`'s sibling `--to`-plan preview
helper (design C2/Slice S2b), and `PreparedUnmerge` grows the remaining
fields (`catalog_log_drifted`, `review`, the drift-guard baselines) --
`_execute_single_unmerge` itself is deleted from `cli/main.py`, since
`unmerge`'s command now calls `prepare_unmerge`/`unmerge_core` directly,
the same full `prepare_X`/`X_core` pair `merge` already has (design's
Slice Plan). `unmerge`'s confirm gate stayed inline in the CLI adapter for
this slice, exactly like `merge`'s; both now carry a
`BooleanConfirmation` built by `consent.boolean_confirmation`, so an
adapter outside `openkos.cli` can read what each gate asks and which flag
bypasses it.

Renders nothing, prompts nothing, never calls `sys.stdin.isatty()`, and
never imports `openkos.cli`, `typer`, `rich`, or `openkos.vcs` (the
layering invariant, `tests/unit/application/test_layering.py`) -- every
`vcs_git.*` call and every `_autocommit`/`_reject_drifted_targets`/
`_refresh_derived_after_write` call stays adapter-side (design D2/D3).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Literal

from openkos import config, fsio
from openkos.application.consent import (
    BooleanConfirmation,
    TypedChallengeConfirmation,
    boolean_confirmation,
)
from openkos.bundle import decisions as bundle_decisions
from openkos.bundle import index as bundle_index
from openkos.bundle import ledger as bundle_ledger
from openkos.bundle import links as bundle_links
from openkos.bundle import log as bundle_log
from openkos.bundle import merge as bundle_merge
from openkos.bundle import provenance as bundle_provenance
from openkos.bundle import references as bundle_references
from openkos.bundle import relations as bundle_relations
from openkos.model import okf
from openkos.resolution.adjudication import AdjudicatedCandidate, Verdict
from openkos.resolution.candidates import CandidateGroup

STACKED_SHARE_GUARDRAIL = 0.8
"""Merged-body share at or above which a proposed merge is flagged as a
likely about-X/is-X confusion (issue #559).

The three semantically wrong merges accepted in the 2026-08-11 e2e run
stacked 89-92% unreconciled foreign text -- a document ABOUT the survivor
imported wholesale, not a second description of the same object. A genuine
merge of two comparable descriptions of one subject stacks roughly half the
merged body (two similar-length bodies), so 0.8 sits between the two
populations with margin on both sides: the absorbed side must contribute at
least four times the survivor's content before the flag fires.

The guardrail is advisory where a human confirms each merge (the shared
preview line gains a warning; `adjudicate --apply`, `curate`'s Identity
stage, and `merge` all render it) and HARD where nothing does:
`adjudicate --apply-same`'s typed-count gate consents to a batch, not to any
single pair, so a dominated merge is excluded from the batch there and
routed to the interactive path instead.

Public (no leading underscore) because `cli/main.py`'s
`_refused_stacked_line` (adapter-side, S5) reads it too -- promoted
alongside the merge core (issue #918 Slice 1) rather than duplicated,
mirroring `fsio.snapshot_read`'s own promotion for the same reason."""


def canonicalize_concept_id(concept_id: str) -> str:
    """Canonicalize `concept_id` to its bundle-relative form, applying every
    path-safety check `resolve_concept_path` applies EXCEPT existence:
    rejects an absolute id (a leading `/`), any `..` path segment, and a
    reserved basename (`index`/`log`, `okf.RESERVED_FILENAMES`, matched
    CASE-INSENSITIVELY so a case-insensitive filesystem -- macOS/Windows
    default -- cannot be tricked into targeting the real `index.md`/
    `log.md`) -- but does NOT require (or refuse) that `<canonical_id>.md`
    currently exists on disk.

    Shared by `resolve_concept_path` (which adds the existence check
    needed for a target that must already be there) and `unmerge`'s
    `absorbed_id`, whose file is EXPECTED to be absent -- it was removed by
    the very merge this command reverses -- until Phase B recreates it.
    """
    if concept_id.startswith("/"):
        raise ValueError(f"'{concept_id}' must be a relative concept-id, not absolute")
    posix_id = PurePosixPath(concept_id.removesuffix(".md"))
    if ".." in posix_id.parts:
        raise ValueError(f"'{concept_id}' must not contain '..' segments")
    canonical_id = "/".join(posix_id.parts)
    if not canonical_id:
        raise ValueError(f"'{concept_id}' is not a valid concept-id")
    reserved = {name.lower() for name in okf.RESERVED_FILENAMES}
    if f"{posix_id.name}.md".lower() in reserved:
        raise ValueError(f"'{concept_id}' is a reserved filename")
    return canonical_id


def resolve_concept_path(bundle_dir: Path, concept_id: str) -> tuple[Path, str]:
    """Resolve `concept_id` to `(concept_file, canonical_id)` under
    `bundle_dir`, or raise `ValueError` (`forget`'s Phase A path-safety gate,
    mirroring `ingest`'s basename-derived containment).

    The `concept_id` is canonicalized ONCE, via `canonicalize_concept_id` --
    a redundant `.md` suffix is stripped and `PurePosixPath` collapses `.`
    and repeated-slash segments -- and that single `canonical_id` is used
    for BOTH the filesystem path and the caller's `index.md` match, so a
    leading `./` (or a `.md` suffix) can never delete a concept file while
    leaving its catalog bullet dangling.

    On top of `canonicalize_concept_id`'s path-safety checks (all
    security-relevant and MUST run before any filesystem read tied to
    `concept_id`, threat matrix: path-traversal deletion), this also
    refuses (`ValueError`) if the resolved `<canonical_id>.md` file does
    not exist -- a nonexistent concept-id is a clear error, never a silent
    no-op (spec: Nonexistent Concept Refusal).
    """
    canonical_id = canonicalize_concept_id(concept_id)
    # `okf.concept_path_for`, not `bundle_dir / f"{canonical_id}.md"` (#430):
    # ids derived from a walked path are NFC-normalized, while the name on disk
    # may be decomposed -- a bundle authored on HFS+ and cloned onto a
    # byte-exact filesystem carries NFD filenames openkos never wrote. The
    # path-safety canonicalization above still runs FIRST and is untouched;
    # this only decides which SPELLING of an already-safe id is on disk, and
    # the `is_file` refusal below is still the one place absence is decided.
    concept_path = okf.concept_path_for(canonical_id, bundle_dir)
    # Symlink boundary (#926), BEFORE the existence check: `is_file()` resolves
    # links, so without this a concept-id naming a path through a linked
    # segment reads as an ordinary present concept. It reproduced as real data
    # loss -- with `bundle/area` linked outside, `forget area/secret` unlinked
    # the EXTERNAL file, exited 0 and reported success; only git noticed, and
    # only as a warning ("pathspec ... is beyond a symbolic link").
    #
    # The boundary is reported ahead of absence deliberately: a path that both
    # escapes and does not exist is an escape first, and "does not exist" would
    # send the operator looking for the wrong problem.
    boundary_reason = config.symlink_boundary_reason(concept_path, bundle_dir)
    if boundary_reason is not None:
        raise ValueError(boundary_reason)
    if not concept_path.is_file():
        raise ValueError(f"concept '{concept_id}' does not exist")
    return concept_path, canonical_id


def _apply_link_rewrite_idempotently(
    text: str, *, file: str, rewrites: list[okf.LinkRewrite]
) -> str:
    """Apply `file`'s recorded inbound-link rewrites to `text`, but treat a
    file that ALREADY shows every rewrite's `new_link` at its recorded
    `offset` as a clean no-op -- returns `text` unchanged instead of
    raising. This is the idempotency guard `merge`'s retry story needs: a
    prior partial Phase-B attempt may have already migrated some OTHER
    file before failing on a later one, and re-running `merge` must not
    error out on a file that is already correctly rewritten.

    Delegates to `bundle_links.apply_link_rewrites` (the SAME bounded,
    offset-exact primitive U3 defined) for the normal not-yet-rewritten
    case, so the bounded-rewrite guarantee is never weakened -- this
    wrapper only adds the already-applied short-circuit."""
    file_rewrites = [rw for rw in rewrites if rw.file == file]
    if file_rewrites and all(
        text[rw.offset : rw.offset + len(rw.new_link)] == rw.new_link
        for rw in file_rewrites
    ):
        return text
    return bundle_links.apply_link_rewrites(text, file=file, rewrites=rewrites)


@dataclass(frozen=True)
class StackedBodyReport:
    """Body-stacking signal for one merge (issue #409, report half):
    `okf.build_merged_document` unconditionally appends the absorbed body
    under a `## Merged content (<absorbed-id>)` heading without comparing
    it against the survivor's body -- this report says the merge DID that,
    since nothing else does.

    A bare "bodies were stacked" boolean would fire on essentially every
    merge (an absorbed body is normally non-empty) and add pure noise, so
    the signal instead carries magnitude: `absorbed_chars` is how much
    unreconciled content the absorbed side contributed, and `share` is
    what fraction of the resulting merged body that now is -- a stacked
    sentence and a stacked essay are materially different things to flag
    for a human. `PreparedMerge.stacked_body` is `None`, printed as
    nothing, when the absorbed body carries no reconcilable content
    (empty or whitespace-only) -- matching the same "print nothing on the
    empty case" discipline `dropped_self_loops` / `deduped_collisions`
    already follow, rather than reporting a report about nothing.

    This does NOT detect disagreement between the two bodies -- that is
    the intra-document contradiction-detection half of #409, a separate,
    larger change. This is purely "the merge stacked N chars of
    unreconciled content"."""

    absorbed_chars: int
    merged_chars: int

    @property
    def share(self) -> float:
        """Fraction of the merged body's chars contributed by the absorbed
        side, unreconciled. `merged_chars` is never zero when this report
        exists (a non-empty absorbed body was appended to the merged
        body), so this never divides by zero."""
        return self.absorbed_chars / self.merged_chars

    @property
    def exceeds_guardrail(self) -> bool:
        """`True` when `share` is at or above `STACKED_SHARE_GUARDRAIL` --
        the deterministic about-X/is-X signal issue #559 promotes from a
        printed number to a guardrail."""
        return self.share >= STACKED_SHARE_GUARDRAIL


@dataclass(frozen=True)
class PreparedMerge:
    """Pure Phase-A result of `prepare_merge`: everything `merge`'s preview,
    confirm gate, and `merge_core` need, built in memory without writing
    anything (design: merge-core Extraction, Slice 2b-i). `review` carries
    `cfg.review`, consumed only by the command's confirm gate -- `prepare_merge`
    itself never prompts.

    The `*_bytes` fields are the drift guard's baselines (issue #334): the
    raw bytes each write/delete target held at the SAME `fsio.snapshot_read`
    observation whose decoded text fed the plan (#318), which the command
    hands to `_reject_drifted_targets` after its confirm gate.
    `touched_bytes` is scoped to `touched_files` -- the rest of the
    whole-bundle scan feeds rewrite detection only and is never written, so
    it never becomes a guard target."""

    survivor_canonical: str
    absorbed_canonical: str
    plan: bundle_merge.MergePlan
    new_index_text: str
    new_log_text: str
    other_files: dict[str, str]
    link_rewrites: list[okf.LinkRewrite]
    relation_rewrites: list[okf.RelationRewrite]
    provenance_rewrites: list[okf.ProvenanceRewrite]
    rewritten_files: list[str]
    relation_rewritten_files: list[str]
    provenance_rewritten_files: list[str]
    touched_files: list[str]
    removed: int
    dropped_self_loops: list[okf.Relation]
    deduped_collisions: list[okf.Relation]
    stacked_body: StackedBodyReport | None
    sensitivity_before: str
    sensitivity_after: str
    review: bool
    now: datetime
    index_bytes: bytes
    log_bytes: bytes
    survivor_bytes: bytes
    absorbed_bytes: bytes
    touched_bytes: dict[str, bytes]
    confirmation: BooleanConfirmation
    """The `--auto`-bypassable gate `merge`'s adapter drives (#918). Staged
    here rather than spelled in `cli/main.py` so a non-CLI caller can learn
    what the gate asks and which flag bypasses it -- the "confirmation
    contracts expressed as data" #918 names `merge` for by name. The
    adapter still decides WHETHER to ask (`review`, `--auto`, TTY); this
    only says what is asked."""


@dataclass(frozen=True)
class MergeResult:
    """Pure Phase-B result of `merge_core`: what got written, for the
    command's success echo and `_autocommit` path list. `merge_core` itself
    performs NO VCS side effect (design decision: `_autocommit` stays in the
    command). `ledger_sidecar_path` (durable-derived-state slice 1a) is the
    workspace-relative `bundle/.state/ledger/**` path callers MUST add to
    their own `_autocommit` path list, or the ledger silently never enters
    git (threat matrix, design's "portability rationale")."""

    survivor_canonical: str
    absorbed_canonical: str
    touched_files: list[str]
    committed_paths: list[str]
    ledger_sidecar_path: str


def prepare_merge(
    bundle_dir: Path,
    index_path: Path,
    log_path: Path,
    survivor_path: Path,
    absorbed_path: Path,
    survivor_canonical: str,
    absorbed_canonical: str,
    root: Path,
    *,
    now: datetime,
) -> PreparedMerge:
    """Phase A (pure, no writes): read config + the four texts, scan for
    inbound link/relation rewrites, plan the merge, and recompute the
    preview data -- extracted verbatim from `merge`'s former inline body
    (`main.py:2453-2519`, design: merge-core Extraction, Slice 2b-i).
    Non-interactive; raises `OSError`/`ValueError` on bad input. Writes
    nothing to disk.

    Every plan-feeding read goes through `fsio.snapshot_read`, capturing the
    raw bytes BESIDE the decoded text -- one observation per target, never
    a second batched read (issue #318) -- so the returned `PreparedMerge`
    can carry the drift guard's baselines for the command to check after
    its confirm gate (issue #334)."""
    cfg = config.read_config(root)
    # One `fsio.snapshot_read` observation per target (issues #313, #318,
    # #334): each path is read exactly once, at the moment its decoded text
    # feeds the plan, and the guard's bytes come from that same read --
    # there is no second read for an edit to slip between.
    survivor_bytes, survivor_text = fsio.snapshot_read(survivor_path)
    absorbed_bytes, absorbed_text = fsio.snapshot_read(absorbed_path)
    index_bytes, index_text = fsio.snapshot_read(index_path)
    log_bytes, log_text = fsio.snapshot_read(log_path)

    # `other_bytes` shadows the whole-bundle snapshot for the guard. Which
    # of these files the run will WRITE is not known until the three rewrite
    # scans below resolve `touched_files`, so the bytes come out of the same
    # `fsio.snapshot_read` observation as the text rather than re-read per
    # touched file afterwards -- a second read would leave every file the
    # #318 window. Only the touched entries reach the guard mapping; the
    # rest feed rewrite DETECTION only and are never written, so they are
    # not guard targets (mirrors `unmerge`'s scoping).
    other_files: dict[str, str] = {}
    other_bytes: dict[str, bytes] = {}
    for path in sorted(bundle_dir.rglob("*.md")):
        if path.name in okf.RESERVED_FILENAMES:
            continue
        if path in (survivor_path, absorbed_path):
            continue
        rel = path.relative_to(bundle_dir).as_posix()
        other_bytes[rel], other_files[rel] = fsio.snapshot_read(path)

    link_rewrites = bundle_links.find_inbound_link_rewrites(
        other_files,
        absorbed_id=absorbed_canonical,
        survivor_id=survivor_canonical,
    )
    # Same `other_files` whole-bundle snapshot, captured ONCE above
    # BEFORE any write -- both scans see identical pre-merge bytes
    # (design D3).
    relation_rewrites = bundle_relations.find_inbound_relation_rewrites(
        other_files,
        absorbed_id=absorbed_canonical,
        survivor_id=survivor_canonical,
    )
    # Same `other_files` whole-bundle snapshot, captured ONCE above BEFORE
    # any write -- all three scans see identical pre-merge bytes (design:
    # rewrite-provenance-on-merge, "no additional bundle walk"). NOT gated
    # on the absorbed concept's `type` (spec: "A merge absorbing a
    # NON-Source concept also retargets third-party provenance").
    provenance_rewrites = bundle_provenance.find_inbound_provenance_rewrites(
        other_files,
        absorbed_id=absorbed_canonical,
        survivor_id=survivor_canonical,
    )

    # Durable-derived-state slice 1a: the survivor's existing ledger entries
    # now live in a sidecar (`bundle/ledger.py`), never the survivor's own
    # frontmatter -- `plan_merge` no longer decodes them from `survivor_text`.
    existing_entries = bundle_ledger.read_entries(survivor_canonical, bundle_dir)

    plan = bundle_merge.plan_merge(
        survivor_id=survivor_canonical,
        absorbed_id=absorbed_canonical,
        survivor_text=survivor_text,
        absorbed_text=absorbed_text,
        index_text=index_text,
        merged_at=now.isoformat(),
        existing_entries=existing_entries,
        link_rewrites=link_rewrites,
        relation_rewrites=relation_rewrites,
        provenance_rewrites=provenance_rewrites,
    )

    # The OUTBOUND merge_relations report (dropped self-loops, deduped
    # collisions) for the preview below: recomputed here from the SAME
    # survivor/absorbed metadata `plan_merge` -> `build_merged_document`
    # already used internally, since neither is exposed on `MergePlan`
    # (design: "preview report comes from merge_relations return").
    # Pure and deterministic -- calling it a second time is cheap and
    # never diverges from what `plan.merged_survivor` actually carries.
    survivor_metadata, _ = okf.load_frontmatter(survivor_text)
    absorbed_metadata, absorbed_body = okf.load_frontmatter(absorbed_text)
    _, dropped_self_loops, deduped_collisions = okf.merge_relations(
        okf.decode_relations(survivor_metadata),
        okf.decode_relations(absorbed_metadata),
        survivor_id=survivor_canonical,
        absorbed_id=absorbed_canonical,
    )

    # Body-stacking report (issue #409, report half): `build_merged_document`
    # stays pure and returns bytes only (design decision -- see
    # `StackedBodyReport`'s docstring), so this recomputes the signal from
    # `plan.merged_survivor` -- the SAME bytes `merge_core` will write --
    # the same way the outbound relations report above is recomputed rather
    # than threaded through `MergePlan`. `None` when there is nothing to
    # report: an empty/whitespace-only absorbed body contributes no
    # unreconciled content, so the "print nothing on empty" discipline
    # applies here exactly as it does to `dropped_self_loops` /
    # `deduped_collisions`.
    stripped_absorbed_body = absorbed_body.strip()
    stacked_body: StackedBodyReport | None = None
    if stripped_absorbed_body:
        _, merged_body = okf.load_frontmatter(plan.merged_survivor)
        stacked_body = StackedBodyReport(
            absorbed_chars=len(stripped_absorbed_body),
            merged_chars=len(merged_body),
        )

    new_index_text, removed = bundle_index.remove_index_entry(
        index_text, absorbed_canonical
    )
    new_log_text = bundle_log.insert_log_entry(
        log_text,
        now.astimezone().date(),
        bundle_merge.merge_log_entry(
            survivor_id=survivor_canonical, absorbed_id=absorbed_canonical
        ),
    )

    rewritten_files = sorted({rewrite.file for rewrite in link_rewrites})
    relation_rewritten_files = sorted({rewrite.file for rewrite in relation_rewrites})
    provenance_rewritten_files = sorted(
        {rewrite.file for rewrite in provenance_rewrites}
    )
    touched_files = sorted(
        set(rewritten_files)
        | set(relation_rewritten_files)
        | set(provenance_rewritten_files)
    )
    touched_bytes = {rel: other_bytes[rel] for rel in touched_files}
    sensitivity_before = plan.ledger_entry.sensitivity_before or "(none)"
    sensitivity_after = plan.ledger_entry.sensitivity_after

    return PreparedMerge(
        confirmation=boolean_confirmation("merge"),
        survivor_canonical=survivor_canonical,
        absorbed_canonical=absorbed_canonical,
        plan=plan,
        new_index_text=new_index_text,
        new_log_text=new_log_text,
        other_files=other_files,
        link_rewrites=link_rewrites,
        relation_rewrites=relation_rewrites,
        provenance_rewrites=provenance_rewrites,
        rewritten_files=rewritten_files,
        relation_rewritten_files=relation_rewritten_files,
        provenance_rewritten_files=provenance_rewritten_files,
        touched_files=touched_files,
        removed=removed,
        dropped_self_loops=dropped_self_loops,
        deduped_collisions=deduped_collisions,
        stacked_body=stacked_body,
        sensitivity_before=sensitivity_before,
        sensitivity_after=sensitivity_after,
        review=cfg.review,
        now=now,
        index_bytes=index_bytes,
        log_bytes=log_bytes,
        survivor_bytes=survivor_bytes,
        absorbed_bytes=absorbed_bytes,
        touched_bytes=touched_bytes,
    )


def merge_core(
    bundle_dir: Path,
    index_path: Path,
    log_path: Path,
    prepared: PreparedMerge,
) -> MergeResult:
    """Phase B (after confirm): ordered writes -- `index.md` then `log.md`,
    every touched file's rewrite, then the ledger sidecar's two-phase write
    around the merged survivor -- S1 (`bundle_ledger.write_pending`), V (the
    merged survivor, unchanged call site), S2
    (`bundle_ledger.commit_pending`) -- and finally removes the absorbed
    file (D) (durable-derived-state slice 1a, design Decision 1; extracted
    verbatim from `merge`'s former inline body, `main.py:2559-2596`, design:
    merge-core Extraction, Slice 2b-i). Non-interactive; raises
    `OSError`/`ValueError`. Performs NO VCS side effect -- `_autocommit`
    stays the command's responsibility."""
    fsio.write_atomic(index_path, prepared.new_index_text)
    fsio.write_atomic(log_path, prepared.new_log_text)

    # All inbound-link rewrites AND inbound-relation retargets are
    # computed BEFORE any of them (or the survivor/ledger) is written: a
    # compute-time failure on any one file thus leaves every other file
    # untouched, so a re-run's fresh Phase-A rescan sees every still-
    # absorbed-linked/related file exactly as it was and rewrites it
    # from scratch -- no file is left silently half-migrated by this
    # step. A file present in BOTH `rewritten_files` and
    # `relation_rewritten_files` gets both transforms applied to the
    # SAME in-memory text -- safe, since they touch disjoint regions
    # (body link vs. frontmatter `relations:`, design D5).
    survivor_canonical = prepared.survivor_canonical
    absorbed_canonical = prepared.absorbed_canonical
    rewritten_texts = {
        rel: bundle_provenance.apply_provenance_rewrites(
            bundle_relations.apply_relation_rewrites(
                _apply_link_rewrite_idempotently(
                    prepared.other_files[rel],
                    file=rel,
                    rewrites=prepared.link_rewrites,
                ),
                file=rel,
                survivor_id=survivor_canonical,
                absorbed_id=absorbed_canonical,
                rewrites=prepared.relation_rewrites,
            ),
            file=rel,
            survivor_id=survivor_canonical,
            absorbed_id=absorbed_canonical,
            rewrites=prepared.provenance_rewrites,
        )
        for rel in prepared.touched_files
    }
    for rel in prepared.touched_files:
        fsio.write_atomic(bundle_dir / rel, rewritten_texts[rel])

    # The merged survivor is committed only once every rewrite above has
    # succeeded -- see `merge`'s docstring for why that ordering is what
    # makes a mid-rewrite failure cleanly retryable. The ledger sidecar's
    # two-phase write wraps that write (design Decision 1): S1 binds
    # `expected_survivor_sha256` to the EXACT bytes V is about to write,
    # so `recover` can tell "V landed, only S2 (the commit rename) was
    # torn" from "V never landed" purely from on-disk state.
    survivor_path = bundle_dir / f"{survivor_canonical}.md"
    absorbed_path = bundle_dir / f"{absorbed_canonical}.md"
    expected_survivor_sha256 = bundle_ledger.survivor_sha256(
        prepared.plan.merged_survivor
    )
    bundle_ledger.write_pending(
        survivor_canonical,
        bundle_dir,
        survivor_id=survivor_canonical,
        entries=prepared.plan.ledger_entries,
        expected_survivor_sha256=expected_survivor_sha256,
    )  # S1
    fsio.write_atomic(survivor_path, prepared.plan.merged_survivor)  # V
    bundle_ledger.commit_pending(survivor_canonical, bundle_dir)  # S2
    fsio.remove_file(absorbed_path)  # D

    sidecar_rel = (
        bundle_ledger.ledger_path_for(survivor_canonical, bundle_dir)
        .relative_to(bundle_dir)
        .as_posix()
    )

    ledger_sidecar_path = f"bundle/{sidecar_rel}"

    return MergeResult(
        survivor_canonical=survivor_canonical,
        absorbed_canonical=absorbed_canonical,
        touched_files=prepared.touched_files,
        committed_paths=[
            "index.md",
            "log.md",
            *(f"bundle/{rel}" for rel in prepared.touched_files),
            f"bundle/{survivor_canonical}.md",
            f"bundle/{absorbed_canonical}.md",
            ledger_sidecar_path,
        ],
        ledger_sidecar_path=ledger_sidecar_path,
    )


@dataclass(frozen=True)
class PreparedUnmerge:
    """Full Phase-A result of `prepare_unmerge` (design C2/Slice S2b,
    completing the split S2a left partial): everything `unmerge`'s preview,
    confirm gate, and post-confirm drift guard need, built in memory
    without writing anything, plus the write inputs `unmerge_core` needs
    (S2a's original fields).

    `survivor_path` is carried explicitly, not derived from
    `survivor_canonical` inside `unmerge_core`, for the same NFC/NFD
    reason `resolve_concept_path` reads `okf.concept_path_for` rather than
    building `bundle_dir / f"{canonical_id}.md"` itself (#430) -- the
    caller already resolved it once and `unmerge_core` must write the same
    path, not a re-derived one that could disagree on a filesystem whose
    on-disk spelling differs from the canonical id's NFC form.

    `catalog_log_drifted` is the warn-and-continue notice `unmerge`'s own
    preview prints (#758): `True` when `index.md`/`log.md` no longer match
    what THIS merge deterministically left there (some OTHER command
    touched the catalog since) -- never a refusal, only a printed warning,
    since a V5 (delta) entry has nothing to compare against and this is
    unconditionally `False` for one.

    `review` carries `cfg.review`, consumed only by the command's confirm
    gate -- mirrors `PreparedMerge.review` -- `prepare_unmerge` itself
    never prompts. `index_bytes`/`log_bytes`/`survivor_bytes`/
    `rewrite_bytes` are the drift guard's baselines (issues #306, #313,
    #318): the raw bytes each write/overwrite target held at the SAME
    `fsio.snapshot_read` observation whose decoded text fed this plan.
    Unlike `merge`'s single combined `touched_bytes` mapping, the three
    rewrite-kind partitions (link/relation/provenance) share one
    `rewrite_bytes` dict keyed by relative path -- the guard only needs the
    union of all three, never partitioned by kind.

    `confirmation` is the plain `--auto`-bypassable gate `merge` also
    uses -- not verb-data-conditional the way `forget`'s scope-dependent
    prompt or `purge`'s typed phrase is, which is why both come from the
    shared `consent.boolean_confirmation` helper rather than being built
    field by field here. The `--to` chain's own gate consents to the whole
    unwind sequence before any step's `prepare_unmerge` runs, so the
    adapter calls that same helper directly for it; the two cannot drift."""

    plan: bundle_merge.UnmergePlan
    new_log_text: str
    link_reversed_texts: dict[str, str]
    relation_reversed_texts: dict[str, str]
    provenance_restored_texts: dict[str, str]
    rewritten_files: list[str]
    relation_rewrite_files: list[str]
    provenance_rewrite_files: list[str]
    survivor_path: Path
    survivor_canonical: str
    absorbed_canonical: str
    catalog_log_drifted: bool
    review: bool
    index_bytes: bytes
    log_bytes: bytes
    survivor_bytes: bytes
    rewrite_bytes: dict[str, bytes]
    confirmation: BooleanConfirmation
    """`unmerge`'s gate, same shape and same reason as `PreparedMerge`'s
    (#918). Both unmerge forms -- classic and `--to` -- drive this one
    request."""


@dataclass(frozen=True)
class UnmergeResult:
    """Pure Phase-B result of `unmerge_core`: what got written, for the
    command's success echo and `_autocommit` path list (mirrors
    `MergeResult`). `unmerge_core` performs NO VCS side effect --
    `_autocommit` stays the command's responsibility."""

    survivor_canonical: str
    absorbed_canonical: str
    committed_paths: list[str]


def _reverse_link_rewrite_idempotently(
    text: str, *, file: str, rewrites: list[okf.LinkRewrite]
) -> str:
    """Reverse `file`'s recorded inbound-link rewrites in `text`, but treat
    a file that ALREADY shows every rewrite's `old_link` at its recorded
    `offset` as a clean no-op -- returns `text` unchanged instead of
    raising. This is the reverse analog of `_apply_link_rewrite_idempotently`
    above, closing the same half-completed-write retry trap for `unmerge`'s
    Phase B: each rewritten file is written atomically in one call covering
    ALL of that file's recorded rewrites at once, so on a retry a file is
    either fully reversed already (this short-circuit) or not reversed at
    all (delegates to the real primitive below, unchanged).

    Delegates to `bundle_links.reverse_link_rewrites` (the SAME bounded,
    offset-exact primitive U3 defined) for the normal not-yet-reversed
    case, so the fail-closed drift contract is never weakened: a file that
    matches NEITHER the fully-reversed nor the not-yet-reversed state still
    raises `ValueError` via that primitive (spec: Unmerge Achieves
    Round-Trip Parity's idempotence/safety contract)."""
    file_rewrites = [rw for rw in rewrites if rw.file == file]
    if file_rewrites and all(
        text[rw.offset : rw.offset + len(rw.old_link)] == rw.old_link
        for rw in file_rewrites
    ):
        return text
    return bundle_links.reverse_link_rewrites(text, file=file, rewrites=rewrites)


def _expected_post_merge_index_and_log(
    entry: okf.MergeLedgerEntry, *, survivor_id: str, absorbed_id: str
) -> tuple[str, str] | None:
    """Reconstruct what `index.md`/`log.md` looked like immediately AFTER
    the merge `entry` records, by replaying the SAME deterministic
    transforms `merge` itself applied to `entry.index_before`/
    `entry.log_before` -- `bundle_index.remove_index_entry` and the exact
    `**Merge**` log line, dated from `entry.merged_at`.

    This lets `unmerge`'s Phase A tell the difference between "index.md/
    log.md look exactly like the merge left them" and "something ELSE
    (another `ingest`/`forget`/unrelated `merge`) touched them since" --
    `unmerge` unconditionally overwrites both with the PRE-merge snapshot
    regardless, but the caller uses this to decide whether to surface a
    warning about that discard (principle #3: reviewable, not silent).

    Returns `None` for a V5 entry (#758), which has no snapshots to
    reconstruct from and needs none: its reversal is surgical, so
    intervening catalog/log work is PRESERVED rather than discarded and
    there is no discard left to warn about. Callers must treat `None` as
    "nothing to compare, nothing to warn" -- not as "no drift"."""
    if entry.schema == okf.MERGE_LEDGER_SCHEMA_V5:
        return None
    expected_index, _ = bundle_index.remove_index_entry(entry.index_before, absorbed_id)
    merge_date = datetime.fromisoformat(entry.merged_at).astimezone().date()
    expected_log = bundle_log.insert_log_entry(
        entry.log_before,
        merge_date,
        bundle_merge.merge_log_entry(survivor_id=survivor_id, absorbed_id=absorbed_id),
    )
    return expected_index, expected_log


def prepare_unmerge(
    root: Path,
    layout: config.WorkspaceLayout,
    survivor_path: Path,
    survivor_canonical: str,
    absorbed_canonical: str,
    *,
    now: datetime,
    cfg: config.Config,
) -> PreparedUnmerge:
    """Phase A (pure, no writes): read the survivor's ledger and the
    current catalog/log, plan the reversal (`bundle_merge.plan_unmerge`,
    U2), reverse every recorded inbound-link/relation/provenance rewrite in
    memory, and compute the preview data -- relocated verbatim from
    `_execute_single_unmerge`'s former inline body (`design C2, Slice
    S2b`, completing the Phase A/B split S2a left partial). Non-
    interactive; raises `OSError`/`ValueError` on bad input, a restore
    collision, or a rewrite-file drift detected by
    `bundle_relations.reverse_relation_rewrites`/`bundle_provenance.
    reverse_provenance_rewrites`'s own fail-closed checks. Writes nothing
    to disk.

    `root` is accepted, not read internally -- mirrors `prepare_forget`'s
    own `root` parameter, kept for interface symmetry across the module's
    `prepare_X` callables even where a given verb's Phase A does not need
    it (the command's own single `config.read_config` call, needed
    regardless for `_autocommit`, is what supplies `cfg`).

    Every plan-feeding read goes through `fsio.snapshot_read`, capturing
    the raw bytes BESIDE the decoded text -- one observation per target,
    never a second read (issues #306, #313, #318) -- so the returned
    `PreparedUnmerge` can carry the drift guard's baselines for the
    command to check after its confirm gate (issue #334)."""
    index_path = layout.bundle_dir / "index.md"
    log_path = layout.bundle_dir / "log.md"

    # One `fsio.snapshot_read` observation (issues #306, #313, #318): the
    # raw bytes are the drift guard's baseline for the survivor.
    # Durable-derived-state slice 1a: `plan_unmerge` no longer needs the
    # DECODED text at all -- the ledger entries live in a sidecar
    # (`bundle/ledger.py`), never the survivor's own frontmatter, and
    # `restored_survivor` comes straight from the tail entry's
    # `survivor_before`, not from parsing this file.
    survivor_bytes, _survivor_text = fsio.snapshot_read(survivor_path)
    existing_entries = bundle_ledger.read_entries(survivor_canonical, layout.bundle_dir)
    # Read BEFORE planning (#758): a V5 entry records the merge's catalog
    # delta, so the reversal is computed against these current texts
    # rather than replayed from a snapshot. Still ONE observation each,
    # feeding both the plan and the drift guard's baseline -- the
    # invariant #318 closed is unchanged, only its position moved.
    index_bytes, current_index_text = fsio.snapshot_read(index_path)
    log_bytes, current_log_text = fsio.snapshot_read(log_path)
    plan = bundle_merge.plan_unmerge(
        survivor_id=survivor_canonical,
        absorbed_id=absorbed_canonical,
        entries=existing_entries,
        current_index_text=current_index_text,
        current_log_text=current_log_text,
    )

    absorbed_path = layout.bundle_dir / f"{absorbed_canonical}.md"
    if absorbed_path.exists():
        raise ValueError(
            f"cannot restore 'bundle/{absorbed_canonical}.md' -- a file "
            "already exists at that path"
        )

    expected_catalog_and_log = _expected_post_merge_index_and_log(
        plan.entry,
        survivor_id=survivor_canonical,
        absorbed_id=absorbed_canonical,
    )
    # `None` is a V5 (delta) entry: nothing is discarded, so nothing is
    # warned about (#758). Only the snapshot shapes can silently drop
    # intervening catalog/log work, and only they warn.
    catalog_log_drifted = expected_catalog_and_log is not None and (
        current_index_text != expected_catalog_and_log[0]
        or current_log_text != expected_catalog_and_log[1]
    )

    # Precedence, generalized to three rewrite kinds (provenance >
    # relations > links): a file present in `provenance_rewrites` is
    # reversed EXCLUSIVELY via its provenance whole-file snapshot below --
    # excluded from BOTH the relation and link partitions. D5's original
    # two-way rule still holds for the remaining files: a file present in
    # BOTH `link_rewrites` and `relation_rewrites` (and NOT in
    # `provenance_rewrites`) is reversed EXCLUSIVELY via its
    # `relation_rewrites` whole-file snapshot -- excluded here so
    # `reverse_link_rewrites` is never attempted on it.
    provenance_rewrite_files = sorted(
        {rewrite.file for rewrite in plan.provenance_rewrites}
    )
    relation_rewrite_files = sorted(
        {rewrite.file for rewrite in plan.relation_rewrites}
        - set(provenance_rewrite_files)
    )
    rewritten_files = sorted(
        {rewrite.file for rewrite in plan.link_rewrites}
        - set(provenance_rewrite_files)
        - set(relation_rewrite_files)
    )
    # Accumulated across all three partitions below, each file's bytes
    # coming out of the same `fsio.snapshot_read` observation as the text
    # its reversal is computed from (issues #306, #313, #318).
    rewrite_bytes: dict[str, bytes] = {}
    provenance_texts: dict[str, str] = {}
    for rel in provenance_rewrite_files:
        rewrite_bytes[rel], provenance_texts[rel] = fsio.snapshot_read(
            layout.bundle_dir / rel
        )
    provenance_reversed_texts = {
        rel: bundle_provenance.reverse_provenance_rewrites(
            provenance_texts[rel],
            file=rel,
            survivor_id=survivor_canonical,
            absorbed_id=absorbed_canonical,
            rewrites=plan.provenance_rewrites,
            link_rewrites=plan.link_rewrites,
            relation_rewrites=plan.relation_rewrites,
        )
        for rel in provenance_rewrite_files
    }
    other_texts: dict[str, str] = {}
    for rel in rewritten_files:
        rewrite_bytes[rel], other_texts[rel] = fsio.snapshot_read(
            layout.bundle_dir / rel
        )
    reversed_texts = {
        rel: _reverse_link_rewrite_idempotently(
            other_texts[rel], file=rel, rewrites=plan.link_rewrites
        )
        for rel in rewritten_files
    }
    # Whole-file absolute restore, never offset math (design D1/D3/D4) --
    # but DRIFT-AWARE and FAIL-CLOSED, symmetric with the link path above:
    # each file's CURRENT on-disk text is read and compared against what
    # this merge deterministically wrote there. A mismatch (a legitimate
    # edit landed on that file after the merge) raises `ValueError` here,
    # refusing the whole unmerge before any write, rather than clobbering
    # the edit with the stale snapshot.
    relation_texts: dict[str, str] = {}
    for rel in relation_rewrite_files:
        rewrite_bytes[rel], relation_texts[rel] = fsio.snapshot_read(
            layout.bundle_dir / rel
        )
    relation_reversed_texts = {
        rel: bundle_relations.reverse_relation_rewrites(
            relation_texts[rel],
            file=rel,
            survivor_id=survivor_canonical,
            absorbed_id=absorbed_canonical,
            rewrites=plan.relation_rewrites,
            link_rewrites=plan.link_rewrites,
        )
        for rel in relation_rewrite_files
    }

    new_log_text = bundle_log.insert_log_entry(
        plan.restored_log,
        now.astimezone().date(),
        f"**Unmerge**: Restored [{absorbed_canonical}](/{absorbed_canonical}.md) "
        f"from [{survivor_canonical}](/{survivor_canonical}.md).",
    )

    return PreparedUnmerge(
        confirmation=boolean_confirmation("unmerge"),
        plan=plan,
        new_log_text=new_log_text,
        link_reversed_texts=reversed_texts,
        relation_reversed_texts=relation_reversed_texts,
        provenance_restored_texts=provenance_reversed_texts,
        rewritten_files=rewritten_files,
        relation_rewrite_files=relation_rewrite_files,
        provenance_rewrite_files=provenance_rewrite_files,
        survivor_path=survivor_path,
        survivor_canonical=survivor_canonical,
        absorbed_canonical=absorbed_canonical,
        catalog_log_drifted=catalog_log_drifted,
        review=cfg.review,
        index_bytes=index_bytes,
        log_bytes=log_bytes,
        survivor_bytes=survivor_bytes,
        rewrite_bytes=rewrite_bytes,
    )


def unwind_step_preview_lines(
    entry: okf.MergeLedgerEntry, survivor_canonical: str
) -> list[str]:
    """One `--to` plan step's preview block body (issue #562): every file
    that step will touch, derived from the ledger entry ALONE (no disk
    reads) -- the same three-way partition `prepare_unmerge`'s own
    pre-gate preview uses (provenance > relations > links, design D5
    generalized) and the same `  ~ `/`  + ` line style, so the whole-plan
    preview and the per-step execution preview name the same files the
    same way. The DEFINITIVE per-step preview is still re-printed by each
    step's own Phase A recompute at execution time (relocated verbatim
    from `main.py`'s sibling helper, design C2/Slice S2b)."""
    provenance_files = sorted({rewrite.file for rewrite in entry.provenance_rewrites})
    relation_files = sorted(
        {rewrite.file for rewrite in entry.relation_rewrites} - set(provenance_files)
    )
    link_files = sorted(
        {rewrite.file for rewrite in entry.link_rewrites}
        - set(provenance_files)
        - set(relation_files)
    )
    return [
        *(f"  ~ bundle/{rel} (reverse inbound link rewrite)" for rel in link_files),
        *(
            f"  ~ bundle/{rel} (restore pre-merge relations snapshot)"
            for rel in relation_files
        ),
        *(
            f"  ~ bundle/{rel} (restore pre-merge provenance snapshot)"
            for rel in provenance_files
        ),
        # #758: same two shapes as the single-step preview -- a V5 entry
        # reverses only this merge's own catalog/log edit.
        *(
            [
                "  ~ index.md (restore this merge's catalog entry)",
                "  ~ log.md (remove this merge's entry, append unmerge)",
            ]
            if entry.schema == okf.MERGE_LEDGER_SCHEMA_V5
            else [
                "  ~ index.md (restore pre-merge contents)",
                "  ~ log.md (restore pre-merge contents, append unmerge entry)",
            ]
        ),
        f"  ~ bundle/{survivor_canonical}.md (restore pre-merge contents)",
        f"  + bundle/{entry.absorbed_id}.md (restore)",
    ]


def unmerge_core(
    layout: config.WorkspaceLayout,
    prepared: PreparedUnmerge,
) -> UnmergeResult:
    """Phase B (after confirm): write-only body relocated verbatim from
    `_execute_single_unmerge`'s former inline tail (issue #918 Slice S2a;
    design C2, which found `_execute_single_unmerge` a 439-line function
    spanning Phase A, preview, confirm gate, drift guard, AND Phase B --
    only this write-only tail moves this slice, everything before it stays
    adapter-side). Non-interactive; raises `OSError`/`ValueError` on a
    write failure, always caught by the caller's own try/except and never
    let out as a raw traceback.

    Writes, in this order: `index.md` then `log.md` restored to their
    exact pre-merge bytes; every reversed inbound-link file; every
    restored relation snapshot; every restored provenance snapshot; then
    the recreated absorbed file (create-only, `fsio.write_exclusive`,
    #323) BEFORE the survivor is restored -- the survivor's `merged_from`
    ledger tail entry is the only record of the absorbed snapshot, kept
    intact on disk until the file it describes has actually landed, so a
    failure between these two writes never loses either copy; then
    `log.md` a SECOND time with the `**Unmerge**` audit line appended on
    top of the just-restored contents; and finally the ledger sidecar's
    tail entry is popped LAST of all, since every restore above is
    idempotent to re-write on a retry and popping the tail only once they
    have all landed makes a partial failure here safely re-runnable."""
    index_path = layout.bundle_dir / "index.md"
    log_path = layout.bundle_dir / "log.md"
    absorbed_path = layout.bundle_dir / f"{prepared.absorbed_canonical}.md"
    plan = prepared.plan

    fsio.write_atomic(index_path, plan.restored_index)
    fsio.write_atomic(log_path, plan.restored_log)

    for rel in prepared.rewritten_files:
        fsio.write_atomic(layout.bundle_dir / rel, prepared.link_reversed_texts[rel])
    for rel in prepared.relation_rewrite_files:
        fsio.write_atomic(
            layout.bundle_dir / rel, prepared.relation_reversed_texts[rel]
        )
    for rel in prepared.provenance_rewrite_files:
        fsio.write_atomic(
            layout.bundle_dir / rel, prepared.provenance_restored_texts[rel]
        )

    fsio.write_exclusive(absorbed_path, plan.restored_absorbed)
    fsio.write_atomic(prepared.survivor_path, plan.restored_survivor)

    fsio.write_atomic(log_path, prepared.new_log_text)

    bundle_ledger.write_entries(
        prepared.survivor_canonical,
        layout.bundle_dir,
        survivor_id=prepared.survivor_canonical,
        entries=plan.remaining_entries,
    )

    ledger_sidecar_rel = (
        bundle_ledger.ledger_path_for(prepared.survivor_canonical, layout.bundle_dir)
        .relative_to(layout.bundle_dir)
        .as_posix()
    )

    return UnmergeResult(
        survivor_canonical=prepared.survivor_canonical,
        absorbed_canonical=prepared.absorbed_canonical,
        committed_paths=[
            "bundle/index.md",
            "bundle/log.md",
            *(f"bundle/{rel}" for rel in prepared.rewritten_files),
            *(f"bundle/{rel}" for rel in prepared.relation_rewrite_files),
            *(f"bundle/{rel}" for rel in prepared.provenance_rewrite_files),
            f"bundle/{prepared.absorbed_canonical}.md",
            f"bundle/{prepared.survivor_canonical}.md",
            f"bundle/{ledger_sidecar_rel}",
        ],
    )


def merge_drift_targets(
    layout: config.WorkspaceLayout, prepared: PreparedMerge
) -> dict[Path, bytes]:
    """Build the drift-guard baseline mapping (issue #334) a prepared merge
    needs for `_reject_drifted_targets` -- extracted from `merge`'s former
    inline dict literal at its own call site (design D6, issue #266) so
    `curate`'s Identity stage can call `_reject_drifted_targets` with the
    EXACT same guard mapping `merge` uses, rather than reconstructing it
    and risking the two drifting apart. `merge` itself now calls this too,
    so there is exactly one place this mapping is built.

    The absorbed file's baseline is included alongside every OVERWRITE
    target: it is the one path `merge_core`/`curate`'s Identity stage
    UNLINKS, not overwrites -- the caller is responsible for passing it in
    `deletes=` to `_reject_drifted_targets` so the refusal message reports
    it as a delete target, not a write target (#329)."""
    index_path = layout.bundle_dir / "index.md"
    log_path = layout.bundle_dir / "log.md"
    survivor_path = layout.bundle_dir / f"{prepared.survivor_canonical}.md"
    absorbed_path = layout.bundle_dir / f"{prepared.absorbed_canonical}.md"
    return {
        index_path: prepared.index_bytes,
        log_path: prepared.log_bytes,
        **{
            layout.bundle_dir / rel: data
            for rel, data in prepared.touched_bytes.items()
        },
        survivor_path: prepared.survivor_bytes,
        absorbed_path: prepared.absorbed_bytes,
    }


# ---------------------------------------------------------------------------
# S3 (issue #918) -- `forget`, de-presented from `cli/main.py`'s inline body
# (design: Interfaces/Contracts "S3 -- forget"). Gate 1 (the surviving-
# reference hard refusal, bypassed only by `--force`, never by a
# confirmation) stays adapter-side by construction: `ForgetPlan` carries
# `surviving_refs`/`unverifiable_refs` as plain counts, never wrapped in a
# `ConfirmationRequest`, so it cannot be satisfied by answering anything
# (design D2, `test_lifecycle_seams.py`'s D2/R3 guard). Gate 2, the ordinary
# confirm gate, is the one seam this slice wires to
# `application.consent.BooleanConfirmation`.
# ---------------------------------------------------------------------------

ReferenceKind = Literal["link", "relation", "unverifiable"]
"""Mirrors `bundle_references.InboundReference.kind` -- restated here
rather than imported because `ForgetPlan.references` is a DISCLOSURE
shape (aggregated, service-owned), not the raw per-occurrence scan
result `bundle_references` returns."""


@dataclass(frozen=True)
class ReferenceDisclosure:
    """One AGGREGATED preview line's worth of inbound-reference data
    (issue #567): `forget`'s former inline preview built one dict entry
    per `(member, referrer, kind, relation type)` combination, keyed so a
    referrer linking a target 24 times renders as ONE line with a count,
    not 24 identical lines -- that aggregation is service-owned (design:
    Interfaces/Contracts), and `ForgetPlan.references`' tuple order is the
    exact order `forget`'s adapter renders, first-seen-first (Python
    `dict` insertion order, preserved through `.items()`).

    `member` is the purge-set member this disclosure was found FOR --
    field 0, matching `resurrection_pairs`' own `(member, target)`
    convention (`main.py`'s own comment: keep both tuple/record shapes
    member-first so a future edit can never silently swap fields). `count`
    is 1 for a single occurrence and >1 only when the SAME referrer
    references the SAME member via the SAME kind/relation-type more than
    once."""

    member: str
    referrer_id: str
    kind: ReferenceKind
    relation_type: str | None
    count: int


@dataclass(frozen=True)
class ForgetPlan:
    """Pure Phase-A result of `prepare_forget`: everything `forget`'s
    preview, both gates, and `forget_core` need, built in memory without
    writing or deleting anything (design: Interfaces/Contracts "S3 --
    forget").

    `surviving_refs`/`unverifiable_refs` are Gate 1's own inputs -- a hard
    refusal independent of any human answer, bypassed only by `--force` --
    and are deliberately plain `int`s, never threaded through
    `confirmation` (design D2): `ConfirmationRequest` has no field named
    either, so Gate 1 cannot be represented as "a request that was
    granted" by construction (`test_lifecycle_seams.py`).

    `index_bytes`/`log_bytes`/`concept_bytes`/`other_bytes` are the drift
    guard's baselines (issues #306, #313, #318): the raw bytes each
    read target held at the SAME `fsio.snapshot_read` observation whose
    decoded text fed this plan. `_require_member_baseline` -- the
    defensive fail-closed lookup over `other_bytes` -- stays adapter-side
    (it calls `typer.echo`/raises `typer.Exit` on its own defensive
    branch), so the command builds `_reject_drifted_targets`' mapping
    itself from these raw bytes rather than this module returning an
    already-built `dict[Path, bytes]` that would require importing that
    helper here."""

    purge_ids: list[str]
    total_removed: int
    new_index_text: str
    new_log_text: str
    references: tuple[ReferenceDisclosure, ...]
    resurrection_pairs: tuple[tuple[str, str], ...]
    surviving_refs: int
    unverifiable_refs: int
    confirmation: BooleanConfirmation
    index_bytes: bytes
    log_bytes: bytes
    concept_bytes: bytes
    other_bytes: dict[str, bytes]


class PartialForgetWrite(OSError):
    """`forget_core`'s write failed, carrying the EXACT number of purge-set
    members already unlinked when it did.

    The count has to travel with the exception because the adapter's K-of-N
    recovery message ("removed K of N concept(s) before failing") is the
    only thing telling an operator how much of the cascade landed, and it
    cannot be re-derived afterwards. Probing the filesystem in the handler
    was tried and is wrong twice over: `Path.exists()` is NOT total -- it
    re-raises `EACCES` (see `cli.main._purge_store_is_gone`, which guards
    exactly this) -- so a probe inside an `except OSError` arm can raise a
    SECOND error out of the handler and replace the operator's diagnosis
    with a traceback, and the failure that opened the handler is usually a
    permission problem, which is precisely when the probe is most likely to
    fail too. It is also inexact: a member that was already missing before
    Phase B began reads as "unlinked".

    Subclasses `OSError` so the adapter's existing `except (OSError,
    ValueError)` arm catches it unchanged, and `str()` reproduces the
    cause's text verbatim so the error line stays byte-identical."""

    def __init__(self, cause: BaseException, unlinked_count: int) -> None:
        super().__init__(str(cause))
        self.unlinked_count = unlinked_count


@dataclass(frozen=True)
class ForgetResult:
    """Pure Phase-B result of `forget_core`: the purge set actually
    written/unlinked, for the command's success echo, `_autocommit` path
    list, and the ledger/decision/findings sweeps that follow it (all
    three stay adapter-side -- see `forget_core`'s own docstring for why).
    `forget_core` performs NO VCS side effect, mirroring `MergeResult`/
    `UnmergeResult`."""

    purge_ids: list[str]


def prepare_forget(
    root: Path,
    layout: config.WorkspaceLayout,
    concept_id: str,
    *,
    scope: Literal["self", "source"],
    now: datetime,
    cfg: config.Config,
) -> ForgetPlan:
    """Phase A (pure, no writes): read the root's own text and one
    whole-bundle snapshot, resolve the purge set (`--scope self` collapses
    to `{concept_id}`; `--scope source` expands it via
    `bundle_provenance.find_provenance_descendants`), collect resurrection
    disclosures and inbound references, and rewrite `index.md`/`log.md` in
    memory -- extracted verbatim from `forget`'s former inline body
    (`cli/main.py`'s `forget` command, design: Interfaces/Contracts "S3 --
    forget"). Non-interactive; raises `OSError`/`ValueError` on bad input.
    Writes nothing to disk.

    `concept_id` arrives ALREADY path-safety-checked and resolved to its
    canonical form -- `forget`'s adapter runs `resolve_concept_path` on the
    user's raw argument BEFORE calling this (threat matrix: path-traversal
    deletion; spec: "Path safety runs before descendant resolution"), so
    this recomputes the same concept file path from the validated id via
    `okf.concept_path_for` rather than re-deriving it from unchecked input.

    Every plan-feeding read goes through `fsio.snapshot_read`, capturing
    the raw bytes BESIDE the decoded text -- one observation per target,
    never a second read (issues #306, #313, #318) -- so the returned
    `ForgetPlan` can carry the drift guard's baselines for the command to
    check after its confirm gate.

    `cfg` is accepted, not read internally, so the command's own single
    `config.read_config` call (needed regardless for its post-write
    `_refresh_derived_after_write` call) is the only one -- a second
    independent read here would risk observing a config edited between the
    two calls."""
    index_path = layout.bundle_dir / "index.md"
    log_path = layout.bundle_dir / "log.md"
    concept_path = okf.concept_path_for(concept_id, layout.bundle_dir)

    # One `fsio.snapshot_read` observation per target (issues #306, #313,
    # #318): each path is read exactly once, at the moment its decoded
    # text feeds the plan, and the guard's bytes come from that same read.
    index_bytes, index_text = fsio.snapshot_read(index_path)
    log_bytes, log_text = fsio.snapshot_read(log_path)
    concept_bytes, concept_text = fsio.snapshot_read(concept_path)

    # One whole-bundle snapshot, read ONCE, mirroring `merge`'s
    # `other_files` construction: every other `*.md` file, reserved
    # filenames and the ROOT's own file excluded. This single snapshot
    # feeds descendant resolution, inbound detection, resurrection, and
    # per-member titles/tombstones -- no extra bundle scan, for either
    # scope.
    #
    # `other_bytes` shadows it for the guard -- and ONLY on `--scope
    # source` (#326): on the default `self` scope the guard's member
    # comprehension is empty by construction (`purge_ids` is statically
    # `[concept_id]`), so retaining the whole bundle's raw bytes there
    # would double Phase A's peak memory for nothing.
    other_files: dict[str, str] = {}
    other_bytes: dict[str, bytes] = {}
    for path in sorted(layout.bundle_dir.rglob("*.md")):
        if path.name in okf.RESERVED_FILENAMES:
            continue
        if path == concept_path:
            continue
        rel = path.relative_to(layout.bundle_dir).as_posix()
        raw, other_files[rel] = fsio.snapshot_read(path)
        if scope == "source":
            other_bytes[rel] = raw

    # Unified Phase-A data path (design decision 6): `--scope self`
    # collapses to a single-member purge set, reproducing S2a byte-for-
    # byte; `--scope source` expands it via the pure orphan-closure
    # helper.
    purge_ids: list[str] = (
        bundle_provenance.find_provenance_descendants(
            other_files, root_ids={concept_id}
        )
        if scope == "source"
        else [concept_id]
    )
    purge_ids_set = set(purge_ids)

    # Per-member text + parsed frontmatter. Every non-root member id in
    # `purge_ids` came out of `find_provenance_descendants`, itself
    # derived only from real `other_files` keys (disk-discovered, never
    # user input) -- so this dict lookup can never escape `bundle_dir`.
    member_texts: dict[str, str] = {concept_id: concept_text}
    for member in purge_ids:
        if member != concept_id:
            member_texts[member] = other_files[f"{member}.md"]
    member_metadata: dict[str, dict[str, object]] = {
        member: okf.load_frontmatter(text)[0] for member, text in member_texts.items()
    }

    # Outbound `supersedes` disclosure, per PURGE-SET MEMBER: a target
    # OUTSIDE the purge set re-enters retrieval once the whole set is
    # gone. Tuple convention: the purge-set MEMBER is ALWAYS field 0,
    # matching `all_refs` below.
    resurrection_pairs = sorted(
        {
            (member, relation.target)
            for member in purge_ids
            for relation in okf.decode_relations(member_metadata[member])
            if relation.type == "supersedes" and relation.target not in purge_ids_set
        },
        key=lambda pair: (pair[1], pair[0]),
    )

    # Set-difference inbound-reference detection (design decision 2):
    # `find_inbound_references` is called once PER purge-set member over
    # the SAME whole-bundle snapshot; any referrer whose id is ITSELF a
    # purge-set member is dropped. `unverifiable` referrers are deduped by
    # `referrer_id` across members.
    all_refs: list[tuple[str, bundle_references.InboundReference]] = []
    seen_unverifiable: set[str] = set()
    for member in purge_ids:
        for ref in bundle_references.find_inbound_references(
            other_files, target_id=member
        ):
            if ref.referrer_id in purge_ids_set:
                continue
            if ref.kind == "unverifiable":
                if ref.referrer_id in seen_unverifiable:
                    continue
                seen_unverifiable.add(ref.referrer_id)
            all_refs.append((member, ref))
    verified_refs = [ref for _, ref in all_refs if ref.kind != "unverifiable"]
    unverifiable_refs = [ref for _, ref in all_refs if ref.kind == "unverifiable"]

    # #567: aggregate per (member, referrer, kind, relation type) -- a
    # referrer linking the target 24 times becomes ONE `ReferenceDisclosure`
    # with a count, not 24 identical entries. Insertion order preserves the
    # first-seen order the per-reference loop discovered in, and a count of
    # 1 keeps the exact singular wording the adapter's preview always had.
    aggregated_refs: dict[tuple[str, str, ReferenceKind, str | None], int] = {}
    for member, ref in all_refs:
        key = (
            member,
            ref.referrer_id,
            ref.kind,
            ref.relation_type if ref.kind == "relation" else None,
        )
        aggregated_refs[key] = aggregated_refs.get(key, 0) + 1
    references = tuple(
        ReferenceDisclosure(
            member=member,
            referrer_id=referrer_id,
            kind=kind,
            relation_type=relation_type,
            count=count,
        )
        for (member, referrer_id, kind, relation_type), count in aggregated_refs.items()
    )

    # `index.md` bullet removal for every purge-set member (a pure text
    # transform -- call order has no effect on the final result).
    new_index_text = index_text
    total_removed = 0
    for member in purge_ids:
        new_index_text, removed_i = bundle_index.remove_index_entry(
            new_index_text, member
        )
        total_removed += removed_i

    # `log.md` tombstones, one per member, all sharing `tombstone_time`.
    # Built in REVERSED sorted order so the LAST prepend (the smallest id)
    # ends up at the very top -- a deterministic ascending top-to-bottom
    # order matching the sorted delete order in `forget_core`.
    tombstone_time = now.strftime("%H:%M:%SZ")
    new_log_text = log_text
    for member in reversed(purge_ids):
        raw_title = member_metadata[member].get("title")
        title = (
            raw_title if isinstance(raw_title, str) and raw_title.strip() else member
        )
        new_log_text = bundle_log.insert_log_entry(
            new_log_text,
            now.astimezone().date(),
            f"**Tombstone** ({tombstone_time}): Removed [{title}]"
            f"(/{member}.md) (id: {member}).",
        )

    # Gate 2's prompt is scope-conditional (design table); `--scope self`
    # keeps S2a's verbatim text (byte-identity, design decision 6).
    prompt = (
        f"Delete {len(purge_ids)} concepts?"
        if scope == "source"
        else "Proceed with these changes?"
    )
    confirmation = BooleanConfirmation(
        prompt=prompt,
        bypass_flag="--auto",
        non_tty_refusal=(
            "openkos forget: refusing to write without confirmation -- "
            "stdin is not a TTY; re-run with --auto."
        ),
    )

    return ForgetPlan(
        purge_ids=purge_ids,
        total_removed=total_removed,
        new_index_text=new_index_text,
        new_log_text=new_log_text,
        references=references,
        resurrection_pairs=tuple(resurrection_pairs),
        surviving_refs=len(verified_refs),
        unverifiable_refs=len(unverifiable_refs),
        confirmation=confirmation,
        index_bytes=index_bytes,
        log_bytes=log_bytes,
        concept_bytes=concept_bytes,
        other_bytes=other_bytes,
    )


def forget_core(layout: config.WorkspaceLayout, plan: ForgetPlan) -> ForgetResult:
    """Phase B (after both gates): writes `index.md` then `log.md`
    (`write_atomic`, catalog FIRST, covering every purge-set member) and
    deletes each member's concept file (`fsio.remove_file`) LAST, in
    deterministic `sorted(purge_ids)` order (design decision 5) -- so
    `index.md`/`log.md` never reference a file that does not exist
    (extracted verbatim from `forget`'s former inline body, design:
    Interfaces/Contracts "S3 -- forget"). Non-interactive; raises
    `OSError`/`ValueError`. Performs NO VCS side effect and no ledger/
    decision/findings sweep -- `_sweep_ledger_sidecars_for_ids`,
    `_sweep_decisions_for_ids`, and `_sweep_findings_for_ids` stay adapter-
    side (they are shared with `purge`'s own Phase B, which calls the same
    three helpers, and this module must stay siblings-only under ADR-0018
    rather than import another verb's helpers), called by the command
    immediately after this, inside the SAME try/except so a mid-sweep
    failure reports the identical K-of-N recovery message a mid-unlink
    failure would.

    This is NOT transactional as a whole: a failure partway through the N
    unlinks leaves a benign, git-recoverable partial result -- the catalog
    already fully updated, one or more concept files possibly still
    present as orphans -- never silent corruption."""
    unlinked_count = 0
    try:
        fsio.write_atomic(layout.bundle_dir / "index.md", plan.new_index_text)
        fsio.write_atomic(layout.bundle_dir / "log.md", plan.new_log_text)
        # N-delete, LAST, in deterministic sorted order (design decision 5)
        # -- the catalog already reflects every removal before any unlink,
        # so a failure partway through leaves a benign, git-recoverable
        # partial result, never a dangling catalog entry.
        for member in sorted(plan.purge_ids):
            fsio.remove_file(layout.bundle_dir / f"{member}.md")
            unlinked_count += 1
    except (OSError, ValueError) as exc:
        # The counter is incremented only AFTER a successful unlink, so it
        # is exactly what a live counter in the command's own former inline
        # loop held -- see `PartialForgetWrite` for why this is carried out
        # rather than re-derived from the filesystem.
        raise PartialForgetWrite(exc, unlinked_count) from exc
    return ForgetResult(purge_ids=plan.purge_ids)


# ---------------------------------------------------------------------------
# S4 -- purge (issue #918, design C1/D3): the disclosure and typed-phrase
# gate move; Phase B (`git filter-repo`, the two live-tree cleanups, the
# derived-store rebuild) stays adapter-side in full -- `purge` drives
# `vcs_git.expunge_paths`, and this module must never import `openkos.vcs`.
# ---------------------------------------------------------------------------


def _decisions_history_targets(bundle_dir: Path, purge_ids: Iterable[str]) -> list[str]:
    """Every `bundle/.state/decisions/**` path -- own OR foreign -- that
    references a purge-set member, for inclusion in `purge`'s
    `expunge_targets` list IN THE SAME `git filter-repo` pass as the
    concept's own file expunge (privacy-purge spec: "Whole-History
    Expunge Covers The Pending-Work Decision Subtree", pending-work design
    Decision 5). Relocated verbatim from `cli/main.py` alongside
    `prepare_purge`, its only caller -- pure (no `typer`, no `vcs`), so it
    moves with the Phase-A computation it feeds rather than staying behind
    as an orphaned adapter helper.

    A record "references" `purge_ids` when its `pair_ids` (either
    element) OR its `merged_absorbed_id` names a purge-set member.

    Unlike the merge-ledger sidecar's history coverage (own sidecar ONLY,
    the `bundle_ledger.ledger_path_for` loop in `prepare_purge`) -- which
    leaves a FOREIGN sidecar's historical `absorbed_id` entries as a
    documented gap -- this covers foreign decisions sidecars too.
    `expunge_paths`' own `--file-info-callback` content-scrub is wired
    ONLY for `bundle/index.md`/`bundle/log.md`, not for `bundle/.state/**`,
    so a whole-file history removal is the only way to guarantee no
    historical blob of ANY decisions path retains a purged id, which the
    spec requires. `_sweep_decisions_for_ids` (adapter-side, shared with
    `forget`'s own Phase B) is the LIVE-tree counterpart that reconstructs
    a foreign file's surviving (unrelated) records afterwards, in the SAME
    Phase B pass.

    Returned as bundle-relative POSIX strings (`bundle/.state/decisions/
    **`), matching the shape every other `expunge_targets` entry already
    uses -- callers append these directly, no further conversion needed."""
    purge_ids_set = set(purge_ids)
    targets: list[str] = []
    for decisions_path in bundle_decisions.iter_decisions(bundle_dir):
        metadata, _ = okf.load_frontmatter(decisions_path.read_text(encoding="utf-8"))
        concept_id = metadata.get("concept_id")
        if not isinstance(concept_id, str) or not concept_id:
            continue
        # Read the WALKED path, not a path rebuilt from the sidecar's own
        # `concept_id` content, so a drifted or hostile id cannot redirect
        # this read outside the bundle (F1b read-side traversal).
        records = bundle_decisions.read_decisions_at(decisions_path)
        # #797: the sidecar carries TWO kinds of human ruling and a purge
        # must cover both. An identity decision names its members in
        # `member_ids`, a field the contradiction records have no notion
        # of -- reading only `pair_ids` would leave a purged id sitting in
        # a keep-distinct record's history blob.
        identity_records = bundle_decisions.read_identity_decisions_at(decisions_path)
        references_purge_set = any(
            record.pair_ids[0] in purge_ids_set
            or record.pair_ids[1] in purge_ids_set
            or record.merged_absorbed_id in purge_ids_set
            for record in records
        ) or any(
            any(member in purge_ids_set for member in record.member_ids)
            for record in identity_records
        )
        if references_purge_set:
            targets.append(
                f"bundle/{decisions_path.relative_to(bundle_dir).as_posix()}"
            )
    return targets


@dataclass(frozen=True)
class PurgeDisclosure:
    """Every line `purge`'s preview renders before rail 1, computed once
    during Phase A (design: Interfaces/Contracts "S4 -- purge"; the
    disclosure is the operator's last full account of the irreversible
    history rewrite before ANY rail runs, including the reference-aware
    one)."""

    expunge_targets: tuple[str, ...]
    """Every path `git filter-repo` will strip from ALL history: each
    purge-set member's `bundle/<id>.md`, any resolved `raw/<name>` source
    material, its own merge-ledger sidecar (if it is/was a survivor), and
    every decisions sidecar (own or foreign) referencing the set --
    rendered `- {target}`, one per line, in construction order."""

    resource_warnings: tuple[str, ...]
    """One line per purge-set member whose `resource` frontmatter is
    present but absent/malformed -- WARNED, not refused (its bundle file
    is still targeted); rendered `! {warning}`."""

    raw_absence: bool
    """`True` when NO purge-set member contributed a raw source path --
    either every member is a derived concept, or a Source's `resource`
    failed validation (already covered by its own warning above). Renders
    the explicit "no raw source material" line: an omission would read the
    same as a shorter-but-complete list, and this is the one disclosure
    line deciding whether the source material itself survives."""

    cascade_total: int | None
    """The purge-set size, `--scope source` only (`None` for `--scope
    self`, which never renders the trailing "Total: N concept(s)" line)."""


@dataclass(frozen=True)
class PurgePlan:
    """Pure Phase-A result of `prepare_purge`: everything `purge`'s
    preview and all six rails need, built in memory without writing,
    deleting, or rewriting any git history (design: Interfaces/Contracts
    "S4 -- purge"). There is no `purge_core` -- Phase B is `git
    filter-repo` plus adapter-only bookkeeping (D3), never a service
    write.

    `verified_refs`/`unverifiable_refs` are rail 1's own inputs -- a hard
    refusal independent of any human answer, bypassed only by `--force` --
    and are deliberately plain `int`s, never threaded through
    `confirmation` (design D2): neither `ConfirmationRequest` variant has a
    field named either, so rail 1 cannot be represented as "a request that
    was granted" by construction (`test_lifecycle_seams.py`).

    `confirmation` is a real, fully-populated `TypedChallengeConfirmation`
    -- but the adapter's rail 6 deliberately does NOT read `.expected` off
    it for the live gate comparison. `purge_confirm_phrase` (this same
    module) computes an IDENTICAL value from the SAME `canonical_id`/
    `purge_ids`/`scope`, and the adapter calls it FRESH, at rail 6, exactly
    where `_purge_confirm_phrase` was called before this move --
    `test_purge.py::test_drift_on_the_unprompted_path_is_refused` patches
    that exact call to inject a race and requires it to fire strictly
    AFTER rail 4's clean-tree check, not during Phase A (which runs before
    rail 1). Computing `confirmation.expected` here via an inline copy of
    `purge_confirm_phrase`'s one-line formula -- rather than by calling
    the function itself -- keeps this field real and independently
    testable (`test_lifecycle.py`) without moving that race window earlier
    and turning the pinned exit-3 refusal into rail 4's exit-1 one."""

    canonical_id: str
    purge_ids: list[str]
    disclosure: PurgeDisclosure
    verified_refs: int
    unverifiable_refs: int
    confirmation: TypedChallengeConfirmation
    drift_targets: dict[Path, bytes]


def purge_confirm_phrase(
    canonical_id: str, purge_ids: list[str], scope: Literal["self", "source"]
) -> str:
    """The exact typed confirmation phrase `purge` requires before Phase B:
    `purge <canonical_id>` for `--scope self`, `purge <canonical_id> (<N>
    concepts)` for `--scope source` -- names the delete COUNT so an
    operator cannot type the self-scope phrase by habit and unknowingly
    confirm a larger cascade (design: Typed Confirmation). Relocated
    verbatim from `cli/main.py`'s `_purge_confirm_phrase`.

    Deliberately called LIVE by the adapter at rail 6, not by
    `prepare_purge` -- see `PurgePlan.confirmation`'s own docstring for
    why."""
    if scope == "source":
        return f"purge {canonical_id} ({len(purge_ids)} concepts)"
    return f"purge {canonical_id}"


def prepare_purge(
    root: Path,
    layout: config.WorkspaceLayout,
    concept_id: str,
    *,
    scope: Literal["self", "source"],
    now: datetime,
) -> PurgePlan:
    """Phase A (pure, no writes, no history rewrite): read the root's own
    text and one whole-bundle snapshot, resolve the purge set (`--scope
    self` collapses to `{concept_id}`; `--scope source` expands it via
    `bundle_provenance.find_provenance_descendants`), resolve each
    member's raw source path (a Source's `resource: raw/<name>`
    frontmatter; a derived concept contributes only its own bundle file),
    collect every expunge target (bundle files, raw paths, ledger
    sidecars, decisions sidecars), and count inbound references (rail 1's
    inputs) -- extracted verbatim from `purge`'s former inline body
    (`cli/main.py`'s `purge` command, design: Interfaces/Contracts "S4 --
    purge"). Non-interactive; raises `OSError`/`ValueError` on bad input.
    Writes nothing, deletes nothing, rewrites no history.

    `concept_id` arrives ALREADY path-safety-checked and resolved to its
    canonical form -- `purge`'s adapter runs `resolve_concept_path` on the
    user's raw argument BEFORE calling this (threat matrix: path-traversal
    deletion; identical to `forget`'s own contract), so this recomputes
    the concept file path from the validated id via `okf.concept_path_for`
    rather than re-deriving it from unchecked input.

    `now` is accepted for signature parity with `prepare_forget` (every
    `prepare_*` in this module takes it) but is not read: `purge` writes
    no timestamped content of its own -- the tombstone/log entries stay
    with `forget`.

    Every plan-feeding read goes through `fsio.snapshot_read`, capturing
    the raw bytes BESIDE the decoded text -- one observation per target,
    never a second read (issues #313, #318, #321) -- so the returned
    `PurgePlan.drift_targets` mapping is already the complete guard input
    the adapter re-validates after rail 6, unconditionally, before the
    first write (#321: `--confirm-phrase` skips the prompt but not the
    window it stood in)."""
    canonical_id = concept_id
    index_path = layout.bundle_dir / "index.md"
    log_path = layout.bundle_dir / "log.md"
    concept_path = okf.concept_path_for(canonical_id, layout.bundle_dir)

    # One `fsio.snapshot_read` observation per target (issues #313, #318,
    # #321): each path is read exactly once, at the moment its decoded
    # text feeds the plan, and the guard's bytes come from that same read.
    # `index.md`/`log.md` are not plan inputs here -- their post-rewrite
    # cleanup re-reads them fresh -- but they ARE what `git filter-repo`'s
    # checkout clobbers, so their baselines are captured with the same
    # single-observation discipline.
    index_bytes, _ = fsio.snapshot_read(index_path)
    log_bytes, _ = fsio.snapshot_read(log_path)
    concept_bytes, concept_text = fsio.snapshot_read(concept_path)

    # Same whole-bundle snapshot construction as `forget`'s (~L917-925), but
    # UNCONDITIONAL for both scopes -- moved verbatim rather than adopting
    # `forget`'s later scope-conditional optimization, matching design D3's
    # "moves as-is" posture. `other_bytes` shadows it for the guard: which
    # of these files the run will EXPUNGE is not known until `purge_ids`
    # resolves below, so the bytes come out of the same `fsio.snapshot_read`
    # observation as the text rather than re-read per member afterwards.
    other_files: dict[str, str] = {}
    other_bytes: dict[str, bytes] = {}
    for path in sorted(layout.bundle_dir.rglob("*.md")):
        if path.name in okf.RESERVED_FILENAMES:
            continue
        if path == concept_path:
            continue
        rel = path.relative_to(layout.bundle_dir).as_posix()
        other_bytes[rel], other_files[rel] = fsio.snapshot_read(path)

    purge_ids: list[str] = (
        bundle_provenance.find_provenance_descendants(
            other_files, root_ids={canonical_id}
        )
        if scope == "source"
        else [canonical_id]
    )
    purge_ids_set = set(purge_ids)

    member_texts: dict[str, str] = {canonical_id: concept_text}
    for member in purge_ids:
        if member != canonical_id:
            member_texts[member] = other_files[f"{member}.md"]
    member_metadata: dict[str, dict[str, object]] = {
        member: okf.load_frontmatter(text)[0] for member, text in member_texts.items()
    }

    # Reference-aware detection (rail 1's data), identical set-difference
    # gate to `forget`'s.
    all_refs: list[tuple[str, bundle_references.InboundReference]] = []
    seen_unverifiable: set[str] = set()
    for member in purge_ids:
        for ref in bundle_references.find_inbound_references(
            other_files, target_id=member
        ):
            if ref.referrer_id in purge_ids_set:
                continue
            if ref.kind == "unverifiable":
                if ref.referrer_id in seen_unverifiable:
                    continue
                seen_unverifiable.add(ref.referrer_id)
            all_refs.append((member, ref))
    verified_refs = [ref for _, ref in all_refs if ref.kind != "unverifiable"]
    unverifiable_refs = [ref for _, ref in all_refs if ref.kind == "unverifiable"]

    # Raw-path resolution (design: "Raw-path resolution"): a Source's
    # `resource` is validated (must start with `raw/`, no `..`, resolve
    # under `layout.raw_dir`) -- an absent or malformed `resource` is
    # WARNED about, never refused, and simply contributes no raw path
    # (this Source's own `bundle/<id>.md` is still targeted).
    expunge_targets: list[str] = []
    # The raw paths are collected into their OWN list as they resolve,
    # rather than sniffed back out of `expunge_targets` by prefix at
    # preview time: that list is deliberately MIXED (raw paths, bundle
    # files, ledger sidecars, decisions sidecars), so a `raw/` prefix test
    # would be a silent liability the day another target kind gains a
    # similar prefix. This list answers exactly one question -- did
    # anything in this purge set resolve source material? -- and cannot
    # drift from the answer.
    resolved_raw_paths: list[str] = []
    resource_warnings: list[str] = []
    raw_dir_resolved = layout.raw_dir.resolve()
    for member in sorted(purge_ids):
        resource = member_metadata[member].get("resource")
        if isinstance(resource, str) and resource:
            posix_resource = PurePosixPath(resource)
            valid = (
                resource.startswith("raw/")
                and not resource.startswith("/")
                and ".." not in posix_resource.parts
            )
            if valid:
                try:
                    (root / resource).resolve().relative_to(raw_dir_resolved)
                except ValueError:
                    valid = False
            if valid:
                resolved_raw_paths.append(resource)
                expunge_targets.append(resource)
            else:
                resource_warnings.append(
                    f"'{member}': resource frontmatter {resource!r} is "
                    "absent/malformed -- skipping its raw-path expunge "
                    "(its bundle file is still targeted)"
                )
        expunge_targets.append(f"bundle/{member}.md")
    # Whole-History Expunge Covers The Ledger Sidecar Store (privacy-purge
    # spec, task 3.4): every purge-set member's OWN `bundle/.state/ledger/`
    # sidecar (i.e. it is/was itself a merge survivor) is expunged in this
    # SAME `git filter-repo` pass -- no second invocation. An
    # absorbed-but-not-itself-a-survivor member has no sidecar of its own;
    # its historical body may still live as an `absorbed_snapshot`
    # fragment inside a DIFFERENT survivor's sidecar, which stays a
    # documented gap (see design's threat matrix note) rather than a
    # whole-file expunge target here.
    for member in sorted(purge_ids):
        member_sidecar = bundle_ledger.ledger_path_for(member, layout.bundle_dir)
        if member_sidecar.is_file():
            expunge_targets.append(
                f"bundle/{member_sidecar.relative_to(layout.bundle_dir).as_posix()}"
            )
    # Whole-History Expunge Covers The Pending-Work Decision Subtree
    # (privacy-purge spec, B1.4): every `bundle/.state/decisions/**`
    # sidecar -- own OR foreign -- that references a purge-set member is
    # expunged in this SAME `git filter-repo` pass. Unlike the ledger
    # sidecar loop above, this covers FOREIGN sidecars too
    # (`_decisions_history_targets`'s own docstring explains why).
    expunge_targets.extend(_decisions_history_targets(layout.bundle_dir, purge_ids))
    # Threat matrix ("Shell / subprocess"): concept ids are user-
    # controlled, and a decisions path derived from one could contain
    # `==>` (git-filter-repo's rename delimiter) or another rejected
    # sequence -- the FULL `expunge_targets` list is re-validated by the
    # adapter (`vcs_git._validate_rel_paths`, which this module must never
    # import) immediately after this call returns, in Phase A's own
    # `except (OSError, ValueError)` arm, before the preview is ever
    # printed.

    disclosure = PurgeDisclosure(
        expunge_targets=tuple(expunge_targets),
        resource_warnings=tuple(resource_warnings),
        raw_absence=not resolved_raw_paths,
        cascade_total=len(purge_ids) if scope == "source" else None,
    )

    # See `PurgePlan.confirmation`'s own docstring: this duplicates
    # `purge_confirm_phrase`'s one-line formula rather than calling it, so
    # that the LIVE call the adapter makes at rail 6 remains the only call
    # `test_purge.py`'s race-injection tests ever observe.
    expected_phrase = (
        f"purge {canonical_id} ({len(purge_ids)} concepts)"
        if scope == "source"
        else f"purge {canonical_id}"
    )
    confirmation = TypedChallengeConfirmation(
        prompt=f"Type '{expected_phrase}' to proceed",
        expected=expected_phrase,
        supplying_flag="--confirm-phrase",
        non_tty_refusal=(
            "openkos purge: refusing to purge -- stdin is not a TTY; "
            "re-run with --confirm-phrase."
        ),
        mismatch_abort=(
            "openkos purge: aborted -- confirmation phrase did not match "
            "exactly; nothing was written."
        ),
        match_mode="exact",
    )

    drift_targets: dict[Path, bytes] = {
        index_path: index_bytes,
        log_path: log_bytes,
        concept_path: concept_bytes,
        **{
            # Defensive-only in the same sense as `_require_member_baseline`
            # (adapter-side, still used by `forget`): `purge_ids` and
            # `other_bytes` are built from the SAME bundle scan above, so
            # this key exists by construction -- Phase A's own
            # `member_texts` lookup would already have raised `KeyError`
            # otherwise.
            layout.bundle_dir / f"{member}.md": other_bytes[f"{member}.md"]
            for member in purge_ids
            if member != canonical_id
        },
    }

    return PurgePlan(
        canonical_id=canonical_id,
        purge_ids=purge_ids,
        disclosure=disclosure,
        verified_refs=len(verified_refs),
        unverifiable_refs=len(unverifiable_refs),
        confirmation=confirmation,
        drift_targets=drift_targets,
    )


def dropped_store_notice(dropped: Sequence[tuple[Path, str]]) -> str | None:
    """The operator-facing account of every store this purge actually
    destroyed (#886), or `None` when it destroyed none. Moved verbatim
    from `cli/main.py`'s `_purge_dropped_store_notice` (design D3's
    exception: it already returned `str | None`, so it moves as-is rather
    than being re-authored into a typed token).

    `dropped` carries each store's cost beside its path, so this renders
    the caller's finding rather than re-deriving it -- there is no lookup
    here that a new store could miss.

    `purge` deleted five stores, rebuilt two, and the notice named ONE. The
    two undisclosed stores held work the operator had paid for: in the
    session that filed the issue, 11 persisted contradiction verdicts, 9
    edge suggestions and 7 identity adjudications went with them, minutes
    after `contradictions` reported "11 of 11 candidate(s) served from
    persisted findings; 0 judged fresh". #142's justification for the
    vectors warning -- warn every time so an operator is never left
    assuming dense retrieval is still intact -- was never applied to the
    other two.

    DESTRUCTION is what makes a store reportable, and it takes both
    halves: the store existed before this purge, and it is gone after.
    Membership in the delete list proves neither. `unlink` can fail --
    warned on stderr rather than raised, adapter-side -- so a notice built
    from the intended list would announce a store as dropped while it is
    still on disk. And absence alone is not loss: a workspace that never
    ran `curate` has no `findings.db` to begin with. The count is derived
    from the same list for the same reason a literal would be wrong.

    The closing sentence about rulings is load-bearing and was VERIFIED,
    not assumed. #886 states purge destroyed "the operator's own recorded
    rulings (two declined identity merges)". All three `findings.db`
    tenants hold MACHINE-computed verdicts, while a `--decline` or
    `--keep-distinct` ruling is written under the bundle's decision
    subtree and committed with the bundle, so a ruling on a SURVIVING
    concept is untouched by the store drop. It is deliberately qualified:
    a decision path referencing a purge-set member IS expunged in the same
    rewrite pass (privacy-purge: Whole-History Expunge Covers The
    Pending-Work Decision Subtree), so an unqualified promise would read
    as the erasure having missed something."""
    if not dropped:
        return None
    lines = [
        f"openkos purge: {len(dropped)} derived store(s) were dropped and "
        "are NOT rebuilt."
    ]
    lines += [f"  - {path.name}: {cost}" for path, cost in dropped]
    lines.append(
        "Your own rulings are not in these stores: a `--decline` or "
        "`--keep-distinct` ruling on a concept OUTSIDE the purge set is "
        "recorded in the bundle and survives. A ruling that named a purged "
        "concept was expunged with it, which is the erasure working."
    )
    return "\n".join(lines)


def residual_store_notice(undeleted: Sequence[Path]) -> str | None:
    """The operator-facing account of an INCOMPLETE erasure (#923), or
    `None` when every delete succeeded. Moved verbatim from
    `cli/main.py`'s `_purge_residual_store_notice` (design D3's exception:
    it already returned `str | None`).

    This is not the dropped-store notice's counterpart -- that one prices
    a restore, this one reports a failure -- and it must name the exact
    paths, because acting on it means removing those files.

    What it deliberately does NOT say is `openkos reindex`. The old
    warning said exactly that, and a reindex rebuilds a store's CONTENT:
    it never removes the pages a failed unlink left behind. An operator
    who ran the recommended command got search back and kept the residue,
    which is the worse of the two failure modes because it looks resolved.
    The delete is the erasure -- the adapter's index-rebuild step unlinks
    rather than issuing a row-level `DELETE` precisely so no
    freelist-recoverable pages survive -- so only removing the file
    finishes what the purge started."""
    if not undeleted:
        return None
    lines = [
        f"openkos purge: INCOMPLETE ERASURE -- {len(undeleted)} derived "
        "store(s) could not be deleted and still hold pre-purge content:"
    ]
    lines += [f"  - {path}" for path in undeleted]
    lines.append(
        "The git history rewrite itself succeeded. To finish the erasure, "
        "clear whatever blocked the delete (an open handle, a read-only "
        "parent directory, file permissions) and remove the file(s) above; "
        "then run `openkos reindex` to restore search. `openkos reindex` "
        "alone does NOT complete the erasure: it rebuilds index content and "
        "leaves the residual pages exactly where they are."
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Slice 5 (S5, issue #918): de-presenting `adjudicate --apply`/`--apply-same`
# ---------------------------------------------------------------------------
#
# `ordered_merge_pair`, `prepare_one_merge`, and `reconcile_planned` moved
# verbatim from `cli/main.py`'s `_ordered_merge_pair`/`_prepare_one_merge`/
# `_reconcile_planned` (design's S5 interface list). `cross_source_same_pair`/
# `cross_type_concern` moved alongside them, undocumented in the design's
# abbreviated S5 sketch but required by `preview_apply_same`'s own signature
# (it takes `include_cross_source`/`include_cross_type` directly, so it must
# perform that classification itself) -- both are pure bundle reads with no
# `typer` dependency, exactly like every other Phase-A helper here. All five
# are aliased back onto `cli.main` under their original names (design D5):
# none carries a dangerous `test_adjudicate.py` patch site (only direct
# `main.X(...)` calls and `cli/curate.py`'s own direct calls survive), so
# aliasing is safe, unlike `prepare_merge`/`merge_core`.

_RECONCILE_SHARE_THRESHOLD = 0.2
"""Stacked share at or above which `merge` plans the reconciliation pass
(#645, opt-out by ruling). Moved verbatim from `cli/main.py` alongside
`reconcile_planned`, the only reader."""

_RECONCILE_MIN_MERGED_CHARS = 200
"""Absolute floor under which the pass is never planned, whatever the
share -- measured on the MERGED body (#803). Moved verbatim from
`cli/main.py` alongside `reconcile_planned`, the only reader."""


def member_body_length(bundle_dir: Path, member_id: str) -> int:
    """Stripped body length of one member's document, or `-1` when it
    cannot be read or parsed (#776) -- the one measurement
    `ordered_merge_pair` ranks on. `-1` rather than `0` so an unreadable
    member can never beat a readable-but-empty one. Moved verbatim from
    `cli/main.py`'s `_member_body_length`.

    PUBLIC, unlike the private name it moved under (issue #974). It has a
    cross-module consumer -- `cli/main._echo_n_gt2_skip` ranks an N>2
    group's members on it to name the survivor its manual-merge script
    should keep -- and the other eight helpers issue #918 Slice 5
    relocated alongside it are all reached publicly. While this one kept a
    leading underscore, the definition site advertised a module-private
    helper that a maintainer could rename or inline, and the reference
    that would have stopped working sat in a `lambda` body in another
    file."""
    try:
        path, _canonical = resolve_concept_path(bundle_dir, member_id)
        _metadata, body = okf.load_frontmatter(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return -1
    return len(body.strip())


def ordered_merge_pair(
    bundle_dir: Path, member_ids: tuple[str, ...]
) -> tuple[str, str, str]:
    """`(survivor, absorbed, criterion)` for one 2-member SAME group
    (#776): the member with the RICHER BODY survives, so a permanent
    Concept ID is no longer decided by `f` sorting before `o`. Ties
    (including two unreadable members, which `-1 == -1` here and
    `prepare_one_merge` then reports as unresolved) keep today's
    ascending-id order, and `criterion` names WHICH rule decided so every
    preview can state it. Moved verbatim from `cli/main.py`'s
    `_ordered_merge_pair`."""
    first, second = member_ids
    first_length = member_body_length(bundle_dir, first)
    second_length = member_body_length(bundle_dir, second)
    if second_length > first_length:
        return second, first, "richer body"
    if first_length > second_length:
        return first, second, "richer body"
    return first, second, "id order -- equal body length"


def cross_source_same_pair(bundle_dir: Path, member_ids: tuple[str, ...]) -> bool:
    """Whether a SAME verdict over `member_ids` is the RISKY class #776
    reports: every member carries a non-empty `provenance:` and the sets
    are DISJOINT. Deliberately `False` on missing evidence. Moved verbatim
    from `cli/main.py`'s `_cross_source_same_pair`."""
    provenance_sets: list[set[str]] = []
    for member_id in member_ids:
        try:
            path, _canonical = resolve_concept_path(bundle_dir, member_id)
            metadata, _body = okf.load_frontmatter(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        raw = metadata.get("provenance")
        if not isinstance(raw, list) or not raw:
            return False
        provenance_sets.append({str(entry).removesuffix(".md") for entry in raw})
    return not set.intersection(*provenance_sets)


def cross_type_concern(bundle_dir: Path, member_ids: tuple[str, ...]) -> str | None:
    """The reason a SAME verdict over `member_ids` must not be merged
    unreviewed on TYPE grounds, or `None` when the members demonstrably
    agree (issue #904). Moved verbatim from `cli/main.py`'s
    `_cross_type_concern`."""
    types: list[str] = []
    for member_id in member_ids:
        try:
            path, _canonical = resolve_concept_path(bundle_dir, member_id)
        except ValueError:
            return None
        try:
            metadata, _body = okf.load_frontmatter(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return f"{member_id} could not be read, so its OKF type is unknown"
        raw = metadata.get("type")
        if not isinstance(raw, str) or not raw:
            return f"{member_id} declares no usable OKF type"
        types.append(raw)
    if len(set(types)) < 2:
        return None
    label = " / ".join(dict.fromkeys(types))
    return f"members declare different OKF types ({label})"


def prepare_one_merge(
    root: Path,
    layout: config.WorkspaceLayout,
    index_path: Path,
    log_path: Path,
    group: CandidateGroup,
    *,
    ordered_pair: tuple[str, str] | None = None,
) -> PreparedMerge | None:
    """Resolve both member ids of one SAME 2-member `group` and build the
    pure `PreparedMerge` preview -- the one apply-one-pair unit both
    `adjudicate --apply`'s interactive walk and `preview_apply_same`'s
    batch share. Returns `None` when either member id fails to resolve.
    Otherwise raises `OSError`/`ValueError` straight from `prepare_merge`,
    unchanged.

    `ordered_pair`, when given, PINS the direction instead of re-deriving
    it from live file contents (#776 review, 3-lens CRITICAL): the
    `--apply-same` typed count consents to Pass 1's PREVIEWED survivor, and
    an earlier merge in the same batch can enrich a shared member enough to
    flip a live recomputation. Moved verbatim from `cli/main.py`'s
    `_prepare_one_merge`."""
    if ordered_pair is None:
        survivor_id, absorbed_id, _criterion = ordered_merge_pair(
            layout.bundle_dir, group.member_ids
        )
    else:
        survivor_id, absorbed_id = ordered_pair
    try:
        survivor_path, survivor_canonical = resolve_concept_path(
            layout.bundle_dir, survivor_id
        )
        absorbed_path, absorbed_canonical = resolve_concept_path(
            layout.bundle_dir, absorbed_id
        )
    except ValueError:
        return None

    now = datetime.now(UTC)
    return prepare_merge(
        layout.bundle_dir,
        index_path,
        log_path,
        survivor_path,
        absorbed_path,
        survivor_canonical,
        absorbed_canonical,
        root,
        now=now,
    )


def reconcile_planned(
    prepared: PreparedMerge, *, no_reconcile: bool, reconcile: bool = False
) -> bool:
    """Whether the #645 merged-body reconciliation pass runs for
    `prepared` -- THE single source of truth for that decision (issue
    #688). Precedence (issue #803): `no_reconcile` wins over everything;
    a `prepared` with no `stacked_body` is always `False`; `reconcile`
    then forces `True`; otherwise the thresholds decide. Moved verbatim
    from `cli/main.py`'s `_reconcile_planned`."""
    if no_reconcile:
        return False
    if prepared.stacked_body is None:
        return False
    if reconcile:
        return True
    return (
        prepared.stacked_body.share >= _RECONCILE_SHARE_THRESHOLD
        and prepared.stacked_body.merged_chars >= _RECONCILE_MIN_MERGED_CHARS
    )


@dataclass(frozen=True)
class PreviewedPair:
    """One SAME 2-member group Pass 1 accepted into the `--apply-same`
    batch preview: the `ordered_merge_pair` direction and criterion, and
    the Pass-1 `PreparedMerge` snapshot used ONLY to render the preview
    line. Pass 2 re-resolves via `ordered` (issue #776's pinned direction)
    rather than reusing `prepared`."""

    group: CandidateGroup
    ordered: tuple[str, str]
    survivor_criterion: str
    prepared: PreparedMerge


@dataclass(frozen=True)
class NGt2Skip:
    """A SAME group with more than 2 members: `adjudicate --apply-same`
    never merges these automatically (issue #191)."""

    group: CandidateGroup


@dataclass(frozen=True)
class CrossSourceSkip:
    """A SAME 2-member pair excluded for disjoint provenance (issue #776),
    unless `include_cross_source` opted in."""

    member_ids: tuple[str, ...]
    ordered: tuple[str, str]


@dataclass(frozen=True)
class CrossTypeSkip:
    """A SAME 2-member pair excluded for disagreeing OKF types (issue
    #904), unless `include_cross_type` opted in."""

    member_ids: tuple[str, ...]
    ordered: tuple[str, str]
    reason: str


BatchSkip = NGt2Skip | CrossSourceSkip | CrossTypeSkip
"""Pass-1 classification exclusions, tagged by type, in the SAME relative
order the original single classification loop encountered them (issue
#918 Slice 5) -- required so `--apply-same`'s adapter can render each
exclusion's distinct message without re-deriving which one applies, and
so the categories stay interleaved exactly as they were when one loop
decided all of them together."""


@dataclass(frozen=True)
class StackedRefusal:
    """A prepared pair excluded because its stacked-body share crosses the
    guardrail (issue #559) -- the typed-count gate consents to a batch,
    not to this pair."""

    report: StackedBodyReport
    survivor_canonical: str
    absorbed_canonical: str


BatchPreviewItem = PreviewedPair | StackedRefusal
"""Pass-1 preview-build outcomes, tagged by type, in `eligible_groups`
order -- a stacked-body-guardrail refusal and a clean previewed pair
interleave in this same pass in the original code, and this preserves
that exact order."""


@dataclass(frozen=True)
class BatchApplyPreview:
    """`preview_apply_same`'s pure result (issue #918 Slice 5): the whole
    of `adjudicate --apply-same`'s Pass 1, performing no echo, no prompt,
    and no write (design D2: no `granted`/`force`/`override` field
    anywhere on `confirmation`). `previewed` is the `PreviewedPair`
    subsequence of `items`, kept as its own field because Pass 2 iterates
    only the previewed pairs, never the stacked refusals."""

    skips: tuple[BatchSkip, ...]
    items: tuple[BatchPreviewItem, ...]
    previewed: tuple[PreviewedPair, ...]
    confirmation: TypedChallengeConfirmation


class PreviewMergeFailure(OSError):
    """`preview_apply_same`'s Pass-1 build failed to prepare one pair,
    carrying the (survivor_id, absorbed_id) identity and everything
    classified so far, so the adapter's "failed while previewing X into Y"
    message -- and every skip/preview line that already printed before the
    failure in the pre-move code -- stays reproducible without the service
    ever importing `typer`. Mirrors `PartialForgetWrite`'s "carry the fact
    out of the exception" shape (design D5's own precedent).

    Subclasses `OSError` so the adapter's existing `except (OSError,
    ValueError)` arm catches it unchanged, and `str()` reproduces the
    cause's text verbatim."""

    def __init__(
        self,
        cause: BaseException,
        survivor_id: str,
        absorbed_id: str,
        partial: BatchApplyPreview,
    ) -> None:
        super().__init__(str(cause))
        self.survivor_id = survivor_id
        self.absorbed_id = absorbed_id
        self.partial = partial


def _apply_same_confirmation(total: int) -> TypedChallengeConfirmation:
    """The `--apply-same` typed-count gate (`main.py:2967-2985`'s policy):
    `match_mode="strip-then-exact"` because the comparison there is
    `typed_count.strip() != str(total)` -- `purge`'s own gate compares the
    RAW response instead, which is exactly why `match_mode` travels as
    data on the request rather than being re-derived per call site."""
    return TypedChallengeConfirmation(
        prompt=f"Type the eligible count ({total}) to proceed",
        expected=str(total),
        supplying_flag="--confirm-count",
        non_tty_refusal=(
            "openkos adjudicate --apply-same: refusing to apply -- stdin is "
            "not a TTY; re-run with --confirm-count."
        ),
        mismatch_abort=(
            "openkos adjudicate --apply-same: aborted -- confirmation count "
            "did not match exactly; nothing was written."
        ),
        match_mode="strip-then-exact",
    )


def merge_walk_confirmation(
    *, survivor_canonical: str, absorbed_canonical: str
) -> BooleanConfirmation:
    """The merge-walk gate shared by `adjudicate --apply`'s per-item walk
    and curate's Identity stage (issue #958, closing the one gate #918
    named but left un-staged pending a protocol decision, design:
    `openspec/changes/lifecycle-application-service/design.md` D1).
    Public, unlike `_apply_same_confirmation` above, because BOTH
    interactive walks that ask this exact question -- `_run_adjudicate_
    apply` in `cli/main.py` and `_identity_run` in `cli/curate.py` -- need
    to render it, and a private helper importable only within
    `lifecycle.py` cannot serve two call sites in `openkos.cli`.

    Keyword-only parameters (issue #958 correction round): both are
    plain, same-typed `str`s, and this factory renders them in the
    OPPOSITE of parameter order -- `f"Merge {absorbed} into {survivor}"`
    reads naturally, but a positional call that mirrors the sentence it is
    reading (absorbed, then survivor) would still type-check and silently
    prompt the operator to approve destroying the wrong side of a merge.
    Keyword-only makes that transposition a `TypeError`, not a silent bug.

    `bypass_flag=None` and `non_tty_refusal=None` are the DECIDED contract
    for this gate, not a gap: the shipped transport is stdin, one answer
    per item, consumed in the walk's own visiting order by `curate.
    _confirm`'s validating `[y/N]` loop -- with no TTY check and no bypass
    flag anywhere in that path. Staging a `non_tty_refusal` here would
    describe a refusal this gate has never performed and, if ever wired
    up, would break that already-shipped piped usage (`tests/unit/cli/
    test_adjudicate.py` drives the walk piped today). There is likewise no
    `bypass_flag`: nothing here shortcuts an individual item's answer.

    Renders through `curate._confirm`'s validating `[y/N]` loop (module
    docstring's D2/D3), not the shared TTY-detection shape
    `boolean_confirmation` builds -- this factory only says WHAT is asked,
    matching the pre-#958 f-string byte-for-byte, so both walks share one
    source instead of re-deriving the same question (a policy re-derived
    at a call site is a policy that drifts, `TypedChallengeConfirmation`'s
    own words above)."""
    return BooleanConfirmation(
        prompt=f"Merge {absorbed_canonical} into {survivor_canonical}? [y/N]",
        bypass_flag=None,
        non_tty_refusal=None,
    )


def preview_apply_same(
    root: Path,
    layout: config.WorkspaceLayout,
    index_path: Path,
    log_path: Path,
    results: Sequence[AdjudicatedCandidate],
    *,
    include_cross_source: bool = False,
    include_cross_type: bool = False,
) -> BatchApplyPreview:
    """`adjudicate --apply-same`'s entire Pass 1 (issue #918 Slice 5),
    de-presented from `cli/main.py`'s former `_run_adjudicate_apply_same`
    inline body: per SAME 2-member group, `ordered_merge_pair` then
    `prepare_one_merge`, filtering N>2 groups, cross-source pairs (#776),
    cross-type pairs (#904), and stacked-body-guardrail-crossing pairs
    (#559) into `BatchApplyPreview.skips`/`items`, and returning the typed-
    count `TypedChallengeConfirmation` whose `expected` is the eligible
    count ACTUALLY PREVIEWED -- never the raw structural eligible-group
    count, since a group that is already unresolvable when the preview is
    built is silently excluded from `previewed` and never counted (matches
    the pre-move behavior exactly).

    Performs no echo, no prompt, and never calls `sys.stdin.isatty()` --
    every rendering decision (which skip category prints which wording,
    when the zero-eligible short-circuit fires before the gate is ever
    used) stays with the adapter, which owns `typer`. Raises no
    `typer.Exit`: a `prepare_one_merge` failure during Pass 1 raises
    `PreviewMergeFailure`, carrying everything classified so far."""
    skips: list[BatchSkip] = []
    eligible_groups: list[CandidateGroup] = []
    for result in results:
        if result.verdict is not Verdict.SAME:
            continue
        group = result.candidate
        if len(group.member_ids) == 2:
            if not include_cross_source and cross_source_same_pair(
                layout.bundle_dir, group.member_ids
            ):
                survivor_id, absorbed_id, _criterion = ordered_merge_pair(
                    layout.bundle_dir, group.member_ids
                )
                skips.append(
                    CrossSourceSkip(group.member_ids, (survivor_id, absorbed_id))
                )
                continue
            survivor_id, absorbed_id, _criterion = ordered_merge_pair(
                layout.bundle_dir, group.member_ids
            )
            reason = cross_type_concern(layout.bundle_dir, (survivor_id, absorbed_id))
            if not include_cross_type and reason is not None:
                skips.append(
                    CrossTypeSkip(group.member_ids, (survivor_id, absorbed_id), reason)
                )
                continue
            eligible_groups.append(group)
        elif len(group.member_ids) > 2:
            skips.append(NGt2Skip(group))

    items: list[BatchPreviewItem] = []
    previewed: list[PreviewedPair] = []
    for group in eligible_groups:
        survivor_id, absorbed_id, survivor_criterion = ordered_merge_pair(
            layout.bundle_dir, group.member_ids
        )
        try:
            prepared = prepare_one_merge(
                root,
                layout,
                index_path,
                log_path,
                group,
                ordered_pair=(survivor_id, absorbed_id),
            )
        except (OSError, ValueError) as exc:
            partial = BatchApplyPreview(
                skips=tuple(skips),
                items=tuple(items),
                previewed=tuple(previewed),
                confirmation=_apply_same_confirmation(len(previewed)),
            )
            raise PreviewMergeFailure(exc, survivor_id, absorbed_id, partial) from exc
        if prepared is None:
            continue
        if (
            prepared.stacked_body is not None
            and prepared.stacked_body.exceeds_guardrail
        ):
            items.append(
                StackedRefusal(
                    prepared.stacked_body,
                    prepared.survivor_canonical,
                    prepared.absorbed_canonical,
                )
            )
            continue
        pair = PreviewedPair(
            group, (survivor_id, absorbed_id), survivor_criterion, prepared
        )
        items.append(pair)
        previewed.append(pair)

    return BatchApplyPreview(
        skips=tuple(skips),
        items=tuple(items),
        previewed=tuple(previewed),
        confirmation=_apply_same_confirmation(len(previewed)),
    )
