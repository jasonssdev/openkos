"""Would a CONTAINMENT twin rule delete anything it should not? (#722)

Reads the stored sweeps already on disk and makes ZERO model calls, the same
move as `measure_acronym_fabrication.py` and `measure_expansion_grounding.py`.

## The question

`_drop_source_title_twins` compares a candidate's title to the source's own
through `_normalize_title` EQUALITY. #722 was filed on a candidate equality
cannot see: on `evals/stage_attrition`'s `es-anchored` fixture the extractor
returned `Proyecto de memoria institucional`, which appears in the source only
inside its title, `Reunión de coordinación del proyecto de memoria
institucional`. A title-derived object -- the class the twin rule exists to
delete (#413 / #459 / #522) -- that the twin rule keeps.

The obvious repair is to widen "same title" from equality to CONTAINMENT. This
probe scores that repair before anyone writes it, on the #613 / #622 / #630 /
#699 precedent: a deterministic treatment is measured against stored data
first, and a treatment that fails ships as a measurement.

## The bar, and why it is zero false positives

`_drop_source_title_twins` DELETES. #699's levers were judged on recall and
precision because fragmentation costs precision directly; a deletion rule is
not in that class. A wrongly deleted object is silent data loss with no
recovery path -- the same argument the drop rule's own docstring makes for the
`Procedure` exemption -- so the bar here is the one #622 and #630 were held to:
**zero ground-truth subjects deleted**, over exposure large enough for a zero
to mean something.

Exposure is reported beside every verdict, never assumed. A zero-FP figure on
a population the rule could never have touched says nothing, which
`evals/participant_anchor` already demonstrated once by scoring a gate that
had discarded nothing in nine runs.

## The two pools, and what each can answer

**The oracle corpus** (`results/runs-*.json`) carries per-run titles AND the
adjudicated ground truth `run_cap_eval.py` parses, so every deletion gets a
verdict: `subject` is a false positive, anything else is not. This pool
answers the question.

**The transcript probes** (`stage_attrition`, `participant_anchor`,
`named_person_volume`) carry per-run titles and a known source title, but no
title-level ground truth -- their subject lists are prose, not matchable
titles. This pool can only count EXPOSURE and surface the hits for a human,
which is exactly what is needed to say whether the meeting-shaped narrowing
below has been tested at all.

`evals/discarded_generation` is deliberately excluded: its `discarded_titles`
are objects production ALREADY dropped, a different population from the
retained set this rule would act on.

## Variants scored

- `equality` -- production today, the baseline every arm is read against.
- `containment` -- the candidate's tokens are a CONTIGUOUS, PROPER
  subsequence of the source title's, at or above a token floor (`--floor`).
- `containment-meeting` -- `containment`, but only when the source title is
  meeting-shaped (`_MEETING_SHAPED_TITLE_RE`). The narrowing the numbers
  themselves suggest; reported with its exposure so it is not mistaken for a
  cleared bar.

Token-contiguous rather than raw substring, deliberately: a raw substring
matches inside a word, which is the bug `#720`'s grounding check shipped and
two review lenses caught (`Ana` inside `mañana`).

Run from the repository root:

    uv run python evals/extraction_cap/measure_title_containment.py
    uv run python evals/extraction_cap/measure_title_containment.py --floor 3
    uv run python evals/extraction_cap/measure_title_containment.py --self-test
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parents[1]
sys.path.insert(0, str(_HERE))

import run_cap_eval as cap  # noqa: E402

from openkos.extraction.concept import (  # noqa: E402
    _MEETING_SHAPED_TITLE_RE,
    _TWIN_EXEMPT_TYPE,
)

DEFAULT_FLOOR = 2
"""Minimum tokens a candidate needs before containment may fire.

Two, not one, as the starting point: a single-token candidate contained in a
multi-word title is the weakest possible evidence of restatement, and the
floor is the only knob #722 itself proposed. `--floor` sweeps it.
"""

_ANCHOR_PROBE = (
    _REPO_ROOT / "evals" / "participant_anchor" / "run_participant_anchor_probe.py"
)
_TRANSCRIPT_POOLS = (
    ("stage_attrition", "final_objects"),
    ("named_person_volume", "objects"),
    ("participant_anchor", "candidates"),
)


# --------------------------------------------------------------------------- #
# The rule under test.                                                         #
# --------------------------------------------------------------------------- #

Match = Callable[[str, str], bool]


def tokens(value: str) -> list[str]:
    """The candidate's tokens under the harness's ONE normalization."""
    return cap.normalize(value).split()


def equal(candidate: str, source_title: str) -> bool:
    """Production's rule today: normalized equality, nothing else."""
    return cap.normalize(candidate) == cap.normalize(source_title)


def contained(candidate: str, source_title: str, *, floor: int) -> bool:
    """The candidate is a CONTIGUOUS, PROPER token subsequence of the title.

    Proper: a candidate as long as the title is equality's business, not this
    one's. Contiguous tokens rather than a raw substring so `Agent` cannot
    match inside `Agents`, and `Ana` cannot match inside `mañana`.
    """
    cand, title = tokens(candidate), tokens(source_title)
    if len(cand) < floor or not cand or len(cand) >= len(title):
        return False
    return any(
        title[i : i + len(cand)] == cand for i in range(len(title) - len(cand) + 1)
    )


def make_match(variant: str, *, floor: int) -> Match:
    """The `(candidate, source_title) -> bool` predicate one variant tests."""
    if variant == "equality":
        return equal

    def widened(candidate: str, source_title: str) -> bool:
        if equal(candidate, source_title):
            return True
        if variant == "containment-meeting" and not _MEETING_SHAPED_TITLE_RE.search(
            source_title
        ):
            return False
        return contained(candidate, source_title, floor=floor)

    return widened


def deleted_indices(
    objects: list[tuple[str, str | None]], source_title: str, *, match: Match
) -> set[int]:
    """What `_drop_source_title_twins` would DELETE from one run's final list.

    Models the whole rule, not just its comparison: the `Procedure` exemption
    (#413) and the floor that returns the list unchanged when every object is
    a twin or only one exists. Scoring the comparison alone would count
    deletions the shipped rule never performs -- the same reading error
    `extraction_collapse`'s `title_twin_runs` made before it consulted the
    type.
    """
    if len(objects) <= 1:
        return set()
    twins = {
        index
        for index, (title, kind) in enumerate(objects)
        if match(title, source_title) and kind != _TWIN_EXEMPT_TYPE
    }
    if not twins or len(twins) == len(objects):
        return set()
    return twins


# --------------------------------------------------------------------------- #
# Pools.                                                                       #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Run:
    """One stored run: what it retained, and under which source title."""

    pool: str
    fixture: str
    arm: str
    source_title: str
    objects: tuple[tuple[str, str | None], ...]
    truth: cap.GroundTruth | None
    """The adjudicated ground truth, when this pool has one."""


@dataclass
class Verdict:
    """One variant's outcome over one pool."""

    runs: int = 0
    objects: int = 0
    reach: int = 0
    """Objects the widened comparison matches that equality does not -- the
    rule's opportunity to act, counted BEFORE the exemption and the floor."""
    eligible: int = 0
    """Objects on a source this variant is even ALLOWED to look at.

    Reported beside `reach` because the two answer different questions and the
    denominator is the one that decides whether a zero means anything.
    `containment-meeting` ignores every prose-titled source, so scoring its
    reach against the whole corpus prints `0 of 1038` for a rule that was only
    ever offered a fraction of that -- the complement this repo has already
    been burned by hiding."""
    deletions: list[tuple[str, str, str, str]] = field(default_factory=list)
    """`(fixture, arm, title, verdict)` per object the variant would delete
    and production today would not."""

    @property
    def false_positives(self) -> int:
        return sum(1 for *_rest, verdict in self.deletions if verdict == cap.SUBJECT)


def _oracle_runs() -> list[Run]:
    """Every stored cap-eval run whose fixture source is in this checkout."""
    truths = {t.name: t for t in cap.discover_ground_truth()}
    truths.update(
        {
            t.name: t
            for t in cap.discover_ground_truth(
                cap._AMI_GROUND_TRUTH, sources_dir=cap._AMI_SOURCES
            )
        }
    )
    runs: list[Run] = []
    for path in sorted((_HERE / "results").glob("runs-*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        for outcome in data.get("outcomes", ()):
            truth = truths.get(outcome["fixture"])
            if truth is None or not truth.source_exists or outcome["status"] != "ok":
                continue
            text = truth.read_source()
            titles = outcome["titles"]
            types = outcome.get("types") or [None] * len(titles)
            runs.append(
                Run(
                    pool="oracle",
                    fixture=outcome["fixture"],
                    arm=outcome["arm"],
                    source_title=cap.resolve_title(
                        text,
                        truth.source_path,
                        stem_title=outcome["arm"].endswith(cap._STEM_TITLE_SUFFIX),
                    ),
                    # strict: a stored run whose type list does not match its
                    # title list is corrupt, and the twin rule reads BOTH
                    # halves of the pair. Truncating silently would score the
                    # exemption against the wrong objects and still print a
                    # number.
                    objects=tuple(zip(titles, types, strict=True)),
                    truth=truth,
                )
            )
    return runs


def _transcript_titles() -> dict[str, str]:
    """Source title per transcript fixture, read from the probe that owns it."""
    spec = importlib.util.spec_from_file_location("_anchor_probe", _ANCHOR_PROBE)
    if spec is None or spec.loader is None:
        return {}
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return {arm.name: arm.title for arm in module.build_arms()}


def _transcript_runs() -> list[Run]:
    """Every stored transcript-probe run carrying titles and a known title."""
    titles = _transcript_titles()
    runs: list[Run] = []
    for pool, key in _TRANSCRIPT_POOLS:
        for path in sorted((_REPO_ROOT / "evals" / pool / "results").glob("*.jsonl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                record = json.loads(line)
                fixture = record.get("fixture") or record.get("arm", "")
                source_title = titles.get(fixture)
                if source_title is None:
                    continue
                objects = [
                    (obj["title"], obj.get("type"))
                    for obj in record.get(key, ())
                    if obj.get("title")
                ]
                if not objects:
                    continue
                runs.append(
                    Run(
                        pool=pool,
                        fixture=fixture,
                        arm=str(record.get("arm", "")),
                        source_title=source_title,
                        objects=tuple(objects),
                        truth=None,
                    )
                )
    return runs


def score(runs: list[Run], *, variant: str, floor: int) -> Verdict:
    """One variant's deletions and exposure over one pool of stored runs."""
    match = make_match(variant, floor=floor)
    verdict = Verdict()
    meeting_only = variant == "containment-meeting"
    for run in runs:
        verdict.runs += 1
        verdict.objects += len(run.objects)
        if not meeting_only or _MEETING_SHAPED_TITLE_RE.search(run.source_title):
            verdict.eligible += len(run.objects)
        for title, _kind in run.objects:
            if not equal(title, run.source_title) and match(title, run.source_title):
                verdict.reach += 1
        baseline = deleted_indices(list(run.objects), run.source_title, match=equal)
        widened = deleted_indices(list(run.objects), run.source_title, match=match)
        for index in sorted(widened - baseline):
            title = run.objects[index][0]
            verdict.deletions.append(
                (
                    run.fixture,
                    run.arm,
                    title,
                    cap.classify(title, run.truth) if run.truth else "unadjudicated",
                )
            )
    return verdict


# --------------------------------------------------------------------------- #
# Report.                                                                      #
# --------------------------------------------------------------------------- #

_VARIANTS = ("containment", "containment-meeting")


def _render(pool_name: str, runs: list[Run], *, floor: int, adjudicated: bool) -> None:
    if not runs:
        print(f"\n## {pool_name}\n\n  no stored runs in this checkout")
        return
    objects = sum(len(r.objects) for r in runs)
    print(f"\n## {pool_name}")
    print(f"\n  {len(runs)} runs, {objects} retained objects")
    for variant in _VARIANTS:
        verdict = score(runs, variant=variant, floor=floor)
        print(
            f"\n  ### {variant} (floor {floor})\n"
            f"    eligible:  {verdict.eligible} of {verdict.objects} objects sit on "
            f"a source this variant may look at\n"
            f"    reach:     {verdict.reach} of {verdict.eligible} eligible objects "
            f"matched by containment and not by equality\n"
            f"    deletions: {len(verdict.deletions)} objects production keeps today"
        )
        if not verdict.deletions:
            if verdict.eligible == 0:
                print(
                    "    verdict:   UNFALSIFIABLE — no object in this pool was even "
                    "offered to the rule"
                )
            elif verdict.reach == 0:
                print(
                    f"    verdict:   UNFALSIFIABLE — the rule was offered "
                    f"{verdict.eligible} objects and matched none, so a zero here is "
                    f"absence of opportunity, not a cleared bar"
                )
            else:
                print(
                    "    verdict:   zero deletions (every match hit the exemption "
                    "or the floor)"
                )
            continue
        counts = Counter(v for *_rest, v in verdict.deletions)
        for title_key, count in Counter(
            (fixture, title, v) for fixture, _arm, title, v in verdict.deletions
        ).most_common():
            fixture, title, v = title_key
            print(f"      {count:4d}x [{v:14s}] {fixture}: {title!r}")
        print(f"    verdicts:  {dict(counts)}")
        if adjudicated:
            outcome = "BAR CLEARED" if not verdict.false_positives else "REJECTED"
            print(
                f"    FALSE POSITIVES: {verdict.false_positives} ground-truth "
                f"subjects deleted — {outcome}"
            )
        else:
            print(
                "    no title-level ground truth in this pool: every hit above "
                "needs a human call before it counts as caught or as lost"
            )


# --------------------------------------------------------------------------- #
# Self-test: the rule model, with no stored data and no model.                 #
# --------------------------------------------------------------------------- #

_MEETING = "Reunión de coordinación del proyecto de memoria institucional"
_PROSE = "Building a Research Agent with the Claude Agent SDK"


def _self_test() -> int:
    """Prove the comparison, the floor and the whole-rule model."""
    failures: list[str] = []

    def check(label: str, got: object, want: object) -> None:
        if got != want:
            failures.append(f"{label}: got {got!r}, want {want!r}")

    # The comparison.
    check("equality still matches itself", equal(_MEETING, _MEETING), True)
    check(
        "the #722 fragment is contained",
        contained("Proyecto de memoria institucional", _MEETING, floor=2),
        True,
    )
    check(
        "a whole-title candidate is not a PROPER fragment",
        contained(_MEETING, _MEETING, floor=1),
        False,
    )
    check(
        "below the floor it does not fire",
        contained("Proyecto", _MEETING, floor=2),
        False,
    )
    check(
        "non-contiguous tokens do not match",
        contained("proyecto institucional", _MEETING, floor=2),
        False,
    )
    # The bug #720 shipped and two lenses caught: a raw substring matches
    # inside a word. Token-contiguous must not.
    check(
        "no match inside a word",
        contained("Ana", "Reunión de mañana con el equipo", floor=1),
        False,
    )

    # The variant gate.
    prose_match = make_match("containment", floor=2)
    meeting_only = make_match("containment-meeting", floor=2)
    check(
        "containment fires on a prose title",
        prose_match("Claude Agent SDK", _PROSE),
        True,
    )
    check(
        "the narrowed variant does not",
        meeting_only("Claude Agent SDK", _PROSE),
        False,
    )
    check(
        "the narrowed variant still fires on a meeting title",
        meeting_only("Proyecto de memoria institucional", _MEETING),
        True,
    )
    check(
        "equality survives in every variant",
        [m(_MEETING, _MEETING) for m in (prose_match, meeting_only)],
        [True, True],
    )

    # The whole rule, not just its comparison.
    twin = ("Proyecto de memoria institucional", "Concept")
    other = ("Rotación de credenciales", "Decision")
    check(
        "a twin beside a non-twin is deleted",
        deleted_indices([twin, other], _MEETING, match=meeting_only),
        {0},
    )
    check(
        "the single-object floor keeps it",
        deleted_indices([twin], _MEETING, match=meeting_only),
        set(),
    )
    check(
        "the all-twins floor keeps them",
        deleted_indices(
            [twin, ("Proyecto de memoria", "Concept")], _MEETING, match=meeting_only
        ),
        set(),
    )
    check(
        "a Procedure is never a twin",
        deleted_indices(
            [("Proyecto de memoria institucional", _TWIN_EXEMPT_TYPE), other],
            _MEETING,
            match=meeting_only,
        ),
        set(),
    )
    check(
        "production today deletes none of them",
        deleted_indices([twin, other], _MEETING, match=equal),
        set(),
    )

    if failures:
        print("SELF-TEST FAILED:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print(
        "SELF-TEST PASSED: proper contiguous-token containment, the token floor, "
        "the meeting-shaped narrowing, and the whole-rule model (Procedure "
        "exemption, single-object floor, all-twins floor)."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--floor",
        type=int,
        default=DEFAULT_FLOOR,
        help="Minimum tokens before containment may fire.",
    )
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        return _self_test()

    print(f"# Title-containment twin rule (#722) — floor {args.floor}, no model calls")
    _render(
        "oracle corpus (adjudicated)",
        _oracle_runs(),
        floor=args.floor,
        adjudicated=True,
    )
    _render(
        "transcript probes (exposure only)",
        _transcript_runs(),
        floor=args.floor,
        adjudicated=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
