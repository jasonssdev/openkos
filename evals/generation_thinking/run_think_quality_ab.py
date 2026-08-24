"""Does `think=false` cost extraction quality? The gate #830's refutation named.

`evals/generation_thinking/` established where a runaway goes: under the
shipped `think` default a cut-off carries THOUSANDS of characters of
deliberation and ZERO characters of answer, so the paid call produces
nothing -- while the same cut-off under `think=false` still returned usable
content. It also said, explicitly, that `think=false` is NOT a
recommendation until its extraction-quality cost is measured. This probe is
that measurement.

## The arms

Both arms run the FULL shipped pipeline -- `extract_concept_union`, judge
and participant capture included -- through a real `OllamaClient` at the
shipped `DEFAULT_MAX_GENERATION_TOKENS`/`DEFAULT_CONTEXT_WINDOW`, on the
same committed public fixtures the sibling probe used:

- **think** -- the untouched request every shipped call sends today.
- **no-think** -- byte-identical except `"think": false` injected into each
  chat body by a transport wrapper (`OllamaClient` takes `urlopen` as a
  constructor argument, so no production code is touched and no monkeypatch
  is involved).

Arms and fixtures are INTERLEAVED run by run, for the sibling probe's
reason: a sweep this long drifts, and the latency column is the measurement
most exposed to it.

## Scoring, and why anchors

A run's quality is the number of ANCHOR SUBJECTS its returned objects
recover, over a fixed per-fixture anchor list committed below. Anchors are
high-confidence subjects only (the platform, the datastore, the named
people) -- not everything a run could produce -- because the question is
whether `think=false` LOSES real subjects, and both arms are scored against
the IDENTICAL list, so the list's coverage affects sensitivity, never
direction. A pipeline run that raises (`OllamaGenerationCapped` included)
recovers zero anchors and is recorded with its exception class: a call that
produces nothing IS the quality cost #830 measured, not a row to discard.

## The pre-registered bar (decided before the first call)

`think=false` SHIPS for extraction only if, on this sweep:

1. for EVERY fixture, its median anchors-recovered is >= the `think` arm's
   median on that fixture, and
2. its pooled failed-run count is <= the `think` arm's.

Anything else is KEEP `think` -- and closes #830 with both proposed
remedies measured and refuted. The bar compares medians, not means or
maxima, because a 15-run arm still swings (a 5-run arm was measured
swinging 0.25 against itself on another harness, which is why `--runs`
defaults to 15 here).

Usage:

    uv run python -u evals/generation_thinking/run_think_quality_ab.py --self-test
    uv run python -u evals/generation_thinking/run_think_quality_ab.py --runs 15
    uv run python -u evals/generation_thinking/run_think_quality_ab.py \
        --rescore evals/generation_thinking/results/<file>.json

`--self-test` and `--rescore` make no model calls and need no Ollama.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import statistics
import sys
import time
import urllib.request
from dataclasses import asdict, dataclass, fields
from typing import Any, Final

_HERE: Final = pathlib.Path(__file__).resolve().parent
_REPO: Final = _HERE.parent.parent
_FIXTURES: Final = _REPO / "evals" / "section_coverage" / "section_fixtures.py"
RESULTS_DIR: Final = _HERE / "results"

sys.path.insert(0, str(_REPO / "src"))

from openkos.config import (  # noqa: E402
    DEFAULT_CONTEXT_WINDOW,
    DEFAULT_MAX_GENERATION_TOKENS,
)
from openkos.extraction.concept import extract_concept_union  # noqa: E402
from openkos.llm.ollama import OllamaClient, OllamaError  # noqa: E402


def _load(path: pathlib.Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise SystemExit(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_fixtures = _load(_FIXTURES, "_section_fixtures_quality_ab")

ARMS: Final = ("think", "no-think")

ANCHORS: Final[dict[str, tuple[str, ...]]] = {
    "helios-overview": ("helios", "mysql", "marta", "becker"),
    "kickoff": ("postgres", "marta", "becker", "priya", "helios"),
}
"""Casefolded substrings an object TITLE must carry to recover the anchor.

High-confidence subjects only: the platform, the datastore each fixture
declares, and the people it names. Deliberately NOT the full plausible
object set (`## Components`' three bullets vary run to run on both arms),
because a noisy anchor adds variance to both arms equally while diluting
the signal. Substring-on-title rather than equality because the same
subject legitimately surfaces as `MySQL 8`, `MySQL 8 Datastore`, or
`Decisión sobre MySQL`, and the arms must not be separated by a spelling."""


@dataclass(frozen=True)
class QualityRecord:
    """One full pipeline run, scored."""

    fixture: str
    arm: str
    run: int
    model: str
    objects: int
    """Objects the pipeline returned (0 for a failed run)."""
    anchors_hit: int
    anchors_total: int
    titles: str
    """The returned `type: title` list, `" | "`-joined -- committed so a
    later reader can re-adjudicate an anchor call without re-running the
    sweep. Empty for a failed run."""
    failed: str
    """Empty for a completed run, else the exception class name."""
    seconds: float


_FIELD_NAMES: Final = tuple(field.name for field in fields(QualityRecord))

_FIELD_KINDS: Final[dict[str, tuple[type, ...]]] = {
    field.name: (str,)
    if field.type == "str"
    else (int, float)
    if field.type == "float"
    else (int,)
    for field in fields(QualityRecord)
}


def _parse_records(value: object) -> list[QualityRecord]:
    """The rescore path's shape gate, mirroring the sibling probe's: a wrong
    file deserves a sentence naming the row, never a KeyError three frames
    deep."""
    if not isinstance(value, list):
        raise SystemExit(
            f"rescore file must hold a JSON array of run rows, got "
            f"{type(value).__name__}"
        )
    records: list[QualityRecord] = []
    for index, row in enumerate(value):
        if not isinstance(row, dict):
            raise SystemExit(
                f"rescore row {index} is {type(row).__name__}, not an object"
            )
        missing = [name for name in _FIELD_NAMES if name not in row]
        extra = sorted(set(row) - set(_FIELD_NAMES))
        if missing or extra:
            raise SystemExit(
                f"rescore row {index} does not match this probe's schema: "
                f"missing {missing or 'nothing'}, unexpected {extra or 'nothing'}"
            )
        for name in _FIELD_NAMES:
            cell = row[name]
            # `bool` subclasses `int` -- the same refusal every counter
            # reader in this repo applies.
            if isinstance(cell, bool) or not isinstance(cell, _FIELD_KINDS[name]):
                kinds = " or ".join(k.__name__ for k in _FIELD_KINDS[name])
                raise SystemExit(
                    f"rescore row {index}: field {name!r} holds "
                    f"{type(cell).__name__} {cell!r}, not {kinds}"
                )
        records.append(QualityRecord(**row))
    return records


def score_anchors(titles: list[str], anchors: tuple[str, ...]) -> int:
    """How many DISTINCT anchors the titles recover.

    Distinct by anchor, not by title: two objects both naming `marta`
    recover her once. Casefolded substring containment, per `ANCHORS`'
    rationale."""
    lowered = [title.casefold() for title in titles]
    return sum(1 for anchor in anchors if any(anchor in title for title in lowered))


class ThinkInjectingUrlopen:
    """A `urlopen` wrapper that adds `"think": false` to every chat body.

    `OllamaClient` takes `urlopen` as a constructor argument, so this is the
    same no-monkeypatch seam `evals/generation_ceiling/`'s recorder uses.
    Only a body carrying `"messages"` is a chat call; an embed body passes
    through untouched, byte for byte. Everything else about the request --
    URL, headers, method, timeout -- is preserved.
    """

    def __init__(self, urlopen: Any = urllib.request.urlopen) -> None:
        self._urlopen = urlopen

    def rewrite(self, request: urllib.request.Request) -> urllib.request.Request:
        data = request.data
        if not isinstance(data, (bytes, bytearray, str)):
            # None, or a streaming body this probe never sends -- pass it
            # through rather than consuming an iterator it cannot restore.
            return request
        try:
            body = json.loads(data)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return request
        if not isinstance(body, dict) or "messages" not in body:
            return request
        body["think"] = False
        # Drop any Content-Length among the copied headers: the body just
        # grew, and a stale length truncates it into a 400 at the server.
        # `urlopen` recomputes the correct one at send time.
        headers = {
            name: value
            for name, value in request.header_items()
            if name.lower() != "content-length"
        }
        # The URL is the incoming request's own -- `OllamaClient`'s
        # configured host, never document content (same trusted-host
        # rationale its call site records).
        return urllib.request.Request(  # noqa: S310
            request.full_url,
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
        )

    def __call__(self, request: Any, timeout: float | None = None) -> Any:
        return self._urlopen(self.rewrite(request), timeout=timeout)


def run_once(
    fixture: Any, *, arm: str, run: int, model: str, host: str
) -> QualityRecord:
    """One full shipped-pipeline extraction, scored against the anchors."""
    urlopen = ThinkInjectingUrlopen() if arm == "no-think" else urllib.request.urlopen
    client = OllamaClient(
        model,
        host=host,
        max_generation_tokens=DEFAULT_MAX_GENERATION_TOKENS,
        context_window=DEFAULT_CONTEXT_WINDOW,
        urlopen=urlopen,
    )
    anchors = ANCHORS[fixture.name]
    started = time.monotonic()
    try:
        outcome = extract_concept_union(
            fixture.text, source_title=fixture.title, llm=client
        )
    except OllamaError as exc:
        return QualityRecord(
            fixture=fixture.name,
            arm=arm,
            run=run,
            model=model,
            objects=0,
            anchors_hit=0,
            anchors_total=len(anchors),
            titles="",
            failed=type(exc).__name__,
            seconds=round(time.monotonic() - started, 1),
        )
    titles = [obj.title for obj in outcome.objects]
    return QualityRecord(
        fixture=fixture.name,
        arm=arm,
        run=run,
        model=model,
        objects=len(outcome.objects),
        anchors_hit=score_anchors(titles, anchors),
        anchors_total=len(anchors),
        titles=" | ".join(f"{obj.type}: {obj.title}" for obj in outcome.objects),
        failed="",
        seconds=round(time.monotonic() - started, 1),
    )


def _rows(records: list[QualityRecord], arm: str, fixture: str) -> list[QualityRecord]:
    return [r for r in records if r.arm == arm and r.fixture == fixture]


def verdict(records: list[QualityRecord]) -> str:
    """The pre-registered bar, applied. SHIP, KEEP, or NO DATA -- with the
    failing clause named, because a verdict a reader cannot check against
    the table above it is an assertion, not a result."""
    fixtures = sorted({r.fixture for r in records})
    if not fixtures or not all(
        _rows(records, arm, fixture) for arm in ARMS for fixture in fixtures
    ):
        return (
            "NO DATA -- every fixture needs BOTH arms measured before the "
            "bar can be applied."
        )
    reasons: list[str] = []
    for fixture in fixtures:
        med = {
            arm: statistics.median(r.anchors_hit for r in _rows(records, arm, fixture))
            for arm in ARMS
        }
        if med["no-think"] < med["think"]:
            reasons.append(
                f"median anchors on {fixture}: no-think {med['no-think']:g} "
                f"< think {med['think']:g}"
            )
    failures = {
        arm: sum(1 for r in records if r.arm == arm and r.failed) for arm in ARMS
    }
    if failures["no-think"] > failures["think"]:
        reasons.append(
            f"failed runs: no-think {failures['no-think']} > think {failures['think']}"
        )
    if reasons:
        return "KEEP think -- " + "; ".join(reasons) + "."
    return (
        "SHIP no-think for extraction -- no fixture's median anchor recall "
        "is worse and failed runs did not increase, per the pre-registered "
        "bar."
    )


def render(records: list[QualityRecord]) -> str:
    lines: list[str] = [
        "",
        "=" * 78,
        "DOES think=false COST EXTRACTION QUALITY? (#830)",
        "=" * 78,
        "",
    ]
    if not records:
        lines.append("NO DATA -- no run to report.")
        return "\n".join(lines)
    fixtures = sorted({r.fixture for r in records})
    lines.append(
        f"   {'arm':<10}{'fixture':<18}{'n':>3}{'failed':>8}{'med hit':>9}"
        f"{'min':>5}{'max':>5}{'med objs':>10}{'med s':>8}"
    )
    for arm in ARMS:
        for fixture in fixtures:
            rows = _rows(records, arm, fixture)
            if not rows:
                continue
            hits = [r.anchors_hit for r in rows]
            lines.append(
                f"   {arm:<10}{fixture:<18}{len(rows):>3}"
                f"{sum(1 for r in rows if r.failed):>8}"
                f"{statistics.median(hits):>9g}{min(hits):>5}{max(hits):>5}"
                f"{statistics.median(r.objects for r in rows):>10g}"
                f"{statistics.median(r.seconds for r in rows):>8.1f}"
            )
    lines += [
        "",
        "   `med hit` is the median count of anchor subjects recovered, of "
        f"{ {name: len(a) for name, a in sorted(ANCHORS.items())} }.",
        "   A failed run scores 0 recovered and is named in its row's "
        "stored `failed` field.",
        "",
        f"VERDICT: {verdict(records)}",
    ]
    return "\n".join(lines)


def _self_test() -> int:
    failures: list[str] = []

    def check(condition: bool, why: str) -> None:
        if not condition:
            failures.append(why)

    # Anchor scoring: casefold, substring, distinct-by-anchor.
    check(
        score_anchors(["Decisión sobre MySQL 8", "MARTA Ruiz"], ("mysql", "marta"))
        == 2,
        "anchor scoring must casefold and match substrings",
    )
    check(
        score_anchors(["Marta Ruiz", "Marta again"], ("marta",)) == 1,
        "two titles naming one anchor must recover it once",
    )
    check(
        score_anchors([], ("marta",)) == 0,
        "a failed run's empty title list must score zero",
    )

    # The injector: think added, other bytes preserved, non-chat untouched.
    injector = ThinkInjectingUrlopen()
    chat = urllib.request.Request(
        "http://127.0.0.1:11434/api/chat",
        data=json.dumps({"model": "m", "messages": [], "options": {"a": 1}}).encode(),
        headers={"Content-Type": "application/json"},
    )
    rewritten = injector.rewrite(chat)
    payload = rewritten.data
    check(isinstance(payload, bytes), "a rewritten chat body must be bytes")
    body = json.loads(payload if isinstance(payload, bytes) else b"{}")
    check(body.get("think") is False, "a chat body must gain think=false")
    check(
        body.get("options") == {"a": 1} and body.get("model") == "m",
        "everything else in the chat body must be preserved",
    )
    check(
        rewritten.full_url == chat.full_url,
        "the rewritten request must keep its URL",
    )
    embed = urllib.request.Request(
        "http://127.0.0.1:11434/api/embed",
        data=json.dumps({"model": "m", "input": "text"}).encode(),
    )
    check(
        injector.rewrite(embed) is embed,
        "a body without `messages` must pass through untouched",
    )
    # A request already carrying Content-Length must NOT keep it: the body
    # just grew, and the stale length truncates it into a 400. Found live --
    # a re-used Request that urlopen had already stamped did exactly this.
    stamped = urllib.request.Request(
        "http://127.0.0.1:11434/api/chat",
        data=json.dumps({"model": "m", "messages": []}).encode(),
        headers={"Content-Type": "application/json", "Content-Length": "3"},
    )
    restamped = injector.rewrite(stamped)
    check(
        not restamped.has_header("Content-length"),
        "a stale Content-Length must be dropped so urlopen recomputes it",
    )

    # The verdict, all three ways it can come out.
    def _rec(**kw: Any) -> QualityRecord:
        base = dict(
            fixture="f",
            arm="think",
            run=1,
            model="m",
            objects=5,
            anchors_hit=3,
            anchors_total=4,
            titles="Concept: A",
            failed="",
            seconds=10.0,
        )
        base.update(kw)
        return QualityRecord(**base)  # type: ignore[arg-type]

    ship = [
        _rec(),
        _rec(arm="no-think", anchors_hit=3),
    ]
    check(
        verdict(ship).startswith("SHIP no-think"),
        f"equal medians and equal failures must SHIP (got {verdict(ship)!r})",
    )
    worse_recall = [
        _rec(anchors_hit=4),
        _rec(arm="no-think", anchors_hit=3),
    ]
    check(
        verdict(worse_recall).startswith("KEEP think")
        and "median anchors on f" in verdict(worse_recall),
        f"a lower no-think median must KEEP and name the fixture "
        f"(got {verdict(worse_recall)!r})",
    )
    worse_failures = [
        _rec(),
        _rec(arm="no-think", anchors_hit=3, failed="OllamaGenerationCapped"),
    ]
    check(
        verdict(worse_failures).startswith("KEEP think")
        and "failed runs" in verdict(worse_failures),
        "more no-think failures must KEEP and say why",
    )
    check(
        verdict([_rec()]).startswith("NO DATA"),
        "one arm alone must be NO DATA, never a verdict",
    )
    # A fixture measured in only one arm must not silently pass the bar.
    lopsided = [
        _rec(),
        _rec(arm="no-think"),
        _rec(fixture="g"),
    ]
    check(
        verdict(lopsided).startswith("NO DATA"),
        "a fixture missing an arm must be NO DATA, not skipped",
    )

    # The rescore gate mirrors the sibling probe's.
    roundtrip = _parse_records(json.loads(json.dumps([asdict(_rec())])))
    check(roundtrip == [_rec()], "a stored sweep must round-trip unchanged")

    def _refused(value: Any) -> str:
        try:
            _parse_records(value)
        except SystemExit as exc:
            return str(exc)
        return "NO REFUSAL"

    check("JSON array" in _refused({}), "a non-list file must be refused")
    aged = asdict(_rec())
    aged["legacy"] = 1
    check("legacy" in _refused([aged]), "an unknown field must be refused by name")
    mistyped = asdict(_rec())
    mistyped["anchors_hit"] = True
    check(
        "'anchors_hit'" in _refused([mistyped]),
        "a boolean where a counter belongs must be refused",
    )

    # The render must carry the verdict, so a stored sweep re-renders it.
    check("VERDICT:" in render(ship), "render must print the verdict")
    check("NO DATA" in render([]), "an empty render must say NO DATA, not raise")

    if failures:
        for why in failures:
            print(f"SELF-TEST FAILED: {why}")
        return 1
    print("self-test OK (no model calls)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=15)
    parser.add_argument("--model", default="qwen3:8b")
    parser.add_argument("--host", default="http://127.0.0.1:11434")
    parser.add_argument("--rescore", type=pathlib.Path, default=None)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()

    if args.rescore is not None:
        print(render(_parse_records(json.loads(args.rescore.read_text()))))
        return 0

    if args.runs < 1:
        parser.error(f"--runs must be >= 1, got {args.runs}")

    fixtures = [_fixtures.KICKOFF, _fixtures.HELIOS_OVERVIEW]
    records: list[QualityRecord] = []
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    path = RESULTS_DIR / f"quality-ab-{stamp}-{args.model.replace(':', '-')}.json"
    print(
        f"model {args.model}, {args.runs} run(s) per fixture per arm, full "
        f"shipped pipeline at num_predict {DEFAULT_MAX_GENERATION_TOKENS}, "
        f"num_ctx {DEFAULT_CONTEXT_WINDOW}\n",
        flush=True,
    )
    # Interleaved on both axes, `run` outermost -- the sibling probe's
    # drift rationale, inherited whole.
    for run in range(1, args.runs + 1):
        for fixture in fixtures:
            for arm in ARMS:
                record = run_once(
                    fixture, arm=arm, run=run, model=args.model, host=args.host
                )
                records.append(record)
                outcome = (
                    record.failed
                    or f"{record.anchors_hit}/{record.anchors_total} anchors"
                )
                print(
                    f"   {fixture.name} {arm} run {run}/{args.runs}: "
                    f"{outcome}, {record.objects} objects, {record.seconds}s",
                    flush=True,
                )
                # Persisted after EVERY call, so one transport failure does
                # not discard an hour of GPU.
                path.write_text(
                    json.dumps(
                        [asdict(r) for r in records], indent=2, ensure_ascii=False
                    )
                )

    print(render(records))
    print(f"\nstored {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
