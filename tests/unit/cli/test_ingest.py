"""Unit tests for the `ingest` CLI command: Phase A preview, confirm gate,
and Phase B create-only writes.

Phase A (D5 Phase A) is a pure read + in-memory build: every refusal
condition -- missing path, missing workspace, collision -- is checked
before any file is written, so a refusal leaves the workspace exactly as
it was found. Phase B writes create-only immutables (raw copy, concept)
first and the catalog (`index.md`, `log.md`) last, but is NOT
transactional -- there is no rollback across the sequence (D5 retreat);
recovery from a partial write is via git, not an in-process undo.
"""

import os
import stat
from datetime import datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner, _NamedTextIOWrapper

from openkos import fsio
from openkos.cli.main import app
from openkos.model import okf

runner = CliRunner()


def _simulate_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make `sys.stdin.isatty()` report `True` inside a `CliRunner.invoke` call.

    See `tests/unit/cli/test_init.py::_simulate_tty` for why the CLASS
    method must be patched rather than the current `sys.stdin` instance.
    """
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


def _set_config_field(tmp_path: Path, old: str, new: str) -> None:
    config_path = tmp_path / "openkos.yaml"
    content = config_path.read_text(encoding="utf-8")
    assert old in content
    config_path.write_text(content.replace(old, new), encoding="utf-8")


def test_successful_ingest_of_valid_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A valid `ingest --auto` copies the raw source, writes one conformant
    Source concept with provenance + `# Citations`, and updates
    `index.md`/`log.md` (scenario: successful ingest of a valid path)."""
    _init_workspace(tmp_path, monkeypatch)
    source = tmp_path / "notes.txt"
    source.write_text("Some raw notes.", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    raw_copy = tmp_path / "raw" / "notes.txt"
    assert raw_copy.read_text(encoding="utf-8") == "Some raw notes."
    concept_path = tmp_path / "bundle" / "sources" / "notes.md"
    assert concept_path.is_file()
    concept_text = concept_path.read_text(encoding="utf-8")
    metadata, body = okf.load_frontmatter(concept_text)
    assert metadata["type"] == "Source"
    assert metadata["provenance"] == ["raw/notes.txt"]
    assert "## Source content" in body
    assert "Some raw notes." in body
    assert body.index("## Source content") < body.index("# Citations")
    assert "# Citations" in body
    assert okf.check_conformance(tmp_path / "bundle") == []
    index_text = (tmp_path / "bundle" / "index.md").read_text(encoding="utf-8")
    assert "sources/notes.md" in index_text
    log_text = (tmp_path / "bundle" / "log.md").read_text(encoding="utf-8")
    today = datetime.now().astimezone().date()
    assert f"## {today.isoformat()}" in log_text
    assert "notes.md" in log_text


def test_description_is_honest_no_extraction_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The generated concept's `description` states the source's content was
    embedded verbatim -- it must not claim extraction/compilation occurred
    (D-honesty, null-compiler scope)."""
    _init_workspace(tmp_path, monkeypatch)
    source = tmp_path / "notes.txt"
    source.write_text("content", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    concept_text = (tmp_path / "bundle" / "sources" / "notes.md").read_text(
        encoding="utf-8"
    )
    metadata, _ = okf.load_frontmatter(concept_text)
    description = str(metadata["description"])
    assert "embedded" in description
    assert "not yet extracted" in description


def test_undecodable_source_degrades_without_crashing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A source that is not valid UTF-8 text does not crash `ingest`: the
    raw copy still lands byte-identical, and the concept body honestly
    states the content could not be embedded (D2, scenario: undecodable
    source falls back)."""
    _init_workspace(tmp_path, monkeypatch)
    source = tmp_path / "notes.bin"
    raw_bytes = b"\xff\xfe not valid utf-8 \x00\x01"
    source.write_bytes(raw_bytes)

    result = runner.invoke(app, ["ingest", "notes.bin", "--auto"])

    assert result.exit_code == 0
    raw_copy = tmp_path / "raw" / "notes.bin"
    assert raw_copy.read_bytes() == raw_bytes
    concept_text = (tmp_path / "bundle" / "sources" / "notes.md").read_text(
        encoding="utf-8"
    )
    metadata, body = okf.load_frontmatter(concept_text)
    assert "could not be embedded as text" in body
    assert "## Source content" not in body
    description = str(metadata["description"])
    assert "binary" in description or "non-text" in description
    assert "could not be embedded" in description
    assert "not yet extracted" in description


def test_empty_source_renders_distinct_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A zero-length source renders a distinct empty-body note -- neither
    the verbatim-embed nor the undecodable-fallback wording (scenario:
    empty source renders a distinct body)."""
    _init_workspace(tmp_path, monkeypatch)
    source = tmp_path / "empty.txt"
    source.write_text("", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "empty.txt", "--auto"])

    assert result.exit_code == 0
    concept_text = (tmp_path / "bundle" / "sources" / "empty.md").read_text(
        encoding="utf-8"
    )
    _, body = okf.load_frontmatter(concept_text)
    assert "file is empty" in body
    assert "## Source content" not in body
    assert "could not be embedded as text" not in body


def test_decode_guard_precedes_generic_value_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A plain `ValueError` (NOT `UnicodeDecodeError`) raised while reading
    the source text still fails `ingest` via the outer `except (OSError,
    ValueError)` handler -- proving the specific `UnicodeDecodeError` guard
    does not swallow an unrelated `ValueError` (D2 ordering)."""
    _init_workspace(tmp_path, monkeypatch)
    source = tmp_path / "notes.txt"
    source.write_text("content", encoding="utf-8")
    before = _snapshot(tmp_path)

    original_read_text = Path.read_text

    def failing_read_text(self: Path, *args: object, **kwargs: object) -> str:
        if self.name == "notes.txt":
            raise ValueError("simulated non-decode value error")
        return original_read_text(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "read_text", failing_read_text)

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "openkos ingest" in result.stderr
    assert "failed" in result.stderr
    assert _snapshot(tmp_path) == before


def test_path_does_not_exist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing `<path>` refuses with exit 1 and writes nothing (scenario:
    path does not exist)."""
    _init_workspace(tmp_path, monkeypatch)
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["ingest", "missing.txt", "--auto"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "missing.txt" in result.stderr
    assert _snapshot(tmp_path) == before


def test_refuses_when_not_a_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A directory with no `bundle/index.md`/`log.md` refuses (scenario:
    missing workspace)."""
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "notes.txt"
    source.write_text("content", encoding="utf-8")
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "workspace" in result.stderr
    assert _snapshot(tmp_path) == before


def test_refuses_when_not_a_workspace_byte_identical_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression (Phase 5.1): `ingest`'s missing-workspace refusal message
    stays BYTE-IDENTICAL after switching from its inline `index.md`/`log.md`
    check to the shared `config.require_workspace` (D1) -- this test MUST
    pass unmodified both before AND after that refactor."""
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "notes.txt"
    source.write_text("content", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 1
    assert result.stderr == (
        "openkos ingest: refusing to ingest -- no OpenKOS workspace found in "
        "this directory (run 'openkos init' first).\n"
    )


def test_differing_source_reingest_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An existing `raw/<name>` whose bytes DIFFER from the incoming source
    refuses in Phase A -- raw is not overwritten, and the message
    distinguishes "differs" from the byte-identical case (scenario:
    differing re-ingest still refused)."""
    _init_workspace(tmp_path, monkeypatch)
    (tmp_path / "raw" / "notes.txt").write_text("original", encoding="utf-8")
    source = tmp_path / "notes.txt"
    source.write_text("new content", encoding="utf-8")
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "differs" in result.stderr
    assert "immutable" in result.stderr
    assert "raw/notes.txt" in result.stderr
    assert _snapshot(tmp_path) == before


def test_raw_absent_concept_present_refuses_inconsistent_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`raw/<name>` absent but `bundle/sources/<slug>.md` present refuses as
    an inconsistent workspace (D5) -- nothing is written (scenario: raw
    absent but concept present)."""
    _init_workspace(tmp_path, monkeypatch)
    sources_dir = tmp_path / "bundle" / "sources"
    sources_dir.mkdir()
    (sources_dir / "notes.md").write_text("original concept", encoding="utf-8")
    source = tmp_path / "notes.txt"
    source.write_text("new content", encoding="utf-8")
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "inconsistent" in result.stderr
    assert "bundle/sources/notes.md" in result.stderr
    assert "raw/notes.txt" in result.stderr
    assert _snapshot(tmp_path) == before


def test_reingest_after_forget_regenerates_concept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`init` -> `ingest --auto` -> `forget --auto` -> `ingest --auto` (same
    file) regenerates the concept, exits 0, leaves `raw/<name>` bytes
    byte-identical to the pre-forget snapshot, produces exactly ONE
    `sources/<slug>.md` bullet in `index.md`, and a new `**Re-ingest**` log
    entry (scenario: byte-identical re-ingest, post-forget sub-case)."""
    _init_workspace(tmp_path, monkeypatch)
    source = tmp_path / "notes.txt"
    source.write_text("Some raw notes.", encoding="utf-8")

    first = runner.invoke(app, ["ingest", "notes.txt", "--auto"])
    assert first.exit_code == 0
    raw_snapshot = (tmp_path / "raw" / "notes.txt").read_bytes()

    forgotten = runner.invoke(app, ["forget", "sources/notes", "--auto"])
    assert forgotten.exit_code == 0
    assert not (tmp_path / "bundle" / "sources" / "notes.md").exists()

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    assert (tmp_path / "raw" / "notes.txt").read_bytes() == raw_snapshot
    concept_path = tmp_path / "bundle" / "sources" / "notes.md"
    assert concept_path.is_file()
    index_text = (tmp_path / "bundle" / "index.md").read_text(encoding="utf-8")
    assert index_text.count("sources/notes.md") == 1
    log_text = (tmp_path / "bundle" / "log.md").read_text(encoding="utf-8")
    assert "**Re-ingest**" in log_text


def test_reingest_without_forget_regenerates_without_duplicate_index_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`init` -> `ingest --auto` -> `ingest --auto` (same file, no forget)
    regenerates the concept, exits 0, and `index.md` contains exactly ONE
    occurrence of `sources/<slug>.md` -- proving D3 dedup -- with raw bytes
    unchanged (scenario: byte-identical re-ingest, no-forget sub-case)."""
    _init_workspace(tmp_path, monkeypatch)
    source = tmp_path / "notes.txt"
    source.write_text("Some raw notes.", encoding="utf-8")

    first = runner.invoke(app, ["ingest", "notes.txt", "--auto"])
    assert first.exit_code == 0
    raw_snapshot = (tmp_path / "raw" / "notes.txt").read_bytes()

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    assert (tmp_path / "raw" / "notes.txt").read_bytes() == raw_snapshot
    index_text = (tmp_path / "bundle" / "index.md").read_text(encoding="utf-8")
    assert index_text.count("sources/notes.md") == 1
    log_text = (tmp_path / "bundle" / "log.md").read_text(encoding="utf-8")
    assert "**Re-ingest**" in log_text


def test_reingest_preview_shows_regenerate_not_new_raw(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A TTY-confirm re-ingest of an identical source shows `~ raw/<name>`
    (existing copy reused) in the preview and NO `+ raw/<name>` line
    (scenario: byte-identical re-ingest, preview wording)."""
    _init_workspace(tmp_path, monkeypatch)
    source = tmp_path / "notes.txt"
    source.write_text("Some raw notes.", encoding="utf-8")
    first = runner.invoke(app, ["ingest", "notes.txt", "--auto"])
    assert first.exit_code == 0
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["ingest", "notes.txt"], input="y\n")

    assert result.exit_code == 0
    assert "~ raw/notes.txt" in result.stdout
    assert "+ raw/notes.txt" not in result.stdout


def test_traversal_basename_lands_inside_raw_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A traversal path like `../../evil.txt` lands as `raw/evil.txt` only
    -- never outside `raw/`/`bundle/sources/` (path-containment)."""
    base = tmp_path
    workspace = base / "a" / "b"
    workspace.mkdir(parents=True)
    outside_source = base / "evil.txt"
    outside_source.write_text("malicious", encoding="utf-8")
    _init_workspace(workspace, monkeypatch)

    result = runner.invoke(app, ["ingest", "../../evil.txt", "--auto"])

    assert result.exit_code == 0
    assert (workspace / "raw" / "evil.txt").is_file()
    assert (workspace / "raw" / "evil.txt").read_text(encoding="utf-8") == "malicious"
    # nothing written outside raw/ or bundle/sources/
    assert not (base / "raw").exists()
    assert (base / "evil.txt").read_text(encoding="utf-8") == "malicious"


def test_phase_a_preview_shown_then_phase_b_writes_on_confirm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A preview of the proposed changes is shown before any write; on
    confirmation, the raw copy, concept document, and index/log updates all
    land together on the happy path (scenarios: preview before write, Phase
    B writes proceed on confirm)."""
    _init_workspace(tmp_path, monkeypatch)
    source = tmp_path / "notes.txt"
    source.write_text("content", encoding="utf-8")
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["ingest", "notes.txt"], input="y\n")

    assert result.exit_code == 0
    assert "raw/notes.txt" in result.stdout
    assert "sources/notes.md" in result.stdout
    assert (tmp_path / "raw" / "notes.txt").is_file()
    assert (tmp_path / "bundle" / "sources" / "notes.md").is_file()


def test_auto_skips_the_prompt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`--auto` skips the confirmation prompt and writes directly (scenario:
    --auto skips the prompt)."""
    _init_workspace(tmp_path, monkeypatch)
    source = tmp_path / "notes.txt"
    source.write_text("content", encoding="utf-8")
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    assert "Proceed" not in result.output
    assert (tmp_path / "raw" / "notes.txt").is_file()


def test_review_false_skips_the_prompt_like_auto(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Config `review: false` skips the prompt the same as `--auto`
    (scenario: review: false skips the prompt like --auto)."""
    _init_workspace(tmp_path, monkeypatch)
    _set_config_field(tmp_path, "review: true", "review: false")
    source = tmp_path / "notes.txt"
    source.write_text("content", encoding="utf-8")
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["ingest", "notes.txt"])

    assert result.exit_code == 0
    assert "Proceed" not in result.output
    assert (tmp_path / "raw" / "notes.txt").is_file()


def test_non_tty_review_true_no_auto_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`review: true`, non-TTY stdin, no `--auto` refuses (exit 1), tells the
    user to re-run with `--auto`, and writes nothing (scenario: non-TTY
    without --auto refuses to write)."""
    _init_workspace(tmp_path, monkeypatch)
    source = tmp_path / "notes.txt"
    source.write_text("content", encoding="utf-8")
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["ingest", "notes.txt"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "--auto" in result.stderr
    assert _snapshot(tmp_path) == before


def test_phase_a_preparation_failure_surfaces_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An invalid `openkos.yaml` (malformed YAML) makes `read_config` raise
    `ValueError`; Phase A's preparation step routes it through the same
    graceful stderr-message + exit-1 path as an `OSError`, not a raw
    traceback, and writes nothing (mirrors `test_init.py`'s
    `test_corrupt_template_surfaces_cleanly`)."""
    _init_workspace(tmp_path, monkeypatch)
    (tmp_path / "openkos.yaml").write_text("not: valid: yaml: [", encoding="utf-8")
    source = tmp_path / "notes.txt"
    source.write_text("content", encoding="utf-8")
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "openkos ingest" in result.stderr
    assert "failed" in result.stderr
    assert _snapshot(tmp_path) == before


def test_missing_config_refuses_via_ingest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A workspace whose `openkos.yaml` was removed (`bundle/index.md` and
    `log.md` still present, so Phase A's workspace check passes) makes
    `read_config` raise while preparing the ingest; `ingest` routes it
    through the same graceful stderr-message + exit-1 path as any other
    `OSError`, not a raw traceback, and writes nothing (spec: Config Reader
    -- no workspace config, reached via `ingest`).

    Characterization test: the existing `except (OSError, ValueError)`
    handler around `read_config` already surfaces a clear, caught message
    naming `openkos.yaml`; no production code change was needed (see
    `test_config.py::test_read_config_raises_clear_error_when_config_missing`
    for the `read_config`-direct counterpart)."""
    _init_workspace(tmp_path, monkeypatch)
    (tmp_path / "openkos.yaml").unlink()
    source = tmp_path / "notes.txt"
    source.write_text("content", encoding="utf-8")
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "openkos ingest" in result.stderr
    assert "openkos.yaml" in result.stderr
    assert "Traceback" not in result.stderr
    assert _snapshot(tmp_path) == before


@pytest.mark.skipif(
    os.name != "posix" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="permission-based write failures require a POSIX non-root user",
)
def test_phase_b_write_failure_surfaces_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Phase-B write failure exits non-zero with a clear message, no
    traceback (mirrors `test_init.py`'s `test_write_failure_surfaces_cleanly`).

    Stripping write permission from `raw/` (created by `init`, so Phase A's
    checks all pass) forces the very first Phase-B write --
    `copy_exclusive(src, raw/<name>)` -- to raise `PermissionError`.
    """
    _init_workspace(tmp_path, monkeypatch)
    source = tmp_path / "notes.txt"
    source.write_text("content", encoding="utf-8")
    raw_dir = tmp_path / "raw"
    original_mode = stat.S_IMODE(raw_dir.stat().st_mode)
    raw_dir.chmod(0o500)
    try:
        result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])
    finally:
        raw_dir.chmod(original_mode)

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "openkos ingest" in result.stderr
    assert "failed" in result.stderr
    assert not (tmp_path / "bundle" / "sources" / "notes.md").exists()


def test_empty_slug_after_sanitization_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A filename stem made only of non-alphanumeric characters would
    slugify to an empty string (`bundle/sources/.md`); Phase A refuses
    instead of writing there (scenario: empty-slug guard)."""
    _init_workspace(tmp_path, monkeypatch)
    source = tmp_path / "+++.txt"
    source.write_text("content", encoding="utf-8")
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["ingest", "+++.txt", "--auto"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "openkos ingest" in result.stderr
    assert "cannot derive a concept name" in result.stderr
    assert _snapshot(tmp_path) == before


def test_phase_a_permission_error_surfaces_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `PermissionError` raised by a Phase A stat call (`is_file`) is
    caught and reported cleanly, not left to surface as a raw traceback
    (scenario: guard Phase A reads)."""
    _init_workspace(tmp_path, monkeypatch)
    source = tmp_path / "notes.txt"
    source.write_text("content", encoding="utf-8")
    before = _snapshot(tmp_path)

    original_is_file = Path.is_file

    def failing_is_file(self: Path) -> bool:
        if self.name == "notes.txt":
            raise PermissionError("simulated permission failure")
        return original_is_file(self)

    monkeypatch.setattr(Path, "is_file", failing_is_file)

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "openkos ingest" in result.stderr
    assert "failed" in result.stderr
    assert _snapshot(tmp_path) == before


@pytest.mark.parametrize("fail_step", ["concept", "index", "log"])
def test_phase_b_failure_surfaces_cleanly_and_leaves_detectable_orphan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fail_step: str
) -> None:
    """A failure at any Phase-B write step (concept, `index.md`, or
    `log.md`) exits cleanly (exit 1, `openkos ingest:` message, no
    traceback) -- but does NOT roll back the steps that already succeeded
    (scenario: Phase B retreat to create-only, non-transactional writes,
    D5). Every write before the failing step is create-only or atomic, so
    none is left half-written; the writes that already landed remain as a
    detectable orphan (e.g. an uncatalogued concept) rather than being
    undone. Recovery is via git (`git status`/`git checkout`/`git clean`),
    not an in-process rollback."""
    _init_workspace(tmp_path, monkeypatch)
    source = tmp_path / "notes.txt"
    source.write_text("content", encoding="utf-8")

    original_write_exclusive = fsio.write_exclusive
    original_write_atomic = fsio.write_atomic

    def failing_write_exclusive(path: Path, content: str) -> None:
        if fail_step == "concept" and path.suffix == ".md":
            raise OSError("simulated concept write failure")
        original_write_exclusive(path, content)

    def failing_write_atomic(path: Path, content: str) -> None:
        if fail_step == "index" and path.name == "index.md":
            raise OSError("simulated index write failure")
        if fail_step == "log" and path.name == "log.md":
            raise OSError("simulated log write failure")
        original_write_atomic(path, content)

    monkeypatch.setattr(fsio, "write_exclusive", failing_write_exclusive)
    monkeypatch.setattr(fsio, "write_atomic", failing_write_atomic)

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "openkos ingest" in result.stderr
    assert "failed" in result.stderr
    assert "Traceback" not in result.stderr
    # the raw copy always lands before every parametrized failing step
    assert (tmp_path / "raw" / "notes.txt").is_file()
    if fail_step in ("index", "log"):
        # the concept document was already written when index/log failed --
        # a detectable, uncatalogued orphan, left in place (not rolled back)
        assert (tmp_path / "bundle" / "sources" / "notes.md").is_file()
    else:
        assert not (tmp_path / "bundle" / "sources" / "notes.md").exists()


def test_sensitivity_matches_config_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The generated Source concept's `sensitivity` equals config's
    `default_sensitivity` (scenario: sensitivity matches config default)."""
    _init_workspace(tmp_path, monkeypatch)
    _set_config_field(
        tmp_path, "default_sensitivity: private", "default_sensitivity: confidential"
    )
    source = tmp_path / "notes.txt"
    source.write_text("content", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    concept_text = (tmp_path / "bundle" / "sources" / "notes.md").read_text(
        encoding="utf-8"
    )
    metadata, _ = okf.load_frontmatter(concept_text)
    assert metadata["sensitivity"] == "confidential"
