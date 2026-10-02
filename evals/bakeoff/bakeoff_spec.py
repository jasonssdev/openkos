"""The #1269 bake-off, as DATA: candidates, eligibility limits, harness plan, bars.

Every number here is transcribed from the issue body and from the binding
pre-registration comment on #1269 ("Pre-registration: numeric bars, latency
budget, run plan"; cited below as "pre-reg"). Nothing is invented or tuned:
where the pre-registration states a rule but not a number the code needs, the
choice is a named constant with its reasoning, and the bake-off README lists
it under "Interpretation choices" so a reviewer can overrule it.

This module holds no logic beyond lookups: `bakeoff_bars.py` evaluates bars,
`bakeoff_metrics.py` produces the metrics, `run_bakeoff.py` executes the plan.

Usage:

    python evals/bakeoff/bakeoff_spec.py --self-test
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass

sys.path.append(str(pathlib.Path(__file__).resolve().parent))

import bakeoff_metrics
from bakeoff_bars import Bar, Mode, Op, Role

EVALS_ROOT = pathlib.Path(__file__).resolve().parents[1]

BASELINE = "qwen3:8b"
"""The baseline model (issue, "Candidates")."""

RUNS = 15
"""Runs per arm (issue, protocol: "15 runs per arm"; pre-reg: "n=15")."""

BUDGET_GB = 24.0
"""Memory ceiling for Ollama: 32 GB machine, 25% reserved (issue, summary).
Decimal GB, the unit `ollama list` and `ollama ps` print."""

PRODUCTION_NUM_CTX = 12288
MEMORY_PROBE_CONTEXTS = (PRODUCTION_NUM_CTX, 32768)
"""`ollama ps` peak is read at both (pre-reg, run plan step 1). Only the
production setting gates (rule 6.3: "fits the memory budget at production
settings"); 32768 is reported, and decides which residency layouts exist."""

MIN_NATIVE_CONTEXT = 12288
EMBEDDING_MODEL = "bge-m3"
"""Held resident while a candidate's memory is measured (issue, rule 6.3)."""

LICENSE_ALLOWLIST = ("apache-2.0", "mit")
"""Issue: "any candidate whose license is not Apache-2.0 or MIT is dropped"."""

SPEED_BASIS = "qwen3:8b"


@dataclass(frozen=True)
class Candidate:
    tag: str
    families: tuple[str, ...]
    approx_gb: float
    speed_multiplier: float
    multiplier_basis: str


CANDIDATES: tuple[Candidate, ...] = (
    Candidate("qwen3.6:35b-a3b", ("generate",), 23.5, 1.5, "assumed (MoE)"),
    Candidate("qwen3.6:27b", ("generate",), 17.0, 4.5, "assumed (27B dense)"),
    Candidate("gemma4:12b", ("generate", "judge", "label"), 8.0, 1.5, "assumed (12B)"),
    Candidate(
        "mistral-small3.2:24b",
        ("generate", "judge", "label"),
        15.0,
        3.5,
        "assumed (24B)",
    ),
    Candidate("gemma4:26b-a4b", ("judge", "label"), 16.0, 1.5, "assumed (MoE)"),
    Candidate("phi4:14b", ("judge", "label"), 9.0, 2.2, "measured on edge typing"),
)
"""Issue, "Candidates", with the pre-reg's speed multipliers (`qwen3:8b`
basis; the pre-reg's forecast paragraph). Sizes are the issue-stated ones."""

BASELINE_CANDIDATE = Candidate(
    BASELINE, ("generate", "judge", "label", "write"), 5.2, 1.0, "baseline"
)


@dataclass(frozen=True)
class Invocation:
    """One harness CLI call. `{model}`, `{runs}`, `{label}` and `{out}` (the
    cell directory) are substituted by the runner."""

    arm: str
    argv: tuple[str, ...]


@dataclass(frozen=True)
class HarnessSpec:
    name: str
    script: str
    family: str
    role: str
    forecast_minutes: float
    """Minutes for one 15-run sweep on the baseline (pre-reg forecast)."""
    invocations: tuple[Invocation, ...]
    bars: tuple[Bar, ...]
    baseline_repeats: int = 1
    """2 where no 15-run baseline exists on the current fixture (pre-reg:
    "the baseline runs twice (two sessions)"), which supplies the spread."""
    writes_to_results_dir: bool = True
    """False when the CLI takes an explicit output path (the cap harness)."""
    decide_after: bool = False
    """auto_merge: `--decide` over its two arms yields the verdict file."""


_PRE = "pre-reg"
CLARIFICATION = "#1269, comment 'Clarifications before the first run'"
"""Owner decisions settled before the first run, encoded here and in
`bakeoff_bars.py`: (1) the same-family arm is NOT MEASURED this round (see
`NOT_MEASURED_THIS_ROUND`); (2) Qwen3.6 runs thinking OFF only, as production
does, with no client change; (3) a blown latency budget DROPS a candidate for
the family at the first harness where it is known at n=15. Also accepted there:
cheapest-first stage order (`stage_order`), "top 2 Generate" ranked by mean
per-fixture pre-cap recall gain, transitivity read `<= baseline + 0.03` (I6),
and "persistently wrong" = wrong in more than half the runs
(`bakeoff_metrics.PERSISTENT_WRONG_SHARE`)."""

NOT_MEASURED_THIS_ROUND = (
    "same-family arm per judge harness (self-preference hypothesis): the judge "
    "harnesses score fixed hand-written fixtures, so there is no generator "
    "model to match a family against; left for a dedicated experiment "
    "(clarification 1)",
)


def _bar(
    bar_id: str,
    role: Role,
    metric: str,
    op: Op,
    mode: Mode,
    value: float,
    source: str,
    *,
    need: int | None = None,
    ceiling: float | None = None,
    waive_if_baseline_fails: bool = False,
) -> Bar:
    return Bar(
        bar_id,
        role,
        metric,
        op,
        mode,
        value,
        need=need,
        ceiling=ceiling,
        waive_if_baseline_fails=waive_if_baseline_fails,
        source=f"{_PRE}: {source}",
    )


_LABEL_BARS = (
    _bar(
        "L1-accuracy",
        "win",
        "accuracy",
        "ge",
        "delta",
        0.10,
        "Label row, accuracy >= baseline + 0.10",
        ceiling=1.0,
    ),
    _bar(
        "L2-discriminated",
        "win",
        "direction_discriminated_share",
        "ge",
        "delta",
        0.15,
        "Label row, discriminated >= baseline + 0.15",
        ceiling=1.0,
    ),
    _bar(
        "L3-blind",
        "gate",
        "direction_blind_share",
        "le",
        "delta",
        0.05,
        "Label row, blind <= baseline + 0.05",
    ),
    _bar(
        "L4-stability",
        "gate",
        "stability",
        "ge",
        "delta",
        -0.05,
        "Label row, stability >= baseline - 0.05",
    ),
    _bar(
        "L5-degraded",
        "gate",
        "degraded",
        "le",
        "absolute",
        0,
        "Label row, degraded replies 0",
    ),
    _bar(
        "L6-latency",
        "budget",
        "latency_median_run_s",
        "le",
        "ratio",
        3.0,
        "Latency budget, Label <= 3x",
    ),
)

_CONTRADICTION_BARS = (
    # The #1275 R1 rule: at most 50% of baseline AND at least 15 cells fewer.
    _bar(
        "C1-field-shape-ratio",
        "win",
        "field_shape_wrong",
        "le",
        "ratio",
        0.5,
        "Judge/contradiction, <= 22 of 150 = the #1275 R1 rule, at most 50% of baseline",
    ),
    _bar(
        "C2-field-shape-delta",
        "win",
        "field_shape_wrong",
        "le",
        "delta",
        -15,
        "Judge/contradiction, the #1275 R1 rule, at least 15 fewer",
    ),
    _bar(
        "C3-persistent",
        "win",
        "persistent_still_wrong",
        "le",
        "absolute",
        1,
        "Judge/contradiction, at most 1 of the 3 persistently wrong cases still wrong",
    ),
    _bar(
        "C4-guards",
        "gate",
        "guards_missed",
        "le",
        "delta",
        2,
        "Judge/contradiction guards, contradiction missed <= baseline + 2 of 60",
    ),
    _bar(
        "C5-typed-tp",
        "gate",
        "typed_tp",
        "ge",
        "absolute",
        0.95,
        "Judge/contradiction guards, typed TP >= 0.95",
        waive_if_baseline_fails=True,
    ),
    _bar(
        "C6-evaluative",
        "gate",
        "evaluative_retention",
        "ge",
        "absolute",
        0.95,
        "Judge/contradiction guards, evaluative retention >= 0.95",
        waive_if_baseline_fails=True,
    ),
    _bar(
        "C7-typed-fp",
        "gate",
        "typed_fp_*",
        "le",
        "delta",
        0.05,
        "Judge/contradiction guards, typed FP <= baseline + 0.05",
    ),
    _bar(
        "C8-older-merged",
        "gate",
        "older_merged_compatible_wrong",
        "le",
        "delta",
        3,
        "Judge/contradiction guards, older merged compatible <= baseline + 3 of 120",
    ),
    _bar(
        "C9-latency",
        "budget",
        "latency_median_run_s",
        "le",
        "ratio",
        3.0,
        "Latency budget, Contradiction <= 3x",
    ),
)

_IDENTITY_BARS = (
    _bar(
        "I1-hard-negatives",
        "win",
        "hard_negative_different_rate",
        "ge",
        "delta",
        0.10,
        "Judge/identity, pooled `different` rate >= baseline + 0.10",
        ceiling=1.0,
    ),
    _bar(
        "I2-event-same",
        "gate",
        "event_same_rate",
        "ge",
        "delta",
        -0.03,
        "Judge/identity controls >= baseline - 0.03 (veto)",
    ),
    _bar(
        "I3-person-same",
        "gate",
        "person_same_rate",
        "ge",
        "delta",
        -0.03,
        "Judge/identity controls >= baseline - 0.03 (veto)",
    ),
    _bar(
        "I4-alias-same",
        "gate",
        "alias_same_rate",
        "ge",
        "delta",
        -0.03,
        "Judge/identity controls >= baseline - 0.03 (veto)",
    ),
    _bar(
        "I5-recurrence",
        "gate",
        "recurrence_precision",
        "ge",
        "delta",
        -0.03,
        "Judge/identity controls >= baseline - 0.03 (veto)",
    ),
    # Lower is better for a violation rate, so the veto reads <= baseline + 0.03.
    _bar(
        "I6-transitivity",
        "gate",
        "transitivity_violation_rate",
        "le",
        "delta",
        0.03,
        "Judge/identity controls, transitivity violation (read lower-is-better)",
    ),
    _bar(
        "I7-latency",
        "budget",
        "latency_median_run_s",
        "le",
        "ratio",
        3.0,
        "Latency budget, Identity <= 3x",
    ),
)

_SUFFICIENCY_BARS = (
    _bar(
        "S1-grounded",
        "gate",
        "grounded_refused",
        "le",
        "absolute",
        0,
        "Judge/sufficiency veto, 0 of 150 grounded checks refused",
        waive_if_baseline_fails=True,
    ),
    _bar(
        "S2-adjacent",
        "gate",
        "adjacent_missed",
        "le",
        "absolute",
        2,
        "Judge/sufficiency veto, adjacent refused >= 148 of 150",
        waive_if_baseline_fails=True,
    ),
    _bar(
        "S3-survivors",
        "gate",
        "survivors_missed",
        "le",
        "absolute",
        0,
        "Judge/sufficiency veto, survivors 45 of 45",
        waive_if_baseline_fails=True,
    ),
    _bar(
        "S4-latency",
        "budget",
        "latency_median_nonrefused_s",
        "le",
        "ratio",
        2.0,
        "Latency budget, answer + sufficiency <= 2x baseline median",
    ),
)

_REVISION_BARS = tuple(
    _bar(
        f"R{n}-b{n}",
        "gate",
        f"b{n}_pass",
        "ge",
        "absolute",
        1.0,
        "Judge/revisions, all of B1-B8 pass (veto)",
        waive_if_baseline_fails=True,
    )
    for n in range(1, 9)
)

_AUTO_MERGE_BARS = (
    # "A candidate PASS is a win; a FAIL is 'not worse'" -- and a baseline FAIL
    # makes a FAIL no worse. If the baseline PASSES, a candidate FAIL is worse.
    _bar(
        "A1-not-worse",
        "gate",
        "verdict_pass",
        "ge",
        "delta",
        0.0,
        "Judge/auto_merge, a FAIL is 'not worse' than a baseline FAIL",
    ),
    _bar(
        "A2-pass",
        "win",
        "verdict_pass",
        "ge",
        "absolute",
        1.0,
        "Judge/auto_merge, a candidate PASS is a win",
    ),
)

_GENERATE_BARS = (
    _bar(
        "G0-adjudicated",
        "precondition",
        "unjudged_titles",
        "le",
        "absolute",
        0,
        "Generate row, unjudged titles = 0 after adjudication",
    ),
    _bar(
        "G1-recall-win",
        "win",
        "recall_precap:*",
        "ge",
        "delta",
        0.10,
        "Generate row, recall >= baseline + 0.10 on at least 3 of 5 fixtures",
        need=3,
        ceiling=1.0,
    ),
    _bar(
        "G2-recall-floor",
        "gate",
        "recall_precap:*",
        "ge",
        "delta",
        -0.10,
        "Generate row, none below baseline - 0.10",
    ),
    _bar(
        "G3-facets",
        "gate",
        "facets_per_run",
        "le",
        "delta",
        0.5,
        "Generate row, facets/run <= baseline + 0.5",
    ),
    _bar(
        "G4-precision",
        "gate",
        "precision_precap",
        "ge",
        "delta",
        -0.05,
        "Generate row, precision >= baseline - 0.05",
    ),
    _bar(
        "G5-latency-fixture",
        "budget",
        "latency_median_s:*",
        "le",
        "ratio",
        1.5,
        "Latency budget, Generate <= 1.5x baseline median per fixture",
    ),
    _bar(
        "G6-latency-total",
        "budget",
        "latency_sweep_total_s",
        "le",
        "ratio",
        1.5,
        "Latency budget, Generate <= 1.5x for the sweep total",
    ),
)

_ATTRIBUTION_BARS = (
    _bar(
        "W1-attribution",
        "gate",
        "compliance",
        "ge",
        "delta",
        -0.03,
        "Write row, attribution compliance >= baseline - 0.03 (veto)",
    ),
    _bar(
        "W2-latency",
        "budget",
        "latency_median_answer_s",
        "le",
        "ratio",
        2.0,
        "Latency budget, answer <= 2x baseline median (answer baseline measured in-sweep)",
    ),
)

HARNESSES: tuple[HarnessSpec, ...] = (
    HarnessSpec(
        "edge_typing",
        "edge_typing/run_edge_typing_eval.py",
        "label",
        "label",
        11,
        (
            Invocation(
                "main", ("--arm", "{label}", "--runs", "{runs}", "--model", "{model}")
            ),
        ),
        _LABEL_BARS,
        baseline_repeats=2,
    ),
    HarnessSpec(
        "contradictions",
        "contradictions/run_contradictions_eval.py",
        "judge",
        "contradiction",
        30,
        (
            Invocation(
                "main", ("--arm", "baseline", "--runs", "{runs}", "--model", "{model}")
            ),
        ),
        _CONTRADICTION_BARS,
        baseline_repeats=2,
    ),
    HarnessSpec(
        "adjudication",
        "adjudication/run_adjudication_eval.py",
        "judge",
        "identity",
        18,
        (
            Invocation(
                "main", ("--arm", "baseline", "--runs", "{runs}", "--model", "{model}")
            ),
        ),
        _IDENTITY_BARS,
        baseline_repeats=2,
    ),
    HarnessSpec(
        "query_sufficiency",
        "query_sufficiency/run_query_sufficiency_probe.py",
        "judge",
        "sufficiency",
        11,
        (
            Invocation(
                "main",
                (
                    "--runs",
                    "{runs}",
                    "--model",
                    "{model}",
                    "--stamp",
                    "{label}",
                    "--arms",
                    "quote",
                ),
            ),
        ),
        _SUFFICIENCY_BARS,
        baseline_repeats=2,
    ),
    HarnessSpec(
        "decision_revisions",
        "decision_revisions/run_decision_revisions_eval.py",
        "judge",
        "revisions",
        70,
        (Invocation("main", ("--runs", "{runs}", "--model", "{model}")),),
        _REVISION_BARS,
    ),
    HarnessSpec(
        "auto_merge",
        "auto_merge/run_auto_merge_eval.py",
        "judge",
        "identity",
        35,
        (
            Invocation(
                "calibration",
                ("--arm", "calibration", "--runs", "{runs}", "--model", "{model}"),
            ),
            Invocation(
                "confirmation",
                ("--arm", "confirmation", "--runs", "{runs}", "--model", "{model}"),
            ),
        ),
        _AUTO_MERGE_BARS,
        decide_after=True,
    ),
    HarnessSpec(
        "extraction_cap",
        "extraction_cap/run_cap_eval.py",
        "generate",
        "generate",
        63,
        (
            Invocation(
                "main",
                (
                    "--model",
                    "{model}",
                    "--runs",
                    "{runs}",
                    "--union-judge",
                    "on",
                    "--output",
                    "{out}/report.md",
                ),
            ),
        ),
        _GENERATE_BARS,
        baseline_repeats=2,
        writes_to_results_dir=False,
    ),
    HarnessSpec(
        "query_attribution",
        "query_attribution/run_query_attribution_probe.py",
        "write",
        "answer",
        100,
        (
            Invocation(
                "main", ("--arm", "baseline", "--runs", "{runs}", "--model", "{model}")
            ),
        ),
        _ATTRIBUTION_BARS,
        baseline_repeats=2,
    ),
)

FAMILY_ORDER = ("label", "judge", "generate", "write")
"""Label, Judge, Generate, then Write last (pre-reg, run plan 2 and 3)."""

WRITE_TOP_N = 2
"""Write runs with the top 2 Generate candidates only (owner decision 2)."""

DERIVERS_BY_HARNESS = bakeoff_metrics.DERIVERS


def harness(name: str) -> HarnessSpec:
    for spec in HARNESSES:
        if spec.name == name:
            return spec
    raise KeyError(name)


def stage_order(family: str) -> tuple[HarnessSpec, ...]:
    """A family's harnesses, cheapest first (owner decision 2: "within a
    family, cheapest harness first"; the cost is the pre-reg forecast)."""
    return tuple(
        sorted(
            (h for h in HARNESSES if h.family == family),
            key=lambda h: (h.forecast_minutes, h.name),
        )
    )


def candidates_for(family: str) -> tuple[Candidate, ...]:
    return tuple(c for c in CANDIDATES if family in c.families)


def forecast_minutes(
    spec: HarnessSpec, candidate: Candidate, runs: int = RUNS
) -> float:
    """Pre-reg forecast scaled to `runs` and to the candidate's speed."""
    return spec.forecast_minutes * (runs / RUNS) * candidate.speed_multiplier


def cell_label(candidate_tag: str, repeat: int) -> str:
    """A filesystem- and CLI-safe label for one cell."""
    slug = candidate_tag.replace(":", "-").replace("/", "-")
    return f"bakeoff-{slug}-r{repeat}"


# --------------------------------------------------------------------------
# Self-test: the data is internally consistent.
# --------------------------------------------------------------------------


def _self_test() -> int:
    failures: list[str] = []
    checks = 0

    def check(label: str, got: object, want: object) -> None:
        nonlocal checks
        checks += 1
        if got != want:
            failures.append(f"{label}: got {got!r}, want {want!r}")

    check(
        "every harness has an extractor",
        sorted(h.name for h in HARNESSES),
        sorted(bakeoff_metrics.EXTRACTORS),
    )
    for h in HARNESSES:
        check(f"{h.name}: runner exists", (EVALS_ROOT / h.script).is_file(), True)
        check(f"{h.name}: family is known", h.family in FAMILY_ORDER, True)
        check(f"{h.name}: has bars", len(h.bars) > 0, True)
        check(f"{h.name}: has a forecast", h.forecast_minutes > 0, True)
        ids = [b.id for b in h.bars]
        check(f"{h.name}: bar ids are unique", len(ids) == len(set(ids)), True)
        undeclared = [
            b.metric for b in h.bars if not bakeoff_metrics.declares(h.name, b.metric)
        ]
        check(
            f"{h.name}: every bar reads a metric its extractor produces", undeclared, []
        )
        uncited = [b.id for b in h.bars if not b.source.startswith("pre-reg:")]
        check(f"{h.name}: every bar cites the pre-registration", uncited, [])
        for inv in h.invocations:
            placeholders = {"model", "runs", "label", "out"}
            joined = " ".join(inv.argv)
            check(
                f"{h.name}/{inv.arm}: passes the model and the runs",
                "{model}" in joined and "{runs}" in joined,
                True,
            )
            stray = [
                w for w in re.findall(r"\{(\w+)\}", joined) if w not in placeholders
            ]
            check(f"{h.name}/{inv.arm}: only known placeholders", stray, [])
        check(
            f"{h.name}: a harness writing to an explicit path says where",
            h.writes_to_results_dir
            or any("{out}" in " ".join(i.argv) for i in h.invocations),
            True,
        )
    check(
        "harnesses with a derived metric have a deriver",
        all(n in DERIVERS_BY_HARNESS for n in ("contradictions",)),
        True,
    )

    cheapest = [h.name for h in stage_order("judge")]
    check(
        "judge stages run cheapest first",
        cheapest,
        [
            "query_sufficiency",
            "adjudication",
            "contradictions",
            "auto_merge",
            "decision_revisions",
        ],
    )
    check(
        "label has one stage", [h.name for h in stage_order("label")], ["edge_typing"]
    )
    check(
        "write has one stage",
        [h.name for h in stage_order("write")],
        ["query_attribution"],
    )

    tags = [c.tag for c in CANDIDATES]
    check("candidate tags are unique", len(tags) == len(set(tags)), True)
    check("the baseline is not a candidate", BASELINE in tags, False)
    check(
        "the retired gemma2 and gpt-oss are not candidates",
        [t for t in tags if t.startswith(("gemma2", "gemma3", "gpt-oss"))],
        [],
    )
    check(
        "every candidate declares a family", all(c.families for c in CANDIDATES), True
    )
    check(
        "generate candidates",
        [c.tag for c in candidates_for("generate")],
        ["qwen3.6:35b-a3b", "qwen3.6:27b", "gemma4:12b", "mistral-small3.2:24b"],
    )
    check(
        "judge/label candidates",
        [c.tag for c in candidates_for("label")],
        ["gemma4:12b", "mistral-small3.2:24b", "gemma4:26b-a4b", "phi4:14b"],
    )
    check(
        "no write candidates (Write takes the top Generate survivors)",
        candidates_for("write"),
        (),
    )

    # Pre-reg constants.
    check(
        "same-family arm is recorded as not measured",
        any("same-family" in i for i in NOT_MEASURED_THIS_ROUND),
        True,
    )
    check(
        "clarification is cited",
        "Clarifications before the first run" in CLARIFICATION,
        True,
    )
    check("n=15", RUNS, 15)
    check("budget", BUDGET_GB, 24.0)
    check("production context", PRODUCTION_NUM_CTX, 12288)
    check("both memory contexts", MEMORY_PROBE_CONTEXTS, (12288, 32768))
    check("license allowlist", LICENSE_ALLOWLIST, ("apache-2.0", "mit"))
    check("write takes two", WRITE_TOP_N, 2)

    # Spot-check transcribed numbers against the pre-registration table.
    by_id = {b.id: b for h in HARNESSES for b in h.bars}
    check(
        "L1 accuracy +0.10",
        (by_id["L1-accuracy"].value, by_id["L1-accuracy"].op),
        (0.10, "ge"),
    )
    check("L2 discriminated +0.15", by_id["L2-discriminated"].value, 0.15)
    check("L3 blind +0.05", by_id["L3-blind"].value, 0.05)
    check("L4 stability -0.05", by_id["L4-stability"].value, -0.05)
    check("C-latency 3x", by_id["C9-latency"].value, 3.0)
    check("S-latency 2x", by_id["S4-latency"].value, 2.0)
    check(
        "G recall quorum 3 of 5, +0.10",
        (by_id["G1-recall-win"].need, by_id["G1-recall-win"].value),
        (3, 0.10),
    )
    check("G recall floor -0.10", by_id["G2-recall-floor"].value, -0.10)
    check("G facets +0.5", by_id["G3-facets"].value, 0.5)
    check("G precision -0.05", by_id["G4-precision"].value, -0.05)
    check("G latency 1.5x", by_id["G5-latency-fixture"].value, 1.5)
    check("I hard-negative +0.10", by_id["I1-hard-negatives"].value, 0.10)
    check("I controls -0.03", by_id["I2-event-same"].value, -0.03)
    check(
        "C guards +2, older +3",
        (by_id["C4-guards"].value, by_id["C8-older-merged"].value),
        (2, 3),
    )
    check(
        "C typed TP and evaluative 0.95",
        (by_id["C5-typed-tp"].value, by_id["C6-evaluative"].value),
        (0.95, 0.95),
    )
    check(
        "C R1 rule: 50% and 15 fewer",
        (by_id["C1-field-shape-ratio"].value, by_id["C2-field-shape-delta"].value),
        (0.5, -15),
    )
    check("W attribution -0.03", by_id["W1-attribution"].value, -0.03)
    check("S adjacent <= 2 missed of 150 (>= 148)", by_id["S2-adjacent"].value, 2)
    check(
        "a Write cell's baseline needs two sessions",
        harness("query_attribution").baseline_repeats,
        2,
    )

    check(
        "placeholder substitution target exists",
        cell_label("qwen3.6:35b-a3b", 1),
        "bakeoff-qwen3.6-35b-a3b-r1",
    )
    check(
        "forecast scales with speed and runs",
        forecast_minutes(harness("edge_typing"), CANDIDATES[4], 15),
        11 * 1.5,
    )
    check(
        "forecast scales with runs",
        forecast_minutes(harness("edge_typing"), BASELINE_CANDIDATE, 30),
        22.0,
    )

    for name in failures:
        print(f"FAIL: {name}")
    print(f"self-test: {checks - len(failures)}/{checks} passed")
    return 1 if failures else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="check the plan data is internally consistent, with no model",
    )
    args = parser.parse_args(argv)
    if args.self_test:
        return _self_test()
    parser.error("nothing to run without --self-test; import this module instead")


if __name__ == "__main__":
    sys.exit(main())
