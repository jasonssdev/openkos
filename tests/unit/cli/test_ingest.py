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
import threading
import unicodedata
import urllib.error
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

import pytest
from typer.testing import CliRunner, _NamedTextIOWrapper

from openkos import config, fsio
from openkos.bundle import index as bundle_index
from openkos.bundle import log as bundle_log
from openkos.cli import main
from openkos.cli.main import app
from openkos.extraction import concept as concept_mod
from openkos.llm.base import EMBED_DIM, Message
from openkos.llm.ollama import (
    OllamaError,
    OllamaModelNotFound,
    OllamaUnavailable,
)
from openkos.model import okf
from openkos.state import fts as state_fts
from openkos.state import reindex as state_reindex
from openkos.vcs import git as vcs_git
from tests.unit.cli.conftest import (
    changed_paths,
    confirm_after,
    echo_after,
    snapshot_with_mtime,
)
from tests.unit.cli.conftest import snapshot_bytes as _snapshot
from tests.unit.conftest import LOCAL_BACKEND_LOCALITY
from tests.unit.vcs.conftest import isolate_git_identity

runner = CliRunner()


def _opt_in_person_offset(tmp_path: Path) -> None:
    """Opt this workspace in to `type_sensitivity_defaults: {Person: 1}`.

    The packaged default is EMPTY since #756 -- sensitivity is the
    operator's call and no type is born above the floor unless they say so.
    The offset MECHANISM is unchanged and still has to be proven, so the
    tests that exercise it now configure it the way a real operator would
    instead of leaning on a shipped value that no longer exists.
    """
    config_path = tmp_path / "openkos.yaml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8")
        + "\ntype_sensitivity_defaults:\n  Person: 1\n",
        encoding="utf-8",
    )


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

    locality = LOCAL_BACKEND_LOCALITY
    """Stands in for `OllamaClient.locality` (issue #240): the CLI reads it
    for the embedding-host advisory and the confidential local exemption,
    and a fake without it raises `AttributeError` inside a fail-open
    handler -- a fixture gap that would read as a degrade."""

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
    # Fixture-churn boundary (source-title-from-heading): this bare
    # `"content"` fixture is now a content-derived title ("content", a
    # single title-plausible line with no trailing blank issue since it is
    # also EOF) rather than the slug ("notes"), while the neighboring
    # `test_successful_ingest_of_valid_path` above keeps its slug title
    # because `"Some raw notes."` ends in `.` and fails the title-plausible
    # predicate's terminal-punctuation clause. Neither test asserts on the
    # title, so both pass unchanged -- the split is the predicate working
    # as designed, not an inconsistency between adjacent fixtures.
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
    source = tmp_path / "notes.txt"
    source.write_text("new content", encoding="utf-8")
    _stage_ingested_raw(tmp_path, "notes.txt", "original", source)
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
    call `llm.chat` (spec: Private floor proceeds unchanged) -- 2 calls
    under the union+judge product default (#456: 2 extraction runs; the
    fixed reply merges to ONE candidate, so the judge call is skipped,
    #644), not blocked by the sensitivity gate."""
    _init_workspace(tmp_path, monkeypatch)
    fake = _patch_llm(monkeypatch, _concept_reply())
    source = tmp_path / "notes.txt"
    source.write_text("Some raw notes about self-control.", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    assert len(fake.calls) == 2
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
    confidential` floor gate: `llm.chat` IS called, and the derived object
    is written, even at a confidential floor (spec: `--include-confidential`
    Escape Flag) -- 2 calls under the union+judge product default (#456:
    2 extraction runs; the fixed reply merges to ONE candidate, so the
    judge call is skipped, #644)."""
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
    assert len(fake.calls) == 2
    concept_path = tmp_path / "bundle" / "concepts" / "stoic-dichotomy-of-control.md"
    assert concept_path.is_file()


# --- `_stage_derived_objects` return-shape (issue #187, design: Stage/Build
# Ordering) -------------------------------------------------------------


def _default_cfg(**overrides: object) -> config.Config:
    """Build a minimal `config.Config` for direct `_stage_derived_objects`
    calls (issue #669) -- mirrors `test_lint.py::_cfg`'s hand-built-Config
    pattern. Defaults to `{"Person": 1}` -- a workspace that has OPTED IN to
    the per-type offset -- so callers exercising the mechanism need only
    override `stamp_sensitivity`/`default_sensitivity`; pass
    `type_sensitivity_defaults={}` to opt out entirely.

    That is no longer the PACKAGED default, which is empty since #756. It is
    written here as a literal on purpose: these tests exist to prove the
    mechanism still raises, and reading the shipped constant would have made
    every one of them go quietly vacuous the moment that constant emptied."""
    fields: dict[str, object] = {
        "model": "qwen3:8b",
        "review": True,
        "default_sensitivity": "private",
        "freshness_window": "7d",
        "embedding_model": "bge-m3",
        "chat_timeout": config.DEFAULT_CHAT_TIMEOUT,
        "max_generation_tokens": config.DEFAULT_MAX_GENERATION_TOKENS,
        "context_window": config.DEFAULT_CONTEXT_WINDOW,
        "confidential_local_exemption": config.DEFAULT_CONFIDENTIAL_LOCAL_EXEMPTION,
        "volatility_windows": {},
        "type_tiers": {},
        "models": {},
        "union_judge": config.DEFAULT_UNION_JUDGE,
        "concurrent_extraction": config.DEFAULT_CONCURRENT_EXTRACTION,
        "type_sensitivity_defaults": {"Person": 1},
    }
    fields.update(overrides)
    return config.Config(**fields)  # type: ignore[arg-type]


def _stage_kwargs(tmp_path: Path, **overrides: object) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "raw_content": "Some raw notes about self-control.",
        "source_title": "Notes",
        "source_slug": "notes",
        "workspace_floor": "private",
        "stamp_sensitivity": "private",
        "timestamp": "2026-07-14T18:30:00Z",
        "bundle_dir": tmp_path / "bundle",
        "llm": _FakeLLM('{"extract": false}'),
        "cfg": _default_cfg(),
    }
    kwargs.update(overrides)
    return kwargs


def test_stage_derived_objects_returns_no_extractable_text_reason(
    tmp_path: Path,
) -> None:
    """`_stage_derived_objects` returns `([], "no-extractable-text")` when
    `raw_content` is blank -- the tuple return shape carries the skip
    reason alongside the (empty) plan list (design: `_stage_derived_objects`
    return shape; spec: no-extractable-text is written)."""
    plans, skip_reason, _notice = main._stage_derived_objects(
        **_stage_kwargs(tmp_path, raw_content="   ")  # type: ignore[arg-type]
    )

    assert plans == []
    assert skip_reason == "no-extractable-text"


def test_stage_derived_objects_returns_blocked_by_sensitivity_reason(
    tmp_path: Path,
) -> None:
    """`_stage_derived_objects` returns `([], "blocked-by-sensitivity")` when
    the workspace floor blocks the LLM send (spec: blocked-by-sensitivity is
    written)."""
    plans, skip_reason, _notice = main._stage_derived_objects(
        **_stage_kwargs(tmp_path, workspace_floor="confidential")  # type: ignore[arg-type]
    )

    assert plans == []
    assert skip_reason == "blocked-by-sensitivity"


def test_stage_derived_objects_returns_failed_reason(tmp_path: Path) -> None:
    """`_stage_derived_objects` returns `([], "failed")` when `llm.chat`
    raises `OllamaError` (spec: failed is written)."""
    plans, skip_reason, _notice = main._stage_derived_objects(
        **_stage_kwargs(  # type: ignore[arg-type]
            tmp_path, llm=_FakeLLM(raises=OllamaUnavailable("boom"))
        )
    )

    assert plans == []
    assert skip_reason == "failed"


def test_stage_derived_objects_returns_no_concepts_found_reason(
    tmp_path: Path,
) -> None:
    """`_stage_derived_objects` returns `([], "no-concepts-found")` when
    extraction succeeds with zero candidates (spec: no-concepts-found is
    written)."""
    plans, skip_reason, _notice = main._stage_derived_objects(
        **_stage_kwargs(tmp_path, llm=_FakeLLM('{"extract": false}'))  # type: ignore[arg-type]
    )

    assert plans == []
    assert skip_reason == "no-concepts-found"


def test_stage_derived_objects_returns_none_reason_on_success(
    tmp_path: Path,
) -> None:
    """`_stage_derived_objects` returns `(plans, None)` when at least one
    candidate is staged -- `skip_reason` is `None` on the healthy path
    (design: sequence diagram, terminal `return plans, None`)."""
    plans, skip_reason, _notice = main._stage_derived_objects(
        **_stage_kwargs(tmp_path, llm=_FakeLLM(_concept_reply()))  # type: ignore[arg-type]
    )

    assert len(plans) == 1
    assert skip_reason is None


# --- type-sensitivity-defaults: ingest birth seam (issue #669) --------------


def test_stage_derived_objects_births_person_above_the_floor(
    tmp_path: Path,
) -> None:
    """A `Person` extracted from a `public`-resolved Source is born
    `private` under the shipped `{"Person": 1}` mapping -- the ingest half
    of `type_birth_sensitivity`'s floor-relative raise (spec:
    `type-sensitivity-defaults`, "Both `build_concept` Birth Seams Consult
    The Type Default"; design D3/D4).

    TWIN-RULE GUARD (design D6): this is one of the two independent birth
    -seam site tests -- it must fail if ONLY `_stage_derived_objects`'s
    call site is reverted to `sensitivity=stamp_sensitivity` (a `base`-only
    stamp), independent of the `query --save` seam's own site test (WU4)."""
    plans, reason, _notice = main._stage_derived_objects(
        **_stage_kwargs(  # type: ignore[arg-type]
            tmp_path,
            llm=_FakeLLM(_person_reply()),
            stamp_sensitivity="public",
            cfg=_default_cfg(default_sensitivity="public"),
        )
    )

    assert reason is None
    assert len(plans) == 1
    metadata, _ = okf.load_frontmatter(plans[0].content)
    assert metadata["sensitivity"] == "private"
    assert plans[0].type_floor_raised is True


def test_stage_derived_objects_non_defaulted_type_is_untouched(
    tmp_path: Path,
) -> None:
    """A type absent from the mapping (`Organization`) is born exactly at
    the Source's resolved level, with no per-type raise (spec: "A type
    absent from the mapping is unaffected")."""
    plans, reason, _notice = main._stage_derived_objects(
        **_stage_kwargs(  # type: ignore[arg-type]
            tmp_path,
            llm=_FakeLLM(_organization_reply()),
            stamp_sensitivity="public",
            cfg=_default_cfg(default_sensitivity="public"),
        )
    )

    assert reason is None
    assert len(plans) == 1
    metadata, _ = okf.load_frontmatter(plans[0].content)
    assert metadata["sensitivity"] == "public"
    assert plans[0].type_floor_raised is False


def test_stage_derived_objects_clamps_at_confidential(tmp_path: Path) -> None:
    """A `confidential` floor stays `confidential` -- clamped, never an
    out-of-range value (spec: "Confidential floor stays confidential")."""
    plans, reason, _notice = main._stage_derived_objects(
        **_stage_kwargs(  # type: ignore[arg-type]
            tmp_path,
            llm=_FakeLLM(_person_reply()),
            stamp_sensitivity="confidential",
            workspace_floor="confidential",
            include_confidential=True,
            cfg=_default_cfg(default_sensitivity="confidential"),
        )
    )

    assert reason is None
    assert len(plans) == 1
    metadata, _ = okf.load_frontmatter(plans[0].content)
    assert metadata["sensitivity"] == "confidential"


def test_ingest_source_sensitivity_is_never_type_defaulted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Source's own resolved `sensitivity` is untouched by the `Person`
    type default -- only `okf.build_concept` call sites consult the
    mapping, `build_source_concept` never does (spec: "Sources Are Never
    Type-Defaulted"). The Person derived from it IS raised, proving the two
    are computed independently rather than one leaking into the other."""
    _init_workspace(tmp_path, monkeypatch)
    _opt_in_person_offset(tmp_path)
    _patch_llm(monkeypatch, _person_reply())
    source = tmp_path / "notes.txt"
    source.write_text("Epictetus was a Stoic philosopher.", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    source_metadata, _ = okf.load_frontmatter(
        (tmp_path / "bundle" / "sources" / "notes.md").read_text(encoding="utf-8")
    )
    assert source_metadata["sensitivity"] == "private"
    person_metadata, _ = okf.load_frontmatter(
        (tmp_path / "bundle" / "people" / "epictetus.md").read_text(encoding="utf-8")
    )
    assert person_metadata["sensitivity"] == "confidential"


def test_ingest_no_backfill_of_existing_person_after_unrelated_ingest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An existing on-disk `Person` concept's `sensitivity` field is
    byte-identical after an unrelated `ingest` run, even though the type
    default is active throughout the run -- birth-time only, never a
    retroactive scan (spec: "No Backfill Of Existing On-Disk Concepts")."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, _person_reply())
    source = tmp_path / "notes.txt"
    source.write_text("Epictetus was a Stoic philosopher.", encoding="utf-8")
    first = runner.invoke(app, ["ingest", "notes.txt", "--auto"])
    assert first.exit_code == 0
    person_path = tmp_path / "bundle" / "people" / "epictetus.md"
    original = person_path.read_text(encoding="utf-8")

    _patch_llm(monkeypatch, _concept_reply())
    other = tmp_path / "other.txt"
    other.write_text("Some unrelated other content.", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "other.txt", "--auto"])

    assert result.exit_code == 0
    assert person_path.read_text(encoding="utf-8") == original


# --- type-sensitivity-defaults: ingest run-summary advisory (issue #669) ----


def test_stock_workspace_births_person_at_the_floor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A workspace nobody configured births a `Person` at the workspace
    floor, like every other type, and says nothing about it (#756).

    The packaged `type_sensitivity_defaults` used to be `{"Person": 1}`. On
    the primary use case -- a local bundle against a local backend -- that
    bought no protection (`confidential_local_exemption` lets confidential
    objects participate normally) and cost signal: when 100% of a type is
    `confidential`, the marker stops meaning "especially sensitive" and
    starts meaning "this is a Person". The offset mechanism is unchanged and
    still proven by the tests around this one; only the shipped policy is
    now "none".
    """
    _init_workspace(tmp_path, monkeypatch)  # deliberately NOT opted in
    _patch_llm(monkeypatch, _person_reply())
    source = tmp_path / "notes.txt"
    source.write_text("Epictetus was a Stoic philosopher.", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0, result.stderr
    person = next((tmp_path / "bundle" / "people").rglob("*.md"))
    metadata, _ = okf.load_frontmatter(person.read_text(encoding="utf-8"))
    assert metadata["sensitivity"] == "private", (
        "a stock workspace must not raise Person above its own floor"
    )
    assert "born above the workspace sensitivity floor" not in result.stderr


def test_ingest_prints_type_floor_advisory_with_confidential_consequence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `private`-floor workspace raises `Person` to `confidential`; the
    ingest run summary names the count/type and adds the #569
    retrieval-exclusion consequence line (spec: "Write-Time Advisory Names
    Type-Defaulted Objects And The Retrieval Consequence")."""
    _init_workspace(tmp_path, monkeypatch)
    _opt_in_person_offset(tmp_path)
    _patch_llm(monkeypatch, _person_reply())
    source = tmp_path / "notes.txt"
    source.write_text("Epictetus was a Stoic philosopher.", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    assert (
        "1 of 1 derived object(s) were born above the workspace sensitivity "
        "floor by type default" in result.stderr
    )
    assert "Person -> confidential" in result.stderr
    assert (
        "confidential objects are excluded from query, contradictions, and "
        "suggest-relations against a non-local backend" in result.stderr
    )


def test_ingest_prints_type_floor_advisory_without_consequence_at_private(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `public`-floor workspace raises `Person` to `private` -- the
    aggregate line fires, but the confidential-exclusion consequence line
    does NOT, since the raised level is not `confidential` (spec: the
    consequence line "fires only when the raised level is confidential")."""
    _init_workspace(tmp_path, monkeypatch)
    _opt_in_person_offset(tmp_path)
    _set_config_field(
        tmp_path, "default_sensitivity: private", "default_sensitivity: public"
    )
    _patch_llm(monkeypatch, _person_reply())
    source = tmp_path / "notes.txt"
    source.write_text("Epictetus was a Stoic philosopher.", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    assert (
        "1 of 1 derived object(s) were born above the workspace sensitivity "
        "floor by type default" in result.stderr
    )
    assert "Person -> private" in result.stderr
    assert "excluded from query" not in result.stderr


def test_ingest_type_floor_advisory_silent_when_nothing_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No object raised by the type default -> no advisory line at all
    (spec: "No advisory when nothing was raised by a type default")."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, _organization_reply())
    source = tmp_path / "notes.txt"
    source.write_text("Praxis Foundation notes.", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    assert "born above the workspace sensitivity floor" not in result.stderr


def test_ingest_batch_aggregates_type_floor_advisory_across_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A directory ingest emits the type-floor advisory ONCE for the whole
    batch, aggregated across files -- identical shape to the single-file
    seam (spec: works identically single-file and batch; mirrors #566's own
    batch-aggregate precedent)."""
    _init_workspace(tmp_path, monkeypatch)
    _opt_in_person_offset(tmp_path)
    _patch_llm(monkeypatch, _person_reply())
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.txt").write_text("Epictetus, first half.", encoding="utf-8")
    (docs / "b.txt").write_text("Epictetus, second half.", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "docs", "--auto"])

    assert result.exit_code == 0
    assert result.stderr.count("born above the workspace sensitivity floor") == 1
    assert "2 of 2 derived object(s) were born above" in result.stderr


# --- union_judge kwarg (#456, design D9) -------------------------------------


class _SequencedLLM:
    """A structural `LLMBackend` whose replies differ per call, mirroring
    `test_concept.py::_SequencedLLM`. `replies[i]` answers call `i`; an
    `Exception` instance raises instead of returning."""

    locality = LOCAL_BACKEND_LOCALITY
    """See `_FakeLLM.locality` above -- required for `ingest`'s embedding-host
    advisory and confidential local exemption checks."""

    def __init__(self, replies: Sequence[str | Exception]) -> None:
        self.replies = list(replies)
        self.calls: list[list[Message]] = []

    def chat(self, messages: Sequence[Message]) -> str:
        self.calls.append(list(messages))
        reply = self.replies[len(self.calls) - 1]
        if isinstance(reply, Exception):
            raise reply
        return reply


def test_stage_derived_objects_union_judge_false_calls_extract_concept_once(
    tmp_path: Path,
) -> None:
    """`union_judge=False` (the kwarg's own default) calls `extract_concept`
    exactly once per source -- no judge call, the untouched single-run path
    (design D9: 39 existing test call sites keep exercising this)."""
    llm = _FakeLLM(_concept_reply())

    plans, skip_reason, _notice = main._stage_derived_objects(
        **_stage_kwargs(tmp_path, llm=llm, union_judge=False)  # type: ignore[arg-type]
    )

    assert len(llm.calls) == 1
    assert len(plans) == 1
    assert skip_reason is None


def test_stage_derived_objects_union_judge_true_calls_extract_concept_union(
    tmp_path: Path,
) -> None:
    """`union_judge=True` routes through `extract_concept_union`: 2
    extraction calls + 1 judge call. Two DISTINCT candidates, because a
    single-candidate union skips the judge call entirely (#644)."""
    llm = _SequencedLLM(
        [
            _concept_reply(),
            _concept_reply(title="Negative Visualization"),
            '{"keep": ["Stoic Dichotomy Of Control", "Negative Visualization"]}',
        ]
    )

    plans, skip_reason, _notice = main._stage_derived_objects(
        **_stage_kwargs(tmp_path, llm=llm, union_judge=True)  # type: ignore[arg-type]
    )

    assert len(llm.calls) == 3
    assert len(plans) == 2
    assert skip_reason is None


def _patch_sequenced_llm(
    monkeypatch: pytest.MonkeyPatch, replies: Sequence[str | Exception]
) -> _SequencedLLM:
    """Replace `openkos.cli.main.OllamaClient` with a factory returning a
    `_SequencedLLM`, mirroring `_patch_llm`'s pattern for a fixed-reply fake."""
    fake = _SequencedLLM(replies)
    monkeypatch.setattr("openkos.cli.main.OllamaClient", lambda *args, **kwargs: fake)
    return fake


def test_ingest_judge_failure_keeps_the_merged_union_and_reports_distinctly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Task 3.6: base extraction succeeds (2 runs, 2 distinct candidates --
    a single-candidate union would skip the judge call entirely, #644),
    the judge call raises `OllamaError` -- the merged-union candidates
    (backstop-truncated) are staged/written, `_judge_failure_notice` fires
    (distinct wording from `_judge_selection_notice`/`_extraction_cap_notice`
    -- neither of which appears), and `ingest` exits 0."""
    _init_workspace(tmp_path, monkeypatch)
    run1 = _concept_reply(title="Stoic Dichotomy Of Control")
    run2 = _concept_reply(title="Negative Visualization")
    _patch_sequenced_llm(monkeypatch, [run1, run2, OllamaUnavailable("boom")])
    source = tmp_path / "notes.txt"
    source.write_text("Some raw notes about self-control.", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    assert "judge selection unavailable" in result.stderr
    assert "cap reached" not in result.stderr
    assert "judge dropped" not in result.stderr
    concept_path = tmp_path / "bundle" / "concepts" / "stoic-dichotomy-of-control.md"
    assert concept_path.is_file()


def test_ingest_judge_empty_admission_keeps_the_merged_union_and_reports_distinctly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Task 2.24 (#456 gate finding): base extraction succeeds (2 distinct
    candidates -- a single-candidate union skips the judge entirely, #644),
    the judge reply is well-formed but names a title absent from every
    candidate -- the admitted set is empty with nothing to re-admit. The
    merged-union candidates (backstop-truncated) are still staged/written,
    `_judge_failure_notice` fires with wording distinct from BOTH the
    `"failed"` degrade and a successful selection -- and honest about the
    cause: the judge REPLIED, so "unavailable" (what #644's reporter was
    misled by) must not appear -- and `ingest` exits 0."""
    _init_workspace(tmp_path, monkeypatch)
    run1 = _concept_reply(title="Stoic Dichotomy Of Control")
    run2 = _concept_reply(title="Negative Visualization")
    _patch_sequenced_llm(monkeypatch, [run1, run2, '{"keep": ["A Fabricated Title"]}'])
    source = tmp_path / "notes.txt"
    source.write_text("Some raw notes about self-control.", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    assert "judge reply matched no candidate" in result.stderr
    assert "judge selection unavailable" not in result.stderr
    assert "cap reached" not in result.stderr
    assert "judge dropped" not in result.stderr
    concept_path = tmp_path / "bundle" / "concepts" / "stoic-dichotomy-of-control.md"
    assert concept_path.is_file()


def test_ingest_single_candidate_union_skips_the_judge_and_reports_no_degrade(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#644's exact reported shape: both union runs agree on ONE candidate.
    No judge call is spent (the fake carries NO judge reply -- a call would
    degrade to `"failed"` and render the very notice #644 reported), the
    sole object is written, and no judge degrade notice appears."""
    _init_workspace(tmp_path, monkeypatch)
    run1 = _concept_reply(title="Stoic Dichotomy Of Control")
    run2 = _concept_reply(title="Stoic Dichotomy Of Control")
    fake = _patch_sequenced_llm(monkeypatch, [run1, run2])
    source = tmp_path / "notes.txt"
    source.write_text("Some raw notes about self-control.", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    assert len(fake.calls) == 2
    assert "judge selection unavailable" not in result.stderr
    assert "judge reply matched no candidate" not in result.stderr
    concept_path = tmp_path / "bundle" / "concepts" / "stoic-dichotomy-of-control.md"
    assert concept_path.is_file()


def test_ingest_base_extraction_ollama_error_still_source_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Task 3.8: `llm.chat` raises during the BASE extraction call itself
    (not the judge) -- behavior is unchanged from the existing Source-only
    degrade, even under the union+judge product default."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_sequenced_llm(monkeypatch, [OllamaUnavailable("boom")])
    source = tmp_path / "notes.txt"
    source.write_text("Some raw notes about self-control.", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    assert "concept extraction skipped" in result.stderr
    assert "keeping the Source only" in result.stderr
    concept_dir = tmp_path / "bundle" / "concepts"
    assert not concept_dir.exists() or list(concept_dir.iterdir()) == []
    source_path = tmp_path / "bundle" / "sources" / "notes.md"
    assert source_path.is_file()


def test_ingest_union_judge_backstop_writes_no_more_than_20_derived_objects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A union+judge selection yielding more than 20 valid objects writes
    no more than 20 derived files -- the `_UNION_BACKSTOP` cap, raised from
    12 by #564 after it bound twice on genuine content-rich sources (15 and
    17 judge-approved objects) and truncated them by position."""
    _init_workspace(tmp_path, monkeypatch)

    def item(i: int) -> str:
        return (
            f'{{"type": "Concept", "title": "Subject {i}", '
            f'"description": "Distinct subject {i}.", "body": "Body {i}."}}'
        )

    run_reply = "[" + ", ".join(item(i) for i in range(1, 24)) + "]"  # 23 distinct
    keep_reply = '{"keep": [' + ", ".join(f'"Subject {i}"' for i in range(1, 24)) + "]}"
    _patch_sequenced_llm(monkeypatch, [run_reply, run_reply, keep_reply])
    source = tmp_path / "notes.txt"
    source.write_text("A long document about many topics.", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    concept_dir = tmp_path / "bundle" / "concepts"
    written = list(concept_dir.glob("*.md")) if concept_dir.exists() else []
    assert len(written) == 20


def test_ingest_pre_judge_ceiling_drop_is_reported_on_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A union run whose merged candidates exceed the 24-candidate
    pre-judge ceiling reports the drop on stderr
    (`_pre_judge_ceiling_notice`), with wording distinct from the judge
    notices and from the final `cap reached` backstop notice -- those
    describe judged or cap-discarded titles, while these candidates never
    reached the judge at all."""
    _init_workspace(tmp_path, monkeypatch)

    def item(i: int) -> str:
        return (
            f'{{"type": "Concept", "title": "Subject {i}", '
            f'"description": "Distinct subject {i}.", "body": "Body {i}."}}'
        )

    run1 = "[" + ", ".join(item(i) for i in range(1, 26)) + "]"  # 25 distinct
    keep_reply = '{"keep": [' + ", ".join(f'"Subject {i}"' for i in range(1, 25)) + "]}"
    _patch_sequenced_llm(monkeypatch, [run1, "[]", keep_reply])
    source = tmp_path / "notes.txt"
    source.write_text("A long document about many topics.", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    ceiling_lines = [
        line for line in result.stderr.splitlines() if "pre-judge ceiling" in line
    ]
    assert len(ceiling_lines) == 1
    assert "24-candidate" in ceiling_lines[0]
    assert "1 merged candidate(s) never reached the judge" in ceiling_lines[0]
    for other_notice_marker in ("cap reached", "judge dropped", "judge selection"):
        assert other_notice_marker not in ceiling_lines[0]


def test_healthy_ingest_builds_the_source_document_exactly_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On the healthy (successful-extraction) path, `ingest` calls
    `okf.build_source_concept` exactly once -- the conditional re-render
    only fires when `_stage_derived_objects` returns a `skip_reason`
    (design: "The ordering conflict", conditional re-render; healthy path
    stays byte-identical to before `extraction_status` existed)."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, _concept_reply())
    calls: list[dict[str, object]] = []
    real_build = okf.build_source_concept

    def _spy(**kwargs: object) -> str:
        calls.append(kwargs)
        return real_build(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(okf, "build_source_concept", _spy)
    source = tmp_path / "notes.txt"
    source.write_text("Some raw notes about self-control.", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    assert len(calls) == 1
    concept_text = (tmp_path / "bundle" / "sources" / "notes.md").read_text(
        encoding="utf-8"
    )
    assert "extraction_status" not in concept_text


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


def test_extracted_title_with_link_delimiter_is_neutralized_not_forged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An LLM-extracted title carrying a markdown link delimiter (`]`) would
    forge/break the catalog bullet's first link in `index.md`/`log.md`,
    making the real entry unremovable and letting a forged id delete another
    entry. The delimiter is NEUTRALIZED (`[`/`]` -> `(`/`)`), not dropped: the
    concept is still extracted, no forged `/concepts/other.md` link reaches
    the catalog or the log, and the entry stays removable by its real id."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(
        monkeypatch,
        _concept_reply(title="Payment Policy](/concepts/other.md) x"),
    )
    source = tmp_path / "notes.txt"
    source.write_text("Some notes about a payment policy.", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0, result.output
    index_text = (tmp_path / "bundle" / "index.md").read_text(encoding="utf-8")
    log_text = (tmp_path / "bundle" / "log.md").read_text(encoding="utf-8")
    # The candidate IS kept, under a neutralized (bracket-free) title.
    concept_files = list((tmp_path / "bundle" / "concepts").glob("*.md"))
    assert len(concept_files) == 1, "the candidate is preserved, not dropped"
    metadata, _ = okf.load_frontmatter(concept_files[0].read_text(encoding="utf-8"))
    stored_title = str(metadata["title"])
    assert "]" not in stored_title
    assert "[" not in stored_title
    # Security property is FUNCTIONAL: the forged target string may survive as
    # inert label text, but it must not be a real link. The real entry is
    # removable by its own id, and the forged `concepts/other` id resolves to
    # nothing -- so the injection can neither hide the real bullet nor forge a
    # deletion of a different one, in either the catalog or the log.
    concept_id = f"concepts/{concept_files[0].stem}"
    _, removed_real = bundle_index.remove_index_entry(index_text, concept_id)
    assert removed_real == 1, "the neutralized entry remains removable by its id"
    _, forged_index = bundle_index.remove_index_entry(index_text, "concepts/other")
    assert forged_index == 0, "the forged target is not a functional catalog link"
    _, forged_log = bundle_log.remove_log_entry(log_text, "concepts/other")
    assert forged_log == 0, "the forged target is not a functional log link"
    assert okf.check_conformance(tmp_path / "bundle") == []


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
    # 2 extraction runs (#456); one merged candidate skips the judge (#644).
    assert len(fake.calls) == 2
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


# --- `extraction_status` frontmatter stamping (issue #187, spec: Extraction
# Status Frontmatter Key on Zero-Derived-Object Degrade) -------------------


def test_no_extractable_text_writes_extraction_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty source writes `extraction_status: no-extractable-text` on
    the Source concept (spec: no-extractable-text is written; path
    main.py:1298)."""
    _init_workspace(tmp_path, monkeypatch)
    source = tmp_path / "notes.txt"
    source.write_text("   \n  ", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    concept_path = tmp_path / "bundle" / "sources" / "notes.md"
    metadata, _ = okf.load_frontmatter(concept_path.read_text(encoding="utf-8"))
    assert metadata["extraction_status"] == "no-extractable-text"


def test_blocked_by_sensitivity_writes_extraction_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `default_sensitivity: confidential` floor writes `extraction_status:
    blocked-by-sensitivity` on the Source concept (spec: blocked-by-
    sensitivity is written; path main.py:1305)."""
    _init_workspace(tmp_path, monkeypatch)
    _set_config_field(
        tmp_path, "default_sensitivity: private", "default_sensitivity: confidential"
    )
    _patch_llm(monkeypatch, _concept_reply())
    source = tmp_path / "notes.txt"
    source.write_text("Some raw notes about self-control.", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    concept_path = tmp_path / "bundle" / "sources" / "notes.md"
    metadata, _ = okf.load_frontmatter(concept_path.read_text(encoding="utf-8"))
    assert metadata["extraction_status"] == "blocked-by-sensitivity"


def test_failed_extraction_writes_extraction_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An `OllamaError`-raising LLM backend writes `extraction_status:
    failed` on the Source concept, and the raw exception text never appears
    in the frontmatter (spec: failed is written, MUST NOT write raw
    exception text; path main.py:1316)."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(
        monkeypatch,
        raises=OllamaUnavailable("boom -- http://127.0.0.1:11434 model llama3"),
    )
    source = tmp_path / "notes.txt"
    source.write_text("Some raw notes about self-control.", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    concept_path = tmp_path / "bundle" / "sources" / "notes.md"
    concept_text = concept_path.read_text(encoding="utf-8")
    metadata, _ = okf.load_frontmatter(concept_text)
    assert metadata["extraction_status"] == "failed"
    assert "boom" not in concept_text
    assert "127.0.0.1" not in concept_text


def test_no_concepts_found_writes_extraction_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A successful `llm.chat` call that declines extraction writes
    `extraction_status: no-concepts-found` on the Source concept (spec:
    no-concepts-found is written; path main.py:1329)."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, '{"extract": false}')
    source = tmp_path / "notes.txt"
    source.write_text("Some raw notes about self-control.", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    concept_path = tmp_path / "bundle" / "sources" / "notes.md"
    metadata, _ = okf.load_frontmatter(concept_path.read_text(encoding="utf-8"))
    assert metadata["extraction_status"] == "no-concepts-found"


def test_successful_extraction_writes_no_extraction_status_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A successful extraction (>=1 derived object written) leaves
    `extraction_status` entirely absent from the Source concept's
    frontmatter (spec: Successful extraction writes no key at all)."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, _concept_reply())
    source = tmp_path / "notes.txt"
    source.write_text("Some raw notes about self-control.", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    concept_path = tmp_path / "bundle" / "sources" / "notes.md"
    concept_text = concept_path.read_text(encoding="utf-8")
    assert "extraction_status" not in concept_text
    derived_path = tmp_path / "bundle" / "concepts" / "stoic-dichotomy-of-control.md"
    assert derived_path.is_file()


def test_successful_reingest_clears_a_previous_failed_extraction_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Source previously marked `extraction_status: failed` ends with the
    key ABSENT after a re-ingest that succeeds (top functional risk, spec:
    A previously failed Source self-clears on later success; design:
    Self-clearing). The first assertion after the second run is the
    anti-merge guard: any implementation that read the on-disk value and
    merged it forward leaves the key present and fails here."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, raises=OllamaUnavailable("boom"))
    source = tmp_path / "notes.txt"
    source.write_text("Some raw notes about self-control.", encoding="utf-8")

    first = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert first.exit_code == 0
    concept_path = tmp_path / "bundle" / "sources" / "notes.md"
    metadata, _ = okf.load_frontmatter(concept_path.read_text(encoding="utf-8"))
    assert metadata["extraction_status"] == "failed"

    _patch_llm(monkeypatch, _concept_reply())

    second = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert second.exit_code == 0
    concept_text = concept_path.read_text(encoding="utf-8")
    assert "extraction_status" not in concept_text
    derived_path = tmp_path / "bundle" / "concepts" / "stoic-dichotomy-of-control.md"
    assert derived_path.is_file()


def test_unrecognized_extraction_status_value_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Source whose on-disk `extraction_status` is outside the closed
    vocabulary (e.g. a value from a future or reverted version) is read
    without raising -- `extraction_status` is never read from disk by
    `ingest` in the first place, so a re-ingest simply recomputes and
    overwrites it silently (spec: Unrecognized value is ignored without
    raising)."""
    _init_workspace(tmp_path, monkeypatch)
    source = tmp_path / "notes.txt"
    source.write_text("Some raw notes about self-control.", encoding="utf-8")
    first = runner.invoke(app, ["ingest", "notes.txt", "--auto"])
    assert first.exit_code == 0
    concept_path = tmp_path / "bundle" / "sources" / "notes.md"
    metadata, body = okf.load_frontmatter(concept_path.read_text(encoding="utf-8"))
    metadata["extraction_status"] = "some-future-value"
    concept_path.write_text(okf.dump_frontmatter(metadata, body), encoding="utf-8")
    _patch_llm(monkeypatch, _concept_reply())

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    concept_text = concept_path.read_text(encoding="utf-8")
    assert "extraction_status" not in concept_text


def test_sensitivity_and_extraction_status_independent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In ONE run, `sensitivity` IS read from disk and combined via
    `okf.combine_sensitivity` (#229) while `extraction_status` is NEVER
    read from disk, only freshly computed for this run (design: "Two
    fields, two opposite rules", cross-guard test #10). An on-disk
    `confidential` Source, config `default_sensitivity: private`, and an
    `OllamaError`-raising LLM backend must end with BOTH `sensitivity ==
    'confidential'` (preserved, never downgraded) AND `extraction_status ==
    'failed'` (this run's own outcome) in the same rewritten document."""
    _init_workspace(tmp_path, monkeypatch)
    source = tmp_path / "notes.txt"
    source.write_text("Some raw notes about self-control.", encoding="utf-8")
    first = runner.invoke(app, ["ingest", "notes.txt", "--auto"])
    assert first.exit_code == 0
    _set_source_sensitivity(tmp_path, "notes", "confidential")
    _patch_llm(monkeypatch, raises=OllamaUnavailable("boom"))

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    concept_path = tmp_path / "bundle" / "sources" / "notes.md"
    metadata, _ = okf.load_frontmatter(concept_path.read_text(encoding="utf-8"))
    assert metadata["sensitivity"] == "confidential"
    assert metadata["extraction_status"] == "failed"


def test_reingest_raises_when_workspace_default_exceeds_on_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A raised `default_sensitivity` still raises a Source on re-ingest
    when it exceeds the on-disk value -- the high-water-mark, not a frozen
    read-and-reuse (design: "(b) dominates (a)"). A final re-ingest then
    lowers the config default back below the just-raised on-disk value: the
    Source must stay at the raised level, not follow the config default
    back down. A pre-fix implementation that writes
    `cfg.default_sensitivity` unconditionally, ignoring the on-disk value,
    would downgrade it to `public` at that final step, so the expected
    outcome is not a bare pass-through of the config default."""
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

    _set_config_field(
        tmp_path, "default_sensitivity: private", "default_sensitivity: public"
    )

    final = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert final.exit_code == 0
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


def test_reingest_with_undecodable_concept_names_the_snapshot_roles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pre-regenerate concept read is ONE `_snapshot_read` observation
    feeding THREE consumers -- the sensitivity high-water mark, the retitle
    preview, and the drift guard's byte baseline (#318) -- so its failure
    message must name that whole role, not just sensitivity (#313 review,
    R2+R3 wave 1: a message claiming the read exists "to resolve its
    existing sensitivity" invited a future edit to move or narrow it as if
    only sensitivity depended on it). Same error surface as before: caught,
    `refusing to ingest`, exit 1, on-disk bytes untouched."""
    _init_workspace(tmp_path, monkeypatch)
    source = tmp_path / "notes.txt"
    source.write_text("Some raw notes about self-control.", encoding="utf-8")
    first = runner.invoke(app, ["ingest", "notes.txt", "--auto"])
    assert first.exit_code == 0
    concept_path = tmp_path / "bundle" / "sources" / "notes.md"
    undecodable = b"\xff\xfe not valid utf-8 \x00\x01"
    concept_path.write_bytes(undecodable)

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 1
    assert "refusing to ingest" in result.stderr
    assert (
        "could not be read to snapshot its current contents "
        "(sensitivity, title, and drift baseline)"
    ) in result.stderr
    assert concept_path.read_bytes() == undecodable


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


def test_reingest_with_non_string_on_disk_sensitivity_fails_closed_to_confidential(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-string on-disk `sensitivity` value (an `int`, here) is the
    second disjunct of the "Unrecognized or non-string on-disk sensitivity
    fails closed to confidential" scenario. Only the unrecognized-string
    disjunct (`"secret"`) was previously exercised at the `ingest`
    integration level; `okf._rank`'s non-string handling itself already had
    a primitive-level unit test, but nothing forged a genuinely non-string
    value through the real CLI path. This is the fail-closed escalation
    branch of a security field, so it is proven end-to-end here, not only
    at the primitive level."""
    _init_workspace(tmp_path, monkeypatch)
    source = tmp_path / "notes.txt"
    source.write_text("Some raw notes about self-control.", encoding="utf-8")
    first = runner.invoke(app, ["ingest", "notes.txt", "--auto"])
    assert first.exit_code == 0
    _set_source_sensitivity(tmp_path, "notes", 42)

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
    """A batch of 5 valid, non-colliding, non-existing candidates are ALL
    staged and written -- `main.py` never re-caps; `concept.py`'s
    `_MAX_OBJECTS_PER_SOURCE` is the only ceiling (Phase 11; spec: "LLM
    proposes more than CAP objects" / "Multiple distinct objects extracted,
    under cap").

    5 is now UNDER the cap rather than exactly at it (#404 raised it to 6),
    which leaves this test checking what it was named for: that no second
    ceiling hides in `main.py`. The at-the-cap case is covered by
    `tests/unit/extraction/test_concept.py`."""
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
        self.updates: list[str] = []

    def __enter__(self) -> "_FakeStatus":
        self.entered = True
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.exited = True

    def update(self, text: str) -> None:
        """Rich's own in-place status rewrite, spied (#701): `ingest` routes
        the extractor's phase labels here so the counter REPLACES the static
        line instead of scrolling underneath the live spinner."""
        self.updates.append(text)


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


def test_family_matches_an_nfd_spelled_filename(tmp_path: Path) -> None:
    """The family scan compares NFC-normalized stems, so a member stored on
    disk in NFD still joins the family of its NFC slug (#414).

    `_slugify` emits NFC, but HFS+ (and some SMB mounts) rewrite a filename
    to NFD on write, and APFS preserves whatever spelling it is handed -- so
    `link_dir.glob` can legitimately return the NFD stem for a file created
    under the NFC slug. Without normalization the anchored regex misses it
    while `derived_path.exists()` (normalization-INSENSITIVE on macOS) still
    reports True. The caller would then read an EMPTY family, conclude the
    slug belongs to a foreign source, and disambiguate to `<slug>-2` on
    every single re-ingest -- until `write_exclusive` finally raised
    `FileExistsError`. Idempotent re-ingest depends on this match."""
    link_dir = tmp_path / "concepts"
    link_dir.mkdir()
    nfc = unicodedata.normalize("NFC", "diseño")
    nfd = unicodedata.normalize("NFD", "diseño")
    assert nfc != nfd
    (link_dir / f"{nfd}.md").write_text("body", encoding="utf-8")
    (link_dir / f"{nfd}-2.md").write_text("body", encoding="utf-8")

    family = main._collision_family(link_dir, nfc)

    assert [unicodedata.normalize("NFC", path.stem) for path in family] == [
        nfc,
        f"{nfc}-2",
    ]


def test_first_free_disambiguated_slug_sees_an_nfd_family_member(
    tmp_path: Path,
) -> None:
    """`_first_free_disambiguated_slug` compares family stems as strings, so
    it too must normalize: an on-disk NFD `<slug>-2.md` has to count as taken
    or the disambiguation loop would hand back a name that already exists
    (#414)."""
    link_dir = tmp_path / "concepts"
    link_dir.mkdir()
    nfc = unicodedata.normalize("NFC", "diseño")
    nfd = unicodedata.normalize("NFD", "diseño")
    (link_dir / f"{nfd}-2.md").write_text("body", encoding="utf-8")
    family = [link_dir / f"{nfd}-2.md"]

    assert main._first_free_disambiguated_slug(family, nfc, set()) == f"{nfc}-3"


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


# --- Title derivation from content (source-title-from-heading) -------------
#
# `title` is derived from `raw_content` via `source_title.derive_source_title`
# and feeds the frontmatter `title`, the Source document's own `# ` heading,
# the `index.md` bullet label, and the `log.md` entry label -- one assignment,
# several consumers (design: "Call-site wiring in `ingest`").


def _read_source(tmp_path: Path, slug: str) -> tuple[dict[str, object], str]:
    concept_path = tmp_path / "bundle" / "sources" / f"{slug}.md"
    return okf.load_frontmatter(concept_path.read_text(encoding="utf-8"))


def test_first_atx_h1_becomes_the_title(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A first non-fenced `# ` line becomes the title everywhere: frontmatter
    `title`, the Source's own `# ` heading, the `index.md` bullet, and the
    `log.md` entry (spec: "First ATX H1 becomes the title")."""
    _init_workspace(tmp_path, monkeypatch)
    source = tmp_path / "notes.txt"
    source.write_text(
        "# Introduction to Stoicism\n\nSome body text.\n", encoding="utf-8"
    )

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0, result.stdout
    metadata, body = _read_source(tmp_path, "notes")
    assert metadata["title"] == "Introduction to Stoicism"
    assert "# Introduction to Stoicism" in body.splitlines()
    index_text = (tmp_path / "bundle" / "index.md").read_text(encoding="utf-8")
    assert "[Introduction to Stoicism]" in index_text
    log_text = (tmp_path / "bundle" / "log.md").read_text(encoding="utf-8")
    assert "[Introduction to Stoicism](/sources/notes.md)" in log_text


def test_h1_inside_a_fenced_block_is_ignored_later_real_h1_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An H1 fenced inside a code block does not become the title; the
    first real H1 outside any fence wins (spec: "An H1 inside a fenced code
    block is ignored")."""
    _init_workspace(tmp_path, monkeypatch)
    source = tmp_path / "notes.txt"
    source.write_text(
        "```\n# Not a title\n```\n\n# Chapter One\n\nBody text.\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0, result.stdout
    metadata, _ = _read_source(tmp_path, "notes")
    assert metadata["title"] == "Chapter One"


def test_no_h1_title_plausible_first_line_is_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no H1 anywhere, a title-plausible first line (followed by a
    blank line) becomes the title (spec: "No H1, a title-plausible first
    line is used")."""
    _init_workspace(tmp_path, monkeypatch)
    source = tmp_path / "notes.txt"
    source.write_text(
        "Call with Maria Salazar — 2026-07-14\n\nMeeting notes follow.\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0, result.stdout
    metadata, _ = _read_source(tmp_path, "notes")
    assert metadata["title"] == "Call with Maria Salazar — 2026-07-14"


def test_wrapped_prose_first_line_falls_back_to_slug_title(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A first line that is the start of a wrapped prose paragraph (no
    blank line immediately after it) is not title-plausible; the title
    falls back to `_titleize(src.stem)` (spec: "Wrapped prose first line is
    not title-plausible")."""
    _init_workspace(tmp_path, monkeypatch)
    source = tmp_path / "notes.txt"
    source.write_text(
        "This paragraph keeps going\non the next physical line.\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0, result.stdout
    metadata, _ = _read_source(tmp_path, "notes")
    assert metadata["title"] == "notes"


def test_titleize_fallback_still_works_after_delegation_to_source_titles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`_titleize` now delegates to `bundle.source_titles.titleize`
    (regression for task 1.4's promotion): a `None`-derived title still
    falls back to the same hyphen-to-space title as before."""
    _init_workspace(tmp_path, monkeypatch)
    source = tmp_path / "01-Introduction.txt"
    source.write_text(
        "This paragraph keeps going\non the next line.\n", encoding="utf-8"
    )

    result = runner.invoke(app, ["ingest", "01-Introduction.txt", "--auto"])

    assert result.exit_code == 0, result.stdout
    metadata, _ = _read_source(tmp_path, "01-introduction")
    assert metadata["title"] == "01 Introduction"


def test_forbidden_character_candidate_falls_back_to_slug_title(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A candidate that, after normalization, carries a forbidden character
    falls back to the slug title (spec: "A candidate carrying a forbidden
    character falls back"). An UNBALANCED bracket (#592): a balanced
    `[Draft]` span now strips to a valid title instead of falling back."""
    _init_workspace(tmp_path, monkeypatch)
    source = tmp_path / "notes.txt"
    source.write_text("# Draft ] Notes\n\nBody text.\n", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0, result.stdout
    metadata, _ = _read_source(tmp_path, "notes")
    assert metadata["title"] == "notes"


def test_candidate_over_120_chars_falls_back_to_slug_title_no_truncation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A normalized candidate exceeding 120 characters falls back to the
    slug title, with no truncation of the over-long candidate (spec: "A
    candidate over 120 characters falls back")."""
    _init_workspace(tmp_path, monkeypatch)
    source = tmp_path / "notes.txt"
    source.write_text(f"# {'A' * 121}\n\nBody text.\n", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0, result.stdout
    metadata, _ = _read_source(tmp_path, "notes")
    assert metadata["title"] == "notes"


def test_well_formed_frontmatter_block_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A well-formed leading `---`...`---` frontmatter block is skipped, its
    own `title:` key is not read, and a later real H1 becomes the title
    (spec: "A well-formed leading frontmatter block is skipped")."""
    _init_workspace(tmp_path, monkeypatch)
    source = tmp_path / "notes.txt"
    source.write_text(
        "---\ntitle: Ignored Frontmatter Title\n---\n\n# Chapter One\n\nBody.\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0, result.stdout
    metadata, _ = _read_source(tmp_path, "notes")
    assert metadata["title"] == "Chapter One"


def test_unclosed_leading_dashes_are_treated_as_content_falls_back_to_slug(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A leading `---` with no later closing `---` anywhere in the file is
    evaluated as an ordinary candidate line, fails the title-plausible
    predicate (begins with block syntax), and the title falls back to the
    slug (spec: "An unclosed leading `---` is treated as content")."""
    _init_workspace(tmp_path, monkeypatch)
    source = tmp_path / "notes.txt"
    source.write_text("---\nno closing dashes anywhere\n", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0, result.stdout
    metadata, _ = _read_source(tmp_path, "notes")
    assert metadata["title"] == "notes"


def test_binary_source_never_calls_derivation_keeps_slug_title(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A source that does not decode as UTF-8 never invokes
    `derive_source_title`; the title stays the slug (spec: "A binary source
    uses the slug title")."""
    _init_workspace(tmp_path, monkeypatch)
    calls: list[str] = []

    def _spy(raw_content: str) -> str:
        calls.append(raw_content)
        return "Should Never Appear"

    monkeypatch.setattr("openkos.source_title.derive_source_title", _spy)
    source = tmp_path / "notes.bin"
    source.write_bytes(b"\xff\xfe\x00\x01binary content")

    result = runner.invoke(app, ["ingest", "notes.bin", "--auto"])

    assert result.exit_code == 0, result.stdout
    metadata, _ = _read_source(tmp_path, "notes")
    assert metadata["title"] == "notes"
    assert calls == []


@pytest.mark.parametrize(
    "content",
    ["", "   \n\n   "],
    ids=["truly-empty", "whitespace-only"],
)
def test_blank_source_never_calls_derivation_keeps_slug_title(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content: str
) -> None:
    """A blank OR whitespace-only decoded source must never invoke
    `derive_source_title` at all (spec: "An empty source uses the slug
    title" -- title derivation MUST NOT run for blank/whitespace-only
    content, the same as it must not run for undecodable/binary content).

    This replaces the former `test_empty_source_never_calls_derivation_keeps_slug_title`,
    which only asserted the FINAL title -- a check that passes whether or
    not the helper actually ran, since `derive_source_title("")` already
    returns `None` on its own. A test whose NAME claims non-invocation but
    never checks it is worse than no test: it looks like coverage for the
    call-site guard while actually providing none. Mirrors
    `test_binary_source_never_calls_derivation_keeps_slug_title`'s
    monkeypatch-and-record-calls shape, parametrized over both blank
    shapes (zero-length and whitespace-only) rather than duplicated."""
    _init_workspace(tmp_path, monkeypatch)
    calls: list[str] = []

    def _spy(raw_content: str) -> str:
        calls.append(raw_content)
        return "Should Never Appear"

    monkeypatch.setattr("openkos.source_title.derive_source_title", _spy)
    source = tmp_path / "notes.txt"
    source.write_text(content, encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0, result.stdout
    metadata, _ = _read_source(tmp_path, "notes")
    assert metadata["title"] == "notes"
    assert calls == []


def test_reingest_of_identical_bytes_writes_a_byte_identical_source_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Title derivation is a pure function of `raw_content`: re-ingesting a
    byte-identical raw file produces a byte-identical Source document,
    including its derived `title` (spec: "Idempotent Title Derivation" /
    "Byte-identical re-ingest yields a byte-identical Source"). Reuses the
    `_FixedClock` monkeypatch pattern (`:2385-2390`) because `timestamp` is
    refreshed on every re-ingest, and reuses the assertion shape of
    `test_reingest_with_equal_values_writes_byte_identical_output`
    (`:2401`) so the diff is limited to the always-refreshing field."""
    _init_workspace(tmp_path, monkeypatch)
    source = tmp_path / "notes.txt"
    source.write_text("# Chapter One\n\nBody text.\n", encoding="utf-8")
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
    assert metadata["title"] == "Chapter One"
    before_lines = before.splitlines()
    after_lines = after.splitlines()
    changed = [(b, a) for b, a in zip(before_lines, after_lines, strict=True) if b != a]
    assert changed
    assert all("timestamp" in b for b, _ in changed)


def test_stage_derived_objects_receives_the_final_derived_title(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`_stage_derived_objects(source_title=title)` -- and therefore
    `extraction/concept.py`'s `SOURCE TITLE:` prompt line -- receives the
    FINAL derived title (the H1 value), not the slug, when a candidate is
    accepted (design: "the easy-to-miss one" / task 3.1)."""
    _init_workspace(tmp_path, monkeypatch)
    fake = _patch_llm(monkeypatch, _concept_reply())
    source = tmp_path / "notes.txt"
    source.write_text("# Introduction to Stoicism\n\nBody text.\n", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0, result.stdout
    assert fake.calls, "the extraction prompt should have been sent"
    user_message = fake.calls[0][1]
    title_line = user_message["content"].splitlines()[0]
    assert title_line.startswith("SOURCE TITLE")
    assert title_line.endswith("Introduction to Stoicism")


# --- Re-ingest preview names a silent title change (review finding) --------
#
# Re-ingest is a documented full regeneration -- only `sensitivity` carries
# forward, everything else (including `title`) is rebuilt from content every
# run (spec: "Idempotent Re-Ingest Reconciles Derived Objects Per Slug",
# "Default Sensitivity from Config"). A byte-identical re-ingest of a Source
# created before issue #248 (or hand-retitled since) recomputes `title` from
# content and silently overwrites the on-disk title in the frontmatter, the
# document's own `# ` heading, and the `index.md`/`log.md` link labels -- the
# regenerate preview never mentioned it. The fix is NOT to make the title
# sticky (that would contradict "re-ingest rebuilds everything but
# sensitivity" and would be a worse change); the fix is to make the preview
# say so, mirroring the existing sensitivity clause on the same line.


def _set_source_title(tmp_path: Path, slug: str, value: str) -> None:
    """Directly rewrite an existing Source concept's on-disk `title`
    frontmatter field, simulating a Source whose title predates content
    derivation (or was hand-edited since) -- mirrors
    `_set_source_sensitivity`'s shape (`:2073`)."""
    concept_path = tmp_path / "bundle" / "sources" / f"{slug}.md"
    text = concept_path.read_text(encoding="utf-8")
    metadata, body = okf.load_frontmatter(text)
    metadata["title"] = value
    concept_path.write_text(okf.dump_frontmatter(metadata, body), encoding="utf-8")


def test_reingest_preview_names_a_title_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the derived title differs from the title already on disk, the
    re-ingest preview's regenerated-Source line names BOTH the old and new
    title -- the user must not be asked to confirm a retitle it was never
    told about."""
    _init_workspace(tmp_path, monkeypatch)
    source = tmp_path / "notes.txt"
    source.write_text("# Chapter One\n\nBody text.\n", encoding="utf-8")
    first = runner.invoke(app, ["ingest", "notes.txt", "--auto"])
    assert first.exit_code == 0
    _set_source_title(tmp_path, "notes", "Legacy Slug Title")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0, result.stdout
    assert "title changed from 'Legacy Slug Title' to 'Chapter One'" in result.stdout


def test_reingest_preview_omits_title_clause_when_title_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The common path -- a re-ingest whose derived title matches the title
    already on disk -- must say NOTHING about the title; noise on the
    unchanged path is its own defect (mirrors the existing sensitivity
    "unchanged" clause's restraint -- but here, restraint means silence, not
    a clause)."""
    _init_workspace(tmp_path, monkeypatch)
    source = tmp_path / "notes.txt"
    source.write_text("# Chapter One\n\nBody text.\n", encoding="utf-8")
    first = runner.invoke(app, ["ingest", "notes.txt", "--auto"])
    assert first.exit_code == 0

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0, result.stdout
    assert "title changed" not in result.stdout


# -- #313: re-validate every write target after the confirm gate ------------


def _ingested_source_on_a_tty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A workspace with `notes.txt` already ingested, on a TTY, positioned
    for a RE-INGEST -- the only `ingest` shape that overwrites an existing
    concept (`write_atomic` on the regenerate branch) rather than creating
    one."""
    _init_workspace(tmp_path, monkeypatch)
    (tmp_path / "notes.txt").write_text("Some raw notes.", encoding="utf-8")
    assert runner.invoke(app, ["ingest", "notes.txt", "--auto"]).exit_code == 0
    _simulate_tty(monkeypatch)


@pytest.mark.parametrize(
    "target",
    ["bundle/sources/notes.md", "bundle/index.md", "bundle/log.md"],
)
def test_a_write_target_edited_during_the_prompt_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    """#313: a re-ingest renders the concept, `index.md` and `log.md` from a
    pre-prompt read and then writes those exact bytes with `write_atomic`,
    so an edit landing while the operator reads the preview was overwritten
    in full and auto-committed.

    Only these three are at risk. The raw copy and the derived objects go
    through `copy_exclusive`/`write_exclusive`, which already fail closed on
    a concurrent create -- guarding them would be redundant, and NOT
    guarding these three is what leaves the verb uneven.
    """
    _ingested_source_on_a_tty(tmp_path, monkeypatch)
    target_path = tmp_path / target
    concurrent = "hand-edited while the prompt waited\n"
    before = snapshot_with_mtime(tmp_path)
    confirm_after(
        monkeypatch, lambda: target_path.write_text(concurrent, encoding="utf-8")
    )

    result = runner.invoke(app, ["ingest", "notes.txt"], input="y\n")

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
    _ingested_source_on_a_tty(tmp_path, monkeypatch)
    deleted_path = tmp_path / "bundle" / "sources" / "notes.md"
    before = snapshot_with_mtime(tmp_path)
    confirm_after(monkeypatch, deleted_path.unlink)

    result = runner.invoke(app, ["ingest", "notes.txt"], input="y\n")

    assert result.exit_code == 3
    assert isinstance(result.exception, SystemExit)
    assert "bundle/sources/notes.md" in result.stderr
    assert not deleted_path.exists()
    after = snapshot_with_mtime(tmp_path)
    changed = changed_paths(before, after)
    assert changed == {Path("bundle/sources/notes.md")}


@pytest.mark.parametrize(
    "target",
    ["bundle/sources/notes.md", "bundle/index.md", "bundle/log.md"],
)
def test_a_crlf_rewrite_during_the_prompt_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    """#306's constraint, re-pinned for `ingest`: line-ending-only drift is
    still drift. `read_text` applies universal-newline translation, so a
    CRLF rewrite compares EQUAL to its own LF snapshot and the guard would
    wave it through -- then `fsio.write_atomic` (which opens with
    `newline=""`) puts the LF plan back over the operator's CRLF file.
    """
    _ingested_source_on_a_tty(tmp_path, monkeypatch)
    target_path = tmp_path / target
    concurrent = target_path.read_bytes().replace(b"\n", b"\r\n")
    assert concurrent != target_path.read_bytes()
    before = snapshot_with_mtime(tmp_path)
    confirm_after(monkeypatch, lambda: target_path.write_bytes(concurrent))

    result = runner.invoke(app, ["ingest", "notes.txt"], input="y\n")

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
    """The other direction: write targets that were ALREADY CRLF at rest,
    untouched by anyone, must not be reported as drift -- otherwise the
    verb refuses forever, naming a cause that never happened and a re-run
    that cannot clear it.
    """
    _ingested_source_on_a_tty(tmp_path, monkeypatch)
    for rel in ("bundle/sources/notes.md", "bundle/index.md", "bundle/log.md"):
        path = tmp_path / rel
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    assert "refusing to write" not in result.stderr


@pytest.mark.parametrize("target", ["bundle/index.md", "bundle/log.md"])
def test_drift_on_the_unprompted_path_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    """#313: the guard must run on `--auto` too.

    Every other drift test here reaches the gate through `typer.confirm`,
    so indenting the `_reject_drifted_targets` call into the
    `if not auto and cfg.review:` block would disable it for unattended
    runs and leave all of them green -- and `ingest --auto` is the shape
    most likely to be scripted against a bundle someone else is editing.
    """
    _init_workspace(tmp_path, monkeypatch)
    (tmp_path / "notes.txt").write_text("Some raw notes.", encoding="utf-8")
    target_path = tmp_path / target
    concurrent = "hand-edited while the preview printed\n"
    before = snapshot_with_mtime(tmp_path)
    hook = echo_after(
        monkeypatch,
        lambda: target_path.write_text(concurrent, encoding="utf-8"),
        trigger="(new dated entry)",
    )

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

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
    """#318's race, pinned for `ingest` (#327 follow-up; the pin existed
    only in `test_relate.py`): the guard's baseline and the text the new
    catalog is rendered from must come from the ONE `_snapshot_read`
    observation. Under a two-read shape, a writer landing between the
    text-read and the bytes-read becomes the guard's own baseline: the
    comparison finds no drift and Phase B writes the plan computed from the
    EARLIER text, silently reverting the edit and autocommitting the revert.

    The edit lands immediately after `index.md`'s snapshot returns -- the
    earliest a concurrent writer can now land relative to the plan -- and
    the guard's later re-read must call it drift and refuse the whole run.
    `test_a_concept_edited_during_the_llm_call_is_refused` covers the OTHER
    seam ingest alone has (the network call inside Phase A); this one
    covers the seam every guarded verb shares.
    """
    _ingested_source_on_a_tty(tmp_path, monkeypatch)
    target_path = tmp_path / "bundle" / "index.md"
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

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert fired, "the racing wrapper never saw the index.md snapshot"
    assert result.exit_code == 3
    assert isinstance(result.exception, SystemExit)
    assert "refusing to write --" in result.stderr
    assert "bundle/index.md" in result.stderr
    assert target_path.read_text(encoding="utf-8") == concurrent
    assert changed_paths(before, snapshot_with_mtime(tmp_path)) == {
        Path("bundle/index.md")
    }


def test_a_fresh_ingest_does_not_guard_the_concept_it_creates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A FIRST ingest must not enter the concept in the guard's mapping:
    there is no Phase-A snapshot of a file that did not exist, and claiming
    one would make every fresh ingest refuse on a path the operator was
    never shown.

    The create-only write is what protects this case instead --
    `write_exclusive` fails closed if the concept appeared meanwhile -- so
    this test pins that the guard stays out of its way.
    """
    _init_workspace(tmp_path, monkeypatch)
    (tmp_path / "notes.txt").write_text("Some raw notes.", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    assert "refusing to write" not in result.stderr
    assert (tmp_path / "bundle" / "sources" / "notes.md").is_file()


def test_a_concept_edited_during_the_llm_call_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#313 review, R4 CRITICAL: the concept's guard snapshot must be taken
    where Phase A READ the concept, not after extraction.

    `ingest` is the only guarded verb whose Phase A makes a network call.
    `_stage_derived_objects` sits between the reads that produce
    `resolved_sensitivity` and the point the other two snapshots are taken,
    so snapshotting the concept there would capture an edit landing during
    the LLM round trip as the guard's OWN baseline: the comparison finds no
    drift, and `write_atomic` then writes back the document built from the
    pre-call reads -- a silent sensitivity DOWNGRADE, auto-committed.

    The edit is landed from inside `chat()` because that is precisely the
    window; `confirm_after` fires later and cannot reach it.
    """
    _init_workspace(tmp_path, monkeypatch)
    (tmp_path / "notes.txt").write_text("Some raw notes.", encoding="utf-8")
    assert runner.invoke(app, ["ingest", "notes.txt", "--auto"]).exit_code == 0
    concept_path = tmp_path / "bundle" / "sources" / "notes.md"

    original = concept_path.read_text(encoding="utf-8")
    on_disk, _ = okf.load_frontmatter(original)
    top = okf.SENSITIVITY_ORDER[-1]
    assert str(on_disk["sensitivity"]) != top, (
        "the fixture must start BELOW the top rank, or the raise this test "
        "lands during the LLM call would be a no-op"
    )
    raised = original.replace(
        f"sensitivity: {on_disk['sensitivity']}", f"sensitivity: {top}"
    )
    assert f"sensitivity: {top}" in raised

    class _EditingLLM(_FakeLLM):
        def chat(self, messages: Sequence[Message]) -> str:
            concept_path.write_text(raised, encoding="utf-8")
            return super().chat(messages)

    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient",
        lambda *args, **kwargs: _EditingLLM('{"extract": false}'),
    )
    before = snapshot_with_mtime(tmp_path)

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 3
    assert "refusing to write --" in result.stderr
    assert "bundle/sources/notes.md" in result.stderr
    # The raise survives: this is the downgrade the guard exists to stop.
    assert f"sensitivity: {top}" in concept_path.read_text(encoding="utf-8")
    after = snapshot_with_mtime(tmp_path)
    changed = changed_paths(before, after)
    assert changed == {Path("bundle/sources/notes.md")}


# -- #322: a create landing at the concept path during the prompt -----------


def test_a_concept_created_during_the_prompt_on_a_post_forget_reingest_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#322: the guard's concept entry is gated on `had_prior_source`, so a
    post-`forget` re-ingest (`regenerate=True`, concept ABSENT at Phase A)
    enters the prompt with NO concept snapshot -- there were no bytes to
    take one of. `write_atomic` on that branch would then silently overwrite
    a file created at the concept path while the operator read the preview.
    The regenerate branch must write create-only (`write_exclusive`) exactly
    when the guard has no snapshot, so the two mechanisms tile the space:
    guard covers existed-at-Phase-A, exclusive-create covers did-not-exist.
    """
    _init_workspace(tmp_path, monkeypatch)
    (tmp_path / "notes.txt").write_text("Some raw notes.", encoding="utf-8")
    assert runner.invoke(app, ["ingest", "notes.txt", "--auto"]).exit_code == 0
    assert runner.invoke(app, ["forget", "sources/notes", "--auto"]).exit_code == 0
    concept_path = tmp_path / "bundle" / "sources" / "notes.md"
    assert not concept_path.exists()
    _simulate_tty(monkeypatch)

    concurrent = "created while the prompt waited\n"
    before = snapshot_with_mtime(tmp_path)
    confirm_after(
        monkeypatch, lambda: concept_path.write_text(concurrent, encoding="utf-8")
    )

    result = runner.invoke(app, ["ingest", "notes.txt"], input="y\n")

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    # Same failure surface as a concurrent create on a FRESH ingest:
    # `write_exclusive` raises `FileExistsError`, reported by the Phase-B
    # error path, naming the colliding path.
    assert "failed while writing the ingest --" in result.stderr
    assert "bundle/sources/notes.md" in result.stderr
    # The concurrently created file survives byte-for-byte...
    assert concept_path.read_text(encoding="utf-8") == concurrent
    # ...and it is the ONLY change on disk: the concept write comes before
    # `index.md`/`log.md` (content before catalog, D3), so the failure
    # leaves the catalog untouched.
    after = snapshot_with_mtime(tmp_path)
    changed = changed_paths(before, after)
    assert changed == {Path("bundle/sources/notes.md")}


# --- Issue #190: TTY-gated stage notice before extraction -------------------


def test_ingest_prints_tty_gated_stage_notice_before_extraction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On a TTY, `ingest` prints ONE `openkos ingest: extracting derived
    objects (waiting on the LLM)...` stage notice to STDERR immediately
    before `_stage_derived_objects`' extraction call -- the single-call
    sibling of the per-item progress hooks (issue #190). STDOUT keeps the
    clean report."""
    _init_workspace(tmp_path, monkeypatch)
    _simulate_tty(monkeypatch)
    source = tmp_path / "notes.txt"
    source.write_text("Some raw notes.", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    assert (
        "openkos ingest: extracting derived objects (waiting on the LLM)..."
        in result.stderr
    )
    assert "waiting on the LLM" not in result.stdout


def test_ingest_stage_notice_is_silent_without_a_tty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without a TTY (`CliRunner`'s default), the stage notice never
    appears -- piped output stays byte-clean (issue #190)."""
    _init_workspace(tmp_path, monkeypatch)
    source = tmp_path / "notes.txt"
    source.write_text("Some raw notes.", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    assert "waiting on the LLM" not in result.stderr
    assert "waiting on the LLM" not in result.stdout


# --- Issue #267: batch ingest -- a directory or glob in one invocation ------


def _stage_ingested_raw(tmp_path: Path, name: str, content: str, origin: Path) -> None:
    """Stage `raw/<name>` together with the Source that OWNS it, recording
    `origin` as that Source's `origin_key`.

    Writing `raw/<name>` alone models a half-built workspace, not an
    already-ingested source. Since #552 the two are meaningfully different:
    a raw copy whose owning Source records no origin cannot be proven to be
    the file now being ingested, so it disambiguates rather than refuses.
    Tests that want the "same source, changed bytes" refusal must say which
    source, and this helper is how they say it."""
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir(exist_ok=True)
    (raw_dir / name).write_text(content, encoding="utf-8")
    slug = Path(name).stem
    sources_dir = tmp_path / "bundle" / "sources"
    sources_dir.mkdir(parents=True, exist_ok=True)
    (sources_dir / f"{slug}.md").write_text(
        okf.build_source_concept(
            title=slug,
            description=f"Raw source imported from '{origin}' as raw/{name}.",
            resource=f"raw/{name}",
            tags=[],
            timestamp="2026-08-12T00:00:00Z",
            sensitivity="private",
            provenance=[f"raw/{name}"],
            raw_content=content,
            origin_key=okf.origin_key_for(origin),
        ),
        encoding="utf-8",
    )


def _write_notes(tmp_path: Path, files: dict[str, str], subdir: str = "notes") -> Path:
    """Create `<tmp_path>/<subdir>/` and populate it with `files` (relative
    name -> content; nested names like `archive/setup.md` create their own
    parent directories), returning the directory. Shared fixture builder for
    the issue #267 batch-ingest scenarios below."""
    directory = tmp_path / subdir
    directory.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return directory


def test_batch_directory_ingests_every_file_sorted_by_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A directory argument ingests EVERY readable file directly inside it
    in one invocation, in sorted-name order regardless of creation order --
    never filesystem order, so `log.md` and the per-file commits are
    reproducible across machines -- and prints per-file outcome lines plus
    an aggregate summary (issue #267, scenario: directory arg, deterministic
    order). `--auto` skips the batch cost gate, so no `LLM call(s)` prompt
    appears; without a TTY the per-file `i/N` progress stays silent
    (issue #190 discipline)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_notes(tmp_path, {"b.txt": "Beta notes.", "a.txt": "Alpha notes."})

    result = runner.invoke(app, ["ingest", "notes", "--auto"])

    assert result.exit_code == 0
    assert (tmp_path / "raw" / "a.txt").read_text(encoding="utf-8") == "Alpha notes."
    assert (tmp_path / "raw" / "b.txt").read_text(encoding="utf-8") == "Beta notes."
    assert (tmp_path / "bundle" / "sources" / "a.md").is_file()
    assert (tmp_path / "bundle" / "sources" / "b.md").is_file()
    # Sorted order: a's outcome line precedes b's, though b was created first.
    a_line = result.stdout.index(f"+ {Path('notes') / 'a.txt'} -- ingested")
    b_line = result.stdout.index(f"+ {Path('notes') / 'b.txt'} -- ingested")
    assert a_line < b_line
    assert "2 ingested, 0 re-ingested, 0 skipped" in result.stdout
    assert "LLM call(s)" not in result.stderr
    assert "ingesting file" not in result.stderr


def test_batch_directory_is_non_recursive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A directory argument matches only files DIRECTLY inside it:
    subdirectories are ignored, never walked into -- recursion is available
    only via an explicit `**` glob (issue #267, scenario: non-recursive
    directory)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_notes(tmp_path, {"a.txt": "Alpha notes.", "sub/deep.md": "Deep notes."})

    result = runner.invoke(app, ["ingest", "notes", "--auto"])

    assert result.exit_code == 0
    assert (tmp_path / "raw" / "a.txt").is_file()
    assert not (tmp_path / "raw" / "deep.md").exists()
    assert not (tmp_path / "bundle" / "sources" / "deep.md").exists()
    assert "1 file(s)" in result.stdout


def test_batch_directory_skips_non_text_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Directory expansion keeps only text-source extensions (#568): a user
    pointing at a project folder must not ingest `.DS_Store`, lockfiles, or
    code into the bundle. The skips are disclosed up front -- one pre-flight
    line BEFORE the cost gate says what is actually about to be ingested."""
    _init_workspace(tmp_path, monkeypatch)
    _write_notes(
        tmp_path,
        {
            "a.md": "Alpha notes.",
            "b.txt": "Beta notes.",
            ".DS_Store": "binary junk",
            "script.py": "print('hi')",
            "uv.lock": "lockfile",
        },
    )

    result = runner.invoke(app, ["ingest", "notes", "--auto"])

    assert result.exit_code == 0
    assert (tmp_path / "raw" / "a.md").is_file()
    assert (tmp_path / "raw" / "b.txt").is_file()
    assert not (tmp_path / "raw" / ".DS_Store").exists()
    assert not (tmp_path / "raw" / "script.py").exists()
    assert not (tmp_path / "raw" / "uv.lock").exists()
    assert "2 file(s) matched; 3 skipped as non-text" in result.stderr
    assert "2 file(s)" in result.stdout


def test_batch_directory_of_only_non_text_refuses_and_says_why(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A directory holding ONLY non-text files refuses like an empty one
    (#568), but the pre-flight line explains WHY nothing matched -- without
    it the refusal reads as a bug when the directory is visibly full."""
    _init_workspace(tmp_path, monkeypatch)
    _write_notes(tmp_path, {"script.py": "print('hi')", ".gitignore": "*.log"})
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["ingest", "notes", "--auto"])

    assert result.exit_code == 1
    assert "0 file(s) matched; 2 skipped as non-text" in result.stderr
    assert "no files matched" in result.stderr
    assert _snapshot(tmp_path) == before


def test_batch_glob_applies_the_same_text_filter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Glob expansion applies the same allowlist as directory expansion
    (#568): `notes/*` over a mixed folder ingests the prose and skips the
    code, with the same pre-flight disclosure."""
    _init_workspace(tmp_path, monkeypatch)
    _write_notes(tmp_path, {"a.md": "Alpha notes.", "script.py": "print('hi')"})

    result = runner.invoke(app, ["ingest", str(Path("notes") / "*"), "--auto"])

    assert result.exit_code == 0
    assert (tmp_path / "raw" / "a.md").is_file()
    assert not (tmp_path / "raw" / "script.py").exists()
    assert "1 file(s) matched; 1 skipped as non-text" in result.stderr


def test_batch_all_text_directory_prints_no_skip_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the filter skipped nothing, no pre-flight line appears (#568) --
    an advisory that fires on the healthy path is noise, and the cost gate
    already names the matched count."""
    _init_workspace(tmp_path, monkeypatch)
    _write_notes(tmp_path, {"a.md": "Alpha notes.", "b.txt": "Beta notes."})

    result = runner.invoke(app, ["ingest", "notes", "--auto"])

    assert result.exit_code == 0
    assert "skipped as non-text" not in result.stderr


def test_explicit_single_file_still_ingests_any_extension(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An explicit single-file path bypasses the allowlist (#568): a user
    naming one exact file gets it ingested whatever its extension -- the
    filter guards EXPANSION, never an explicit choice."""
    _init_workspace(tmp_path, monkeypatch)
    source = tmp_path / "script.py"
    source.write_text("print('hi')\n", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "script.py", "--auto"])

    assert result.exit_code == 0
    assert (tmp_path / "raw" / "script.py").is_file()
    assert "skipped as non-text" not in result.stderr


def test_batch_empty_directory_refuses_nothing_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty directory matches nothing: clear message, exit 1, nothing
    written (issue #267, scenario: empty directory)."""
    _init_workspace(tmp_path, monkeypatch)
    (tmp_path / "notes").mkdir()
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["ingest", "notes", "--auto"])

    assert result.exit_code == 1
    assert "no files matched" in result.stderr
    assert "notes" in result.stderr
    assert _snapshot(tmp_path) == before


def test_batch_glob_expands_relative_to_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A quoted glob arrives as a literal string and is expanded relative to
    the cwd: only matching files are ingested, non-matching siblings stay
    untouched (issue #267, scenario: explicit glob)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_notes(tmp_path, {"a.md": "Alpha notes.", "b.txt": "Beta notes."})

    result = runner.invoke(app, ["ingest", "notes/*.md", "--auto"])

    assert result.exit_code == 0
    assert (tmp_path / "raw" / "a.md").is_file()
    assert not (tmp_path / "raw" / "b.txt").exists()
    assert "1 ingested" in result.stdout


def test_batch_glob_recursion_only_via_double_star(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recursion happens ONLY via an explicit `**` glob: `notes/**/*.md`
    reaches a nested file the non-recursive directory form ignores
    (issue #267, scenario: recursive glob)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_notes(tmp_path, {"a.md": "Alpha notes.", "sub/deep.md": "Deep notes."})

    result = runner.invoke(app, ["ingest", "notes/**/*.md", "--auto"])

    assert result.exit_code == 0
    assert (tmp_path / "raw" / "a.md").is_file()
    assert (tmp_path / "raw" / "deep.md").is_file()
    assert "2 ingested" in result.stdout


def test_batch_glob_matching_nothing_refuses_nothing_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A glob matching no files refuses with a clear message, exit 1,
    nothing written (issue #267, scenario: glob matches nothing)."""
    _init_workspace(tmp_path, monkeypatch)
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["ingest", "notes/*.md", "--auto"])

    assert result.exit_code == 1
    assert "no files matched" in result.stderr
    assert _snapshot(tmp_path) == before


def test_batch_basename_collision_refuses_whole_run_naming_both_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The destination name and slug derive ONLY from the basename
    (path-traversal defense), so two matched files sharing a basename would
    fight over `raw/<name>`. Phase A detects this BEFORE any write and
    refuses the WHOLE run -- exit 1, both colliding paths named, nothing
    written (issue #267, settled decision 1)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_notes(
        tmp_path,
        {"setup.md": "Root setup.", "archive/setup.md": "Archived setup."},
    )
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["ingest", "notes/**/*.md", "--auto"])

    assert result.exit_code == 1
    assert "collision" in result.stderr
    assert str(Path("notes") / "setup.md") in result.stderr
    assert str(Path("notes") / "archive" / "setup.md") in result.stderr
    assert _snapshot(tmp_path) == before


def test_batch_cost_gate_prints_counts_and_confirms_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Before any LLM contact, the batch prints `{n} file(s) -> {n} LLM
    call(s)` (the #134 pattern) to stderr and asks ONE up-front
    confirmation; a `y` answer covers every file -- the per-file prompt is
    suppressed the way `--auto` suppresses it today, so a single `y` on
    stdin completes a two-file batch (issue #267, settled decision 4)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_notes(tmp_path, {"a.txt": "Alpha notes.", "b.txt": "Beta notes."})
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["ingest", "notes"], input="y\n")

    assert result.exit_code == 0
    assert "2 file(s) -> 2 LLM call(s)" in result.stderr
    assert (tmp_path / "raw" / "a.txt").is_file()
    assert (tmp_path / "raw" / "b.txt").is_file()
    # ONE gate: the batch prompt appears exactly once and the single-file
    # "Proceed with these changes?" prompt never does.
    assert "Proceed with these changes?" not in result.output
    assert result.output.count("Proceed") == 1


def test_batch_cost_gate_decline_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Declining the batch cost gate aborts with nothing written and no LLM
    contact (issue #267, settled decision 4)."""
    _init_workspace(tmp_path, monkeypatch)
    fake = _patch_llm(monkeypatch)
    _write_notes(tmp_path, {"a.txt": "Alpha notes."})
    _simulate_tty(monkeypatch)
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["ingest", "notes"], input="n\n")

    assert result.exit_code == 1
    assert fake.calls == []
    assert _snapshot(tmp_path) == before


def test_batch_non_tty_without_auto_refuses_nothing_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-TTY stdin without `--auto` refuses to write rather than
    defaulting silently -- mirroring the single-file convention -- with
    nothing written (issue #267, settled decision 4)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_notes(tmp_path, {"a.txt": "Alpha notes."})
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["ingest", "notes"])

    assert result.exit_code == 1
    assert "re-run with --auto" in result.stderr
    assert _snapshot(tmp_path) == before


def test_batch_review_false_skips_the_cost_gate_like_auto(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Config `review: false` skips the batch cost gate the same way it
    skips the single-file prompt today -- same precedence, mirrored
    (issue #267, settled decision 4)."""
    _init_workspace(tmp_path, monkeypatch)
    _set_config_field(tmp_path, "review: true", "review: false")
    _write_notes(tmp_path, {"a.txt": "Alpha notes."})
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["ingest", "notes"])

    assert result.exit_code == 0
    assert "Proceed" not in result.output
    assert (tmp_path / "raw" / "a.txt").is_file()


def test_batch_progress_lines_on_tty_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On a TTY, the batch reports per-file `i/N` progress on stderr via the
    TTY-gated `observability` helpers (issue #267 citing #190); stdout keeps
    the clean report."""
    _init_workspace(tmp_path, monkeypatch)
    _write_notes(tmp_path, {"a.txt": "Alpha notes.", "b.txt": "Beta notes."})
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["ingest", "notes"], input="y\n")

    assert result.exit_code == 0
    assert "openkos ingest: ingesting file 1/2 - " in result.stderr
    assert "openkos ingest: ingesting file 2/2 - " in result.stderr
    assert "ingesting file" not in result.stdout


def test_batch_partial_failure_skips_that_file_and_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each file runs through the existing single-file pipeline
    independently, in order: a per-file refusal (differing bytes under an
    existing `raw/` copy) SKIPS that file with its reason and CONTINUES to
    the rest; the run exits 1 because a file was refused (issue #267,
    settled decision 2)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_notes(
        tmp_path,
        {"a.txt": "Alpha notes.", "b.txt": "Beta notes.", "c.txt": "Gamma notes."},
    )
    _stage_ingested_raw(
        tmp_path, "b.txt", "conflicting bytes", tmp_path / "notes" / "b.txt"
    )

    result = runner.invoke(app, ["ingest", "notes", "--auto"])

    assert result.exit_code == 1
    # a and c landed despite b's refusal -- the batch continued.
    assert (tmp_path / "raw" / "a.txt").is_file()
    assert (tmp_path / "bundle" / "sources" / "a.md").is_file()
    assert (tmp_path / "raw" / "c.txt").is_file()
    assert (tmp_path / "bundle" / "sources" / "c.md").is_file()
    # b's raw copy is untouched, its refusal reason is the single-file
    # message, unchanged, and its outcome line marks the skip.
    assert (tmp_path / "raw" / "b.txt").read_text(
        encoding="utf-8"
    ) == "conflicting bytes"
    assert "differs from the existing 'raw/b.txt'" in result.stderr
    assert f"! {Path('notes') / 'b.txt'} -- skipped" in result.stdout
    assert "2 ingested, 0 re-ingested, 1 skipped" in result.stdout


def test_batch_reingest_counts_as_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Re-running the same batch is idempotent for completed files: every
    byte-identical file re-ingests, the summary counts them as
    `re-ingested`, and the run exits 0 -- idempotent re-ingests count as
    success (issue #267, settled decisions 2 and 3)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_notes(tmp_path, {"a.txt": "Alpha notes.", "b.txt": "Beta notes."})
    first = runner.invoke(app, ["ingest", "notes", "--auto"])
    assert first.exit_code == 0

    result = runner.invoke(app, ["ingest", "notes", "--auto"])

    assert result.exit_code == 0
    assert "0 ingested, 2 re-ingested, 0 skipped" in result.stdout
    assert f"~ {Path('notes') / 'a.txt'} -- re-ingested" in result.stdout


def test_batch_forwards_include_confidential_per_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--include-confidential` is forwarded unchanged to every per-file
    ingest: under a `confidential` workspace floor the flag bypasses the
    extraction gate for EACH file, so the LLM is called for each file
    (issue #267) -- 2 calls per file under the union+judge product default
    (#456: 2 extraction runs; the declining reply leaves the merged union
    empty, so no judge call is spent on it)."""
    _init_workspace(tmp_path, monkeypatch)
    _set_config_field(
        tmp_path, "default_sensitivity: private", "default_sensitivity: confidential"
    )
    fake = _patch_llm(monkeypatch)
    _write_notes(tmp_path, {"a.txt": "Alpha notes.", "b.txt": "Beta notes."})

    result = runner.invoke(app, ["ingest", "notes", "--auto", "--include-confidential"])

    assert result.exit_code == 0
    assert len(fake.calls) == 4


def test_batch_confidential_floor_degrades_every_file_without_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without `--include-confidential`, a `confidential` workspace floor
    skips extraction per file exactly as today -- `llm.chat` is never
    called, every file lands Source-only, and the summary tallies them as
    extraction-degraded (issue #267, settled decision 2)."""
    _init_workspace(tmp_path, monkeypatch)
    _set_config_field(
        tmp_path, "default_sensitivity: private", "default_sensitivity: confidential"
    )
    fake = _patch_llm(monkeypatch)
    _write_notes(tmp_path, {"a.txt": "Alpha notes.", "b.txt": "Beta notes."})

    result = runner.invoke(app, ["ingest", "notes", "--auto"])

    assert result.exit_code == 0
    assert fake.calls == []
    assert (tmp_path / "bundle" / "sources" / "a.md").is_file()
    assert "2 extraction-degraded" in result.stdout


def test_batch_extraction_failure_stays_per_file_nonfatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unreachable LLM degrades each file to Source-only exactly as the
    single-file path does today (stderr note, exit unaffected): the batch
    still ingests every Source, exits 0, and tallies the degrades
    (issue #267, settled decision 2)."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, raises=OllamaUnavailable("connection refused"))
    _write_notes(tmp_path, {"a.txt": "Alpha notes.", "b.txt": "Beta notes."})

    result = runner.invoke(app, ["ingest", "notes", "--auto"])

    assert result.exit_code == 0
    assert (tmp_path / "bundle" / "sources" / "a.md").is_file()
    assert (tmp_path / "bundle" / "sources" / "b.md").is_file()
    assert "concept extraction skipped" in result.stderr
    assert "2 extraction-degraded" in result.stdout


def test_batch_commits_per_file_not_per_batch(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Commit granularity is PER FILE -- the existing per-ingest auto-commit
    is reused unchanged, so each completed file is its own checkpoint and an
    interrupted run leaves a committed, consistent workspace (issue #267,
    settled decision 3). Two batch files add exactly two commits, each
    naming its own source."""
    monkeypatch.chdir(tmp_path)
    config_dir = tmp_path_factory.mktemp("git-identity-config")
    isolate_git_identity(
        monkeypatch, config_dir, name="Isolated Tester", email="tester@example.invalid"
    )
    init_result = runner.invoke(app, ["init"])
    assert init_result.exit_code == 0
    _write_notes(tmp_path, {"a.txt": "Alpha notes.", "b.txt": "Beta notes."})

    def _log_subjects() -> list[str]:
        completed = vcs_git._run(["git", "log", "--format=%s"], cwd=tmp_path)
        return completed.stdout.splitlines()

    before_subjects = _log_subjects()

    result = runner.invoke(app, ["ingest", "notes", "--auto"])

    assert result.exit_code == 0
    new_subjects = _log_subjects()[: len(_log_subjects()) - len(before_subjects)]
    assert new_subjects == [
        "openkos: ingest b.txt (+0 concepts)",
        "openkos: ingest a.txt (+0 concepts)",
    ]


def test_batch_plain_file_argument_keeps_single_file_behavior(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A plain existing file path keeps today's exact single-file behavior:
    no batch summary, no cost-gate line -- the batch path wraps, never
    modifies, the single-file pipeline (issue #267)."""
    _init_workspace(tmp_path, monkeypatch)
    (tmp_path / "notes.txt").write_text("Some raw notes.", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    assert "batch summary" not in result.stdout
    assert "LLM call(s)" not in result.stderr
    assert (tmp_path / "raw" / "notes.txt").is_file()


# --- Issue #349: batch ingest polish ----------------------------------------


def test_batch_all_drift_skips_exit_3_preserving_retry_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When EVERY skipped file was a drift refusal (the per-file pipeline's
    exit 3, #319), the batch itself exits 3 -- preserving the retry contract:
    a script that treats exit 3 as "safe to re-run" must be able to trust
    the batch exit the same way it trusts the single-file one. A generic
    exit 1 here would silently downgrade the one retryable failure into a
    non-retryable-looking one (issue #349)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_notes(tmp_path, {"a.txt": "Alpha notes."})
    index_path = tmp_path / "bundle" / "index.md"
    hook = echo_after(
        monkeypatch,
        lambda: index_path.write_text(
            index_path.read_text(encoding="utf-8") + "drifted\n", encoding="utf-8"
        ),
        trigger="(new dated entry)",
    )

    result = runner.invoke(app, ["ingest", "notes", "--auto"])

    assert hook.fired, "echo_after trigger never matched -- stale preview wording?"
    assert result.exit_code == 3
    assert "refusing to write --" in result.stderr
    assert (
        f"! {Path('notes') / 'a.txt'} -- skipped (refused with exit code 3"
        in result.stdout
    )
    assert "0 ingested, 0 re-ingested, 1 skipped" in result.stdout


def test_batch_mixed_drift_and_hard_refusal_exits_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A batch mixing a drift refusal (exit 3) with a hard refusal (exit 1)
    exits 1: the retry guarantee only holds when EVERY skip was retryable,
    and a hard refusal in the mix means a plain re-run would refuse again --
    so the batch must not advertise retryability it cannot deliver
    (issue #349)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_notes(tmp_path, {"a.txt": "Alpha notes.", "b.txt": "Beta notes."})
    # b hard-refuses: the SAME source's raw copy with DIFFERING bytes
    # (exit 1) -- staged with its owning Source so the origin is known.
    _stage_ingested_raw(
        tmp_path, "b.txt", "conflicting bytes", tmp_path / "notes" / "b.txt"
    )
    # a drift-refuses: an index.md edit lands inside a's preview window
    # (exit 3); the hook fires ONCE, on a's preview -- a sorts before b.
    index_path = tmp_path / "bundle" / "index.md"
    hook = echo_after(
        monkeypatch,
        lambda: index_path.write_text(
            index_path.read_text(encoding="utf-8") + "drifted\n", encoding="utf-8"
        ),
        trigger="(new dated entry)",
    )

    result = runner.invoke(app, ["ingest", "notes", "--auto"])

    assert hook.fired, "echo_after trigger never matched -- stale preview wording?"
    assert result.exit_code == 1
    assert (
        f"! {Path('notes') / 'a.txt'} -- skipped (refused with exit code 3"
        in result.stdout
    )
    assert (
        f"! {Path('notes') / 'b.txt'} -- skipped (refused with exit code 1"
        in result.stdout
    )
    assert "0 ingested, 0 re-ingested, 2 skipped" in result.stdout


def test_batch_hard_refusal_only_exits_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A batch whose only skip was a hard refusal (exit 1) exits 1 -- the
    exit-3 ladder rung is reserved for the all-drift case; a hard refusal
    is not retryable and must not read as one (issue #349, #234: distinct
    causes must not read alike)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_notes(tmp_path, {"b.txt": "Beta notes."})
    _stage_ingested_raw(
        tmp_path, "b.txt", "conflicting bytes", tmp_path / "notes" / "b.txt"
    )

    result = runner.invoke(app, ["ingest", "notes", "--auto"])

    assert result.exit_code == 1
    assert (
        f"! {Path('notes') / 'b.txt'} -- skipped (refused with exit code 1"
        in result.stdout
    )
    assert "0 ingested, 0 re-ingested, 1 skipped" in result.stdout


def test_batch_outcome_lines_precede_aggregate_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The run closes with per-file outcome lines FIRST, then the aggregate
    summary -- the order `ingest`'s docstring, `_ingest_batch`'s docstring,
    and docs/cli.md all promise ("per-file outcome lines plus an aggregate
    summary"): the summary is the batch's last word, not its headline
    (issue #349)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_notes(tmp_path, {"a.txt": "Alpha notes.", "b.txt": "Beta notes."})

    result = runner.invoke(app, ["ingest", "notes", "--auto"])

    assert result.exit_code == 0
    a_line = result.stdout.index(f"+ {Path('notes') / 'a.txt'} -- ingested")
    b_line = result.stdout.index(f"+ {Path('notes') / 'b.txt'} -- ingested")
    summary_line = result.stdout.index("batch summary")
    assert a_line < summary_line
    assert b_line < summary_line


def test_batch_existing_file_named_with_glob_magic_keeps_single_file_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`_expand_batch_sources` checks `is_file()` FIRST, so an existing file
    whose literal name contains a glob magic character keeps today's exact
    single-file behavior -- it is never expanded as a pattern (the docstring's
    promise, previously untested). `notes[1].txt` doubles as the pattern
    `notes1.txt`, so a decoy sibling by that name pins the distinction: the
    literal file is ingested, the glob match is not (issue #349)."""
    _init_workspace(tmp_path, monkeypatch)
    (tmp_path / "notes[1].txt").write_text("Literal name.", encoding="utf-8")
    # The decoy is what the PATTERN `notes[1].txt` would match.
    (tmp_path / "notes1.txt").write_text("Glob decoy.", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes[1].txt", "--auto"])

    assert result.exit_code == 0
    assert (tmp_path / "raw" / "notes[1].txt").read_text(
        encoding="utf-8"
    ) == "Literal name."
    assert not (tmp_path / "raw" / "notes1.txt").exists()
    assert "batch summary" not in result.stdout


def test_batch_outside_workspace_refuses_before_cost_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Outside an initialized workspace, the batch's up-front workspace check
    refuses BEFORE the cost gate can prompt (`_ingest_batch`'s Phase-A
    promise, previously untested for the directory entry point): the refusal
    reaches stderr and no `LLM call(s)` line or `Proceed?` prompt ever
    appears, even on a TTY without `--auto` -- the shape that would otherwise
    ask (issue #349)."""
    monkeypatch.chdir(tmp_path)
    _write_notes(tmp_path, {"a.txt": "Alpha notes."})
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["ingest", "notes"], input="y\n")

    assert result.exit_code == 1
    assert (
        "openkos ingest: refusing to ingest -- no OpenKOS workspace found in "
        "this directory (run 'openkos init' first)." in result.stderr
    )
    assert "LLM call(s)" not in result.stderr
    assert "Proceed" not in result.output


# --- Cap truncation notice (#404) -------------------------------------------


def _many_concepts_reply(n: int) -> str:
    items = ", ".join(
        f'{{"type": "Concept", "title": "Topic {i}", '
        f'"description": "Description {i}.", "body": "Body {i}."}}'
        for i in range(n)
    )
    return f"[{items}]"


_CAP = concept_mod._MAX_OBJECTS_PER_SOURCE
"""Read symbolically so these tests describe the NOTICE, not the cap value.

They were written against a hardcoded 5 and all three broke when #404's
measurement moved it to 6 -- while the notice itself was behaving correctly.
A test that fails on a deliberate, separately-pinned change it does not
govern costs a debugging trip and teaches nothing. The value has its own
literal assertion in `tests/unit/extraction/test_concept.py`.
"""


def test_ingest_reports_when_the_object_cap_discarded_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#404: a source proposing 20 objects and one proposing exactly the cap
    were indistinguishable in the output -- both simply wrote `cap` documents.
    The loss is now named, so a reader can tell the difference."""
    _init_workspace(tmp_path, monkeypatch)
    # Pinned to the single-run path (#404's own cap, `_MAX_OBJECTS_PER_SOURCE`)
    # -- distinct from the union+judge `_UNION_BACKSTOP` of 20 (#456, #564).
    _set_config_field(tmp_path, "# union_judge: true", "union_judge: false")
    _patch_llm(monkeypatch, _many_concepts_reply(20))
    source = tmp_path / "notes.txt"
    source.write_text("A long document about many topics.", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    assert f"{_CAP} of 20 extracted object(s) kept (cap reached)" in result.stderr


def test_ingest_cap_notice_names_the_discarded_titles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bare count says something vanished but not what. The measurement
    behind #404 showed the discarded tail is exactly what a reader needs to
    judge whether the cap cost them anything real."""
    _init_workspace(tmp_path, monkeypatch)
    _set_config_field(tmp_path, "# union_judge: true", "union_judge: false")
    # Exactly `_CAP_NOTICE_TITLE_LIMIT` over the cap, so every discarded title
    # is named and none is folded into the "+N more" remainder.
    proposed = _CAP + main._CAP_NOTICE_TITLE_LIMIT
    _patch_llm(monkeypatch, _many_concepts_reply(proposed))
    source = tmp_path / "notes.txt"
    source.write_text("A long document about many topics.", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    for index in range(_CAP, proposed):
        assert f"Topic {index}" in result.stderr


def test_ingest_cap_notice_bounds_how_many_titles_it_echoes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A source proposing 61 objects would otherwise dump 55 titles into the
    terminal. Name enough to judge, then count the rest."""
    _init_workspace(tmp_path, monkeypatch)
    _set_config_field(tmp_path, "# union_judge: true", "union_judge: false")
    _patch_llm(monkeypatch, _many_concepts_reply(20))
    source = tmp_path / "notes.txt"
    source.write_text("A long document about many topics.", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    unnamed = (20 - _CAP) - main._CAP_NOTICE_TITLE_LIMIT
    assert f"(+{unnamed} more)" in result.stderr
    assert "Topic 19" not in result.stderr


def test_ingest_says_nothing_about_the_cap_when_it_did_not_fire(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An advisory that fires on the healthy path is noise. A source under
    the cap must produce no notice at all."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, _many_concepts_reply(3))
    source = tmp_path / "notes.txt"
    source.write_text("A short document.", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    assert "cap reached" not in result.stderr


# --- near-boundary type reporting (#401) -------------------------------------

_NEAR_BOUNDARY_REPLY = (
    '[{"type": "Event", "title": "Hellenistic Ethics Seminar", '
    '"description": "A seminar taught this term.", "body": "", '
    '"type_alternative": "Project"}]'
)


def test_stage_derived_objects_records_the_alternative_in_frontmatter(
    tmp_path: Path,
) -> None:
    """The runner-up type reaches the written document, not just the echo.

    The stderr line scrolls away; the frontmatter is what a human reading
    the bundle later, or `lint`, can still act on.
    """
    plans, reason, _notice = main._stage_derived_objects(
        **_stage_kwargs(tmp_path, llm=_FakeLLM(_NEAR_BOUNDARY_REPLY))  # type: ignore[arg-type]
    )

    assert reason is None
    assert len(plans) == 1
    metadata, _ = okf.load_frontmatter(plans[0].content)
    assert metadata["type"] == "Event"
    assert metadata[okf.TYPE_ALTERNATIVE_KEY] == "Project"


def test_stage_derived_objects_carries_the_alternative_on_the_plan_silently(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Staging records the runner-up on the plan and says nothing per
    candidate (#566).

    The per-candidate line fired on roughly 100% of extracted objects in a
    real session -- 12 of 13, 11 of 11 -- so it carried no discriminating
    signal and doubled the terminal output of a directory ingest. The pair
    now rides `_DerivedPlan.type_alternative` so the CALLER can aggregate
    it into one summary line per run; the frontmatter record (the durable
    signal) is untouched.
    """
    plans, _, _notice = main._stage_derived_objects(
        **_stage_kwargs(tmp_path, llm=_FakeLLM(_NEAR_BOUNDARY_REPLY))  # type: ignore[arg-type]
    )

    assert len(plans) == 1
    assert plans[0].type_alternative == "Project"
    err = capsys.readouterr().err
    assert "also weighed" not in err
    assert "Hellenistic Ethics Seminar" not in err


_TORN_PAIR_REPLY = json.dumps(
    [
        {
            "type": "Event",
            "title": "Hellenistic Ethics Seminar",
            "description": "A seminar taught this term.",
            "body": "",
            "type_alternative": "Project",
        },
        {
            "type": "Concept",
            "title": "Apatheia",
            "description": "A Stoic concept.",
            "body": "",
            "type_alternative": "Procedure",
        },
        {
            "type": "Concept",
            "title": "Eudaimonia",
            "description": "A Greek concept of flourishing.",
            "body": "",
        },
    ]
)
"""Three staged objects: two torn (Event/Project, Concept/Procedure), one
clear -- the aggregate line must read `2 of 3` and name the most common
pair without repeating per-object noise."""


def test_ingest_prints_one_aggregate_type_alternative_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A single-file ingest closes with ONE aggregate line naming how many
    objects recorded an alternative type, not one line per object (#566)."""
    _init_workspace(tmp_path, monkeypatch)
    _set_config_field(tmp_path, "# union_judge: true", "union_judge: false")
    _patch_llm(monkeypatch, _TORN_PAIR_REPLY)
    source = tmp_path / "notes.txt"
    source.write_text("Notes about Hellenistic ethics.", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    assert "also weighed" not in result.stderr
    assert result.stderr.count("recorded a type_alternative") == 1
    assert "2 of 3 derived object(s) recorded a type_alternative" in result.stderr


def test_ingest_batch_aggregates_type_alternative_across_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A directory ingest emits the aggregate ONCE for the whole batch --
    never per file (#566): the per-file wording that motivated the issue
    printed 12 lines for 13 objects in one real batch."""
    _init_workspace(tmp_path, monkeypatch)
    _set_config_field(tmp_path, "# union_judge: true", "union_judge: false")
    _patch_llm(monkeypatch, _NEAR_BOUNDARY_REPLY)
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.txt").write_text("Seminar notes, first half.", encoding="utf-8")
    (docs / "b.txt").write_text("Seminar notes, second half.", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "docs", "--auto"])

    assert result.exit_code == 0
    assert "also weighed" not in result.stderr
    assert result.stderr.count("recorded a type_alternative") == 1
    assert "2 of 2 derived object(s) recorded a type_alternative" in result.stderr


def test_stage_derived_objects_stays_silent_when_the_type_was_clear(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """No alternative means no notice and no frontmatter key.

    The common path must stay exactly as loud -- and the document exactly as
    shaped -- as before #401, or the signal drowns in its own noise.
    """
    reply = (
        '[{"type": "Concept", "title": "Apatheia", '
        '"description": "A Stoic concept.", "body": ""}]'
    )

    plans, _, _notice = main._stage_derived_objects(
        **_stage_kwargs(tmp_path, llm=_FakeLLM(reply))  # type: ignore[arg-type]
    )

    metadata, _ = okf.load_frontmatter(plans[0].content)
    assert okf.TYPE_ALTERNATIVE_KEY not in metadata
    assert plans[0].type_alternative is None
    err = capsys.readouterr().err
    assert "also weighed" not in err
    assert "recorded a type_alternative" not in err


def test_ingest_sole_twin_re_ask_reports_what_it_added_on_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#584: a source whose whole extraction collapses to one object
    restating its own title spends ONE extra re-ask call, and that call is
    reported -- with the titles it contributed, and in wording distinct from
    the cap, judge, and pre-judge ceiling notices. A silent extra model call
    is exactly the cost this project surfaces rather than hides."""
    _init_workspace(tmp_path, monkeypatch)
    twin = _concept_reply(title="Replica Lag")
    added = _concept_reply(title="Read-Your-Writes Consistency")
    keep = '{"keep": ["Replica Lag", "Read-Your-Writes Consistency"]}'
    _patch_sequenced_llm(monkeypatch, [twin, twin, added, keep])
    source = tmp_path / "replica-lag.txt"
    source.write_text(
        "Replica Lag\n\nA replica trails its primary, and a read routed to "
        "it can miss a write the client just made.\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["ingest", "replica-lag.txt", "--auto"])

    assert result.exit_code == 0
    reask_lines = [line for line in result.stderr.splitlines() if "re-ask call" in line]
    assert len(reask_lines) == 1
    assert "one object restating the source title" in reask_lines[0]
    assert (
        "1 extra re-ask call added 1 object(s): Read-Your-Writes Consistency"
        in reask_lines[0]
    )
    for other_notice_marker in (
        "cap reached",
        "judge dropped",
        "judge selection",
        "pre-judge ceiling",
    ):
        assert other_notice_marker not in reask_lines[0]
    concept_dir = tmp_path / "bundle" / "concepts"
    assert (concept_dir / "replica-lag.md").is_file()
    assert (concept_dir / "read-your-writes-consistency.md").is_file()


def test_ingest_reports_a_re_ask_that_found_nothing_further(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The genuinely single-subject case -- the probe's negative control
    shape. The re-ask answers `[]`, which its prompt names as correct and
    expected; the object the first pass produced is written unchanged, and
    the spent call is still reported."""
    _init_workspace(tmp_path, monkeypatch)
    twin = _concept_reply(title="Replica Lag")
    keep = '{"keep": ["Replica Lag"]}'
    _patch_sequenced_llm(monkeypatch, [twin, twin, "[]", keep])
    source = tmp_path / "replica-lag.txt"
    source.write_text(
        "Replica Lag\n\nA replica trails its primary, and a read routed to "
        "it can miss a write the client just made.\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["ingest", "replica-lag.txt", "--auto"])

    assert result.exit_code == 0
    assert "1 extra re-ask call found nothing further" in result.stderr
    concept_dir = tmp_path / "bundle" / "concepts"
    assert [p.name for p in sorted(concept_dir.glob("*.md"))] == ["replica-lag.md"]


def test_ingest_reask_notice_bounds_how_many_added_titles_it_echoes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The re-ask notice names enough added titles to judge the call, then
    counts the rest -- the same bound `_CAP_NOTICE_TITLE_LIMIT` puts on the
    cap notice, for the same reason."""
    _init_workspace(tmp_path, monkeypatch)
    _set_config_field(tmp_path, "# union_judge: true", "union_judge: false")
    added = 5
    reask_reply = (
        "[" + ", ".join(_concept_reply(f"Added {i}") for i in range(added)) + "]"
    )
    _patch_sequenced_llm(
        monkeypatch, [_concept_reply(title="Replica Lag"), reask_reply]
    )
    source = tmp_path / "replica-lag.txt"
    source.write_text(
        "Replica Lag\n\nA replica trails its primary, and a read routed to "
        "it can miss a write the client just made.\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["ingest", "replica-lag.txt", "--auto"])

    assert result.exit_code == 0
    unnamed = added - main._CAP_NOTICE_TITLE_LIMIT
    assert f"1 extra re-ask call added {added} object(s)" in result.stderr
    assert f"(+{unnamed} more)" in result.stderr
    assert "Added 4" not in result.stderr


# --- `extraction_notice` frontmatter stamping (issue #585) ----------------
#
# #585's chosen criterion: keep the object, mark the Source. A degrade to
# `[]` was rejected because it destroys the `mcp-launch` shape -- a
# genuinely single-subject source whose only subject IS what its title
# names -- which by title alone is indistinguishable from the defect.
# Marking is strictly information-adding: it cannot regress recall, and it
# never has to tell the two cases apart.


def test_sole_object_restating_the_source_stamps_the_extraction_notice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole of #585, end to end: a source whose only derived object
    restates it keeps that object AND carries the disclosure.

    The derived-file assertion is not decoration -- it is the half of the
    criterion that says this is not a degrade. Any implementation that
    started dropping the twin to "fix" the dishonesty fails here."""
    _init_workspace(tmp_path, monkeypatch)
    twin = _concept_reply(title="Replica Lag")
    keep = '{"keep": ["Replica Lag"]}'
    _patch_sequenced_llm(monkeypatch, [twin, twin, "[]", keep])
    source = tmp_path / "replica-lag.txt"
    source.write_text(
        "Replica Lag\n\nA replica trails its primary, and a read routed to "
        "it can miss a write the client just made.\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["ingest", "replica-lag.txt", "--auto"])

    assert result.exit_code == 0
    assert (tmp_path / "bundle" / "concepts" / "replica-lag.md").is_file()
    concept_path = tmp_path / "bundle" / "sources" / "replica-lag.md"
    metadata, _ = okf.load_frontmatter(concept_path.read_text(encoding="utf-8"))
    assert metadata["extraction_notice"] == "sole-object-restates-source"
    assert "extraction_status" not in metadata


def test_sole_object_restating_the_source_is_reported_on_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The disclosure reaches the operator at the moment it is decided, not
    only the reader who later opens the Source -- the same reason every
    other extraction drop and cost in this verb prints a stderr line."""
    _init_workspace(tmp_path, monkeypatch)
    twin = _concept_reply(title="Replica Lag")
    keep = '{"keep": ["Replica Lag"]}'
    _patch_sequenced_llm(monkeypatch, [twin, twin, "[]", keep])
    source = tmp_path / "replica-lag.txt"
    source.write_text(
        "Replica Lag\n\nA replica trails its primary, and a read routed to "
        "it can miss a write the client just made.\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["ingest", "replica-lag.txt", "--auto"])

    assert result.exit_code == 0
    assert "only derived object restates this source" in result.stderr


def test_successful_extraction_writes_no_extraction_notice_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A source yielding a subject its own title does not name leaves the
    key entirely absent. This is the guard against the failure #566 is open
    for: a notice that fires on nearly every object carries no signal."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, _concept_reply())
    source = tmp_path / "notes.txt"
    source.write_text("Some raw notes about self-control.", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    concept_path = tmp_path / "bundle" / "sources" / "notes.md"
    assert "extraction_notice" not in concept_path.read_text(encoding="utf-8")


def test_degraded_extraction_writes_no_extraction_notice_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Zero derived objects is `extraction_status`' territory, never this
    key's. A Source that derived NOTHING must not be marked as having
    derived one thing that restates it -- the two keys would then contradict
    each other on the same document."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, '{"extract": false}')
    source = tmp_path / "notes.txt"
    source.write_text("Some raw notes about self-control.", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    concept_path = tmp_path / "bundle" / "sources" / "notes.md"
    text = concept_path.read_text(encoding="utf-8")
    metadata, _ = okf.load_frontmatter(text)
    assert metadata["extraction_status"] == "no-concepts-found"
    assert "extraction_notice" not in text


def test_reingest_clears_a_previous_extraction_notice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The anti-merge guard, mirroring `extraction_status`' self-clearing
    test: the key is recomputed fresh for THIS run alone.

    A re-ingest whose extraction now finds a second subject must end with
    the key ABSENT. Any implementation that read the on-disk value and
    merged it forward leaves a stale marker asserting a collapse that no
    longer happens, and the Source lies in the opposite direction from the
    one #585 set out to fix."""
    _init_workspace(tmp_path, monkeypatch)
    twin = _concept_reply(title="Replica Lag")
    keep_one = '{"keep": ["Replica Lag"]}'
    _patch_sequenced_llm(monkeypatch, [twin, twin, "[]", keep_one])
    source = tmp_path / "replica-lag.txt"
    source.write_text(
        "Replica Lag\n\nA replica trails its primary, and a read routed to "
        "it can miss a write the client just made.\n",
        encoding="utf-8",
    )

    assert runner.invoke(app, ["ingest", "replica-lag.txt", "--auto"]).exit_code == 0
    concept_path = tmp_path / "bundle" / "sources" / "replica-lag.md"
    metadata, _ = okf.load_frontmatter(concept_path.read_text(encoding="utf-8"))
    assert metadata["extraction_notice"] == "sole-object-restates-source"

    second = _concept_reply(title="Read-Your-Writes Consistency")
    both = f"[{twin[1:-1]}, {second[1:-1]}]"
    keep_two = '{"keep": ["Replica Lag", "Read-Your-Writes Consistency"]}'
    _patch_sequenced_llm(monkeypatch, [both, both, keep_two])

    result = runner.invoke(app, ["ingest", "replica-lag.txt", "--auto"])

    assert result.exit_code == 0
    assert "extraction_notice" not in concept_path.read_text(encoding="utf-8")


# --- Basename collision across folders (issue #552) -----------------------
#
# `raw/` is a FLAT namespace derived from `Path(src).name` -- the
# path-traversal defence, which is correct and stays. What was missing is
# disambiguation when the basename is already taken by a DIFFERENT file.
# One shape refused a legitimate file; the other silently absorbed a second
# source into the first one's Source, and provenance is a core promise of
# this product.
#
# Identity comes from `origin_key` (the resolved origin path's digest), not
# from the bytes: two empty `__init__.py` files are two sources.


def _folder_source(tmp_path: Path, folder: str, name: str, text: str) -> Path:
    """A source file under its own folder, so two can share a basename."""
    directory = tmp_path / folder
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(text, encoding="utf-8")
    return path


def test_same_basename_different_content_no_longer_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#552's headline evidence: two unrelated courses each holding an
    `01-bienvenida.md`. The second was REFUSED and its content never
    entered the bundle. Both must now land, under distinct raw copies."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch)
    _folder_source(tmp_path, "course-a", "01-bienvenida.md", "# A\n\nWelcome to A.\n")
    _folder_source(tmp_path, "course-b", "01-bienvenida.md", "# B\n\nWelcome to B.\n")

    first = runner.invoke(app, ["ingest", "course-a/01-bienvenida.md", "--auto"])
    second = runner.invoke(app, ["ingest", "course-b/01-bienvenida.md", "--auto"])

    assert first.exit_code == 0
    assert second.exit_code == 0
    raw_dir = tmp_path / "raw"
    assert (raw_dir / "01-bienvenida.md").read_text(encoding="utf-8") == (
        "# A\n\nWelcome to A.\n"
    )
    assert (raw_dir / "01-bienvenida-2.md").read_text(encoding="utf-8") == (
        "# B\n\nWelcome to B.\n"
    )
    sources = tmp_path / "bundle" / "sources"
    assert (sources / "01-bienvenida.md").is_file()
    assert (sources / "01-bienvenida-2.md").is_file()


def test_same_basename_identical_bytes_is_still_a_distinct_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The silent merge #552 calls worse, and the decision taken for it:
    two empty `__init__.py` files from different packages are two sources.

    The byte check passed, so this used to report a re-ingest and reuse the
    first copy -- leaving the Source misrepresenting its own provenance with
    no warning. Identity is the ORIGIN, never the content."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch)
    _folder_source(tmp_path, "pkg-a", "__init__.py", "")
    _folder_source(tmp_path, "pkg-b", "__init__.py", "")

    assert runner.invoke(app, ["ingest", "pkg-a/__init__.py", "--auto"]).exit_code == 0
    result = runner.invoke(app, ["ingest", "pkg-b/__init__.py", "--auto"])

    assert result.exit_code == 0
    raw_dir = tmp_path / "raw"
    assert (raw_dir / "__init__.py").is_file()
    assert (raw_dir / "__init__-2.py").is_file()


def test_disambiguated_sources_carry_distinct_origin_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each Source records WHICH file it came from. Without this the second
    run has nothing to compare against and every re-ingest disambiguates
    again -- an unbounded suffix chain."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch)
    _folder_source(tmp_path, "course-a", "01-bienvenida.md", "# A\n\nWelcome to A.\n")
    _folder_source(tmp_path, "course-b", "01-bienvenida.md", "# B\n\nWelcome to B.\n")

    runner.invoke(app, ["ingest", "course-a/01-bienvenida.md", "--auto"])
    runner.invoke(app, ["ingest", "course-b/01-bienvenida.md", "--auto"])

    sources = tmp_path / "bundle" / "sources"
    first, _ = okf.load_frontmatter(
        (sources / "01-bienvenida.md").read_text(encoding="utf-8")
    )
    second, _ = okf.load_frontmatter(
        (sources / "01-bienvenida-2.md").read_text(encoding="utf-8")
    )

    assert first["origin_key"] == okf.origin_key_for(
        tmp_path / "course-a" / "01-bienvenida.md"
    )
    assert second["origin_key"] == okf.origin_key_for(
        tmp_path / "course-b" / "01-bienvenida.md"
    )
    assert first["origin_key"] != second["origin_key"]
    assert second["resource"] == "raw/01-bienvenida-2.md"


def test_reingesting_the_same_file_spawns_no_disambiguated_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The regression this whole mechanism has to avoid. A plain re-ingest
    matches on `origin_key` and stays idempotent -- one raw copy, one
    Source, no `-2`."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch)
    _folder_source(tmp_path, "course-a", "01-bienvenida.md", "# A\n\nWelcome to A.\n")

    for _ in range(3):
        result = runner.invoke(app, ["ingest", "course-a/01-bienvenida.md", "--auto"])
        assert result.exit_code == 0

    raw_files = sorted(p.name for p in (tmp_path / "raw").glob("*"))
    assert raw_files == ["01-bienvenida.md"]
    sources = sorted(p.name for p in (tmp_path / "bundle" / "sources").glob("*.md"))
    assert sources == ["01-bienvenida.md"]


def test_reingesting_from_a_different_cwd_is_still_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`origin_key` is keyed on the RESOLVED path, so the same file reached
    by two different spellings is one source. Keying on the string as typed
    would spawn a copy per spelling."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch)
    _folder_source(tmp_path, "course-a", "01-bienvenida.md", "# A\n\nWelcome to A.\n")

    assert (
        runner.invoke(app, ["ingest", "course-a/01-bienvenida.md", "--auto"]).exit_code
        == 0
    )
    result = runner.invoke(
        app, ["ingest", str(tmp_path / "course-a" / "01-bienvenida.md"), "--auto"]
    )

    assert result.exit_code == 0
    assert sorted(p.name for p in (tmp_path / "raw").glob("*")) == ["01-bienvenida.md"]


def test_modified_bytes_for_the_same_origin_still_refuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Raw immutability survives #552 intact. Editing an already-ingested
    file and re-ingesting it is still a refusal, not a silent new copy --
    disambiguation is for a DIFFERENT file, never for changed bytes of the
    same one."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch)
    source = _folder_source(tmp_path, "course-a", "01-bienvenida.md", "# A\n\nOne.\n")
    assert (
        runner.invoke(app, ["ingest", "course-a/01-bienvenida.md", "--auto"]).exit_code
        == 0
    )
    source.write_text("# A\n\nEdited.\n", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "course-a/01-bienvenida.md", "--auto"])

    assert result.exit_code == 1
    assert "raw sources are immutable" in result.stderr
    assert not (tmp_path / "raw" / "01-bienvenida-2.md").exists()


def test_a_legacy_source_without_an_origin_key_matches_on_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The migration path. A Source written before #552 carries no
    `origin_key`; matching it on identical bytes preserves today's exact
    behaviour, and the re-ingest backfills the key -- so the workspace
    self-migrates with no verb and no repair step."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch)
    _folder_source(tmp_path, "course-a", "01-bienvenida.md", "# A\n\nWelcome to A.\n")
    assert (
        runner.invoke(app, ["ingest", "course-a/01-bienvenida.md", "--auto"]).exit_code
        == 0
    )
    concept_path = tmp_path / "bundle" / "sources" / "01-bienvenida.md"
    text = concept_path.read_text(encoding="utf-8")
    metadata, body = okf.load_frontmatter(text)
    del metadata["origin_key"]
    concept_path.write_text(okf.dump_frontmatter(metadata, body), encoding="utf-8")

    result = runner.invoke(app, ["ingest", "course-a/01-bienvenida.md", "--auto"])

    assert result.exit_code == 0
    assert sorted(p.name for p in (tmp_path / "raw").glob("*")) == ["01-bienvenida.md"]
    refreshed, _ = okf.load_frontmatter(concept_path.read_text(encoding="utf-8"))
    assert refreshed["origin_key"] == okf.origin_key_for(
        tmp_path / "course-a" / "01-bienvenida.md"
    )


def test_disambiguation_is_reported_on_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A destination the user did not name is never chosen silently: the
    line states the basename that was taken and the copy actually written."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch)
    _folder_source(tmp_path, "course-a", "01-bienvenida.md", "# A\n\nWelcome to A.\n")
    _folder_source(tmp_path, "course-b", "01-bienvenida.md", "# B\n\nWelcome to B.\n")
    runner.invoke(app, ["ingest", "course-a/01-bienvenida.md", "--auto"])

    result = runner.invoke(app, ["ingest", "course-b/01-bienvenida.md", "--auto"])

    assert result.exit_code == 0
    assert "01-bienvenida.md" in result.stderr
    assert "raw/01-bienvenida-2.md" in result.stderr


def test_a_third_collision_takes_the_next_free_suffix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The scan is ascending and skips what is taken, matching the
    derived-object convention (#131) rather than inventing a second one."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch)
    for folder in ("a", "b", "c"):
        _folder_source(tmp_path, folder, "notes.txt", f"content {folder}")
        assert (
            runner.invoke(app, ["ingest", f"{folder}/notes.txt", "--auto"]).exit_code
            == 0
        )

    assert sorted(p.name for p in (tmp_path / "raw").glob("*")) == [
        "notes-2.txt",
        "notes-3.txt",
        "notes.txt",
    ]


def test_a_legacy_source_with_differing_bytes_disambiguates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rule that resolves #552 stated at its hardest point:
    immutability is scoped to a file we KNOW is the same one.

    A legacy Source records no origin, so a differing-bytes candidate under
    its basename cannot be proven to be the same file. #552's own text asks
    for disambiguation "when the basename exists with different content",
    and refusing here is exactly the harm it was filed for -- real content
    turned away. The candidate lands under its own copy; nothing existing is
    rewritten, so immutability holds for every byte already on disk."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch)
    _folder_source(tmp_path, "course-a", "01-bienvenida.md", "# A\n\nWelcome to A.\n")
    assert (
        runner.invoke(app, ["ingest", "course-a/01-bienvenida.md", "--auto"]).exit_code
        == 0
    )
    concept_path = tmp_path / "bundle" / "sources" / "01-bienvenida.md"
    metadata, body = okf.load_frontmatter(concept_path.read_text(encoding="utf-8"))
    del metadata["origin_key"]
    concept_path.write_text(okf.dump_frontmatter(metadata, body), encoding="utf-8")
    _folder_source(tmp_path, "course-b", "01-bienvenida.md", "# B\n\nWelcome to B.\n")

    result = runner.invoke(app, ["ingest", "course-b/01-bienvenida.md", "--auto"])

    assert result.exit_code == 0
    raw_dir = tmp_path / "raw"
    assert (raw_dir / "01-bienvenida.md").read_text(encoding="utf-8") == (
        "# A\n\nWelcome to A.\n"
    )
    assert (raw_dir / "01-bienvenida-2.md").read_text(encoding="utf-8") == (
        "# B\n\nWelcome to B.\n"
    )


# --- #553: ingest builds the FTS index once at the end of each run ---------


def test_single_ingest_builds_the_fts_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After a single-file `ingest --auto`, the on-disk FTS index exists and
    already serves the ingested Source -- the README quickstart
    (`init` -> `ingest` -> `query`) gets hybrid retrieval without a manual
    `openkos reindex` in between (issue #553; ingestion spec: Ingest Builds
    The FTS Index At The End Of Each Run)."""
    _init_workspace(tmp_path, monkeypatch)
    source = tmp_path / "notes.txt"
    source.write_text("Zorbification quarterly review notes.", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    fts_db = tmp_path / ".openkos" / "fts.db"
    assert fts_db.is_file()
    index = state_fts.open_fts_index_readonly(fts_db)
    assert index is not None
    hits = index.search("Zorbification")
    assert any(hit.concept_id == "sources/notes" for hit in hits)


def test_batch_ingest_builds_the_fts_index_once_at_the_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A directory batch pays exactly ONE FTS build for the whole run, at
    the end -- never one rebuild per ingested file (issue #553 decision:
    batch-end build, not per-document upsert)."""
    _init_workspace(tmp_path, monkeypatch)
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "a.txt").write_text("Alpha notes.", encoding="utf-8")
    (inbox / "b.txt").write_text("Beta notes.", encoding="utf-8")
    (inbox / "c.txt").write_text("Gamma notes.", encoding="utf-8")
    real_build = state_reindex._reindex_fts
    calls: list[bool] = []

    def counting(bundle_dir: Path, fts_db_path: Path, *, force: bool) -> None:
        calls.append(force)
        real_build(bundle_dir, fts_db_path, force=force)

    monkeypatch.setattr(state_reindex, "_reindex_fts", counting)

    result = runner.invoke(app, ["ingest", "inbox", "--auto"])

    assert result.exit_code == 0
    assert len(calls) == 1
    assert (tmp_path / ".openkos" / "fts.db").is_file()


def test_fts_build_failure_degrades_and_never_fails_the_ingest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The end-of-run FTS build is FAIL-OPEN, exactly like the embed: the
    Source and its concepts are committed by the time it runs, so a build
    failure costs one stderr advisory naming `openkos reindex`, never the
    exit code and never the ingest itself (issue #553; advisory wording
    unified by #640's shared refresh helper)."""
    _init_workspace(tmp_path, monkeypatch)
    source = tmp_path / "notes.txt"
    source.write_text("Some raw notes.", encoding="utf-8")

    def boom(bundle_dir: Path, fts_db_path: Path, *, force: bool) -> None:
        raise state_fts.FtsUnavailable("fts5 module unavailable")

    monkeypatch.setattr(state_reindex, "_reindex_fts", boom)

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    assert (tmp_path / "bundle" / "sources" / "notes.md").is_file()
    assert "derived-index refresh incomplete" in result.stderr
    assert "openkos reindex" in result.stderr


# --- Wrong-language drop notice (#618) ---------------------------------------


def test_ingest_wrong_language_drop_notice_names_the_dropped_titles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A chunked Spanish source whose window emits the harmful class (a
    translatable title rendered in English, not quoted from the prose)
    drops that candidate AND says so on stderr -- a silent deterministic
    drop would be the same disclosure defect the cap notice fixed (#404)."""
    _init_workspace(tmp_path, monkeypatch)
    _set_config_field(tmp_path, "# union_judge: true", "union_judge: false")
    lines = [
        "Ana: Revisamos el avance del proyecto y las decisiones pendientes "
        "sobre la capa de almacenamiento con el equipo de datos.",
        "Bruno: La migración terminó y los índices se regeneran con el "
        "modelo nuevo; la búsqueda mejoró bastante en las pruebas.",
    ]
    blocks: list[str] = []
    while sum(len(b) + 1 for b in blocks) <= 19_000:
        blocks.append(f"{lines[len(blocks) % 2]} (bloque {len(blocks)})")
    text = "\n".join(blocks)
    assert len(text) > concept_mod._CHUNK_THRESHOLD
    windows = concept_mod._chunk_lines(text)
    replies: list[str | Exception] = ["[]"] * len(windows)
    replies[0] = "[" + _concept_reply(title="Procedimiento de ingesta") + "]"
    replies[1] = "[" + _concept_reply(title="Recovery of Knowledge Project") + "]"
    _patch_sequenced_llm(monkeypatch, replies)
    source = tmp_path / "notas.txt"
    source.write_text(text, encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notas.txt", "--auto"])

    assert result.exit_code == 0
    assert (
        "dropped 1 wrong-language title(s) not quoted from the source: "
        "Recovery of Knowledge Project" in result.stderr
    )
    assert (tmp_path / "bundle" / "concepts" / "procedimiento-de-ingesta.md").exists()
    assert not (
        tmp_path / "bundle" / "concepts" / "recovery-of-knowledge-project.md"
    ).exists()


def test_participant_unreadmitted_notice_names_the_real_second_decision() -> None:
    """Issue #690: the engine has known since #668 which participant
    candidates re-admission declined to restore --
    `participant_unreadmitted_discarded_titles` -- and no caller had ever
    read it. The operator saw only `judge dropped 2 candidate(s): ...` and
    reasonably concluded the judge rejected them on merit.

    Two different findings with two different remedies, and reporting the
    first when the second is true is what made the earlier runs
    unfalsifiable.

    #712 changed WHICH second decision this can be. With the anchor gate
    retired, a participant lands here for exactly one reason -- the source is
    not meeting-shaped -- so the notice must say that. Asserting the old
    wording ("no role, affiliation, or relation cue") would pin an
    explanation that now sends the operator hunting for a cue no production
    code reads."""
    report = concept_mod.ExtractionReport(
        produced=9,
        retained=9,
        judge_status="ok",
        judged_out_titles=("Jason Sepulveda", "Gustavo Martinez"),
        participant_unreadmitted_discarded_titles=(
            "Jason Sepulveda",
            "Gustavo Martinez",
        ),
    )

    notice = main._participant_unreadmitted_notice(report)

    assert notice is not None
    assert "not meeting-shaped" in notice
    assert "not re-admitted after the judge dropped them" in notice
    assert "role, affiliation, or relation cue" not in notice
    assert "Jason Sepulveda" in notice
    assert "Gustavo Martinez" in notice
    assert "2 participant candidate(s)" in notice


def test_participant_unreadmitted_notice_is_silent_when_the_gate_discarded_none() -> (
    None
):
    """A run whose participants were selected or re-admitted says nothing
    extra -- the healthy path stays quiet, like every other notice here."""
    report = concept_mod.ExtractionReport(
        produced=4,
        retained=4,
        judge_status="ok",
        judged_out_titles=("Some Concept",),
    )

    assert main._participant_unreadmitted_notice(report) is None


def test_participant_unreadmitted_notice_truncates_past_the_title_limit() -> None:
    """A meeting naming a dozen people can discard a dozen stubs, and the
    notice caps the list at `_CAP_NOTICE_TITLE_LIMIT` like every other
    notice here -- the COUNT stays exact and the remainder is disclosed as
    `(+N more)` rather than silently dropped.

    Coverage gap named by PR #705's four-lens review: the truncation branch
    had no test, so a change that dropped the remainder entirely would have
    read as a passing suite."""
    titles = tuple(f"Person {index}" for index in range(1, 6))
    report = concept_mod.ExtractionReport(
        produced=9,
        retained=9,
        judge_status="ok",
        judged_out_titles=titles,
        participant_unreadmitted_discarded_titles=titles,
    )

    notice = main._participant_unreadmitted_notice(report)

    assert notice is not None
    assert "5 participant candidate(s)" in notice
    assert "Person 1, Person 2, Person 3 (+2 more)" in notice
    assert "Person 4" not in notice


def test_ingest_reports_the_unreadmitted_participant_when_the_judge_drops_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The notice reaches stderr on a REAL `ingest` run, not only from its
    helper (PR #705's review: the helper was unit-tested, the render path
    was not -- a notice computed and never printed is the same defect the
    engine already had, one layer up).

    The source is deliberately NOT meeting-shaped. #712 retired the anchor
    gate, so on a transcript every participant is now re-admitted and this
    notice correctly never fires there. The remaining path to it is the SCOPE
    half of the conjunct: an ordinary document whose extraction proposed a
    `Person`, which the judge then dropped. BOTH notices must appear, because
    the two decisions are the finding."""
    _init_workspace(tmp_path, monkeypatch)
    judge_reply = json.dumps({"keep": ["Ingestion Backlog"]})
    person_reply = json.dumps(
        [
            {
                "type": "Person",
                "title": "Ana",
                "description": "Ana.",
                "body": "",
            }
        ]
    )
    _patch_sequenced_llm(
        monkeypatch,
        [
            "[" + _concept_reply(title="Ingestion Backlog") + "]",
            person_reply,
            judge_reply,
        ],
    )
    source = tmp_path / "notes.txt"
    source.write_text(
        "A short article about the ingestion backlog and how it is worked "
        "through, written up by Ana for the team handbook.",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    assert "1 participant candidate(s) not re-admitted" in result.stderr
    assert "not meeting-shaped" in result.stderr
    assert "Ana" in result.stderr
    assert "judge dropped" in result.stderr


# ---------------------------------------------------------------------------
# #701 -- the extraction phases reach the spinner instead of a static line
# ---------------------------------------------------------------------------


def test_ingest_updates_the_spinner_with_each_extraction_phase(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point of the seam, end to end.

    Before #701 a 4m 28s ingest showed ONE line for its entire duration
    while a dozen model calls ran underneath it. This asserts the phases the
    extractor now reports actually arrive at the live status object, through
    the real CLI, rather than only being emitted somewhere in the library.
    """
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, _concept_reply())
    _FakeConsole.instances.clear()
    monkeypatch.setattr(main, "Console", _FakeConsole)
    # `CliRunner` swaps `sys.stderr` for its own wrapper, so patching the
    # module-level object has no effect inside `invoke` -- patch the CLASS,
    # the convention every other TTY-gated CLI test here follows.
    monkeypatch.setattr(_NamedTextIOWrapper, "isatty", lambda self: True)
    source = tmp_path / "notes.txt"
    source.write_text("Some raw notes about self-control.", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    status = _FakeConsole.instances[0].statuses[0]
    assert status.updates, "the spinner was never updated -- still one static line"
    assert all(u.startswith("openkos ingest: ") for u in status.updates)
    assert any("extracting pass 1/2" in u for u in status.updates)


def test_ingest_leaves_the_spinner_alone_when_stderr_is_not_a_tty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A piped run passes NO hook, so the extractor makes no per-phase call
    at all.

    This is the property the issue asked to preserve: the spinner is
    stderr-only and no-ops when output is piped, so stdout stays clean for
    scripting. `phase_callback` returning `None` is what enforces it at the
    seam rather than at every emission site.
    """
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, _concept_reply())
    _FakeConsole.instances.clear()
    monkeypatch.setattr(main, "Console", _FakeConsole)
    monkeypatch.setattr(_NamedTextIOWrapper, "isatty", lambda self: False)
    source = tmp_path / "notes.txt"
    source.write_text("Some raw notes about self-control.", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    assert _FakeConsole.instances[0].statuses[0].updates == []


def test_participant_ungrounded_notice_names_the_source_not_the_model() -> None:
    """#712 D5: the advisory reaches the operator, or it is not an advisory.

    Wording matters here. The retired anchor gate reported a candidate as
    lacking a 'role, affiliation, or relation cue', which pointed the
    operator at a lexicon. This one points at the SOURCE, because that is
    the only text the check consulted and the only place the operator can
    go to confirm or refute it."""
    report = concept_mod.ExtractionReport(
        produced=4,
        retained=4,
        judge_status="ok",
        participant_names_absent_from_source=("Nadie Real",),
    )

    notice = main._participant_ungrounded_notice(report)

    assert notice is not None
    assert "Nadie Real" in notice
    assert "source" in notice
    assert "1 participant" in notice


def test_participant_ungrounded_notice_is_silent_when_every_name_is_grounded() -> None:
    """The healthy path says nothing, like every other notice here."""
    report = concept_mod.ExtractionReport(produced=4, retained=4, judge_status="ok")

    assert main._participant_ungrounded_notice(report) is None


# --- concurrent_extraction reaches BOTH extractors (#744) --------------------


def _capturing_extractor(seen: dict[str, object]) -> object:
    """A stand-in extractor that records the `concurrent` it was handed."""

    def _extractor(
        source_text: str,
        *,
        source_title: str,
        llm: object,
        on_progress: object = None,
        concurrent: bool = False,
    ) -> concept_mod.ExtractionOutcome:
        seen["concurrent"] = concurrent
        return concept_mod.ExtractionOutcome(
            objects=[],
            report=concept_mod.ExtractionReport(
                produced=0, retained=0, chunks=1, runs=1
            ),
        )

    return _extractor


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("union_judge", [True, False])
def test_stage_derived_objects_forwards_concurrent_extraction_from_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    enabled: bool,
    union_judge: bool,
) -> None:
    """`cfg.concurrent_extraction` reaches whichever extractor `union_judge`
    selected.

    Parametrized over BOTH extractors deliberately: `cli/main.py` picks
    between them on an unrelated setting, so a lever wired into one only
    would make whether #744 is active depend on `union_judge` -- the exact
    arm-inert defect `_chunk_threshold_for`'s one-definition rule exists to
    prevent, and one no end-to-end assertion about a single path can see.
    """
    seen: dict[str, object] = {}
    target = "extract_concept_union" if union_judge else "extract_concept"
    monkeypatch.setattr(main, target, _capturing_extractor(seen))

    main._stage_derived_objects(
        **_stage_kwargs(  # type: ignore[arg-type]
            tmp_path,
            cfg=_default_cfg(concurrent_extraction=enabled),
            union_judge=union_judge,
        )
    )

    assert seen["concurrent"] is enabled


def test_stage_derived_objects_degrades_when_a_concurrent_window_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The CLI's `OllamaError` degrade path must still hold through the REAL
    concurrent fan-out, not just through a stubbed extractor.

    The wiring test above replaces the extractor entirely, so it proves the
    flag arrives and nothing about what happens when a window fails once the
    flag is honoured. Here a genuinely chunked source runs the real
    `_fan_out_windows` with one window raising: the command must keep the
    Source and report the skip, never surface a partial extraction and never
    raise. Which window fails is keyed on CONTENT, because under concurrency
    call order is not the test's to decide.

    What this does NOT prove is that the windows overlapped: a serial run of
    the same source fails identically, which is the point -- the contract is
    supposed to be schedule-independent. The barrier test in
    `tests/unit/extraction/test_concept.py` is the one that can see whether
    concurrency happened at all.
    """

    class _FailingWindowLLM:
        locality = LOCAL_BACKEND_LOCALITY

        def __init__(self, doomed: str) -> None:
            self._doomed = doomed
            self.calls = 0
            self._lock = threading.Lock()

        def chat(self, messages: Sequence[Message]) -> str:
            with self._lock:
                self.calls += 1
            if self._doomed in messages[1]["content"]:
                raise OllamaUnavailable("backend down")
            return "[]"

        def embed(self, texts: Sequence[str]) -> list[list[float]]:
            return [[0.0] * EMBED_DIM for _ in texts]

    text = "\n".join(f"A: line {i:04d} " + "x" * 30 for i in range(700))
    windows = concept_mod._chunk_lines(text)
    assert len(windows) > concept_mod.FAN_OUT_CONCURRENCY
    llm = _FailingWindowLLM(windows[1])

    plans, skip_reason, notice = main._stage_derived_objects(
        **_stage_kwargs(  # type: ignore[arg-type]
            tmp_path,
            raw_content=text,
            llm=llm,
            cfg=_default_cfg(concurrent_extraction=True),
            union_judge=False,
        )
    )

    assert plans == []
    assert skip_reason == "failed"
    assert notice is None
    assert "keeping the Source only" in capsys.readouterr().err


# --- a timeout on the concurrent path names the queuing risk (#746) ----------


class _TimingOutLLM:
    locality = LOCAL_BACKEND_LOCALITY

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def chat(self, messages: Sequence[Message]) -> str:
        raise self._exc

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [[0.0] * EMBED_DIM for _ in texts]


def _timeout_exc() -> OllamaUnavailable:
    try:
        raise OllamaUnavailable("Ollama not reachable at localhost") from TimeoutError(
            "timed out"
        )
    except OllamaUnavailable as exc:
        return exc


def _chunked_text() -> str:
    return "\n".join(f"A: line {i:04d} " + "x" * 30 for i in range(700))


def test_timeout_on_the_concurrent_path_names_queuing_and_both_exits(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A timeout during a concurrent chunked run must say WHY it might be one.

    The engine knows both halves -- that the run was concurrent, and that the
    failure was the deadline -- and before #746 it said neither, leaving an
    operator to connect a timeout to a setting they had changed. The advisory
    names the interaction and BOTH exits, following the same
    name-the-resolving-verb convention `doctor` already uses.
    """
    plans, skip_reason, _notice = main._stage_derived_objects(
        **_stage_kwargs(  # type: ignore[arg-type]
            tmp_path,
            raw_content=_chunked_text(),
            llm=_TimingOutLLM(_timeout_exc()),
            cfg=_default_cfg(concurrent_extraction=True),
            union_judge=False,
        )
    )

    err = capsys.readouterr().err
    assert plans == []
    assert skip_reason == "failed"
    assert "keeping the Source only" in err
    assert "concurrent_extraction" in err
    assert "OLLAMA_NUM_PARALLEL" in err


def test_no_queuing_advisory_when_the_failure_is_not_a_timeout(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A refused connection is not a deadline. Blaming queuing here would send
    the operator after a concurrency setting while their server is simply not
    running -- worse than saying nothing."""
    refused = None
    try:
        raise OllamaUnavailable("not reachable") from urllib.error.URLError(
            ConnectionRefusedError("refused")
        )
    except OllamaUnavailable as exc:
        refused = exc

    main._stage_derived_objects(
        **_stage_kwargs(  # type: ignore[arg-type]
            tmp_path,
            raw_content=_chunked_text(),
            llm=_TimingOutLLM(refused),
            cfg=_default_cfg(concurrent_extraction=True),
            union_judge=False,
        )
    )

    assert "concurrent_extraction" not in capsys.readouterr().err


def test_no_queuing_advisory_when_concurrency_was_never_enabled(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A serial run's timeout has nothing to do with #744, so the advisory
    must not fire on the DEFAULT path -- where most timeouts will happen."""
    main._stage_derived_objects(
        **_stage_kwargs(  # type: ignore[arg-type]
            tmp_path,
            raw_content=_chunked_text(),
            llm=_TimingOutLLM(_timeout_exc()),
            cfg=_default_cfg(concurrent_extraction=False),
            union_judge=False,
        )
    )

    assert "concurrent_extraction" not in capsys.readouterr().err


def test_no_queuing_advisory_when_the_source_never_fanned_out(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A source below the chunking threshold has no windows to overlap, so
    concurrency was not involved even with the flag on. This is the arm a
    naive `if cfg.concurrent_extraction` check would get wrong."""
    main._stage_derived_objects(
        **_stage_kwargs(  # type: ignore[arg-type]
            tmp_path,
            raw_content="Short notes about self-control.",
            llm=_TimingOutLLM(_timeout_exc()),
            cfg=_default_cfg(concurrent_extraction=True),
            union_judge=False,
        )
    )

    assert "concurrent_extraction" not in capsys.readouterr().err


def test_ingest_judge_unavailable_notice_names_both_compounding_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#754: the old wording -- "kept the full merged extraction union (20
    object(s)) unfiltered" -- is accurate and still misleads. It does not say
    that NO QUALITY GATE RAN, and it does not say that a positional cap then
    cut the unranked set by arrival order.

    The second half is now false (the cap is skipped, #754), so the notice
    must state both: no selection happened, and nothing was discarded for
    it. `_extraction_cap_notice` must stay silent, since there is no longer
    anything for it to report."""
    _init_workspace(tmp_path, monkeypatch)
    run1 = _concept_reply(title="Stoic Dichotomy Of Control")
    run2 = _concept_reply(title="Negative Visualization")
    # TWO failures: the retry (#754) must be exhausted before the degrade.
    _patch_sequenced_llm(
        monkeypatch,
        [run1, run2, OllamaUnavailable("boom"), OllamaUnavailable("boom")],
    )
    source = tmp_path / "notes.txt"
    source.write_text("Some raw notes about self-control.", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    assert "judge selection unavailable" in result.stderr
    assert "no quality selection ran" in result.stderr
    assert "cap reached" not in result.stderr
    assert "judge dropped" not in result.stderr
