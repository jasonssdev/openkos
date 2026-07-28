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
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

import pytest
from typer.testing import CliRunner, _NamedTextIOWrapper

from openkos import fsio
from openkos.cli import main
from openkos.cli.main import app
from openkos.llm.base import EMBED_DIM, Message
from openkos.llm.ollama import (
    OllamaError,
    OllamaModelNotFound,
    OllamaUnavailable,
)
from openkos.model import okf

runner = CliRunner()


def test_format_type_tally_empty_dict_yields_empty_string() -> None:
    """`_format_type_tally({})` returns `""` -- signals "no line to print"
    (spec: Reusable Type-Tally Formatting Helper, empty dict scenario)."""
    assert main._format_type_tally({}) == ""


def test_format_type_tally_single_object_singular_wording() -> None:
    """A single `Concept` renders singular `"object"` wording (spec:
    Single-entry dict yields singular line)."""
    assert main._format_type_tally({"Concept": 1}) == "extracted 1 object — 1 Concept"


def test_format_type_tally_multiple_objects_one_type_plural_wording() -> None:
    """Three `Entity` objects render plural `"objects"` wording (spec:
    Per-Type Derived-Object Tally Summary, multiple objects one type)."""
    assert main._format_type_tally({"Entity": 3}) == "extracted 3 objects — 3 Entity"


def test_format_type_tally_orders_by_canonical_registry_not_insertion_order() -> None:
    """`{"Person": 2, "Concept": 1}` (insertion order Person-then-Concept)
    renders `Concept` before `Person`, per canonical `_TYPE_TO_SECTION`
    order (spec: Multi-entry dict is ordered by canonical registry, not
    insertion order)."""
    assert (
        main._format_type_tally({"Person": 2, "Concept": 1})
        == "extracted 3 objects — 1 Concept, 2 Person"
    )


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


def _place_reply(title: str = "Yellowstone National Park") -> str:
    """A well-formed `extract_concept` JSON reply classifying as `Place`."""
    return json.dumps(
        {
            "extract": True,
            "type": "Place",
            "title": title,
            "description": "A national park in the western United States.",
            "body": "Known for its geysers and geothermal features.",
        }
    )


def _event_reply(title: str = "Stoicon 2026") -> str:
    """A well-formed `extract_concept` JSON reply classifying as `Event`."""
    return json.dumps(
        {
            "extract": True,
            "type": "Event",
            "title": title,
            "description": "An annual conference on Stoic philosophy.",
            "body": "Held over a single weekend with talks and workshops.",
        }
    )


def _procedure_reply(title: str = "Morning Journaling Routine") -> str:
    """A well-formed `extract_concept` JSON reply classifying as `Procedure`."""
    return json.dumps(
        {
            "extract": True,
            "type": "Procedure",
            "title": title,
            "description": "A repeatable daily reflection practice.",
            "body": "Write three things you are grateful for, then one obstacle.",
        }
    )


def _decision_reply(title: str = "Frame the Essay Around Control") -> str:
    """A well-formed `extract_concept` JSON reply classifying as `Decision`."""
    return json.dumps(
        {
            "extract": True,
            "type": "Decision",
            "title": title,
            "description": (
                "A choice to structure the essay around the dichotomy of "
                "control, made after weighing two alternative framings."
            ),
            "body": "Chosen over a chronological framing; status: adopted.",
        }
    )


def _project_reply(title: str = "Stoicism Essay Series") -> str:
    """A well-formed `extract_concept` JSON reply classifying as `Project`."""
    return json.dumps(
        {
            "extract": True,
            "type": "Project",
            "title": title,
            "description": (
                "An ongoing series of essays on Stoic practice, running "
                "over several months toward a publishable collection."
            ),
            "body": "Six essays planned across Q1-Q2, each drafted then revised.",
        }
    )


def _multi_object_reply(*replies: str) -> str:
    """Combine N single-object JSON replies (each from a `_..._reply()`
    helper above) into one JSON-array reply, mirroring a real multi-object
    `extract_concept` batch (design D1: array-shaped reply)."""
    return json.dumps([json.loads(reply) for reply in replies])


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


def test_phase_b_write_failure_on_second_derived_object_leaves_first_as_orphan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `write_exclusive` failure on the SECOND of three staged derived
    objects leaves the FIRST derived object written as a detectable
    orphan, never reaches the THIRD, and never extends `index.md`/`log.md`
    for either un-written object -- documents the accepted
    non-transactional Phase B behavior for multi-object batches (D5
    retreat), mirroring
    `test_phase_b_failure_surfaces_cleanly_and_leaves_detectable_orphan`'s
    failure-injection pattern."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(
        monkeypatch,
        _multi_object_reply(_concept_reply(), _person_reply(), _organization_reply()),
    )
    source = tmp_path / "notes.txt"
    source.write_text("content", encoding="utf-8")

    original_write_exclusive = fsio.write_exclusive

    def failing_write_exclusive(path: Path, content: str) -> None:
        if path.parent.name == "people":
            raise OSError("simulated second-derived-object write failure")
        original_write_exclusive(path, content)

    monkeypatch.setattr(fsio, "write_exclusive", failing_write_exclusive)

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "openkos ingest" in result.stderr
    assert "failed" in result.stderr
    concept_path = tmp_path / "bundle" / "concepts" / "stoic-dichotomy-of-control.md"
    person_path = tmp_path / "bundle" / "people" / "epictetus.md"
    organization_path = tmp_path / "bundle" / "organizations" / "praxis-foundation.md"
    assert concept_path.is_file()
    assert not person_path.exists()
    assert not organization_path.exists()
    index_text = (tmp_path / "bundle" / "index.md").read_text(encoding="utf-8")
    log_text = (tmp_path / "bundle" / "log.md").read_text(encoding="utf-8")
    assert "epictetus.md" not in index_text
    assert "praxis-foundation.md" not in index_text
    assert "epictetus.md" not in log_text
    assert "praxis-foundation.md" not in log_text


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


# --- sensitivity-fail-closed-filter, S3b: extract floor gate --------------


def test_confidential_default_sensitivity_floor_skips_extraction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`default_sensitivity: confidential` short-circuits BEFORE
    `extract_concept`/`llm.chat` is ever called, returns a Source-only
    result, and emits the existing "keeping the Source only" degrade message
    (spec: Confidential floor skips extract's llm.chat call)."""
    _init_workspace(tmp_path, monkeypatch)
    _set_config_field(
        tmp_path, "default_sensitivity: private", "default_sensitivity: confidential"
    )
    fake = _patch_llm(monkeypatch, _concept_reply())
    source = tmp_path / "notes.txt"
    source.write_text("Some raw notes about self-control.", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    assert "keeping the Source only" in result.stderr
    assert fake.calls == []
    concept_path = tmp_path / "bundle" / "concepts" / "stoic-dichotomy-of-control.md"
    assert not concept_path.exists()
    source_path = tmp_path / "bundle" / "sources" / "notes.md"
    assert source_path.is_file()


def test_private_default_sensitivity_floor_calls_llm_chat_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`default_sensitivity: private` (the packaged default) proceeds to
    call `extract_concept`/`llm.chat` exactly as before this change (spec:
    Private floor proceeds unchanged)."""
    _init_workspace(tmp_path, monkeypatch)
    fake = _patch_llm(monkeypatch, _concept_reply())
    source = tmp_path / "notes.txt"
    source.write_text("Some raw notes about self-control.", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    assert len(fake.calls) == 1
    concept_path = tmp_path / "bundle" / "concepts" / "stoic-dichotomy-of-control.md"
    assert concept_path.is_file()


def test_blank_default_sensitivity_still_trips_the_confidential_floor_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A blank/whitespace `default_sensitivity: ""` MUST still be treated as
    confidential-or-more-restrictive and skip extraction -- `okf._rank("")`
    alone resolves to `"private"` (the merge-floor default), which would
    wrongly leave the gate untripped and send raw source text to `llm.chat`
    (correction batch, post-4R-review FIX 1: extract floor-gate fail-open)."""
    _init_workspace(tmp_path, monkeypatch)
    _set_config_field(
        tmp_path, "default_sensitivity: private", 'default_sensitivity: ""'
    )
    fake = _patch_llm(monkeypatch, _concept_reply())
    source = tmp_path / "notes.txt"
    source.write_text("Some raw notes about self-control.", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    assert "keeping the Source only" in result.stderr
    assert fake.calls == []
    concept_path = tmp_path / "bundle" / "concepts" / "stoic-dichotomy-of-control.md"
    assert not concept_path.exists()
    source_path = tmp_path / "bundle" / "sources" / "notes.md"
    assert source_path.is_file()


def test_include_confidential_bypasses_the_confidential_floor_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--include-confidential` bypasses the `default_sensitivity:
    confidential` floor gate: `extract_concept`/`llm.chat` IS called, and the
    derived object is written, even at a confidential floor (spec:
    `--include-confidential` Escape Flag)."""
    _init_workspace(tmp_path, monkeypatch)
    _set_config_field(
        tmp_path, "default_sensitivity: private", "default_sensitivity: confidential"
    )
    fake = _patch_llm(monkeypatch, _concept_reply())
    source = tmp_path / "notes.txt"
    source.write_text("Some raw notes about self-control.", encoding="utf-8")

    result = runner.invoke(
        app, ["ingest", "notes.txt", "--auto", "--include-confidential"]
    )

    assert result.exit_code == 0
    assert len(fake.calls) == 1
    concept_path = tmp_path / "bundle" / "concepts" / "stoic-dichotomy-of-control.md"
    assert concept_path.is_file()


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


def test_successful_place_extraction_writes_places_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A well-formed `Place` reply writes the Source AND a
    `bundle/places/<slug>.md` document, whose `provenance` references the
    Source, cataloged under `# Places` and passing conformance (spec: "Place
    derived object is written and cataloged")."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, _place_reply())
    source = tmp_path / "notes.txt"
    source.write_text(
        "Yellowstone is a national park known for its geysers.", encoding="utf-8"
    )

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    place_path = tmp_path / "bundle" / "places" / "yellowstone-national-park.md"
    assert place_path.is_file()
    metadata, body = okf.load_frontmatter(place_path.read_text(encoding="utf-8"))
    assert metadata["type"] == "Place"
    assert metadata["freshness"] == "snapshot"
    assert metadata["provenance"] == ["sources/notes"]
    assert "sources/notes.md" in body
    assert okf.check_conformance(tmp_path / "bundle") == []
    index_text = (tmp_path / "bundle" / "index.md").read_text(encoding="utf-8")
    assert "places/yellowstone-national-park.md" in index_text
    assert "# Places" in index_text


def test_successful_event_extraction_writes_events_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A well-formed `Event` reply writes the Source AND a
    `bundle/events/<slug>.md` document, whose `provenance` references the
    Source, cataloged under `# Events` and passing conformance (spec:
    "Event and Procedure are written and cataloged")."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, _event_reply())
    source = tmp_path / "notes.txt"
    source.write_text(
        "Stoicon 2026 is an annual conference on Stoic philosophy.",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    event_path = tmp_path / "bundle" / "events" / "stoicon-2026.md"
    assert event_path.is_file()
    metadata, body = okf.load_frontmatter(event_path.read_text(encoding="utf-8"))
    assert metadata["type"] == "Event"
    assert metadata["freshness"] == "snapshot"
    assert metadata["provenance"] == ["sources/notes"]
    assert "sources/notes.md" in body
    assert okf.check_conformance(tmp_path / "bundle") == []
    index_text = (tmp_path / "bundle" / "index.md").read_text(encoding="utf-8")
    assert "events/stoicon-2026.md" in index_text
    assert "# Events" in index_text


def test_successful_procedure_extraction_writes_procedures_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A well-formed `Procedure` reply writes the Source AND a
    `bundle/procedures/<slug>.md` document, whose `provenance` references
    the Source, cataloged under `# Procedures` and passing conformance
    (spec: "Event and Procedure are written and cataloged")."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, _procedure_reply())
    source = tmp_path / "notes.txt"
    source.write_text("A daily morning journaling routine.", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    procedure_path = (
        tmp_path / "bundle" / "procedures" / "morning-journaling-routine.md"
    )
    assert procedure_path.is_file()
    metadata, body = okf.load_frontmatter(procedure_path.read_text(encoding="utf-8"))
    assert metadata["type"] == "Procedure"
    assert metadata["freshness"] == "snapshot"
    assert metadata["provenance"] == ["sources/notes"]
    assert "sources/notes.md" in body
    assert okf.check_conformance(tmp_path / "bundle") == []
    index_text = (tmp_path / "bundle" / "index.md").read_text(encoding="utf-8")
    assert "procedures/morning-journaling-routine.md" in index_text
    assert "# Procedures" in index_text


def test_successful_decision_extraction_writes_decisions_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A well-formed `Decision` reply writes the Source AND a
    `bundle/decisions/<slug>.md` document, whose `provenance` references
    the Source, cataloged under `# Decisions` and passing conformance --
    reversing the prior rejection of `Decision` (spec: "Decision is now
    accepted, reversing prior rejection"; "Decision and Project Route to
    Dedicated Catalog Sections")."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, _decision_reply())
    source = tmp_path / "notes.txt"
    source.write_text(
        "We decided to frame the essay around the dichotomy of control.",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    decision_path = (
        tmp_path / "bundle" / "decisions" / "frame-the-essay-around-control.md"
    )
    assert decision_path.is_file()
    metadata, body = okf.load_frontmatter(decision_path.read_text(encoding="utf-8"))
    assert metadata["type"] == "Decision"
    assert metadata["freshness"] == "snapshot"
    assert metadata["provenance"] == ["sources/notes"]
    assert "sources/notes.md" in body
    assert okf.check_conformance(tmp_path / "bundle") == []
    index_text = (tmp_path / "bundle" / "index.md").read_text(encoding="utf-8")
    assert "decisions/frame-the-essay-around-control.md" in index_text
    assert "# Decisions" in index_text


def test_successful_project_extraction_writes_projects_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A well-formed `Project` reply writes the Source AND a
    `bundle/projects/<slug>.md` document, whose `provenance` references the
    Source, cataloged under `# Projects` and passing conformance (spec:
    "Decision and Project Route to Dedicated Catalog Sections")."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, _project_reply())
    source = tmp_path / "notes.txt"
    source.write_text(
        "A multi-month series of essays on Stoic practice.", encoding="utf-8"
    )

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    project_path = tmp_path / "bundle" / "projects" / "stoicism-essay-series.md"
    assert project_path.is_file()
    metadata, body = okf.load_frontmatter(project_path.read_text(encoding="utf-8"))
    assert metadata["type"] == "Project"
    assert metadata["freshness"] == "snapshot"
    assert metadata["provenance"] == ["sources/notes"]
    assert "sources/notes.md" in body
    assert okf.check_conformance(tmp_path / "bundle") == []
    index_text = (tmp_path / "bundle" / "index.md").read_text(encoding="utf-8")
    assert "projects/stoicism-essay-series.md" in index_text
    assert "# Projects" in index_text


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
    """A well-formed reply whose `type` is outside the closed `{Concept,
    Entity, Place, Event, Procedure, Decision, Project, Person,
    Organization}` set degrades to Source-only, with a stderr note and exit
    0 (scenario: type outside the vocabulary degrades to Source-only).
    `"Animal"` is a genuinely invalid sentinel."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(
        monkeypatch,
        json.dumps(
            {
                "extract": True,
                "type": "Animal",
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
    assert not (tmp_path / "bundle" / "places").exists()
    assert not (tmp_path / "bundle" / "events").exists()
    assert not (tmp_path / "bundle" / "procedures").exists()
    assert not (tmp_path / "bundle" / "decisions").exists()
    assert not (tmp_path / "bundle" / "projects").exists()
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
    entry -- the same per-slug `derived_path.exists()` reconciliation (design
    D5) `_stage_derived_objects` applies to `concepts/`/`entities/` covers
    `people/` too (spec: Re-ingesting a Person source does not
    duplicate)."""
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
    catalog entry -- the same per-slug `derived_path.exists()`
    reconciliation (design D5) `_stage_derived_objects` applies to
    `concepts/`/`entities/`/`people/` covers `organizations/` too (spec:
    Re-ingesting an Organization source does not duplicate)."""
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


def test_idempotent_reingest_leaves_existing_place_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Re-ingesting a source whose Place derived object already exists
    leaves that file byte-unchanged and does not duplicate the catalog
    entry -- the same per-slug `derived_path.exists()` reconciliation
    (design D5) `_stage_derived_objects` applies to
    `concepts/`/`entities/`/`people/`/`organizations/` covers `places/` too
    (spec: "Re-ingesting a Place source does not duplicate")."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, _place_reply())
    source = tmp_path / "notes.txt"
    source.write_text(
        "Yellowstone is a national park known for its geysers.", encoding="utf-8"
    )

    first = runner.invoke(app, ["ingest", "notes.txt", "--auto"])
    assert first.exit_code == 0
    place_path = tmp_path / "bundle" / "places" / "yellowstone-national-park.md"
    assert place_path.is_file()
    hand_edited = place_path.read_text(encoding="utf-8") + "\n<!-- hand edit -->\n"
    place_path.write_text(hand_edited, encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    assert place_path.read_text(encoding="utf-8") == hand_edited
    index_text = (tmp_path / "bundle" / "index.md").read_text(encoding="utf-8")
    assert index_text.count("places/yellowstone-national-park.md") == 1
    log_text = (tmp_path / "bundle" / "log.md").read_text(encoding="utf-8")
    assert log_text.count("yellowstone-national-park.md") == 1


def test_idempotent_reingest_leaves_existing_event_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Re-ingesting a source whose Event derived object already exists
    leaves that file byte-unchanged and does not duplicate the catalog
    entry -- the same per-slug `derived_path.exists()` reconciliation
    (design D5) `_stage_derived_objects` applies to the other classifiable
    types covers `events/` too (spec: "Re-ingesting an Event source does
    not duplicate")."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, _event_reply())
    source = tmp_path / "notes.txt"
    source.write_text(
        "Stoicon 2026 is an annual conference on Stoic philosophy.",
        encoding="utf-8",
    )

    first = runner.invoke(app, ["ingest", "notes.txt", "--auto"])
    assert first.exit_code == 0
    event_path = tmp_path / "bundle" / "events" / "stoicon-2026.md"
    assert event_path.is_file()
    hand_edited = event_path.read_text(encoding="utf-8") + "\n<!-- hand edit -->\n"
    event_path.write_text(hand_edited, encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    assert event_path.read_text(encoding="utf-8") == hand_edited
    index_text = (tmp_path / "bundle" / "index.md").read_text(encoding="utf-8")
    assert index_text.count("events/stoicon-2026.md") == 1
    log_text = (tmp_path / "bundle" / "log.md").read_text(encoding="utf-8")
    assert log_text.count("stoicon-2026.md") == 1


def test_idempotent_reingest_leaves_existing_procedure_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Re-ingesting a source whose Procedure derived object already exists
    leaves that file byte-unchanged and does not duplicate the catalog
    entry -- the idempotency scan must cover `procedures/` (spec:
    "Re-ingesting a Procedure source does not duplicate")."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, _procedure_reply())
    source = tmp_path / "notes.txt"
    source.write_text("A daily morning journaling routine.", encoding="utf-8")

    first = runner.invoke(app, ["ingest", "notes.txt", "--auto"])
    assert first.exit_code == 0
    procedure_path = (
        tmp_path / "bundle" / "procedures" / "morning-journaling-routine.md"
    )
    assert procedure_path.is_file()
    hand_edited = procedure_path.read_text(encoding="utf-8") + "\n<!-- hand edit -->\n"
    procedure_path.write_text(hand_edited, encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    assert procedure_path.read_text(encoding="utf-8") == hand_edited
    index_text = (tmp_path / "bundle" / "index.md").read_text(encoding="utf-8")
    assert index_text.count("procedures/morning-journaling-routine.md") == 1
    log_text = (tmp_path / "bundle" / "log.md").read_text(encoding="utf-8")
    assert log_text.count("morning-journaling-routine.md") == 1


def test_idempotent_reingest_leaves_existing_decision_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Re-ingesting a source whose Decision derived object already exists
    leaves that file byte-unchanged and does not duplicate the catalog
    entry -- the same per-slug `derived_path.exists()` reconciliation
    (design D5) `_stage_derived_objects` applies to the other classifiable
    types covers `decisions/` too (spec: "Decision and Project Route to
    Dedicated Catalog Sections")."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, _decision_reply())
    source = tmp_path / "notes.txt"
    source.write_text(
        "We decided to frame the essay around the dichotomy of control.",
        encoding="utf-8",
    )

    first = runner.invoke(app, ["ingest", "notes.txt", "--auto"])
    assert first.exit_code == 0
    decision_path = (
        tmp_path / "bundle" / "decisions" / "frame-the-essay-around-control.md"
    )
    assert decision_path.is_file()
    hand_edited = decision_path.read_text(encoding="utf-8") + "\n<!-- hand edit -->\n"
    decision_path.write_text(hand_edited, encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    assert decision_path.read_text(encoding="utf-8") == hand_edited
    index_text = (tmp_path / "bundle" / "index.md").read_text(encoding="utf-8")
    assert index_text.count("decisions/frame-the-essay-around-control.md") == 1
    log_text = (tmp_path / "bundle" / "log.md").read_text(encoding="utf-8")
    assert log_text.count("frame-the-essay-around-control.md") == 1


def test_idempotent_reingest_leaves_existing_project_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Re-ingesting a source whose Project derived object already exists
    leaves that file byte-unchanged and does not duplicate the catalog
    entry -- the idempotency scan must cover `projects/` (spec: "Decision
    and Project Route to Dedicated Catalog Sections")."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, _project_reply())
    source = tmp_path / "notes.txt"
    source.write_text(
        "A multi-month series of essays on Stoic practice.", encoding="utf-8"
    )

    first = runner.invoke(app, ["ingest", "notes.txt", "--auto"])
    assert first.exit_code == 0
    project_path = tmp_path / "bundle" / "projects" / "stoicism-essay-series.md"
    assert project_path.is_file()
    hand_edited = project_path.read_text(encoding="utf-8") + "\n<!-- hand edit -->\n"
    project_path.write_text(hand_edited, encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    assert project_path.read_text(encoding="utf-8") == hand_edited
    index_text = (tmp_path / "bundle" / "index.md").read_text(encoding="utf-8")
    assert index_text.count("projects/stoicism-essay-series.md") == 1
    log_text = (tmp_path / "bundle" / "log.md").read_text(encoding="utf-8")
    assert log_text.count("stoicism-essay-series.md") == 1


def test_reingest_with_nondeterministic_llm_title_inserts_a_new_distinct_object(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reversal of the old all-or-nothing `_source_has_derived_object` gate
    (Phase 7, design D5): re-ingest reconciliation is now SLUG-LEVEL, not
    provenance-level. A re-ingest whose (nondeterministic) LLM reply
    proposes a DIFFERENT title calls the LLM again -- unlike the retired
    provenance-keyed skip, which never called it -- and, since the new
    title slugifies to a slug that does not yet exist for this source,
    INSERTS a second, distinct derived object; the pre-existing one is left
    byte-unchanged. This is the accepted cost of the D5 tradeoff, not a
    bug."""
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
    first_content = first_concept_path.read_text(encoding="utf-8")

    fake = _patch_llm(monkeypatch, _concept_reply(title="A Completely Different Title"))
    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    assert len(fake.calls) == 1
    assert first_concept_path.read_text(encoding="utf-8") == first_content
    second_concept_path = (
        tmp_path / "bundle" / "concepts" / "a-completely-different-title.md"
    )
    assert second_concept_path.is_file()
    index_text = (tmp_path / "bundle" / "index.md").read_text(encoding="utf-8")
    assert index_text.count("concepts/stoic-dichotomy-of-control.md") == 1
    assert "concepts/a-completely-different-title.md" in index_text
    log_text = (tmp_path / "bundle" / "log.md").read_text(encoding="utf-8")
    assert "a-completely-different-title.md" in log_text
    assert okf.check_conformance(tmp_path / "bundle") == []


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
    """Same-value baseline: the derived object's `sensitivity` equals the
    Source's configured `default_sensitivity` (scenario: provenance and
    sensitivity inherited). Uses `public`, not `confidential` -- since
    sensitivity-fail-closed-filter S3b, a `confidential` floor short-circuits
    extraction entirely (see
    `test_confidential_default_sensitivity_floor_skips_extraction`), so
    `public` is the non-default value that still proves genuine inheritance
    rather than merely matching the packaged default.

    This test ALONE no longer proves real inheritance: the Source and its
    derived object both receive the SAME `cfg.default_sensitivity` constant
    here, so it would pass identically whether the derived object's value
    is genuinely read back from the built Source document or merely shares
    the same config constant by coincidence. See
    `test_derived_object_inherits_source_document_value_not_config` below
    for the test that distinguishes the two."""
    _init_workspace(tmp_path, monkeypatch)
    _set_config_field(
        tmp_path, "default_sensitivity: private", "default_sensitivity: public"
    )
    _patch_llm(monkeypatch, _concept_reply())
    source = tmp_path / "notes.txt"
    source.write_text("Some raw notes about self-control.", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    concept_path = tmp_path / "bundle" / "concepts" / "stoic-dichotomy-of-control.md"
    metadata, _ = okf.load_frontmatter(concept_path.read_text(encoding="utf-8"))
    assert metadata["sensitivity"] == "public"


def test_derived_object_inherits_source_document_value_not_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The derived object's `sensitivity` MUST be read back from the built
    Source document's own resolved value, not merely share
    `cfg.default_sensitivity` with it. The Source's built content is forged
    (via `okf.build_source_concept`) to carry `confidential` while the
    config default stays `public` -- an implementation that stamps derived
    objects from the config constant instead of the Source's own value gets
    `public` here and fails (spec: ingestion, "Inheritance tracks the
    Source's resolved value, not the config default")."""
    _init_workspace(tmp_path, monkeypatch)
    _set_config_field(
        tmp_path, "default_sensitivity: private", "default_sensitivity: public"
    )
    _patch_llm(monkeypatch, _concept_reply())

    real_build_source_concept = okf.build_source_concept

    def _forged_build_source_concept(**kwargs: object) -> str:
        kwargs["sensitivity"] = "confidential"
        return real_build_source_concept(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(
        "openkos.cli.main.okf.build_source_concept", _forged_build_source_concept
    )
    source = tmp_path / "notes.txt"
    source.write_text("Some raw notes about self-control.", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    source_path = tmp_path / "bundle" / "sources" / "notes.md"
    source_metadata, _ = okf.load_frontmatter(source_path.read_text(encoding="utf-8"))
    assert source_metadata["sensitivity"] == "confidential"
    concept_path = tmp_path / "bundle" / "concepts" / "stoic-dichotomy-of-control.md"
    metadata, _ = okf.load_frontmatter(concept_path.read_text(encoding="utf-8"))
    assert metadata["sensitivity"] == "confidential"


def test_extract_gate_still_reads_workspace_floor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reading the Source's own resolved `sensitivity` back for the derived-
    object STAMP must not change what the fail-closed `extract` gate reads:
    the gate stays pinned to the WORKSPACE floor (`cfg.default_sensitivity`),
    never the Source's own resolved value, even when the two differ
    (`sensitivity-aware-llm` Requirement 4, declared unchanged by this
    change). The Source's built content is forged to `public` while the
    config floor stays `confidential` -- an implementation that (incorrectly)
    fed the Source's own value into the `extract` gate instead of the
    workspace floor would let extraction proceed here and fail this test."""
    _init_workspace(tmp_path, monkeypatch)
    _set_config_field(
        tmp_path, "default_sensitivity: private", "default_sensitivity: confidential"
    )
    fake = _patch_llm(monkeypatch, _concept_reply())

    real_build_source_concept = okf.build_source_concept

    def _forged_build_source_concept(**kwargs: object) -> str:
        kwargs["sensitivity"] = "public"
        return real_build_source_concept(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(
        "openkos.cli.main.okf.build_source_concept", _forged_build_source_concept
    )
    source = tmp_path / "notes.txt"
    source.write_text("Some raw notes about self-control.", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    assert "keeping the Source only" in result.stderr
    assert fake.calls == []
    concept_path = tmp_path / "bundle" / "concepts" / "stoic-dichotomy-of-control.md"
    assert not concept_path.exists()
    source_path = tmp_path / "bundle" / "sources" / "notes.md"
    source_metadata, _ = okf.load_frontmatter(source_path.read_text(encoding="utf-8"))
    assert source_metadata["sensitivity"] == "public"


# --- issue #229: re-ingest must not lower a Source's sensitivity ---------


def _set_source_sensitivity(tmp_path: Path, slug: str, value: object) -> None:
    """Directly rewrite an existing Source concept's on-disk `sensitivity`
    frontmatter field to `value`, bypassing `set-sensitivity`'s CLI/gate
    machinery (git identity, autocommit, downgrade prompt) so these tests
    exercise `ingest`'s own resolution logic in isolation. `value` may be
    non-canonical (blank, unrecognized, non-string) to exercise fail-closed
    ranking."""
    concept_path = tmp_path / "bundle" / "sources" / f"{slug}.md"
    text = concept_path.read_text(encoding="utf-8")
    metadata, body = okf.load_frontmatter(text)
    metadata["sensitivity"] = value
    concept_path.write_text(okf.dump_frontmatter(metadata, body), encoding="utf-8")


def test_reingest_does_not_downgrade_the_source_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The half issue #229 did not describe: a Source raised to
    `confidential` on disk, with `default_sensitivity: private` in config,
    stays `confidential` after a `regenerate=True` re-ingest.
    `write_atomic(concept_path, concept_content)` (`main.py:1794`) used to
    overwrite the on-disk Source with a freshly built document stamped
    `cfg.default_sensitivity`, silently declassifying it -- with no
    `--allow-downgrade` and no prompt, routing around ADR-0008's gate
    (design: "Resolve before build, not merge after build")."""
    _init_workspace(tmp_path, monkeypatch)
    source = tmp_path / "notes.txt"
    source.write_text("Some raw notes about self-control.", encoding="utf-8")
    first = runner.invoke(app, ["ingest", "notes.txt", "--auto"])
    assert first.exit_code == 0
    _set_source_sensitivity(tmp_path, "notes", "confidential")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    concept_path = tmp_path / "bundle" / "sources" / "notes.md"
    metadata, _ = okf.load_frontmatter(concept_path.read_text(encoding="utf-8"))
    assert metadata["sensitivity"] == "confidential"


def test_reingest_stamps_new_derived_objects_with_the_preserved_level(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A derived object newly extracted on the SAME re-ingest that
    preserves a raised Source sensitivity is stamped with that preserved
    level, not the (lower) config default (design: "one resolved value
    flows through every downstream consumer unchanged")."""
    _init_workspace(tmp_path, monkeypatch)
    source = tmp_path / "notes.txt"
    source.write_text("Some raw notes about self-control.", encoding="utf-8")
    first = runner.invoke(app, ["ingest", "notes.txt", "--auto"])
    assert first.exit_code == 0
    _set_source_sensitivity(tmp_path, "notes", "confidential")
    _patch_llm(monkeypatch, _concept_reply())

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    concept_path = tmp_path / "bundle" / "concepts" / "stoic-dichotomy-of-control.md"
    metadata, _ = okf.load_frontmatter(concept_path.read_text(encoding="utf-8"))
    assert metadata["sensitivity"] == "confidential"


def test_reingest_raises_when_workspace_default_exceeds_on_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A raised `default_sensitivity` still raises a Source on re-ingest
    when it exceeds the on-disk value -- the high-water-mark, not a frozen
    read-and-reuse (design: "(b) dominates (a)")."""
    _init_workspace(tmp_path, monkeypatch)
    _set_config_field(
        tmp_path, "default_sensitivity: private", "default_sensitivity: public"
    )
    source = tmp_path / "notes.txt"
    source.write_text("Some raw notes about self-control.", encoding="utf-8")
    first = runner.invoke(app, ["ingest", "notes.txt", "--auto"])
    assert first.exit_code == 0
    concept_path = tmp_path / "bundle" / "sources" / "notes.md"
    metadata, _ = okf.load_frontmatter(concept_path.read_text(encoding="utf-8"))
    assert metadata["sensitivity"] == "public"
    _set_config_field(
        tmp_path, "default_sensitivity: public", "default_sensitivity: private"
    )

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    metadata, _ = okf.load_frontmatter(concept_path.read_text(encoding="utf-8"))
    assert metadata["sensitivity"] == "private"


def test_reingest_still_refreshes_timestamp_and_description(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only `sensitivity` carries across a re-ingest's merge; `timestamp`
    keeps refreshing to the current build's clock value exactly as before
    this change -- a merge into the freshly built metadata, never a
    restore of the prior document (design: "Refresh semantics")."""
    _init_workspace(tmp_path, monkeypatch)
    source = tmp_path / "notes.txt"
    source.write_text("Some raw notes about self-control.", encoding="utf-8")
    first = runner.invoke(app, ["ingest", "notes.txt", "--auto"])
    assert first.exit_code == 0
    concept_path = tmp_path / "bundle" / "sources" / "notes.md"
    first_metadata, _ = okf.load_frontmatter(concept_path.read_text(encoding="utf-8"))
    first_timestamp = first_metadata["timestamp"]
    _set_source_sensitivity(tmp_path, "notes", "confidential")

    class _FixedClock:
        @staticmethod
        def now(tz: object = None) -> datetime:
            return datetime(2099, 1, 1, tzinfo=UTC)

    monkeypatch.setattr("openkos.cli.main.datetime", _FixedClock)

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    metadata, _ = okf.load_frontmatter(concept_path.read_text(encoding="utf-8"))
    assert metadata["sensitivity"] == "confidential"
    assert metadata["timestamp"] != first_timestamp
    assert metadata["timestamp"] == "2099-01-01T00:00:00Z"


def test_reingest_with_equal_values_writes_byte_identical_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the on-disk `sensitivity` already equals `cfg.default_sensitivity`
    the resolved value is unchanged, and every OTHER line of the rewritten
    Source is unchanged too except the always-refreshing `timestamp` --
    byte-identical to the pre-existing regenerate behavior for the
    `sensitivity` field (spec: "Re-ingest with equal values is
    byte-identical to today")."""
    _init_workspace(tmp_path, monkeypatch)
    source = tmp_path / "notes.txt"
    source.write_text("Some raw notes about self-control.", encoding="utf-8")
    first = runner.invoke(app, ["ingest", "notes.txt", "--auto"])
    assert first.exit_code == 0
    concept_path = tmp_path / "bundle" / "sources" / "notes.md"
    before = concept_path.read_text(encoding="utf-8")

    class _FixedClock:
        @staticmethod
        def now(tz: object = None) -> datetime:
            return datetime(2099, 1, 1, tzinfo=UTC)

    monkeypatch.setattr("openkos.cli.main.datetime", _FixedClock)

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    after = concept_path.read_text(encoding="utf-8")
    metadata, _ = okf.load_frontmatter(after)
    assert metadata["sensitivity"] == "private"
    before_lines = before.splitlines()
    after_lines = after.splitlines()
    changed = [(b, a) for b, a in zip(before_lines, after_lines, strict=True) if b != a]
    assert changed
    assert all("timestamp" in b for b, _ in changed)


def test_reingest_leaves_existing_derived_objects_byte_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pre-existing derived object's file, including its own `sensitivity`
    field, is left byte-unchanged by a re-ingest that raises the Source's
    resolved sensitivity -- create-only stays create-only, out of scope for
    this change (spec: "Existing derived objects are untouched by
    re-ingest regardless of resolved level")."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, _concept_reply())
    source = tmp_path / "notes.txt"
    source.write_text("Some raw notes about self-control.", encoding="utf-8")
    first = runner.invoke(app, ["ingest", "notes.txt", "--auto"])
    assert first.exit_code == 0
    concept_path = tmp_path / "bundle" / "concepts" / "stoic-dichotomy-of-control.md"
    original = concept_path.read_text(encoding="utf-8")
    _set_source_sensitivity(tmp_path, "notes", "confidential")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    assert concept_path.read_text(encoding="utf-8") == original


def test_reingest_after_forget_uses_the_config_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Concept absent (post-`forget`, `main.py:1791-1793`) on the
    regenerate path resolves directly to `cfg.default_sensitivity` --
    feeding `None` into `combine_sensitivity` would wrongly rank as
    `private` (`okf._rank(None)`), silently raising a `public` workspace
    above its own config default (design: "Do not pass `None` into
    `combine_sensitivity`")."""
    _init_workspace(tmp_path, monkeypatch)
    _set_config_field(
        tmp_path, "default_sensitivity: private", "default_sensitivity: public"
    )
    source = tmp_path / "notes.txt"
    source.write_text("Some raw notes about self-control.", encoding="utf-8")
    first = runner.invoke(app, ["ingest", "notes.txt", "--auto"])
    assert first.exit_code == 0
    forgotten = runner.invoke(app, ["forget", "sources/notes", "--auto"])
    assert forgotten.exit_code == 0
    concept_path = tmp_path / "bundle" / "sources" / "notes.md"
    assert not concept_path.exists()

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    metadata, _ = okf.load_frontmatter(concept_path.read_text(encoding="utf-8"))
    assert metadata["sensitivity"] == "public"


def test_reingest_with_unparseable_source_frontmatter_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An existing Source whose on-disk frontmatter fails to parse (YAML
    syntax error, not `OSError`/`ValueError`) aborts the re-ingest with
    exit 1 and leaves the on-disk bytes unchanged -- degrading to the
    config default would silently write a LOWER level over an unreadable
    classification, the exact declassification this change removes
    (design: "Abort, exit 1")."""
    _init_workspace(tmp_path, monkeypatch)
    source = tmp_path / "notes.txt"
    source.write_text("Some raw notes about self-control.", encoding="utf-8")
    first = runner.invoke(app, ["ingest", "notes.txt", "--auto"])
    assert first.exit_code == 0
    concept_path = tmp_path / "bundle" / "sources" / "notes.md"
    before = concept_path.read_bytes()
    concept_path.write_text(
        "---\nsensitivity: [unterminated\n---\nbroken body\n", encoding="utf-8"
    )
    corrupted = concept_path.read_bytes()

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 1
    assert "refusing to ingest" in result.stderr
    assert concept_path.read_bytes() == corrupted
    assert concept_path.read_bytes() != before


def test_reingest_with_unknown_on_disk_sensitivity_fails_closed_to_confidential(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unrecognized on-disk `sensitivity` string (`"secret"`) ranks
    `confidential` under `okf._rank`'s fail-closed fallback, and THAT
    resolved value is what gets written and staged (spec: "Malformed
    on-disk sensitivity fails closed to confidential")."""
    _init_workspace(tmp_path, monkeypatch)
    source = tmp_path / "notes.txt"
    source.write_text("Some raw notes about self-control.", encoding="utf-8")
    first = runner.invoke(app, ["ingest", "notes.txt", "--auto"])
    assert first.exit_code == 0
    _set_source_sensitivity(tmp_path, "notes", "secret")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    concept_path = tmp_path / "bundle" / "sources" / "notes.md"
    metadata, _ = okf.load_frontmatter(concept_path.read_text(encoding="utf-8"))
    assert metadata["sensitivity"] == "confidential"


def _delete_source_sensitivity(tmp_path: Path, slug: str) -> None:
    """Directly rewrite an existing Source concept's frontmatter to drop the
    `sensitivity` key entirely, simulating a genuinely MISSING key -- as
    opposed to `_set_source_sensitivity`'s non-canonical VALUES -- so this
    exercises `okf._rank`'s `None`-input branch (floors at `private`), not
    its unrecognized-string-or-non-string branch (fails closed to
    `confidential`)."""
    concept_path = tmp_path / "bundle" / "sources" / f"{slug}.md"
    text = concept_path.read_text(encoding="utf-8")
    metadata, body = okf.load_frontmatter(text)
    del metadata["sensitivity"]
    concept_path.write_text(okf.dump_frontmatter(metadata, body), encoding="utf-8")


def test_reingest_with_missing_on_disk_sensitivity_resolves_to_private(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Source whose on-disk `sensitivity` key is missing entirely -- not
    merely unrecognized -- ranks `private` under `okf._rank`'s `None`-input
    handling (the config default floor), NOT `confidential`. The spec used
    to lump `missing, non-string, or otherwise unrecognized` together as all
    failing closed to `confidential`; `_rank(None)` (`okf.py`) actually
    floors at `private` (spec: "Missing on-disk sensitivity floors to
    private").

    Config `default_sensitivity` is raised to `public` specifically so this
    test discriminates: a broken implementation that ignored the on-disk
    value entirely and just wrote `cfg.default_sensitivity` would produce
    `public` here, not `private` -- only a real
    `combine_sensitivity(None, "public")` call floors the result at
    `private` (mirrors `test_reingest_after_forget_uses_the_config_default`'s
    technique)."""
    _init_workspace(tmp_path, monkeypatch)
    _set_config_field(
        tmp_path, "default_sensitivity: private", "default_sensitivity: public"
    )
    source = tmp_path / "notes.txt"
    source.write_text("Some raw notes about self-control.", encoding="utf-8")
    first = runner.invoke(app, ["ingest", "notes.txt", "--auto"])
    assert first.exit_code == 0
    _delete_source_sensitivity(tmp_path, "notes")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    concept_path = tmp_path / "bundle" / "sources" / "notes.md"
    metadata, _ = okf.load_frontmatter(concept_path.read_text(encoding="utf-8"))
    assert metadata["sensitivity"] == "private"


def test_reingest_with_blank_on_disk_sensitivity_resolves_to_private(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Source whose on-disk `sensitivity` value is present but
    whitespace-only ranks `private` under `okf._rank`'s blank-string
    handling (the config default floor), NOT `confidential` -- distinct
    from both a missing key and an unrecognized string (spec: "Blank
    on-disk sensitivity floors to private").

    Config `default_sensitivity` is raised to `public` for the same reason
    as `test_reingest_with_missing_on_disk_sensitivity_resolves_to_private`:
    a broken implementation that ignored the on-disk value entirely and
    just wrote `cfg.default_sensitivity` would produce `public` here, not
    `private` -- only a real `combine_sensitivity("   ", "public")` call
    floors the result at `private`."""
    _init_workspace(tmp_path, monkeypatch)
    _set_config_field(
        tmp_path, "default_sensitivity: private", "default_sensitivity: public"
    )
    source = tmp_path / "notes.txt"
    source.write_text("Some raw notes about self-control.", encoding="utf-8")
    first = runner.invoke(app, ["ingest", "notes.txt", "--auto"])
    assert first.exit_code == 0
    _set_source_sensitivity(tmp_path, "notes", "   ")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    concept_path = tmp_path / "bundle" / "sources" / "notes.md"
    metadata, _ = okf.load_frontmatter(concept_path.read_text(encoding="utf-8"))
    assert metadata["sensitivity"] == "private"


def test_reingest_resolved_sensitivity_does_not_leak_into_workspace_floor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Invariant guard (design "Testing Strategy" #11): re-ingest with a
    Source raised to `confidential` on disk, config `default_sensitivity:
    public`, and a NEW-slug LLM reply must still call the LLM and write the
    new derived object -- `blocks_llm_send` gates on the LITERAL
    `cfg.default_sensitivity` (`public`), never the resolved value. Feeding
    `resolved` into `workspace_floor` would short-circuit extraction here
    and fail this test. Complements
    `test_extract_gate_still_reads_workspace_floor` (above), which must
    pass unmodified."""
    _init_workspace(tmp_path, monkeypatch)
    _set_config_field(
        tmp_path, "default_sensitivity: private", "default_sensitivity: public"
    )
    source = tmp_path / "notes.txt"
    source.write_text("Some raw notes about self-control.", encoding="utf-8")
    first = runner.invoke(app, ["ingest", "notes.txt", "--auto"])
    assert first.exit_code == 0
    _set_source_sensitivity(tmp_path, "notes", "confidential")
    fake = _patch_llm(monkeypatch, _concept_reply())

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    assert fake.calls != []
    concept_path = tmp_path / "bundle" / "concepts" / "stoic-dichotomy-of-control.md"
    assert concept_path.is_file()
    metadata, _ = okf.load_frontmatter(concept_path.read_text(encoding="utf-8"))
    assert metadata["sensitivity"] == "confidential"
    assert "workspace default_sensitivity floor is confidential" not in result.stderr


def test_reingest_preview_reports_preserved_level(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the on-disk value exceeds the config default, the re-ingest
    preview names the resolved level with the "preserved from the existing
    Source" clause -- the preserved level is reported, never presented
    silently as the config default (spec: "Preview reports a preserved
    level")."""
    _init_workspace(tmp_path, monkeypatch)
    source = tmp_path / "notes.txt"
    source.write_text("Some raw notes about self-control.", encoding="utf-8")
    first = runner.invoke(app, ["ingest", "notes.txt", "--auto"])
    assert first.exit_code == 0
    _set_source_sensitivity(tmp_path, "notes", "confidential")
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["ingest", "notes.txt"], input="y\n")

    assert result.exit_code == 0
    assert (
        "~ bundle/sources/notes.md (regenerated -- sensitivity confidential "
        "preserved from the existing Source)" in result.stdout
    )


def test_reingest_preview_reports_raised_level(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the config default exceeds the on-disk value, the re-ingest
    preview names the resolved level with the "raised by the workspace
    default" clause (spec: "Preview reports a raised level")."""
    _init_workspace(tmp_path, monkeypatch)
    _set_config_field(
        tmp_path, "default_sensitivity: private", "default_sensitivity: public"
    )
    source = tmp_path / "notes.txt"
    source.write_text("Some raw notes about self-control.", encoding="utf-8")
    first = runner.invoke(app, ["ingest", "notes.txt", "--auto"])
    assert first.exit_code == 0
    _set_config_field(
        tmp_path, "default_sensitivity: public", "default_sensitivity: private"
    )
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["ingest", "notes.txt"], input="y\n")

    assert result.exit_code == 0
    assert (
        "~ bundle/sources/notes.md (regenerated -- sensitivity private "
        "raised by the workspace default)" in result.stdout
    )


def test_reingest_preview_reports_unchanged_level(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the on-disk value already equals the config default, the
    re-ingest preview names the resolved level with the "unchanged" clause
    (spec: "Preview reports an unchanged level")."""
    _init_workspace(tmp_path, monkeypatch)
    source = tmp_path / "notes.txt"
    source.write_text("Some raw notes about self-control.", encoding="utf-8")
    first = runner.invoke(app, ["ingest", "notes.txt", "--auto"])
    assert first.exit_code == 0
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["ingest", "notes.txt"], input="y\n")

    assert result.exit_code == 0
    assert (
        "~ bundle/sources/notes.md (regenerated -- sensitivity private "
        "unchanged)" in result.stdout
    )


def test_reingest_after_forget_preview_reports_workspace_default_clause(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The post-`forget` regenerate path -- raw copy reused, but no prior
    Source to read (`had_prior_source is False`) -- reports the resolved
    level with the "from the workspace default" clause, distinct from the
    on-disk-Source clauses ("preserved from the existing Source" / "raised
    by the workspace default" / "unchanged"). Implemented at `main.py`'s
    `else: sensitivity_clause = "from the workspace default"` branch, but
    pinned by no test before this one."""
    _init_workspace(tmp_path, monkeypatch)
    source = tmp_path / "notes.txt"
    source.write_text("Some raw notes about self-control.", encoding="utf-8")
    first = runner.invoke(app, ["ingest", "notes.txt", "--auto"])
    assert first.exit_code == 0
    forgotten = runner.invoke(app, ["forget", "sources/notes", "--auto"])
    assert forgotten.exit_code == 0
    concept_path = tmp_path / "bundle" / "sources" / "notes.md"
    assert not concept_path.exists()
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["ingest", "notes.txt"], input="y\n")

    assert result.exit_code == 0
    assert (
        "~ bundle/sources/notes.md (regenerated -- sensitivity private "
        "from the workspace default)" in result.stdout
    )


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


# --- Multi-object extraction (PR 2, Phases 7-14) ----------------------------


def test_multi_object_extraction_writes_all_valid_objects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A single ingest whose extraction reply contains 3 distinct, valid
    objects writes the Source AND all 3 derived documents, each cataloged
    and logged, all passing conformance (Phase 8: core N-object end-to-end
    scenario; spec: "Multiple distinct objects extracted, under cap")."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(
        monkeypatch,
        _multi_object_reply(_concept_reply(), _person_reply(), _organization_reply()),
    )
    source = tmp_path / "notes.txt"
    source.write_text(
        "Epictetus, at the Praxis Foundation, taught the dichotomy of control.",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    concept_path = tmp_path / "bundle" / "concepts" / "stoic-dichotomy-of-control.md"
    person_path = tmp_path / "bundle" / "people" / "epictetus.md"
    org_path = tmp_path / "bundle" / "organizations" / "praxis-foundation.md"
    assert concept_path.is_file()
    assert person_path.is_file()
    assert org_path.is_file()
    assert okf.check_conformance(tmp_path / "bundle") == []
    index_text = (tmp_path / "bundle" / "index.md").read_text(encoding="utf-8")
    assert "concepts/stoic-dichotomy-of-control.md" in index_text
    assert "people/epictetus.md" in index_text
    assert "organizations/praxis-foundation.md" in index_text
    log_text = (tmp_path / "bundle" / "log.md").read_text(encoding="utf-8")
    assert log_text.count("Extracted") == 3
    assert "bundle/concepts/stoic-dichotomy-of-control.md" in result.stdout
    assert "bundle/people/epictetus.md" in result.stdout
    assert "bundle/organizations/praxis-foundation.md" in result.stdout


def test_empty_extraction_array_degrades_to_source_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An extraction reply that is a well-formed but EMPTY JSON array
    degrades to Source-only, same as a decline/malformed reply (Phase 8:
    `[]` is a valid, distinct "nothing worth extracting" contract)."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, "[]")
    source = tmp_path / "notes.txt"
    source.write_text("content", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    assert not (tmp_path / "bundle" / "concepts").exists()
    assert "no concept extracted" in result.stderr
    assert (tmp_path / "bundle" / "sources" / "notes.md").is_file()


def test_in_batch_slug_collision_keeps_first_drops_second(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two candidates in the SAME extraction reply that slugify to the same
    slug: only the first (in reply order) is staged and written; the
    second is dropped with a stderr note, never written (Phase 9; spec:
    In-Batch Slug-Collision Guard)."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(
        monkeypatch,
        _multi_object_reply(
            _concept_reply(title="Stoic Practice"),
            _entity_reply(title="Stoic Practice"),
        ),
    )
    source = tmp_path / "notes.txt"
    source.write_text("Notes about Stoic practice.", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    concept_path = tmp_path / "bundle" / "concepts" / "stoic-practice.md"
    assert concept_path.is_file()
    metadata, _ = okf.load_frontmatter(concept_path.read_text(encoding="utf-8"))
    assert metadata["type"] == "Concept"
    assert not (tmp_path / "bundle" / "entities").exists()
    assert "duplicate slug" in result.stderr
    assert okf.check_conformance(tmp_path / "bundle") == []


def test_in_batch_collision_guard_does_not_reserve_slug_before_candidate_lands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The in-batch collision guard must reserve a slug only once the
    candidate that owns it actually becomes a plan: a FIRST candidate that
    shares a slug with a SECOND, valid candidate, but itself fails
    `okf.build_concept` (never staged, never written), must NOT block the
    second, valid candidate from being written -- regression for a bug
    where `seen_slugs.add()` ran before the exists/build checks, so an
    earlier candidate that never wrote anything could still falsely shadow
    a later, valid same-slug candidate."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(
        monkeypatch,
        _multi_object_reply(
            _concept_reply(title="Test Object\nExtra"),
            _entity_reply(title="Test Object Extra"),
        ),
    )
    source = tmp_path / "notes.txt"
    source.write_text("content", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    assert not (tmp_path / "bundle" / "concepts").exists()
    entity_path = tmp_path / "bundle" / "entities" / "test-object-extra.md"
    assert entity_path.is_file()
    assert "extracted content failed validation" in result.stderr
    assert "duplicate slug" not in result.stderr
    assert okf.check_conformance(tmp_path / "bundle") == []


def test_empty_slug_item_skipped_other_items_still_staged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A candidate whose title slugifies to an empty string is skipped with
    a stderr note; other valid candidates in the same batch are still
    staged and written -- a per-item fail-closed drop, not a whole-batch
    degrade (Phase 9.2)."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(
        monkeypatch,
        _multi_object_reply(_concept_reply(title="!!!"), _person_reply()),
    )
    source = tmp_path / "notes.txt"
    source.write_text("content", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    assert not (tmp_path / "bundle" / "concepts").exists()
    person_path = tmp_path / "bundle" / "people" / "epictetus.md"
    assert person_path.is_file()
    assert "could not be turned into a slug" in result.stderr
    assert okf.check_conformance(tmp_path / "bundle") == []


def test_reingest_reconciles_per_slug_skips_existing_inserts_new(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Re-ingest whose extraction yields the pre-existing derived object's
    slug PLUS one new distinct slug inserts only the new one; the existing
    slug's file is left byte-unchanged (Phase 10.1; spec: "Re-ingest with
    one new and one existing object")."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, _concept_reply())
    source = tmp_path / "notes.txt"
    source.write_text("Some raw notes about self-control.", encoding="utf-8")
    first = runner.invoke(app, ["ingest", "notes.txt", "--auto"])
    assert first.exit_code == 0
    concept_path = tmp_path / "bundle" / "concepts" / "stoic-dichotomy-of-control.md"
    hand_edited = concept_path.read_text(encoding="utf-8") + "\n<!-- hand edit -->\n"
    concept_path.write_text(hand_edited, encoding="utf-8")

    _patch_llm(monkeypatch, _multi_object_reply(_concept_reply(), _person_reply()))
    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    assert concept_path.read_text(encoding="utf-8") == hand_edited
    person_path = tmp_path / "bundle" / "people" / "epictetus.md"
    assert person_path.is_file()
    index_text = (tmp_path / "bundle" / "index.md").read_text(encoding="utf-8")
    assert index_text.count("concepts/stoic-dichotomy-of-control.md") == 1
    assert "people/epictetus.md" in index_text
    assert okf.check_conformance(tmp_path / "bundle") == []


def test_reingest_all_slugs_already_exist_is_a_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Re-ingest whose extraction yields ONLY slugs that already exist for
    this source writes no derived object and raises no error (Phase 10.2;
    spec: "Re-ingest with all objects already present")."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, _multi_object_reply(_concept_reply(), _person_reply()))
    source = tmp_path / "notes.txt"
    source.write_text("content", encoding="utf-8")
    first = runner.invoke(app, ["ingest", "notes.txt", "--auto"])
    assert first.exit_code == 0
    concept_path = tmp_path / "bundle" / "concepts" / "stoic-dichotomy-of-control.md"
    person_path = tmp_path / "bundle" / "people" / "epictetus.md"
    assert concept_path.is_file()
    assert person_path.is_file()
    concept_before = concept_path.read_text(encoding="utf-8")
    person_before = person_path.read_text(encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    assert concept_path.read_text(encoding="utf-8") == concept_before
    assert person_path.read_text(encoding="utf-8") == person_before
    index_text = (tmp_path / "bundle" / "index.md").read_text(encoding="utf-8")
    assert index_text.count("concepts/stoic-dichotomy-of-control.md") == 1
    assert index_text.count("people/epictetus.md") == 1


def test_phase_a_existence_checks_precede_phase_b_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ALL Phase A `derived_path.exists()` checks complete before the FIRST
    Phase B `write_exclusive` call -- the write set is fully computed and
    deduped before the first byte lands (Phase 10.3; spec: Slug-Level
    Re-Ingest Reconciliation, ordering guarantee; design D5 pinned
    ordering)."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(
        monkeypatch,
        _multi_object_reply(_concept_reply(), _person_reply(), _organization_reply()),
    )
    source = tmp_path / "notes.txt"
    source.write_text("content", encoding="utf-8")

    calls: list[str] = []
    derived_dirs = ("concepts", "people", "organizations")
    original_exists = Path.exists
    original_write_exclusive = fsio.write_exclusive

    def recording_exists(self: Path) -> bool:
        outcome = original_exists(self)
        if self.suffix == ".md" and self.parent.name in derived_dirs:
            calls.append(f"exists:{self.name}")
        return outcome

    def recording_write_exclusive(path: Path, content: str) -> None:
        if path.suffix == ".md" and path.parent.name in derived_dirs:
            calls.append(f"write:{path.name}")
        original_write_exclusive(path, content)

    monkeypatch.setattr(Path, "exists", recording_exists)
    monkeypatch.setattr(fsio, "write_exclusive", recording_write_exclusive)

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    exists_calls = [c for c in calls if c.startswith("exists:")]
    write_calls = [c for c in calls if c.startswith("write:")]
    assert len(exists_calls) == 3
    assert len(write_calls) == 3
    first_write_index = calls.index(write_calls[0])
    assert all(calls.index(c) < first_write_index for c in exists_calls)


def test_build_concept_failure_skips_only_that_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One candidate whose title contains an embedded newline (passes
    `extract_concept`'s own validation but fails `okf.build_concept`'s
    stricter single-line gate) is skipped without discarding the OTHER
    valid candidates in the same batch (Phase 10.4)."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(
        monkeypatch,
        _multi_object_reply(
            _concept_reply(title="Stoic Framework\nExtra Line"), _person_reply()
        ),
    )
    source = tmp_path / "notes.txt"
    source.write_text("content", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    assert not (tmp_path / "bundle" / "concepts").exists()
    person_path = tmp_path / "bundle" / "people" / "epictetus.md"
    assert person_path.is_file()
    assert "extracted content failed validation" in result.stderr
    assert okf.check_conformance(tmp_path / "bundle") == []


def test_batch_of_five_all_staged_no_second_cap_in_main(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A batch of exactly 5 valid, non-colliding, non-existing candidates
    are ALL staged and written -- `main.py` never re-caps; `concept.py`'s
    `_MAX_OBJECTS_PER_SOURCE = 5` is the only ceiling (Phase 11; spec:
    "LLM proposes more than CAP objects" / "Multiple distinct objects
    extracted, under cap")."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(
        monkeypatch,
        _multi_object_reply(
            _concept_reply(),
            _entity_reply(),
            _person_reply(),
            _organization_reply(),
            _place_reply(),
        ),
    )
    source = tmp_path / "notes.txt"
    source.write_text("content", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    assert (
        tmp_path / "bundle" / "concepts" / "stoic-dichotomy-of-control.md"
    ).is_file()
    assert (tmp_path / "bundle" / "entities" / "enchiridion.md").is_file()
    assert (tmp_path / "bundle" / "people" / "epictetus.md").is_file()
    assert (tmp_path / "bundle" / "organizations" / "praxis-foundation.md").is_file()
    assert (tmp_path / "bundle" / "places" / "yellowstone-national-park.md").is_file()
    assert okf.check_conformance(tmp_path / "bundle") == []


def test_two_same_type_candidates_in_one_batch_are_both_indexed_and_logged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two distinct, non-colliding candidates of the SAME derived type in
    one extraction batch are BOTH written and BOTH get their own
    `index.md`/`log.md` entries under the SAME catalog section -- the
    in-batch collision guard and per-slug reconciliation only drop a
    candidate on a genuine slug match, never merely for sharing a type."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(
        monkeypatch,
        _multi_object_reply(
            _concept_reply(title="Stoic Dichotomy Of Control"),
            _concept_reply(title="Amor Fati"),
        ),
    )
    source = tmp_path / "notes.txt"
    source.write_text("content", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    first_path = tmp_path / "bundle" / "concepts" / "stoic-dichotomy-of-control.md"
    second_path = tmp_path / "bundle" / "concepts" / "amor-fati.md"
    assert first_path.is_file()
    assert second_path.is_file()
    index_text = (tmp_path / "bundle" / "index.md").read_text(encoding="utf-8")
    log_text = (tmp_path / "bundle" / "log.md").read_text(encoding="utf-8")
    assert index_text.count("# Concepts") == 1
    assert "concepts/stoic-dichotomy-of-control.md" in index_text
    assert "concepts/amor-fati.md" in index_text
    assert "Stoic Dichotomy Of Control" in log_text
    assert "Amor Fati" in log_text
    assert okf.check_conformance(tmp_path / "bundle") == []


def test_interactive_preview_lists_all_staged_objects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The confirmation preview lists the Source AND every staged derived
    object, one `+ bundle/<link_dir>/<slug>.md` line each, before the
    confirm gate (Phase 12.1)."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, _multi_object_reply(_concept_reply(), _person_reply()))
    source = tmp_path / "notes.txt"
    source.write_text("content", encoding="utf-8")
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["ingest", "notes.txt"], input="y\n")

    assert result.exit_code == 0
    assert "+ bundle/concepts/stoic-dichotomy-of-control.md" in result.stdout
    assert "+ bundle/people/epictetus.md" in result.stdout


def test_final_echo_lists_all_derived_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The final confirmation echo lists the Source path plus every staged
    derived object's path (0..N), alongside the always-present Source path
    (Phase 12.4)."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, _multi_object_reply(_concept_reply(), _person_reply()))
    source = tmp_path / "notes.txt"
    source.write_text("content", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    assert (
        "raw/notes.txt, bundle/sources/notes.md, "
        "bundle/concepts/stoic-dichotomy-of-control.md, "
        "bundle/people/epictetus.md" in result.stdout
    )


def test_exists_skip_reports_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Re-ingesting a source whose derived object's slug already exists
    prints a per-candidate stderr note naming the skipped slug (Phase 13;
    design D4 drop transparency) -- distinct from the whole-batch
    "no concept extracted" wording used when extraction itself yields
    nothing."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, _concept_reply())
    source = tmp_path / "notes.txt"
    source.write_text("Some raw notes about self-control.", encoding="utf-8")
    first = runner.invoke(app, ["ingest", "notes.txt", "--auto"])
    assert first.exit_code == 0

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    assert "stoic-dichotomy-of-control" in result.stderr
    assert "already exists" in result.stderr


# --- Ingest Progress Feedback (per-type tally + spinner) --------------------


def test_zero_derived_objects_prints_no_tally_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Source-only degrade (zero derived objects written) MUST NOT emit an
    `extracted ... objects` tally line (spec: Zero derived objects -- no
    tally line)."""
    _init_workspace(tmp_path, monkeypatch)
    source = tmp_path / "notes.txt"
    source.write_text("Some raw notes.", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    assert "extracted" not in result.stdout


def test_single_derived_object_prints_singular_tally_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Writing exactly one `Concept` derived object prints the singular
    tally line on stdout (spec: Single object, singular wording)."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, _concept_reply())
    source = tmp_path / "notes.txt"
    source.write_text("Some raw notes about self-control.", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    assert "extracted 1 object — 1 Concept" in result.stdout


def test_mixed_derived_objects_print_tally_in_canonical_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Writing derived objects of types `Person`, `Concept`, `Event` (in
    that reply order) prints the tally line ordered by canonical
    `_TYPE_TO_SECTION` registry order (`Concept`, `Event`, `Person`), not
    reply order (spec: Multiple objects, mixed types in canonical order)."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(
        monkeypatch,
        _multi_object_reply(_person_reply(), _concept_reply(), _event_reply()),
    )
    source = tmp_path / "notes.txt"
    source.write_text("content", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    assert "extracted 3 objects — 1 Concept, 1 Event, 1 Person" in result.stdout


def test_non_tty_ingest_stdout_has_no_spinner_control_chars(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Under the default (non-TTY) `CliRunner` invocation, `ingest`'s exit
    code is unchanged and stdout contains no spinner control characters or
    partial-line artifacts (spec: Spinner is stderr-only and stdout stays
    clean)."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, _concept_reply())
    source = tmp_path / "notes.txt"
    source.write_text("Some raw notes about self-control.", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    assert "\x1b[" not in result.stdout


class _FakeStatus:
    """A minimal spy standing in for `rich.console.Console(...).status(...)`'s
    returned context manager: records whether it was entered/exited so tests
    can assert the spinner is invoked and cleared without a real TTY."""

    def __init__(self) -> None:
        self.entered = False
        self.exited = False

    def __enter__(self) -> "_FakeStatus":
        self.entered = True
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.exited = True


class _FakeConsole:
    """A spy standing in for `openkos.cli.main.Console`: records the
    constructor kwargs and every `.status(...)` call so tests can assert
    `stderr=True` construction and that the spinner is entered/exited."""

    instances: ClassVar[list["_FakeConsole"]] = []

    def __init__(self, *args: object, **kwargs: object) -> None:
        self.init_kwargs = kwargs
        self.status_calls: list[str] = []
        self.statuses: list[_FakeStatus] = []
        _FakeConsole.instances.append(self)

    def status(self, message: str) -> _FakeStatus:
        self.status_calls.append(message)
        fake_status = _FakeStatus()
        self.statuses.append(fake_status)
        return fake_status


def test_spinner_console_constructed_with_stderr_and_cleared_on_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The `main.Console` spy seam is constructed with `stderr=True`,
    `.status(...)` is entered, and `__exit__` runs (spinner cleared) when
    `extract_concept` succeeds (spec: Spinner clears on extraction
    success; design: spy seam verification)."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, _concept_reply())
    _FakeConsole.instances.clear()
    monkeypatch.setattr(main, "Console", _FakeConsole)
    source = tmp_path / "notes.txt"
    source.write_text("Some raw notes about self-control.", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    assert len(_FakeConsole.instances) == 1
    console_instance = _FakeConsole.instances[0]
    assert console_instance.init_kwargs == {"stderr": True}
    assert len(console_instance.statuses) == 1
    assert console_instance.statuses[0].entered is True
    assert console_instance.statuses[0].exited is True


def test_spinner_cleared_on_ollama_error_and_degrade_proceeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The `main.Console` spy seam's `.status(...)` `__exit__` still runs
    (spinner cleared) when `extract_concept` raises `OllamaError`, and
    `ingest` proceeds to its existing Source-only degrade stdout/stderr
    behavior unchanged (spec: Spinner clears on OllamaError)."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, raises=OllamaUnavailable("boom"))
    _FakeConsole.instances.clear()
    monkeypatch.setattr(main, "Console", _FakeConsole)
    source = tmp_path / "notes.txt"
    source.write_text("Some raw notes about self-control.", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    assert len(_FakeConsole.instances) == 1
    console_instance = _FakeConsole.instances[0]
    assert console_instance.init_kwargs == {"stderr": True}
    assert console_instance.statuses[0].exited is True
    assert "concept extraction skipped" in result.stderr


# --- Deterministic slug-collision disambiguation (#131) ---------------------


def test_family_regex_excludes_base_word_slug(tmp_path: Path) -> None:
    """`_collision_family` matches `<base>.md` and `<base>-N.md` (N numeric)
    but NOT `<base>-word.md` -- a regex-anchored family, never a naive glob
    (spec: Collision loop mechanics false-positive guard)."""
    link_dir = tmp_path / "concepts"
    link_dir.mkdir()
    (link_dir / "note.md").write_text("body", encoding="utf-8")
    (link_dir / "note-2.md").write_text("body", encoding="utf-8")
    (link_dir / "note-extra.md").write_text("body", encoding="utf-8")

    family = main._collision_family(link_dir, "note")

    names = {path.name for path in family}
    assert names == {"note.md", "note-2.md"}


def test_family_scan_skips_malformed_frontmatter_member(tmp_path: Path) -> None:
    """A collision family member with malformed frontmatter is skipped by
    `_family_owns_source` rather than raising -- the scan degrades per
    member, it never crashes (spec: Parse-error tolerance)."""
    link_dir = tmp_path / "concepts"
    link_dir.mkdir()
    (link_dir / "note.md").write_text(
        "---\nprovenance:\n  - sources/other-source\n---\nbody", encoding="utf-8"
    )
    (link_dir / "note-2.md").write_text(
        "---\ntitle: [unclosed\n---\nbody", encoding="utf-8"
    )

    family = main._collision_family(link_dir, "note")
    owns = main._family_owns_source(family, "target-source")

    assert owns is False


def test_foreign_collision_writes_slug_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second source whose extraction yields the SAME title as an
    already-ingested, DIFFERENT source's derived object is written to
    `<slug>-2` with its own single-source `provenance`, leaving the first
    source's file untouched (spec: Second, different-source, same-title
    candidate writes to `<slug>-2`)."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, _concept_reply(title="Stoic Practice"))
    source_a = tmp_path / "notes-a.txt"
    source_a.write_text("Notes from source A.", encoding="utf-8")
    first = runner.invoke(app, ["ingest", "notes-a.txt", "--auto"])
    assert first.exit_code == 0
    base_path = tmp_path / "bundle" / "concepts" / "stoic-practice.md"
    assert base_path.is_file()

    source_b = tmp_path / "notes-b.txt"
    source_b.write_text("Notes from source B.", encoding="utf-8")
    result = runner.invoke(app, ["ingest", "notes-b.txt", "--auto"])

    assert result.exit_code == 0
    disambiguated_path = tmp_path / "bundle" / "concepts" / "stoic-practice-2.md"
    assert disambiguated_path.is_file()
    base_metadata, _ = okf.load_frontmatter(base_path.read_text(encoding="utf-8"))
    assert base_metadata["provenance"] == ["sources/notes-a"]
    disambiguated_metadata, _ = okf.load_frontmatter(
        disambiguated_path.read_text(encoding="utf-8")
    )
    assert disambiguated_metadata["provenance"] == ["sources/notes-b"]
    assert okf.check_conformance(tmp_path / "bundle") == []


def test_third_foreign_source_writes_slug_3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A THIRD different source with the same title, after `<slug>` and
    `<slug>-2` are already taken by different sources, is written to the
    first free numeric suffix `<slug>-3` (spec: Third, different-source,
    same-title candidate writes to `<slug>-3`)."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, _concept_reply(title="Stoic Practice"))
    for name, text in (
        ("notes-a.txt", "Notes from source A."),
        ("notes-b.txt", "Notes from source B."),
        ("notes-c.txt", "Notes from source C."),
    ):
        source = tmp_path / name
        source.write_text(text, encoding="utf-8")
        result = runner.invoke(app, ["ingest", name, "--auto"])
        assert result.exit_code == 0

    assert (tmp_path / "bundle" / "concepts" / "stoic-practice.md").is_file()
    assert (tmp_path / "bundle" / "concepts" / "stoic-practice-2.md").is_file()
    third_path = tmp_path / "bundle" / "concepts" / "stoic-practice-3.md"
    assert third_path.is_file()
    metadata, _ = okf.load_frontmatter(third_path.read_text(encoding="utf-8"))
    assert metadata["provenance"] == ["sources/notes-c"]
    assert okf.check_conformance(tmp_path / "bundle") == []


def test_reingest_owner_of_base_slug_is_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Re-ingesting the source that owns the base `<slug>` recognizes it as
    this source's own object via the provenance family scan and writes no
    new file, even after a foreign source has since taken `<slug>-2` (spec:
    Re-ingesting the first source spawns no new file)."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, _concept_reply(title="Stoic Practice"))
    source_a = tmp_path / "notes-a.txt"
    source_a.write_text("Notes from source A.", encoding="utf-8")
    first = runner.invoke(app, ["ingest", "notes-a.txt", "--auto"])
    assert first.exit_code == 0
    source_b = tmp_path / "notes-b.txt"
    source_b.write_text("Notes from source B.", encoding="utf-8")
    second = runner.invoke(app, ["ingest", "notes-b.txt", "--auto"])
    assert second.exit_code == 0

    result = runner.invoke(app, ["ingest", "notes-a.txt", "--auto"])

    assert result.exit_code == 0
    assert not (tmp_path / "bundle" / "concepts" / "stoic-practice-3.md").exists()
    concept_dir = tmp_path / "bundle" / "concepts"
    assert sorted(p.name for p in concept_dir.glob("*.md")) == [
        "stoic-practice-2.md",
        "stoic-practice.md",
    ]


def test_reingest_owner_of_slug_2_does_not_spawn_slug_3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CRITICAL: re-ingesting the source that owns the disambiguated
    `<slug>-2` scans the WHOLE collision family, recognizes `<slug>-2` as
    its own object, and writes no `<slug>-3` -- a prior `-N` winner must
    never spawn a further disambiguation on re-ingest (spec: Re-ingesting
    the source that owns `<slug>-2` does not spawn `-3`)."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, _concept_reply(title="Stoic Practice"))
    source_a = tmp_path / "notes-a.txt"
    source_a.write_text("Notes from source A.", encoding="utf-8")
    first = runner.invoke(app, ["ingest", "notes-a.txt", "--auto"])
    assert first.exit_code == 0
    source_b = tmp_path / "notes-b.txt"
    source_b.write_text("Notes from source B.", encoding="utf-8")
    second = runner.invoke(app, ["ingest", "notes-b.txt", "--auto"])
    assert second.exit_code == 0
    assert (tmp_path / "bundle" / "concepts" / "stoic-practice-2.md").is_file()

    result = runner.invoke(app, ["ingest", "notes-b.txt", "--auto"])

    assert result.exit_code == 0
    assert not (tmp_path / "bundle" / "concepts" / "stoic-practice-3.md").exists()
    concept_dir = tmp_path / "bundle" / "concepts"
    assert sorted(p.name for p in concept_dir.glob("*.md")) == [
        "stoic-practice-2.md",
        "stoic-practice.md",
    ]


def test_noncolliding_candidate_written_without_suffix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A candidate whose slug has no existing on-disk collision at all is
    written to the plain `<slug>.md`, unchanged by the disambiguation
    machinery (spec: First foreign-source collision writes to `<slug>`,
    baseline no-collision path)."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, _concept_reply(title="Unique Stoic Idea"))
    source = tmp_path / "notes.txt"
    source.write_text("content", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    concept_path = tmp_path / "bundle" / "concepts" / "unique-stoic-idea.md"
    assert concept_path.is_file()
    metadata, _ = okf.load_frontmatter(concept_path.read_text(encoding="utf-8"))
    assert metadata["provenance"] == ["sources/notes"]
    assert okf.check_conformance(tmp_path / "bundle") == []


def test_disambiguation_writes_audit_log_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A foreign-source disambiguation appends one durable `log.md` bullet,
    via `insert_log_entry`, naming the source slug, the extracted title,
    the original colliding slug, and the chosen disambiguated slug (spec:
    Durable Disambiguation Audit Log)."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, _concept_reply(title="Stoic Practice"))
    source_a = tmp_path / "notes-a.txt"
    source_a.write_text("Notes from source A.", encoding="utf-8")
    first = runner.invoke(app, ["ingest", "notes-a.txt", "--auto"])
    assert first.exit_code == 0

    source_b = tmp_path / "notes-b.txt"
    source_b.write_text("Notes from source B.", encoding="utf-8")
    result = runner.invoke(app, ["ingest", "notes-b.txt", "--auto"])

    assert result.exit_code == 0
    log_text = (tmp_path / "bundle" / "log.md").read_text(encoding="utf-8")
    assert "Disambiguation" in log_text
    assert "notes-b" in log_text
    assert "Stoic Practice" in log_text
    assert "stoic-practice" in log_text
    assert "stoic-practice-2" in log_text


def test_status_surfaces_disambiguation_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The disambiguation audit entry, once written, is surfaced by
    `openkos status`'s recent-activity section alongside other log entries
    -- no new persisted ledger file is introduced (spec: Disambiguating
    ingest is recorded and surfaced)."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, _concept_reply(title="Stoic Practice"))
    source_a = tmp_path / "notes-a.txt"
    source_a.write_text("Notes from source A.", encoding="utf-8")
    first = runner.invoke(app, ["ingest", "notes-a.txt", "--auto"])
    assert first.exit_code == 0
    source_b = tmp_path / "notes-b.txt"
    source_b.write_text("Notes from source B.", encoding="utf-8")
    second = runner.invoke(app, ["ingest", "notes-b.txt", "--auto"])
    assert second.exit_code == 0

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert "Disambiguation" in result.stdout


def test_byte_identical_reingest_short_circuit_still_holds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A byte-identical re-ingest still short-circuits exactly as before
    (D2, unmodified by the disambiguation feature): the raw copy is
    reused, no new derived-object file of any kind is written, and the
    provenance-scan machinery is never even reached because
    `_stage_derived_objects` sees the SAME slugs it staged the first time
    (spec: Byte-identical raw re-ingest short-circuits, unchanged)."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, _concept_reply(title="Stoic Practice"))
    source = tmp_path / "notes.txt"
    source.write_text("Some raw notes about self-control.", encoding="utf-8")
    first = runner.invoke(app, ["ingest", "notes.txt", "--auto"])
    assert first.exit_code == 0
    concept_dir = tmp_path / "bundle" / "concepts"
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    assert sorted(p.name for p in concept_dir.glob("*.md")) == ["stoic-practice.md"]
    after = _snapshot(tmp_path)
    concept_relpath = Path("bundle") / "concepts" / "stoic-practice.md"
    assert before[concept_relpath] == after[concept_relpath]


# --- Phase 3.3 (#183): embeddings computed during ingest ------------------


_EMBED_FAILURES = [
    OllamaUnavailable("connection refused"),
    OllamaModelNotFound("model 'bge-m3' is not installed"),
    OllamaError("malformed response"),
    RuntimeError("something nobody mapped"),
]


class _EmbeddingLLM(_FakeLLM):
    """A `_FakeLLM` that also serves `embed()`, as the real `OllamaClient`
    does -- `ingest` builds both roles from that one class."""

    def __init__(self, reply: str, *, embed_raises: Exception | None = None) -> None:
        super().__init__(reply)
        self.embed_raises = embed_raises
        self.embed_calls: list[list[str]] = []

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.embed_calls.append(list(texts))
        if self.embed_raises is not None:
            raise self.embed_raises
        return [[1.0] + [0.0] * (EMBED_DIM - 1) for _ in texts]


def test_ingest_embeds_the_concepts_it_just_wrote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Issue #183's fix: embeddings are computed in the SAME run that
    creates the concepts, so candidate edges are available immediately
    rather than only after a separate `openkos reindex`.

    Without this, a user's first `suggest-relations` after ingesting always
    reports an empty graph -- which is the symptom the issue opens with."""
    _init_workspace(tmp_path, monkeypatch)
    fake = _EmbeddingLLM(_concept_reply())
    monkeypatch.setattr("openkos.cli.main.OllamaClient", lambda *a, **k: fake)
    src = tmp_path / "note.md"
    src.write_text("# Note\n\nRaw material.\n", encoding="utf-8")

    result = runner.invoke(app, ["ingest", str(src), "--auto"])

    assert result.exit_code == 0, result.stdout
    assert fake.embed_calls, "ingest never embedded the concepts it wrote"
    assert (tmp_path / ".openkos" / "vectors.db").exists()


@pytest.mark.parametrize("failure", _EMBED_FAILURES, ids=lambda e: type(e).__name__)
def test_ingest_survives_every_embedder_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    """Fail-open: embeddings are an ENHANCEMENT layered onto ingest, so
    losing them must never cost the user the ingest itself.

    Parametrized over the three mapped Ollama errors AND one deliberately
    unmapped exception type: the guard is broad on purpose, because a
    future backend raising something nobody anticipated must still not
    destroy a successful ingest. Exit code stays 0, the Source and its
    concepts are still on disk, and the failure is REPORTED rather than
    swallowed silently."""
    _init_workspace(tmp_path, monkeypatch)
    fake = _EmbeddingLLM(_concept_reply(), embed_raises=failure)
    monkeypatch.setattr("openkos.cli.main.OllamaClient", lambda *a, **k: fake)
    src = tmp_path / "note.md"
    src.write_text("# Note\n\nRaw material.\n", encoding="utf-8")

    result = runner.invoke(app, ["ingest", str(src), "--auto"])

    assert result.exit_code == 0, result.stdout
    assert list((tmp_path / "bundle" / "sources").glob("*.md"))
    assert list((tmp_path / "bundle" / "concepts").glob("*.md"))
    assert "openkos ingest: embeddings not updated" in result.stderr
    assert "openkos reindex" in result.stderr
    # Distinct from the pre-existing concept-extraction-skipped message, so
    # an operator can tell which half degraded.
    assert "concept extraction skipped" not in result.stderr


def test_ingest_embedding_failure_does_not_abort_the_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Embedding runs AFTER `_autocommit`, so a failing embedder cannot
    leave the workspace with files written but uncommitted -- the ingest is
    already durable by the time embeddings are attempted."""
    _init_workspace(tmp_path, monkeypatch)
    fake = _EmbeddingLLM(_concept_reply(), embed_raises=OllamaUnavailable("down"))
    monkeypatch.setattr("openkos.cli.main.OllamaClient", lambda *a, **k: fake)
    committed: list[str] = []
    monkeypatch.setattr(
        main,
        "_autocommit",
        lambda root, paths, message: committed.append(message),
    )
    src = tmp_path / "note.md"
    src.write_text("# Note\n\nRaw material.\n", encoding="utf-8")

    result = runner.invoke(app, ["ingest", str(src), "--auto"])

    assert result.exit_code == 0, result.stdout
    assert len(committed) == 1


# --- Sensitivity honesty + non-local backend warning (#183 review) ---------


def test_confidential_skip_message_admits_embeddings_still_ran(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`ingest` tells the user it is withholding confidential content from
    the LLM, then embeds that same content. Both are intentional -- the
    embedding backend is local and `.openkos/` is gitignored -- but saying
    only the first half leaves the user believing NOTHING was sent.

    The sensitivity contract covers the six `llm.chat` call sites, not
    `embed()`. That scope is defensible; silently implying otherwise is
    not."""
    _init_workspace(tmp_path, monkeypatch)
    _set_config_field(
        tmp_path, "default_sensitivity: private", "default_sensitivity: confidential"
    )
    fake = _EmbeddingLLM(_concept_reply())
    monkeypatch.setattr("openkos.cli.main.OllamaClient", lambda *a, **k: fake)
    src = tmp_path / "note.md"
    src.write_text("# Note\n\nSecret material.\n", encoding="utf-8")

    result = runner.invoke(app, ["ingest", str(src), "--auto"])

    assert result.exit_code == 0, result.stdout
    assert "skipping concept extraction" in result.stderr
    assert fake.embed_calls, "embeddings should still be computed"
    assert "added to the embedding index" in result.stderr
