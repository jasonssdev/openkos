"""Unit tests for the `forget` CLI command: mirror-image delete of `ingest`.

Phase A (validate + in-memory build) checks path safety, workspace
presence, and concept existence before any write; Phase B (after confirm)
updates `index.md`/`log.md` FIRST and deletes the concept file LAST, so the
catalog never references a missing file. Not transactional as a whole --
recovery is via git, mirroring `ingest`'s D5 retreat."""

from pathlib import Path

import pytest
from typer.testing import CliRunner, _NamedTextIOWrapper

from openkos import fsio
from openkos.cli.main import app

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


def _ingest_source(tmp_path: Path, name: str = "notes.txt") -> str:
    """Ingest one Source concept via `ingest --auto`, returning its concept-id."""
    source = tmp_path / name
    source.write_text("content", encoding="utf-8")
    result = runner.invoke(app, ["ingest", name, "--auto"])
    assert result.exit_code == 0
    slug = Path(name).stem
    return f"sources/{slug}"


def _write_hand_authored_concept(
    tmp_path: Path, section: str, concept_id: str, link_form: str
) -> None:
    """Write a concept file and hand-author a matching bullet into `index.md`
    under `# {section}`, using the given raw link form."""
    concept_path = tmp_path / "bundle" / f"{concept_id}.md"
    concept_path.parent.mkdir(parents=True, exist_ok=True)
    concept_path.write_text(
        "---\ntype: Concept\ntitle: Test\n---\n\n# Test\n\nBody.\n",
        encoding="utf-8",
    )
    index_path = tmp_path / "bundle" / "index.md"
    index_text = index_path.read_text(encoding="utf-8")
    bullet = f"* [Test]({link_form}) - A hand-authored entry.\n"
    index_path.write_text(index_text + f"\n# {section}\n\n{bullet}", encoding="utf-8")


def test_traversal_concept_id_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A concept-id containing a `..` segment refuses (exit 1) and writes
    nothing (spec: Traversal segment rejected)."""
    _init_workspace(tmp_path, monkeypatch)
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["forget", "../../evil", "--auto"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert _snapshot(tmp_path) == before


def test_absolute_concept_id_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An absolute concept-id refuses (exit 1) and writes nothing."""
    _init_workspace(tmp_path, monkeypatch)
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["forget", "/etc/passwd", "--auto"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert _snapshot(tmp_path) == before


def test_reserved_basename_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A concept-id resolving to the reserved `index` basename refuses
    (exit 1) and writes nothing (spec: Reserved filename rejected)."""
    _init_workspace(tmp_path, monkeypatch)
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["forget", "index", "--auto"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert _snapshot(tmp_path) == before


def test_nonexistent_concept_id_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A concept-id with no corresponding file refuses (exit 1) with a
    clear error and writes nothing (spec: Concept file missing)."""
    _init_workspace(tmp_path, monkeypatch)
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["forget", "sources/nonexistent", "--auto"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert _snapshot(tmp_path) == before


@pytest.mark.parametrize("reserved", ["INDEX", "Log", "index.md"])
def test_reserved_basename_case_insensitive_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reserved: str
) -> None:
    """A differently-cased or `.md`-suffixed reserved basename (`INDEX`,
    `Log`, `index.md`) is refused as reserved on every platform, so a
    case-insensitive filesystem cannot be tricked into deleting the real
    `index.md`/`log.md` catalog files (spec: Reserved filename rejected)."""
    _init_workspace(tmp_path, monkeypatch)
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["forget", reserved, "--auto"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "reserved" in result.stderr
    assert (tmp_path / "bundle" / "index.md").is_file()
    assert (tmp_path / "bundle" / "log.md").is_file()
    assert _snapshot(tmp_path) == before


def test_dot_segment_concept_id_removes_index_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A concept-id with a leading `./` is canonicalized before BOTH the file
    delete and the index match, so the catalog bullet is removed rather than
    left dangling (regression: the raw concept_id was used for index matching
    while the filesystem path was pathlib-normalized)."""
    _init_workspace(tmp_path, monkeypatch)
    concept_id = _ingest_source(tmp_path)

    result = runner.invoke(app, ["forget", f"./{concept_id}", "--auto"])

    assert result.exit_code == 0
    assert not (tmp_path / "bundle" / f"{concept_id}.md").exists()
    index_text = (tmp_path / "bundle" / "index.md").read_text(encoding="utf-8")
    assert f"{concept_id}.md" not in index_text


def test_missing_workspace_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A directory that is not an initialized workspace refuses (exit 1)
    with no raw traceback."""
    monkeypatch.chdir(tmp_path)
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["forget", "sources/notes", "--auto"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "Traceback" not in result.stderr
    assert _snapshot(tmp_path) == before


def test_successful_forget_of_sources_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Forgetting a Sources-section entry removes the index bullet, appends
    a `**Forget**` log line (no tombstone marker), and deletes the concept
    file."""
    _init_workspace(tmp_path, monkeypatch)
    concept_id = _ingest_source(tmp_path)

    result = runner.invoke(app, ["forget", concept_id, "--auto"])

    assert result.exit_code == 0
    concept_path = tmp_path / "bundle" / f"{concept_id}.md"
    assert not concept_path.exists()
    index_text = (tmp_path / "bundle" / "index.md").read_text(encoding="utf-8")
    assert f"{concept_id}.md" not in index_text
    log_text = (tmp_path / "bundle" / "log.md").read_text(encoding="utf-8")
    assert "**Forget**" in log_text
    assert "tombstone" not in log_text.lower()


@pytest.mark.parametrize(
    ("section", "link_form"),
    [
        ("Concepts", "concepts/stoicism"),
        ("Concepts", "/concepts/stoicism"),
        ("Concepts", "/concepts/stoicism.md"),
        ("Concepts", "concepts/stoicism.md"),
        ("People", "people/maria-salazar"),
    ],
)
def test_successful_forget_of_hand_authored_bullet_across_link_forms(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, section: str, link_form: str
) -> None:
    """A hand-authored Concepts/People bullet is removed regardless of which
    tolerated link form (relative, leading-slash, with/without `.md`) it
    uses (spec: Entry removed from any section)."""
    _init_workspace(tmp_path, monkeypatch)
    concept_id = link_form.lstrip("/").removesuffix(".md")
    _write_hand_authored_concept(tmp_path, section, concept_id, link_form)

    result = runner.invoke(app, ["forget", concept_id, "--auto"])

    assert result.exit_code == 0
    assert not (tmp_path / "bundle" / f"{concept_id}.md").exists()
    index_text = (tmp_path / "bundle" / "index.md").read_text(encoding="utf-8")
    assert "[Test]" not in index_text


def test_auto_skips_the_prompt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`--auto` skips the confirmation prompt and Phase B proceeds directly."""
    _init_workspace(tmp_path, monkeypatch)
    concept_id = _ingest_source(tmp_path)
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["forget", concept_id, "--auto"])

    assert result.exit_code == 0
    assert "Proceed" not in result.output
    assert not (tmp_path / "bundle" / f"{concept_id}.md").exists()


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
    concept_id = _ingest_source(tmp_path)
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["forget", concept_id])

    assert result.exit_code == 0
    assert "Proceed" not in result.output
    assert not (tmp_path / "bundle" / f"{concept_id}.md").exists()


def test_non_tty_without_auto_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`review: true`, non-TTY stdin, no `--auto` refuses (exit 1) and
    writes/deletes nothing."""
    _init_workspace(tmp_path, monkeypatch)
    concept_id = _ingest_source(tmp_path)
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["forget", concept_id])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "--auto" in result.stderr
    assert _snapshot(tmp_path) == before


def test_tty_confirm_prompts_then_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An interactive TTY prompts via `typer.confirm`; confirming proceeds
    with Phase B."""
    _init_workspace(tmp_path, monkeypatch)
    concept_id = _ingest_source(tmp_path)
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["forget", concept_id], input="y\n")

    assert result.exit_code == 0
    assert not (tmp_path / "bundle" / f"{concept_id}.md").exists()


def test_phase_b_ordering_catalog_before_file_delete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`index.md`/`log.md` are updated BEFORE the concept file is deleted --
    monkeypatching `fsio.remove_file` to raise proves the catalog already
    landed while the concept file still exists (spec: Catalog updated
    before file deletion)."""
    _init_workspace(tmp_path, monkeypatch)
    concept_id = _ingest_source(tmp_path)

    def raising_remove_file(path: Path) -> None:
        raise OSError("simulated delete failure")

    monkeypatch.setattr(fsio, "remove_file", raising_remove_file)

    result = runner.invoke(app, ["forget", concept_id, "--auto"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "Traceback" not in result.stderr
    concept_path = tmp_path / "bundle" / f"{concept_id}.md"
    assert concept_path.is_file()
    index_text = (tmp_path / "bundle" / "index.md").read_text(encoding="utf-8")
    assert f"{concept_id}.md" not in index_text
    log_text = (tmp_path / "bundle" / "log.md").read_text(encoding="utf-8")
    assert "**Forget**" in log_text


def test_malformed_index_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A malformed `index.md` (no parseable frontmatter block) refuses
    (exit 1) and writes/deletes nothing."""
    _init_workspace(tmp_path, monkeypatch)
    concept_id = _ingest_source(tmp_path)
    (tmp_path / "bundle" / "index.md").write_text(
        "not a frontmatter block at all", encoding="utf-8"
    )
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["forget", concept_id, "--auto"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "Traceback" not in result.stderr
    assert _snapshot(tmp_path) == before
