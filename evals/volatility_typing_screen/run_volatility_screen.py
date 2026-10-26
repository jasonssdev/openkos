"""Volatility-typing model screen (#1563): forecast, run, score.

`volatility_typing` asks a model, once per concept TYPE in a bundle, whether
the knowledge in that type goes stale `static`, `slow` or `volatile`
(`resolution/volatility_typing.py`). The task key had no harness; this is it.
Each fixture item is a one-type bundle put through production's own
`suggest_volatility`, so the sampling, the truncation, the prompt and the
fail-closed parser measured are the shipped ones.

Subcommands:

- `forecast`  the call count and Ollama wall-clock a run would cost, and the
              model-free floors (the registry-default predictor, the majority
              class). Never contacts a model. `run --dry-run` is the same.
- `run`       one model, every item, every arm, N seeded runs; cached by row.
- `score`     the registered numbers and decision for the stored runs.

Read `README.md` (the fixture's labels are one author's and unadjudicated) and
`PREREGISTRATION-DRAFT.md` (a DRAFT, not a registration) before trusting any
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

import screen_core as core  # noqa: E402
from harness_stamp import (  # noqa: E402
    TimedBackend,
    build_stamp,
    stamp_problems,
)

from openkos import lint  # noqa: E402
from openkos.config import (  # noqa: E402
    DEFAULT_CONTEXT_WINDOW,
    DEFAULT_MAX_GENERATION_TOKENS,
)
from openkos.llm.base import BackendError, is_timeout_failure  # noqa: E402
from openkos.llm.ollama import OllamaClient  # noqa: E402
from openkos.model import types  # noqa: E402
from openkos.resolution import volatility_typing as vt  # noqa: E402

DEFAULT_RUNS_DIR: Final = (
    pathlib.Path.home() / "Dev/Projects/openkos-eval-runs/volatility-screen"
)
BASELINE: Final = "qwen3:8b"
CANDIDATES: Final = ("gemma4:26b-a4b", "qwen3:14b", "qwen3.6:27b", "qwen3.6:35b-a3b")
DEFAULT_TIMEOUT: Final = 120.0
LOAD_S: Final = 60.0
ASSUMED_MEDIAN_LATENCY_S: Final[dict[str, float]] = {
    "qwen3:8b": 4.75,
    "gemma4:26b-a4b": 2.83,
    "qwen3:14b": 6.57,
    "qwen3.6:27b": 6.57,
    "qwen3.6:35b-a3b": 3.22,
}
"""ASSUMED per-call latencies, NOT measured for this task: the answer-call
medians #1560 recorded (`ask_model_screen/PREREGISTRATION-1596.md`), with
`qwen3.6:27b` borrowing `qwen3:14b`'s because it has none. This task's reply is
a short JSON object over at most five 1,000-character bodies, so these are
probably high; a stage-0 timing of three calls per model replaces them before
any registered forecast."""

Row = dict[str, Any]


# --- one call ----------------------------------------------------------------


def run_item(
    llm: Any, item: core.Item, arm: str, scratch: pathlib.Path
) -> tuple[Row, str | None]:
    """One production `suggest_volatility` over the item's one-type bundle.

    Returns the row without model/run fields and the rationale (kept for
    reading, never scored). A failed call is a row, never an exception: a
    transport failure is `backend` (`timeout` if it ran out of time), a reply
    production's parser refused is `invalid_reply`."""
    bundle = core.write_item_bundle(scratch, item)
    type_tiers = {item.type: core.anchor_tier(item)} if arm == "wrong-anchor" else None
    timed = TimedBackend(llm)
    batch = vt.suggest_volatility(bundle, llm=timed, type_tiers=type_tiers)
    latency = timed.call_latencies_s[-1] if timed.call_latencies_s else 0.0
    row: Row = {
        "item": item.id,
        "arm": arm,
        "tier": None,
        "failure": None,
        "latency_s": latency,
    }
    rationale: str | None = None
    if batch.failure is not None:
        row["failure"] = "timeout" if is_timeout_failure(batch.failure) else "backend"
    elif not batch.results:
        row["failure"] = "no_question"
    else:
        suggestion = batch.results[0]
        rationale = suggestion.rationale
        if suggestion.suggested_tier is None:
            row["failure"] = "invalid_reply"
        else:
            row["tier"] = suggestion.suggested_tier
    return row, rationale


def run_model(
    make_client: Callable[[int], Any],
    model: str,
    items: Sequence[core.Item],
    *,
    arms: Sequence[str],
    runs: Sequence[int],
    existing: Sequence[Row] = (),
    on_row: Callable[[Row], None] | None = None,
) -> list[Row]:
    """Every (run, arm, item) not already in `existing`, in a fixed order. The
    cache key is the row's identity, so an interrupted run resumes and a
    finished one costs nothing."""
    rows = list(existing)
    done = {(r["run"], r["arm"], r["item"]) for r in rows}
    with tempfile.TemporaryDirectory(prefix="vt-screen-") as scratch:
        for run in runs:
            client = make_client(run)
            for arm in arms:
                for item in items:
                    if (run, arm, item.id) in done:
                        continue
                    row, rationale = run_item(client, item, arm, pathlib.Path(scratch))
                    row.update(model=model, run=run, rationale=rationale)
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


def render_forecast(
    items: Sequence[core.Item],
    *,
    models: Sequence[str],
    arms: Sequence[str],
    runs: int,
    latencies: Mapping[str, float],
) -> str:
    balance = core.class_balance(items)
    registry = core.registry_baseline(items)
    plan = core.forecast(
        n_items=len(items),
        arms=len(arms),
        runs=runs,
        median_latency_s={m: latencies[m] for m in models},
        load_s=LOAD_S,
    )
    lines = [
        "# Forecast (no model contacted)",
        "",
        f"items: {len(items)}  class balance: {balance}  "
        f"ambiguous: {sum(i.ambiguous for i in items)}",
        f"arms: {list(arms)}  runs: {runs}  models: {list(models)}",
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
        "model-free floors on this fixture (strict / lenient):",
        f"  always the registry default: {registry['strict_accuracy']:.3f} / "
        f"{registry['lenient_accuracy']:.3f}",
        f"  always the commonest label:  {core.majority_baseline(items):.3f}",
    ]
    return "\n".join(lines)


# --- score -------------------------------------------------------------------


def _fmt(value: float | None, digits: int = 3) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def render_report(
    rows_by_model: Mapping[str, Sequence[Row]],
    items: Sequence[core.Item],
    *,
    baseline: str,
    resamples: int = 10_000,
) -> str:
    """The registered numbers for stored runs. Descriptive: the verdict lines
    apply the DRAFT rule mechanically and adopt nothing."""
    registry = core.registry_baseline(items)
    lines = [
        "# Volatility-typing screen",
        "",
        "Labels are one author's and unadjudicated: read every number as a",
        "measurement against that author, not as accuracy against a truth.",
        "",
        f"floors (strict): registry default {_fmt(registry['strict_accuracy'])}, "
        f"commonest label {_fmt(core.majority_baseline(items))}",
        "",
        "| model | strict | lenient | failures | median s | p95 s | anchor follow | stability |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    summaries: dict[str, dict[str, dict[str, Any]]] = {}
    for model, rows in rows_by_model.items():
        summaries[model] = {arm: core.summarize(rows, items, arm) for arm in core.ARMS}
        d, w = summaries[model]["default"], summaries[model]["wrong-anchor"]
        lines.append(
            f"| {model} | {_fmt(d['strict_accuracy'])} | {_fmt(d['lenient_accuracy'])} "
            f"| {_fmt(d['failure_rate'])} | {_fmt(d['median_latency_s'], 2)} "
            f"| {_fmt(d['p95_latency_s'], 2)} | {_fmt(w['anchor_follow_rate'])} "
            f"| {_fmt(d['stability'])} |"
        )
    for model, by_arm in summaries.items():
        lines += ["", f"## {model}: per-class recall and confusion (default arm)", ""]
        recall = by_arm["default"]["per_class_recall"]
        lines.append(
            "recall: " + ", ".join(f"{t} {_fmt(recall[t])}" for t in core.TIERS)
        )
        lines.append("| gold \\ answered | static | slow | volatile | none |")
        lines.append("| --- | --- | --- | --- | --- |")
        for gold in core.TIERS:
            cells = by_arm["default"]["confusion"][gold]
            lines.append(
                f"| {gold} | "
                + " | ".join(str(cells[c]) for c in (*core.TIERS, "none"))
                + " |"
            )
        lines.append(
            f"agrees with the registry default: "
            f"{_fmt(by_arm['default']['registry_agreement'])}"
        )
    lines += ["", "## Decision (DRAFT rule, applied mechanically)", ""]
    for model, rows in rows_by_model.items():
        own = core.per_item_strict(rows, items, "default")
        gap = core.paired_bootstrap(
            own, registry["per_item_strict"], resamples=resamples
        )
        verdict = f"- {model}: gain over the registry default {_fmt(gap['point'])} "
        verdict += f"[{_fmt(gap['lo'])}, {_fmt(gap['hi'])}] -> "
        verdict += "key justified" if core.justified(gap) else "key NOT justified"
        lines.append(verdict)
        if model == baseline or baseline not in rows_by_model:
            continue
        base_rows = rows_by_model[baseline]
        delta = core.paired_bootstrap(
            own,
            core.per_item_strict(base_rows, items, "default"),
            resamples=resamples,
        )
        tripped = core.vetoes(
            summaries[model]["default"],
            summaries[baseline]["default"],
            summaries[model]["wrong-anchor"],
            summaries[baseline]["wrong-anchor"],
        )
        lines.append(
            f"  vs {baseline}: delta {_fmt(delta['point'])} "
            f"[{_fmt(delta['lo'])}, {_fmt(delta['hi'])}], vetoes {tripped or 'none'} "
            f"-> {'SELECTED' if core.selected(delta, tripped) else 'not selected'}"
        )
    return "\n".join(lines)


# --- self-test ---------------------------------------------------------------


class _Stub:
    """A scripted `LLMBackend`: answers per question from the prompt it was sent."""

    def __init__(self, answer: Callable[[str, str, str], str | BaseException]) -> None:
        self.answer = answer
        self.prompts: list[tuple[str, str]] = []

    def chat(self, messages: Sequence[Mapping[str, str]]) -> str:
        user = messages[-1]["content"]
        type_name = re.search(r"^TYPE: (.+)$", user, re.M)
        default = re.search(r"^CURRENT DEFAULT TIER: (.*)$", user, re.M)
        if type_name is None or default is None:
            raise ValueError(f"not a volatility prompt: {user[:80]!r}")
        self.prompts.append((messages[0]["content"], user))
        reply = self.answer(type_name.group(1), default.group(1), user)
        if isinstance(reply, BaseException):
            raise reply
        return reply


def _json(tier: str) -> str:
    return json.dumps({"tier": tier, "rationale": "stub"})


def _self_test() -> int:
    failures: list[str] = []

    def check(name: str, cond: bool) -> None:
        print(f"{'ok  ' if cond else 'FAIL'} {name}")
        if not cond:
            failures.append(name)

    items = core.load_fixture()
    check(
        "the fixture loads and has every tier", all(core.class_balance(items).values())
    )
    check(
        "every doc is a verbatim quotation of its source",
        core.provenance_problems(items) == [],
    )
    check(
        "no item shows more docs than production samples",
        all(len(i.docs) <= vt.N_SAMPLE_CONCEPTS for i in items),
    )
    cut = sorted(
        f"{i.id}/{d.id}"
        for i in items
        for d in i.docs
        if len(f"# {d.title}\n\n{d.text}\n") > vt.M_TRUNCATE_CHARS
    )
    check(
        "only the two docs whose Related/Citations tail production cuts are over its limit",
        cut == ["v01/stoicism", "v02/frame-the-essay-on-the-dichotomy-of-control"],
    )

    with tempfile.TemporaryDirectory() as scratch_name:
        scratch = pathlib.Path(scratch_name)

        # The oracle answers the label: a perfect model.
        oracle = _Stub(
            lambda t, d, u: _json(
                next(i.label for i in items if i.type == t and _same(i, u))
            )
        )
        rows = run_model(
            lambda run: oracle, "oracle", items, arms=core.ARMS, runs=[1, 2]
        )
        check(
            "one chat call per item, arm and run",
            len(oracle.prompts) == len(items) * 2 * 2,
        )
        d = core.summarize(rows, items, "default")
        check(
            "an oracle scores 1.0 strict",
            d["strict_accuracy"] == 1.0 and d["failure_rate"] == 0.0,
        )
        check(
            "an oracle has no anchor following",
            core.summarize(rows, items, "wrong-anchor")["anchor_follow_rate"] == 0.0,
        )

        # The prompt shows the registry default, or the wrong anchor on its arm.
        seen_default = {
            (m.group(1), m.group(2))
            for _, u in oracle.prompts[: len(items)]
            if (m := re.search(r"TYPE: (.+)\nCURRENT DEFAULT TIER: (.*)\n", u))
        }
        check(
            "the default arm shows the registry default",
            all(types.TYPE_TO_DEFAULT_VOLATILITY[t] == s for t, s in seen_default),
        )
        wrong_prompts = oracle.prompts[len(items) : 2 * len(items)]
        check(
            "the wrong-anchor arm shows a tier that is neither label nor alternative",
            all(
                f"CURRENT DEFAULT TIER: {core.anchor_tier(item)}\n" in u
                for item, (_, u) in zip(items, wrong_prompts, strict=True)
            ),
        )
        check(
            "every doc's title reaches the prompt",
            all(
                d.title in u
                for item, (_, u) in zip(
                    items, oracle.prompts[: len(items)], strict=True
                )
                for d in item.docs
            ),
        )

        # An echo model copies the shown tier: the registry predictor on the default
        # arm, and a pure anchor follower on the other.
        echo = _Stub(lambda t, d, u: _json(d))
        rows = run_model(lambda run: echo, "echo", items, arms=core.ARMS, runs=[1])
        registry = core.registry_baseline(items)
        check(
            "an echo model scores exactly the registry predictor",
            abs(
                core.summarize(rows, items, "default")["strict_accuracy"]
                - registry["strict_accuracy"]
            )
            < 1e-12,
        )
        check(
            "an echo model follows every anchor",
            core.summarize(rows, items, "wrong-anchor")["anchor_follow_rate"] == 1.0,
        )
        check(
            "an echo model agrees with the registry on the default arm",
            core.summarize(rows, items, "default")["registry_agreement"] == 1.0,
        )

        # Failures are rows, with their kind, never exceptions.
        modes: list[str | BaseException] = [
            BackendError("boom"),
            "not json at all",
            _json("forever"),
            _json("slow"),
        ]
        cycle = iter(modes * len(items))
        flaky = _Stub(lambda t, d, u: next(cycle))
        rows = run_model(
            lambda run: flaky, "flaky", items[:4], arms=["default"], runs=[1]
        )
        kinds = [r["failure"] for r in rows]
        check(
            "a backend error, an unparseable reply and an invalid tier are distinct failures",
            kinds == ["backend", "invalid_reply", "invalid_reply", None],
        )
        check(
            "a failed call scores zero, never skipped",
            core.summarize(rows, items[:4], "default")["failure_rate"] == 0.75,
        )

        # A resumed run asks nothing it already holds.
        resumed = _Stub(lambda t, d, u: _json("slow"))
        again = run_model(
            lambda run: resumed,
            "oracle",
            items,
            arms=core.ARMS,
            runs=[1, 2],
            existing=rows_for(items, [1, 2]),
        )
        check(
            "rows already stored are not asked again",
            resumed.prompts == [] and len(again) == len(items) * 4,
        )
        bundle = core.write_item_bundle(scratch, items[3])
        collected, notices = lint.collect_docs(bundle)
        check(
            "an item's bundle is read back by production as exactly its docs",
            notices == []
            and {d.type for d in collected} == {items[3].type}
            and len(collected) == len(items[3].docs),
        )

    # Report and forecast render and apply the rule.
    perfect = _synthetic(items, lambda i: i.label)
    echoing = _synthetic(items, lambda i: i.registry_default)
    report = render_report(
        {BASELINE: echoing, "cand": perfect}, items, baseline=BASELINE, resamples=300
    )
    check(
        "the report names the verdict for each model",
        "key justified" in report and "key NOT justified" in report,
    )
    check("the report applies the selection rule", "SELECTED" in report)
    text = render_forecast(
        items,
        models=[BASELINE],
        arms=core.ARMS,
        runs=3,
        latencies=ASSUMED_MEDIAN_LATENCY_S,
    )
    check(
        "the forecast names the call count",
        f"total calls: {len(items) * 2 * 3}" in text,
    )
    check(
        "the forecast prints the floors without a model",
        "always the registry default" in text,
    )
    check("the forecast says its latencies are assumed", "assumed" in text)

    print(f"\n{'FAILED: ' + ', '.join(failures) if failures else 'all checks passed'}")
    return 1 if failures else 0


def _same(item: core.Item, user: str) -> bool:
    return all(d.title in user for d in item.docs)


def rows_for(items: Sequence[core.Item], runs: Sequence[int]) -> list[Row]:
    return [
        {
            "item": i.id,
            "arm": arm,
            "run": run,
            "model": "oracle",
            "tier": "slow",
            "failure": None,
            "latency_s": 1.0,
            "rationale": None,
        }
        for run in runs
        for arm in core.ARMS
        for i in items
    ]


def _synthetic(
    items: Sequence[core.Item], answer: Callable[[core.Item], str]
) -> list[Row]:
    return [
        {
            "item": i.id,
            "arm": arm,
            "run": 1,
            "model": "m",
            "tier": answer(i),
            "failure": None,
            "latency_s": 1.0,
        }
        for arm in core.ARMS
        for i in items
    ]


# --- cli ---------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    sub = parser.add_subparsers(dest="stage")

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--runs-dir", type=pathlib.Path, default=DEFAULT_RUNS_DIR)
        p.add_argument("--arms", default=",".join(core.ARMS))

    fc = sub.add_parser("forecast", help="call count and wall-clock; no model")
    common(fc)
    fc.add_argument("--models", default=",".join([BASELINE, *CANDIDATES]))
    fc.add_argument("--runs", type=int, default=3)

    run = sub.add_parser("run", help="one model, every item and arm")
    common(run)
    run.add_argument("--model", required=True)
    run.add_argument("--runs", type=int, default=3)
    run.add_argument("--first-run", type=int, default=1)
    run.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    run.add_argument("--dry-run", action="store_true", help="print the forecast only")

    score = sub.add_parser("score", help="registered numbers for stored runs")
    common(score)
    score.add_argument("--models", default=",".join([BASELINE, *CANDIDATES]))
    score.add_argument("--baseline", default=BASELINE)
    score.add_argument("--resamples", type=int, default=10_000)
    return parser


def _arms(raw: str, parser: argparse.ArgumentParser) -> list[str]:
    arms = [a for a in raw.split(",") if a]
    if not arms or not set(arms) <= set(core.ARMS):
        parser.error(f"--arms takes {core.ARMS}")
    return arms


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.self_test:
        return _self_test()
    if args.stage is None:
        parser.error("choose a subcommand, or --self-test")
    items = core.load_fixture()
    arms = _arms(args.arms, parser)

    if args.stage == "forecast" or (args.stage == "run" and args.dry_run):
        models = args.models.split(",") if args.stage == "forecast" else [args.model]
        unknown = [m for m in models if m not in ASSUMED_MEDIAN_LATENCY_S]
        latencies = {**ASSUMED_MEDIAN_LATENCY_S, **dict.fromkeys(unknown, 6.57)}
        print(
            render_forecast(
                items, models=models, arms=arms, runs=args.runs, latencies=latencies
            )
        )
        return 0

    if args.stage == "run":
        problems = core.provenance_problems(items)
        if problems:
            raise SystemExit(
                "fixture drifted from its sources:\n" + "\n".join(problems)
            )
        stamp = build_stamp(
            model=args.model, prompts={"volatility_typing/system": vt._SYSTEM_PROMPT}
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
            arms=arms,
            runs=range(args.first_run, args.first_run + args.runs),
            existing=stored["rows"],
            on_row=lambda row: print(
                f"{row['run']} {row['arm']} {row['item']} -> {row['tier'] or row['failure']}"
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
