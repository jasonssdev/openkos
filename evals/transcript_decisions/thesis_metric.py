"""The thesis's decision-level alignment metric, reproduced for the decision pass.

The maintainer's thesis scores extracted decisions against the adjudicated gold
by embedding similarity, not by evidence overlap (its alignment protocol, thesis
section 5.4; ADR 0011 in that repository). This module reproduces that metric
so the transcript decision pass can be read on the thesis's own yardstick, next
to this harness's evidence-overlap recall (`scoring.py`). It imports nothing
from the thesis repository and writes nothing into it.

THE METRIC, as the thesis defines it:

- **Units.** A reference decision is a gold row whose `final_status` is
  `decision`, the split children of a `compuesta` row included (the parent is
  excluded). It is represented by `"<final_object>: <final_content>"`. A
  produced Decision is represented by `"<subject>: <value>"` -- the pass's
  subject is the thesis's decision object and its value the content.
- **Similarity.** Cosine similarity of the two texts' embeddings, from a local
  Ollama embedding model (`bge-m3`, the thesis's `embedding_model`), computed at
  run time. No vector or text is stored by this module.
- **Matching.** One alignment per run and series at each threshold tau: a pair
  is a candidate only when its similarity is >= tau AND the two come from the
  same meeting (the primary `origin_meetings` scope); the candidates are
  resolved as a one-to-one minimum-cost assignment (the Hungarian algorithm)
  over the thesis's cost matrix -- `-similarity` for a candidate, `1e6`
  otherwise -- and assigned pairs below tau are dropped. TP is the number of
  matches, precision TP / produced, recall TP / reference decisions.
- **Curve.** Reported over the thesis's `tau_grid`, 0.50 to 0.90 in steps of
  0.05, never as a single point.

The TP count of a minimum-cost assignment over that cost matrix is the maximum
number of disjoint candidate pairs, which does not depend on how ties between
equally good assignments are broken; so precision, recall and F1 reproduce
exactly even where the chosen pairs might differ from `scipy`'s.

Only development series are scored (`scoring.require_dev_meetings`), as in the
rest of this harness.

Usage:

    uv run python evals/transcript_decisions/thesis_metric.py --self-test
    uv run python evals/transcript_decisions/thesis_metric.py \\
        --gold <SERIES>.decisions.adjudicated.csv runs-pass-*.json
    uv run python evals/transcript_decisions/thesis_metric.py \\
        --gold <SERIES>.decisions.adjudicated.csv --thesis-runs extracciones.jsonl
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import pathlib
import random
import re
import statistics
import sys
import urllib.request
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import scoring

TAU_GRID: Final = (0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90)
"""The thesis's `[alignment].tau_grid`."""

EMBEDDING_MODEL: Final = "bge-m3"
"""The thesis's `[shared_budget].embedding_model`."""

EMBED_BATCH: Final = 32
"""The batch size of the thesis's Ollama embedder."""

EXCLUDED_PAIR_COST: Final = 1.0e6
"""The thesis's cost for a pair that is not a candidate."""

Embed = Callable[[Sequence[str]], list[list[float]]]


# --- units -------------------------------------------------------------------


@dataclass(frozen=True)
class Unit:
    """One decision on either side: its meeting and its `object: content` text."""

    meeting: str
    text: str


def pair_text(decision_object: str, content: str) -> str:
    """The thesis's `pair_text`: what represents a decision for embedding."""
    return f"{decision_object}: {content}"


def load_reference(paths: Sequence[pathlib.Path]) -> list[Unit]:
    """The reference decisions: `final_status == "decision"` rows, children included.

    A blank `meeting_id` (the split children of a `compuesta` row) is derived from
    the decision id, as `scoring.load_gold` does. A meeting outside the
    development allowlist is refused."""
    units: list[Unit] = []
    for path in paths:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                if (row.get("final_status") or "").strip() != "decision":
                    continue
                decision_id = (row.get("decision_id") or "").strip()
                meeting = (row.get("meeting_id") or "").strip()
                meeting = meeting or scoring.meeting_from_decision_id(decision_id)
                obj = (row.get("final_object") or "").strip()
                content = (row.get("final_content") or "").strip()
                if not (obj and content):
                    raise SystemExit(
                        f"{path.name}: {decision_id} is a decision without "
                        "final_object/final_content"
                    )
                units.append(Unit(meeting, pair_text(obj, content)))
    scoring.require_dev_meetings(sorted({u.meeting for u in units}))
    return units


def harness_runs(paths: Sequence[pathlib.Path]) -> dict[tuple[str, int], list[Unit]]:
    """`{(label, run): produced units}` from stored `runs-pass-*.json` files.

    A meeting-run that failed contributes the Decisions it kept (none when every
    window failed): the metric scores what the pass would have written."""
    runs: dict[tuple[str, int], list[Unit]] = {}
    for path in paths:
        stored = json.loads(path.read_text(encoding="utf-8"))
        if stored.get("arm") != "pass":
            raise SystemExit(f"{path.name}: only the pass arm stores subject and value")
        for record in stored["records"]:
            key = (str(stored["model"]), int(record["run"]))
            produced = runs.setdefault(key, [])
            for item in record["items"]:
                if "subject" not in item or "value" not in item:
                    raise SystemExit(
                        f"{path.name}: an item has no subject/value (stored before "
                        "the harness kept them); it cannot be scored on this metric"
                    )
                produced.append(
                    Unit(record["meeting"], pair_text(item["subject"], item["value"]))
                )
    return runs


def thesis_runs(path: pathlib.Path) -> dict[tuple[str, int], list[Unit]]:
    """`{(label, run): units}` from a thesis `extracciones.jsonl` (validation input).

    Only `accepted` decisions are scored, as in the thesis. A decision with
    several origin meetings is refused: this reproduction handles one meeting
    per decision, which is what the validation input has."""
    runs: dict[tuple[str, int], list[Unit]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        units = runs.setdefault((path.parent.name, int(record["run"])), [])
        for decision in record["decisions"]:
            if decision["status"] != "accepted":
                continue
            if len(decision.get("origin_meeting_ids") or []) > 1:
                raise SystemExit(f"{decision['id']}: several origin meetings")
            units.append(
                Unit(
                    decision["meeting_id"],
                    pair_text(decision["decision_object"], decision["content"]),
                )
            )
    return runs


# --- alignment -----------------------------------------------------------------


def min_cost_assignment(cost: Sequence[Sequence[float]]) -> list[tuple[int, int]]:
    """A minimum-cost one-to-one assignment of a rectangular matrix (Hungarian).

    Assigns `min(rows, cols)` pairs, as `scipy.optimize.linear_sum_assignment`
    does. Shortest-augmenting-path form with potentials, O(n^2 m)."""
    n_rows = len(cost)
    n_cols = len(cost[0]) if n_rows else 0
    if n_rows == 0 or n_cols == 0:
        return []
    transposed = n_rows > n_cols
    matrix = [list(col) for col in zip(*cost, strict=True)] if transposed else cost
    n, m = len(matrix), len(matrix[0])
    u = [0.0] * (n + 1)
    v = [0.0] * (m + 1)
    owner = [0] * (m + 1)  # owner[j] = row (1-based) assigned to column j
    way = [0] * (m + 1)
    for i in range(1, n + 1):
        owner[0] = i
        j0 = 0
        minv = [math.inf] * (m + 1)
        used = [False] * (m + 1)
        while True:
            used[j0] = True
            i0, delta, j1 = owner[j0], math.inf, 0
            for j in range(1, m + 1):
                if not used[j]:
                    current = matrix[i0 - 1][j - 1] - u[i0] - v[j]
                    if current < minv[j]:
                        minv[j], way[j] = current, j0
                    if minv[j] < delta:
                        delta, j1 = minv[j], j
            for j in range(m + 1):
                if used[j]:
                    u[owner[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if owner[j0] == 0:
                break
        while j0:
            j1 = way[j0]
            owner[j0] = owner[j1]
            j0 = j1
    pairs = [(owner[j] - 1, j - 1) for j in range(1, m + 1) if owner[j]]
    if transposed:
        pairs = [(c, r) for r, c in pairs]
    return sorted(pairs)


@dataclass(frozen=True)
class Alignment:
    tau: float
    tp: int
    n_predicted: int
    n_reference: int

    @property
    def precision(self) -> float:
        return self.tp / self.n_predicted if self.n_predicted else 0.0

    @property
    def recall(self) -> float:
        return self.tp / self.n_reference if self.n_reference else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if p + r else 0.0


def align(
    similarity: Sequence[Sequence[float]],
    admissible: Sequence[Sequence[bool]],
    tau: float,
    n_predicted: int,
    n_reference: int,
) -> Alignment:
    """The thesis's `align` under the `origin_meetings` mask, at one tau."""
    if n_predicted == 0 or n_reference == 0:
        return Alignment(tau, 0, n_predicted, n_reference)
    cost = [
        [
            -sim if ok and sim >= tau else EXCLUDED_PAIR_COST
            for sim, ok in zip(row, oks, strict=True)
        ]
        for row, oks in zip(similarity, admissible, strict=True)
    ]
    tp = sum(
        1
        for r, c in min_cost_assignment(cost)
        if admissible[r][c] and similarity[r][c] >= tau
    )
    return Alignment(tau, tp, n_predicted, n_reference)


def cosine_matrix(
    left: Sequence[Sequence[float]], right: Sequence[Sequence[float]]
) -> list[list[float]]:
    def unit(vector: Sequence[float]) -> list[float]:
        norm = math.sqrt(sum(x * x for x in vector)) or 1.0
        return [x / norm for x in vector]

    a = [unit(v) for v in left]
    b = [unit(v) for v in right]
    return [[sum(x * y for x, y in zip(p, q, strict=True)) for q in b] for p in a]


def score_run(
    predicted: Sequence[Unit],
    reference: Sequence[Unit],
    vectors: Mapping[str, Sequence[float]],
    taus: Sequence[float] = TAU_GRID,
) -> list[Alignment]:
    """One run of one series over the tau grid."""
    similarity = cosine_matrix(
        [vectors[u.text] for u in predicted], [vectors[r.text] for r in reference]
    )
    admissible = [[u.meeting == r.meeting for r in reference] for u in predicted]
    return [
        align(similarity, admissible, tau, len(predicted), len(reference))
        for tau in taus
    ]


# --- embeddings ------------------------------------------------------------------


def ollama_embedder(model: str = EMBEDDING_MODEL) -> Embed:
    """Embeddings from Ollama `POST /api/embed`, as the thesis's embedder calls it."""
    host = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
    if "://" not in host:
        host = f"http://{host}"

    def embed(texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), EMBED_BATCH):
            batch = list(texts[start : start + EMBED_BATCH])
            request = urllib.request.Request(  # noqa: S310 -- http(s) Ollama host
                f"{host.rstrip('/')}/api/embed",
                data=json.dumps({"model": model, "input": batch}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=300) as response:  # noqa: S310
                payload = json.loads(response.read().decode("utf-8"))
            got = payload.get("embeddings")
            if not isinstance(got, list) or len(got) != len(batch):
                raise SystemExit(f"unexpected embed response from {model}")
            vectors.extend([float(x) for x in v] for v in got)
        return vectors

    return embed


def embed_all(texts: Iterable[str], embed: Embed) -> dict[str, list[float]]:
    distinct = list(dict.fromkeys(texts))
    return dict(zip(distinct, embed(distinct), strict=True))


# --- language ----------------------------------------------------------------------

_WORD_RE: Final = re.compile(r"[^\W\d_]+")
_SPANISH: Final = frozenset(
    [
        "el",
        "la",
        "los",
        "las",
        "de",
        "del",
        "que",
        "en",
        "un",
        "una",
        "con",
        "por",
        "para",
        "se",
        "al",
        "lo",
        "es",
        "son",
        "como",
        "su",
        "sus",
        "y",
        "o",
        "pero",
        "más",
        "no",
        "sobre",
        "este",
        "esta",
        "usar",
        "tener",
    ]
)
_ENGLISH: Final = frozenset(
    [
        "the",
        "of",
        "to",
        "and",
        "a",
        "in",
        "is",
        "will",
        "be",
        "with",
        "for",
        "on",
        "that",
        "it",
        "as",
        "are",
        "this",
        "by",
        "use",
        "have",
        "we",
        "they",
        "should",
        "from",
    ]
)


def language_of(text: str) -> str:
    """`en`, `es` or `unknown`, by stopword counts (enough to tell the two apart)."""
    words = [w.casefold() for w in _WORD_RE.findall(text)]
    es = sum(w in _SPANISH for w in words)
    en = sum(w in _ENGLISH for w in words)
    if es == en:
        return "unknown"
    return "es" if es > en else "en"


# --- reporting -----------------------------------------------------------------------


def summarize(
    runs: Mapping[tuple[str, int], Sequence[Unit]],
    reference: Sequence[Unit],
    vectors: Mapping[str, Sequence[float]],
    taus: Sequence[float] = TAU_GRID,
) -> dict[str, Any]:
    """Per label: per-tau per-run P/R/F1 (mean, min, max, n) and the pooled (micro) figures."""
    out: dict[str, Any] = {}
    for label in sorted({key[0] for key in runs}):
        keys = sorted(key for key in runs if key[0] == label)
        per_run = [score_run(runs[key], reference, vectors, taus) for key in keys]
        languages = [language_of(u.text) for key in keys for u in runs[key]]
        curve = []
        for index, tau in enumerate(taus):
            column = [scores[index] for scores in per_run]
            tp = sum(a.tp for a in column)
            pooled = Alignment(
                tau,
                tp,
                sum(a.n_predicted for a in column),
                sum(a.n_reference for a in column),
            )
            curve.append(
                {
                    "tau": tau,
                    "recall": _stats([a.recall for a in column]),
                    "f1": _stats([a.f1 for a in column]),
                    "precision": _stats([a.precision for a in column]),
                    "tp": [a.tp for a in column],
                    "pooled": {
                        "tp": pooled.tp,
                        "n_predicted": pooled.n_predicted,
                        "n_reference": pooled.n_reference,
                        "precision": pooled.precision,
                        "recall": pooled.recall,
                        "f1": pooled.f1,
                    },
                }
            )
        out[label] = {
            "runs": len(keys),
            "n_reference": len(reference),
            "produced": [len(runs[key]) for key in keys],
            "language": {
                lang: languages.count(lang) for lang in ("en", "es", "unknown")
            },
            "curve": curve,
        }
    return out


def _stats(values: Sequence[float]) -> dict[str, float]:
    return {
        "mean": statistics.fmean(values),
        "min": min(values),
        "max": max(values),
        "n": len(values),
    }


def render(summary: Mapping[str, Any]) -> str:
    lines: list[str] = []
    for label, data in summary.items():
        lang = data["language"]
        total = sum(lang.values())
        lines.append(
            f"{label}: {data['runs']} run(s), {data['n_reference']} reference decisions, "
            f"produced per run {data['produced']}; language en {lang['en']} / es "
            f"{lang['es']} / unknown {lang['unknown']} of {total}"
        )
        lines.append(
            "  tau   recall mean (min-max)   F1 mean (min-max)   precision mean   "
            "pooled TP/ref   pooled TP/produced"
        )
        for point in data["curve"]:
            r, f, p, pooled = (
                point["recall"],
                point["f1"],
                point["precision"],
                point["pooled"],
            )
            lines.append(
                f"  {point['tau']:.2f}  {r['mean']:.3f} ({r['min']:.3f}-{r['max']:.3f})"
                f"     {f['mean']:.3f} ({f['min']:.3f}-{f['max']:.3f})"
                f"   {p['mean']:.3f}"
                f"            {pooled['tp']}/{pooled['n_reference']}"
                f"          {pooled['tp']}/{pooled['n_predicted']}"
            )
    return "\n".join(lines)


# --- command line --------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("runs", nargs="*", type=pathlib.Path)
    parser.add_argument("--gold", action="append", type=pathlib.Path)
    parser.add_argument("--thesis-runs", type=pathlib.Path)
    parser.add_argument("--embedding-model", default=EMBEDDING_MODEL)
    parser.add_argument("--json", type=pathlib.Path, help="also write the summary here")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        return _self_test()
    if not args.gold:
        parser.error("--gold is required unless --self-test")
    reference = load_reference(args.gold)
    runs = (
        thesis_runs(args.thesis_runs) if args.thesis_runs else harness_runs(args.runs)
    )
    if not runs:
        parser.error("no runs given")
    meetings = {r.meeting for r in reference}
    for units in runs.values():
        scoring.require_dev_meetings(sorted({u.meeting for u in units}))
        stray = {u.meeting for u in units} - meetings
        if stray:
            raise SystemExit(
                f"produced decisions from meetings outside the gold: {stray}"
            )
    texts = [r.text for r in reference] + [u.text for us in runs.values() for u in us]
    vectors = embed_all(texts, ollama_embedder(args.embedding_model))
    summary = summarize(runs, reference, vectors)
    print(render(summary))
    if args.json:
        args.json.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return 0


# --- self-test -----------------------------------------------------------------------


def _brute_force_tp(
    similarity: Sequence[Sequence[float]],
    admissible: Sequence[Sequence[bool]],
    tau: float,
) -> int:
    """The largest number of disjoint candidate pairs, by exhaustive search."""
    n_rows = len(similarity)
    n_cols = len(similarity[0]) if n_rows else 0

    def best(row: int, taken: frozenset[int]) -> int:
        if row == n_rows:
            return 0
        top = best(row + 1, taken)
        for col in range(n_cols):
            if (
                col not in taken
                and admissible[row][col]
                and similarity[row][col] >= tau
            ):
                top = max(top, 1 + best(row + 1, taken | {col}))
        return top

    return best(0, frozenset())


def _brute_force_cost(cost: Sequence[Sequence[float]]) -> float:
    import itertools

    n, m = len(cost), len(cost[0])
    if n <= m:
        return min(
            sum(cost[i][c] for i, c in enumerate(cols))
            for cols in itertools.permutations(range(m), n)
        )
    return min(
        sum(cost[r][j] for j, r in enumerate(rows))
        for rows in itertools.permutations(range(n), m)
    )


def _self_test() -> int:
    failures: list[str] = []

    def check(label: str, got: object, want: object) -> None:
        if got != want:
            failures.append(f"{label}: got {got!r}, want {want!r}")

    rng = random.Random(7)  # noqa: S311 -- a fixed-seed test oracle
    for trial in range(200):
        n, m = rng.randint(1, 5), rng.randint(1, 5)
        cost = [[rng.choice([rng.random(), 1e6]) for _ in range(m)] for _ in range(n)]
        pairs = min_cost_assignment(cost)
        check(f"assignment size, trial {trial}", len(pairs), min(n, m))
        check(
            f"assignment is one-to-one, trial {trial}",
            len({r for r, _ in pairs}) == len({c for _, c in pairs}) == len(pairs),
            True,
        )
        got = round(sum(cost[r][c] for r, c in pairs), 9)
        check(
            f"assignment is minimal, trial {trial}",
            got,
            round(_brute_force_cost(cost), 9),
        )
        similarity = [[rng.random() for _ in range(m)] for _ in range(n)]
        admissible = [[rng.random() < 0.7 for _ in range(m)] for _ in range(n)]
        tau = rng.choice([0.3, 0.5, 0.7])
        check(
            f"TP is the maximum candidate matching, trial {trial}",
            align(similarity, admissible, tau, n, m).tp,
            _brute_force_tp(similarity, admissible, tau),
        )

    # one-to-one: two predictions cannot claim one reference
    check("one-to-one", align([[0.9], [0.95]], [[True], [True]], 0.6, 2, 1).tp, 1)
    # the assignment prefers covering more references over one higher similarity
    check(
        "cardinality before similarity",
        align([[0.99, 0.61], [0.98, 0.10]], [[True, True], [True, True]], 0.6, 2, 2).tp,
        2,
    )
    check("below tau is no match", align([[0.59]], [[True]], 0.6, 1, 1).tp, 0)
    check("tau is inclusive", align([[0.6]], [[True]], 0.6, 1, 1).tp, 1)
    check("another meeting is no match", align([[0.99]], [[False]], 0.6, 1, 1).tp, 0)
    empty = align([], [], 0.6, 0, 3)
    check(
        "nothing produced: precision 0, recall 0",
        (empty.precision, empty.recall),
        (0.0, 0.0),
    )
    a = Alignment(0.6, 2, 8, 4)
    check("precision", a.precision, 0.25)
    check("recall", a.recall, 0.5)
    check("f1", round(a.f1, 6), round(2 * 0.25 * 0.5 / 0.75, 6))

    check(
        "pair text",
        pair_text("battery", "use a solar cell"),
        "battery: use a solar cell",
    )
    check("language en", language_of("The remote will use a solar cell"), "en")
    check("language es", language_of("usar una celda solar para la batería"), "es")
    check("language unknown", language_of("kinetic"), "unknown")

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        gold = root / "TS3009.decisions.adjudicated.csv"
        gold.write_text(
            "decision_id,meeting_id,evidence_text,final_status,final_object,final_content\n"
            "TS3009a.d01,TS3009a,x,decision,batería,usar una celda solar\n"
            "TS3009a.d02,TS3009a,x,no_decision,,\n"
            "TS3009c.d07,TS3009c,x,compuesta,varios,dos cosas\n"
            "TS3009c.d07-1,,,decision,carcasa,de goma\n"
            "TS3009c.d07-2,,,descartar,,\n",
            encoding="utf-8",
        )
        reference = load_reference([gold])
        check(
            "reference: decision rows and children only, meeting derived",
            reference,
            [
                Unit("TS3009a", "batería: usar una celda solar"),
                Unit("TS3009c", "carcasa: de goma"),
            ],
        )
        held_out = root / "TS3005.decisions.adjudicated.csv"
        held_out.write_text(
            "decision_id,meeting_id,final_status,final_object,final_content\n"
            "TS3005a.d01,TS3005a,decision,a,b\n",
            encoding="utf-8",
        )
        try:
            load_reference([held_out])
            failures.append("an evaluation series was accepted")
        except scoring.SeriesRefused:
            pass

        stored = root / "runs-pass-x.json"
        stored.write_text(
            json.dumps(
                {
                    "arm": "pass",
                    "model": "fake",
                    "records": [
                        {
                            "run": 1,
                            "meeting": "TS3009a",
                            "items": [
                                {"subject": "battery", "value": "use a solar cell"},
                                {"subject": "colour", "value": "make it yellow"},
                            ],
                        },
                        {
                            "run": 1,
                            "meeting": "TS3009c",
                            "items": [{"subject": "case", "value": "made of rubber"}],
                        },
                        {"run": 2, "meeting": "TS3009a", "items": []},
                        {"run": 2, "meeting": "TS3009c", "items": []},
                    ],
                }
            ),
            encoding="utf-8",
        )
        runs = harness_runs([stored])
        check("runs keyed by model and run", sorted(runs), [("fake", 1), ("fake", 2)])

        # A fake embedder: each text maps to a fixed direction, chosen so that
        # "battery" ~ "batería" (0.8), "case" ~ "carcasa" (0.65) and the rest is far.
        basis = {
            "battery: use a solar cell": [1.0, 0.0, 0.0, 0.0],
            "batería: usar una celda solar": [0.8, 0.6, 0.0, 0.0],
            "colour: make it yellow": [0.0, 0.0, 1.0, 0.0],
            "case: made of rubber": [0.0, 0.0, 0.0, 1.0],
            "carcasa: de goma": [0.0, 0.0, math.sqrt(1 - 0.65**2), 0.65],
        }
        vectors = embed_all(basis, lambda texts: [basis[t] for t in texts])
        summary = summarize(runs, reference, vectors, taus=(0.60, 0.70, 0.90))
        curve = summary["fake"]["curve"]
        check("tp per run at 0.60", curve[0]["tp"], [2, 0])
        check("tp per run at 0.70", curve[1]["tp"], [1, 0])
        check("tp per run at 0.90", curve[2]["tp"], [0, 0])
        check("mean recall at 0.60", curve[0]["recall"]["mean"], 0.5)
        check("pooled recall at 0.70", curve[1]["pooled"]["recall"], 0.25)
        check("pooled precision at 0.60", curve[0]["pooled"]["precision"], 2 / 3)
        check("produced per run", summary["fake"]["produced"], [3, 0])
        check(
            "language counts",
            summary["fake"]["language"],
            {"en": 3, "es": 0, "unknown": 0},
        )
        check("render names every tau", render(summary).count("\n  0."), 3)

        # the validation input shape (a thesis extraction record)
        jsonl = root / "es" / "extracciones.jsonl"
        jsonl.parent.mkdir()
        jsonl.write_text(
            json.dumps(
                {
                    "run": 0,
                    "decisions": [
                        {
                            "id": "x",
                            "meeting_id": "TS3009a",
                            "decision_object": "battery",
                            "content": "use a solar cell",
                            "status": "accepted",
                            "origin_meeting_ids": [],
                        },
                        {
                            "id": "y",
                            "meeting_id": "TS3009a",
                            "decision_object": "z",
                            "content": "w",
                            "status": "rejected",
                            "origin_meeting_ids": [],
                        },
                    ],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        check(
            "thesis runs: accepted decisions only",
            thesis_runs(jsonl),
            {("es", 0): [Unit("TS3009a", "battery: use a solar cell")]},
        )

    if failures:
        print("self-test: FAILED")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("self-test: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
