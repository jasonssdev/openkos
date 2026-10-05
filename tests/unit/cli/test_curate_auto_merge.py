"""`openkos curate --auto-merge` (#1298, ADR-0049): the CLI contracts around the
structural Identity auto-merge pass.

The pass itself is proven in `tests/unit/application/test_auto_merge.py`; this
module proves what only the command can: the flag and its refusals, the
spend-consent split, the cost line, the ineligibility report, the disclosure
and the exit code. No test reaches a model: every judge is a stub and the
installed-models listing is a stub.
"""

import re
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest
from typer.testing import CliRunner, _NamedTextIOWrapper

from openkos import config
from openkos.application import auto_merge
from openkos.cli import curate
from openkos.cli.main import app
from openkos.llm.base import BackendError, InstalledModel
from openkos.resolution.adjudication import (
    AdjudicatedCandidate,
    AdjudicationBatch,
    Verdict,
)
from openkos.resolution.candidates import CandidateGroup, CandidateGroupReport, Tier
from tests.unit.application.curation_support import write_concept
from tests.unit.conftest import OfflineOllama

runner = CliRunner()

_RECONCILE_REFUSAL = (
    "openkos curate: --auto-merge merges mechanically and cannot be combined "
    "with --reconcile."
)


def _plain(text: str) -> str:
    """Help text with the panel borders and the wrapping removed."""
    return " ".join(re.sub(r"[│╭╮╰╯─]", " ", text).split())


# --------------------------------------------------------------------------- #
# the flag and the --reconcile refusal (tasks 3.1-3.3)
# --------------------------------------------------------------------------- #


def test_reconcile_with_auto_merge_is_refused_before_any_workspace_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run from a directory that is not a workspace: a refusal that came AFTER
    the workspace gate would exit 1 with "refusing to run", so the exact
    message and exit 2 prove the order. The config read is a stub that fails
    if reached."""
    monkeypatch.chdir(tmp_path)
    reads: list[Path] = []

    def _read(root: Path) -> config.Config:
        reads.append(root)
        raise AssertionError("the workspace was read")

    monkeypatch.setattr(config, "read_config", _read)

    result = runner.invoke(app, ["curate", "--reconcile", "--auto-merge"])

    assert result.exit_code == 2
    assert result.stderr.strip() == _RECONCILE_REFUSAL
    assert reads == []


def test_no_reconcile_with_auto_merge_is_not_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["curate", "--no-reconcile", "--auto-merge"])

    # Not a workspace, so it stops at the workspace gate (exit 1) -- the
    # point is that it got that far instead of being refused as a usage error.
    assert result.exit_code == 1
    assert "refusing to run" in result.stderr
    assert "cannot be combined" not in result.stderr


def test_reconcile_alone_is_not_refused_so_the_flag_defaults_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["curate", "--reconcile"])

    assert result.exit_code == 1
    assert "cannot be combined" not in result.stderr


def test_the_context_flag_is_off_unless_it_is_passed() -> None:
    assert curate.CurateContext.__dataclass_fields__["auto_merge"].default is False


def test_help_names_the_class_the_opt_in_the_one_commit_and_the_undo() -> None:
    result = runner.invoke(
        app, ["curate", "--help"], env={"COLUMNS": "200", "NO_COLOR": "1"}
    )

    assert result.exit_code == 0
    page = _plain(result.stdout)
    assert "--auto-merge" in page
    assert "base id and its -N" in page
    assert "this run only" in page
    assert "one commit" in page
    assert "openkos unmerge" in page


# --------------------------------------------------------------------------- #
# the shared fixture: a real workspace in a real git repo, a stub judge
# --------------------------------------------------------------------------- #

_SAME = (Verdict.SAME, 0.95)
_UNSURE = (Verdict.SAME, 0.85)
_DIFFERENT = (Verdict.DIFFERENT, 0.95)


def _git(root: Path, *args: str) -> str:
    return subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def _pin_git_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """A CI runner configures no identity; `GIT_CONFIG_COUNT` is what
    `git config` reads back (`GIT_AUTHOR_*` is not)."""
    monkeypatch.setenv("GIT_CONFIG_COUNT", "2")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "user.name")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "openkos tests")
    monkeypatch.setenv("GIT_CONFIG_KEY_1", "user.email")
    monkeypatch.setenv("GIT_CONFIG_VALUE_1", "tests@openkos.invalid")


def _family(base: str, suffix: str = "-2", *, tier: Tier = Tier.HIGH) -> CandidateGroup:
    return CandidateGroup(
        okf_type="Concept",
        member_ids=(f"concepts/{base}", f"concepts/{base}{suffix}"),
        tier=tier,
        trigger="key",
        member_types=("Concept", "Concept"),
    )


class _Judge:
    """Stands in for `adjudicate_candidates`: one verdict per group, and a
    record of every group it was asked about (the model is called once per
    group, so `asked` is the chat-call count)."""

    def __init__(
        self,
        verdicts: dict[tuple[str, ...], tuple[Verdict, float]],
        *,
        failure: BackendError | None = None,
        stop_after: int | None = None,
        before_returning: Callable[[], None] | None = None,
    ) -> None:
        self.verdicts = verdicts
        self.failure = failure
        self.stop_after = stop_after
        self.before_returning = before_returning
        self.batches: list[tuple[tuple[str, ...], ...]] = []

    @property
    def asked(self) -> int:
        return sum(len(batch) for batch in self.batches)

    def __call__(
        self, to_judge: Sequence[CandidateGroup], **_kwargs: object
    ) -> AdjudicationBatch:
        self.batches.append(tuple(g.member_ids for g in to_judge))
        if self.before_returning is not None:
            self.before_returning()
        judged = list(to_judge)
        if self.stop_after is not None:
            judged = judged[: self.stop_after]
        results = [
            AdjudicatedCandidate(
                candidate=group,
                verdict=self.verdicts[group.member_ids][0],
                confidence=self.verdicts[group.member_ids][1],
                rationale="stub",
            )
            for group in judged
        ]
        return AdjudicationBatch(results=results, failure=self.failure)


class _MeasuredOllama(OfflineOllama):
    """An Ollama whose listing reports exactly the measured model digest."""

    def list_models(self) -> list[InstalledModel]:
        return [
            InstalledModel(
                tag=auto_merge.MEASURED_MODEL,
                family="gemma4",
                digest=auto_merge.MEASURED_MODEL_DIGEST,
            )
        ]


def _simulate_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_NamedTextIOWrapper, "isatty", lambda self: True)


def _build(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    groups: Sequence[CandidateGroup],
    verdicts: dict[tuple[str, ...], tuple[Verdict, float]],
    *,
    confidential: Sequence[str] = (),
    client: type[OfflineOllama] = _MeasuredOllama,
    judge: _Judge | None = None,
) -> tuple[Path, _Judge]:
    """A workspace holding every member of `groups`, committed, with the
    candidate finder and the judge stubbed and the listing eligible."""
    _pin_git_identity(monkeypatch)
    root = tmp_path / "ws"
    root.mkdir()
    monkeypatch.chdir(root)
    assert runner.invoke(app, ["init"]).exit_code == 0
    for group in groups:
        for member in group.member_ids:
            path = write_concept(root, member, title=member.rsplit("/", 1)[1])
            # The pass reads an absent sensitivity as blocked, so every
            # document states one, as `ingest` stamps it.
            level = "confidential" if member in confidential else "private"
            path.write_text(
                path.read_text(encoding="utf-8").replace(
                    "type: Concept\n", f"type: Concept\nsensitivity: {level}\n", 1
                ),
                encoding="utf-8",
            )
    assert runner.invoke(app, ["reindex"]).exit_code == 0
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "fixture")

    stub = judge if judge is not None else _Judge(verdicts)
    monkeypatch.setattr(
        "openkos.cli.curate.find_candidates_report",
        lambda *a, **k: CandidateGroupReport(
            groups=tuple(groups), produced=len(groups), retained=len(groups)
        ),
    )
    monkeypatch.setattr("openkos.cli.curate.adjudicate_candidates", stub)
    monkeypatch.setattr("openkos.cli.main.OllamaClient", client)
    # Identity only: the later stages' queues are empty.
    monkeypatch.setattr("openkos.cli.curate.candidate_edges", lambda *a, **k: [])
    monkeypatch.setattr("openkos.cli.curate._concept_type_names", lambda *a, **k: [])
    from openkos.resolution.contradiction import CandidatePlan

    monkeypatch.setattr(
        "openkos.cli.curate._contradiction_plan",
        lambda *a, **k: CandidatePlan(specs=(), edge_total=0, merged_total=0),
    )
    return root, stub


def _present(root: Path, concept_id: str) -> bool:
    return (root / "bundle" / f"{concept_id}.md").exists()


_FOO = _family("foo")
_FOO_PAIR = _FOO.member_ids
_UNATTENDED_REFUSAL = (
    "Identity: non-interactive write consent unavailable -- run "
    "`openkos adjudicate --apply-same --confirm-count <n>` instead."
)


# --------------------------------------------------------------------------- #
# spend consent stays separate (tasks 3.4-3.5)
# --------------------------------------------------------------------------- #


def test_auto_merge_alone_on_a_non_tty_declines_before_any_model_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, judge = _build(tmp_path, monkeypatch, [_FOO], {_FOO_PAIR: _DIFFERENT})

    result = runner.invoke(app, ["curate", "--auto-merge"])

    assert result.exit_code == 0
    assert "Identity: declined -- no LLM calls made." in result.stdout.splitlines()
    assert judge.asked == 0
    assert _present(root, "concepts/foo-2")


def test_auto_merge_alone_on_a_tty_still_asks_for_the_spend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _root, judge = _build(tmp_path, monkeypatch, [_FOO], {_FOO_PAIR: _DIFFERENT})
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["curate", "--auto-merge"], input="n\n")

    assert "1 candidate group(s) -> 1 LLM call(s)" in result.stderr
    assert "Proceed?" in result.stdout
    assert "Identity: declined -- no LLM calls made." in result.stdout.splitlines()
    assert judge.asked == 0


def test_auto_with_auto_merge_passes_the_cost_gate_on_a_non_tty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _root, judge = _build(tmp_path, monkeypatch, [_FOO], {_FOO_PAIR: _DIFFERENT})

    result = runner.invoke(app, ["curate", "--auto", "--auto-merge"])

    assert result.exit_code == 0
    assert judge.asked == 1
    assert _UNATTENDED_REFUSAL not in result.stdout


def test_auto_without_the_flag_keeps_the_identity_refusal_on_a_non_tty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, judge = _build(tmp_path, monkeypatch, [_FOO], {_FOO_PAIR: _SAME})

    result = runner.invoke(app, ["curate", "--auto"])

    assert result.exit_code == 0
    assert any(
        line.startswith(_UNATTENDED_REFUSAL) for line in result.stdout.splitlines()
    )
    assert judge.asked == 0
    assert _present(root, "concepts/foo-2")


def test_review_false_does_not_enable_the_exemption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, judge = _build(tmp_path, monkeypatch, [_FOO], {_FOO_PAIR: _SAME})
    cfg_path = root / "openkos.yaml"
    cfg_path.write_text(
        cfg_path.read_text(encoding="utf-8").replace("review: true", "review: false"),
        encoding="utf-8",
    )
    assert "review: false" in cfg_path.read_text(encoding="utf-8")

    result = runner.invoke(app, ["curate", "--auto"])

    assert any(
        line.startswith(_UNATTENDED_REFUSAL) for line in result.stdout.splitlines()
    )
    assert judge.asked == 0


def test_accept_identity_stays_refused_alongside_the_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, judge = _build(tmp_path, monkeypatch, [_FOO], {_FOO_PAIR: _SAME})

    result = runner.invoke(
        app, ["curate", "--auto", "--auto-merge", "--accept", "identity"]
    )

    assert result.exit_code == 2
    assert judge.asked == 0
    assert _present(root, "concepts/foo-2")


def test_the_exemption_covers_identity_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Another writing stage on a non-TTY still declines under the flag."""
    _build(tmp_path, monkeypatch, [_FOO], {_FOO_PAIR: _DIFFERENT})
    ran: list[str] = []

    def _run(
        ctx: curate.CurateContext, probe: curate.StageProbe
    ) -> curate.StageOutcome:
        ran.append("run")
        return curate.StageOutcome(status="applied", applied=1)

    writer = curate.Stage(
        name="Metadata",
        noun="thing",
        probe=lambda ctx: curate.StageProbe(items=(1,), llm_calls=1),
        run=_run,
        needs_llm=False,
        writes=True,
        unattended_hint="openkos stub --apply",
    )
    monkeypatch.setattr(curate, "_STAGES", (writer,))

    result = runner.invoke(app, ["curate", "--auto", "--auto-merge"])

    assert ran == []
    assert (
        "Metadata: non-interactive write consent unavailable -- run "
        "`openkos stub --apply` instead." in result.stdout.splitlines()
    )


# --------------------------------------------------------------------------- #
# probe parity: the cost line prices what the run pays (tasks 3.6-3.7)
# --------------------------------------------------------------------------- #

_BAR = _family("bar")
_QUX = _family("qux", "-b")  # outside the class: `-b` is not a numeric suffix
_ALL_DIFFERENT = {
    _FOO.member_ids: _DIFFERENT,
    _BAR.member_ids: _DIFFERENT,
    _QUX.member_ids: _DIFFERENT,
}


def _prime_store(root: Path, group: CandidateGroup) -> None:
    """A persisted verdict for `group`, as an earlier `adjudicate` run leaves."""
    from openkos.cli import main as cli_main

    cli_main._persist_adjudications(
        config.WorkspaceLayout(root),
        [
            AdjudicatedCandidate(
                candidate=group,
                verdict=Verdict.DIFFERENT,
                confidence=0.95,
                rationale="cached",
            )
        ],
        include_confidential=True,
    )


def _priced_calls(stderr: str) -> int:
    match = re.search(r"-> (\d+) LLM call\(s\)", stderr)
    assert match is not None, stderr
    return int(match.group(1))


def _parity_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **kwargs: object
) -> tuple[Path, _Judge]:
    root, judge = _build(
        tmp_path,
        monkeypatch,
        [_FOO, _BAR, _QUX],
        _ALL_DIFFERENT,
        **kwargs,  # type: ignore[arg-type]
    )
    _prime_store(root, _FOO)  # an in-class group the store already answers
    return root, judge


def test_probe_without_the_flag_prices_only_the_unserved_groups(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _root, judge = _parity_workspace(tmp_path, monkeypatch)
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["curate", "--auto"])

    assert "1 of 3 candidate group(s) served from persisted adjudications" in (
        result.stderr
    )
    assert _priced_calls(result.stderr) == 2
    assert judge.asked == 2


def test_probe_with_the_flag_prices_the_in_class_groups_it_judges_fresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _root, judge = _parity_workspace(tmp_path, monkeypatch)
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["curate", "--auto", "--auto-merge"])

    # The cached in-class group is judged again: only a verdict this run's
    # model produced may be acted on.
    assert _priced_calls(result.stderr) == 3
    assert judge.asked == 3


def test_probe_with_the_flag_on_a_non_tty_prices_only_the_in_class_groups(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _root, judge = _parity_workspace(tmp_path, monkeypatch)

    result = runner.invoke(app, ["curate", "--auto", "--auto-merge"])

    assert _priced_calls(result.stderr) == 2
    assert judge.asked == 2
    assert judge.batches == [(_FOO.member_ids, _BAR.member_ids)]


def test_probe_with_a_statically_ineligible_run_prices_as_today(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, judge = _parity_workspace(tmp_path, monkeypatch)
    cfg_path = root / "openkos.yaml"
    cfg_path.write_text(
        cfg_path.read_text(encoding="utf-8").replace(
            "context_window: 12288", "context_window: 16384"
        ),
        encoding="utf-8",
    )
    assert "context_window: 16384" in cfg_path.read_text(encoding="utf-8")
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["curate", "--auto", "--auto-merge"])

    assert _priced_calls(result.stderr) == 2
    assert judge.asked == 2


def test_a_digest_mismatch_at_run_time_pays_less_than_the_line_priced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _OtherDigest(_MeasuredOllama):
        def list_models(self) -> list[InstalledModel]:
            return [
                InstalledModel(
                    tag=auto_merge.MEASURED_MODEL, family="gemma4", digest="f" * 64
                )
            ]

    _root, judge = _parity_workspace(tmp_path, monkeypatch, client=_OtherDigest)
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["curate", "--auto", "--auto-merge"])

    # The probe cannot read the digest (it does no I/O), so it priced the
    # in-class group as fresh; the run found the digest wrong and fell back to
    # serving it. Under, never over.
    assert _priced_calls(result.stderr) == 3
    assert judge.asked == 2


# --------------------------------------------------------------------------- #
# an ineligible run says why, for every failed check (tasks 3.8-3.9)
# --------------------------------------------------------------------------- #


def _edit_config(
    root: Path, *, replace: dict[str, str] | None = None, append: str = ""
) -> None:
    path = root / "openkos.yaml"
    text = path.read_text(encoding="utf-8")
    for old, new in (replace or {}).items():
        assert old in text, old
        text = text.replace(old, new, 1)
    path.write_text(text + append, encoding="utf-8")


def _unavailable_line(*reasons: str) -> str:
    return (
        "openkos curate: Identity: --auto-merge is not available this run -- "
        + "; ".join(reasons)
        + "; every group keeps its per-item prompt."
    )


_MODEL_REASON = (
    "adjudication model is 'qwen3:8b', the measured model is 'gemma4:26b-a4b'"
)
_WINDOW_REASON = "context_window is 16384, the measured value is 12288"
_TOKENS_REASON = "max_generation_tokens is 4096, the measured value is 8192"
_TEMPERATURE_REASON = "temperature is pinned to 0.2, the measured run pinned none"
_SEED_REASON = "seed is pinned to 42, the measured run pinned none"


@pytest.mark.parametrize(
    ("replace", "append", "reasons"),
    [
        pytest.param(
            {}, "models:\n  adjudication: qwen3:8b\n", (_MODEL_REASON,), id="model tag"
        ),
        pytest.param(
            {"context_window: 12288": "context_window: 16384"},
            "",
            (_WINDOW_REASON,),
            id="context_window",
        ),
        pytest.param(
            {"max_generation_tokens: 8192": "max_generation_tokens: 4096"},
            "",
            (_TOKENS_REASON,),
            id="max_generation_tokens",
        ),
        pytest.param(
            {}, "temperature: 0.2\n", (_TEMPERATURE_REASON,), id="temperature"
        ),
        pytest.param({}, "seed: 42\n", (_SEED_REASON,), id="seed"),
        pytest.param(
            {"context_window: 12288": "context_window: 16384"},
            "seed: 42\n",
            (_WINDOW_REASON, _SEED_REASON),
            id="every failed check is named",
        ),
    ],
)
def test_a_statically_ineligible_run_prints_every_reason(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replace: dict[str, str],
    append: str,
    reasons: tuple[str, ...],
) -> None:
    root, _judge = _build(tmp_path, monkeypatch, [_FOO], {_FOO_PAIR: _SAME})
    _edit_config(root, replace=replace, append=append)
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["curate", "--auto", "--auto-merge"], input="s\n")

    assert _unavailable_line(*reasons) in result.stderr.splitlines()


def test_a_digest_mismatch_is_reported_and_the_walk_runs_as_today(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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

    result = runner.invoke(app, ["curate", "--auto", "--auto-merge"], input="s\n")

    assert (
        _unavailable_line(
            "installed digest ffffffffffff differs from the measured digest 001e5dafc3c7"
        )
        in result.stderr.splitlines()
    )
    # A TTY then proceeds into today's per-item walk: the pair was offered and
    # the answer `s` skipped it.
    assert "Identity: applied 0, skipped 1." in result.stdout.splitlines()
    assert _present(root, "concepts/foo-2")


def test_a_listing_failure_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _Down(_MeasuredOllama):
        def list_models(self) -> list[InstalledModel]:
            raise BackendError("connection refused")

    _root, _judge = _build(
        tmp_path, monkeypatch, [_FOO], {_FOO_PAIR: _SAME}, client=_Down
    )
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["curate", "--auto", "--auto-merge"], input="s\n")

    assert (
        _unavailable_line("could not list installed models (connection refused)")
        in result.stderr.splitlines()
    )


def test_an_unknown_digest_on_an_openai_compatible_backend_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.unit.conftest import OfflineOpenAICompatible

    class _NoDigest(OfflineOpenAICompatible):
        def list_models(self) -> list[InstalledModel]:
            return [InstalledModel(tag=auto_merge.MEASURED_MODEL, family=None)]

    root, _judge = _build(tmp_path, monkeypatch, [_FOO], {_FOO_PAIR: _SAME})
    _edit_config(
        root,
        replace={"model: qwen3:8b": "model: gemma4:26b-a4b"},
        append="backend: openai-compatible\nbase_url: http://127.0.0.1:9\n",
    )
    monkeypatch.setattr("openkos.cli.main.OpenAICompatibleClient", _NoDigest)
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["curate", "--auto", "--auto-merge"], input="s\n")

    assert (
        _unavailable_line(
            "the backend reports no digest for 'gemma4:26b-a4b' (digest unknown)"
        )
        in result.stderr.splitlines()
    )


def test_an_ineligible_non_tty_run_is_declined_with_the_hint_and_judges_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, judge = _build(tmp_path, monkeypatch, [_FOO], {_FOO_PAIR: _SAME})
    _edit_config(root, append="seed: 42\n")

    result = runner.invoke(app, ["curate", "--auto", "--auto-merge"])

    assert result.exit_code == 0
    assert _unavailable_line(_SEED_REASON) in result.stderr.splitlines()
    assert (
        "Identity: non-interactive write consent unavailable -- run "
        "`openkos adjudicate --apply-same --confirm-count <n>` instead."
        in result.stdout.splitlines()
    )
    assert judge.asked == 0
    assert _present(root, "concepts/foo-2")


def test_an_eligible_run_prints_no_ineligibility_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _root, judge = _build(tmp_path, monkeypatch, [_FOO], {_FOO_PAIR: _DIFFERENT})

    result = runner.invoke(app, ["curate", "--auto", "--auto-merge"])

    assert "is not available this run" not in result.stderr
    assert judge.asked == 1


# --------------------------------------------------------------------------- #
# the pass, end to end: `curate --auto --auto-merge` on a pipe (tasks 3.10-3.11)
# --------------------------------------------------------------------------- #

_CONF = _family("conf")
_ZED = _family("zed")
_MIXED = [_FOO, _BAR, _family("baz"), _CONF, _QUX]
_MIXED_VERDICTS = {
    _FOO.member_ids: _SAME,
    _BAR.member_ids: _UNSURE,
    _family("baz").member_ids: _DIFFERENT,
    _CONF.member_ids: _SAME,
    _QUX.member_ids: _SAME,
}
_COMMIT_SENTENCE = re.compile(
    r"^  committed as ([0-9a-f]+) -- `git revert \1` undoes it only while it is "
    r"the latest commit\.$"
)


def _commit_count(root: Path) -> int:
    return int(_git(root, "rev-list", "--count", "HEAD").strip())


def _head_files(root: Path) -> set[str]:
    return set(_git(root, "show", "--name-only", "--format=", "HEAD").split())


def _head_sha(root: Path) -> str:
    return _git(root, "rev-parse", "--short", "HEAD").strip()


def _no_prompts(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Make every prompt fail loudly and record the ones that were reached."""
    reached: list[str] = []

    def _fail(*args: object, **kwargs: object) -> str:
        reached.append(str(args[0]) if args else "")
        raise AssertionError("a prompt was reached")

    monkeypatch.setattr("typer.prompt", _fail)
    monkeypatch.setattr("typer.confirm", _fail)
    return reached


def _mixed_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, _Judge]:
    return _build(
        tmp_path,
        monkeypatch,
        _MIXED,
        _MIXED_VERDICTS,
        confidential=("concepts/conf-2",),
    )


def test_a_non_tty_run_merges_exactly_the_eligible_pair_in_one_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openkos.bundle import ledger as bundle_ledger

    root, judge = _mixed_workspace(tmp_path, monkeypatch)
    reached = _no_prompts(monkeypatch)
    before = _commit_count(root)

    result = runner.invoke(app, ["curate", "--auto", "--auto-merge"])

    assert result.exit_code == 0, result.stderr
    assert reached == []
    # One commit holding exactly the one eligible merge.
    assert _commit_count(root) == before + 1
    sidecar = bundle_ledger.ledger_path_for(
        "concepts/foo", config.WorkspaceLayout(root).bundle_dir
    ).relative_to(root)
    assert _head_files(root) == {
        "bundle/index.md",
        "bundle/log.md",
        "bundle/concepts/foo.md",
        "bundle/concepts/foo-2.md",
        sidecar.as_posix(),
    }
    # Every other pair is untouched on disk.
    assert not _present(root, "concepts/foo-2")
    for kept in (
        "concepts/foo",
        "concepts/bar",
        "concepts/bar-2",
        "concepts/baz-2",
        "concepts/conf",
        "concepts/conf-2",
        "concepts/qux-b",
    ):
        assert _present(root, kept), kept
    # Only the in-class groups were judged; the non-class pair never was.
    assert judge.batches == [
        (
            _FOO.member_ids,
            _BAR.member_ids,
            _family("baz").member_ids,
            _CONF.member_ids,
        )
    ]

    lines = result.stdout.splitlines()
    sha = _head_sha(root)
    header = (
        "openkos curate: Identity: merged 1 pair(s) automatically (measured "
        "class, gemma4:26b-a4b, confidence >= 0.90):"
    )
    merge_line = (
        "  concepts/foo-2 -> concepts/foo (confidence 0.95) -- undo: "
        "openkos unmerge concepts/foo"
    )
    assert lines.index(merge_line) == lines.index(header) + 1
    assert _COMMIT_SENTENCE.match(lines[lines.index(merge_line) + 1])
    assert lines[lines.index(merge_line) + 1].split()[2] == sha
    assert (
        "Identity: applied 1 automatically; 2 in-class group(s) left for review "
        "-- run `openkos curate` on a terminal." in lines
    )
    stderr = result.stderr.splitlines()
    assert (
        "openkos curate: Identity: not merged automatically: concepts/bar-2 -> "
        "concepts/bar -- confidence 0.85 below 0.90." in stderr
    )
    assert (
        "openkos curate: Identity: not merged automatically: concepts/conf-2 -> "
        "concepts/conf -- a member is confidential or cannot be sent to the "
        "model." in stderr
    )


def test_the_prompt_patch_is_the_one_a_terminal_run_reaches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The control for the test above: the same workspace on a TTY DOES reach
    the patched prompt for a group the pass left, so "never called" there is
    not an unread patch."""
    root, _judge = _mixed_workspace(tmp_path, monkeypatch)
    asked: list[str] = []

    def _answer(prompt_text: str, **kwargs: object) -> str:
        asked.append(prompt_text)
        return "s"

    monkeypatch.setattr("typer.prompt", _answer)
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["curate", "--auto", "--auto-merge"])

    assert result.exit_code == 0, result.stderr
    # bar-2 (below t*), conf-2 (confidential) and the non-class qux pair keep
    # their per-item prompt; foo-2 was merged without one and is not re-offered.
    assert [p.split("?")[0] for p in asked] == [
        "Merge concepts/bar-2 into concepts/bar",
        "Merge concepts/conf-2 into concepts/conf",
        "Merge concepts/qux into concepts/qux-b",
    ]
    assert not _present(root, "concepts/foo-2")
    assert "Identity: applied 1, skipped 3." in result.stdout.splitlines()


def test_two_eligible_pairs_share_one_commit_and_each_names_its_undo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _judge = _build(
        tmp_path,
        monkeypatch,
        [_FOO, _ZED],
        {_FOO.member_ids: _SAME, _ZED.member_ids: (Verdict.SAME, 0.97)},
    )
    before = _commit_count(root)

    result = runner.invoke(app, ["curate", "--auto", "--auto-merge"])

    assert result.exit_code == 0, result.stderr
    assert _commit_count(root) == before + 1
    assert _git(root, "log", "-1", "--format=%B").splitlines()[0] == (
        "openkos: auto-merge 2 pair(s) in the measured identity class"
    )
    lines = result.stdout.splitlines()
    header = lines.index(
        "openkos curate: Identity: merged 2 pair(s) automatically (measured "
        "class, gemma4:26b-a4b, confidence >= 0.90):"
    )
    assert lines[header + 1 : header + 3] == [
        "  concepts/foo-2 -> concepts/foo (confidence 0.95) -- undo: "
        "openkos unmerge concepts/foo",
        "  concepts/zed-2 -> concepts/zed (confidence 0.97) -- undo: "
        "openkos unmerge concepts/zed",
    ]
    assert _COMMIT_SENTENCE.match(lines[header + 3])


def test_a_degraded_commit_prints_the_merges_without_a_commit_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _judge = _build(tmp_path, monkeypatch, [_FOO], {_FOO.member_ids: _SAME})
    monkeypatch.setattr("openkos.cli.main._autocommit", lambda *a, **k: None)

    result = runner.invoke(app, ["curate", "--auto", "--auto-merge"])

    assert result.exit_code == 0, result.stderr
    assert not _present(root, "concepts/foo-2")
    assert (
        "  concepts/foo-2 -> concepts/foo (confidence 0.95) -- undo: "
        "openkos unmerge concepts/foo" in result.stdout.splitlines()
    )
    assert "committed as" not in result.stdout


def _prime_verdict(root: Path, group: CandidateGroup, verdict: Verdict) -> None:
    from openkos.cli import main as cli_main

    cli_main._persist_adjudications(
        config.WorkspaceLayout(root),
        [
            AdjudicatedCandidate(
                candidate=group, verdict=verdict, confidence=0.99, rationale="cached"
            )
        ],
        include_confidential=True,
    )


def test_a_cached_same_is_never_acted_on_when_the_fresh_verdict_disagrees(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, judge = _build(
        tmp_path, monkeypatch, [_FOO], {_FOO.member_ids: (Verdict.UNCERTAIN, 0.9)}
    )
    _prime_verdict(root, _FOO, Verdict.SAME)

    result = runner.invoke(app, ["curate", "--auto", "--auto-merge"])

    assert result.exit_code == 0, result.stderr
    assert judge.asked == 1  # judged again although the store answered
    assert _present(root, "concepts/foo-2")


def _queue_identity_row(root: Path, group: CandidateGroup) -> None:
    """An open identity row holding a `same` verdict at 0.99, as the unattended
    engine leaves one."""
    import contextlib
    import json

    from openkos.application import pending as application_pending
    from openkos.state import derived
    from openkos.state import pending_queue as pq

    layout = config.WorkspaceLayout(root)
    digest_of = application_pending.current_finding_digest(layout.bundle_dir)
    conn = derived.open_derived_connection(layout.findings_db_path)
    try:
        pq.ensure_schema(conn)
        pq.upsert_proposal(
            conn,
            pq.Proposal(
                kind="identity",
                key_body=pq.identity_key(group.member_ids),
                producer="duplicates/1",
                payload=json.dumps(
                    {
                        "member_ids": list(group.member_ids),
                        "member_types": list(group.member_types),
                        "okf_type": group.okf_type,
                        "tier": group.tier.value,
                        "trigger": group.trigger,
                        "adjudication": {
                            "verdict": "same",
                            "confidence": 0.99,
                            "rationale": "queued",
                        },
                    }
                ),
                targets=group.member_ids,
                input_digests=tuple(
                    pq.InputDigest(m, str(digest_of(m))) for m in group.member_ids
                ),
            ),
            commit_section=contextlib.nullcontext,
            bundle_dir=layout.bundle_dir,
        )
    finally:
        conn.close()


def test_a_queued_same_row_is_never_acted_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openkos.application import curate_queue

    root, judge = _build(
        tmp_path, monkeypatch, [_FOO], {_FOO.member_ids: (Verdict.UNCERTAIN, 0.9)}
    )
    _queue_identity_row(root, _FOO)
    assert curate_queue.identity_verdicts(config.WorkspaceLayout(root))

    result = runner.invoke(app, ["curate", "--auto", "--auto-merge"])

    assert result.exit_code == 0, result.stderr
    assert judge.asked == 1  # judged again although the queue answered
    assert _present(root, "concepts/foo-2")


def test_a_capped_batch_still_merges_its_completed_verdicts_and_reports_the_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    judge = _Judge(
        {_FOO.member_ids: _SAME, _BAR.member_ids: _SAME},
        failure=BackendError("generation capped"),
        stop_after=1,
    )
    root, _ = _build(tmp_path, monkeypatch, [_FOO, _BAR], {}, judge=judge)

    result = runner.invoke(app, ["curate", "--auto", "--auto-merge"])

    assert result.exit_code == 0, result.stderr
    assert not _present(root, "concepts/foo-2")  # the completed verdict merged
    assert _present(root, "concepts/bar-2")  # the group past the failure did not
    assert (
        "Identity: failed -- generation capped (adjudicated 1 of 2 candidate "
        "group(s); applied 1, skipped 0)." in result.stdout.splitlines()
    )


def _stored_verdicts(root: Path) -> dict[tuple[str, ...], str]:
    from openkos.state import adjudications as adjudications_store
    from openkos.state import derived

    conn = derived.open_derived_connection(
        config.WorkspaceLayout(root).findings_db_path
    )
    try:
        return {
            tuple(row.member_ids): row.verdict
            for row in adjudications_store.open_adjudications(conn)
        }
    finally:
        conn.close()


def test_a_fresh_in_class_verdict_replaces_the_cached_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _judge = _build(
        tmp_path, monkeypatch, [_FOO], {_FOO.member_ids: (Verdict.UNCERTAIN, 0.9)}
    )
    _prime_verdict(root, _FOO, Verdict.SAME)
    assert _stored_verdicts(root) == {_FOO.member_ids: "same"}

    result = runner.invoke(app, ["curate", "--auto", "--auto-merge"])

    assert result.exit_code == 0, result.stderr
    # Display-only write-back: the newest judgment is what a later prompt shows.
    assert _stored_verdicts(root) == {_FOO.member_ids: "uncertain"}


@pytest.mark.parametrize("prime", ["store", "queue"])
def test_on_a_terminal_a_served_same_is_judged_again_and_not_merged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, prime: str
) -> None:
    root, judge = _build(
        tmp_path, monkeypatch, [_FOO], {_FOO.member_ids: (Verdict.UNCERTAIN, 0.9)}
    )
    if prime == "store":
        _prime_verdict(root, _FOO, Verdict.SAME)
    else:
        _queue_identity_row(root, _FOO)
    _simulate_tty(monkeypatch)
    monkeypatch.setattr("typer.prompt", lambda *a, **k: "s")

    result = runner.invoke(app, ["curate", "--auto", "--auto-merge"])

    assert result.exit_code == 0, result.stderr
    assert judge.asked == 1
    assert _present(root, "concepts/foo-2")


def test_the_planner_is_given_only_this_runs_judged_groups_as_fresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On a terminal the results also hold a served group (here the cached
    `qux` pair). `fresh_keys` must name only what this run's model judged: it
    is the one input that keeps a served verdict from ever being acted on."""
    from openkos.state import adjudications as adjudications_store

    root, judge = _build(
        tmp_path,
        monkeypatch,
        [_FOO, _QUX],
        {_FOO.member_ids: _SAME, _QUX.member_ids: _DIFFERENT},
    )
    _prime_store(root, _QUX)
    seen: list[frozenset[str]] = []
    real = auto_merge.plan_auto_merges

    def _spy(results: Sequence[AdjudicatedCandidate], **kwargs: object) -> object:
        seen.append(kwargs["fresh_keys"])  # type: ignore[arg-type]
        assert len(results) == 2  # the served group is among them
        return real(results, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(auto_merge, "plan_auto_merges", _spy)
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["curate", "--auto", "--auto-merge"])

    assert result.exit_code == 0, result.stderr
    assert judge.batches == [(_FOO.member_ids,)]
    assert seen == [frozenset({adjudications_store.group_key_for(_FOO.member_ids)})]


def test_a_pair_whose_files_disagree_on_type_is_not_merged_automatically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openkos.application import lifecycle

    root, _judge = _build(tmp_path, monkeypatch, [_FOO], {_FOO.member_ids: _SAME})
    # The candidate group says both are Concepts; the file on disk disagrees.
    other = root / "bundle" / "concepts" / "foo-2.md"
    other.write_text(
        other.read_text(encoding="utf-8").replace("type: Concept", "type: Project", 1),
        encoding="utf-8",
    )
    concern = lifecycle.cross_type_concern(
        config.WorkspaceLayout(root).bundle_dir, _FOO_PAIR
    )
    assert concern is not None

    result = runner.invoke(app, ["curate", "--auto", "--auto-merge"])

    assert result.exit_code == 0, result.stderr
    assert (
        f"openkos curate: Identity: not merged automatically: concepts/foo-2 -> "
        f"concepts/foo -- {concern}." in result.stderr.splitlines()
    )
    assert _present(root, "concepts/foo-2")


# --------------------------------------------------------------------------- #
# failure semantics: later stages still run, the run exits 1 (tasks 3.12-3.13)
# --------------------------------------------------------------------------- #


def _fail_the_second_merge(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """`merge_core` raises `OSError` on its second call. Patched at the module
    the pass calls it through; the returned list proves the patch was read."""
    from openkos.application import lifecycle

    calls: list[str] = []
    real = lifecycle.merge_core

    def _merge_core(*args: object, **kwargs: object) -> object:
        calls.append("merge_core")
        if len(calls) == 2:
            raise OSError("disk full")
        return real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(lifecycle, "merge_core", _merge_core)
    return calls


def _after_stage(ran: list[str]) -> curate.Stage:
    def _run(
        ctx: curate.CurateContext, probe: curate.StageProbe
    ) -> curate.StageOutcome:
        ran.append("Metadata")
        return curate.StageOutcome(status="applied", applied=0)

    return curate.Stage(
        name="Metadata",
        noun="thing",
        probe=lambda ctx: curate.StageProbe(items=(1,), llm_calls=0),
        run=_run,
        needs_llm=False,
        writes=False,
    )


def test_a_mid_run_failure_keeps_later_stages_and_exits_one_at_the_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bar_zed = [_FOO, _BAR, _ZED]
    root, _judge = _build(
        tmp_path,
        monkeypatch,
        bar_zed,
        {g.member_ids: _SAME for g in bar_zed},
    )
    calls = _fail_the_second_merge(monkeypatch)
    ran: list[str] = []
    monkeypatch.setattr(curate, "_STAGES", (*curate._STAGES[:2], _after_stage(ran)))
    refreshed: list[str] = []
    monkeypatch.setattr(
        "openkos.cli.main._refresh_derived_after_write",
        lambda *a, **k: refreshed.append("refresh"),
    )
    before = _commit_count(root)

    result = runner.invoke(app, ["curate", "--auto", "--auto-merge"])

    assert calls == ["merge_core", "merge_core"]  # merge 3 was never attempted
    assert result.exit_code == 1
    assert ran == ["Metadata"]  # a later stage still ran
    assert refreshed == ["refresh"]  # and the refresh ran for what landed
    lines = result.stdout.splitlines()
    assert (
        "Identity: failed -- could not merge concepts/bar-2 into concepts/bar "
        "automatically (OSError: disk full); applied 1, skipped 2." in lines
    )
    assert "Metadata: applied" in lines  # the later stage reported
    assert (
        "openkos curate: Identity: failed while merging concepts/bar-2 into "
        "concepts/bar -- OSError: disk full." in result.stderr.splitlines()
    )
    # Merge 1 is committed; merges 2 (restored) and 3 (never tried) are not.
    assert _commit_count(root) == before + 1
    assert not _present(root, "concepts/foo-2")
    assert _present(root, "concepts/bar-2")
    assert _present(root, "concepts/zed-2")


def test_a_mid_run_failure_on_a_terminal_skips_the_walk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _judge = _build(
        tmp_path,
        monkeypatch,
        [_FOO, _BAR],
        {_FOO.member_ids: _SAME, _BAR.member_ids: _UNSURE},
    )
    # Fail the FIRST merge so merge_core's second-call trigger is moved up.
    from openkos.application import lifecycle

    def _boom(*args: object, **kwargs: object) -> object:
        raise ValueError("bad plan")

    monkeypatch.setattr(lifecycle, "merge_core", _boom)
    reached = _no_prompts(monkeypatch)
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["curate", "--auto", "--auto-merge"])

    assert reached == []  # bar-2 (0.85) would have been prompted by the walk
    assert result.exit_code == 1
    assert "Identity: failed -- could not merge concepts/foo-2 into concepts/foo" in (
        result.stdout
    )
    assert _present(root, "concepts/foo-2")


def test_a_busy_workspace_exits_three_before_anything_is_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openkos import lock

    root, _judge = _build(tmp_path, monkeypatch, [_FOO], {_FOO.member_ids: _SAME})
    entered: list[str] = []

    def _busy() -> object:
        entered.append("section")
        raise lock.WorkspaceBusyError("held by another process")

    monkeypatch.setattr("openkos.cli.main._commit_section_for", lambda root: _busy)
    before = _commit_count(root)

    result = runner.invoke(app, ["curate", "--auto", "--auto-merge"])

    assert "section" in entered
    assert result.exit_code == 3
    assert "refusing to run -- held by another process" in result.stderr
    assert _present(root, "concepts/foo-2")
    assert _commit_count(root) == before


def test_a_member_edited_while_judging_is_skipped_never_a_drift_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _judge = _build(tmp_path, monkeypatch, [_FOO], {})

    def _edit() -> None:
        path = root / "bundle" / "concepts" / "foo-2.md"
        path.write_text(
            path.read_text(encoding="utf-8") + "A hand edit.\n", encoding="utf-8"
        )

    judge = _Judge({_FOO.member_ids: _SAME}, before_returning=_edit)
    monkeypatch.setattr("openkos.cli.curate.adjudicate_candidates", judge)

    result = runner.invoke(app, ["curate", "--auto", "--auto-merge"])

    assert result.exit_code == 0, result.stderr
    assert (
        "openkos curate: Identity: not merged automatically: concepts/foo-2 -> "
        "concepts/foo -- changed since it was judged." in result.stderr.splitlines()
    )
    assert "A hand edit." in (root / "bundle" / "concepts" / "foo-2.md").read_text(
        encoding="utf-8"
    )


# --------------------------------------------------------------------------- #
# the survivor-edit caveat (tasks 3.14-3.15)
# --------------------------------------------------------------------------- #


def _note(survivor: str) -> str:
    return (
        f"  note: {survivor} changed after its automatic merge; "
        f"`openkos unmerge {survivor}` will refuse unless run with "
        "--discard-survivor-edits, which discards that later edit."
    )


def _editing_stage(root: Path, concept_id: str) -> object:
    """A later stage (Metadata's place in the order) that edits one survivor,
    as Structure or Metadata does when it writes a relation or a tier."""

    def _run(
        ctx: curate.CurateContext, probe: curate.StageProbe
    ) -> curate.StageOutcome:
        path = root / "bundle" / f"{concept_id}.md"
        path.write_text(
            path.read_text(encoding="utf-8") + "A later stage wrote this.\n",
            encoding="utf-8",
        )
        return curate.StageOutcome(status="applied", applied=1)

    return curate.Stage(
        name="Metadata",
        noun="thing",
        probe=lambda ctx: curate.StageProbe(items=(1,), llm_calls=0),
        run=_run,
        needs_llm=False,
        writes=False,
    )


def test_a_survivor_a_later_stage_edited_gets_the_caveat_and_an_untouched_one_does_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _judge = _build(
        tmp_path,
        monkeypatch,
        [_FOO, _ZED],
        {_FOO.member_ids: _SAME, _ZED.member_ids: _SAME},
    )
    monkeypatch.setattr(
        curate, "_STAGES", (*curate._STAGES[:2], _editing_stage(root, "concepts/foo"))
    )

    result = runner.invoke(app, ["curate", "--auto", "--auto-merge"])

    assert result.exit_code == 0, result.stderr
    lines = result.stdout.splitlines()
    assert _note("concepts/foo") in lines
    assert [line for line in lines if line.startswith("  note:")] == [
        _note("concepts/foo")
    ]


def test_no_caveat_when_no_later_stage_touched_a_survivor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _judge = _build(tmp_path, monkeypatch, [_FOO], {_FOO.member_ids: _SAME})

    result = runner.invoke(app, ["curate", "--auto", "--auto-merge"])

    assert result.exit_code == 0, result.stderr
    assert not _present(root, "concepts/foo-2")
    assert "  note:" not in result.stdout
