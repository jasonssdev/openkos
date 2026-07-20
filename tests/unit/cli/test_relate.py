"""Unit tests for the `relate` CLI command: writes one typed `relations:`
edge into the SOURCE concept's frontmatter (PR2 of typed-relationships,
slice 1). Mirrors `forget`'s Phase A path-safety/existence gates and the
`ingest`/`forget`/`merge` review-gated write scaffold verbatim -- only the
write target (an EXISTING concept's frontmatter, not a new/deleted file)
differs."""

from pathlib import Path

import pytest
from typer.testing import CliRunner, _NamedTextIOWrapper

from openkos.cli.main import app
from openkos.model import okf

runner = CliRunner()


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


# -- 2.1: successful relate writes into source frontmatter -----------------


def test_successful_relate_writes_into_source_frontmatter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A confirmed/`--auto` `relate a references b` appends
    `{target: b, type: references}` to `a`'s `relations:` and logs the
    change (spec: "Successful relate writes into source frontmatter")."""
    _init_workspace(tmp_path, monkeypatch)
    source_id = _ingest_source(tmp_path, "a.txt")
    target_id = _ingest_source(tmp_path, "b.txt")

    result = runner.invoke(
        app, ["relate", source_id, "references", target_id, "--auto"]
    )

    assert result.exit_code == 0
    relations = _relations_of(tmp_path, source_id)
    assert relations == [okf.Relation(target=target_id, type="references")]
    log_text = (tmp_path / "bundle" / "log.md").read_text(encoding="utf-8")
    assert "**Relate**" in log_text
    # target's own frontmatter is untouched (only source is written)
    assert _relations_of(tmp_path, target_id) == []


# -- 2.2: missing target fails closed ---------------------------------------


def test_missing_target_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A nonexistent target concept refuses (exit 1) and writes nothing."""
    _init_workspace(tmp_path, monkeypatch)
    source_id = _ingest_source(tmp_path, "a.txt")
    before = _snapshot(tmp_path)

    result = runner.invoke(
        app, ["relate", source_id, "references", "sources/nonexistent", "--auto"]
    )

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert _snapshot(tmp_path) == before


# -- 2.3: missing source fails closed ---------------------------------------


def test_missing_source_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A nonexistent source concept refuses (exit 1) and writes nothing."""
    _init_workspace(tmp_path, monkeypatch)
    target_id = _ingest_source(tmp_path, "b.txt")
    before = _snapshot(tmp_path)

    result = runner.invoke(
        app, ["relate", "sources/nonexistent", "references", target_id, "--auto"]
    )

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert _snapshot(tmp_path) == before


# -- 2.4: non-TTY without --auto refuses ------------------------------------


def test_non_tty_without_auto_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`review: true`, non-TTY stdin, no `--auto` refuses (exit 1) and
    writes nothing."""
    _init_workspace(tmp_path, monkeypatch)
    source_id = _ingest_source(tmp_path, "a.txt")
    target_id = _ingest_source(tmp_path, "b.txt")
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["relate", source_id, "references", target_id])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "--auto" in result.stderr
    assert _snapshot(tmp_path) == before


# -- 2.5: known/unknown/empty relation type ---------------------------------


def test_known_relation_type_accepted_silently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A seeded relation type (`references`) is accepted with no WARN on
    stderr."""
    _init_workspace(tmp_path, monkeypatch)
    source_id = _ingest_source(tmp_path, "a.txt")
    target_id = _ingest_source(tmp_path, "b.txt")

    result = runner.invoke(
        app, ["relate", source_id, "references", target_id, "--auto"]
    )

    assert result.exit_code == 0
    assert "note" not in result.stderr.lower()
    assert _relations_of(tmp_path, source_id) == [
        okf.Relation(target=target_id, type="references")
    ]


def test_unknown_relation_type_accepted_with_warn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unrecognized relation type is still WRITTEN, with an advisory note
    on stderr (spec: "Unknown type accepted with WARN to stderr")."""
    _init_workspace(tmp_path, monkeypatch)
    source_id = _ingest_source(tmp_path, "a.txt")
    target_id = _ingest_source(tmp_path, "b.txt")

    result = runner.invoke(
        app, ["relate", source_id, "custom_relation", target_id, "--auto"]
    )

    assert result.exit_code == 0
    assert "note" in result.stderr.lower()
    assert "custom_relation" in result.stderr
    assert _relations_of(tmp_path, source_id) == [
        okf.Relation(target=target_id, type="custom_relation")
    ]


@pytest.mark.parametrize("rel_type", ["", "   "])
def test_empty_or_whitespace_relation_type_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rel_type: str
) -> None:
    """An empty or whitespace-only relation type is rejected (exit 1),
    writing nothing (spec: "Empty/whitespace type rejected")."""
    _init_workspace(tmp_path, monkeypatch)
    source_id = _ingest_source(tmp_path, "a.txt")
    target_id = _ingest_source(tmp_path, "b.txt")
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["relate", source_id, rel_type, target_id, "--auto"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert _snapshot(tmp_path) == before


# -- 2.6: traversal-shaped id for source or target is refused --------------


def test_traversal_source_id_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A traversal-shaped `source` id is refused (exit 1), no write
    (threat matrix: path traversal)."""
    _init_workspace(tmp_path, monkeypatch)
    target_id = _ingest_source(tmp_path, "b.txt")
    before = _snapshot(tmp_path)

    result = runner.invoke(
        app, ["relate", "../../evil", "references", target_id, "--auto"]
    )

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert _snapshot(tmp_path) == before


def test_traversal_target_id_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A traversal-shaped `target` id is refused (exit 1), no write
    (threat matrix: path traversal)."""
    _init_workspace(tmp_path, monkeypatch)
    source_id = _ingest_source(tmp_path, "a.txt")
    before = _snapshot(tmp_path)

    result = runner.invoke(
        app, ["relate", source_id, "references", "../../evil", "--auto"]
    )

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert _snapshot(tmp_path) == before


# -- 2.7: source == target rejected -----------------------------------------


def test_source_equals_target_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`source == target` (after canonicalization) is rejected before any
    write."""
    _init_workspace(tmp_path, monkeypatch)
    source_id = _ingest_source(tmp_path, "a.txt")
    before = _snapshot(tmp_path)

    result = runner.invoke(
        app, ["relate", source_id, "references", source_id, "--auto"]
    )

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert _snapshot(tmp_path) == before


# -- 2.8: duplicate (target, rel) relate call is idempotent ----------------


def test_duplicate_relate_call_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Calling `relate` twice with the identical `(source, rel, target)`
    triple does not duplicate the `relations:` entry."""
    _init_workspace(tmp_path, monkeypatch)
    source_id = _ingest_source(tmp_path, "a.txt")
    target_id = _ingest_source(tmp_path, "b.txt")

    first = runner.invoke(app, ["relate", source_id, "references", target_id, "--auto"])
    assert first.exit_code == 0
    second = runner.invoke(
        app, ["relate", source_id, "references", target_id, "--auto"]
    )
    assert second.exit_code == 0

    assert _relations_of(tmp_path, source_id) == [
        okf.Relation(target=target_id, type="references")
    ]


# -- PR2 correction batch, finding 1 (CRITICAL): hand-edited .md-suffixed
# target must still be recognized by the idempotency dedup ----------------


def test_duplicate_relate_recognizes_hand_edited_md_suffixed_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A source whose `relations:` entry already stores a `.md`-suffixed
    target (e.g. hand-edited) is still recognized as already-present by
    `relate`'s idempotency dedup -- `encode_relation`/`decode_relation` must
    be symmetric so a stored `.md`-suffixed target decodes to its stripped
    form (CRITICAL regression: encode/decode `.md` asymmetry)."""
    _init_workspace(tmp_path, monkeypatch)
    source_id = _ingest_source(tmp_path, "a.txt")
    target_id = _ingest_source(tmp_path, "b.txt")

    source_path = tmp_path / "bundle" / f"{source_id}.md"
    text = source_path.read_text(encoding="utf-8")
    metadata, body = okf.load_frontmatter(text)
    metadata[okf.RELATIONS_KEY] = [{"target": f"{target_id}.md", "type": "references"}]
    source_path.write_text(okf.dump_frontmatter(metadata, body), encoding="utf-8")

    result = runner.invoke(
        app, ["relate", source_id, "references", target_id, "--auto"]
    )

    assert result.exit_code == 0
    relations = _relations_of(tmp_path, source_id)
    assert relations == [okf.Relation(target=target_id, type="references")]


# -- PR2 correction batch, finding 2 (WARNING): preview must not claim an
# addition on a no-op repeat -------------------------------------------------


def test_repeated_relate_preview_shows_no_change_not_addition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A repeated `relate` call (relation already present) previews as
    unchanged/no-op, NOT as a claimed `+` addition (WARNING: misleading
    preview on no-op)."""
    _init_workspace(tmp_path, monkeypatch)
    source_id = _ingest_source(tmp_path, "a.txt")
    target_id = _ingest_source(tmp_path, "b.txt")

    first = runner.invoke(app, ["relate", source_id, "references", target_id, "--auto"])
    assert first.exit_code == 0

    second = runner.invoke(
        app, ["relate", source_id, "references", target_id, "--auto"]
    )

    assert second.exit_code == 0
    assert "+{target:" not in second.output
    assert (
        "unchanged" in second.output.lower()
        or "already present" in second.output.lower()
    )
    assert "1 -> 1" in second.output


# -- PR2 correction batch, finding 3: `review: false` config skips the
# prompt like --auto (coverage gap flagged by sdd-verify) -------------------


def test_relate_review_false_skips_the_prompt_like_auto(
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
    source_id = _ingest_source(tmp_path, "a.txt")
    target_id = _ingest_source(tmp_path, "b.txt")
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["relate", source_id, "references", target_id])

    assert result.exit_code == 0
    assert "Proceed" not in result.output
    assert _relations_of(tmp_path, source_id) == [
        okf.Relation(target=target_id, type="references")
    ]


# -- Confirm gate reuse (design: "Reuses preview -> confirm/--auto/review:
# gate verbatim") -----------------------------------------------------------


def test_auto_skips_the_prompt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`--auto` skips the confirmation prompt and Phase B proceeds directly."""
    _init_workspace(tmp_path, monkeypatch)
    source_id = _ingest_source(tmp_path, "a.txt")
    target_id = _ingest_source(tmp_path, "b.txt")
    _simulate_tty(monkeypatch)

    result = runner.invoke(
        app, ["relate", source_id, "references", target_id, "--auto"]
    )

    assert result.exit_code == 0
    assert "Proceed" not in result.output
    assert _relations_of(tmp_path, source_id) == [
        okf.Relation(target=target_id, type="references")
    ]


def test_tty_confirm_prompts_then_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An interactive TTY prompts via `typer.confirm`; confirming proceeds
    with Phase B."""
    _init_workspace(tmp_path, monkeypatch)
    source_id = _ingest_source(tmp_path, "a.txt")
    target_id = _ingest_source(tmp_path, "b.txt")
    _simulate_tty(monkeypatch)

    result = runner.invoke(
        app, ["relate", source_id, "references", target_id], input="y\n"
    )

    assert result.exit_code == 0
    assert _relations_of(tmp_path, source_id) == [
        okf.Relation(target=target_id, type="references")
    ]
