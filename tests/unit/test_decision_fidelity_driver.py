"""`evals/decision_fidelity/run_decision_fidelity.py`: the run driver (#1649).

Model-free: a fake backend stands in for Ollama, the clock is injected, and
every check is on what the driver stores, resumes and refuses.
"""

import importlib.util
import json
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DIR = _REPO_ROOT / "evals" / "decision_fidelity"
sys.path.insert(0, str(_DIR))


def _load(name: str, filename: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, _DIR / filename)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


core = _load("fidelity_core", "fidelity_core.py")
driver = _load("run_decision_fidelity", "run_decision_fidelity.py")

BASE = "intro\n- subject: s\n- value: one complete sentence.\n- evidence: e\noutro"
CLAUSE = "- value: one complete sentence. Keep the figure."
MEETINGS = ("ES2015a", "ES2015b")


def _transcript(meeting: str) -> str:
    turns = "".join(
        f"{'PM' if n % 2 == 0 else 'ID'}: Turn number {n} of the meeting today.\n\n"
        for n in range(12)
    )
    return (
        f"---\nmeeting_id: {meeting}\n---\n\n{turns}"
        "PM: Alright, so we'll go with rubber for the casing then.\n"
    )


TEXTS = {m: _transcript(m) for m in MEETINGS}


class FakeLLM:
    """Answers every window with one verbatim-evidence Decision."""

    def __init__(self) -> None:
        self.systems: list[str] = []
        self.calls = 0
        self.fail_with: Exception | None = None

    def chat(self, messages: Sequence[Any]) -> str:
        self.calls += 1
        if self.fail_with is not None:
            raise self.fail_with
        self.systems.append(messages[0]["content"])
        return json.dumps(
            [
                {
                    "subject": "casing",
                    "value": "Use rubber for the casing",
                    "evidence": "Alright, so we'll go with rubber for the casing then.",
                }
            ]
        )


def _stamp(arm: str, prompt: str, digest: str = "d1") -> dict[str, Any]:
    return {
        "stamp_version": 1,
        "harness": {"commit": "c" * 40, "dirty": False},
        "model": {"name": "qwen3:8b", "digest": digest},
        "prompts": [
            {"id": f"extraction/decision_pass/{arm}", "sha256_16": "0123456789abcdef"}
        ],
    }


class Clock:
    """Advances `step` seconds per reading."""

    def __init__(self, step: float = 1.0) -> None:
        self.now = 0.0
        self.step = step

    def __call__(self) -> float:
        self.now += self.step
        return self.now


def _drive(
    tmp_path: Path,
    llm: FakeLLM,
    *,
    runs: int = 2,
    budget_s: float = 10_000.0,
    clock: Callable[[], float] | None = None,
    stamp_fn: Callable[[str, str], dict[str, Any]] = _stamp,
    texts: dict[str, str] | None = None,
) -> Any:
    return driver.drive(
        texts=TEXTS if texts is None else texts,
        out_dir=tmp_path / "out",
        log_dir=tmp_path / "logs",
        model="qwen3:8b",
        runs=runs,
        llm_factory=lambda: llm,
        stamp_fn=stamp_fn,
        pin={"files": {"a": "1"}, "registration_commit": "r", "unchanged": True},
        clock=clock or Clock(),
        budget_s=budget_s,
        base_prompt=BASE,
        clause=CLAUSE,
        log=lambda _line: None,
    )


# --- the treatment prompt ----------------------------------------------------


def test_the_treated_prompt_replaces_exactly_the_value_bullet() -> None:
    treated = driver.treated_prompt(BASE, CLAUSE)
    assert CLAUSE in treated
    assert "- value: one complete sentence.\n" not in treated
    assert treated.splitlines()[1:2] == ["- subject: s"]
    assert treated.count("- value:") == 1


@pytest.mark.parametrize("base", ["no bullet here", "- value: a\n- value: b"])
def test_the_treated_prompt_refuses_anything_but_one_bullet(base: str) -> None:
    with pytest.raises(ValueError, match="exactly one"):
        driver.treated_prompt(base, CLAUSE)


def test_the_shipped_prompt_and_clause_combine() -> None:
    shipped = (
        _REPO_ROOT / "src" / "openkos" / "prompts" / "extraction" / "decision_pass.md"
    ).read_text(encoding="utf-8")
    clause = (_DIR / "prompt_value_clause.md").read_text(encoding="utf-8").strip()
    treated = driver.treated_prompt(shipped, clause)
    assert clause in treated
    assert len(treated.splitlines()) == len(shipped.splitlines())


# --- the schedule ------------------------------------------------------------


def test_runs_are_interleaved_so_drift_hits_both_arms() -> None:
    assert driver.schedule(("A0", "A1"), 2) == [
        (1, "A0"),
        (1, "A1"),
        (2, "A0"),
        (2, "A1"),
    ]


# --- driving, storing, assembling ---------------------------------------------


def test_a_full_drive_stores_both_arms_in_the_shape_the_scorer_reads(
    tmp_path: Path,
) -> None:
    llm = FakeLLM()
    result = _drive(tmp_path, llm)
    assert result.status == "complete"
    for arm in ("A0", "A1"):
        doc = json.loads((tmp_path / "out" / f"runs-{arm}.json").read_text())
        assert doc["partial"] is False
        assert doc["stamp"]["model"]["digest"] == "d1"
        runs = core.load_runs(doc)
        assert len(runs) == 2
        assert set(runs[0]) == set(MEETINGS)
        assert runs[0]["ES2015a"][0]["title"] == "Use rubber for the casing"


def test_the_prompt_arm_sends_the_clause_and_the_baseline_does_not(
    tmp_path: Path,
) -> None:
    llm = FakeLLM()
    _drive(tmp_path, llm, runs=1)
    assert set(llm.systems) == {BASE, driver.treated_prompt(BASE, CLAUSE)}
    # interleaved: baseline first, then prompt, per run
    assert llm.systems[0] == BASE
    assert llm.systems[len(MEETINGS)] == driver.treated_prompt(BASE, CLAUSE)


def test_the_module_prompt_is_restored_after_the_prompt_arm(tmp_path: Path) -> None:
    before = driver.decision_pass._SYSTEM_PROMPT
    _drive(tmp_path, FakeLLM(), runs=1)
    assert before == driver.decision_pass._SYSTEM_PROMPT


def test_a_cell_records_failures_and_timing(tmp_path: Path) -> None:
    _drive(tmp_path, FakeLLM(), runs=1)
    cell = json.loads(
        (tmp_path / "out" / "cells" / "A0-r01" / "ES2015a.json").read_text()
    )
    assert cell["windows"] == 1
    assert cell["calls"] == 1
    assert cell["failure"] is None
    assert cell["wall_s"] > 0
    assert len(cell["call_latencies_s"]) == 1


# --- budget and resume --------------------------------------------------------


def test_the_budget_stops_the_drive_and_marks_the_arms_partial(tmp_path: Path) -> None:
    llm = FakeLLM()
    result = _drive(tmp_path, llm, runs=3, budget_s=5.0, clock=Clock(step=1.0))
    assert result.status == "budget"
    doc = json.loads((tmp_path / "out" / "runs-A0.json").read_text())
    assert doc["partial"] is True
    assert "NOT THE PRE-REGISTERED RESULT" in doc["partial_notice"]
    assert doc["runs_complete"] < 3


def test_resume_runs_only_the_missing_cells_and_matches_an_uninterrupted_drive(
    tmp_path: Path,
) -> None:
    first = FakeLLM()
    stopped = _drive(tmp_path, first, runs=2, budget_s=5.0)
    assert stopped.status == "budget"
    second = FakeLLM()
    resumed = _drive(tmp_path, second, runs=2)
    assert resumed.status == "complete"
    uninterrupted = FakeLLM()
    other = tmp_path / "other"
    other.mkdir()
    _drive(other, uninterrupted, runs=2)
    assert first.calls + second.calls == uninterrupted.calls
    for arm in ("A0", "A1"):
        mine = core.load_runs(
            json.loads((tmp_path / "out" / f"runs-{arm}.json").read_text())
        )
        theirs = core.load_runs(
            json.loads((other / "out" / f"runs-{arm}.json").read_text())
        )
        assert mine == theirs


def test_the_budget_counts_time_spent_before_a_resume(tmp_path: Path) -> None:
    _drive(tmp_path, FakeLLM(), runs=2, budget_s=5.0)
    again = _drive(tmp_path, FakeLLM(), runs=2, budget_s=5.0)
    assert again.status == "budget"
    assert again.cells_run == 0


def test_a_resume_refuses_a_model_that_moved_under_the_run(tmp_path: Path) -> None:
    _drive(tmp_path, FakeLLM(), runs=2, budget_s=5.0)
    with pytest.raises(driver.DriftError, match="digest"):
        _drive(
            tmp_path,
            FakeLLM(),
            runs=2,
            stamp_fn=lambda arm, prompt: _stamp(arm, prompt, digest="moved"),
        )


def test_an_unpinned_stamp_is_refused_before_any_call(tmp_path: Path) -> None:
    llm = FakeLLM()

    def bad(arm: str, prompt: str) -> dict[str, Any]:
        stamp = _stamp(arm, prompt)
        stamp["model"]["digest"] = None
        return stamp

    with pytest.raises(driver.StampError):
        _drive(tmp_path, llm, stamp_fn=bad)
    assert llm.calls == 0


def test_an_unreachable_backend_aborts_instead_of_storing_empty_cells(
    tmp_path: Path,
) -> None:
    from openkos.llm.base import BackendUnavailable

    llm = FakeLLM()
    llm.fail_with = BackendUnavailable("down")
    with pytest.raises(driver.BackendDown):
        _drive(tmp_path, llm)
    assert not list((tmp_path / "out").glob("cells/*/*.json"))


# --- eligibility and configuration ----------------------------------------------


@pytest.mark.parametrize("meeting", ["TS3012a", "TS3005a", "ES2002a", "IS1002b"])
def test_a_meeting_outside_the_development_pool_is_refused(
    tmp_path: Path, meeting: str
) -> None:
    llm = FakeLLM()
    with pytest.raises(core.MeetingRefused):
        _drive(tmp_path, llm, texts={meeting: _transcript(meeting)})
    assert llm.calls == 0


def test_a_text_the_production_gate_does_not_see_as_a_meeting_is_refused(
    tmp_path: Path,
) -> None:
    llm = FakeLLM()
    with pytest.raises(SystemExit, match="meeting-shaped"):
        _drive(tmp_path, llm, texts={"ES2015a": "just one paragraph of prose."})
    assert llm.calls == 0


def test_the_registered_configuration_is_what_production_ships() -> None:
    assert driver.frozen_config_problems() == []


def test_a_drifted_production_default_is_named(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(driver, "REGISTERED_CONTEXT_WINDOW", 1)
    assert any("context" in p for p in driver.frozen_config_problems())


def test_results_may_not_be_written_inside_the_repository() -> None:
    with pytest.raises(SystemExit, match="outside the repository"):
        driver.require_outside_repo(_REPO_ROOT / "evals" / "x")
    driver.require_outside_repo(Path.home() / "openkos-eval-runs" / "x")


def test_the_pin_report_hashes_the_files_and_asks_git_about_the_registration_commit() -> (
    None
):
    calls: list[list[str]] = []

    def runner(cmd: list[str], **_kw: Any) -> Any:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    pin = driver.pin_report(_REPO_ROOT, runner=runner)
    assert set(pin["files"]) == set(driver.PINNED_FILES)
    assert pin["unchanged"] is True
    assert pin["registration_commit"] == driver.REGISTRATION_COMMIT
    assert driver.REGISTRATION_COMMIT in calls[0]


def test_the_pin_report_flags_a_changed_file() -> None:
    def runner(cmd: list[str], **_kw: Any) -> Any:
        return subprocess.CompletedProcess(cmd, 1, "", "")

    assert driver.pin_report(_REPO_ROOT, runner=runner)["unchanged"] is False


def test_waiting_for_the_gpu_polls_until_no_model_is_loaded() -> None:
    answers = iter(
        [{"models": [{"name": "x"}]}, {"models": [{"name": "x"}]}, {"models": []}]
    )
    slept: list[float] = []
    driver.wait_for_gpu(
        lambda: next(answers), sleep=slept.append, log=lambda _l: None, poll_s=7.0
    )
    assert slept == [7.0, 7.0]


def test_the_self_test_passes_and_needs_no_model() -> None:
    done = subprocess.run(  # noqa: S603 -- our own interpreter and script
        [sys.executable, str(_DIR / "run_decision_fidelity.py"), "--self-test"],
        capture_output=True,
        text=True,
        check=False,
        env={"OLLAMA_HOST": "127.0.0.1:1", "PATH": "/usr/bin:/bin"},
        timeout=120,
    )
    assert done.returncode == 0, done.stderr
    assert "self-test: OK" in done.stdout


# --- the pinned data -------------------------------------------------------------


def _pins(texts: dict[str, str]) -> dict[str, str]:
    import hashlib

    return {m: hashlib.sha256(t.encode("utf-8")).hexdigest() for m, t in texts.items()}


def test_a_transcript_that_is_not_the_pinned_bytes_is_named() -> None:
    pins = _pins(TEXTS)
    changed = {**TEXTS, "ES2015a": TEXTS["ES2015a"] + "x"}
    assert driver.pin_problems(changed, pins) == [
        "ES2015a: sha256 differs from bundle_sources.json"
    ]
    assert driver.pin_problems(TEXTS, pins) == []


def test_a_meeting_with_no_pin_is_a_problem() -> None:
    assert driver.pin_problems(TEXTS, {}) == [
        "ES2015a: no sha256 pin",
        "ES2015b: no sha256 pin",
    ]


def test_the_registered_pins_load_from_the_repository() -> None:
    pins = driver.load_transcript_pins()
    assert set(core.GOLD_MEETINGS) <= set(pins)


def test_a_window_count_off_the_registered_figure_is_a_note_not_a_refusal() -> None:
    full = {m: _transcript(m) for m in core.GOLD_MEETINGS}
    problems, notes = driver.preflight(full, _pins(full))
    assert problems == []
    assert any(str(driver.REGISTERED_WINDOWS) in n for n in notes)
