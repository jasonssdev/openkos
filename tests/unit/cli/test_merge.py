"""Unit tests for the `merge` CLI command: the first DESTRUCTIVE
entity-resolution write -- a confirm-gated, two-phase fusion of two
concept-ids, mirroring `forget`'s Phase A/B + confirm-gate shape (spec:
Merge Fuses Two Distinct Concept-IDs; Confirm-Gated Two-Phase Execution).

`unmerge` is a later unit (U5) and is intentionally NOT exercised here.
"""

from pathlib import Path

import pytest
from typer.testing import CliRunner, _NamedTextIOWrapper

from openkos import fsio
from openkos.bundle import index as bundle_index
from openkos.bundle import ledger as bundle_ledger
from openkos.bundle import links as bundle_links
from openkos.cli import main
from openkos.cli.main import _apply_link_rewrite_idempotently, app
from openkos.model import okf
from tests.unit.cli.conftest import changed_paths, confirm_after, echo_after
from tests.unit.cli.conftest import snapshot_with_mtime as _snapshot

runner = CliRunner()


def _ledger_entries(tmp_path: Path, survivor_id: str) -> list[okf.MergeLedgerEntry]:
    """Read `survivor_id`'s ledger sidecar (durable-derived-state slice 1a) --
    the ledger no longer lives in the survivor's own frontmatter."""
    return bundle_ledger.read_entries(survivor_id, tmp_path / "bundle")


def _simulate_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make `sys.stdin.isatty()` report `True` inside a `CliRunner.invoke` call."""
    monkeypatch.setattr(_NamedTextIOWrapper, "isatty", lambda self: True)


def _init_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0


def _write_concept(
    tmp_path: Path,
    concept_id: str,
    *,
    title: str,
    section: str = "Concepts",
    sensitivity: str | None = None,
    body: str = "Body.",
) -> None:
    """Write a concept file directly to the bundle and hand-author its
    matching `index.md` bullet via the real `bundle_index.insert_index_entry`
    (mirrors `test_forget.py::_write_hand_authored_concept`, extended with
    `sensitivity` for merge's high-water-mark recomputation)."""
    concept_path = tmp_path / "bundle" / f"{concept_id}.md"
    concept_path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---", "type: Concept", f"title: {title}"]
    if sensitivity is not None:
        lines.append(f"sensitivity: {sensitivity}")
    lines.append("---")
    lines.append("")
    lines.append(f"# {title}")
    lines.append("")
    lines.append(body)
    lines.append("")
    concept_path.write_text("\n".join(lines), encoding="utf-8")

    link_dir, slug = concept_id.rsplit("/", 1)
    index_path = tmp_path / "bundle" / "index.md"
    index_text = index_path.read_text(encoding="utf-8")
    new_index_text = bundle_index.insert_index_entry(
        index_text,
        section=section,
        link_dir=link_dir,
        title=title,
        slug=slug,
        description=f"{title}.",
    )
    index_path.write_text(new_index_text, encoding="utf-8")


def _write_concept_with_relations(
    tmp_path: Path,
    concept_id: str,
    *,
    title: str,
    relations: list[dict[str, str]] | None = None,
) -> None:
    """Write a concept file directly to the bundle via `okf.dump_frontmatter`,
    optionally carrying a `relations:` list -- deliberately NOT registered
    in `index.md` (unneeded: the merge-guard tests below fail closed before
    `index.md` is ever touched)."""
    concept_path = tmp_path / "bundle" / f"{concept_id}.md"
    concept_path.parent.mkdir(parents=True, exist_ok=True)
    metadata: dict[str, object] = {"type": "Concept", "title": title}
    if relations is not None:
        metadata["relations"] = relations
    concept_path.write_text(
        okf.dump_frontmatter(metadata, f"# {title}\n\nBody.\n"), encoding="utf-8"
    )


def _write_concept_with_provenance(
    tmp_path: Path,
    concept_id: str,
    *,
    title: str,
    concept_type: str = "Concept",
    provenance: list[str] | None = None,
) -> None:
    """Same shape as `_write_concept_with_relations`, but carries a
    `provenance:` frontmatter list -- deliberately NOT registered in
    `index.md` (unneeded: the provenance-scan tests below don't exercise
    `index.md`)."""
    concept_path = tmp_path / "bundle" / f"{concept_id}.md"
    concept_path.parent.mkdir(parents=True, exist_ok=True)
    metadata: dict[str, object] = {"type": concept_type, "title": title}
    if provenance is not None:
        metadata["provenance"] = provenance
    concept_path.write_text(
        okf.dump_frontmatter(metadata, f"# {title}\n\nBody.\n"), encoding="utf-8"
    )


def test_successful_merge_writes_ledger_rewrites_links_removes_absorbed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Happy path: the merged survivor gains the absorbed content and a
    `merged_from` ledger entry, an inbound link in a THIRD file is rewritten
    to point at the survivor, `index.md` drops the absorbed entry (but keeps
    the survivor's), a `**Merge**` line lands in `log.md`, and the absorbed
    file is removed (spec: Successful merge; Inbound-Link Rewrite)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(
        tmp_path,
        "concepts/survivor",
        title="Survivor",
        sensitivity="private",
        body="Survivor body.",
    )
    _write_concept(
        tmp_path,
        "concepts/absorbed",
        title="Absorbed",
        sensitivity="confidential",
        body="Absorbed body.",
    )
    _write_concept(
        tmp_path,
        "concepts/other",
        title="Other",
        body="See [Absorbed](/concepts/absorbed.md) for details.",
    )

    result = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )

    assert result.exit_code == 0, result.stderr

    absorbed_path = tmp_path / "bundle" / "concepts" / "absorbed.md"
    assert not absorbed_path.exists()

    survivor_text = (tmp_path / "bundle" / "concepts" / "survivor.md").read_text(
        encoding="utf-8"
    )
    assert "merged_from" not in survivor_text
    assert "## Merged content (concepts/absorbed)" in survivor_text
    assert "Absorbed body." in survivor_text
    assert "sensitivity: confidential" in survivor_text
    entries = _ledger_entries(tmp_path, "concepts/survivor")
    assert len(entries) == 1
    assert entries[0].absorbed_id == "concepts/absorbed"

    other_text = (tmp_path / "bundle" / "concepts" / "other.md").read_text(
        encoding="utf-8"
    )
    assert "/concepts/survivor.md" in other_text
    assert "/concepts/absorbed.md" not in other_text

    index_text = (tmp_path / "bundle" / "index.md").read_text(encoding="utf-8")
    assert "concepts/absorbed.md" not in index_text
    assert "concepts/survivor.md" in index_text

    log_text = (tmp_path / "bundle" / "log.md").read_text(encoding="utf-8")
    assert "**Merge**" in log_text


def test_sensitivity_high_water_mark_applied_regardless_of_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """confidential + public -> confidential is applied to the WRITTEN
    survivor, proving the high-water-mark recompute (never a copy) is wired
    through the CLI (spec: Sensitivity High-Water-Mark Recomputation)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(
        tmp_path, "concepts/survivor", title="Survivor", sensitivity="confidential"
    )
    _write_concept(
        tmp_path, "concepts/absorbed", title="Absorbed", sensitivity="public"
    )

    result = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )

    assert result.exit_code == 0, result.stderr
    survivor_text = (tmp_path / "bundle" / "concepts" / "survivor.md").read_text(
        encoding="utf-8"
    )
    assert "sensitivity: confidential" in survivor_text


def test_preview_surfaces_sensitivity_outcome_and_link_rewrites(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Phase A preview (printed before the confirm gate) MUST surface
    the recomputed sensitivity outcome and every file whose inbound link
    will be rewritten (spec: Confirm-Gated Two-Phase Execution)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(
        tmp_path, "concepts/survivor", title="Survivor", sensitivity="private"
    )
    _write_concept(
        tmp_path, "concepts/absorbed", title="Absorbed", sensitivity="confidential"
    )
    _write_concept(
        tmp_path,
        "concepts/other",
        title="Other",
        body="See [Absorbed](/concepts/absorbed.md).",
    )

    result = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )

    assert result.exit_code == 0, result.stderr
    assert "private" in result.output
    assert "confidential" in result.output
    assert "concepts/other.md" in result.output


def test_decline_at_prompt_writes_nothing_bytes_and_mtimes_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Declining the TTY confirm prompt aborts (exit 1) and leaves EVERY
    bundle file byte- and mtime-identical -- nothing written (spec:
    Confirm-Gated Two-Phase Execution)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path, "concepts/survivor", title="Survivor")
    _write_concept(tmp_path, "concepts/absorbed", title="Absorbed")
    _simulate_tty(monkeypatch)
    before = _snapshot(tmp_path)

    result = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed"], input="n\n"
    )

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert _snapshot(tmp_path) == before


def test_auto_bypasses_the_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--auto` skips the confirmation prompt and Phase B proceeds directly."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path, "concepts/survivor", title="Survivor")
    _write_concept(tmp_path, "concepts/absorbed", title="Absorbed")
    _simulate_tty(monkeypatch)

    result = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )

    assert result.exit_code == 0, result.stderr
    assert "Proceed" not in result.output
    assert not (tmp_path / "bundle" / "concepts" / "absorbed.md").exists()


def test_tty_confirm_prompts_then_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An interactive TTY prompts via `typer.confirm`; confirming proceeds
    with Phase B."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path, "concepts/survivor", title="Survivor")
    _write_concept(tmp_path, "concepts/absorbed", title="Absorbed")
    _simulate_tty(monkeypatch)

    result = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed"], input="y\n"
    )

    assert result.exit_code == 0, result.stderr
    assert not (tmp_path / "bundle" / "concepts" / "absorbed.md").exists()


def test_review_false_skips_the_prompt_like_auto(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Config `review: false` skips the prompt the same as `--auto`."""
    _init_workspace(tmp_path, monkeypatch)
    config_path = tmp_path / "openkos.yaml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "review: true", "review: false"
        ),
        encoding="utf-8",
    )
    _write_concept(tmp_path, "concepts/survivor", title="Survivor")
    _write_concept(tmp_path, "concepts/absorbed", title="Absorbed")
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["merge", "concepts/survivor", "concepts/absorbed"])

    assert result.exit_code == 0, result.stderr
    assert "Proceed" not in result.output
    assert not (tmp_path / "bundle" / "concepts" / "absorbed.md").exists()


def test_non_tty_without_auto_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`review: true`, non-TTY stdin, no `--auto` refuses (exit 1) and
    writes nothing."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path, "concepts/survivor", title="Survivor")
    _write_concept(tmp_path, "concepts/absorbed", title="Absorbed")
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["merge", "concepts/survivor", "concepts/absorbed"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "--auto" in result.stderr
    assert _snapshot(tmp_path) == before


def test_same_id_refuses_no_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`survivor-id == absorbed-id` refuses (exit 1) with a clean error and
    writes nothing (spec: Same-id or unknown id rejected)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path, "concepts/survivor", title="Survivor")
    before = _snapshot(tmp_path)

    result = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/survivor", "--auto"]
    )

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "distinct" in result.stderr
    assert _snapshot(tmp_path) == before


def test_unknown_absorbed_id_refuses_no_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unknown `absorbed-id` refuses (exit 1) and writes nothing (spec:
    Same-id or unknown id rejected)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path, "concepts/survivor", title="Survivor")
    before = _snapshot(tmp_path)

    result = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/nonexistent", "--auto"]
    )

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert _snapshot(tmp_path) == before


def test_unknown_survivor_id_refuses_no_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unknown `survivor-id` refuses (exit 1) and writes nothing."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path, "concepts/absorbed", title="Absorbed")
    before = _snapshot(tmp_path)

    result = runner.invoke(
        app, ["merge", "concepts/nonexistent", "concepts/absorbed", "--auto"]
    )

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert _snapshot(tmp_path) == before


def test_path_traversal_on_absorbed_id_refuses_no_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `..`-segment `absorbed-id` refuses (exit 1) and writes nothing,
    proving `_resolve_concept_path`'s path-safety gate is wired for BOTH
    arguments, not just `survivor-id`."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path, "concepts/survivor", title="Survivor")
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["merge", "concepts/survivor", "../../evil", "--auto"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert _snapshot(tmp_path) == before


def test_merge_refuses_on_a_torn_pending_ledger_write_no_force(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Task 2.8, design Decision 5 Check A: `merge` refuses (exit 1, writes
    nothing) while a `.pending` intent marker exists for the survivor's
    ledger sidecar -- a torn two-phase write from a prior crashed merge --
    and there is NO `--force` override for this refusal at all (distinct
    from the doctor-flagged, `--force`-escapable refusal)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path, "concepts/survivor", title="Survivor")
    _write_concept(tmp_path, "concepts/absorbed", title="Absorbed")
    bundle_dir = tmp_path / "bundle"
    pending_path = bundle_ledger.pending_path_for("concepts/survivor", bundle_dir)
    pending_path.parent.mkdir(parents=True, exist_ok=True)
    pending_path.write_text("stale pending marker", encoding="utf-8")
    before = _snapshot(tmp_path)

    result = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "torn write pending" in result.stderr
    assert _snapshot(tmp_path) == before


def _make_flagged_ledger_entry(
    absorbed_id: str = "concepts/absorbed-0",
    *,
    survivor_before: str = "survivor text",
) -> okf.MergeLedgerEntry:
    """A minimal V3 ledger entry, mirroring `test_doctor.py`'s
    `_make_ledger_entry` helper -- kept as a local copy since `test_doctor`
    is a sibling test module, not a shared fixture location."""
    return okf.MergeLedgerEntry(
        schema=okf.MERGE_LEDGER_SCHEMA_V3,
        merged_at="2026-07-20T00:00:00Z",
        absorbed_id=absorbed_id,
        absorbed_snapshot="absorbed text",
        survivor_before=survivor_before,
        index_before="index text",
        log_before="log text",
        link_rewrites=[],
        sensitivity_before="private",
        sensitivity_after="private",
    )


def _write_flagged_ledger(tmp_path: Path, survivor_id: str) -> None:
    """Commit a two-entry ledger sidecar for `survivor_id` whose entry 1
    embeds a TAMPERED copy of entry 0 in its `survivor_before` -- doctor's
    Check B (`bundle_ledger.scan_nesting_violations`) flags this as
    post-merge mutation, mirroring `test_doctor.py`'s corrupted-ledger
    fixtures."""
    bundle_dir = tmp_path / "bundle"
    entry_0 = _make_flagged_ledger_entry(absorbed_id="concepts/absorbed-0")
    tampered = okf.MergeLedgerEntry(
        schema=entry_0.schema,
        merged_at=entry_0.merged_at,
        absorbed_id=entry_0.absorbed_id,
        absorbed_snapshot="TAMPERED",
        survivor_before=entry_0.survivor_before,
        index_before=entry_0.index_before,
        log_before=entry_0.log_before,
        link_rewrites=entry_0.link_rewrites,
        sensitivity_before=entry_0.sensitivity_before,
        sensitivity_after=entry_0.sensitivity_after,
    )
    embedded_metadata: dict[str, object] = {
        "type": "Concept",
        "title": "Survivor",
        "merged_from": okf.encode_merged_from([tampered]),
    }
    entry_1 = _make_flagged_ledger_entry(
        absorbed_id="concepts/absorbed-1",
        survivor_before=okf.dump_frontmatter(embedded_metadata),
    )
    bundle_ledger.write_entries(
        survivor_id,
        bundle_dir,
        survivor_id=survivor_id,
        entries=[entry_0, entry_1],
    )


def test_merge_refuses_on_a_doctor_flagged_ledger_no_force(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scenario: "Merge onto a flagged ledger refuses by default" -- doctor's
    Check B (`bundle_ledger.scan_nesting_violations`) flags the survivor's
    sidecar as post-merge-mutated, so `merge` refuses in Phase A (exit
    non-zero, writes nothing) without `--force`, naming both remediation
    paths and the non-guaranteed-reversibility statement."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path, "concepts/survivor", title="Survivor")
    _write_concept(tmp_path, "concepts/absorbed", title="Absorbed")
    _write_flagged_ledger(tmp_path, "concepts/survivor")
    monkeypatch.setattr("openkos.cli.main.vcs_git.has_reset_point", lambda root: True)
    before = _snapshot(tmp_path)

    result = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "openkos repair" in result.stderr
    assert "git reset --hard" in result.stderr
    assert "openkos reindex" in result.stderr
    assert "reversibility" in result.stderr.lower()
    assert "not guaranteed" in result.stderr.lower()
    assert "--force" in result.stderr
    assert _snapshot(tmp_path) == before


def test_merge_force_bypasses_flagged_ledger_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--force` bypasses the doctor-flagged refusal and the merge
    completes (with `--auto` also skipping the unrelated confirm gate)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path, "concepts/survivor", title="Survivor")
    _write_concept(tmp_path, "concepts/absorbed", title="Absorbed")
    _write_flagged_ledger(tmp_path, "concepts/survivor")

    result = runner.invoke(
        app,
        ["merge", "concepts/survivor", "concepts/absorbed", "--auto", "--force"],
    )

    assert result.exit_code == 0, result.stderr
    assert not (tmp_path / "bundle" / "concepts" / "absorbed.md").exists()


def test_merge_force_bypasses_refusal_not_confirm_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scenario: "`--force` bypasses the refusal, not the confirm gate" --
    on an interactive TTY WITHOUT `--auto`, `--force` bypasses ONLY the
    ledger-integrity refusal; the existing confirm-gate precedence still
    governs the write, so declining still leaves the bundle untouched."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path, "concepts/survivor", title="Survivor")
    _write_concept(tmp_path, "concepts/absorbed", title="Absorbed")
    _write_flagged_ledger(tmp_path, "concepts/survivor")
    _simulate_tty(monkeypatch)
    before = _snapshot(tmp_path)

    result = runner.invoke(
        app,
        ["merge", "concepts/survivor", "concepts/absorbed", "--force"],
        input="n\n",
    )

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert _snapshot(tmp_path) == before

    result = runner.invoke(
        app,
        ["merge", "concepts/survivor", "concepts/absorbed", "--force"],
        input="y\n",
    )

    assert result.exit_code == 0, result.stderr
    assert not (tmp_path / "bundle" / "concepts" / "absorbed.md").exists()


def test_missing_workspace_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A directory that is not an initialized workspace refuses (exit 1)
    with no raw traceback."""
    monkeypatch.chdir(tmp_path)
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["merge", "concepts/a", "concepts/b", "--auto"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "Traceback" not in result.stderr
    assert _snapshot(tmp_path) == before


def test_phase_b_ordering_catalog_and_survivor_before_absorbed_removal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`index.md`/`log.md`/the merged survivor are written BEFORE the
    absorbed file is removed -- monkeypatching `fsio.remove_file` to raise
    proves the catalog and merged survivor already landed while the
    absorbed file (the one irreversible step) still exists, so a failure
    can't half-destroy irrecoverably (spec: Confirm-Gated Two-Phase
    Execution -- Phase B catalog/log before removing absorbed file)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(
        tmp_path, "concepts/survivor", title="Survivor", sensitivity="private"
    )
    _write_concept(
        tmp_path, "concepts/absorbed", title="Absorbed", sensitivity="confidential"
    )

    def raising_remove_file(path: Path) -> None:
        raise OSError("simulated delete failure")

    monkeypatch.setattr(fsio, "remove_file", raising_remove_file)

    result = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "Traceback" not in result.stderr

    absorbed_path = tmp_path / "bundle" / "concepts" / "absorbed.md"
    assert absorbed_path.is_file()  # recoverable: the destructive step never ran

    survivor_text = (tmp_path / "bundle" / "concepts" / "survivor.md").read_text(
        encoding="utf-8"
    )
    assert "merged_from" not in survivor_text
    assert "sensitivity: confidential" in survivor_text
    # The ledger sidecar's two-phase write (S1/V/S2) lands BEFORE D (the
    # absorbed-file removal that just raised): the merge is already
    # durably recorded, only the (recoverable) cleanup step failed.
    entries = _ledger_entries(tmp_path, "concepts/survivor")
    assert len(entries) == 1
    assert entries[0].absorbed_id == "concepts/absorbed"

    index_text = (tmp_path / "bundle" / "index.md").read_text(encoding="utf-8")
    assert "concepts/absorbed.md" not in index_text

    log_text = (tmp_path / "bundle" / "log.md").read_text(encoding="utf-8")
    assert "**Merge**" in log_text


def _patch_flaky_apply_link_rewrites(
    monkeypatch: pytest.MonkeyPatch, *, fails_for_file: str
) -> None:
    """Monkeypatch `bundle_links.apply_link_rewrites` (the SAME module
    object `cli/main.py` calls through) so its FIRST invocation for
    `fails_for_file` raises `OSError`, every other invocation (including a
    later retry for the same file) delegates to the real implementation."""
    original_apply = bundle_links.apply_link_rewrites
    failures = {"count": 0}

    def flaky_apply_link_rewrites(
        text: str, *, file: str, rewrites: list[okf.LinkRewrite]
    ) -> str:
        if file == fails_for_file:
            failures["count"] += 1
            if failures["count"] == 1:
                raise OSError("simulated mid-loop rewrite failure")
        return original_apply(text, file=file, rewrites=rewrites)

    monkeypatch.setattr(bundle_links, "apply_link_rewrites", flaky_apply_link_rewrites)


def test_retry_after_mid_rewrite_failure_completes_the_merge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failure while computing the SECOND inbound-linked file's rewrite
    must leave the survivor with NO `merged_from` ledger entry yet -- the
    ledger is committed LAST, only after every rewrite succeeds -- so a
    clean re-run of `merge S A --auto` completes the merge instead of
    being permanently blocked by `plan_merge`'s "already merged" guard.
    Regression test for a half-completed-merge state trap: with the OLD
    ordering (survivor/ledger written BEFORE the rewrite loop), this same
    failure leaves the ledger falsely claiming the merge is done, and the
    retry below is refused (spec: Confirm-Gated Two-Phase Execution)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(
        tmp_path, "concepts/survivor", title="Survivor", sensitivity="private"
    )
    _write_concept(
        tmp_path, "concepts/absorbed", title="Absorbed", sensitivity="confidential"
    )
    _write_concept(
        tmp_path,
        "concepts/linker1",
        title="Linker1",
        body="See [Absorbed](/concepts/absorbed.md).",
    )
    _write_concept(
        tmp_path,
        "concepts/linker2",
        title="Linker2",
        body="Also see [Absorbed](/concepts/absorbed.md).",
    )
    _patch_flaky_apply_link_rewrites(monkeypatch, fails_for_file="concepts/linker2.md")

    first = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )
    assert first.exit_code == 1
    assert isinstance(first.exception, SystemExit)

    absorbed_path = tmp_path / "bundle" / "concepts" / "absorbed.md"
    assert absorbed_path.is_file()  # never removed -- the failure happened first

    survivor_after_failure = (
        tmp_path / "bundle" / "concepts" / "survivor.md"
    ).read_text(encoding="utf-8")
    assert "merged_from" not in survivor_after_failure  # ledger not yet committed
    assert _ledger_entries(tmp_path, "concepts/survivor") == []

    retry = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )
    assert retry.exit_code == 0, retry.stderr  # NOT blocked by "already merged"

    assert not absorbed_path.exists()

    survivor_text = (tmp_path / "bundle" / "concepts" / "survivor.md").read_text(
        encoding="utf-8"
    )
    assert "merged_from" not in survivor_text

    linker1_text = (tmp_path / "bundle" / "concepts" / "linker1.md").read_text(
        encoding="utf-8"
    )
    linker2_text = (tmp_path / "bundle" / "concepts" / "linker2.md").read_text(
        encoding="utf-8"
    )
    assert "/concepts/survivor.md" in linker1_text
    assert "/concepts/absorbed.md" not in linker1_text
    assert "/concepts/survivor.md" in linker2_text
    assert "/concepts/absorbed.md" not in linker2_text


def test_retry_produces_a_correct_fully_reversible_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After a mid-rewrite failure and a successful retry, the survivor's
    `merged_from` ledger entry's `link_rewrites` are recorded AND every one
    of them matches what is actually on disk at its recorded `offset` --
    the ledger the retry commits is exactly what a future `unmerge` would
    need for a faithful reversal, not a stale or incomplete record (spec:
    Reversibility Ledger). Because no inbound-link write happens until
    every rewrite computes successfully, a compute-time failure on ANY one
    file leaves EVERY file untouched, so the retry's fresh rescan finds --
    and records -- every still-absorbed-linked file, not just the one that
    failed the first time."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(
        tmp_path, "concepts/survivor", title="Survivor", sensitivity="private"
    )
    _write_concept(
        tmp_path, "concepts/absorbed", title="Absorbed", sensitivity="confidential"
    )
    _write_concept(
        tmp_path,
        "concepts/linker1",
        title="Linker1",
        body="See [Absorbed](/concepts/absorbed.md).",
    )
    _write_concept(
        tmp_path,
        "concepts/linker2",
        title="Linker2",
        body="Also see [Absorbed](/concepts/absorbed.md).",
    )
    _patch_flaky_apply_link_rewrites(monkeypatch, fails_for_file="concepts/linker2.md")

    first = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )
    assert first.exit_code == 1

    retry = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )
    assert retry.exit_code == 0, retry.stderr

    entries = _ledger_entries(tmp_path, "concepts/survivor")
    assert len(entries) == 1
    rewrites = entries[0].link_rewrites
    assert {rw.file for rw in rewrites} == {
        "concepts/linker1.md",
        "concepts/linker2.md",
    }

    for rewrite in rewrites:
        file_text = (tmp_path / "bundle" / rewrite.file).read_text(encoding="utf-8")
        end = rewrite.offset + len(rewrite.new_link)
        assert file_text[rewrite.offset : end] == rewrite.new_link


def test_apply_link_rewrite_idempotently_skips_an_already_rewritten_file() -> None:
    """`_apply_link_rewrite_idempotently` is the Phase-B loop's idempotency
    guard: a file that ALREADY shows the recorded `new_link` at the
    recorded `offset` is returned unchanged (no-op), a not-yet-rewritten
    file is rewritten exactly as `bundle_links.apply_link_rewrites` would,
    and a file matching NEITHER state still raises -- the bounded-rewrite
    guarantee is never weakened for the normal case."""
    not_yet = "See [Absorbed](/concepts/absorbed.md)."
    already_done = "See [Absorbed](/concepts/survivor.md)."
    drifted = "See [Absorbed](/concepts/elsewhere.md)."
    rewrite = okf.LinkRewrite(
        file="concepts/other.md",
        old_link="/concepts/absorbed.md",
        new_link="/concepts/survivor.md",
        offset=not_yet.index("/concepts/absorbed.md"),
    )

    rewritten = _apply_link_rewrite_idempotently(
        not_yet, file="concepts/other.md", rewrites=[rewrite]
    )
    assert rewritten == bundle_links.apply_link_rewrites(
        not_yet, file="concepts/other.md", rewrites=[rewrite]
    )

    assert (
        _apply_link_rewrite_idempotently(
            already_done, file="concepts/other.md", rewrites=[rewrite]
        )
        == already_done
    )

    with pytest.raises(ValueError, match="no occurrence of link target"):
        _apply_link_rewrite_idempotently(
            drifted, file="concepts/other.md", rewrites=[rewrite]
        )


def test_path_traversal_on_survivor_id_refuses_no_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `..`-segment `survivor-id` refuses (exit 1) and writes nothing,
    mirroring the existing absorbed-id path-traversal guard for the OTHER
    argument (spec: Same-id or unknown id rejected)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path, "concepts/absorbed", title="Absorbed")
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["merge", "../../evil", "concepts/absorbed", "--auto"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert _snapshot(tmp_path) == before


def test_merge_succeeds_and_moves_absorbed_outbound_relations_onto_survivor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`merge` no longer refuses when the absorbed object bears its own
    typed `relations:` entries -- slice 2a rewires instead of blocking
    (spec: "Merge of an edge-bearing object always succeeds", "Outbound
    relations move to the survivor"; REPLACES the removed slice-1 refuse
    guard)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path, "concepts/survivor", title="Survivor")
    _write_concept_with_relations(
        tmp_path,
        "concepts/absorbed",
        title="Absorbed",
        relations=[{"target": "concepts/other", "type": "depends_on"}],
    )

    result = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )

    assert result.exit_code == 0, result.stderr
    assert result.exception is None
    assert not (tmp_path / "bundle" / "concepts" / "absorbed.md").exists()

    survivor_text = (tmp_path / "bundle" / "concepts" / "survivor.md").read_text(
        encoding="utf-8"
    )
    survivor_metadata, _ = okf.load_frontmatter(survivor_text)
    assert okf.decode_relations(survivor_metadata) == [
        okf.Relation(target="concepts/other", type="depends_on")
    ]


def test_preview_surfaces_relation_drop_dedupe_and_retarget_bullets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Phase A preview (printed before the confirm gate) surfaces the
    outbound `merge_relations` report -- dropped self-loop, deduped
    collision -- AND every third-party file whose inbound relation will be
    retargeted, all non-silently before any write (spec: "Resulting
    self-loop is dropped, non-silently", "Duplicate edge is deduped,
    non-silently"; design D3/"Preview")."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept_with_relations(
        tmp_path,
        "concepts/survivor",
        title="Survivor",
        relations=[
            {"target": "concepts/absorbed", "type": "references"},
            {"target": "concepts/other", "type": "depends_on"},
        ],
    )
    _write_concept_with_relations(
        tmp_path,
        "concepts/absorbed",
        title="Absorbed",
        relations=[{"target": "concepts/other", "type": "depends_on"}],
    )
    _write_concept(tmp_path, "concepts/other", title="Other")
    _write_concept_with_relations(
        tmp_path,
        "concepts/linker",
        title="Linker",
        relations=[{"target": "concepts/absorbed", "type": "mentions"}],
    )

    result = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )

    assert result.exit_code == 0, result.stderr
    assert "drop self-loop: concepts/survivor (references)" in result.output
    assert "dedupe collision: concepts/other (depends_on)" in result.output
    assert "bundle/concepts/linker.md (retarget relation to survivor)" in result.output


def test_preview_surfaces_stacked_body_bullet_when_absorbed_body_non_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Phase A preview surfaces the body-stacking report (issue #409,
    report half) as a non-silent bullet, mirroring the existing
    drop-self-loop / dedupe-collision bullets: the merge stacks the
    absorbed body under a `## Merged content` heading without comparing it
    to the survivor's -- this bullet says so, with the unreconciled char
    count so the operator knows how much content to actually go read."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path, "concepts/survivor", title="Survivor", body="Real body.")
    _write_concept(
        tmp_path,
        "concepts/absorbed",
        title="Absorbed",
        body="Contradicting body content.",
    )

    result = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )

    assert result.exit_code == 0, result.stderr
    assert "stack absorbed body:" in result.output
    assert "unreconciled" in result.output


def test_preview_bullets_match_committed_survivor_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SUGGESTION coverage (review correction batch, regression insurance):
    couples the merge PREVIEW's drop-self-loop/dedupe-collision bullets to
    the ACTUAL post-write survivor document. `cli/main.py::merge` recomputes
    these bullets via a SECOND, separate `okf.merge_relations` call (since
    `MergePlan` doesn't expose them -- see apply-progress's documented
    deviation); this test guards that recompute against ever diverging from
    what is actually committed to `plan.merged_survivor`."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept_with_relations(
        tmp_path,
        "concepts/survivor",
        title="Survivor",
        relations=[
            {"target": "concepts/absorbed", "type": "references"},
            {"target": "concepts/other", "type": "depends_on"},
        ],
    )
    _write_concept_with_relations(
        tmp_path,
        "concepts/absorbed",
        title="Absorbed",
        relations=[{"target": "concepts/other", "type": "depends_on"}],
    )
    _write_concept(tmp_path, "concepts/other", title="Other")

    result = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )

    assert result.exit_code == 0, result.stderr
    assert "drop self-loop: concepts/survivor (references)" in result.output
    assert "dedupe collision: concepts/other (depends_on)" in result.output

    survivor_text = (tmp_path / "bundle" / "concepts" / "survivor.md").read_text(
        encoding="utf-8"
    )
    metadata, _ = okf.load_frontmatter(survivor_text)
    committed_relations = okf.decode_relations(metadata)

    # The "drop self-loop" bullet must correspond to NO surviving
    # `target=concepts/survivor` entry in the committed document.
    assert not any(r.target == "concepts/survivor" for r in committed_relations)
    # The "dedupe collision" bullet must correspond to EXACTLY ONE surviving
    # `(concepts/other, depends_on)` entry, never two.
    assert (
        sum(
            1
            for r in committed_relations
            if r.target == "concepts/other" and r.type == "depends_on"
        )
        == 1
    )


def test_merge_succeeds_despite_unrelated_file_with_malformed_frontmatter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A THIRD, wholly unrelated bundle file with malformed YAML
    frontmatter must not crash or block a merge between a clean survivor
    and absorbed pair -- `bundle_links.find_inbound_link_rewrites`'s
    inbound scan reads every other bundle file's frontmatter, but a
    hand-edited/corrupt file elsewhere in the bundle is not this merge's
    concern (correction batch, finding 1: unhandled `yaml.YAMLError`
    escaping the `except (OSError, ValueError)` fail-closed handler and
    crashing with a raw traceback instead of completing cleanly)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path, "concepts/survivor", title="Survivor")
    _write_concept(tmp_path, "concepts/absorbed", title="Absorbed")

    broken_path = tmp_path / "bundle" / "concepts" / "broken.md"
    broken_path.write_text('---\ntitle: "Broken\n---\n\nBody.\n', encoding="utf-8")

    result = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )

    assert result.exit_code == 0, result.stderr
    assert result.exception is None
    assert not (tmp_path / "bundle" / "concepts" / "absorbed.md").exists()


def test_merge_retargets_inbound_provenance_and_previews_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A third-party file whose `provenance:` names the absorbed id is
    retargeted to the survivor id, and the Phase A preview surfaces it
    before the confirm gate (spec: Reversible Inbound-Provenance Rewiring)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path, "concepts/survivor", title="Survivor")
    _write_concept(tmp_path, "concepts/absorbed", title="Absorbed")
    _write_concept_with_provenance(
        tmp_path,
        "concepts/derived",
        title="Derived",
        provenance=["concepts/absorbed"],
    )

    result = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )

    assert result.exit_code == 0, result.stderr
    assert "bundle/concepts/derived.md (retarget provenance to survivor)" in (
        result.output
    )

    derived_text = (tmp_path / "bundle" / "concepts" / "derived.md").read_text(
        encoding="utf-8"
    )
    derived_metadata, _ = okf.load_frontmatter(derived_text)
    assert derived_metadata["provenance"] == ["concepts/survivor"]


def test_merge_absorbing_non_source_concept_still_retargets_third_party_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P3 / spec scenario: the provenance scan is NOT gated on the absorbed
    concept's `type` -- absorbing a non-Source `Decision` still retargets a
    third party's `provenance` entry naming it, since `query --save` can
    file any cited concept id as another object's provenance."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path, "concepts/survivor", title="Survivor")
    _write_concept_with_provenance(
        tmp_path,
        "concepts/absorbed",
        title="Absorbed",
        concept_type="Decision",
    )
    _write_concept_with_provenance(
        tmp_path,
        "concepts/derived",
        title="Derived",
        provenance=["concepts/absorbed"],
    )

    result = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )

    assert result.exit_code == 0, result.stderr
    derived_metadata, _ = okf.load_frontmatter(
        (tmp_path / "bundle" / "concepts" / "derived.md").read_text(encoding="utf-8")
    )
    assert derived_metadata["provenance"] == ["concepts/survivor"]


def test_merge_scans_bundle_exactly_once_via_rglob_when_scanning_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T5 -- zero extra bundle walks: `prepare_merge` calls
    `Path.rglob(bundle_dir, "*.md")` exactly once even with the provenance
    scanner added as a third pass. Uses a PLAIN-FUNCTION counting wrapper
    (never a generator/`yield from`), mirroring
    `test_contradictions.py:1041`'s `_counting_build_graph` precedent: a
    generator body would defer the count to the first `next()`, measuring
    iteration rather than invocation, and would prove nothing."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path, "concepts/survivor", title="Survivor")
    _write_concept(tmp_path, "concepts/absorbed", title="Absorbed")
    _write_concept_with_provenance(
        tmp_path,
        "concepts/derived",
        title="Derived",
        provenance=["concepts/absorbed"],
    )

    calls: list[tuple[Path, str]] = []
    original = Path.rglob

    def _counting_rglob(self: Path, pattern: str, **kwargs: object) -> object:
        calls.append((self, pattern))
        return original(self, pattern, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "rglob", _counting_rglob)
    # The #640 write-time refresh legitimately re-walks the bundle AFTER the
    # merge committed; stub it so this count keeps measuring what T5 is
    # about -- `prepare_merge`'s single scan -- not the post-write refresh.
    monkeypatch.setattr(main, "_refresh_derived_after_write", lambda *a, **k: True)

    result = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )

    assert result.exit_code == 0, result.stderr
    bundle_dir = tmp_path / "bundle"
    matching_calls = [call for call in calls if call == (bundle_dir, "*.md")]
    assert matching_calls == [(bundle_dir, "*.md")]


def test_merge_retarget_then_later_set_sensitivity_raise_reaches_descendant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The functional defect #230 proof: after `merge` retargets a
    third-party object's `provenance` from absorbed to survivor, a LATER
    `set-sensitivity <survivor> <higher-level>` (confirmed) resolves that
    object as a provenance descendant, raises its sensitivity via
    `combine_sensitivity`, and names it in the preview/success message.
    Before the fix, the object's `provenance` still named the removed
    absorbed id, so it was unreachable and silently skipped by
    `find_provenance_descendants` (spec: Retargeted Provenance Reaches
    Later Sensitivity Propagation)."""
    _init_workspace(tmp_path, monkeypatch)
    survivor_path = tmp_path / "bundle" / "sources" / "survivor.md"
    survivor_path.parent.mkdir(parents=True, exist_ok=True)
    survivor_path.write_text(
        okf.dump_frontmatter(
            {"type": "Source", "title": "Survivor", "sensitivity": "private"},
            "# Survivor\n\nBody.\n",
        ),
        encoding="utf-8",
    )
    absorbed_path = tmp_path / "bundle" / "sources" / "absorbed.md"
    absorbed_path.write_text(
        okf.dump_frontmatter(
            {"type": "Source", "title": "Absorbed", "sensitivity": "private"},
            "# Absorbed\n\nBody.\n",
        ),
        encoding="utf-8",
    )
    _write_concept_with_provenance(
        tmp_path,
        "concepts/derived",
        title="Derived",
        provenance=["sources/absorbed"],
    )
    derived_path = tmp_path / "bundle" / "concepts" / "derived.md"
    derived_metadata, derived_body = okf.load_frontmatter(
        derived_path.read_text(encoding="utf-8")
    )
    derived_metadata["sensitivity"] = "private"
    derived_path.write_text(
        okf.dump_frontmatter(derived_metadata, derived_body), encoding="utf-8"
    )

    merge_result = runner.invoke(
        app, ["merge", "sources/survivor", "sources/absorbed", "--auto"]
    )
    assert merge_result.exit_code == 0, merge_result.stderr

    derived_metadata_after_merge, _ = okf.load_frontmatter(
        derived_path.read_text(encoding="utf-8")
    )
    assert derived_metadata_after_merge["provenance"] == ["sources/survivor"]

    set_sensitivity_result = runner.invoke(
        app, ["set-sensitivity", "sources/survivor", "confidential", "--auto"]
    )

    assert set_sensitivity_result.exit_code == 0, set_sensitivity_result.stderr
    assert "concepts/derived.md" in set_sensitivity_result.output

    derived_metadata_final, _ = okf.load_frontmatter(
        derived_path.read_text(encoding="utf-8")
    )
    assert derived_metadata_final["sensitivity"] == "confidential"


# -- #334: re-validate every write AND delete target after the confirm gate --


def _pair_with_all_three_rewrite_groups(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A not-yet-merged survivor/absorbed pair plus one third-party file per
    REWRITE GROUP, on a TTY.

    `merge`'s guard mapping feeds its dynamic entries from `touched_files`,
    the union of the link, relation, and provenance partitions. A fixture
    that produced only a link rewrite would leave the other two partitions
    empty, so a mapping that silently lost either would keep every test
    here green -- the same trap `test_unmerge.py`'s fixture documents
    (#313 review, R3), applied to the forward direction.
    """
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path, "concepts/survivor", title="Survivor")
    _write_concept(tmp_path, "concepts/absorbed", title="Absorbed")
    # link group: an inbound body link, no frontmatter references
    _write_concept(
        tmp_path,
        "concepts/other",
        title="Other",
        body="See [Absorbed](/concepts/absorbed.md) for details.",
    )
    # relation group: a typed relation and no `provenance:`
    _write_concept_with_relations(
        tmp_path,
        "concepts/relator",
        title="Relator",
        relations=[{"target": "concepts/absorbed", "type": "depends_on"}],
    )
    # provenance group
    _write_concept_with_provenance(
        tmp_path,
        "concepts/derived",
        title="Derived",
        provenance=["concepts/absorbed"],
    )
    _simulate_tty(monkeypatch)


_MERGE_WRITE_TARGETS = [
    "bundle/index.md",
    "bundle/log.md",
    "bundle/concepts/other.md",
    "bundle/concepts/relator.md",
    "bundle/concepts/derived.md",
    "bundle/concepts/survivor.md",
]
"""One entry per guard-mapping contributor `merge_core` OVERWRITES: the two
fixed catalog/log keys, one touched file per rewrite partition, and the
survivor -- so no single contributor can be dropped from the mapping without
failing at least one parametrized case below. The absorbed DELETE target has
its own dedicated tests."""


@pytest.mark.parametrize("target", _MERGE_WRITE_TARGETS)
def test_a_write_target_edited_during_the_prompt_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    """#334: `merge` writes every one of these from bytes computed before
    the prompt, so an edit landing while the operator reads the preview was
    overwritten in full and auto-committed.

    Parametrized over every overwrite target -- one per rewrite group, plus
    the fixed `index.md`/`log.md`/survivor keys -- so no single contributor
    to the guard mapping can be deleted unnoticed.
    """
    _pair_with_all_three_rewrite_groups(tmp_path, monkeypatch)
    target_path = tmp_path / target
    concurrent = "hand-edited while the prompt waited\n"
    before = _snapshot(tmp_path)
    confirm_after(
        monkeypatch, lambda: target_path.write_text(concurrent, encoding="utf-8")
    )

    result = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed"], input="y\n"
    )

    assert result.exit_code == 3
    assert isinstance(result.exception, SystemExit)
    assert "refusing to write --" in result.stderr
    assert target in result.stderr
    assert target_path.read_text(encoding="utf-8") == concurrent
    after = _snapshot(tmp_path)
    changed = changed_paths(before, after)
    assert changed == {Path(target)}


def test_the_absorbed_delete_target_edited_during_the_prompt_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#334's worst case: the absorbed file is DELETED, not overwritten, so
    an edit landing there during the prompt is destroyed outright -- nothing
    survives to recover from. The delete target therefore belongs in the
    guard mapping alongside the write targets, and drift on it must leave
    the edited file intact on disk."""
    _pair_with_all_three_rewrite_groups(tmp_path, monkeypatch)
    target = "bundle/concepts/absorbed.md"
    target_path = tmp_path / target
    concurrent = "hand-edited while the prompt waited\n"
    before = _snapshot(tmp_path)
    confirm_after(
        monkeypatch, lambda: target_path.write_text(concurrent, encoding="utf-8")
    )

    result = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed"], input="y\n"
    )

    assert result.exit_code == 3
    assert isinstance(result.exception, SystemExit)
    assert "refusing to write --" in result.stderr
    assert target in result.stderr
    # #319: the absorbed file is the mapping's one DELETE target, so the
    # message must label it as such and extend the fail-closed footer to
    # "nothing was deleted".
    assert "delete target(s)" in result.stderr
    assert "nothing was deleted" in result.stderr
    # The edit survives: not unlinked, not rewritten.
    assert target_path.read_text(encoding="utf-8") == concurrent
    after = _snapshot(tmp_path)
    changed = changed_paths(before, after)
    assert changed == {Path(target)}


def test_a_write_target_deleted_during_the_prompt_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A touched file that has since VANISHED is drift too: rewriting it
    from a snapshot the operator can no longer see is the same silent
    revert."""
    _pair_with_all_three_rewrite_groups(tmp_path, monkeypatch)
    deleted_path = tmp_path / "bundle" / "concepts" / "other.md"
    before = _snapshot(tmp_path)
    confirm_after(monkeypatch, deleted_path.unlink)

    result = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed"], input="y\n"
    )

    assert result.exit_code == 3
    assert "refusing to write --" in result.stderr
    assert "bundle/concepts/other.md" in result.stderr
    assert not deleted_path.exists()
    after = _snapshot(tmp_path)
    changed = changed_paths(before, after)
    assert changed == {Path("bundle/concepts/other.md")}


@pytest.mark.parametrize(
    "target", [*_MERGE_WRITE_TARGETS, "bundle/concepts/absorbed.md"]
)
def test_a_crlf_rewrite_during_the_prompt_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    """#306's constraint, re-pinned for `merge`: `read_text`'s
    universal-newline translation makes a CRLF rewrite compare EQUAL to its
    own LF snapshot, and `fsio.write_atomic` (opening with `newline=""`)
    would then put the LF plan back over it -- or, for the absorbed file,
    unlink it outright."""
    _pair_with_all_three_rewrite_groups(tmp_path, monkeypatch)
    target_path = tmp_path / target
    concurrent = target_path.read_bytes().replace(b"\n", b"\r\n")
    assert concurrent != target_path.read_bytes()
    before = _snapshot(tmp_path)
    confirm_after(monkeypatch, lambda: target_path.write_bytes(concurrent))

    result = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed"], input="y\n"
    )

    assert result.exit_code == 3
    assert "refusing to write --" in result.stderr
    assert target in result.stderr
    assert target_path.read_bytes() == concurrent
    after = _snapshot(tmp_path)
    changed = changed_paths(before, after)
    assert changed == {Path(target)}


def test_targets_that_were_already_crlf_are_not_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other direction: every guarded target already CRLF at rest,
    untouched, must not be reported as drift -- otherwise `merge` refuses
    forever on a CRLF workspace, naming a cause that never happened."""
    _pair_with_all_three_rewrite_groups(tmp_path, monkeypatch)
    for rel in [*_MERGE_WRITE_TARGETS, "bundle/concepts/absorbed.md"]:
        path = tmp_path / rel
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))

    result = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )

    assert result.exit_code == 0
    assert "refusing to write" not in result.stderr
    assert not (tmp_path / "bundle" / "concepts" / "absorbed.md").exists()


def test_drift_on_the_unprompted_path_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#334: the guard must run on `--auto` too -- nothing pauses for a
    human there, which makes an unattended merge the likeliest to race a
    second writer, not the least."""
    _pair_with_all_three_rewrite_groups(tmp_path, monkeypatch)
    target = "bundle/concepts/survivor.md"
    target_path = tmp_path / target
    concurrent = "hand-edited while the preview printed\n"
    before = _snapshot(tmp_path)
    hook = echo_after(
        monkeypatch,
        lambda: target_path.write_text(concurrent, encoding="utf-8"),
        trigger="- bundle/concepts/absorbed.md",
    )

    result = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )

    assert hook.fired, "echo_after trigger never matched -- stale preview wording?"
    assert result.exit_code == 3
    assert "refusing to write --" in result.stderr
    assert target in result.stderr
    assert target_path.read_text(encoding="utf-8") == concurrent
    after = _snapshot(tmp_path)
    changed = changed_paths(before, after)
    assert changed == {Path(target)}


def test_drift_under_review_false_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#334's OTHER unprompted path: config `review: false` skips the
    prompt exactly like `--auto`, but the window between Phase A's read
    and Phase B's first write is still open -- so the guard must run
    unconditionally, not only when `review` is set."""
    _pair_with_all_three_rewrite_groups(tmp_path, monkeypatch)
    config_path = tmp_path / "openkos.yaml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "review: true", "review: false"
        ),
        encoding="utf-8",
    )
    target = "bundle/concepts/survivor.md"
    target_path = tmp_path / target
    concurrent = "hand-edited while the preview printed\n"
    before = _snapshot(tmp_path)
    hook = echo_after(
        monkeypatch,
        lambda: target_path.write_text(concurrent, encoding="utf-8"),
        trigger="- bundle/concepts/absorbed.md",
    )

    result = runner.invoke(app, ["merge", "concepts/survivor", "concepts/absorbed"])

    assert hook.fired, "echo_after trigger never matched -- stale preview wording?"
    assert result.exit_code == 3
    assert "refusing to write --" in result.stderr
    assert target in result.stderr
    assert target_path.read_text(encoding="utf-8") == concurrent
    after = _snapshot(tmp_path)
    changed = changed_paths(before, after)
    assert changed == {Path(target)}


def test_an_edit_landing_after_the_snapshot_observation_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#318's race, pinned for `merge` (#327 follow-up; the pin existed only
    in `test_relate.py`): the guard's baseline and the text the merged
    survivor is computed from must come from the ONE `_snapshot_read`
    observation -- under a two-read shape a writer landing between the two
    reads becomes the guard's own baseline, and Phase B writes the merge
    computed from the EARLIER text, silently reverting the edit and
    auto-committing the revert.

    The edit lands immediately after the survivor's snapshot returns (the
    verb's FIRST snapshot -- absorbed, `index.md`, `log.md`, and every
    touched third-party file are read after it), the earliest a concurrent
    writer can now land relative to the plan; the guard's later re-read
    must call it drift and refuse the whole run.
    """
    _pair_with_all_three_rewrite_groups(tmp_path, monkeypatch)
    target_path = tmp_path / "bundle" / "concepts" / "survivor.md"
    concurrent = "hand-edited the instant the snapshot returned\n"
    real_snapshot_read = main._snapshot_read
    fired = False

    def racing_snapshot_read(path: Path) -> tuple[bytes, str]:
        nonlocal fired
        snapshot = real_snapshot_read(path)
        if not fired and path == target_path:
            fired = True
            target_path.write_text(concurrent, encoding="utf-8")
        return snapshot

    before = _snapshot(tmp_path)
    monkeypatch.setattr(main, "_snapshot_read", racing_snapshot_read)

    result = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )

    assert fired, "the racing wrapper never saw the survivor snapshot"
    assert result.exit_code == 3
    assert isinstance(result.exception, SystemExit)
    assert "refusing to write --" in result.stderr
    assert "bundle/concepts/survivor.md" in result.stderr
    assert target_path.read_text(encoding="utf-8") == concurrent
    assert changed_paths(before, _snapshot(tmp_path)) == {
        Path("bundle/concepts/survivor.md")
    }


# --- #645: the opt-out reconciliation pass ----------------------------------

_LONG_BODY = (
    "This side says a great deal about the shared subject, repeating the "
    "same claims in its own voice across several sentences so the stacked "
    "share of the merged body is well above the reconciliation threshold."
)

_RECONCILED_BODY = (
    "One coherent document now covers the shared subject in a single "
    "voice, folding both sides' claims together without repeating them."
)


def _patch_reconciliation(
    monkeypatch: pytest.MonkeyPatch, reply: "str | None"
) -> list[dict[str, object]]:
    """Patch the model seam (`reconcile_merged_body`) and record calls."""
    calls: list[dict[str, object]] = []

    def _fake(**kwargs: object) -> "str | None":
        calls.append(kwargs)
        return reply

    monkeypatch.setattr(main, "reconcile_merged_body", _fake)
    return calls


def test_merge_reconciles_the_stacked_body_above_the_threshold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#645 end to end: above the unreconciled-share threshold the merge
    runs the (mocked) reconciliation call, the preview discloses it before
    consent, and the WRITTEN survivor carries the reconciled body -- no
    stacked `## Merged content` heading left."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path, "concepts/survivor", title="Survivor", body=_LONG_BODY)
    _write_concept(tmp_path, "concepts/absorbed", title="Absorbed", body=_LONG_BODY)
    calls = _patch_reconciliation(monkeypatch, _RECONCILED_BODY)

    result = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )

    assert result.exit_code == 0, result.stderr
    assert "reconcile merged body" in result.stdout
    assert len(calls) == 1
    survivor_text = (tmp_path / "bundle" / "concepts" / "survivor.md").read_text(
        encoding="utf-8"
    )
    assert _RECONCILED_BODY in survivor_text
    assert "## Merged content" not in survivor_text


def test_merge_below_the_threshold_never_calls_the_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A small absorbed contribution stays an honest append -- zero model
    calls, stacked body kept, no reconcile preview line."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path, "concepts/survivor", title="Survivor", body=_LONG_BODY * 5)
    _write_concept(tmp_path, "concepts/absorbed", title="Absorbed", body="Tiny.")
    calls = _patch_reconciliation(monkeypatch, _RECONCILED_BODY)

    result = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )

    assert result.exit_code == 0, result.stderr
    assert "reconcile merged body" not in result.stdout
    assert calls == []
    survivor_text = (tmp_path / "bundle" / "concepts" / "survivor.md").read_text(
        encoding="utf-8"
    )
    assert "## Merged content (concepts/absorbed)" in survivor_text


def test_merge_below_the_absolute_floor_never_calls_the_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two one-line bodies stack at a share above the threshold while
    carrying nothing worth a model call -- the absolute floor keeps the
    pass off.

    The counter-example the floor exists for, and it still lands below the
    floor after #803 re-anchored it onto the MERGED body: the whole merged
    document here is well under 200 chars."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path, "concepts/survivor", title="Survivor", body="One line.")
    _write_concept(tmp_path, "concepts/absorbed", title="Absorbed", body="One line.")
    calls = _patch_reconciliation(monkeypatch, _RECONCILED_BODY)

    result = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )

    assert result.exit_code == 0, result.stderr
    assert "reconcile merged body" not in result.stdout
    assert calls == []


# The issue #803 shape, transposed: two SHORT `Person`-sized documents whose
# absorbed half is nearly 40% of the result but well under 200 chars. The
# pre-#803 absolute floor read `absorbed_chars`, which a short document can
# never clear no matter how large its share; the re-anchored floor reads
# `merged_chars`, which this pair does clear.
_SHORT_SURVIVOR_BODY = (
    "Primary datastore advocate. Marta argued for PostgreSQL 16 over MySQL "
    "because the billing service already runs Postgres and the operational "
    "burden of a second engine was judged not worth it."
)

_SHORT_ABSORBED_BODY = (
    "Technical lead of the Helios Data Platform. She owns the roadmap for "
    "the ingestion tier and reports to the platform director."
)


def test_merge_short_document_above_the_share_is_reconciled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#803: a short pair whose absorbed half is ~36% of the merged body but
    contributes only ~137 chars is reconciled.

    The pre-#803 gate refused it on the absolute `absorbed_chars` floor
    alone -- the exact blind spot the issue reports, where two `Person`
    documents at 39%/40% share missed the floor by 11 and 19 characters and
    landed on disk with two `# ` document roots."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(
        tmp_path, "concepts/survivor", title="Survivor", body=_SHORT_SURVIVOR_BODY
    )
    _write_concept(
        tmp_path, "concepts/absorbed", title="Absorbed", body=_SHORT_ABSORBED_BODY
    )
    calls = _patch_reconciliation(monkeypatch, _RECONCILED_BODY)

    result = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )

    assert result.exit_code == 0, result.stderr
    assert "reconcile merged body" in result.stdout
    assert len(calls) == 1
    survivor_text = (tmp_path / "bundle" / "concepts" / "survivor.md").read_text(
        encoding="utf-8"
    )
    assert _RECONCILED_BODY in survivor_text
    assert "## Merged content" not in survivor_text


def test_merge_no_reconcile_flag_keeps_the_stacked_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--no-reconcile` is the opt-out: no model call even above the
    threshold, stacked body written."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path, "concepts/survivor", title="Survivor", body=_LONG_BODY)
    _write_concept(tmp_path, "concepts/absorbed", title="Absorbed", body=_LONG_BODY)
    calls = _patch_reconciliation(monkeypatch, _RECONCILED_BODY)

    result = runner.invoke(
        app,
        [
            "merge",
            "concepts/survivor",
            "concepts/absorbed",
            "--auto",
            "--no-reconcile",
        ],
    )

    assert result.exit_code == 0, result.stderr
    assert calls == []
    survivor_text = (tmp_path / "bundle" / "concepts" / "survivor.md").read_text(
        encoding="utf-8"
    )
    assert "## Merged content (concepts/absorbed)" in survivor_text


# #803: a pair that clears NEITHER threshold -- a short survivor and a tiny
# absorbed body, so the share sits far below 0.2 and the merged body below
# the 200-char floor. `--reconcile` must plan and run the pass anyway.
_TINY_SURVIVOR_BODY = "A short standalone note about the shared subject."

_TINY_ABSORBED_BODY = "Tiny."


def test_merge_reconcile_flag_forces_the_pass_below_both_thresholds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#803: `--reconcile` is the explicit counterpart to `--no-reconcile`.

    The preview already told the operator that bodies were appended rather
    than reconciled; this is the lever that acts on it. It bypasses BOTH
    thresholds -- a deliberate operator opt-in outranks a heuristic tuned
    for the unattended default."""
    _init_workspace(tmp_path, monkeypatch)
    for suffix in ("", "2"):
        _write_concept(
            tmp_path,
            f"concepts/survivor{suffix}",
            title=f"Survivor{suffix}",
            body=_TINY_SURVIVOR_BODY,
        )
        _write_concept(
            tmp_path,
            f"concepts/absorbed{suffix}",
            title=f"Absorbed{suffix}",
            body=_TINY_ABSORBED_BODY,
        )
    calls = _patch_reconciliation(monkeypatch, _RECONCILED_BODY)

    # The control pair, with no flag: neither threshold is cleared, so the
    # pass is not planned. Without this the opt-in would prove nothing.
    without = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )
    assert without.exit_code == 0, without.stderr
    assert "reconcile merged body" not in without.stdout
    assert calls == []

    result = runner.invoke(
        app,
        [
            "merge",
            "concepts/survivor2",
            "concepts/absorbed2",
            "--auto",
            "--reconcile",
        ],
    )

    assert result.exit_code == 0, result.stderr
    assert "reconcile merged body" in result.stdout
    assert len(calls) == 1
    survivor_text = (tmp_path / "bundle" / "concepts" / "survivor2.md").read_text(
        encoding="utf-8"
    )
    assert _RECONCILED_BODY in survivor_text
    assert "## Merged content" not in survivor_text


def _blank_the_body(tmp_path: Path, concept_id: str) -> None:
    """Reduce an already-written concept to frontmatter plus whitespace.

    `_write_concept` always emits an `# {title}` heading, and the stacking
    report reads the WHOLE post-frontmatter body, so `body=""` alone still
    stacks the heading. This is the only shape that reaches
    `_reconcile_planned`'s clause 2."""
    path = tmp_path / "bundle" / f"{concept_id}.md"
    metadata, _ = okf.load_frontmatter(path.read_text(encoding="utf-8"))
    path.write_text(okf.dump_frontmatter(metadata) + "\n   \n", encoding="utf-8")


def test_merge_reconcile_flag_still_skips_an_absorbed_body_that_stacks_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`_reconcile_planned` clause 2 outranks clause 3: an absorbed body
    that strips to nothing returns `False` EVEN under `--reconcile`.

    Raised as a reliability finding by #803's four-lens review, which had
    no correction slot because nothing blocked. Every other `--reconcile`
    test pairs the flag with a real absorbed body, so clauses 2 and 3
    could be swapped and the whole suite would still pass -- while the
    reconciliation pass sent the survivor's OWN text to the model to be
    rewritten against nothing.

    Asserted on the MODEL CALL, not on the preview line: a pass that is
    disclosed but not run and a pass that is run but not disclosed are
    different defects, and the expensive, output-changing half is the
    call."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(
        tmp_path, "concepts/survivor", title="Survivor", body=_TINY_SURVIVOR_BODY
    )
    _write_concept(tmp_path, "concepts/absorbed", title="Absorbed")
    _blank_the_body(tmp_path, "concepts/absorbed")
    calls = _patch_reconciliation(monkeypatch, _RECONCILED_BODY)

    result = runner.invoke(
        app,
        [
            "merge",
            "concepts/survivor",
            "concepts/absorbed",
            "--auto",
            "--reconcile",
        ],
    )

    assert result.exit_code == 0, result.stderr
    assert calls == []
    assert "reconcile merged body" not in result.stdout
    survivor_text = (tmp_path / "bundle" / "concepts" / "survivor.md").read_text(
        encoding="utf-8"
    )
    assert _RECONCILED_BODY not in survivor_text
    assert _TINY_SURVIVOR_BODY in survivor_text


def test_merge_refuses_reconcile_together_with_no_reconcile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#803: the two levers contradict each other, so the pair is REFUSED
    rather than silently resolved -- the same up-front, exit-2 shape
    `adjudicate` uses for `--apply` plus `--json`."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path, "concepts/survivor", title="Survivor", body=_LONG_BODY)
    _write_concept(tmp_path, "concepts/absorbed", title="Absorbed", body=_LONG_BODY)
    before = _snapshot(tmp_path)

    result = runner.invoke(
        app,
        [
            "merge",
            "concepts/survivor",
            "concepts/absorbed",
            "--auto",
            "--reconcile",
            "--no-reconcile",
        ],
    )

    assert result.exit_code == 2
    assert "--reconcile and --no-reconcile are mutually exclusive" in result.stderr
    assert _snapshot(tmp_path) == before


def test_merge_reconciliation_failure_falls_back_to_the_stacked_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A refused/failed reconciliation keeps the stacked body, notices on
    stderr, and the merge still succeeds (exit 0) -- the pass is an
    improvement step, never a new failure mode for the merge itself."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path, "concepts/survivor", title="Survivor", body=_LONG_BODY)
    _write_concept(tmp_path, "concepts/absorbed", title="Absorbed", body=_LONG_BODY)
    calls = _patch_reconciliation(monkeypatch, None)

    result = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )

    assert result.exit_code == 0, result.stderr
    assert len(calls) == 1
    assert "kept the stacked body" in result.stderr
    survivor_text = (tmp_path / "bundle" / "concepts" / "survivor.md").read_text(
        encoding="utf-8"
    )
    assert "## Merged content (concepts/absorbed)" in survivor_text


def test_merge_reconciliation_preview_lands_before_the_confirm_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ruling's disclosure requirement: the reconcile line is part of
    the plan the human consents to, and declining leaves the bundle
    untouched with zero model calls."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path, "concepts/survivor", title="Survivor", body=_LONG_BODY)
    _write_concept(tmp_path, "concepts/absorbed", title="Absorbed", body=_LONG_BODY)
    calls = _patch_reconciliation(monkeypatch, _RECONCILED_BODY)
    _simulate_tty(monkeypatch)
    before = _snapshot(tmp_path)

    result = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed"], input="n\n"
    )

    assert result.exit_code == 1
    assert "reconcile merged body" in result.stdout
    assert calls == []
    assert _snapshot(tmp_path) == before


# --- #796: the cross-source warning reaches plain `merge` too --------------


def test_cross_source_merge_warns_before_the_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#776 locked the batch door and left this one open. `merge` is what
    `duplicates` and `adjudicate` BOTH print as their closing hint, so it is
    the path a user most likely takes — and it was the only one of the four
    that never named the class that fuses two distinct real-world items."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept_with_provenance(
        tmp_path,
        "events/afg-eval",
        title="AFG Eval",
        provenance=["sources/transcript-1"],
    )
    _write_concept_with_provenance(
        tmp_path,
        "events/afg-eval-2",
        title="AFG Eval",
        provenance=["sources/transcript-3"],
    )

    result = runner.invoke(
        app, ["merge", "events/afg-eval", "events/afg-eval-2", "--auto"]
    )

    assert result.exit_code == 0, result.stderr
    assert "cross-source SAME" in result.stdout
    assert "may be distinct real-world items" in result.stdout


def test_a_shared_source_merge_carries_no_cross_source_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The warning names one specific risky class. Printing it on every
    merge would make it furniture, and the two concepts here came from the
    SAME source, which is the ordinary duplicate case."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept_with_provenance(
        tmp_path,
        "events/afg-eval",
        title="AFG Eval",
        provenance=["sources/transcript-1"],
    )
    _write_concept_with_provenance(
        tmp_path,
        "events/afg-eval-2",
        title="AFG Eval",
        provenance=["sources/transcript-1"],
    )

    result = runner.invoke(
        app, ["merge", "events/afg-eval", "events/afg-eval-2", "--auto"]
    )

    assert result.exit_code == 0, result.stderr
    assert "cross-source" not in result.stdout


def test_a_hand_written_concept_merge_carries_no_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Absence of provenance is no signal, not a risk signal — flagging on
    absence would mark every hand-authored concept forever
    (`_cross_source_same_pair`'s own documented posture)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept_with_provenance(tmp_path, "concepts/a", title="A")
    _write_concept_with_provenance(tmp_path, "concepts/b", title="A")

    result = runner.invoke(app, ["merge", "concepts/a", "concepts/b", "--auto"])

    assert result.exit_code == 0, result.stderr
    assert "cross-source" not in result.stdout
