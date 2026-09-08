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
