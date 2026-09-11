"""Direct unit tests for `openkos.application.lifecycle` and
`openkos.application.consent` (issue #918, lifecycle slice).

Slice 1 (S1) covers `ConfirmationRequest`'s two variants (`consent.py`) and
the relocated merge core (`prepare_merge`/`merge_core`/
`merge_drift_targets`/`canonicalize_concept_id`/`resolve_concept_path`),
moved verbatim from `cli/main.py` (design C2/D5, task Phase 2). No Typer
runner and no bundle beyond a `tmp_path`-scoped fixture -- the whole point
of the application layer (ADR-0018) is that this module's behavior is
reachable without driving a CLI command. Mirrors `test_ingest.py`'s and
`test_query_service.py`'s posture for the query/ingest slices.
"""

import dataclasses
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from openkos import config
from openkos.application import consent as consent_service
from openkos.application import lifecycle as lifecycle_service
from openkos.bundle import bundle
from openkos.bundle import ledger as bundle_ledger
from openkos.bundle import log as bundle_log
from openkos.bundle import merge as bundle_merge
from openkos.model import okf
from openkos.resolution.adjudication import AdjudicatedCandidate, Verdict
from openkos.resolution.candidates import CandidateGroup, Tier

# ---------------------------------------------------------------------------
# Phase 1: the `ConfirmationRequest` union (task 1.1)
# ---------------------------------------------------------------------------


def test_boolean_confirmation_is_granted_by_the_adapters_own_value() -> None:
    """The request carries no `granted` field of its own (D2) -- the
    adapter's boolean answer is a value it holds separately and decides
    whether to act on; the request only supplies the question."""
    request = consent_service.BooleanConfirmation(
        prompt="Proceed with these changes?",
        bypass_flag="--auto",
        non_tty_refusal=(
            "openkos merge: refusing to write without confirmation -- "
            "stdin is not a TTY; re-run with --auto."
        ),
    )
    granted = True  # the adapter's own value, never a field on the request

    assert request.kind == "boolean"
    assert granted is True
    assert request.prompt == "Proceed with these changes?"


def test_typed_challenge_matches_under_exact_mode_purge_style() -> None:
    """`purge`'s confirmation phrase compares the RAW response
    (`main.py:7331`) -- `match_mode="exact"`."""
    request = consent_service.TypedChallengeConfirmation(
        prompt="Type 'purge x (3 concepts)' to proceed",
        expected="purge x (3 concepts)",
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

    assert request.matches("purge x (3 concepts)") is True
    assert request.matches("purge x (3 concepts) ") is False
    assert request.matches(" purge x (3 concepts)") is False


def test_typed_challenge_matches_under_strip_then_exact_mode_adjudicate_style() -> None:
    """`adjudicate --apply-same`'s eligible count compares
    `response.strip()` (`main.py:3057`) -- `match_mode="strip-then-exact"`."""
    request = consent_service.TypedChallengeConfirmation(
        prompt="Type the eligible count (5) to proceed",
        expected="5",
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

    assert request.matches("5") is True
    assert request.matches(" 5 ") is True
    assert request.matches("5 ") is True
    assert request.matches("4") is False
    assert request.matches("yes") is False


def test_the_two_typed_gates_diverge_on_a_whitespace_only_difference() -> None:
    """A response that differs from `expected` ONLY by surrounding
    whitespace must diverge between the two match modes -- reproducing
    `main.py:3057` (strips) and `main.py:7331` (does not) exactly. This is
    what makes `match_mode` a field on the request rather than a policy
    re-derived at each call site (design D1)."""
    purge_style = consent_service.TypedChallengeConfirmation(
        prompt="Type 'purge x (3 concepts)' to proceed",
        expected="purge x (3 concepts)",
        supplying_flag="--confirm-phrase",
        non_tty_refusal="refusing",
        mismatch_abort="aborted",
        match_mode="exact",
    )
    adjudicate_style = consent_service.TypedChallengeConfirmation(
        prompt="Type the eligible count (3) to proceed",
        expected="purge x (3 concepts)",
        supplying_flag="--confirm-count",
        non_tty_refusal="refusing",
        mismatch_abort="aborted",
        match_mode="strip-then-exact",
    )
    response_with_whitespace = "  purge x (3 concepts)  "

    assert purge_style.matches(response_with_whitespace) is False
    assert adjudicate_style.matches(response_with_whitespace) is True


def test_purge_is_the_typed_challenge_variant_with_no_bypass_representable() -> None:
    """`purge`'s request (design C1) is the TYPED-CHALLENGE variant, its
    `supplying_flag` is `--confirm-phrase`, and no `--auto` bypass is
    representable on it -- `TypedChallengeConfirmation` carries no
    `bypass_flag` field anywhere in its shape, unlike `BooleanConfirmation`."""
    purge_request: consent_service.ConfirmationRequest = (
        consent_service.TypedChallengeConfirmation(
            prompt="Type 'purge x (3 concepts)' to proceed",
            expected="purge x (3 concepts)",
            supplying_flag="--confirm-phrase",
            non_tty_refusal="refusing",
            mismatch_abort="aborted",
        )
    )

    assert purge_request.kind == "typed-challenge"
    assert isinstance(purge_request, consent_service.TypedChallengeConfirmation)
    assert purge_request.supplying_flag == "--confirm-phrase"
    field_names = {f.name for f in __import__("dataclasses").fields(purge_request)}
    assert "bypass_flag" not in field_names


# ---------------------------------------------------------------------------
# Phase 2: the relocated merge core (task 2.1)
# ---------------------------------------------------------------------------


def _workspace(root: Path) -> config.WorkspaceLayout:
    config.write_config(root)
    layout = config.WorkspaceLayout(root)
    bundle.create(layout.bundle_dir, date(2026, 1, 1))
    return layout


def _write_concept(
    bundle_dir: Path,
    concept_id: str,
    *,
    title: str,
    sensitivity: str | None = None,
    body: str = "Body.",
) -> Path:
    concept_path = bundle_dir / f"{concept_id}.md"
    concept_path.parent.mkdir(parents=True, exist_ok=True)
    metadata: dict[str, object] = {"type": "Concept", "title": title}
    if sensitivity is not None:
        metadata["sensitivity"] = sensitivity
    concept_path.write_text(
        okf.dump_frontmatter(metadata, f"# {title}\n\n{body}\n"), encoding="utf-8"
    )
    return concept_path


def test_resolve_concept_path_and_canonicalize_concept_id_are_directly_callable(
    tmp_path: Path,
) -> None:
    """Both id-resolution helpers are reachable without any `openkos.cli`
    import, and their existing path-safety behavior is preserved verbatim
    (design D5)."""
    layout = _workspace(tmp_path)
    _write_concept(layout.bundle_dir, "concepts/a", title="A")

    canonical = lifecycle_service.canonicalize_concept_id("concepts/a.md")
    assert canonical == "concepts/a"

    path, resolved_canonical = lifecycle_service.resolve_concept_path(
        layout.bundle_dir, "concepts/a"
    )
    assert path == layout.bundle_dir / "concepts" / "a.md"
    assert resolved_canonical == "concepts/a"


def test_canonicalize_concept_id_refuses_a_path_traversal_escape() -> None:
    """Threat matrix: path-traversal deletion -- a `..` segment must be
    refused before any filesystem read is attempted."""
    with pytest.raises(ValueError, match=r"\.\."):
        lifecycle_service.canonicalize_concept_id("../outside")


def test_resolve_concept_path_refuses_a_symlink_escaping_the_bundle(
    tmp_path: Path,
) -> None:
    """Threat matrix: path-traversal deletion -- a symlinked segment
    pointing outside `bundle_dir` must be refused (issue #926), reported
    ahead of any "does not exist" absence check."""
    layout = _workspace(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    victim = outside / "secret.md"
    victim.write_text(
        okf.dump_frontmatter({"type": "Concept", "title": "Secret"}, "# Secret\n"),
        encoding="utf-8",
    )
    (layout.bundle_dir / "area").symlink_to(outside)

    with pytest.raises(ValueError, match="symlink"):
        lifecycle_service.resolve_concept_path(layout.bundle_dir, "area/secret")


def test_prepare_merge_and_merge_core_are_directly_callable(tmp_path: Path) -> None:
    """`prepare_merge`/`merge_core` are callable by a module that imports
    nothing from `openkos.cli`, and produce the same `PreparedMerge`/
    `MergeResult` shape the CLI command already exercised (spec: Non-CLI
    Callable Lifecycle Composition)."""
    layout = _workspace(tmp_path)
    _write_concept(
        layout.bundle_dir, "concepts/survivor", title="Survivor", sensitivity="private"
    )
    _write_concept(
        layout.bundle_dir,
        "concepts/absorbed",
        title="Absorbed",
        sensitivity="confidential",
    )
    index_path = layout.bundle_dir / "index.md"
    log_path = layout.bundle_dir / "log.md"
    survivor_path, survivor_canonical = lifecycle_service.resolve_concept_path(
        layout.bundle_dir, "concepts/survivor"
    )
    absorbed_path, absorbed_canonical = lifecycle_service.resolve_concept_path(
        layout.bundle_dir, "concepts/absorbed"
    )
    now = datetime(2026, 1, 1, tzinfo=UTC)

    prepared = lifecycle_service.prepare_merge(
        layout.bundle_dir,
        index_path,
        log_path,
        survivor_path,
        absorbed_path,
        survivor_canonical,
        absorbed_canonical,
        tmp_path,
        now=now,
    )

    assert isinstance(prepared, lifecycle_service.PreparedMerge)
    assert prepared.sensitivity_before == "private"
    assert prepared.sensitivity_after == "confidential"
    # Phase A writes nothing.
    assert absorbed_path.exists()

    result = lifecycle_service.merge_core(
        layout.bundle_dir, index_path, log_path, prepared
    )

    assert isinstance(result, lifecycle_service.MergeResult)
    assert result.survivor_canonical == survivor_canonical
    assert result.absorbed_canonical == absorbed_canonical
    assert not absorbed_path.exists()
    assert "## Merged content" in survivor_path.read_text(encoding="utf-8")
    assert (tmp_path / result.ledger_sidecar_path).is_file()


def test_merge_drift_targets_builds_the_guard_mapping(tmp_path: Path) -> None:
    """`merge_drift_targets` builds the same drift-guard baseline mapping
    (issue #334) the CLI adapter hands to `_reject_drifted_targets`."""
    layout = _workspace(tmp_path)
    _write_concept(layout.bundle_dir, "concepts/survivor", title="Survivor")
    _write_concept(layout.bundle_dir, "concepts/absorbed", title="Absorbed")
    index_path = layout.bundle_dir / "index.md"
    log_path = layout.bundle_dir / "log.md"
    survivor_path, survivor_canonical = lifecycle_service.resolve_concept_path(
        layout.bundle_dir, "concepts/survivor"
    )
    absorbed_path, absorbed_canonical = lifecycle_service.resolve_concept_path(
        layout.bundle_dir, "concepts/absorbed"
    )
    prepared = lifecycle_service.prepare_merge(
        layout.bundle_dir,
        index_path,
        log_path,
        survivor_path,
        absorbed_path,
        survivor_canonical,
        absorbed_canonical,
        tmp_path,
        now=datetime(2026, 1, 1, tzinfo=UTC),
    )

    targets = lifecycle_service.merge_drift_targets(layout, prepared)

    assert targets[index_path] == prepared.index_bytes
    assert targets[log_path] == prepared.log_bytes
    assert targets[survivor_path] == prepared.survivor_bytes
    assert targets[absorbed_path] == prepared.absorbed_bytes


# ---------------------------------------------------------------------------
# Phase 4: unmerge's write-only core (task 4.1, Slice S2a)
# ---------------------------------------------------------------------------


def test_unmerge_core_is_directly_callable_and_restores_the_pre_merge_state(
    tmp_path: Path,
) -> None:
    """`unmerge_core` is reachable without any `openkos.cli` import, and
    restores the survivor/absorbed files, the catalog, and the ledger
    sidecar to their pre-merge state -- built against a `PreparedUnmerge`-
    shaped fixture assembled from the same write inputs
    `_execute_single_unmerge`'s write-only body reads today (design
    C2/Slice S2a; Phase A, the preview, the confirm gate, and the
    post-confirm drift guard all stay adapter-side this slice, unlike
    `merge`'s single-slice `prepare_merge`/`merge_core` pair)."""
    layout = _workspace(tmp_path)
    survivor_path = _write_concept(
        layout.bundle_dir, "concepts/survivor", title="Survivor", sensitivity="private"
    )
    absorbed_path = _write_concept(
        layout.bundle_dir,
        "concepts/absorbed",
        title="Absorbed",
        sensitivity="confidential",
    )
    index_path = layout.bundle_dir / "index.md"
    log_path = layout.bundle_dir / "log.md"
    now = datetime(2026, 1, 1, tzinfo=UTC)

    merge_prepared = lifecycle_service.prepare_merge(
        layout.bundle_dir,
        index_path,
        log_path,
        survivor_path,
        absorbed_path,
        "concepts/survivor",
        "concepts/absorbed",
        tmp_path,
        now=now,
    )
    lifecycle_service.merge_core(
        layout.bundle_dir, index_path, log_path, merge_prepared
    )
    assert not absorbed_path.exists()

    # Mirrors `_execute_single_unmerge`'s Phase A, unchanged this slice:
    # read the ledger, plan the reversal, and compute the post-restore log
    # entry -- exactly the inputs the write-only body reads today.
    entries = bundle_ledger.read_entries("concepts/survivor", layout.bundle_dir)
    current_index_text = index_path.read_text(encoding="utf-8")
    current_log_text = log_path.read_text(encoding="utf-8")
    plan = bundle_merge.plan_unmerge(
        survivor_id="concepts/survivor",
        absorbed_id="concepts/absorbed",
        entries=entries,
        current_index_text=current_index_text,
        current_log_text=current_log_text,
    )
    new_log_text = bundle_log.insert_log_entry(
        plan.restored_log,
        now.astimezone().date(),
        "**Unmerge**: Restored [concepts/absorbed](/concepts/absorbed.md) "
        "from [concepts/survivor](/concepts/survivor.md).",
    )
    prepared = lifecycle_service.PreparedUnmerge(
        confirmation=consent_service.boolean_confirmation("unmerge"),
        plan=plan,
        new_log_text=new_log_text,
        link_reversed_texts={},
        relation_reversed_texts={},
        provenance_restored_texts={},
        rewritten_files=[],
        relation_rewrite_files=[],
        provenance_rewrite_files=[],
        survivor_path=survivor_path,
        survivor_canonical="concepts/survivor",
        absorbed_canonical="concepts/absorbed",
        # Slice S2b's fields: `unmerge_core` never reads any of these (they
        # feed only the adapter's preview/drift-guard, assembled by S2b's
        # `prepare_unmerge`) -- placeholder values are enough to satisfy the
        # dataclass shape for this Phase-B-only fixture.
        catalog_log_drifted=False,
        review=True,
        index_bytes=b"",
        log_bytes=b"",
        survivor_bytes=b"",
        rewrite_bytes={},
    )

    result = lifecycle_service.unmerge_core(layout, prepared)

    assert isinstance(result, lifecycle_service.UnmergeResult)
    assert result.survivor_canonical == "concepts/survivor"
    assert result.absorbed_canonical == "concepts/absorbed"
    assert absorbed_path.is_file()
    survivor_text = survivor_path.read_text(encoding="utf-8")
    assert "merged_from" not in survivor_text
    assert bundle_ledger.read_entries("concepts/survivor", layout.bundle_dir) == []
    assert "bundle/index.md" in result.committed_paths
    assert "bundle/concepts/absorbed.md" in result.committed_paths
    assert "bundle/concepts/survivor.md" in result.committed_paths


def test_unmerge_core_performs_no_typer_or_stdin_access() -> None:
    """`unmerge_core` never touches `sys.stdin` or `typer` -- the layering
    invariant `test_layering.py` enforces at module scope (design D2/D3),
    exercised directly against the relocated function itself (task 4.1)."""
    import inspect

    source = inspect.getsource(lifecycle_service.unmerge_core)

    assert "typer" not in source
    assert "sys.stdin" not in source


# ---------------------------------------------------------------------------
# Phase 6: the missing Phase A -- `prepare_unmerge` (task 6.1, Slice S2b)
# ---------------------------------------------------------------------------


def test_prepare_unmerge_is_directly_callable_and_feeds_unmerge_core(
    tmp_path: Path,
) -> None:
    """`prepare_unmerge` completes the Phase A/B split S2a left partial
    (design C2/Slice S2b): reachable without any `openkos.cli` import,
    writes nothing, and its `PreparedUnmerge` feeds `unmerge_core` directly
    -- the same full `prepare_X`/`X_core` pair `merge` already has (design's
    Slice Plan, "unmerge matches merge's public prepare/core pair")."""
    layout = _workspace(tmp_path)
    survivor_path = _write_concept(
        layout.bundle_dir, "concepts/survivor", title="Survivor", sensitivity="private"
    )
    absorbed_path = _write_concept(
        layout.bundle_dir,
        "concepts/absorbed",
        title="Absorbed",
        sensitivity="confidential",
    )
    index_path = layout.bundle_dir / "index.md"
    log_path = layout.bundle_dir / "log.md"
    now = datetime(2026, 1, 1, tzinfo=UTC)
    cfg = config.read_config(tmp_path)

    merge_prepared = lifecycle_service.prepare_merge(
        layout.bundle_dir,
        index_path,
        log_path,
        survivor_path,
        absorbed_path,
        "concepts/survivor",
        "concepts/absorbed",
        tmp_path,
        now=now,
    )
    lifecycle_service.merge_core(
        layout.bundle_dir, index_path, log_path, merge_prepared
    )
    assert not absorbed_path.exists()

    prepared = lifecycle_service.prepare_unmerge(
        tmp_path,
        layout,
        survivor_path,
        "concepts/survivor",
        "concepts/absorbed",
        now=now,
        cfg=cfg,
    )

    assert isinstance(prepared, lifecycle_service.PreparedUnmerge)
    assert prepared.survivor_path == survivor_path
    assert prepared.survivor_canonical == "concepts/survivor"
    assert prepared.absorbed_canonical == "concepts/absorbed"
    assert prepared.catalog_log_drifted is False
    assert prepared.review is cfg.review
    assert prepared.rewritten_files == []
    assert prepared.relation_rewrite_files == []
    assert prepared.provenance_rewrite_files == []
    # Phase A writes nothing.
    assert not absorbed_path.exists()

    result = lifecycle_service.unmerge_core(layout, prepared)

    assert isinstance(result, lifecycle_service.UnmergeResult)
    assert absorbed_path.is_file()
    survivor_text = survivor_path.read_text(encoding="utf-8")
    assert "merged_from" not in survivor_text


def test_prepare_unmerge_flags_catalog_log_drift_since_the_merge(
    tmp_path: Path,
) -> None:
    """`catalog_log_drifted` is `True` (triangulation) when `index.md` no
    longer matches what THIS merge deterministically left there -- e.g. an
    unrelated `ingest`/`forget`/`merge` touched the catalog afterwards
    (design: Interfaces/Contracts, `catalog_log_drifted`; mirrors
    `_execute_single_unmerge`'s own warn-and-continue notice, #758).

    `plan_merge` always writes a `MERGE_LEDGER_SCHEMA_V5` (delta) entry
    today, and `_expected_post_merge_index_and_log` returns `None`
    unconditionally for one -- the drift notice can only ever fire for a
    pre-#758 snapshot-shaped entry (`test_unmerge.py`'s own
    `test_unmerge_snapshot_entry_still_warns_on_interleaved_drift` pins
    the identical CLI-level scenario), so this downgrades the just-written
    V5 entry to V4 the same way, directly through `bundle_ledger`."""
    layout = _workspace(tmp_path)
    survivor_path = _write_concept(
        layout.bundle_dir, "concepts/survivor", title="Survivor"
    )
    absorbed_path = _write_concept(
        layout.bundle_dir, "concepts/absorbed", title="Absorbed"
    )
    index_path = layout.bundle_dir / "index.md"
    log_path = layout.bundle_dir / "log.md"
    now = datetime(2026, 1, 1, tzinfo=UTC)
    cfg = config.read_config(tmp_path)
    pre_merge_index = index_path.read_text(encoding="utf-8")
    pre_merge_log = log_path.read_text(encoding="utf-8")

    merge_prepared = lifecycle_service.prepare_merge(
        layout.bundle_dir,
        index_path,
        log_path,
        survivor_path,
        absorbed_path,
        "concepts/survivor",
        "concepts/absorbed",
        tmp_path,
        now=now,
    )
    lifecycle_service.merge_core(
        layout.bundle_dir, index_path, log_path, merge_prepared
    )

    entries = bundle_ledger.read_entries("concepts/survivor", layout.bundle_dir)
    assert entries[-1].schema == okf.MERGE_LEDGER_SCHEMA_V5
    downgraded = dataclasses.replace(
        entries[-1],
        schema=okf.MERGE_LEDGER_SCHEMA_V4,
        index_before=pre_merge_index,
        log_before=pre_merge_log,
        index_restores=[],
    )
    bundle_ledger.write_entries(
        "concepts/survivor",
        layout.bundle_dir,
        survivor_id="concepts/survivor",
        entries=[*entries[:-1], downgraded],
    )

    # An unrelated catalog edit landing after the merge -- an `ingest`'s
    # fresh bullet is the design's own example scenario.
    index_path.write_text(
        index_path.read_text(encoding="utf-8") + "- [Extra](/extra.md)\n",
        encoding="utf-8",
    )

    prepared = lifecycle_service.prepare_unmerge(
        tmp_path,
        layout,
        survivor_path,
        "concepts/survivor",
        "concepts/absorbed",
        now=now,
        cfg=cfg,
    )

    assert prepared.catalog_log_drifted is True


def test_prepare_unmerge_refuses_when_a_file_already_sits_at_the_absorbed_path(
    tmp_path: Path,
) -> None:
    """Threat matrix: Unmerge restore collision -- Phase A refuses
    (`ValueError`) before any write when a file already exists at the
    absorbed concept's path, since the recreate write below is create-only
    and cannot silently overwrite it."""
    layout = _workspace(tmp_path)
    survivor_path = _write_concept(
        layout.bundle_dir, "concepts/survivor", title="Survivor"
    )
    absorbed_path = _write_concept(
        layout.bundle_dir, "concepts/absorbed", title="Absorbed"
    )
    index_path = layout.bundle_dir / "index.md"
    log_path = layout.bundle_dir / "log.md"
    now = datetime(2026, 1, 1, tzinfo=UTC)
    cfg = config.read_config(tmp_path)

    merge_prepared = lifecycle_service.prepare_merge(
        layout.bundle_dir,
        index_path,
        log_path,
        survivor_path,
        absorbed_path,
        "concepts/survivor",
        "concepts/absorbed",
        tmp_path,
        now=now,
    )
    lifecycle_service.merge_core(
        layout.bundle_dir, index_path, log_path, merge_prepared
    )
    # A file lands back at the absorbed path before `unmerge` ever runs.
    absorbed_path.write_text("collision", encoding="utf-8")

    with pytest.raises(ValueError, match="already exists"):
        lifecycle_service.prepare_unmerge(
            tmp_path,
            layout,
            survivor_path,
            "concepts/survivor",
            "concepts/absorbed",
            now=now,
            cfg=cfg,
        )


def test_prepare_unmerge_performs_no_typer_or_stdin_access() -> None:
    """`prepare_unmerge` never touches `sys.stdin` or `typer` -- the
    layering invariant `test_layering.py` enforces at module scope (design
    D2/D3), exercised directly against the relocated function itself
    (task 6.1)."""
    import inspect

    source = inspect.getsource(lifecycle_service.prepare_unmerge)

    assert "typer" not in source
    assert "sys.stdin" not in source


def test_unwind_step_preview_lines_mirrors_the_per_step_preview_for_a_v4_entry() -> (
    None
):
    """`unwind_step_preview_lines` (task 6.1) reproduces the exact
    `  ~ `/`  + ` preview block `_execute_single_unmerge`'s own per-step
    preview builds from the SAME three-way partitioned rewrite-file sets
    (provenance > relations > links, design D5 generalized) -- the
    whole-plan `--to` preview and the per-step execution preview must name
    the same files the same way, for a pre-#758 (V4) snapshot-shaped
    entry."""
    entry = okf.MergeLedgerEntry(
        schema=okf.MERGE_LEDGER_SCHEMA_V4,
        merged_at=datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
        absorbed_id="concepts/absorbed",
        absorbed_snapshot="absorbed snapshot",
        survivor_before="survivor before",
        index_before="index before",
        log_before="log before",
        link_rewrites=[
            okf.LinkRewrite(file="a.md", old_link="x", new_link="y", offset=0)
        ],
        sensitivity_before="private",
        sensitivity_after="private",
        relation_rewrites=[okf.RelationRewrite(file="b.md", snapshot="b snapshot")],
    )

    lines = lifecycle_service.unwind_step_preview_lines(entry, "concepts/survivor")

    assert lines == [
        "  ~ bundle/a.md (reverse inbound link rewrite)",
        "  ~ bundle/b.md (restore pre-merge relations snapshot)",
        "  ~ index.md (restore pre-merge contents)",
        "  ~ log.md (restore pre-merge contents, append unmerge entry)",
        "  ~ bundle/concepts/survivor.md (restore pre-merge contents)",
        "  + bundle/concepts/absorbed.md (restore)",
    ]


def test_unwind_step_preview_lines_names_the_catalog_delta_for_a_v5_entry() -> None:
    """Triangulation: a `MERGE_LEDGER_SCHEMA_V5` entry (#758) reverses only
    THIS merge's own catalog/log edit, so the index/log lines read
    differently -- `unwind_step_preview_lines` must pick the branch from
    `entry.schema`, not always the pre-#758 wording."""
    entry = okf.MergeLedgerEntry(
        schema=okf.MERGE_LEDGER_SCHEMA_V5,
        merged_at=datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
        absorbed_id="concepts/absorbed",
        absorbed_snapshot="absorbed snapshot",
        survivor_before="survivor before",
        index_before="",
        log_before="",
        link_rewrites=[],
        sensitivity_before="private",
        sensitivity_after="private",
    )

    lines = lifecycle_service.unwind_step_preview_lines(entry, "concepts/survivor")

    assert lines == [
        "  ~ index.md (restore this merge's catalog entry)",
        "  ~ log.md (remove this merge's entry, append unmerge)",
        "  ~ bundle/concepts/survivor.md (restore pre-merge contents)",
        "  + bundle/concepts/absorbed.md (restore)",
    ]


# Phase 8: `ForgetPlan`/`ReferenceDisclosure` (S3, task 8.1)
# ---------------------------------------------------------------------------


def test_prepare_forget_and_forget_core_are_directly_callable(tmp_path: Path) -> None:
    """`prepare_forget`/`forget_core` are callable by a module that imports
    nothing from `openkos.cli`, and reproduce `forget`'s own scope-`self`
    shape: no inbound references, a single-member purge set, and the
    verbatim S2a confirmation prompt (spec: Non-CLI Callable Lifecycle
    Composition; design decision 6, byte-identity)."""
    layout = _workspace(tmp_path)
    _write_concept(layout.bundle_dir, "concepts/target", title="Target")
    now = datetime(2026, 1, 1, tzinfo=UTC)
    cfg = config.read_config(tmp_path)

    plan = lifecycle_service.prepare_forget(
        tmp_path, layout, "concepts/target", scope="self", now=now, cfg=cfg
    )

    assert isinstance(plan, lifecycle_service.ForgetPlan)
    assert plan.purge_ids == ["concepts/target"]
    assert plan.total_removed == 0  # never ingested via `index.md`, nothing to drop
    assert plan.references == ()
    assert plan.resurrection_pairs == ()
    assert plan.surviving_refs == 0
    assert plan.unverifiable_refs == 0
    assert isinstance(plan.confirmation, consent_service.BooleanConfirmation)
    assert plan.confirmation.prompt == "Proceed with these changes?"
    assert plan.confirmation.bypass_flag == "--auto"
    assert plan.confirmation.non_tty_refusal == (
        "openkos forget: refusing to write without confirmation -- stdin "
        "is not a TTY; re-run with --auto."
    )
    concept_path = layout.bundle_dir / "concepts" / "target.md"
    assert concept_path.exists()  # Phase A writes nothing

    result = lifecycle_service.forget_core(layout, plan)

    assert isinstance(result, lifecycle_service.ForgetResult)
    assert not concept_path.exists()
    log_text = (layout.bundle_dir / "log.md").read_text(encoding="utf-8")
    assert "Tombstone" in log_text
    assert "concepts/target" in log_text


def test_prepare_forget_scope_source_aggregates_references_in_insertion_order(
    tmp_path: Path,
) -> None:
    """`--scope source`'s cascade aggregates inbound references per
    `(member, referrer, kind, relation type)` (#567), preserving the
    first-seen order across the purge set's own (sorted) member walk --
    `ForgetPlan.references` is what `forget`'s adapter renders verbatim, one
    line per tuple entry, a referrer linking twice becoming ONE line with
    `count=2` rather than two identical lines."""
    layout = _workspace(tmp_path)
    _write_concept(layout.bundle_dir, "concepts/root", title="Root")
    child_path = layout.bundle_dir / "concepts" / "child.md"
    child_path.write_text(
        okf.dump_frontmatter(
            {
                "type": "Concept",
                "title": "Child",
                "provenance": ["concepts/root"],
            },
            "Body.\n",
        ),
        encoding="utf-8",
    )
    referrer_a = layout.bundle_dir / "concepts" / "referrer-a.md"
    referrer_a.write_text(
        okf.dump_frontmatter(
            {"type": "Concept", "title": "Referrer A"},
            "See [root](/concepts/root.md) and again [root](/concepts/root.md).\n",
        ),
        encoding="utf-8",
    )
    referrer_b = layout.bundle_dir / "concepts" / "referrer-b.md"
    referrer_b.write_text(
        okf.dump_frontmatter(
            {
                "type": "Concept",
                "title": "Referrer B",
                "relations": [{"target": "concepts/child", "type": "mentions"}],
            },
            "Body.\n",
        ),
        encoding="utf-8",
    )
    now = datetime(2026, 1, 1, tzinfo=UTC)
    cfg = config.read_config(tmp_path)

    plan = lifecycle_service.prepare_forget(
        tmp_path, layout, "concepts/root", scope="source", now=now, cfg=cfg
    )

    assert plan.purge_ids == ["concepts/child", "concepts/root"]  # sorted closure
    assert plan.references == (
        lifecycle_service.ReferenceDisclosure(
            member="concepts/child",
            referrer_id="concepts/referrer-b",
            kind="relation",
            relation_type="mentions",
            count=1,
        ),
        lifecycle_service.ReferenceDisclosure(
            member="concepts/root",
            referrer_id="concepts/referrer-a",
            kind="link",
            relation_type=None,
            count=2,
        ),
    )
    # `surviving_refs` counts every individual occurrence (Gate 1's own
    # input), not the aggregated line count above -- 2 link occurrences
    # plus 1 relation, matching `all_refs`'s per-occurrence tally.
    assert plan.surviving_refs == 3
    assert plan.unverifiable_refs == 0
    assert plan.confirmation.prompt == "Delete 2 concepts?"


def test_prepare_forget_confirmation_prompt_differs_by_scope(tmp_path: Path) -> None:
    """`forget`'s confirmation prompt is scope-conditional (design table):
    `--scope source` names the delete COUNT; `--scope self` keeps S2a's
    verbatim `"Proceed with these changes?"` text (design decision 6,
    byte-identity)."""
    layout = _workspace(tmp_path)
    _write_concept(layout.bundle_dir, "concepts/self-target", title="Self target")
    now = datetime(2026, 1, 1, tzinfo=UTC)
    cfg = config.read_config(tmp_path)

    self_plan = lifecycle_service.prepare_forget(
        tmp_path, layout, "concepts/self-target", scope="self", now=now, cfg=cfg
    )

    assert self_plan.confirmation.prompt == "Proceed with these changes?"
    assert isinstance(self_plan.confirmation, consent_service.BooleanConfirmation)


def test_forget_core_carries_the_exact_unlink_count_without_probing_the_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A mid-cascade unlink failure raises `PartialForgetWrite` carrying the
    number of members ALREADY unlinked, counted in the loop rather than
    re-derived from the filesystem afterwards.

    `fsio.remove_file` is replaced by a stub that deletes NOTHING and fails
    on the second call. Every concept file therefore still exists when the
    exception surfaces, so a filesystem-derived count would report 0 --
    only a counter incremented after each successful unlink reports the
    true 1. That gap is the whole point: probing `Path.exists()` in the
    handler is both inexact and unsafe, because `Path.exists()` re-raises
    `EACCES` (see `cli.main._purge_store_is_gone`) and would replace the
    operator's K-of-N diagnosis with a traceback in exactly the permission
    failure that opened the handler."""
    layout = _workspace(tmp_path)
    for slug in ("concepts/a", "concepts/b", "concepts/c"):
        _write_concept(layout.bundle_dir, slug, title=slug.split("/")[1].upper())
    cfg = config.read_config(tmp_path)
    plan = lifecycle_service.prepare_forget(
        tmp_path,
        layout,
        "concepts/a",
        scope="self",
        now=datetime(2026, 1, 1, tzinfo=UTC),
        cfg=cfg,
    )
    plan = dataclasses.replace(
        plan, purge_ids=["concepts/a", "concepts/b", "concepts/c"]
    )

    calls: list[Path] = []

    def _never_deletes(path: Path) -> None:
        calls.append(path)
        if len(calls) == 2:
            raise OSError("simulated delete failure on 2nd unlink")

    monkeypatch.setattr("openkos.fsio.remove_file", _never_deletes)

    with pytest.raises(lifecycle_service.PartialForgetWrite) as caught:
        lifecycle_service.forget_core(layout, plan)

    assert caught.value.unlinked_count == 1
    # The stub deleted nothing, so the disk cannot tell you that.
    for slug in ("a", "b", "c"):
        assert (layout.bundle_dir / "concepts" / f"{slug}.md").exists()
    # The error line stays byte-identical to the cause's own text, so the
    # adapter's "failed while writing the forget -- {exc}." is unchanged.
    assert str(caught.value) == "simulated delete failure on 2nd unlink"


# ---------------------------------------------------------------------------
# Phase 10: `PurgePlan`/`PurgeDisclosure` (task 10.1)
# ---------------------------------------------------------------------------


def test_purge_confirm_phrase_self_scope_names_only_the_root() -> None:
    """`--scope self` names only the root concept -- an operator typing it
    can never mistake it for a wider cascade confirmation."""
    phrase = lifecycle_service.purge_confirm_phrase(
        "concepts/a", ["concepts/a"], "self"
    )

    assert phrase == "purge concepts/a"


def test_purge_confirm_phrase_source_scope_names_the_cascade_count() -> None:
    """`--scope source` names the delete COUNT so an operator cannot type
    the self-scope phrase by habit and unknowingly confirm a larger
    cascade (design: Typed Confirmation)."""
    phrase = lifecycle_service.purge_confirm_phrase(
        "concepts/a", ["concepts/a", "concepts/b", "concepts/c"], "source"
    )

    assert phrase == "purge concepts/a (3 concepts)"


def test_prepare_purge_self_scope_is_directly_callable(tmp_path: Path) -> None:
    """`prepare_purge` is callable by a module that imports nothing from
    `openkos.cli`, and reproduces `purge`'s own scope-`self` shape: a
    single-member purge set, no inbound references, a `raw_absence`
    disclosure (a derived concept has no `resource`), and a
    `TypedChallengeConfirmation` with no `--auto` bypass field anywhere on
    it (design C1: purge's gate is a typed phrase, never a boolean)."""
    layout = _workspace(tmp_path)
    _write_concept(layout.bundle_dir, "concepts/target", title="Target")
    now = datetime(2026, 1, 1, tzinfo=UTC)

    plan = lifecycle_service.prepare_purge(
        tmp_path, layout, "concepts/target", scope="self", now=now
    )

    assert isinstance(plan, lifecycle_service.PurgePlan)
    assert plan.canonical_id == "concepts/target"
    assert plan.purge_ids == ["concepts/target"]
    assert plan.verified_refs == 0
    assert plan.unverifiable_refs == 0
    assert isinstance(plan.disclosure, lifecycle_service.PurgeDisclosure)
    assert plan.disclosure.expunge_targets == ("bundle/concepts/target.md",)
    assert plan.disclosure.resource_warnings == ()
    assert plan.disclosure.raw_absence is True
    assert plan.disclosure.cascade_total is None  # never rendered for --scope self
    assert isinstance(plan.confirmation, consent_service.TypedChallengeConfirmation)
    assert plan.confirmation.supplying_flag == "--confirm-phrase"
    assert plan.confirmation.match_mode == "exact"
    assert not hasattr(plan.confirmation, "bypass_flag")
    assert plan.confirmation.expected == lifecycle_service.purge_confirm_phrase(
        "concepts/target", ["concepts/target"], "self"
    )
    assert plan.confirmation.prompt == "Type 'purge concepts/target' to proceed"
    concept_path = layout.bundle_dir / "concepts" / "target.md"
    assert concept_path.exists()  # Phase A writes nothing, deletes nothing


def test_prepare_purge_scope_source_expands_the_cascade_and_reports_the_total(
    tmp_path: Path,
) -> None:
    """`--scope source` expands the purge set via the SAME provenance
    closure `forget --scope source` uses, and the disclosure's
    `cascade_total` names the FULL set size (source scope only)."""
    layout = _workspace(tmp_path)
    _write_concept(layout.bundle_dir, "concepts/root", title="Root")
    child_path = layout.bundle_dir / "concepts" / "child.md"
    child_path.parent.mkdir(parents=True, exist_ok=True)
    child_path.write_text(
        okf.dump_frontmatter(
            {
                "type": "Concept",
                "title": "Child",
                "provenance": ["concepts/root"],
            },
            "Body.\n",
        ),
        encoding="utf-8",
    )
    now = datetime(2026, 1, 1, tzinfo=UTC)

    plan = lifecycle_service.prepare_purge(
        tmp_path, layout, "concepts/root", scope="source", now=now
    )

    assert plan.purge_ids == ["concepts/child", "concepts/root"]  # sorted closure
    assert plan.disclosure.cascade_total == 2
    assert set(plan.disclosure.expunge_targets) == {
        "bundle/concepts/child.md",
        "bundle/concepts/root.md",
    }
    assert plan.confirmation.expected == "purge concepts/root (2 concepts)"
    assert (
        plan.confirmation.prompt == "Type 'purge concepts/root (2 concepts)' to proceed"
    )


def test_prepare_purge_resolves_a_sources_raw_material(tmp_path: Path) -> None:
    """A Source with a valid `resource: raw/<name>` frontmatter contributes
    its raw path to `expunge_targets`, and the disclosure reports
    `raw_absence=False` -- the true-erasure counterpart to `forget`, which
    never touches raw source material at all."""
    layout = _workspace(tmp_path)
    raw_path = layout.raw_dir / "notes.txt"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text("raw notes\n", encoding="utf-8")
    source_path = layout.bundle_dir / "concepts" / "source.md"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text(
        okf.dump_frontmatter(
            {"type": "Source", "title": "Source", "resource": "raw/notes.txt"},
            "Body.\n",
        ),
        encoding="utf-8",
    )
    now = datetime(2026, 1, 1, tzinfo=UTC)

    plan = lifecycle_service.prepare_purge(
        tmp_path, layout, "concepts/source", scope="self", now=now
    )

    assert plan.disclosure.raw_absence is False
    assert "raw/notes.txt" in plan.disclosure.expunge_targets
    assert plan.disclosure.resource_warnings == ()


def test_prepare_purge_warns_on_a_malformed_resource_but_still_targets_the_bundle_file(
    tmp_path: Path,
) -> None:
    """An absent/malformed `resource` is WARNED about, never refused --
    the Source's own bundle file is still targeted, and `raw_absence`
    stays `True` because nothing valid resolved."""
    layout = _workspace(tmp_path)
    source_path = layout.bundle_dir / "concepts" / "source.md"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text(
        okf.dump_frontmatter(
            {"type": "Source", "title": "Source", "resource": "../escape.txt"},
            "Body.\n",
        ),
        encoding="utf-8",
    )
    now = datetime(2026, 1, 1, tzinfo=UTC)

    plan = lifecycle_service.prepare_purge(
        tmp_path, layout, "concepts/source", scope="self", now=now
    )

    assert plan.disclosure.raw_absence is True
    assert len(plan.disclosure.resource_warnings) == 1
    assert "concepts/source" in plan.disclosure.resource_warnings[0]
    assert "bundle/concepts/source.md" in plan.disclosure.expunge_targets


def test_prepare_purge_counts_inbound_references_for_rail_one(
    tmp_path: Path,
) -> None:
    """`verified_refs`/`unverifiable_refs` are rail 1's own inputs -- a
    hard refusal independent of any human answer -- and are plain `int`s,
    never threaded through `confirmation` (design D2)."""
    layout = _workspace(tmp_path)
    _write_concept(layout.bundle_dir, "concepts/target", title="Target")
    referrer_path = layout.bundle_dir / "concepts" / "referrer.md"
    referrer_path.write_text(
        okf.dump_frontmatter(
            {"type": "Concept", "title": "Referrer"},
            "See [target](/concepts/target.md).\n",
        ),
        encoding="utf-8",
    )
    now = datetime(2026, 1, 1, tzinfo=UTC)

    plan = lifecycle_service.prepare_purge(
        tmp_path, layout, "concepts/target", scope="self", now=now
    )

    assert plan.verified_refs == 1
    assert plan.unverifiable_refs == 0
    for cls in (
        consent_service.BooleanConfirmation,
        consent_service.TypedChallengeConfirmation,
    ):
        field_names = {f.name for f in dataclasses.fields(cls)}
        assert "verified_refs" not in field_names
        assert "unverifiable_refs" not in field_names


def test_prepare_purge_drift_targets_carries_the_same_observation_bytes(
    tmp_path: Path,
) -> None:
    """`drift_targets` is the complete post-gate guard mapping, built from
    the SAME `fsio.snapshot_read` observation that fed the plan (issues
    #313, #318, #321) -- `index.md`, `log.md`, and the concept file, all
    byte-for-byte what is currently on disk."""
    layout = _workspace(tmp_path)
    concept_path = _write_concept(layout.bundle_dir, "concepts/target", title="Target")
    now = datetime(2026, 1, 1, tzinfo=UTC)

    plan = lifecycle_service.prepare_purge(
        tmp_path, layout, "concepts/target", scope="self", now=now
    )

    index_path = layout.bundle_dir / "index.md"
    log_path = layout.bundle_dir / "log.md"
    assert plan.drift_targets[index_path] == index_path.read_bytes()
    assert plan.drift_targets[log_path] == log_path.read_bytes()
    assert plan.drift_targets[concept_path] == concept_path.read_bytes()


def test_dropped_store_notice_and_residual_store_notice_move_unchanged(
    tmp_path: Path,
) -> None:
    """Both notices already returned `str | None` before this move (design
    D3's stated exception) and move verbatim -- `None` on an empty
    sequence, and the exact wording their `cli/main.py` originals used."""
    assert lifecycle_service.dropped_store_notice(()) is None
    assert lifecycle_service.residual_store_notice(()) is None

    vectors_db = tmp_path / "vectors.db"
    findings_db = tmp_path / "findings.db"

    dropped_text = lifecycle_service.dropped_store_notice(
        ((vectors_db, "dense retrieval degraded."),)
    )
    assert dropped_text is not None
    assert "1 derived store(s) were dropped" in dropped_text
    assert "vectors.db: dense retrieval degraded." in dropped_text

    residual_text = lifecycle_service.residual_store_notice((findings_db,))
    assert residual_text is not None
    assert "INCOMPLETE ERASURE -- 1 derived" in residual_text
    assert str(findings_db) in residual_text


# ---------------------------------------------------------------------------
# Phase 12: `adjudicate --apply-same`'s batch preview (task 12.1, Slice S5)
# ---------------------------------------------------------------------------


def _group(ids: tuple[str, ...], *, trigger: str = "stub") -> CandidateGroup:
    return CandidateGroup(
        okf_type="Concept", member_ids=ids, tier=Tier.HIGH, trigger=trigger
    )


def _same(group: CandidateGroup, *, rationale: str = "same") -> AdjudicatedCandidate:
    return AdjudicatedCandidate(
        candidate=group, verdict=Verdict.SAME, confidence=0.9, rationale=rationale
    )


def _different(group: CandidateGroup) -> AdjudicatedCandidate:
    return AdjudicatedCandidate(
        candidate=group, verdict=Verdict.DIFFERENT, confidence=0.9, rationale="diff"
    )


def test_ordered_merge_pair_picks_the_richer_body(tmp_path: Path) -> None:
    """#776: the member with the richer body survives, and the criterion
    names the rule (directly callable, no `openkos.cli` import)."""
    layout = _workspace(tmp_path)
    _write_concept(layout.bundle_dir, "concepts/short", title="Short", body="x")
    _write_concept(
        layout.bundle_dir, "concepts/long", title="Long", body="much longer body. " * 5
    )

    survivor, absorbed, criterion = lifecycle_service.ordered_merge_pair(
        layout.bundle_dir, ("concepts/short", "concepts/long")
    )

    assert survivor == "concepts/long"
    assert absorbed == "concepts/short"
    assert criterion == "richer body"


def test_ordered_merge_pair_ties_keep_ascending_id_order(tmp_path: Path) -> None:
    """Equal body length (including two unreadable members) keeps today's
    ascending-id convention, and the criterion says so."""
    layout = _workspace(tmp_path)
    _write_concept(layout.bundle_dir, "concepts/a", title="A", body="same length")
    _write_concept(layout.bundle_dir, "concepts/b", title="B", body="same length")

    survivor, absorbed, criterion = lifecycle_service.ordered_merge_pair(
        layout.bundle_dir, ("concepts/a", "concepts/b")
    )

    assert (survivor, absorbed) == ("concepts/a", "concepts/b")
    assert criterion == "id order -- equal body length"


def test_prepare_one_merge_recomputes_direction_by_default(tmp_path: Path) -> None:
    """Without a pinned `ordered_pair`, `prepare_one_merge` re-derives the
    direction from `ordered_merge_pair` -- the richer body survives."""
    layout = _workspace(tmp_path)
    _write_concept(layout.bundle_dir, "concepts/a", title="A", body="x")
    _write_concept(
        layout.bundle_dir, "concepts/b", title="B", body="much longer body. " * 5
    )
    group = _group(("concepts/a", "concepts/b"))

    prepared = lifecycle_service.prepare_one_merge(
        tmp_path,
        layout,
        layout.bundle_dir / "index.md",
        layout.bundle_dir / "log.md",
        group,
    )

    assert prepared is not None
    assert prepared.survivor_canonical == "concepts/b"
    assert prepared.absorbed_canonical == "concepts/a"


def test_prepare_one_merge_honors_a_pinned_ordered_pair(tmp_path: Path) -> None:
    """#776 review CRITICAL: a pinned `ordered_pair` overrides live
    recomputation -- Pass 2's stale-id guard depends on this."""
    layout = _workspace(tmp_path)
    _write_concept(layout.bundle_dir, "concepts/a", title="A", body="x")
    _write_concept(
        layout.bundle_dir, "concepts/b", title="B", body="much longer body. " * 5
    )
    group = _group(("concepts/a", "concepts/b"))

    prepared = lifecycle_service.prepare_one_merge(
        tmp_path,
        layout,
        layout.bundle_dir / "index.md",
        layout.bundle_dir / "log.md",
        group,
        ordered_pair=("concepts/a", "concepts/b"),
    )

    assert prepared is not None
    assert prepared.survivor_canonical == "concepts/a"
    assert prepared.absorbed_canonical == "concepts/b"


def test_prepare_one_merge_returns_none_for_an_unresolved_member(
    tmp_path: Path,
) -> None:
    """A member already absorbed by an earlier merge (or simply missing)
    resolves to `None`, never an exception."""
    layout = _workspace(tmp_path)
    _write_concept(layout.bundle_dir, "concepts/a", title="A")
    group = _group(("concepts/a", "concepts/gone"))

    prepared = lifecycle_service.prepare_one_merge(
        tmp_path,
        layout,
        layout.bundle_dir / "index.md",
        layout.bundle_dir / "log.md",
        group,
    )

    assert prepared is None


def _prepared_merge_fixture(tmp_path: Path) -> "lifecycle_service.PreparedMerge":
    layout = _workspace(tmp_path)
    _write_concept(layout.bundle_dir, "concepts/survivor", title="Survivor")
    _write_concept(layout.bundle_dir, "concepts/absorbed", title="Absorbed")
    index_path = layout.bundle_dir / "index.md"
    log_path = layout.bundle_dir / "log.md"
    survivor_path, survivor_canonical = lifecycle_service.resolve_concept_path(
        layout.bundle_dir, "concepts/survivor"
    )
    absorbed_path, absorbed_canonical = lifecycle_service.resolve_concept_path(
        layout.bundle_dir, "concepts/absorbed"
    )
    return lifecycle_service.prepare_merge(
        layout.bundle_dir,
        index_path,
        log_path,
        survivor_path,
        absorbed_path,
        survivor_canonical,
        absorbed_canonical,
        tmp_path,
        now=datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_reconcile_planned_precedence(tmp_path: Path) -> None:
    """Issue #803's precedence, at the relocated predicate: `no_reconcile`
    wins over everything; no `stacked_body` is always `False`; `reconcile`
    forces `True` when a `stacked_body` exists; otherwise the thresholds
    decide."""
    prepared = _prepared_merge_fixture(tmp_path)
    strong = dataclasses.replace(
        prepared,
        stacked_body=lifecycle_service.StackedBodyReport(
            absorbed_chars=950, merged_chars=1000
        ),
    )
    weak = dataclasses.replace(
        prepared,
        stacked_body=lifecycle_service.StackedBodyReport(
            absorbed_chars=5, merged_chars=1000
        ),
    )
    unstacked = dataclasses.replace(prepared, stacked_body=None)

    assert lifecycle_service.reconcile_planned(strong, no_reconcile=True) is False
    assert (
        lifecycle_service.reconcile_planned(
            unstacked, no_reconcile=False, reconcile=True
        )
        is False
    )
    assert (
        lifecycle_service.reconcile_planned(weak, no_reconcile=False, reconcile=True)
        is True
    )
    assert lifecycle_service.reconcile_planned(strong, no_reconcile=False) is True
    assert lifecycle_service.reconcile_planned(weak, no_reconcile=False) is False


def test_preview_apply_same_confirmation_is_strip_then_exact_typed_count(
    tmp_path: Path,
) -> None:
    """`BatchApplyPreview.confirmation.expected == str(len(previewed))` and
    `match_mode == "strip-then-exact"` (`main.py:2979`'s policy) -- the
    exact shape a `{granted: bool}` contract cannot represent."""
    layout = _workspace(tmp_path)
    _write_concept(layout.bundle_dir, "concepts/a", title="A")
    _write_concept(layout.bundle_dir, "concepts/b", title="B")
    group = _group(("concepts/a", "concepts/b"))

    preview = lifecycle_service.preview_apply_same(
        tmp_path,
        layout,
        layout.bundle_dir / "index.md",
        layout.bundle_dir / "log.md",
        [_same(group)],
    )

    assert len(preview.previewed) == 1
    assert preview.confirmation.expected == "1"
    assert preview.confirmation.match_mode == "strip-then-exact"
    assert preview.confirmation.supplying_flag == "--confirm-count"
    assert preview.confirmation.matches(" 1 ") is True
    assert preview.confirmation.matches("2") is False


def test_preview_apply_same_zero_eligible_short_circuit_is_decided_first(
    tmp_path: Path,
) -> None:
    """A batch with zero eligible SAME 2-member groups returns an empty
    `previewed` tuple and a `confirmation.expected == "0"` -- pure data,
    computed with no prompt and no TTY check, so the adapter can decide the
    "nothing to apply" short-circuit BEFORE ever touching the gate."""
    layout = _workspace(tmp_path)
    group = _group(("c", "d"))

    preview = lifecycle_service.preview_apply_same(
        tmp_path,
        layout,
        layout.bundle_dir / "index.md",
        layout.bundle_dir / "log.md",
        [_different(group)],
    )

    assert preview.previewed == ()
    assert preview.skips == ()
    assert preview.confirmation.expected == "0"


def test_preview_apply_same_skips_n_gt2_and_records_the_group(tmp_path: Path) -> None:
    """A SAME group with more than 2 members is excluded and recorded as an
    `NGt2Skip`, carrying the group so the adapter can render `_echo_n_gt2_
    skip`'s multi-line report unchanged."""
    layout = _workspace(tmp_path)
    group = _group(("x", "y", "z"))

    preview = lifecycle_service.preview_apply_same(
        tmp_path,
        layout,
        layout.bundle_dir / "index.md",
        layout.bundle_dir / "log.md",
        [_same(group)],
    )

    assert preview.previewed == ()
    assert len(preview.skips) == 1
    assert isinstance(preview.skips[0], lifecycle_service.NGt2Skip)
    assert preview.skips[0].group == group


def test_preview_apply_same_previewed_pair_ordered_pins_pass_two_direction(
    tmp_path: Path,
) -> None:
    """#776 review CRITICAL: `PreviewedPair.ordered` carries Pass 1's
    DISPLAYED direction, so Pass 2's `prepare_one_merge(ordered_pair=...)`
    never re-derives it from live (possibly enriched) file contents."""
    layout = _workspace(tmp_path)
    _write_concept(layout.bundle_dir, "concepts/a", title="A", body="x")
    _write_concept(
        layout.bundle_dir, "concepts/b", title="B", body="much longer body. " * 5
    )
    group = _group(("concepts/a", "concepts/b"))

    preview = lifecycle_service.preview_apply_same(
        tmp_path,
        layout,
        layout.bundle_dir / "index.md",
        layout.bundle_dir / "log.md",
        [_same(group)],
    )

    assert len(preview.previewed) == 1
    pair = preview.previewed[0]
    # Pass 1 displayed b -> a (richer body). Pin the OPPOSITE direction to
    # prove Pass 2 honors `ordered` rather than recomputing.
    reprepared = lifecycle_service.prepare_one_merge(
        tmp_path,
        layout,
        layout.bundle_dir / "index.md",
        layout.bundle_dir / "log.md",
        pair.group,
        ordered_pair=pair.ordered,
    )
    assert reprepared is not None
    assert (
        reprepared.survivor_canonical,
        reprepared.absorbed_canonical,
    ) == pair.ordered


def test_preview_apply_same_records_a_stacked_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Issue #559: a prepared pair whose stacked-body share crosses the
    guardrail is excluded from `previewed` and recorded as a
    `StackedRefusal` in `items` -- the typed-count gate consents to a
    batch, not to this pair.

    Exercised via a forced `PreparedMerge`: under `ordered_merge_pair`'s
    richer-body-survives rule (#776) the absorbed side can never
    legitimately dominate the merged body (absorbed is always the
    NON-richer member, so its share is capped at 0.5), so this branch is
    unreachable through realistic body lengths post-#776 and stays only
    for a caller that could pin a non-richer-body direction --
    `test_adjudicate.py`'s own
    `test_adjudicate_apply_same_formerly_dominated_pair_rides_the_batch`
    documents the identical post-#776 unreachability at the CLI layer
    (`assert "refused (stacked-body" not in result.stdout`)."""
    layout = _workspace(tmp_path)
    _write_concept(layout.bundle_dir, "concepts/a", title="A")
    _write_concept(
        layout.bundle_dir, "concepts/b", title="B", body="much longer body. " * 5
    )
    group = _group(("concepts/a", "concepts/b"))
    original_prepare_merge = lifecycle_service.prepare_merge

    def _force_stacked(*args: object, **kwargs: object) -> object:
        prepared = original_prepare_merge(*args, **kwargs)  # type: ignore[arg-type]
        return dataclasses.replace(
            prepared,
            stacked_body=lifecycle_service.StackedBodyReport(
                absorbed_chars=950, merged_chars=1000
            ),
        )

    monkeypatch.setattr(lifecycle_service, "prepare_merge", _force_stacked)

    preview = lifecycle_service.preview_apply_same(
        tmp_path,
        layout,
        layout.bundle_dir / "index.md",
        layout.bundle_dir / "log.md",
        [_same(group)],
    )

    assert preview.previewed == ()
    assert len(preview.items) == 1
    assert isinstance(preview.items[0], lifecycle_service.StackedRefusal)
    assert preview.items[0].report.exceeds_guardrail is True
    assert preview.confirmation.expected == "0"


def test_preview_apply_same_raises_preview_merge_failure_with_partial_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `prepare_merge` failure during Pass 1 raises `PreviewMergeFailure`
    (mirroring `PartialForgetWrite`'s shape) carrying the failing pair's
    identity AND everything classified before it, so the adapter can still
    render every skip/preview line that would have printed before the
    failure in the pre-move code."""
    layout = _workspace(tmp_path)
    _write_concept(layout.bundle_dir, "concepts/n1", title="N1")
    _write_concept(layout.bundle_dir, "concepts/n2", title="N2")
    _write_concept(layout.bundle_dir, "concepts/n3", title="N3")
    n_gt2_group = _group(("concepts/n1", "concepts/n2", "concepts/n3"), trigger="a")
    _write_concept(layout.bundle_dir, "concepts/a", title="A")
    _write_concept(layout.bundle_dir, "concepts/b", title="B")
    failing_group = _group(("concepts/a", "concepts/b"), trigger="b")

    def _raise(*args: object, **kwargs: object) -> object:
        raise OSError("disk full")

    monkeypatch.setattr(lifecycle_service, "prepare_merge", _raise)

    with pytest.raises(lifecycle_service.PreviewMergeFailure) as excinfo:
        lifecycle_service.preview_apply_same(
            tmp_path,
            layout,
            layout.bundle_dir / "index.md",
            layout.bundle_dir / "log.md",
            [_same(n_gt2_group), _same(failing_group)],
        )

    failure = excinfo.value
    assert isinstance(failure.survivor_id, str)
    assert isinstance(failure.absorbed_id, str)
    assert str(failure) == "disk full"
    assert len(failure.partial.skips) == 1
    assert isinstance(failure.partial.skips[0], lifecycle_service.NGt2Skip)
    assert failure.partial.previewed == ()


def test_boolean_confirmation_helper_builds_the_shared_auto_gate_shape() -> None:
    """#918: the five lifecycle verbs' boolean gates differ only in the verb
    name inside the refusal, so `consent.boolean_confirmation` owns that one
    sentence rather than letting each `prepare_*` re-spell it. A refusal
    re-spelled per call site is a refusal that drifts -- this repository has
    already shipped a silently reworded one when two display paths were
    folded together during an extraction."""
    request = consent_service.boolean_confirmation("merge")

    assert isinstance(request, consent_service.BooleanConfirmation)
    assert request.prompt == "Proceed with these changes?"
    assert request.bypass_flag == "--auto"
    assert request.non_tty_refusal == (
        "openkos merge: refusing to write without confirmation -- "
        "stdin is not a TTY; re-run with --auto."
    )


def test_merge_walk_confirmation_pins_the_null_transport_contract() -> None:
    """Issue #958: the merge walk's per-item gate is the sixth lifecycle
    gate #918 named but left un-staged pending a protocol decision. The
    decision is BOTH `None`s -- see `merge_walk_confirmation`'s own
    docstring for why, kept in one place rather than restated here.

    This pins the exact prompt wording both `_run_adjudicate_apply`
    (`cli/main.py`) and curate's Identity stage (`_identity_run`,
    `cli/curate.py`) must now render byte-for-byte -- the same string the
    pre-#958 code independently spelled at each call site -- plus the two
    `None`s that say this gate has neither an unattended bypass nor a
    non-TTY refusal of its own."""
    request = lifecycle_service.merge_walk_confirmation(
        survivor_canonical="concepts/survivor",
        absorbed_canonical="concepts/absorbed",
    )

    assert isinstance(request, consent_service.BooleanConfirmation)
    assert request.prompt == "Merge concepts/absorbed into concepts/survivor? [y/N]"
    assert request.bypass_flag is None
    assert request.non_tty_refusal is None


def test_merge_walk_confirmation_orders_absorbed_before_survivor() -> None:
    """Issue #958 correction round: `merge_walk_confirmation` takes
    `survivor_canonical`/`absorbed_canonical` but renders the ABSORBED one
    FIRST -- the opposite of parameter order, by design (the sentence
    reads naturally as "merge X into Y"). Keyword-only parameters make a
    positional call a `TypeError` instead of a silent transposition, but
    that alone does not prove the RENDERED order is still correct -- this
    pins the actual rendered order using two ids distinct enough that a
    swap cannot hide, and it must fail if the two names were ever swapped
    inside the f-string."""
    request = lifecycle_service.merge_walk_confirmation(
        survivor_canonical="concepts/keep-me",
        absorbed_canonical="concepts/drop-me",
    )

    assert request.prompt == "Merge concepts/drop-me into concepts/keep-me? [y/N]"
    assert request.prompt.index("concepts/drop-me") < request.prompt.index(
        "concepts/keep-me"
    )


def test_prepare_merge_stages_its_gate_as_data(tmp_path: Path) -> None:
    """#918's lifecycle goal names `merge` explicitly: "confirmation
    contracts expressed as data (so a non-TTY adapter can drive them)".

    `forget`, `purge` and `adjudicate --apply-same` staged theirs in slices
    3-5; `merge` and `unmerge` kept a hardcoded literal in the adapter, so
    an `api`/`mcp` caller could not learn what those gates ask or which flag
    bypasses them without reading `cli/main.py`. Both now carry it."""
    layout = _workspace(tmp_path)
    _write_concept(layout.bundle_dir, "concepts/survivor", title="Survivor")
    _write_concept(layout.bundle_dir, "concepts/absorbed", title="Absorbed")
    survivor_path, survivor_canonical = lifecycle_service.resolve_concept_path(
        layout.bundle_dir, "concepts/survivor"
    )
    absorbed_path, absorbed_canonical = lifecycle_service.resolve_concept_path(
        layout.bundle_dir, "concepts/absorbed"
    )

    prepared = lifecycle_service.prepare_merge(
        layout.bundle_dir,
        layout.bundle_dir / "index.md",
        layout.bundle_dir / "log.md",
        survivor_path,
        absorbed_path,
        survivor_canonical,
        absorbed_canonical,
        tmp_path,
        now=datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert isinstance(prepared.confirmation, consent_service.BooleanConfirmation)
    assert prepared.confirmation.prompt == "Proceed with these changes?"
    assert prepared.confirmation.bypass_flag == "--auto"
    assert prepared.confirmation.non_tty_refusal == (
        "openkos merge: refusing to write without confirmation -- "
        "stdin is not a TTY; re-run with --auto."
    )
    # D2: a gate is a question, never an answer -- no field an adapter
    # could set to "already granted".
    assert not hasattr(prepared.confirmation, "granted")
