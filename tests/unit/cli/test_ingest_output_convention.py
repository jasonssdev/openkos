"""`ingest`'s human output follows the ADR-0042 convention.

Piped, the proposal and the notices keep their text; the post-confirm line is
a summary that no longer repeats the paths the user just approved. On a
terminal the sections are separated and notices carry a text prefix.
"""

from pathlib import Path

import pytest

from openkos.cli import main as cli_main
from openkos.cli.main import app
from tests.unit.cli.test_ingest import (
    _concept_reply,
    _init_workspace,
    _patch_llm,
    _simulate_tty,
    runner,
)


def test_import_summary_names_the_objects_by_type_in_registry_order() -> None:
    line = cli_main._format_import_summary(
        "helios.md", {"Organization": 1, "Concept": 5, "Person": 2}
    )
    assert line == (
        "openkos ingest: imported 'helios.md' -- 8 objects "
        "(5 Concept, 2 Person, 1 Organization)."
    )


def test_import_summary_for_a_source_only_import() -> None:
    assert (
        cli_main._format_import_summary("n.txt", {})
        == "openkos ingest: imported 'n.txt' -- Source only."
    )


def test_piped_post_confirm_line_is_a_summary_without_the_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, _concept_reply())
    (tmp_path / "notes.txt").write_text("Some notes.\nMore notes.\n", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    lines = result.stdout.splitlines()
    assert "openkos ingest: imported 'notes.txt' -- 1 object (1 Concept)." in lines
    assert not any(line.startswith("extracted ") for line in lines)
    assert not any("imported" in line and "->" in line for line in lines)
    # The proposal the user reviewed still lists every path.
    assert "  + bundle/sources/notes.md" in lines


def test_tty_run_separates_sections_and_prefixes_notices(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch)  # declines extraction -> a notice, Source only
    (tmp_path / "notes.txt").write_text("Some notes.\nMore notes.\n", encoding="utf-8")
    _simulate_tty(monkeypatch)
    monkeypatch.setenv("NO_COLOR", "1")

    result = runner.invoke(app, ["ingest", "notes.txt"], input="y\n")

    assert result.exit_code == 0
    assert (
        "note: no concept extracted from this source; keeping the Source only."
        in result.stderr
    )
    assert "openkos ingest: no concept" not in result.stderr
    out = result.stdout
    assert out.startswith("\nopenkos ingest: proposed changes:")
    assert "\n\nopenkos ingest: imported 'notes.txt' -- Source only." in out


def test_piped_run_has_no_blank_separator_lines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch)
    (tmp_path / "notes.txt").write_text("Some notes.\nMore notes.\n", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    assert "\n\n" not in result.stdout
    assert "openkos ingest: no concept extracted from this source" in result.stderr


def test_tty_marks_a_lost_extraction_as_a_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openkos.llm.ollama import OllamaUnavailable

    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, raises=OllamaUnavailable("boom"))
    (tmp_path / "notes.txt").write_text("Some notes.\n", encoding="utf-8")
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["ingest", "notes.txt"], input="y\n")

    assert result.exit_code == 0
    assert "warning: concept extraction skipped" in result.stderr
