"""Byte-identity characterization goldens for `openkos curate`'s Identity
walk and `adjudicate --apply`'s keep-distinct answer (the
`curate-decisions-as-data` change, slice S0a).

Recorded on the tree BEFORE the per-item write sequences move into an
application service: each scenario pins stdout, stderr, the exit code, the
commit subjects made and the queue rows resolved. Set
`RECORD_CURATE_GOLDENS=1` to re-record (only ever on an unchanged tree).
"""

import contextlib
import json
import os
import sqlite3
import types
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from openkos import config
from openkos.application import catalog_delta, curate_queue
from openkos.cli import observability
from openkos.cli.main import app
from openkos.llm.base import BackendUnavailable
from openkos.resolution.adjudication import (
    AdjudicatedCandidate,
    AdjudicationBatch,
    Verdict,
)
from openkos.resolution.candidates import CandidateGroup, CandidateGroupReport
from openkos.state import derived
from openkos.state import pending_queue as pq
from tests.unit.cli.golden_support import Goldens, normalise, normalise_shas
from tests.unit.cli.test_curate_auto_merge import (
    _BAR,
    _FOO,
    _SAME,
    _build,
    _edit_config,
    _git,
    _Judge,
    _simulate_tty,
)

runner = CliRunner()

_GOLDENS = Path(__file__).parent / "fixtures" / "curate_characterization_goldens.json"
_RECORD = os.environ.get("RECORD_CURATE_GOLDENS") == "1"
_WINDOW = {"context_window: 12288": "context_window: 16384"}


@pytest.fixture(autouse=True)
def _pinned_environment(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pin everything a golden could read from the machine: the git identity
    through `GIT_CONFIG_COUNT` (`GIT_AUTHOR_*` is NOT read back by
    `git config`, so a CI runner with no global identity prints an
    `identity unset` line), an empty global config, no system config, and no
    reachable model server."""
    empty = tmp_path_factory.mktemp("git-global") / "gitconfig"
    empty.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "2")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "user.name")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "openkos tests")
    monkeypatch.setenv("GIT_CONFIG_KEY_1", "user.email")
    monkeypatch.setenv("GIT_CONFIG_VALUE_1", "tests@openkos.invalid")
    monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:9")
    # `_simulate_tty` makes stderr a terminal, so the embedding progress line
    # prints an elapsed time; a frozen clock keeps it at `0s` on any machine.
    monkeypatch.setattr(
        observability, "time", types.SimpleNamespace(monotonic=lambda: 0.0)
    )


def test_the_pin_is_set_through_the_environment_fixture() -> None:
    assert os.environ["GIT_CONFIG_COUNT"] == "2"
    assert os.environ["GIT_CONFIG_NOSYSTEM"] == "1"
    assert Path(os.environ["GIT_CONFIG_GLOBAL"]).read_text(encoding="utf-8") == ""


# --------------------------------------------------------------------------- #
# the recorder
# --------------------------------------------------------------------------- #


def _commit_subjects(root: Path, since: str) -> list[str]:
    out = _git(root, "log", "--reverse", "--format=%s", f"{since}..HEAD")
    return out.splitlines()


def _rows(root: Path) -> list[list[str | None]]:
    conn = sqlite3.connect(config.WorkspaceLayout(root).findings_db_path)
    try:
        if not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'pending_items'"
        ).fetchone():
            return []
        return [
            [kind, status, resolution]
            for kind, status, resolution in conn.execute(
                "SELECT kind, status, resolution FROM pending_items ORDER BY id"
            )
        ]
    finally:
        conn.close()


def _observe(
    root: Path, args: list[str], *, since: str, input: str | None = None
) -> dict[str, Any]:
    result = runner.invoke(app, args, input=input)

    def _clean(text: str) -> str:
        return normalise_shas(normalise(text, root))

    return {
        "stdout": _clean(result.stdout),
        "stderr": _clean(result.stderr),
        "exit_code": result.exit_code,
        "commits": _commit_subjects(root, since),
        "rows": _rows(root),
    }


def _head(root: Path) -> str:
    return _git(root, "rev-parse", "HEAD").strip()


def _settle(scenario: str, actual: dict[str, Any]) -> None:
    """Record the scenario under `RECORD_CURATE_GOLDENS=1`, else compare."""
    if _RECORD:
        data = (
            json.loads(_GOLDENS.read_text(encoding="utf-8"))
            if _GOLDENS.exists()
            else {}
        )
        data[scenario] = actual
        _GOLDENS.parent.mkdir(parents=True, exist_ok=True)
        _GOLDENS.write_text(
            json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return
    Goldens(_GOLDENS).check(scenario, actual)


def _workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    groups: list[CandidateGroup],
    verdicts: dict[tuple[str, ...], tuple[Verdict, float]],
) -> tuple[Path, _Judge]:
    """`_build` plus an existing (empty) pending queue, so the stage's rows are
    enqueued and the human's answer visibly resolves or keeps them."""
    root, judge = _build(tmp_path, monkeypatch, groups, verdicts)
    layout = config.WorkspaceLayout(root)
    conn = derived.open_derived_connection(layout.findings_db_path)
    try:
        pq.ensure_schema(conn)
    finally:
        conn.close()
    return root, judge


def _walk_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    groups: list[CandidateGroup] | None = None,
) -> tuple[Path, _Judge]:
    """In-class SAME groups the accept-recommended pre-pass cannot offer (a
    different context window makes the run ineligible), so the per-item walk
    is the only thing that can write. The survivor is the base id."""
    members = groups if groups is not None else [_FOO]
    root, judge = _workspace(
        tmp_path, monkeypatch, members, {g.member_ids: _SAME for g in members}
    )
    _edit_config(root, replace=_WINDOW)
    _simulate_tty(monkeypatch)
    return root, judge


def _answers(monkeypatch: pytest.MonkeyPatch, *replies: str) -> None:
    remaining = iter(replies)
    monkeypatch.setattr("typer.prompt", lambda *a, **k: next(remaining))


def _edit(root: Path, concept_id: str) -> None:
    path = root / "bundle" / f"{concept_id}.md"
    path.write_text(
        path.read_text(encoding="utf-8") + "A hand edit.\n", encoding="utf-8"
    )


def test_normalise_shas_replaces_the_hex_token() -> None:
    assert normalise_shas("  committed as a1b2c3d.") == "  committed as <SHA>."


def test_normalise_shas_also_covers_the_revert_hint_in_the_same_sentence() -> None:
    line = "  committed as 6087722 -- `git revert 6087722` undoes it."
    assert normalise_shas(line) == (
        "  committed as <SHA> -- `git revert <SHA>` undoes it."
    )


def test_normalise_shas_makes_two_different_shas_equal() -> None:
    first = "committed as " + "a" * 40
    second = "committed as " + "0123456"
    assert normalise_shas(first) == normalise_shas(second)


def test_normalise_shas_leaves_other_words_untouched() -> None:
    text = "committed as nobody; decided as abcdef0 here; committed as 12"
    assert normalise_shas(text) == text


# --------------------------------------------------------------------------- #
# Identity, the per-item walk
# --------------------------------------------------------------------------- #


def test_identity_y_merges_and_resolves_the_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _judge = _walk_workspace(tmp_path, monkeypatch)
    before = _head(root)

    actual = _observe(root, ["curate", "--auto"], since=before, input="y\n")

    _settle("identity_y", actual)


def test_identity_s_writes_nothing_and_leaves_the_row_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _judge = _walk_workspace(tmp_path, monkeypatch)
    before = _head(root)

    actual = _observe(root, ["curate", "--auto"], since=before, input="s\n")

    _settle("identity_s", actual)


def test_identity_d_records_the_ruling_and_declines_the_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _judge = _walk_workspace(tmp_path, monkeypatch)
    before = _head(root)

    actual = _observe(root, ["curate", "--auto"], since=before, input="d\n")

    _settle("identity_d", actual)


# --------------------------------------------------------------------------- #
# Identity, the bulk paths
# --------------------------------------------------------------------------- #


def test_accept_recommended_writes_each_merge_through_the_per_merge_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _judge = _workspace(
        tmp_path,
        monkeypatch,
        [_FOO, _BAR],
        {_FOO.member_ids: _SAME, _BAR.member_ids: (Verdict.SAME, 0.60)},
    )
    _simulate_tty(monkeypatch)
    before = _head(root)

    actual = _observe(root, ["curate", "--auto"], since=before, input="y\n")

    _settle("accept_recommended", actual)


def test_auto_merge_makes_one_commit_per_run_not_per_merge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _judge = _workspace(
        tmp_path,
        monkeypatch,
        [_FOO, _BAR],
        {_FOO.member_ids: _SAME, _BAR.member_ids: _SAME},
    )
    before = _head(root)

    actual = _observe(root, ["curate", "--auto", "--auto-merge"], since=before)

    _settle("auto_merge", actual)


# --------------------------------------------------------------------------- #
# Identity, the commit phase's refusals
# --------------------------------------------------------------------------- #


def test_a_member_that_vanished_is_skipped_and_the_walk_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _judge = _walk_workspace(tmp_path, monkeypatch)

    def _forget_then_accept(*args: object, **kwargs: object) -> str:
        (root / "bundle" / "concepts" / "foo-2.md").unlink()
        return "y"

    monkeypatch.setattr("typer.prompt", _forget_then_accept)
    before = _head(root)

    actual = _observe(root, ["curate", "--auto"], since=before)

    _settle("member_vanished", actual)


def test_drift_exits_three_with_nothing_written_and_no_later_item(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _judge = _walk_workspace(tmp_path, monkeypatch, [_FOO, _BAR])

    def _edit_then_accept(*args: object, **kwargs: object) -> str:
        _edit(root, "concepts/foo")
        return "y"

    monkeypatch.setattr("typer.prompt", _edit_then_accept)
    before = _head(root)

    actual = _observe(root, ["curate", "--auto"], since=before)

    _settle("drift_exit_three", actual)


def _fail_second_call(target: str, error: Exception) -> Callable[..., Any]:
    """A wrapper over the real function that raises `error` on its second
    call, so the first merge lands and the second one fails."""
    module_path, name = target.rsplit(".", 1)
    module = __import__(module_path, fromlist=[name])
    real = getattr(module, name)
    calls = {"n": 0}

    def _wrapper(*args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 2:
            raise error
        return real(*args, **kwargs)

    return _wrapper


def test_a_failure_at_prepare_exits_one_after_the_first_merge_landed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _judge = _walk_workspace(tmp_path, monkeypatch, [_FOO, _BAR])
    monkeypatch.setattr(
        "openkos.application.lifecycle.prepare_one_merge",
        _fail_second_call(
            "openkos.application.lifecycle.prepare_one_merge", ValueError("no plan")
        ),
    )
    _answers(monkeypatch, "y", "y")
    before = _head(root)

    actual = _observe(root, ["curate", "--auto"], since=before)

    _settle("write_failure_at_prepare", actual)


def test_a_failure_at_the_merge_write_exits_one_after_the_first_merge_landed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _judge = _walk_workspace(tmp_path, monkeypatch, [_FOO, _BAR])
    monkeypatch.setattr(
        "openkos.application.merge_service.commit_merge",
        _fail_second_call(
            "openkos.application.merge_service.commit_merge", OSError("disk full")
        ),
    )
    _answers(monkeypatch, "y", "y")
    before = _head(root)

    actual = _observe(root, ["curate", "--auto"], since=before)

    _settle("write_failure_at_merge", actual)


def test_drift_is_reported_before_a_catalog_recompose_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both guards would fire; curate runs drift first, then the recompose,
    then read-drift -- the order `merge_concepts` does NOT keep."""
    root, _judge = _walk_workspace(tmp_path, monkeypatch)

    def _refuse(*args: object, **kwargs: object) -> object:
        raise catalog_delta.CatalogRecomposeError("the catalog moved")

    monkeypatch.setattr(
        "openkos.application.lifecycle.recompose_merge_catalog", _refuse
    )

    def _edit_then_accept(*args: object, **kwargs: object) -> str:
        _edit(root, "concepts/foo")
        return "y"

    monkeypatch.setattr("typer.prompt", _edit_then_accept)
    before = _head(root)

    actual = _observe(root, ["curate", "--auto"], since=before)

    _settle("guard_order_drift_first", actual)


# --------------------------------------------------------------------------- #
# Identity, the keep-distinct record and the refusals around the walk
# --------------------------------------------------------------------------- #


def test_a_keep_distinct_record_failure_is_fail_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _judge = _walk_workspace(tmp_path, monkeypatch)

    def _fail(*args: object, **kwargs: object) -> str:
        raise OSError("read-only filesystem")

    monkeypatch.setattr("openkos.cli.main._apply_identity_decision", _fail)
    _answers(monkeypatch, "d")
    before = _head(root)

    actual = _observe(root, ["curate", "--auto"], since=before)

    _settle("keep_distinct_record_failure", actual)


def test_a_write_walk_without_a_terminal_refuses_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _judge = _workspace(tmp_path, monkeypatch, [_FOO], {_FOO.member_ids: _SAME})
    before = _head(root)

    actual = _observe(root, ["curate", "--auto"], since=before)

    _settle("non_tty_refusal", actual)


def test_a_backend_that_goes_down_mid_batch_reports_partial_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _judge = _walk_workspace(tmp_path, monkeypatch, [_FOO, _BAR])
    down = _Judge(
        {_FOO.member_ids: _SAME, _BAR.member_ids: _SAME},
        failure=BackendUnavailable("connection refused"),
        stop_after=1,
    )
    monkeypatch.setattr("openkos.cli.curate.adjudicate_candidates", down)
    _answers(monkeypatch, "y")
    before = _head(root)

    actual = _observe(root, ["curate", "--auto"], since=before)

    _settle("partial_availability", actual)


# --------------------------------------------------------------------------- #
# `adjudicate --apply`: the other caller of the keep-distinct helper
# --------------------------------------------------------------------------- #


def test_adjudicate_apply_d_records_the_ruling_without_a_commit_section(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _judge = _workspace(tmp_path, monkeypatch, [_FOO], {_FOO.member_ids: _SAME})
    monkeypatch.setattr(
        "openkos.cli.main.find_candidates_report",
        lambda *a, **k: CandidateGroupReport(groups=(_FOO,), produced=1, retained=1),
    )
    monkeypatch.setattr(
        "openkos.cli.main.adjudicate_candidates",
        lambda candidates, **k: AdjudicationBatch(
            results=[
                AdjudicatedCandidate(
                    candidate=candidates[0],
                    verdict=Verdict.SAME,
                    confidence=0.95,
                    rationale="stub",
                )
            ]
        ),
    )
    # An open row for the pair, so the answer's effect on the queue is visible.
    layout = config.WorkspaceLayout(root)
    curate_queue.enqueue_identity(
        layout,
        [_FOO],
        [
            AdjudicatedCandidate(
                candidate=_FOO,
                verdict=Verdict.SAME,
                confidence=0.95,
                rationale="stub",
            )
        ],
        pinned={},
        commit_section=contextlib.nullcontext,
    )
    before = _head(root)

    actual = _observe(root, ["adjudicate", "--apply"], since=before, input="d\n")

    _settle("adjudicate_apply_d", actual)


def test_every_recorded_scenario_has_a_test() -> None:
    if _RECORD:
        return
    recorded = set(json.loads(_GOLDENS.read_text(encoding="utf-8")))
    assert recorded == {
        "identity_y",
        "identity_s",
        "identity_d",
        "accept_recommended",
        "auto_merge",
        "member_vanished",
        "drift_exit_three",
        "write_failure_at_prepare",
        "write_failure_at_merge",
        "guard_order_drift_first",
        "keep_distinct_record_failure",
        "non_tty_refusal",
        "partial_availability",
        "adjudicate_apply_d",
    }
