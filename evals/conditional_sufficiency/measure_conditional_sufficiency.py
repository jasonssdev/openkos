"""Can the sufficiency check run less often without losing what it catches? (#1740)

Read `PREREGISTRATION.md` first: it fixes the arms, the bars and the stopping
rules. Production code, prompts and defaults are unchanged. Every treatment
lives in this file only.

The shipped sequence (`retrieval/answer.py::answer`) is: assemble the context,
run the sufficiency check on the FULL context, refuse when it finds no
quotation, otherwise synthesize, then let the `USED:` attribution decide the
citations. The check and the synthesis are independent calls over the same
context, so this harness measures them once per (question, run) and REPLAYS
each arm's sequence over the paired measurements. No arm re-asks a model.

Arms: `baseline` (always check, first), `cond_unattributed` (synthesize first,
check only when the reply has no usable `USED:` line), `cond_lowrank` (the same,
and also when the reply cites a block ranked below `LOW_RANK_TOP_K`), `reduced`
(check first, over the top `REDUCED_BLOCKS` blocks only).

Subcommands (all but `contexts`, `checks`, `synth` are model-free):
`contexts` (embedder), `checks` (check model), `synth` (answer model), `score`,
`decide`. Check: `--self-test` (model-free).
"""

from __future__ import annotations

import argparse
import json
import math
import pathlib
import random
import re
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Final

_EVALS = pathlib.Path(__file__).resolve().parent.parent
sys.path.append(str(_EVALS))
sys.path.append(str(_EVALS / "query_grounding"))
sys.path.append(str(_EVALS / "query_sufficiency"))

from grounding_corpus import ADJACENT, GROUNDED, QUESTIONS  # noqa: E402
from run_query_sufficiency_probe import (  # noqa: E402
    _ATTRIBUTION_SURVIVORS,
    _verdict,
)

from openkos.retrieval.answer import (  # noqa: E402
    _SUFFICIENCY_PROMPT,
    _SYSTEM_PROMPT,
    _build_messages,
    _split_attribution,
    _user_content,
)

SURVIVORS: Final = _ATTRIBUTION_SURVIVORS
"""The three adjacent questions `USED:` attribution alone does not refuse (#760)."""

BARS: Final[dict[str, float]] = {
    "RUNS": 5,
    "BOOTSTRAP_SEED": 1740,
    "RESAMPLES": 10_000,
    "LOW_RANK_TOP_K": 2,
    "REDUCED_BLOCKS": 3,
    "CHECK_MEAN_FLOOR_S": 1.0,
    "SURVIVORS_MIN": 3,
    "ADJACENT_DROP_MAX": 0,
    "FALSE_REFUSAL_RISE_MAX": 0,
    "DELIVERED_DROP_MAX": 0.0,
    "LATENCY_RATIO_MAX": 0.85,
    "LATENCY_UPPER_MAX": 0.95,
    "P95_RATIO_MAX": 1.0,
    "FAILED_MAX": 0.15,
}

ARMS: Final = ("baseline", "cond_unattributed", "cond_lowrank", "reduced")
TREATMENTS: Final = ARMS[1:]


def _check_messages(user_content: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": _SUFFICIENCY_PROMPT},
        {"role": "user", "content": user_content},
    ]


@dataclass(frozen=True)
class CheckRow:
    run: int
    label: str
    question: str
    blocks: int
    t_full: float
    holds_full: bool
    t_reduced: float
    holds_reduced: bool


@dataclass(frozen=True)
class SynthRow:
    run: int
    question: str
    t_synth: float
    attribution: str
    cited: tuple[int, ...]


@dataclass(frozen=True)
class Cell:
    run: int
    label: str
    question: str
    blocks: int
    t_full: float
    holds_full: bool
    t_reduced: float
    holds_reduced: bool
    t_synth: float
    attribution: str
    cited: tuple[int, ...]


def gate_fires(arm: str, cell: Cell) -> bool:
    """Whether the post-synthesis check runs for this cell. Only the two
    conditional arms have a gate; a reported-but-empty `USED:` list never
    opens it, because that answer already abstains through its citations."""
    if arm not in ("cond_unattributed", "cond_lowrank"):
        return False
    if cell.attribution != "reported":
        return True
    if arm == "cond_lowrank" and cell.cited:
        return max(cell.cited) > BARS["LOW_RANK_TOP_K"]
    return False


def simulate(arm: str, cell: Cell) -> dict[str, Any]:
    """Replay one arm's shipped-order sequence over one paired measurement."""
    empty = cell.attribution == "reported" and not cell.cited
    if arm == "baseline":
        refused = not cell.holds_full
        return {
            "abstained": refused or empty,
            "seconds": cell.t_full + (0.0 if refused else cell.t_synth),
            "checks": 1,
            "synth": not refused,
        }
    if arm == "reduced":
        refused = not cell.holds_reduced
        return {
            "abstained": refused or empty,
            "seconds": cell.t_reduced + (0.0 if refused else cell.t_synth),
            "checks": 1,
            "synth": not refused,
        }
    fires = gate_fires(arm, cell)
    refused = fires and not cell.holds_full
    return {
        "abstained": refused or empty,
        "seconds": cell.t_synth + (cell.t_full if fires else 0.0),
        "checks": 1 if fires else 0,
        "synth": True,
    }


def percentile(values: Sequence[float], q: float) -> float:
    """Nearest-rank percentile; `0.0` for no values."""
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[max(1, math.ceil(q * len(ordered))) - 1]


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def score_arm(cells: Sequence[Cell], arm: str) -> dict[str, Any]:
    outs = [(c, simulate(arm, c)) for c in cells]
    by_q: dict[str, list[bool]] = {}
    for c, o in outs:
        by_q.setdefault(c.question, []).append(bool(o["abstained"]))
    labels = {c.question: c.label for c in cells}
    grounded = [(c, o) for c, o in outs if c.label == GROUNDED]
    g_seconds = [float(o["seconds"]) for _, o in grounded]
    a_seconds = [float(o["seconds"]) for _, o in outs]
    return {
        "false_refusals": sum(
            1 for q, v in by_q.items() if labels[q] == GROUNDED and any(v)
        ),
        "delivered_rate": _mean([0.0 if o["abstained"] else 1.0 for _, o in grounded]),
        "survivors_caught": sum(1 for q in SURVIVORS if q in by_q and all(by_q[q])),
        "adjacent_caught": sum(
            1 for q, v in by_q.items() if labels[q] == ADJACENT and all(v)
        ),
        "checks_grounded": _mean([float(o["checks"]) for _, o in grounded]),
        "mean_grounded": _mean(g_seconds),
        "p50_grounded": percentile(g_seconds, 0.5),
        "p95_grounded": percentile(g_seconds, 0.95),
        "mean_all": _mean(a_seconds),
        "p50_all": percentile(a_seconds, 0.5),
        "p95_all": percentile(a_seconds, 0.95),
    }


def _grounded_ratio(cells: Sequence[Cell], arm: str) -> float:
    arm_s = sum(float(simulate(arm, c)["seconds"]) for c in cells)
    base_s = sum(float(simulate("baseline", c)["seconds"]) for c in cells)
    return arm_s / base_s if base_s else 0.0


def bootstrap_upper(cells: Sequence[Cell], arm: str) -> float:
    """One-sided 95% upper bound of the grounded latency ratio, a seeded
    cluster bootstrap over grounded questions."""
    clusters: dict[str, list[Cell]] = {}
    for c in cells:
        if c.label == GROUNDED:
            clusters.setdefault(c.question, []).append(c)
    groups = list(clusters.values())
    if not groups:
        return 0.0
    rng = random.Random(int(BARS["BOOTSTRAP_SEED"]))  # noqa: S311 -- seeded resampling, not security
    ratios = []
    for _ in range(int(BARS["RESAMPLES"])):
        picked = [c for g in (rng.choice(groups) for _ in groups) for c in g]
        ratios.append(_grounded_ratio(picked, arm))
    return percentile(ratios, 0.95)


def baseline_stop(rows: Sequence[Any]) -> str | None:
    """The futility stops that read the baseline alone (so they can fire after
    the checks and before any synthesis): a baseline that misses a survivor,
    or a check too cheap to be worth skipping."""
    held: dict[str, list[bool]] = {}
    for r in rows:
        held.setdefault(r.question, []).append(bool(r.holds_full))
    if any(q not in held or any(held[q]) for q in SURVIVORS):
        return "NO-BASELINE"
    grounded = [float(r.t_full) for r in rows if r.label == GROUNDED]
    if _mean(grounded) < BARS["CHECK_MEAN_FLOOR_S"]:
        return "NO-EXPOSURE"
    return None


def decide_arm(cells: Sequence[Cell], arm: str) -> tuple[str, list[str]]:
    base = score_arm(cells, "baseline")
    s = score_arm(cells, arm)
    failed: list[str] = []
    if s["survivors_caught"] < BARS["SURVIVORS_MIN"]:
        failed.append("B1")
    if s["adjacent_caught"] < base["adjacent_caught"] - BARS["ADJACENT_DROP_MAX"]:
        failed.append("B2")
    if (
        s["false_refusals"] > base["false_refusals"] + BARS["FALSE_REFUSAL_RISE_MAX"]
        or s["delivered_rate"] < base["delivered_rate"] - BARS["DELIVERED_DROP_MAX"]
    ):
        failed.append("B3")
    grounded = [c for c in cells if c.label == GROUNDED]
    if (
        _grounded_ratio(grounded, arm) > BARS["LATENCY_RATIO_MAX"]
        or bootstrap_upper(cells, arm) > BARS["LATENCY_UPPER_MAX"]
    ):
        failed.append("B4")
    if s["p95_grounded"] > base["p95_grounded"] * BARS["P95_RATIO_MAX"]:
        failed.append("B5")
    return ("REJECTED", failed) if failed else ("SUPPORTED", [])


def decide(
    cells: Sequence[Cell], failed: int, total: int
) -> dict[str, tuple[str, list[str]]]:
    if total and failed / total > BARS["FAILED_MAX"]:
        return {a: ("INCONCLUSIVE", ["failed calls"]) for a in TREATMENTS}
    stop = baseline_stop(cells)
    if stop is not None:
        return {a: (stop, []) for a in TREATMENTS}
    return {a: decide_arm(cells, a) for a in TREATMENTS}


def reduced_user_content(blocks: Sequence[str], question: str) -> str:
    return _user_content(list(blocks[: int(BARS["REDUCED_BLOCKS"])]), question)


def _cell_key(run: int, index: int) -> tuple[int, int]:
    return (run, index)


def collect_checks(
    contexts: Sequence[Any], check_llm: Any, runs: int
) -> tuple[list[CheckRow], set[tuple[int, int]]]:
    """Phase A: both checks for every (run, question), check model only."""
    rows: list[CheckRow] = []
    failed: set[tuple[int, int]] = set()
    for run in range(runs):
        for index, ctx in enumerate(contexts):
            full = _user_content(list(ctx.blocks), ctx.question)
            red = reduced_user_content(ctx.blocks, ctx.question)
            try:
                t0 = time.monotonic()
                r_full = check_llm.chat(_check_messages(full))
                t_full = time.monotonic() - t0
                t0 = time.monotonic()
                r_red = check_llm.chat(_check_messages(red))
                t_red = time.monotonic() - t0
            except Exception as exc:
                print(
                    f"  check run {run} {ctx.question[:40]!r}: {type(exc).__name__}",
                    flush=True,
                )
                failed.add(_cell_key(run, index))
                continue
            rows.append(
                CheckRow(
                    run,
                    ctx.label,
                    ctx.question,
                    len(ctx.blocks),
                    round(t_full, 2),
                    _verdict("quote", r_full),
                    round(t_red, 2),
                    _verdict("quote", r_red),
                )
            )
    return rows, failed


def collect_synth(
    contexts: Sequence[Any], synth_llm: Any, runs: int
) -> tuple[list[SynthRow], set[tuple[int, int]]]:
    """Phase B: the shipped synthesis call for every (run, question)."""
    rows: list[SynthRow] = []
    failed: set[tuple[int, int]] = set()
    for run in range(runs):
        for index, ctx in enumerate(contexts):
            user = _user_content(list(ctx.blocks), ctx.question)
            try:
                t0 = time.monotonic()
                reply = synth_llm.chat(_build_messages(user))
                elapsed = time.monotonic() - t0
            except Exception as exc:
                print(
                    f"  synth run {run} {ctx.question[:40]!r}: {type(exc).__name__}",
                    flush=True,
                )
                failed.add(_cell_key(run, index))
                continue
            _, reported, attribution = _split_attribution(reply, len(ctx.blocks))
            cited = (
                tuple(sorted(reported))
                if reported is not None
                else tuple(range(1, len(ctx.blocks) + 1))
            )
            rows.append(
                SynthRow(run, ctx.question, round(elapsed, 2), attribution, cited)
            )
    return rows, failed


def merge_cells(checks: Sequence[CheckRow], synths: Sequence[SynthRow]) -> list[Cell]:
    by_synth = {(s.run, s.question): s for s in synths}
    cells = []
    for c in checks:
        s = by_synth.get((c.run, c.question))
        if s is None:
            continue
        cells.append(
            Cell(
                c.run,
                c.label,
                c.question,
                c.blocks,
                c.t_full,
                c.holds_full,
                c.t_reduced,
                c.holds_reduced,
                s.t_synth,
                s.attribution,
                s.cited,
            )
        )
    return cells


def collect_cells(
    contexts: Sequence[Any], check_llm: Any, synth_llm: Any, runs: int
) -> tuple[list[Cell], int]:
    checks, _ = collect_checks(contexts, check_llm, runs)
    synths, _ = collect_synth(contexts, synth_llm, runs)
    cells = merge_cells(checks, synths)
    return cells, runs * len(contexts) - len(cells)


def registered_constants(path: pathlib.Path) -> dict[str, float]:
    """The `KEY = value` lines of the fenced block in the registration that
    holds `BOOTSTRAP_SEED`; empty when the file or the block is missing."""
    if not path.exists():
        return {}
    for block in re.findall(r"```\n(.*?)```", path.read_text(encoding="utf-8"), re.S):
        if "BOOTSTRAP_SEED" in block:
            found = re.findall(r"^([A-Z_0-9]+) = ([0-9_.]+)$", block, re.M)
            return {k: float(v.replace("_", "")) for k, v in found}
    return {}


def _self_test() -> int:
    failures: list[str] = []
    checks = 0

    def check(name: str, cond: bool) -> None:
        nonlocal checks
        checks += 1
        if not cond:
            failures.append(name)

    def cell(**kw: Any) -> Cell:
        base: dict[str, Any] = {
            "run": 0,
            "label": GROUNDED,
            "question": "q",
            "blocks": 5,
            "t_full": 10.0,
            "holds_full": True,
            "t_reduced": 4.0,
            "holds_reduced": True,
            "t_synth": 6.0,
            "attribution": "reported",
            "cited": (1,),
        }
        base.update(kw)
        return Cell(**base)

    def guarded(name: str, fn: Callable[[], None]) -> None:
        try:
            fn()
        except NotImplementedError:
            check(f"{name}: not implemented", False)

    # --- the gating predicate: when does the post-hoc check run? ----------
    def gates() -> None:
        for attr in ("absent", "unparsed"):
            check(
                f"unattributed fires on {attr}",
                gate_fires("cond_unattributed", cell(attribution=attr)),
            )
            check(
                f"lowrank fires on {attr}",
                gate_fires("cond_lowrank", cell(attribution=attr)),
            )
        check(
            "unattributed skips a reported line",
            not gate_fires("cond_unattributed", cell(cited=(1, 2))),
        )
        check(
            "unattributed skips a reported empty line",
            not gate_fires("cond_unattributed", cell(cited=())),
        )
        check(
            "unattributed ignores a low-ranked citation",
            not gate_fires("cond_unattributed", cell(cited=(4,))),
        )
        check(
            "lowrank skips top-ranked citations",
            not gate_fires("cond_lowrank", cell(cited=(1, 2))),
        )
        check(
            "lowrank fires just below the rank line",
            gate_fires("cond_lowrank", cell(cited=(1, 3))),
        )
        check(
            "lowrank skips exactly the rank line",
            not gate_fires("cond_lowrank", cell(cited=(2,))),
        )
        check(
            "lowrank skips an already-empty citation list",
            not gate_fires("cond_lowrank", cell(cited=())),
        )
        check(
            "baseline and reduced have no gate",
            not gate_fires("baseline", cell(attribution="absent")),
        )

    guarded("gates", gates)

    # --- one cell replayed through each arm ------------------------------
    def replay() -> None:
        ok = cell()
        b = simulate("baseline", ok)
        check(
            "baseline pays check plus synthesis",
            b["seconds"] == 16.0 and b["checks"] == 1 and b["synth"],
        )
        check("baseline delivers a cited answer", not b["abstained"])
        refused = simulate("baseline", cell(holds_full=False))
        check(
            "baseline refusal skips synthesis",
            refused["seconds"] == 10.0
            and not refused["synth"]
            and refused["abstained"],
        )
        empty = simulate("baseline", cell(cited=()))
        check(
            "an empty USED list abstains in the shipped sequence",
            empty["abstained"] and empty["seconds"] == 16.0,
        )

        skip = simulate("cond_lowrank", ok)
        check(
            "conditional skips the check when the gate is shut",
            skip["seconds"] == 6.0 and skip["checks"] == 0 and not skip["abstained"],
        )
        fire = simulate("cond_lowrank", cell(cited=(4,)))
        check(
            "conditional pays both when the gate opens",
            fire["seconds"] == 16.0 and fire["checks"] == 1,
        )
        late = simulate("cond_lowrank", cell(cited=(4,), holds_full=False))
        check(
            "a late refusal still paid for synthesis",
            late["abstained"] and late["seconds"] == 16.0 and late["synth"],
        )
        check(
            "a shut gate never refuses, even if the check would",
            not simulate("cond_unattributed", cell(holds_full=False))["abstained"],
        )
        check(
            "a shut gate on an empty list still abstains",
            simulate("cond_unattributed", cell(holds_full=False, cited=()))[
                "abstained"
            ],
        )

        r = simulate("reduced", ok)
        check(
            "reduced pays the cheap check plus synthesis",
            r["seconds"] == 10.0 and r["checks"] == 1,
        )
        rr = simulate("reduced", cell(holds_reduced=False))
        check(
            "reduced refusal skips synthesis",
            rr["seconds"] == 4.0 and rr["abstained"] and not rr["synth"],
        )
        check(
            "reduced ignores the full-context verdict",
            not simulate("reduced", cell(holds_full=False))["abstained"],
        )

    guarded("replay", replay)

    # --- percentile and bootstrap ----------------------------------------
    def stats() -> None:
        check(
            "p50 of four is the second (nearest rank)",
            percentile([4.0, 1.0, 3.0, 2.0], 0.5) == 2.0,
        )
        check(
            "p95 of twenty is the nineteenth",
            percentile([float(i) for i in range(1, 21)], 0.95) == 19.0,
        )
        check("p100 is the max", percentile([1.0, 9.0], 1.0) == 9.0)
        check("a fractional rank rounds up", percentile([3.0, 1.0, 2.0], 0.5) == 2.0)
        check("empty sequence is zero", percentile([], 0.5) == 0.0)

    guarded("stats", stats)

    # --- a fixture of the 20 questions -----------------------------------
    questions = list(QUESTIONS)
    survivors = set(SURVIVORS)

    def world(
        *,
        runs: int = 2,
        full_survivors_caught: bool = True,
        t_full: float = 10.0,
        grounded_attr: str = "reported",
        grounded_cited: tuple[int, ...] = (1,),
        reduced_false_refusal: bool = False,
        survivor_cited: tuple[int, ...] = (4, 5),
    ) -> list[Cell]:
        out: list[Cell] = []
        for run in range(runs):
            fr_done = False
            for label, question in questions:
                if label == GROUNDED:
                    hr = True
                    if reduced_false_refusal and not fr_done:
                        hr, fr_done = False, True
                    out.append(
                        cell(
                            run=run,
                            label=label,
                            question=question,
                            t_full=t_full,
                            holds_reduced=hr,
                            attribution=grounded_attr,
                            cited=grounded_cited,
                        )
                    )
                elif question in survivors:
                    out.append(
                        cell(
                            run=run,
                            label=label,
                            question=question,
                            t_full=t_full,
                            holds_full=not full_survivors_caught,
                            holds_reduced=False,
                            cited=survivor_cited,
                        )
                    )
                else:
                    out.append(
                        cell(
                            run=run,
                            label=label,
                            question=question,
                            t_full=t_full,
                            holds_full=False,
                            holds_reduced=False,
                            cited=(),
                        )
                    )
        return out

    check(
        "the fixture has the registered shape",
        len(questions) == 20
        and len(survivors) == 3
        and sum(1 for lbl, _ in questions if lbl == ADJACENT) == 10,
    )

    def scoring() -> None:
        s = score_arm(world(), "baseline")
        check("baseline catches every survivor", s["survivors_caught"] == 3)
        check("baseline catches all ten adjacent", s["adjacent_caught"] == 10)
        check(
            "baseline has no false refusals",
            s["false_refusals"] == 0 and s["delivered_rate"] == 1.0,
        )
        check("baseline checks once per grounded query", s["checks_grounded"] == 1.0)
        check(
            "baseline grounded seconds are check plus synthesis",
            s["mean_grounded"] == 16.0,
        )
        c = score_arm(world(), "cond_unattributed")
        check(
            "a shut gate on cited survivors loses them",
            c["survivors_caught"] == 0 and c["adjacent_caught"] == 7,
        )
        check(
            "a shut gate removes every grounded check",
            c["checks_grounded"] == 0.0 and c["mean_grounded"] == 6.0,
        )
        fr = score_arm(world(reduced_false_refusal=True), "reduced")
        check(
            "any-run false refusal is counted once per question",
            fr["false_refusals"] == 1,
        )
        check(
            "delivered rate is per cell", abs(fr["delivered_rate"] - (18 / 20)) < 1e-9
        )
        # a survivor refused in only one of two runs is not caught
        partial = world()
        flipped = [
            Cell(**{**c_.__dict__, "holds_full": True})
            if (c_.run == 1 and c_.question in survivors)
            else c_
            for c_ in partial
        ]
        check(
            "a survivor must be caught in ALL runs",
            score_arm(flipped, "baseline")["survivors_caught"] == 0,
        )

    guarded("scoring", scoring)

    def boot() -> None:
        w = world(grounded_attr="reported", grounded_cited=(1,))
        check(
            "bootstrap is deterministic",
            bootstrap_upper(w, "cond_lowrank") == bootstrap_upper(w, "cond_lowrank"),
        )
        check(
            "uniform clusters make the bound equal the ratio",
            abs(bootstrap_upper(w, "cond_lowrank") - 6.0 / 16.0) < 1e-9,
        )

    guarded("bootstrap", boot)

    # --- verdicts ---------------------------------------------------------
    def verdicts() -> None:
        good = world()
        res = decide(good, failed=0, total=40)
        check(
            "cond_lowrank is SUPPORTED when it keeps the survivors",
            res["cond_lowrank"][0] == "SUPPORTED",
        )
        v_unatt, why_unatt = res["cond_unattributed"]
        check(
            "cond_unattributed is REJECTED on survivors",
            v_unatt == "REJECTED" and "B1" in why_unatt,
        )
        check(
            "a clean cheap reduced check with no false refusal is SUPPORTED",
            res["reduced"][0] == "SUPPORTED",
        )
        low = decide(world(survivor_cited=(1, 4)), 0, 40)
        check(
            "a lowrank gate that opens on the survivors keeps them",
            low["cond_lowrank"][0] == "SUPPORTED",
        )
        high = decide(world(survivor_cited=(1, 2)), 0, 40)
        check(
            "a lowrank gate that stays shut loses them",
            high["cond_lowrank"][0] == "REJECTED" and "B1" in high["cond_lowrank"][1],
        )
        fr = decide(world(reduced_false_refusal=True), 0, 40)
        check(
            "a reduced false refusal is REJECTED on B3",
            fr["reduced"][0] == "REJECTED" and "B3" in fr["reduced"][1],
        )
        allfire = decide(
            world(grounded_attr="absent", grounded_cited=tuple(range(1, 6))), 0, 40
        )
        check(
            "a gate that always opens saves nothing",
            allfire["cond_unattributed"][0] == "REJECTED"
            and "B4" in allfire["cond_unattributed"][1],
        )
        nobase = decide(world(full_survivors_caught=False), 0, 40)
        check(
            "a baseline that misses a survivor is NO-BASELINE",
            all(v[0] == "NO-BASELINE" for v in nobase.values()),
        )
        cheap = decide(world(t_full=0.2), 0, 40)
        check(
            "a nearly free check leaves nothing to save",
            all(v[0] == "NO-EXPOSURE" for v in cheap.values()),
        )
        broken = decide(world(), failed=8, total=40)
        check(
            "too many failed calls is INCONCLUSIVE",
            all(v[0] == "INCONCLUSIVE" for v in broken.values()),
        )
        edge = decide(world(), failed=6, total=40)
        check(
            "exactly the failure cap is not INCONCLUSIVE",
            edge["reduced"][0] != "INCONCLUSIVE",
        )
        mixed = [
            Cell(**{**c_.__dict__, "holds_full": True})
            if (c_.run == 1 and c_.question in survivors)
            else c_
            for c_ in world()
        ]
        check(
            "a survivor missed in only one run is NO-BASELINE",
            baseline_stop(mixed) == "NO-BASELINE",
        )
        check(
            "the baseline stop reads check rows too",
            baseline_stop(world(full_survivors_caught=False)) == "NO-BASELINE"
            and baseline_stop(world()) is None,
        )

    guarded("verdicts", verdicts)

    # --- the reduced context, and the collection order -------------------
    def collection() -> None:
        blocks = [f"block {i}" for i in range(1, 6)]
        red = reduced_user_content(blocks, "pregunta")
        check(
            "reduced content is the shipped builder over the top blocks",
            red == _user_content(blocks[:3], "pregunta"),
        )
        check(
            "reduced content drops the lower blocks", "[4]" not in red and "[3]" in red
        )
        check(
            "a short context is left whole",
            reduced_user_content(blocks[:2], "p") == _user_content(blocks[:2], "p"),
        )

        calls: list[tuple[str, str, str]] = []

        class Stub:
            def __init__(self, name: str, fail_on: str | None = None) -> None:
                self.name, self.fail_on = name, fail_on

            def chat(self, messages: list[dict[str, str]]) -> str:
                system, user = messages[0]["content"], messages[1]["content"]
                calls.append((self.name, system[:20], user))
                if self.fail_on and self.fail_on in user:
                    raise RuntimeError("boom")
                return (
                    "NONE"
                    if system == _SUFFICIENCY_PROMPT
                    else "Respuesta.\nUSED: 1, 2"
                )

        @dataclass
        class Ctx:
            label: str
            question: str
            blocks: list[str]

        ctxs = [Ctx(GROUNDED, "uno", blocks), Ctx(ADJACENT, "dos", blocks)]
        cells, failed = collect_cells(ctxs, Stub("check"), Stub("synth"), runs=2)
        check("one cell per question and run", len(cells) == 4 and failed == 0)
        names = [c[0] for c in calls]
        check("three calls per cell", len(calls) == 12)
        check(
            "every check precedes every synthesis (no model ping-pong)",
            names.index("synth") == names.count("check")
            and "check" not in names[names.index("synth") :],
        )
        check(
            "the full check sends the production prompt",
            any(c[1] == _SUFFICIENCY_PROMPT[:20] for c in calls if c[0] == "check"),
        )
        check(
            "synthesis sends the production system prompt",
            any(c[1] == _SYSTEM_PROMPT[:20] for c in calls if c[0] == "synth"),
        )
        check(
            "a NONE reply is a refusal",
            all(not c.holds_full and not c.holds_reduced for c in cells),
        )
        check(
            "the USED line is parsed with the shipped parser",
            all(c.attribution == "reported" and c.cited == (1, 2) for c in cells),
        )
        calls.clear()
        cells2, failed2 = collect_cells(
            ctxs, Stub("check"), Stub("synth", fail_on="dos"), runs=1
        )
        check(
            "a failed synthesis drops the whole cell and is counted",
            len(cells2) == 1 and failed2 == 1,
        )

    guarded("collection", collection)

    # --- the registration and the code must agree ---------------------------
    registered = registered_constants(
        pathlib.Path(__file__).resolve().parent / "PREREGISTRATION.md"
    )
    check("the registration lists a constant block", bool(registered))
    check(
        "every registered constant matches BARS, and none is missing",
        registered == {k: float(v) for k, v in BARS.items()},
    )

    for name in failures:
        print(f"FAIL: {name}")
    print(f"self-test: {checks - len(failures)}/{checks} passed")
    return 1 if failures else 0


OUT_DEFAULT: Final = (
    pathlib.Path.home() / "Dev/Projects/openkos-eval-runs/conditional-sufficiency"
)
EMBED_MODEL: Final = "bge-m3"
SYNTH_MODEL: Final = "qwen3:8b"
CHECK_MODEL: Final = "gemma4:26b-a4b"
LIMIT: Final = 5
TIMEOUT: Final = 1800.0


@dataclass(frozen=True)
class Ctx:
    label: str
    question: str
    blocks: list[str]


def _write(path: pathlib.Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _stamp(model: str) -> dict[str, Any]:
    from harness_stamp import build_stamp

    return build_stamp(
        model=model,
        prompts={
            "answer/sufficiency": _SUFFICIENCY_PROMPT,
            "answer/system": _SYSTEM_PROMPT,
        },
    )


def cmd_contexts(out: pathlib.Path) -> int:
    """Embedder only: assemble the shipped context once per question."""
    import tempfile

    from run_query_sufficiency_probe import _write_corpus

    from openkos.llm.ollama import OllamaClient
    from openkos.retrieval import fusion, pool
    from openkos.retrieval.answer import _assemble_context, _dense_search, _fts_search
    from openkos.state import reindex as reindex_module
    from openkos.state.fts import open_fts_index_readonly
    from openkos.state.vectorstore import open_vector_store

    embedder = OllamaClient(model=EMBED_MODEL, timeout=TIMEOUT)
    ctxs: list[Ctx] = []
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        bundle = _write_corpus(root)
        vectors, fts_path = (
            root / ".openkos" / "vectors.db",
            root / ".openkos" / "fts.db",
        )
        with open_vector_store(vectors) as db:
            reindex_module.reindex(
                bundle, db, embedder, fts_db_path=fts_path, model_tag=EMBED_MODEL
            )
        fts_index = open_fts_index_readonly(fts_path)
        try:
            with open_vector_store(vectors) as db:
                for label, question in QUESTIONS:
                    limit_pool = pool.pool_limit(LIMIT)
                    hits, _ = _fts_search(fts_index, question, limit=limit_pool)
                    vec_hits, _ = _dense_search(
                        question,
                        embedder=embedder,
                        vector_store=db,
                        pool_limit=limit_pool,
                    )
                    fused = fusion.fuse(hits, vec_hits)[:LIMIT]
                    blocks, _ = _assemble_context(bundle, fused)
                    if blocks:
                        ctxs.append(Ctx(label, question, list(blocks)))
        finally:
            if fts_index is not None:
                fts_index.close()
    _write(
        out / "contexts.json",
        {"contexts": [c.__dict__ for c in ctxs], "expected": len(QUESTIONS)},
    )
    print(f"contexts: {len(ctxs)} of {len(QUESTIONS)} questions assembled")
    return 0 if len(ctxs) == len(QUESTIONS) else 1


def _load_contexts(out: pathlib.Path) -> list[Ctx]:
    raw = json.loads((out / "contexts.json").read_text(encoding="utf-8"))
    return [Ctx(**c) for c in raw["contexts"]]


def cmd_checks(out: pathlib.Path, model: str, runs: int) -> int:
    """Phase A, check model only. Prints the baseline futility stop and ends."""
    from openkos.config import DEFAULT_CONTEXT_WINDOW, DEFAULT_MAX_GENERATION_TOKENS
    from openkos.llm.ollama import OllamaClient

    llm = OllamaClient(
        model=model,
        max_generation_tokens=DEFAULT_MAX_GENERATION_TOKENS,
        context_window=DEFAULT_CONTEXT_WINDOW,
        timeout=TIMEOUT,
    )
    contexts = _load_contexts(out)
    rows, failed = collect_checks(contexts, llm, runs)
    _write(
        out / "checks.json",
        {
            "model": model,
            "runs": runs,
            "stamp": _stamp(model),
            "rows": [r.__dict__ for r in rows],
            "failed": len(failed),
        },
    )
    stop = baseline_stop(rows)
    print(f"checks: {len(rows)} rows, {len(failed)} failed; phase-1 stop: {stop}")
    return 0


def cmd_synth(out: pathlib.Path, model: str) -> int:
    """Phase B, answer model only. Refuses when the phase-1 stop fired."""
    from openkos.config import DEFAULT_CONTEXT_WINDOW, DEFAULT_MAX_GENERATION_TOKENS
    from openkos.llm.ollama import OllamaClient

    checks = json.loads((out / "checks.json").read_text(encoding="utf-8"))
    rows = [CheckRow(**r) for r in checks["rows"]]
    stop = baseline_stop(rows)
    if stop is not None:
        print(f"refusing to synthesize: phase-1 stop {stop}")
        return 1
    llm = OllamaClient(
        model=model,
        max_generation_tokens=DEFAULT_MAX_GENERATION_TOKENS,
        context_window=DEFAULT_CONTEXT_WINDOW,
        timeout=TIMEOUT,
    )
    synths, failed = collect_synth(_load_contexts(out), llm, checks["runs"])
    _write(
        out / "synth.json",
        {
            "model": model,
            "stamp": _stamp(model),
            "rows": [s.__dict__ for s in synths],
            "failed": len(failed),
        },
    )
    print(f"synth: {len(synths)} rows, {len(failed)} failed")
    return 0


def _load_cells(out: pathlib.Path) -> tuple[list[Cell], int, int]:
    checks = json.loads((out / "checks.json").read_text(encoding="utf-8"))
    synth = json.loads((out / "synth.json").read_text(encoding="utf-8"))
    crows = [CheckRow(**r) for r in checks["rows"]]
    srows = [SynthRow(**{**r, "cited": tuple(r["cited"])}) for r in synth["rows"]]
    cells = merge_cells(crows, srows)
    total = checks["runs"] * len(QUESTIONS)
    return cells, total - len(cells), total


def cmd_score(out: pathlib.Path) -> int:
    """Every metric, no bar applied."""
    cells, failed, total = _load_cells(out)
    print(f"cells {len(cells)} of {total} ({failed} failed)")
    for arm in ARMS:
        print(arm, json.dumps(score_arm(cells, arm), sort_keys=True))
    return 0


def cmd_decide(out: pathlib.Path) -> int:
    cells, failed, total = _load_cells(out)
    for arm, (verdict, why) in decide(cells, failed, total).items():
        print(f"{arm}: {verdict} {','.join(why)}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument(
        "command", nargs="?", choices=("contexts", "checks", "synth", "score", "decide")
    )
    parser.add_argument("--out", type=pathlib.Path, default=OUT_DEFAULT)
    parser.add_argument("--check-model", default=CHECK_MODEL)
    parser.add_argument("--synth-model", default=SYNTH_MODEL)
    parser.add_argument("--runs", type=int, default=int(BARS["RUNS"]))
    args = parser.parse_args(argv)
    if args.self_test:
        return _self_test()
    if args.command == "contexts":
        return cmd_contexts(args.out)
    if args.command == "checks":
        return cmd_checks(args.out, args.check_model, args.runs)
    if args.command == "synth":
        return cmd_synth(args.out, args.synth_model)
    if args.command == "score":
        return cmd_score(args.out)
    if args.command == "decide":
        return cmd_decide(args.out)
    return parser.error("choose a subcommand or --self-test")


if __name__ == "__main__":
    raise SystemExit(main())
