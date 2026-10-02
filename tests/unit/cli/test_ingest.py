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
from collections.abc import Sequence
from pathlib import Path
from typing import ClassVar

import pytest
from typer.testing import CliRunner, _NamedTextIOWrapper

from openkos import config
from openkos.application import ingest as application_ingest
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
    echo_after,
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


_CONCEPT_BODY_LINE = "Elaboration on applying the framework day to day."
"""The body every `_concept_reply` carries, named once so a fixture source
can QUOTE it. Since #801 an object whose written text reproduces no line of
its source is disclosed on the Source, and a healthy-path fixture whose
source says nothing the object quotes is not a healthy path -- it is #801's
defect, and it would stamp `objects-without-evidence` onto every test that
means to assert a clean run. Two copies of this string is how a later edit
to one silently re-breaks those tests, so there is one."""


_GROUNDED_NOTES = f"Some raw notes about self-control.\n{_CONCEPT_BODY_LINE}\n"
"""A source text a `_concept_reply` object genuinely quotes -- the fixture
for every test that asserts a run finished with NO `extraction_notice`."""


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
            "body": _CONCEPT_BODY_LINE,
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


def _patch_sequenced_llm(
    monkeypatch: pytest.MonkeyPatch, replies: Sequence[str | Exception]
) -> _SequencedLLM:
    """Replace `openkos.cli.main.OllamaClient` with a factory returning a
    `_SequencedLLM`, mirroring `_patch_llm`'s pattern for a fixed-reply fake."""
    fake = _SequencedLLM(replies)
    monkeypatch.setattr("openkos.cli.main.OllamaClient", lambda *args, **kwargs: fake)
    return fake


_HELIOS_NOTES = (
    "Priya owns the schema migration plan.\n"
    "The team chose PostgreSQL as the primary datastore.\n"
)
"""#801's source, reduced to the two lines that decide the check."""


def _ungrounded_decision_reply() -> str:
    """#801's actual stored object: its body restates its own description
    and quotes no line of the source."""
    return json.dumps(
        {
            "type": "Decision",
            "title": "Schema Migration Ownership Decision",
            "description": "Who owns the schema migration plan.",
            "body": (
                "The decision regarding who owns the schema migration plan "
                "for Project Helios."
            ),
        }
    )


def _grounded_decision_reply() -> str:
    """A sibling object from the same source that DOES quote a line of it --
    the seven-of-eight case #801 measured."""
    return json.dumps(
        {
            "type": "Decision",
            "title": "Primary Datastore Decision",
            "description": "The datastore the team settled on.",
            "body": "The team chose PostgreSQL as the primary datastore.",
        }
    )


def test_batch_reingest_of_extracted_source_skips_extraction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#773's reported reproduction ran through the DIRECTORY batch path, so
    the skip must hold there too: re-ingesting a directory holding one
    unchanged, already-extracted source spends zero model calls and reports
    the file as re-ingested with extraction skipped."""
    _init_workspace(tmp_path, monkeypatch)
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    run1 = _concept_reply(title="Stoic Dichotomy Of Control")
    run2 = _concept_reply(title="Negative Visualization")
    _patch_sequenced_llm(
        monkeypatch,
        [
            run1,
            run2,
            '{"keep": ["Stoic Dichotomy Of Control", "Negative Visualization"]}',
        ],
    )
    (corpus / "notes.md").write_text(
        "Some raw notes about self-control.", encoding="utf-8"
    )
    result = runner.invoke(app, ["ingest", "corpus", "--auto"])
    assert result.exit_code == 0

    fake = _patch_sequenced_llm(monkeypatch, [])
    result = runner.invoke(app, ["ingest", "corpus", "--auto"])

    assert result.exit_code == 0
    assert fake.calls == []
    assert "1 re-ingested" in result.stdout
    assert "extraction skipped" in result.stdout


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
    """The proposal lists the Source path plus every staged derived object's
    path (0..N); the post-confirm line is a summary that does not repeat them
    (ADR-0040)."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, _multi_object_reply(_concept_reply(), _person_reply()))
    source = tmp_path / "notes.txt"
    source.write_text("content", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    lines = result.stdout.splitlines()
    for path in (
        "raw/notes.txt",
        "bundle/sources/notes.md",
        "bundle/concepts/stoic-dichotomy-of-control.md",
        "bundle/people/epictetus.md",
    ):
        assert f"  + {path}" in lines
    assert (
        "openkos ingest: imported 'notes.txt' -- 2 objects (1 Concept, 1 Person)."
        in (lines)
    )
    assert not any("imported" in line and "->" in line for line in lines)


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
    assert "openkos ingest: imported 'notes.txt' -- 1 object (1 Concept)." in (
        result.stdout
    )


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
    assert (
        "openkos ingest: imported 'notes.txt' -- 3 objects "
        "(1 Concept, 1 Event, 1 Person)." in result.stdout
    )


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


# --- Embed construction routes through the resolver seam (#1057 Phase 10) --


def test_single_file_ingest_embed_sites_use_the_embed_client_delegator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A single-file `ingest --auto` builds its embedding client through
    `cli.main._embed_client(cfg)` -- the one-line delegator over
    `application_backends.embed_client` (issue #1057 Phase 10, tasks
    10.3-10.4) -- at BOTH of its two embed sites: `_ingest_single`'s own
    embed (issue #183) and the end-of-run `_refresh_derived_after_write`
    call. Confirmed by spying on `main._embed_client` rather than on
    `OllamaClient` directly, so the assertion pins the DELEGATOR, not merely
    that some client was built. **RED today**: both sites still construct
    `OllamaClient(model=cfg.embedding_model)` directly, so `_embed_client`
    is never called."""
    _init_workspace(tmp_path, monkeypatch)
    fake = _EmbeddingLLM(_concept_reply())
    monkeypatch.setattr("openkos.cli.main.OllamaClient", lambda *a, **k: fake)
    calls: list[object] = []
    original_embed_client = main._embed_client

    def _spy(cfg: object) -> object:
        calls.append(cfg)
        return original_embed_client(cfg)  # type: ignore[arg-type]

    monkeypatch.setattr(main, "_embed_client", _spy)
    src = tmp_path / "note.md"
    src.write_text("# Note\n\nRaw material.\n", encoding="utf-8")

    result = runner.invoke(app, ["ingest", str(src), "--auto"])

    assert result.exit_code == 0, result.stdout
    # `_ingest_single`'s own embed, plus `_refresh_derived_after_write`'s.
    assert len(calls) == 2


def test_batch_ingest_embed_sites_use_the_embed_client_delegator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A DIRECTORY (batch-mode) `ingest --auto` builds its embedding client
    through `_embed_client` at every one of its embed sites: the
    once-per-batch embedding-host advisory (`_ingest_batch`, issue #1057
    Phase 10, tasks 10.5-10.6), the per-file `_ingest_single` call the
    batch loop invokes, and the batch's own end-of-run
    `_refresh_derived_after_write` call. A batch of exactly one file makes
    the expected count exact: one advisory call, one per-file call, one
    end-of-run refresh call. **RED today**: the advisory site still
    constructs `OllamaClient(model=cfg.embedding_model).locality` directly."""
    _init_workspace(tmp_path, monkeypatch)
    fake = _EmbeddingLLM(_concept_reply())
    monkeypatch.setattr("openkos.cli.main.OllamaClient", lambda *a, **k: fake)
    calls: list[object] = []
    original_embed_client = main._embed_client

    def _spy(cfg: object) -> object:
        calls.append(cfg)
        return original_embed_client(cfg)  # type: ignore[arg-type]

    monkeypatch.setattr(main, "_embed_client", _spy)
    batch_dir = tmp_path / "notes"
    batch_dir.mkdir()
    (batch_dir / "note.md").write_text("# Note\n\nRaw material.\n", encoding="utf-8")

    result = runner.invoke(app, ["ingest", str(batch_dir), "--auto"])

    assert result.exit_code == 0, result.stdout
    assert len(calls) == 3


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
            generated=okf.Generated(by="openkos/test", at="2026-08-12T00:00:00Z"),
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
    """Before any LLM contact, the batch prints `{n} file(s) -> ~{k} LLM
    call(s)` to stderr and asks ONE up-front confirmation; a `y` answer
    covers every file -- the per-file prompt is suppressed the way `--auto`
    suppresses it today, so a single `y` on stdin completes a two-file
    batch (issue #267, settled decision 4). Since #775 the count is a
    fan-out-aware ESTIMATE, labelled as one: two small union-path prose
    files cost ~3 calls each (two extraction passes plus the judge), never
    "one extraction per file"."""
    _init_workspace(tmp_path, monkeypatch)
    _write_notes(tmp_path, {"a.txt": "Alpha notes.", "b.txt": "Beta notes."})
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["ingest", "notes"], input="y\n")

    assert result.exit_code == 0
    assert "2 file(s) -> ~6 LLM call(s)" in result.stderr
    assert "estimate; this can take a while" in result.stderr
    assert (tmp_path / "raw" / "a.txt").is_file()
    assert (tmp_path / "raw" / "b.txt").is_file()
    # ONE gate: the batch prompt appears exactly once and the single-file
    # "Proceed with these changes?" prompt never does.
    assert "Proceed with these changes?" not in result.output
    assert result.output.count("Proceed") == 1


def test_batch_cost_gate_counts_windows_for_a_chunking_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#775's reported defect: a source above its chunking threshold takes
    one extraction call PER WINDOW, and the old gate still announced one
    call per file (announced 3, made ~16). The gate must announce a
    fan-out-aware estimate and say which source will split."""
    _init_workspace(tmp_path, monkeypatch)
    line = "La plataforma registra la decision y su justificacion en el acta.\n"
    big = line * (30_000 // len(line) + 1)
    _write_notes(tmp_path, {"small.txt": "Alpha notes.", "big.txt": big})
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["ingest", "notes"], input="n\n")

    windows = len(concept_mod._chunk_lines(big))
    assert windows > 1
    expected = 3 + (windows + 1)  # small: 2 passes + judge; big: windows + judge
    assert f"1 will be split into ~{windows} window(s)" in result.stderr
    assert f"~{expected} LLM call(s)" in result.stderr


def test_batch_cost_gate_counts_zero_for_a_convergent_reingest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#773 x #775: a file the convergence skip will not extract must not be
    billed by the gate either -- the operator consenting to a re-run of an
    unchanged corpus is consenting to ~0 calls, and the gate says so."""
    _init_workspace(tmp_path, monkeypatch)
    corpus = tmp_path / "notes"
    corpus.mkdir()
    (corpus / "a.md").write_text("Some raw notes about self-control.", encoding="utf-8")
    run1 = _concept_reply(title="Stoic Dichotomy Of Control")
    run2 = _concept_reply(title="Negative Visualization")
    _patch_sequenced_llm(
        monkeypatch,
        [
            run1,
            run2,
            '{"keep": ["Stoic Dichotomy Of Control", "Negative Visualization"]}',
        ],
    )
    assert runner.invoke(app, ["ingest", "notes", "--auto"]).exit_code == 0

    fake = _patch_sequenced_llm(monkeypatch, [])
    _simulate_tty(monkeypatch)
    result = runner.invoke(app, ["ingest", "notes"], input="y\n")

    assert result.exit_code == 0
    assert fake.calls == []
    assert "1 unchanged -- extraction will be skipped" in result.stderr
    assert "~0 LLM call(s)" in result.stderr
    # #872: a gate consenting to ~0 calls has nothing slow to warn about --
    # the pace clause is priced for the paid path only. The positive half
    # pins the rendered parenthetical, so an empty or malformed `()` cannot
    # pass on the absence assertion alone.
    assert "~0 LLM call(s) (estimate). Pass --auto to skip this prompt." in (
        result.stderr
    )
    assert "this can take a while" not in result.stderr


def test_batch_cost_gate_bills_zero_for_blank_and_undecodable_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#775: files the pipeline will never send to the model are billed at
    zero -- a blank source (no extractable text) and an undecodable one
    (Source-only degrade). One healthy small file keeps the total honest:
    only ITS ~3 calls are announced."""
    _init_workspace(tmp_path, monkeypatch)
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "real.txt").write_text("Alpha notes.", encoding="utf-8")
    (notes / "blank.txt").write_text("   \n", encoding="utf-8")
    (notes / "binary.txt").write_bytes(b"\xff\xfe\x00garbage\x00")
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["ingest", "notes"], input="n\n")

    assert "3 file(s)" in result.stderr
    assert "~3 LLM call(s)" in result.stderr


def test_batch_cost_gate_bills_a_legacy_encoded_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#1224: a legacy-encoded text file is extracted, so the gate bills it
    (it used to be billed zero as "undecodable")."""
    _init_workspace(tmp_path, monkeypatch)
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "legacy.txt").write_bytes("Reuni\u00f3n\rok\r".encode("mac_roman"))
    (notes / "real.txt").write_text("Alpha notes.", encoding="utf-8")
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["ingest", "notes"], input="n\n")

    assert "~6 LLM call(s)" in result.stderr


def test_estimate_bills_an_unreadable_file_at_the_unchunked_cost(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#775's fail-open mandate, tested at the helper because batch
    expansion already drops a file unreadable at expansion time -- the
    OSError branch guards the RACE where a file turns unreadable between
    expansion and the gate. Such a file is billed at the unchunked ordinary
    cost (~3 on the union path), never silently dropped to zero."""
    if os.name != "posix" or os.geteuid() == 0:
        pytest.skip("permission-based unreadability needs a non-root POSIX user")
    _init_workspace(tmp_path, monkeypatch)
    locked = tmp_path / "locked.txt"
    locked.write_text("Beta notes.", encoding="utf-8")
    locked.chmod(0)
    layout = config.WorkspaceLayout(tmp_path)
    cfg = config.read_config(tmp_path)
    try:
        estimate = main._estimate_batch_calls(
            [locked], layout, cfg, include_confidential=False, re_extract=False
        )
    finally:
        locked.chmod(0o644)

    assert estimate.calls == 3
    assert estimate.skipped_files == 0


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


def test_batch_summary_counts_files_that_finished_with_an_extraction_notice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The batch summary carries a term for `extraction_notice` (issue
    #805, item 1).

    The summary is deliberately the run's last word (#349), and it had no
    field for the notice at all, while the notice's own stderr line can sit
    seventeen minutes upstream in a long batch. `extraction-degraded`
    cannot absorb it: that term counts a `skip_reason` (Source-only), and
    `_stage_derived_objects` returns `skip_reason` and `extraction_notice`
    on mutually exclusive paths -- zero derived objects versus at least
    one -- so the two counts never overlap.

    Two files, one notice: the term must DISCRIMINATE, not count every
    file that finished. `a.txt`'s judge is unusable on both attempts
    (#754), which quarantines its Source; `b.txt`'s judge answers, so that
    file carries nothing."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_sequenced_llm(
        monkeypatch,
        [
            # a.txt: two distinct candidates, then a judge that fails every
            # attempt -> `extraction_notice: judge-selection-unavailable`.
            _concept_reply(title="Stoic Dichotomy Of Control"),
            _concept_reply(title="Negative Visualization"),
            OllamaUnavailable("boom"),
            OllamaUnavailable("boom"),
            # b.txt: the judge replies and admits both -- no notice.
            _concept_reply(title="Morning Review Ritual"),
            _concept_reply(title="Evening Review Ritual"),
            json.dumps({"keep": ["Morning Review Ritual", "Evening Review Ritual"]}),
        ],
    )
    # `b.txt` is grounded (#801) so it finishes with NO notice, which is
    # what makes the term below discriminate. `a.txt` is left ungrounded:
    # its judge token outranks the evidence one, so it still carries
    # exactly `judge-selection-unavailable`.
    _write_notes(
        tmp_path,
        {"a.txt": "Alpha notes.", "b.txt": f"Beta notes.\n{_CONCEPT_BODY_LINE}\n"},
    )

    result = runner.invoke(app, ["ingest", "notes", "--auto"])

    assert result.exit_code == 0
    a_metadata, _ = okf.load_frontmatter(
        (tmp_path / "bundle" / "sources" / "a.md").read_text(encoding="utf-8")
    )
    b_metadata, _ = okf.load_frontmatter(
        (tmp_path / "bundle" / "sources" / "b.md").read_text(encoding="utf-8")
    )
    assert "judge-selection-unavailable" in okf.extraction_notices(a_metadata)
    assert "extraction_notice" not in b_metadata
    assert (
        "2 file(s): 2 ingested, 0 re-ingested, 0 skipped, 0 extraction-degraded, "
        "1 with extraction notice(s)." in result.stdout
    )


def test_batch_summary_counts_a_converged_reingest_that_still_carries_a_notice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The term counts what the Source CARRIES when the run ends, not what
    the run stamped -- so #773's convergence short-circuit must still be
    counted (issue #805, item 1).

    A byte-identical re-ingest of a source whose previous extraction ran to
    its intended conclusion writes nothing at all: `_ingest_single` returns
    before staging, so no `extraction_notice` is computed. But the Source it
    deliberately left untouched on disk still carries the PRIOR run's
    marker, and #585's `sole-object-restates-source` is exactly the token
    that survives there -- `_extraction_retry_due` sends the two judge
    tokens back through a full extraction, so the disclosure is the one
    notice a converged re-ingest can still be sitting on.

    That is the case the summary exists for. The disclosure was echoed to
    stderr on a run that may have happened days ago; the operator re-running
    the batch today sees only this line, and a zero here would tell them the
    bundle is clean when it is not."""
    _init_workspace(tmp_path, monkeypatch)
    twin = _concept_reply(title="Replica Lag")
    _patch_sequenced_llm(
        monkeypatch,
        [
            # replica-lag.txt: both extraction runs return the same object,
            # and it restates the source -> `sole-object-restates-source`.
            twin,
            twin,
            "[]",
            '{"keep": ["Replica Lag"]}',
            # zeta.txt: two distinct objects the judge admits -> no notice,
            # so the term has to DISCRIMINATE on the re-ingest too.
            _concept_reply(title="Morning Review Ritual"),
            _concept_reply(title="Evening Review Ritual"),
            "[]",
            json.dumps({"keep": ["Morning Review Ritual", "Evening Review Ritual"]}),
        ],
    )
    _write_notes(
        tmp_path,
        {
            "replica-lag.txt": (
                "Replica Lag\n\nA replica trails its primary, and a read "
                "routed to it can miss a write the client just made.\n"
            ),
            "zeta.txt": (
                # Grounded (#801) so this file finishes with no notice and
                # the term below still counts exactly one.
                f"Notes on two separate review rituals.\n{_CONCEPT_BODY_LINE}\n"
            ),
        },
    )
    first = runner.invoke(app, ["ingest", "notes", "--auto"])
    assert first.exit_code == 0
    source_path = tmp_path / "bundle" / "sources" / "replica-lag.md"
    first_metadata, _ = okf.load_frontmatter(source_path.read_text(encoding="utf-8"))
    assert "sole-object-restates-source" in okf.extraction_notices(first_metadata)
    before = source_path.read_bytes()

    result = runner.invoke(app, ["ingest", "notes", "--auto"])

    assert result.exit_code == 0
    # The short-circuit really did take the untouched-on-disk path: the
    # marker is still there because nothing rewrote the document.
    assert source_path.read_bytes() == before
    assert "skipping extraction" in result.stderr
    assert (
        "2 file(s): 0 ingested, 2 re-ingested, 0 skipped, 0 extraction-degraded, "
        "1 with extraction notice(s)." in result.stdout
    )


def test_carried_extraction_notice_returns_every_vocabulary_member() -> None:
    """Issue #814, item 1, positive control: a Source carrying a token this
    build knows is reported as that exact token, now as a one-token tuple
    (#884 -- the key went plural, and a legacy scalar reads back as the
    one-element tuple a list of one would).

    Asserted over the WHOLE of `okf.EXTRACTION_NOTICE_VALUES` rather than
    one sample, so a token added to the vocabulary without a matching
    branch here is caught. This test exists to keep its fail-closed
    sibling below falsifiable -- a helper that simply returned `None`
    would satisfy the fail-closed assertions on its own."""
    for token in okf.EXTRACTION_NOTICE_VALUES:
        metadata = {okf.EXTRACTION_NOTICE_KEY: token}

        assert application_ingest.carried_extraction_notice(metadata) == (token,)


def test_carried_extraction_notice_fails_closed_outside_the_vocabulary() -> None:
    """Issue #814, item 1: both fail-closed branches the helper documents,
    neither of which any existing test reached.

    Every test added with the helper feeds a RECOGNISED token via a real
    prior run, so the contract it states in prose -- an absent key and an
    unrecognised value both narrow to `()` (#884) -- was never executed. The
    reasoning matters and is worth pinning: frontmatter is hand-editable,
    and a Source written by a LATER release may carry a token this build
    cannot spell. Neither may crash a run that is otherwise writing
    nothing, and neither may be counted under a summary term whose wording
    promises a vocabulary member.

    The unknown-token case uses a plausible future spelling rather than
    junk, because that is the case the contract was written for: a real
    token from a newer release, not a typo."""
    assert application_ingest.carried_extraction_notice({}) == ()
    assert (
        application_ingest.carried_extraction_notice(
            {okf.EXTRACTION_NOTICE_KEY: "judge-selection-postponed"}
        )
        == ()
    )
    # #884: an unknown token is dropped INDIVIDUALLY -- one unspellable
    # member from a later release never discards its recognised siblings,
    # which a whole-value reject would have done the moment the key went
    # plural.
    assert application_ingest.carried_extraction_notice(
        {
            okf.EXTRACTION_NOTICE_KEY: [
                "judge-selection-postponed",
                okf.EXTRACTION_NOTICE_OBJECTS_WITHOUT_EVIDENCE,
            ]
        }
    ) == (okf.EXTRACTION_NOTICE_OBJECTS_WITHOUT_EVIDENCE,)
    # A non-string value cannot match a token either -- frontmatter is
    # hand-editable, so `extraction_notice: true` is a YAML boolean.
    assert (
        application_ingest.carried_extraction_notice({okf.EXTRACTION_NOTICE_KEY: True})
        == ()
    )
    assert (
        application_ingest.carried_extraction_notice({okf.EXTRACTION_NOTICE_KEY: None})
        == ()
    )


def test_batch_summary_never_counts_one_file_under_both_notice_and_degraded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Issue #814, item 2: the disjointness the spec argues in prose, run.

    `openspec/specs/ingestion/spec.md` requires the notice term to never
    overlap `extraction-degraded`, on the reasoning that
    `extraction-degraded` counts a Source-only degrade (zero derived
    objects) while a notice presupposes at least one object WAS written,
    and `_stage_derived_objects` returns `skip_reason` and
    `extraction_notice` on mutually exclusive paths.

    That is argued and implemented, but no test had ever put both kinds of
    file in ONE batch, so the two counters had never been observed to be
    disjoint in practice -- exactly the kind of requirement that stays true
    silently until it does not.

    One file of each: `a.txt`'s extraction reaches the model and produces
    nothing, so it lands Source-only; `b.txt` produces two objects and then
    loses its judge, so its Source finishes carrying a notice. The summary
    is asserted as a WHOLE line rather than term by term, because the
    failure this guards against is a single file counted twice -- which
    only the full line can show, since each term alone still reads 1."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_sequenced_llm(
        monkeypatch,
        [
            # a.txt: both extraction arms decline -> zero derived objects
            # -> `skip_reason` -> extraction-degraded, and NO notice.
            '{"extract": false}',
            '{"extract": false}',
            # b.txt: two distinct candidates, then a judge unusable on every
            # attempt (#754) -> `judge-selection-unavailable`, and NOT
            # degraded, because objects were written.
            _concept_reply(title="Morning Review Ritual"),
            _concept_reply(title="Evening Review Ritual"),
            OllamaUnavailable("boom"),
            OllamaUnavailable("boom"),
        ],
    )
    _write_notes(
        tmp_path,
        {"a.txt": "Alpha notes.", "b.txt": "Notes on two separate review rituals."},
    )

    result = runner.invoke(app, ["ingest", "notes", "--auto"])

    assert result.exit_code == 0
    a_metadata, _ = okf.load_frontmatter(
        (tmp_path / "bundle" / "sources" / "a.md").read_text(encoding="utf-8")
    )
    b_metadata, _ = okf.load_frontmatter(
        (tmp_path / "bundle" / "sources" / "b.md").read_text(encoding="utf-8")
    )
    # The premise: one file really did degrade, the OTHER really did carry
    # a notice. Without this the summary assertion could pass on a batch
    # where neither condition occurred.
    assert "extraction_notice" not in a_metadata
    assert "judge-selection-unavailable" in okf.extraction_notices(b_metadata)
    assert (
        "2 file(s): 2 ingested, 0 re-ingested, 0 skipped, 1 extraction-degraded, "
        "1 with extraction notice(s)." in result.stdout
    )


def test_batch_summary_notice_term_pluralizes_like_every_other_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The notice term renders `N with extraction notice(s)` -- the `(s)`
    idiom this module already uses for `file(s)`, `candidate(s)` and
    `derived object(s)` (issue #805, item 1).

    The four terms beside it are bare participles (`ingested`,
    `re-ingested`, `skipped`, `extraction-degraded`), so none of them ever
    had to agree with its count. This one names a noun, and the first
    spelling put an article in front of it, which made a two-file batch
    close on `2 with an extraction notice.` -- the run's LAST word (#349),
    read by an operator who was not watching the stderr it summarizes."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_sequenced_llm(
        monkeypatch,
        [
            # Both files: two distinct candidates, then a judge that fails
            # every attempt -> `judge-selection-unavailable` on each Source.
            _concept_reply(title="Stoic Dichotomy Of Control"),
            _concept_reply(title="Negative Visualization"),
            OllamaUnavailable("boom"),
            OllamaUnavailable("boom"),
            _concept_reply(title="Morning Review Ritual"),
            _concept_reply(title="Evening Review Ritual"),
            OllamaUnavailable("boom"),
            OllamaUnavailable("boom"),
        ],
    )
    _write_notes(tmp_path, {"a.txt": "Alpha notes.", "b.txt": "Beta notes."})

    result = runner.invoke(app, ["ingest", "notes", "--auto"])

    assert result.exit_code == 0
    assert "2 with extraction notice(s)." in result.stdout
    assert "2 with an extraction notice" not in result.stdout


def test_batch_summary_notice_term_is_zero_and_silent_on_a_healthy_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The term is always present (a zero says "checked, none found",
    exactly like the four terms beside it), but the pointer clause that
    names where to recover is NOT: an advisory that fires on the healthy
    path is noise, the same rule every ingest advisory follows."""
    _init_workspace(tmp_path, monkeypatch)
    keep_reply = json.dumps({"keep": ["Stoic Dichotomy Of Control"]})
    _patch_sequenced_llm(
        monkeypatch,
        [
            _concept_reply(title="Stoic Dichotomy Of Control"),
            "[]",
            keep_reply,
            _concept_reply(title="Negative Visualization"),
            "[]",
            json.dumps({"keep": ["Negative Visualization"]}),
        ],
    )
    # Both grounded (#801): a healthy batch means zero notices of ANY kind.
    _write_notes(
        tmp_path,
        {
            "a.txt": f"Alpha notes.\n{_CONCEPT_BODY_LINE}\n",
            "b.txt": f"Beta notes.\n{_CONCEPT_BODY_LINE}\n",
        },
    )

    result = runner.invoke(app, ["ingest", "notes", "--auto"])

    assert result.exit_code == 0
    assert "0 with extraction notice(s)." in result.stdout
    assert "names all but the sole-object disclosure" not in result.stdout
    assert "`extraction_notice`" not in result.stdout


def test_batch_notice_pointer_names_what_lint_actually_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pointer must describe the `lint` this build ships (#801).

    It read "`openkos lint` names the retryable ones" while `lint` reported
    exactly the two judge tokens. That stopped being true when
    `check_unevidenced` landed: `lint` now also reports #801's token, which
    is a disclosure rather than retryable debt. A reader whose only notice
    was that one would have taken the old wording to mean `lint` had
    nothing for them, which is precisely backwards.

    The sole-object disclosure and #1053's chunk-partial disclosure remain
    the two tokens no `lint` section names, which is what the summary term
    is still wider than."""
    _init_workspace(tmp_path, monkeypatch)
    run = _multi_object_reply(_ungrounded_decision_reply(), _grounded_decision_reply())
    _patch_sequenced_llm(
        monkeypatch,
        [
            run,
            run,
            json.dumps(
                {
                    "keep": [
                        "Schema Migration Ownership Decision",
                        "Primary Datastore Decision",
                    ]
                }
            ),
        ],
    )
    _write_notes(tmp_path, {"a.txt": _HELIOS_NOTES})

    result = runner.invoke(app, ["ingest", "notes", "--auto"])

    assert result.exit_code == 0
    assert "1 with extraction notice(s)." in result.stdout
    assert "Their Sources carry `extraction_notice`" in result.stdout
    assert (
        "`openkos lint` names all but the sole-object and chunk-partial "
        "disclosures." in result.stdout
    )


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
    config_path = tmp_path / "openkos.yaml"
    hook = echo_after(
        monkeypatch,
        lambda: config_path.write_text(
            config_path.read_text(encoding="utf-8") + "\n# drifted\n",
            encoding="utf-8",
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
    # a drift-refuses: an openkos.yaml edit lands inside a's preview window
    # (exit 3); the hook fires ONCE, on a's preview -- a sorts before b.
    config_path = tmp_path / "openkos.yaml"
    hook = echo_after(
        monkeypatch,
        lambda: config_path.write_text(
            config_path.read_text(encoding="utf-8") + "\n# drifted\n",
            encoding="utf-8",
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


# --- near-boundary type reporting (#401) -------------------------------------

_NEAR_BOUNDARY_REPLY = (
    '[{"type": "Event", "title": "Hellenistic Ethics Seminar", '
    '"description": "A seminar taught this term.", "body": "", '
    '"type_alternative": "Project"}]'
)


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
    # `with`, not a bare `close()` between the search and the assertion:
    # `FtsIndex` is a context manager, and this way the connection is
    # released even when the assertion below fails -- a bare close on the
    # happy path alone would leak on exactly the runs where the leak gate
    # is loudest, and would mask the real failure behind an unraisable
    # ResourceWarning (#927).
    with index:
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


def test_main_no_longer_exposes_the_extractor_names() -> None:
    """`main` carries neither `extract_concept` nor `extract_concept_union`
    (issue #918 Slice 2, design: "Test Migration Plan") -- the extractor-
    selection line moved into `application/ingest.py::stage_derived_objects`
    with the production use, so `ruff check .` (F401) forced both imports'
    deletion, and `monkeypatch.setattr(main, "extract_concept", ...)` (the
    computed-name form this repo actually used, `test_ingest.py:8862-8863`
    pre-move) now raises `AttributeError` under pytest's default
    `raising=True` instead of silently no-opping -- confirmed empirically
    during Slice 2 apply. This test pins the absence so a future re-import
    cannot re-open that failure mode."""
    assert not hasattr(main, "extract_concept")
    assert not hasattr(main, "extract_concept_union")


# --- issue #1014c: event_date (ADR-0023, design.md Decisions 3, 5, 6, 7)


def test_ingest_rejects_invalid_event_date_flag_before_any_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ingestion spec: "An invalid calendar date is refused before any
    write" / "A malformed value is refused before any write" (design.md
    Decision 5)."""
    _init_workspace(tmp_path, monkeypatch)
    source = tmp_path / "notes.txt"
    source.write_text("Some raw notes.", encoding="utf-8")
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["ingest", "notes.txt", "--event-date", "2026-13-01"])

    assert result.exit_code == 2
    assert (
        "openkos ingest: --event-date must be a calendar date written "
        "YYYY-MM-DD, got '2026-13-01'." in result.stderr
    )
    assert _snapshot(tmp_path) == before

    result = runner.invoke(app, ["ingest", "notes.txt", "--event-date", "not-a-date"])

    assert result.exit_code == 2
    assert (
        "openkos ingest: --event-date must be a calendar date written "
        "YYYY-MM-DD, got 'not-a-date'." in result.stderr
    )
    assert _snapshot(tmp_path) == before


def test_ingest_rejects_event_date_with_a_directory_before_any_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ingestion spec: "A directory input with --event-date is refused"
    (design.md Decision 5) -- decided by shape, before any write."""
    _init_workspace(tmp_path, monkeypatch)
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "a.md").write_text("Alpha notes.", encoding="utf-8")
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["ingest", "notes", "--event-date", "2026-07-14"])

    assert result.exit_code == 2
    assert "--event-date applies to a single file" in result.stderr
    assert "notes" in result.stderr
    assert _snapshot(tmp_path) == before


def test_ingest_rejects_event_date_with_a_glob_before_any_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ingestion spec: "A glob input with --event-date is refused, even
    matching one file" (design.md Decision 5)."""
    _init_workspace(tmp_path, monkeypatch)
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "a.md").write_text("Alpha notes.", encoding="utf-8")
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["ingest", "notes/*.md", "--event-date", "2026-07-14"])

    assert result.exit_code == 2
    assert "--event-date applies to a single file" in result.stderr
    assert _snapshot(tmp_path) == before


def test_batch_cost_gate_bills_zero_for_a_converged_date_only_rewrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#775 x #1014c: a batch file whose `event_date` backfills from its
    dated name on a converged re-ingest still spends zero LLM calls
    (design.md Decision 6 makes no LLM call on that path either), so the
    cost gate's `_reingest_will_skip` estimate stays accurate even though
    the file is no longer left byte-for-byte untouched."""
    _init_workspace(tmp_path, monkeypatch)
    corpus = tmp_path / "notes"
    corpus.mkdir()
    (corpus / "call-2026-07-14.md").write_text(_GROUNDED_NOTES, encoding="utf-8")
    run1 = _concept_reply(title="Stoic Dichotomy Of Control")
    run2 = _concept_reply(title="Negative Visualization")
    _patch_sequenced_llm(
        monkeypatch,
        [
            run1,
            run2,
            '{"keep": ["Stoic Dichotomy Of Control", "Negative Visualization"]}',
        ],
    )
    assert runner.invoke(app, ["ingest", "notes", "--auto"]).exit_code == 0
    concept_path = tmp_path / "bundle" / "sources" / "call-2026-07-14.md"
    metadata, body = okf.load_frontmatter(concept_path.read_text(encoding="utf-8"))
    assert metadata["event_date"] == "2026-07-14"
    del metadata["event_date"]
    concept_path.write_text(okf.dump_frontmatter(metadata, body), encoding="utf-8")

    fake = _patch_llm(monkeypatch, raises=AssertionError("must not be called"))
    _simulate_tty(monkeypatch)
    result = runner.invoke(app, ["ingest", "notes"], input="y\n")

    assert result.exit_code == 0
    assert fake.calls == []
    assert "~0 LLM call(s)" in result.stderr
    metadata, _ = okf.load_frontmatter(concept_path.read_text(encoding="utf-8"))
    assert metadata["event_date"] == "2026-07-14"
