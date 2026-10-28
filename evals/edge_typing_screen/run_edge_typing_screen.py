"""Edge-typing model screen (#1563): forecast, run, score.

`edge_typing` asks a model, once per untyped body-link edge, which relation type
describes how SOURCE relates to TARGET, with `related_to` as the honest
abstention (`resolution/edge_typing.py`). The task key has a harness
(`evals/edge_typing/`) but no pre-registered, multi-model, baseline-referenced
measurement; this is that screen. Each fixture item is a two-document bundle put
through production's own `suggest_edge_types`, so the prompt, the parser and the
two direction checks measured are the shipped ones.

Subcommands:

- `forecast`  the call count and Ollama wall-clock a run would cost, and the
              model-free baselines. Never contacts a model. `run --dry-run` is
              the same.
- `stage0`    warm-up and timed calls per model on calibration inputs that
              are not fixture items; contacts Ollama, scores nothing.
- `run`       one model, every item, N seeded runs; cached by row.
- `score`     the numbers and the draft decision rule for the stored runs.
- `blind-sheet` / `adjudicate`  the blind second adjudication: write the sheet a
              second labeller fills in, then compare it with the fixture's
              labels. No model; see `README.md`, "Second adjudication".

STATUS: `PREREGISTRATION.md` registers this screen when it merges, before any
scored call; Stage 0 (`stage0`) has run and no scored call has. Read `README.md`
before trusting any number. Model-free check: `--self-test`.
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

import edge_blind_adjudication as adj  # noqa: E402
import edge_screen_core as core  # noqa: E402
import stage0_timing as s0  # noqa: E402
from harness_stamp import (  # noqa: E402
    TimedBackend,
    build_stamp,
    stamp_problems,
)

from openkos.config import (  # noqa: E402
    DEFAULT_CONTEXT_WINDOW,
    DEFAULT_MAX_GENERATION_TOKENS,
)
from openkos.graph.base import Edge  # noqa: E402
from openkos.llm.base import BackendError, is_timeout_failure  # noqa: E402
from openkos.llm.ollama import OllamaClient  # noqa: E402
from openkos.model import okf  # noqa: E402
from openkos.resolution import edge_typing as et  # noqa: E402

DEFAULT_RUNS_DIR: Final = (
    pathlib.Path.home() / "Dev/Projects/openkos-eval-runs/edge-typing-screen"
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
    "qwen3:8b": 4.75,
    "gemma4:26b-a4b": 2.83,
    "qwen3:14b": 6.57,
    "qwen3.6:27b": 6.57,
    "qwen3.6:35b-a3b": 3.22,
    "gemma2:27b": 6.57,
}
"""ASSUMED per-call latencies, NOT measured for this task: the answer-call
medians #1560 recorded, with `qwen3.6:27b` and `gemma2:27b` (a candidate by
the owner's decision of 2026-10-27) borrowing `qwen3:14b`'s because they have
none (the volatility screen's table). `evals/edge_typing/README.md` measured
`qwen3:8b` at about 1.3 s per edge on 145-character documents, so these are
probably high for the small documents and about right for the ADR pairs (up to
12,000 characters); a stage-0 timing of three calls per model replaces them
before any registered forecast."""

Row = dict[str, Any]


# --- one call ----------------------------------------------------------------


def run_item(
    llm: Any, item: core.Item, arm: str, scratch: pathlib.Path
) -> tuple[Row, str | None]:
    """One production `suggest_edge_types` over the item's two-document bundle.

    Returns the row without model/run fields and the rationale (kept for
    reading, never scored). A failed call is a row, never an exception: a
    transport failure is `backend` (`timeout` if it ran out of time), a reply
    production's parser refused is `invalid_reply`, and a reply naming a type
    outside the suggestable vocabulary (production accepts any non-blank string)
    is `out_of_vocab` with the string kept as `raw_type`."""
    bundle = core.write_item_bundle(scratch, item)
    timed = TimedBackend(llm)
    batch = et.suggest_edge_types(
        [Edge(source_id=item.source.id, target_id=item.target.id)],
        bundle_dir=bundle,
        llm=timed,
    )
    latency = timed.call_latencies_s[-1] if timed.call_latencies_s else 0.0
    row: Row = {
        "item": item.id,
        "arm": arm,
        "type": None,
        "raw_type": None,
        "corrected": False,
        "withdrawn": False,
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
        row["corrected"] = suggestion.corrected_edge is not None
        row["withdrawn"] = et.DIRECTION_WITHDRAWN_NOTE in suggestion.rationale
        if suggestion.suggested_type is None:
            row["failure"] = "invalid_reply"
        elif suggestion.suggested_type not in core.TYPES:
            row["failure"] = "out_of_vocab"
            row["raw_type"] = suggestion.suggested_type
        else:
            row["type"] = suggestion.suggested_type
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
    with tempfile.TemporaryDirectory(prefix="et-screen-") as scratch:
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


# --- stage 0 -----------------------------------------------------------------

STAGE0_SMALL_CALLS: Final = 3
STAGE0_TIMEOUT: Final = 240.0
"""Stage 0 measures slow models rather than cutting them off at the screen's
own timeout, which Stage 0 is what sets."""
STAGE0_ADR_IDS: Final = (
    "0025-temporal-direction-never-comes-from-the-model",
    "0044-the-quiet-engine-arc-precedes-interoperability",
)
"""Two ADRs that are not in the fixture (10.4 KB together; the largest scored
pair is about 12 KB), so the large calibration call is ADR-sized and scores
nothing."""


def calibration_items(
    repo_root: pathlib.Path = REPO_ROOT,
) -> tuple[core.Item, core.Item]:
    """`(small, large)`: two items that are in no way scored items.

    The small one is a pair of 100-200 character concept documents like the
    constructed stratum; the large one is a pair of ADRs outside the fixture, like
    the ADR stratum. Neither id is a fixture id, and `--self-test` pins that."""

    def doc(id_: str, type_: str, title: str, text: str) -> core.Doc:
        return core.Doc(
            id=id_, type=type_, title=title, text=text, language="en", source={}
        )

    small = core.Item(
        id="stage0-small",
        stratum="calibration",
        source=doc(
            "concepts/sourdough-starter",
            "Concept",
            "Sourdough Starter",
            "# Sourdough Starter\n\nA sourdough starter is a live culture of flour and "
            "water. The loaf recipe uses it in place of commercial yeast.",
        ),
        target=doc(
            "concepts/loaf-recipe",
            "Concept",
            "Loaf Recipe",
            "# Loaf Recipe\n\nThe loaf recipe makes one country loaf from flour, water, "
            "salt and a ripe starter.",
        ),
        label="references",
        alt_label=None,
        trap_type=None,
        how="calibration only; never scored",
    )
    adrs = []
    for stem in STAGE0_ADR_IDS:
        text = (repo_root / "docs" / "adr" / f"{stem}.md").read_text(encoding="utf-8")
        adrs.append(
            doc(
                f"decisions/{stem}",
                "Decision",
                text.splitlines()[0].lstrip("# ").strip(),
                text,
            )
        )
    large = core.Item(
        id="stage0-large",
        stratum="calibration",
        source=adrs[0],
        target=adrs[1],
        label="references",
        alt_label=None,
        trap_type=None,
        how="calibration only; never scored",
    )
    return small, large


def stage0_model(model: str, runs_dir: pathlib.Path) -> dict[str, Any]:
    """Warm-up, then 3 timed small calls and 1 timed large call, seed 1, on the
    calibration items. Returns the stamp (digest) and the latencies."""
    small, large = calibration_items()
    stamp = build_stamp(model=model, prompts={"edge_typing/system": et._SYSTEM_PROMPT})
    if stamp_problems(stamp):
        raise SystemExit(f"cannot stamp {model}: {stamp_problems(stamp)}")
    client = OllamaClient(
        model=model,
        max_generation_tokens=DEFAULT_MAX_GENERATION_TOKENS,
        context_window=DEFAULT_CONTEXT_WINDOW,
        timeout=STAGE0_TIMEOUT,
        seed=1,
    )
    with tempfile.TemporaryDirectory(prefix="et-stage0-") as scratch:
        root = pathlib.Path(scratch)
        warm, _ = run_item(client, small, "default", root)
        rows = [
            run_item(client, small, "default", root)[0]
            for _ in range(STAGE0_SMALL_CALLS)
        ]
        big, _ = run_item(client, large, "default", root)
    failures = [r["failure"] for r in [warm, *rows, big] if r["failure"]]
    return {
        "model": model,
        "digest": stamp["model"]["digest"],
        "stamp": stamp,
        "warmup_s": warm["latency_s"],
        "small_s": [r["latency_s"] for r in rows],
        "large_s": [big["latency_s"]],
        "failures": failures,
        "large_chars": len(large.source.text) + len(large.target.text),
    }


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
    arms: Sequence[str],
    runs: int,
    latencies: Mapping[str, float],
) -> str:
    plan = core.forecast(
        n_items=len(items),
        arms=len(arms),
        runs=runs,
        median_latency_s={m: latencies[m] for m in models},
        load_s=LOAD_S,
    )
    table = core.baselines(items)
    reference, _ = core.reference_baseline(items)
    nobody = sum(
        not any(table[n]["per_item_strict"][i.id] for n in core.BASELINE_NAMES)
        for i in items
    )
    lines = [
        "# Forecast (no model contacted)",
        "",
        f"items: {len(items)}  strata: {core.stratum_balance(items)}  "
        f"ambiguous: {sum(i.ambiguous for i in items)}  "
        f"reversed probes: {sum(i.trap_type is not None for i in items)}",
        f"class balance: {core.class_balance(items)}",
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
        "model-free baselines on this fixture (strict / lenient):",
    ]
    for name in core.BASELINE_NAMES:
        hits = round(table[name]["strict_accuracy"] * len(items))
        lines.append(
            f"  {name}: {table[name]['strict_accuracy']:.3f} "
            f"({hits} of {len(items)}) / {table[name]['lenient_accuracy']:.3f}"
        )
    lines += [
        f"  commonest label: {core.majority_baseline(items):.3f}",
        f"reference baseline (rule J): {reference}",
        f"items no model-free baseline gets right (strict): {nobody} of {len(items)}",
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
        "# Edge-typing screen",
        "",
        "Labels: the first author's"
        + (
            f", with {adjudicated} disagreement(s) adjudicated."
            if adjudicated
            else "; NO second adjudication is recorded yet. Read every number as a"
            " measurement against one person's labels."
        ),
        "`strict` and `lenient` decide; `strict (1st)` is the sensitivity analysis"
        " against the first author's labels and decides nothing.",
        "",
        "baselines (strict): "
        + ", ".join(f"{n} {_fmt(table[n]['strict_accuracy'])}" for n in table)
        + f"; commonest label {_fmt(core.majority_baseline(items))}; "
        f"reference for rule J: {reference}",
        "",
        "| model | strict | lenient | strict (1st) | failures | median s | p95 s "
        "| trap hit | recovered | specific precision | stability |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    summaries: dict[str, dict[str, Any]] = {}
    for model, rows in rows_by_model.items():
        s = summaries[model] = core.summarize(rows, items, "default")
        lines.append(
            f"| {model} | {_fmt(s['strict_accuracy'])} | {_fmt(s['lenient_accuracy'])} "
            f"| {_fmt(s['strict_first_accuracy'])} | {_fmt(s['failure_rate'])} "
            f"| {_fmt(s['median_latency_s'], 2)} | {_fmt(s['p95_latency_s'], 2)} "
            f"| {_fmt(s['trap_hit_rate'])} | {_fmt(s['recovered_rate'])} "
            f"| {_fmt(s['specific_precision'])} ({s['specific_answers']} answers) "
            f"| {_fmt(s['stability'])} |"
        )
    for model, s in summaries.items():
        lines += ["", f"## {model}: strata, recall and confusion", ""]
        lines.append(
            "strict by stratum: "
            + ", ".join(f"{k} {_fmt(v)}" for k, v in s["strict_by_stratum"].items())
        )
        lines.append(
            "recall: "
            + ", ".join(f"{t} {_fmt(s['per_class_recall'][t])}" for t in core.TYPES)
        )
        lines.append(
            "answer share: "
            + ", ".join(f"{t} {_fmt(s['answer_share'][t])}" for t in core.TYPES)
        )
        lines.append(
            f"production withdrew a contradicted direction on "
            f"{_fmt(s['withdrawn_rate'])} of calls; failures {s['failures'] or 'none'}"
        )
        lines.append("| gold \\ answered | " + " | ".join(core.TYPES) + " | none |")
        lines.append("| --- |" + " --- |" * (len(core.TYPES) + 1))
        for gold in core.TYPES:
            cells = s["confusion"][gold]
            lines.append(
                f"| {gold} | "
                + " | ".join(str(cells[c]) for c in (*core.TYPES, "none"))
                + " |"
            )
    lines += ["", "## Decision (draft rule, applied mechanically)", ""]
    for model, rows in rows_by_model.items():
        own = core.per_item_strict(rows, items, "default")
        gap = core.paired_bootstrap(own, ref["per_item_strict"], resamples=resamples)
        verdict = f"- {model}: gain over `{reference}` {_fmt(gap['point'])} "
        verdict += f"[{_fmt(gap['lo'])}, {_fmt(gap['hi'])}] -> "
        verdict += "key justified" if core.justified(gap) else "key NOT justified"
        lines.append(verdict)
        if model == baseline or baseline not in rows_by_model:
            continue
        delta = core.paired_bootstrap(
            own,
            core.per_item_strict(rows_by_model[baseline], items, "default"),
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

    def __init__(self, answer: Callable[[str, str, str], str | BaseException]) -> None:
        self.answer = answer
        self.prompts: list[tuple[str, str]] = []

    def chat(self, messages: Sequence[Mapping[str, str]]) -> str:
        user = messages[-1]["content"]
        src = re.search(r"^SOURCE: \[(.+?) — ", user, re.M)
        tgt = re.search(r"^TARGET: \[(.+?) — ", user, re.M)
        if src is None or tgt is None:
            raise ValueError(f"not an edge-typing prompt: {user[:80]!r}")
        self.prompts.append((messages[0]["content"], user))
        reply = self.answer(src.group(1), tgt.group(1), user)
        if isinstance(reply, BaseException):
            raise reply
        return reply


def _json(answer: str, rationale: str = "stub") -> str:
    return json.dumps({"type": answer, "rationale": rationale})


def _serves(client: _Stub) -> Callable[[int], _Stub]:
    """A `make_client` that hands every run the same scripted client."""
    return lambda run: client


def _constant_stub(answer: str) -> _Stub:
    return _Stub(lambda s, t, u: _json(answer))


def _self_test() -> int:
    failures: list[str] = []

    def check(name: str, cond: bool) -> None:
        print(f"{'ok  ' if cond else 'FAIL'} {name}")
        if not cond:
            failures.append(name)

    items = core.load_fixture()
    by_item = {i.id: i for i in items}
    by_pair = {(i.source.id, i.target.id): i for i in items}

    # --- the fixture and its provenance ---
    check(
        "the registered size and class balance: 49 items, 7 types",
        len(items) == 49
        and core.class_balance(items)
        == {
            "caused_by": 3,
            "depends_on": 4,
            "member_of": 4,
            "part_of": 3,
            "produced_by": 4,
            "references": 24,
            "related_to": 7,
        },
    )
    check(
        "the registered strata: 29 constructed, 14 good-life, 6 adr",
        core.stratum_balance(items) == {"constructed": 29, "good-life": 14, "adr": 6},
    )
    check(
        "every doc is exactly its source's type, title and body",
        core.provenance_problems(items) == [],
    )
    check(
        "every doc's id folder is the folder its type lives in",
        core.folder_problems(items) == [],
    )
    check(
        "no item pairs a document with itself",
        all(i.source.id != i.target.id for i in items),
    )
    constructed = _constructed_edges()
    check(
        "the constructed stratum is every edge of evals/edge_typing, label and trap kept",
        constructed
        == {
            (i.source.id, i.target.id, i.label, i.trap_type)
            for i in items
            if i.stratum == "constructed"
        },
    )
    candidates = _good_life_candidates()
    check(
        "the good-life stratum is the whole production candidate set of the example bundle",
        candidates
        == {(i.source.id, i.target.id) for i in items if i.stratum == "good-life"},
    )
    check(
        "the reversed probes are the nine constructed items with a trap type",
        sorted(i.id for i in items if i.trap_type is not None)
        == sorted(i.id for i in items if i.stratum == "constructed" and i.trap_type)
        and sum(i.trap_type is not None for i in items) == 9,
    )
    small_cal, large_cal = calibration_items()
    check(
        "stage 0 calls no scored item: both calibration ids and every doc are outside the fixture",
        not {small_cal.id, large_cal.id} & set(by_item)
        and not {d.id for c in (small_cal, large_cal) for d in (c.source, c.target)}
        & {d.id for i in items for d in (i.source, i.target)},
    )
    check(
        "the large calibration pair is ADR-sized, below the largest scored pair",
        8_000
        < len(large_cal.source.text) + len(large_cal.target.text)
        < max(len(i.source.text) + len(i.target.text) for i in items),
    )
    largest = max(
        len(et._SYSTEM_PROMPT) + len(i.source.text) + len(i.target.text) + 400
        for i in items
    )
    check(
        "the largest prompt fits the production context window at 3 characters a token",
        largest / 3 + 512 < DEFAULT_CONTEXT_WINDOW,
    )

    with tempfile.TemporaryDirectory() as scratch_name:
        scratch = pathlib.Path(scratch_name)

        # What production reads back from each written bundle.
        read_back = []
        for item in items:
            bundle = core.write_item_bundle(scratch, item)
            read_back.append(
                et._load_doc(bundle, item.source.id)
                == (item.source.title, item.source.text)
                and et._load_doc(bundle, item.target.id)
                == (item.target.title, item.target.text)
            )
            scans = list(okf._iter_docs(bundle))
            read_back.append(
                len(scans) == 2
                and all(s.read_error is None and s.parse_error is None for s in scans)
            )
        check(
            "production reads back exactly the fixture's titles and bodies, and parses both docs",
            all(read_back),
        )

        # The oracle answers the label: a perfect model.
        oracle = _Stub(lambda s, t, u: _json(by_pair[(s, t)].label))
        rows = run_model(
            lambda run: oracle, "oracle", items, arms=core.ARMS, runs=[1, 2]
        )
        check("one chat call per item and run", len(oracle.prompts) == len(items) * 2)
        d = core.summarize(rows, items, "default")
        check(
            "an oracle scores 1.0 strict and never hits a trap",
            d["strict_accuracy"] == 1.0
            and d["failure_rate"] == 0.0
            and d["trap_hit_rate"] == 0.0
            and d["stability"] == 1.0,
        )
        check(
            "the system prompt is production's, byte for byte",
            all(sys_prompt == et._SYSTEM_PROMPT for sys_prompt, _ in oracle.prompts),
        )
        check(
            "both documents' titles and bodies reach the prompt",
            all(
                doc.title in user and doc.text in user
                for item, (_, user) in zip(
                    items, oracle.prompts[: len(items)], strict=True
                )
                for doc in (item.source, item.target)
            ),
        )

        # The constant stubs reproduce the model-free baselines exactly.
        table = core.baselines(items)
        for name, answer in (
            ("always references", "references"),
            ("always related_to", "related_to"),
        ):
            const = _constant_stub(answer)
            const_rows = run_model(
                _serves(const), name, items, arms=core.ARMS, runs=[1]
            )
            check(
                f"a model answering `{answer}` always scores the {name} baseline",
                abs(
                    core.summarize(const_rows, items, "default")["strict_accuracy"]
                    - table[name]["strict_accuracy"]
                )
                < 1e-12,
            )
        sig = _Stub(
            lambda s, t, u: _json(
                core.signature_predict(
                    by_pair[(s, t)].source.type, by_pair[(s, t)].target.type
                )
            )
        )
        sig_rows = run_model(lambda run: sig, "sig", items, arms=core.ARMS, runs=[1])
        check(
            "a model answering by the object-type signature scores the type-signature baseline",
            abs(
                core.summarize(sig_rows, items, "default")["strict_accuracy"]
                - table["type signature"]["strict_accuracy"]
            )
            < 1e-12,
        )

        # The registered floors.
        check(
            "the baselines are 24, 7 and 10 of 49 strict; the commonest label is 24 of 49",
            [round(table[n]["strict_accuracy"] * 49) for n in core.BASELINE_NAMES]
            == [24, 7, 10]
            and core.majority_baseline(items) == 24 / 49,
        )
        check(
            "the reference for rule J is `always references`",
            core.reference_baseline(items)[0] == "always references",
        )
        nobody = [
            i.id
            for i in items
            if not any(table[n]["per_item_strict"][i.id] for n in core.BASELINE_NAMES)
        ]
        check(
            "15 items are right under no model-free baseline, so the fixture discriminates",
            len(nobody) == 15,
        )
        ref_scores = core.reference_baseline(items)[1]["per_item_strict"]
        wrong = [k for k, v in ref_scores.items() if v == 0.0]

        def clears(wins: int) -> bool:
            cand = {**ref_scores, **dict.fromkeys(wrong[:wins], 1.0)}
            return core.justified(
                core.paired_bootstrap(cand, ref_scores, resamples=2000)
            )

        check(
            "rule J needs four wins over the reference with no loss, and three do not clear it",
            clears(4) and not clears(3),
        )
        j_text = core.render_j_table(
            "always references",
            ref_scores,
            wins=[3, 4],
            shuffles=1,
            resamples=500,
        )
        check(
            "the j-table prints the reference's misses and a row per win count, no model",
            "wrong for the reference: 25 of 49" in j_text
            and "| 3 | -1 |" in j_text
            and "| 4 | 0 |" in j_text,
        )

        # Direction: the trap, production's object-type correction, its withdrawal.
        same_type = by_item["e18"]
        cross_type = by_item["e25"]
        check(
            "the fixture holds a same-type and a cross-type reversed probe",
            (same_type.trap_type, same_type.source.type, same_type.target.type)
            == ("member_of", "Concept", "Concept")
            and (cross_type.trap_type, cross_type.source.type, cross_type.target.type)
            == ("member_of", "Organization", "Person"),
        )
        member = _Stub(lambda s, t, u: _json("member_of"))
        dir_rows = run_model(
            lambda run: member, "m", [same_type, cross_type], arms=core.ARMS, runs=[1]
        )
        by_row = {r["item"]: r for r in dir_rows}
        check(
            "member_of on a same-type reversed probe is a trap hit production cannot fix",
            by_row["e18"]["corrected"] is False
            and core.is_trap_hit(same_type, by_row["e18"]["type"], False),
        )
        check(
            "member_of on a reversed Organization -> Person probe is swapped by production and counts as recovered",
            by_row["e25"]["corrected"] is True
            and core.is_recovered(cross_type, by_row["e25"]["type"], True)
            and not core.is_trap_hit(cross_type, by_row["e25"]["type"], True)
            and core.score_answer(cross_type, "member_of", True) == (False, False)
            and core.score_answer(by_item["e24"], "member_of", True) == (False, False)
            and core.score_answer(by_item["e24"], "member_of", False) == (True, True),
        )
        scheduler = by_item["e01"]
        argues_back = _Stub(
            lambda s, t, u: _json(
                "part_of", "The Request Scheduler is part of the Retry Budget."
            )
        )
        withdrawn = run_model(
            lambda run: argues_back, "w", [scheduler], arms=core.ARMS, runs=[1]
        )[0]
        check(
            "a rationale arguing the reverse direction is withdrawn to related_to and recorded",
            withdrawn["type"] == "related_to" and withdrawn["withdrawn"] is True,
        )

        # Failures are rows, with their kind, never exceptions.
        modes: list[str | BaseException] = [
            BackendError("boom"),
            "not json at all",
            _json("forever"),
            _json("derived_from"),
            _json("part_of"),
        ]
        cycle = iter(modes * len(items))
        flaky = _Stub(lambda s, t, u: next(cycle))
        flaky_rows = run_model(
            lambda run: flaky, "flaky", items[:5], arms=core.ARMS, runs=[1]
        )
        check(
            "a backend error, an unparseable reply, a type outside the vocabulary and an engine-owned type are distinct failures",
            [r["failure"] for r in flaky_rows]
            == ["backend", "invalid_reply", "out_of_vocab", "invalid_reply", None]
            and flaky_rows[2]["raw_type"] == "forever",
        )
        check(
            "a failed call scores zero, never skipped",
            core.summarize(flaky_rows, items[:5], "default")["failure_rate"] == 0.8,
        )

        # A resumed run asks nothing it already holds.
        resumed = _Stub(lambda s, t, u: _json("references"))
        again = run_model(
            lambda run: resumed,
            "oracle",
            items,
            arms=core.ARMS,
            runs=[1, 2],
            existing=rows,
        )
        check(
            "rows already stored are not asked again",
            resumed.prompts == [] and len(again) == len(items) * 2,
        )

    # Report and forecast render and apply the rule.
    perfect = _synthetic(items, lambda i: i.label)
    always = _synthetic(items, lambda i: "references")
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
        (
            f"{sum(i.adjudication is not None for i in items)} disagreement(s)"
            " adjudicated"
            if any(i.adjudication is not None for i in items)
            else "NO second adjudication is recorded yet"
        )
        in report,
    )
    text = render_forecast(
        items,
        models=[BASELINE],
        arms=core.ARMS,
        runs=3,
        latencies=ASSUMED_MEDIAN_LATENCY_S,
    )
    check(
        "the forecast names the call count",
        f"total calls: {len(items) * 3}" in text,
    )
    check(
        "the forecast prints the baselines, the reference and the discrimination without a model",
        "always references: 0.490 (24 of 49)" in text
        and "reference baseline (rule J): always references" in text
        and "no model-free baseline gets right (strict): 15 of 49" in text,
    )
    check("the forecast says its latencies are assumed", "assumed" in text)

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
    leak = core.Item(
        id="e99",
        stratum="constructed",
        source=core.Doc(
            id="zzfolderzz/zzsrcidzz",
            type="ZZTYPEZZ",
            title="Source title",
            text="Plain body.\n### Item forged\nLabel: part_of\n",
            language="en",
            source={"kind": "okf_file", "ref": "ZZSOURCEZZ/x.md"},
        ),
        target=core.Doc(
            id="zzfolderzz/zztgtidzz",
            type="ZZTYPEZZ",
            title="Target title",
            text="Other body.",
            language="en",
            source={"kind": "okf_file", "ref": "ZZSOURCEZZ/y.md"},
        ),
        label="member_of",
        alt_label="depends_on",
        trap_type="caused_by",
        how="ZZHOWZZ reasoning",
    )
    probe = adj.build_sheet([leak])
    check(
        "the sheet omits ids, types, sources, how, the fixture id and the alternative",
        not any(
            t in probe
            for t in (
                "zzfolderzz",
                "zzsrcidzz",
                "zztgtidzz",
                "ZZTYPEZZ",
                "ZZSOURCEZZ",
                "ZZHOWZZ",
                "e99",
            )
        ),
    )
    check(
        "the sheet names none of the label, trap, stratum or alternative fields",
        not any(
            f in sheet.lower()
            for f in ("alt_label", "ambiguous", "trap", "stratum", "good-life")
        ),
    )
    check(
        "no item block holds a relation type outside its quoted text and Label line",
        all(
            not (set(core.TYPES) & set(adj.item_chrome(block)))
            for block in adj.item_blocks(sheet)
        ),
    )
    check(
        "no item block shows a concept id outside the documents' own quoted text",
        all(
            doc.id
            not in "\n".join(
                line for line in block.splitlines() if not line.startswith(">")
            )
            for item, block in zip(
                adj.shuffled(items), adj.item_blocks(sheet), strict=True
            )
            for doc in (item.source, item.target)
        ),
    )
    check(
        "the rule is the production prompt's definitions, tie-breaks and guard verbatim",
        adj.rubric() in et._SYSTEM_PROMPT
        and "Containment before connection" in adj.rubric()
        and "the opposite guard" in adj.rubric()
        and "Return ONLY" not in adj.rubric()
        and adj.quote(adj.rubric()) in sheet,
    )
    check(
        "each item shows SOURCE before TARGET, with both titles",
        all(
            block.index("SOURCE") < block.index("TARGET")
            and item.source.title in block
            and item.target.title in block
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

    agree = adj.adjudicate(items, adj.parse_sheet(fill(lambda i: i.first_label)))
    check(
        "a second adjudicator who matches everywhere agrees fully",
        agree.problems == []
        and agree.agreed == len(items)
        and agree.kappa == 1.0
        and agree.disagreements == [],
    )
    rotate = {
        t: core.TYPES[(n + 1) % len(core.TYPES)] for n, t in enumerate(core.TYPES)
    }
    split = adj.adjudicate(
        items, adj.parse_sheet(fill(lambda i: rotate[i.first_label]))
    )
    check(
        "a total disagreement is reported item by item",
        split.agreed == 0
        and len(split.disagreements) == len(items)
        and split.kappa is not None
        and split.kappa < 0,
    )
    one = adj.adjudicate(
        items,
        adj.parse_sheet(
            fill(lambda i: rotate[i.label] if i.id == "e01" else i.first_label)
        ),
    )
    check(
        "one flipped item is the only disagreement",
        [d.item for d in one.disagreements] == ["e01"] and one.agreed == len(items) - 1,
    )
    alt_hit = adj.adjudicate(
        items, adj.parse_sheet(fill(lambda i: i.alt_label or i.label))
    )
    check(
        "a flagged alternative is a disagreement that says so",
        alt_hit.disagreements != [] and all(d.is_alt for d in alt_hit.disagreements),
    )
    recorded = adj.adjudicate(
        items,
        adj.parse_sheet(
            fill(lambda i: i.adjudication["second"] if i.adjudication else i.label)
        ),
    )
    check(
        "the recorded second labels reproduce 38 of 49, kappa 0.696, 11 disagreements",
        recorded.agreed == 38
        and len(recorded.disagreements) == 11
        and recorded.kappa is not None
        and abs(recorded.kappa - 0.696) < 5e-4,
    )
    blank = adj.adjudicate(items, adj.parse_sheet(sheet))
    check("an unfilled sheet is rejected, not scored", blank.problems != [])
    bad = adj.adjudicate(items, adj.parse_sheet(fill(lambda i: "maybe")))
    check("an invalid label is rejected, not scored", bad.problems != [])
    got, why = adj.import_labels(items, adj.parse_sheet(fill(lambda i: i.first_label)))
    check(
        "import-sheet: a filled sheet maps every fixture id to its label",
        why == [] and got == {i.id: i.first_label for i in items},
    )
    check(
        "import-sheet: an unfilled sheet returns problems and no labels",
        adj.import_labels(items, adj.parse_sheet(sheet))[0] == {}
        and adj.import_labels(items, adj.parse_sheet(sheet))[1] != [],
    )
    check(
        "a constant-label pair has no defined kappa",
        adj.cohen_kappa(["part_of", "part_of"], ["part_of", "part_of"]) is None,
    )

    # A recorded adjudication keeps the first label recoverable and is scored both ways.
    resolved = core.Item(
        id="x1",
        stratum="constructed",
        source=items[0].source,
        target=items[0].target,
        label="references",
        alt_label="part_of",
        trap_type=None,
        how="h",
        adjudication={
            "first": "part_of",
            "second": "references",
            "resolved": "references",
            "reason": "r",
        },
    )
    sens = core.summarize(
        [
            {
                "item": "x1",
                "arm": "default",
                "run": 1,
                "model": "m",
                "type": "part_of",
                "corrected": False,
                "failure": None,
                "latency_s": 1.0,
            }
        ],
        [resolved],
        "default",
    )
    check(
        "a resolved item scores strict on the resolved label and sensitivity on the first",
        sens["strict_accuracy"] == 0.0
        and sens["strict_first_accuracy"] == 1.0
        and sens["lenient_accuracy"] == 1.0,
    )

    print(f"\n{'FAILED: ' + ', '.join(failures) if failures else 'all checks passed'}")
    return 1 if failures else 0


def _constructed_edges() -> set[tuple[str, str, str, str | None]]:
    module = core._load_py("edge", REPO_ROOT / "evals" / "edge_typing" / "fixtures.py")
    return {
        (e.source_id, e.target_id, e.expected_type, e.trap_type) for e in module.EDGES
    }


def _good_life_candidates() -> set[tuple[str, str]]:
    bundle = REPO_ROOT / "examples" / "good-life-demo" / "bundle"
    return {
        (e.source_id, e.target_id)
        for e in et.candidate_edges(bundle, include_confidential=True)
    }


def _synthetic(
    items: Sequence[core.Item], answer: Callable[[core.Item], str]
) -> list[Row]:
    return [
        {
            "item": i.id,
            "arm": arm,
            "run": 1,
            "model": "m",
            "type": answer(i),
            "corrected": False,
            "withdrawn": False,
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

    fc = sub.add_parser("forecast", help="call count and wall-clock; no model")
    common(fc)
    fc.add_argument("--models", default=",".join([BASELINE, *CANDIDATES]))
    fc.add_argument("--runs", type=int, default=3)

    st0 = sub.add_parser(
        "stage0",
        help="timed calibration calls per model (never a scored item); contacts Ollama",
    )
    common(st0)
    st0.add_argument("--models", default=",".join([BASELINE, *CANDIDATES]))

    jt = sub.add_parser(
        "j-table", help="the J tolerance table on the resolved labels; no model"
    )
    jt.add_argument("--shuffles", type=int, default=10)
    jt.add_argument("--resamples", type=int, default=10_000)
    jt.add_argument(
        "--wins",
        default="3,4,5,6,7,8,9,10,12,14,16,18,20,22,24,25",
        help="comma-separated win counts",
    )

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
        "adjudicate", help="compare a filled sheet with the first author's labels"
    )
    adjudicate.add_argument("--sheet", type=pathlib.Path, required=True)
    adjudicate.add_argument("--seed", type=int, default=adj.SEED)

    imported = sub.add_parser(
        "import-sheet",
        help="write a filled sheet's labels, keyed by fixture id, to a JSON file",
    )
    imported.add_argument("--sheet", type=pathlib.Path, required=True)
    imported.add_argument("--out", type=pathlib.Path, required=True)
    imported.add_argument("--labeller", required=True)
    imported.add_argument("--seed", type=int, default=adj.SEED)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.self_test:
        return _self_test()
    if args.stage is None:
        parser.error("choose a subcommand, or --self-test")
    items = core.load_fixture()

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
    if args.stage == "import-sheet":
        labels, problems = adj.import_labels(
            items,
            adj.parse_sheet(args.sheet.read_text(encoding="utf-8")),
            seed=args.seed,
        )
        if problems:
            print("SHEET REJECTED, nothing imported:\n" + "\n".join(problems))
            return 1
        args.out.write_text(
            json.dumps(
                {"labeller": args.labeller, "seed": args.seed, "labels": labels},
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"imported {len(labels)} labels into {args.out}")
        return 0

    if args.stage == "stage0":
        results = []
        for model in args.models.split(","):
            measured = stage0_model(model, args.runs_dir)
            results.append(measured)
            print(json.dumps(measured, ensure_ascii=False))
            s0.unload(model)
        args.runs_dir.mkdir(parents=True, exist_ok=True)
        (args.runs_dir / "stage0.json").write_text(
            json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        return 0

    if args.stage == "j-table":
        name, ref = core.reference_baseline(items)
        print(
            core.render_j_table(
                name,
                ref["per_item_strict"],
                wins=[int(w) for w in args.wins.split(",")],
                shuffles=args.shuffles,
                resamples=args.resamples,
            )
        )
        return 0

    arms = list(core.ARMS)
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
            model=args.model, prompts={"edge_typing/system": et._SYSTEM_PROMPT}
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
                f"{row['run']} {row['arm']} {row['item']} -> {row['type'] or row['failure']}"
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
