"""Run every harness `--self-test` and fail if any of them is red (#831).

The harness self-tests exist to catch production drifting out from under a
measurement. One had been red since #754 gave the judge a retry
(2026-08-17), a second since #719 renamed a report label (2026-08-15), and
neither was going to be noticed by anything: the elapsed time is beside the
point when nothing was ever going to look. (#831 attributes the first to
#795; the constant it names arrived with #754, and the probe's expectation
was already stale one commit later.) A self-test nobody runs is worse than none: it reads
as a guarantee while guaranteeing nothing, and it does so exactly when a
number from that harness is being trusted.

Three properties do the work here, and each one is a way the previous
arrangement failed:

- **Harnesses are DISCOVERED, never listed.** A checked-in list is the same
  kind of hardcoded expectation this job exists to catch: a new harness
  would be silently unguarded, and nobody would learn that from a green
  run. Anything under `evals/` declaring a `--self-test` flag is run.
- **`OLLAMA_HOST` is poisoned to a closed port.** These self-tests are
  model-free by construction, which is what makes running them in CI cheap
  — but "by construction" is a claim, and this turns it into a check. A
  harness that reaches for a model fails here, loudly and immediately,
  instead of passing on a developer's machine because a server happened to
  be listening.
- **Discovering nothing is a FAILURE.** A rename or a refactor that moves
  the flag would otherwise leave this job reporting green over an empty
  set, which is the precise shape of the rot it is meant to prevent.

Every harness runs even after one fails, for the reason the test matrix
gives for `fail-fast: false`: which ones are red is the information.

Usage:

    uv run python evals/run_self_tests.py
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path

EVALS_ROOT = Path(__file__).resolve().parent
REPO_ROOT = EVALS_ROOT.parent

SELF_TEST_FLAG = "--self-test"
_DECLARATION = re.compile(r"""['"]--self-test['"]""")
"""Discovery marker: the flag as a QUOTED literal, the way an
`add_argument` call spells it. Quoting is what separates a declaration from
prose -- a docstring that merely mentions the flag must not be run as a
harness -- and either quote character counts, so discovery does not quietly
depend on the formatter's string-style preference holding forever."""

UNREACHABLE_OLLAMA = "http://127.0.0.1:1"
"""Port 1 is privileged and unbound, so a connection attempt is refused at
once rather than hanging until a transport deadline. `OllamaClient`
resolves `host` argument > `OLLAMA_HOST` > default, so a harness that
builds a client without an explicit host lands here."""

_FAILURE_CONTEXT_LINES = 20
"""How much of a failing harness's output the report keeps. Enough to carry
a finding printed above its summary line, small enough that one red harness
cannot bury the others."""

PER_HARNESS_TIMEOUT_SECONDS = 120
"""Generous against the ~0.1s these actually take, and small enough that a
harness which starts waiting on something fails the job instead of eating
the workflow's whole budget."""

TOTAL_BUDGET_SECONDS = 300
"""Aggregate ceiling for the whole sweep, and it is not redundant with the
per-harness one: 30 harnesses at 120s each is 3600s against a workflow step
capped at 600s, so a handful of simultaneous hangs would have the job KILLED
by the runner -- with no report at all, which is the one thing this job
exists to produce.

Half the step's ceiling, not most of it: the remaining 300s has to cover
checkout, `setup-uv` and `uv sync --locked` as well, and a budget that only
fits when the setup is fast would fail exactly on the slow runner where a
hang is most likely. The real sweep takes about two seconds, so this is
already three orders of magnitude of headroom. Exhausting this budget stops the sweep and names what
was left unrun, and each harness's own timeout is CLAMPED to what remains,
so the sweep cannot overrun the total by a trailing per-harness hang."""


def discover(root: Path) -> list[Path]:
    """Every harness under `root` that declares a `--self-test` flag,
    sorted so the report reads the same way twice."""
    me = Path(__file__).resolve()
    # By NAME, not by resolved path, and that is not laziness: this module's
    # own source necessarily contains the marker it searches for, so a COPY
    # of it under `evals/` would be discovered, run, and would discover this
    # one in turn. Excluding the identity alone leaves that mutual recursion
    # open.
    #
    # The cost of a name-based rule is that a genuine harness sharing the
    # name would be skipped, so that case RAISES instead of passing quietly:
    # a gate that silently guards less than it appears to is the whole defect
    # #831 is about.
    namesakes = [path for path in root.rglob(me.name) if path.resolve() != me]
    if namesakes:
        raise AssertionError(
            f"{len(namesakes)} file(s) other than the runner are named "
            f"{me.name!r}: {[str(p) for p in namesakes]}. Rename them, or "
            "they will be skipped by the self-exclusion rule."
        )
    found = [
        path
        for path in root.rglob("*.py")
        if path.name != me.name
        and _DECLARATION.search(path.read_text(encoding="utf-8", errors="ignore"))
    ]
    return sorted(found)


def _spawn(path: Path, env: dict[str, str]) -> subprocess.Popen[str]:
    """Start one harness in its own process group."""
    return subprocess.Popen(  # noqa: S603
        # Not untrusted input: the interpreter is this process's own, and the
        # path came from walking `evals/` in the checkout being tested.
        [sys.executable, "-u", str(path.relative_to(REPO_ROOT)), "--self-test"],
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        # A harness that emits a stray non-UTF-8 byte must be REPORTED, not
        # crash the sweep decoding its own diagnostics.
        errors="replace",
        start_new_session=True,
    )


def run_one(path: Path, budget_left: float | None = None) -> tuple[int, float, str]:
    """`(returncode, seconds, trailing output)` for one harness.

    `Popen` rather than `subprocess.run`, for the timeout path alone:
    `run`'s own timeout kills only the direct child, and at least one
    discovered harness starts a server of its own. An orphaned grandchild
    would outlive the job and, if it held a port, fail the NEXT run
    somewhere unrelated. `start_new_session=True` puts the harness in its
    own process group so the whole tree can be killed together.

    Running each harness as a SUBPROCESS at all is deliberate: these modules
    monkeypatch production at import time (`urllib.request.urlopen`,
    module-level prompts), and one leaking into the next would make the
    results depend on discovery order.
    """
    env = dict(os.environ, OLLAMA_HOST=UNREACHABLE_OLLAMA)
    # Clamped to what is LEFT of the sweep's budget, not just the per-harness
    # ceiling. Checking the deadline before starting is not enough: a harness
    # begun one second inside the budget could still run the full ceiling
    # past it, and the total this guards is what keeps the workflow step from
    # being killed with no report at all.
    timeout = float(PER_HARNESS_TIMEOUT_SECONDS)
    if budget_left is not None:
        timeout = max(1.0, min(timeout, budget_left))
    started = time.monotonic()
    try:
        process = _spawn(path, env)
    except OSError as exc:
        # A runner out of file descriptors or memory must report THIS
        # harness as failed and carry on, not abandon the sweep: which
        # harnesses are red is the output, and a traceback here would
        # discard every result already collected.
        return 1, time.monotonic() - started, f"could not start: {exc!r}"
    try:
        output, _ = process.communicate(timeout=timeout)
        code = process.returncode
    except subprocess.TimeoutExpired:
        # Keep what it managed to say. A hang is the failure mode hardest to
        # diagnose from outside, and the lines printed just before it stopped
        # are the whole clue -- replacing them with the word TIMEOUT throws
        # away the only evidence there was.
        before_hang = _kill_tree(process)
        output = f"TIMEOUT after {timeout:.0f}s\n{before_hang}"
        code = 124
    elapsed = time.monotonic() - started
    # The last LINES, not the last line: a harness that fails after printing
    # its per-run table puts the finding above the summary, and one line of
    # context is not enough to act on without re-running it by hand.
    kept = [line for line in (output or "").strip().splitlines() if line.strip()]
    return code, elapsed, "\n    ".join(kept[-_FAILURE_CONTEXT_LINES:])


def _kill_tree(process: subprocess.Popen[str]) -> str:
    """Kill the timed-out harness AND anything it spawned.

    Falls back to killing the process alone if the group is already gone --
    a harness that exits between the timeout firing and this call is a race,
    not an error, and must not turn a reported timeout into a traceback.

    Returns whatever the harness had written before it hung, which the
    reaping `communicate` hands back anyway.
    """
    try:
        group = os.getpgid(process.pid)
    except ProcessLookupError:
        group = None
    # NEVER signal our own group. Without `start_new_session=True` above, the
    # child shares this process's group and `killpg` would take down the
    # runner -- and, in CI, the job reporting the result. That is not
    # hypothetical: removing the kwarg during a mutation check killed the
    # pytest process that was verifying this function.
    if group is not None and group != os.getpgrp():
        with suppress(ProcessLookupError, PermissionError):
            os.killpg(group, signal.SIGKILL)
    process.kill()
    # Reap through `communicate`, not `wait`: it also drains and CLOSES the
    # stdout pipe. `wait` alone leaves the read end open until the object is
    # collected, and this loop runs once per harness.
    with suppress(subprocess.TimeoutExpired, ValueError):
        buffered, _ = process.communicate(timeout=10)
        return buffered or ""
    return ""


def main() -> int:
    harnesses = discover(EVALS_ROOT)
    if not harnesses:
        print(
            f"FAIL: no harness under {EVALS_ROOT.name}/ declares a quoted "
            f"{SELF_TEST_FLAG!r} flag. "
            "Either every self-test was removed, or the flag was renamed and "
            "this job is now guarding nothing.",
            file=sys.stderr,
        )
        return 1

    failures: list[tuple[Path, int, str]] = []
    deadline = time.monotonic() + TOTAL_BUDGET_SECONDS
    unrun: list[Path] = []
    for index, path in enumerate(harnesses):
        if time.monotonic() >= deadline:
            unrun = harnesses[index:]
            break
        code, elapsed, tail = run_one(path, budget_left=deadline - time.monotonic())
        relative = path.relative_to(REPO_ROOT)
        status = "ok  " if code == 0 else "FAIL"
        # Flushed: this job exists to say WHICH harness is red, and a
        # block-buffered pipe loses exactly that if the run is cancelled
        # or the step times out.
        print(f"{status} {elapsed:5.1f}s  {relative}", flush=True)
        if code != 0:
            failures.append((relative, code, tail))

    print(
        f"\n{len(harnesses) - len(unrun)} of {len(harnesses)} harness "
        f"self-test(s) run, {len(failures)} failing.",
        flush=True,
    )
    for relative, code, tail in failures:
        print(f"\n--- {relative} (exit {code})\n    {tail}", file=sys.stderr)
    if unrun:
        # Reported, never silent. A sweep that stopped early and a sweep that
        # passed must not read alike -- an unrun harness is unguarded, and a
        # green line over a short sweep is the rot this job exists to catch.
        print(
            f"\nFAIL: the {TOTAL_BUDGET_SECONDS}s budget ran out with "
            f"{len(unrun)} harness self-test(s) never run: "
            f"{[str(p.relative_to(REPO_ROOT)) for p in unrun]}",
            file=sys.stderr,
        )
    return 1 if failures or unrun else 0


if __name__ == "__main__":
    raise SystemExit(main())
