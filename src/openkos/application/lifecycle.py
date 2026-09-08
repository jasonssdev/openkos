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

Slice 2a (S2a, issue #918) adds `unmerge`'s write-only Phase B --
`PreparedUnmerge`, `UnmergeResult`, `unmerge_core` -- relocated verbatim
from `_execute_single_unmerge`'s former inline tail (design C2: that
439-line function is Phase A, preview, confirm gate, drift guard, AND
Phase B in one body, too large for one slice, so only the write-only tail
moves here). `PreparedUnmerge` is deliberately PARTIAL this slice -- it
carries only the write inputs `unmerge_core` needs, not yet a full Phase-A
result; `unmerge`'s preview, confirm gate, and post-confirm drift guard
all stay adapter-side until S2b's `prepare_unmerge` completes the split.

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

from openkos import config, fsio
from openkos.bundle import index as bundle_index
from openkos.bundle import ledger as bundle_ledger
from openkos.bundle import links as bundle_links
from openkos.bundle import log as bundle_log
from openkos.bundle import merge as bundle_merge
from openkos.bundle import provenance as bundle_provenance
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
    """Slice S2a's PARTIAL Phase-A result: only the write inputs
    `unmerge_core` needs, assembled by `main.py`'s `_execute_single_unmerge`
    from its own still-inline preview computation (design C2/Slice S2a).
    Unlike `PreparedMerge`, this is not yet the full Phase A -- the reads,
    the preview text, the confirm gate, and the post-confirm drift guard
    all stay adapter-side this slice; S2b's `prepare_unmerge` is what makes
    this dataclass's construction itself non-interactive and moves the
    remaining fields (`catalog_log_drifted`, `review`, the drift-guard
    baselines) onto it, matching `unmerge` up to `merge`'s full
    Phase-A/Phase-B pair (design's Slice Plan).

    `survivor_path` is carried explicitly, not derived from
    `survivor_canonical` inside `unmerge_core`, for the same NFC/NFD
    reason `resolve_concept_path` reads `okf.concept_path_for` rather than
    building `bundle_dir / f"{canonical_id}.md"` itself (#430) -- the
    caller already resolved it once and `unmerge_core` must write the same
    path, not a re-derived one that could disagree on a filesystem whose
    on-disk spelling differs from the canonical id's NFC form."""

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


@dataclass(frozen=True)
class UnmergeResult:
    """Pure Phase-B result of `unmerge_core`: what got written, for the
    command's success echo and `_autocommit` path list (mirrors
    `MergeResult`). `unmerge_core` performs NO VCS side effect --
    `_autocommit` stays the command's responsibility."""

    survivor_canonical: str
    absorbed_canonical: str
    committed_paths: list[str]


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
