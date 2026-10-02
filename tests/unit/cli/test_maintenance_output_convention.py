"""The maintenance verbs' human output follows the ADR-0042 convention.

Piped, every verb keeps the text it always wrote, except the post-confirm
lines that repeated the paths the user had just approved (rule 1), which are
now summaries. On a terminal the sections are separated and the advisory
notices carry a `note:` / `warning:` prefix.
"""

import unicodedata
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from openkos.application import watch_notify
from openkos.cli import daemon as daemon_module
from openkos.cli.main import app
from openkos.state.reindex import ReindexReport
from tests.unit.cli.test_backfill_sensitivity import (
    _ingest_source,
    _write_derived_concept,
    _write_raw_sensitivity,
)
from tests.unit.cli.test_backfill_source_titles import _staged
from tests.unit.cli.test_doctor import _fake_ollama_client as _doctor_ollama
from tests.unit.cli.test_init import _fake_ollama_client, _simulate_tty
from tests.unit.cli.test_normalize_names import NFD_CAFE, _write_nfd_file
from tests.unit.cli.test_repair import _make_entry, _write_legacy_survivor
from tests.unit.cli.test_sync_tags import _write_concept, _write_source

runner = CliRunner()


def _init(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    with monkeypatch.context() as scoped:
        scoped.setattr(
            "openkos.cli.main.OllamaClient",
            _fake_ollama_client(installed=["qwen3:8b"]),
        )
        assert runner.invoke(app, ["init"]).exit_code == 0


# -- normalize-names ---------------------------------------------------------


def test_normalize_names_post_confirm_line_is_a_count_not_the_pairs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init(tmp_path, monkeypatch)
    _write_nfd_file(tmp_path, "", "café")

    result = runner.invoke(app, ["normalize-names", "--auto"])

    assert result.exit_code == 0
    lines = result.stdout.splitlines()
    assert (
        "openkos normalize-names: renamed 1 on-disk name(s) (log.md updated)." in lines
    )
    assert "\n\n" not in result.stdout


def test_normalize_names_tty_separates_proposal_and_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init(tmp_path, monkeypatch)
    _write_nfd_file(tmp_path, "", "café")
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["normalize-names"], input="y\n")

    assert result.exit_code == 0
    out = result.stdout
    assert out.startswith("\nopenkos normalize-names: proposed renames")
    assert "\n\nopenkos normalize-names: renamed 1 on-disk name(s)" in out
    assert unicodedata.normalize("NFC", "café") not in out.split("renamed 1")[1]
    assert NFD_CAFE not in out.split("renamed 1")[1]


# -- backfill-sensitivity ----------------------------------------------------


def _sensitivity_fixture(tmp_path: Path) -> None:
    source = _ingest_source(tmp_path, "a.txt")
    _write_raw_sensitivity(tmp_path, source, "confidential")
    _write_derived_concept(
        tmp_path, slug="derived-a", provenance=[source], sensitivity="public"
    )


def test_backfill_sensitivity_post_confirm_line_is_a_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init(tmp_path, monkeypatch)
    _sensitivity_fixture(tmp_path)

    result = runner.invoke(app, ["backfill-sensitivity", "--auto"])

    assert result.exit_code == 0
    lines = result.stdout.splitlines()
    assert (
        "openkos backfill-sensitivity: raised 1 document(s) (log.md updated)." in lines
    )
    assert "\n\n" not in result.stdout


def test_backfill_sensitivity_tty_separates_sections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init(tmp_path, monkeypatch)
    _sensitivity_fixture(tmp_path)
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["backfill-sensitivity"], input="y\n")

    assert result.exit_code == 0
    assert result.stdout.startswith("\nopenkos backfill-sensitivity: proposed changes:")
    assert "\n\nopenkos backfill-sensitivity: raised 1 document(s)" in result.stdout


# -- backfill-source-titles --------------------------------------------------


def test_backfill_source_titles_post_confirm_line_is_a_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init(tmp_path, monkeypatch)
    _staged(tmp_path, "mechanical")

    result = runner.invoke(app, ["backfill-source-titles", "--auto"])

    assert result.exit_code == 0
    last = result.stdout.splitlines()[-1]
    assert last.startswith("openkos backfill-source-titles: retitled 1 Source(s) (")
    assert last.endswith("log.md updated).")
    assert "sources/mechanical" not in last
    assert "\n\n" not in result.stdout


def test_backfill_source_titles_tty_separates_sections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init(tmp_path, monkeypatch)
    _staged(tmp_path, "mechanical")
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["backfill-source-titles"], input="y\n")

    assert result.exit_code == 0
    assert result.stdout.startswith(
        "\nopenkos backfill-source-titles: proposed changes:"
    )
    assert "\n\nopenkos backfill-source-titles: retitled 1 Source(s)" in result.stdout


# -- sync-tags ---------------------------------------------------------------


def _sync_tags_fixture(tmp_path: Path) -> None:
    _write_source(
        tmp_path,
        "sources/notes",
        title="Notes",
        tags=["alpha"],
        sensitivity="confidential",
    )
    _write_concept(
        tmp_path,
        "concepts/malformed",
        title="Malformed",
        provenance=["sources/notes"],
        tags={"a": 1},
    )
    _write_concept(
        tmp_path,
        "concepts/low",
        title="Low",
        provenance=["sources/notes"],
        sensitivity="private",
    )
    _write_concept(
        tmp_path,
        "concepts/ok",
        title="Ok",
        provenance=["sources/notes"],
        sensitivity="confidential",
    )


def test_sync_tags_piped_notices_keep_their_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init(tmp_path, monkeypatch)
    _sync_tags_fixture(tmp_path)

    result = runner.invoke(app, ["sync-tags", "sources/notes", "--auto"])

    assert (
        "openkos sync-tags: WARNING -- 'bundle/concepts/malformed.md'" in result.stderr
    )
    assert "openkos sync-tags: note -- 'bundle/concepts/low.md'" in result.stderr
    assert "\n\n" not in result.stdout


def test_sync_tags_tty_prefixes_notices_and_separates_sections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init(tmp_path, monkeypatch)
    _sync_tags_fixture(tmp_path)
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["sync-tags", "sources/notes"], input="y\n")

    assert result.exit_code == 0
    assert "warning: 'bundle/concepts/malformed.md' has a malformed" in result.stderr
    assert "note: 'bundle/concepts/low.md' is below" in result.stderr
    assert "openkos sync-tags: WARNING" not in result.stderr
    assert "openkos sync-tags: note" not in result.stderr
    assert result.stdout.startswith("\nopenkos sync-tags: proposed changes:")
    assert "\n\nopenkos sync-tags: added tags to 1 concept(s)" in result.stdout


# -- repair ------------------------------------------------------------------


def test_repair_tty_prefixes_the_reset_point_warning_and_separates_the_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init(tmp_path, monkeypatch)
    _write_legacy_survivor(
        tmp_path / "bundle", "concepts/survivor", entries=[_make_entry()]
    )
    monkeypatch.setattr("openkos.cli.main.vcs_git.repo_root", lambda root: None)
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["repair"])

    assert result.exit_code == 0
    assert "warning: no git reset point is available" in result.stderr
    assert "openkos repair: WARNING" not in result.stderr
    assert result.stdout.startswith("\nopenkos repair: migrated 1 ledger")


def test_repair_piped_warning_and_result_are_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init(tmp_path, monkeypatch)
    _write_legacy_survivor(
        tmp_path / "bundle", "concepts/survivor", entries=[_make_entry()]
    )
    monkeypatch.setattr("openkos.cli.main.vcs_git.repo_root", lambda root: None)

    result = runner.invoke(app, ["repair"])

    assert "openkos repair: WARNING -- no git reset point" in result.stderr
    assert "\n\n" not in result.stdout


# -- reindex -----------------------------------------------------------------


def _fake_incomplete_reindex(monkeypatch: pytest.MonkeyPatch) -> None:
    report = ReindexReport(
        embedded=9, cache_hits=0, pruned=0, skipped=0, embed_failed=1
    )
    monkeypatch.setattr(
        "openkos.cli.main.reindex_module.reindex", lambda *a, **k: report
    )


def test_reindex_piped_incomplete_notice_is_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init(tmp_path, monkeypatch)
    _fake_incomplete_reindex(monkeypatch)

    result = runner.invoke(app, ["reindex"])

    assert result.stderr.startswith("openkos reindex: INCOMPLETE -- 1 doc could not")


def test_reindex_tty_prefixes_the_incomplete_notice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init(tmp_path, monkeypatch)
    _fake_incomplete_reindex(monkeypatch)
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["reindex"])

    assert result.stderr.startswith("warning: INCOMPLETE -- 1 doc could not")
    assert result.stdout.startswith("openkos reindex: 9 embedded")


# -- doctor ------------------------------------------------------------------


def test_doctor_tty_separates_the_summary_and_keeps_one_line_per_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init(tmp_path, monkeypatch)
    monkeypatch.setattr("openkos.cli.main.OllamaClient", _doctor_ollama(installed=[]))
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["doctor"])

    lines = result.stdout.splitlines()
    summary = next(i for i, ln in enumerate(lines) if "check(s) completed" in ln)
    assert lines[summary - 1] == ""
    assert all(
        ln.startswith(("[PASS]", "[FAIL]", "[SKIP]", "[NOT RUN]", "  ->"))
        for ln in lines[3 : summary - 1]
    )


def test_doctor_piped_summary_directly_follows_the_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init(tmp_path, monkeypatch)
    monkeypatch.setattr("openkos.cli.main.OllamaClient", _doctor_ollama(installed=[]))

    result = runner.invoke(app, ["doctor"])

    lines = result.stdout.splitlines()
    summary = next(i for i, ln in enumerate(lines) if "check(s) completed" in ln)
    assert lines[summary - 1] != ""


# -- init --------------------------------------------------------------------

_INIT_ARGS = ["init", "--model", "qwen3:8b", "--embedding-model", "bge-m3"]


def test_init_piped_notices_and_stdout_are_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("openkos.cli.main.OllamaClient", _fake_ollama_client())

    result = runner.invoke(app, _INIT_ARGS)

    assert result.exit_code == 0
    assert (
        "openkos init: note -- the embedding model ('bge-m3') is sticky"
        in result.stderr
    )
    assert "openkos init: note -- Ollama isn't ready" in result.stderr
    assert "\n\n" not in result.stdout


def test_init_tty_prefixes_notices_and_separates_the_next_steps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("openkos.cli.main.OllamaClient", _fake_ollama_client())
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, _INIT_ARGS)

    assert result.exit_code == 0
    assert "note: the embedding model ('bge-m3') is sticky" in result.stderr
    assert "note: Ollama isn't ready" in result.stderr
    assert "openkos init:" not in result.stderr
    assert "\n\nNext: run `openkos ingest <path>`" in result.stdout


def test_init_tty_prefixes_the_allowlist_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("openkos.cli.main.OllamaClient", _fake_ollama_client())
    _simulate_tty(monkeypatch)

    result = runner.invoke(
        app, ["init", "--model", "qwen3:8b", "--embedding-model", "weird-model"]
    )

    assert result.exit_code == 0
    assert "warning: 'weird-model' is not on the vetted" in result.stderr


# -- daemon ------------------------------------------------------------------


class _TtyStderr:
    def __init__(self, tty: bool) -> None:
        self.tty = tty
        self.text = ""

    def isatty(self) -> bool:
        return self.tty

    def write(self, data: str) -> int:
        self.text += data
        return len(data)

    def flush(self) -> None:
        pass


@pytest.mark.parametrize(
    ("tty", "expected"),
    [
        (False, "openkos daemon: native watch unavailable; polling.\n"),
        (True, "warning: native watch unavailable; polling.\n"),
    ],
)
def test_daemon_watch_backend_warning(
    monkeypatch: pytest.MonkeyPatch, tty: bool, expected: str
) -> None:
    stream = _TtyStderr(tty)
    monkeypatch.setattr("sys.stderr", stream)
    monkeypatch.setattr(
        watch_notify,
        "open_notifier",
        lambda *a, **k: watch_notify.OpenedNotifier(
            warning="native watch unavailable; polling."
        ),
    )
    unattended = SimpleNamespace(watch_backend="native", inbox=Path("inbox"))

    daemon_module._open_notifier(unattended, once=False)  # type: ignore[arg-type]

    assert stream.text.endswith(expected)
