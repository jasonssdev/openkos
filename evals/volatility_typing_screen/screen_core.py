"""The deterministic half of the volatility-typing screen (#1563).

Volatility typing asks a model, once per concept TYPE present in a bundle,
which of `static`, `slow` or `volatile` describes how fast the knowledge in
that type's concepts goes stale. This module holds everything about measuring
that which needs no model: the labelled fixture and its contract, the bundle
each question is asked over, the scoring of one answer, the aggregate numbers,
the anchoring probe, the zero-model floors, the paired bootstrap, the vetoes
and the forecast. The runner (`run_volatility_screen.py`) owns the model.

Labels are the resolved ones of two adjudicators (kappa 0.544); read
`README.md` and `ADJUDICATION.md` before trusting any number computed from them.

Imports only the standard library and two production modules that carry the
vocabulary being measured (`model.types`, and the sampling constants of
`resolution.volatility_typing`), so a unit test can load it straight from its
path.
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

from openkos.model import types
from openkos.resolution import volatility_typing as vt

HERE: Final = pathlib.Path(__file__).resolve().parent
REPO_ROOT: Final = HERE.parents[1]
FIXTURE_PATH: Final = HERE / "fixture.json"

TIERS: Final = ("static", "slow", "volatile")
"""The closed taxonomy, in a fixed order (`types.VOLATILITY_TIERS` is a set)."""
if set(TIERS) != set(types.VOLATILITY_TIERS):
    raise RuntimeError("the production tier set changed; update TIERS and the fixture")

ARMS: Final = ("default", "wrong-anchor")
"""`default`: the production prompt as shipped, which shows the type's registry
default as `CURRENT DEFAULT TIER`. `wrong-anchor`: the same call with that line
naming a tier that is neither the label nor its alternative -- the anchoring
probe. Both arms go through production's own `type_tiers` argument; nothing in
the prompt is edited."""

FAILURE_ABS_CAP: Final = 0.05
FAILURE_MARGIN: Final = 0.02
LATENCY_RATIO_CAP: Final = 2.0
ANCHOR_MARGIN: Final = 0.10
"""The registered vetoes (see `PREREGISTRATION.md`)."""


@dataclass(frozen=True)
class Doc:
    """One concept document of an item, as the harness writes it."""

    id: str
    title: str
    text: str
    source: Mapping[str, str]


@dataclass(frozen=True)
class Item:
    """One question: a type and the (at most five) concept bodies shown for it."""

    id: str
    type: str
    language: str
    docs: tuple[Doc, ...]
    label: str
    alt_label: str | None
    type_assigned: bool
    how: str
    adjudication: Mapping[str, str] | None = None
    """The second-adjudication record (`first`, `second`, `resolved`, `reason`)
    of an item whose two adjudicators disagreed; `None` otherwise."""

    @property
    def first_label(self) -> str:
        """The first author's label: `label` is the RESOLVED one, so this differs
        exactly on the items with an adjudication record."""
        return self.adjudication["first"] if self.adjudication else self.label

    @property
    def ambiguous(self) -> bool:
        return self.alt_label is not None

    @property
    def registry_default(self) -> str:
        return types.TYPE_TO_DEFAULT_VOLATILITY[self.type]


# --- the fixture -------------------------------------------------------------


def _doc(raw: Mapping[str, Any]) -> Doc:
    return Doc(
        id=str(raw["id"]),
        title=str(raw["title"]),
        text=str(raw["text"]),
        source=dict(raw.get("source") or {}),
    )


def _item(raw: Mapping[str, Any]) -> Item:
    item_id = str(raw["id"])
    label = raw["label"]
    alt = raw.get("alt_label")
    if label not in TIERS:
        raise ValueError(f"{item_id}: label {label!r} is not a tier")
    if alt is not None and (alt not in TIERS or alt == label):
        raise ValueError(f"{item_id}: alt_label {alt!r} must be another tier")
    if bool(raw.get("ambiguous")) != (alt is not None):
        raise ValueError(
            f"{item_id}: `ambiguous` must hold exactly when alt_label is set"
        )
    if raw["type"] not in types.TYPE_TO_DEFAULT_VOLATILITY:
        raise ValueError(f"{item_id}: unknown type {raw['type']!r}")
    record = raw.get("adjudication")
    if record is not None:
        if set(record) != {"first", "second", "resolved", "reason"}:
            raise ValueError(
                f"{item_id}: adjudication needs first/second/resolved/reason"
            )
        if record["resolved"] != label or record["first"] == label:
            raise ValueError(f"{item_id}: adjudication must resolve to `label`")
        if alt != record["first"]:
            raise ValueError(f"{item_id}: the first label must stay as alt_label")
    docs = tuple(_doc(d) for d in raw["docs"])
    if not 1 <= len(docs) <= vt.N_SAMPLE_CONCEPTS:
        raise ValueError(
            f"{item_id}: needs 1..{vt.N_SAMPLE_CONCEPTS} docs, has {len(docs)}"
        )
    if len({d.id for d in docs}) != len(docs):
        raise ValueError(f"{item_id}: duplicate doc ids")
    if not str(raw.get("how", "")).strip():
        raise ValueError(f"{item_id}: every label states how it was decided")
    return Item(
        id=item_id,
        type=str(raw["type"]),
        language=str(raw.get("language", "en")),
        docs=docs,
        label=str(label),
        alt_label=None if alt is None else str(alt),
        type_assigned=bool(raw.get("type_assigned")),
        how=str(raw["how"]),
        adjudication=None if record is None else {k: str(v) for k, v in record.items()},
    )


def load_fixture(path: pathlib.Path = FIXTURE_PATH) -> list[Item]:
    """The labelled items, validated. Raises `ValueError` on an incoherent one."""
    data = json.loads(path.read_text(encoding="utf-8"))
    items = [_item(raw) for raw in data["items"]]
    ids = [i.id for i in items]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate item ids")
    return items


def class_balance(items: Sequence[Item]) -> dict[str, int]:
    counts = Counter(i.label for i in items)
    return {tier: counts.get(tier, 0) for tier in TIERS}


def _squash(text: str) -> str:
    return " ".join(text.split())


_PY_SOURCES: dict[str, Any] = {}


def _load_py(name: str, path: pathlib.Path, extra_path: Sequence[pathlib.Path]) -> Any:
    key = f"{name}:{path}"
    if key not in _PY_SOURCES:
        saved = list(sys.path)
        sys.path[:0] = [str(p) for p in extra_path]
        try:
            spec = importlib.util.spec_from_file_location(f"_vt_src_{name}", path)
            if spec is None or spec.loader is None:
                raise ImportError(path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            _PY_SOURCES[key] = module
        finally:
            sys.path[:] = saved
    return _PY_SOURCES[key]


def _source_text(source: Mapping[str, str], repo_root: pathlib.Path) -> str | None:
    kind, ref = source.get("kind"), source.get("ref", "")
    evals = repo_root / "evals"
    if kind == "file":
        path = repo_root / ref
        return path.read_text(encoding="utf-8") if path.is_file() else None
    if kind == "edge_fixtures":
        module = _load_py("edge", evals / "edge_typing" / "fixtures.py", ())
        for doc in module.DOCS:
            if doc.concept_id == ref:
                return str(doc.body)
        return None
    if kind == "attribution_en":
        module = _load_py(
            "attribution",
            evals / "query_attribution" / "attribution_corpus.py",
            (evals / "query_attribution", evals / "query_grounding"),
        )
        entry = module.EN_DOCS.get(ref)
        return None if entry is None else str(entry[1])
    return None


def provenance_problems(
    items: Sequence[Item], *, repo_root: pathlib.Path = REPO_ROOT
) -> list[str]:
    """Every doc whose text is no longer a verbatim quotation of its source.

    A label was decided on this text; if the source it came from has been
    edited since, the label is about something else and the fixture is stale.
    A doc may quote several paragraphs of one source that are not adjacent, so
    each blank-line-separated piece is checked on its own."""
    problems: list[str] = []
    for item in items:
        for doc in item.docs:
            source = _source_text(doc.source, repo_root)
            if source is None:
                problems.append(
                    f"{item.id}/{doc.id}: source {dict(doc.source)} not found"
                )
            elif not all(
                _squash(piece) in _squash(source) for piece in doc.text.split("\n\n")
            ):
                problems.append(
                    f"{item.id}/{doc.id}: text is not in its source any more"
                )
    return problems


# --- the question the model sees ---------------------------------------------


def _frontmatter_value(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def write_item_bundle(root: pathlib.Path, item: Item) -> pathlib.Path:
    """Write the item's docs as a one-type bundle under `root / item.id`.

    The documents carry the minimum frontmatter production reads (`type`,
    `title`, a `private` sensitivity so the disclosure gate has nothing to
    drop) and a body that opens with the title heading, as a compiled
    concept's does."""
    bundle = root / item.id
    link_dir = next(ot.link_dir for ot in types.REGISTRY if ot.name == item.type)
    directory = bundle / str(link_dir)
    directory.mkdir(parents=True, exist_ok=True)
    for doc in item.docs:
        (directory / f"{doc.id}.md").write_text(
            "---\n"
            f"type: {_frontmatter_value(item.type)}\n"
            f"title: {_frontmatter_value(doc.title)}\n"
            "sensitivity: private\n"
            "---\n\n"
            f"# {doc.title}\n\n{doc.text}\n",
            encoding="utf-8",
        )
    return bundle


def anchor_tier(item: Item) -> str:
    """The tier the `wrong-anchor` arm shows as the type's current default: the
    first tier after the label that is not the flagged alternative, so a model
    that follows the anchor is wrong on BOTH the strict and the lenient score."""
    index = TIERS.index(item.label)
    for step in range(1, len(TIERS)):
        candidate = TIERS[(index + step) % len(TIERS)]
        if candidate != item.alt_label:
            return candidate
    raise AssertionError("unreachable: three tiers, at most two excluded")


# --- scoring -----------------------------------------------------------------


def score_answer(item: Item, tier: str | None) -> tuple[bool, bool]:
    """`(strict, lenient)`: strict is the primary label; lenient also accepts the
    flagged alternative. A failed call (`None`) is wrong on both, never skipped."""
    if tier is None:
        return (False, False)
    strict = tier == item.label
    return (strict, strict or tier == item.alt_label)


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
    matrix and the failure rate are over calls."""
    by_id = {i.id: i for i in items}
    mine = [r for r in rows if r["arm"] == arm and r["item"] in by_id]
    per_item_strict: dict[str, list[float]] = {}
    per_item_lenient: dict[str, list[float]] = {}
    per_item_first: dict[str, list[float]] = {}
    per_item_tiers: dict[str, list[str | None]] = {}
    confusion = {g: {**dict.fromkeys(TIERS, 0), "none": 0} for g in TIERS}
    class_hits: dict[str, list[float]] = {g: [] for g in TIERS}
    failures: Counter[str] = Counter()
    answered = agree = follow = 0
    for row in mine:
        item = by_id[row["item"]]
        tier = row["tier"]
        strict, lenient = score_answer(item, tier)
        per_item_strict.setdefault(item.id, []).append(float(strict))
        per_item_lenient.setdefault(item.id, []).append(float(lenient))
        per_item_first.setdefault(item.id, []).append(float(tier == item.first_label))
        per_item_tiers.setdefault(item.id, []).append(tier)
        class_hits[item.label].append(float(strict))
        confusion[item.label][tier if tier is not None else "none"] += 1
        if row.get("failure"):
            failures[str(row["failure"])] += 1
        if tier is not None:
            answered += 1
            agree += tier == item.registry_default
        follow += tier == anchor_tier(item)
    repeated = [t for t in per_item_tiers.values() if len(t) >= 2]
    latencies = [float(r["latency_s"]) for r in mine]
    n = len(mine)
    return {
        "arm": arm,
        "n_calls": n,
        "n_items": len(per_item_strict),
        "strict_accuracy": _mean([sum(v) / len(v) for v in per_item_strict.values()]),
        "lenient_accuracy": _mean([sum(v) / len(v) for v in per_item_lenient.values()]),
        "strict_first_accuracy": _mean(
            [sum(v) / len(v) for v in per_item_first.values()]
        ),
        "per_class_recall": {g: _mean(class_hits[g]) for g in TIERS},
        "confusion": confusion,
        "failure_rate": sum(failures.values()) / n if n else None,
        "failures": dict(failures),
        "registry_agreement": agree / answered if answered else None,
        "anchor_follow_rate": (follow / n if n else None)
        if arm == "wrong-anchor"
        else None,
        "stability": (
            sum(len(set(t)) == 1 for t in repeated) / len(repeated)
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
                float(score_answer(by_id[row["item"]], row["tier"])[0])
            )
    return {k: sum(v) / len(v) for k, v in scores.items()}


# --- the zero-model floors ---------------------------------------------------


def registry_baseline(items: Sequence[Item]) -> dict[str, Any]:
    """What always answering the type's registry default would score. The model
    has to beat this for the `volatility_typing` key to be worth a call at all."""
    strict = {i.id: float(i.registry_default == i.label) for i in items}
    lenient = [i.registry_default in (i.label, i.alt_label) for i in items]
    first = [i.registry_default == i.first_label for i in items]
    return {
        "strict_first_accuracy": sum(first) / len(first),
        "strict_accuracy": sum(strict.values()) / len(strict),
        "lenient_accuracy": sum(lenient) / len(lenient),
        "per_item_strict": strict,
    }


def majority_baseline(items: Sequence[Item]) -> float:
    """Strict accuracy of always answering the fixture's commonest label."""
    return max(Counter(i.label for i in items).values()) / len(items)


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


def vetoes(
    candidate: Mapping[str, Any],
    baseline: Mapping[str, Any],
    candidate_anchor: Mapping[str, Any],
    baseline_anchor: Mapping[str, Any] | None = None,
) -> list[str]:
    """The registered vetoes a candidate trips, named. A missing number trips its veto."""
    tripped: list[str] = []
    base_anchor = baseline if baseline_anchor is None else baseline_anchor

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
    follow = num(candidate_anchor, "anchor_follow_rate")
    base_follow = num(base_anchor, "anchor_follow_rate")
    if follow is None or base_follow is None or follow > base_follow + ANCHOR_MARGIN:
        tripped.append("V3")
    return tripped


def selected(delta: Mapping[str, float], tripped: Sequence[str]) -> bool:
    """A candidate replaces the baseline only with a positive 95% lower bound on
    its paired gain and no veto."""
    return delta["point"] > 0 and delta["lo"] > 0 and not tripped


def justified(delta_vs_registry: Mapping[str, float]) -> bool:
    """The task key is justified for a model only if its paired gain over always
    answering the registry default has a positive 95% lower bound."""
    return delta_vs_registry["lo"] > 0


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
