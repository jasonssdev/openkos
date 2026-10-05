"""`openkos curate`'s accept-recommended Identity question (#1298, ADR-0049).

The selector is proven in `tests/unit/application/test_auto_merge.py`; this
module proves what only the command can: the shared write helper, the offer's
text and answers, the per-merge commits, and every situation that must NOT
offer. No test reaches a model: every judge and the installed-models listing
are stubs, built by the sibling `--auto-merge` module's fixtures.
"""

from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from openkos.application import auto_merge
from openkos.cli import curate
from openkos.cli.main import app
from openkos.llm.base import InstalledModel
from openkos.resolution.adjudication import Verdict
from tests.unit.cli.test_curate_auto_merge import (
    _BAR,
    _CONF,
    _DIFFERENT,
    _FOO,
    _FOO_PAIR,
    _QUX,
    _SAME,
    _build,
    _commit_count,
    _edit_config,
    _family,
    _git,
    _MeasuredOllama,
    _no_prompts,
    _present,
    _priced_calls,
    _prime_verdict,
    _simulate_tty,
)

runner = CliRunner()

_WINDOW = {"context_window: 12288": "context_window: 16384"}


def _walk_only_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Any]:
    """A statically ineligible run (a different context window), so the
    per-item walk is the only thing that can write: the accept-recommended
    offer is never made."""
    root, judge = _build(tmp_path, monkeypatch, [_FOO], {_FOO_PAIR: _SAME})
    _edit_config(root, replace=_WINDOW)
    _simulate_tty(monkeypatch)
    return root, judge


# --------------------------------------------------------------------------- #
# the write helper both paths share (tasks 4.4-4.5)
# --------------------------------------------------------------------------- #


def test_the_walk_writes_each_accepted_item_through_the_shared_helper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _judge = _walk_only_workspace(tmp_path, monkeypatch)
    calls: list[tuple[str, str]] = []
    real = curate._identity_write_one

    def _spy(ctx: curate.CurateContext, prepared: Any) -> bool:
        calls.append((prepared.survivor_canonical, prepared.absorbed_canonical))
        return real(ctx, prepared)

    monkeypatch.setattr(curate, "_identity_write_one", _spy)
    monkeypatch.setattr("typer.prompt", lambda *a, **k: "y")
    before = _git(root, "rev-list", "--count", "HEAD").strip()

    result = runner.invoke(app, ["curate", "--auto"])

    assert result.exit_code == 0, result.stderr
    assert calls == [("concepts/foo", "concepts/foo-2")]
    assert not _present(root, "concepts/foo-2")
    assert int(_git(root, "rev-list", "--count", "HEAD").strip()) == int(before) + 1
    # The per-item commit disclosure is still printed, indented under its item.
    assert any(
        line.startswith("  committed as ") for line in result.stdout.splitlines()
    )


def test_a_drift_refusal_inside_the_helper_still_exits_three(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _judge = _walk_only_workspace(tmp_path, monkeypatch)

    def _answer_after_an_edit(*args: object, **kwargs: object) -> str:
        # The survivor changes while the prompt waits: the commit-phase
        # re-validation inside the helper must refuse it.
        path = root / "bundle" / "concepts" / "foo.md"
        path.write_text(
            path.read_text(encoding="utf-8") + "A hand edit.\n", encoding="utf-8"
        )
        return "y"

    monkeypatch.setattr("typer.prompt", _answer_after_an_edit)

    result = runner.invoke(app, ["curate", "--auto"])

    assert result.exit_code == 3
    assert _present(root, "concepts/foo-2")


def test_a_member_forgotten_while_the_prompt_waits_is_skipped_not_applied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _judge = _walk_only_workspace(tmp_path, monkeypatch)

    def _answer_after_a_forget(*args: object, **kwargs: object) -> str:
        (root / "bundle" / "concepts" / "foo-2.md").unlink()
        return "y"

    monkeypatch.setattr("typer.prompt", _answer_after_a_forget)

    result = runner.invoke(app, ["curate", "--auto"])

    assert result.exit_code == 0, result.stderr
    assert (
        "openkos curate: Identity: skipped concepts/foo-2 -> concepts/foo -- a "
        "member no longer exists." in result.stderr.splitlines()
    )
    assert "Identity: applied 0, skipped 1." in result.stdout.splitlines()


# --------------------------------------------------------------------------- #
# the offer (tasks 4.6-4.7)
# --------------------------------------------------------------------------- #

_OFFER = "Accept all {n} recommended merge(s)? [y/N]"


def _script(monkeypatch: pytest.MonkeyPatch, answers: list[str]) -> list[str]:
    """Answer every prompt from `answers` in order, and record the prompt text
    of each one reached."""
    asked: list[str] = []
    remaining = iter(answers)

    def _prompt(text: str, **kwargs: object) -> str:
        asked.append(text)
        return next(remaining)

    monkeypatch.setattr("typer.prompt", _prompt)
    return asked


def _undo_line(absorbed: str, survivor: str, confidence: str) -> str:
    return (
        f"  {absorbed} -> {survivor} (confidence {confidence}) -- undo: "
        f"openkos unmerge {survivor}"
    )


def _walk_prompts(asked: list[str]) -> list[str]:
    """The per-item merge prompts, as `Merge <absorbed> into <survivor>`."""
    return [a.split("?")[0] for a in asked if a.startswith("Merge ")]


def _commits_since(root: Path, before: int) -> list[str]:
    return _git(root, "log", f"-{_commit_count(root) - before}", "--format=%s").split(
        "\n"
    )[:-1]


_TWO = [_FOO, _BAR]


def _two_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Any]:
    """Two in-class `same` groups, at confidence 0.95 and 0.60, on a TTY."""
    root, judge = _build(
        tmp_path,
        monkeypatch,
        _TWO,
        {_FOO.member_ids: _SAME, _BAR.member_ids: (Verdict.SAME, 0.60)},
    )
    _simulate_tty(monkeypatch)
    return root, judge


def test_the_offer_lists_every_fresh_in_class_same_group_at_any_confidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _root, _judge = _two_workspace(tmp_path, monkeypatch)
    asked = _script(monkeypatch, ["n", "s", "s"])

    result = runner.invoke(app, ["curate", "--auto"])

    assert result.exit_code == 0, result.stderr
    lines = result.stdout.splitlines()
    assert _undo_line("concepts/foo-2", "concepts/foo", "0.95") in lines
    assert _undo_line("concepts/bar-2", "concepts/bar", "0.60") in lines
    assert asked[0] == _OFFER.format(n=2)


def test_yes_accepts_every_listed_item_with_one_commit_per_merge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _judge = _two_workspace(tmp_path, monkeypatch)
    asked = _script(monkeypatch, ["y"])
    before = _commit_count(root)

    result = runner.invoke(app, ["curate", "--auto"])

    assert result.exit_code == 0, result.stderr
    assert asked == [_OFFER.format(n=2)]
    assert not _present(root, "concepts/foo-2")
    assert not _present(root, "concepts/bar-2")
    assert _commit_count(root) == before + 2
    subjects = _commits_since(root, before)
    assert len(subjects) == 2
    assert {s.split(" ")[0] for s in subjects} == {"openkos:"}
    # Each merge discloses its own commit, indented under its item.
    committed = [
        line
        for line in result.stdout.splitlines()
        if line.startswith("  committed as ")
    ]
    assert len(committed) == 2
    assert "Identity: applied 2, skipped 0." in result.stdout.splitlines()


@pytest.mark.parametrize("answer", ["n", "N", "no", ""])
def test_declining_sends_every_group_to_the_per_item_walk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, answer: str
) -> None:
    root, _judge = _two_workspace(tmp_path, monkeypatch)
    asked = _script(monkeypatch, [answer, "s", "s"])
    before = _commit_count(root)

    result = runner.invoke(app, ["curate", "--auto"])

    assert result.exit_code == 0, result.stderr
    assert _walk_prompts(asked) == [
        "Merge concepts/foo-2 into concepts/foo",
        "Merge concepts/bar-2 into concepts/bar",
    ]
    assert _present(root, "concepts/foo-2")
    assert _present(root, "concepts/bar-2")
    assert _commit_count(root) == before


def test_an_unrecognised_answer_asks_again_and_never_merges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _judge = _two_workspace(tmp_path, monkeypatch)
    asked = _script(monkeypatch, ["maybe", "n", "s", "s"])

    result = runner.invoke(app, ["curate", "--auto"])

    assert result.exit_code == 0, result.stderr
    assert asked[:2] == [_OFFER.format(n=2)] * 2
    assert "Unrecognized answer 'maybe'" in result.stdout
    assert _present(root, "concepts/foo-2")


def test_groups_outside_the_set_keep_their_prompt_after_accepting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _judge = _build(
        tmp_path,
        monkeypatch,
        [_FOO, _QUX, _CONF],
        {
            _FOO.member_ids: _SAME,
            _QUX.member_ids: _SAME,  # not in the class
            _CONF.member_ids: _SAME,  # a confidential member
        },
        confidential=("concepts/conf-2",),
    )
    _simulate_tty(monkeypatch)
    asked = _script(monkeypatch, ["y", "s", "s"])

    result = runner.invoke(app, ["curate", "--auto"])

    assert result.exit_code == 0, result.stderr
    assert asked[0] == _OFFER.format(n=1)
    assert not _present(root, "concepts/foo-2")
    assert sorted(_walk_prompts(asked)) == [
        "Merge concepts/conf-2 into concepts/conf",
        "Merge concepts/qux into concepts/qux-b",
    ]
    assert _present(root, "concepts/conf-2")
    assert _present(root, "concepts/qux-b")


def test_a_cross_type_group_is_not_offered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _judge = _build(tmp_path, monkeypatch, [_FOO], {_FOO_PAIR: _SAME})
    other = root / "bundle" / "concepts" / "foo-2.md"
    other.write_text(
        other.read_text(encoding="utf-8").replace("type: Concept", "type: Project", 1),
        encoding="utf-8",
    )
    _simulate_tty(monkeypatch)
    asked = _script(monkeypatch, ["s"])

    result = runner.invoke(app, ["curate", "--auto"])

    assert result.exit_code == 0, result.stderr
    assert not any(a.startswith("Accept all") for a in asked)
    assert _present(root, "concepts/foo-2")


def test_a_guardrail_crossing_item_keeps_its_per_item_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One answer must not cover a merge whose stacked body is mostly the
    absorbed document (the `--apply-same` rule): it is listed, then left for
    its own prompt when it is prepared."""
    root, _judge = _two_workspace(tmp_path, monkeypatch)
    heavy = root / "bundle" / "concepts" / "foo-2.md"
    heavy.write_text(
        heavy.read_text(encoding="utf-8") + "A long body. " * 200, encoding="utf-8"
    )
    asked = _script(monkeypatch, ["y", "s"])
    before = _commit_count(root)

    result = runner.invoke(app, ["curate", "--auto"])

    assert result.exit_code == 0, result.stderr
    assert asked[0] == _OFFER.format(n=2)
    assert _present(root, "concepts/foo-2")
    assert not _present(root, "concepts/bar-2")
    assert _commit_count(root) == before + 1
    assert _walk_prompts(asked) == ["Merge concepts/foo-2 into concepts/foo"]
    assert any(
        line.startswith(
            "openkos curate: Identity: concepts/foo-2 -> concepts/foo keeps its "
            "per-item prompt -- stacked-body guardrail"
        )
        for line in result.stderr.splitlines()
    )


def test_a_served_verdict_is_not_recommended_and_the_cost_line_is_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, judge = _two_workspace(tmp_path, monkeypatch)
    _prime_verdict(root, _FOO, Verdict.SAME)  # an earlier `adjudicate` run
    asked = _script(monkeypatch, ["n", "s", "s"])

    result = runner.invoke(app, ["curate", "--auto"])

    assert result.exit_code == 0, result.stderr
    # Only the group judged this run is offered; the cached one is served.
    assert asked[0] == _OFFER.format(n=1)
    lines = result.stdout.splitlines()
    assert _undo_line("concepts/bar-2", "concepts/bar", "0.60") in lines
    assert _undo_line("concepts/foo-2", "concepts/foo", "0.95") not in lines
    assert judge.asked == 1
    assert _priced_calls(result.stderr) == 1


def test_a_group_the_pass_merged_is_not_offered_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _judge = _two_workspace(tmp_path, monkeypatch)
    asked = _script(monkeypatch, ["y"])

    result = runner.invoke(app, ["curate", "--auto", "--auto-merge"])

    assert result.exit_code == 0, result.stderr
    # foo-2 (0.95) merged by the pass; only bar-2 (0.60) is offered.
    assert asked == [_OFFER.format(n=1)]
    lines = result.stdout.splitlines()
    assert _undo_line("concepts/foo-2", "concepts/foo", "0.95") in lines
    assert not _present(root, "concepts/foo-2")
    assert not _present(root, "concepts/bar-2")


def test_a_survivor_the_pass_already_merged_is_not_offered_a_second_partner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = _family("foo", "-3")
    root, _judge = _build(
        tmp_path,
        monkeypatch,
        [_FOO, other],
        {_FOO.member_ids: _SAME, other.member_ids: (Verdict.SAME, 0.60)},
    )
    _simulate_tty(monkeypatch)
    asked = _script(monkeypatch, ["s"])

    result = runner.invoke(app, ["curate", "--auto", "--auto-merge"])

    assert result.exit_code == 0, result.stderr
    assert not any(a.startswith("Accept all") for a in asked)
    assert _walk_prompts(asked) == ["Merge concepts/foo-3 into concepts/foo"]
    assert _present(root, "concepts/foo-3")


def test_no_question_when_no_group_is_in_the_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An eligible run whose judged groups are all `different` or `uncertain`:
    nothing to recommend, nothing to walk, so no prompt of any kind."""
    _root, _judge = _build(
        tmp_path,
        monkeypatch,
        [_FOO, _BAR],
        {_FOO.member_ids: _DIFFERENT, _BAR.member_ids: (Verdict.UNCERTAIN, 0.9)},
    )
    asked = _script(monkeypatch, [])
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["curate", "--auto"])

    assert result.exit_code == 0, result.stderr
    assert asked == []
    assert "Accept all" not in result.stdout


def test_a_non_tty_run_never_offers_the_question(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _judge = _build(
        tmp_path,
        monkeypatch,
        _TWO,
        {_FOO.member_ids: _SAME, _BAR.member_ids: (Verdict.SAME, 0.60)},
    )
    reached = _no_prompts(monkeypatch)

    result = runner.invoke(app, ["curate", "--auto", "--auto-merge"])

    assert result.exit_code == 0, result.stderr
    assert reached == []
    assert "Accept all" not in result.stdout
    assert _present(root, "concepts/bar-2")  # 0.60 left for a terminal
    assert not _present(root, "concepts/foo-2")  # merged by the pass alone


# --------------------------------------------------------------------------- #
# eligibility: reused when --auto-merge computed it, read lazily otherwise
# --------------------------------------------------------------------------- #


class _CountingListing(_MeasuredOllama):
    reads = 0

    def list_models(self) -> list[InstalledModel]:
        type(self).reads += 1
        return super().list_models()


@pytest.fixture
def listing_reads() -> type[_CountingListing]:
    _CountingListing.reads = 0
    return _CountingListing


def test_the_offer_reuses_the_eligibility_the_flag_already_computed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    listing_reads: type[_CountingListing],
) -> None:
    _root, _judge = _build(
        tmp_path,
        monkeypatch,
        _TWO,
        {_FOO.member_ids: _SAME, _BAR.member_ids: (Verdict.SAME, 0.60)},
        client=listing_reads,
    )
    _simulate_tty(monkeypatch)
    asked = _script(monkeypatch, ["n", "s"])

    result = runner.invoke(app, ["curate", "--auto", "--auto-merge"])

    assert result.exit_code == 0, result.stderr
    assert asked[0] == _OFFER.format(n=1)
    assert listing_reads.reads == 1


def test_without_the_flag_the_listing_is_read_once_when_there_is_a_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    listing_reads: type[_CountingListing],
) -> None:
    _root, _judge = _build(
        tmp_path, monkeypatch, [_FOO], {_FOO_PAIR: _SAME}, client=listing_reads
    )
    _simulate_tty(monkeypatch)
    asked = _script(monkeypatch, ["n", "s"])

    result = runner.invoke(app, ["curate", "--auto"])

    assert result.exit_code == 0, result.stderr
    assert asked[0] == _OFFER.format(n=1)
    assert listing_reads.reads == 1


@pytest.mark.parametrize(
    "verdicts",
    [{_FOO_PAIR: _DIFFERENT}, {_FOO_PAIR: (Verdict.UNCERTAIN, 0.9)}],
    ids=["different", "uncertain"],
)
def test_without_a_candidate_the_listing_is_never_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    listing_reads: type[_CountingListing],
    verdicts: dict[tuple[str, ...], tuple[Verdict, float]],
) -> None:
    _root, _judge = _build(
        tmp_path, monkeypatch, [_FOO], verdicts, client=listing_reads
    )
    _simulate_tty(monkeypatch)
    _script(monkeypatch, [])

    result = runner.invoke(app, ["curate", "--auto"])

    assert result.exit_code == 0, result.stderr
    assert listing_reads.reads == 0


@pytest.mark.parametrize("flag", [[], ["--auto-merge"]], ids=["no flag", "flag"])
def test_an_ineligible_run_offers_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, flag: list[str]
) -> None:
    class _OtherDigest(_MeasuredOllama):
        def list_models(self) -> list[InstalledModel]:
            return [
                InstalledModel(
                    tag=auto_merge.MEASURED_MODEL, family="gemma4", digest="f" * 64
                )
            ]

    root, _judge = _build(
        tmp_path, monkeypatch, [_FOO], {_FOO_PAIR: _SAME}, client=_OtherDigest
    )
    _simulate_tty(monkeypatch)
    asked = _script(monkeypatch, ["s"])

    result = runner.invoke(app, ["curate", "--auto", *flag])

    assert result.exit_code == 0, result.stderr
    assert not any(a.startswith("Accept all") for a in asked)
    assert _walk_prompts(asked) == ["Merge concepts/foo-2 into concepts/foo"]
    assert _present(root, "concepts/foo-2")


def test_a_statically_ineligible_run_offers_nothing_and_reads_no_listing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    listing_reads: type[_CountingListing],
) -> None:
    root, _judge = _build(
        tmp_path, monkeypatch, [_FOO], {_FOO_PAIR: _SAME}, client=listing_reads
    )
    _edit_config(root, replace=_WINDOW)
    _simulate_tty(monkeypatch)
    asked = _script(monkeypatch, ["s"])

    result = runner.invoke(app, ["curate", "--auto"])

    assert result.exit_code == 0, result.stderr
    assert not any(a.startswith("Accept all") for a in asked)
    assert listing_reads.reads == 0
