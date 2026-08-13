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

from openkos.cli import main
from openkos.cli.main import app
from openkos.model import okf
from tests.unit.cli.conftest import (
    changed_paths,
    confirm_after,
    echo_after,
    snapshot_with_mtime,
)
from tests.unit.cli.conftest import snapshot_bytes as _snapshot

runner = CliRunner()


# -- 1.1: shared fixtures, mirroring test_relate.py -------------------------


def _simulate_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make `sys.stdin.isatty()` report `True` inside a `CliRunner.invoke` call."""
    monkeypatch.setattr(_NamedTextIOWrapper, "isatty", lambda self: True)


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


# -- #313: re-validate every write target after the confirm gate ------------


def _pair_on_a_tty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[str, str]:
    """A workspace with the reconcilable pair ingested, on a TTY -- the
    minimum that reaches `reconcile`'s confirm gate with all THREE of its
    write targets present."""
    _init_workspace(tmp_path, monkeypatch)
    id_a = _ingest_source(tmp_path, "a.txt")
    id_b = _ingest_source(tmp_path, "b.txt")
    _simulate_tty(monkeypatch)
    return id_a, id_b


@pytest.mark.parametrize(
    "target", ["bundle/sources/a.md", "bundle/sources/b.md", "bundle/log.md"]
)
def test_a_write_target_edited_during_the_prompt_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    """#313: `reconcile` renders BOTH concept documents and the new `log.md`
    from a pre-prompt read, then writes those exact bytes. An edit landing
    while the operator reads the preview was overwritten in full, silently,
    and auto-committed. Every target must be re-read after the gate.

    Parametrized over all three because the guard is whole-run: `reconcile`
    writes a symmetric pair, so honouring one side while clobbering the
    other would leave the two concepts disagreeing about their own
    resolution -- the one state the refuse-on-conflict gate exists to
    prevent.
    """
    id_a, id_b = _pair_on_a_tty(tmp_path, monkeypatch)
    target_path = tmp_path / target
    concurrent = "hand-edited while the prompt waited\n"
    before = snapshot_with_mtime(tmp_path)
    confirm_after(
        monkeypatch, lambda: target_path.write_text(concurrent, encoding="utf-8")
    )

    result = runner.invoke(app, ["reconcile", id_a, id_b], input="y\n")

    assert result.exit_code == 3
    assert isinstance(result.exception, SystemExit)
    assert "refusing to write --" in result.stderr
    assert target in result.stderr
    assert target_path.read_text(encoding="utf-8") == concurrent
    after = snapshot_with_mtime(tmp_path)
    changed = changed_paths(before, after)
    assert changed == {Path(target)}


def test_a_write_target_deleted_during_the_prompt_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A target that has since been DELETED is drift too: re-creating it
    from a snapshot the operator can no longer see is the same silent
    revert as overwriting it."""
    id_a, id_b = _pair_on_a_tty(tmp_path, monkeypatch)
    deleted_path = tmp_path / "bundle" / "sources" / "b.md"
    before = snapshot_with_mtime(tmp_path)
    confirm_after(monkeypatch, deleted_path.unlink)

    result = runner.invoke(app, ["reconcile", id_a, id_b], input="y\n")

    assert result.exit_code == 3
    assert isinstance(result.exception, SystemExit)
    assert "bundle/sources/b.md" in result.stderr
    assert not deleted_path.exists()
    after = snapshot_with_mtime(tmp_path)
    changed = changed_paths(before, after)
    assert changed == {Path("bundle/sources/b.md")}


@pytest.mark.parametrize(
    "target", ["bundle/sources/a.md", "bundle/sources/b.md", "bundle/log.md"]
)
def test_a_crlf_rewrite_during_the_prompt_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    """#306's constraint, re-pinned for `reconcile`: line-ending-only drift
    is still drift. `read_text` applies universal-newline translation, so a
    CRLF rewrite compares EQUAL to its own LF snapshot and the guard would
    wave it through -- then `fsio.write_atomic` (which opens with
    `newline=""`) puts the LF plan back over the operator's CRLF file.

    Parametrized over all THREE targets (#313 review, R3): `reconcile`
    carries three independent snapshots, and covering only `log.md` left
    both concept snapshots free to be paired with `read_text` -- every
    other assertion in this block is reader-independent and stayed green.
    """
    id_a, id_b = _pair_on_a_tty(tmp_path, monkeypatch)
    target_path = tmp_path / target
    concurrent = target_path.read_bytes().replace(b"\n", b"\r\n")
    assert concurrent != target_path.read_bytes()
    before = snapshot_with_mtime(tmp_path)
    confirm_after(monkeypatch, lambda: target_path.write_bytes(concurrent))

    result = runner.invoke(app, ["reconcile", id_a, id_b], input="y\n")

    assert result.exit_code == 3
    assert "refusing to write --" in result.stderr
    assert target in result.stderr
    assert target_path.read_bytes() == concurrent
    after = snapshot_with_mtime(tmp_path)
    changed = changed_paths(before, after)
    assert changed == {Path(target)}


def test_targets_that_were_already_crlf_are_not_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other direction: a write target that was ALREADY CRLF at rest,
    untouched by anyone, must not be reported as drift -- otherwise the
    verb refuses forever, naming a cause that never happened and a re-run
    that cannot clear it.

    All three targets are CRLF here (#313 review, R3): this is the only
    case that can catch a concept snapshot paired with `read_text`, and
    `reconcile` has two of them.
    """
    id_a, id_b = _pair_on_a_tty(tmp_path, monkeypatch)
    for rel in ("bundle/sources/a.md", "bundle/sources/b.md", "bundle/log.md"):
        path = tmp_path / rel
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))

    result = runner.invoke(app, ["reconcile", id_a, id_b, "--auto"])

    assert result.exit_code == 0
    assert "refusing to write" not in result.stderr
    assert _relations_of(tmp_path, id_a) == [
        okf.Relation(target=id_b, type="reconciled_with")
    ]


@pytest.mark.parametrize(
    "target", ["bundle/sources/a.md", "bundle/sources/b.md", "bundle/log.md"]
)
def test_drift_on_the_unprompted_path_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    """#313 review, R3: the guard must run on `--auto` too.

    Every other drift test here reaches the gate through `typer.confirm`,
    so indenting the `_reject_drifted_targets` call into the
    `if not auto and cfg.review:` block would disable it for unattended
    runs and leave all of them green. `--auto` is the path most likely to
    race a second writer, precisely because nothing pauses for a human.
    """
    _init_workspace(tmp_path, monkeypatch)
    id_a = _ingest_source(tmp_path, "a.txt")
    id_b = _ingest_source(tmp_path, "b.txt")
    target_path = tmp_path / target
    concurrent = "hand-edited while the preview printed\n"
    before = snapshot_with_mtime(tmp_path)
    hook = echo_after(
        monkeypatch,
        lambda: target_path.write_text(concurrent, encoding="utf-8"),
        trigger="(new dated entry)",
    )

    result = runner.invoke(app, ["reconcile", id_a, id_b, "--auto"])

    assert hook.fired, "echo_after trigger never matched -- stale preview wording?"
    assert result.exit_code == 3
    assert "refusing to write --" in result.stderr
    assert target in result.stderr
    assert target_path.read_text(encoding="utf-8") == concurrent
    after = snapshot_with_mtime(tmp_path)
    changed = changed_paths(before, after)
    assert changed == {Path(target)}


def test_an_edit_landing_after_the_snapshot_observation_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#318's race, pinned for `reconcile` (#327 follow-up; the pin existed
    only in `test_relate.py`): the guard's baseline and the text both new
    documents are computed from must come from the ONE `_snapshot_read`
    observation -- under a two-read shape a writer landing between the two
    reads becomes the guard's own baseline, and Phase B writes the pair
    computed from the EARLIER text, silently reverting the edit on a verb
    whose whole contract is refuse-on-conflict.

    The edit lands immediately after side A's snapshot returns (the verb's
    FIRST snapshot -- side B and `log.md` are read after it), the earliest
    a concurrent writer can now land relative to the plan; the guard's
    later re-read must call it drift and refuse the whole run.
    """
    _init_workspace(tmp_path, monkeypatch)
    id_a = _ingest_source(tmp_path, "a.txt")
    id_b = _ingest_source(tmp_path, "b.txt")
    target_path = tmp_path / "bundle" / "sources" / "a.md"
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

    before = snapshot_with_mtime(tmp_path)
    monkeypatch.setattr(main, "_snapshot_read", racing_snapshot_read)

    result = runner.invoke(app, ["reconcile", id_a, id_b, "--auto"])

    assert fired, "the racing wrapper never saw side A's snapshot"
    assert result.exit_code == 3
    assert isinstance(result.exception, SystemExit)
    assert "refusing to write --" in result.stderr
    assert "bundle/sources/a.md" in result.stderr
    assert target_path.read_text(encoding="utf-8") == concurrent
    assert changed_paths(before, snapshot_with_mtime(tmp_path)) == {
        Path("bundle/sources/a.md")
    }


# -- #324: two ids aliasing ONE file must be refused ------------------------


def _symlinks_are_creatable(probe_dir: Path) -> bool:
    """Detect at runtime whether this process may create symlinks in
    `probe_dir` -- mirroring `_filesystem_is_case_insensitive` below: probe
    the exact filesystem and privilege the test runs under, never a
    platform check. On Windows, `symlink_to` raises `OSError` without the
    `SeCreateSymbolicLink` privilege (admin or Developer Mode), and a bare
    call would then ERROR the test rather than skip it (#327, wave-3 R3);
    some filesystems raise `NotImplementedError` instead."""
    probe_target = probe_dir / "okos-symlink-probe-target.tmp"
    probe_target.write_text("probe", encoding="utf-8")
    probe_link = probe_dir / "okos-symlink-probe-link.tmp"
    try:
        probe_link.symlink_to(probe_target.name)
    except (OSError, NotImplementedError):
        return False
    else:
        probe_link.unlink()
        return True
    finally:
        probe_target.unlink()


def test_symlinked_pair_resolving_to_one_file_refuses_no_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#324: the distinctness gate is a STRING comparison, so two ids whose
    paths alias the same file (case-insensitive filesystem, or a symlink)
    pass it -- the drift guard then holds two identical-byte keys (no
    drift), and Phase B's second `write_atomic` over the same inode
    silently discards the first document's edge and note.

    A symlink reproduces the same-inode condition on EVERY filesystem that
    lets this process create one, so this is the host-independent pin:
    `samefile` (device+inode) must refuse the pair in Phase A, before any
    write. Where symlink creation itself is unavailable (Windows without
    the privilege), the aliasing this test pins cannot be constructed, so
    it SKIPS rather than errors -- the case-insensitivity test below still
    covers the aliasing report on those hosts.
    """
    if not _symlinks_are_creatable(tmp_path):
        pytest.skip(
            "symlink creation unavailable here (e.g. Windows without the "
            "symlink privilege): the same-inode aliasing this test pins "
            "cannot be constructed"
        )
    _init_workspace(tmp_path, monkeypatch)
    a_id = _ingest_source(tmp_path, "a.txt")
    alias_path = tmp_path / "bundle" / "sources" / "alias.md"
    alias_path.symlink_to("a.md")
    before = snapshot_with_mtime(tmp_path)

    result = runner.invoke(app, ["reconcile", a_id, "sources/alias", "--auto"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "refusing to reconcile --" in result.stderr
    assert "resolve to the same file" in result.stderr
    # Both ids, quoted as the message reprs them. The quotes are what makes
    # the first assertion non-vacuous (#327, wave-3 R3): a bare
    # `"sources/a" in stderr` is a substring of "sources/alias" and would
    # pass with side A missing from the message entirely.
    assert "'sources/a'" in result.stderr
    assert "'sources/alias'" in result.stderr
    assert snapshot_with_mtime(tmp_path) == before


def _filesystem_is_case_insensitive(probe_dir: Path) -> bool:
    """Detect at runtime whether `probe_dir`'s filesystem folds case, by
    creating a lowercase file and probing for its uppercase spelling --
    cheap, and truthful for the exact filesystem the test runs on (a
    platform check would lie on a case-sensitive APFS volume)."""
    probe = probe_dir / "okos-case-probe.tmp"
    probe.write_text("probe", encoding="utf-8")
    try:
        return (probe_dir / "OKOS-CASE-PROBE.TMP").exists()
    finally:
        probe.unlink()


def test_case_differing_pair_refuses_on_a_case_insensitive_filesystem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#324's literal reported scenario, pinned directly where the host
    allows: `reconcile foo Foo` on a case-insensitive filesystem (macOS
    default) names ONE file twice. The symlink test above is the
    host-independent guarantee; this one runs on macOS dev machines and any
    case-insensitive CI runner as a bonus pin of the exact report."""
    if not _filesystem_is_case_insensitive(tmp_path):
        pytest.skip(
            "case-sensitive filesystem: 'foo'/'Foo' denote two distinct "
            "files here, so the aliasing this test pins cannot occur"
        )
    _init_workspace(tmp_path, monkeypatch)
    _ingest_source(tmp_path, "foo.txt")
    before = snapshot_with_mtime(tmp_path)

    result = runner.invoke(app, ["reconcile", "sources/foo", "sources/Foo", "--auto"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "refusing to reconcile --" in result.stderr
    assert "resolve to the same file" in result.stderr
    assert snapshot_with_mtime(tmp_path) == before


def test_reconcile_names_the_status_the_loser_will_show_as(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The success line names the lifecycle status the superseded concept
    will carry, not only the act of superseding (#389).

    `reconcile` reported "recorded as superseding" while `list` shows
    `deprecated` in its STATUS column, so the operator saw two different
    words for the action they just performed and its effect, with nothing
    connecting them."""
    id_a, id_b = _pair_on_a_tty(tmp_path, monkeypatch)

    result = runner.invoke(app, ["reconcile", id_a, id_b, "--winner", id_a, "--auto"])

    assert result.exit_code == 0
    assert "superseding" in result.stdout
    assert "deprecated" in result.stdout


# -- #567: `--from-findings` batch mode --------------------------------------


def _seed_finding(
    tmp_path: Path,
    pair: tuple[str, str],
    *,
    verdict: str = "contradicts",
    confidence: float = 0.9,
    merged_absorbed_id: str | None = None,
) -> None:
    """Persist one finding straight into `.openkos/findings.db` (the same
    store curate's Contradictions stage writes). Empty `input_digests`
    keeps it non-stale by construction."""
    from openkos import config as config_mod
    from openkos.state import derived, findings

    layout = config_mod.WorkspaceLayout(tmp_path)
    conn = derived.open_derived_connection(layout.findings_db_path)
    try:
        findings.record_findings(
            conn,
            [
                findings.Finding(
                    pair_ids=pair,
                    merged_absorbed_id=merged_absorbed_id,
                    verdict=verdict,
                    confidence=confidence,
                    rationale="they disagree",
                    input_digests=(),
                )
            ],
        )
    finally:
        conn.close()


def test_from_findings_walks_open_findings_with_per_item_consent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--from-findings` reads the persisted findings and walks them with a
    per-item [y/N] prompt: an accepted pair gets the SAME symmetric
    `reconciled_with` write the two-id form performs, a declined pair is
    left untouched and listed (#567: no more transcribing ids by hand)."""
    _init_workspace(tmp_path, monkeypatch)
    a = _ingest_source(tmp_path, "alpha.md")
    b = _ingest_source(tmp_path, "beta.md")
    c = _ingest_source(tmp_path, "gamma.md")
    d = _ingest_source(tmp_path, "delta.md")
    _seed_finding(tmp_path, (a, b))
    _seed_finding(tmp_path, (c, d))
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["reconcile", "--from-findings"], input="y\nn\n")

    assert result.exit_code == 0
    assert any(
        rel.target == b and rel.type == "reconciled_with"
        for rel in _relations_of(tmp_path, a)
    )
    assert any(
        rel.target == a and rel.type == "reconciled_with"
        for rel in _relations_of(tmp_path, b)
    )
    assert _relations_of(tmp_path, c) == []
    assert "applied 1, skipped 0, declined 1." in result.output
    assert f"  declined: {c} <-> {d}" in result.output


def test_from_findings_refuses_without_a_tty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No TTY means no per-item consent channel: the batch walk refuses
    outright rather than writing anything unattended (#567) -- there is
    deliberately no --auto bulk path for reconciliations."""
    _init_workspace(tmp_path, monkeypatch)
    a = _ingest_source(tmp_path, "alpha.md")
    b = _ingest_source(tmp_path, "beta.md")
    _seed_finding(tmp_path, (a, b))

    result = runner.invoke(app, ["reconcile", "--from-findings"])

    assert result.exit_code == 1
    assert "non-interactive write consent unavailable" in result.stderr
    assert _relations_of(tmp_path, a) == []


def test_from_findings_skips_non_actionable_findings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Consistent verdicts, low confidence, and merged-content findings are
    not reconcilable pairs -- the walk never offers them (#567)."""
    _init_workspace(tmp_path, monkeypatch)
    a = _ingest_source(tmp_path, "alpha.md")
    b = _ingest_source(tmp_path, "beta.md")
    _seed_finding(tmp_path, (a, b), verdict="consistent")
    _seed_finding(tmp_path, (a, b), confidence=0.2)
    _seed_finding(tmp_path, (a, b), merged_absorbed_id="concepts/gone")
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["reconcile", "--from-findings"])

    assert result.exit_code == 0
    assert "No open contradiction findings to reconcile." in result.output
    assert _relations_of(tmp_path, a) == []


def test_from_findings_rejects_explicit_ids_and_winner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--from-findings` is a whole mode: mixing it with explicit ids or
    `--winner` refuses -- a directional resolution still needs the two-id
    form, where the human names the winner (#567)."""
    _init_workspace(tmp_path, monkeypatch)
    a = _ingest_source(tmp_path, "alpha.md")
    b = _ingest_source(tmp_path, "beta.md")

    with_ids = runner.invoke(app, ["reconcile", a, b, "--from-findings"])
    with_winner = runner.invoke(app, ["reconcile", "--from-findings", "--winner", a])

    assert with_ids.exit_code == 1
    assert "--from-findings" in with_ids.stderr
    assert with_winner.exit_code == 1
    assert "--from-findings" in with_winner.stderr


def test_two_id_form_still_requires_both_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without `--from-findings`, omitting an id still refuses -- making the
    arguments optional for the batch mode must not weaken the two-id
    form (#567)."""
    _init_workspace(tmp_path, monkeypatch)
    a = _ingest_source(tmp_path, "alpha.md")

    result = runner.invoke(app, ["reconcile", a])

    assert result.exit_code == 1
    assert "two concept ids" in result.stderr
