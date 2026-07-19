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

import json
import os
import stat
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner, _NamedTextIOWrapper

from openkos import fsio
from openkos.cli.main import app
from openkos.llm.base import Message
from openkos.llm.ollama import OllamaUnavailable
from openkos.model import okf

runner = CliRunner()


def _simulate_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make `sys.stdin.isatty()` report `True` inside a `CliRunner.invoke` call.

    See `tests/unit/cli/test_init.py::_simulate_tty` for why the CLASS
    method must be patched rather than the current `sys.stdin` instance.
    """
    monkeypatch.setattr(_NamedTextIOWrapper, "isatty", lambda self: True)


class _FakeLLM:
    """A structural `LLMBackend`, mirroring `test_answer.py::_FakeLLM`
    (test_answer.py:41-50): records every `chat()` call and returns a fixed
    reply, or raises a fixed exception instead -- zero network, zero real
    Ollama process."""

    def __init__(self, reply: str = "", *, raises: Exception | None = None) -> None:
        self.reply = reply
        self.raises = raises
        self.calls: list[list[Message]] = []

    def chat(self, messages: Sequence[Message]) -> str:
        self.calls.append(list(messages))
        if self.raises is not None:
            raise self.raises
        return self.reply


def _patch_llm(
    monkeypatch: pytest.MonkeyPatch,
    reply: str = '{"extract": false}',
    *,
    raises: Exception | None = None,
) -> _FakeLLM:
    """Replace `openkos.cli.main.OllamaClient` with a factory returning a
    configured `_FakeLLM` -- mirrors `test_query.py`'s pattern of patching
    the CLI's LLM seam directly (module docstring: "zero network, zero real
    Ollama process") rather than mocking `extract_concept`, so `ingest`
    exercises the REAL `extraction.extract_concept` parse/validation path
    end to end. Default reply declines extraction (`extract: false`)."""
    fake = _FakeLLM(reply, raises=raises)
    monkeypatch.setattr("openkos.cli.main.OllamaClient", lambda *args, **kwargs: fake)
    return fake


@pytest.fixture(autouse=True)
def _default_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Protect every test in this module from a real Ollama network call by
    default: `openkos.cli.main.OllamaClient` is replaced with a fake backend
    that always declines extraction, so `ingest`'s pre-existing Source-only
    scenarios stay deterministic and offline. Tests that need a specific
    extraction outcome call `_patch_llm` again to override this default."""
    _patch_llm(monkeypatch)


def _concept_reply(title: str = "Stoic Dichotomy Of Control") -> str:
    """A well-formed `extract_concept` JSON reply classifying as `Concept`."""
    return json.dumps(
        {
            "extract": True,
            "type": "Concept",
            "title": title,
            "description": (
                "A framework distinguishing what is and is not within our control."
            ),
            "body": "Elaboration on applying the framework day to day.",
        }
    )


def _entity_reply(title: str = "Enchiridion") -> str:
    """A well-formed `extract_concept` JSON reply classifying as `Entity`."""
    return json.dumps(
        {
            "extract": True,
            "type": "Entity",
            "title": title,
            "description": "A short handbook of Stoic ethical advice.",
            "body": "",
        }
    )


def _person_reply(title: str = "Epictetus") -> str:
    """A well-formed `extract_concept` JSON reply classifying as `Person`."""
    return json.dumps(
        {
            "extract": True,
            "type": "Person",
            "title": title,
            "description": "A Stoic philosopher and former slave.",
            "body": "Taught that we control only our own judgments.",
        }
    )


def _organization_reply(title: str = "Praxis Foundation") -> str:
    """A well-formed `extract_concept` JSON reply classifying as `Organization`."""
    return json.dumps(
        {
            "extract": True,
            "type": "Organization",
            "title": title,
            "description": "A nonprofit researching Stoic philosophy.",
            "body": "",
        }
    )


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


# --- Extraction (WU4, Phase 5-6): LLM Concept/Entity extraction ------------


def test_successful_concept_extraction_writes_both_documents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A well-formed `Concept` reply writes the Source AND a
    `bundle/concepts/<slug>.md` document, the derived doc's `provenance`
    references the Source, and both pass `check_conformance` (scenario:
    successful extraction yields a Concept)."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, _concept_reply())
    source = tmp_path / "notes.txt"
    source.write_text("Some raw notes about self-control.", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    concept_path = tmp_path / "bundle" / "concepts" / "stoic-dichotomy-of-control.md"
    assert concept_path.is_file()
    metadata, body = okf.load_frontmatter(concept_path.read_text(encoding="utf-8"))
    assert metadata["type"] == "Concept"
    assert metadata["provenance"] == ["sources/notes"]
    assert "sources/notes.md" in body
    assert okf.check_conformance(tmp_path / "bundle") == []
    index_text = (tmp_path / "bundle" / "index.md").read_text(encoding="utf-8")
    assert "sources/notes.md" in index_text
    assert "concepts/stoic-dichotomy-of-control.md" in index_text
    assert "# Concepts" in index_text
    log_text = (tmp_path / "bundle" / "log.md").read_text(encoding="utf-8")
    assert "Extracted" in log_text
    assert "stoic-dichotomy-of-control.md" in log_text


def test_successful_entity_extraction_writes_entities_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A well-formed `Entity` reply writes the Source AND a
    `bundle/entities/<slug>.md` document, whose `provenance` references the
    Source (scenario: successful extraction yields an Entity)."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, _entity_reply())
    source = tmp_path / "notes.txt"
    source.write_text("A field manual for Stoic practice.", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    entity_path = tmp_path / "bundle" / "entities" / "enchiridion.md"
    assert entity_path.is_file()
    metadata, body = okf.load_frontmatter(entity_path.read_text(encoding="utf-8"))
    assert metadata["type"] == "Entity"
    assert metadata["provenance"] == ["sources/notes"]
    assert "sources/notes.md" in body
    assert okf.check_conformance(tmp_path / "bundle") == []
    index_text = (tmp_path / "bundle" / "index.md").read_text(encoding="utf-8")
    assert "entities/enchiridion.md" in index_text
    assert "# Entities" in index_text


def test_successful_person_extraction_writes_people_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A well-formed `Person` reply writes the Source AND a
    `bundle/people/<slug>.md` document, whose `provenance` references the
    Source (scenario: Extraction yields a Person)."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, _person_reply())
    source = tmp_path / "notes.txt"
    source.write_text("Epictetus was a Stoic philosopher.", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    person_path = tmp_path / "bundle" / "people" / "epictetus.md"
    assert person_path.is_file()
    metadata, body = okf.load_frontmatter(person_path.read_text(encoding="utf-8"))
    assert metadata["type"] == "Person"
    assert metadata["provenance"] == ["sources/notes"]
    assert "sources/notes.md" in body
    assert okf.check_conformance(tmp_path / "bundle") == []
    index_text = (tmp_path / "bundle" / "index.md").read_text(encoding="utf-8")
    assert "sources/notes.md" in index_text
    assert "people/epictetus.md" in index_text
    assert "# People" in index_text
    log_text = (tmp_path / "bundle" / "log.md").read_text(encoding="utf-8")
    assert "Extracted" in log_text
    assert "epictetus.md" in log_text


def test_successful_organization_extraction_writes_organizations_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A well-formed `Organization` reply writes the Source AND a
    `bundle/organizations/<slug>.md` document, whose `provenance` references
    the Source (scenario: Extraction yields an Organization)."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, _organization_reply())
    source = tmp_path / "notes.txt"
    source.write_text("The Praxis Foundation researches Stoicism.", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    org_path = tmp_path / "bundle" / "organizations" / "praxis-foundation.md"
    assert org_path.is_file()
    metadata, body = okf.load_frontmatter(org_path.read_text(encoding="utf-8"))
    assert metadata["type"] == "Organization"
    assert metadata["provenance"] == ["sources/notes"]
    assert "sources/notes.md" in body
    assert okf.check_conformance(tmp_path / "bundle") == []
    index_text = (tmp_path / "bundle" / "index.md").read_text(encoding="utf-8")
    assert "organizations/praxis-foundation.md" in index_text
    assert "# Organizations" in index_text


def test_malformed_json_reply_degrades_to_source_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reply that is not parseable structured output degrades to
    Source-only: no `bundle/concepts/`/`bundle/entities/` directory is
    created, a note appears on stderr, and the exit code is 0 (scenario:
    malformed JSON degrades to Source-only)."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, "this is not JSON at all")
    source = tmp_path / "notes.txt"
    source.write_text("content", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    assert not (tmp_path / "bundle" / "concepts").exists()
    assert not (tmp_path / "bundle" / "entities").exists()
    assert "no concept extracted" in result.stderr
    assert (tmp_path / "bundle" / "sources" / "notes.md").is_file()


def test_invalid_type_degrades_to_source_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A well-formed reply whose `type` is outside `{Concept, Entity,
    Person, Organization}` degrades to Source-only, with a stderr note and
    exit 0 (scenario: type outside the 4-value set degrades to
    Source-only). `"Place"` is a real future KOM continuant, still out of
    scope for this vocabulary batch -- a genuinely invalid sentinel."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(
        monkeypatch,
        json.dumps(
            {
                "extract": True,
                "type": "Place",
                "title": "Athens",
                "description": "An ancient city.",
                "body": "",
            }
        ),
    )
    source = tmp_path / "notes.txt"
    source.write_text("content", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    assert not (tmp_path / "bundle" / "concepts").exists()
    assert not (tmp_path / "bundle" / "entities").exists()
    assert not (tmp_path / "bundle" / "people").exists()
    assert not (tmp_path / "bundle" / "organizations").exists()
    assert "no concept extracted" in result.stderr
    assert (tmp_path / "bundle" / "sources" / "notes.md").is_file()


def test_missing_title_degrades_to_source_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A well-formed reply with an empty `title` degrades to Source-only,
    with a stderr note and exit 0 (scenario: missing title degrades to
    Source-only)."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(
        monkeypatch,
        json.dumps(
            {
                "extract": True,
                "type": "Concept",
                "title": "",
                "description": "A framework.",
                "body": "",
            }
        ),
    )
    source = tmp_path / "notes.txt"
    source.write_text("content", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    assert not (tmp_path / "bundle" / "concepts").exists()
    assert "no concept extracted" in result.stderr
    assert (tmp_path / "bundle" / "sources" / "notes.md").is_file()


def test_llm_backend_error_degrades_to_source_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An `OllamaError`-family exception raised by `chat()` is caught locally
    (never crashes `ingest`), degrades to Source-only, prints a
    distinguishing stderr note, and exits 0 (scenario: LLM backend
    unavailable)."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(
        monkeypatch,
        raises=OllamaUnavailable("Ollama not reachable at http://localhost:11434"),
    )
    source = tmp_path / "notes.txt"
    source.write_text("content", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    assert "concept extraction skipped" in result.stderr
    assert "Ollama not reachable" in result.stderr
    assert not (tmp_path / "bundle" / "concepts").exists()
    assert (tmp_path / "bundle" / "sources" / "notes.md").is_file()


def test_auto_runs_extraction_and_writes_both_without_prompting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--auto` still runs extraction (only the confirmation PROMPT is
    skipped): both the Source and the derived object are written with no
    `Proceed` prompt in the output (scenario: --auto writes both without
    prompting)."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, _concept_reply())
    source = tmp_path / "notes.txt"
    source.write_text("Some raw notes about self-control.", encoding="utf-8")
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    assert "Proceed" not in result.output
    assert (tmp_path / "bundle" / "sources" / "notes.md").is_file()
    assert (
        tmp_path / "bundle" / "concepts" / "stoic-dichotomy-of-control.md"
    ).is_file()


def test_interactive_preview_lists_both_objects_before_confirm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The confirmation preview lists BOTH the proposed Source concept and
    the proposed derived object before the confirm gate (scenario:
    interactive confirm shows both objects)."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, _concept_reply())
    source = tmp_path / "notes.txt"
    source.write_text("Some raw notes about self-control.", encoding="utf-8")
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["ingest", "notes.txt"], input="y\n")

    assert result.exit_code == 0
    assert "sources/notes.md" in result.stdout
    assert "concepts/stoic-dichotomy-of-control.md" in result.stdout
    assert (tmp_path / "bundle" / "sources" / "notes.md").is_file()
    assert (
        tmp_path / "bundle" / "concepts" / "stoic-dichotomy-of-control.md"
    ).is_file()


def test_declining_confirm_writes_neither_object(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Declining the confirm prompt aborts with NEITHER the Source nor the
    derived object written (scenario: interactive confirm shows both
    objects, declining aborts with no files written)."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, _concept_reply())
    source = tmp_path / "notes.txt"
    source.write_text("Some raw notes about self-control.", encoding="utf-8")
    _simulate_tty(monkeypatch)
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["ingest", "notes.txt"], input="n\n")

    assert result.exit_code == 1
    assert not (tmp_path / "bundle" / "sources" / "notes.md").exists()
    assert not (tmp_path / "bundle" / "concepts").exists()
    assert _snapshot(tmp_path) == before


def test_idempotent_reingest_leaves_existing_derived_object_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Re-ingesting a source whose derived object already exists (possibly
    hand-edited) leaves that file byte-unchanged -- no overwrite, no
    re-extraction of its content (scenario: re-ingest does not overwrite
    existing derived object)."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, _concept_reply())
    source = tmp_path / "notes.txt"
    source.write_text("Some raw notes about self-control.", encoding="utf-8")

    first = runner.invoke(app, ["ingest", "notes.txt", "--auto"])
    assert first.exit_code == 0
    concept_path = tmp_path / "bundle" / "concepts" / "stoic-dichotomy-of-control.md"
    assert concept_path.is_file()
    hand_edited = concept_path.read_text(encoding="utf-8") + "\n<!-- hand edit -->\n"
    concept_path.write_text(hand_edited, encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    assert concept_path.read_text(encoding="utf-8") == hand_edited
    index_text = (tmp_path / "bundle" / "index.md").read_text(encoding="utf-8")
    assert index_text.count("concepts/stoic-dichotomy-of-control.md") == 1
    log_text = (tmp_path / "bundle" / "log.md").read_text(encoding="utf-8")
    assert log_text.count("stoic-dichotomy-of-control.md") == 1


def test_idempotent_reingest_leaves_existing_person_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Re-ingesting a source whose Person derived object already exists
    leaves that file byte-unchanged and does not duplicate the catalog
    entry -- the idempotency scan must cover `people/`, the SAME guard
    `_source_has_derived_object` applies to `concepts/`/`entities/` (spec:
    Re-ingesting a Person source does not duplicate)."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, _person_reply())
    source = tmp_path / "notes.txt"
    source.write_text("Epictetus was a Stoic philosopher.", encoding="utf-8")

    first = runner.invoke(app, ["ingest", "notes.txt", "--auto"])
    assert first.exit_code == 0
    person_path = tmp_path / "bundle" / "people" / "epictetus.md"
    assert person_path.is_file()
    hand_edited = person_path.read_text(encoding="utf-8") + "\n<!-- hand edit -->\n"
    person_path.write_text(hand_edited, encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    assert person_path.read_text(encoding="utf-8") == hand_edited
    index_text = (tmp_path / "bundle" / "index.md").read_text(encoding="utf-8")
    assert index_text.count("people/epictetus.md") == 1
    log_text = (tmp_path / "bundle" / "log.md").read_text(encoding="utf-8")
    assert log_text.count("epictetus.md") == 1


def test_idempotent_reingest_leaves_existing_organization_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Re-ingesting a source whose Organization derived object already
    exists leaves that file byte-unchanged and does not duplicate the
    catalog entry -- the idempotency scan must cover `organizations/`, the
    SAME guard `_source_has_derived_object` applies to
    `concepts/`/`entities/`/`people/` (spec: Re-ingesting an Organization
    source does not duplicate)."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, _organization_reply())
    source = tmp_path / "notes.txt"
    source.write_text("The Praxis Foundation researches Stoicism.", encoding="utf-8")

    first = runner.invoke(app, ["ingest", "notes.txt", "--auto"])
    assert first.exit_code == 0
    org_path = tmp_path / "bundle" / "organizations" / "praxis-foundation.md"
    assert org_path.is_file()
    hand_edited = org_path.read_text(encoding="utf-8") + "\n<!-- hand edit -->\n"
    org_path.write_text(hand_edited, encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    assert org_path.read_text(encoding="utf-8") == hand_edited
    index_text = (tmp_path / "bundle" / "index.md").read_text(encoding="utf-8")
    assert index_text.count("organizations/praxis-foundation.md") == 1
    log_text = (tmp_path / "bundle" / "log.md").read_text(encoding="utf-8")
    assert log_text.count("praxis-foundation.md") == 1


def test_reingest_with_nondeterministic_llm_title_skips_second_extraction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Re-ingesting a source whose derived object already exists must NOT
    create a second derived document even when the LLM (nondeterministic)
    returns a DIFFERENT title on the second attempt -- idempotency is keyed
    off provenance (deterministic), not the LLM's slugified title. The LLM
    must not even be called on the re-ingest (quiet idempotent skip)."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, _concept_reply(title="Stoic Dichotomy Of Control"))
    source = tmp_path / "notes.txt"
    source.write_text("Some raw notes about self-control.", encoding="utf-8")

    first = runner.invoke(app, ["ingest", "notes.txt", "--auto"])
    assert first.exit_code == 0
    first_concept_path = (
        tmp_path / "bundle" / "concepts" / "stoic-dichotomy-of-control.md"
    )
    assert first_concept_path.is_file()

    fake = _patch_llm(monkeypatch, _concept_reply(title="A Completely Different Title"))
    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    assert fake.calls == []
    concepts_dir = tmp_path / "bundle" / "concepts"
    assert [p.name for p in concepts_dir.glob("*.md")] == [
        "stoic-dichotomy-of-control.md"
    ]
    assert not (tmp_path / "bundle" / "entities").exists()
    index_text = (tmp_path / "bundle" / "index.md").read_text(encoding="utf-8")
    assert index_text.count("concepts/stoic-dichotomy-of-control.md") == 1
    assert "a-completely-different-title" not in index_text
    log_text = (tmp_path / "bundle" / "log.md").read_text(encoding="utf-8")
    assert log_text.count("stoic-dichotomy-of-control.md") == 1
    assert "a-completely-different-title" not in log_text


def test_source_has_derived_object_ignores_unrelated_and_malformed_concepts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`_source_has_derived_object` must skip over derived documents that
    belong to OTHER sources (provenance mismatch -- keeps scanning) and over
    a derived document with unparseable frontmatter (caught, keeps scanning)
    before concluding no match exists -- ingesting a second, unrelated
    source still gets its own extraction."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, _concept_reply(title="Stoic Dichotomy Of Control"))
    source_a = tmp_path / "notes-a.txt"
    source_a.write_text("Some raw notes about self-control.", encoding="utf-8")
    first = runner.invoke(app, ["ingest", "notes-a.txt", "--auto"])
    assert first.exit_code == 0
    assert (
        tmp_path / "bundle" / "concepts" / "stoic-dichotomy-of-control.md"
    ).is_file()

    malformed_path = tmp_path / "bundle" / "concepts" / "malformed.md"
    malformed_path.write_text("---\nfoo: [unclosed\n---\nbody", encoding="utf-8")

    _patch_llm(monkeypatch, _concept_reply(title="Enchiridion"))
    source_b = tmp_path / "notes-b.txt"
    source_b.write_text("Different raw notes entirely.", encoding="utf-8")
    result = runner.invoke(app, ["ingest", "notes-b.txt", "--auto"])

    assert result.exit_code == 0
    assert (tmp_path / "bundle" / "concepts" / "enchiridion.md").is_file()


def test_reingest_of_identical_source_can_still_stage_a_new_derived_object(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A byte-identical re-ingest (Source `regenerate` path) that now gets a
    successful extraction (e.g. the LLM declined on the first attempt)
    still stages and writes the derived object -- Source `regenerate` and
    derived-object staging are independent (preview shows `+
    bundle/concepts/<slug>.md` even under the re-ingest preview banner)."""
    _init_workspace(tmp_path, monkeypatch)
    source = tmp_path / "notes.txt"
    source.write_text("Some raw notes about self-control.", encoding="utf-8")

    first = runner.invoke(app, ["ingest", "notes.txt", "--auto"])
    assert first.exit_code == 0
    assert not (tmp_path / "bundle" / "concepts").exists()

    _patch_llm(monkeypatch, _concept_reply())
    _simulate_tty(monkeypatch)
    result = runner.invoke(app, ["ingest", "notes.txt"], input="y\n")

    assert result.exit_code == 0
    assert "re-ingest" in result.stdout
    assert "+ bundle/concepts/stoic-dichotomy-of-control.md" in result.stdout
    concept_path = tmp_path / "bundle" / "concepts" / "stoic-dichotomy-of-control.md"
    assert concept_path.is_file()
    index_text = (tmp_path / "bundle" / "index.md").read_text(encoding="utf-8")
    assert "concepts/stoic-dichotomy-of-control.md" in index_text


def test_derived_object_inherits_source_sensitivity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The derived object's `sensitivity` equals the Source's configured
    `default_sensitivity` (scenario: provenance and sensitivity
    inherited)."""
    _init_workspace(tmp_path, monkeypatch)
    _set_config_field(
        tmp_path, "default_sensitivity: private", "default_sensitivity: confidential"
    )
    _patch_llm(monkeypatch, _concept_reply())
    source = tmp_path / "notes.txt"
    source.write_text("Some raw notes about self-control.", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    concept_path = tmp_path / "bundle" / "concepts" / "stoic-dichotomy-of-control.md"
    metadata, _ = okf.load_frontmatter(concept_path.read_text(encoding="utf-8"))
    assert metadata["sensitivity"] == "confidential"


def test_symbol_only_title_slugifies_empty_degrades_to_source_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A well-formed reply whose `title` is made only of characters
    `_slugify` strips (so it would collide with an empty concept name)
    degrades to Source-only rather than writing to `bundle/concepts/.md`
    (fail-closed slug guard, mirroring the Source's own empty-slug refusal
    -- but degrading, not refusing the whole ingest)."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, _concept_reply(title="!!!"))
    source = tmp_path / "notes.txt"
    source.write_text("content", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    assert not (tmp_path / "bundle" / "concepts").exists()
    assert "could not be turned into a slug" in result.stderr
    assert (tmp_path / "bundle" / "sources" / "notes.md").is_file()


def test_builder_validation_failure_degrades_to_source_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An extracted `title` containing an embedded newline passes
    `extract_concept`'s own non-empty check but fails `okf.build_concept`'s
    stricter single-line gate; that `ValueError` is caught locally and
    degrades to Source-only, never crashing the whole ingest (fail-closed
    validation of untrusted LLM output that slipped past the extraction
    leaf's own validation)."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, _concept_reply(title="Stoic Framework\nExtra Line"))
    source = tmp_path / "notes.txt"
    source.write_text("content", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    assert not (tmp_path / "bundle" / "concepts").exists()
    assert "extracted content failed validation" in result.stderr
    assert (tmp_path / "bundle" / "sources" / "notes.md").is_file()


def test_undecodable_source_skips_extraction_without_network_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A binary/undecodable source has no text to extract from: extraction
    is never attempted (the fake LLM records zero `chat()` calls), a note is
    reported to stderr (every degrade case is reported per the docstring),
    and the Source-only result is unaffected."""
    _init_workspace(tmp_path, monkeypatch)
    fake = _patch_llm(monkeypatch, _concept_reply())
    source = tmp_path / "notes.bin"
    source.write_bytes(b"\xff\xfe not valid utf-8 \x00\x01")

    result = runner.invoke(app, ["ingest", "notes.bin", "--auto"])

    assert result.exit_code == 0
    assert fake.calls == []
    assert not (tmp_path / "bundle" / "concepts").exists()
    assert (tmp_path / "bundle" / "sources" / "notes.md").is_file()
    assert "no extractable text" in result.stderr
