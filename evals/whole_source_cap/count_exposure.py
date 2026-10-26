"""Model-free exposure count for the whole-source generation cap (#1607).

On the UNCHUNKED path of `extract_concept_union`, an `OllamaGenerationCapped`
from `_extract_once` discards the whole source's extraction; the chunked path
retries a failed window once and skips it (#1053). This harness answers, before
any model is called, how large the exposure is:

1. **How many sources take the unchunked path?** Under production `fans_out`,
   over the allowed corpora only -- the fail-closed AMI allowlist of
   `evals/concept_model_screen/screen_data.py` and the extraction-corpus prose
   sources -- with the character-length distribution of those.
2. **How often does a whole-source call cap?** From stored records, per model,
   `k of N` records that took the unchunked path.
3. **How often does a window cap?** The per-window first-attempt cap rate and
   how many capped windows the retry rescued: the best available prior for
   remedy (1), fall back to the chunked path.
4. **What does a call cost?** Measured seconds per non-cached call, for the
   forecast in `PREREGISTRATION.md`.

The negligibility threshold is fixed here, before the counts were read:
`NEGLIGIBLE_THRESHOLD`. No model is called and none can be: the harness reads
transcripts, prose sources and stored records only.

Model-free check: `--self-test`.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import pathlib
import statistics
import sys
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Final

HERE = pathlib.Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "evals" / "concept_model_screen"))

import screen_data as sd  # noqa: E402

from openkos.extraction import concept as concept_mod  # noqa: E402

Record = dict[str, Any]

NEGLIGIBLE_THRESHOLD: Final = 0.05
"""One unchunked source lost in twenty is the line. Below it a user who
ingests a few dozen notes rarely meets the loss and option (3) -- accept the
all-or-nothing cost -- is the honest answer; at or above it the loss is a
routine event. Fixed before the counts were read."""

WILSON_Z: Final = 1.96
"""Two-sided 95% interval."""

PROSE_FIXTURES: Final = (
    "large-03-skills-vs-tools",
    "medium-08-sdk-skills",
    "medium-09-sdk-skills-notes",
    "medium-10-reunion-plataforma",
    "small-04-pre-build-skills",
)
"""The extraction-corpus sources the concept screen uses."""

CAPPED: Final = "capped"
KIND_WHOLE: Final = "whole_source_extract"
KIND_WINDOW: Final = "window"


# --- statistics --------------------------------------------------------------


def wilson(k: int, n: int) -> tuple[float, float]:
    """Wilson score interval for `k` of `n`; the whole interval when `n` is 0."""
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    z2 = WILSON_Z * WILSON_Z
    denom = 1 + z2 / n
    centre = (p + z2 / (2 * n)) / denom
    half = WILSON_Z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def verdict(k: int, n: int, threshold: float = NEGLIGIBLE_THRESHOLD) -> str:
    """NEGLIGIBLE only when the interval's upper end is under the threshold,
    NOT_NEGLIGIBLE only when its lower end is at or over it; otherwise the
    data cannot say, which is a finding and not a pass."""
    lo, hi = wilson(k, n)
    if hi < threshold:
        return "NEGLIGIBLE"
    if lo >= threshold:
        return "NOT_NEGLIGIBLE"
    return "UNDETERMINED"


def percentile(values: Sequence[float], q: float) -> float:
    """Nearest-rank percentile, `q` in (0, 1]."""
    ordered = sorted(values)
    rank = max(1, math.ceil(q * len(ordered)))
    return float(ordered[rank - 1])


# --- exposure ----------------------------------------------------------------


@dataclass(frozen=True)
class Source:
    name: str
    title: str
    text: str
    pool: str


@dataclass(frozen=True)
class Exposure:
    name: str
    pool: str
    chars: int
    unchunked: bool
    windows: int
    """How many windows production's `_chunk_lines` cuts the source into --
    what remedy (1) would fan out to."""


def classify(sources: Iterable[Source]) -> list[Exposure]:
    """Which path production takes, asked of `fans_out` and never re-derived."""
    return [
        Exposure(
            s.name,
            s.pool,
            len(s.text),
            not concept_mod.fans_out(s.text, source_title=s.title),
            len(concept_mod._chunk_lines(s.text)),
        )
        for s in sources
    ]


def length_summary(chars: Sequence[int]) -> dict[str, float]:
    if not chars:
        return {"n": 0}
    return {
        "n": len(chars),
        "min": min(chars),
        "median": float(statistics.median(chars)),
        "p90": percentile(chars, 0.9),
        "max": max(chars),
    }


# --- stored records ----------------------------------------------------------


def is_extraction_record(data: object) -> bool:
    return isinstance(data, dict) and "chunked" in data and "calls" in data


def _extraction_calls(record: Record) -> list[Record]:
    return [c for c in record["calls"] if c.get("kind") in (KIND_WHOLE, KIND_WINDOW)]


def dedup_key(record: Record) -> tuple[str, ...]:
    """Two records that sent the same extraction generations are one record.

    A call key hashes (model, messages, seed, ...), so equal keys are the same
    generation reused from the shared cache by a later study."""
    keys = sorted(str(c.get("key")) for c in _extraction_calls(record))
    return (str(record.get("model")), *keys)


def whole_source_rates(records: Iterable[Record]) -> dict[str, tuple[int, int]]:
    """Per model: (records whose whole-source call capped, unchunked records)."""
    counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for record in records:
        if record["chunked"]:
            continue
        row = counts[str(record["model"])]
        row[1] += 1
        if any(
            c.get("kind") == KIND_WHOLE and c.get("outcome") == CAPPED
            for c in record["calls"]
        ):
            row[0] += 1
    return {m: (c[0], c[1]) for m, c in counts.items()}


def whole_source_by_source(
    records: Iterable[Record],
) -> dict[tuple[str, str], tuple[int, int]]:
    """Per (model, source): (capped records, unchunked records). Runs of one
    source are not independent draws of the source population, so the pooled
    rate must be read beside this."""
    counts: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    for record in records:
        if record["chunked"]:
            continue
        row = counts[(str(record["model"]), str(record["source"]))]
        row[1] += 1
        if any(
            c.get("kind") == KIND_WHOLE and c.get("outcome") == CAPPED
            for c in record["calls"]
        ):
            row[0] += 1
    return {key: (c[0], c[1]) for key, c in counts.items()}


def window_rates(records: Iterable[Record]) -> dict[str, dict[str, int]]:
    """Per model, over chunked records' window calls: first attempts, how many
    capped, how many of those the retry rescued, how many were lost."""
    out: dict[str, dict[str, int]] = {}
    for record in records:
        windows = [c for c in record["calls"] if c.get("kind") == KIND_WINDOW]
        if not windows:
            continue
        row = out.setdefault(
            str(record["model"]),
            {"first_attempts": 0, "first_capped": 0, "rescued": 0, "lost": 0},
        )
        firsts = [c for c in windows if c.get("attempt") == 0]
        row["first_attempts"] += len(firsts)
        for first in firsts:
            if first.get("outcome") != CAPPED:
                continue
            row["first_capped"] += 1
            retries = [
                c
                for c in windows
                if c.get("window") == first.get("window") and c.get("attempt") == 1
            ]
            if retries and retries[0].get("outcome") == "ok":
                row["rescued"] += 1
            else:
                row["lost"] += 1
    return out


def record_wall_by_windows(
    records: Iterable[Record],
) -> dict[str, dict[int, tuple[float, int]]]:
    """Per model, chunked records only: window count -> (median whole-record
    wall seconds, n). The whole record, judge and re-ask included, because
    remedy (1) pays for all of it, not just the window calls."""
    walls: dict[str, dict[int, list[float]]] = defaultdict(lambda: defaultdict(list))
    for record in records:
        if not record["chunked"] or record.get("wall_s") is None:
            continue
        walls[str(record["model"])][len(record.get("windows", []))].append(
            float(record["wall_s"])
        )
    return {
        model: {n: (float(statistics.median(v)), len(v)) for n, v in by_n.items()}
        for model, by_n in walls.items()
    }


def call_seconds(records: Iterable[Record]) -> dict[str, dict[str, float]]:
    """Measured wall seconds per non-cached call, keyed
    `model|kind` for completed calls and `model|kind|capped` for capped ones."""
    walls: dict[str, list[float]] = defaultdict(list)
    evals: dict[str, list[float]] = defaultdict(list)
    for record in records:
        for call in _extraction_calls(record):
            if call.get("cache_hit") or call.get("wall_s") is None:
                continue
            outcome = call.get("outcome")
            if outcome not in ("ok", CAPPED):
                continue
            key = f"{record['model']}|{call['kind']}" + (
                "|capped" if outcome == CAPPED else ""
            )
            walls[key].append(float(call["wall_s"]))
            if call.get("eval_count") is not None:
                evals[key].append(float(call["eval_count"]))
    return {
        key: {
            "n": float(len(values)),
            "median_s": float(statistics.median(values)),
            "p90_s": percentile(values, 0.9),
            "median_eval_count": float(statistics.median(evals[key]))
            if evals[key]
            else 0.0,
        }
        for key, values in walls.items()
    }


# --- loading -----------------------------------------------------------------


def load_records(runs_root: pathlib.Path) -> tuple[list[Record], dict[str, int]]:
    """Every `records*/**/r*.json` under `runs_root`, deduplicated.

    Reads only; a file that fails to parse (one being written right now) is
    counted and skipped, never raised."""
    stats = {"files": 0, "unreadable": 0, "not_extraction": 0, "duplicates": 0}
    seen: set[tuple[str, ...]] = set()
    records: list[Record] = []
    for path in sorted(runs_root.glob("**/r*.json")):
        parts = path.relative_to(runs_root).parts
        if not any(p.startswith("records") for p in parts):
            continue
        stats["files"] += 1
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except OSError, ValueError:
            stats["unreadable"] += 1
            continue
        if not is_extraction_record(data):
            stats["not_extraction"] += 1
            continue
        key = dedup_key(data)
        if key in seen:
            stats["duplicates"] += 1
            continue
        seen.add(key)
        records.append(data)
    return records, stats


def load_sources(
    runs_root: pathlib.Path, prose_dir: pathlib.Path
) -> tuple[list[Source], list[str]]:
    """The allowed corpora. A meeting off the allowlist is refused by
    `require_allowed`; a missing file is reported, not skipped silently."""
    sources: list[Source] = []
    missing: list[str] = []
    transcripts = runs_root / "concept-screen" / "inputs" / "transcripts"
    for meeting in sorted(sd.ALLOWED_MEETINGS):
        sd.require_allowed(meeting)
        path = transcripts / f"{meeting}.md"
        if not path.is_file():
            missing.append(meeting)
            continue
        body = sd.strip_frontmatter(path.read_text(encoding="utf-8"))
        sources.append(Source(meeting, meeting, body, "ami"))
    spec = importlib.util.spec_from_file_location(
        "run_cap_eval", REPO_ROOT / "evals" / "extraction_cap" / "run_cap_eval.py"
    )
    if spec is None or spec.loader is None:
        raise SystemExit("cannot load run_cap_eval")
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_cap_eval"] = module
    spec.loader.exec_module(module)
    for name in PROSE_FIXTURES:
        path = prose_dir / f"{name}.md"
        if not path.is_file():
            missing.append(f"prose-{name}")
            continue
        text = path.read_text(encoding="utf-8")
        sources.append(
            Source(f"prose-{name}", module.source_title_for(text, path), text, "prose")
        )
    return sources, missing


# --- report ------------------------------------------------------------------


def _rate(k: int, n: int) -> str:
    lo, hi = wilson(k, n)
    return f"{k} of {n} ({lo:.3f}..{hi:.3f}) {verdict(k, n)}"


def report(runs_root: pathlib.Path, prose_dir: pathlib.Path) -> None:
    sources, missing = load_sources(runs_root, prose_dir)
    exposures = classify(sources)
    print(f"threshold: {NEGLIGIBLE_THRESHOLD} (Wilson 95%)")
    print(f"sources present: {len(sources)}; missing: {missing or 'none'}")
    for pool in ("ami", "prose"):
        part = [e for e in exposures if e.pool == pool]
        un = [e for e in part if e.unchunked]
        print(f"{pool}: {len(un)} of {len(part)} take the unchunked path")
        print(f"  unchunked chars: {length_summary([e.chars for e in un])}")
        for e in sorted(un, key=lambda x: x.chars):
            print(f"    {e.name}\t{e.chars} chars\t{e.windows} windows if chunked")
    total_un = sum(1 for e in exposures if e.unchunked)
    print(f"all: {total_un} of {len(exposures)} take the unchunked path")

    records, stats = load_records(runs_root)
    print(f"records: {len(records)} extraction records; load stats {stats}")
    print("whole-source cap, unchunked records, per model:")
    for model, (k, n) in sorted(whole_source_rates(records).items()):
        print(f"  {model}: {_rate(k, n)}")
    print("whole-source cap, per (model, source) with at least one cap:")
    by_source = whole_source_by_source(records)
    for (model, source), (k, n) in sorted(by_source.items()):
        if k:
            print(f"  {model} {source}: {k} of {n}")
    distinct = {s for (_m, s) in by_source}
    print(f"  distinct unchunked sources in the records: {len(distinct)}")
    print("window cap, chunked records, per model (first attempts):")
    for model, row in sorted(window_rates(records).items()):
        print(
            f"  {model}: capped {_rate(row['first_capped'], row['first_attempts'])};"
            f" of {row['first_capped']} capped: rescued {row['rescued']},"
            f" lost {row['lost']}"
        )
    print("measured whole-record wall seconds, chunked records (windows: median, n):")
    for model, by_n in sorted(record_wall_by_windows(records).items()):
        cells = ", ".join(f"{w}: {m:.0f}s n={n}" for w, (m, n) in sorted(by_n.items()))
        print(f"  {model}: {cells}")
    print("measured seconds per non-cached call:")
    for key, timing in sorted(call_seconds(records).items()):
        print(
            f"  {key}: n={int(timing['n'])} median={timing['median_s']:.1f}s"
            f" p90={timing['p90_s']:.1f}s"
            f" median_eval={timing['median_eval_count']:.0f}"
        )


# --- self-test ---------------------------------------------------------------


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _call(
    kind: str,
    outcome: str,
    *,
    window: int | None = None,
    attempt: int = 0,
    wall: float = 10.0,
    evals: int = 100,
    key: str = "k",
    cached: bool = False,
) -> Record:
    return {
        "kind": kind,
        "window": window,
        "attempt": attempt,
        "outcome": outcome,
        "wall_s": wall,
        "eval_count": evals,
        "key": key,
        "cache_hit": cached,
    }


def _rec(
    model: str,
    source: str,
    calls: list[Record],
    *,
    chunked: bool,
    run: int = 1,
) -> Record:
    return {
        "model": model,
        "source": source,
        "run": run,
        "chunked": chunked,
        "calls": calls,
        "failure": None,
    }


def _t_wilson_zero_of_twenty() -> None:
    lo, hi = wilson(0, 20)
    _check(lo == 0.0, f"lower {lo}")
    _check(abs(hi - 0.1611) < 0.001, f"upper {hi}")


def _t_wilson_two_of_twenty() -> None:
    lo, hi = wilson(2, 20)
    _check(abs(lo - 0.0279) < 0.001, f"lower {lo}")
    _check(abs(hi - 0.3010) < 0.001, f"upper {hi}")


def _t_wilson_empty() -> None:
    _check(wilson(0, 0) == (0.0, 1.0), "no data is the whole interval")


def _t_verdict_three_ways() -> None:
    _check(verdict(0, 1000) == "NEGLIGIBLE", "0 of 1000")
    _check(verdict(2, 20) == "UNDETERMINED", "2 of 20 straddles the line")
    _check(verdict(30, 100) == "NOT_NEGLIGIBLE", "30 of 100")
    _check(verdict(0, 5) == "UNDETERMINED", "0 of 5 proves nothing")


def _t_percentile() -> None:
    _check(percentile([1, 2, 3, 4, 5], 0.5) == 3, "median")
    _check(percentile([1, 2, 3, 4, 5], 1.0) == 5, "max")
    _check(percentile([7], 0.9) == 7, "single")


def _t_classify_uses_production_fans_out() -> None:
    short = "a short note.\n" * 10
    long = "word " * 5000
    out = classify(
        [Source("s", "short", short, "prose"), Source("l", "long", long, "prose")]
    )
    by = {e.name: e for e in out}
    _check(by["s"].unchunked and not by["l"].unchunked, "split by length")
    _check(by["l"].chars == len(long), "chars are the text length")
    _check(
        by["l"].unchunked == (not concept_mod.fans_out(long, source_title="long")),
        "agrees with production fans_out",
    )


def _t_length_summary() -> None:
    s = length_summary([10, 20, 30, 40, 100])
    _check(s["n"] == 5 and s["min"] == 10 and s["max"] == 100, "range")
    _check(s["median"] == 30, "median")
    _check(length_summary([])["n"] == 0, "empty")


def _t_is_extraction_record() -> None:
    _check(is_extraction_record(_rec("m", "x", [], chunked=False)), "yes")
    _check(not is_extraction_record({"calls": []}), "no chunked field")
    _check(not is_extraction_record([1]), "not a dict")


def _t_whole_source_rates_unchunked_only() -> None:
    recs = [
        _rec("m", "a", [_call(KIND_WHOLE, CAPPED)], chunked=False, run=1),
        _rec("m", "b", [_call(KIND_WHOLE, "ok", key="b")], chunked=False, run=1),
        _rec("m", "c", [_call(KIND_WINDOW, CAPPED, window=0)], chunked=True),
    ]
    _check(whole_source_rates(recs) == {"m": (1, 2)}, "chunked record excluded")


def _t_whole_source_rate_counts_zero_models() -> None:
    recs = [_rec("z", "a", [_call(KIND_WHOLE, "ok")], chunked=False)]
    _check(whole_source_rates(recs) == {"z": (0, 1)}, "a model with no cap shows 0")


def _t_windows_if_chunked() -> None:
    text = "".join(f"line {i} " + "x" * 90 + "\n" for i in range(150)).rstrip("\n")
    out = classify([Source("s", "t", text, "prose")])[0]
    _check(out.unchunked, "15 KB of 100-char lines is under the prose threshold")
    _check(
        out.windows == len(concept_mod._chunk_lines(text)) and out.windows > 1,
        f"windows the chunked path would cut: {out.windows}",
    )


def _t_wall_by_window_count() -> None:
    def chunked(model: str, n: int, wall: float) -> Record:
        rec = _rec(model, "a", [], chunked=True)
        rec["windows"] = [{"index": i} for i in range(n)]
        rec["wall_s"] = wall
        return rec

    recs = [chunked("m", 3, 100.0), chunked("m", 3, 140.0), chunked("m", 5, 300.0)]
    whole = _rec("m", "b", [], chunked=False)
    whole["wall_s"] = 9999.0
    recs.append(whole)
    _check(
        record_wall_by_windows(recs) == {"m": {3: (120.0, 2), 5: (300.0, 1)}},
        "median wall and n per window count; unchunked records excluded",
    )


def _t_whole_source_by_source() -> None:
    recs = [
        _rec("m", "a", [_call(KIND_WHOLE, CAPPED, key="1")], chunked=False, run=1),
        _rec("m", "a", [_call(KIND_WHOLE, CAPPED, key="2")], chunked=False, run=2),
        _rec("m", "b", [_call(KIND_WHOLE, "ok", key="3")], chunked=False, run=1),
        _rec("m", "c", [_call(KIND_WINDOW, "ok", window=0)], chunked=True),
    ]
    _check(
        whole_source_by_source(recs) == {("m", "a"): (2, 2), ("m", "b"): (0, 1)},
        "the cap concentrates on one source, and chunked records are excluded",
    )


def _t_window_rates() -> None:
    calls = [
        _call(KIND_WINDOW, "ok", window=0, key="w0"),
        _call(KIND_WINDOW, CAPPED, window=1, key="w1a"),
        _call(KIND_WINDOW, "ok", window=1, attempt=1, key="w1b"),
        _call(KIND_WINDOW, CAPPED, window=2, key="w2a"),
        _call(KIND_WINDOW, CAPPED, window=2, attempt=1, key="w2b"),
    ]
    r = window_rates([_rec("m", "a", calls, chunked=True)])["m"]
    _check(r["first_attempts"] == 3, f"first attempts {r}")
    _check(r["first_capped"] == 2, f"first capped {r}")
    _check(r["rescued"] == 1, f"rescued {r}")
    _check(r["lost"] == 1, f"lost {r}")


def _t_window_rates_ignore_whole_calls() -> None:
    recs = [_rec("m", "a", [_call(KIND_WHOLE, CAPPED)], chunked=False)]
    _check(window_rates(recs) == {}, "no window calls, no row")


def _t_dedup_key_collapses_shared_generations() -> None:
    a = _rec("m", "a", [_call(KIND_WHOLE, "ok", key="K")], chunked=False)
    b = _rec("m", "a", [_call(KIND_WHOLE, "ok", key="K")], chunked=False)
    c = _rec("m", "a", [_call(KIND_WHOLE, "ok", key="Q")], chunked=False)
    _check(dedup_key(a) == dedup_key(b), "same generations, same key")
    _check(dedup_key(a) != dedup_key(c), "different generation, different key")


def _t_call_seconds_skips_cache_hits() -> None:
    calls = [
        _call(KIND_WINDOW, "ok", wall=10, window=0, key="a"),
        _call(KIND_WINDOW, "ok", wall=30, window=1, key="b"),
        _call(KIND_WINDOW, "ok", wall=999, window=2, key="c", cached=True),
    ]
    s = call_seconds([_rec("m", "a", calls, chunked=True)])
    _check(s["m|window"]["n"] == 2, f"cache hit excluded {s}")
    _check(s["m|window"]["median_s"] == 20, "median of 10 and 30")


def _t_call_seconds_separates_capped() -> None:
    calls = [
        _call(KIND_WHOLE, CAPPED, wall=260, key="a"),
        _call(KIND_WHOLE, "ok", wall=40, key="b"),
    ]
    s = call_seconds([_rec("m", "a", calls, chunked=False)])
    _check(s["m|whole_source_extract|capped"]["median_s"] == 260, "capped apart")
    _check(s["m|whole_source_extract"]["median_s"] == 40, "ok apart")


def _t_load_records_dedups_and_survives_partial_files() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        rec = _rec("m", "a", [_call(KIND_WHOLE, "ok", key="K")], chunked=False)
        for store in ("records/m/a", "records-pre-fix/m/a"):
            folder = root / store
            folder.mkdir(parents=True)
            (folder / "r1.json").write_text(json.dumps(rec))
        part = root / "records" / "m" / "b"
        part.mkdir(parents=True)
        (part / "r1.json").write_text('{"chunked": fa')
        other = root / "cache" / "x"
        other.mkdir(parents=True)
        (other / "r9.json").write_text(json.dumps(rec))
        records, stats = load_records(root)
        _check(len(records) == 1, f"one distinct record, got {len(records)}")
        _check(stats["duplicates"] == 1, f"stats {stats}")
        _check(stats["unreadable"] == 1, f"stats {stats}")
        _check(stats["files"] == 3, f"files outside records* ignored: {stats}")


def _t_disallowed_meeting_is_refused() -> None:
    try:
        sd.require_allowed("ES2002a")
    except sd.DisallowedMeeting:
        return
    raise AssertionError("an evaluation-series meeting must be refused")


def self_test() -> int:
    tests: list[Callable[[], None]] = [
        _t_wilson_zero_of_twenty,
        _t_wilson_two_of_twenty,
        _t_wilson_empty,
        _t_verdict_three_ways,
        _t_percentile,
        _t_classify_uses_production_fans_out,
        _t_length_summary,
        _t_is_extraction_record,
        _t_whole_source_rates_unchunked_only,
        _t_whole_source_rate_counts_zero_models,
        _t_windows_if_chunked,
        _t_wall_by_window_count,
        _t_whole_source_by_source,
        _t_window_rates,
        _t_window_rates_ignore_whole_calls,
        _t_dedup_key_collapses_shared_generations,
        _t_call_seconds_skips_cache_hits,
        _t_call_seconds_separates_capped,
        _t_load_records_dedups_and_survives_partial_files,
        _t_disallowed_meeting_is_refused,
    ]
    failed = 0
    for test in tests:
        try:
            test()
            print(f"ok   {test.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"FAIL {test.__name__}: {exc}")
    print(f"self-test: {len(tests) - failed} of {len(tests)} passed")
    return 1 if failed else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--self-test", action="store_true", help="model-free checks, then exit"
    )
    parser.add_argument(
        "--runs-root",
        type=pathlib.Path,
        default=pathlib.Path.home() / "Dev/Projects/openkos-eval-runs",
    )
    parser.add_argument(
        "--prose-sources",
        type=pathlib.Path,
        default=pathlib.Path.home()
        / "Dev/Projects/openkos/examples/extraction-corpus/sources",
        help="the extraction-corpus sources (untracked in a worktree)",
    )
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    report(args.runs_root, args.prose_sources)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
