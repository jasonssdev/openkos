"""Decision-revisions model screen (#1563): forecast, run, score.

The decision-revision judge asks a model, once per candidate pair of Decisions,
whether the later one `reverses`, `refines`, `reaffirms` or is `unrelated` to the
earlier one's choice (`resolution/decision_revision.py`, `judge_pairs`). A
`reverses`/`refines` verdict with confidence and two verified quotes is queued for
a person to accept or decline. `evals/decision_revisions/` measured `qwen3:8b` on
it (58 labelled pairs, 15 runs) and tuned the prompt against that fixture; no
other model has been measured, and the labels have no second adjudication. This
is the key-justification screen the volatility screen was: does any local model
beat what needs no model, and does the task key need a different model than the
global one. Each fixture item is put through production's own `judge_pairs`, so
the shipped prompt and parser are what is measured; the candidate stage is not
(it needs an embedder), the pair is handed to the judge directly.

Subcommands:

- `forecast`  the call count and Ollama wall-clock a run would cost, and the
              model-free baselines. Never contacts a model. `run --dry-run` is
              the same.
- `run`       one model, every item, N seeded runs; cached by row.
- `score`     the numbers and the draft decision rule for the stored runs.
- `blind-sheet` / `adjudicate`  the blind second adjudication: write the sheet a
              second labeller fills in, then compare it with the fixture's
              labels. No model; see `README.md`, "Second adjudication".

STATUS: DRAFT. `PREREGISTRATION-DRAFT.md` is blocked on the owner's blind
adjudication; no model has been run. Read `README.md` before trusting any
number. Model-free check: `--self-test`.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Final

HERE = pathlib.Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(HERE))
sys.path.append(str(REPO_ROOT / "evals"))

import revisions_blind_adjudication as adj  # noqa: E402
import revisions_screen_core as core  # noqa: E402
from harness_stamp import (  # noqa: E402
    TimedBackend,
    build_stamp,
    stamp_problems,
)

from openkos.config import (  # noqa: E402
    DEFAULT_CONTEXT_WINDOW,
    DEFAULT_MAX_GENERATION_TOKENS,
)
from openkos.llm.base import BackendError, is_timeout_failure  # noqa: E402
from openkos.llm.ollama import OllamaClient  # noqa: E402
from openkos.resolution.decision_revision import (  # noqa: E402
    JUDGE_PROMPT_VERSION,
    JudgeSide,
    build_judge_messages,
    is_actionable_revision,
    judge_pairs,
)

DEFAULT_RUNS_DIR: Final = (
    pathlib.Path.home() / "Dev/Projects/openkos-eval-runs/decision-revisions-screen"
)
BASELINE: Final = "qwen3:8b"
CANDIDATES: Final = (
    "gemma4:26b-a4b",
    "qwen3:14b",
    "qwen3.6:27b",
    "qwen3.6:35b-a3b",
    "gemma2:27b",
)
DEFAULT_TIMEOUT: Final = 120.0
LOAD_S: Final = 60.0
ASSUMED_MEDIAN_LATENCY_S: Final[dict[str, float]] = {
    "qwen3:8b": 2.32,
    "gemma4:26b-a4b": 3.02,
    "qwen3:14b": 4.24,
    "qwen3.6:27b": 20.88,
    "qwen3.6:35b-a3b": 3.72,
    "gemma2:27b": 7.3,
}
"""ASSUMED per-call latencies, NOT measured for this task. For the five models
the volatility screen ran, twice that screen's MEASURED per-call median
(1.16, 1.51, 2.12, 10.44 and 1.86 s on 162 calls each): a judge reply carries a
rationale and two verbatim quotes where a volatility reply is one short object,
so the reply is longer, and doubling is a guess, stated as one. `gemma2:27b` was
not in that screen; 7.3 s is `evals/edge_typing/README.md`'s "about 9 minutes for
74 edges" divided out. No judge latency was stored by `evals/decision_revisions/`
(its rows carry none). A stage-0 timing of three calls per model replaces these
before any registered forecast."""

Row = dict[str, Any]


# --- one call ----------------------------------------------------------------


def judge_sides(item: core.Item) -> tuple[JudgeSide, JudgeSide]:
    """The pair as the harness hands it to production: both Decisions with the
    dates the fixture resolves for them. Which is shown first, and under which
    header, is production's own decision (`build_judge_messages`)."""
    first, second = (
        JudgeSide(
            concept_id=item.pair_ids[n],
            title=item.titles[n],
            body=item.bodies[n],
            date=item.dates[n],
        )
        for n in (0, 1)
    )
    return first, second


def run_item(llm: Any, item: core.Item) -> Row:
    """One production `judge_pairs` call over one pair.

    A failed call is a row, never an exception: a transport failure is `backend`
    (`timeout` if it ran out of time), a reply production's parser refused is
    `malformed` (production itself degrades it to `unrelated` with confidence 0;
    the screen scores it wrong and counts it as a failure instead, so a model
    that cannot answer is not credited for a pair that happens to be unrelated)."""
    timed = TimedBackend(llm)
    batch = judge_pairs([judge_sides(item)], llm=timed)
    latency = timed.call_latencies_s[-1] if timed.call_latencies_s else 0.0
    row: Row = {
        "item": item.id,
        "verdict": None,
        "confidence": None,
        "actionable": False,
        "failure": None,
        "latency_s": latency,
    }
    if batch.failure is not None:
        row["failure"] = "timeout" if is_timeout_failure(batch.failure) else "backend"
    elif not batch.results:
        row["failure"] = "no_result"
    else:
        result = batch.results[0]
        row["confidence"] = result.confidence
        if result.malformed:
            row["failure"] = "malformed"
        else:
            row["verdict"] = result.verdict.value
            row["actionable"] = is_actionable_revision(
                result.verdict.value,
                result.confidence,
                result.quotes[0],
                result.quotes[1],
            )
    return row


def run_model(
    make_client: Callable[[int], Any],
    model: str,
    items: Sequence[core.Item],
    *,
    runs: Sequence[int],
    existing: Sequence[Row] = (),
    on_row: Callable[[Row], None] | None = None,
) -> list[Row]:
    """Every (run, item) not already in `existing`, in a fixed order. The cache
    key is the row's identity, so an interrupted run resumes and a finished one
    costs nothing."""
    rows = list(existing)
    done = {(r["run"], r["item"]) for r in rows}
    for run in runs:
        client = make_client(run)
        for item in items:
            if (run, item.id) in done:
                continue
            row = run_item(client, item)
            row.update(model=model, run=run)
            rows.append(row)
            if on_row is not None:
                on_row(row)
    return rows


# --- storage -----------------------------------------------------------------


def slug(model: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", model)


def runs_path(runs_dir: pathlib.Path, model: str) -> pathlib.Path:
    return runs_dir / f"runs-{slug(model)}.json"


def load_runs(path: pathlib.Path) -> dict[str, Any]:
    if not path.is_file():
        return {"stamp": None, "rows": []}
    return dict(json.loads(path.read_text(encoding="utf-8")))


def save_runs(
    path: pathlib.Path, stamp: Mapping[str, Any], rows: Sequence[Row]
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"stamp": stamp, "rows": list(rows)}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )


# --- forecast ----------------------------------------------------------------


def _fmt(value: float | None, digits: int = 3) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def render_forecast(
    items: Sequence[core.Item],
    *,
    models: Sequence[str],
    runs: int,
    latencies: Mapping[str, float],
) -> str:
    plan = core.forecast(
        n_items=len(items),
        runs=runs,
        median_latency_s={m: latencies[m] for m in models},
        load_s=LOAD_S,
    )
    table = core.baselines(items)
    reference, ref = core.reference_baseline(items)
    ref_wrong = sum(1 for v in ref["per_item_strict"].values() if v == 0.0)
    both = sum(
        not table[core.BASELINE_NAMES[0]]["per_item_strict"][i.id]
        and not table[core.LEXICAL_BASELINE]["per_item_strict"][i.id]
        for i in items
    )
    lines = [
        "# Forecast (no model contacted)",
        "",
        f"items: {len(items)}  splits: {core.split_balance(items)}  "
        f"undirected: {sum(not i.directed for i in items)}  "
        f"contested: {sum(i.contested for i in items)}",
        f"class balance: {core.class_balance(items)}",
        f"runs: {runs}  models: {list(models)}",
        f"calls per model: {plan['calls_per_model']}  total calls: {plan['calls']}",
    ]
    for model in models:
        lines.append(
            f"  {model}: {plan['seconds'][model] / 60:.1f} min "
            f"(assumed median {latencies[model]:.2f} s/call + {LOAD_S:.0f} s load)"
        )
    lines += [
        f"total Ollama wall-clock: {plan['total_seconds'] / 3600:.2f} h "
        "(latencies assumed, not measured for this task)",
        "",
        "model-free baselines on this fixture (strict):",
    ]
    for name in core.BASELINE_NAMES:
        hits = round(table[name]["strict_accuracy"] * len(items))
        lines.append(
            f"  {name}: {table[name]['strict_accuracy']:.3f} ({hits} of {len(items)})"
        )
    lines += [
        f"  commonest label: {core.majority_baseline(items):.3f}",
        f"reference baseline (rule J): {reference}",
        f"items wrong for the reference (room for a model to win): "
        f"{ref_wrong} of {len(items)}",
        f"items wrong for both `{core.BASELINE_NAMES[0]}` and the lexical cut: "
        f"{both} of {len(items)}",
    ]
    return "\n".join(lines)


# --- score -------------------------------------------------------------------


def render_report(
    rows_by_model: Mapping[str, Sequence[Row]],
    items: Sequence[core.Item],
    *,
    baseline: str,
    resamples: int = 10_000,
) -> str:
    """The numbers for stored runs. Descriptive: the verdict lines apply the
    draft rule mechanically and adopt nothing."""
    table = core.baselines(items)
    reference, ref = core.reference_baseline(items)
    adjudicated = sum(i.adjudication is not None for i in items)
    lines = [
        "# Decision-revisions screen",
        "",
        "Labels: the drafter's, with the owner having accepted the contested ones"
        + (
            f"; {adjudicated} item(s) resolved by a second adjudicator."
            if adjudicated
            else "; NO second adjudication is recorded yet. Read every number as a"
            " measurement against one author's labels."
        ),
        "`strict` decides; `strict (1st)` and `strict (clean)` (the "
        f"{sum(not i.contested for i in items)} pairs the drafter did not flag) are"
        " sensitivity analyses and decide nothing.",
        "",
        "baselines (strict): "
        + ", ".join(f"{n} {_fmt(table[n]['strict_accuracy'])}" for n in table)
        + f"; commonest label {_fmt(core.majority_baseline(items))}; "
        f"reference for rule J: {reference}",
        "",
        "| model | strict | strict (1st) | strict (clean) | original | confirmation "
        "| failures | median s | p95 s | false supersede | queue recall "
        "| queue noise | stability |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    summaries: dict[str, dict[str, Any]] = {}
    for model, rows in rows_by_model.items():
        s = summaries[model] = core.summarize(rows, items)
        split = s["strict_by_split"]
        lines.append(
            f"| {model} | {_fmt(s['strict_accuracy'])} "
            f"| {_fmt(s['strict_first_accuracy'])} "
            f"| {_fmt(s['strict_uncontested_accuracy'])} "
            f"| {_fmt(split.get('original'))} | {_fmt(split.get('confirmation'))} "
            f"| {_fmt(s['failure_rate'])} | {_fmt(s['median_latency_s'], 2)} "
            f"| {_fmt(s['p95_latency_s'], 2)} | {_fmt(s['false_supersede_rate'])} "
            f"| {_fmt(s['queue_recall'])} | {_fmt(s['queue_noise_rate'])} "
            f"| {_fmt(s['stability'])} |"
        )
    for model, s in summaries.items():
        lines += ["", f"## {model}: recall and confusion", ""]
        lines.append(
            "recall: "
            + ", ".join(f"{g} {_fmt(s['per_class_recall'][g])}" for g in core.LABELS)
        )
        lines.append(f"failures {s['failures'] or 'none'}")
        answers = (*core.WIRE.values(), "none")
        lines.append("| gold \\ answered | " + " | ".join(answers) + " |")
        lines.append("| --- |" + " --- |" * len(answers))
        for gold in core.LABELS:
            cells = s["confusion"][gold]
            lines.append(
                f"| {gold} | " + " | ".join(str(cells[a]) for a in answers) + " |"
            )
    lines += ["", "## Decision (draft rule, applied mechanically)", ""]
    for model, rows in rows_by_model.items():
        own = core.per_item_strict(rows, items)
        gap = core.paired_bootstrap(own, ref["per_item_strict"], resamples=resamples)
        verdict = f"- {model}: gain over `{reference}` {_fmt(gap['point'])} "
        verdict += f"[{_fmt(gap['lo'])}, {_fmt(gap['hi'])}] -> "
        verdict += "key justified" if core.justified(gap) else "key NOT justified"
        lines.append(verdict)
        if model == baseline or baseline not in rows_by_model:
            continue
        delta = core.paired_bootstrap(
            own,
            core.per_item_strict(rows_by_model[baseline], items),
            resamples=resamples,
        )
        tripped = core.vetoes(summaries[model], summaries[baseline])
        lines.append(
            f"  vs {baseline}: delta {_fmt(delta['point'])} "
            f"[{_fmt(delta['lo'])}, {_fmt(delta['hi'])}], vetoes {tripped or 'none'} "
            f"-> {'SELECTED' if core.selected(delta, tripped) else 'not selected'}"
        )
    return "\n".join(lines)


# --- self-test ---------------------------------------------------------------


class _Stub:
    """A scripted `LLMBackend`: answers per question from the prompt it was sent."""

    def __init__(self, answer: Callable[[str, str], str | BaseException]) -> None:
        self.answer = answer
        self.prompts: list[tuple[str, str]] = []

    def chat(self, messages: Sequence[Mapping[str, str]]) -> str:
        user = messages[-1]["content"]
        ids = re.findall(
            r"^(?:EARLIER DECISION|LATER DECISION|DECISION \d)[^\[]*\[(.+?) — ",
            user,
            re.M,
        )
        if len(ids) != 2:
            raise ValueError(f"not a revision-judge prompt: {user[:80]!r}")
        self.prompts.append((messages[0]["content"], user))
        reply = self.answer(ids[0], ids[1])
        if isinstance(reply, BaseException):
            raise reply
        return reply


def _reply(item: core.Item, verdict: str, confidence: float = 0.9) -> str:
    """A well-formed judge reply quoting a sentence verbatim from each body. The
    quotes are in the order the judge is shown the pair."""
    shown = [item.earlier, 1 - item.earlier] if item.earlier is not None else [0, 1]
    first, second = (_first_sentence(item.bodies[n]) for n in shown)
    return json.dumps(
        {
            "verdict": verdict,
            "confidence": confidence,
            "rationale": "stub",
            "quote_first": first,
            "quote_second": second,
        }
    )


def _first_sentence(body: str) -> str:
    return re.split(r"(?<=[.!?])\s", body.strip(), maxsplit=1)[0]


def _by_ids(items: Sequence[core.Item]) -> dict[frozenset[str], core.Item]:
    return {frozenset(i.pair_ids): i for i in items}


def _constant_stub(by_pair: Mapping[frozenset[str], core.Item], verdict: str) -> _Stub:
    """A stub that answers `verdict` to every pair, well-formed and quoted."""
    return _Stub(lambda a, b: _reply(by_pair[frozenset((a, b))], verdict))


def _serves(client: _Stub) -> Callable[[int], _Stub]:
    return lambda run: client


def _synthetic(
    items: Sequence[core.Item], answer: Callable[[core.Item], str]
) -> list[Row]:
    return [
        {
            "item": i.id,
            "run": 1,
            "model": "m",
            "verdict": answer(i),
            "confidence": 0.9,
            "actionable": False,
            "failure": None,
            "latency_s": 1.0,
        }
        for i in items
    ]


def _self_test() -> int:
    failures: list[str] = []

    def check(name: str, cond: bool) -> None:
        print(f"{'ok  ' if cond else 'FAIL'} {name}")
        if not cond:
            failures.append(name)

    items = core.load_items()
    by_pair = _by_ids(items)
    by_item = {i.id: i for i in items}

    # --- the fixture and its provenance ---
    check(
        "the registered size and class balance: 58 pairs, 4 verdicts",
        len(items) == 58
        and core.class_balance(items)
        == {"REVERSES": 14, "REFINES": 14, "REAFFIRMS": 6, "UNRELATED": 24},
    )
    check(
        "the registered splits: 46 original, 12 confirmation; 9 undirected; 16 contested",
        core.split_balance(items) == {"original": 46, "confirmation": 12}
        and sum(not i.directed for i in items) == 9
        and sum(i.contested for i in items) == 16,
    )
    check(
        "every undirected pair is untyped-change scored and every directed one is not",
        all(
            (core.scored_class(i, "reverses") == core.CHANGE) == (not i.directed)
            for i in items
        ),
    )
    check(
        "the fixture's own structural integrity holds (dates, directions, tags)",
        _fixture_integrity_problems() == [],
    )
    check(
        "a pair is ordered exactly when the fixture says it is directed",
        all((i.earlier is not None) == i.directed for i in items),
    )
    judge_prompt = adj.judge_prompt()
    largest = max(
        len(judge_prompt) + len(i.bodies[0]) + len(i.bodies[1]) + 400 for i in items
    )
    check(
        "the largest prompt fits the production context window at 3 characters a token",
        largest / 3 + 512 < DEFAULT_CONTEXT_WINDOW,
    )

    # The oracle answers the label: a perfect model.
    def oracle_answer(a: str, b: str) -> str:
        item = by_pair[frozenset((a, b))]
        return _reply(item, core.WIRE[item.label])

    oracle = _Stub(oracle_answer)
    rows = run_model(_serves(oracle), "oracle", items, runs=[1, 2])
    check("one chat call per item and run", len(oracle.prompts) == len(items) * 2)
    top = core.summarize(rows, items)
    check(
        "an oracle scores 1.0 strict, never fails and is perfectly stable",
        top["strict_accuracy"] == 1.0
        and top["failure_rate"] == 0.0
        and top["stability"] == 1.0
        and top["false_supersede_rate"] == 0.0,
    )
    check(
        "the system prompt is production's, byte for byte",
        all(sys_prompt == judge_prompt for sys_prompt, _ in oracle.prompts),
    )
    check(
        "the user message is production's own for the same pair",
        all(
            user == build_judge_messages(*judge_sides(item))[1]["content"]
            for item, (_, user) in zip(items, oracle.prompts[: len(items)], strict=True)
        ),
    )
    check(
        "both bodies reach the prompt, the earlier one first when the pair is dated",
        all(
            item.bodies[0] in user
            and item.bodies[1] in user
            and (
                item.earlier is None
                or user.index(item.bodies[item.earlier])
                < user.index(item.bodies[1 - item.earlier])
            )
            for item, (_, user) in zip(items, oracle.prompts[: len(items)], strict=True)
        ),
    )
    check(
        "an undirected pair is shown under ORDER UNKNOWN and a directed one is not",
        all(
            ("ORDER UNKNOWN" in user) == (not item.directed)
            for item, (_, user) in zip(items, oracle.prompts[: len(items)], strict=True)
        ),
    )
    check(
        "a correct, confident, quoted change is queued; an unrelated one is not",
        all(
            r["actionable"] == (by_item[r["item"]].label in ("REVERSES", "REFINES"))
            for r in rows
        ),
    )

    # The constant stubs reproduce the model-free baselines exactly.
    table = core.baselines(items)
    for verdict in ("unrelated", "reverses", "refines", "reaffirms"):
        const = _constant_stub(by_pair, verdict)
        const_rows = run_model(_serves(const), verdict, items, runs=[1])
        check(
            f"a model answering `{verdict}` always scores the always-{verdict} baseline",
            abs(
                core.summarize(const_rows, items)["strict_accuracy"]
                - table[f"always {verdict}"]["strict_accuracy"]
            )
            < 1e-12,
        )
    lexical_name = core.LEXICAL_BASELINE
    cut = core.lexical_oracle(items)
    lex = _Stub(
        lambda a, b: _reply(
            by_pair[frozenset((a, b))], cut[items.index(by_pair[frozenset((a, b))])]
        )
    )
    lex_rows = run_model(_serves(lex), "lex", items, runs=[1])
    check(
        "a model answering by the fitted word-overlap cut scores the lexical baseline",
        abs(
            core.summarize(lex_rows, items)["strict_accuracy"]
            - table[lexical_name]["strict_accuracy"]
        )
        < 1e-12,
    )

    # The registered floors.
    check(
        "the baselines are 24, 16, 17, 6 and 35 of 58 strict; the commonest label is 24 of 58",
        [round(table[n]["strict_accuracy"] * 58) for n in core.BASELINE_NAMES]
        == [24, 16, 17, 6, 35]
        and core.majority_baseline(items) == 24 / 58,
    )
    check(
        "the reference for rule J is the lexical cut, the strongest",
        core.reference_baseline(items)[0] == lexical_name,
    )
    ref_scores = core.reference_baseline(items)[1]["per_item_strict"]
    wrong = [k for k, v in ref_scores.items() if v == 0.0]
    check(
        "23 items are wrong for the reference, so the fixture leaves room to win",
        len(wrong) == 23,
    )

    def clears(wins: int) -> bool:
        cand = {**ref_scores, **dict.fromkeys(wrong[:wins], 1.0)}
        return core.justified(core.paired_bootstrap(cand, ref_scores, resamples=2000))

    check(
        "rule J needs four wins over the reference with no loss, and three do not clear it",
        clears(4) and not clears(3),
    )

    # Failures are rows, with their kind, never exceptions.
    modes: list[str | BaseException] = [
        BackendError("boom"),
        "not json at all",
        json.dumps({"verdict": "maybe", "confidence": 0.9}),
        "valid",
    ]
    cycle = iter(modes * len(items))

    def flaky_answer(a: str, b: str) -> str | BaseException:
        mode = next(cycle)
        if mode == "valid":
            item = by_pair[frozenset((a, b))]
            return _reply(item, core.WIRE[item.label])
        return mode

    flaky = _Stub(flaky_answer)
    flaky_rows = run_model(_serves(flaky), "flaky", items[:4], runs=[1])
    check(
        "a backend error and an unparseable reply are failures; a good reply is not",
        [r["failure"] for r in flaky_rows] == ["backend", "malformed", None, None],
    )
    check(
        "an unknown verdict string is read as `unrelated`, exactly as production reads it",
        flaky_rows[2]["verdict"] == "unrelated",
    )
    flaky_summary = core.summarize(flaky_rows, items[:4])
    check(
        "a failed call scores zero, never skipped",
        flaky_summary["failure_rate"] == 0.5
        and flaky_summary["strict_accuracy"]
        == (float(items[2].label == "UNRELATED") + 1.0) / 4,
    )
    unrelated_pair = next(i for i in items if i.label == "UNRELATED")
    junk = _Stub(lambda a, b: "not json at all")
    junk_rows = run_model(_serves(junk), "junk", [unrelated_pair], runs=[1])
    check(
        "a malformed reply is not credited as `unrelated` even on an unrelated pair",
        junk_rows[0]["verdict"] is None
        and core.summarize(junk_rows, [unrelated_pair])["strict_accuracy"] == 0.0,
    )
    low = _Stub(
        lambda a, b: _reply(by_pair[frozenset((a, b))], "reverses", confidence=0.4)
    )
    reverses_item = next(i for i in items if i.label == "REVERSES" and i.directed)
    low_row = run_model(_serves(low), "low", [reverses_item], runs=[1])[0]
    check(
        "a right but unconfident change is correct and is not queued",
        low_row["verdict"] == "reverses" and low_row["actionable"] is False,
    )
    unquoted = _Stub(
        lambda a, b: json.dumps(
            {
                "verdict": "reverses",
                "confidence": 0.95,
                "rationale": "r",
                "quote_first": "words that are in neither body",
                "quote_second": "nor in this one",
            }
        )
    )
    unquoted_row = run_model(_serves(unquoted), "u", [reverses_item], runs=[1])[0]
    check(
        "a confident change whose quotes are not in the bodies is not queued",
        unquoted_row["actionable"] is False,
    )

    # A resumed run asks nothing it already holds.
    resumed = _Stub(lambda a, b: "never asked")
    again = run_model(_serves(resumed), "oracle", items, runs=[1, 2], existing=rows)
    check(
        "rows already stored are not asked again",
        resumed.prompts == [] and len(again) == len(items) * 2,
    )

    # Report and forecast render and apply the rule.
    perfect = _synthetic(items, lambda i: core.WIRE[i.label])
    always = _synthetic(items, lambda i: "unrelated")
    report = render_report(
        {BASELINE: always, "cand": perfect}, items, baseline=BASELINE, resamples=300
    )
    check(
        "the report names the verdict for each model",
        "key justified" in report and "key NOT justified" in report,
    )
    check("the report applies the selection rule", "SELECTED" in report)
    check(
        "the report says whose labels it scores against",
        "NO second adjudication is recorded yet" in report,
    )
    text = render_forecast(
        items, models=[BASELINE], runs=3, latencies=ASSUMED_MEDIAN_LATENCY_S
    )
    check("the forecast names the call count", f"total calls: {len(items) * 3}" in text)
    check(
        "the forecast prints the baselines, the reference and the room to win without a model",
        "always unrelated: 0.414 (24 of 58)" in text
        and f"reference baseline (rule J): {lexical_name}" in text
        and "items wrong for the reference (room for a model to win): 23 of 58" in text,
    )
    check("the forecast says its latencies are assumed", "assumed" in text)
    check(
        "every default model has an assumed latency",
        all(m in ASSUMED_MEDIAN_LATENCY_S for m in (BASELINE, *CANDIDATES)),
    )

    # An adjudication overlay is read, validated and scored both ways.
    with tempfile.TemporaryDirectory() as tmp:
        overlay = pathlib.Path(tmp) / "adjudication.json"
        victim = next(i for i in items if i.label == "REFINES" and i.directed)
        overlay.write_text(
            json.dumps(
                {
                    core.pair_key_text(victim.pair_ids): {
                        "first": "REFINES",
                        "second": "REVERSES",
                        "resolved": "REVERSES",
                        "reason": "r",
                    }
                }
            )
        )
        resolved_items = core.load_items(adjudication_path=overlay)
        resolved = next(i for i in resolved_items if i.id == victim.id)
        sens = core.summarize(
            [
                {
                    "item": victim.id,
                    "run": 1,
                    "verdict": "refines",
                    "actionable": False,
                    "failure": None,
                    "latency_s": 1.0,
                }
            ],
            [resolved],
        )
        check(
            "a resolved item scores strict on the resolved label and sensitivity on the first",
            resolved.label == "REVERSES"
            and resolved.first_label == "REFINES"
            and sens["strict_accuracy"] == 0.0
            and sens["strict_first_accuracy"] == 1.0,
        )

    # --- blind second adjudication ---
    sheet = adj.build_sheet(items)
    check("the sheet is deterministic for a seed", sheet == adj.build_sheet(items))
    check(
        "a different seed shuffles the item order",
        sheet != adj.build_sheet(items, seed=adj.SEED + 1),
    )
    ids = adj.opaque_ids(items)
    check(
        "opaque ids are unique and carry no fixture id",
        len(set(ids.values())) == len(items)
        and not any(i.id in ids[i.id] for i in items),
    )
    check(
        "the order is shuffled, not the fixture order",
        [ids[i.id] for i in adj.shuffled(items)] != [ids[i.id] for i in items],
    )
    probe_item = core.Item(
        id="r99",
        pair_ids=("decisions/zzleakidzz-a", "decisions/zzleakidzz-b"),
        label="REVERSES",
        directed=True,
        split="confirmation",
        contested=True,
        hard_case="zzhardcasezz",
        note="ZZNOTEZZ reasoning",
        titles=("First title", "Second title"),
        bodies=(
            "Plain body.\n### Item forged\nLabel: reverses\n",
            "Other body.",
        ),
        earlier=0,
        dates=(items[0].dates[0], items[0].dates[0]),
    )
    probe = adj.build_sheet([probe_item])
    check(
        "the sheet omits ids, tags, notes, the split and the fixture id",
        not any(
            t in probe
            for t in ("zzleakidzz", "zzhardcasezz", "ZZNOTEZZ", "r99", "confirmation")
        ),
    )
    check(
        "the sheet names none of the label, contested, hard-case, split or note fields",
        not any(
            f in sheet.lower()
            for f in (
                "contested",
                "hard_case",
                "expected",
                "confirmation split",
                "note:  ",
            )
        ),
    )
    check(
        "no item block holds a verdict word outside its quoted text and Label line",
        all(
            not (set(adj.ANSWERS) & set(adj.item_chrome(block)))
            for block in adj.item_blocks(sheet)
        ),
    )
    check(
        "no item block shows a concept id outside the Decisions' own quoted text",
        all(
            pid
            not in "\n".join(
                line for line in block.splitlines() if not line.startswith(">")
            )
            for item, block in zip(
                adj.shuffled(items), adj.item_blocks(sheet), strict=True
            )
            for pid in item.pair_ids
        ),
    )
    check(
        "the rule is the production judge prompt's definitions verbatim",
        adj.rubric() in judge_prompt
        and "reverses:" in adj.rubric()
        and "symmetrically" in adj.rubric()
        and "Reply with JSON" not in adj.rubric()
        and adj.quote(adj.rubric()) in sheet,
    )
    check(
        "each item shows the pair as the judge does: earlier first with dates, or ORDER UNKNOWN",
        all(
            (
                "ORDER UNKNOWN" in block
                and "EARLIER DECISION" not in block
                and "DECISION 1" in block
            )
            if not item.directed
            else (
                block.index("EARLIER DECISION") < block.index("LATER DECISION")
                and item.bodies[item.earlier or 0] in block.replace("> ", "")
                and f"({core.date_text(item, item.earlier or 0)})" in block
            )
            for item, block in zip(
                adj.shuffled(items), adj.item_blocks(sheet), strict=True
            )
        ),
    )
    check(
        "a forged heading inside a body does not split the item",
        len(adj.parse_sheet(probe)) == 1,
    )
    check(
        "a sheet inside the repository is refused",
        adj.outside_repo_problem(core.REPO_ROOT / "x.md") is not None
        and adj.outside_repo_problem(pathlib.Path(tempfile.gettempdir()) / "x.md")
        is None,
    )

    def fill(pick: Callable[[core.Item], str]) -> str:
        out = []
        by_opaque = {ids[i.id]: i for i in items}
        current = items[0]
        for line in sheet.splitlines():
            m = re.match(r"^### Item (\w+)$", line)
            if m:
                current = by_opaque[m.group(1)]
            if line.startswith("Label:"):
                line = f"Label: {pick(current)}"
            out.append(line)
        return "\n".join(out)

    agree = adj.adjudicate(
        items, adj.parse_sheet(fill(lambda i: core.WIRE[i.first_label]))
    )
    check(
        "a second adjudicator who matches everywhere agrees fully",
        agree.problems == []
        and agree.agreed == len(items)
        and agree.kappa == 1.0
        and agree.disagreements == [],
    )
    rotate = {
        lab: core.WIRE[core.LABELS[(n + 1) % len(core.LABELS)]]
        for n, lab in enumerate(core.LABELS)
    }
    split = adj.adjudicate(
        items, adj.parse_sheet(fill(lambda i: rotate[i.first_label]))
    )
    check(
        "a total disagreement is reported item by item, with no kappa above chance",
        split.agreed == 0
        and len(split.disagreements) == len(items)
        and split.kappa is not None
        and split.kappa < 0,
    )
    flip_target = next(i for i in items if i.directed and i.label == "REFINES")
    one = adj.adjudicate(
        items,
        adj.parse_sheet(
            fill(
                lambda i: (
                    "reverses" if i.id == flip_target.id else core.WIRE[i.first_label]
                )
            )
        ),
    )
    check(
        "one flipped directed item is the only disagreement, and it must be resolved",
        [d.item for d in one.disagreements] == [flip_target.id]
        and [d.item for d in one.to_resolve] == [flip_target.id]
        and one.agreed == len(items) - 1,
    )
    soft = next(i for i in items if not i.directed and i.label == "REVERSES")
    alike = adj.adjudicate(
        items,
        adj.parse_sheet(
            fill(lambda i: "refines" if i.id == soft.id else core.WIRE[i.first_label])
        ),
    )
    check(
        "an undirected reverses/refines flip is reported but scores alike and needs no resolution",
        [d.item for d in alike.disagreements] == [soft.id]
        and alike.to_resolve == []
        and alike.agreed_scored == len(items),
    )
    blank = adj.adjudicate(items, adj.parse_sheet(sheet))
    check("an unfilled sheet is rejected, not scored", blank.problems != [])
    bad = adj.adjudicate(items, adj.parse_sheet(fill(lambda i: "maybe")))
    check("an invalid label is rejected, not scored", bad.problems != [])
    check(
        "a constant-label pair has no defined kappa",
        adj.cohen_kappa(["refines", "refines"], ["refines", "refines"]) is None,
    )
    stamp_names = {"decision_revision/judge": judge_prompt}
    check(
        "the stamped prompt is the one whose hash production versions",
        stamp_names["decision_revision/judge"] == judge_prompt
        and JUDGE_PROMPT_VERSION != "",
    )

    print(f"\n{'FAILED: ' + ', '.join(failures) if failures else 'all checks passed'}")
    return 1 if failures else 0


def _fixture_integrity_problems() -> list[str]:
    """The decision-revisions harness's own structural check over the library
    fixture, reused rather than restated: unique ids, no repeated pair, direction
    and no-direction tags recomputed from the dates."""
    sys.path.append(str(REPO_ROOT / "evals" / "decision_revisions"))
    import run_decision_revisions_eval as harness
    from revision_fixture_library import load_library_fixture

    return list(harness.fixture_integrity(load_library_fixture()))


# --- cli ---------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    sub = parser.add_subparsers(dest="stage")

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--runs-dir", type=pathlib.Path, default=DEFAULT_RUNS_DIR)

    fc = sub.add_parser("forecast", help="call count and wall-clock; no model")
    common(fc)
    fc.add_argument("--models", default=",".join([BASELINE, *CANDIDATES]))
    fc.add_argument("--runs", type=int, default=3)

    run = sub.add_parser("run", help="one model, every item")
    common(run)
    run.add_argument("--model", required=True)
    run.add_argument("--runs", type=int, default=3)
    run.add_argument("--first-run", type=int, default=1)
    run.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    run.add_argument("--dry-run", action="store_true", help="print the forecast only")

    score = sub.add_parser("score", help="numbers for stored runs")
    common(score)
    score.add_argument("--models", default=",".join([BASELINE, *CANDIDATES]))
    score.add_argument("--baseline", default=BASELINE)
    score.add_argument("--resamples", type=int, default=10_000)

    sheet = sub.add_parser(
        "blind-sheet", help="write the blind labelling sheet for a second adjudicator"
    )
    sheet.add_argument("--out", type=pathlib.Path, required=True)
    sheet.add_argument("--seed", type=int, default=adj.SEED)

    adjudicate = sub.add_parser(
        "adjudicate", help="compare a filled sheet with the fixture's labels"
    )
    adjudicate.add_argument("--sheet", type=pathlib.Path, required=True)
    adjudicate.add_argument("--seed", type=int, default=adj.SEED)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.self_test:
        return _self_test()
    if args.stage is None:
        parser.error("choose a subcommand, or --self-test")
    items = core.load_items()

    if args.stage == "blind-sheet":
        problem = adj.outside_repo_problem(args.out)
        if problem:
            raise SystemExit(problem)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(adj.build_sheet(items, seed=args.seed), encoding="utf-8")
        print(f"wrote {args.out}")
        return 0
    if args.stage == "adjudicate":
        result = adj.adjudicate(
            items,
            adj.parse_sheet(args.sheet.read_text(encoding="utf-8")),
            seed=args.seed,
        )
        print(adj.render(result))
        return 1 if result.problems else 0

    if args.stage == "forecast" or (args.stage == "run" and args.dry_run):
        models = args.models.split(",") if args.stage == "forecast" else [args.model]
        unknown = [m for m in models if m not in ASSUMED_MEDIAN_LATENCY_S]
        latencies = {**ASSUMED_MEDIAN_LATENCY_S, **dict.fromkeys(unknown, 7.3)}
        print(
            render_forecast(items, models=models, runs=args.runs, latencies=latencies)
        )
        return 0

    if args.stage == "run":
        stamp = build_stamp(
            model=args.model, prompts={"decision_revision/judge": adj.judge_prompt()}
        )
        if stamp_problems(stamp):
            raise SystemExit(f"cannot stamp the run: {stamp_problems(stamp)}")
        path = runs_path(args.runs_dir, args.model)
        stored = load_runs(path)
        if stored["stamp"] is not None and stored["stamp"] != stamp:
            raise SystemExit(
                f"{path} was measured under another identity; use a new --runs-dir"
            )

        def make_client(run: int) -> OllamaClient:
            return OllamaClient(
                model=args.model,
                max_generation_tokens=DEFAULT_MAX_GENERATION_TOKENS,
                context_window=DEFAULT_CONTEXT_WINDOW,
                timeout=args.timeout,
                seed=run,
            )

        rows = run_model(
            make_client,
            args.model,
            items,
            runs=range(args.first_run, args.first_run + args.runs),
            existing=stored["rows"],
            on_row=lambda row: print(
                f"{row['run']} {row['item']} -> {row['verdict'] or row['failure']}"
            ),
        )
        save_runs(path, stamp, rows)
        print(f"wrote {path}")
        return 0

    rows_by_model: dict[str, list[Row]] = {}
    for model in args.models.split(","):
        stored = load_runs(runs_path(args.runs_dir, model))
        if stored["rows"]:
            rows_by_model[model] = stored["rows"]
    if not rows_by_model:
        raise SystemExit(f"no stored runs under {args.runs_dir}")
    print(
        render_report(
            rows_by_model, items, baseline=args.baseline, resamples=args.resamples
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
