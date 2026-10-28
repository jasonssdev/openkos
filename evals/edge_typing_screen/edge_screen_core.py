"""The deterministic half of the edge-typing screen (#1563).

Edge typing asks a model, once per untyped body-link edge, which relation type
describes how SOURCE relates to TARGET, from the closed suggestable vocabulary
of `model.relations`, with `related_to` as the honest abstention
(`resolution/edge_typing.py`). This module holds everything about measuring
that which needs no model: the labelled fixture and its contract, the bundle
each question is asked over, the scoring of one answer, the direction trap, the
aggregate numbers, the zero-model baselines, the paired bootstrap, the vetoes
and the forecast. The runner (`run_edge_typing_screen.py`) owns the model.

The labels are ONE author's until the repository owner's blind adjudication is
recorded (`PREREGISTRATION.md`); read `README.md` before trusting any
number computed from them.

Imports only the standard library and the production modules that carry the
vocabulary being measured, so a unit test can load it straight from its path.
"""

from __future__ import annotations

import importlib.util
import json
import math
import pathlib
import random
import statistics
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from openkos.model import okf, relations, types
from openkos.resolution import edge_typing as et

HERE: Final = pathlib.Path(__file__).resolve().parent
REPO_ROOT: Final = HERE.parents[1]
FIXTURE_PATH: Final = HERE / "fixture.json"

TYPES: Final = tuple(sorted(relations.SUGGESTABLE_RELATION_TYPES))
"""The closed answer vocabulary, in a fixed order: what production may suggest
(the seeded types minus the engine-owned `derived_from`)."""

STRUCTURAL_TYPES: Final = tuple(
    t for t in TYPES if t in relations.ASYMMETRIC_RELATION_TYPES
)
"""The five asymmetric types: each asserts structure (containment, dependence,
cause, authorship) and a direction, so a wrong one is the harm the production
prompt's closing guard names. `references` and `related_to` assert no structure
beyond the link that already exists."""

STRATA: Final = ("constructed", "good-life", "adr")
"""Where an item came from: `constructed` (the hand-written pairs of
`evals/edge_typing/fixtures.py`, whose bodies were written to the rubric),
`good-life` (the whole production candidate set of `examples/good-life-demo`)
and `adr` (ADRs of this repository that name one another)."""

ARMS: Final = ("default",)
"""One arm: the production prompt as shipped. There is no anchor to probe (the
prompt shows no current type); direction is probed by the reversed items."""

FAILURE_ABS_CAP: Final = 0.05
FAILURE_MARGIN: Final = 0.02
LATENCY_RATIO_CAP: Final = 2.0
TRAP_MARGIN: Final = 0.10
"""The vetoes of `PREREGISTRATION.md`."""

BASELINE_NAMES: Final = ("always references", "always related_to", "type signature")


@dataclass(frozen=True)
class Doc:
    """One concept document as production would read it."""

    id: str
    type: str
    title: str
    text: str
    language: str
    source: Mapping[str, str]


@dataclass(frozen=True)
class Item:
    """One question: an untyped link SOURCE -> TARGET and what it should be typed."""

    id: str
    stratum: str
    source: Doc
    target: Doc
    label: str
    alt_label: str | None
    trap_type: str | None
    how: str
    adjudication: Mapping[str, str] | None = None
    """The second-adjudication record (`first`, `second`, `resolved`, `reason`)
    of an item whose two adjudicators disagreed; `None` otherwise, and for every
    item until the owner's blind adjudication is recorded."""

    @property
    def first_label(self) -> str:
        """The first author's label: `label` is the RESOLVED one."""
        return self.adjudication["first"] if self.adjudication else self.label

    @property
    def ambiguous(self) -> bool:
        return self.alt_label is not None


# --- the fixture -------------------------------------------------------------


def _doc(key: str, raw: Mapping[str, Any]) -> Doc:
    if str(raw["id"]) != key:
        raise ValueError(f"doc key {key!r} differs from its id {raw['id']!r}")
    return Doc(
        id=key,
        type=str(raw["type"]),
        title=str(raw["title"]),
        text=str(raw["text"]),
        language=str(raw.get("language", "en")),
        source=dict(raw.get("source") or {}),
    )


def _item(raw: Mapping[str, Any], docs: Mapping[str, Doc]) -> Item:
    item_id = str(raw["id"])
    label, alt, trap = raw["label"], raw.get("alt_label"), raw.get("trap_type")
    if label not in TYPES:
        raise ValueError(f"{item_id}: label {label!r} is not a suggestable type")
    if alt is not None and (alt not in TYPES or alt == label):
        raise ValueError(f"{item_id}: alt_label {alt!r} must be another type")
    if bool(raw.get("ambiguous")) != (alt is not None):
        raise ValueError(
            f"{item_id}: `ambiguous` must hold exactly when alt_label is set"
        )
    if trap is not None and (trap not in STRUCTURAL_TYPES or trap == label):
        raise ValueError(f"{item_id}: trap_type {trap!r} must be a structural type")
    if raw["stratum"] not in STRATA:
        raise ValueError(f"{item_id}: unknown stratum {raw['stratum']!r}")
    for end in ("source", "target"):
        if raw[end] not in docs:
            raise ValueError(f"{item_id}: {end} {raw[end]!r} is not in `docs`")
    if raw["source"] == raw["target"]:
        raise ValueError(f"{item_id}: a link joins two different documents")
    record = raw.get("adjudication")
    if record is not None:
        if set(record) != {"first", "second", "resolved", "reason"}:
            raise ValueError(
                f"{item_id}: adjudication needs first/second/resolved/reason"
            )
        if record["resolved"] != label:
            raise ValueError(f"{item_id}: adjudication must resolve to `label`")
        if record["first"] == label:
            # Resolved to the first author: the losing second label is the
            # alternative a careful reader could still defend.
            if alt != record["second"]:
                raise ValueError(f"{item_id}: the second label must stay as alt_label")
        elif alt != record["first"]:
            raise ValueError(f"{item_id}: the first label must stay as alt_label")
    if not str(raw.get("how", "")).strip():
        raise ValueError(f"{item_id}: every label states how it was decided")
    return Item(
        id=item_id,
        stratum=str(raw["stratum"]),
        source=docs[raw["source"]],
        target=docs[raw["target"]],
        label=str(label),
        alt_label=None if alt is None else str(alt),
        trap_type=None if trap is None else str(trap),
        how=str(raw["how"]),
        adjudication=None if record is None else {k: str(v) for k, v in record.items()},
    )


def load_fixture(path: pathlib.Path = FIXTURE_PATH) -> list[Item]:
    """The labelled items, validated. Raises `ValueError` on an incoherent one."""
    data = json.loads(path.read_text(encoding="utf-8"))
    docs = {key: _doc(key, raw) for key, raw in data["docs"].items()}
    items = [_item(raw, docs) for raw in data["items"]]
    ids = [i.id for i in items]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate item ids")
    return items


def class_balance(items: Sequence[Item]) -> dict[str, int]:
    counts = Counter(i.label for i in items)
    return {t: counts.get(t, 0) for t in TYPES}


def stratum_balance(items: Sequence[Item]) -> dict[str, int]:
    counts = Counter(i.stratum for i in items)
    return {s: counts.get(s, 0) for s in STRATA}


_PY_SOURCES: dict[str, Any] = {}


def _load_py(name: str, path: pathlib.Path) -> Any:
    key = f"{name}:{path}"
    if key not in _PY_SOURCES:
        spec = importlib.util.spec_from_file_location(f"_et_src_{name}", path)
        if spec is None or spec.loader is None:
            raise ImportError(path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        _PY_SOURCES[key] = module
    return _PY_SOURCES[key]


def _source_doc(
    source: Mapping[str, str], repo_root: pathlib.Path
) -> tuple[str, str, str] | None:
    """`(type, title, body)` of the quoted source as production would read it."""
    kind, ref = source.get("kind"), source.get("ref", "")
    if kind == "okf_file":
        path = repo_root / ref
        if not path.is_file():
            return None
        meta, body = okf.load_frontmatter(path.read_text(encoding="utf-8"))
        return str(meta.get("type")), str(meta.get("title")), body
    if kind == "edge_fixtures":
        module = _load_py("edge", repo_root / "evals" / "edge_typing" / "fixtures.py")
        for doc in module.DOCS:
            if doc.concept_id == ref:
                return (
                    str(doc.okf_type),
                    str(doc.title),
                    f"# {doc.title}\n\n{doc.body}",
                )
    return None


def provenance_problems(
    items: Sequence[Item], *, repo_root: pathlib.Path = REPO_ROOT
) -> list[str]:
    """Every doc whose type, title or body is no longer exactly its source's.

    A label was decided on this text; if the source it came from has been
    edited since, the label is about something else and the fixture is stale.
    The comparison is exact (the body is what `load_frontmatter` returns), not
    a substring test, because production sends the whole body to the model."""
    problems: list[str] = []
    seen: set[str] = set()
    for item in items:
        for doc in (item.source, item.target):
            if doc.id in seen:
                continue
            seen.add(doc.id)
            found = _source_doc(doc.source, repo_root)
            if found is None:
                problems.append(f"{doc.id}: source {dict(doc.source)} not found")
            elif found != (doc.type, doc.title, doc.text):
                problems.append(f"{doc.id}: differs from its source any more")
    return problems


# --- the question the model sees ---------------------------------------------


def write_item_bundle(root: pathlib.Path, item: Item) -> pathlib.Path:
    """Write the item's two docs as a two-document bundle under `root / item.id`.

    Frontmatter is rendered by the shipped `okf.dump_frontmatter` (type, title,
    and a `private` sensitivity so the disclosure gate has nothing to drop); the
    body is the fixture text verbatim."""
    bundle = root / item.id
    for doc in (item.source, item.target):
        path = bundle / f"{doc.id}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        frontmatter = okf.dump_frontmatter(
            {"type": doc.type, "title": doc.title, "sensitivity": "private"}
        )
        path.write_text(f"{frontmatter}{doc.text}\n", encoding="utf-8")
    return bundle


def folder_problems(items: Sequence[Item]) -> list[str]:
    """Docs whose id folder is not the folder their type lives in."""
    problems: list[str] = []
    for item in items:
        for doc in (item.source, item.target):
            wanted = {ot.name: ot.link_dir for ot in types.REGISTRY if ot.link_dir}.get(
                doc.type
            )
            if doc.id.split("/", 1)[0] != wanted:
                problems.append(f"{doc.id} (type {doc.type} wants {wanted}/)")
    return sorted(set(problems))


# --- scoring -----------------------------------------------------------------


def score_answer(
    item: Item, answer: str | None, corrected: bool = False
) -> tuple[bool, bool]:
    """`(strict, lenient)` for the edge AS SHOWN: strict is the primary label,
    lenient also accepts the flagged alternative. A failed call (`None`) is wrong
    on both, never skipped. A direction-corrected answer (production swapped the
    pair, `corrected_edge`) describes the REVERSE edge, so it is wrong on both
    here; `is_recovered` reports it separately."""
    if answer is None or corrected:
        return (False, False)
    strict = answer == item.label
    return (strict, strict or answer == item.alt_label)


def is_trap_hit(item: Item, answer: str | None, corrected: bool) -> bool:
    """A reversed probe answered with the asymmetric type that is only true the
    other way round, and left uncorrected: a wrong structural claim that would
    reach the operator."""
    return item.trap_type is not None and answer == item.trap_type and not corrected


def is_recovered(item: Item, answer: str | None, corrected: bool) -> bool:
    """The same trap answer, but production's object-type check swapped the pair
    so the claim it surfaces is the true one."""
    return item.trap_type is not None and answer == item.trap_type and corrected


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _p95(values: Sequence[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[math.ceil(0.95 * len(ordered)) - 1]


def summarize(
    rows: Sequence[Mapping[str, Any]], items: Sequence[Item], arm: str
) -> dict[str, Any]:
    """Every number the screen reports for one model on one arm.

    Accuracy is the mean over ITEMS of each item's mean over runs, so every item
    weighs the same however many runs it has. The class recalls, the confusion
    matrix, the failure rate and the trap rates are over calls."""
    by_id = {i.id: i for i in items}
    mine = [r for r in rows if r["arm"] == arm and r["item"] in by_id]
    strict: dict[str, list[float]] = {}
    lenient: dict[str, list[float]] = {}
    first: dict[str, list[float]] = {}
    answers: dict[str, list[str | None]] = {}
    confusion = {g: {**dict.fromkeys(TYPES, 0), "none": 0} for g in TYPES}
    class_hits: dict[str, list[float]] = {g: [] for g in TYPES}
    failures: Counter[str] = Counter()
    answered: Counter[str] = Counter()
    structural = structural_right = trap_calls = trap_hits = recovered = withdrawn = 0
    for row in mine:
        item = by_id[row["item"]]
        answer, corrected = row["type"], bool(row.get("corrected"))
        s, lenient_hit = score_answer(item, answer, corrected)
        strict.setdefault(item.id, []).append(float(s))
        lenient.setdefault(item.id, []).append(float(lenient_hit))
        first.setdefault(item.id, []).append(
            float(answer == item.first_label and not corrected)
        )
        answers.setdefault(item.id, []).append(answer)
        class_hits[item.label].append(float(s))
        confusion[item.label][answer if answer is not None else "none"] += 1
        if row.get("failure"):
            failures[str(row["failure"])] += 1
        if answer is not None:
            answered[answer] += 1
            if answer in STRUCTURAL_TYPES:
                structural += 1
                structural_right += s
        withdrawn += bool(row.get("withdrawn"))
        if item.trap_type is not None:
            trap_calls += 1
            trap_hits += is_trap_hit(item, answer, corrected)
            recovered += is_recovered(item, answer, corrected)
    repeated = [a for a in answers.values() if len(a) >= 2]
    latencies = [float(r["latency_s"]) for r in mine]
    n = len(mine)
    total_answered = sum(answered.values())
    per_item = {k: sum(v) / len(v) for k, v in strict.items()}
    strata = {
        s: _mean([v for k, v in per_item.items() if by_id[k].stratum == s])
        for s in STRATA
        if any(by_id[k].stratum == s for k in per_item)
    }
    return {
        "arm": arm,
        "n_calls": n,
        "n_items": len(strict),
        "strict_accuracy": _mean(list(per_item.values())),
        "lenient_accuracy": _mean([sum(v) / len(v) for v in lenient.values()]),
        "strict_first_accuracy": _mean([sum(v) / len(v) for v in first.values()]),
        "strict_by_stratum": strata,
        "per_class_recall": {g: _mean(class_hits[g]) for g in TYPES},
        "confusion": confusion,
        "failure_rate": sum(failures.values()) / n if n else None,
        "failures": dict(failures),
        "specific_answers": structural,
        "specific_precision": structural_right / structural if structural else None,
        "trap_calls": trap_calls,
        "trap_hit_rate": trap_hits / trap_calls if trap_calls else None,
        "recovered_rate": recovered / trap_calls if trap_calls else None,
        "withdrawn_rate": withdrawn / n if n else None,
        "answer_share": {
            t: answered[t] / total_answered if total_answered else None for t in TYPES
        },
        "stability": (
            sum(len(set(a)) == 1 for a in repeated) / len(repeated)
            if repeated
            else None
        ),
        "median_latency_s": statistics.median(latencies) if latencies else None,
        "p95_latency_s": _p95(latencies),
    }


def per_item_strict(
    rows: Sequence[Mapping[str, Any]], items: Sequence[Item], arm: str
) -> dict[str, float]:
    """Each item's mean strict score over the runs of `arm`, for the paired test."""
    by_id = {i.id: i for i in items}
    scores: dict[str, list[float]] = {}
    for row in rows:
        if row["arm"] == arm and row["item"] in by_id:
            scores.setdefault(row["item"], []).append(
                float(
                    score_answer(
                        by_id[row["item"]], row["type"], bool(row.get("corrected"))
                    )[0]
                )
            )
    return {k: sum(v) / len(v) for k, v in scores.items()}


# --- the zero-model baselines ------------------------------------------------


def signature_predict(source_type: str, target_type: str) -> str:
    """What production's object-type direction table alone would answer: the
    relation type whose declared `(subordinate, superordinate)` pair this is, else
    the abstention. It is the only deterministic opinion the engine holds about a
    relation type, derived from `edge_typing._DIRECTION_TYPE_SIGNATURES` (which
    production uses to correct a model's direction, never to propose a type)."""
    for rel_type, pairs in sorted(et._DIRECTION_TYPE_SIGNATURES.items()):
        if (source_type, target_type) in pairs:
            return rel_type
    return et.LEAST_SPECIFIC_RELATION_TYPE


def _constant(items: Sequence[Item], predicted: Sequence[str]) -> dict[str, Any]:
    strict = {i.id: float(p == i.label) for i, p in zip(items, predicted, strict=True)}
    lenient = [
        p in (i.label, i.alt_label) for i, p in zip(items, predicted, strict=True)
    ]
    first = [p == i.first_label for i, p in zip(items, predicted, strict=True)]
    return {
        "strict_accuracy": sum(strict.values()) / len(strict),
        "lenient_accuracy": sum(lenient) / len(lenient),
        "strict_first_accuracy": sum(first) / len(first),
        "per_item_strict": strict,
    }


def baselines(items: Sequence[Item]) -> dict[str, dict[str, Any]]:
    """The model-free predictors a model has to beat for the `edge_typing` key to
    be worth a call: always `references` (the rubric-literal answer to any explicit
    link), always `related_to` (the honest abstention, and what a field bundle's
    suggester says most), and the object-type signature table."""
    return {
        "always references": _constant(items, ["references"] * len(items)),
        "always related_to": _constant(items, ["related_to"] * len(items)),
        "type signature": _constant(
            items, [signature_predict(i.source.type, i.target.type) for i in items]
        ),
    }


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


def paired_bootstrap(
    candidate: Mapping[str, float],
    reference: Mapping[str, float],
    *,
    resamples: int = 10_000,
    seed: int = 42,
) -> dict[str, float]:
    """Mean per-item difference `candidate - reference` and its 95% percentile
    bootstrap interval over items (items resampled with replacement)."""
    if set(candidate) != set(reference):
        raise ValueError("paired scores need the same items on both sides")
    keys = sorted(candidate)
    deltas = [candidate[k] - reference[k] for k in keys]
    rng = random.Random(seed)  # noqa: S311 -- bootstrap resampling, not secrets
    means = sorted(
        sum(rng.choice(deltas) for _ in deltas) / len(deltas) for _ in range(resamples)
    )
    return {
        "point": sum(deltas) / len(deltas),
        "lo": means[int(0.025 * resamples)],
        "hi": means[min(resamples - 1, int(0.975 * resamples))],
    }


def vetoes(candidate: Mapping[str, Any], baseline: Mapping[str, Any]) -> list[str]:
    """The registered vetoes a candidate trips, named. A missing number trips its veto.

    V1 failure rate, V2 median call latency, V3 direction-trap hit rate."""
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
    trap, base_trap = num(candidate, "trap_hit_rate"), num(baseline, "trap_hit_rate")
    if trap is None or base_trap is None or trap > base_trap + TRAP_MARGIN:
        tripped.append("V3")
    return tripped


def selected(delta: Mapping[str, float], tripped: Sequence[str]) -> bool:
    """A candidate replaces the baseline only with a positive 95% lower bound on
    its paired gain and no veto."""
    return delta["point"] > 0 and delta["lo"] > 0 and not tripped


def justified(delta_vs_reference: Mapping[str, float]) -> bool:
    """The task key is justified for a model only if its paired gain over the
    strongest model-free predictor has a positive 95% lower bound."""
    return delta_vs_reference["lo"] > 0


def j_tolerance(
    reference: Mapping[str, float],
    *,
    wins: int,
    shuffle_seed: int | None = None,
    resamples: int = 10_000,
) -> int:
    """How many items a candidate can lose and still clear J when it wins `wins`.

    The reference scores each item 0 or 1. The candidate matches it except that it
    WINS `wins` of the items the reference gets wrong (to 1.0) and LOSES the
    most losses it can afford of the items the reference gets right (to 0.0); the
    answer is the largest loss count whose paired gain (the screen's bootstrap,
    seed 42) still has a positive 95% lower bound. -1 means no loss count clears
    it, losing nothing included.

    Which items are won and lost moves the bootstrap by one or so, so the choice is
    explicit: by default the first wrong items and the first right items in sorted
    id order, or, with `shuffle_seed`, that seeded shuffle of each list."""
    ids = sorted(reference)
    wrong = [i for i in ids if reference[i] == 0.0]
    right = [i for i in ids if reference[i] == 1.0]
    if wins > len(wrong):
        raise ValueError(
            f"cannot win {wins} items: the reference gets only {len(wrong)} wrong"
        )
    if shuffle_seed is not None:
        rng = random.Random(shuffle_seed)  # noqa: S311 -- arrangement, not secrets
        rng.shuffle(wrong)
        rng.shuffle(right)
    best = -1
    for losses in range(len(right) + 1):
        candidate = dict(reference)
        candidate.update(dict.fromkeys(wrong[:wins], 1.0))
        candidate.update(dict.fromkeys(right[:losses], 0.0))
        if justified(paired_bootstrap(candidate, reference, resamples=resamples)):
            best = losses
        else:
            break
    return best


def j_table_rows(
    reference: Mapping[str, float],
    *,
    wins: Sequence[int],
    shuffles: int,
    resamples: int = 10_000,
) -> list[dict[str, int]]:
    """`j_tolerance` per win count: in id order, and its lowest and highest value
    over `shuffles` seeded arrangements (seeds 1 to `shuffles`)."""
    rows: list[dict[str, int]] = []
    for count in wins:
        by_id = j_tolerance(reference, wins=count, resamples=resamples)
        shuffled = [
            j_tolerance(reference, wins=count, shuffle_seed=seed, resamples=resamples)
            for seed in range(1, shuffles + 1)
        ]
        rows.append(
            {
                "wins": count,
                "by_id": by_id,
                "low": min([by_id, *shuffled]),
                "high": max([by_id, *shuffled]),
            }
        )
    return rows


def render_j_table(
    name: str,
    reference: Mapping[str, float],
    *,
    wins: Sequence[int],
    shuffles: int,
    resamples: int = 10_000,
) -> str:
    """The J tolerance table as markdown, from the labels alone (no model)."""
    wrong = sum(v == 0.0 for v in reference.values())
    lines = [
        f"# J tolerance table (no model contacted); reference: {name}",
        "",
        f"wrong for the reference: {wrong} of {len(reference)}; "
        f"bootstrap {resamples} resamples, seed 42",
        "A candidate matches the reference except it wins the stated number of the "
        "items the reference gets wrong and loses up to the stated number of the "
        "items it gets right; the cell is the most losses that keep the 95% lower "
        "bound of the paired gain above 0 (-1: none, losing nothing included).",
        "",
        f"| wins | lose at most (id order) | range over {shuffles} shuffles |",
        "| --- | --- | --- |",
    ]
    for row in j_table_rows(
        reference, wins=wins, shuffles=shuffles, resamples=resamples
    ):
        lines.append(
            f"| {row['wins']} | {row['by_id']} | {row['low']} to {row['high']} |"
        )
    return "\n".join(lines)


# --- forecast ----------------------------------------------------------------


def call_count(n_items: int, *, arms: int, runs: int, models: int) -> int:
    return n_items * arms * runs * models


def forecast(
    *,
    n_items: int,
    arms: int,
    runs: int,
    median_latency_s: Mapping[str, float],
    load_s: float,
) -> dict[str, Any]:
    """Calls and Ollama wall-clock for a run: per model, calls times its median
    call latency plus one load. The latencies are an INPUT (the registration
    names where each came from); nothing here measures one."""
    per_model = call_count(n_items, arms=arms, runs=runs, models=1)
    seconds = {m: per_model * lat + load_s for m, lat in median_latency_s.items()}
    return {
        "calls_per_model": per_model,
        "calls": per_model * len(median_latency_s),
        "seconds": seconds,
        "total_seconds": sum(seconds.values()),
    }
