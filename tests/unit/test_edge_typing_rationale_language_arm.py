"""`evals/edge_typing/` must be able to measure what pinning the rationale
language COSTS (issue #812).

#812's fix is a prompt change on a common path, and this repository has a
standing rule against adopting one unmeasured
(`extraction.concept._LANGUAGE_ANCHOR`'s docstring; a longer extraction
prompt already lost its A/B here). The unset default sidesteps that rule by
sending the pre-#812 bytes -- but the PINNED path is a real, longer prompt,
and an operator who turns the key on deserves to know whether it costs
accuracy or stability. `evals/edge_typing/` is the one harness that scores
this suggester, so the arm belongs there or nowhere.

WHY SOURCE TEXT AND NOT AN IMPORT. Every runner inserts into `sys.path` at
module scope and imports a bare `fixtures` module that only resolves because
of it -- side effects `test_harness_report.py`'s docstring records as not
worth bringing into the unit suite, which is why the sibling
`test_edge_typing_confidence_column.py` guards this same runner by reading
it as text. This module follows that precedent.

The weakness of that choice is named rather than papered over: source text
cannot prove the flag reaches the model, only that the three links spelling
it out are present and connected -- the parser declares it, `main` hands it
to `_run_once`, and `_run_once` forwards it into `suggest_edge_types`. What
proves the value actually reaches the prompt is
`tests/unit/resolution/test_rationale_language.py`, which executes that
seam. This module guards the harness wiring above it.

Nothing here runs the harness. These tests make zero model calls.
"""

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RUNNER = _REPO_ROOT / "evals" / "edge_typing" / "run_edge_typing_eval.py"

_FLAG = "--rationale-language"
"""The arm's CLI flag, hyphenated, which argparse maps to
`args.rationale_language`."""


def _source() -> str:
    return _RUNNER.read_text(encoding="utf-8")


def _slice_between(text: str, start: str, end: str) -> str:
    """The source between two anchors, so an assertion lands inside the
    function it is about rather than anywhere in the file."""
    head = text.index(start)
    return text[head : text.index(end, head)]


def test_the_runner_declares_the_pinned_language_flag() -> None:
    """The arm is selectable, and defaults to the unpinned baseline.

    `default=None` is the load-bearing half: every arm already measured --
    every `runs-*.json` in `results/` -- was taken with no language pinned,
    so a flag that defaulted to anything else would silently re-baseline the
    harness against a prompt none of those runs used.

    Sliced from the FLAG, not from the parser block. The first version of
    this test started at `parser.add_argument("--arm"`, which spans all four
    arguments, so `default=None` on any one of them satisfied it: giving
    `--model` that default while this flag defaulted to `"Spanish"` left the
    test green with the harness silently re-baselined -- the exact claim the
    docstring makes. The existence check above the slice keeps whole-file
    scope on purpose: the slice is anchored ON the flag, so a missing flag
    has to be reported by an assertion rather than by `_slice_between`
    raising."""
    source = _source()
    assert f'"{_FLAG}"' in source

    flag_slice = _slice_between(source, f'"{_FLAG}"', "args =")

    assert "default=None" in flag_slice


def test_main_hands_the_pinned_language_to_the_run() -> None:
    """`main` passes the parsed flag into the call that spends the models.

    Asserted as the whole `_run_once(...)` call expression, not as a
    mention of `args.rationale_language` anywhere in `main`: the flag is
    also written into the report and the JSON, so a loose membership check
    stays green with the run itself left on the baseline. That exact
    mutation survived the first version of this test.

    An accepted-but-recorded-only flag is the worst outcome available here:
    the arm would be labelled pinned in `results/`, be measured as the
    baseline, and the two would be indistinguishable afterwards."""
    assert "_run_once(bundle_dir, client, args.rationale_language)" in _source()


def test_run_once_forwards_the_pinned_language_into_the_suggester() -> None:
    """`_run_once` forwards its parameter into `suggest_edge_types`.

    Asserted as the forwarding EXPRESSION, not just the keyword: a call
    site reading `rationale_language=None` would satisfy a keyword-only
    check while measuring the baseline under the pinned arm's name."""
    run_once_slice = _slice_between(_source(), "def _run_once(", "def main()")

    assert "rationale_language: str | None" in run_once_slice
    assert "rationale_language=rationale_language" in run_once_slice


def test_the_stored_run_json_records_which_language_the_arm_pinned() -> None:
    """`runs-*.json` carries the pinned language as its own FIELD.

    The same reasoning #738/#740 applied to the generation ceiling and the
    context window: a stored run that cannot be told apart from one taken
    under different prompt bytes is not re-analyzable, and `arm` will not do
    -- it is a free-text label the operator types.

    Asserted as the whole `"key": value` pair inside a slice that stops at
    `"outcomes"`. Two looser versions of this test were written first and
    both survived mutation: one bounded the slice at `if __name__`, which
    swallowed the report builder's own mention, and one matched the bare
    name `rationale_language`, which still matched after the KEY was renamed
    because the VALUE expression contains it."""
    payload_slice = _slice_between(_source(), "json.dumps(", '"outcomes": rows')

    assert '"rationale_language": args.rationale_language' in payload_slice


def test_the_rendered_report_names_which_language_the_arm_pinned() -> None:
    """The human-readable report says it too.

    The report is the artifact a person opens, and #740's finding was
    exactly that a reader who opens only the report cannot otherwise tell
    the run's conditions apart. Both halves are asserted -- the label a
    reader scans for, and the interpolation that makes it true -- because
    the branch CONDITION also mentions the attribute, so matching the name
    alone stays green with the rendered line deleted."""
    report_slice = _slice_between(_source(), "lines = [", '"## Type distribution"')

    assert "Rationale language pinned" in report_slice
    assert "{args.rationale_language}" in report_slice
