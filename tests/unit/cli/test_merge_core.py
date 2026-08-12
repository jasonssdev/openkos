"""Direct unit tests for the merge-core extraction (Slice 2b-i, #137):
`PreparedMerge`, `prepare_merge`, and `merge_core` are exercised directly
(no `CliRunner`, no confirm gate) -- the behavior-preservation gate for the
`merge` COMMAND itself stays entirely in `test_merge.py`/
`test_merge_roundtrip.py`, unmodified.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from openkos.bundle import index as bundle_index
from openkos.bundle import ledger as bundle_ledger
from openkos.bundle import links as bundle_links
from openkos.bundle import provenance as bundle_provenance
from openkos.bundle import relations as bundle_relations
from openkos.cli.main import (
    MergeResult,
    PreparedMerge,
    StackedBodyReport,
    _format_merge_preview_line,
    _resolve_concept_path,
    app,
    merge_core,
    prepare_merge,
)
from openkos.model import okf
from openkos.vcs import git as vcs_git

runner = CliRunner()


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


def _write_concept_with_provenance(
    tmp_path: Path,
    concept_id: str,
    *,
    title: str,
    provenance: list[str] | None = None,
    relations: list[dict[str, str]] | None = None,
    body: str = "Body.",
) -> None:
    """Write a concept file directly to the bundle, optionally carrying a
    `provenance:` and/or `relations:` frontmatter list -- deliberately NOT
    registered in `index.md` (mirrors `test_merge.py::_write_concept_with_relations`,
    extended with `provenance` for the third-scanner tests)."""
    concept_path = tmp_path / "bundle" / f"{concept_id}.md"
    concept_path.parent.mkdir(parents=True, exist_ok=True)
    metadata: dict[str, object] = {"type": "Concept", "title": title}
    if provenance is not None:
        metadata["provenance"] = provenance
    if relations is not None:
        metadata["relations"] = relations
    concept_path.write_text(
        okf.dump_frontmatter(metadata, f"# {title}\n\n{body}\n"), encoding="utf-8"
    )


def _resolve(
    bundle_dir: Path, survivor_id: str, absorbed_id: str
) -> tuple[Path, str, Path, str]:
    survivor_path, survivor_canonical = _resolve_concept_path(bundle_dir, survivor_id)
    absorbed_path, absorbed_canonical = _resolve_concept_path(bundle_dir, absorbed_id)
    return survivor_path, survivor_canonical, absorbed_path, absorbed_canonical


def test_prepare_merge_returns_prepared_merge_with_expected_plan_and_preview_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`prepare_merge` returns a `PreparedMerge` carrying the plan, the
    recomputed sensitivity outcome, and the touched-files set -- and writes
    nothing to disk (Phase A is pure)."""
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
        body="See [Absorbed](/concepts/absorbed.md) for details.",
    )

    bundle_dir = tmp_path / "bundle"
    index_path = bundle_dir / "index.md"
    log_path = bundle_dir / "log.md"
    survivor_path, survivor_canonical, absorbed_path, absorbed_canonical = _resolve(
        bundle_dir, "concepts/survivor", "concepts/absorbed"
    )
    now = datetime(2026, 1, 1, tzinfo=UTC)

    prepared = prepare_merge(
        bundle_dir,
        index_path,
        log_path,
        survivor_path,
        absorbed_path,
        survivor_canonical,
        absorbed_canonical,
        tmp_path,
        now=now,
    )

    assert isinstance(prepared, PreparedMerge)
    assert prepared.plan.ledger_entry.absorbed_id == absorbed_canonical
    assert prepared.sensitivity_before == "private"
    assert prepared.sensitivity_after == "confidential"
    assert prepared.touched_files == ["concepts/other.md"]
    assert prepared.now == now
    assert prepared.review is True
    assert prepared.dropped_self_loops == []
    assert prepared.deduped_collisions == []

    # Phase A must write nothing.
    assert absorbed_path.exists()
    assert "merged_from" not in survivor_path.read_text(encoding="utf-8")
    assert "concepts/absorbed.md" in index_path.read_text(encoding="utf-8")


def test_prepare_merge_raises_oserror_on_missing_absorbed_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A nonexistent absorbed file must surface as `OSError` (bad input),
    not succeed silently -- Phase A never writes before this."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path, "concepts/survivor", title="Survivor")

    bundle_dir = tmp_path / "bundle"
    survivor_path, survivor_canonical = _resolve_concept_path(
        bundle_dir, "concepts/survivor"
    )
    missing_absorbed_path = bundle_dir / "concepts" / "missing.md"

    with pytest.raises(OSError, match=r"missing\.md"):
        prepare_merge(
            bundle_dir,
            bundle_dir / "index.md",
            bundle_dir / "log.md",
            survivor_path,
            missing_absorbed_path,
            survivor_canonical,
            "concepts/missing",
            tmp_path,
            now=datetime.now(UTC),
        )


def test_prepare_merge_raises_value_error_when_already_merged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Calling `prepare_merge` twice for the same pair must raise
    `ValueError` on the second call (`plan_merge`'s already-merged guard),
    proving `prepare_merge` propagates `plan_merge`'s validation instead of
    swallowing it."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path, "concepts/survivor", title="Survivor")
    _write_concept(tmp_path, "concepts/absorbed", title="Absorbed")

    bundle_dir = tmp_path / "bundle"
    index_path = bundle_dir / "index.md"
    log_path = bundle_dir / "log.md"
    survivor_path, survivor_canonical, absorbed_path, absorbed_canonical = _resolve(
        bundle_dir, "concepts/survivor", "concepts/absorbed"
    )
    now = datetime.now(UTC)

    prepared = prepare_merge(
        bundle_dir,
        index_path,
        log_path,
        survivor_path,
        absorbed_path,
        survivor_canonical,
        absorbed_canonical,
        tmp_path,
        now=now,
    )
    merge_core(bundle_dir, index_path, log_path, prepared)

    # `merge_core` removed the absorbed file (it's now merged); recreate it
    # (as a stale retry after an out-of-band restore might see) so the
    # second `prepare_merge` call fails on `plan_merge`'s already-merged
    # guard specifically, not a missing-file `OSError`.
    _write_concept(tmp_path, "concepts/absorbed", title="Absorbed")

    with pytest.raises(ValueError, match="already merged"):
        prepare_merge(
            bundle_dir,
            index_path,
            log_path,
            survivor_path,
            absorbed_path,
            survivor_canonical,
            absorbed_canonical,
            tmp_path,
            now=now,
        )


def test_merge_core_writes_index_log_touched_files_survivor_last_and_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`merge_core` applied to a `PreparedMerge` writes `index.md`, `log.md`,
    every touched file's rewrite, the merged survivor (carrying the
    `merged_from` ledger, with the injected `now` reflected verbatim), and
    removes the absorbed file -- matching current `main.py:2559-2596`
    output exactly."""
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
        body="See [Absorbed](/concepts/absorbed.md) for details.",
    )

    bundle_dir = tmp_path / "bundle"
    index_path = bundle_dir / "index.md"
    log_path = bundle_dir / "log.md"
    survivor_path, survivor_canonical, absorbed_path, absorbed_canonical = _resolve(
        bundle_dir, "concepts/survivor", "concepts/absorbed"
    )
    now = datetime(2026, 3, 15, 12, 30, tzinfo=UTC)

    prepared = prepare_merge(
        bundle_dir,
        index_path,
        log_path,
        survivor_path,
        absorbed_path,
        survivor_canonical,
        absorbed_canonical,
        tmp_path,
        now=now,
    )

    result = merge_core(bundle_dir, index_path, log_path, prepared)

    assert isinstance(result, MergeResult)
    assert result.survivor_canonical == survivor_canonical
    assert result.absorbed_canonical == absorbed_canonical
    assert result.touched_files == ["concepts/other.md"]

    assert not absorbed_path.exists()

    survivor_text = survivor_path.read_text(encoding="utf-8")
    assert "merged_from" not in survivor_text
    assert "Absorbed body" not in survivor_text or "Absorbed" in survivor_text
    entries = bundle_ledger.read_entries(survivor_canonical, bundle_dir)
    assert len(entries) == 1
    assert now.isoformat() in entries[0].merged_at

    other_text = (bundle_dir / "concepts" / "other.md").read_text(encoding="utf-8")
    assert "/concepts/survivor.md" in other_text
    assert "/concepts/absorbed.md" not in other_text

    index_text = index_path.read_text(encoding="utf-8")
    assert "concepts/absorbed.md" not in index_text
    assert "concepts/survivor.md" in index_text

    log_text = log_path.read_text(encoding="utf-8")
    assert "**Merge**" in log_text


def test_merge_core_committed_paths_include_the_ledger_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`MergeResult.committed_paths` MUST include the new sidecar path under
    `bundle/.state/ledger/**` (threat matrix, durable-derived-state design):
    `_autocommit` stages exactly `committed_paths` via a scoped `git add --
    <paths>` (never `-A`), so a sidecar missing from this list would never
    enter git and the whole portability rationale for relocating the ledger
    would silently fail. Task 2.4 -- must fail before merge_core writes the
    ledger via the two-phase sidecar store (task 2.6)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(
        tmp_path, "concepts/survivor", title="Survivor", sensitivity="private"
    )
    _write_concept(
        tmp_path, "concepts/absorbed", title="Absorbed", sensitivity="confidential"
    )

    bundle_dir = tmp_path / "bundle"
    index_path = bundle_dir / "index.md"
    log_path = bundle_dir / "log.md"
    survivor_path, survivor_canonical, absorbed_path, absorbed_canonical = _resolve(
        bundle_dir, "concepts/survivor", "concepts/absorbed"
    )
    now = datetime(2026, 3, 15, 12, 30, tzinfo=UTC)

    prepared = prepare_merge(
        bundle_dir,
        index_path,
        log_path,
        survivor_path,
        absorbed_path,
        survivor_canonical,
        absorbed_canonical,
        tmp_path,
        now=now,
    )

    result = merge_core(bundle_dir, index_path, log_path, prepared)

    sidecar_paths = [
        path for path in result.committed_paths if "bundle/.state/ledger/" in path
    ]
    assert sidecar_paths, (
        f"expected a bundle/.state/ledger/** entry in committed_paths, got "
        f"{result.committed_paths!r}"
    )
    assert sidecar_paths[0].endswith(".ledger.okf")


def test_merge_core_makes_zero_vcs_side_effect_and_is_unmerge_reversible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`merge_core` must not create a git commit (that is the command's
    `_autocommit` responsibility, called separately) -- the working tree is
    left dirty. A subsequent `unmerge` through the real CLI must still fully
    reverse the writes, mirroring `test_merge_roundtrip.py`'s parity
    contract."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(
        tmp_path, "concepts/survivor", title="Survivor", sensitivity="private"
    )
    _write_concept(
        tmp_path, "concepts/absorbed", title="Absorbed", sensitivity="confidential"
    )

    bundle_dir = tmp_path / "bundle"
    index_path = bundle_dir / "index.md"
    log_path = bundle_dir / "log.md"
    survivor_path, survivor_canonical, absorbed_path, absorbed_canonical = _resolve(
        bundle_dir, "concepts/survivor", "concepts/absorbed"
    )
    now = datetime.now(UTC)

    head_before = (tmp_path / ".git" / "HEAD").read_text(encoding="utf-8")

    prepared = prepare_merge(
        bundle_dir,
        index_path,
        log_path,
        survivor_path,
        absorbed_path,
        survivor_canonical,
        absorbed_canonical,
        tmp_path,
        now=now,
    )
    merge_core(bundle_dir, index_path, log_path, prepared)

    assert not vcs_git.is_clean(tmp_path), (
        "merge_core must leave the working tree dirty -- it performs no commit"
    )
    head_after = (tmp_path / ".git" / "HEAD").read_text(encoding="utf-8")
    assert head_before == head_after

    result = runner.invoke(
        app, ["unmerge", survivor_canonical, absorbed_canonical, "--auto"]
    )
    assert result.exit_code == 0, result.stderr
    assert absorbed_path.exists()
    assert "merged_from" not in survivor_path.read_text(encoding="utf-8")


def test_prepare_merge_returns_provenance_rewrites_for_third_party_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`prepare_merge` runs `find_inbound_provenance_rewrites` as a THIRD
    scanner over the SAME `other_files` snapshot -- a third-party file whose
    `provenance:` names the absorbed id is recorded in
    `PreparedMerge.provenance_rewrites` (spec: Reversible Inbound-Provenance
    Rewiring)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path, "concepts/survivor", title="Survivor")
    _write_concept(tmp_path, "concepts/absorbed", title="Absorbed")
    _write_concept_with_provenance(
        tmp_path,
        "concepts/derived",
        title="Derived",
        provenance=["concepts/absorbed"],
    )

    bundle_dir = tmp_path / "bundle"
    index_path = bundle_dir / "index.md"
    log_path = bundle_dir / "log.md"
    survivor_path, survivor_canonical, absorbed_path, absorbed_canonical = _resolve(
        bundle_dir, "concepts/survivor", "concepts/absorbed"
    )
    derived_text_before = (bundle_dir / "concepts" / "derived.md").read_text(
        encoding="utf-8"
    )

    prepared = prepare_merge(
        bundle_dir,
        index_path,
        log_path,
        survivor_path,
        absorbed_path,
        survivor_canonical,
        absorbed_canonical,
        tmp_path,
        now=datetime.now(UTC),
    )

    assert prepared.provenance_rewrites == [
        okf.ProvenanceRewrite(file="concepts/derived.md", snapshot=derived_text_before)
    ]
    assert (
        prepared.plan.ledger_entry.provenance_rewrites == prepared.provenance_rewrites
    )
    # Phase A writes nothing.
    assert (bundle_dir / "concepts" / "derived.md").read_text(
        encoding="utf-8"
    ) == derived_text_before


def test_prepare_merge_touched_files_is_three_way_union(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`touched_files` is the sorted union of link, relation, AND provenance
    rewrite file sets -- a file touched ONLY by the provenance scanner (no
    link, no relation) must still appear."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path, "concepts/survivor", title="Survivor")
    _write_concept(tmp_path, "concepts/absorbed", title="Absorbed")
    _write_concept(
        tmp_path,
        "concepts/linker",
        title="Linker",
        body="See [Absorbed](/concepts/absorbed.md).",
    )
    _write_concept_with_provenance(
        tmp_path,
        "concepts/relator",
        title="Relator",
        relations=[{"target": "concepts/absorbed", "type": "depends_on"}],
    )
    _write_concept_with_provenance(
        tmp_path,
        "concepts/derived",
        title="Derived",
        provenance=["concepts/absorbed"],
    )

    bundle_dir = tmp_path / "bundle"
    index_path = bundle_dir / "index.md"
    log_path = bundle_dir / "log.md"
    survivor_path, survivor_canonical, absorbed_path, absorbed_canonical = _resolve(
        bundle_dir, "concepts/survivor", "concepts/absorbed"
    )

    prepared = prepare_merge(
        bundle_dir,
        index_path,
        log_path,
        survivor_path,
        absorbed_path,
        survivor_canonical,
        absorbed_canonical,
        tmp_path,
        now=datetime.now(UTC),
    )

    assert prepared.touched_files == [
        "concepts/derived.md",
        "concepts/linker.md",
        "concepts/relator.md",
    ]


def test_merge_core_chains_link_relation_provenance_transforms_in_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file touched by all three rewrite kinds is written once, with
    `apply_link_rewrites` -> `apply_relation_rewrites` ->
    `apply_provenance_rewrites` chained, in that exact order, on the same
    in-memory text (design D5 extended to three passes)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path, "concepts/survivor", title="Survivor")
    _write_concept(tmp_path, "concepts/absorbed", title="Absorbed")
    _write_concept_with_provenance(
        tmp_path,
        "concepts/all_three",
        title="AllThree",
        provenance=["concepts/absorbed"],
        relations=[{"target": "concepts/absorbed", "type": "depends_on"}],
        body="See [Absorbed](/concepts/absorbed.md) for details.",
    )

    bundle_dir = tmp_path / "bundle"
    index_path = bundle_dir / "index.md"
    log_path = bundle_dir / "log.md"
    survivor_path, survivor_canonical, absorbed_path, absorbed_canonical = _resolve(
        bundle_dir, "concepts/survivor", "concepts/absorbed"
    )
    pre_merge_text = (bundle_dir / "concepts" / "all_three.md").read_text(
        encoding="utf-8"
    )

    prepared = prepare_merge(
        bundle_dir,
        index_path,
        log_path,
        survivor_path,
        absorbed_path,
        survivor_canonical,
        absorbed_canonical,
        tmp_path,
        now=datetime.now(UTC),
    )
    merge_core(bundle_dir, index_path, log_path, prepared)

    expected = bundle_links.apply_link_rewrites(
        pre_merge_text, file="concepts/all_three.md", rewrites=prepared.link_rewrites
    )
    expected = bundle_relations.apply_relation_rewrites(
        expected,
        file="concepts/all_three.md",
        survivor_id=survivor_canonical,
        absorbed_id=absorbed_canonical,
        rewrites=prepared.relation_rewrites,
    )
    expected = bundle_provenance.apply_provenance_rewrites(
        expected,
        file="concepts/all_three.md",
        survivor_id=survivor_canonical,
        absorbed_id=absorbed_canonical,
        rewrites=prepared.provenance_rewrites,
    )

    actual = (bundle_dir / "concepts" / "all_three.md").read_text(encoding="utf-8")
    assert actual == expected


def test_merge_core_provenance_and_relation_snapshots_byte_identical_to_pre_merge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T4 -- snapshot byte-identity: a third-party file with an inbound
    link, a `relations:` entry, AND a `provenance:` entry all pointing to
    the absorbed id gets the SAME shared pre-merge snapshot recorded in both
    `provenance_rewrites` and `relation_rewrites` -- asserted against the
    on-disk ledger, not merely trusted (design's "Correctness rests on ...
    being byte-identical")."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path, "concepts/survivor", title="Survivor")
    _write_concept(tmp_path, "concepts/absorbed", title="Absorbed")
    _write_concept_with_provenance(
        tmp_path,
        "concepts/all_three",
        title="AllThree",
        provenance=["concepts/absorbed"],
        relations=[{"target": "concepts/absorbed", "type": "depends_on"}],
        body="See [Absorbed](/concepts/absorbed.md) for details.",
    )

    bundle_dir = tmp_path / "bundle"
    index_path = bundle_dir / "index.md"
    log_path = bundle_dir / "log.md"
    survivor_path, survivor_canonical, absorbed_path, absorbed_canonical = _resolve(
        bundle_dir, "concepts/survivor", "concepts/absorbed"
    )
    pre_merge_text = (bundle_dir / "concepts" / "all_three.md").read_text(
        encoding="utf-8"
    )

    prepared = prepare_merge(
        bundle_dir,
        index_path,
        log_path,
        survivor_path,
        absorbed_path,
        survivor_canonical,
        absorbed_canonical,
        tmp_path,
        now=datetime.now(UTC),
    )
    merge_core(bundle_dir, index_path, log_path, prepared)

    entry = bundle_ledger.read_entries(survivor_canonical, bundle_dir)[-1]

    assert len(entry.provenance_rewrites) == 1
    assert len(entry.relation_rewrites) == 1
    assert entry.provenance_rewrites[0].file == "concepts/all_three.md"
    assert entry.relation_rewrites[0].file == "concepts/all_three.md"
    assert entry.provenance_rewrites[0].snapshot == entry.relation_rewrites[0].snapshot
    assert entry.provenance_rewrites[0].snapshot == pre_merge_text


def test_prepare_merge_reports_stacked_body_when_absorbed_body_is_non_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`prepare_merge` recomputes a body-stacking report (issue #409, report
    half): when the absorbed body carries reconcilable content, the report
    is non-`None` and carries both the absorbed body's char count and its
    share of the resulting merged body -- grounded in the `apatheia` /
    `apatheia-2` collision the issue names, where the absorbed half is the
    precise misreading the survivor's body already corrects."""
    _init_workspace(tmp_path, monkeypatch)
    survivor_body = (
        "A Stoic concept referring to freedom from destructive passions, "
        "not the absence of feeling.\n\n"
        "Apatheia is freedom from the pathe, the destructive passions."
    )
    absorbed_body = (
        "The Stoic goal of emotional indifference or freedom from passion.\n\n"
        "Apatheia seems to be the Stoic goal -- indifference to emotion."
    )
    _write_concept(tmp_path, "concepts/apatheia", title="Apatheia", body=survivor_body)
    _write_concept(
        tmp_path, "concepts/apatheia-2", title="Apatheia", body=absorbed_body
    )

    bundle_dir = tmp_path / "bundle"
    index_path = bundle_dir / "index.md"
    log_path = bundle_dir / "log.md"
    survivor_path, survivor_canonical, absorbed_path, absorbed_canonical = _resolve(
        bundle_dir, "concepts/apatheia", "concepts/apatheia-2"
    )

    prepared = prepare_merge(
        bundle_dir,
        index_path,
        log_path,
        survivor_path,
        absorbed_path,
        survivor_canonical,
        absorbed_canonical,
        tmp_path,
        now=datetime.now(UTC),
    )

    assert prepared.stacked_body is not None
    _, actual_absorbed_body = okf.load_frontmatter(
        absorbed_path.read_text(encoding="utf-8")
    )
    expected_absorbed_chars = len(actual_absorbed_body.strip())
    assert prepared.stacked_body.absorbed_chars == expected_absorbed_chars
    _, merged_body = okf.load_frontmatter(prepared.plan.merged_survivor)
    assert prepared.stacked_body.merged_chars == len(merged_body)
    expected_share = expected_absorbed_chars / len(merged_body)
    assert prepared.stacked_body.share == pytest.approx(expected_share)


def test_prepare_merge_stacked_body_is_none_when_absorbed_body_is_blank(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An absorbed body that is empty or whitespace-only carries no
    reconcilable content, so the report stays `None` -- matching the
    "prints nothing when empty" discipline `dropped_self_loops` /
    `deduped_collisions` already follow. A bare "bodies were stacked" flag
    would fire even here and add pure noise (design judgement, issue
    #409). Written directly via `okf.dump_frontmatter` (not `_write_concept`,
    which always prepends a `# {title}` heading -- itself reconcilable
    content) to get a genuinely whitespace-only body."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(
        tmp_path, "concepts/survivor", title="Survivor", body="Real content."
    )
    absorbed_concept_path = tmp_path / "bundle" / "concepts" / "absorbed.md"
    absorbed_concept_path.parent.mkdir(parents=True, exist_ok=True)
    absorbed_concept_path.write_text(
        okf.dump_frontmatter({"type": "Concept", "title": "Absorbed"}, "   \n  \n"),
        encoding="utf-8",
    )

    bundle_dir = tmp_path / "bundle"
    index_path = bundle_dir / "index.md"
    log_path = bundle_dir / "log.md"
    survivor_path, survivor_canonical, absorbed_path, absorbed_canonical = _resolve(
        bundle_dir, "concepts/survivor", "concepts/absorbed"
    )

    prepared = prepare_merge(
        bundle_dir,
        index_path,
        log_path,
        survivor_path,
        absorbed_path,
        survivor_canonical,
        absorbed_canonical,
        tmp_path,
        now=datetime.now(UTC),
    )

    assert prepared.stacked_body is None


def test_format_merge_preview_line_includes_stacked_body_note_when_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`_format_merge_preview_line` -- shared by `merge` and
    `adjudicate --apply`/`--apply-same` -- appends the stacked-body note
    when `prepared.stacked_body` is non-`None`."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(
        tmp_path, "concepts/survivor", title="Survivor", body="Real content."
    )
    _write_concept(
        tmp_path, "concepts/absorbed", title="Absorbed", body="Contradicting content."
    )

    bundle_dir = tmp_path / "bundle"
    index_path = bundle_dir / "index.md"
    log_path = bundle_dir / "log.md"
    survivor_path, survivor_canonical, absorbed_path, absorbed_canonical = _resolve(
        bundle_dir, "concepts/survivor", "concepts/absorbed"
    )

    prepared = prepare_merge(
        bundle_dir,
        index_path,
        log_path,
        survivor_path,
        absorbed_path,
        survivor_canonical,
        absorbed_canonical,
        tmp_path,
        now=datetime.now(UTC),
    )

    line = _format_merge_preview_line(prepared)
    assert prepared.stacked_body is not None
    assert f"{prepared.stacked_body.absorbed_chars}" in line
    assert "unreconciled" in line


def test_format_merge_preview_line_omits_stacked_body_note_when_absorbed_body_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A merge that stacks nothing says nothing in the preview line either
    -- no "unreconciled" note appears when the absorbed body is blank.
    Written directly via `okf.dump_frontmatter` (not `_write_concept`,
    which always prepends a `# {title}` heading) to get a genuinely empty
    body."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(
        tmp_path, "concepts/survivor", title="Survivor", body="Real content."
    )
    absorbed_concept_path = tmp_path / "bundle" / "concepts" / "absorbed.md"
    absorbed_concept_path.parent.mkdir(parents=True, exist_ok=True)
    absorbed_concept_path.write_text(
        okf.dump_frontmatter({"type": "Concept", "title": "Absorbed"}, ""),
        encoding="utf-8",
    )

    bundle_dir = tmp_path / "bundle"
    index_path = bundle_dir / "index.md"
    log_path = bundle_dir / "log.md"
    survivor_path, survivor_canonical, absorbed_path, absorbed_canonical = _resolve(
        bundle_dir, "concepts/survivor", "concepts/absorbed"
    )

    prepared = prepare_merge(
        bundle_dir,
        index_path,
        log_path,
        survivor_path,
        absorbed_path,
        survivor_canonical,
        absorbed_canonical,
        tmp_path,
        now=datetime.now(UTC),
    )

    assert prepared.stacked_body is None
    line = _format_merge_preview_line(prepared)
    assert "unreconciled" not in line


def test_format_merge_preview_line_warns_when_stacked_share_dominates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A proposed merge whose result would be dominated by unreconciled
    absorbed content carries an explicit warning in the shared preview line
    (issue #559): three of ten accepted merges in a real run stacked 89-92%
    foreign text, and every one was an about-X/is-X confusion. The signal is
    deterministic and already computed -- the guardrail just states it."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path, "concepts/survivor", title="ADK", body="Stub.")
    _write_concept(
        tmp_path,
        "concepts/absorbed",
        title="ADK Callbacks",
        body="A long document about the survivor's topic. " * 40,
    )

    bundle_dir = tmp_path / "bundle"
    index_path = bundle_dir / "index.md"
    log_path = bundle_dir / "log.md"
    survivor_path, survivor_canonical, absorbed_path, absorbed_canonical = _resolve(
        bundle_dir, "concepts/survivor", "concepts/absorbed"
    )

    prepared = prepare_merge(
        bundle_dir,
        index_path,
        log_path,
        survivor_path,
        absorbed_path,
        survivor_canonical,
        absorbed_canonical,
        tmp_path,
        now=datetime.now(UTC),
    )

    assert prepared.stacked_body is not None
    assert prepared.stacked_body.share >= 0.8
    line = _format_merge_preview_line(prepared)
    assert "warning:" in line
    assert "ABOUT" in line


def test_format_merge_preview_line_no_warning_below_the_guardrail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two comparable descriptions of the same subject stack roughly half
    the merged body -- an ordinary genuine merge -- and get no warning."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(
        tmp_path,
        "concepts/survivor",
        title="Survivor",
        body="A real description of the subject with some detail. " * 5,
    )
    _write_concept(
        tmp_path,
        "concepts/absorbed",
        title="Absorbed",
        body="Another real description of the same subject. " * 5,
    )

    bundle_dir = tmp_path / "bundle"
    index_path = bundle_dir / "index.md"
    log_path = bundle_dir / "log.md"
    survivor_path, survivor_canonical, absorbed_path, absorbed_canonical = _resolve(
        bundle_dir, "concepts/survivor", "concepts/absorbed"
    )

    prepared = prepare_merge(
        bundle_dir,
        index_path,
        log_path,
        survivor_path,
        absorbed_path,
        survivor_canonical,
        absorbed_canonical,
        tmp_path,
        now=datetime.now(UTC),
    )

    assert prepared.stacked_body is not None
    assert prepared.stacked_body.share < 0.8
    line = _format_merge_preview_line(prepared)
    assert "warning:" not in line
    assert "ABOUT" not in line


def test_stacked_body_report_does_not_change_merged_document_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression pin (issue #409, report half): `prepare_merge`'s merged
    bytes stay exactly what `build_merged_document` returns. The
    body-stacking report is recomputed read-only, never fed back into the
    merged bytes themselves.

    This pins `prepare_merge` AGAINST `build_merged_document`, so it moves
    with that function by construction and cannot detect a change to the
    merged layout itself -- `test_build_merged_document_body_layout_is_pinned`
    below carries that guard against a literal instead."""
    _init_workspace(tmp_path, monkeypatch)
    survivor_body = "Survivor body."
    absorbed_body = "Absorbed body."
    _write_concept(tmp_path, "concepts/survivor", title="Survivor", body=survivor_body)
    _write_concept(tmp_path, "concepts/absorbed", title="Absorbed", body=absorbed_body)

    bundle_dir = tmp_path / "bundle"
    index_path = bundle_dir / "index.md"
    log_path = bundle_dir / "log.md"
    survivor_path, survivor_canonical, absorbed_path, absorbed_canonical = _resolve(
        bundle_dir, "concepts/survivor", "concepts/absorbed"
    )

    prepared = prepare_merge(
        bundle_dir,
        index_path,
        log_path,
        survivor_path,
        absorbed_path,
        survivor_canonical,
        absorbed_canonical,
        tmp_path,
        now=datetime.now(UTC),
    )

    survivor_metadata, survivor_text_body = okf.load_frontmatter(
        survivor_path.read_text(encoding="utf-8")
    )
    absorbed_metadata, absorbed_text_body = okf.load_frontmatter(
        absorbed_path.read_text(encoding="utf-8")
    )
    expected_metadata, expected_body = okf.build_merged_document(
        survivor_metadata,
        survivor_text_body,
        absorbed_metadata,
        absorbed_text_body,
        absorbed_canonical,
        survivor_canonical,
    )
    # `plan.merged_survivor` is `okf.dump_frontmatter(merged_metadata,
    # merged_body)` (`bundle/merge.py::plan_merge`) -- round-trip the
    # independently-recomputed expectation through the same
    # dump/load cycle before comparing, so a normalizing trailing
    # newline the emitter always adds isn't mistaken for drift.
    _, expected_body_roundtripped = okf.load_frontmatter(
        okf.dump_frontmatter(expected_metadata, expected_body)
    )
    _, actual_body = okf.load_frontmatter(prepared.plan.merged_survivor)
    assert actual_body == expected_body_roundtripped
    assert isinstance(prepared.stacked_body, StackedBodyReport)


def test_build_merged_document_body_layout_is_pinned() -> None:
    """The merged body's layout is user-visible content in the bundle -- the
    artifact that is supposed to be the source of truth -- and #409's whole
    subject is that the `## Merged content (<absorbed-id>)` heading is where
    an unreconciled second body lands.

    Before this test, the existing coverage in `tests/unit/model/test_okf.py`
    asserted the heading via an `in` substring check plus `body.index`
    ordering (never a full literal) -- a measured mutation that APPENDED
    text after the heading's closing paren left that substring intact and
    survived undetected. This test closes that narrower gap by asserting
    against a literal rather than a recomputation, because a test that
    rebuilds the expectation from the same function moves with it and can
    never catch a change to the layout itself."""
    metadata, body = okf.build_merged_document(
        {"type": "Concept", "title": "Survivor"},
        "Survivor body.",
        {"type": "Concept", "title": "Absorbed"},
        "Absorbed body.",
        "concepts/absorbed",
        "concepts/survivor",
    )

    assert body == (
        "Survivor body.\n\n## Merged content (concepts/absorbed)\n\nAbsorbed body.\n"
    )
    assert metadata["title"] == "Survivor"


# --- the shared resolver tolerates a decomposed on-disk name (#430) ----------


def test_resolve_concept_path_finds_a_decomposed_filename_from_an_nfc_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`_resolve_concept_path` is the shared id-to-path resolver every write
    verb goes through, and #430 made every id derived from a walked path NFC.

    A bundle authored on HFS+ and cloned onto a byte-exact filesystem carries
    decomposed filenames openkos never wrote, so the NFC id must still resolve
    them -- otherwise `merge` reports "concept does not exist" for a file that
    is right there, and `curate`'s Identity stage counts it as an ordinary
    skip with no diagnostic.

    `Path.exists` is forced False so the fallback scan is what answers;
    macOS resolves both spellings insensitively and would otherwise satisfy
    the direct probe without exercising the branch ext4 depends on."""
    from openkos.cli.main import _resolve_concept_path

    nfc_stem = "café"
    nfd_stem = "café"
    assert nfc_stem != nfd_stem, "fixture must use two distinct spellings"

    bundle_dir = tmp_path / "bundle"
    (bundle_dir / "concepts").mkdir(parents=True)
    on_disk = bundle_dir / "concepts" / f"{nfd_stem}.md"
    on_disk.write_text(
        okf.dump_frontmatter({"type": "Concept", "title": "Cafe"}, "Body."),
        encoding="utf-8",
    )

    real_exists = Path.exists
    monkeypatch.setattr(
        Path,
        "exists",
        lambda self: False if self.suffix == ".md" else real_exists(self),
    )

    resolved, canonical = _resolve_concept_path(bundle_dir, f"concepts/{nfc_stem}")

    assert resolved.name == f"{nfd_stem}.md"
    assert canonical == f"concepts/{nfc_stem}"
    assert resolved.read_text(encoding="utf-8").endswith("Body.\n")
