"""The lifecycle verbs' human output follows the ADR-0042 convention.

Piped, every verb prints the text it always did, except that a post-confirm
result line is now a summary that no longer repeats the bundle paths the
user approved in the preview. On a terminal the preview and the result are
separate sections and advisory notices carry a text prefix.
"""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from openkos.cli.main import app
from tests.unit.cli import test_forget as forget_tests
from tests.unit.cli import test_merge as merge_tests
from tests.unit.cli import test_reconcile as reconcile_tests
from tests.unit.cli import test_relate as relate_tests
from tests.unit.cli import test_set_sensitivity as sens_tests
from tests.unit.cli import test_set_volatility as vol_tests
from tests.unit.cli import test_unmerge as unmerge_tests
from tests.unit.cli.conftest import commit_pending_fixture_docs
from tests.unit.vcs.conftest import TmpGitRepo, tmp_git_repo

__all__ = ["tmp_git_repo"]

runner = CliRunner()


def _tty(monkeypatch: pytest.MonkeyPatch) -> None:
    relate_tests._simulate_tty(monkeypatch)
    monkeypatch.setenv("NO_COLOR", "1")


def _assert_sections(out: str, preview_lead: str, result_lead: str) -> None:
    """A terminal run opens with a blank line before the preview and puts one
    blank line between the preview and the result."""
    assert out.startswith(f"\n{preview_lead}")
    assert f"\n\n{result_lead}" in out


def _assert_piped(out: str, result_lead: str) -> None:
    assert not out.startswith("\n")
    assert "\n\n" not in out
    assert result_lead in out


# -- relate / unrelate --------------------------------------------------------


def _two_sources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[str, str]:
    relate_tests._init_workspace(tmp_path, monkeypatch)
    return (
        relate_tests._ingest_source(tmp_path, "a.txt"),
        relate_tests._ingest_source(tmp_path, "b.txt"),
    )


def test_relate_tty_separates_preview_and_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    a, b = _two_sources(tmp_path, monkeypatch)
    _tty(monkeypatch)
    result = runner.invoke(app, ["relate", a, "references", b], input="y\n")
    assert result.exit_code == 0
    _assert_sections(
        result.stdout,
        "openkos relate: proposed changes:",
        "openkos relate: added a 'references' relation",
    )


def test_relate_piped_result_is_a_summary_without_bundle_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    a, b = _two_sources(tmp_path, monkeypatch)
    result = runner.invoke(app, ["relate", a, "references", b, "--auto"])
    assert result.exit_code == 0
    _assert_piped(
        result.stdout,
        f"openkos relate: added a 'references' relation from '{a}' to '{b}' "
        "(log.md updated).",
    )


def test_unrelate_tty_separates_and_piped_is_a_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    a, b = _two_sources(tmp_path, monkeypatch)
    assert runner.invoke(app, ["relate", a, "references", b, "--auto"]).exit_code == 0
    piped = runner.invoke(app, ["unrelate", a, "references", b, "--auto"])
    assert piped.exit_code == 0
    _assert_piped(
        piped.stdout,
        f"openkos unrelate: removed the 'references' relation from '{a}' to "
        f"'{b}' (log.md updated).",
    )

    assert runner.invoke(app, ["relate", a, "references", b, "--auto"]).exit_code == 0
    _tty(monkeypatch)
    result = runner.invoke(app, ["unrelate", a, "references", b], input="y\n")
    assert result.exit_code == 0
    _assert_sections(
        result.stdout,
        "openkos unrelate: proposed changes:",
        "openkos unrelate: removed the 'references' relation",
    )


# -- set-sensitivity / set-volatility -----------------------------------------


def test_set_sensitivity_tty_separates_and_prefixes_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sens_tests._init_workspace(tmp_path, monkeypatch)
    source_id = sens_tests._ingest_source(tmp_path, "s.txt")
    sens_tests._write_derived_concept(
        tmp_path, slug="d", provenance=[source_id, "sources/missing"]
    )
    _tty(monkeypatch)
    result = runner.invoke(
        app, ["set-sensitivity", source_id, "confidential"], input="y\n"
    )
    assert result.exit_code == 0
    _assert_sections(
        result.stdout,
        "openkos set-sensitivity: proposed changes:",
        "openkos set-sensitivity: set ",
    )
    assert "warning: 'concepts/d' cites unresolvable provenance" in result.stderr
    assert "WARNING -- 'concepts/d'" not in result.stderr


def test_set_sensitivity_piped_warning_keeps_its_legacy_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sens_tests._init_workspace(tmp_path, monkeypatch)
    source_id = sens_tests._ingest_source(tmp_path, "s.txt")
    sens_tests._write_derived_concept(
        tmp_path, slug="d", provenance=[source_id, "sources/missing"]
    )
    result = runner.invoke(
        app, ["set-sensitivity", source_id, "confidential", "--auto"]
    )
    assert result.exit_code == 0
    assert "openkos set-sensitivity: WARNING -- 'concepts/d' cites" in result.stderr
    _assert_piped(result.stdout, "openkos set-sensitivity: set ")


def test_set_volatility_tty_separates_preview_and_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vol_tests._init_workspace(tmp_path, monkeypatch)
    _tty(monkeypatch)
    result = runner.invoke(app, ["set-volatility", "Person", "volatile"], input="y\n")
    assert result.exit_code == 0
    _assert_sections(
        result.stdout,
        "openkos set-volatility: proposed changes:",
        "openkos set-volatility: set Person -> volatile",
    )


def test_set_volatility_piped_is_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vol_tests._init_workspace(tmp_path, monkeypatch)
    result = runner.invoke(app, ["set-volatility", "Person", "volatile", "--auto"])
    assert result.exit_code == 0
    _assert_piped(
        result.stdout,
        "openkos set-volatility: set Person -> volatile in openkos.yaml.",
    )


# -- merge / unmerge -----------------------------------------------------------


def _merge_pair(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    merge_tests._init_workspace(tmp_path, monkeypatch)
    merge_tests._write_concept(tmp_path, "concepts/survivor", title="Survivor")
    merge_tests._write_concept(tmp_path, "concepts/absorbed", title="Absorbed")


def test_merge_piped_result_is_a_summary_and_tty_separates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _merge_pair(tmp_path, monkeypatch)
    piped = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )
    assert piped.exit_code == 0
    assert (
        "openkos merge: merged 'concepts/absorbed' into 'concepts/survivor' "
        "(index.md, log.md updated)." in piped.stdout
    )
    assert "merged 'bundle/" not in piped.stdout

    unmerged = runner.invoke(
        app, ["unmerge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )
    assert unmerged.exit_code == 0
    assert (
        "openkos unmerge: restored 'concepts/absorbed' from 'concepts/survivor' "
        "(index.md, log.md updated)." in unmerged.stdout
    )

    _tty(monkeypatch)
    result = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed"], input="y\n"
    )
    assert result.exit_code == 0
    _assert_sections(
        result.stdout, "openkos merge: proposed changes:", "openkos merge: merged "
    )
    undo = runner.invoke(
        app, ["unmerge", "concepts/survivor", "concepts/absorbed"], input="y\n"
    )
    assert undo.exit_code == 0
    _assert_sections(
        undo.stdout, "openkos unmerge: proposed changes:", "openkos unmerge: restored "
    )


def test_unmerge_tty_wraps_its_warnings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    unmerge_tests._init_workspace(tmp_path, monkeypatch)
    unmerge_tests._write_concept(tmp_path, "concepts/survivor", title="Survivor")
    unmerge_tests._write_concept(tmp_path, "concepts/absorbed", title="Absorbed")
    merged = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )
    assert merged.exit_code == 0
    survivor = tmp_path / "bundle" / "concepts" / "survivor.md"
    survivor.write_text(
        survivor.read_text(encoding="utf-8") + "\nA later edit.\n", encoding="utf-8"
    )
    commit_pending_fixture_docs()
    _tty(monkeypatch)
    result = runner.invoke(
        app,
        [
            "unmerge",
            "concepts/survivor",
            "concepts/absorbed",
            "--discard-survivor-edits",
        ],
        input="y\n",
    )
    assert result.exit_code == 0
    assert "Warning: 'concepts/survivor''s post-merge edits" in result.stdout
    # Wrapped to the pinned 80 columns, with a hanging indent.
    block = ("Warning:" + result.stdout.split("Warning:", 1)[1]).split("\n\n")[0]
    assert all(len(line) <= 80 for line in block.splitlines())
    assert "\n  " in block


# -- forget --------------------------------------------------------------------


def test_forget_self_summary_and_tty_sections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    forget_tests._init_workspace(tmp_path, monkeypatch)
    cid = forget_tests._ingest_source(tmp_path, "n.txt")
    _tty(monkeypatch)
    result = runner.invoke(app, ["forget", cid], input="y\n")
    assert result.exit_code == 0
    _assert_sections(
        result.stdout, "openkos forget: proposed changes:", "openkos forget: removed "
    )


def test_forget_source_scope_summary_does_not_repeat_the_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    forget_tests._init_workspace(tmp_path, monkeypatch)
    cid = forget_tests._ingest_source(tmp_path, "n.txt")
    sens_tests._write_derived_concept(tmp_path, slug="kid", provenance=[cid])
    commit_pending_fixture_docs()
    result = runner.invoke(app, ["forget", cid, "--scope", "source", "--auto"])
    assert result.exit_code == 0
    assert (
        "openkos forget: removed 2 concept(s) (index.md, log.md updated)."
        in result.stdout
    )
    assert "removed 2 concept(s) (bundle/" not in result.stdout


# -- reconcile -----------------------------------------------------------------


def test_reconcile_tty_separates_preview_and_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reconcile_tests._init_workspace(tmp_path, monkeypatch)
    a = reconcile_tests._ingest_source(tmp_path, "a.txt")
    b = reconcile_tests._ingest_source(tmp_path, "b.txt")
    _tty(monkeypatch)
    result = runner.invoke(app, ["reconcile", a, b], input="y\n")
    assert result.exit_code == 0
    _assert_sections(
        result.stdout,
        "openkos reconcile: proposed changes:",
        "openkos reconcile: recorded a symmetric reconciliation",
    )


# -- purge ---------------------------------------------------------------------


def test_purge_tty_separates_the_result_and_prefixes_the_point_of_no_return(
    tmp_git_repo: TmpGitRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    _tty(monkeypatch)
    phrase = f"purge {tmp_git_repo.source_id}"
    result = runner.invoke(
        app, ["purge", tmp_git_repo.source_id, "--confirm-phrase", phrase]
    )
    assert result.exit_code == 0, result.output
    assert result.stdout.startswith(
        "\nopenkos purge: proposed IRREVERSIBLE history rewrite:"
    )
    assert "\n\nopenkos purge: permanently expunged" in result.stdout
    assert "\n\n\n" not in result.stdout
    assert result.stderr.startswith("warning: beginning the irreversible history")


def test_purge_piped_keeps_its_legacy_notice_and_has_no_extra_blank_lines(
    tmp_git_repo: TmpGitRepo,
) -> None:
    phrase = f"purge {tmp_git_repo.source_id}"
    result = runner.invoke(
        app, ["purge", tmp_git_repo.source_id, "--confirm-phrase", phrase]
    )
    assert result.exit_code == 0, result.output
    assert not result.stdout.startswith("\n")
    assert result.stderr.startswith(
        "openkos purge: beginning the irreversible history rewrite now"
    )
    assert result.stdout.count("\n\n") == 1
