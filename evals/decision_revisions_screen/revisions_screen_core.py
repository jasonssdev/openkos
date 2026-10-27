"""The deterministic half of the decision-revisions model screen (#1563).

The decision-revision judge asks a model, once per candidate pair of Decisions,
how the later one relates to the earlier one's choice: `reverses`, `refines`,
`reaffirms` or `unrelated` (`resolution/decision_revision.py`, `judge_pairs`).
A `reverses`/`refines` verdict with enough confidence and two verified quotes is
an *actionable* finding: it goes to the review queue, where a person accepts or
declines it. This module holds everything about measuring that which needs no
model: the labelled items, the scoring of one verdict, the zero-model baselines,
the aggregate numbers and the vetoes. The runner (`run_revisions_screen.py`) owns
the model.

The labelled pairs are NOT written here. They are the 58 pairs of
`evals/decision_revisions/revision_fixture_library.py` (the harness that measured
`qwen3:8b` for #1014), loaded through its own loader so the two never drift. The
labels are one drafter's, with the owner having accepted the 16 contested ones;
no blind second labeller has checked them (`README.md`). An optional
`adjudication.json` beside this file records a second adjudication once one
exists; it does not exist yet.

Imports the standard library, the fixture loader, the shared statistics of the
edge-typing screen and the production modules that carry the vocabulary being
measured, so a unit test can load it straight from its path.
"""

from __future__ import annotations

import importlib.util
import json
import math
import pathlib
import re
import statistics
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise
from typing import Any, Final

HERE: Final = pathlib.Path(__file__).resolve().parent
REPO_ROOT: Final = HERE.parents[1]
ADJUDICATION_PATH: Final = HERE / "adjudication.json"

sys.path.insert(0, str(REPO_ROOT / "src"))
# Appended, never inserted first: the fixture library's own sibling module is
# `revision_fixtures`, and nothing earlier on the path may shadow it.
sys.path.append(str(REPO_ROOT / "evals" / "decision_revisions"))

from revision_fixture_library import load_library_fixture  # noqa: E402
from revision_fixtures import resolve_decision_date  # noqa: E402

from openkos.model import okf  # noqa: E402
from openkos.resolution.decision_revision import (  # noqa: E402
    DecisionDate,
    RevisionVerdictValue,
    pair_direction,
)


def _load_edge_core() -> Any:
    """The edge-typing screen's statistics (`paired_bootstrap`, `selected`,
    `justified`, `forecast`-free helpers) are task-agnostic; they are reused
    rather than copied a fifth time."""
    path = REPO_ROOT / "evals" / "edge_typing_screen" / "edge_screen_core.py"
    spec = importlib.util.spec_from_file_location("_rev_screen_edge_core", path)
    if spec is None or spec.loader is None:
        raise ImportError(path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_EDGE: Final = _load_edge_core()


def paired_bootstrap(
    candidate: Mapping[str, float],
    reference: Mapping[str, float],
    *,
    resamples: int = 10_000,
    seed: int = 42,
) -> dict[str, float]:
    """Mean per-item difference `candidate - reference` and its 95% percentile
    bootstrap interval over items (the edge-typing screen's, unchanged)."""
    return dict(
        _EDGE.paired_bootstrap(candidate, reference, resamples=resamples, seed=seed)
    )


def selected(delta: Mapping[str, float], tripped: Sequence[str]) -> bool:
    """A candidate replaces the baseline only with a positive 95% lower bound on
    its paired gain and no veto."""
    return bool(_EDGE.selected(delta, tripped))


def justified(delta_vs_reference: Mapping[str, float]) -> bool:
    """The task key is justified for a model only if its paired gain over the
    strongest model-free predictor has a positive 95% lower bound."""
    return bool(_EDGE.justified(delta_vs_reference))


LABELS: Final = ("REVERSES", "REFINES", "REAFFIRMS", "UNRELATED")
"""The fixture's label vocabulary, in a fixed order (upper case, as the fixture
writes it; the judge's wire values are the lower-case `RevisionVerdictValue`s)."""

WIRE: Final = {label: label.lower() for label in LABELS}
if set(WIRE.values()) != {v.value for v in RevisionVerdictValue}:
    raise ImportError("the fixture labels no longer match the judge's verdicts")

SPLITS: Final = ("original", "confirmation")
CHANGE: Final = "change"
"""What REVERSES and REFINES both become on an undirected pair: production never
lets the model's choice between them stand there (`relation_for` returns `None`
for an undirected change, the person names the type), so the screen does not
score it either."""

FAILURE_ABS_CAP: Final = 0.05
FAILURE_MARGIN: Final = 0.02
LATENCY_RATIO_CAP: Final = 2.0
SUPERSEDE_MARGIN: Final = 0.10
"""The vetoes of `PREREGISTRATION-DRAFT.md`."""

BASELINE_NAMES: Final = (
    "always unrelated",
    "always reverses",
    "always refines",
    "always reaffirms",
    "lexical overlap (oracle cut)",
)

LEXICAL_BASELINE: Final = "lexical overlap (oracle cut)"


_NO_DATE: Final = DecisionDate(value=None, state="missing")


@dataclass(frozen=True)
class Item:
    """One question: a pair of Decisions and how the later relates to the earlier."""

    id: str
    pair_ids: tuple[str, str]
    label: str
    directed: bool
    split: str
    contested: bool
    hard_case: str | None
    note: str
    titles: tuple[str, str]
    bodies: tuple[str, str]
    earlier: int | None = None
    """Index into `pair_ids` of the EARLIER Decision when the dates order the pair
    (the order production shows it in), else `None`: the judge then sees the pair
    in sorted-id order under `ORDER UNKNOWN`."""
    dates: tuple[DecisionDate, DecisionDate] = (_NO_DATE, _NO_DATE)
    """Each Decision's resolved date as production resolves it, aligned with
    `pair_ids`; the judge is handed exactly these."""
    adjudication: Mapping[str, str] | None = None
    """The second-adjudication record (`first`, `second`, `resolved`, `reason`) of
    an item whose two adjudicators disagreed; `None` otherwise, and for every item
    until a blind second adjudication is recorded."""

    @property
    def first_label(self) -> str:
        """The first author's label: `label` is the RESOLVED one."""
        return self.adjudication["first"] if self.adjudication else self.label


# --- the fixture -------------------------------------------------------------


def _pair_key(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a <= b else (b, a)


def date_text(item: Item, index: int) -> str:
    """Decision `index`'s date as production prints it in the judge's header."""
    value = item.dates[index].value
    if value is None:
        raise ValueError(f"{item.id}: Decision {index} has no resolved date")
    return okf.format_event_when(value)


def pair_key_text(pair_ids: tuple[str, str]) -> str:
    return "|".join(pair_ids)


def _overlay(path: pathlib.Path) -> dict[str, Mapping[str, str]]:
    if not path.is_file():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {str(k): dict(v) for k, v in raw.items()}


def load_items(adjudication_path: pathlib.Path = ADJUDICATION_PATH) -> list[Item]:
    """The labelled items, ordered by sorted pair key, ids `r01`... in that order.

    Raises `ValueError` on an overlay record that names a pair the fixture does
    not hold, or whose `first` differs from the fixture's own label (the first
    author's label must stay recoverable)."""
    fixture = load_library_fixture()
    sources = {s.source_id: s for s in fixture.sources}
    decisions = {d.concept_id: d for d in fixture.decisions}
    overlay = _overlay(adjudication_path)
    pairs = sorted(fixture.pairs, key=lambda p: _pair_key(*p.decision_ids))
    known = {pair_key_text(_pair_key(*p.decision_ids)) for p in pairs}
    for key in overlay:
        if key not in known:
            raise ValueError(f"adjudication names an unknown pair {key!r}")
    items: list[Item] = []
    for number, pair in enumerate(pairs, start=1):
        ids = _pair_key(*pair.decision_ids)
        record = overlay.get(pair_key_text(ids))
        label: str = pair.expected_verdict
        if record is not None:
            if set(record) != {"first", "second", "resolved", "reason"}:
                raise ValueError(
                    f"{ids}: adjudication needs first/second/resolved/reason"
                )
            if record["first"] != label:
                raise ValueError(
                    f"{ids}: adjudication `first` {record['first']!r} is not the "
                    f"fixture's label {label!r}"
                )
            if record["resolved"] not in LABELS:
                raise ValueError(f"{ids}: adjudication must resolve to a label")
            label = record["resolved"]
        a, b = decisions[ids[0]], decisions[ids[1]]
        directed = pair.expected_later_id is not None
        when = [resolve_decision_date(d, sources) for d in (a, b)]
        direction = pair_direction(ids[0], when[0], ids[1], when[1])
        if (direction.holder is not None) != directed:
            raise ValueError(f"{ids}: the dates no longer give the fixture's direction")
        earlier = None if direction.earlier is None else ids.index(direction.earlier)
        items.append(
            Item(
                id=f"r{number:02d}",
                pair_ids=ids,
                label=label,
                directed=directed,
                split=pair.split,
                contested=pair.contested,
                hard_case=pair.hard_case,
                note=pair.note,
                titles=(a.title, b.title),
                bodies=(a.body, b.body),
                earlier=earlier,
                dates=(when[0], when[1]),
                adjudication=None if record is None else dict(record),
            )
        )
    return items


def class_balance(items: Sequence[Item]) -> dict[str, int]:
    counts = Counter(i.label for i in items)
    return {label: counts.get(label, 0) for label in LABELS}


def split_balance(items: Sequence[Item]) -> dict[str, int]:
    counts = Counter(i.split for i in items)
    return {s: counts.get(s, 0) for s in SPLITS}


# --- scoring -----------------------------------------------------------------


def scored_class(item: Item, verdict: str) -> str:
    """The class a verdict (wire or fixture spelling) is scored as, for `item`:
    on an undirected pair REVERSES and REFINES are one `change`."""
    wire = verdict.lower()
    if not item.directed and wire in (WIRE["REVERSES"], WIRE["REFINES"]):
        return CHANGE
    return wire


def is_correct(item: Item, verdict: str | None) -> bool:
    """`True` iff the verdict is the item's label, up to the undirected
    equivalence. A failed call (`None`) is wrong, never skipped."""
    if verdict is None:
        return False
    return scored_class(item, verdict) == scored_class(item, item.label)


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _p95(values: Sequence[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[math.ceil(0.95 * len(ordered)) - 1]


def summarize(
    rows: Sequence[Mapping[str, Any]], items: Sequence[Item]
) -> dict[str, Any]:
    """Every number the screen reports for one model.

    Accuracy is the mean over ITEMS of each item's mean over runs, so every item
    weighs the same however many runs it has. The class recalls, the confusion
    matrix, the failure rate and the queue and supersede rates are over calls."""
    by_id = {i.id: i for i in items}
    mine = [r for r in rows if r["item"] in by_id]
    strict: dict[str, list[float]] = {}
    first: dict[str, list[float]] = {}
    answers: dict[str, list[str | None]] = {}
    confusion = {g: {**dict.fromkeys(WIRE.values(), 0), "none": 0} for g in LABELS}
    class_hits: dict[str, list[float]] = {g: [] for g in LABELS}
    failures: Counter[str] = Counter()
    supersede_calls = supersede_hits = 0
    change_calls = change_queued = quiet_calls = quiet_queued = 0
    for row in mine:
        item = by_id[row["item"]]
        verdict = row["verdict"]
        right = is_correct(item, verdict)
        strict.setdefault(item.id, []).append(float(right))
        first.setdefault(item.id, []).append(
            float(
                verdict is not None
                and scored_class(item, verdict) == scored_class(item, item.first_label)
            )
        )
        answers.setdefault(item.id, []).append(verdict)
        class_hits[item.label].append(float(right))
        confusion[item.label][verdict if verdict is not None else "none"] += 1
        if row.get("failure"):
            failures[str(row["failure"])] += 1
        if item.directed and item.label != "REVERSES":
            supersede_calls += 1
            supersede_hits += verdict == WIRE["REVERSES"]
        if item.label in ("REVERSES", "REFINES"):
            change_calls += 1
            change_queued += bool(row.get("actionable"))
        else:
            quiet_calls += 1
            quiet_queued += bool(row.get("actionable"))
    repeated = [a for a in answers.values() if len(a) >= 2]
    latencies = [float(r["latency_s"]) for r in mine]
    n = len(mine)
    per_item = {k: sum(v) / len(v) for k, v in strict.items()}
    uncontested = [v for k, v in per_item.items() if not by_id[k].contested]
    return {
        "n_calls": n,
        "n_items": len(strict),
        "strict_accuracy": _mean(list(per_item.values())),
        "strict_first_accuracy": _mean([sum(v) / len(v) for v in first.values()]),
        "strict_uncontested_accuracy": _mean(uncontested),
        "strict_by_split": {
            s: _mean([v for k, v in per_item.items() if by_id[k].split == s])
            for s in SPLITS
            if any(by_id[k].split == s for k in per_item)
        },
        "per_class_recall": {g: _mean(class_hits[g]) for g in LABELS},
        "confusion": confusion,
        "failure_rate": sum(failures.values()) / n if n else None,
        "failures": dict(failures),
        "false_supersede_rate": (
            supersede_hits / supersede_calls if supersede_calls else None
        ),
        "queue_recall": change_queued / change_calls if change_calls else None,
        "queue_noise_rate": quiet_queued / quiet_calls if quiet_calls else None,
        "stability": (
            sum(len(set(a)) == 1 for a in repeated) / len(repeated)
            if repeated
            else None
        ),
        "median_latency_s": statistics.median(latencies) if latencies else None,
        "p95_latency_s": _p95(latencies),
    }


def per_item_strict(
    rows: Sequence[Mapping[str, Any]], items: Sequence[Item]
) -> dict[str, float]:
    """Each item's mean strict score over its runs, for the paired test."""
    by_id = {i.id: i for i in items}
    scores: dict[str, list[float]] = {}
    for row in rows:
        if row["item"] in by_id:
            scores.setdefault(row["item"], []).append(
                float(is_correct(by_id[row["item"]], row["verdict"]))
            )
    return {k: sum(v) / len(v) for k, v in scores.items()}


# --- the zero-model baselines ------------------------------------------------

_WORD: Final = re.compile(r"[a-z']+")


def overlap(item: Item) -> float:
    """Jaccard overlap of the two Decisions' title-and-body word sets."""
    sets = [
        set(_WORD.findall(f"{title} {body}".lower()))
        for title, body in zip(item.titles, item.bodies, strict=True)
    ]
    union = sets[0] | sets[1]
    return len(sets[0] & sets[1]) / len(union) if union else 0.0


def _constant(items: Sequence[Item], predicted: Sequence[str]) -> dict[str, Any]:
    strict = {
        i.id: float(is_correct(i, p)) for i, p in zip(items, predicted, strict=True)
    }
    first = [
        scored_class(i, p) == scored_class(i, i.first_label)
        for i, p in zip(items, predicted, strict=True)
    ]
    return {
        "strict_accuracy": sum(strict.values()) / len(strict),
        "strict_first_accuracy": sum(first) / len(first),
        "per_item_strict": strict,
    }


def lexical_oracle(items: Sequence[Item]) -> list[str]:
    """The best single word-overlap cut: below it one class, at or above it
    another, the cut and both classes chosen to maximise strict accuracy ON THE
    LABELS. That fits the answer key, so it is an optimistic reference, the most
    any word-overlap heuristic could do here, not something production has.
    Ties go to the lower cut, then the earlier class pair, so it is reproducible."""
    if not items:
        raise ValueError("no items to fit a cut on")
    values = sorted({overlap(i) for i in items})
    cuts = [values[0] - 1.0, *((a + b) / 2 for a, b in pairwise(values))]
    best: tuple[int, float, str, str] | None = None
    for cut in cuts:
        for low in WIRE.values():
            for high in WIRE.values():
                hits = sum(
                    is_correct(i, low if overlap(i) < cut else high) for i in items
                )
                if best is None or hits > best[0]:
                    best = (hits, cut, low, high)
    if best is None:
        raise ValueError("no items to fit a cut on")
    _, cut, low, high = best
    return [low if overlap(i) < cut else high for i in items]


def baselines(items: Sequence[Item]) -> dict[str, dict[str, Any]]:
    """The model-free predictors a model has to beat for the task key to be worth
    a call: one constant answer per verdict, and the best word-overlap cut.
    Production holds no deterministic opinion about a verdict (its direction rule
    orders a pair; its candidate stage needs an embedder), so these are the
    honest floors."""
    table = {
        f"always {WIRE[label]}": _constant(items, [WIRE[label]] * len(items))
        for label in ("UNRELATED", "REVERSES", "REFINES", "REAFFIRMS")
    }
    table[LEXICAL_BASELINE] = _constant(items, lexical_oracle(items))
    return table


def majority_baseline(items: Sequence[Item]) -> float:
    """Strict accuracy of always answering the fixture's commonest label."""
    return max(Counter(i.label for i in items).values()) / len(items)


def reference_baseline(items: Sequence[Item]) -> tuple[str, dict[str, Any]]:
    """The strongest model-free predictor on the labels, ties to the earlier name.

    Chosen from the labels alone, so it is fixed before any model runs. It is the
    reference of rule J: a model has to beat what no model can already do."""
    table = baselines(items)
    name = max(
        BASELINE_NAMES,
        key=lambda n: (table[n]["strict_accuracy"], -BASELINE_NAMES.index(n)),
    )
    return name, table[name]


# --- comparison --------------------------------------------------------------


def vetoes(candidate: Mapping[str, Any], baseline: Mapping[str, Any]) -> list[str]:
    """The registered vetoes a candidate trips, named. A missing number trips its veto.

    V1 failure rate, V2 median call latency, V3 false-supersede rate (a
    `reverses` verdict on a directed pair that is not a reversal: the error that
    would write `supersedes` and hide a Decision still in force)."""
    tripped: list[str] = []

    def num(summary: Mapping[str, Any], key: str) -> float | None:
        value = summary.get(key)
        return None if value is None else float(value)

    fail, base_fail = num(candidate, "failure_rate"), num(baseline, "failure_rate")
    if (
        fail is None
        or base_fail is None
        or fail > base_fail + FAILURE_MARGIN
        or fail > FAILURE_ABS_CAP
    ):
        tripped.append("V1")
    lat, base_lat = (
        num(candidate, "median_latency_s"),
        num(baseline, "median_latency_s"),
    )
    if lat is None or base_lat is None or lat > LATENCY_RATIO_CAP * base_lat:
        tripped.append("V2")
    sup, base_sup = (
        num(candidate, "false_supersede_rate"),
        num(baseline, "false_supersede_rate"),
    )
    if sup is None or base_sup is None or sup > base_sup + SUPERSEDE_MARGIN:
        tripped.append("V3")
    return tripped


# --- forecast ----------------------------------------------------------------


def forecast(
    *,
    n_items: int,
    runs: int,
    median_latency_s: Mapping[str, float],
    load_s: float,
) -> dict[str, Any]:
    """Calls and Ollama wall-clock for a run: per model, calls times its median
    call latency plus one load. The latencies are an INPUT (the registration
    names where each came from); nothing here measures one."""
    per_model = n_items * runs
    seconds = {m: per_model * lat + load_s for m, lat in median_latency_s.items()}
    return {
        "calls_per_model": per_model,
        "calls": per_model * len(median_latency_s),
        "seconds": seconds,
        "total_seconds": sum(seconds.values()),
    }
