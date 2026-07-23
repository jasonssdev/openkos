"""Unit tests for the `reconcile` CLI command: records a human's resolution
of an S3 contradiction between two concepts as additive typed edges + body
notes -- the first WRITE verb of the freshness-lint-v1 arc (spec: Reconcile
Command Specification). Mirrors `relate`'s Phase A path-safety/existence
gates and the `ingest`/`forget`/`relate`/`merge` review-gated write scaffold
verbatim -- only the write target (TWO existing concepts, a symmetric or
`--winner`-directional edge, plus a `## Reconciliation` body note) differs.
"""

from pathlib import Path

import pytest
from typer.testing import CliRunner, _NamedTextIOWrapper

from openkos.cli.main import app
from openkos.model import okf

runner = CliRunner()


# -- 1.1: shared fixtures, mirroring test_relate.py -------------------------


def _simulate_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make `sys.stdin.isatty()` report `True` inside a `CliRunner.invoke` call."""
    monkeypatch.setattr(_NamedTextIOWrapper, "isatty", lambda self: True)


def _snapshot_entry(path: Path) -> bytes | None:
    if path.is_dir():
        return None
    return path.read_bytes()


def _snapshot(root: Path) -> dict[Path, bytes | None]:
    """Capture every entry under `root`, keyed by relative path."""
    return {path.relative_to(root): _snapshot_entry(path) for path in root.rglob("*")}


def _init_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0


def _ingest_source(tmp_path: Path, name: str) -> str:
    """Ingest one Source concept via `ingest --auto`, returning its concept-id."""
    source = tmp_path / name
    source.write_text("content", encoding="utf-8")
    result = runner.invoke(app, ["ingest", name, "--auto"])
    assert result.exit_code == 0
    slug = Path(name).stem
    return f"sources/{slug}"


def _relations_of(tmp_path: Path, concept_id: str) -> list[okf.Relation]:
    text = (tmp_path / "bundle" / f"{concept_id}.md").read_text(encoding="utf-8")
    metadata, _ = okf.load_frontmatter(text)
    return okf.decode_relations(metadata)


def _body_of(tmp_path: Path, concept_id: str) -> str:
    text = (tmp_path / "bundle" / f"{concept_id}.md").read_text(encoding="utf-8")
    _, body = okf.load_frontmatter(text)
    return body


def _log_text(tmp_path: Path) -> str:
    return (tmp_path / "bundle" / "log.md").read_text(encoding="utf-8")


# -- 1.2: error-before-write (unknown id, self-pair, --winner not in pair) --


def test_unknown_id_a_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A nonexistent `id_a` refuses (exit 1) and writes nothing."""
    _init_workspace(tmp_path, monkeypatch)
    b_id = _ingest_source(tmp_path, "b.txt")
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["reconcile", "sources/nonexistent", b_id, "--auto"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert _snapshot(tmp_path) == before


def test_unknown_id_b_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A nonexistent `id_b` refuses (exit 1) and writes nothing."""
    _init_workspace(tmp_path, monkeypatch)
    a_id = _ingest_source(tmp_path, "a.txt")
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["reconcile", a_id, "sources/nonexistent", "--auto"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert _snapshot(tmp_path) == before


def test_self_pair_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`id_a == id_b` is rejected before any write (spec: self-pair
    rejected)."""
    _init_workspace(tmp_path, monkeypatch)
    a_id = _ingest_source(tmp_path, "a.txt")
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["reconcile", a_id, a_id, "--auto"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert _snapshot(tmp_path) == before


def test_winner_not_in_pair_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--winner` resolving to neither pair member refuses (exit 1), no
    write (spec: "--winner gamma (not in pair {alpha,beta})")."""
    _init_workspace(tmp_path, monkeypatch)
    a_id = _ingest_source(tmp_path, "a.txt")
    b_id = _ingest_source(tmp_path, "b.txt")
    c_id = _ingest_source(tmp_path, "c.txt")
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["reconcile", a_id, b_id, "--winner", c_id, "--auto"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert _snapshot(tmp_path) == before


def test_winner_unknown_id_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--winner` pointing at a nonexistent concept refuses (exit 1), no
    write."""
    _init_workspace(tmp_path, monkeypatch)
    a_id = _ingest_source(tmp_path, "a.txt")
    b_id = _ingest_source(tmp_path, "b.txt")
    before = _snapshot(tmp_path)

    result = runner.invoke(
        app,
        ["reconcile", a_id, b_id, "--winner", "sources/nonexistent", "--auto"],
    )

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert _snapshot(tmp_path) == before


def test_traversal_id_a_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A traversal-shaped `id_a` is refused (exit 1), no write (threat
    matrix: path traversal)."""
    _init_workspace(tmp_path, monkeypatch)
    b_id = _ingest_source(tmp_path, "b.txt")
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["reconcile", "../../evil", b_id, "--auto"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert _snapshot(tmp_path) == before


def test_traversal_id_b_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A traversal-shaped `id_b` is refused (exit 1), no write (threat
    matrix: path traversal)."""
    _init_workspace(tmp_path, monkeypatch)
    a_id = _ingest_source(tmp_path, "a.txt")
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["reconcile", a_id, "../../evil", "--auto"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert _snapshot(tmp_path) == before


def test_traversal_winner_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A traversal-shaped `--winner` is refused (exit 1), no write (threat
    matrix: path traversal)."""
    _init_workspace(tmp_path, monkeypatch)
    a_id = _ingest_source(tmp_path, "a.txt")
    b_id = _ingest_source(tmp_path, "b.txt")
    before = _snapshot(tmp_path)

    result = runner.invoke(
        app, ["reconcile", a_id, b_id, "--winner", "../../evil", "--auto"]
    )

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert _snapshot(tmp_path) == before


def test_reserved_basename_id_a_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A concept-id resolving to the reserved `index`/`log` basename refuses
    (exit 1) and writes nothing (mirrors `forget`'s reserved-basename
    gate)."""
    _init_workspace(tmp_path, monkeypatch)
    b_id = _ingest_source(tmp_path, "b.txt")
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["reconcile", "index", b_id, "--auto"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "reserved" in result.stderr
    assert _snapshot(tmp_path) == before


def test_reserved_basename_id_b_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reserved `log` basename as `id_b` refuses (exit 1) and writes
    nothing."""
    _init_workspace(tmp_path, monkeypatch)
    a_id = _ingest_source(tmp_path, "a.txt")
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["reconcile", a_id, "log", "--auto"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "reserved" in result.stderr
    assert _snapshot(tmp_path) == before


# -- 1.3: confirm-gate -------------------------------------------------------


def test_interactive_decline_aborts_no_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An interactive TTY decline aborts (exit 1) and writes nothing."""
    _init_workspace(tmp_path, monkeypatch)
    a_id = _ingest_source(tmp_path, "a.txt")
    b_id = _ingest_source(tmp_path, "b.txt")
    _simulate_tty(monkeypatch)
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["reconcile", a_id, b_id], input="n\n")

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert _snapshot(tmp_path) == before


def test_auto_bypasses_confirm_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--auto` skips the confirmation prompt and Phase B proceeds directly."""
    _init_workspace(tmp_path, monkeypatch)
    a_id = _ingest_source(tmp_path, "a.txt")
    b_id = _ingest_source(tmp_path, "b.txt")
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["reconcile", a_id, b_id, "--auto"])

    assert result.exit_code == 0
    assert "Proceed" not in result.output
    assert _relations_of(tmp_path, a_id) == [
        okf.Relation(target=b_id, type="reconciled_with")
    ]


def test_review_false_bypasses_confirm_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Config `review: false` skips the confirmation prompt the same as
    `--auto`."""
    _init_workspace(tmp_path, monkeypatch)
    config_path = tmp_path / "openkos.yaml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "review: true", "review: false"
        ),
        encoding="utf-8",
    )
    a_id = _ingest_source(tmp_path, "a.txt")
    b_id = _ingest_source(tmp_path, "b.txt")
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["reconcile", a_id, b_id])

    assert result.exit_code == 0
    assert "Proceed" not in result.output
    assert _relations_of(tmp_path, a_id) == [
        okf.Relation(target=b_id, type="reconciled_with")
    ]


def test_non_tty_without_auto_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`review: true`, non-TTY stdin, no `--auto` refuses (exit 1) and
    writes nothing."""
    _init_workspace(tmp_path, monkeypatch)
    a_id = _ingest_source(tmp_path, "a.txt")
    b_id = _ingest_source(tmp_path, "b.txt")
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["reconcile", a_id, b_id])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "--auto" in result.stderr
    assert _snapshot(tmp_path) == before


# -- 1.4: symmetric success ---------------------------------------------------


def test_symmetric_reconcile_writes_edges_and_notes_on_both(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No `--winner` writes a SYMMETRIC `reconciled_with` edge on BOTH
    concepts (each targeting the other), a `## Reconciliation` note + anchor
    on both bodies, and a `**Reconcile**` log line (spec: Default Symmetric
    Reconciliation)."""
    _init_workspace(tmp_path, monkeypatch)
    a_id = _ingest_source(tmp_path, "a.txt")
    b_id = _ingest_source(tmp_path, "b.txt")

    result = runner.invoke(app, ["reconcile", a_id, b_id, "--auto"])

    assert result.exit_code == 0
    assert _relations_of(tmp_path, a_id) == [
        okf.Relation(target=b_id, type="reconciled_with")
    ]
    assert _relations_of(tmp_path, b_id) == [
        okf.Relation(target=a_id, type="reconciled_with")
    ]

    body_a = _body_of(tmp_path, a_id)
    body_b = _body_of(tmp_path, b_id)
    assert "## Reconciliation" in body_a
    assert f"<!-- okos:reconcile target={b_id} role=reconciled -->" in body_a
    assert f"[{b_id}](/{b_id}.md)" in body_a
    assert "## Reconciliation" in body_b
    assert f"<!-- okos:reconcile target={a_id} role=reconciled -->" in body_b
    assert f"[{a_id}](/{a_id}.md)" in body_b

    log_text = _log_text(tmp_path)
    assert "**Reconcile**" in log_text
    assert "symmetric" in log_text.lower()


# -- 1.5: --winner success -----------------------------------------------------


def test_winner_reconcile_writes_directional_edge_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--winner <id>` writes a single DIRECTIONAL `supersedes` edge
    winner->loser (no back-edge), with a note on BOTH sides (spec:
    Directional Reconciliation via --winner)."""
    _init_workspace(tmp_path, monkeypatch)
    a_id = _ingest_source(tmp_path, "a.txt")
    b_id = _ingest_source(tmp_path, "b.txt")

    result = runner.invoke(app, ["reconcile", a_id, b_id, "--winner", a_id, "--auto"])

    assert result.exit_code == 0
    assert _relations_of(tmp_path, a_id) == [
        okf.Relation(target=b_id, type="supersedes")
    ]
    assert _relations_of(tmp_path, b_id) == []

    body_a = _body_of(tmp_path, a_id)
    body_b = _body_of(tmp_path, b_id)
    assert f"<!-- okos:reconcile target={b_id} role=supersedes -->" in body_a
    assert f"<!-- okos:reconcile target={a_id} role=superseded -->" in body_b

    log_text = _log_text(tmp_path)
    assert "**Reconcile**" in log_text
    assert "supersedes" in log_text.lower()


# -- 1.6: idempotent re-run ---------------------------------------------------


def test_symmetric_reconcile_idempotent_rerun(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Re-running a symmetric reconcile does not duplicate the edge or the
    note (anchor-suppressed) and logs a "no change" variant (spec:
    Idempotent Re-run)."""
    _init_workspace(tmp_path, monkeypatch)
    a_id = _ingest_source(tmp_path, "a.txt")
    b_id = _ingest_source(tmp_path, "b.txt")

    first = runner.invoke(app, ["reconcile", a_id, b_id, "--auto"])
    assert first.exit_code == 0

    second = runner.invoke(app, ["reconcile", a_id, b_id, "--auto"])
    assert second.exit_code == 0

    assert _relations_of(tmp_path, a_id) == [
        okf.Relation(target=b_id, type="reconciled_with")
    ]
    assert _relations_of(tmp_path, b_id) == [
        okf.Relation(target=a_id, type="reconciled_with")
    ]
    assert _body_of(tmp_path, a_id).count("## Reconciliation") == 1
    assert _body_of(tmp_path, b_id).count("## Reconciliation") == 1

    log_text = _log_text(tmp_path)
    assert "already reconciled; no change." in log_text


def test_winner_reconcile_idempotent_rerun(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Re-running a `--winner` reconcile does not duplicate the edge or the
    note and logs a "no change" variant."""
    _init_workspace(tmp_path, monkeypatch)
    a_id = _ingest_source(tmp_path, "a.txt")
    b_id = _ingest_source(tmp_path, "b.txt")

    first = runner.invoke(app, ["reconcile", a_id, b_id, "--winner", a_id, "--auto"])
    assert first.exit_code == 0

    second = runner.invoke(app, ["reconcile", a_id, b_id, "--winner", a_id, "--auto"])
    assert second.exit_code == 0

    assert _relations_of(tmp_path, a_id) == [
        okf.Relation(target=b_id, type="supersedes")
    ]
    assert _relations_of(tmp_path, b_id) == []
    assert _body_of(tmp_path, a_id).count("## Reconciliation") == 1
    assert _body_of(tmp_path, b_id).count("## Reconciliation") == 1

    log_text = _log_text(tmp_path)
    assert "already reconciled; no change." in log_text


# -- 1.6b: mode-switch refuses (CRITICAL fix -- no contradictory state) ------


def test_symmetric_then_winner_mode_switch_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A symmetric reconcile followed by a `--winner` re-run on the SAME
    pair REFUSES (exit 1) instead of adding a contradictory `supersedes`
    edge alongside the stale `reconciled_with` edge -- the workspace is
    byte-unchanged after the refused 2nd call."""
    _init_workspace(tmp_path, monkeypatch)
    a_id = _ingest_source(tmp_path, "a.txt")
    b_id = _ingest_source(tmp_path, "b.txt")

    first = runner.invoke(app, ["reconcile", a_id, b_id, "--auto"])
    assert first.exit_code == 0

    before = _snapshot(tmp_path)

    second = runner.invoke(app, ["reconcile", a_id, b_id, "--winner", a_id, "--auto"])

    assert second.exit_code == 1
    assert isinstance(second.exception, SystemExit)
    assert "already reconciled" in second.stderr
    assert _snapshot(tmp_path) == before

    # frontmatter must NOT carry both edge types -- only the original
    # symmetric edge survives
    assert _relations_of(tmp_path, a_id) == [
        okf.Relation(target=b_id, type="reconciled_with")
    ]
    assert _relations_of(tmp_path, b_id) == [
        okf.Relation(target=a_id, type="reconciled_with")
    ]


def test_winner_a_then_winner_b_mode_switch_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--winner a` followed by `--winner b` (opposite winner) on the SAME
    pair REFUSES (exit 1) instead of adding a 2nd `supersedes` edge
    alongside the first, workspace byte-unchanged."""
    _init_workspace(tmp_path, monkeypatch)
    a_id = _ingest_source(tmp_path, "a.txt")
    b_id = _ingest_source(tmp_path, "b.txt")

    first = runner.invoke(app, ["reconcile", a_id, b_id, "--winner", a_id, "--auto"])
    assert first.exit_code == 0

    before = _snapshot(tmp_path)

    second = runner.invoke(app, ["reconcile", a_id, b_id, "--winner", b_id, "--auto"])

    assert second.exit_code == 1
    assert isinstance(second.exception, SystemExit)
    assert "already reconciled" in second.stderr
    assert _snapshot(tmp_path) == before

    assert _relations_of(tmp_path, a_id) == [
        okf.Relation(target=b_id, type="supersedes")
    ]
    assert _relations_of(tmp_path, b_id) == []


def test_winner_then_symmetric_mode_switch_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--winner a` followed by a symmetric re-run on the SAME pair REFUSES
    (exit 1), workspace byte-unchanged, note not silently mislabeled."""
    _init_workspace(tmp_path, monkeypatch)
    a_id = _ingest_source(tmp_path, "a.txt")
    b_id = _ingest_source(tmp_path, "b.txt")

    first = runner.invoke(app, ["reconcile", a_id, b_id, "--winner", a_id, "--auto"])
    assert first.exit_code == 0

    before = _snapshot(tmp_path)

    second = runner.invoke(app, ["reconcile", a_id, b_id, "--auto"])

    assert second.exit_code == 1
    assert isinstance(second.exception, SystemExit)
    assert "already reconciled" in second.stderr
    assert _snapshot(tmp_path) == before

    assert _relations_of(tmp_path, a_id) == [
        okf.Relation(target=b_id, type="supersedes")
    ]
    assert _relations_of(tmp_path, b_id) == []


# -- 1.7: additive-only -------------------------------------------------------


def test_additive_only_preserves_existing_body_and_relations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pre-existing unrelated body content and relations on BOTH concepts
    are preserved verbatim; only the new edge + note are appended (spec:
    Additive-Only, No Status/Lifecycle Write)."""
    _init_workspace(tmp_path, monkeypatch)
    a_id = _ingest_source(tmp_path, "a.txt")
    b_id = _ingest_source(tmp_path, "b.txt")
    c_id = _ingest_source(tmp_path, "c.txt")

    for concept_id, other_target in ((a_id, c_id), (b_id, c_id)):
        path = tmp_path / "bundle" / f"{concept_id}.md"
        text = path.read_text(encoding="utf-8")
        metadata, body = okf.load_frontmatter(text)
        metadata[okf.RELATIONS_KEY] = [{"target": other_target, "type": "references"}]
        body = body + "\n\n## Pre-existing section\n\nUnrelated hand-authored text.\n"
        path.write_text(okf.dump_frontmatter(metadata, body), encoding="utf-8")

    result = runner.invoke(app, ["reconcile", a_id, b_id, "--auto"])

    assert result.exit_code == 0

    relations_a = _relations_of(tmp_path, a_id)
    relations_b = _relations_of(tmp_path, b_id)
    assert okf.Relation(target=c_id, type="references") in relations_a
    assert okf.Relation(target=b_id, type="reconciled_with") in relations_a
    assert okf.Relation(target=c_id, type="references") in relations_b
    assert okf.Relation(target=a_id, type="reconciled_with") in relations_b

    body_a = _body_of(tmp_path, a_id)
    body_b = _body_of(tmp_path, b_id)
    assert "Unrelated hand-authored text." in body_a
    assert "## Pre-existing section" in body_a
    assert "Unrelated hand-authored text." in body_b
    assert "## Pre-existing section" in body_b
    assert "## Reconciliation" in body_a
    assert "## Reconciliation" in body_b

    # status must never be touched by reconcile (label-only supersedes,
    # additive-only, no lifecycle write)
    metadata_a, _ = okf.load_frontmatter(
        (tmp_path / "bundle" / f"{a_id}.md").read_text(encoding="utf-8")
    )
    assert metadata_a.get("status") == "active"
