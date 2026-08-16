"""`evals/edge_typing/` must not report a stated-confidence metric, and
`evals/contradictions/` must keep reporting one (issue #740).

The two harnesses look alike and their reports sat one directory apart, but
only one of those columns was ever able to carry a number.

`resolution.edge_typing.EdgeSuggestion` has no `confidence` field, and the
#508 investigation concluded it should not gain one. The runner reads the
value through `getattr(s, "confidence", 0.0)` on purpose -- a seam so that an
arm re-adding the field can be measured without touching the runner -- which
means that on every arm actually run, the value is the default. All NINETEEN
stored `runs-*.json` back to 2026-08-09 hold `confidences` arrays that are
entirely `0.0`, and every report beside them printed `mean stated confidence
0.00` for CORRECT answers and `0.00` for WRONG ones.

Nineteen, counted rather than quoted: #740's own text says twelve, which was
already an undercount when it was filed. Every number in this module is the
measured one, because restating a figure nobody re-derived is the failure
this repository keeps paying for.

That reads exactly like a finding: a model uniformly unconfident, or a
threshold policy shown to be worthless. It is neither. It is a column that
could not vary. The sibling `evals/contradictions/` harness draws a real
conclusion from its own confidence column -- wrong verdicts are the MOST
confident, which is why `CONTRADICTS && confidence >= 0.7` needed #558 -- so
a reader has every reason to carry that meaning across. A measurement that
cannot vary cannot support a conclusion.

TWO GUARDS, NOT ONE. Removing the dead column is a grep away from removing
the live one, and the live one is load-bearing: it feeds the high-confidence
gate this product ships. So this pins the absence AND the presence. Deleting
either half alone leaves the other assertion green.
"""

import json
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]

_EDGE_RESULTS = _REPO_ROOT / "evals" / "edge_typing" / "results"
_EDGE_RUNNER = _REPO_ROOT / "evals" / "edge_typing" / "run_edge_typing_eval.py"
_CONTRADICTIONS_RUNNER = (
    _REPO_ROOT / "evals" / "contradictions" / "run_contradictions_eval.py"
)

_DEAD_COLUMN = "mean stated confidence"
"""The exact row label `evals/edge_typing/` printed until #740.

Matched as the shared prefix of both rows (`, CORRECT answers` and `, WRONG
answers`) so that reviving either one alone still fails."""

_LIVE_COLUMN = "mean confidence, CORRECT verdicts"
"""`evals/contradictions/`'s row, which measures a real reply field."""


def test_the_stored_edge_typing_reports_carry_no_stated_confidence_row() -> None:
    """The artifacts a person actually opens are clean.

    Fixing the runner only stops FUTURE reports from printing the number;
    the nineteen already on disk are what someone reads tomorrow. The
    `runs-*.json` beside them are deliberately left untouched: the JSON is
    the evidence and still records the all-zero arrays, while the `.md` is
    a render of a field that was never collected."""
    reports = sorted(_EDGE_RESULTS.glob("edge-typing-*.md"))
    # Guard the guard: an empty (or renamed) results directory would make
    # every assertion below pass without inspecting anything.
    assert len(reports) >= 19, (
        f"expected the stored edge-typing reports, found {len(reports)} in "
        f"{_EDGE_RESULTS}"
    )

    offenders = [
        report.name
        for report in reports
        if _DEAD_COLUMN in report.read_text(encoding="utf-8")
    ]
    assert offenders == [], (
        f"{len(offenders)} of {len(reports)} stored reports still print a "
        f"'{_DEAD_COLUMN}' row, which no arm has ever been able to populate: "
        f"{offenders}"
    )


def test_the_edge_typing_runner_no_longer_builds_a_stated_confidence_row() -> None:
    """The source, not just its output.

    The stored-artifact guard above only sees a regression AFTER someone
    pays for a run, because a re-added row reaches disk one measurement
    later. This one fails on the commit that re-adds it."""
    source = _EDGE_RUNNER.read_text(encoding="utf-8")
    assert _DEAD_COLUMN not in source, (
        f"{_EDGE_RUNNER.name} builds a '{_DEAD_COLUMN}' row again; "
        "`EdgeSuggestion` still carries no `confidence`, so the row can only "
        "print a structural 0.00"
    )


def test_the_edge_typing_runner_aggregates_no_confidence_at_all() -> None:
    """Bound to the MECHANISM, not to one row label.

    The guard above forbids the exact string the removed rows carried, which
    a revival under any other name walks straight past -- and the defect was
    never the wording. It was reporting an aggregate over a per-edge value
    that is the `getattr` default on every arm. These are the two
    accumulators that computed it; their absence is what makes the label
    unreachable."""
    source = _EDGE_RUNNER.read_text(encoding="utf-8")
    for accumulator in ("right_confidences", "wrong_confidences"):
        assert accumulator not in source, (
            f"{_EDGE_RUNNER.name} accumulates `{accumulator}` again -- the "
            "removed rows are back under some name, since nothing else in "
            "this harness consumes a per-edge confidence"
        )


def test_the_json_seam_the_report_promises_is_really_still_written() -> None:
    """The report prose and this module's own docstring both promise that the
    per-edge `confidences` arrays survive in `runs-*.json` as the seam for an
    arm that re-adds the field. A promise no test reads is the shipped-prose
    failure this repository has already paid for once.

    Both halves: the runner still writes the key, and the stored runs still
    carry it. Asserting only the runner would let the stored evidence be
    stripped by a future cleanup that read '#740 removed the confidences' too
    broadly -- which is exactly the over-correction this change is one grep
    away from."""
    source = _EDGE_RUNNER.read_text(encoding="utf-8")
    assert '"confidences"' in source, (
        f"{_EDGE_RUNNER.name} no longer writes the per-edge `confidences` "
        "arrays, so the seam its own report promises does not exist"
    )

    stored = sorted(_EDGE_RESULTS.glob("runs-*.json"))
    assert len(stored) >= 19, (
        f"expected the stored edge-typing runs, found {len(stored)} in {_EDGE_RESULTS}"
    )
    missing: list[str] = []
    for run in stored:
        parsed = json.loads(run.read_text(encoding="utf-8"))
        outcomes = parsed.get("outcomes")
        # Named rather than indexed: a run written under a different shape
        # would otherwise raise KeyError or TypeError from inside the scan,
        # and the traceback names the comprehension instead of the file.
        # Non-empty, not merely a list: `all()` over an empty `outcomes`
        # is True, so a run with no rows would report as though its arrays
        # had survived.
        assert isinstance(outcomes, list), (
            f"{run.name} carries no `outcomes` list, so this guard cannot tell "
            "whether its per-edge confidences survived"
        )
        assert outcomes, (
            f"{run.name} carries an EMPTY `outcomes` list; the scan below "
            "would report it as intact, since `all()` over nothing is true"
        )
        if not all(
            isinstance(outcome, dict) and "confidences" in outcome
            for outcome in outcomes
        ):
            missing.append(run.name)
    assert missing == [], (
        f"{len(missing)} of {len(stored)} stored runs lost their per-edge "
        f"`confidences` arrays: {missing}"
    )


def test_the_contradictions_runner_still_reports_its_confidence_column() -> None:
    """The other half of the floor.

    `evals/contradictions/` reads `ContradictionVerdict.confidence`, a real
    reply field that the shipped `>= 0.7` gate depends on, and its report is
    where #558's antonym result was read. Removing #740's dead column by
    grepping for `confidence` across `evals/` would take this with it."""
    source = _CONTRADICTIONS_RUNNER.read_text(encoding="utf-8")
    assert _LIVE_COLUMN in source, (
        f"{_CONTRADICTIONS_RUNNER.name} no longer reports "
        f"'{_LIVE_COLUMN}'; that column measures a real field and is not the "
        "one #740 removed"
    )


@pytest.mark.parametrize(
    "runner",
    [
        pytest.param(_EDGE_RUNNER, id="edge_typing"),
        pytest.param(_CONTRADICTIONS_RUNNER, id="contradictions"),
    ],
)
def test_the_report_names_the_generation_ceiling_and_context_window(
    runner: Path,
) -> None:
    """#738 made both settings part of an arm's identity and wrote them into
    each `runs-*.json`; the human-readable report beside it did not name
    them, so the artifact a person reads could not be told apart from a
    pre-#738 run measured under unbounded conditions (#740 item 3).

    Asserted against the report-building source rather than the stored
    reports: every `.md` on disk predates this change, and back-filling a
    measurement artifact with settings nobody recorded at the time would be
    inventing evidence. The runners are read as text rather than imported --
    both insert into `sys.path` at module scope and import a bare `fixtures`
    module that only resolves because of it, which is not a side effect worth
    importing into the unit suite.

    KNOWN LIMIT, stated rather than papered over: this proves the identifiers
    are referenced where the report is built, NOT that the rendered line comes
    out right. Nothing here executes the f-string, so a broken format would
    still pass. Rendering it would mean either importing the runner (rejected
    above) or extracting a report builder out of both harnesses, which is a
    larger change to measurement tooling than a one-line report fix earns.
    The runners have never had unit tests; this is the cheapest guard that
    fails on the commit that drops the line, which is the regression the
    issue actually describes.

    The section is bounded on BOTH ends, and the first draft of this guard
    shows why. Splitting on the first `lines = [` picked up an unrelated list
    at `run_contradictions_eval.py:83` and swept in the client construction
    two hundred lines above the report, so the assertion passed against a
    runner that named the settings nowhere in its report. The table header
    below is the anti-vacuity check: if the slice is not the report builder,
    the guard fails instead of passing for free."""
    source = runner.read_text(encoding="utf-8")
    # The LAST `lines = [` is the report builder in both runners.
    _, marker, after = source.rpartition("lines = [")
    assert marker, (
        f"{runner.name} no longer builds its report through a `lines` list; "
        "this guard needs updating with it"
    )
    block, joined, _ = after.partition('report = "\\n".join(lines)')
    assert joined, (
        f"{runner.name} no longer joins its report out of `lines`; this guard "
        "needs updating with it"
    )
    assert "| metric | value |" in block, (
        f"the slice taken from {runner.name} is not its report builder -- it "
        "carries no metric table, so every assertion below would pass without "
        "reading the report"
    )
    # Comments stripped first: both runners carry a comment above the line
    # explaining WHY the settings belong in the report, and a bare substring
    # search over the raw slice is satisfied by that comment alone -- so
    # deleting the line while leaving its rationale behind would pass.
    emitted = "\n".join(
        line for line in block.splitlines() if not line.lstrip().startswith("#")
    )
    for setting in ("DEFAULT_MAX_GENERATION_TOKENS", "DEFAULT_CONTEXT_WINDOW"):
        assert setting in emitted, (
            f"{runner.name}'s markdown report does not name {setting} in any "
            "emitted line, so a reader cannot tell the arm apart from a "
            "pre-#738 run"
        )
