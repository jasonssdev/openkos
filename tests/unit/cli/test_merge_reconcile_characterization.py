"""Byte-identity characterization tests for `openkos merge`, `unmerge` and
`reconcile` (issue #1168, last slice), the `test_ingest_characterization.py`
pattern.

The three write cores moved into `application/merge_service.py`,
`application/unmerge_service.py` and `application/reconcile_service.py`; the
CLI kept prompting, rendering and the exit-code mapping. The existing
`test_merge.py` / `test_unmerge.py` / `test_reconcile.py` assertions pin
individual substrings; this file additionally pins, per scenario, the
COMPLETE `stdout` + `stderr` + exit code, every file under `bundle/` (the
ledger sidecars included) and the commit subjects, against goldens recorded
on the tree BEFORE the move. A stray byte, a reworded refusal, a write that
moved across a refusal, or a commit that changed its paths all go red here.

Volatile values (the workspace path, timestamps, today's date, commit shas
and sha256 digests of timestamped text) are replaced by placeholders before
comparison; everything else is compared verbatim. The git identity is pinned
(`pinned_git_identity`) because the complete stderr is under test and a CI
runner configures none.
"""

import json
import re
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner, _NamedTextIOWrapper

from openkos import fsio
from openkos.bundle import ledger as bundle_ledger
from openkos.cli import main
from openkos.cli.main import app
from openkos.model import okf
from tests.unit.cli.conftest import confirm_after, echo_after
from tests.unit.cli.test_merge import (
    _LONG_BODY,
    _PRIVATE_SENTENCE,
    _RECONCILED_BODY,
    _RecordingChat,
    _write_concept,
    _write_flagged_ledger,
)

runner = CliRunner()

_GOLDENS_PATH = (
    Path(__file__).parent / "fixtures" / "merge_reconcile_characterization_goldens.json"
)
_GOLDENS: dict[str, dict[str, Any]] = json.loads(
    _GOLDENS_PATH.read_text(encoding="utf-8")
)

pytestmark = pytest.mark.usefixtures("pinned_git_identity")

_SURVIVOR = "concepts/survivor"
_ABSORBED = "concepts/absorbed"
_THIRD = "concepts/third"
_LINKER = "concepts/linker"


# -- harness ----------------------------------------------------------------


def _simulate_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_NamedTextIOWrapper, "isatty", lambda self: True)


def _normalize(text: str, root: Path) -> str:
    for spelling in {str(root), str(root.resolve())}:
        text = text.replace(spelling, "<ROOT>")
    text = re.sub(r"\d{4}-\d{2}-\d{2}T[0-9:.]+(?:Z|[+-]\d{2}:\d{2})?", "<TS>", text)
    text = re.sub(r"\b[0-9a-f]{64}\b", "<SHA256>", text)
    text = re.sub(r"(embedding doc \d+/\d+ - )\d+s", r"\1<N>s", text)
    text = re.sub(r"committed as [0-9a-f]{7,40}", "committed as <SHA>", text)
    text = re.sub(r"git revert [0-9a-f]{7,40}", "git revert <SHA>", text)
    today = datetime.now(UTC).astimezone().date().isoformat()
    utc_today = datetime.now(UTC).date().isoformat()
    for date_text in {today, utc_today}:
        text = text.replace(date_text, "<DATE>")
    return text


def _files(root: Path) -> dict[str, str]:
    bundle = root / "bundle"
    return {
        path.relative_to(root).as_posix(): _normalize(
            path.read_text(encoding="utf-8"), root
        )
        for path in sorted(bundle.rglob("*"))
        if path.is_file()
    }


def _commits(root: Path) -> list[str]:
    done = subprocess.run(
        ["git", "log", "--format=%s"],  # noqa: S607
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.splitlines()


def _run(
    root: Path,
    args: list[str],
    *,
    stdin: str | None = None,
    workspace: bool = True,
) -> dict[str, Any]:
    result = runner.invoke(app, args, input=stdin)
    actual: dict[str, Any] = {
        "exit_code": result.exit_code,
        "stdout": _normalize(result.stdout, root),
        "stderr": _normalize(result.stderr, root),
    }
    if workspace:
        actual["files"] = _files(root)
        actual["commits"] = _commits(root)
    return actual


def _assert_matches_golden(scenario: str, actual: dict[str, Any]) -> None:
    expected = _GOLDENS[scenario]
    assert actual.keys() == expected.keys(), scenario
    for key in actual:
        assert actual[key] == expected[key], f"{scenario}: {key}"


def _init(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(app, ["init"]).exit_code == 0


def _pair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    survivor_body: str = "Survivor body.",
    absorbed_body: str = "Absorbed body.",
    with_linker: bool = True,
) -> None:
    """A survivor, an absorbed concept and (by default) a third concept that
    links to the absorbed one, so a merge exercises the link rewrite."""
    _init(tmp_path, monkeypatch)
    _write_concept(tmp_path, _SURVIVOR, title="Survivor", body=survivor_body)
    _write_concept(tmp_path, _ABSORBED, title="Absorbed", body=absorbed_body)
    if with_linker:
        _write_concept(
            tmp_path,
            _LINKER,
            title="Linker",
            body="See [Absorbed](/concepts/absorbed.md) for detail.",
        )


def _merged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _pair(tmp_path, monkeypatch)
    assert runner.invoke(app, ["merge", _SURVIVOR, _ABSORBED, "--auto"]).exit_code == 0


def _chain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """survivor absorbed `absorbed` then `third` (two ledger entries)."""
    _pair(tmp_path, monkeypatch)
    _write_concept(tmp_path, _THIRD, title="Third", body="Third body.")
    assert runner.invoke(app, ["merge", _SURVIVOR, _ABSORBED, "--auto"]).exit_code == 0
    assert runner.invoke(app, ["merge", _SURVIVOR, _THIRD, "--auto"]).exit_code == 0


def _edit(path: Path, suffix: str) -> Callable[[], object]:
    return lambda: path.write_text(
        path.read_text(encoding="utf-8") + suffix, encoding="utf-8"
    )


def _fail_write_of(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    real = fsio.write_atomic

    def _write(path: Path, text: str) -> None:
        if Path(path).name == name:
            raise OSError(f"simulated failure writing {name}")
        real(path, text)

    monkeypatch.setattr(fsio, "write_atomic", _write)


# -- merge ------------------------------------------------------------------


def test_merge_auto_writes_and_commits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pair(tmp_path, monkeypatch)
    _assert_matches_golden(
        "merge_auto", _run(tmp_path, ["merge", _SURVIVOR, _ABSORBED, "--auto"])
    )


def test_merge_conflicting_reconcile_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pair(tmp_path, monkeypatch)
    _assert_matches_golden(
        "merge_conflicting_reconcile_flags",
        _run(
            tmp_path,
            ["merge", _SURVIVOR, _ABSORBED, "--reconcile", "--no-reconcile"],
        ),
    )


def test_merge_outside_a_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _assert_matches_golden(
        "merge_not_a_workspace",
        _run(tmp_path, ["merge", _SURVIVOR, _ABSORBED, "--auto"], workspace=False),
    )


def test_merge_same_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _pair(tmp_path, monkeypatch)
    _assert_matches_golden(
        "merge_same_id", _run(tmp_path, ["merge", _SURVIVOR, _SURVIVOR, "--auto"])
    )


def test_merge_unknown_absorbed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pair(tmp_path, monkeypatch)
    _assert_matches_golden(
        "merge_unknown_absorbed",
        _run(tmp_path, ["merge", _SURVIVOR, "concepts/ghost", "--auto"]),
    )


def test_merge_non_tty_refuses_without_auto(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pair(tmp_path, monkeypatch)
    _assert_matches_golden(
        "merge_non_tty_refusal", _run(tmp_path, ["merge", _SURVIVOR, _ABSORBED])
    )


def test_merge_tty_accepted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _pair(tmp_path, monkeypatch)
    _simulate_tty(monkeypatch)
    _assert_matches_golden(
        "merge_tty_accepted",
        _run(tmp_path, ["merge", _SURVIVOR, _ABSORBED], stdin="y\n"),
    )


def test_merge_tty_declined(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _pair(tmp_path, monkeypatch)
    _simulate_tty(monkeypatch)
    _assert_matches_golden(
        "merge_tty_declined",
        _run(tmp_path, ["merge", _SURVIVOR, _ABSORBED], stdin="n\n"),
    )


def test_merge_torn_ledger_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pair(tmp_path, monkeypatch)
    pending = bundle_ledger.pending_path_for(_SURVIVOR, tmp_path / "bundle")
    pending.parent.mkdir(parents=True, exist_ok=True)
    pending.write_text("stale pending marker", encoding="utf-8")
    _assert_matches_golden(
        "merge_torn_ledger",
        _run(tmp_path, ["merge", _SURVIVOR, _ABSORBED, "--auto"]),
    )


def test_merge_flagged_ledger_refusal_with_a_reset_point(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pair(tmp_path, monkeypatch, with_linker=False)
    _write_flagged_ledger(tmp_path, _SURVIVOR)
    monkeypatch.setattr("openkos.cli.main.vcs_git.has_reset_point", lambda root: True)
    _assert_matches_golden(
        "merge_flagged_ledger_reset_point",
        _run(tmp_path, ["merge", _SURVIVOR, _ABSORBED, "--auto"]),
    )


def test_merge_flagged_ledger_refusal_without_a_reset_point(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pair(tmp_path, monkeypatch, with_linker=False)
    _write_flagged_ledger(tmp_path, _SURVIVOR)
    monkeypatch.setattr("openkos.cli.main.vcs_git.has_reset_point", lambda root: False)
    _assert_matches_golden(
        "merge_flagged_ledger_no_reset_point",
        _run(tmp_path, ["merge", _SURVIVOR, _ABSORBED, "--auto"]),
    )


def test_merge_drift_after_the_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pair(tmp_path, monkeypatch)
    _simulate_tty(monkeypatch)
    confirm_after(
        monkeypatch, _edit(tmp_path / "bundle" / f"{_ABSORBED}.md", "\nedit\n")
    )
    _assert_matches_golden(
        "merge_drift_after_prompt",
        _run(tmp_path, ["merge", _SURVIVOR, _ABSORBED]),
    )


def test_merge_drift_on_the_unprompted_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pair(tmp_path, monkeypatch)
    hook = echo_after(
        monkeypatch,
        _edit(tmp_path / "bundle" / f"{_LINKER}.md", "\nedit\n"),
        trigger=f"bundle/{_ABSORBED}.md",
    )
    actual = _run(tmp_path, ["merge", _SURVIVOR, _ABSORBED, "--auto"])
    assert hook.fired
    _assert_matches_golden("merge_drift_unprompted", actual)


def test_merge_write_failure_partway(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pair(tmp_path, monkeypatch)
    _fail_write_of(monkeypatch, "log.md")
    _assert_matches_golden(
        "merge_write_failure",
        _run(tmp_path, ["merge", _SURVIVOR, _ABSORBED, "--auto"]),
    )


def test_merge_preparation_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pair(tmp_path, monkeypatch)
    (tmp_path / "bundle" / f"{_ABSORBED}.md").write_text(
        "---\ntype: [unclosed\n---\n", encoding="utf-8"
    )
    _assert_matches_golden(
        "merge_preparation_failure",
        _run(tmp_path, ["merge", _SURVIVOR, _ABSORBED, "--auto"]),
    )


def test_merge_reconciles_a_long_stacked_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pair(
        tmp_path,
        monkeypatch,
        survivor_body=_LONG_BODY,
        absorbed_body=_LONG_BODY,
        with_linker=False,
    )
    monkeypatch.setattr(
        main, "reconcile_merged_body", lambda **kwargs: _RECONCILED_BODY
    )
    _assert_matches_golden(
        "merge_reconciled",
        _run(tmp_path, ["merge", _SURVIVOR, _ABSORBED, "--auto"]),
    )


def test_merge_reconciliation_failure_keeps_the_stacked_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pair(
        tmp_path,
        monkeypatch,
        survivor_body=_LONG_BODY,
        absorbed_body=_LONG_BODY,
        with_linker=False,
    )
    monkeypatch.setattr(main, "reconcile_merged_body", lambda **kwargs: None)
    _assert_matches_golden(
        "merge_reconciliation_failed",
        _run(tmp_path, ["merge", _SURVIVOR, _ABSORBED, "--auto"]),
    )


def test_merge_reconciliation_skipped_for_confidential_on_a_remote_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init(tmp_path, monkeypatch)
    _write_concept(
        tmp_path,
        _SURVIVOR,
        title="Survivor",
        body=_LONG_BODY,
        sensitivity="confidential",
    )
    _write_concept(
        tmp_path,
        _ABSORBED,
        title="Absorbed",
        body=f"{_PRIVATE_SENTENCE} {_LONG_BODY}",
        sensitivity="private",
    )
    backend = _RecordingChat(is_local=False)
    monkeypatch.setattr(main, "_chat_client", lambda cfg, *, task=None: backend)
    actual = _run(tmp_path, ["merge", _SURVIVOR, _ABSORBED, "--auto"])
    assert backend.sent == []
    _assert_matches_golden("merge_reconciliation_confidential_skip", actual)


def test_merge_forced_reconcile_on_a_short_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pair(tmp_path, monkeypatch, with_linker=False)
    monkeypatch.setattr(
        main, "reconcile_merged_body", lambda **kwargs: _RECONCILED_BODY
    )
    _assert_matches_golden(
        "merge_forced_reconcile",
        _run(tmp_path, ["merge", _SURVIVOR, _ABSORBED, "--auto", "--reconcile"]),
    )


def test_merge_confidential_commit_notice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init(tmp_path, monkeypatch)
    _write_concept(tmp_path, _SURVIVOR, title="Survivor", sensitivity="confidential")
    _write_concept(tmp_path, _ABSORBED, title="Absorbed")
    _assert_matches_golden(
        "merge_confidential_commit_notice",
        _run(tmp_path, ["merge", _SURVIVOR, _ABSORBED, "--auto"]),
    )


def test_merge_in_a_workspace_without_git_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pair(tmp_path, monkeypatch)
    monkeypatch.setattr("openkos.cli.main.vcs_git.repo_root", lambda root: None)
    _assert_matches_golden(
        "merge_not_a_repository",
        _run(tmp_path, ["merge", _SURVIVOR, _ABSORBED, "--auto"]),
    )


# -- unmerge ----------------------------------------------------------------


def test_unmerge_auto(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _merged(tmp_path, monkeypatch)
    _assert_matches_golden(
        "unmerge_auto", _run(tmp_path, ["unmerge", _SURVIVOR, _ABSORBED, "--auto"])
    )


def test_unmerge_needs_a_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _merged(tmp_path, monkeypatch)
    _assert_matches_golden("unmerge_no_target", _run(tmp_path, ["unmerge", _SURVIVOR]))


def test_unmerge_refuses_both_forms(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _merged(tmp_path, monkeypatch)
    _assert_matches_golden(
        "unmerge_both_forms",
        _run(tmp_path, ["unmerge", _SURVIVOR, _ABSORBED, "--to", _ABSORBED]),
    )


def test_unmerge_outside_a_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _assert_matches_golden(
        "unmerge_not_a_workspace",
        _run(tmp_path, ["unmerge", _SURVIVOR, _ABSORBED, "--auto"], workspace=False),
    )


def test_unmerge_of_an_absorbed_survivor_names_the_absorber(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _merged(tmp_path, monkeypatch)
    _assert_matches_golden(
        "unmerge_absorbed_survivor",
        _run(tmp_path, ["unmerge", _ABSORBED, _SURVIVOR, "--auto"]),
    )


def test_unmerge_not_the_lifo_tail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _chain(tmp_path, monkeypatch)
    _assert_matches_golden(
        "unmerge_not_the_tail",
        _run(tmp_path, ["unmerge", _SURVIVOR, _ABSORBED, "--auto"]),
    )


def test_unmerge_to_unwinds_the_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _chain(tmp_path, monkeypatch)
    _assert_matches_golden(
        "unmerge_to_chain",
        _run(tmp_path, ["unmerge", _SURVIVOR, "--to", _ABSORBED, "--auto"]),
    )


def test_unmerge_to_keep_distinct_rules_every_restored_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#1334 item 9: with `--to`, each step's restored pair gets its ruling,
    mirroring the per-step commit and restore reporting."""
    _chain(tmp_path, monkeypatch)

    result = runner.invoke(
        app, ["unmerge", _SURVIVOR, "--to", _ABSORBED, "--auto", "--keep-distinct"]
    )

    assert result.exit_code == 0, result.stderr
    listed = runner.invoke(app, ["duplicates", "--kept-distinct"]).stdout
    assert f"{_ABSORBED} + {_SURVIVOR}" in listed
    assert f"{_SURVIVOR} + {_THIRD}" in listed


def test_unmerge_to_unknown_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _chain(tmp_path, monkeypatch)
    _assert_matches_golden(
        "unmerge_to_unknown",
        _run(tmp_path, ["unmerge", _SURVIVOR, "--to", "concepts/ghost", "--auto"]),
    )


def test_unmerge_to_chain_stops_when_a_later_step_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _chain(tmp_path, monkeypatch)
    real = fsio.write_atomic
    calls = {"n": 0}

    def _write(path: Path, text: str) -> None:
        calls["n"] += 1
        if calls["n"] > 6:
            raise OSError("simulated failure mid-chain")
        real(path, text)

    monkeypatch.setattr(fsio, "write_atomic", _write)
    _assert_matches_golden(
        "unmerge_to_chain_step_failure",
        _run(tmp_path, ["unmerge", _SURVIVOR, "--to", _ABSORBED, "--auto"]),
    )


def test_unmerge_to_chain_tty_confirms_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _chain(tmp_path, monkeypatch)
    _simulate_tty(monkeypatch)
    _assert_matches_golden(
        "unmerge_to_chain_tty",
        _run(tmp_path, ["unmerge", _SURVIVOR, "--to", _ABSORBED], stdin="y\n"),
    )


def test_unmerge_to_chain_non_tty_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _chain(tmp_path, monkeypatch)
    _assert_matches_golden(
        "unmerge_to_chain_non_tty",
        _run(tmp_path, ["unmerge", _SURVIVOR, "--to", _ABSORBED]),
    )


def test_unmerge_non_tty_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _merged(tmp_path, monkeypatch)
    _assert_matches_golden(
        "unmerge_non_tty", _run(tmp_path, ["unmerge", _SURVIVOR, _ABSORBED])
    )


def test_unmerge_tty_declined(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _merged(tmp_path, monkeypatch)
    _simulate_tty(monkeypatch)
    _assert_matches_golden(
        "unmerge_tty_declined",
        _run(tmp_path, ["unmerge", _SURVIVOR, _ABSORBED], stdin="n\n"),
    )


def test_unmerge_tty_accepted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _merged(tmp_path, monkeypatch)
    _simulate_tty(monkeypatch)
    _assert_matches_golden(
        "unmerge_tty_accepted",
        _run(tmp_path, ["unmerge", _SURVIVOR, _ABSORBED], stdin="y\n"),
    )


def test_unmerge_survivor_edited_since_the_merge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _merged(tmp_path, monkeypatch)
    _edit(tmp_path / "bundle" / f"{_SURVIVOR}.md", "\nlater edit\n")()
    _assert_matches_golden(
        "unmerge_survivor_edited",
        _run(tmp_path, ["unmerge", _SURVIVOR, _ABSORBED, "--auto"]),
    )


def test_unmerge_discarding_survivor_edits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _merged(tmp_path, monkeypatch)
    _edit(tmp_path / "bundle" / f"{_SURVIVOR}.md", "\nlater edit\n")()
    _assert_matches_golden(
        "unmerge_discard_survivor_edits",
        _run(
            tmp_path,
            ["unmerge", _SURVIVOR, _ABSORBED, "--auto", "--discard-survivor-edits"],
        ),
    )


def test_unmerge_legacy_ledger_entry_warns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _merged(tmp_path, monkeypatch)
    path = bundle_ledger.ledger_path_for(_SURVIVOR, tmp_path / "bundle")
    metadata, _ = okf.load_frontmatter(path.read_text(encoding="utf-8"))
    raw = metadata["merged_from"]
    assert isinstance(raw, list)
    entries = list(raw)
    tail = dict(entries[-1])
    del tail["survivor_after_sha256"]
    entries[-1] = tail
    metadata["merged_from"] = entries
    path.write_text(okf.dump_frontmatter(metadata), encoding="utf-8")
    _assert_matches_golden(
        "unmerge_legacy_ledger_entry",
        _run(tmp_path, ["unmerge", _SURVIVOR, _ABSORBED, "--auto"]),
    )


def test_unmerge_warns_when_the_catalog_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _merged(tmp_path, monkeypatch)
    _edit(tmp_path / "bundle" / "log.md", "\nlater log line\n")()
    _assert_matches_golden(
        "unmerge_catalog_log_drifted",
        _run(tmp_path, ["unmerge", _SURVIVOR, _ABSORBED, "--auto"]),
    )


def test_unmerge_torn_ledger_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _merged(tmp_path, monkeypatch)
    pending = bundle_ledger.pending_path_for(_SURVIVOR, tmp_path / "bundle")
    pending.parent.mkdir(parents=True, exist_ok=True)
    pending.write_text("stale pending marker", encoding="utf-8")
    _assert_matches_golden(
        "unmerge_torn_ledger",
        _run(tmp_path, ["unmerge", _SURVIVOR, _ABSORBED, "--auto"]),
    )


def test_unmerge_absorbed_path_collision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _merged(tmp_path, monkeypatch)
    (tmp_path / "bundle" / f"{_ABSORBED}.md").write_text("squatter\n", encoding="utf-8")
    _assert_matches_golden(
        "unmerge_absorbed_collision",
        _run(tmp_path, ["unmerge", _SURVIVOR, _ABSORBED, "--auto"]),
    )


def test_unmerge_drift_after_the_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _merged(tmp_path, monkeypatch)
    _simulate_tty(monkeypatch)
    confirm_after(monkeypatch, _edit(tmp_path / "bundle" / f"{_LINKER}.md", "\nedit\n"))
    _assert_matches_golden(
        "unmerge_drift_after_prompt",
        _run(tmp_path, ["unmerge", _SURVIVOR, _ABSORBED]),
    )


def test_unmerge_drift_on_the_unprompted_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _merged(tmp_path, monkeypatch)
    hook = echo_after(
        monkeypatch,
        _edit(tmp_path / "bundle" / f"{_SURVIVOR}.md", "\nedit\n"),
        trigger=f"+ bundle/{_ABSORBED}.md (restore",
    )
    actual = _run(tmp_path, ["unmerge", _SURVIVOR, _ABSORBED, "--auto"])
    assert hook.fired
    _assert_matches_golden("unmerge_drift_unprompted", actual)


def test_unmerge_write_failure_partway(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _merged(tmp_path, monkeypatch)
    real = fsio.write_atomic
    seen: list[str] = []

    def _write(path: Path, text: str) -> None:
        seen.append(Path(path).name)
        if Path(path).name == "log.md" and seen.count("log.md") == 2:
            raise OSError("simulated failure on the audit-line write")
        real(path, text)

    monkeypatch.setattr(fsio, "write_atomic", _write)
    _assert_matches_golden(
        "unmerge_write_failure",
        _run(tmp_path, ["unmerge", _SURVIVOR, _ABSORBED, "--auto"]),
    )


# -- reconcile --------------------------------------------------------------


def _reconcilable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init(tmp_path, monkeypatch)
    _write_concept(tmp_path, "concepts/alpha", title="Alpha", body="Alpha claims X.")
    _write_concept(tmp_path, "concepts/beta", title="Beta", body="Beta claims Y.")


_ALPHA = "concepts/alpha"
_BETA = "concepts/beta"


def test_reconcile_symmetric_auto(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _reconcilable(tmp_path, monkeypatch)
    _assert_matches_golden(
        "reconcile_symmetric", _run(tmp_path, ["reconcile", _ALPHA, _BETA, "--auto"])
    )


def test_reconcile_winner_auto(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _reconcilable(tmp_path, monkeypatch)
    _assert_matches_golden(
        "reconcile_winner",
        _run(tmp_path, ["reconcile", _ALPHA, _BETA, "--winner", _ALPHA, "--auto"]),
    )


def test_reconcile_winner_is_the_second_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _reconcilable(tmp_path, monkeypatch)
    _assert_matches_golden(
        "reconcile_winner_second",
        _run(tmp_path, ["reconcile", _ALPHA, _BETA, "--winner", _BETA, "--auto"]),
    )


def test_reconcile_revision_auto(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _reconcilable(tmp_path, monkeypatch)
    _assert_matches_golden(
        "reconcile_revision",
        _run(tmp_path, ["reconcile", _ALPHA, _BETA, "--revision", _BETA, "--auto"]),
    )


def test_reconcile_rerun_is_a_no_op_with_a_log_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _reconcilable(tmp_path, monkeypatch)
    assert runner.invoke(app, ["reconcile", _ALPHA, _BETA, "--auto"]).exit_code == 0
    _assert_matches_golden(
        "reconcile_idempotent_rerun",
        _run(tmp_path, ["reconcile", _ALPHA, _BETA, "--auto"]),
    )


def test_reconcile_refuses_to_change_an_existing_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _reconcilable(tmp_path, monkeypatch)
    assert runner.invoke(app, ["reconcile", _ALPHA, _BETA, "--auto"]).exit_code == 0
    _assert_matches_golden(
        "reconcile_mode_switch_refused",
        _run(tmp_path, ["reconcile", _ALPHA, _BETA, "--winner", _ALPHA, "--auto"]),
    )


def test_reconcile_refuses_winner_with_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _reconcilable(tmp_path, monkeypatch)
    _assert_matches_golden(
        "reconcile_winner_and_revision",
        _run(
            tmp_path,
            [
                "reconcile",
                _ALPHA,
                _BETA,
                "--winner",
                _ALPHA,
                "--revision",
                _ALPHA,
                "--auto",
            ],
        ),
    )


def test_reconcile_refuses_a_winner_outside_the_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _reconcilable(tmp_path, monkeypatch)
    _write_concept(tmp_path, "concepts/gamma", title="Gamma")
    _assert_matches_golden(
        "reconcile_winner_outside_pair",
        _run(
            tmp_path,
            ["reconcile", _ALPHA, _BETA, "--winner", "concepts/gamma", "--auto"],
        ),
    )


def test_reconcile_refuses_the_same_id_twice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _reconcilable(tmp_path, monkeypatch)
    _assert_matches_golden(
        "reconcile_same_id",
        _run(tmp_path, ["reconcile", _ALPHA, _ALPHA, "--auto"]),
    )


def test_reconcile_needs_two_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _reconcilable(tmp_path, monkeypatch)
    _assert_matches_golden(
        "reconcile_one_id", _run(tmp_path, ["reconcile", _ALPHA, "--auto"])
    )


def test_reconcile_unknown_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _reconcilable(tmp_path, monkeypatch)
    _assert_matches_golden(
        "reconcile_unknown_id",
        _run(tmp_path, ["reconcile", _ALPHA, "concepts/ghost", "--auto"]),
    )


def test_reconcile_outside_a_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _assert_matches_golden(
        "reconcile_not_a_workspace",
        _run(tmp_path, ["reconcile", _ALPHA, _BETA, "--auto"], workspace=False),
    )


def test_reconcile_non_tty_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _reconcilable(tmp_path, monkeypatch)
    _assert_matches_golden(
        "reconcile_non_tty", _run(tmp_path, ["reconcile", _ALPHA, _BETA])
    )


def test_reconcile_tty_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _reconcilable(tmp_path, monkeypatch)
    _simulate_tty(monkeypatch)
    _assert_matches_golden(
        "reconcile_tty_accepted",
        _run(tmp_path, ["reconcile", _ALPHA, _BETA], stdin="y\n"),
    )


def test_reconcile_tty_declined(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _reconcilable(tmp_path, monkeypatch)
    _simulate_tty(monkeypatch)
    _assert_matches_golden(
        "reconcile_tty_declined",
        _run(tmp_path, ["reconcile", _ALPHA, _BETA], stdin="n\n"),
    )


def test_reconcile_drift_after_the_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _reconcilable(tmp_path, monkeypatch)
    _simulate_tty(monkeypatch)
    confirm_after(monkeypatch, _edit(tmp_path / "bundle" / f"{_BETA}.md", "\nedit\n"))
    _assert_matches_golden(
        "reconcile_drift_after_prompt", _run(tmp_path, ["reconcile", _ALPHA, _BETA])
    )


def test_reconcile_drift_on_the_unprompted_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _reconcilable(tmp_path, monkeypatch)
    hook = echo_after(
        monkeypatch,
        _edit(tmp_path / "bundle" / f"{_ALPHA}.md", "\nedit\n"),
        trigger="(new dated entry)",
    )
    actual = _run(tmp_path, ["reconcile", _ALPHA, _BETA, "--auto"])
    assert hook.fired
    _assert_matches_golden("reconcile_drift_unprompted", actual)


def test_reconcile_write_failure_partway(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _reconcilable(tmp_path, monkeypatch)
    _fail_write_of(monkeypatch, "log.md")
    _assert_matches_golden(
        "reconcile_write_failure",
        _run(tmp_path, ["reconcile", _ALPHA, _BETA, "--auto"]),
    )


def test_reconcile_preparation_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _reconcilable(tmp_path, monkeypatch)
    (tmp_path / "bundle" / f"{_BETA}.md").write_text(
        "---\ntype: [unclosed\n---\n", encoding="utf-8"
    )
    _assert_matches_golden(
        "reconcile_preparation_failure",
        _run(tmp_path, ["reconcile", _ALPHA, _BETA, "--auto"]),
    )


def test_reconcile_unreadable_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _reconcilable(tmp_path, monkeypatch)
    (tmp_path / "openkos.yaml").write_text("review: [unterminated\n", encoding="utf-8")
    _assert_matches_golden(
        "reconcile_unreadable_config",
        _run(tmp_path, ["reconcile", _ALPHA, _BETA, "--auto"]),
    )


def _seed_finding(
    root: Path, pair: tuple[str, str], *, verdict: str = "contradicts"
) -> None:
    from openkos import config as config_mod
    from openkos.state import derived, findings

    layout = config_mod.WorkspaceLayout(root)
    conn = derived.open_derived_connection(layout.findings_db_path)
    try:
        findings.record_findings(
            conn,
            [
                findings.Finding(
                    pair_ids=pair,
                    merged_absorbed_id=None,
                    verdict=verdict,
                    confidence=0.9,
                    rationale="they disagree",
                    input_digests=(),
                )
            ],
        )
    finally:
        conn.close()


def test_reconcile_from_findings_walk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _reconcilable(tmp_path, monkeypatch)
    _write_concept(tmp_path, "concepts/gamma", title="Gamma", body="Gamma claims Z.")
    _write_concept(tmp_path, "concepts/delta", title="Delta", body="Delta claims W.")
    _seed_finding(tmp_path, (_ALPHA, _BETA))
    _seed_finding(tmp_path, ("concepts/gamma", "concepts/delta"))
    _simulate_tty(monkeypatch)
    _assert_matches_golden(
        "reconcile_from_findings",
        _run(tmp_path, ["reconcile", "--from-findings"], stdin="y\nn\n"),
    )


def test_reconcile_from_findings_skips_a_pair_resolved_differently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _reconcilable(tmp_path, monkeypatch)
    assert (
        runner.invoke(
            app, ["reconcile", _ALPHA, _BETA, "--winner", _ALPHA, "--auto"]
        ).exit_code
        == 0
    )
    _seed_finding(tmp_path, (_ALPHA, _BETA))
    _simulate_tty(monkeypatch)
    _assert_matches_golden(
        "reconcile_from_findings_conflict",
        _run(tmp_path, ["reconcile", "--from-findings"], stdin="y\n"),
    )
