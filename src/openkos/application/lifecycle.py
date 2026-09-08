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
own confirmation gate stays inline in the CLI adapter, unchanged, for this
slice -- see design's Interfaces/Contracts note ("unchanged fields") --
later slices (S3/S4/S5) are what wire a verb's `Prepared*.confirmation`
field to `application.consent.ConfirmationRequest`.

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
Slice Plan). `unmerge`'s confirm gate stays inline in the CLI adapter,
unchanged, exactly like `merge`'s own gate (S1's own docstring note) --
neither verb's `Prepared*` carries a `ConfirmationRequest` field; only
`forget`/`purge`/`adjudicate` (S3-S5) wire one, because only those three
gates' WORDING varies with verb-specific data.

Renders nothing, prompts nothing, never calls `sys.stdin.isatty()`, and
never imports `openkos.cli`, `typer`, `rich`, or `openkos.vcs` (the
layering invariant, `tests/unit/application/test_layering.py`) -- every
`vcs_git.*` call and every `_autocommit`/`_reject_drifted_targets`/
`_refresh_derived_after_write` call stays adapter-side (design D2/D3).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Literal

from openkos import config, fsio
from openkos.application.consent import BooleanConfirmation
from openkos.bundle import index as bundle_index
from openkos.bundle import ledger as bundle_ledger
from openkos.bundle import links as bundle_links
from openkos.bundle import log as bundle_log
from openkos.bundle import merge as bundle_merge
from openkos.bundle import provenance as bundle_provenance
from openkos.bundle import references as bundle_references
from openkos.bundle import relations as bundle_relations
from openkos.model import okf

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

    No `confirmation: ConfirmationRequest` field, unlike `ForgetPlan`/
    `PurgePlan` (S3/S4): `unmerge`'s gate is the SAME hardcoded boolean
    `merge`'s own gate is (`Proceed with these changes?`, `--auto`), never
    verb-data-conditional the way `forget`'s scope-dependent prompt or
    `purge`'s typed phrase is, so it stays inline in the CLI adapter
    exactly as `merge`'s does (this module's own top docstring)."""

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
