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
from openkos.bundle import links as bundle_links
from openkos.cli.main import _apply_link_rewrite_idempotently, app
from openkos.model import okf

runner = CliRunner()


def _simulate_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make `sys.stdin.isatty()` report `True` inside a `CliRunner.invoke` call."""
    monkeypatch.setattr(_NamedTextIOWrapper, "isatty", lambda self: True)


def _snapshot_entry(path: Path) -> tuple[bytes, int] | None:
    if path.is_dir():
        return None
    return path.read_bytes(), path.stat().st_mtime_ns


def _snapshot(root: Path) -> dict[Path, tuple[bytes, int] | None]:
    """Capture every entry's bytes AND `st_mtime_ns`, keyed by relative
    path -- a decline (or refusal) must leave the bundle untouched at the
    filesystem level, not merely byte-identical after a write-then-restore."""
    return {path.relative_to(root): _snapshot_entry(path) for path in root.rglob("*")}


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
    assert "merged_from" in survivor_text
    assert "## Merged content (concepts/absorbed)" in survivor_text
    assert "Absorbed body." in survivor_text
    assert "sensitivity: confidential" in survivor_text

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
    assert "merged_from" in survivor_text
    assert "sensitivity: confidential" in survivor_text

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

    retry = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )
    assert retry.exit_code == 0, retry.stderr  # NOT blocked by "already merged"

    assert not absorbed_path.exists()

    survivor_text = (tmp_path / "bundle" / "concepts" / "survivor.md").read_text(
        encoding="utf-8"
    )
    assert "merged_from" in survivor_text

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

    survivor_text = (tmp_path / "bundle" / "concepts" / "survivor.md").read_text(
        encoding="utf-8"
    )
    metadata, _ = okf.load_frontmatter(survivor_text)
    entries = okf.decode_merged_from(metadata)
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
