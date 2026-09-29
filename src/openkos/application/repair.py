"""The `repair` verb's application service (ADR-0018, okf-v02-migration
Phase 6, design.md Decision 9): `plan_repair(bundle_dir) -> RepairPlan |
RepairRefusal` (pure reads, no writes) and `apply_repair(root, plan) ->
RepairOutcome` (write-only). `cli.main.repair` stays a thin adapter over
these two: CLI parsing, the reset-point note, the post-confirm drift guard
(`_reject_drifted_targets`), printing, `_autocommit`, and
`_refresh_derived_after_write` all stay adapter-side (design D2/D3,
mirroring `application/lifecycle.py`'s own `prepare_X`/`X_core` split) --
this module never imports `openkos.cli`, `typer`, `rich`, or `openkos.vcs`
(`tests/unit/application/test_layering.py`).

`repair` is the ONE verb that migrates an existing bundle from OKF v0.1 to
v0.2 shape, on top of its pre-existing merge-ledger relocation migration.
Plan phase (every refusal happens here, before any write):

1. Gate 1 (`bundle_ledger.scan_torn_writes`): any `.pending` marker refuses
   the whole run, unconditionally (unchanged text/behavior).
2. Ledger extraction scan (`bundle_ledger.scan_unmigrated`) + the scoped
   Gate 2 (`entity-resolution-merge` delta, Decision 2): the pollution-risk
   gate (`bundle_ledger.bundle_wide_max_entries(...) >= 2`) is evaluated
   ONLY when there is pre-relocation extraction to do.
3. OKF scan: every non-reserved `.md`, via `fsio.snapshot_read` (bytes kept
   as drift baselines for every actual write target), through
   `okf.migrate_document` -- run over the POST-EXTRACTION text (`merged_from`
   already stripped in memory for an extraction target), since that is the
   real on-disk state by the time the apply phase's document-write step
   runs. Any `Refused` refuses the whole run, naming every offending
   concept id and reason. The same post-extraction texts feed
   `bundle_ledger.migrate_sidecars_to_okf_v02`'s dry run; a `ValueError`
   there (an embedded ledger snapshot that cannot be migrated
   deterministically) also refuses the whole run.
4. Bundle version: `index.md`, when present, is flip-planned when its
   `okf_version` differs from `okf.OKF_VERSION` (a bundle with no
   `index.md` plans no flip -- OKF §11 tolerance).
5. Nothing to do (no extraction, no document/sidecar whose bytes change, no
   flip) is represented as a `RepairPlan` with every field empty
   (`RepairPlan.has_work` is `False`) -- not a refusal, since the CLI exits
   0 on it, unlike every other branch above.

Apply phase writes, in this order (torn-write safety, design.md Decision 9):
ledger extraction writes -> sidecar OKF migrations -> concept documents ->
`index.md` `okf_version` flip LAST. A crash before the flip leaves
`okf_version: "0.1"` over a partially migrated bundle -- never a bundle
claiming v0.2 while holding v0.1 documents -- and each artifact is
idempotent, so a re-run completes it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from openkos import config, fsio
from openkos.bundle import ledger as bundle_ledger
from openkos.model import okf


def _strip_merged_from(text: str) -> str:
    """The extraction step's own transform, in memory: pop
    `okf.MERGED_FROM_KEY` from `text`'s frontmatter, re-dump, leave the body
    untouched. Used by `plan_repair` to compute the POST-extraction text an
    extraction target will actually hold on disk by the time the apply
    phase's later steps run, and reused verbatim by `apply_repair`'s own
    extraction write (so the two never drift apart)."""
    metadata, body = okf.load_frontmatter(text)
    new_metadata = dict(metadata)
    new_metadata.pop(okf.MERGED_FROM_KEY, None)
    return okf.dump_frontmatter(new_metadata, body)


@dataclass(frozen=True)
class RepairRefusal:
    """A whole-run refusal, computed entirely from reads (`repair` never
    guesses, design.md Decision 9): `message` is the COMPLETE user-facing
    line, including the `openkos repair: refusing to run -- ` prefix, so
    the CLI adapter only echoes it to stderr and exits 1 -- there is
    exactly one place this text is assembled."""

    message: str


@dataclass(frozen=True)
class DocumentRewrite:
    """One concept document `migrate_document` rewrote (a `Migrated`
    result) during the plan's OKF scan. `path` is absolute; `text` is the
    full new bytes `apply_repair` writes verbatim via `fsio.write_atomic`."""

    concept_id: str
    path: Path
    text: str
    changes: okf.MigrationChanges


@dataclass(frozen=True)
class RepairPlan:
    """Pure Phase-A result of `plan_repair`: everything `apply_repair` and
    the CLI's report/commit-message rendering need, built in memory without
    writing anything.

    `extraction` is `bundle_ledger.scan_unmigrated`'s own return shape
    (concept id, its frontmatter-embedded entries) -- the apply phase's
    ledger-extraction step re-reads each survivor fresh at write time
    (unchanged behavior). `sidecar_rewrites` is
    `bundle_ledger.migrate_sidecars_to_okf_v02`'s own return shape (sidecar
    path, survivor id, migrated entries). `index_new_text` is `None` when
    no `okf_version` flip is needed (already v0.2, or no `index.md` at
    all); otherwise the full re-rendered `index.md` text, body kept
    verbatim.

    `baselines` is the post-confirm drift guard's mapping (issues #306,
    #313, #318 precedent): the raw bytes every write target (extraction
    survivors, migrated documents, migrated sidecars, a flipped
    `index.md`) held at the SAME `fsio.snapshot_read` observation whose
    decoded text fed this plan -- the CLI adapter hands this straight to
    `_reject_drifted_targets` before calling `apply_repair`.
    `legacy_citations_ids` is every concept id whose `# Citations` section
    was left in place (hand-authored, non-empty, or not the document's
    true bare trailing heading), sorted, for the report's "left in place"
    line."""

    extraction: list[tuple[str, list[okf.MergeLedgerEntry]]]
    document_rewrites: list[DocumentRewrite]
    sidecar_rewrites: list[tuple[Path, str, list[okf.MergeLedgerEntry]]]
    index_path: Path
    index_new_text: str | None
    baselines: dict[Path, bytes]
    legacy_citations_ids: tuple[str, ...]

    @property
    def has_work(self) -> bool:
        """`False` exactly when there is nothing for `repair` to do
        (design.md Decision 9 step 5): the CLI prints "nothing to migrate"
        and exits 0 without calling `apply_repair`."""
        return bool(
            self.extraction
            or self.document_rewrites
            or self.sidecar_rewrites
            or self.index_new_text is not None
        )


@dataclass(frozen=True)
class RepairOutcome:
    """Pure Phase-B result of `apply_repair`: every workspace-relative
    (`bundle/...`) path actually written, in write order -- the CLI's
    `_autocommit` path list and the touched-file list its `test_repair_
    commit_message_and_exactly_one_commit` regression pins."""

    touched: list[str]


def plan_repair(bundle_dir: Path) -> RepairPlan | RepairRefusal:
    """Phase A (pure, no writes): every refusal decision, computed from
    reads alone -- see this module's own docstring for the five plan
    steps."""
    torn = bundle_ledger.scan_torn_writes(bundle_dir)
    if torn:
        return RepairRefusal(
            message=(
                "openkos repair: refusing to run -- "
                f"{len(torn)} pending marker(s) found (a prior merge crashed "
                "mid-commit); this refusal has no override. Run `openkos "
                "doctor` to inspect, or `openkos merge`/`openkos unmerge` on "
                "the affected survivor to trigger recovery."
            )
        )

    unmigrated = bundle_ledger.scan_unmigrated(bundle_dir)
    if unmigrated and bundle_ledger.bundle_wide_max_entries(bundle_dir) >= 2:
        return RepairRefusal(
            message=(
                "openkos repair: refusing to run -- at least one survivor in "
                "this bundle carries 2 or more merge-ledger entries. Migrating "
                "a possibly-corrupted ledger verbatim would convert a "
                "git-revertible bug into a permanent durable fact, so this "
                "refusal has NO override. The only path forward is `git reset "
                "--hard <first-merge>~1` followed by `openkos reindex`; "
                "reversibility of merges made before this fix is not "
                "guaranteed. Run `openkos doctor` to inspect."
            )
        )

    extraction_ids = {concept_id for concept_id, _ in unmigrated}

    baselines: dict[Path, bytes] = {}
    document_rewrites: list[DocumentRewrite] = []
    legacy_citations_ids: list[str] = []
    refused: list[tuple[str, str]] = []
    current_texts: dict[str, str] = {}

    for path in okf.iter_bundle_markdown(bundle_dir):
        if path.name in okf.RESERVED_FILENAMES:
            continue
        raw_bytes, text = fsio.snapshot_read(path)
        concept_id = okf.concept_id_for(path, bundle_dir)
        rel = path.relative_to(bundle_dir).as_posix()
        is_extraction_target = concept_id in extraction_ids
        working_text = _strip_merged_from(text) if is_extraction_target else text
        current_texts[rel] = working_text

        result = okf.migrate_document(working_text)
        if isinstance(result, okf.Refused):
            refused.append((concept_id, result.reason))
            continue
        if isinstance(result, okf.Migrated):
            document_rewrites.append(
                DocumentRewrite(
                    concept_id=concept_id,
                    path=path,
                    text=result.text,
                    changes=result.changes,
                )
            )
            baselines[path] = raw_bytes
            if result.changes.legacy_citations:
                legacy_citations_ids.append(concept_id)
        elif result.legacy_citations:
            legacy_citations_ids.append(concept_id)

        if is_extraction_target:
            baselines[path] = raw_bytes

    if refused:
        named = "; ".join(f"{cid} ({reason})" for cid, reason in sorted(refused))
        return RepairRefusal(
            message=(
                "openkos repair: refusing to run -- "
                f"{len(refused)} document(s) cannot be migrated to OKF 0.2 "
                f"deterministically: {named}. This refusal has no override. "
                "Run `openkos doctor` to inspect."
            )
        )

    try:
        sidecar_rewrites = bundle_ledger.migrate_sidecars_to_okf_v02(
            bundle_dir, current_texts=current_texts
        )
    except ValueError as exc:
        return RepairRefusal(
            message=(
                "openkos repair: refusing to run -- the merge ledger cannot "
                f"be migrated to OKF 0.2 deterministically: {exc}. This "
                "refusal has no override."
            )
        )
    for sidecar_path, _survivor_id, _entries in sidecar_rewrites:
        sidecar_bytes, _ = fsio.snapshot_read(sidecar_path)
        baselines[sidecar_path] = sidecar_bytes

    index_path = bundle_dir / "index.md"
    index_new_text: str | None = None
    if index_path.is_file():
        index_bytes, index_text = fsio.snapshot_read(index_path)
        index_metadata, index_body = okf.load_frontmatter(index_text)
        if index_metadata.get("okf_version") != okf.OKF_VERSION:
            new_index_metadata = dict(index_metadata)
            new_index_metadata["okf_version"] = okf.OKF_VERSION
            index_new_text = okf.dump_frontmatter(new_index_metadata, index_body)
            baselines[index_path] = index_bytes

    return RepairPlan(
        extraction=unmigrated,
        document_rewrites=document_rewrites,
        sidecar_rewrites=sidecar_rewrites,
        index_path=index_path,
        index_new_text=index_new_text,
        baselines=baselines,
        legacy_citations_ids=tuple(sorted(set(legacy_citations_ids))),
    )


def apply_repair(root: Path, plan: RepairPlan) -> RepairOutcome:
    """Phase B (write-only, after the CLI's post-confirm drift guard):
    writes in the exact order design.md Decision 9 requires -- ledger
    extraction, sidecar OKF migrations, concept documents, `index.md`
    flip LAST. Raises `OSError`/`ValueError` on a write failure, exactly
    like `application.lifecycle`'s own `*_core` functions; the caller is
    responsible for catching it (the CLI does, on every other mutating
    verb's precedent)."""
    bundle_dir = config.WorkspaceLayout(root).bundle_dir
    touched: list[str] = []

    for concept_id, entries in plan.extraction:
        bundle_ledger.write_entries(
            concept_id, bundle_dir, survivor_id=concept_id, entries=entries
        )
        sidecar_path = bundle_ledger.ledger_path_for(concept_id, bundle_dir)
        touched.append(f"bundle/{sidecar_path.relative_to(bundle_dir).as_posix()}")

        survivor_path = okf.concept_path_for(concept_id, bundle_dir)
        fsio.write_atomic(
            survivor_path,
            _strip_merged_from(survivor_path.read_text(encoding="utf-8")),
        )
        touched.append(f"bundle/{survivor_path.relative_to(bundle_dir).as_posix()}")

    for sidecar_path, survivor_id, entries in plan.sidecar_rewrites:
        bundle_ledger.rewrite_entries_at(
            sidecar_path, survivor_id=survivor_id, entries=entries
        )
        touched.append(f"bundle/{sidecar_path.relative_to(bundle_dir).as_posix()}")

    for rewrite in plan.document_rewrites:
        fsio.write_atomic(rewrite.path, rewrite.text)
        touched.append(f"bundle/{rewrite.path.relative_to(bundle_dir).as_posix()}")

    if plan.index_new_text is not None:
        fsio.write_atomic(plan.index_path, plan.index_new_text)
        touched.append(f"bundle/{plan.index_path.relative_to(bundle_dir).as_posix()}")

    return RepairOutcome(touched=touched)
