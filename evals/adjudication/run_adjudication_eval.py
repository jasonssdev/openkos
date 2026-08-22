"""Scores the entity-resolution adjudicator so a prompt change to it can be
measured.

Issue #796: two Events sharing one title were judged `same` -- and the
judge's own rationale said "the event appears to be a continuation or
follow-up of the same meeting", which is an argument for `different` written
under a `same` verdict. A `same` verdict feeds a DESTRUCTIVE merge, so this
class of error costs a document. No harness scored this judge, so a prompt
fix would have been adopted on intuition, which this project has already
paid for (see `evals/edge_typing/README.md`).

The measurement runs BOTH directions of the change, because a rule that
makes identical Event titles read as a recurring series buys its precision
somewhere. `adjudication_fixtures.py` carries the five probe classes; the
three control classes exist so a rubric that answered `different` to
every Event pair -- or to everything -- cannot score well.

Numbers, per arm:

**Recurrence precision** -- share of `recurrence` pairs judged `different`.
This is THE number #796 is about.

**event-same retention** -- share of `event-same` pairs judged `same`. What
the fix must NOT cost: these are one meeting recorded twice, and they are
the only thing standing between a passing recurrence number and a judge
that has simply stopped merging Events.

**person-same / alias-same retention** -- share judged `same`. The
surrounding recall an Event-specific rule has no business disturbing;
`person-same` is the control #796 names explicitly.

**part-whole** -- share judged `different`. The exclusion the shipped prompt
already states, carried here so a rewrite cannot quietly drop it.

**Per-probe verdict distribution** -- the full same/different/uncertain
split per class, because the single headline rate per class cannot tell a
verdict that flipped to the other pole from one that collapsed into
`uncertain`, and those are different failures with different costs.

**Verdict accuracy** overall, against `adjudication_fixtures.LabelledPair.
expected`. Labels are CONSTRUCTED, not adjudicated -- read as
rubric-consistency.

**Stability** -- modal verdict share per pair across runs; needs no labels.

**Confidence separation** -- mean stated confidence on correct verdicts vs
wrong ones. #796's misjudgment was confident, so a change that only
lowers confidence on the class is worth telling apart from one that
changes the verdict.

Usage:

    python evals/adjudication/run_adjudication_eval.py --arm baseline --runs 15
    python evals/adjudication/run_adjudication_eval.py --arm baseline --ablate-clause --runs 15
    python evals/adjudication/run_adjudication_eval.py --self-test

`baseline` runs production untouched. The two ablation flags remove one
shipped mechanism each -- `--ablate-clause` swaps
`adjudication._SYSTEM_PROMPT` for `adjudication_prompts.ABLATED_SYSTEM_PROMPT`,
and `--ablate-withdrawal` disables the deterministic self-refutation check.
Both shipped for #796, so measuring either one's contribution means taking
it away; a probe that re-added it would compare production against
itself. Writes
`results/adjudication-<arm>-<stamp>.md` and a sibling `runs-*.json` so
stored emissions stay re-analyzable without re-spending them -- and, per
#807, that JSON carries every `rationale` VERBATIM, not just the verdict
and the confidence. #807's precedent is the reason: `evals/edge_typing/`
discarded rationales, and a later question about what the model had
ARGUED could only be answered by paying for the runs again. Here the
question is already on the table -- a deterministic post-parse rule ("this
rationale calls one member a continuation or a follow-up of the other,
which has already conceded they are different") has to be measurable
against stored runs, and it cannot be if the text is thrown away. Never
compare arms measured on different fixture sets.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import statistics
import sys
import tempfile
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime
from typing import cast

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
# APPENDED, not inserted at zero: an insert would put the evals root
# AHEAD of this harness's own directory, so a module added at the root
# would shadow a same-named one beside this file (`fixtures.py` is the
# obvious candidate).
sys.path.append(str(REPO_ROOT / "evals"))

from adjudication_fixtures import PAIRS, PROBES, LabelledPair, documents  # noqa: E402
from adjudication_prompts import (  # noqa: E402
    _RECURRENCE_CLAUSE,
    ABLATED_SYSTEM_PROMPT,
)
from harness_report import arm_identity_line  # noqa: E402

from openkos.config import (  # noqa: E402
    DEFAULT_CONTEXT_WINDOW,
    DEFAULT_MAX_GENERATION_TOKENS,
)
from openkos.llm.ollama import OllamaClient, OllamaError  # noqa: E402
from openkos.resolution import adjudication as adjudication_mod  # noqa: E402
from openkos.resolution import candidates as candidates_mod  # noqa: E402

DEFAULT_MODEL = "qwen3:8b"
DEFAULT_RUNS = 15
"""15, not the 3 the contradiction harness defaults to: #765 measured a
single arm swinging 0.25 against ITSELF across 5 runs, which is wider than
the per-class shift this harness has to resolve. A 5-run arm here would
report noise as an effect in either direction."""

_MISSING = "<missing>"
"""Recorded for a labelled pair whose group produced no result in a run --
a `find_candidates` change, a mid-loop `OllamaError` that cut the batch
short (#441), or a group that never formed. Kept as its own verdict token
rather than folded into `uncertain`: the two mean opposite things about
whether the model was ever asked."""


def _materialize_bundle(bundle_dir: pathlib.Path) -> None:
    """Write every fixture document as a minimal OKF concept file.
    `sensitivity: private` is NOT decoration: `sensitive_concept_ids` fails
    CLOSED on an absent value, so a document without it is dropped from the
    prompt before it is read, every group short-circuits to
    `UNCERTAIN`/`0.0`/"no readable member content", and the arm records a
    full sweep of uncertain verdicts having never called the model once.
    Same reason as the CLI test helpers and the contradiction harness."""
    for doc in documents():
        path = bundle_dir / f"{doc.concept_id}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "---",
            f"type: {doc.okf_type}",
            f"title: {doc.title}",
            "sensitivity: private",
            "---",
            f"# {doc.title}",
            "",
            doc.body,
        ]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _pair_key(labelled: LabelledPair) -> frozenset[str]:
    """The identity a labelled pair is matched to a real `CandidateGroup`
    by. A `frozenset`, not a sorted tuple: `CandidateGroup.member_ids` is
    already sorted, but the grouping is a SET relation and a HIGH group is
    permitted more than two members -- an order-carrying key would silently
    stop matching the day a third fixture joined a title bucket, and the
    pair would read as `<missing>` rather than as a fixture defect."""
    return frozenset((labelled.left.concept_id, labelled.right.concept_id))


def _run_once(
    bundle_dir: pathlib.Path, client: OllamaClient
) -> dict[frozenset[str], tuple[str, float, str]]:
    """One full candidate-then-adjudicate pass; returns `(verdict,
    confidence, rationale)` keyed by member-id set. Runs the REAL production
    path -- the tiered `find_candidates` scan, the sensitivity filter, the
    member load, prompt assembly, fail-closed parse -- so what is measured
    is what ships. The groups are DISCOVERED, never hand-constructed: a
    hand-built `CandidateGroup` would pin the tier and the trigger this
    harness is supposed to observe, and would keep scoring after a
    `find_candidates` change had stopped producing the pair at all.

    The `rationale` rides along per #807 (module docstring): it is the only
    field a later re-analysis of a stored run can ask a NEW question of."""
    groups = candidates_mod.find_candidates(bundle_dir)
    batch = adjudication_mod.adjudicate_candidates(
        groups, bundle_dir=bundle_dir, llm=client
    )
    return {
        frozenset(result.candidate.member_ids): (
            result.verdict.value,
            result.confidence,
            result.rationale,
        )
        for result in batch.results
    }


# --------------------------------------------------------------------------- #
# self-test -- no model, no network
# --------------------------------------------------------------------------- #


def _self_test() -> int:
    """Assert the two things that would make a paid run measure nothing.

    (a) The fixture bundle materializes and the SHIPPED `find_candidates`
    returns exactly one group per labelled pair, with no extras and none
    missing. An extra group is a paid LLM call scored against no label; a
    missing one is a probe class silently shrinking, and a class that lost
    its only `event-same` pair would let a `different`-everything rubric
    read as a clean win.

    (b) The ablation arm is not a silent no-op: its prompt must DIFFER
    from the shipped one and must be exactly the shipped one plus the
    inserted clause. Reaching for `_RECURRENCE_CLAUSE` by name is
    deliberate -- deriving the delta by diffing the two strings would
    re-implement the insertion the arm performs, and a re-implementation
    agrees with a broken original."""
    failures: list[str] = []

    def check(label: str, got: object, want: object) -> None:
        if got != want:
            failures.append(f"{label}: got {got!r}, want {want!r}")

    docs = documents()
    check("every pair contributes two documents", len(docs), len(PAIRS) * 2)
    check(
        "every probe class in PROBES is populated",
        sorted({p.probe for p in PAIRS}),
        sorted(PROBES),
    )

    with tempfile.TemporaryDirectory() as tmp:
        bundle_dir = pathlib.Path(tmp) / "bundle"
        bundle_dir.mkdir(parents=True)
        _materialize_bundle(bundle_dir)
        check(
            "the bundle materializes one file per document",
            len(list(bundle_dir.rglob("*.md"))),
            len(docs),
        )
        groups = candidates_mod.find_candidates(bundle_dir)

    found = [frozenset(group.member_ids) for group in groups]
    wanted = [_pair_key(pair) for pair in PAIRS]
    missing = sorted(
        f"{pair.probe}:{'+'.join(sorted(_pair_key(pair)))}"
        for pair in PAIRS
        if _pair_key(pair) not in found
    )
    extra = sorted("+".join(sorted(key)) for key in found if key not in wanted)
    check("no labelled pair is missing from find_candidates", missing, [])
    check("find_candidates produces no unlabelled group", extra, [])
    check("one group per labelled pair, none duplicated", len(found), len(PAIRS))

    shipped = adjudication_mod._SYSTEM_PROMPT
    check(
        "the ablated prompt differs from the shipped one",
        shipped != ABLATED_SYSTEM_PROMPT,
        True,
    )
    check(
        "and differs from it by exactly the removed clause",
        shipped.replace(_RECURRENCE_CLAUSE, "", 1),
        ABLATED_SYSTEM_PROMPT,
    )
    check(
        "the clause is present in production and absent from the ablation",
        _RECURRENCE_CLAUSE in shipped
        and _RECURRENCE_CLAUSE not in ABLATED_SYSTEM_PROMPT,
        True,
    )

    if failures:
        print("self-test FAILED:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print(
        f"self-test OK: {len(PAIRS)} labelled pairs across {len(PROBES)} probe "
        "classes materialize into exactly that many candidate groups with no "
        "extras, and the ablation arm is the shipped rubric minus the one clause "
        "production carries."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    # Not `required=True`: `--self-test` runs no arm at all, and a required
    # `--arm` would force the caller to name one the self-test then ignores,
    # which is how a harness starts reporting an arm it never ran.
    parser.add_argument("--arm", choices=["baseline"])
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="check the fixtures and the ablation arm with no model and no network",
    )
    # Without this the harness cannot reproduce its own stored arms. The
    # #796 withdrawal now ships INSIDE `adjudicate_candidates`, so a plain
    # `--arm baseline` today measures the shipped judge WITH the fix, not
    # the judge whose verdicts the stored baseline recorded -- the probe
    # would be comparing production against itself and reporting a tie
    # (the failure `evals/edge_typing/README.md` names). Ablating the
    # withdrawal restores the arm the stored runs were measured under.
    parser.add_argument(
        "--ablate-clause",
        action="store_true",
        help=(
            "remove #796's recurrence paragraph from the rubric, restoring "
            "the pre-fix prompt"
        ),
    )
    parser.add_argument(
        "--ablate-withdrawal",
        action="store_true",
        help=(
            "disable the shipped #796 self-refuting-SAME withdrawal, "
            "restoring the pre-fix judge; required to reproduce the stored "
            "arms, which were measured before it shipped"
        ),
    )
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()
    if args.arm is None:
        parser.error("--arm is required unless --self-test is given")

    if args.ablate_clause:
        adjudication_mod._SYSTEM_PROMPT = ABLATED_SYSTEM_PROMPT
    if args.ablate_withdrawal:
        adjudication_mod.withdraw_self_refuting_same = lambda verdict, rationale: (
            verdict,
            rationale,
        )

    # Built with production's OWN generation ceiling and context window, not
    # the client's opted-out defaults (#700). Unpinned, this harness measured a
    # model under conditions `curate` never runs it in: `num_predict` absent, so
    # a model that fails to terminate burns the full 600s transport deadline and
    # the arm records a timeout rather than a verdict; and `num_ctx` absent, so
    # the model reserves whatever window its own Modelfile ships -- the 32K/10 GB
    # footprint #691 pinned away. Both matter most for exactly the comparison
    # this harness serves: a rubric that only makes the model deliberate
    # longer must not be confusable with one that changes its mind.
    client = OllamaClient(
        model=args.model,
        max_generation_tokens=DEFAULT_MAX_GENERATION_TOKENS,
        context_window=DEFAULT_CONTEXT_WINDOW,
    )
    observed: list[dict[frozenset[str], tuple[str, float, str]]] = []
    latencies: list[float] = []

    with tempfile.TemporaryDirectory() as tmp:
        bundle_dir = pathlib.Path(tmp) / "bundle"
        bundle_dir.mkdir(parents=True)
        _materialize_bundle(bundle_dir)
        for index in range(args.runs):
            started = time.monotonic()
            # A mid-loop transport failure must not discard the runs that
            # already succeeded. Each run is minutes of paid local compute,
            # and `_MISSING` already names this failure mode -- losing an
            # arm on its fourteenth run and reporting nothing is how a
            # measurement session ends with no measurement. What completed
            # is written, and the report says how many runs it actually
            # holds rather than the number that was asked for.
            try:
                observed.append(_run_once(bundle_dir, client))
            except OllamaError as exc:
                print(
                    f"  run {index + 1}/{args.runs} FAILED ({exc}); "
                    f"scoring the {len(observed)} completed run(s)",
                    file=sys.stderr,
                )
                break
            latencies.append(time.monotonic() - started)
            print(f"  run {index + 1}/{args.runs} done ({latencies[-1]:.1f}s)")

    if not observed:
        raise SystemExit("no run completed; nothing to score")
    completed = len(observed)

    per_pair: dict[frozenset[str], list[tuple[str, float, str]]] = defaultdict(list)
    for run in observed:
        for labelled in PAIRS:
            key = _pair_key(labelled)
            per_pair[key].append(run.get(key, (_MISSING, 0.0, "")))

    rows: list[dict[str, object]] = []
    right_confidences: list[float] = []
    wrong_confidences: list[float] = []
    class_totals: Counter[str] = Counter()
    class_verdicts: dict[str, Counter[str]] = defaultdict(Counter)
    correct = 0
    stabilities: list[float] = []

    for labelled in PAIRS:
        key = _pair_key(labelled)
        outcomes = per_pair[key]
        verdicts = [v for v, _, _ in outcomes]
        for verdict, confidence, _rationale in outcomes:
            class_totals[labelled.probe] += 1
            class_verdicts[labelled.probe][verdict] += 1
            if verdict == labelled.expected:
                correct += 1
                right_confidences.append(confidence)
            else:
                wrong_confidences.append(confidence)
        counts = Counter(verdicts)
        modal, modal_count = counts.most_common(1)[0]
        stability = modal_count / len(verdicts)
        stabilities.append(stability)
        rows.append(
            {
                "left_id": labelled.left.concept_id,
                "right_id": labelled.right.concept_id,
                "probe": labelled.probe,
                "expected": labelled.expected,
                "note": labelled.note,
                "modal": modal,
                "stability": stability,
                "accuracy": sum(1 for v in verdicts if v == labelled.expected)
                / len(verdicts),
                # Verbatim rationales ride here per #807 (module docstring):
                # a stored run that kept only the verdict and the confidence
                # cannot answer a question nobody had asked yet.
                "outcomes": [[v, c, r] for v, c, r in outcomes],
            }
        )

    total = len(PAIRS) * args.runs
    accuracy = correct / total if total else 0.0
    mean_stability = statistics.fmean(stabilities) if stabilities else 0.0

    def _share(probe: str, verdict: str) -> float:
        denom = class_totals[probe]
        return class_verdicts[probe][verdict] / denom if denom else 0.0

    recurrence_precision = _share("recurrence", "different")
    event_same_retention = _share("event-same", "same")
    person_same_retention = _share("person-same", "same")
    alias_same_retention = _share("alias-same", "same")
    part_whole_rate = _share("part-whole", "different")

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    results_dir = pathlib.Path(__file__).resolve().parent / "results"
    results_dir.mkdir(exist_ok=True)
    ablated = "".join(
        (
            "-noclause" if args.ablate_clause else "",
            "-nowithdrawal" if args.ablate_withdrawal else "",
        )
    )
    slug = f"{args.arm}{ablated}-{stamp}-{args.model.replace(':', '-')}"

    (results_dir / f"runs-{slug}.json").write_text(
        json.dumps(
            {
                "arm": args.arm,
                # Part of the arm's identity for the same reason the client
                # settings are: a run measured with the shipped withdrawal
                # active and one measured without it are different arms, and
                # a stored file that does not say which cannot be compared
                # to anything.
                "clause_ablated": args.ablate_clause,
                "withdrawal_ablated": args.ablate_withdrawal,
                "model": args.model,
                "runs": completed,
                "runs_requested": args.runs,
                "generated_at": stamp,
                # The client settings are part of the arm's identity, not
                # trivia (#700): they were unpinned before that issue, so a
                # stored run that does not name them cannot be told apart from
                # one measured under the old, unbounded conditions.
                "max_generation_tokens": DEFAULT_MAX_GENERATION_TOKENS,
                "context_window": DEFAULT_CONTEXT_WINDOW,
                "outcomes": rows,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    lines = [
        f"# adjudication eval — arm `{args.arm}`"
        f"{' — ABLATED:' + ablated.replace('-', ' ') if ablated else ''} (#796)",
        "",
        f"_Generated: {stamp}_ · model `{args.model}` · **{completed} runs**"
        f"{'' if completed == args.runs else f' of {args.runs} requested'}"
        f" over {len(PAIRS)} labelled pairs.",
        "",
        # Part of the arm's identity, not trivia (#700/#740): the JSON beside
        # this file has recorded both since #738, but a reader who opens only
        # the report cannot otherwise tell this run apart from a pre-#738 one
        # measured under unbounded conditions.
        arm_identity_line(
            max_generation_tokens=DEFAULT_MAX_GENERATION_TOKENS,
            context_window=DEFAULT_CONTEXT_WINDOW,
        ),
        "",
        "Labels are CONSTRUCTED, not adjudicated — see `adjudication_fixtures.py`.",
        "Rationales are not printed here; every one is stored verbatim in the"
        " sibling `runs-*.json` (#807).",
        "",
        "| metric | value |",
        "| --- | --- |",
        f"| **recurrence precision (judged `different`)** | "
        f"**{recurrence_precision:.2f}** |",
        f"| **event-same retention (judged `same`)** | **{event_same_retention:.2f}** |",
        f"| person-same retention (judged `same`) | {person_same_retention:.2f} |",
        f"| alias-same retention (judged `same`) | {alias_same_retention:.2f} |",
        f"| part-whole (judged `different`) | {part_whole_rate:.2f} |",
        f"| verdict accuracy vs label | {accuracy:.2f} |",
        f"| mean stability (modal share) | {mean_stability:.2f} |",
        f"| mean run latency | {statistics.fmean(latencies):.1f}s |",
        f"| mean confidence, CORRECT verdicts | "
        f"{statistics.fmean(right_confidences) if right_confidences else 0.0:.2f} |",
        f"| mean confidence, WRONG verdicts | "
        f"{statistics.fmean(wrong_confidences) if wrong_confidences else 0.0:.2f} |",
        "",
        "## Per probe",
        "",
        # `missing` is its own column rather than absorbed into `uncertain`:
        # the three verdict shares must be readable as a distribution over
        # answers the model actually gave, and a column that silently
        # includes runs it was never asked for is the kind of denominator
        # this project has already been burned by.
        "| probe | expected | n | same | different | uncertain | missing |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    expected_by_probe = {pair.probe: pair.expected for pair in PAIRS}
    for probe in PROBES:
        lines.append(
            f"| {probe} | `{expected_by_probe[probe]}` | {class_totals[probe]} | "
            f"{_share(probe, 'same'):.2f} | {_share(probe, 'different'):.2f} | "
            f"{_share(probe, 'uncertain'):.2f} | {_share(probe, _MISSING):.2f} |"
        )

    lines += [
        "",
        "## Per pair",
        "",
        "| pair | probe | expected | modal | acc | stab | confidences |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        label = f"{row['left_id']} <-> {row['right_id']}"
        row_outcomes = cast("list[list[object]]", row["outcomes"])
        confs = ", ".join(f"{float(str(o[1])):.2f}" for o in row_outcomes)
        lines.append(
            f"| {label} | {row['probe']} | `{row['expected']}` | "
            f"`{row['modal']}` | {row['accuracy']:.2f} | "
            f"{row['stability']:.2f} | {confs} |"
        )

    report = "\n".join(lines) + "\n"
    (results_dir / f"adjudication-{slug}.md").write_text(report, encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
