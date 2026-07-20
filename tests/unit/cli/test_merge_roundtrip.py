"""THE CENTRAL reversibility property tests for `merge`/`unmerge` (spec:
Unmerge Achieves Round-Trip Parity): a bundle-wide byte-parity contract,
not just per-field assertions.

Both tests snapshot every bundle file's bytes, run a `merge`/`unmerge`
sequence end-to-end through the real Typer CLI against a real `tmp_path`
workspace, and assert the resulting bundle is byte-identical to the
pre-merge snapshot -- MODULO `log.md`, whose append-only audit trail
net-grows (spec explicitly excludes it from the byte-parity claim; every
OTHER file, including the restored absorbed file and every reversed
inbound link, must match exactly).
"""

from pathlib import Path

import pytest
from typer.testing import CliRunner, _NamedTextIOWrapper

from openkos.bundle import index as bundle_index
from openkos.cli.main import app
from openkos.model import okf

runner = CliRunner()


def _simulate_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_NamedTextIOWrapper, "isatty", lambda self: True)


def _snapshot_entry(path: Path) -> tuple[bytes, int] | None:
    if path.is_dir():
        return None
    return path.read_bytes(), path.stat().st_mtime_ns


def _snapshot(root: Path) -> dict[Path, tuple[bytes, int] | None]:
    return {path.relative_to(root): _snapshot_entry(path) for path in root.rglob("*")}


def _bundle_bytes_snapshot(root: Path) -> dict[Path, bytes]:
    """Every bundle file's raw bytes, keyed by relative path -- the
    byte-parity comparison basis (mtime is irrelevant here; only content
    matters for round-trip parity)."""
    bundle_dir = root / "bundle"
    return {
        path.relative_to(root): path.read_bytes()
        for path in bundle_dir.rglob("*")
        if path.is_file()
    }


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


def _write_concept_with_relations(
    tmp_path: Path,
    concept_id: str,
    *,
    title: str,
    section: str = "Concepts",
    relations: list[dict[str, str]] | None = None,
    body: str = "Body.",
) -> None:
    """Same shape as `_write_concept`, but carries a `relations:` frontmatter
    list -- registered in `index.md` like every other concept here, so
    byte-parity comparisons cover it the same way."""
    concept_path = tmp_path / "bundle" / f"{concept_id}.md"
    concept_path.parent.mkdir(parents=True, exist_ok=True)
    metadata: dict[str, object] = {"type": "Concept", "title": title}
    if relations is not None:
        metadata["relations"] = relations
    concept_path.write_text(
        okf.dump_frontmatter(metadata, f"# {title}\n\n{body}\n"), encoding="utf-8"
    )

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


def _assert_byte_parity_except_log(root: Path, pre_snapshot: dict[Path, bytes]) -> None:
    """Assert the CURRENT bundle is byte-identical to `pre_snapshot` for
    every path except `bundle/log.md` -- the append-only audit trail is the
    ONE deliberate exception to round-trip byte parity (spec: Unmerge
    Achieves Round-Trip Parity)."""
    post_snapshot = _bundle_bytes_snapshot(root)
    log_path = Path("bundle/log.md")
    assert post_snapshot.keys() == pre_snapshot.keys()
    for path in pre_snapshot:
        if path == log_path:
            continue
        assert post_snapshot[path] == pre_snapshot[path], f"{path} drifted"


def test_single_merge_then_unmerge_is_byte_identical_modulo_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """merge(S, A) --auto -> unmerge(S, A) --auto restores every bundle
    file to byte parity with the pre-merge snapshot, EXCEPT `log.md` (which
    net-grows by the merge+unmerge audit lines) -- the absorbed file is
    recreated, every inbound link is reversed, the survivor is restored,
    and `index.md` matches exactly (spec: Unmerge Achieves Round-Trip
    Parity)."""
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

    pre_snapshot = _bundle_bytes_snapshot(tmp_path)

    merge_result = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )
    assert merge_result.exit_code == 0, merge_result.stderr

    unmerge_result = runner.invoke(
        app, ["unmerge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )
    assert unmerge_result.exit_code == 0, unmerge_result.stderr

    _assert_byte_parity_except_log(tmp_path, pre_snapshot)

    log_text = (tmp_path / "bundle" / "log.md").read_text(encoding="utf-8")
    pre_log_text = pre_snapshot[Path("bundle/log.md")].decode("utf-8")
    assert log_text != pre_log_text
    assert "**Unmerge**" in log_text


def test_sequential_lifo_merge_then_unmerge_is_byte_identical_modulo_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """merge(S,B) -> merge(S,C) -> unmerge(S,C) -> unmerge(S,B) restores
    the bundle to its ORIGINAL pre-any-merge byte state, modulo `log.md` --
    proving `survivor_before` correctly retains the PRIOR entry across a
    sequential merge, and that LIFO-ordered reversal correctly undoes both
    inbound-link rewrites even though a single linking file's link to BOTH
    B and C is touched by BOTH merges (spec: Unmerge Achieves Round-Trip
    Parity; Reversibility Ledger's sequential-merge requirement)."""
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
        "concepts/b",
        title="B",
        sensitivity="confidential",
        body="B body.",
    )
    _write_concept(
        tmp_path,
        "concepts/c",
        title="C",
        sensitivity="public",
        body="C body.",
    )
    _write_concept(
        tmp_path,
        "concepts/linker",
        title="Linker",
        body=("See [B](/concepts/b.md) and [C](/concepts/c.md) for details."),
    )

    pre_snapshot = _bundle_bytes_snapshot(tmp_path)

    merge_b = runner.invoke(app, ["merge", "concepts/survivor", "concepts/b", "--auto"])
    assert merge_b.exit_code == 0, merge_b.stderr

    merge_c = runner.invoke(app, ["merge", "concepts/survivor", "concepts/c", "--auto"])
    assert merge_c.exit_code == 0, merge_c.stderr

    linker_text_after_merges = (
        tmp_path / "bundle" / "concepts" / "linker.md"
    ).read_text(encoding="utf-8")
    assert "/concepts/survivor.md" in linker_text_after_merges
    assert "/concepts/b.md" not in linker_text_after_merges
    assert "/concepts/c.md" not in linker_text_after_merges

    unmerge_c = runner.invoke(
        app, ["unmerge", "concepts/survivor", "concepts/c", "--auto"]
    )
    assert unmerge_c.exit_code == 0, unmerge_c.stderr

    unmerge_b = runner.invoke(
        app, ["unmerge", "concepts/survivor", "concepts/b", "--auto"]
    )
    assert unmerge_b.exit_code == 0, unmerge_b.stderr

    _assert_byte_parity_except_log(tmp_path, pre_snapshot)

    log_text = (tmp_path / "bundle" / "log.md").read_text(encoding="utf-8")
    pre_log_text = pre_snapshot[Path("bundle/log.md")].decode("utf-8")
    assert log_text != pre_log_text
    assert "**Unmerge**" in log_text


def test_unmerge_non_tail_absorbed_id_refuses_clean_error_no_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After `merge(S,B)` then `merge(S,C)`, `unmerge S B` targets the
    NON-tail entry (the LIFO tail is C) and MUST refuse with a clean error
    and no write at all (spec scenario: Absorbed-id is not the LIFO tail)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path, "concepts/survivor", title="Survivor")
    _write_concept(tmp_path, "concepts/b", title="B")
    _write_concept(tmp_path, "concepts/c", title="C")

    merge_b = runner.invoke(app, ["merge", "concepts/survivor", "concepts/b", "--auto"])
    assert merge_b.exit_code == 0, merge_b.stderr
    merge_c = runner.invoke(app, ["merge", "concepts/survivor", "concepts/c", "--auto"])
    assert merge_c.exit_code == 0, merge_c.stderr

    before = _snapshot(tmp_path)

    result = runner.invoke(
        app, ["unmerge", "concepts/survivor", "concepts/b", "--auto"]
    )

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "Traceback" not in result.stderr
    assert _snapshot(tmp_path) == before


def test_merge_then_unmerge_rematerializes_dropped_self_loop_and_deduped_collision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A survivor whose outbound `relations:` merge with the absorbed's
    produces BOTH a dropped self-loop (survivor -> absorbed retargets to a
    survivor -> survivor self-loop, dropped) and a deduped collision (both
    sides carry an identical `(target, type)` edge to a genuine third
    party) -- `unmerge` restores the survivor via its verbatim
    `survivor_before` snapshot, re-materializing both the dropped self-loop
    and the deduped collision exactly, with the whole bundle byte-identical
    to the pre-merge snapshot except `log.md` (spec: "Resulting self-loop is
    dropped, non-silently", "Duplicate edge is deduped, non-silently";
    Unmerge Achieves Round-Trip Parity)."""
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

    pre_snapshot = _bundle_bytes_snapshot(tmp_path)

    merge_result = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )
    assert merge_result.exit_code == 0, merge_result.stderr

    unmerge_result = runner.invoke(
        app, ["unmerge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )
    assert unmerge_result.exit_code == 0, unmerge_result.stderr

    _assert_byte_parity_except_log(tmp_path, pre_snapshot)


def test_overlapping_third_party_relation_lifo_restores_each_intermediate_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`merge(S, B)` then `merge(S, C)`, both retargeting the SAME
    third-party file's `relations:` (one entry targeting B, one targeting
    C) -- `unmerge(S, C)` then `unmerge(S, B)` restores that file to each
    exact INTERMEDIATE byte state in LIFO order (design D4's overlapping-
    LIFO proof, applied to whole-file relation snapshots rather than
    offset-based link rewrites): after unmerging C the file must match its
    state right after the FIRST merge (B already retargeted, C not yet);
    after also unmerging B it must match its ORIGINAL pre-any-merge
    state."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path, "concepts/survivor", title="Survivor")
    _write_concept(tmp_path, "concepts/b", title="B")
    _write_concept(tmp_path, "concepts/c", title="C")
    _write_concept_with_relations(
        tmp_path,
        "concepts/linker",
        title="Linker",
        relations=[
            {"target": "concepts/b", "type": "references"},
            {"target": "concepts/c", "type": "depends_on"},
        ],
    )
    linker_path = tmp_path / "bundle" / "concepts" / "linker.md"

    f0 = linker_path.read_bytes()

    merge_b = runner.invoke(app, ["merge", "concepts/survivor", "concepts/b", "--auto"])
    assert merge_b.exit_code == 0, merge_b.stderr

    f1 = linker_path.read_bytes()
    assert f1 != f0  # B's relation must have been retargeted to the survivor

    merge_c = runner.invoke(app, ["merge", "concepts/survivor", "concepts/c", "--auto"])
    assert merge_c.exit_code == 0, merge_c.stderr

    unmerge_c = runner.invoke(
        app, ["unmerge", "concepts/survivor", "concepts/c", "--auto"]
    )
    assert unmerge_c.exit_code == 0, unmerge_c.stderr
    assert linker_path.read_bytes() == f1  # exact intermediate state restored

    unmerge_b = runner.invoke(
        app, ["unmerge", "concepts/survivor", "concepts/b", "--auto"]
    )
    assert unmerge_b.exit_code == 0, unmerge_b.stderr
    assert linker_path.read_bytes() == f0  # exact original state restored


def test_unmerge_decline_at_prompt_writes_nothing_bytes_and_mtimes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Declining the confirm prompt after a real merge leaves EVERY bundle
    file byte- and mtime-identical -- nothing written (spec: Confirm-Gated
    Two-Phase Execution, applied to `unmerge`)."""
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
