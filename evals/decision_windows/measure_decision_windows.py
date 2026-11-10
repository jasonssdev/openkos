"""Measures overlap and a rolling subject list between decision-pass windows (#1738).

Question: the transcript decision pass (`extraction/decision_pass.py`) asks each
`_chunk_lines` window independently, so a decision proposed in one window and agreed
in the next is split or lost, and one restated later becomes a new item. Does a short
line overlap between consecutive windows (with dedup by evidence position), or a
rolling bounded list of the subjects already extracted from earlier windows, recover
hand-annotated decisions without inflating what the pass produces? The
pre-registration is `PREREGISTRATION.md`; read it before trusting a number from here.
Nothing here changes a production default: both treatments live in this file only.

Usage:

    uv run python -u evals/decision_windows/measure_decision_windows.py --self-test
    uv run python -u evals/decision_windows/measure_decision_windows.py \\
        --gold <SERIES>.decisions.adjudicated.csv ... --transcripts <dir> --exposure
    uv run python -u evals/decision_windows/measure_decision_windows.py \\
        --gold <csv> ... --transcripts <dir> --runs 15 --model qwen3:8b --results-dir <dir>
    uv run python -u evals/decision_windows/measure_decision_windows.py \\
        --gold <csv> ... --report <runs-*.json ...>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import pathlib
import statistics
import sys
import tempfile
import time
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "evals" / "transcript_decisions"))
sys.path.append(str(REPO_ROOT / "evals"))

import run_transcript_decisions as harness  # noqa: E402
import scoring  # noqa: E402
from harness_stamp import TimedBackend, build_stamp, prompt_hash  # noqa: E402

from openkos import prompt_budget  # noqa: E402
from openkos.config import (  # noqa: E402
    DEFAULT_CONTEXT_WINDOW,
    DEFAULT_MAX_GENERATION_TOKENS,
)
from openkos.extraction import decision_pass  # noqa: E402
from openkos.llm.base import LLMBackend, Message  # noqa: E402
from openkos.llm.ollama import OllamaClient  # noqa: E402

DEFAULT_MODEL: Final = "qwen3:8b"
RESULTS_DIR: Final = pathlib.Path(__file__).resolve().parent / "results"

# --- the registered configuration (PREREGISTRATION.md). Fixed before any call.

MIN_RUNS: Final = 15
"""n = 15 is the floor on this harness; fewer is not a result."""
REQUIRED_SERIES: Final = ("ES2015", "IS1004", "TS3009")
ARM_SHIPPED: Final = "shipped"
ARM_OVERLAP: Final = "overlap"
ARM_ROLLING: Final = "rolling"
ARM_BOTH: Final = "both"
ARMS: Final = (ARM_SHIPPED, ARM_OVERLAP, ARM_ROLLING, ARM_BOTH)
DEFAULT_ARMS: Final = (ARM_SHIPPED, ARM_OVERLAP, ARM_ROLLING)
CANDIDATES: Final = (ARM_OVERLAP, ARM_ROLLING)
"""Only these two are tested against `shipped`; `both` is reported, no bar."""
OVERLAP_ARMS: Final = (ARM_OVERLAP, ARM_BOTH)
ROLLING_ARMS: Final = (ARM_ROLLING, ARM_BOTH)

GOLD_SHA256: Final = {
    "ES2015.decisions.adjudicated.csv": (
        "de3e6439e7e8fe79b623d1d58f72a009fb691ebe0ee96d01f1c105e18debad9c"
    ),
    "IS1004.decisions.adjudicated.csv": (
        "1b84ae083f1547fc6f356663fc80df9aa2f2aa978196406e50ae2e5e9e3cae7d"
    ),
    "TS3009.decisions.adjudicated.csv": (
        "40aa8312480462bcedddaa31a5f379d893ccf8235e3ed7dcf845c666cb46921a"
    ),
}
"""The gold files are pinned by content. A different hash voids the run."""

SHIPPED_CAP: Final = 25
"""`decision_pass.MAX_PASS_DECISIONS` as shipped; every arm is read at this cap."""
OVERLAP_LINES: Final = 6
"""Lines of the previous window repeated at the head of the next. Proposed."""
OVERLAP_MAX_CHARS: Final = 800
"""Upper bound on the repeated head, whole lines only. Proposed."""
ROLLING_MAX_SUBJECTS: Final = 30
"""Most recent distinct subjects shown to the next window. Proposed."""
ROLLING_SUBJECT_CHARS: Final = 80
"""Each shown subject is cut to this many characters."""
ROLLING_HEADER: Final = (
    "SUBJECTS ALREADY EXTRACTED FROM EARLIER WINDOWS. Do not list a decision "
    "again if it only restates one of these subjects; list it only if this "
    "window settles, changes or completes it:"
)
ROLLING_HEADER_HASH: Final = "47a4b2f6997419ef"
"""`prompt_hash(ROLLING_HEADER)`; a change to the header is a new registration."""

# Bars (each candidate against `shipped`, per series, unpaired runs).
K_TOLERANCE_ROWS: Final = 1.0
G_GAIN_ROWS: Final = 1.0
G_PRECISION_RATIO: Final = 1.25
G_Z: Final = 2.0
V_FACTOR: Final = 1.25
P_SLACK: Final = 0.05
F_SLACK: Final = 0.05
C_FACTOR: Final = 1.15
MIN_EXPOSURE_ROWS: Final = 6
"""Gold `decision` rows pooled over the series that a boundary can affect."""


# --- windows ---------------------------------------------------------------


@dataclass(frozen=True)
class Window:
    """One window of the pass. `text` may start with `own_start - start` repeated
    lines; `[own_start, end)` are the lines this window owns (global indices)."""

    text: str
    start: int
    own_start: int
    end: int


def build_windows(
    base: Sequence[str], overlap_lines: int, overlap_max_chars: int
) -> list[Window]:
    """Prefix each window after the first with the tail of the previous one.

    `base` is the shipped `_chunk_lines` windows. The tail is at most
    `overlap_lines` whole lines and at most `overlap_max_chars` characters;
    `overlap_lines=0` returns the shipped windows byte for byte."""
    windows: list[Window] = []
    cursor = 0
    previous: list[str] = []
    for text in base:
        lines = text.split("\n")
        lead: list[str] = []
        used = 0
        for line in reversed(previous[-overlap_lines:] if overlap_lines else []):
            if used + len(line) + 1 > overlap_max_chars:
                break
            lead.insert(0, line)
            used += len(line) + 1
        body = "\n".join(lead + lines) if lead else text
        windows.append(Window(body, cursor - len(lead), cursor, cursor + len(lines)))
        cursor += len(lines)
        previous = lines
    return windows


def locate_line(lines: Sequence[str], evidence: str, lo: int, hi: int) -> int | None:
    """Global index of the first line in `[lo, hi)` that quotes `evidence`."""
    for index in range(max(lo, 0), min(hi, len(lines))):
        if decision_pass.quoted_verbatim(evidence, lines[index]):
            return index
    return None


@dataclass(frozen=True)
class Admitted:
    """One verbatim-admitted item with the window that produced it and the
    global line its evidence sits on (`None` when no single line holds it)."""

    item: decision_pass.DecisionItem
    window: int
    pos: int | None


def dedup_overlap(
    admitted: Sequence[Admitted], windows: Sequence[Window]
) -> list[Admitted]:
    """Drop what an overlap made a window say twice.

    An item of window i whose evidence line lies in its repeated head is dropped
    only as far as window i-1 produced an item on that same line, one for one, so
    two real decisions sharing one evidence line are not collapsed and an item the
    previous window missed survives (that is the point of the overlap)."""
    previous_at: dict[tuple[int, int], int] = defaultdict(int)
    for entry in admitted:
        if entry.pos is not None:
            previous_at[(entry.window, entry.pos)] += 1
    budget = dict(previous_at)
    kept: list[Admitted] = []
    for entry in admitted:
        if (
            entry.window > 0
            and entry.pos is not None
            and windows[entry.window].start
            <= entry.pos
            < windows[entry.window].own_start
        ):
            key = (entry.window - 1, entry.pos)
            if budget.get(key, 0) > 0:
                budget[key] -= 1
                continue
        kept.append(entry)
    return kept


# --- rolling subjects ------------------------------------------------------


def rolling_subjects(
    items: Sequence[decision_pass.DecisionItem], bound: int, max_chars: int
) -> list[str]:
    """The most recent `bound` distinct subjects, oldest first, each cut."""
    seen: dict[str, str] = {}
    for item in items:
        key = decision_pass._normalize(item.subject)
        seen.pop(key, None)
        seen[key] = item.subject[:max_chars]
    return list(seen.values())[len(seen) - bound :] if bound > 0 else []


def with_known_subjects(
    messages: Sequence[Message], subjects: Sequence[str]
) -> list[Message]:
    """The messages with the subject list appended to the last user message."""
    if not subjects:
        return [dict(m) for m in messages]  # type: ignore[misc]
    block = "\n\n" + ROLLING_HEADER + "\n" + "\n".join(f"- {s}" for s in subjects)
    out: list[Message] = [dict(m) for m in messages]  # type: ignore[misc]
    out[-1] = {"role": out[-1]["role"], "content": out[-1]["content"] + block}
    return out


class InjectingLLM:
    """Adds `known` to every prompt it forwards; empty `known` forwards unchanged."""

    def __init__(self, inner: LLMBackend) -> None:
        self._inner = inner
        self.known: list[str] = []

    def chat(self, messages: Sequence[Message]) -> str:
        return self._inner.chat(with_known_subjects(messages, self.known))


# --- the pass loop ---------------------------------------------------------


@dataclass(frozen=True)
class PassOutcome:
    admitted: list[Admitted]
    failure: str | None
    windows: int


def run_windows(
    windows: Sequence[Window],
    llm: InjectingLLM,
    lines: Sequence[str],
    *,
    rolling: bool,
    retry_after_cap: bool = True,
) -> PassOutcome:
    """`decision_pass.run_decision_pass` over `windows`, without its dedup and cap,
    keeping which window and which line each admitted item came from. With
    `rolling` off and no overlap it asks exactly what the shipped pass asks."""
    admitted: list[Admitted] = []
    failure: str | None = None
    for index, window in enumerate(windows):
        llm.known = (
            rolling_subjects(
                [a.item for a in admitted], ROLLING_MAX_SUBJECTS, ROLLING_SUBJECT_CHARS
            )
            if rolling
            else []
        )
        parsed, cause, retryable = decision_pass._ask_window(
            window.text, llm, None, retry_after_cap=retry_after_cap
        )
        if retryable:
            parsed, cause, _ = decision_pass._ask_window(window.text, llm, None)
        if parsed is None:
            failure = failure or cause
            continue
        valid = [item for data in parsed if (item := decision_pass.validate_item(data))]
        for item in decision_pass.verbatim_items(valid, window.text):
            pos = locate_line(lines, item.evidence, window.start, window.end)
            admitted.append(Admitted(item, index, pos))
    llm.known = []
    return PassOutcome(admitted, failure, len(windows))


def finalize(
    outcome: PassOutcome, windows: Sequence[Window], *, overlap: bool
) -> list[Admitted]:
    """Overlap dedup (overlap arms), then the shipped exact-subject dedup; no cap."""
    entries = dedup_overlap(outcome.admitted, windows) if overlap else outcome.admitted
    kept = decision_pass.dedup_by_subject([e.item for e in entries])
    by_item = {id(e.item): e for e in entries}
    return [by_item[id(item)] for item in kept]


# --- records and scoring ---------------------------------------------------


def make_record(
    arm: str,
    run: int,
    meeting: str,
    rows: Sequence[scoring.GoldRow],
    outcome: PassOutcome,
    final: Sequence[Admitted],
    *,
    calls: int,
    wall_s: float,
) -> dict[str, Any]:
    items = []
    for entry in final:
        item = entry.item
        items.append(
            {
                "title": decision_pass.compose_title(item),
                "subject": item.subject,
                "value": item.value,
                "evidence": [item.evidence],
                "matched_ids": scoring.match_item([item.evidence], rows),
                "window": entry.window,
                "pos": entry.pos,
            }
        )
    return {
        "arm": arm,
        "run": run,
        "meeting": meeting,
        "series": scoring.series_of(meeting),
        "shaped": True,
        "windows": outcome.windows,
        "calls": calls,
        "failure": outcome.failure,
        "wall_s": round(wall_s, 2),
        "items": items,
    }


def capped(records: Sequence[Mapping[str, Any]], cap: int) -> list[dict[str, Any]]:
    """The records as the shipped cap would leave them."""
    return [{**r, "items": list(r["items"])[:cap]} for r in records]


def per_run_metrics(
    records: Sequence[Mapping[str, Any]],
    rows: Sequence[scoring.GoldRow],
    arm: str,
    cap: int,
) -> list[dict[str, dict[str, Any]]]:
    """`[ {series: metrics} ]` over the arm's runs, in run order."""
    by_run: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
    for record in records:
        if record["arm"] == arm:
            by_run[record["run"]].append(record)
    return [
        scoring.series_run_metrics(capped(by_run[run], cap), rows)
        for run in sorted(by_run)
    ]


def strict_precision(metrics: Mapping[str, Any]) -> float | None:
    produced = metrics["produced"]
    return metrics["recalled"] / produced if produced else None


def exposed_rows(
    base: Sequence[str], meeting_rows: Sequence[scoring.GoldRow], head_lines: int
) -> int:
    """Gold `decision` rows whose dialogue act sits in the first `head_lines`
    lines of a window after the first: the rows a boundary can split."""
    decisions = [r for r in meeting_rows if r.status == "decision"]
    hit: set[str] = set()
    lines = [ln for w in base for ln in w.split("\n")]
    cursor = 0
    for index, text in enumerate(base):
        count = len(text.split("\n"))
        if index > 0:
            for line in lines[cursor : cursor + head_lines]:
                hit.update(i for i in scoring.match_item([line], decisions))
        cursor += count
    return len(hit)


# --- bars ------------------------------------------------------------------


def _mean(values: Sequence[float]) -> float:
    return statistics.fmean(values)


def z_score(candidate: Sequence[float], control: Sequence[float]) -> float | None:
    """(mean difference) / standard error of the unpaired difference."""
    if len(candidate) < 2 or len(control) < 2:
        return None
    diff = _mean(candidate) - _mean(control)
    error = math.sqrt(
        statistics.variance(candidate) / len(candidate)
        + statistics.variance(control) / len(control)
    )
    if error == 0:
        return 0.0 if diff == 0 else math.copysign(math.inf, diff)
    return diff / error


def _values(runs: Sequence[Mapping[str, Any]], key: str) -> list[float]:
    return [float(r[key]) for r in runs if r[key] is not None]


def evaluate_series(
    control: Sequence[Mapping[str, Any]], cand: Sequence[Mapping[str, Any]]
) -> dict[str, str]:
    """`{bar: pass|fail|vacuous}` for one series; inputs are per-run metrics."""
    bars: dict[str, str] = {}
    rec_c, rec_s = _values(cand, "recalled"), _values(control, "recalled")
    bars["K"] = "pass" if _mean(rec_c) >= _mean(rec_s) - K_TOLERANCE_ROWS else "fail"
    prec_c = [p for r in cand if (p := strict_precision(r)) is not None]
    prec_s = [p for r in control if (p := strict_precision(r)) is not None]
    if _mean(rec_s) == 0:
        bars["G"] = "vacuous"
    else:
        z_rec = z_score(rec_c, rec_s)
        gain_rec = (
            _mean(rec_c) - _mean(rec_s) >= G_GAIN_ROWS
            and z_rec is not None
            and z_rec >= G_Z
        )
        z_prec = z_score(prec_c, prec_s)
        gain_prec = (
            bool(prec_s)
            and bool(prec_c)
            and _mean(prec_c) >= G_PRECISION_RATIO * _mean(prec_s)
            and bars["K"] == "pass"
            and z_prec is not None
            and z_prec >= G_Z
        )
        bars["G"] = "pass" if gain_rec or gain_prec else "fail"
    bars["V"] = (
        "pass"
        if _mean(_values(cand, "produced"))
        <= V_FACTOR * _mean(_values(control, "produced"))
        else "fail"
    )
    ann_c, ann_s = (
        _values(cand, "precision_on_annotated"),
        _values(control, "precision_on_annotated"),
    )
    bars["P"] = (
        "vacuous"
        if not ann_c or not ann_s
        else ("pass" if _mean(ann_c) >= _mean(ann_s) - P_SLACK else "fail")
    )

    def fail_rate(runs: Sequence[Mapping[str, Any]]) -> float:
        meetings = sum(r["meetings"] for r in runs)
        return sum(r["failed_meetings"] for r in runs) / meetings if meetings else 0.0

    bars["F"] = "pass" if fail_rate(cand) <= fail_rate(control) + F_SLACK else "fail"

    def calls_per_meeting(runs: Sequence[Mapping[str, Any]]) -> float:
        meetings = sum(r["meetings"] for r in runs)
        return sum(r["calls"] for r in runs) / meetings if meetings else 0.0

    bars["C"] = (
        "pass"
        if calls_per_meeting(cand) <= C_FACTOR * calls_per_meeting(control)
        else "fail"
    )
    return bars


def verdict(
    bars_by_series: Mapping[str, Mapping[str, str]],
    *,
    runs: Mapping[str, int],
    exposure: int,
) -> str:
    """INVALID > NOT SUPPORTED > UNDETERMINED > SUPPORTED, no pooling of bars."""
    if any(n < MIN_RUNS for n in runs.values()) or sorted(bars_by_series) != sorted(
        REQUIRED_SERIES
    ):
        return "INVALID"
    statuses = [s for bars in bars_by_series.values() for s in bars.values()]
    if "fail" in statuses:
        return "NOT SUPPORTED"
    if any(bars["G"] == "vacuous" for bars in bars_by_series.values()):
        return "UNDETERMINED"
    if exposure < MIN_EXPOSURE_ROWS:
        return "UNDETERMINED"
    return "SUPPORTED"


# --- self-test -------------------------------------------------------------

_LINES: Final = [f"A: utterance number {i:02d} goes right here." for i in range(12)]
_TEXT: Final = "\n".join(_LINES)
_TARGET: Final = 130
"""Three of the synthetic 42-character lines per window: four windows."""


def _item(subject: str, line: int) -> decision_pass.DecisionItem:
    return decision_pass.DecisionItem(
        subject, f"we agreed about {subject} today", _LINES[line].split(": ", 1)[1]
    )


def _reply(*items: decision_pass.DecisionItem) -> str:
    return json.dumps(
        [
            {"subject": i.subject, "value": i.value, "evidence": i.evidence}
            for i in items
        ]
    )


class _Scripted:
    """Replies by a function of the prompt; records every prompt it saw."""

    def __init__(self, answer: Any) -> None:
        self.answer = answer
        self.seen: list[list[Message]] = []

    def chat(self, messages: Sequence[Message]) -> str:
        self.seen.append([dict(m) for m in messages])  # type: ignore[misc]
        return str(self.answer(len(self.seen), messages[-1]["content"]))


_CHOICES: Final = ("rubber", "kinetic", "orange", "titanium", "solar")


def _gold_rows() -> list[scoring.GoldRow]:
    rows: list[scoring.GoldRow] = []
    for series in REQUIRED_SERIES:
        for k in range(5):
            text = f"A: the group decided {_CHOICES[k]} for the remote casing"
            rows.append(
                scoring.GoldRow(
                    f"{series}a.d{k}",
                    f"{series}a",
                    "decision",
                    (scoring.act_tokens(text),),
                )
            )
    return rows


def _synth_record(
    arm: str,
    run: int,
    series: str,
    recalled: int,
    produced: int,
    *,
    calls: int = 12,
    failed: bool = False,
) -> dict[str, Any]:
    items = [
        {
            "title": f"t{k}",
            "evidence": [],
            "matched_ids": [f"{series}a.d{k}"] if k < recalled else [],
        }
        for k in range(produced)
    ]
    return {
        "arm": arm,
        "run": run,
        "meeting": f"{series}a",
        "series": series,
        "shaped": True,
        "windows": 4,
        "calls": calls,
        "failure": "x" if failed else None,
        "wall_s": 1.0,
        "items": items,
    }


def _synth_records(
    arm: str,
    runs: int,
    recalled: Sequence[int],
    produced: int,
    *,
    calls: int = 12,
    failed_runs: int = 0,
) -> list[dict[str, Any]]:
    return [
        _synth_record(
            arm,
            run,
            series,
            recalled[run % len(recalled)],
            produced,
            calls=calls,
            failed=run <= failed_runs,
        )
        for series in REQUIRED_SERIES
        for run in range(1, runs + 1)
    ]


def _series_runs(
    records: Sequence[Mapping[str, Any]],
    rows: Sequence[scoring.GoldRow],
    arm: str,
    series: str,
) -> list[dict[str, Any]]:
    return [m[series] for m in per_run_metrics(records, rows, arm, SHIPPED_CAP)]


def verify_gold(
    paths: Sequence[pathlib.Path], registered: Mapping[str, str] = GOLD_SHA256
) -> None:
    """Refuse a gold file whose name or SHA-256 is not the registered one."""
    for path in paths:
        want = registered.get(path.name)
        if want is None:
            raise SystemExit(f"REFUSED: {path.name} is not a registered gold file")
        got = hashlib.sha256(path.read_bytes()).hexdigest()
        if got != want:
            raise SystemExit(
                f"REFUSED: {path.name} SHA-256 {got} is not the registered {want}"
            )


def _self_test() -> int:
    failures: list[str] = []

    def check(label: str, got: object, want: object) -> None:
        if got != want:
            failures.append(f"{label}: got {got!r}, want {want!r}")

    def _dup_refused(recs: list[dict[str, Any]]) -> bool:
        try:
            harness.refuse_duplicate_records(recs)
        except SystemExit as exc:
            return "-PARTIAL" in str(exc) and "REFUSED" in str(exc)
        return False

    def _refused(call: Any) -> bool:
        try:
            call()
        except SystemExit:
            return True
        return False

    dup_set = [
        {"arm": "pass", "run": 1, "meeting": "ES2015a"},
        {"arm": "pass", "run": 2, "meeting": "ES2015a"},
    ]
    check(
        "a single record set passes the duplicate guard", _dup_refused(dup_set), False
    )
    check(
        "checkpoint plus final store is refused",
        _dup_refused(dup_set + dup_set),
        True,
    )

    base = prompt_budget.chunk_lines(_TEXT, _TARGET)
    check("synthetic transcript packs into four windows", len(base), 4)

    # --- windowing with overlap ---
    plain = build_windows(base, 0, OVERLAP_MAX_CHARS)
    check("no overlap reproduces the shipped windows", [w.text for w in plain], base)
    check(
        "no overlap owns every line it shows",
        [(w.start, w.own_start, w.end) for w in plain],
        [(0, 0, 3), (3, 3, 6), (6, 6, 9), (9, 9, 12)],
    )
    over = build_windows(base, 2, OVERLAP_MAX_CHARS)
    check("the first window never has a head", over[0].text, base[0])
    check(
        "later windows repeat the last two lines of the previous one",
        over[1].text,
        "\n".join(_LINES[1:6]),
    )
    check(
        "overlap spans: start, own_start, end",
        [(w.start, w.own_start, w.end) for w in over],
        [(0, 0, 3), (1, 3, 6), (4, 6, 9), (7, 9, 12)],
    )
    check(
        "the owned part of every window is the shipped window",
        ["\n".join(_LINES[w.own_start : w.end]) for w in over],
        base,
    )
    short = build_windows(base, 2, 50)
    check(
        "the character bound keeps whole lines only",
        short[1].text,
        "\n".join(_LINES[2:6]),
    )
    check(
        "overlap larger than the window repeats it whole",
        len(build_windows(base, 9, 800)[1].text.split("\n")),
        6,
    )

    # --- locating evidence ---
    check(
        "locate finds the owning line",
        locate_line(_LINES, "utterance number 04 goes right here.", 1, 6),
        4,
    )
    check(
        "locate stays inside the span",
        locate_line(_LINES, "utterance number 04 goes right here.", 5, 6),
        None,
    )
    check(
        "locate refuses multi-line evidence",
        locate_line(_LINES, "number 04 goes right here. A: utterance number 05", 0, 12),
        None,
    )

    # --- overlap dedup by evidence position ---
    def adm(subject: str, window: int, pos: int | None) -> Admitted:
        return Admitted(_item(subject, pos if pos is not None else 0), window, pos)

    entries = [
        adm("casing", 0, 2),
        adm("casing again", 1, 2),
        adm("missed before", 1, 1),
        adm("own zone", 1, 4),
        adm("unlocated", 1, None),
        adm("shared one", 1, 2),
    ]
    kept = [e.item.subject for e in dedup_overlap(entries, over)]
    check(
        "an overlap item the previous window already produced is dropped, one for one",
        kept,
        ["casing", "missed before", "own zone", "unlocated", "shared one"],
    )
    check(
        "window 0 is never dropped",
        len(dedup_overlap([adm("a", 0, 1), adm("b", 0, 1)], over)),
        2,
    )
    check(
        "a head item the previous window missed survives",
        [e.item.subject for e in dedup_overlap([adm("late", 2, 4)], over)],
        ["late"],
    )
    check(
        "an item in its own zone is never dropped",
        len(dedup_overlap([adm("a", 1, 3), adm("b", 1, 3)], over)),
        2,
    )

    # --- rolling subjects ---
    items = [
        _item("Battery", 1),
        _item("Casing", 2),
        _item("battery", 3),
        _item("Colour", 4),
    ]
    check(
        "rolling keeps distinct subjects, most recent, oldest first",
        rolling_subjects(items, 2, 80),
        ["battery", "Colour"],
    )
    check(
        "rolling cuts each subject",
        rolling_subjects(items, 9, 4),
        ["Casi", "batt", "Colo"],
    )
    check("rolling over nothing is empty", rolling_subjects([], 3, 80), [])
    check("a bound of zero shows nothing", rolling_subjects(items, 0, 80), [])
    messages: list[Message] = [
        {"role": "system", "content": "S"},
        {"role": "user", "content": "U"},
    ]
    check(
        "no subjects leave the prompt untouched",
        with_known_subjects(messages, []),
        messages,
    )
    injected = with_known_subjects(messages, ["Battery", "Casing"])
    check("the system message is untouched", injected[0], messages[0])
    check(
        "the user message gets the header and the subjects",
        injected[1]["content"],
        "U\n\n" + ROLLING_HEADER + "\n- Battery\n- Casing",
    )
    check("the input messages are not mutated", messages[1]["content"], "U")
    check(
        "the rolling header is pinned", prompt_hash(ROLLING_HEADER), ROLLING_HEADER_HASH
    )

    # --- the pass loop is the shipped pass when no treatment is on ---
    def answer(call: int, prompt: str) -> str:
        first = "utterance number 00" in prompt and "utterance number 01" in prompt
        if first and "utterance number 03" not in prompt:
            return _reply(_item("casing", 2))
        if "utterance number 04" in prompt and "utterance number 05" in prompt:
            return _reply(_item("casing choice", 2), _item("buttons", 4))
        return "[]"

    shipped_llm = _Scripted(answer)
    outcome = run_windows(plain, InjectingLLM(shipped_llm), _LINES, rolling=False)
    final = finalize(outcome, plain, overlap=False)
    production = decision_pass.run_decision_pass(base, _Scripted(answer))
    check(
        "control arm equals run_decision_pass, titles",
        [decision_pass.compose_title(e.item) for e in final][:SHIPPED_CAP],
        [r.title for r in production.results],
    )
    check("control arm windows", outcome.windows, production.windows)
    check(
        "control arm prompts are the shipped prompts",
        [m[1]["content"] for m in shipped_llm.seen],
        [f"TRANSCRIPT WINDOW:\n{w}" for w in base],
    )
    flaky = _Scripted(lambda call, prompt: "garbage" if call == 1 else "[]")
    again = run_windows(plain, InjectingLLM(flaky), _LINES, rolling=False)
    check("an unparseable window is asked once more", len(flaky.seen), 5)
    check("and then no failure is reported", again.failure, None)
    dead = _Scripted(lambda call, prompt: "garbage")
    failed = run_windows(plain, InjectingLLM(dead), _LINES, rolling=False)
    check(
        "two bad replies fail the window",
        failed.failure,
        "decision_pass: unparseable reply",
    )

    # --- overlap through the loop ---
    over_llm = _Scripted(answer)
    over_outcome = run_windows(over, InjectingLLM(over_llm), _LINES, rolling=False)
    check("overlap asks one call per window", len(over_llm.seen), 4)
    check(
        "overlap prompts carry the head",
        "utterance number 01" in over_llm.seen[1][1]["content"],
        True,
    )
    with_dedup = finalize(over_outcome, over, overlap=True)
    check(
        "overlap dedup removes the restatement of an overlap line",
        [e.item.subject for e in with_dedup],
        ["casing", "buttons"],
    )
    check(
        "without the dedup the restatement is a third item",
        len(decision_pass.dedup_by_subject([e.item for e in over_outcome.admitted])),
        3,
    )

    # --- rolling through the loop ---
    roll_llm = _Scripted(
        lambda call, prompt: _reply(_item("casing", 2)) if call == 1 else "[]"
    )
    roll_inj = InjectingLLM(roll_llm)
    run_windows(plain, roll_inj, _LINES, rolling=True)
    check(
        "the first window sees no subjects",
        ROLLING_HEADER in roll_llm.seen[0][1]["content"],
        False,
    )
    check(
        "the next window sees the earlier subject",
        "- casing" in roll_llm.seen[1][1]["content"],
        True,
    )
    check("the list is reset afterwards", roll_inj.known, [])
    off_llm = _Scripted(
        lambda call, prompt: _reply(_item("casing", 2)) if call == 1 else "[]"
    )
    run_windows(plain, InjectingLLM(off_llm), _LINES, rolling=False)
    check(
        "rolling off never shows a subject",
        any(ROLLING_HEADER in m[1]["content"] for m in off_llm.seen),
        False,
    )

    # --- records and scoring ---
    rows = _gold_rows()
    es_text = "A: the group decided kinetic for the remote casing"
    es_item = decision_pass.DecisionItem("option", "the option is number one", es_text)
    record = make_record(
        ARM_OVERLAP,
        3,
        "ES2015a",
        [r for r in rows if r.meeting_id == "ES2015a"],
        PassOutcome([], None, 4),
        [Admitted(es_item, 1, 7)],
        calls=5,
        wall_s=1.234,
    )
    check(
        "make_record matches the gold row",
        record["items"][0]["matched_ids"],
        ["ES2015a.d1"],
    )
    check(
        "make_record keeps the position",
        (record["items"][0]["window"], record["items"][0]["pos"]),
        (1, 7),
    )
    check(
        "make_record names the series",
        (record["series"], record["run"], record["calls"]),
        ("ES2015", 3, 5),
    )
    check(
        "capped keeps the first items",
        len(capped([_synth_record("a", 1, "ES2015", 3, 9)], 4)[0]["items"]),
        4,
    )
    check(
        "capped does not mutate", len(_synth_record("a", 1, "ES2015", 3, 9)["items"]), 9
    )
    check(
        "strict precision counts every produced item",
        strict_precision({"recalled": 3, "produced": 6}),
        0.5,
    )
    check(
        "strict precision of nothing is undefined",
        strict_precision({"recalled": 0, "produced": 0}),
        None,
    )

    # --- exposure ---
    expo_rows = [
        scoring.GoldRow("m.a", "ES2015a", "decision", (scoring.act_tokens(_LINES[3]),)),
        scoring.GoldRow("m.b", "ES2015a", "decision", (scoring.act_tokens(_LINES[4]),)),
        scoring.GoldRow("m.c", "ES2015a", "decision", (scoring.act_tokens(_LINES[0]),)),
        scoring.GoldRow(
            "m.d", "ES2015a", "no_decision", (scoring.act_tokens(_LINES[6]),)
        ),
    ]
    check(
        "exposure counts decision rows at a window head",
        exposed_rows(base, expo_rows, 1),
        1,
    )
    check("a longer head exposes more", exposed_rows(base, expo_rows, 2), 2)
    check("the first window is never exposed", exposed_rows(base[:1], expo_rows, 3), 0)

    # --- statistics ---
    check("z of identical constants is zero", z_score([2, 2], [2, 2]), 0.0)
    check("z of separated constants is infinite", z_score([4, 4], [2, 2]), math.inf)
    check("z needs two runs", z_score([4], [2, 2]), None)
    z = z_score([4, 5, 4, 5], [2, 3, 2, 3])
    check("z is the difference over its standard error", round(z or 0, 3), 4.899)

    # --- bars ---
    def bars(
        c_rec: Sequence[int],
        c_prod: int,
        s_rec: Sequence[int] = (2, 3),
        s_prod: int = 8,
        **kw: Any,
    ) -> dict[str, str]:
        control = _synth_records(ARM_SHIPPED, 15, s_rec, s_prod)
        cand = _synth_records(ARM_OVERLAP, 15, c_rec, c_prod, **kw)
        allr = control + cand
        return evaluate_series(
            _series_runs(allr, rows, ARM_SHIPPED, "ES2015"),
            _series_runs(allr, rows, ARM_OVERLAP, "ES2015"),
        )

    good = bars((4, 5), 8)
    check(
        "a recall gain passes every bar",
        good,
        {"K": "pass", "G": "pass", "V": "pass", "P": "pass", "F": "pass", "C": "pass"},
    )
    check("losing two rows fails K", bars((1, 1), 8)["K"], "fail")
    check("losing 0.5 row keeps K", bars((2, 2), 8)["K"], "pass")
    check("no gain fails G", bars((2, 3), 8)["G"], "fail")
    check("a precision gain at equal recall passes G", bars((2, 3), 4)["G"], "pass")
    check("a recall gain of under one row fails G", bars((3, 3), 8)["G"], "fail")
    check("volume 1.375x fails V", bars((4, 5), 11)["V"], "fail")
    check("volume 1.25x keeps V", bars((4, 5), 10)["V"], "pass")
    check("calls 1.33x fail C", bars((4, 5), 8, calls=16)["C"], "fail")
    check("calls 1.15x keep C", bars((4, 5), 8, calls=13)["C"], "pass")
    check(
        "window failures in 3 of 15 runs fail F",
        bars((4, 5), 8, failed_runs=3)["F"],
        "fail",
    )
    check("window failures in 0 runs keep F", bars((4, 5), 8)["F"], "pass")
    check(
        "a control that recalls nothing makes G vacuous",
        bars((4, 5), 8, s_rec=(0,))["G"],
        "vacuous",
    )

    # --- verdict and report ---
    ok = {s: {"K": "pass", "G": "pass", "P": "vacuous"} for s in REQUIRED_SERIES}
    runs15 = {ARM_SHIPPED: 15, ARM_OVERLAP: 15}
    check(
        "every bar passing is SUPPORTED",
        verdict(ok, runs=runs15, exposure=6),
        "SUPPORTED",
    )
    check(
        "too little exposure is UNDETERMINED",
        verdict(ok, runs=runs15, exposure=5),
        "UNDETERMINED",
    )
    vac = {**ok, "ES2015": {"K": "pass", "G": "vacuous"}}
    check(
        "a vacuous G is UNDETERMINED",
        verdict(vac, runs=runs15, exposure=9),
        "UNDETERMINED",
    )
    bad = {**ok, "IS1004": {"K": "fail", "G": "pass"}}
    check(
        "a failure on any series is NOT SUPPORTED",
        verdict(bad, runs=runs15, exposure=9),
        "NOT SUPPORTED",
    )
    check(
        "a failure outranks a vacuous bar",
        verdict({**bad, "ES2015": {"G": "vacuous"}}, runs=runs15, exposure=0),
        "NOT SUPPORTED",
    )
    check(
        "fewer than 15 runs is INVALID",
        verdict(ok, runs={ARM_SHIPPED: 15, ARM_OVERLAP: 14}, exposure=9),
        "INVALID",
    )
    check(
        "a missing series is INVALID",
        verdict({"ES2015": ok["ES2015"]}, runs=runs15, exposure=9),
        "INVALID",
    )

    meetings = [f"{s}a" for s in REQUIRED_SERIES]
    recs = (
        _synth_records(ARM_SHIPPED, 15, (2, 3), 8)
        + _synth_records(ARM_OVERLAP, 15, (4, 5), 8)
        + _synth_records(ARM_ROLLING, 15, (2, 3), 8)
    )
    check("complete runs are counted", complete_runs(recs, ARM_OVERLAP, meetings), 15)
    check(
        "a missing meeting-run is not complete",
        complete_runs(recs[:-1], ARM_ROLLING, meetings),
        14,
    )
    result = build_verdicts(recs, rows, meetings, exposure=9)
    check(
        "a better candidate is SUPPORTED", result["verdicts"][ARM_OVERLAP], "SUPPORTED"
    )
    check(
        "an unchanged candidate is NOT SUPPORTED",
        result["verdicts"][ARM_ROLLING],
        "NOT SUPPORTED",
    )
    short_run = build_verdicts(recs[:-1], rows, meetings, exposure=9)
    check("an incomplete arm is INVALID", short_run["verdicts"][ARM_ROLLING], "INVALID")
    text = render_report(recs, rows, meetings, exposure=9)
    check("the report states the verdict", f"{ARM_OVERLAP}: SUPPORTED" in text, True)
    check(
        "the report states the other verdict",
        f"{ARM_ROLLING}: NOT SUPPORTED" in text,
        True,
    )

    # --- registered constants ---
    check("the floor is fifteen runs", MIN_RUNS, 15)
    check(
        "the shipped cap is the production cap",
        SHIPPED_CAP,
        decision_pass.MAX_PASS_DECISIONS,
    )
    check(
        "the candidates are the two single treatments",
        CANDIDATES,
        (ARM_OVERLAP, ARM_ROLLING),
    )

    with tempfile.TemporaryDirectory() as tmp:
        gold_path = pathlib.Path(tmp) / "ES2015.decisions.adjudicated.csv"
        gold_path.write_bytes(b"decision_id\nd1\n")
        good_digest = hashlib.sha256(gold_path.read_bytes()).hexdigest()
        check(
            "a gold file whose digest matches is accepted",
            _refused(lambda: verify_gold([gold_path], {gold_path.name: good_digest})),
            False,
        )
        check(
            "a gold file whose digest differs is refused",
            _refused(lambda: verify_gold([gold_path], {gold_path.name: "0" * 64})),
            True,
        )
        other = pathlib.Path(tmp) / "XX0000.decisions.adjudicated.csv"
        other.write_bytes(b"x")
        check(
            "an unregistered gold file name is refused",
            _refused(lambda: verify_gold([other], {gold_path.name: good_digest})),
            True,
        )
        check(
            "the registered digests cover exactly the required series",
            sorted(GOLD_SHA256),
            sorted(f"{series}.decisions.adjudicated.csv" for series in REQUIRED_SERIES),
        )

    if failures:
        print(f"SELF-TEST FAILED ({len(failures)}):")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("self-test ok")
    return 0


# --- running, verdicts, report ---------------------------------------------


def complete_runs(
    records: Sequence[Mapping[str, Any]], arm: str, meetings: Sequence[str]
) -> int:
    """Runs of `arm` that hold a record for every meeting."""
    seen: dict[int, set[str]] = defaultdict(set)
    for record in records:
        if record["arm"] == arm:
            seen[record["run"]].add(record["meeting"])
    return sum(1 for got in seen.values() if got == set(meetings))


def build_verdicts(
    records: Sequence[Mapping[str, Any]],
    rows: Sequence[scoring.GoldRow],
    meetings: Sequence[str],
    exposure: int,
) -> dict[str, Any]:
    """Bars and verdict of each candidate against `shipped`, from stored records."""
    metrics = {
        arm: per_run_metrics(records, rows, arm, SHIPPED_CAP)
        for arm in ARMS
        if any(r["arm"] == arm for r in records)
    }
    runs = {arm: complete_runs(records, arm, meetings) for arm in metrics}
    bars: dict[str, dict[str, dict[str, str]]] = {}
    verdicts: dict[str, str] = {}
    for cand in CANDIDATES:
        if cand not in metrics or ARM_SHIPPED not in metrics:
            verdicts[cand] = "INVALID"
            continue
        bars[cand] = {}
        for series in REQUIRED_SERIES:
            control = [m[series] for m in metrics[ARM_SHIPPED] if series in m]
            candidate = [m[series] for m in metrics[cand] if series in m]
            if control and candidate:
                bars[cand][series] = evaluate_series(control, candidate)
        verdicts[cand] = verdict(
            bars[cand],
            runs={ARM_SHIPPED: runs[ARM_SHIPPED], cand: runs[cand]},
            exposure=exposure,
        )
    return {"bars": bars, "verdicts": verdicts, "runs": runs}


def _fmt(values: Sequence[float]) -> str:
    if not values:
        return "n/a"
    return f"{_mean(values):.3f} ({min(values):.3f}-{max(values):.3f}, n={len(values)})"


def render_report(
    records: Sequence[Mapping[str, Any]],
    rows: Sequence[scoring.GoldRow],
    meetings: Sequence[str],
    exposure: int,
) -> str:
    """Per arm and series: recalled, produced, strict precision, calls; then the
    bars and verdicts. Read at the shipped cap; the stored items are uncapped."""
    result = build_verdicts(records, rows, meetings, exposure)
    out = [
        f"boundary exposure: {exposure} gold decision rows (floor {MIN_EXPOSURE_ROWS})"
    ]
    for arm in ARMS:
        runs = per_run_metrics(records, rows, arm, SHIPPED_CAP)
        if not runs:
            continue
        out.append(f"{arm}: {result['runs'][arm]} complete run(s)")
        for series in REQUIRED_SERIES:
            m = [r[series] for r in runs if series in r]
            if not m:
                continue
            precision = [p for r in m if (p := strict_precision(r)) is not None]
            out.append(
                f"  {series}: recalled {_fmt(_values(m, 'recalled'))} of "
                f"{m[0]['decision_rows']}; produced {_fmt(_values(m, 'produced'))}; "
                f"strict precision {_fmt(precision)}; calls {_fmt(_values(m, 'calls'))}"
            )
    for cand, per_series in result["bars"].items():
        for series, bars in per_series.items():
            out.append(
                f"bars {cand} {series}: "
                + ", ".join(f"{k}={v}" for k, v in sorted(bars.items()))
            )
    for cand in CANDIDATES:
        out.append(f"{cand}: {result['verdicts'][cand]}")
    return "\n".join(out)


def run_meeting_arm(
    arm: str,
    meeting: str,
    text: str,
    rows: Sequence[scoring.GoldRow],
    timed: TimedBackend,
    run: int,
) -> dict[str, Any]:
    """One arm on one meeting: the JSON-safe record to persist."""
    base = harness.pass_windows(text, meeting)
    overlap = arm in OVERLAP_ARMS
    windows = build_windows(base, OVERLAP_LINES if overlap else 0, OVERLAP_MAX_CHARS)
    calls_before = len(timed.call_latencies_s)
    started = time.monotonic()
    outcome = run_windows(
        windows,
        InjectingLLM(timed),
        text.split("\n"),
        rolling=arm in ROLLING_ARMS,
    )
    final = finalize(outcome, windows, overlap=overlap)
    return make_record(
        arm,
        run,
        meeting,
        rows,
        outcome,
        final,
        calls=len(timed.call_latencies_s) - calls_before,
        wall_s=time.monotonic() - started,
    )


def exposure_of(texts: Mapping[str, str], rows: Sequence[scoring.GoldRow]) -> int:
    """Gold `decision` rows at a window head, pooled over the meetings."""
    return sum(
        exposed_rows(
            harness.pass_windows(text, meeting),
            [r for r in rows if r.meeting_id == meeting],
            OVERLAP_LINES,
        )
        for meeting, text in texts.items()
    )


def write_results(
    records: list[dict[str, Any]],
    *,
    arms: Sequence[str],
    model: str,
    runs: int,
    partial: bool,
    stamp: dict[str, Any],
    directory: pathlib.Path,
) -> pathlib.Path:
    directory.mkdir(parents=True, exist_ok=True)
    now = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    suffix = "-PARTIAL" if partial else ""
    path = directory / f"windows-{now}-{model.replace(':', '-')}{suffix}.json"
    path.write_text(
        json.dumps(
            {
                "arms": list(arms),
                "model": model,
                "runs": runs,
                "partial": partial,
                "max_generation_tokens": DEFAULT_MAX_GENERATION_TOKENS,
                "context_window": DEFAULT_CONTEXT_WINDOW,
                "overlap_lines": OVERLAP_LINES,
                "overlap_max_chars": OVERLAP_MAX_CHARS,
                "rolling_max_subjects": ROLLING_MAX_SUBJECTS,
                "stamp": stamp,
                "records": records,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--gold", action="append", type=pathlib.Path)
    parser.add_argument("--transcripts", type=pathlib.Path)
    parser.add_argument("--runs", type=int, default=MIN_RUNS)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--arms", default=",".join(DEFAULT_ARMS))
    parser.add_argument("--results-dir", type=pathlib.Path, default=RESULTS_DIR)
    parser.add_argument("--exposure", action="store_true")
    parser.add_argument("--report", nargs="+", type=pathlib.Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()
    if not args.gold:
        parser.error("--gold is required unless --self-test")
    verify_gold(args.gold)
    rows, meetings = harness.prepare(args.gold, allow_partial=False)
    if args.report:
        records = harness.load_records(args.report)
        exposure = 0
        if args.transcripts is not None:
            exposure = exposure_of(
                harness.load_transcripts(args.transcripts, meetings), rows
            )
        print(render_report(records, rows, meetings, exposure))
        return 0
    if args.transcripts is None:
        parser.error("--transcripts is required to run or to count exposure")
    texts = harness.load_transcripts(args.transcripts, meetings)
    unshaped = harness.check_gate(texts)
    if unshaped:
        raise SystemExit(
            f"REFUSED: _is_meeting_shaped is FALSE for {', '.join(unshaped)}: the "
            "pass would not fire in production, so a run would measure a false zero."
        )
    if args.exposure:
        print(f"boundary exposure: {exposure_of(texts, rows)} gold decision rows")
        return 0
    arms = tuple(a for a in args.arms.split(",") if a)
    if ARM_SHIPPED not in arms or any(a not in ARMS for a in arms):
        parser.error(f"--arms must include {ARM_SHIPPED} and come from {ARMS}")
    if args.runs < MIN_RUNS:
        raise SystemExit(f"REFUSED: --runs {args.runs} is below the floor {MIN_RUNS}")
    timed = TimedBackend(
        OllamaClient(
            model=args.model,
            max_generation_tokens=DEFAULT_MAX_GENERATION_TOKENS,
            context_window=DEFAULT_CONTEXT_WINDOW,
        )
    )
    stamp = build_stamp(
        model=args.model,
        prompts={
            "extraction/decision_pass": decision_pass._SYSTEM_PROMPT,
            "decision_windows/rolling_header": ROLLING_HEADER,
        },
    )
    records = []
    for run in range(1, args.runs + 1):
        for meeting, text in texts.items():
            meeting_rows = [r for r in rows if r.meeting_id == meeting]
            for arm in arms:
                record = run_meeting_arm(arm, meeting, text, meeting_rows, timed, run)
                records.append(record)
                print(
                    f"    {arm} run {run} {meeting}: items={len(record['items'])} "
                    f"windows={record['windows']} calls={record['calls']} "
                    f"failure={record['failure']} {record['wall_s']}s"
                )
        path = write_results(
            records,
            arms=arms,
            model=args.model,
            runs=args.runs,
            partial=run < args.runs,
            stamp=stamp,
            directory=args.results_dir,
        )
        print(f"stored {path}")
    print(render_report(records, rows, meetings, exposure_of(texts, rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
