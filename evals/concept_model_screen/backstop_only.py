"""Can the selector judge's cut be removed, keeping only the positional backstop? (#1579)

Implements `BACKSTOP_ONLY_PREREGISTRATION.md` and decides nothing it does not
register. Read that file first: the arms, metrics, margins, bootstrap, cost
budgets, confirmation set and decision rule below are copied from it, and the
self-test pins each registered number so a silent edit fails loudly.

Reads the #1571 replay records (`judge_study.py`), the #1557 records and
caches, the step 1 labels (`cut_precision.py`) and the step 3 labels
(`label_study.py`); never writes any of them. Only `confirm --run` calls a
model (`gemma4:26b-a4b` as the fixed selector judge, `gpt-oss:20b` for G1);
every other subcommand is CPU-only and reads embeddings from the cache.

Subcommands:

- `population`  the pinned primary population and the confirmation list;
                refuses any meeting outside the registered sets, and TS3012
                explicitly.
- `replay`      V1.1: the backstop applied to the recorded `ceiling` stage,
                checked against the stored records.
- `cost`        C1: `downstream_cost.py` over the backstop-only set against
                the registered budget.
- `score`       P1, P2, S1, per-type counts, V1, and the decision rule.
- `confirm`     preflight (model-free) and, with `--run`, the fixed-judge
                replays and G1 labels for the confirmation set.
- `decide`      the decision rule over the stored score, cost and
                confirmation results.

Model-free check: `--self-test`.
"""

from __future__ import annotations

import argparse
import collections
import dataclasses
import functools
import hashlib
import inspect
import json
import pathlib
import random
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Final

HERE = pathlib.Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(HERE))
sys.path.append(str(REPO_ROOT / "evals"))

import cut_precision as cp  # noqa: E402
import downstream_cost as dc  # noqa: E402
import drop_ablation as ab  # noqa: E402
import judge_study as js  # noqa: E402
import label_study as ls  # noqa: E402
import screen_data as sd  # noqa: E402
import screen_judge as sj  # noqa: E402
import screen_metrics as sm  # noqa: E402
import screen_pipeline as sp  # noqa: E402

from openkos.resolution import candidates  # noqa: E402

Obj = dict[str, Any]
Item = tuple[int, int, int, int]
"""(backstop-only hits, backstop-only labelled, judge hits, judge labelled) for
one window: the first pair is the candidate, the second the baseline."""
Send = Callable[[str, Sequence[Any], int, float], tuple[str | None, dict[str, Any]]]

OUT_DIR: Final = pathlib.Path.home() / "Dev/Projects/openkos-eval-runs/backstop-only"
REGISTRATION: Final = HERE / "BACKSTOP_ONLY_PREREGISTRATION.md"
ARMS: Final = ("self", "fixed")
"""The judge arms `backstop-only` is compared with: `judge-self` and `judge-fixed`."""
BACKSTOP: Final = js.concept_mod._UNION_BACKSTOP
"""The production positional backstop, imported and never restated."""
ID_CAP: Final = candidates._MAX_CANDIDATE_GROUPS
"""The identity review cap that must not bind (registered: 50)."""

# --- registered numbers (pinned by a self-test) ---------------------------------
P1_MARGIN: Final = -0.005
"""P1, window lift: the one-sided 95% lower bound of `backstop-only` minus
judge must be above this."""
P2_MARGIN: Final = -0.05
"""P2, strict-G1 precision: same direction, owner-confirmed."""
S1_MARGIN: Final = 0.05
"""S1, clutter share, only if the owner makes it gating: the one-sided 95%
upper bound of the INCREASE must be below this. Non-gating by default."""
OBJECTS_RATIO_MAX: Final = 1.65
"""C1: objects and reindex embedding calls, backstop-only / judge-kept."""
ROWS_PER_25_MAX: Final = 12
"""C1: extra identity review rows per 25 sources."""
ROWS_PER_WORKSPACE_MAX: Final = 25
"""C1: extra identity review rows per five-bundle workspace."""
ONE_SIDED: Final = 0.05
"""One-sided 95% bounds are the 5th and 95th bootstrap percentiles."""
EXPECTED_RECORDS: Final = 25
EXPECTED_CEILING_OBJECTS: Final = 352
EXPECTED_BACKSTOP_CUT: Final = 23
EXPECTED_CUT_SOURCES: Final = 8
EXPECTED_PLAN_RECORDS: Final = 35
CONFIRM_MODEL: Final = "qwen3:8b"
CONFIRM_RUNS: Final = (1, 2, 3)
CONFIRM_MEETINGS: Final = (
    "IS1002b",
    "IS1002c",
    "IS1002d",
    "IS1005a",
    "IS1005b",
    "IS1005c",
)
INELIGIBLE_SERIES: Final = ("TS3012",)
"""Ineligible under epic #1556 although `POOL_F_WINDOWED` lists them."""
GPU_BUDGET_S: Final = 45 * 60.0
EMBED_BUDGET_S: Final = 300.0
"""Amendment 1: wall-clock ceiling for the confirmation-set embeddings, on top
of `GPU_BUDGET_S`."""
EMBED_BATCH: Final = 32
CONTROL_CYCLE: Final = ("IS1002", "IS1005", "TS3012")
"""Amendment 1: the thesis cross-team control cycle the stock rule walks."""
STOP_AFTER_CALLS: Final = 20
STOP_MEAN_WALL_S: Final = 25.0
GOOD_STRICT: Final = frozenset({"supported"})
PER_TYPE: Final = ("Decision", "Project", "Concept", "Event")
NI: Final = "non_inferior"
INF: Final = "inferior"
UNR: Final = "unresolved"


class RefusedMeeting(ValueError):
    """A meeting the registration forbids was requested."""


class RefusedEmbedding(ValueError):
    """An embedding was requested for a text outside the confirmation set."""


class StopLine(RuntimeError):
    """The registered stop line fired; no partial verdict is read."""


# --- pure: meetings ------------------------------------------------------------


def refuse_meetings(meetings: Sequence[str]) -> list[str]:
    """`meetings` unchanged if every one may be read, else `RefusedMeeting`.

    TS3012 is refused by name BEFORE the allowlist is consulted: the allowlist
    (and `POOL_F_WINDOWED`) lists it, so relying on the allowlist would let it
    through. Thesis evaluation series are refused with their own reason."""
    for meeting in meetings:
        try:
            series = sd.series_of(meeting)
        except sd.DisallowedMeeting as err:
            raise RefusedMeeting(f"refusing {meeting!r}: {err}") from err
        if series in INELIGIBLE_SERIES:
            raise RefusedMeeting(
                f"refusing {meeting!r}: {series} is ineligible under epic #1556"
            )
        if series in sd.THESIS_EVALUATION_SERIES:
            raise RefusedMeeting(
                f"refusing {meeting!r}: it belongs to a thesis evaluation series"
            )
        try:
            sd.require_allowed(meeting)
        except sd.DisallowedMeeting as err:
            raise RefusedMeeting(str(err)) from err
    return list(meetings)


def confirmation_meetings(requested: Sequence[str] | None = None) -> list[str]:
    """The registered confirmation meetings (or a registered subset)."""
    wanted = list(CONFIRM_MEETINGS) if requested is None else list(requested)
    refuse_meetings(wanted)
    for meeting in wanted:
        if meeting not in CONFIRM_MEETINGS:
            raise RefusedMeeting(
                f"refusing {meeting!r}: not in the registered confirmation set "
                f"{CONFIRM_MEETINGS}"
            )
    return wanted


def foreign_control(meeting: str) -> str:
    """The meeting whose gold is the null control for `meeting` (amendment 1).

    Primary meetings keep the stock rule (`screen_data.foreign_meeting`). For a
    confirmation meeting the rule, fixed before any run, is: walk the control
    cycle from the next series, skip TS3012 and the meeting's own series, and
    take the first same-letter meeting on the allowlist; if none, take the first
    same-letter meeting of the development series in their registered order.
    Every candidate passes `refuse_meetings`, so TS3012 and held-out series
    never enter."""
    refuse_meetings([meeting])
    if meeting not in CONFIRM_MEETINGS:
        control = sd.foreign_meeting(meeting)
        refuse_meetings([control])
        return control
    series, letter = sd.series_of(meeting), sd.letter_of(meeting)
    start = CONTROL_CYCLE.index(series)
    ordered = [CONTROL_CYCLE[(start + k) % len(CONTROL_CYCLE)] for k in range(1, 3)]
    for candidate_series in [*ordered, *sd.DEV_SERIES]:
        candidate = f"{candidate_series}{letter}"
        if candidate_series in (series, *INELIGIBLE_SERIES):
            continue
        if candidate in sd.ALLOWED_MEETINGS:
            refuse_meetings([candidate])
            return candidate
    raise RefusedMeeting(f"no eligible null control for {meeting!r}")


def guarded_embed(
    embed: Callable[[list[str]], list[list[float]]], allowed: set[str]
) -> Callable[[list[str]], list[list[float]]]:
    """Amendment 1: embeddings may be computed only for confirmation-set texts.
    A batch holding any other text embeds nothing."""

    def guarded(texts: list[str]) -> list[list[float]]:
        outside = [t for t in texts if t not in allowed]
        if outside:
            raise RefusedEmbedding(
                f"refusing to embed {len(outside)} text(s) outside the "
                f"confirmation set, e.g. {outside[0]!r}"
            )
        return embed(texts)

    return guarded


def timed_embed(
    embed: Callable[[list[str]], list[list[float]]],
    spent: list[float],
    *,
    budget_s: float,
    clock: Callable[[], float] = time.monotonic,
) -> Callable[[list[str]], list[list[float]]]:
    """Charge each embedding call's wall time to `spent[0]`; past `budget_s`
    stop (nothing is read from a stopped run)."""

    def timed(texts: list[str]) -> list[list[float]]:
        t0 = clock()
        out = embed(texts)
        spent[0] += clock() - t0
        if spent[0] > budget_s:
            raise StopLine(f"embedding budget: {spent[0]:.0f} s > {budget_s:.0f} s")
        return out

    return timed


def count_missing(store: Any, texts: Sequence[str]) -> int:
    """Unique texts with no cached vector; computes nothing."""
    return sum(
        1
        for t in dict.fromkeys(texts)
        if store.db.execute(
            "SELECT 1 FROM emb WHERE key=?", (store._key(t),)
        ).fetchone()
        is None
    )


# --- pure: bootstrap and gates -------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Bounds:
    """A statistic with its one-sided 95% bounds (5th and 95th percentiles)."""

    point: float
    lower: float
    upper: float

    def fmt(self) -> str:
        return f"{self.point:+.4f} [lower {self.lower:+.4f}, upper {self.upper:+.4f}]"


def bootstrap_draws(
    clusters: Mapping[str, Sequence[Any]],
    statistic: Callable[[Sequence[Any]], float],
    *,
    resamples: int = sm.BOOTSTRAP_RESAMPLES,
    seed: int = sm.BOOTSTRAP_SEED,
) -> tuple[float, list[float]]:
    """The full-sample statistic and its bootstrap draws.

    Draws use the screen's two-stage scheme (`screen_metrics.two_stage_bootstrap`:
    meetings with replacement, then items within each drawn meeting) with the
    same generator calls in the same order, so a self-test can pin the two to
    the same percentiles. It exists because that function returns only the
    two-sided 2.5/97.5 interval and the registration needs one-sided bounds."""
    keys = sorted(k for k, items in clusters.items() if items)
    if not keys:
        raise ValueError("no items to bootstrap")
    point = statistic([item for k in keys for item in clusters[k]])
    rng = random.Random(seed)  # noqa: S311 -- statistical resampling, not security
    draws: list[float] = []
    for _ in range(resamples):
        sample: list[Any] = []
        for _m in range(len(keys)):
            items = clusters[keys[rng.randrange(len(keys))]]
            sample.extend(items[rng.randrange(len(items))] for _ in range(len(items)))
        draws.append(statistic(sample))
    return point, draws


def bootstrap_bounds(
    clusters: Mapping[str, Sequence[Any]],
    statistic: Callable[[Sequence[Any]], float],
    *,
    resamples: int = sm.BOOTSTRAP_RESAMPLES,
    seed: int = sm.BOOTSTRAP_SEED,
) -> Bounds:
    point, draws = bootstrap_draws(clusters, statistic, resamples=resamples, seed=seed)
    return Bounds(
        point,
        sm._percentile(draws, ONE_SIDED),
        sm._percentile(draws, 1 - ONE_SIDED),
    )


def status_noninferior(b: Bounds, margin: float) -> str:
    """Higher is better. `non_inferior` when the lower bound is above the
    margin, `inferior` when the upper bound is below it, else `unresolved`."""
    if b.lower > margin:
        return NI
    if b.upper < margin:
        return INF
    return UNR


def status_increase(b: Bounds, margin: float) -> str:
    """Lower is better (S1 clutter). `non_inferior` when the upper bound is
    below the margin, `inferior` when the lower bound is above it."""
    if b.upper < margin:
        return NI
    if b.lower > margin:
        return INF
    return UNR


# --- pure: cost ----------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class CostCounts:
    objects_backstop: int
    objects_judge: int
    embed_backstop: int | None
    embed_judge: int | None
    rows25_backstop: int
    rows25_judge: int
    rows_ws_backstop: int
    rows_ws_judge: int
    produced_ws_max: int
    """The largest identity-group count produced by any workspace, either side."""


def _ratio_check(num: int | None, den: int | None, budget: float) -> dict[str, Any]:
    r = dc.ratio(num, den)
    return {
        "value": r,
        "budget": budget,
        "ok": None if r is None else r <= budget + 1e-12,
    }


def c1_check(c: CostCounts) -> dict[str, Any]:
    """C1 against the registered budget. `ok` is False if any count is over
    budget (that decides even when another is unmeasured), None if none is
    over but one is unmeasured, True otherwise."""
    checks: dict[str, dict[str, Any]] = {
        "objects": _ratio_check(c.objects_backstop, c.objects_judge, OBJECTS_RATIO_MAX),
        "embeddings": _ratio_check(c.embed_backstop, c.embed_judge, OBJECTS_RATIO_MAX),
    }
    extra25 = c.rows25_backstop - c.rows25_judge
    extra_ws = c.rows_ws_backstop - c.rows_ws_judge
    checks["rows25"] = {
        "value": extra25,
        "budget": ROWS_PER_25_MAX,
        "ok": extra25 <= ROWS_PER_25_MAX,
    }
    checks["rows_ws"] = {
        "value": extra_ws,
        "budget": ROWS_PER_WORKSPACE_MAX,
        "ok": extra_ws <= ROWS_PER_WORKSPACE_MAX,
    }
    checks["cap"] = {
        "value": c.produced_ws_max,
        "budget": ID_CAP,
        "ok": c.produced_ws_max <= ID_CAP,
    }
    oks = [v["ok"] for v in checks.values()]
    ok: bool | None
    if any(x is False for x in oks):
        ok = False
    elif any(x is None for x in oks):
        ok = None
    else:
        ok = True
    return {"ok": ok, "checks": checks}


# --- pure: decision ------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Decision:
    verdict: str
    reasons: tuple[str, ...]


def decide(
    *,
    v1_ok: bool,
    degraded_run: bool,
    p1: Mapping[str, str],
    p2: Mapping[str, str],
    c1: Mapping[str, bool | None],
    confirm_points: Mapping[str, float] | None,
    s1: Mapping[str, str] | None = None,
    s1_gating: bool = False,
) -> Decision:
    """The registered decision rule. A V1 failure (void) or a degraded run
    decides nothing; otherwise NOT if any gate is inferior or over budget in
    either arm, ADOPT-CANDIDATE if every gate holds in both arms and the
    confirmation ran with its lift point not below the margin, else
    INCONCLUSIVE."""
    if not v1_ok:
        return Decision("void", ("a V1 validity check failed",))
    if degraded_run:
        return Decision("degraded", ("too many windows unlabelled",))
    gates: list[tuple[str, Mapping[str, str]]] = [("P1", p1), ("P2", p2)]
    if s1_gating:
        gates.append(("S1", s1 or {}))
    bad: list[str] = []
    unmet: list[str] = []
    for name, by_arm in gates:
        for arm in ARMS:
            status = by_arm.get(arm, UNR)
            if status == INF:
                bad.append(f"{name} inferior in arm {arm}")
            elif status != NI:
                unmet.append(f"{name} {status} in arm {arm}")
    for arm in ARMS:
        ok = c1.get(arm)
        if ok is False:
            bad.append(f"C1 over budget in arm {arm}")
        elif ok is None:
            unmet.append(f"C1 unmeasured in arm {arm}")
    if bad:
        return Decision("NOT", tuple(bad))
    if confirm_points is None:
        unmet.append("the confirmation set did not run")
    else:
        for arm in ARMS:
            point = confirm_points.get(arm)
            if point is None or point < P1_MARGIN:
                unmet.append(f"confirmation lift {point} below {P1_MARGIN} in {arm}")
    if unmet:
        return Decision("INCONCLUSIVE", tuple(unmet))
    return Decision("ADOPT-CANDIDATE", ("every registered gate holds in both arms",))


# --- pure: pairing -------------------------------------------------------------


def paired_item(
    labels: Mapping[int, str], kept: Sequence[bool], good: frozenset[str]
) -> Item:
    """(backstop-only hits, labelled, judge-kept hits, labelled) for one window.

    `labels` maps a ceiling index to its label; `kept` flags the judge's kept
    objects by ceiling index. Objects at index `BACKSTOP` or beyond are
    positional cuts: they leave BOTH sides, even where the judge kept them, so
    the judge's set stays nested in the backstop-only set."""
    bh = bn = jh = jn = 0
    for index, label in labels.items():
        if index >= BACKSTOP:
            continue
        bn += 1
        bh += label in good
        if kept[index]:
            jn += 1
            jh += label in good
    return bh, bn, jh, jn


def positional_cut(ceiling: Sequence[Mapping[str, Any]]) -> tuple[int, int, int]:
    """(windowed objects the backstop cuts, windowed objects, all objects cut)
    for one record: the `k of N` the registration asks to be printed."""
    cut = ceiling[BACKSTOP:]
    k = sum(1 for o in cut if o.get("window") is not None)
    n = sum(1 for o in ceiling if o.get("window") is not None)
    return k, n, len(cut)


def window_means(
    per_record: Sequence[tuple[str, Mapping[str, Mapping[int, float]]]], variant: str
) -> dict[str, dict[int, float]]:
    """meeting -> window -> lift averaged over the records that score it (the
    quantity `drop_ablation.variant_effect` pairs)."""
    runs: dict[str, dict[int, list[float]]] = {}
    for meeting, variants in per_record:
        for window, value in variants[variant].items():
            runs.setdefault(meeting, {}).setdefault(window, []).append(value)
    return {m: {w: sm.mean(v) for w, v in ws.items()} for m, ws in runs.items()}


def paired_clusters(
    per_record: Sequence[tuple[str, Mapping[str, Mapping[int, float]]]],
    cand: str,
    base: str,
) -> dict[str, list[float]]:
    """meeting -> per-window `cand` minus `base`, paired on the window."""
    c = window_means(per_record, cand)
    b = window_means(per_record, base)
    return {
        m: [c[m][w] - v for w, v in ws.items() if w in c.get(m, {})]
        for m, ws in b.items()
    }


def delta_share(items: Sequence[Item]) -> float:
    """Candidate pooled share minus baseline pooled share."""
    return sm.pooled_precision_delta(items)


# --- pure: validity ------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class ReplayRow:
    label: str
    primary: bool
    ceiling: list[Obj]
    judge: list[Obj]
    final: list[Obj]
    valid: bool


def replay_problems(
    rows: Sequence[ReplayRow], *, expect: tuple[int, int, int, int] | None
) -> list[str]:
    """V1.1. Every stored replay must be valid, and the production backstop
    applied to its `judge` stage must give its recorded `final` (when the judge
    kept everything that is exactly the backstop-only set). Over the primary
    rows, `expect` is (records, ceiling objects, objects cut, sources cut)."""
    problems: list[str] = []
    for row in rows:
        if not row.valid:
            problems.append(f"{row.label}: stored replay invalid")
        if not js.same_objects(row.final, row.judge[:BACKSTOP]):
            problems.append(
                f"{row.label}: backstop on the judge stage != recorded final"
            )
    if expect is not None:
        primary = [r for r in rows if r.primary]
        cuts = [max(0, len(r.ceiling) - BACKSTOP) for r in primary]
        got = (
            len(primary),
            sum(len(r.ceiling) for r in primary),
            sum(cuts),
            sum(1 for c in cuts if c > 0),
        )
        names = ("records", "ceiling objects", "objects cut", "sources cut")
        for name, want, have in zip(names, expect, got, strict=True):
            if want != have:
                problems.append(f"{name}: registered {want}, replayed {have}")
    return problems


def files_digest(root: pathlib.Path) -> str:
    """sha256 over every file under `root`: sorted relative names and bytes."""
    h = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        h.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0")
        h.update(path.read_bytes() + b"\0")
    return h.hexdigest()


# --- pure: confirmation --------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class LabelItem:
    model: str
    meeting: str
    run: int
    window: int
    indices: list[int]
    entries: list[str]


def restored_by_window(
    ceiling: Sequence[Mapping[str, Any]], kept: Sequence[Sequence[bool]]
) -> dict[int, list[int]]:
    """window -> ceiling indices of the restored objects: windowed, inside the
    backstop, and cut by at least one judge arm (`kept` holds one flag list per
    arm)."""
    out: dict[int, list[int]] = {}
    for i, obj in enumerate(ceiling[:BACKSTOP]):
        if obj.get("window") is None:
            continue
        if any(not flags[i] for flags in kept):
            out.setdefault(int(obj["window"]), []).append(i)
    return out


def stop_line(walls: Sequence[float]) -> bool:
    """The registered stop: the first `STOP_AFTER_CALLS` label calls average
    more than `STOP_MEAN_WALL_S`."""
    first = list(walls[:STOP_AFTER_CALLS])
    return len(first) >= STOP_AFTER_CALLS and sm.mean(first) > STOP_MEAN_WALL_S


def budget_exceeded(model_time_s: float) -> bool:
    return model_time_s > GPU_BUDGET_S


def _bounds(d: Mapping[str, float]) -> Bounds:
    return Bounds(d["point"], d["lower"], d["upper"])


def arm_statuses(summary: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    """Gate status per arm for P1, P2 and S1 from stored bounds."""
    out: dict[str, dict[str, str]] = {"p1": {}, "p2": {}, "s1": {}}
    for arm in ARMS:
        out["p1"][arm] = status_noninferior(_bounds(summary["p1"][arm]), P1_MARGIN)
        out["p2"][arm] = status_noninferior(_bounds(summary["p2"][arm]), P2_MARGIN)
        out["s1"][arm] = status_increase(_bounds(summary["s1"][arm]), S1_MARGIN)
    return out


def run_restored_labels(
    items: Sequence[LabelItem],
    texts: Mapping[str, Sequence[str]],
    send: Send,
    out_dir: pathlib.Path,
    log: Callable[[str], None],
    *,
    budget_s: float | None = None,
    spent_s: float = 0.0,
) -> int:
    """One G1 call per window over the restored objects only (the step 1
    protocol: `gpt-oss:20b`, seed 0, one retry). Resumable: a stored label file
    is skipped and a cached reply is not re-asked. The stop line is checked
    after every call, and so is the model-time budget (`spent_s` already used
    plus these calls against `budget_s`); when either fires nothing is stored,
    so no partial result can be read. Returns the number of calls issued."""
    import run_concept_screen as rcs

    cache = sp.ChatCache(out_dir / "cache" / "judge")
    path = out_dir / "restored" / "labels.json"
    stored: dict[str, Any] = {}
    if path.is_file():
        stored = json.loads(path.read_text(encoding="utf-8"))
    walls: list[float] = []
    calls = 0
    fresh: dict[str, Any] = {}
    for item in items:
        key = json.dumps([item.model, item.meeting, item.run, item.window])
        if key in stored:
            continue
        labels, wall = rcs.judge_call(
            cache,
            None,
            sj.g1_messages(texts[item.meeting][item.window], item.entries),
            len(item.entries),
            sj.G1_LABELS,
            send=send,
        )
        calls += 1
        walls.append(wall)
        fresh[key] = {"indices": item.indices, "labels": labels, "wall_s": wall}
        if stop_line(walls):
            raise StopLine(
                f"stop line: first {STOP_AFTER_CALLS} label calls averaged "
                f"{sm.mean(walls[:STOP_AFTER_CALLS]):.1f} s (> {STOP_MEAN_WALL_S} s)"
            )
        if budget_s is not None and spent_s + sum(walls) > budget_s:
            raise StopLine(
                f"budget: {spent_s + sum(walls):.0f} s of model time exceeds "
                f"{budget_s:.0f} s"
            )
        if calls % 10 == 0:
            log(f"{time.strftime('%H:%M:%S')} {calls} label calls")
    if fresh:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({**stored, **fresh}, ensure_ascii=False) + "\n")
        tmp.replace(path)
    return calls


# --- records -------------------------------------------------------------------


def _load(path: pathlib.Path) -> dict[str, Any]:
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise SystemExit(f"{path}: not an object")
    return loaded


def _screen() -> Any:
    return js._screen()


def _write_json(path: pathlib.Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


@dataclasses.dataclass
class PrimaryRec:
    model: str
    meeting: str
    run: int
    ceiling: list[Obj]
    kept: dict[str, list[bool] | None]
    """arm -> kept flag per ceiling object; None when the arm's record does not
    share this ceiling (the record is then skipped for that arm, and counted)."""
    finals: dict[str, list[Obj] | None]


def load_primary(study_dir: pathlib.Path) -> list[PrimaryRec]:
    """The 25 primary meeting-runs (round 1, run 1, pool DA, five extractors).
    `gemma4:26b-a4b` judges itself and is the fixed judge: one sample."""
    out: list[PrimaryRec] = []
    for model, runs in js.PRIMARY:
        for meeting in sd.POOL_DA:
            for run in runs:
                base = _load(js.replay_path(study_dir, "self", model, meeting, run, 0))
                ceiling = base["stages"]["ceiling"] or []
                kept: dict[str, list[bool] | None] = {}
                finals: dict[str, list[Obj] | None] = {}
                for arm in ARMS:
                    judge_arm = "fixed" if model == js.JUDGE_MODEL else arm
                    rec = _load(
                        js.replay_path(study_dir, judge_arm, model, meeting, run, 0)
                    )
                    if js.same_objects(rec["stages"]["ceiling"], ceiling):
                        kept[arm] = cp.partition(ceiling, rec["stages"]["judge"] or [])
                        finals[arm] = rec["stages"]["final"] or []
                    else:
                        kept[arm] = None
                        finals[arm] = None
                out.append(PrimaryRec(model, meeting, run, ceiling, kept, finals))
    return out


def load_replay_rows(study_dir: pathlib.Path) -> list[ReplayRow]:
    primary = {(m, r) for m, runs in js.PRIMARY for r in runs}
    rows: list[ReplayRow] = []
    for model, run in js._unique_plan():
        for meeting in sd.POOL_DA:
            rec = _load(js.replay_path(study_dir, "self", model, meeting, run, 0))
            st = rec["stages"]
            rows.append(
                ReplayRow(
                    f"{model} {meeting} r{run}",
                    (model, run) in primary,
                    st["ceiling"] or [],
                    st["judge"] or [],
                    st["final"] or [],
                    bool(rec.get("valid"))
                    and bool(rec.get("ceiling_matches_recorded")),
                )
            )
    return rows


def population_summary(study_dir: pathlib.Path) -> dict[str, Any]:
    recs = ls.load_recs(
        study_dir,
        [(m, r) for m, runs in js.PRIMARY for r in runs],
        sd.POOL_DA,
        arms=False,
    )
    keys = ls.population_keys(recs)
    problem = ls.check_pins(keys, ls.EXPECTED_N, ls.EXPECTED_SHA, "population")
    ceiling = sum(len(r.ceiling) for r in recs)
    return {
        "records": len(recs),
        "ceiling_objects": ceiling,
        "windowed_objects": len(keys),
        "sha256": ls.population_hash(keys),
        "problems": [p for p in [problem] if p]
        + (
            []
            if (len(recs), ceiling) == (EXPECTED_RECORDS, EXPECTED_CEILING_OBJECTS)
            else [f"records/ceiling objects {(len(recs), ceiling)}"]
        ),
    }


# --- commands: population, replay ----------------------------------------------


def cmd_population(args: argparse.Namespace) -> int:
    try:
        refuse_meetings([*sd.POOL_DA, *CONFIRM_MEETINGS, *(args.meetings or [])])
    except RefusedMeeting as err:
        raise SystemExit(str(err)) from err
    pop = population_summary(args.study_dir)
    print(
        f"primary: {pop['records']} meeting-runs, {pop['ceiling_objects']} ceiling "
        f"objects, {pop['windowed_objects']} carrying a window"
    )
    print(f"primary sha256: {pop['sha256']}")
    print(f"primary pool: {', '.join(sd.POOL_DA)}")
    print(
        f"confirmation: {CONFIRM_MODEL} runs {CONFIRM_RUNS}: {', '.join(CONFIRM_MEETINGS)}"
    )
    print(f"refused by name: {', '.join(INELIGIBLE_SERIES)} (epic #1556)")
    for problem in pop["problems"]:
        print(f"PIN MISMATCH {problem}")
    return 1 if pop["problems"] else 0


def cmd_replay(args: argparse.Namespace) -> int:
    rows = load_replay_rows(args.study_dir)
    problems = replay_problems(
        rows,
        expect=(
            EXPECTED_RECORDS,
            EXPECTED_CEILING_OBJECTS,
            EXPECTED_BACKSTOP_CUT,
            EXPECTED_CUT_SOURCES,
        ),
    )
    primary = [r for r in rows if r.primary]
    cuts = [max(0, len(r.ceiling) - BACKSTOP) for r in primary]
    print(f"records replayed: {len(rows)} (registered {EXPECTED_PLAN_RECORDS})")
    print(
        f"backstop {BACKSTOP} on the ceiling stage: {sum(cuts)} of "
        f"{sum(len(r.ceiling) for r in primary)} objects cut, in "
        f"{sum(1 for c in cuts if c)} of {len(primary)} sources"
    )
    matched = sum(1 for r in rows if js.same_objects(r.final, r.judge[:BACKSTOP]))
    print(f"recorded final == backstop(judge stage): {matched} of {len(rows)}")
    if len(rows) != EXPECTED_PLAN_RECORDS:
        problems.append(
            f"plan has {len(rows)} records, registered {EXPECTED_PLAN_RECORDS}"
        )
    for problem in problems:
        print(f"V1.1 FAIL {problem}")
    return 1 if problems else 0


# --- commands: cost ------------------------------------------------------------


def compute_cost(study_dir: pathlib.Path) -> dict[str, Any]:
    """C1 for each judge arm, from `downstream_cost.py` over the same sets."""
    out: dict[str, Any] = {}
    for arm in ARMS:
        data = dc.collect(study_dir, arm)
        total_j, total_a = dc.Measure(), dc.Measure()
        for _model, _meeting, both in data["per_record"]:
            total_j = dc.add(total_j, both[dc.JUDGE])
            total_a = dc.add(total_a, both[dc.ALONE])
        ws_j, ws_a = dc.Measure(), dc.Measure()
        produced = 0
        for both in data["workspaces"].values():
            ws_j = dc.add(ws_j, both[dc.JUDGE])
            ws_a = dc.add(ws_a, both[dc.ALONE])
            produced = max(
                produced,
                both[dc.JUDGE].identity_produced,
                both[dc.ALONE].identity_produced,
            )
        counts = CostCounts(
            total_a.objects,
            total_j.objects,
            total_a.embed_calls,
            total_j.embed_calls,
            total_a.identity_retained,
            total_j.identity_retained,
            ws_a.identity_retained,
            ws_j.identity_retained,
            produced,
        )
        out[arm] = {
            "counts": dataclasses.asdict(counts),
            "c1": c1_check(counts),
            "revision_pool_reported_only": {
                "backstop_only": ws_a.revision_pool,
                "judge_kept": ws_j.revision_pool,
                "ratio": dc.ratio(ws_a.revision_pool, ws_j.revision_pool),
            },
        }
    return out


def render_cost(cost: Mapping[str, Any]) -> str:
    lines = ["\n## C1, backstop-only vs judge-kept (deterministic counts)\n"]
    for arm in ARMS:
        row = cost[arm]
        lines.append(f"arm {arm}: within budget: {row['c1']['ok']}")
        for name, chk in row["c1"]["checks"].items():
            value = chk["value"]
            shown = (
                "n/a"
                if value is None
                else f"{value:.3f}"
                if isinstance(value, float)
                else str(value)
            )
            lines.append(f"  {name}: {shown} (budget {chk['budget']}) ok={chk['ok']}")
        c = row["counts"]
        lines.append(
            f"  objects {c['objects_backstop']} vs {c['objects_judge']}; identity rows "
            f"per 25 sources {c['rows25_backstop']} vs {c['rows25_judge']}; per "
            f"workspace {c['rows_ws_backstop']} vs {c['rows_ws_judge']}"
        )
        rp = row["revision_pool_reported_only"]
        lines.append(
            f"  revision pool (reported only): {rp['backstop_only']} vs {rp['judge_kept']}"
        )
    return "\n".join(lines)


def cmd_cost(args: argparse.Namespace) -> int:
    cost = compute_cost(args.study_dir)
    _write_json(args.out_dir / "scores" / "cost.json", cost)
    print(render_cost(cost))
    return 0


# --- commands: score -----------------------------------------------------------


def _label_rows(path: pathlib.Path, key: str) -> list[dict[str, Any]]:
    rows = _load(path)[key]
    if not isinstance(rows, list):
        raise SystemExit(f"{path}: {key} is not a list")
    return rows


def gather_items(
    recs: Sequence[PrimaryRec],
    path_of: Callable[[PrimaryRec], pathlib.Path],
    arm: str,
    good: frozenset[str],
) -> tuple[dict[str, list[Item]], dict[str, Any]]:
    """Per-meeting paired window items for one arm, plus coverage counts."""
    clusters: dict[str, list[Item]] = {}
    counts: dict[str, Any] = {
        "windows": 0,
        "windows_unlabelled": 0,
        "records_skipped": 0,
        "missing_files": [],
    }
    for rec in recs:
        kept = rec.kept[arm]
        if kept is None:
            counts["records_skipped"] += 1
            continue
        path = path_of(rec)
        if not path.is_file():
            counts["missing_files"].append(str(path))
            continue
        for row in _label_rows(path, "windows"):
            counts["windows"] += 1
            if row["labels"] is None:
                counts["windows_unlabelled"] += 1
                continue
            labels = dict(zip(row["indices"], row["labels"], strict=True))
            item = paired_item(labels, kept, good)
            if item[1]:
                clusters.setdefault(rec.meeting, []).append(item)
    return clusters, counts


def _share(hit: int, n: int) -> float | None:
    return hit / n if n else None


def summarise_items(clusters: Mapping[str, list[Item]]) -> dict[str, Any]:
    flat = [i for items in clusters.values() for i in items]
    bounds = bootstrap_bounds({k: v for k, v in clusters.items() if v}, delta_share)
    two = sm.two_stage_bootstrap({k: v for k, v in clusters.items() if v}, delta_share)
    bh, bn = sum(i[0] for i in flat), sum(i[1] for i in flat)
    jh, jn = sum(i[2] for i in flat), sum(i[3] for i in flat)
    return {
        **dataclasses.asdict(bounds),
        "two_sided_low": two.low,
        "two_sided_high": two.high,
        "backstop_only": {"hits": bh, "labelled": bn, "share": _share(bh, bn)},
        "judge": {"hits": jh, "labelled": jn, "share": _share(jh, jn)},
        "windows": len(flat),
    }


def per_type_counts(recs: Sequence[PrimaryRec]) -> dict[str, Any]:
    """Descriptive: restored objects (backstop-only minus judge-kept) by type."""
    out: dict[str, Any] = {}
    for arm in ARMS:
        restored: collections.Counter[str] = collections.Counter()
        total: collections.Counter[str] = collections.Counter()
        for rec in recs:
            kept = rec.kept[arm]
            if kept is None:
                continue
            for i, obj in enumerate(rec.ceiling):
                total[str(obj["type"])] += 1
                if i < BACKSTOP and not kept[i]:
                    restored[str(obj["type"])] += 1
        out[arm] = {
            t: {"restored": restored[t], "ceiling": total[t]}
            for t in sorted(set(total) | set(PER_TYPE))
        }
    return out


def p1_summary(
    rcs: Any, corpus: Any, store: Any, recs: Sequence[PrimaryRec]
) -> dict[str, Any]:
    """Paired per-window lift, backstop-only minus each judge arm. Raises
    `ab.EmbeddingCacheMiss` if any text is missing from the cache."""

    def variants(rec: PrimaryRec, arm: str) -> dict[str, list[Obj]] | None:
        final = rec.finals[arm]
        if final is None:
            return None
        return {
            "backstop": rec.ceiling[:BACKSTOP],
            "judge": final,
            "none": rec.ceiling,
        }

    texts: list[str] = []
    for rec in recs:
        _t, _w, gold = corpus.meeting(rec.meeting)
        texts += [g.text for g in gold.sentences]
        texts += [
            g.text for g in corpus.gold_only(foreign_control(rec.meeting)).sentences
        ]
        for arm in ARMS:
            v = variants(rec, arm)
            if v is not None:
                texts += [rcs.unit_text(o) for objs in v.values() for o in objs]
    vec = store.vectors(texts)
    out: dict[str, Any] = {}
    for arm in ARMS:
        per_record: list[tuple[str, dict[str, dict[int, float]]]] = []
        skipped = 0
        for rec in recs:
            v = variants(rec, arm)
            if v is None:
                skipped += 1
                continue
            _t, _w, gold = corpus.meeting(rec.meeting)
            fvecs = [
                vec[g.text]
                for g in corpus.gold_only(foreign_control(rec.meeting)).sentences
            ]
            per_record.append(
                (
                    rec.meeting,
                    {
                        name: rcs.window_lifts(
                            {"stages": {"x": objs}}, gold, fvecs, vec, "x"
                        )
                        for name, objs in v.items()
                    },
                )
            )
        clusters = paired_clusters(per_record, "backstop", "judge")
        live = {k: v for k, v in clusters.items() if v}
        bounds = bootstrap_bounds(live, sm.mean)
        two = sm.two_stage_bootstrap(live, sm.mean)
        out[arm] = {
            **dataclasses.asdict(bounds),
            "two_sided_low": two.low,
            "two_sided_high": two.high,
            "lift": {
                name: sm.mean(
                    [
                        v
                        for ws in window_means(per_record, name).values()
                        for v in ws.values()
                    ]
                )
                for name in ("backstop", "judge", "none")
            },
            "windows": sum(len(v) for v in live.values()),
            "records_skipped": skipped,
        }
    return out


def compute_score(args: argparse.Namespace) -> dict[str, Any]:
    refuse_meetings(sd.POOL_DA)
    rcs = _screen()
    corpus = rcs.Corpus(args.screen_dir)
    store = rcs.EmbedStore(
        args.screen_dir / "cache" / "embed.sqlite", ab.cache_only_embed, ab.EMBED_DIGEST
    )
    recs = load_primary(args.study_dir)
    problems = replay_problems(
        load_replay_rows(args.study_dir),
        expect=(
            EXPECTED_RECORDS,
            EXPECTED_CEILING_OBJECTS,
            EXPECTED_BACKSTOP_CUT,
            EXPECTED_CUT_SOURCES,
        ),
    )
    problems += population_summary(args.study_dir)["problems"]
    out: dict[str, Any] = {"v1_problems": problems}
    cut = [positional_cut(r.ceiling) for r in recs]
    out["positional_cut"] = {
        "windowed_cut": sum(c[0] for c in cut),
        "windowed": sum(c[1] for c in cut),
        "all_cut": sum(c[2] for c in cut),
        "all": sum(len(r.ceiling) for r in recs),
    }
    out["label_digests"] = {
        "step1_g1": files_digest(args.precision_dir / "labels")
        if (args.precision_dir / "labels").is_dir()
        else None,
        "step3_clutter": files_digest(args.label_dir / "labels")
        if (args.label_dir / "labels").is_dir()
        else None,
    }
    try:
        out["p1"] = p1_summary(rcs, corpus, store, recs)
    except ab.EmbeddingCacheMiss as err:
        out["p1"] = None
        out["v1_problems"].append(f"embedding cache miss: {err}")
    out["p2"], out["s1"] = {}, {}
    out["coverage"] = {}
    unlabelled = total = 0
    for arm in ARMS:
        c2, n2 = gather_items(
            recs,
            lambda r: cp.label_path(args.precision_dir, r.model, r.meeting, r.run),
            arm,
            GOOD_STRICT,
        )
        c3, n3 = gather_items(
            recs,
            lambda r: ls.store_path(
                args.label_dir,
                "labels",
                ls.Rec(r.model, r.meeting, r.run, r.ceiling, [], [], {}),
            ),
            arm,
            ls.CLUTTER,
        )
        for name, counts in (("step1", n2), ("step3", n3)):
            out["coverage"][f"{arm}/{name}"] = counts
            unlabelled += counts["windows_unlabelled"]
            total += counts["windows"]
            if counts["missing_files"]:
                out["v1_problems"].append(
                    f"{name} label files missing: {len(counts['missing_files'])}"
                )
        if not out["v1_problems"] or all(
            "missing" not in p for p in out["v1_problems"]
        ):
            out["p2"][arm] = summarise_items(c2)
            out["s1"][arm] = summarise_items(c3)
    out["unlabelled"] = {"windows_unlabelled": unlabelled, "windows": total}
    out["per_type"] = per_type_counts(recs)
    return out


def load_confirmation(out_dir: pathlib.Path) -> dict[str, Any] | None:
    path = out_dir / "scores" / "confirmation.json"
    return _load(path) if path.is_file() else None


def verdict_from(
    score: Mapping[str, Any],
    cost: Mapping[str, Any],
    confirmation: Mapping[str, Any] | None,
    *,
    s1_gating: bool,
) -> Decision:
    problems = list(score["v1_problems"])
    complete = score.get("p1") is not None and set(score["p2"]) == set(ARMS)
    if problems or not complete:
        return decide(
            v1_ok=False, degraded_run=False, p1={}, p2={}, c1={}, confirm_points=None
        )
    status = arm_statuses(score)
    ran = confirmation is not None and confirmation.get("status") == "ran"
    points = (
        {arm: float(confirmation["points"][arm]) for arm in ARMS}  # type: ignore[index]
        if ran
        else None
    )
    u = score["unlabelled"]
    return decide(
        v1_ok=True,
        degraded_run=cp.degraded(u["windows_unlabelled"], u["windows"]),
        p1=status["p1"],
        p2=status["p2"],
        c1={arm: cost[arm]["c1"]["ok"] for arm in ARMS},
        confirm_points=points,
        s1=status["s1"],
        s1_gating=s1_gating,
    )


def _fmt_b(d: Mapping[str, Any]) -> str:
    return (
        f"{d['point']:+.4f} [lower {d['lower']:+.4f}, upper {d['upper']:+.4f}] "
        f"(two-sided 95%: {d['two_sided_low']:+.4f}, {d['two_sided_high']:+.4f})"
    )


def render_score(score: Mapping[str, Any]) -> str:
    lines = ["\n## V1\n", f"problems: {score['v1_problems'] or 'none'}"]
    pc = score["positional_cut"]
    lines.append(
        f"positional cut: {pc['windowed_cut']} of {pc['windowed']} windowed objects "
        f"({pc['all_cut']} of {pc['all']} objects)"
    )
    lines.append(f"label digests: {score['label_digests']}")
    if score.get("p1"):
        lines += ["\n## P1, window lift, backstop-only minus judge (margin -0.005)\n"]
        for arm in ARMS:
            r = score["p1"][arm]
            lines.append(
                f"- {arm}: {_fmt_b(r)}; lift backstop-only {r['lift']['backstop']:.4f}, "
                f"judge {r['lift']['judge']:.4f}, ceiling {r['lift']['none']:.4f}; "
                f"{r['windows']} windows; skipped {r['records_skipped']}"
            )
    for key, title in (
        ("p2", "P2, strict G1 precision (margin -0.05)"),
        ("s1", "S1, clutter share (non-gating; +0.05 if gating)"),
    ):
        if score.get(key):
            lines += [f"\n## {title}\n"]
            for arm in ARMS:
                r = score[key][arm]
                lines.append(
                    f"- {arm}: {_fmt_b(r)}; backstop-only {r['backstop_only']}; "
                    f"judge {r['judge']}; {r['windows']} windows"
                )
    lines += ["\n## Coverage\n"]
    for key, c in score["coverage"].items():
        lines.append(
            f"- {key}: windows labelled {c['windows'] - c['windows_unlabelled']} of "
            f"{c['windows']}; records skipped {c['records_skipped']}"
        )
    lines += ["\n## Restored objects by type (descriptive)\n"]
    for arm in ARMS:
        row = ", ".join(
            f"{t} {v['restored']} of {v['ceiling']}"
            for t, v in score["per_type"][arm].items()
            if t in PER_TYPE
        )
        lines.append(f"- {arm}: {row}")
    return "\n".join(lines)


def render_decision(d: Decision, confirmation: Mapping[str, Any] | None) -> str:
    conf = "not run" if confirmation is None else confirmation.get("status")
    return f"\nconfirmation: {conf}\nVERDICT: {d.verdict}; {'; '.join(d.reasons)}"


def cmd_score(args: argparse.Namespace) -> int:
    score = compute_score(args)
    _write_json(args.out_dir / "scores" / "score.json", score)
    print(render_score(score))
    cost_path = args.out_dir / "scores" / "cost.json"
    cost = _load(cost_path) if cost_path.is_file() else compute_cost(args.study_dir)
    _write_json(cost_path, cost)
    print(render_cost(cost))
    confirmation = load_confirmation(args.out_dir)
    d = verdict_from(score, cost, confirmation, s1_gating=args.s1_gating)
    print(render_decision(d, confirmation))
    return 0


def cmd_decide(args: argparse.Namespace) -> int:
    scores = args.out_dir / "scores"
    for name in ("score.json", "cost.json"):
        if not (scores / name).is_file():
            raise SystemExit(f"{scores / name} missing: run `score` first")
    confirmation = load_confirmation(args.out_dir)
    d = verdict_from(
        _load(scores / "score.json"),
        _load(scores / "cost.json"),
        confirmation,
        s1_gating=args.s1_gating,
    )
    print(render_decision(d, confirmation))
    return 0


# --- commands: confirm ---------------------------------------------------------


@dataclasses.dataclass
class ConfRec:
    meeting: str
    run: int
    record: dict[str, Any]
    fixed: dict[str, Any] | None


def load_confirmation_records(
    screen_dir: pathlib.Path, out_dir: pathlib.Path, meetings: Sequence[str]
) -> tuple[list[ConfRec], list[str]]:
    """The stored #1557 records of the confirmation set, plus any missing."""
    rcs = _screen()
    recs: list[ConfRec] = []
    missing: list[str] = []
    for meeting in meetings:
        for run in CONFIRM_RUNS:
            record = rcs.load_record(screen_dir, CONFIRM_MODEL, meeting, run)
            if record is None:
                missing.append(f"{CONFIRM_MODEL} {meeting} r{run}")
                continue
            fpath = js.replay_path(out_dir, "fixed", CONFIRM_MODEL, meeting, run, 0)
            recs.append(
                ConfRec(meeting, run, record, _load(fpath) if fpath.is_file() else None)
            )
    return recs, missing


def _conf_variants(rec: ConfRec, arm: str) -> dict[str, list[Obj]] | None:
    st = rec.record["stages"]
    ceiling = st["ceiling"] or []
    if arm == "self":
        final = st["final"] or []
    elif rec.fixed is not None and rec.fixed.get("ceiling_matches_recorded"):
        final = rec.fixed["stages"]["final"] or []
    else:
        return None
    return {"backstop": ceiling[:BACKSTOP], "judge": final}


def confirmation_texts(corpus: Any, recs: Sequence[ConfRec]) -> list[str]:
    """Every text the confirmation scoring embeds: gold, the amended null
    control's gold, and the ceiling and final objects."""
    rcs = _screen()
    texts: list[str] = []
    for meeting in dict.fromkeys(rec.meeting for rec in recs):
        texts += [g.text for g in corpus.gold_only(foreign_control(meeting)).sentences]
    for rec in recs:
        _t, _w, gold = corpus.meeting(rec.meeting)
        texts += [g.text for g in gold.sentences]
        for stage in ("ceiling", "final"):
            texts += [rcs.unit_text(o) for o in rec.record["stages"][stage] or []]
    return texts


def preflight(
    screen_dir: pathlib.Path, out_dir: pathlib.Path, meetings: Sequence[str]
) -> dict[str, Any]:
    """Model-free feasibility: records present, embeddings cached (zero misses)."""
    rcs = _screen()
    result: dict[str, Any] = {"ok": False, "reasons": []}
    recs, missing = load_confirmation_records(screen_dir, out_dir, meetings)
    if missing:
        result["reasons"].append(f"missing stored records: {missing}")
        return result
    corpus = rcs.Corpus(screen_dir)
    store = rcs.EmbedStore(
        screen_dir / "cache" / "embed.sqlite", ab.cache_only_embed, ab.EMBED_DIGEST
    )
    for meeting in dict.fromkeys(rec.meeting for rec in recs):
        try:
            foreign_control(meeting)
        except RefusedMeeting as err:
            result["reasons"].append(f"null control of {meeting}: {err}")
    if result["reasons"]:
        return result
    texts = confirmation_texts(corpus, recs)
    result["embeddings_missing"] = count_missing(store, texts)
    if result["reasons"]:
        return result
    windows = sum(
        len(
            {
                o["window"]
                for o in (r.record["stages"]["ceiling"] or [])[:BACKSTOP]
                if o.get("window") is not None
            }
        )
        for r in recs
    )
    result.update(
        ok=True,
        meeting_runs=len(recs),
        fixed_replays_needed=sum(1 for r in recs if r.fixed is None),
        embed_batches=-(-result["embeddings_missing"] // EMBED_BATCH),
        label_windows_upper_bound=windows,
    )
    return result


def label_items(recs: Sequence[ConfRec]) -> list[LabelItem]:
    rcs = _screen()
    items: list[LabelItem] = []
    for rec in recs:
        ceiling = rec.record["stages"]["ceiling"] or []
        flags: list[list[bool]] = [
            cp.partition(ceiling, rec.record["stages"]["judge"] or [])
        ]
        if rec.fixed is not None and rec.fixed.get("ceiling_matches_recorded"):
            flags.append(cp.partition(ceiling, rec.fixed["stages"]["judge"] or []))
        for window, indices in sorted(restored_by_window(ceiling, flags).items()):
            items.append(
                LabelItem(
                    CONFIRM_MODEL,
                    rec.meeting,
                    rec.run,
                    window,
                    indices,
                    [rcs.unit_text(ceiling[i]) for i in indices],
                )
            )
    return items


def score_confirmation(
    screen_dir: pathlib.Path,
    out_dir: pathlib.Path,
    meetings: Sequence[str],
    model_time_s: float,
) -> dict[str, Any]:
    rcs = _screen()
    corpus = rcs.Corpus(screen_dir)
    store = rcs.EmbedStore(
        screen_dir / "cache" / "embed.sqlite", ab.cache_only_embed, ab.EMBED_DIGEST
    )
    recs, _missing = load_confirmation_records(screen_dir, out_dir, meetings)
    texts: list[str] = []
    for rec in recs:
        _t, _w, gold = corpus.meeting(rec.meeting)
        texts += [g.text for g in gold.sentences]
        texts += [
            g.text for g in corpus.gold_only(foreign_control(rec.meeting)).sentences
        ]
        for arm in ARMS:
            v = _conf_variants(rec, arm)
            if v is not None:
                texts += [rcs.unit_text(o) for objs in v.values() for o in objs]
    vec = store.vectors(texts)
    points: dict[str, float] = {}
    intervals: dict[str, Any] = {}
    excluded: list[str] = []
    for arm in ARMS:
        per_record: list[tuple[str, dict[str, dict[int, float]]]] = []
        for rec in recs:
            v = _conf_variants(rec, arm)
            if v is None:
                excluded.append(f"{rec.meeting} r{rec.run} {arm}")
                continue
            _t, _w, gold = corpus.meeting(rec.meeting)
            fvecs = [
                vec[g.text]
                for g in corpus.gold_only(foreign_control(rec.meeting)).sentences
            ]
            per_record.append(
                (
                    rec.meeting,
                    {
                        name: rcs.window_lifts(
                            {"stages": {"x": objs}}, gold, fvecs, vec, "x"
                        )
                        for name, objs in v.items()
                    },
                )
            )
        clusters = {
            k: v
            for k, v in paired_clusters(per_record, "backstop", "judge").items()
            if v
        }
        if not clusters:
            continue
        two = sm.two_stage_bootstrap(clusters, sm.mean)
        points[arm] = two.point
        intervals[arm] = {"point": two.point, "low": two.low, "high": two.high}
    labelled: dict[str, Any] = {}
    lpath = out_dir / "restored" / "labels.json"
    if lpath.is_file():
        stored = _load(lpath)
        good = n = 0
        for row in stored.values():
            if row["labels"] is None:
                continue
            n += len(row["labels"])
            good += sum(1 for x in row["labels"] if x == "supported")
        labelled = {"restored_supported": good, "restored_labelled": n}
    complete = set(points) == set(ARMS) and not excluded
    return {
        "status": "ran" if complete else "incomplete",
        "points": points,
        "intervals_descriptive": intervals,
        "restored_g1_descriptive": labelled,
        "excluded": excluded,
        "model_time_s": model_time_s,
    }


def cmd_confirm(args: argparse.Namespace) -> int:
    try:
        meetings = confirmation_meetings(args.meetings)
    except RefusedMeeting as err:
        raise SystemExit(str(err)) from err
    pre = preflight(args.screen_dir, args.out_dir, meetings)
    print(f"preflight: {pre}")
    path = args.out_dir / "scores" / "confirmation.json"
    if not pre["ok"]:
        _write_json(path, {"status": "not_run", "reasons": pre["reasons"]})
        print("confirmation NOT RUN: the verdict cannot be ADOPT-CANDIDATE")
        return 1
    if not args.run:
        print(
            f"forecast: {pre['embeddings_missing']} bge-m3 embedding text(s) in "
            f"{pre['embed_batches']} call(s) (budget {EMBED_BUDGET_S:.0f} s), "
            f"{pre['fixed_replays_needed']} gemma4:26b-a4b judge call(s), at most "
            f"{pre['label_windows_upper_bound']} gpt-oss:20b label call(s), budget "
            f"{GPU_BUDGET_S / 60:.0f} min; nothing was called (pass --run to call models)"
        )
        return 0
    return run_confirmation(args, meetings)


def write_stamp_sidecar(path: pathlib.Path, stamp: Mapping[str, Any]) -> None:
    """The stamp of a result that cannot carry one in-band (the label file's
    values are all read as records), beside it, as `harness_stamp` does."""
    import harness_stamp

    harness_stamp.write_stamp_sidecar(path, stamp)


def write_result(
    path: pathlib.Path, payload: Mapping[str, Any], stamp: Mapping[str, Any]
) -> None:
    """Store a result of a run that called a model, with its identity stamp
    in-band (`harness_stamp`): harness commit, model digest, prompt hashes."""
    if "stamp" in payload:
        raise ValueError("payload already holds a 'stamp'")
    _write_json(path, {**payload, "stamp": dict(stamp)})


def run_stamp(host: str | None) -> dict[str, Any]:
    """The stamp of a confirmation run: the G1 judge as the stamped model and
    the prompts sent, plus the fixed selector judge and the embedder identity."""
    from harness_prompts import extraction_prompts
    from harness_stamp import build_stamp, model_identity

    stamp = build_stamp(
        model=sj.JUDGE_MODEL,
        prompts={"screen/g1": sj.G1_SYSTEM, **extraction_prompts()},
        host=host,
    )
    stamp["other_models"] = [
        model_identity(js.JUDGE_MODEL, host),
        model_identity(_screen().EMBED_MODEL, host),
    ]
    return stamp


def run_confirmation(args: argparse.Namespace, meetings: Sequence[str]) -> int:
    from harness_stamp import model_identity

    rcs = _screen()
    for model, pinned in (
        (js.JUDGE_MODEL, js.JUDGE_DIGEST),
        (sj.JUDGE_MODEL, ls.JUDGE_DIGEST),
    ):
        ident = model_identity(model, args.host)
        if ident.get("digest") != pinned:
            raise SystemExit(
                f"refusing: {model} digest {ident.get('digest')} != pinned {pinned}"
            )
    path = args.out_dir / "scores" / "confirmation.json"
    stamp = run_stamp(args.host)
    corpus = rcs.Corpus(args.screen_dir)
    # Amendment 1: missing confirmation-set vectors are computed, with the
    # registration's pinned bge-m3 digest, and only for confirmation texts.
    ident = model_identity(rcs.EMBED_MODEL, args.host)
    if ident.get("digest") != ab.EMBED_DIGEST:
        raise SystemExit(
            f"refusing: {rcs.EMBED_MODEL} digest {ident.get('digest')} != "
            f"pinned {ab.EMBED_DIGEST}"
        )
    conf_recs, _gone = load_confirmation_records(
        args.screen_dir, args.out_dir, meetings
    )
    texts = confirmation_texts(corpus, conf_recs)
    client = rcs.OllamaClient(rcs.EMBED_MODEL, host=args.host, timeout=600)
    embed_spent = [0.0]
    store = rcs.EmbedStore(
        args.screen_dir / "cache" / "embed.sqlite",
        guarded_embed(
            timed_embed(client.embed, embed_spent, budget_s=EMBED_BUDGET_S),
            set(texts),
        ),
        ab.EMBED_DIGEST,
    )
    try:
        store.vectors(texts)
    except StopLine as err:
        write_result(path, {"status": "stopped", "reasons": [str(err)]}, stamp)
        print(f"STOP: {err}; no partial verdict is read")
        return 1
    print(f"embeddings: {embed_spent[0]:.0f} s", flush=True)
    timeouts = dict(rcs.load_timeouts(args.screen_dir))
    send = sp.ollama_sender(args.host)
    spent = 0.0
    warmed = False
    for meeting in meetings:
        for run in CONFIRM_RUNS:
            fpath = js.replay_path(
                args.out_dir, "fixed", CONFIRM_MODEL, meeting, run, 0
            )
            if fpath.is_file():
                spent += js._judge_wall(_load(fpath))
                continue
            if not warmed:
                print(
                    f"warm-up {js.JUDGE_MODEL}: {rcs.warm_up(js.JUDGE_MODEL, args.host):.1f} s",
                    flush=True,
                )
                warmed = True
            rec = js.replay_one(
                rcs,
                corpus,
                screen_dir=args.screen_dir,
                out_dir=args.out_dir,
                model=CONFIRM_MODEL,
                meeting=meeting,
                run=run,
                judge_model=js.JUDGE_MODEL,
                judge_digest=js.JUDGE_DIGEST,
                seed_index=0,
                send=send,
                timeouts=timeouts,
            )
            write_result(fpath, rec, stamp)
            spent += js._judge_wall(rec)
            print(
                f"{time.strftime('%H:%M:%S')} fixed judge {meeting} r{run}: {spent:.0f} s",
                flush=True,
            )
            if budget_exceeded(spent):
                write_result(
                    path,
                    {"status": "stopped", "reasons": ["GPU budget exceeded"]},
                    stamp,
                )
                print("STOP: GPU budget exceeded; no partial verdict is read")
                return 1
    recs, _missing = load_confirmation_records(args.screen_dir, args.out_dir, meetings)
    texts_of: dict[str, list[str]] = {}
    for meeting in meetings:
        _t, wins, _g = corpus.meeting(meeting)
        texts_of[meeting] = [w.text for w in wins]
    try:
        run_restored_labels(
            label_items(recs),
            texts_of,
            sp.ollama_sender(args.host),
            args.out_dir,
            lambda m: print(m, flush=True),
            budget_s=GPU_BUDGET_S,
            spent_s=spent,
        )
    except StopLine as err:
        write_result(path, {"status": "stopped", "reasons": [str(err)]}, stamp)
        print(f"STOP: {err}; no partial verdict is read")
        return 1
    lpath = args.out_dir / "restored" / "labels.json"
    if lpath.is_file():
        write_stamp_sidecar(lpath, stamp)
    label_wall = (
        sum(float(r["wall_s"]) for r in _load(lpath).values())
        if lpath.is_file()
        else 0.0
    )
    result = score_confirmation(
        args.screen_dir, args.out_dir, meetings, spent + label_wall
    )
    write_result(path, result, stamp)
    print(json.dumps(result, indent=2))
    return 0


# --- self-test -----------------------------------------------------------------


def _check(cond: bool, message: str) -> None:
    if not cond:
        raise AssertionError(message)


def _raises(exc: type[BaseException], fn: Callable[[], object], text: str) -> None:
    try:
        fn()
    except exc as err:
        _check(text in str(err), f"message {str(err)!r} lacks {text!r}")
        return
    raise AssertionError(f"{exc.__name__} not raised for {text!r}")


def _b(point: float, lower: float, upper: float) -> Bounds:
    return Bounds(point, lower, upper)


def _o(title: str, window: int | None = 0, kind: str = "Concept") -> Obj:
    return {"type": kind, "title": title, "description": "d", "window": window}


_GOOD = {"self": NI, "fixed": NI}
_OK = {"self": True, "fixed": True}
_PTS = {"self": 0.004, "fixed": 0.009}


def _decide(**over: Any) -> Decision:
    args: dict[str, Any] = {
        "v1_ok": True,
        "degraded_run": False,
        "p1": _GOOD,
        "p2": _GOOD,
        "c1": _OK,
        "confirm_points": _PTS,
    }
    args.update(over)
    return decide(**args)


def _t_registered_constants() -> None:
    _check(P1_MARGIN == -0.005 and P2_MARGIN == -0.05, "lift and precision margins")
    _check(S1_MARGIN == 0.05, "S1 margin if gating")
    _check(OBJECTS_RATIO_MAX == 1.65, "objects and embeddings ratio")
    _check(ROWS_PER_25_MAX == 12 and ROWS_PER_WORKSPACE_MAX == 25, "review rows")
    _check(ID_CAP == 50, f"identity cap {ID_CAP}")
    _check(BACKSTOP == 20 and BACKSTOP == js.concept_mod._UNION_BACKSTOP, "backstop")
    _check(sm.BOOTSTRAP_RESAMPLES == 10_000 and sm.BOOTSTRAP_SEED == 42, "bootstrap")
    sig = inspect.signature(bootstrap_bounds).parameters
    _check(sig["resamples"].default == 10_000 and sig["seed"].default == 42, "defaults")
    _check(ONE_SIDED == 0.05, "one-sided 95%")
    _check(
        CONFIRM_MEETINGS
        == ("IS1002b", "IS1002c", "IS1002d", "IS1005a", "IS1005b", "IS1005c"),
        "confirmation meetings",
    )
    _check(CONFIRM_MODEL == "qwen3:8b" and CONFIRM_RUNS == (1, 2, 3), "confirmation")
    _check(GPU_BUDGET_S == 2700.0, "45 minutes")
    _check(STOP_AFTER_CALLS == 20 and STOP_MEAN_WALL_S == 25.0, "stop line")
    _check(js.JUDGE_DIGEST.startswith("001e5dafc3c7"), "fixed judge digest pin")
    _check(ls.JUDGE_DIGEST.startswith("f38aa0c53da5"), "G1 judge digest pin")
    _check(ls.EXPECTED_N == 303 and ls.EXPECTED_SHA.startswith("76a9d42c"), "pop pin")
    _check(ARMS == ("self", "fixed"), "arms")
    _check(not any(m.startswith("TS3012") for m in CONFIRM_MEETINGS), "no TS3012")


def _t_gate_direction() -> None:
    _check(status_noninferior(_b(0.0, -0.004, 0.01), P1_MARGIN) == NI, "clears")
    _check(status_noninferior(_b(0.0, -0.006, 0.01), P1_MARGIN) == UNR, "straddles")
    _check(status_noninferior(_b(-0.02, -0.03, -0.006), P1_MARGIN) == INF, "below")
    _check(status_noninferior(_b(0, -0.005, 0.01), P1_MARGIN) == UNR, "lower strict")
    _check(status_noninferior(_b(0, -0.1, -0.005), P1_MARGIN) == UNR, "upper strict")
    # The margin direction: the same bounds must flip when the margin flips.
    _check(status_noninferior(_b(0.0, -0.004, 0.01), -P1_MARGIN) == UNR, "flipped")
    _check(status_noninferior(_b(0.0, -0.004, 0.003), -P1_MARGIN) == INF, "flipped 2")
    _check(status_noninferior(_b(-0.01, -0.06, -0.051), P2_MARGIN) == INF, "P2 below")
    _check(status_noninferior(_b(-0.01, -0.04, 0.02), P2_MARGIN) == NI, "P2 clears")
    _check(status_increase(_b(0.02, -0.01, 0.04), S1_MARGIN) == NI, "increase small")
    _check(status_increase(_b(0.06, 0.051, 0.07), S1_MARGIN) == INF, "increase big")
    _check(status_increase(_b(0.05, 0.01, 0.06), S1_MARGIN) == UNR, "increase wide")
    _check(status_increase(_b(0.0, -0.1, 0.05), S1_MARGIN) == UNR, "upper strict")


def _t_decision_rule() -> None:
    _check(_decide().verdict == "ADOPT-CANDIDATE", "everything holds")
    _check(_decide(p1={"self": NI, "fixed": INF}).verdict == "NOT", "P1 inferior")
    _check(_decide(p2={"self": INF, "fixed": NI}).verdict == "NOT", "P2 inferior")
    _check(_decide(c1={"self": True, "fixed": False}).verdict == "NOT", "over budget")
    _check(_decide(p1={"self": NI, "fixed": UNR}).verdict == "INCONCLUSIVE", "arms")
    _check(_decide(p2={"self": UNR, "fixed": UNR}).verdict == "INCONCLUSIVE", "P2")
    _check(_decide(c1={"self": True, "fixed": None}).verdict == "INCONCLUSIVE", "C1")
    _check(_decide(confirm_points=None).verdict == "INCONCLUSIVE", "not run")
    _check(
        _decide(confirm_points={"self": -0.006, "fixed": 0.01}).verdict
        == "INCONCLUSIVE",
        "confirmation clearly below the margin in one arm",
    )
    _check(
        _decide(confirm_points={"self": -0.005, "fixed": -0.005}).verdict
        == "ADOPT-CANDIDATE",
        "exactly the margin is not below it",
    )
    _check(
        _decide(p1={"self": INF, "fixed": NI}, confirm_points=None).verdict == "NOT",
        "inferior decides even without the confirmation",
    )
    _check(_decide(v1_ok=False).verdict == "void", "V1 failure")
    _check(_decide(degraded_run=True).verdict == "degraded", "degraded")
    _check(
        _decide(v1_ok=False, degraded_run=True, p1={"self": INF, "fixed": INF}).verdict
        == "void",
        "void decides nothing, whatever the gates say",
    )
    _check(
        _decide(degraded_run=True, c1={"self": False, "fixed": False}).verdict
        == "degraded",
        "degraded decides nothing",
    )
    bad = {"self": INF, "fixed": NI}
    _check(_decide(s1=bad).verdict == "ADOPT-CANDIDATE", "S1 is non-gating")
    _check(_decide(s1=bad, s1_gating=True).verdict == "NOT", "gating S1 inferior")
    _check(
        _decide(s1={"self": UNR, "fixed": NI}, s1_gating=True).verdict
        == "INCONCLUSIVE",
        "gating S1 unresolved",
    )
    _check(_decide(s1=_GOOD, s1_gating=True).verdict == "ADOPT-CANDIDATE", "S1 holds")
    _check(len(_decide(p1={"self": NI, "fixed": UNR}).reasons) >= 1, "says why")


def _t_ts3012_refusal() -> None:
    for letter in "abcd":
        meeting = f"TS3012{letter}"
        _raises(RefusedMeeting, functools.partial(refuse_meetings, [meeting]), "TS3012")
        _raises(
            RefusedMeeting,
            functools.partial(confirmation_meetings, [meeting]),
            "TS3012",
        )
    _check("TS3012a" in sd.ALLOWED_MEETINGS, "the allowlist alone would let it in")
    _check("TS3012a" in sd.POOL_F_WINDOWED, "and POOL_F_WINDOWED lists it")
    _raises(
        RefusedMeeting,
        lambda: refuse_meetings(["IS1002b", "TS3012a"]),
        "ineligible under epic #1556",
    )
    for held_out in ("ES2002a", "TS3005a", "IS1003a"):
        _raises(
            RefusedMeeting, functools.partial(refuse_meetings, [held_out]), "thesis"
        )
    _raises(RefusedMeeting, lambda: refuse_meetings(["XX9999z"]), "XX9999z")
    _raises(RefusedMeeting, lambda: refuse_meetings(["not-a-meeting"]), "not-a-meeting")
    _raises(RefusedMeeting, lambda: confirmation_meetings(["IB4003"]), "IB4003")
    _check(confirmation_meetings() == list(CONFIRM_MEETINGS), "the registered set")
    _check(confirmation_meetings(["IS1005c"]) == ["IS1005c"], "a registered subset")
    _check(
        refuse_meetings([*sd.POOL_DA, *CONFIRM_MEETINGS])
        == [*sd.POOL_DA, *CONFIRM_MEETINGS],
        "the registered sets pass",
    )


def _t_foreign_control_refusal() -> None:
    _check(sd.foreign_meeting("IS1005a") == "TS3012a", "the stock control is TS3012")
    # Amendment 1, rule 2: TS3012 never enters, and the replacement is a rule.
    want = {
        "IS1002b": "IS1005b",
        "IS1002c": "IS1005c",
        "IS1002d": "ES2015d",
        "IS1005a": "ES2015a",
        "IS1005b": "IS1002b",
        "IS1005c": "IS1002c",
    }
    for meeting, control in want.items():
        _check(
            foreign_control(meeting) == control,
            f"{meeting}: {foreign_control(meeting)}",
        )
    _check(set(want) == set(CONFIRM_MEETINGS), "every confirmation meeting is covered")
    for meeting in CONFIRM_MEETINGS:
        c = foreign_control(meeting)
        _check(not c.startswith("TS3012") and c in sd.ALLOWED_MEETINGS, "eligible")
        _check(sd.series_of(c) != sd.series_of(meeting), "a different series")
        _check(sd.series_of(c) not in sd.THESIS_EVALUATION_SERIES, "not held out")
    _raises(RefusedMeeting, functools.partial(foreign_control, "TS3012a"), "TS3012")
    _raises(RefusedMeeting, functools.partial(foreign_control, "ES2002a"), "thesis")
    _check(foreign_control("ES2015a") == "IS1004a", "primary controls are unchanged")
    for meeting in sd.POOL_DA:
        _check(foreign_control(meeting) == sd.foreign_meeting(meeting), "primary rule")


def _t_embedding_guard() -> None:
    import tempfile

    import run_concept_screen as rcs

    sent: list[str] = []

    def embed(texts: list[str]) -> list[list[float]]:
        sent.extend(texts)
        return [[1.0, 0.0] for _ in texts]

    guarded = guarded_embed(embed, {"conf a", "conf b"})
    _check(guarded(["conf a"]) == [[1.0, 0.0]], "a confirmation text embeds")
    _raises(
        RefusedEmbedding, functools.partial(guarded, ["primary text"]), "primary text"
    )
    _check(sent == ["conf a"], f"nothing else reached the model: {sent}")
    _raises(
        RefusedEmbedding,
        functools.partial(guarded, ["conf b", "primary text"]),
        "primary text",
    )
    _check(sent == ["conf a"], "a batch with one outsider embeds nothing")
    with tempfile.TemporaryDirectory() as tmp:
        store = rcs.EmbedStore(pathlib.Path(tmp) / "e.sqlite", embed, "digest")
        store.vectors(["conf a"])
        _check(
            count_missing(store, ["conf a", "conf b", "conf b"]) == 1, "unique misses"
        )
        _check(count_missing(store, ["conf a"]) == 0, "all cached")
    _check(EMBED_BUDGET_S == 300.0 and EMBED_BATCH == 32, "embedding budget pinned")
    calls = [0.0]
    timed = timed_embed(embed, calls, budget_s=1.0, clock=iter([0.0, 2.0]).__next__)
    _raises(StopLine, functools.partial(timed, ["x"]), "embedding budget")


def _t_result_is_stamped() -> None:
    import io
    import subprocess
    import tempfile

    import harness_stamp as hs

    class _Resp(io.BytesIO):
        def __enter__(self) -> _Resp:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    def opener(url: str, timeout: float = 0) -> _Resp:
        body = {"models": [{"name": "gpt-oss:20b", "digest": "d" * 64}]}
        return _Resp(json.dumps(body).encode("utf-8"))

    def runner(cmd: Sequence[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 0, stdout="c" * 40 + "\n", stderr="")

    stamp = hs.build_stamp(
        model="gpt-oss:20b",
        prompts={"screen/g1": sj.G1_SYSTEM},
        opener=opener,
        runner=runner,
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "scores" / "confirmation.json"
        write_result(path, {"status": "ran", "points": {"self": 0.01}}, stamp)
        got = hs.load_stamp(path)
        _check(got is not None, "a stored result carries its stamp")
        _check(
            got is not None and hs.stamp_problems(got) == [], "and it pins the result"
        )
        _check(
            json.loads(path.read_text(encoding="utf-8"))["status"] == "ran",
            "payload kept",
        )
        _raises(
            ValueError,
            functools.partial(write_result, path, {"stamp": {}, "status": "x"}, stamp),
            "stamp",
        )
    _check(
        "write_result(" in inspect.getsource(run_confirmation), "run stores through it"
    )
    _check(
        "_write_json(path" not in inspect.getsource(run_confirmation), "no bare store"
    )


def _t_cost_gate() -> None:
    def counts(**over: Any) -> CostCounts:
        base: dict[str, Any] = {
            "objects_backstop": 329,
            "objects_judge": 214,
            "embed_backstop": 329,
            "embed_judge": 214,
            "rows25_backstop": 30,
            "rows25_judge": 22,
            "rows_ws_backstop": 60,
            "rows_ws_judge": 41,
            "produced_ws_max": 40,
        }
        base.update(over)
        return CostCounts(**base)

    ok = c1_check(counts())
    _check(ok["ok"] is True, f"the measured self arm is within budget: {ok}")
    fixed = c1_check(counts(objects_judge=209, rows25_judge=20, rows_ws_judge=37))
    _check(fixed["ok"] is True, f"the measured fixed arm: {fixed}")
    _check(c1_check(counts(objects_backstop=330, objects_judge=200))["ok"] is True, "=")
    _check(
        c1_check(counts(objects_backstop=331, objects_judge=200))["ok"] is False, ">"
    )
    _check(c1_check(counts(embed_backstop=331, embed_judge=200))["ok"] is False, "emb")
    _check(c1_check(counts(embed_backstop=None))["ok"] is None, "unmeasured")
    _check(c1_check(counts(rows25_backstop=34))["ok"] is True, "+12 is at most +12")
    _check(c1_check(counts(rows25_backstop=35))["ok"] is False, "+13")
    _check(c1_check(counts(rows_ws_backstop=66))["ok"] is True, "+25")
    _check(c1_check(counts(rows_ws_backstop=67))["ok"] is False, "+26")
    _check(c1_check(counts(produced_ws_max=50))["ok"] is True, "cap not binding")
    _check(c1_check(counts(produced_ws_max=51))["ok"] is False, "cap binds")
    _check(
        c1_check(counts(embed_backstop=None, objects_backstop=400))["ok"] is False,
        "an over-budget count decides even when another is unmeasured",
    )
    _check(
        set(ok["checks"]) >= {"objects", "embeddings", "rows25", "rows_ws", "cap"}, ""
    )


def _t_bootstrap_determinism() -> None:
    clusters = {
        "m1": [0.01, 0.02, -0.01, 0.03],
        "m2": [0.0, 0.015],
        "m3": [0.02, 0.01, 0.005],
        "m4": [-0.005, 0.02],
    }
    a = bootstrap_bounds(clusters, sm.mean)
    b = bootstrap_bounds(clusters, sm.mean)
    _check(a == b, "same input, same seed, same bounds")
    _check(a != bootstrap_bounds(clusters, sm.mean, seed=7), "the seed matters")
    _check(
        a != bootstrap_bounds(clusters, sm.mean, resamples=500),
        "the resample count matters",
    )
    two_sided = sm.two_stage_bootstrap(clusters, sm.mean)
    point, draws = bootstrap_draws(clusters, sm.mean)
    _check(len(draws) == 10_000, f"10,000 resamples: {len(draws)}")
    _check(point == two_sided.point == a.point, "point is the full-sample statistic")
    _check(
        sm._percentile(draws, 0.025) == two_sided.low
        and sm._percentile(draws, 0.975) == two_sided.high,
        "same resampling as the screen's two-stage bootstrap",
    )
    _check(two_sided.low <= a.lower <= a.point, "5th percentile is above the 2.5th")
    _check(a.point <= a.upper <= two_sided.high, "95th is below the 97.5th")
    _check(a.lower == sm._percentile(draws, ONE_SIDED), "lower is the 5th percentile")
    _check(a.upper == sm._percentile(draws, 1 - ONE_SIDED), "upper is the 95th")
    _raises(ValueError, lambda: bootstrap_bounds({}, sm.mean), "no items")


def _t_pairing_nested() -> None:
    labels = {0: "supported", 1: "unsupported", 2: "supported", 3: "partial"}
    kept = [True, False, True, False]
    got = paired_item(labels, kept, GOOD_STRICT)
    _check(
        got == (2, 4, 2, 2),
        f"backstop side holds every object, judge side its kept: {got}",
    )
    _check(got[3] <= got[1] and got[2] <= got[0], "the judge-kept set is nested")
    # Index >= BACKSTOP is removed from BOTH sides, even when the judge kept it.
    labels = {19: "supported", 20: "supported", 21: "unsupported"}
    kept = [False] * 19 + [True] * 3
    got = paired_item(labels, kept, GOOD_STRICT)
    _check(got == (1, 1, 1, 1), f"positional cuts leave both sides: {got}")
    _check(paired_item({}, [], GOOD_STRICT) == (0, 0, 0, 0), "empty window")
    clutter = paired_item(
        {0: "drop", 1: "keep", 2: "merge"}, [False, True, False], ls.CLUTTER
    )
    _check(clutter == (2, 3, 0, 1), f"clutter hits: {clutter}")
    ceiling = [_o("a")] * 22 + [_o("w", None)] * 2
    ceiling[21] = _o("late")
    _check(positional_cut(ceiling) == (2, 22, 4), f"{positional_cut(ceiling)}")
    _check(
        delta_share([(2, 4, 1, 2), (0, 1, 1, 2)]) == 2 / 5 - 2 / 4,
        "candidate share minus judge share, pooled",
    )


def _t_p1_pairing() -> None:
    per_record = [
        ("m1", {"bs": {0: 0.5, 1: 0.4}, "j": {0: 0.4, 1: 0.4}}),
        ("m1", {"bs": {0: 0.3}, "j": {0: 0.2}}),
        ("m2", {"bs": {0: 0.1}, "j": {0: 0.3}}),
    ]
    got = paired_clusters(per_record, "bs", "j")
    _check(set(got) == {"m1", "m2"}, f"clusters are meetings: {set(got)}")
    m1 = sorted(got["m1"])
    _check(
        abs(m1[0]) < 1e-12 and abs(m1[1] - 0.1) < 1e-12,
        f"window means over records: {got['m1']}",
    )
    _check(abs(got["m2"][0] - (-0.2)) < 1e-12, "candidate minus baseline")
    flipped = paired_clusters(per_record, "j", "bs")
    _check(abs(flipped["m2"][0] - 0.2) < 1e-12, "the sign follows the argument order")


def _row(
    label: str, ceiling: int, kept: int, final: int | None = None, **kw: Any
) -> ReplayRow:
    objs = [_o(f"o{i}") for i in range(ceiling)]
    fin = objs[: (final if final is not None else min(kept, BACKSTOP))]
    return ReplayRow(
        label, kw.get("primary", True), objs, objs[:kept], fin, kw.get("valid", True)
    )


def _t_replay_checks() -> None:
    rows = [_row("a", 24, 10), _row("b", 12, 12), _row("c", 21, 21)]
    problems = replay_problems(rows, expect=(3, 57, 5, 2))
    _check(problems == [], f"clean fixture: {problems}")
    _check(
        any(
            "invalid" in p
            for p in replay_problems([_row("x", 5, 3, valid=False)], expect=None)
        ),
        "an invalid stored replay is a V1 failure",
    )
    kept_all = _row("y", 24, 24, final=19)
    _check(
        any("final" in p for p in replay_problems([kept_all], expect=None)),
        "a judge that kept everything must leave the backstop's final",
    )
    _check(
        any("cut" in p for p in replay_problems(rows, expect=(3, 57, 4, 2))),
        "wrong cut count",
    )
    _check(
        any("sources" in p for p in replay_problems(rows, expect=(3, 57, 5, 3))),
        "wrong source count",
    )
    _check(
        any("objects" in p for p in replay_problems(rows, expect=(3, 58, 5, 2))),
        "wrong object count",
    )
    secondary = [_row("s", 30, 30, final=20, primary=False)]
    _check(
        replay_problems([*rows, *secondary], expect=(3, 57, 5, 2)) == [], "primary only"
    )


def _t_stop_line_and_budget() -> None:
    _check(stop_line([30.0] * 20) is True, "20 calls averaging 30 s")
    _check(stop_line([30.0] * 19) is False, "fewer than 20 calls: not yet")
    _check(stop_line([25.0] * 20) is False, "exactly 25 s is not more than 25 s")
    _check(stop_line([26.0] * 20) is True, "more than 25 s")
    _check(stop_line([5.0] * 20 + [500.0] * 20) is False, "only the first 20 count")
    _check(
        budget_exceeded(2700.0) is False and budget_exceeded(2700.1) is True, "45 min"
    )


def _t_restored() -> None:
    ceiling = [_o("a", 0), _o("b", 0), _o("c", 1), _o("d", None), _o("e", 1)]
    ceiling += [_o(f"x{i}", 2) for i in range(18)]
    self_kept = [True, False, False, False, True] + [True] * 18
    fixed_kept = [False, False, True, True, True] + [True] * 18
    got = restored_by_window(ceiling, [self_kept, fixed_kept])
    _check(got == {0: [0, 1], 1: [2]}, f"union of restored, windowed, in order: {got}")
    late = [_o(f"y{i}", 3) for i in range(21)]
    got = restored_by_window(late, [[False] * 21, [False] * 21])
    _check(got == {3: list(range(20))}, f"index >= backstop is not restored: {got}")


def _t_digest_and_degraded() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        (root / "a").mkdir()
        (root / "a" / "1.json").write_text("one", encoding="utf-8")
        (root / "2.json").write_text("two", encoding="utf-8")
        first = files_digest(root)
        _check(first == files_digest(root) and len(first) == 64, "stable sha256")
        (root / "2.json").write_text("TWO", encoding="utf-8")
        _check(first != files_digest(root), "content-sensitive")
    _check(cp.degraded(2, 20) is False and cp.degraded(3, 20) is True, "10% bar")


def _t_arm_statuses() -> None:
    def iv(p: float, lo: float, hi: float) -> dict[str, float]:
        return {"point": p, "lower": lo, "upper": hi}

    summary = {
        "p1": {"self": iv(0.01, 0.0, 0.02), "fixed": iv(0.0, -0.02, 0.02)},
        "p2": {"self": iv(0, -0.04, 0.03), "fixed": iv(-0.09, -0.1, -0.06)},
        "s1": {"self": iv(0.03, 0.0, 0.06), "fixed": iv(0.1, 0.06, 0.12)},
    }
    got = arm_statuses(summary)
    _check(got["p1"] == {"self": NI, "fixed": UNR}, f"P1 {got['p1']}")
    _check(got["p2"] == {"self": NI, "fixed": INF}, f"P2 {got['p2']}")
    _check(got["s1"] == {"self": UNR, "fixed": INF}, f"S1 {got['s1']}")


def _t_label_run() -> None:
    import tempfile

    def sender(wall: float) -> Send:
        def send(
            model: str, messages: Sequence[Any], seed: int, timeout: float
        ) -> tuple[str | None, dict[str, Any]]:
            n = str(messages[-1]["content"]).count("\n") - 3
            reply = json.dumps([{"id": i + 1, "label": "supported"} for i in range(n)])
            return reply, {"outcome": "ok", "wall_s": wall, "content": reply}

        return send

    items = [
        LabelItem("qwen3:8b", "IS1002b", 1, w, list(range(2)), ["t: d", "u: e"])
        for w in range(25)
    ]
    texts = {"IS1002b": [f"window {i}" for i in range(25)]}
    with tempfile.TemporaryDirectory() as tmp:
        out = pathlib.Path(tmp)
        _raises(
            StopLine,
            lambda: run_restored_labels(
                items, texts, sender(30.0), out, lambda _m: None
            ),
            "stop line",
        )
        _check(
            len(list((out / "restored").rglob("*.json"))) == 0,
            "a stopped run stores no partial label file",
        )
    with tempfile.TemporaryDirectory() as tmp:
        out = pathlib.Path(tmp)
        _raises(
            StopLine,
            lambda: run_restored_labels(
                items,
                texts,
                sender(5.0),
                out,
                lambda _m: None,
                budget_s=60.0,
                spent_s=0.0,
            ),
            "budget",
        )
        _raises(
            StopLine,
            lambda: run_restored_labels(
                items,
                texts,
                sender(5.0),
                out,
                lambda _m: None,
                budget_s=100.0,
                spent_s=50.0,
            ),
            "budget",
        )
        _check(
            not list((out / "restored").rglob("*.json")),
            "a budget stop stores no partial label file",
        )
    with tempfile.TemporaryDirectory() as tmp:
        out = pathlib.Path(tmp)
        calls = run_restored_labels(items, texts, sender(5.0), out, lambda _m: None)
        _check(calls == 25, f"one call per window: {calls}")
        again = run_restored_labels(items, texts, sender(5.0), out, lambda _m: None)
        _check(again == 0, f"resumable: {again}")


TESTS: Final[Sequence[Callable[[], None]]] = (
    _t_registered_constants,
    _t_foreign_control_refusal,
    _t_embedding_guard,
    _t_result_is_stamped,
    _t_gate_direction,
    _t_decision_rule,
    _t_ts3012_refusal,
    _t_cost_gate,
    _t_bootstrap_determinism,
    _t_pairing_nested,
    _t_p1_pairing,
    _t_replay_checks,
    _t_stop_line_and_budget,
    _t_restored,
    _t_digest_and_degraded,
    _t_arm_statuses,
    _t_label_run,
)


def self_test() -> int:
    failed = 0
    for test in TESTS:
        try:
            test()
            print(f"ok   {test.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"FAIL {test.__name__}: {exc}")
    print(f"self-test: {len(TESTS) - failed} of {len(TESTS)} passed")
    return 1 if failed else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--self-test", action="store_true", help="model-free checks, then exit"
    )
    parser.add_argument("--screen-dir", type=pathlib.Path, default=js.SCREEN_DIR)
    parser.add_argument("--study-dir", type=pathlib.Path, default=js.OUT_DIR)
    parser.add_argument("--precision-dir", type=pathlib.Path, default=cp.OUT_DIR)
    parser.add_argument("--label-dir", type=pathlib.Path, default=ls.OUT_DIR)
    parser.add_argument("--out-dir", type=pathlib.Path, default=OUT_DIR)
    parser.add_argument("--host", default=None)
    sub = parser.add_subparsers(dest="command")
    pop = sub.add_parser("population")
    pop.add_argument(
        "--meetings", nargs="*", default=None, help="extra meetings to vet"
    )
    sub.add_parser("replay")
    sub.add_parser("cost")
    for name in ("score", "decide"):
        p = sub.add_parser(name)
        p.add_argument(
            "--s1-gating",
            action="store_true",
            help="the owner made S1 (clutter) gating: it joins the decision rule",
        )
    conf = sub.add_parser("confirm")
    conf.add_argument("--meetings", nargs="*", default=None)
    conf.add_argument(
        "--run", action="store_true", help="call models (default: preflight only)"
    )
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    commands: dict[str, Callable[[argparse.Namespace], int]] = {
        "population": cmd_population,
        "replay": cmd_replay,
        "cost": cmd_cost,
        "score": cmd_score,
        "decide": cmd_decide,
        "confirm": cmd_confirm,
    }
    if args.command not in commands:
        raise SystemExit("choose a subcommand, or --self-test")
    return commands[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
