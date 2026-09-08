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
