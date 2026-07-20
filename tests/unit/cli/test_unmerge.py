"""Unit tests for the `unmerge` CLI command: the reversal `merged_from`
(ADR-0002) makes possible -- a confirm-gated, two-arg, LIFO-tail-enforced
restore of the survivor/absorbed pair from their pre-merge snapshots,
mirroring `merge`/`forget`'s Phase A/B + confirm-gate shape (spec: Unmerge
Achieves Round-Trip Parity).

The two CENTRAL byte-parity property tests (single round-trip, sequential
LIFO round-trip) live in `test_merge_roundtrip.py`; this file covers the
command's own mechanics and threat matrix (LIFO-tail check, restore
collision, link drift, confirm gate, path safety).
"""

from pathlib import Path

import pytest
from typer.testing import CliRunner, _NamedTextIOWrapper

from openkos import fsio
from openkos.bundle import index as bundle_index
from openkos.cli.main import app

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
    matching `index.md` bullet (mirrors `test_merge.py::_write_concept`)."""
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


def test_unmerge_restores_survivor_absorbed_index_log_and_reverses_links(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Happy path: `merge` then `unmerge` restores the survivor, recreates
    the absorbed file, reverses the inbound-link rewrite in a THIRD file,
    restores `index.md`, and appends a `**Unmerge**` line to `log.md`
    (spec: Unmerge Achieves Round-Trip Parity)."""
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

    pre_survivor = (tmp_path / "bundle" / "concepts" / "survivor.md").read_text(
        encoding="utf-8"
    )
    pre_absorbed = (tmp_path / "bundle" / "concepts" / "absorbed.md").read_text(
        encoding="utf-8"
    )
    pre_other = (tmp_path / "bundle" / "concepts" / "other.md").read_text(
        encoding="utf-8"
    )
    pre_index = (tmp_path / "bundle" / "index.md").read_text(encoding="utf-8")
    pre_log = (tmp_path / "bundle" / "log.md").read_text(encoding="utf-8")

    merge_result = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )
    assert merge_result.exit_code == 0, merge_result.stderr

    unmerge_result = runner.invoke(
        app, ["unmerge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )
    assert unmerge_result.exit_code == 0, unmerge_result.stderr

    assert (tmp_path / "bundle" / "concepts" / "survivor.md").read_text(
        encoding="utf-8"
    ) == pre_survivor
    assert (tmp_path / "bundle" / "concepts" / "absorbed.md").read_text(
        encoding="utf-8"
    ) == pre_absorbed
    assert (tmp_path / "bundle" / "concepts" / "other.md").read_text(
        encoding="utf-8"
    ) == pre_other
    assert (tmp_path / "bundle" / "index.md").read_text(encoding="utf-8") == pre_index

    log_text = (tmp_path / "bundle" / "log.md").read_text(encoding="utf-8")
    assert log_text != pre_log
    assert "**Unmerge**" in log_text


def test_unmerge_of_non_merged_pair_refuses_no_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A survivor with no `merged_from` ledger at all refuses (exit 1) and
    writes nothing (spec scenario: Unmerge of a non-merged pair)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path, "concepts/survivor", title="Survivor")
    _write_concept(tmp_path, "concepts/absorbed", title="Absorbed")
    before = _snapshot(tmp_path)

    result = runner.invoke(
        app, ["unmerge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert _snapshot(tmp_path) == before


def test_unmerge_restore_collision_refuses_no_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If a file has since appeared at the absorbed concept's path (drift),
    `unmerge` refuses (exit 1) rather than overwrite it (threat matrix:
    Unmerge restore collision)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path, "concepts/survivor", title="Survivor")
    _write_concept(tmp_path, "concepts/absorbed", title="Absorbed")

    merge_result = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )
    assert merge_result.exit_code == 0, merge_result.stderr

    drift_path = tmp_path / "bundle" / "concepts" / "absorbed.md"
    drift_path.write_text("drifted content", encoding="utf-8")
    before = _snapshot(tmp_path)

    result = runner.invoke(
        app, ["unmerge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert _snapshot(tmp_path) == before


def test_unmerge_link_drift_fails_closed_no_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the rewritten inbound-link file drifted after the merge (its
    recorded `new_link` no longer sits at the recorded offset), `unmerge`
    degrades cleanly (exit 1) instead of corrupting the file, and writes
    nothing else either (threat matrix: Link-file drift before unmerge)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path, "concepts/survivor", title="Survivor")
    _write_concept(tmp_path, "concepts/absorbed", title="Absorbed")
    _write_concept(
        tmp_path,
        "concepts/other",
        title="Other",
        body="See [Absorbed](/concepts/absorbed.md).",
    )

    merge_result = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )
    assert merge_result.exit_code == 0, merge_result.stderr

    other_path = tmp_path / "bundle" / "concepts" / "other.md"
    drifted_text = other_path.read_text(encoding="utf-8").replace(
        "/concepts/survivor.md", "/concepts/elsewhere.md"
    )
    other_path.write_text(drifted_text, encoding="utf-8")
    before = _snapshot(tmp_path)

    result = runner.invoke(
        app, ["unmerge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "Traceback" not in result.stderr
    assert _snapshot(tmp_path) == before


def test_unmerge_auto_bypasses_the_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--auto` skips the confirmation prompt and Phase B proceeds directly."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path, "concepts/survivor", title="Survivor")
    _write_concept(tmp_path, "concepts/absorbed", title="Absorbed")
    _simulate_tty(monkeypatch)

    merge_result = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )
    assert merge_result.exit_code == 0, merge_result.stderr

    result = runner.invoke(
        app, ["unmerge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )

    assert result.exit_code == 0, result.stderr
    assert "Proceed" not in result.output
    assert (tmp_path / "bundle" / "concepts" / "absorbed.md").exists()


def test_unmerge_tty_confirm_prompts_then_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An interactive TTY prompts via `typer.confirm`; confirming proceeds
    with Phase B."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path, "concepts/survivor", title="Survivor")
    _write_concept(tmp_path, "concepts/absorbed", title="Absorbed")
    _simulate_tty(monkeypatch)

    merge_result = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )
    assert merge_result.exit_code == 0, merge_result.stderr

    result = runner.invoke(
        app, ["unmerge", "concepts/survivor", "concepts/absorbed"], input="y\n"
    )

    assert result.exit_code == 0, result.stderr
    assert (tmp_path / "bundle" / "concepts" / "absorbed.md").exists()


def test_unmerge_decline_at_prompt_writes_nothing_bytes_and_mtimes_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Declining the TTY confirm prompt aborts (exit 1) and leaves EVERY
    bundle file byte- and mtime-identical -- nothing written."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path, "concepts/survivor", title="Survivor")
    _write_concept(tmp_path, "concepts/absorbed", title="Absorbed")

    merge_result = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )
    assert merge_result.exit_code == 0, merge_result.stderr

    _simulate_tty(monkeypatch)
    before = _snapshot(tmp_path)

    result = runner.invoke(
        app, ["unmerge", "concepts/survivor", "concepts/absorbed"], input="n\n"
    )

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert _snapshot(tmp_path) == before


def test_unmerge_non_tty_without_auto_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`review: true`, non-TTY stdin, no `--auto` refuses (exit 1) and
    writes nothing."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path, "concepts/survivor", title="Survivor")
    _write_concept(tmp_path, "concepts/absorbed", title="Absorbed")

    merge_result = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )
    assert merge_result.exit_code == 0, merge_result.stderr
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["unmerge", "concepts/survivor", "concepts/absorbed"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "--auto" in result.stderr
    assert _snapshot(tmp_path) == before


def test_unmerge_missing_workspace_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A directory that is not an initialized workspace refuses (exit 1)
    with no raw traceback."""
    monkeypatch.chdir(tmp_path)
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["unmerge", "concepts/a", "concepts/b", "--auto"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "Traceback" not in result.stderr
    assert _snapshot(tmp_path) == before


def test_unmerge_unknown_survivor_id_refuses_no_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unknown `survivor-id` refuses (exit 1) and writes nothing."""
    _init_workspace(tmp_path, monkeypatch)
    before = _snapshot(tmp_path)

    result = runner.invoke(
        app, ["unmerge", "concepts/nonexistent", "concepts/absorbed", "--auto"]
    )

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert _snapshot(tmp_path) == before


def test_unmerge_path_traversal_on_survivor_id_refuses_no_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `..`-segment `survivor-id` refuses (exit 1) and writes nothing."""
    _init_workspace(tmp_path, monkeypatch)
    before = _snapshot(tmp_path)

    result = runner.invoke(
        app, ["unmerge", "../../evil", "concepts/absorbed", "--auto"]
    )

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert _snapshot(tmp_path) == before


def test_unmerge_path_traversal_on_absorbed_id_refuses_no_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `..`-segment `absorbed-id` refuses (exit 1) and writes nothing --
    proving path safety is enforced even though the absorbed file is
    EXPECTED to be absent (it was removed by the merge being reversed)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path, "concepts/survivor", title="Survivor")
    before = _snapshot(tmp_path)

    result = runner.invoke(
        app, ["unmerge", "concepts/survivor", "../../evil", "--auto"]
    )

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert _snapshot(tmp_path) == before


def test_unmerge_phase_b_ordering_survivor_ledger_kept_until_last(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`index.md`/`log.md`/reversed inbound links/the recreated absorbed
    file are all written BEFORE the survivor itself -- monkeypatching
    `fsio.write_atomic` to fail specifically on the survivor's own write
    proves the survivor's `merged_from` ledger entry (the one record of
    `absorbed_snapshot`) is kept intact on disk until the absorbed file it
    describes has actually landed, so a mid-way failure never loses either
    snapshot (spec: Unmerge Achieves Round-Trip Parity; mirrors `merge`'s
    own Phase B recoverability contract)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(
        tmp_path, "concepts/survivor", title="Survivor", sensitivity="private"
    )
    _write_concept(
        tmp_path, "concepts/absorbed", title="Absorbed", sensitivity="confidential"
    )

    merge_result = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )
    assert merge_result.exit_code == 0, merge_result.stderr

    survivor_path = tmp_path / "bundle" / "concepts" / "survivor.md"
    original_write_atomic = fsio.write_atomic

    def raising_write_atomic(path: Path, content: str) -> None:
        if path == survivor_path:
            raise OSError("simulated survivor write failure")
        original_write_atomic(path, content)

    monkeypatch.setattr(fsio, "write_atomic", raising_write_atomic)

    result = runner.invoke(
        app, ["unmerge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "Traceback" not in result.stderr

    # The absorbed file already landed -- recoverable, not lost.
    absorbed_path = tmp_path / "bundle" / "concepts" / "absorbed.md"
    assert absorbed_path.is_file()

    # The survivor was never overwritten -- its `merged_from` ledger entry
    # (the ledger/git recovery path) is still intact on disk.
    survivor_text = survivor_path.read_text(encoding="utf-8")
    assert "merged_from" in survivor_text

    # The catalog/log already reflect the pre-merge state.
    index_text = (tmp_path / "bundle" / "index.md").read_text(encoding="utf-8")
    assert "concepts/absorbed.md" in index_text


def test_retry_after_mid_reverse_failure_completes_the_unmerge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failure while WRITING the second reversed inbound-link file during
    Phase B must not permanently block a retry: the survivor's
    `merged_from` tail entry is still intact after the failure (nothing
    destructive has happened yet), and a clean re-run of
    `unmerge S A --auto` must complete the restoration -- recreating the
    absorbed file, reversing every inbound link, and restoring the
    survivor.

    Regression test for the half-completed-write retry trap `merge` fixed
    with `_apply_link_rewrite_idempotently`: `unmerge`'s Phase B has no
    idempotency guard on `bundle_links.reverse_link_rewrites`, so a retry
    re-reads the ALREADY-reversed first file and `reverse_link_rewrites`
    raises `ValueError` ("new_link not found at recorded offset") because
    the file now shows `old_link` -- refusing on EVERY retry even though
    the state is safe to resume."""
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

    merge_result = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )
    assert merge_result.exit_code == 0, merge_result.stderr

    # `rewritten_files` is processed in sorted order, so linker1 is written
    # (reversed) before linker2 -- inject the failure on the SECOND file.
    linker2_path = tmp_path / "bundle" / "concepts" / "linker2.md"
    original_write_atomic = fsio.write_atomic
    failures = {"count": 0}

    def flaky_write_atomic(path: Path, content: str) -> None:
        if path == linker2_path:
            failures["count"] += 1
            if failures["count"] == 1:
                raise OSError("simulated mid-reverse write failure")
        original_write_atomic(path, content)

    monkeypatch.setattr(fsio, "write_atomic", flaky_write_atomic)

    first = runner.invoke(
        app, ["unmerge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )
    assert first.exit_code == 1
    assert isinstance(first.exception, SystemExit)

    # Nothing destructive has happened: the survivor's `merged_from` tail
    # entry is still intact on disk.
    survivor_after_failure = (
        tmp_path / "bundle" / "concepts" / "survivor.md"
    ).read_text(encoding="utf-8")
    assert "merged_from" in survivor_after_failure

    # The key assertion: a clean retry completes the restoration instead of
    # being permanently refused by a stale "new_link not found" error.
    retry = runner.invoke(
        app, ["unmerge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )
    assert retry.exit_code == 0, retry.stderr

    absorbed_path = tmp_path / "bundle" / "concepts" / "absorbed.md"
    assert absorbed_path.is_file()

    survivor_text = (tmp_path / "bundle" / "concepts" / "survivor.md").read_text(
        encoding="utf-8"
    )
    assert "merged_from" not in survivor_text

    linker1_text = (tmp_path / "bundle" / "concepts" / "linker1.md").read_text(
        encoding="utf-8"
    )
    linker2_text = linker2_path.read_text(encoding="utf-8")
    assert "/concepts/absorbed.md" in linker1_text
    assert "/concepts/survivor.md" not in linker1_text
    assert "/concepts/absorbed.md" in linker2_text
    assert "/concepts/survivor.md" not in linker2_text


def test_unmerge_warns_on_interleaved_index_log_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If `index.md`/`log.md` changed since the merge (an `ingest`/`forget`/
    unrelated `merge` ran in between), `unmerge`'s preview surfaces a clear
    warning BEFORE the confirm gate instead of silently discarding those
    changes when it restores the pre-merge snapshot (principle #3:
    reviewable, not silent)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path, "concepts/survivor", title="Survivor")
    _write_concept(tmp_path, "concepts/absorbed", title="Absorbed")

    merge_result = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )
    assert merge_result.exit_code == 0, merge_result.stderr

    index_path = tmp_path / "bundle" / "index.md"
    log_path = tmp_path / "bundle" / "log.md"
    index_path.write_text(
        index_path.read_text(encoding="utf-8") + "\n* Unrelated bullet.\n",
        encoding="utf-8",
    )
    log_path.write_text(
        log_path.read_text(encoding="utf-8") + "\n* Unrelated log entry.\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app, ["unmerge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )

    assert result.exit_code == 0, result.stderr
    assert "changed since the merge" in result.stdout
    assert "discard" in result.stdout
