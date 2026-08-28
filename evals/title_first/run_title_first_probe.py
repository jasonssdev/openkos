"""Does a title-first phase 1 pay for itself? (#728, option 2)

#692 measured that extraction throws away 53-93% of what it generates, and
#728 narrowed the finding: **81-100% of the discarded tail dies in gates that
read `type` and `title` and nothing else**. The largest of them,
`_drop_framing_objects`, is a per-object predicate. A candidate it kills had
its `description` and `body` written for nothing.

This probe costs the repair before production is touched, on the #613 / #622 /
#630 / #699 precedent. Production is UNCHANGED by this file.

## The lever, and why it is exactly one function

`extract_concept_union` calls `_extract_once` once per window. The treatment
replaces THAT ONE FUNCTION with: survey the window for `type` + `title` only,
drop the framing objects, then spend one hydration call writing
`description` + `body` for the survivors.

Everything else -- chunking, dedup, the twin rule, the wrong-language gate,
the re-ask, participant capture, the judge, the backstop, the report -- runs
production's own code, byte-identical in both arms. That is deliberate: a
probe that reimplemented the union would make every difference ambiguous
between the lever and my copy of the pipeline.

**What the lever therefore does NOT recover**: kills charged to
`_drop_source_title_twins`, `_dedup_merged` and `_drop_wrong_language_titles`.
Those run on the MERGED candidate list, and the twin rule's floor reads the
whole set -- applying it per window decides on a set no source ever emitted,
which is the exact defect #581 documents. They are a small share of the
discarded tail (#728's table) and they stay unrecovered here.

## The confound, stated up front

Phase 1 asks for a different reply SHAPE, and this repo has measured five
times that touching the prompt moves what the model proposes, not only how it
formats it (#613, #622, #715 slice 1, #713's first shape, #699 carry-titles).
So the treatment arm changes pipeline shape AND prompt together.

Two things bound it:

1. The survey prompt is DERIVED from `concept._SYSTEM_PROMPT` by replacing
   only its final reply-shape clause. The nine-type rubric, the
   anti-enumeration paragraph (#380, pinned) and the transcript-subjects
   clause (#715) are carried byte-identical. `_survey_system_prompt` fails
   closed if production's clause ever moves, rather than silently sending the
   full-shape instruction.
2. The report prints **title-set overlap between arms**. If phase 1 proposes a
   different set of subjects, the wall-clock comparison is not measuring the
   same work and the overlap figure is what says so.

## The bar

Ruled before measuring: adopt only if wall clock improves outside the noise
band AND quality stays inside the #694 oracle's -- recall 0.80 +/-0.12,
precision 0.95 +/-0.08. A latency win that costs extraction quality is not a
win; it is the silent regression this corpus exists to catch.

## A loss channel the baseline does not have

A survivor the hydration call fails to return is DROPPED, and counted as
`hydration_lost`. It is never back-filled from the survey title, because a
candidate with an invented description is worse than an absent one, and a
silent fallback would hide the treatment's own failure mode inside its
quality score.

Run from the repository root (needs Ollama):

    uv run python -u evals/title_first/run_title_first_probe.py --runs 6
    uv run python evals/title_first/run_title_first_probe.py --self-test
"""

from __future__ import annotations

import argparse
import contextlib
import json
import statistics
import sys
import time
from collections.abc import Iterator, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parents[1]
_CAP = _REPO_ROOT / "evals" / "extraction_cap"
sys.path.insert(0, str(_CAP))

import run_cap_eval as cap  # noqa: E402

from openkos.extraction import concept  # noqa: E402
from openkos.llm import parsing  # noqa: E402
from openkos.llm.base import LLMBackend, Message  # noqa: E402
from openkos.llm.ollama import OllamaClient, OllamaError  # noqa: E402
from openkos.model.types import CLASSIFIABLE_TYPES  # noqa: E402

DEFAULT_MODEL = "qwen3:8b"
DEFAULT_RUNS = 6
DEFAULT_TIMEOUT = 600.0
DEFAULT_FIXTURE = "medium-10-reunion-plataforma"
"""The #694 oracle transcript.

Chosen over `evals/discarded_generation`'s own fixtures because it is the only
meeting-shaped source in this repo carrying adjudicated title-level ground
truth. #728's bar is a QUALITY bar, and the fixtures the waste was measured on
cannot score recall or precision at all.

It also takes the chunked path (its ground truth declares that invariant,
#726), which is where `_extract_once` runs per window and the lever has
anything to do.
"""

BASELINE = "baseline"
TREATMENT = "title-first"


# --------------------------------------------------------------------------- #
# The prompts, derived from production rather than restated.                   #
# --------------------------------------------------------------------------- #

_SHAPE_MARKER = "Return ONLY a JSON array,"
"""Where `concept._SYSTEM_PROMPT`'s reply-shape clause begins."""

_SURVEY_SHAPE = (
    "Return ONLY a JSON array, with NO prose, NO markdown, and NO code "
    "fences around it. Each element matches exactly this shape:\n"
    '[{"type": "Person"|"Organization"|"Place"|"Event"|"Procedure"'
    '|"Decision"|"Project"|"Concept"|"Entity", "title": "..."}, ...]\n'
    "Do NOT wrap the array in an outer object. Give the TITLE only -- do NOT "
    "write a description and do NOT write a body for any object. Choosing "
    "WHICH objects to propose is exactly the same judgement it always was; "
    "only the fields you write have changed."
)
"""The survey reply shape.

The trailing sentence is defensive, not decorative: without it a model that
has been told to write less can read the instruction as "propose less", and
proposing less is the outcome this probe would then measure as a latency win.
"""

_HYDRATE_SYSTEM_PROMPT = (
    "You are the second phase of a two-phase extraction step in a "
    "local-first knowledge engine. A first pass already read the SOURCE text "
    "below and chose which derived knowledge objects are worth extracting. "
    "That selection is FINAL and is not yours to revisit.\n\n"
    "For EACH object in the OBJECTS list, write its `description` and "
    "`body` from the SOURCE text. Do NOT add objects, do NOT drop objects, "
    "do NOT rename a title, and do NOT change a type. Echo each `type` and "
    "`title` back EXACTLY as given.\n\n"
    "`description` is one or two sentences naming what the object is. "
    "`body` is the fuller account the source supports, in the SOURCE's own "
    "language. Ground both in the source: do not invent detail it does not "
    "carry.\n\n"
    "Return ONLY a JSON array, with NO prose, NO markdown, and NO code "
    "fences around it. Each element matches exactly this shape:\n"
    '[{"type": "<echoed>", "title": "<echoed>", "description": "...", '
    '"body": "..."}, ...]\n'
    "Do NOT wrap the array in an outer object."
)


def _survey_system_prompt() -> str:
    """Production's system prompt with ONLY its reply-shape clause replaced.

    Derived, never restated. The rubric this returns -- nine types, the
    pinned anti-enumeration paragraph (#380), the transcript-subjects clause
    (#715) -- is byte-identical to what the baseline arm sends, which is the
    whole reason the two arms can be compared at all.

    Fails closed on drift. If production's clause ever moves or is reworded,
    the marker stops matching and this raises, rather than quietly sending
    the FULL-shape instruction and measuring an arm that asks for exactly
    what the baseline asks for.
    """
    head, marker, tail = concept._SYSTEM_PROMPT.partition(_SHAPE_MARKER)
    if not marker:
        raise SystemExit(
            "title-first probe: concept._SYSTEM_PROMPT no longer contains "
            f"{_SHAPE_MARKER!r}; the survey prompt cannot be derived and this "
            "probe must not guess one"
        )
    if '"description"' not in tail:
        raise SystemExit(
            "title-first probe: the clause after the marker no longer names "
            '"description"; production\'s reply shape changed and the survey '
            "prompt derived from it would be wrong"
        )
    return head + _SURVEY_SHAPE


def _survey_messages(source_text: str, source_title: str) -> list[Message]:
    """The survey turn: production's user message, the survey system half.

    The user turn is built by production's own `_build_messages` and then
    re-used verbatim, so the meeting-shaped title suppression (#459/#673) and
    the language anchor (#713) behave identically in both arms.
    """
    built = concept._build_messages(source_text, source_title)
    user = next(m for m in built if m["role"] == "user")
    return [
        {"role": "system", "content": _survey_system_prompt()},
        {"role": "user", "content": user["content"]},
    ]


def _hydrate_messages(
    survivors: Sequence[tuple[str, str]], source_text: str
) -> list[Message]:
    """The hydration turn: the surviving `(type, title)` pairs and the window."""
    listed = "\n".join(
        f"{index}. type={obj_type!r} title={title!r}"
        for index, (obj_type, title) in enumerate(survivors, start=1)
    )
    return [
        {"role": "system", "content": _HYDRATE_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"SOURCE TEXT:\n{source_text}\n\nOBJECTS:\n{listed}",
        },
    ]


# --------------------------------------------------------------------------- #
# The lever.                                                                   #
# --------------------------------------------------------------------------- #


@dataclass
class LeverLedger:
    """What the treatment's own phases cost and lost, across a whole run."""

    survey_calls: int = 0
    hydrate_calls: int = 0
    surveyed: int = 0
    """Candidates phase 1 proposed, before the framing gate."""
    framing_dropped: int = 0
    """Candidates the framing gate killed BEFORE a body was ever written."""
    hydration_lost: int = 0
    """Survivors the hydration call did not return.

    A new loss channel the baseline does not have. Never back-filled: a
    candidate carrying an invented description is worse than an absent one,
    and a silent fallback would hide this failure mode inside the quality
    score."""
    survey_titles: list[str] = field(default_factory=list)


def _validate_survey_item(data: dict[str, Any]) -> tuple[str, str] | None:
    """Fail-closed `(type, title)` from one survey element.

    Mirrors `concept._validate`'s type and title rules exactly, and applies
    no others: the survey has no description or body to check.
    """
    obj_type = data.get("type")
    if not isinstance(obj_type, str) or obj_type not in CLASSIFIABLE_TYPES:
        return None
    title = data.get("title")
    if not isinstance(title, str) or not title.strip():
        return None
    return obj_type, title.strip()


def title_first_extract_once(
    source_text: str,
    source_title: str,
    llm: LLMBackend,
    *,
    framing_shaped: bool,
    ledger: LeverLedger,
) -> list[concept.ExtractionResult]:
    """`_extract_once`, with the framing gate moved BEFORE the body is written.

    Survey the window for `type` + `title`, drop the framing objects with
    production's own predicate, then spend one call hydrating the survivors.

    `framing_shaped` is passed in rather than recomputed per window: the
    union computes it ONCE for the whole source (#673 design D3), and a
    per-window verdict would gate the treatment on a different predicate than
    the baseline runs under.

    It is `_framing_shaped`, NOT `_is_meeting_shaped` (#903): production's
    `_drop_framing_objects` call sites take the wider verdict, and this probe
    exists to measure that exact call. Feeding it the narrower one would
    leave the treatment arm blind to a heading-only gathering signal while
    the baseline it is compared against sees it -- an arm that differs from
    production in the one predicate the lever is built around.
    """
    ledger.survey_calls += 1
    reply = llm.chat(_survey_messages(source_text, source_title))
    surveyed: list[tuple[str, str]] = []
    for item in parsing.extract_json_items(reply):
        pair = _validate_survey_item(item)
        if pair is not None:
            surveyed.append(pair)
    ledger.surveyed += len(surveyed)
    ledger.survey_titles.extend(title for _t, title in surveyed)

    # Production's own predicate, on a placeholder carrying the only two
    # fields it reads. `_drop_framing_objects` is per-object and stateless,
    # which is what makes it safe to run per window here; the twin rule is
    # not, and is deliberately left downstream (#581).
    placeholders = [
        concept.ExtractionResult(type=t, title=title, description="-", body="")
        for t, title in surveyed
    ]
    kept = concept._drop_framing_objects(placeholders, framing_shaped=framing_shaped)
    ledger.framing_dropped += len(placeholders) - len(kept)
    if not kept:
        return []

    survivors = [(r.type, r.title) for r in kept]
    ledger.hydrate_calls += 1
    hydrated_reply = llm.chat(_hydrate_messages(survivors, source_text))
    by_key: dict[tuple[str, str], concept.ExtractionResult] = {}
    for item in parsing.extract_json_items(hydrated_reply):
        result = concept._validate(item)
        if result is not None:
            by_key[(result.type, concept._normalize_title(result.title))] = result

    out: list[concept.ExtractionResult] = []
    for obj_type, title in survivors:
        result = by_key.get((obj_type, concept._normalize_title(title)))
        if result is None:
            ledger.hydration_lost += 1
            continue
        out.append(result)
    return out


@contextlib.contextmanager
def applied_lever(*, framing_shaped: bool, ledger: LeverLedger) -> Iterator[None]:
    """Swap `_extract_once` for the title-first phase, for one run.

    Patched on the MODULE, because `extract_concept_union` resolves it as a
    module global at call time -- the same property `evals/extraction_cap`'s
    `--lever` relies on, and the one a reviewer caught missing in the #714
    probe when a default bound at definition made the arm inert.
    """
    original = concept._extract_once

    def patched(
        source_text: str, source_title: str, llm: LLMBackend
    ) -> list[concept.ExtractionResult]:
        return title_first_extract_once(
            source_text,
            source_title,
            llm,
            framing_shaped=framing_shaped,
            ledger=ledger,
        )

    concept._extract_once = patched
    try:
        yield
    finally:
        concept._extract_once = original


# --------------------------------------------------------------------------- #
# Running.                                                                     #
# --------------------------------------------------------------------------- #


@dataclass
class RunRecord:
    """One (arm, run) with its latency, its shape and its score."""

    arm: str
    run: int
    model: str
    latency_s: float
    produced: int
    retained: int
    titles: list[str] = field(default_factory=list)
    verdicts: list[str] = field(default_factory=list)
    subjects_found: int = 0
    subject_total: int = 0
    survey_titles: list[str] = field(default_factory=list)
    survey_calls: int = 0
    hydrate_calls: int = 0
    framing_dropped: int = 0
    hydration_lost: int = 0
    error: str | None = None

    @property
    def ok(self) -> bool:
        """The run completed. NOT "the run returned something".

        A run that legitimately retains nothing is a real observation with a
        real recall of 0.0, and folding it into the errored count would both
        overstate the error rate and delete the arm's worst outcomes from
        every average -- the failure mode
        [[a-gate-must-see-the-regime-it-guards]] describes, where dropping
        the bad runs makes the treatment look better than it is."""
        return self.error is None

    @property
    def scored(self) -> bool:
        """The run retained something, so precision has a denominator."""
        return self.ok and bool(self.verdicts)

    @property
    def recall(self) -> float:
        """Adjudicated subjects recovered, over the ground truth's own count."""
        return self.subjects_found / self.subject_total if self.subject_total else 0.0

    @property
    def precision(self) -> float:
        """Retained titles that name an adjudicated subject.

        Scored over the RETAINED objects, the same population the #694 oracle
        scores, so the two numbers are read against the same denominator.
        """
        if not self.verdicts:
            return 0.0
        hits = sum(1 for v in self.verdicts if v == cap.SUBJECT)
        return hits / len(self.verdicts)


def run_once(
    truth: cap.GroundTruth,
    source_text: str,
    source_title: str,
    llm: LLMBackend,
    *,
    arm: str,
    run: int,
    model: str,
) -> RunRecord:
    """Drive the REAL union pipeline once, under one arm. Never raises."""
    framing_shaped = concept._framing_shaped(source_title, source_text)
    ledger = LeverLedger()
    started = time.perf_counter()
    try:
        if arm == TREATMENT:
            with applied_lever(framing_shaped=framing_shaped, ledger=ledger):
                outcome = concept.extract_concept_union(
                    source_text, source_title=source_title, llm=llm
                )
        else:
            outcome = concept.extract_concept_union(
                source_text, source_title=source_title, llm=llm
            )
    except (OllamaError, SystemExit) as exc:
        # `SystemExit` is deliberate and is NOT covered by the `Exception`
        # clause below -- it inherits from `BaseException`. The prompt-drift
        # guard in `_survey_system_prompt` raises exactly that, from inside
        # this try via the treatment's survey call, so without this clause an
        # anticipated failure would escape a function whose docstring
        # promises it never raises and would abort `main`'s sweep loop,
        # discarding every run already collected. On this material that is
        # GPU-minutes per run, thrown away for a condition the probe saw
        # coming. `main` also checks the derivation once before the sweep, so
        # the ordinary drift case costs nothing at all; this clause is what
        # keeps a mid-sweep raise from taking the collected work with it.
        return RunRecord(
            arm=arm,
            run=run,
            model=model,
            latency_s=time.perf_counter() - started,
            produced=0,
            retained=0,
            error=f"{type(exc).__name__}: {exc}",
        )
    except Exception as exc:  # one bad run must not lose the sweep
        return RunRecord(
            arm=arm,
            run=run,
            model=model,
            latency_s=time.perf_counter() - started,
            produced=0,
            retained=0,
            error=f"unexpected {type(exc).__name__}: {exc}",
        )
    latency = time.perf_counter() - started
    titles = [obj.title for obj in outcome.objects]
    return RunRecord(
        arm=arm,
        run=run,
        model=model,
        latency_s=latency,
        produced=outcome.report.produced,
        retained=outcome.report.retained,
        titles=titles,
        verdicts=[cap.classify(t, truth) for t in titles],
        subjects_found=len(cap.subjects_found(titles, truth)),
        subject_total=truth.subject_count,
        survey_titles=sorted(set(ledger.survey_titles)),
        survey_calls=ledger.survey_calls,
        hydrate_calls=ledger.hydrate_calls,
        framing_dropped=ledger.framing_dropped,
        hydration_lost=ledger.hydration_lost,
    )


# --------------------------------------------------------------------------- #
# Reporting.                                                                   #
# --------------------------------------------------------------------------- #


def _spread(values: Sequence[float], fmt: str = "{:.2f}") -> str:
    """`mean ±sd [min-max] n=N`, the shape #694 requires of a metric."""
    if not values:
        return "no successful runs"
    mean = statistics.fmean(values)
    sd = statistics.stdev(values) if len(values) > 1 else 0.0
    return (
        f"{fmt.format(mean)} ±{fmt.format(sd)} "
        f"[{fmt.format(min(values))}-{fmt.format(max(values))}] n={len(values)}"
    )


ORACLE_RECALL = (0.80, 0.12)
ORACLE_PRECISION = (0.95, 0.08)
"""#694's baseline band on this exact fixture, at n=8.

The adoption bar quotes these rather than re-deriving them, and the baseline
arm measured here is printed beside them so a drifted baseline is visible
instead of being silently compared against a stale number.
"""


def title_overlap(baseline: Sequence[RunRecord], treatment: Sequence[RunRecord]) -> str:
    """Jaccard overlap of the retained title sets across arms.

    The confound control. The treatment changes the phase-1 reply shape, and
    this repo has five measured cases of a prompt change moving WHAT the model
    proposes. A low overlap means the arms did different work and the
    wall-clock comparison is not answering #728's question.
    """
    left = {cap.normalize(t) for r in baseline if r.ok for t in r.titles}
    right = {cap.normalize(t) for r in treatment if r.ok for t in r.titles}
    if not left and not right:
        return "no titles in either arm"
    union = left | right
    shared = left & right
    return (
        f"{len(shared) / len(union):.2f} "
        f"({len(shared)} shared of {len(union)} distinct titles; "
        f"{len(left - right)} baseline-only, {len(right - left)} treatment-only)"
    )


def _arm(records: Sequence[RunRecord], arm: str) -> list[RunRecord]:
    return [r for r in records if r.arm == arm]


def render(records: Sequence[RunRecord], *, fixture: str, model: str) -> str:
    """The report: latency, quality, the confound control, and the verdict."""
    base, treat = _arm(records, BASELINE), _arm(records, TREATMENT)
    ok_base = [r for r in base if r.ok]
    ok_treat = [r for r in treat if r.ok]
    # Precision needs a denominator, so it aggregates over runs that
    # retained something. Recall does not: a run that retained nothing
    # recovered no subject, which is a real 0.0 and belongs in the mean.
    scored_base = [r for r in base if r.scored]
    scored_treat = [r for r in treat if r.scored]

    lines = [
        "# Title-first phase 1, measured (#728 option 2)",
        "",
        f"`{model}`, fixture `{fixture}`, {len(base)} runs per arm.",
        "",
        "| metric | baseline | title-first |",
        "| --- | --- | --- |",
    ]

    def row(label: str, left: str, right: str) -> None:
        lines.append(f"| {label} | {left} | {right} |")

    row(
        "**wall clock (s)**",
        _spread([r.latency_s for r in ok_base], "{:.1f}"),
        _spread([r.latency_s for r in ok_treat], "{:.1f}"),
    )
    row(
        "recall",
        _spread([r.recall for r in ok_base]),
        _spread([r.recall for r in ok_treat]),
    )
    row(
        "precision",
        _spread([r.precision for r in scored_base]),
        _spread([r.precision for r in scored_treat]),
    )
    row(
        "retained objects",
        _spread([float(r.retained) for r in ok_base], "{:.1f}"),
        _spread([float(r.retained) for r in ok_treat], "{:.1f}"),
    )
    row("errored runs", str(len(base) - len(ok_base)), str(len(treat) - len(ok_treat)))
    # Counted apart from errors on purpose: a run that completed and
    # retained nothing is an extraction result, not a failure, and one
    # number covering both would let either move while the other hid it.
    row(
        "completed but empty",
        str(len(ok_base) - len(scored_base)),
        str(len(ok_treat) - len(scored_treat)),
    )
    row(
        "chat calls in `_extract_once`",
        f"{sum(r.survey_calls for r in base)} (unwrapped)",
        f"{sum(r.survey_calls for r in treat)} survey + "
        f"{sum(r.hydrate_calls for r in treat)} hydrate",
    )
    row(
        "framing objects killed before a body",
        "0 (killed after)",
        str(sum(r.framing_dropped for r in treat)),
    )
    row(
        "survivors the hydration lost", "n/a", str(sum(r.hydration_lost for r in treat))
    )

    lines += [
        "",
        f"**Title overlap between arms:** {title_overlap(base, treat)}",
        "",
        f"The #694 oracle band on this fixture is recall "
        f"{ORACLE_RECALL[0]:.2f} ±{ORACLE_RECALL[1]:.2f}, precision "
        f"{ORACLE_PRECISION[0]:.2f} ±{ORACLE_PRECISION[1]:.2f}. The baseline "
        f"measured here is printed above so a drifted baseline is visible "
        f"rather than compared against a stale number.",
        "",
    ]
    return "\n".join(lines)


def write_results(records: Sequence[RunRecord], *, model: str, fixture: str) -> Path:
    """Persist every observation. A sweep costs GPU minutes; never lose it."""
    results = _HERE / "results"
    results.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = results / f"title-first-{stamp}-{model.replace(':', '-')}.json"
    path.write_text(
        json.dumps(
            {
                "model": model,
                "fixture": fixture,
                "generated_at": stamp,
                "runs": [asdict(r) for r in records],
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    return path


# --------------------------------------------------------------------------- #
# Self-test: everything that does not need a model.                            #
# --------------------------------------------------------------------------- #


class _ScriptedLLM:
    """An `LLMBackend` returning canned replies in order."""

    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)
        self.sent: list[list[Message]] = []

    def chat(self, messages: Sequence[Message]) -> str:
        self.sent.append(list(messages))
        return self.replies.pop(0)


def _synthetic_truth() -> cap.GroundTruth:
    """A minimal ground truth for checks that never score anything."""
    parsed = cap.parse_ground_truth(
        "## Genuinely distinct subjects\n\n- Concept | A\n", name="synthetic"
    )
    absent = Path("/nonexistent/synthetic.md")
    return cap.GroundTruth(
        name="synthetic",
        gt_path=absent,
        source_path=absent,
        subjects=parsed.subjects,
        facets=parsed.facets,
        near_duplicates=parsed.near_duplicates,
        out_of_scope=parsed.out_of_scope,
        path_invariant=parsed.path_invariant,
    )


def _self_test() -> int:
    """Prove the prompt derivation, the lever and the scoring, with no model."""
    failures: list[str] = []

    def check(label: str, got: object, want: object) -> None:
        if got != want:
            failures.append(f"{label}: got {got!r}, want {want!r}")

    # The derivation. The rubric must survive byte-identical, and the
    # full-shape clause must be gone -- a survey prompt still asking for a
    # body would make the arm inert while every number still rendered.
    survey = _survey_system_prompt()
    head = concept._SYSTEM_PROMPT.partition(_SHAPE_MARKER)[0]
    check("the rubric is carried byte-identical", survey.startswith(head), True)
    check("the survey asks for no body", '"body"' in survey, False)
    check("the survey asks for no description", '"description"' in survey, False)
    check("the survey still names the closed vocabulary", '"Entity"' in survey, True)
    check(
        "production's full-shape clause is gone",
        '"description": "...", "body": "..."' in survey,
        False,
    )
    # The rubric's PROSE about `type_alternative` survives, because it sits
    # above the shape clause and cutting it would break the byte-identical
    # carry this arm depends on. The survey shape does not list the field, so
    # a model that emits it anyway is simply ignored by
    # `_validate_survey_item` -- a harmless wart, recorded rather than fixed,
    # because the alternative is a second hand-maintained copy of the rubric.
    check(
        "the surviving type_alternative prose is prose, not a shape field",
        '"type_alternative": ' in survey,
        False,
    )

    # The user turn is production's, so the meeting-shaped suppression holds.
    meeting_msgs = _survey_messages("A: hola\nB: hola\n" * 20, "Reunión semanal")
    check(
        "a meeting-shaped title is still suppressed in the user turn",
        "Reunión semanal" in meeting_msgs[1]["content"],
        False,
    )
    prose_msgs = _survey_messages("Some prose about a tool.", "A Prose Document")
    check(
        "a prose title is still carried",
        "A Prose Document" in prose_msgs[1]["content"],
        True,
    )

    # The lever: survey, framing drop, hydrate, and the loss channel.
    ledger = LeverLedger()
    llm = _ScriptedLLM(
        [
            json.dumps(
                [
                    {"type": "Event", "title": "Reunión de plataforma"},
                    {"type": "Decision", "title": "Cifrado de respaldos"},
                    {"type": "Concept", "title": "Latencia de búsqueda"},
                ]
            ),
            json.dumps(
                [
                    {
                        "type": "Decision",
                        "title": "Cifrado de respaldos",
                        "description": "d",
                        "body": "b",
                    }
                ]
            ),
        ]
    )
    out = title_first_extract_once(
        "A: hola\nB: hola\n" * 20,
        "Reunión semanal",
        llm,
        framing_shaped=True,
        ledger=ledger,
    )
    check("surveyed everything phase 1 proposed", ledger.surveyed, 3)
    check("the framing object died before a body", ledger.framing_dropped, 1)
    check(
        "one survey call and one hydrate call",
        (ledger.survey_calls, ledger.hydrate_calls),
        (1, 1),
    )
    check(
        "only hydrated survivors are returned",
        [r.title for r in out],
        ["Cifrado de respaldos"],
    )
    check("the missing survivor is counted, not invented", ledger.hydration_lost, 1)
    check(
        "the hydration turn lists only survivors",
        "Reunión de plataforma" in llm.sent[1][1]["content"],
        False,
    )

    # No survivors means no hydration call is spent at all.
    empty_ledger = LeverLedger()
    empty_llm = _ScriptedLLM(
        [json.dumps([{"type": "Event", "title": "Reunión de plataforma"}])]
    )
    check(
        "an all-framing window spends no hydration call",
        title_first_extract_once(
            "A: hola\nB: hola\n" * 20,
            "Reunión semanal",
            empty_llm,
            framing_shaped=True,
            ledger=empty_ledger,
        ),
        [],
    )
    check("and records that it did not", empty_ledger.hydrate_calls, 0)

    # The patch must be installed on the module and removed again.
    original = concept._extract_once
    with applied_lever(framing_shaped=True, ledger=LeverLedger()):
        check("the lever bites", concept._extract_once is original, False)
    check("the lever is removed", concept._extract_once is original, True)

    # The confound control.
    def _rec(arm: str, titles: list[str]) -> RunRecord:
        return RunRecord(
            arm=arm,
            run=1,
            model="m",
            latency_s=1.0,
            produced=len(titles),
            retained=len(titles),
            titles=titles,
        )

    check(
        "identical title sets overlap fully",
        title_overlap([_rec(BASELINE, ["A", "B"])], [_rec(TREATMENT, ["A", "B"])]),
        "1.00 (2 shared of 2 distinct titles; 0 baseline-only, 0 treatment-only)",
    )
    check(
        "disjoint title sets overlap at zero",
        title_overlap([_rec(BASELINE, ["A"])], [_rec(TREATMENT, ["B"])]).startswith(
            "0.00"
        ),
        True,
    )

    # The CRITICAL the reliability lens found: `SystemExit` is a
    # `BaseException`, so `except Exception` never saw the drift guard's own
    # raise and a sweep would have died with its collected runs unwritten.
    original_union = concept.extract_concept_union

    def _exits(*_args: object, **_kwargs: object) -> Any:
        raise SystemExit("simulated prompt drift")

    concept.extract_concept_union = _exits
    try:
        drifted = run_once(
            _synthetic_truth(),
            "text",
            "title",
            _ScriptedLLM([]),
            arm=BASELINE,
            run=1,
            model="m",
        )
    finally:
        concept.extract_concept_union = original_union
    check("a SystemExit becomes an errored run", drifted.error is not None, True)
    check(
        "and names itself so the cause is not guessed",
        "SystemExit" in (drifted.error or ""),
        True,
    )

    # The WARNING: a completed-but-empty run is an observation, not an error.
    empty = RunRecord(
        arm=BASELINE, run=1, model="m", latency_s=1.0, produced=0, retained=0
    )
    check("an empty run completed", empty.ok, True)
    check("but has no precision denominator", empty.scored, False)
    check("and its recall is a real zero", empty.recall, 0.0)
    check("an errored run is not ok", drifted.ok, False)

    if failures:
        print("SELF-TEST FAILED:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print(
        "SELF-TEST PASSED: the survey prompt is derived from production and "
        "drops only the shape clause, the user turn keeps the meeting-shaped "
        "suppression, the lever surveys/gates/hydrates and counts its own "
        "loss channel, the patch is installed and removed, and the overlap "
        "control reads both extremes."
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--fixture", default=DEFAULT_FIXTURE)
    parser.add_argument("--host", default=None)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        return _self_test()

    gt_path = cap._GROUND_TRUTH / f"{args.fixture}.md"
    if not gt_path.is_file():
        print(f"error: no ground truth at {gt_path}", file=sys.stderr)
        return 2
    truth = cap.load_ground_truth(gt_path)
    if not truth.source_exists:
        print(f"error: source missing for {args.fixture}", file=sys.stderr)
        return 2
    source_text = truth.read_source()
    source_title = cap.resolve_title(source_text, truth.source_path)

    # #726's guard, borrowed: this lever only exists on the chunked path, so
    # a fixture that has drifted below the boundary would measure a pipeline
    # the report does not describe.
    violation = cap.path_invariant_violation(
        truth, source_text=source_text, title=source_title
    )
    if violation is not None:
        print(f"error: {violation}", file=sys.stderr)
        return 2

    # The prompt derivation is exercised ONCE, before a single GPU second is
    # spent. `_survey_system_prompt` is the drift guard, and the cheapest
    # moment to discover that production moved its reply-shape clause is
    # before the sweep rather than four windows into the treatment arm.
    _survey_system_prompt()

    llm = OllamaClient(model=args.model, host=args.host, timeout=args.timeout)
    records: list[RunRecord] = []
    for arm in (BASELINE, TREATMENT):
        for run in range(1, args.runs + 1):
            print(f"=== {arm} run {run}/{args.runs}", flush=True)
            record = run_once(
                truth,
                source_text,
                source_title,
                llm,
                arm=arm,
                run=run,
                model=args.model,
            )
            records.append(record)
            if record.error:
                print(f"  ERROR {record.error}", flush=True)
            else:
                print(
                    f"  {record.latency_s:.1f}s retained={record.retained} "
                    f"recall={record.recall:.2f} precision={record.precision:.2f}",
                    flush=True,
                )

    report = render(records, fixture=args.fixture, model=args.model)
    saved = write_results(records, model=args.model, fixture=args.fixture)
    # The rendered table lands beside its raw runs, never on `report.md`.
    # `report.md` carries a human's reading of a sweep, and a probe that
    # overwrites it turns every re-run into silent loss of the analysis the
    # numbers were published with.
    saved.with_suffix(".md").write_text(report, encoding="utf-8")
    print()
    print(report)
    print(f"Saved raw runs: {saved}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
