"""Unit tests for `evals/run_self_tests.py`, the harness self-test gate (#831).

The gate is the thing that keeps every other harness self-test honest, so
leaving it as the one unguarded piece would reproduce the defect it exists
to fix — a guarantee nobody checks. Its three load-bearing properties are
pinned here: a red harness fails the run, discovering nothing fails the
run, and the child really does get an unreachable `OLLAMA_HOST`.

Loaded by path because `evals/` is a directory of standalone scripts, not
an importable package, which is also why the gate runs them as subprocesses
rather than importing them.
"""

import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNNER_PATH = REPO_ROOT / "evals" / "run_self_tests.py"


def _load_runner() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_eval_self_test_runner", RUNNER_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


runner = _load_runner()


def _write_harness(path: Path, body: str) -> None:
    """A minimal harness: declares `--self-test` the way the real ones do,
    so `discover` sees it, then runs `body`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "import argparse, os, sys\n"
        "p = argparse.ArgumentParser()\n"
        'p.add_argument("--self-test", action="store_true")\n'
        "p.parse_args()\n" + body,
        encoding="utf-8",
    )


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the runner at a throwaway evals tree. `run_one` resolves each
    harness relative to `REPO_ROOT`, so both roots move together."""
    evals = tmp_path / "evals"
    evals.mkdir()
    monkeypatch.setattr(runner, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(runner, "EVALS_ROOT", evals)
    return evals


# --- discovery --------------------------------------------------------------


def test_discovery_finds_a_harness_declaring_the_flag(sandbox: Path) -> None:
    _write_harness(sandbox / "probe" / "run_probe.py", 'print("ok")\n')

    assert [p.name for p in runner.discover(sandbox)] == ["run_probe.py"]


def test_discovery_ignores_a_file_that_only_mentions_the_flag(sandbox: Path) -> None:
    """The marker is the quoted literal an `add_argument` call carries, so
    a README-ish module talking ABOUT --self-test is not run as one."""
    (sandbox / "notes.py").write_text(
        '"""This module explains how --self-test works."""\n', encoding="utf-8"
    )

    assert runner.discover(sandbox) == []


def test_discovery_never_includes_the_runner_itself() -> None:
    """Against the real tree, because that is where the hazard is.

    This module's source necessarily contains the marker it searches for --
    it has to, in order to search -- so without the exclusion the gate would
    discover itself, run itself, and recurse.
    """
    found = runner.discover(runner.EVALS_ROOT)

    assert RUNNER_PATH.resolve() not in {path.resolve() for path in found}


def test_discovery_accepts_either_quote_character(sandbox: Path) -> None:
    """Discovery must not depend on the formatter's string-style preference
    holding forever. A harness declared with single quotes is a harness."""
    path = sandbox / "probe" / "run_probe.py"
    _write_harness(path, 'print("ok")\n')
    path.write_text(
        path.read_text(encoding="utf-8").replace('"--self-test"', "'--self-test'"),
        encoding="utf-8",
    )

    assert [p.name for p in runner.discover(sandbox)] == ["run_probe.py"]


def test_discovery_finds_the_real_harnesses() -> None:
    """Against the REAL tree, not a synthetic one.

    Everything else here proves the runner does the right thing with
    harnesses this file wrote. None of it proves the marker matches how the
    shipped harnesses actually spell the flag -- and a marker that matched
    only a subset would leave the rest unguarded while the job still
    reported green over the ones it found. The two named below are the two
    that were red when #831 was filed.

    Takes no `sandbox`: that fixture repoints the runner's roots at a
    throwaway tree, which is the opposite of what this asserts.
    """
    found = {
        path.relative_to(runner.REPO_ROOT).as_posix()
        for path in runner.discover(runner.EVALS_ROOT)
    }

    assert "evals/generation_ceiling/run_generation_ceiling_probe.py" in found
    assert "evals/decision_extraction/scripts/run_type_coverage.py" in found
    assert len(found) >= 25, f"only {len(found)} harness(es) discovered"


def test_entry_point_discovery_accepts_either_quote_character(sandbox: Path) -> None:
    """The regression pin for #928's second corroborated finding: `discover`
    above tolerates either quote character on `--self-test`, but
    `_ENTRY_POINT` was holding files to the OTHER standard -- double quotes
    only, justified by a census of the tree that will not stay true forever.
    A file spelling its guard with single quotes was silently invisible to
    `discover_entry_points`, which means `check_coverage` could never flag
    it as missing a self-test: the exact "unguarded and nobody notices"
    failure #928 exists to close, one layer down from where #928 found it."""
    path = sandbox / "p" / "run_p.py"
    path.parent.mkdir(parents=True)
    path.write_text("if __name__ == '__main__':\n    pass\n", encoding="utf-8")

    assert [p.name for p in runner.discover_entry_points(sandbox)] == ["run_p.py"]


def test_entry_point_discovery_tolerates_whitespace_variation(sandbox: Path) -> None:
    """Same reasoning as the quote tolerance above, extended to spacing:
    `ruff format` enforces `if __name__ == "__main__":` today, but this
    check must not quietly depend on that holding forever any more than it
    depends on the quote style holding."""
    path = sandbox / "p" / "run_p.py"
    path.parent.mkdir(parents=True)
    path.write_text('if __name__=="__main__":\n    pass\n', encoding="utf-8")

    assert [p.name for p in runner.discover_entry_points(sandbox)] == ["run_p.py"]


def test_entry_point_discovery_finds_the_real_population() -> None:
    """Against the REAL tree, mirroring `test_discovery_finds_the_real_
    harnesses`'s reasoning: a synthetic-only test proves the runner handles
    files IT wrote, never that the marker matches how the shipped entry
    points actually spell their guard. Takes no `sandbox` for the same
    reason that test does.
    """
    found = {
        path.relative_to(runner.REPO_ROOT).as_posix()
        for path in runner.discover_entry_points(runner.EVALS_ROOT)
    }

    assert "evals/generation_ceiling/run_generation_ceiling_probe.py" in found
    assert "evals/decision_extraction/scripts/run_type_coverage.py" in found
    assert len(found) >= 25, f"only {len(found)} entry point(s) discovered"


def test_discovery_is_sorted(sandbox: Path) -> None:
    """So a failure list reads the same way twice."""
    for name in ("zulu", "alpha", "mike"):
        _write_harness(sandbox / name / f"run_{name}.py", 'print("ok")\n')

    assert [p.parent.name for p in runner.discover(sandbox)] == [
        "alpha",
        "mike",
        "zulu",
    ]


# --- the coverage guard ------------------------------------------------------


def test_coverage_fails_for_an_unexempted_entry_point(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guard #928 was filed over: a `__main__` entry point that declares
    no `--self-test` and is not named in `EXEMPTIONS` was simply invisible to
    `discover` -- the sweep read every declared harness as green while this
    file sat outside the count entirely. `check_coverage` must name it, not
    just notice something is short.

    `EXEMPTIONS` is cleared first: the real roster is a module-level global
    unrelated to the sandbox, and leaving it in place would only ever add
    false accounting for production files that do not exist under `sandbox`
    -- it would not hide THIS finding, but a cleared roster keeps the
    assertion about exactly what it claims.
    """
    monkeypatch.setattr(runner, "EXEMPTIONS", {})
    path = sandbox / "orphan" / "run_orphan.py"
    path.parent.mkdir(parents=True)
    path.write_text('if __name__ == "__main__":\n    pass\n', encoding="utf-8")

    problems = runner.check_coverage(sandbox)

    assert any("run_orphan.py" in problem for problem in problems)


def test_coverage_fails_for_a_stale_exemption(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An exemption naming a file that no longer exists is the same rot in
    reverse: a roster nobody prunes reads as accounted-for while proving
    nothing about the code that actually ships. `_EXEMPTIONS_ROOT` is frozen
    at import time to the real `evals/` root, not the sandbox `EVALS_ROOT`
    the `sandbox` fixture repoints -- see its module docstring -- so it has
    to be monkeypatched here too, or every relative name would resolve
    against the real tree instead of this test's throwaway one.
    """
    monkeypatch.setattr(runner, "_EXEMPTIONS_ROOT", sandbox)
    monkeypatch.setattr(runner, "EXEMPTIONS", {"gone/run_gone.py": "no longer exists"})

    problems = runner.check_coverage(sandbox)

    assert any("gone/run_gone.py" in problem for problem in problems)


def test_coverage_passes_a_properly_exempted_file(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other half of the roster: a file that IS named, WITH a reason,
    and DOES exist must not be reported. Without this, a fix that made
    `check_coverage` fail on every exemption -- accidentally exempting
    nothing -- would pass the two tests above and still be wrong."""
    monkeypatch.setattr(runner, "_EXEMPTIONS_ROOT", sandbox)
    path = sandbox / "builder" / "build_sources.py"
    path.parent.mkdir(parents=True)
    path.write_text('if __name__ == "__main__":\n    pass\n', encoding="utf-8")
    monkeypatch.setattr(
        runner,
        "EXEMPTIONS",
        {"builder/build_sources.py": "fixture builder, not a measurement harness"},
    )

    assert runner.check_coverage(sandbox) == []


# --- the verdict ------------------------------------------------------------


def test_a_green_sweep_passes(sandbox: Path) -> None:
    _write_harness(sandbox / "a" / "run_a.py", 'print("fine")\n')
    _write_harness(sandbox / "b" / "run_b.py", 'print("fine")\n')

    assert runner.main() == 0


def test_one_red_harness_fails_the_run(sandbox: Path) -> None:
    """The whole point. A harness self-test that exits non-zero must take
    the job down with it, and the other harnesses must still run -- which
    ones are red is the information (the test matrix's `fail-fast: false`
    reasoning)."""
    _write_harness(sandbox / "a" / "run_a.py", 'print("fine")\n')
    _write_harness(sandbox / "b" / "run_b.py", "sys.exit(1)\n")
    _write_harness(sandbox / "c" / "run_c.py", 'print("also fine")\n')

    assert runner.main() == 1


def test_discovering_nothing_fails_the_run(sandbox: Path) -> None:
    """A rename or a refactor that moves the flag would otherwise leave the
    job green over an empty set -- the precise rot it exists to prevent."""
    assert runner.main() == 1


def test_discovering_nothing_says_why(
    sandbox: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An empty sweep and a clean sweep must not read alike."""
    runner.main()

    assert "guarding nothing" in capsys.readouterr().err


# --- the poisoned host ------------------------------------------------------


def test_the_child_gets_an_unreachable_ollama_host(sandbox: Path) -> None:
    """ "Model-free by construction" is a claim; this is the check.

    The harness asserts the value itself and exits non-zero if it is
    missing or reachable, so the assertion lives where the guarantee is
    consumed rather than in a mock of it.
    """
    _write_harness(
        sandbox / "p" / "run_p.py",
        f'sys.exit(0 if os.environ.get("OLLAMA_HOST") == '
        f"{runner.UNREACHABLE_OLLAMA!r} else 1)\n",
    )

    code, _elapsed, _tail = runner.run_one(sandbox / "p" / "run_p.py")

    assert code == 0


def test_a_harness_reaching_a_model_is_refused_fast(sandbox: Path) -> None:
    """Poisoning to a closed port must REFUSE rather than hang: a harness
    that reaches for a model has to fail the job quickly, not sit on a
    transport deadline until the per-harness timeout."""
    _write_harness(
        sandbox / "p" / "run_p.py",
        "import urllib.request\n"
        'urllib.request.urlopen(os.environ["OLLAMA_HOST"], timeout=5)\n',
    )

    code, elapsed, _tail = runner.run_one(sandbox / "p" / "run_p.py")

    assert code != 0
    assert elapsed < runner.PER_HARNESS_TIMEOUT_SECONDS


# --- the timeout path -------------------------------------------------------


def test_a_hanging_harness_times_out_rather_than_blocking(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """And it is reported as a timeout, not as a pass."""
    monkeypatch.setattr(runner, "PER_HARNESS_TIMEOUT_SECONDS", 1)
    _write_harness(sandbox / "p" / "run_p.py", "import time\ntime.sleep(30)\n")

    code, _elapsed, tail = runner.run_one(sandbox / "p" / "run_p.py")

    assert code == 124
    assert "TIMEOUT" in tail


def test_a_timeout_keeps_what_the_harness_said_before_hanging(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hang is the failure hardest to diagnose from outside, and the lines
    printed just before it stopped are the whole clue. Reporting only the
    word TIMEOUT throws away the only evidence there was."""
    monkeypatch.setattr(runner, "PER_HARNESS_TIMEOUT_SECONDS", 3)
    _write_harness(
        sandbox / "p" / "run_p.py",
        'print("checking the thing that hangs", flush=True)\n'
        "import time\ntime.sleep(120)\n",
    )

    code, _elapsed, tail = runner.run_one(sandbox / "p" / "run_p.py")

    assert code == 124
    assert "checking the thing that hangs" in tail


def test_undecodable_output_is_reported_not_crashed(sandbox: Path) -> None:
    """A harness emitting a stray non-UTF-8 byte must fail as a harness, not
    take the sweep down while decoding its own diagnostics."""
    _write_harness(
        sandbox / "p" / "run_p.py",
        "sys.stdout.buffer.write(b'oops \\xff\\n')\nsys.stdout.flush()\nsys.exit(1)\n",
    )

    code, _elapsed, tail = runner.run_one(sandbox / "p" / "run_p.py")

    assert code == 1
    assert "oops" in tail


def test_a_timeout_kills_what_the_harness_spawned(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reason the child leads its own process group.

    At least one real harness starts a server of its own. Killing only the
    direct child would leave that grandchild running past the job, holding
    whatever port it opened, and fail some later run somewhere unrelated.
    """
    import os
    import time
    from contextlib import suppress

    # Five seconds, not one: the harness has to be scheduled and write its
    # pidfile before the timeout fires, and a loaded runner is exactly where
    # a one-second budget turns a real assertion into a flake.
    monkeypatch.setattr(runner, "PER_HARNESS_TIMEOUT_SECONDS", 5)
    pidfile = sandbox / "grandchild.pid"
    _write_harness(
        sandbox / "p" / "run_p.py",
        "import subprocess, sys, time, pathlib\n"
        "kid = subprocess.Popen([sys.executable, '-c', 'import time;"
        " time.sleep(120)'])\n"
        f"pathlib.Path({str(pidfile)!r}).write_text(str(kid.pid))\n"
        "time.sleep(120)\n",
    )

    code, _elapsed, _tail = runner.run_one(sandbox / "p" / "run_p.py")

    assert code == 124
    assert pidfile.exists(), "the harness never got far enough to spawn a child"
    grandchild = int(pidfile.read_text())
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            os.kill(grandchild, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:  # pragma: no cover - only on a regression
        import signal

        with suppress(ProcessLookupError):
            os.kill(grandchild, signal.SIGKILL)
        pytest.fail(f"grandchild {grandchild} outlived the timed-out harness")


def test_a_failure_keeps_more_than_its_last_line(sandbox: Path) -> None:
    """A harness that fails after printing its table puts the finding ABOVE
    the summary. Keeping one line would report the summary and drop the
    finding, which is the half a reader needs."""
    _write_harness(
        sandbox / "p" / "run_p.py",
        'print("FINDING: the thing that actually broke")\n'
        'print("summary: 1 failing")\n'
        "sys.exit(1)\n",
    )

    _code, _elapsed, tail = runner.run_one(sandbox / "p" / "run_p.py")

    assert "FINDING: the thing that actually broke" in tail
    assert "summary: 1 failing" in tail


def test_a_harness_that_cannot_start_is_reported_not_raised(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A runner out of descriptors must fail THAT harness and carry on. A
    traceback here would discard every result already collected, which is
    the output the job exists to produce."""
    _write_harness(sandbox / "a" / "run_a.py", 'print("fine")\n')
    _write_harness(sandbox / "b" / "run_b.py", 'print("fine")\n')

    def _refuse(path: Path, env: dict[str, str]) -> object:
        if path.parent.name == "a":
            raise OSError(24, "Too many open files")
        return original(path, env)

    original = runner._spawn
    monkeypatch.setattr(runner, "_spawn", _refuse)

    assert runner.main() == 1


def test_a_namesake_harness_raises_instead_of_being_skipped(sandbox: Path) -> None:
    """Self-exclusion is by NAME, because this module's source necessarily
    contains the marker it searches for and a copy would recurse. The cost
    is that a genuine harness sharing the name would be skipped -- so it is
    refused loudly instead. A gate that silently guards less than it appears
    to is the defect #831 is about."""
    _write_harness(sandbox / "elsewhere" / "run_self_tests.py", 'print("ok")\n')

    with pytest.raises(AssertionError, match=re.escape("named 'run_self_tests.py'")):
        runner.discover(sandbox)


def test_the_killer_never_signals_its_own_process_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The guard that makes the previous test safe to have.

    Without `start_new_session=True` a child shares this process's group,
    and `killpg` on it takes down the runner -- in CI, the job reporting the
    result. Removing that kwarg during a mutation check killed the pytest
    process verifying this very function, so the refusal is pinned rather
    than left to the kwarg alone.
    """
    import os
    import subprocess

    signalled: list[int] = []
    monkeypatch.setattr(runner.os, "killpg", lambda pgid, sig: signalled.append(pgid))
    # No `start_new_session`, so this child sits in OUR group on purpose.
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert os.getpgid(process.pid) == os.getpgrp(), "fixture must share our group"

        runner._kill_tree(process)

        assert signalled == [], "the runner must not signal the group it lives in"
        assert process.poll() is not None, "the child itself must still be killed"
    finally:
        process.kill()
        process.wait(timeout=10)


def test_an_exhausted_budget_fails_and_names_what_it_skipped(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The per-harness ceiling alone does not bound the sweep: 30 harnesses
    at 120s each is 3600s against a workflow step capped at 600s, so a few
    simultaneous hangs would have the job KILLED with no report -- the one
    thing it exists to produce. Running out of budget must therefore fail
    LOUDLY and name the harnesses left unguarded, never read as a pass."""
    monkeypatch.setattr(runner, "TOTAL_BUDGET_SECONDS", 0)
    _write_harness(sandbox / "a" / "run_a.py", 'print("fine")\n')
    _write_harness(sandbox / "b" / "run_b.py", 'print("fine")\n')

    code = runner.main()

    captured = capsys.readouterr()
    assert code == 1
    assert "never run" in captured.err
    assert "run_a.py" in captured.err
    assert "0 of 2" in captured.out


def test_a_harness_timeout_is_clamped_to_the_remaining_budget(sandbox: Path) -> None:
    """Checking the deadline before starting is not enough on its own.

    A harness begun one second inside the budget could still run the full
    per-harness ceiling past it, and the total is what keeps the workflow
    step from being killed with no report at all. Each harness's timeout is
    therefore clamped to what is left.
    """
    _write_harness(sandbox / "p" / "run_p.py", "import time\ntime.sleep(120)\n")

    code, elapsed, tail = runner.run_one(sandbox / "p" / "run_p.py", budget_left=1.5)

    assert code == 124
    assert "TIMEOUT after 2s" in tail
    assert elapsed < runner.PER_HARNESS_TIMEOUT_SECONDS
