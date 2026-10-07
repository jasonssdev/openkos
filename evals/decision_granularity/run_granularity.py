"""Decision granularity and a twice-named person: a measured A/B (#1231 b, c).

MANUAL eval tool (not pytest, not shipped). Drives the REAL union pipeline,
`openkos.extraction.concept.extract_concept_union` over `OllamaClient`, built
with production's own generation ceiling and context window, on synthetic
sources (`granularity_fixtures.py`). A treatment is a one-sentence edit applied as a
monkeypatch on `concept._SYSTEM_PROMPT`; production is never edited by this
file.

Field report (#1231): a five-decision meeting export collapsed to one
`Decision` object listing all five, while a three-bullet review split into
three; and a new engineer named twice was not extracted as a `Person`.

What is scored, per run, from the retained objects:

- `n_decisions` -- `Decision` objects retained. `split` is
  `n_decisions >= ceil(0.6 * expected_decisions)`, written down before the
  first live run.
- `topics` -- expected subject groups covered by ANY retained object
  (recall; a fixture's hand-written keyword groups, never regex-derived from
  the output).
- `person_hit` -- a `Person` object titled with the fixture's twice-named
  target; `person_stubs` -- every OTHER `Person` object (precision guard).
- latency, judge status, errors.

Usage:

    uv run python -u evals/decision_granularity/run_granularity.py --self-test
    uv run python -u evals/decision_granularity/run_granularity.py --arm baseline --runs 15
    uv run python -u evals/decision_granularity/run_granularity.py --arm decisions --runs 15
    uv run python -u evals/decision_granularity/run_granularity.py --rescore

**Use `-u`.** Piping a long run through `tee` makes Python buffer, and the run
then looks hung.

## Exposure first

A fixture the treatment cannot change makes the comparison vacuous: a
baseline that already splits (or already finds the person) in every run
leaves nothing to improve. `--rescore` prints, per fixture and metric, how
many baseline runs FAILED it (`n of TOTAL`). Only those fixtures are
evidence for a win; the rest are guards.

## Decision rule (pre-registered)

A treatment ships only if, over n >= 15 runs per arm, ALL hold:

1. its targeted metric (`split` rate for `decisions`, `person_hit` rate for
   `persons`) improves on the fixtures the baseline fails by MORE than the
   baseline's own spread (max - min of its per-5-run block rates) and by at
   least 0.25 absolute;
2. mean topic recall does not drop by more than 0.05 on any fixture;
3. the single-decision control's over-split rate (`n_decisions > 1`) rises
   by no more than 0.10, and `person_stubs` per run does not rise by more
   than 0.5 on any fixture;
4. errored runs do not increase and mean latency stays under 1.5x baseline.

Anything else records the result and ships nothing prompt-level.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import math
import sys
import tempfile
import time
import unicodedata
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path
from statistics import mean
from typing import Any, Final

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(HERE))
sys.path.append(str(REPO_ROOT / "evals"))

from granularity_fixtures import FIXTURES, Fixture  # noqa: E402
from harness_prompts import extraction_prompts  # noqa: E402
from harness_report import arm_identity_line  # noqa: E402
from harness_stamp import build_stamp, prompt_hash  # noqa: E402

from openkos.config import (  # noqa: E402
    DEFAULT_CONTEXT_WINDOW,
    DEFAULT_MAX_GENERATION_TOKENS,
)
from openkos.extraction import concept as concept_mod  # noqa: E402
from openkos.extraction.concept import extract_concept_union  # noqa: E402
from openkos.llm.ollama import OllamaClient  # noqa: E402

RESULTS_DIR = HERE / "results"
DEFAULT_MODEL: Final = "qwen3:8b"
SPLIT_FRACTION: Final = 0.6
BLOCK_SIZE: Final = 5
MIN_GAIN: Final = 0.25

# --------------------------------------------------------------------------- #
# Treatments: ONE sentence each, applied by exact replacement.
# --------------------------------------------------------------------------- #

TREATMENTS: Final[dict[str, tuple[str, str]]] = {
    # `(anchor, replacement)`: the anchor must occur exactly once in the
    # shipped prompt, or the arm would measure the baseline twice.
    "persons": (
        "extract it only when the source is genuinely about it.",
        "extract it only when the source is genuinely about it -- a person "
        "the source names more than once and describes (for example a new "
        "team member) is about that person, not a passing mention.",
    ),
    "decisions": (
        "not a general idea or a dated happening.",
        "not a general idea or a dated happening. A source that records "
        "several choices yields one Decision per choice, never one object "
        "listing them all.",
    ),
    # #1231 (c), second pass: the baseline failure is a whole-run collapse to
    # one Event, so each candidate is a narrow clause at the place the model
    # decides who counts as a subject. See PREREGISTRATION-1231c.md.
    "role": (
        "their identity, role, work, or biography.",
        "their identity, role, work, or biography. A newcomer introduced by "
        "role (for example joining a team as its second backend engineer) is "
        "such an individual, even when named only in a decision or staffing "
        "line.",
    ),
    "attendees": (
        "not five Person stubs.",
        "not five Person stubs; but a person the decisions or staffing lines "
        "are about (for example a new hire) is a subject, not an attendee.",
    ),
    "newcomer": (
        "their identity, role, work, or biography.",
        "their identity, role, work, or biography (including a newcomer's "
        "role on a team).",
    ),
}


# --------------------------------------------------------------------------- #
# Pipeline arms: a monkeypatch of one deterministic function, never a prompt
# edit (#1318). The patch is undone after the arm and the prompt an arm sends
# is the shipped one.
#
# #1318 shipped its treatment (`concept._title_tokens` ignoring date tokens;
# measured as the `datefold` arm, stored under results/). A shipped treatment
# turns its own arm into a no-op against the baseline, so the arm that remains
# is the ABLATION: the pre-#1318 tokens, which reproduces the collapse.
# --------------------------------------------------------------------------- #


def _title_tokens_with_dates(value: str) -> frozenset[str]:
    """`concept._title_tokens` as it was before #1318: digits and month names
    are ordinary tokens."""
    return frozenset(
        token
        for token in concept_mod._title_words(value)
        if len(token) >= concept_mod._MIN_TOPIC_TOKEN_LENGTH
    )


PIPELINE_TREATMENTS: Final[dict[str, str]] = {
    "undated": (
        "ABLATION of #1318: `_title_tokens` keeps date tokens again, so the "
        "re-ask trigger is blind to a date written two ways."
    ),
}


@contextlib.contextmanager
def pipeline_patch(arm: str) -> Iterator[None]:
    """Apply `arm`'s pipeline treatment for the duration of the block."""
    if arm not in PIPELINE_TREATMENTS:
        yield
        return
    original = concept_mod._title_tokens
    concept_mod._title_tokens = _title_tokens_with_dates
    try:
        yield
    finally:
        concept_mod._title_tokens = original


def treated_prompt(arm: str, shipped: str) -> str:
    """The shipped prompt with `arm`'s edit applied; `baseline` and the
    pipeline arms are unchanged. Raises when the anchor is missing or
    ambiguous -- a no-op treatment would silently compare the baseline
    against itself."""
    if arm == "baseline" or arm in PIPELINE_TREATMENTS:
        return shipped
    anchor, replacement = TREATMENTS[arm]
    if shipped.count(anchor) != 1:
        raise SystemExit(
            f"treatment {arm!r}: anchor found {shipped.count(anchor)} times in "
            "the shipped prompt (need exactly 1). The prompt moved; re-point it."
        )
    patched = shipped.replace(anchor, replacement)
    if patched == shipped:
        raise SystemExit(f"treatment {arm!r} changed nothing")
    return patched


# --------------------------------------------------------------------------- #
# Records and scoring
# --------------------------------------------------------------------------- #


@dataclass
class ObjectRecord:
    type: str
    title: str
    description: str
    body: str


@dataclass
class RunRecord:
    fixture: str
    arm: str
    run: int
    model: str
    judge_status: str
    produced: int
    retained: int
    latency_s: float
    objects: list[ObjectRecord] = field(default_factory=list)
    error: str | None = None
    # #1318: which optional calls the run spent. Absent (None) on stored
    # runs from before they were recorded.
    reask_runs: int | None = None
    participant_capture_runs: int | None = None


def fold(text: str) -> str:
    """Case- and accent-folded text, so `Joaquín` matches `joaquin`."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def _object_text(obj: dict[str, Any]) -> str:
    return fold(f"{obj['title']} {obj['description']} {obj['body']}")


def score_run(fixture: Fixture, objects: list[dict[str, Any]]) -> dict[str, Any]:
    """Pure scoring of one run's retained objects against one fixture."""
    decisions = [o for o in objects if o["type"] == "Decision"]
    persons = [o for o in objects if o["type"] == "Person"]
    corpus = [_object_text(o) for o in objects]
    covered = sum(
        1
        for group in fixture.topics
        if any(fold(alt) in text for text in corpus for alt in group)
    )
    expected = fixture.expected_decisions
    split: bool | None = None
    if expected is not None and not fixture.single_decision:
        split = len(decisions) >= math.ceil(SPLIT_FRACTION * expected)
    person_hit: bool | None = None
    stubs = len(persons)
    if fixture.target_person is not None:
        target = fold(fixture.target_person)
        hits = [p for p in persons if target in fold(p["title"])]
        person_hit = bool(hits)
        stubs = len(persons) - len(hits)
    return {
        "n_decisions": len(decisions),
        "split": split,
        "oversplit": (len(decisions) > 1) if fixture.single_decision else None,
        "topics_covered": covered,
        "topics_total": len(fixture.topics),
        "person_hit": person_hit,
        "person_stubs": stubs,
    }


def block_rates(flags: list[bool], size: int = BLOCK_SIZE) -> list[float]:
    """Rate per consecutive block of `size` runs (a trailing partial block
    is dropped: its rate would be a coarser number than its siblings')."""
    return [
        sum(flags[i : i + size]) / size for i in range(0, len(flags) - size + 1, size)
    ]


def spread(flags: list[bool]) -> float:
    """Max - min of the block rates: the arm measured against itself."""
    rates = block_rates(flags)
    return max(rates) - min(rates) if rates else 0.0


def fixture_by_name(name: str) -> Fixture:
    for fixture in FIXTURES:
        if fixture.name == name:
            return fixture
    raise KeyError(name)


def scored(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach `score` to every non-errored record."""
    out = []
    for record in records:
        if record.get("error"):
            out.append({**record, "score": None})
            continue
        fixture = fixture_by_name(record["fixture"])
        out.append({**record, "score": score_run(fixture, record["objects"])})
    return out


def summarize_arm(records: list[dict[str, Any]], arm: str) -> str:
    """One markdown table for one arm; every filtered metric prints
    `n of TOTAL`."""
    rows = [
        "| fixture | runs ok of TOTAL | decisions/run | split n of TOTAL "
        "(spread) | person hit n of TOTAL | stubs/run | recall | "
        "over-split n of TOTAL | latency |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for fixture in FIXTURES:
        mine = [r for r in records if r["arm"] == arm and r["fixture"] == fixture.name]
        total = len(mine)
        ok = [r for r in mine if r["score"] is not None]
        if not total:
            continue
        scores = [r["score"] for r in ok]
        split_flags = [s["split"] for s in scores if s["split"] is not None]
        hit_flags = [s["person_hit"] for s in scores if s["person_hit"] is not None]
        over = [s["oversplit"] for s in scores if s["oversplit"] is not None]
        recall = (
            mean(s["topics_covered"] / s["topics_total"] for s in scores)
            if scores
            else float("nan")
        )
        rows.append(
            "| {name} | {ok} of {total} | {dec} | {split} | {hit} | {stubs} | "
            "{recall:.2f} | {over} | {lat} |".format(
                name=fixture.name,
                ok=len(ok),
                total=total,
                dec=f"{mean(s['n_decisions'] for s in scores):.2f}" if scores else "-",
                split=(
                    f"{sum(split_flags)} of {len(split_flags)} "
                    f"({spread(split_flags):.2f})"
                    if split_flags
                    else "-"
                ),
                hit=(
                    f"{sum(hit_flags)} of {len(hit_flags)} ({spread(hit_flags):.2f})"
                    if hit_flags
                    else "-"
                ),
                stubs=f"{mean(s['person_stubs'] for s in scores):.2f}"
                if scores
                else "-",
                recall=recall,
                over=f"{sum(over)} of {len(over)}" if over else "-",
                lat=f"{mean(r['latency_s'] for r in ok):.0f}s" if ok else "-",
            )
        )
    return "\n".join(rows)


def exposure(records: list[dict[str, Any]], arm: str = "baseline") -> str:
    """Which fixtures the baseline FAILS -- the only ones a treatment can
    be credited on. `n of TOTAL`, never a bare rate."""
    lines = []
    for fixture in FIXTURES:
        mine = [
            r["score"]
            for r in records
            if r["arm"] == arm and r["fixture"] == fixture.name and r["score"]
        ]
        if fixture.single_decision:
            fails = sum(1 for s in mine if s["oversplit"])
            what = "over-split (a control: failing here is a regression)"
        elif fixture.target_person is not None:
            fails = sum(1 for s in mine if not s["person_hit"])
            what = "target person missed"
        else:
            fails = sum(1 for s in mine if not s["split"])
            what = "decisions not split"
        lines.append(
            f"- {fixture.name}: {fails} of {len(mine)} baseline runs fail ({what})"
        )
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Running
# --------------------------------------------------------------------------- #


def run_combo(
    fixture: Fixture, arm: str, llm: Any, runs: int, model: str
) -> list[RunRecord]:
    with pipeline_patch(arm):
        return _run_combo(fixture, arm, llm, runs, model)


def _run_combo(
    fixture: Fixture, arm: str, llm: Any, runs: int, model: str
) -> list[RunRecord]:
    original = concept_mod._SYSTEM_PROMPT
    concept_mod._SYSTEM_PROMPT = treated_prompt(arm, original)
    records: list[RunRecord] = []
    try:
        for index in range(1, runs + 1):
            started = time.monotonic()
            try:
                outcome = extract_concept_union(
                    fixture.text, source_title=fixture.title, llm=llm
                )
            except Exception as exc:
                records.append(
                    RunRecord(
                        fixture.name,
                        arm,
                        index,
                        model,
                        "error",
                        0,
                        0,
                        round(time.monotonic() - started, 1),
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )
                print(f"    {fixture.name}/{arm} run {index}: ERROR {exc}")
                continue
            latency = round(time.monotonic() - started, 1)
            objects = [
                ObjectRecord(o.type, o.title, o.description, o.body)
                for o in outcome.objects
            ]
            records.append(
                RunRecord(
                    fixture.name,
                    arm,
                    index,
                    model,
                    outcome.report.judge_status,
                    outcome.report.produced,
                    outcome.report.retained,
                    latency,
                    objects,
                    reask_runs=outcome.report.reask_runs,
                    participant_capture_runs=(outcome.report.participant_capture_runs),
                )
            )
            kinds = ",".join(o.type[0] for o in outcome.objects)
            print(
                f"    {fixture.name}/{arm} run {index}: retained="
                f"{outcome.report.retained} [{kinds}] "
                f"judge={outcome.report.judge_status} {latency}s",
                flush=True,
            )
    finally:
        concept_mod._SYSTEM_PROMPT = original
    return records


def arm_prompts(arm: str) -> dict[str, str]:
    """The system prompts `arm` sends, the treated one under `<id>+<arm>`.

    Computed from `treated_prompt` rather than read off `concept_mod`, which
    `run_combo` has already restored by the time results are written."""
    return extraction_prompts(
        system=treated_prompt(arm, concept_mod._SYSTEM_PROMPT),
        arm=None if arm == "baseline" or arm in PIPELINE_TREATMENTS else arm,
    )


def write_results(records: list[RunRecord], arm: str, model: str) -> Path:
    RESULTS_DIR.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    path = RESULTS_DIR / f"runs-{arm}-{stamp}-{model.replace(':', '-')}.json"
    path.write_text(
        json.dumps(
            {
                "arm": arm,
                "model": model,
                "max_generation_tokens": DEFAULT_MAX_GENERATION_TOKENS,
                "context_window": DEFAULT_CONTEXT_WINDOW,
                "generated_at": stamp,
                "pipeline_treatment": PIPELINE_TREATMENTS.get(arm),
                # #1277: the exact prompt text this arm sent, with the model
                # and harness identity.
                "stamp": build_stamp(model=model, prompts=arm_prompts(arm)),
                "records": [asdict(r) for r in records],
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def load_records() -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in sorted(RESULTS_DIR.glob("runs-*.json")):
        records.extend(json.loads(path.read_text(encoding="utf-8"))["records"])
    return records


def report(records: list[dict[str, Any]]) -> str:
    done = scored(records)
    arms = sorted({r["arm"] for r in done}, key=lambda a: (a != "baseline", a))
    parts = [
        arm_identity_line(
            max_generation_tokens=DEFAULT_MAX_GENERATION_TOKENS,
            context_window=DEFAULT_CONTEXT_WINDOW,
        ),
        "",
        "## Exposure (baseline failures; a fixture with 0 cannot credit a treatment)",
        "",
        exposure(done),
    ]
    for arm in arms:
        parts += ["", f"## Arm `{arm}`", "", summarize_arm(done, arm)]
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# Self-test (no model)
# --------------------------------------------------------------------------- #


class _FakeLLM:
    """Scripted backend: every extraction pass returns the same two objects,
    and the judge keeps both. Records the system prompt it was sent."""

    def __init__(self) -> None:
        self.systems: list[str] = []

    def chat(self, messages: list[dict[str, str]]) -> str:
        system = messages[0]["content"]
        self.systems.append(system)
        if "selection step" in system:
            return json.dumps({"keep": ["Cola nueva", "Joaquín Beltrán"]})
        return json.dumps(
            [
                {
                    "type": "Decision",
                    "title": "Cola nueva",
                    "description": "Se migra a la cola de mensajes.",
                    "body": "",
                },
                {
                    "type": "Person",
                    "title": "Joaquín Beltrán",
                    "description": "Nuevo ingeniero.",
                    "body": "",
                },
            ],
            ensure_ascii=False,
        )


def _self_test() -> int:
    failures: list[str] = []

    def check(label: str, actual: object, expected: object) -> None:
        if actual != expected:
            failures.append(f"{label}: expected {expected!r}, got {actual!r}")

    # Every fixture's expectations are internally consistent.
    for fixture in FIXTURES:
        check(f"{fixture.name} has topics", bool(fixture.topics), True)
        if fixture.target_person is not None:
            check(
                f"{fixture.name} full name stated twice",
                fold(fixture.text).count(fold(fixture.target_person)) >= 2,
                True,
            )
        if fixture.target_person is not None:
            first = fold(fixture.target_person).split()[0]
            check(
                f"{fixture.name} first name appears 3+ times",
                fold(fixture.text).count(first) >= 3,
                True,
            )
    check(
        "one single-decision control",
        sum(1 for f in FIXTURES if f.single_decision),
        1,
    )

    five = fixture_by_name("es-notes-5-decisions")
    lumped = [
        {
            "type": "Decision",
            "title": "Decisiones de la reunión",
            "description": "Cola de mensajes, esquema de la API, soporte nocturno.",
            "body": "registro estructurado y panel interno",
        }
    ]
    s = score_run(five, lumped)
    check("lumped: 1 decision", s["n_decisions"], 1)
    check("lumped: not split (needs 3 of 5)", s["split"], False)
    check("lumped: topics all covered", s["topics_covered"], 5)
    split3 = [
        {"type": "Decision", "title": t, "description": "d", "body": ""}
        for t in ("a", "b", "c")
    ]
    check("three decisions split", score_run(five, split3)["split"], True)

    newhire = fixture_by_name("es-meeting-new-engineer")
    s = score_run(
        newhire,
        [
            {
                "type": "Person",
                "title": "Joaquin Beltran",
                "description": "",
                "body": "",
            },
            {
                "type": "Person",
                "title": "Marta Lindqvist",
                "description": "",
                "body": "",
            },
        ],
    )
    check("accent-folded person hit", s["person_hit"], True)
    check("other persons are stubs", s["person_stubs"], 1)
    check(
        "missing person",
        score_run(newhire, [])["person_hit"],
        False,
    )

    single = fixture_by_name("en-note-single-decision")
    check(
        "over-split control",
        score_run(
            single,
            [
                {"type": "Decision", "title": "a", "description": "", "body": ""},
                {"type": "Decision", "title": "b", "description": "", "body": ""},
            ],
        )["oversplit"],
        True,
    )

    # Spread: 3 blocks of 5 -> 0.2, 0.6, 0.4 -> spread 0.4; a partial
    # trailing block is dropped.
    flags = [True] + [False] * 4 + [True] * 3 + [False] * 2 + [True] * 2 + [False] * 3
    check("block rates", block_rates(flags), [0.2, 0.6, 0.4])
    check("spread", round(spread(flags), 2), 0.4)
    check("partial block dropped", block_rates([True] * 7), [1.0])

    # Treatment: applies exactly once to the shipped prompt, never to the
    # baseline, and the monkeypatch is restored.
    shipped = concept_mod._SYSTEM_PROMPT
    check(
        "baseline is the shipped prompt", treated_prompt("baseline", shipped), shipped
    )
    for arm in TREATMENTS:
        patched = treated_prompt(arm, shipped)
        check(f"{arm} differs from shipped", patched != shipped, True)
        check(f"{arm} adds one sentence only", len(patched) - len(shipped) < 200, True)
    try:
        treated_prompt("decisions", "no anchor here")
    except SystemExit:
        pass
    else:
        failures.append("a missing anchor must refuse")

    # #1318: date tokens are ignored by the shipped containment; the
    # `undated` ablation restores the old blindness only while it is active,
    # and the prompt is the shipped one.
    sdate = fixture_by_name("en-review-new-engineer")
    collapsed = concept_mod.ExtractionResult(
        type="Event",
        title="Architecture review, 10 February",
        description="d",
        body="b",
    )
    check(
        "shipped containment reads the date-stamped twin as restating",
        concept_mod._restates_source_topic(collapsed, source_title=sdate.title),
        True,
    )
    real_tokens = concept_mod._title_tokens
    with pipeline_patch("undated"):
        check(
            "undated is blind to the date written two ways",
            concept_mod._restates_source_topic(collapsed, source_title=sdate.title),
            False,
        )
    check("pipeline patch restored", concept_mod._title_tokens is real_tokens, True)
    with pipeline_patch("baseline"):
        check(
            "baseline leaves the pipeline alone",
            concept_mod._title_tokens is real_tokens,
            True,
        )
    check(
        "undated sends the shipped prompt",
        treated_prompt("undated", shipped),
        shipped,
    )

    # #1277: a stored result names the text each arm sent. The write goes to a
    # scratch dir; the Ollama digest lookup fails fast under the sweep's
    # poisoned host and is recorded, never raised.
    global RESULTS_DIR
    real_dir = RESULTS_DIR
    with tempfile.TemporaryDirectory() as scratch:
        RESULTS_DIR = Path(scratch)
        try:
            by_arm = {
                arm: json.loads(write_results([], arm, "fake").read_text())["stamp"]
                for arm in ("baseline", *TREATMENTS)
            }
        finally:
            RESULTS_DIR = real_dir
    base_ids = {p["id"]: p["sha256_16"] for p in by_arm["baseline"]["prompts"]}
    check("baseline stamps the registered id", "extraction/system" in base_ids, True)
    check(
        "baseline hash is the shipped text",
        base_ids["extraction/system"],
        prompt_hash(shipped),
    )
    for arm in TREATMENTS:
        got = {p["id"]: p["sha256_16"] for p in by_arm[arm]["prompts"]}
        check(
            f"{arm} stamps the spliced text under its own id",
            got.get(f"extraction/system+{arm}"),
            prompt_hash(treated_prompt(arm, shipped)),
        )
        check(f"{arm} does not stamp the shipped id", "extraction/system" in got, False)

    llm = _FakeLLM()
    recs = run_combo(newhire, "decisions", llm, 1, "fake")
    check("fake run produced a record", len(recs), 1)
    check(
        "the treatment reached the model",
        any("one Decision per choice" in s for s in llm.systems),
        True,
    )
    check("prompt restored after run", concept_mod._SYSTEM_PROMPT, shipped)
    llm_base = _FakeLLM()
    run_combo(newhire, "baseline", llm_base, 1, "fake")
    check(
        "baseline never carries the treatment",
        any("one Decision per choice" in s for s in llm_base.systems),
        False,
    )

    table = report(
        [{**asdict(r), "objects": [asdict(o) for o in r.objects]} for r in recs]
    )
    check("report prints n of TOTAL", "of 1" in table, True)

    for line in failures:
        print(f"FAIL {line}")
    print(f"\nself-test: {'FAILED' if failures else 'passed'}")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--arm", choices=["baseline", *TREATMENTS, *PIPELINE_TREATMENTS]
    )
    parser.add_argument("--runs", type=int, default=15)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument(
        "--fixture",
        action="append",
        help="restrict to this fixture name (repeatable); default all",
    )
    parser.add_argument("--rescore", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()
    if args.rescore:
        records = load_records()
        if not records:
            raise SystemExit(f"no stored runs in {RESULTS_DIR}")
        print(report(records))
        return 0
    if args.arm is None:
        parser.error("--arm is required unless --self-test or --rescore")

    fixtures = [f for f in FIXTURES if not args.fixture or f.name in args.fixture]
    llm = OllamaClient(
        model=args.model,
        max_generation_tokens=DEFAULT_MAX_GENERATION_TOKENS,
        context_window=DEFAULT_CONTEXT_WINDOW,
    )
    print(f"model {args.model}, arm {args.arm}, {args.runs} run(s) per fixture\n")
    run_records: list[RunRecord] = []
    for fixture in fixtures:
        print(f"  {fixture.name} ({len(fixture.text)} chars)")
        run_records.extend(run_combo(fixture, args.arm, llm, args.runs, args.model))
    path = write_results(run_records, args.arm, args.model)
    print(f"stored {path}")
    print(report([asdict(r) for r in run_records]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
