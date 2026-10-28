"""The deterministic half of the merged-body reconciliation screen (#1563).

`merge` can ask a model to rewrite two overlapping notes as one document
(`resolution/reconciliation.py::reconcile_merged_body`). The user consents to the
merge on a plan that says a model call will happen; the rewritten body itself is
not shown before it is written, and the only guards on it are a length floor and
a heading check. This module holds everything about measuring that task which
needs no model: the labelled fixture and its contract, the scoring of one merged
body, the model-free baselines (including the stacked body production keeps when
a reply is refused), the fabrication check, the aggregate numbers, the paired
bootstrap, the vetoes and the forecast. The runner (`run_reconcile_screen.py`)
owns the model.

A claim is kept when its literal anchors survive in the merged body; a claim both
notes state must additionally appear exactly once, because the production prompt
asks for "every distinct claim from both notes exactly once". Labels are the
first author's until the owner's blind labelling is recorded; read `README.md`
before trusting any number computed from them.

Imports only the standard library and `openkos.model.okf`, for the one spelling
of the stacked-body heading, so a unit test can load it straight from its path.
"""

from __future__ import annotations

import json
import math
import pathlib
import random
import re
import statistics
from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from openkos.model import okf

HERE: Final = pathlib.Path(__file__).resolve().parent
REPO_ROOT: Final = HERE.parents[1]
FIXTURE_PATH: Final = HERE / "fixture.json"

KINDS: Final = ("a_only", "b_only", "both")
"""Which note states a claim: only the survivor's (NOTE A), only the absorbed
note's (NOTE B), or both."""

ARMS: Final = ("default",)
"""One arm: the production prompt, one chat call per case and run. There is no
second arm because the prompt has no current value to anchor on."""

FAILURE_ABS_CAP: Final = 0.10
FAILURE_MARGIN: Final = 0.02
LATENCY_RATIO_CAP: Final = 2.0
FABRICATION_MARGIN: Final = 0.10
"""The registered vetoes (see `PREREGISTRATION.md`)."""

UNION_THRESHOLD: Final = 0.30
"""Token-overlap (Jaccard) at which the model-free `sentence_union` baseline
treats a NOTE B sentence as a restatement of one already kept. Fixed before any
number was computed from it, and not tuned: a baseline tuned on the fixture
would be a baseline fitted to the labels."""

BASELINES: Final = (
    "stack",
    "survivor_only",
    "absorbed_only",
    "longer_only",
    "sentence_union",
)


# --- the fixture -------------------------------------------------------------


@dataclass(frozen=True)
class Piece:
    """One verbatim quotation from a repository file."""

    source: Mapping[str, str]
    text: str


@dataclass(frozen=True)
class Claim:
    """One claim the merged body must keep, and which note states it."""

    id: str
    kind: str
    claim: str
    anchors: tuple[str, ...]
    once: str | None
    alt_label: str | None
    adjudication: Mapping[str, str] | None
    retired: str | None

    @property
    def first_kind(self) -> str:
        """The first author's label: `kind` is the RESOLVED one, so this differs
        exactly on the claims with an adjudication record."""
        return self.adjudication["first"] if self.adjudication else self.kind

    @property
    def ambiguous(self) -> bool:
        return self.alt_label is not None


@dataclass(frozen=True)
class Case:
    """Two notes about one subject, as `merge` hands them to the model."""

    id: str
    title: str
    survivor: tuple[Piece, ...]
    absorbed: tuple[Piece, ...]
    claims: tuple[Claim, ...]

    @property
    def survivor_body(self) -> str:
        return "\n\n".join(p.text for p in self.survivor)

    @property
    def absorbed_body(self) -> str:
        return "\n\n".join(p.text for p in self.absorbed)

    @property
    def active(self) -> tuple[Claim, ...]:
        return tuple(c for c in self.claims if c.retired is None)


def parse_claim(raw: Mapping[str, Any]) -> Claim:
    claim_id = str(raw["id"])
    kind = str(raw["kind"])
    if kind not in KINDS:
        raise ValueError(f"{claim_id}: kind {kind!r} is not one of {KINDS}")
    alt = raw.get("alt_label")
    if alt is not None and (alt not in KINDS or alt == kind):
        raise ValueError(f"{claim_id}: alt_label {alt!r} must be another kind")
    record = raw.get("adjudication")
    if record is not None:
        if set(record) != {"first", "second", "resolved", "reason"}:
            raise ValueError(
                f"{claim_id}: adjudication needs first/second/resolved/reason"
            )
        if record["resolved"] != kind or record["first"] == kind:
            raise ValueError(f"{claim_id}: adjudication must resolve to `kind`")
        if alt != record["first"]:
            raise ValueError(
                f"{claim_id}: adjudication keeps the first label as alt_label"
            )
    return Claim(
        id=claim_id,
        kind=kind,
        claim=str(raw["claim"]),
        anchors=tuple(str(a) for a in raw["anchors"]),
        once=None if raw.get("once") is None else str(raw["once"]),
        alt_label=None if alt is None else str(alt),
        adjudication=None if record is None else {k: str(v) for k, v in record.items()},
        retired=None if raw.get("retired") is None else str(raw["retired"]),
    )


def _piece(raw: Mapping[str, Any]) -> Piece:
    return Piece(source=dict(raw.get("source") or {}), text=str(raw["text"]))


def _case(raw: Mapping[str, Any]) -> Case:
    return Case(
        id=str(raw["id"]),
        title=str(raw["title"]),
        survivor=tuple(_piece(p) for p in raw["survivor"]),
        absorbed=tuple(_piece(p) for p in raw["absorbed"]),
        claims=tuple(parse_claim(c) for c in raw["claims"]),
    )


def load_fixture(path: pathlib.Path = FIXTURE_PATH) -> list[Case]:
    """The labelled cases, parsed. `integrity_problems` is the separate check
    that each one is scoreable."""
    data = json.loads(path.read_text(encoding="utf-8"))
    cases = [_case(raw) for raw in data["cases"]]
    ids = [c.id for c in cases]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate case ids")
    return cases


def class_balance(cases: Sequence[Case]) -> dict[str, int]:
    counts = Counter(c.kind for case in cases for c in case.active)
    return {kind: counts.get(kind, 0) for kind in KINDS}


def norm(text: str) -> str:
    """Casefold and collapse all whitespace: the one matching rule."""
    return " ".join(text.casefold().split())


def integrity_problems(cases: Sequence[Case]) -> list[str]:
    """Every way a claim cannot be scored as the fixture says it can.

    A one-sided claim's anchors must be in its own note and absent from the
    other, or keeping them would not show the claim was kept. A shared claim's
    anchors must be in both notes and its `once` literal must occur exactly once
    in each. No anchor may sit in the case title, because production pins the
    title as the body's heading and would hand every model that claim."""
    problems: list[str] = []
    seen: set[str] = set()
    for case in cases:
        a, b = norm(case.survivor_body), norm(case.absorbed_body)
        title = norm(case.title)
        if not case.active:
            problems.append(f"{case.id}: no active claims")
        for claim in case.active:
            where = claim.id
            if claim.id in seen:
                problems.append(f"{where}: duplicate claim id")
            seen.add(claim.id)
            if claim.kind not in KINDS:
                problems.append(f"{where}: unknown kind {claim.kind!r}")
                continue
            if not claim.anchors:
                problems.append(f"{where}: no anchors")
            for anchor in claim.anchors:
                x = norm(anchor)
                if x in title:
                    problems.append(f"{where}: anchor {anchor!r} is in the case title")
                in_a, in_b = x in a, x in b
                if claim.kind in ("a_only", "both") and not in_a:
                    problems.append(
                        f"{where}: anchor {anchor!r} is not in the survivor"
                    )
                if claim.kind in ("b_only", "both") and not in_b:
                    problems.append(
                        f"{where}: anchor {anchor!r} is not in the absorbed note"
                    )
                if claim.kind == "a_only" and in_b:
                    problems.append(
                        f"{where}: anchor {anchor!r} is also in the absorbed note"
                    )
                if claim.kind == "b_only" and in_a:
                    problems.append(
                        f"{where}: anchor {anchor!r} is also in the survivor"
                    )
            if claim.kind == "both":
                if claim.once is None:
                    problems.append(f"{where}: a `both` claim needs a `once` literal")
                else:
                    x = norm(claim.once)
                    if a.count(x) != 1 or b.count(x) != 1:
                        problems.append(
                            f"{where}: `once` {claim.once!r} must occur exactly once in "
                            f"each note (survivor {a.count(x)}, absorbed {b.count(x)})"
                        )
            elif claim.once is not None:
                problems.append(f"{where}: only a `both` claim carries `once`")
    return problems


def _squash(text: str) -> str:
    return " ".join(text.split())


def provenance_problems(
    cases: Sequence[Case], *, repo_root: pathlib.Path = REPO_ROOT
) -> list[str]:
    """Every quoted piece that is no longer verbatim in its source file.

    A label was decided on this text; if the source has been edited since, the
    label is about something else and the fixture is stale."""
    problems: list[str] = []
    for case in cases:
        for side, pieces in (("survivor", case.survivor), ("absorbed", case.absorbed)):
            for piece in pieces:
                kind, ref = piece.source.get("kind"), piece.source.get("ref", "")
                if kind != "file":
                    problems.append(f"{case.id}/{side}: unknown source kind {kind!r}")
                    continue
                path = repo_root / ref
                if not path.is_file():
                    problems.append(f"{case.id}/{side}: source {ref} not found")
                elif _squash(piece.text) not in _squash(
                    path.read_text(encoding="utf-8")
                ):
                    problems.append(f"{case.id}/{side}: text is not in {ref} any more")
    return problems


# --- scoring one merged body ----------------------------------------------------


def score_claim(claim: Claim, text: str, kind: str | None = None) -> tuple[bool, bool]:
    """`(strict, lenient)` for one claim against one merged body.

    Strict: every anchor survives and, for a shared claim, its `once` literal
    appears exactly once. Lenient also accepts a claim the first author flagged
    as arguable in any number. Scoring against another `kind` (the first
    author's, for the sensitivity analysis) falls back to retention when that
    kind is `both` and the claim carries no `once` literal."""
    hay = norm(text)
    kept = all(norm(a) in hay for a in claim.anchors)
    if not kept:
        return (False, False)
    use = claim.kind if kind is None else kind
    strict = True
    if use == "both" and claim.once is not None:
        strict = hay.count(norm(claim.once)) == 1
    return (strict, True if claim.ambiguous else strict)


def case_score(case: Case, text: str, *, which: str = "strict") -> float:
    """The share of the case's active claims kept; `which` is `strict`,
    `lenient` or `first` (strict, against the first author's kinds)."""
    claims = case.active
    if not claims:
        return 0.0
    total = 0.0
    for claim in claims:
        if which == "first":
            total += score_claim(claim, text, kind=claim.first_kind)[0]
        else:
            strict, lenient = score_claim(claim, text)
            total += lenient if which == "lenient" else strict
    return total / len(claims)


# --- the model-free baselines ----------------------------------------------------


def stack_text(case: Case) -> str:
    """The body production keeps when a reply is refused or the call fails: the
    survivor's body, then the absorbed one under the stacked heading."""
    heading = okf.merged_content_heading("absorbed")
    return f"{case.survivor_body}{heading}\n\n{case.absorbed_body}"


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?;])\s+|\n+", text)
    return [p.strip() for p in parts if p.strip()]


def _content_tokens(sentence: str) -> frozenset[str]:
    return frozenset(
        w for w in re.findall(r"[a-z0-9]+", sentence.casefold()) if len(w) > 3
    )


def sentence_union(
    survivor: str, absorbed: str, *, threshold: float = UNION_THRESHOLD
) -> str:
    """A cheap, deterministic dedupe: every survivor sentence, plus each absorbed
    sentence whose content words do not overlap (Jaccard) any kept sentence by
    `threshold` or more. It is what one would ship without a model."""
    kept = _sentences(survivor)
    tokens = [_content_tokens(s) for s in kept]
    for sentence in _sentences(absorbed):
        mine = _content_tokens(sentence)
        restated = any(
            mine and other and len(mine & other) / len(mine | other) >= threshold
            for other in tokens
        )
        if not restated:
            kept.append(sentence)
            tokens.append(mine)
    return "\n\n".join(kept)


def baseline_texts(case: Case) -> dict[str, str]:
    a, b = case.survivor_body, case.absorbed_body
    return {
        "stack": stack_text(case),
        "survivor_only": a,
        "absorbed_only": b,
        "longer_only": a if len(a) >= len(b) else b,
        "sentence_union": sentence_union(a, b),
    }


def baseline_claim_scores(cases: Sequence[Case]) -> dict[str, dict[str, bool]]:
    """Claim id -> strict kept, per baseline."""
    out: dict[str, dict[str, bool]] = {name: {} for name in BASELINES}
    for case in cases:
        for name, text in baseline_texts(case).items():
            for claim in case.active:
                out[name][claim.id] = score_claim(claim, text)[0]
    return out


def baseline_case_scores(
    cases: Sequence[Case], *, which: str = "strict"
) -> dict[str, dict[str, float]]:
    """Case id -> score, per baseline; the per-case reference of the paired test."""
    out: dict[str, dict[str, float]] = {name: {} for name in BASELINES}
    for case in cases:
        for name, text in baseline_texts(case).items():
            out[name][case.id] = case_score(case, text, which=which)
    return out


def baseline_summary(cases: Sequence[Case]) -> dict[str, Any]:
    """Every number the fixture's floor rests on: claims kept (k of N) and mean
    case score per baseline, the strongest, how many cases no baseline merges
    perfectly, and how many claims the strongest baseline gets wrong."""
    claims = baseline_claim_scores(cases)
    per_case = baseline_case_scores(cases)
    n = sum(len(case.active) for case in cases)
    baselines = {
        name: {
            "claims_kept": sum(claims[name].values()),
            "n_claims": n,
            "mean_case_score": statistics.fmean(per_case[name].values()),
        }
        for name in BASELINES
    }
    strongest = max(BASELINES, key=lambda name: baselines[name]["mean_case_score"])
    return {
        "baselines": baselines,
        "strongest": strongest,
        "cases_no_baseline_perfect": sum(
            all(per_case[name][case.id] < 1.0 for name in BASELINES) for case in cases
        ),
        "claims_strongest_wrong": sum(not v for v in claims[strongest].values()),
        "claims_all_baselines_wrong": sum(
            all(not claims[name][c.id] for name in BASELINES)
            for case in cases
            for c in case.active
        ),
    }


# --- fabrication ------------------------------------------------------------------


_DIGIT_TOKEN: Final = re.compile(r"[\w§#.\-/]*\d[\w§#.\-/]*")
_CODE_SPAN: Final = re.compile(r"`[^`\n]+`")
_LIST_MARKER: Final = re.compile(r"^\s*\d+[.)]\s+", re.M)


def novel_tokens(text: str, case: Case) -> list[str]:
    """Digit-bearing words and code spans in a merged body that neither note nor
    the title contains: invented figures, versions, identifiers and paths. Numbered
    list markers are not tokens. Order of first appearance, de-duplicated."""
    allowed = norm(f"{case.survivor_body} {case.absorbed_body} {case.title}")
    body = _LIST_MARKER.sub("", text)
    spans = [(m.start(), m.group(0)) for m in _CODE_SPAN.finditer(body)]
    spans += [
        (m.start(), m.group(0).strip(".,;:-/")) for m in _DIGIT_TOKEN.finditer(body)
    ]
    found: list[str] = []
    for _position, token in sorted(spans):
        if token and norm(token) not in allowed and token not in found:
            found.append(token)
    return found


# --- rows and the aggregate --------------------------------------------------------


def effective_text(case: Case, row: Mapping[str, Any]) -> str:
    """What the user would end up with: the model's body, or the stack when the
    call failed or the reply was refused."""
    if row["failure"] is None and row["output"] is not None:
        return str(row["output"])
    return stack_text(case)


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _p95(values: Sequence[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[math.ceil(0.95 * len(ordered)) - 1]


def summarize(
    rows: Sequence[Mapping[str, Any]], cases: Sequence[Case], arm: str = "default"
) -> dict[str, Any]:
    """Every number the screen reports for one model.

    Accuracy is the mean over CASES of each case's mean over runs, so every case
    weighs the same however many runs it has. Retention, dedupe and fabrication
    describe the bodies the model actually produced (a refused or failed call has
    none); the failure rate and the latencies are over calls."""
    by_id = {c.id: c for c in cases}
    mine = [r for r in rows if r["arm"] == arm and r["case"] in by_id]
    scores: dict[str, dict[str, list[float]]] = {
        w: {} for w in ("strict", "lenient", "first")
    }
    failures: Counter[str] = Counter()
    kept = total = 0
    kept_by_kind: dict[str, list[bool]] = {k: [] for k in KINDS}
    shared_once: list[bool] = []
    fabricated: list[bool] = []
    for row in mine:
        case = by_id[row["case"]]
        text = effective_text(case, row)
        for which in scores:
            scores[which].setdefault(case.id, []).append(
                case_score(case, text, which=which)
            )
        if row["failure"]:
            failures[str(row["failure"])] += 1
        if row["failure"] is None and row["output"] is not None:
            fabricated.append(bool(novel_tokens(text, case)))
            for claim in case.active:
                strict, _lenient = score_claim(claim, text)
                retained = all(norm(a) in norm(text) for a in claim.anchors)
                kept += retained
                total += 1
                kept_by_kind[claim.kind].append(retained)
                if claim.kind == "both" and retained:
                    shared_once.append(strict)
    latencies = [float(r["latency_s"]) for r in mine]
    repeated = [v for v in scores["strict"].values() if len(v) >= 2]
    n = len(mine)

    def case_mean(which: str) -> float | None:
        return _mean([sum(v) / len(v) for v in scores[which].values()])

    return {
        "arm": arm,
        "n_calls": n,
        "n_cases": len(scores["strict"]),
        "strict_accuracy": case_mean("strict"),
        "lenient_accuracy": case_mean("lenient"),
        "strict_first_accuracy": case_mean("first"),
        "retention": kept / total if total else None,
        "retention_by_kind": {
            k: _mean([float(x) for x in v]) for k, v in kept_by_kind.items()
        },
        "dedupe_rate": _mean([float(x) for x in shared_once]),
        "fabrication_rate": _mean([float(x) for x in fabricated]),
        "failure_rate": sum(failures.values()) / n if n else None,
        "failures": dict(failures),
        "stability": (
            sum(len(set(v)) == 1 for v in repeated) / len(repeated)
            if repeated
            else None
        ),
        "median_latency_s": statistics.median(latencies) if latencies else None,
        "p95_latency_s": _p95(latencies),
    }


def per_case_scores(
    rows: Sequence[Mapping[str, Any]], cases: Sequence[Case], arm: str = "default"
) -> dict[str, float]:
    """Each case's mean strict score over the runs, for the paired test."""
    by_id = {c.id: c for c in cases}
    scores: dict[str, list[float]] = {}
    for row in rows:
        if row["arm"] == arm and row["case"] in by_id:
            case = by_id[row["case"]]
            scores.setdefault(case.id, []).append(
                case_score(case, effective_text(case, row))
            )
    return {k: sum(v) / len(v) for k, v in scores.items()}


# --- comparison ---------------------------------------------------------------------


def paired_bootstrap(
    candidate: Mapping[str, float],
    reference: Mapping[str, float],
    *,
    resamples: int = 10_000,
    seed: int = 42,
) -> dict[str, float]:
    """Mean per-case difference `candidate - reference` and its 95% percentile
    bootstrap interval over cases (resampled with replacement)."""
    if set(candidate) != set(reference):
        raise ValueError("paired scores need the same cases on both sides")
    deltas = [candidate[k] - reference[k] for k in sorted(candidate)]
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
    """The registered vetoes a candidate trips, named. A missing number trips its veto."""
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
    fab, base_fab = (
        num(candidate, "fabrication_rate"),
        num(baseline, "fabrication_rate"),
    )
    if fab is None or base_fab is None or fab > base_fab + FABRICATION_MARGIN:
        tripped.append("V3")
    return tripped


def selected(delta: Mapping[str, float], tripped: Sequence[str]) -> bool:
    """A candidate replaces the baseline only with a positive 95% lower bound on
    its paired gain and no veto."""
    return delta["point"] > 0 and delta["lo"] > 0 and not tripped


def justified(delta_vs_reference: Mapping[str, float]) -> bool:
    """The model call is justified for a model only if its paired gain over the
    strongest model-free baseline has a positive 95% lower bound."""
    return delta_vs_reference["lo"] > 0


def reference_baseline(cases: Sequence[Case]) -> tuple[str, dict[str, float]]:
    """The strongest model-free predictor on the labels as they stand (name and
    per-case strict scores): the bar J is measured against."""
    per_case = baseline_case_scores(cases)
    name = max(BASELINES, key=lambda n: statistics.fmean(per_case[n].values()))
    return name, per_case[name]


def j_tolerance(
    reference: Mapping[str, float], *, wins: int, resamples: int = 10_000
) -> int:
    """How many cases a candidate can lose outright and still clear J, when it
    wins `wins` outright (the reference's lowest-scoring cases, to 1.0), loses
    the reference's highest-scoring cases (to 0.0) and matches it elsewhere.
    -1 means it cannot clear J even losing nothing."""
    order = sorted(reference, key=lambda k: (reference[k], k))
    best = -1
    for losses in range(len(order) - wins + 1):
        candidate = dict(reference)
        for case_id in order[:wins]:
            candidate[case_id] = 1.0
        for case_id in order[len(order) - losses :] if losses else []:
            candidate[case_id] = 0.0
        if justified(paired_bootstrap(candidate, reference, resamples=resamples)):
            best = losses
        else:
            break
    return best


def constant_gain_bound(
    reference: Mapping[str, float], *, gain: float, resamples: int = 10_000
) -> dict[str, float]:
    """The paired gain of a candidate that matches the reference on every case and
    is better by `gain` on each, a case score never going above 1.0 (the
    convention `test_the_j_bar_on_the_real_reference_is_pinned` uses)."""
    candidate = {k: min(1.0, v + gain) for k, v in reference.items()}
    return paired_bootstrap(candidate, reference, resamples=resamples)


def j_table_rows(
    reference: Mapping[str, float], *, resamples: int = 10_000
) -> list[dict[str, int]]:
    """`j_tolerance` for every win count from 0 to the number of cases the
    reference scores below 1.0 (a win on a perfect case changes nothing)."""
    below = sum(v < 1.0 for v in reference.values())
    return [
        {
            "wins": wins,
            "tolerance": j_tolerance(reference, wins=wins, resamples=resamples),
        }
        for wins in range(below + 1)
    ]


def render_j_table(
    name: str,
    reference: Mapping[str, float],
    *,
    gains: Sequence[float],
    resamples: int = 10_000,
) -> str:
    """The J bar as markdown, from the labels alone (no model)."""
    below = sum(v < 1.0 for v in reference.values())
    lines = [
        f"# J bar (no model contacted); reference: {name}",
        "",
        f"the reference scores below 1.0 in {below} of {len(reference)} cases "
        f"(bootstrap {resamples} resamples over cases, seed 42)",
        "",
        "constant gain on every case (a case score capped at 1.0):",
        "",
        "| gain | point | 95% lower bound |",
        "| --- | --- | --- |",
    ]
    for gain in gains:
        bound = constant_gain_bound(reference, gain=gain, resamples=resamples)
        lines.append(f"| +{gain:.2f} | {bound['point']:.3f} | {bound['lo']:.3f} |")
    lines += [
        "",
        "outright wins (the reference's lowest cases to 1.0) with the most outright "
        "losses (its highest cases to 0.0) that still clear J (-1: none, losing "
        "nothing included):",
        "",
        "| wins | lose at most |",
        "| --- | --- |",
    ]
    lines += [
        f"| {r['wins']} | {r['tolerance']} |"
        for r in j_table_rows(reference, resamples=resamples)
    ]
    return "\n".join(lines)


# --- forecast ----------------------------------------------------------------------


def call_count(n_cases: int, *, runs: int, models: int) -> int:
    return n_cases * runs * models


def forecast(
    *,
    n_cases: int,
    runs: int,
    median_latency_s: Mapping[str, float],
    load_s: float,
) -> dict[str, Any]:
    """Calls and Ollama wall-clock for a run: per model, calls times its median
    call latency plus one load. The latencies are an INPUT (the registration
    names where each came from); nothing here measures one."""
    per_model = call_count(n_cases, runs=runs, models=1)
    seconds = {m: per_model * lat + load_s for m, lat in median_latency_s.items()}
    return {
        "calls_per_model": per_model,
        "calls": per_model * len(median_latency_s),
        "seconds": seconds,
        "total_seconds": sum(seconds.values()),
    }


# --- a synthetic merge, for the self-tests -----------------------------------------


def synthetic_merge(
    case: Case,
    *,
    drop: Collection[str] = (),
    duplicate: Collection[str] = (),
    invent: str = "",
) -> str:
    """A merged body assembled from the claims' own anchors: every claim once, minus
    the ids in `drop`, with the ids in `duplicate` stated twice, plus `invent`. It
    stands in for a model in tests that must not call one."""
    parts: list[str] = []
    for claim in case.active:
        if claim.id in drop:
            continue
        sentence = " ".join(claim.anchors)
        if claim.once is not None and norm(claim.once) not in norm(sentence):
            sentence += f" {claim.once}"
        parts.append(sentence)
        if claim.id in duplicate:
            parts.append(sentence)
    if invent:
        parts.append(invent)
    return "\n\n".join(parts)
