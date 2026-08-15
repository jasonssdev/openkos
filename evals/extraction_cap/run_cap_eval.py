"""Cap-and-decay measurement harness for `examples/extraction-corpus/` (#404).

MANUAL eval tool (NOT pytest, NOT part of the shipped package). It drives the
REAL pipeline -- `openkos.extraction.concept.extract_concept` over
`openkos.llm.ollama.OllamaClient` -- across the hand-written corpus fixtures
and answers the two questions #404 turns on:

1. **Is the cap discarding REAL material?** Scored as `cap_cost`: genuine
   subjects the model DID produce, which `_MAX_OBJECTS_PER_SOURCE` then threw
   away. A non-zero `cap_cost` is the cap-too-low half of #404, measured
   rather than argued.
2. **Where does enumeration decay start?** Scored as the POSITION CURVE: for
   each reply position `k`, how often position `k` held a genuine subject, a
   known facet, a near-duplicate, something out of scope, or something nobody
   has judged yet. Those are four distinct ways to not be a subject and they
   are counted apart, because folding any two together lets one failure
   inflate another's figure. `extract_concept`'s
   docstring asserts that "the model front-loads genuine subjects and degrades
   into facets of the last one afterwards, so reply order correlates with
   quality" and that any future ranking "has to be measured AGAINST this
   prefix". That claim is currently ASSERTED. This curve is what tests it.

## Why this exists next to `evals/model_spike/run_spike.py`

`run_spike.py`'s `Fixture.target_types` is a MULTISET OF TYPES. `("Concept",)*8`
cannot tell `Agent Security` (a genuine subject) from `Skill Collaboration` (a
facet of one), and that distinction is the whole of #404. This harness scores
by TITLE against the hand-written ground truth, so it can separate the two.

## The matching rule is deliberately dumb, and that is the point

A title matches a ground-truth subject ONLY on an exact normalized comparison
(strip, casefold, collapse whitespace, strip wrapping backticks/quotes), or on
a HUMAN-CURATED alias listed in the ground-truth file itself. There is no fuzzy
matching, no token overlap, no embedding similarity, anywhere in this file.

That is not laziness. A fuzzy matcher would credit `Skill Creation Process`
against the subject `Skill Creator` -- a facet scored as a subject -- and the
harness would then report the defect as smaller than it is. A measurement whose
scorer can be generous in the direction of the hypothesis measures nothing.

Everything matching no ground-truth list at all lands in the **UNJUDGED
ADJUDICATION QUEUE** at the end of the report. That queue is the harness's real
output loop: a human reads it and files each title as an alias, a facet, a
near-duplicate, an out-of-scope mention, or a subject nobody had listed. The
ground truth grows by human judgment; the scores get sharper each round. See
the corpus README -- ground truth "cannot be model-generated", and that applies
to the matcher too.

## What it deliberately does NOT print

There is no corpus-wide average. `small-04-pre-build-skills` is the SAME lesson
as `large-03-skills-vs-tools`, in Spanish at ~45% of the length, so averaging
them counts one document's evidence twice; both ground-truth files say so and
forbid it. Rather than special-case the pair, this harness never aggregates
across fixtures at all. Three real documents of different sizes and languages
were never a population to take a mean over.

## Running it

The corpus sources are git-ignored (third-party material, see the corpus
`.gitignore`), so a fresh clone has ground truth but no sources. Missing
sources are reported and skipped, never crashed on.

    uv run python evals/extraction_cap/run_cap_eval.py
    uv run python evals/extraction_cap/run_cap_eval.py --temperature 0.0 --runs 5

Prove the parsing/scoring/report logic with no model at all:

    uv run python evals/extraction_cap/run_cap_eval.py --self-test
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import tempfile
import time
import urllib.request
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple

from openkos.extraction.concept import (
    _TWIN_EXEMPT_TYPE,
    ExtractionOutcome,
    ExtractionReport,
    ExtractionResult,
    extract_concept,
    extract_concept_union,
)
from openkos.llm.ollama import OllamaClient, OllamaError
from openkos.source_title import derive_source_title

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CORPUS = _REPO_ROOT / "examples" / "extraction-corpus"
_SOURCES = _CORPUS / "sources"
_GROUND_TRUTH = _CORPUS / "ground-truth"
_AMI_CORPUS = _REPO_ROOT / "evals" / "decision_extraction"
_AMI_SOURCES = _AMI_CORPUS / "sources"
_AMI_GROUND_TRUTH = _AMI_CORPUS / "ground_truth"

DEFAULT_MODEL = "qwen3:8b"
DEFAULT_RUNS = 5
DEFAULT_TIMEOUT = 600.0
"""Matches `config.DEFAULT_CHAT_TIMEOUT` since #405. The 120s default was
measured timing out 5/5 on exactly this material, which is what made #405 and
#404 share a root cause -- a runaway enumeration no deadline can fix."""


# --------------------------------------------------------------------------- #
# Normalization: the ONLY comparison this harness performs.                    #
# --------------------------------------------------------------------------- #

_WRAPPERS = "`\"'*"


def normalize(value: str) -> str:
    """Strip, casefold, collapse internal whitespace, drop wrapping marks.

    Mirrors `_drop_source_title_twins`'s normalization (strip + casefold +
    collapsed whitespace) so a twin detected there is detected here, plus a
    strip of wrapping backticks/quotes/asterisks that only ever come from
    markdown emphasis in a hand-written ground-truth bullet.

    This is the harness's ENTIRE notion of "the same title". Nothing else in
    this file compares strings.
    """
    return " ".join(value.strip().strip(_WRAPPERS).strip().casefold().split())


# --------------------------------------------------------------------------- #
# Ground truth: parsed from the hand-written markdown, strictly.               #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Subject:
    """One genuinely distinct subject a source is actually about."""

    type: str
    title: str
    aliases: tuple[str, ...] = ()
    """Human-curated alternate phrasings that count as THIS subject. Populated
    by a maintainer from the adjudication queue, never by a model."""

    @property
    def keys(self) -> tuple[str, ...]:
        """Every normalized string that identifies this subject."""
        return tuple(normalize(v) for v in (self.title, *self.aliases))


class GroundTruthError(ValueError):
    """The ground-truth file is malformed or internally inconsistent.

    Raised rather than tolerated. A ground truth that half-parses produces a
    score that looks fine and means nothing, which is worse than no score.
    """


@dataclass(frozen=True)
class GroundTruth:
    """One hand-written expectation, parsed from `ground-truth/<name>.md`."""

    name: str
    gt_path: Path
    source_path: Path
    subjects: tuple[Subject, ...]
    facets: tuple[str, ...]
    near_duplicates: tuple[tuple[str, str], ...]
    """`(canonical subject, the duplicate phrasing)` pairs.

    A near-duplicate is NOT an alias. An alias is the same subject under
    another name and costs nothing; a near-duplicate is a SECOND object naming
    a subject already emitted, which burns a cap slot that a genuine subject
    could have used. Scoring it as an alias would hide that cost, which is why
    it gets its own verdict."""
    out_of_scope: tuple[str, ...] = ()
    """Things the source MENTIONS that it is not ABOUT -- a tool the case study
    researches, a product named in passing. Separate from `facets` on purpose.

    A facet is decay: the model ran out of subjects and started shredding one
    into attributes. An out-of-scope emission is a different error, and lumping
    the two would inflate the decay figure -- which is exactly the number that
    argues AGAINST raising the cap. A scorer must not be generous in either
    direction, so the two are counted apart."""

    @property
    def subject_count(self) -> int:
        """The number a run is scored against."""
        return len(self.subjects)

    @property
    def source_exists(self) -> bool:
        """Whether the git-ignored raw source is present in this checkout."""
        return self.source_path.is_file()

    def read_source(self) -> str:
        """Read the raw source text (call only when `source_exists`)."""
        return self.source_path.read_text(encoding="utf-8")

    def subject_for(self, title: str) -> Subject | None:
        """The subject `title` names exactly (or by curated alias), else None."""
        key = normalize(title)
        for subject in self.subjects:
            if key in subject.keys:
                return subject
        return None

    def is_facet(self, title: str) -> bool:
        """Whether `title` exactly names a heading the ground truth calls a facet."""
        key = normalize(title)
        return any(normalize(f) == key for f in self.facets)

    def is_out_of_scope(self, title: str) -> bool:
        """Whether `title` exactly names something the ground truth excluded."""
        key = normalize(title)
        return any(normalize(o) == key for o in self.out_of_scope)

    def near_duplicate_of(self, title: str) -> str | None:
        """The subject `title` redundantly re-names, else None."""
        key = normalize(title)
        for canonical, duplicate in self.near_duplicates:
            if normalize(duplicate) == key:
                return canonical
        return None


_SECTION_RE = re.compile(r"^##\s+(.*\S)\s*$", re.MULTILINE)
_BULLET_RE = re.compile(r"^-\s+(.*\S)\s*$", re.MULTILINE)
_COUNT_RE = re.compile(r"\*\*Count:\s*(\d+)")
_NONE_RE = re.compile(r"^\s*(\*\*)?none\b", re.IGNORECASE)

_SUBJECTS_HEADING = "genuinely distinct subjects"
_FACETS_HEADING = "facets, not subjects"
_NEAR_DUP_HEADING = "near-duplicates"
_ALIASES_HEADING = "aliases"
_OUT_OF_SCOPE_HEADING = "out of scope"


def _sections(text: str) -> dict[str, str]:
    """Split a ground-truth file into `## heading` -> body, keyed casefolded.

    Only `##` is a section boundary. `###` and deeper stay inside their parent,
    and the preamble before the first `##` is dropped -- it is prose framing,
    never data.
    """
    matches = list(_SECTION_RE.finditer(text))
    out: dict[str, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        out[match.group(1).strip().casefold()] = text[match.end() : end]
    return out


def _find_section(sections: dict[str, str], needle: str) -> str | None:
    """Locate a section by a casefolded SUBSTRING of its heading.

    Substring, not equality, because the hand-written files decorate headings
    (`## KNOWN RULE COLLISION -- this fixture exposes it on purpose`) and a
    maintainer writing `## Facets, not subjects (revised)` must not silently
    lose the section.
    """
    for heading, body in sections.items():
        if needle in heading:
            return body
    return None


def _bullets(body: str) -> list[str]:
    """Every `- ` bullet in a section body, in order, prose paragraphs ignored."""
    return [m.group(1).strip() for m in _BULLET_RE.finditer(body)]


def _parse_aliases(body: str) -> dict[str, tuple[str, ...]]:
    """Parse `- Canonical Title | alias one | alias two` lines.

    Keyed by the NORMALIZED canonical title so it can be joined against the
    subject list regardless of how either was capitalized.
    """
    out: dict[str, tuple[str, ...]] = {}
    for bullet in _bullets(body):
        parts = [p.strip() for p in bullet.split("|")]
        if len(parts) < 2:
            raise GroundTruthError(
                f"alias line needs `Canonical Title | alias [| alias]`: {bullet!r}"
            )
        canonical, *aliases = parts
        kept = tuple(a for a in aliases if a)
        if not kept:
            raise GroundTruthError(f"alias line lists no alias: {bullet!r}")
        out[normalize(canonical)] = out.get(normalize(canonical), ()) + kept
    return out


def _parse_near_duplicates(body: str) -> tuple[tuple[str, str], ...]:
    """Parse `- Canonical Subject | the duplicate phrasing` lines.

    Same pipe shape as `## Aliases`, and the distinction between the two is the
    whole point: an alias is free, a near-duplicate costs a cap slot.
    """
    if _NONE_RE.match(body.strip()):
        return ()
    pairs: list[tuple[str, str]] = []
    for bullet in _bullets(body):
        canonical, sep, duplicate = bullet.partition("|")
        if not sep or not canonical.strip() or not duplicate.strip():
            raise GroundTruthError(
                f"near-duplicate line needs `Canonical Subject | the duplicate "
                f"phrasing`: {bullet!r}"
            )
        pairs.append((canonical.strip(), duplicate.strip()))
    return tuple(pairs)


class Parsed(NamedTuple):
    """The four judgment lists a ground-truth file carries."""

    subjects: tuple[Subject, ...]
    facets: tuple[str, ...]
    near_duplicates: tuple[tuple[str, str], ...]
    out_of_scope: tuple[str, ...]


def parse_ground_truth(text: str, *, name: str = "<memory>") -> Parsed:
    """Parse the judgment lists out of a ground-truth file.

    Strict on the things that would corrupt a score silently:

    - The subjects section must exist and yield at least one `Type | Title`.
    - A declared `**Count: N**` must EQUAL the number of bullets. The count is
      what the prose reasons about ("Count: 7 -- the same seven as ..."), so a
      file where the two disagree was edited half-way, and every recall figure
      computed from it would be wrong by a silent constant.

    Lenient where leniency costs nothing: the aliases and out-of-scope sections
    are optional, and any list section reading "None." parses as empty.
    """
    sections = _sections(text)

    subjects_body = _find_section(sections, _SUBJECTS_HEADING)
    if subjects_body is None:
        raise GroundTruthError(f"{name}: no `## Genuinely distinct subjects` section")

    aliases_body = _find_section(sections, _ALIASES_HEADING)
    alias_map = _parse_aliases(aliases_body) if aliases_body else {}

    subjects: list[Subject] = []
    for bullet in _bullets(subjects_body):
        if "|" not in bullet:
            raise GroundTruthError(
                f"{name}: subject bullet must read `Type | Title`, got {bullet!r}"
            )
        obj_type, _, title = bullet.partition("|")
        obj_type, title = obj_type.strip(), title.strip()
        if not obj_type or not title:
            raise GroundTruthError(f"{name}: subject bullet is incomplete: {bullet!r}")
        subjects.append(
            Subject(
                type=obj_type, title=title, aliases=alias_map.get(normalize(title), ())
            )
        )

    if not subjects:
        raise GroundTruthError(
            f"{name}: subjects section lists no `Type | Title` bullet"
        )

    declared = _COUNT_RE.search(subjects_body)
    if declared is not None and int(declared.group(1)) != len(subjects):
        raise GroundTruthError(
            f"{name}: declares **Count: {declared.group(1)}** but lists "
            f"{len(subjects)} subject bullets -- the file was edited half-way, "
            f"and every recall figure from it would be wrong by that difference"
        )

    unknown = {normalize(k) for k in alias_map} - {normalize(s.title) for s in subjects}
    if unknown:
        raise GroundTruthError(
            f"{name}: aliases name subjects that do not exist: {sorted(unknown)}"
        )

    def _listed(needle: str) -> tuple[str, ...]:
        body = _find_section(sections, needle)
        if body is None:
            return ()
        if _NONE_RE.match(body.strip()):
            return ()
        return tuple(_bullets(body))

    near_dup_body = _find_section(sections, _NEAR_DUP_HEADING)
    near_duplicates = (
        _parse_near_duplicates(near_dup_body) if near_dup_body is not None else ()
    )
    known = {normalize(s.title) for s in subjects}
    unknown_dups = {c for c, _ in near_duplicates if normalize(c) not in known}
    if unknown_dups:
        raise GroundTruthError(
            f"{name}: near-duplicates name subjects that do not exist: "
            f"{sorted(unknown_dups)}"
        )

    return Parsed(
        subjects=tuple(subjects),
        facets=_listed(_FACETS_HEADING),
        near_duplicates=near_duplicates,
        out_of_scope=_listed(_OUT_OF_SCOPE_HEADING),
    )


def _resolve_source(sources_dir: Path, name: str) -> Path:
    """Pair a ground-truth stem with its source: `.md` (prose corpus) wins,
    `.txt` (AMI transcripts, #457) is the fallback. When neither exists the
    untried `.md` path is returned so `source_exists` stays the sweep's seam
    for a missing source -- resolution itself never raises."""
    for suffix in (".md", ".txt"):
        candidate = sources_dir / f"{name}{suffix}"
        if candidate.is_file():
            return candidate
    return sources_dir / f"{name}.md"


def load_ground_truth(gt_path: Path, *, sources_dir: Path = _SOURCES) -> GroundTruth:
    """Parse one `ground-truth/<name>.md` and pair it with its raw source."""
    name = gt_path.stem
    parsed = parse_ground_truth(gt_path.read_text(encoding="utf-8"), name=name)
    return GroundTruth(
        name=name,
        gt_path=gt_path,
        source_path=_resolve_source(sources_dir, name),
        subjects=parsed.subjects,
        facets=parsed.facets,
        near_duplicates=parsed.near_duplicates,
        out_of_scope=parsed.out_of_scope,
    )


def discover_ground_truth(
    directory: Path = _GROUND_TRUTH, *, sources_dir: Path = _SOURCES
) -> list[GroundTruth]:
    """Load every ground-truth file, sorted by name."""
    return [
        load_ground_truth(p, sources_dir=sources_dir)
        for p in sorted(directory.glob("*.md"))
    ]


# --------------------------------------------------------------------------- #
# Verdicts.                                                                    #
# --------------------------------------------------------------------------- #

SUBJECT = "subject"
FACET = "facet"
NEAR_DUPLICATE = "near_duplicate"
OUT_OF_SCOPE = "out_of_scope"
UNJUDGED = "unjudged"

_VERDICT_MARK = {
    SUBJECT: "S",
    FACET: "F",
    NEAR_DUPLICATE: "D",
    OUT_OF_SCOPE: "X",
    UNJUDGED: "?",
}


def classify(title: str, truth: GroundTruth) -> str:
    """Judge one produced title against every ground-truth list.

    Four ways to not be a subject, kept apart because they are four different
    failures and #404 turns on telling them apart:

    - `FACET` -- decay: the model ran out of subjects and started shredding one
      into attributes. This is the argument AGAINST raising the cap.
    - `NEAR_DUPLICATE` -- a second object re-naming a subject already emitted.
      It burns a cap slot without adding knowledge, so it argues against the
      cap being the binding constraint at all.
    - `OUT_OF_SCOPE` -- something the document merely mentions.
    - `UNJUDGED` -- nobody has ruled on it yet.

    Merging any of these would let one failure inflate another's figure.

    Subject wins over all of them when a title appears in two lists -- the
    ground truth would be self-contradictory, and crediting the subject is the
    reading that does NOT inflate the measured defect.
    """
    if truth.subject_for(title) is not None:
        return SUBJECT
    if truth.near_duplicate_of(title) is not None:
        return NEAR_DUPLICATE
    if truth.is_facet(title):
        return FACET
    if truth.is_out_of_scope(title):
        return OUT_OF_SCOPE
    return UNJUDGED


# --------------------------------------------------------------------------- #
# One run.                                                                     #
# --------------------------------------------------------------------------- #

_OK = "ok"
_EMPTY = "empty"
_ERROR = "backend_error"

_CAP = 5
"""Local mirror of `extraction.concept._MAX_OBJECTS_PER_SOURCE`, used ONLY to
label the report and to split reconstructed positions. Not imported: a private
name is not this harness's to depend on, and if the cap moves, a run's real
split still comes from `ExtractionReport.retained`, which is read per run."""


@dataclass(frozen=True)
class RunOutcome:
    """One (fixture, arm, run) extraction attempt, scored by position."""

    fixture: str
    arm: str
    run_index: int
    status: str
    titles: tuple[str, ...]
    """PRE-cap produced titles in reply order. Positions `1..retained` are the
    objects that survived; the rest are what the cap discarded."""
    retained: int
    verdicts: tuple[str, ...]
    latency_s: float
    types: tuple[str | None, ...] = ()
    """The OKF type the model gave each position, aligned with `titles`.

    `None` means the type is NOT AVAILABLE FROM THE PIPELINE, never that the
    object was untyped: `ExtractionOutcome` carries full objects only for the
    retained prefix and bare strings for the casualties, so every cap-discarded
    position is `None` by construction (#534).

    Unlike `verdicts`, a type is an OBSERVATION of what the model said, so it
    cannot go stale against a later adjudication and is safe to persist.
    """
    twin_deleted_subjects: tuple[str, ...] = ()
    error: str | None = None

    @property
    def responded(self) -> bool:
        """True when the backend replied at all (`ok`/`empty`)."""
        return self.status in (_OK, _EMPTY)

    @property
    def produced(self) -> int:
        """Pre-cap validated, twin-dropped object count."""
        return len(self.titles)

    @property
    def discarded(self) -> int:
        """How many objects the cap threw away on this run."""
        return max(0, self.produced - self.retained)


def _positions_of(outcome: ExtractionOutcome) -> tuple[str, ...]:
    """Rebuild the full PRE-cap title list, in reply order, from an outcome.

    `ExtractionOutcome` hands back full objects only for the retained prefix
    and bare titles for the casualties (`ExtractionReport.discarded_titles`,
    deliberately -- "the bodies of 15 discarded objects are not something
    anyone wants echoed to a terminal"). Titles are all this harness scores on,
    so concatenating them reconstructs exactly what it needs.
    """
    return tuple(r.title for r in outcome.objects) + tuple(
        outcome.report.discarded_titles
    )


def _types_of(outcome: ExtractionOutcome) -> tuple[str | None, ...]:
    """The type per position, aligned with `_positions_of` (#534).

    The casualties are bare titles by design -- see `_positions_of` -- so their
    type is unrecoverable here and lands as `None`. That is the honest record:
    the retained prefix is exactly what reaches a bundle, and this answers
    every type question about it without a fresh sweep.
    """
    return tuple(r.type for r in outcome.objects) + (None,) * len(
        outcome.report.discarded_titles
    )


def score_run(titles: Sequence[str], truth: GroundTruth) -> tuple[str, ...]:
    """Verdict per position, in reply order."""
    return tuple(classify(t, truth) for t in titles)


def subjects_found(titles: Sequence[str], truth: GroundTruth) -> set[str]:
    """The DISTINCT ground-truth subject titles `titles` recovered.

    Distinct by canonical subject, so a run emitting a subject twice (or once
    under its title and once under a curated alias) recovers it once. Recall is
    coverage of the ground truth, not a count of lucky strings.
    """
    out: set[str] = set()
    for title in titles:
        subject = truth.subject_for(title)
        if subject is not None:
            out.add(subject.title)
    return out


def cap_cost(outcome: RunOutcome, truth: GroundTruth) -> set[str]:
    """Genuine subjects this run PRODUCED and the cap then threw away.

    This is the cap-too-low half of #404 expressed as a set of names rather
    than a count, because "which real subject did we lose" is the reviewable
    fact -- `Agent Security` and `Agent Observability` are what made the issue
    legible in the first place.

    Deliberately measured against what the model produced, not against the full
    ground truth: a subject the model never proposed was missed by extraction,
    which is a different defect with a different fix. Blaming the cap for it
    would recommend raising a ceiling that was never the constraint.
    """
    kept = subjects_found(outcome.titles[: outcome.retained], truth)
    return subjects_found(outcome.titles, truth) - kept


def twin_deleted_subjects(source_text: str, truth: GroundTruth) -> tuple[str, ...]:
    """Ground-truth subjects that `_drop_source_title_twins` can delete.

    A subject whose title normalizes equal to `derive_source_title(source)` is
    at risk: the prompt asks for it (a tutorial's primary subject IS its title)
    and the twin rule then deletes it whenever at least one other object
    survives -- the collision `medium-08-sdk-skills.md`'s ground truth
    documents on purpose.

    Flagged per fixture so a miss on such a subject is never silently recorded
    as an extraction failure. Its own ground truth is explicit about this:
    "Whoever scores a run here must check which of the two happened before
    recording a miss, or the number will blame the extractor for a rule
    interaction." This function is that check, made automatic.

    A `Procedure` subject is NOT at risk since #413: `_drop_source_title_twins`
    exempts that type outright, exactly because a tutorial's primary object is
    the one the title names. Excusing a miss on it here would now hide a real
    extraction failure behind a rule that no longer fires -- the opposite of
    what this flag is for.
    """
    derived = derive_source_title(source_text)
    if derived is None:
        return ()
    key = normalize(derived)
    return tuple(
        s.title for s in truth.subjects if key in s.keys and s.type != _TWIN_EXEMPT_TYPE
    )


# --------------------------------------------------------------------------- #
# Aggregation across the runs of one (fixture, arm) cell.                      #
# --------------------------------------------------------------------------- #


@dataclass
class Cell:
    """Every run of one fixture under one sampling arm."""

    fixture: str
    arm: str
    truth: GroundTruth
    outcomes: list[RunOutcome] = field(default_factory=list)
    skip_reason: str | None = None

    @property
    def responded(self) -> list[RunOutcome]:
        """Runs where the backend replied."""
        return [o for o in self.outcomes if o.responded]

    @property
    def errors(self) -> int:
        """Runs that failed with a backend error."""
        return sum(1 for o in self.outcomes if o.status == _ERROR)

    def _mean(self, values: Sequence[float]) -> float:
        return statistics.fmean(values) if values else 0.0

    @property
    def mean_produced(self) -> float:
        """Mean PRE-cap object count -- the number #404 tabulated as RAW."""
        return self._mean([float(o.produced) for o in self.responded])

    @property
    def mean_retained(self) -> float:
        """Mean POST-cap object count -- what the bundle actually got."""
        return self._mean([float(o.retained) for o in self.responded])

    @property
    def mean_recall_precap(self) -> float:
        """Mean fraction of ground-truth subjects recovered before the cap."""
        total = self.truth.subject_count
        return self._mean(
            [len(subjects_found(o.titles, self.truth)) / total for o in self.responded]
        )

    @property
    def mean_recall_postcap(self) -> float:
        """Mean fraction of ground-truth subjects that SURVIVED the cap."""
        total = self.truth.subject_count
        return self._mean(
            [
                len(subjects_found(o.titles[: o.retained], self.truth)) / total
                for o in self.responded
            ]
        )

    def _precision_of(self, titles: Sequence[str]) -> float | None:
        """Share of this run's JUDGED positions that earned a NEW subject.

        `None`, never `0.0`, when the run judged nothing: a run whose every
        title is `UNJUDGED` has no measured precision, and scoring it zero
        would report an unannotated ground truth as a model failure (#694).
        Excluded from the mean rather than dragging it down.

        Two rules that the recall side does not need, and that a naive
        subject-count would get wrong:

        - The denominator is the JUDGED positions, not all of them. Precision
          over a mostly-unjudged reply is not a measurement, which is why
          `mean_unjudged` is reported directly beside it -- a reader has to be
          able to see how much of the output the figure actually covers.
        - A subject already credited earlier in the same reply does NOT earn
          the credit twice. `classify` answers per title, so two positions
          naming one ground-truth subject both return `SUBJECT` unless the
          curated near-duplicate list happens to name the second one. Counting
          both would let a run inflate its precision by repeating itself --
          the exact behaviour `NEAR_DUPLICATE` exists to penalise. First
          occurrence in reply order wins; later ones count against.
        """
        judged = 0
        credited = 0
        seen: set[str] = set()
        for title in titles:
            verdict = classify(title, self.truth)
            if verdict == UNJUDGED:
                continue
            judged += 1
            subject = self.truth.subject_for(title)
            if subject is not None and subject.title not in seen:
                seen.add(subject.title)
                credited += 1
        if judged == 0:
            return None
        return credited / judged

    @property
    def precision_values_precap(self) -> list[float]:
        """Per-run pre-cap precision, runs with nothing judged omitted."""
        values = [self._precision_of(o.titles) for o in self.responded]
        return [v for v in values if v is not None]

    @property
    def precision_values_postcap(self) -> list[float]:
        """Per-run post-cap precision -- what a bundle actually received."""
        values = [self._precision_of(o.titles[: o.retained]) for o in self.responded]
        return [v for v in values if v is not None]

    @property
    def recall_values_precap(self) -> list[float]:
        """Per-run pre-cap recall, kept so variance can be reported."""
        total = self.truth.subject_count
        return [
            len(subjects_found(o.titles, self.truth)) / total for o in self.responded
        ]

    @property
    def recall_values_postcap(self) -> list[float]:
        """Per-run post-cap recall, kept so variance can be reported."""
        total = self.truth.subject_count
        return [
            len(subjects_found(o.titles[: o.retained], self.truth)) / total
            for o in self.responded
        ]

    @property
    def mean_precision_precap(self) -> float:
        return self._mean(self.precision_values_precap)

    @property
    def mean_precision_postcap(self) -> float:
        return self._mean(self.precision_values_postcap)

    @property
    def mean_cap_cost(self) -> float:
        """Mean number of genuine subjects lost to the cap per run."""
        return self._mean([float(len(cap_cost(o, self.truth))) for o in self.responded])

    @property
    def cap_cost_names(self) -> Counter[str]:
        """How often each genuine subject was lost to the cap."""
        counter: Counter[str] = Counter()
        for outcome in self.responded:
            counter.update(cap_cost(outcome, self.truth))
        return counter

    @property
    def mean_facets(self) -> float:
        """Mean count of known-facet objects produced (pre-cap) -- the decay."""
        return self._mean(
            [float(sum(1 for v in o.verdicts if v == FACET)) for o in self.responded]
        )

    @property
    def mean_near_duplicates(self) -> float:
        """Mean count of redundant re-namings produced (pre-cap).

        Every one of these consumed a cap slot that a genuine subject could
        have used, so a high figure alongside a high `cap_cost` means the cap
        is being spent on redundancy, not that it is too small.
        """
        return self._mean(
            [
                float(sum(1 for v in o.verdicts if v == NEAR_DUPLICATE))
                for o in self.responded
            ]
        )

    @property
    def mean_out_of_scope(self) -> float:
        """Mean count of explicitly-excluded objects produced (pre-cap).

        Counted apart from `mean_facets` so a scope error never reads as decay.
        """
        return self._mean(
            [
                float(sum(1 for v in o.verdicts if v == OUT_OF_SCOPE))
                for o in self.responded
            ]
        )

    @property
    def mean_unjudged(self) -> float:
        """Mean count of titles nobody has judged yet (pre-cap)."""
        return self._mean(
            [float(sum(1 for v in o.verdicts if v == UNJUDGED)) for o in self.responded]
        )

    @property
    def distinct_title_sets(self) -> int:
        """How many DISTINCT pre-cap title sets the runs produced.

        #404 reported this as `distinct-title-sets=4/5`: the instability that
        makes a compiled bundle non-reconstructible. Set-valued (not ordered),
        because re-ordering the same five objects is a different complaint.
        """
        return len({frozenset(normalize(t) for t in o.titles) for o in self.responded})

    def position_curve(self) -> list[tuple[int, Counter[str]]]:
        """Verdict distribution at each 1-based reply position, across runs.

        THE decay measurement. `extract_concept` claims reply order correlates
        with quality; this is the evidence for or against it, and the input any
        proposal to raise the cap or to rank instead of truncate has to be
        argued against.
        """
        depth = max((o.produced for o in self.responded), default=0)
        curve: list[tuple[int, Counter[str]]] = []
        for position in range(depth):
            counter: Counter[str] = Counter()
            for outcome in self.responded:
                if position < len(outcome.verdicts):
                    counter[outcome.verdicts[position]] += 1
            curve.append((position + 1, counter))
        return curve

    def unjudged_queue(self) -> Counter[str]:
        """Every unjudged title seen in this cell, by frequency."""
        counter: Counter[str] = Counter()
        for outcome in self.responded:
            for title, verdict in zip(outcome.titles, outcome.verdicts, strict=True):
                if verdict == UNJUDGED:
                    counter[title] += 1
        return counter


# --------------------------------------------------------------------------- #
# Driving the real pipeline.                                                   #
# --------------------------------------------------------------------------- #

BASELINE_ARM = "baseline"
"""No `options` sent: whatever the model's own default sampling is. #404's
tables call this column `baseline`, and it is kept as an arm so a new run is
comparable against those numbers rather than against a fresh baseline."""


def temperature_urlopen(
    temperature: float, inner: Callable[..., Any] = urllib.request.urlopen
) -> Callable[..., Any]:
    """Wrap `urlopen` so `/api/chat` payloads carry `options.temperature`.

    `OllamaClient` exposes no sampling knobs, and this harness does not add
    any: a measurement tool must not widen the production surface to make
    itself easier to write. It injects through the client's already-public
    `urlopen` seam instead -- the same technique the temperature spike used,
    with zero production bytes touched.

    Only `/api/chat` is rewritten. `/api/tags` and `/api/embed` pass straight
    through, so a stray `options` key can never reach an endpoint that does not
    take one.
    """

    def _open(request: Any, *args: Any, **kwargs: Any) -> Any:
        url = getattr(request, "full_url", "")
        data = getattr(request, "data", None)
        if url.endswith("/api/chat") and data:
            payload = json.loads(data.decode("utf-8"))
            options = dict(payload.get("options") or {})
            options["temperature"] = temperature
            payload["options"] = options
            # `Request.data`'s setter drops the stale Content-length itself.
            request.data = json.dumps(payload).encode("utf-8")
        return inner(request, *args, **kwargs)

    return _open


def runs_to_json(cells: Sequence[Cell], *, model: str, runs: int, stamp: str) -> str:
    """Serialize the RAW observations of a sweep -- never the verdicts.

    Verdicts are deliberately excluded. They are a FUNCTION of the ground truth
    at scoring time, and the whole point of `--rescore` is to recompute them
    after an adjudication. Persisting them would let a stale judgment travel
    with the data and quietly outvote the file a human just edited.
    """
    payload = {
        "model": model,
        "runs": runs,
        "generated_at": stamp,
        "outcomes": [
            {
                "fixture": o.fixture,
                "arm": o.arm,
                "run_index": o.run_index,
                "status": o.status,
                "titles": list(o.titles),
                "types": list(o.types),
                "retained": o.retained,
                "latency_s": o.latency_s,
                "twin_deleted_subjects": list(o.twin_deleted_subjects),
                "error": o.error,
            }
            for cell in cells
            for o in cell.outcomes
        ],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


def cells_from_json(text: str, truths: Sequence[GroundTruth]) -> tuple[list[Cell], str]:
    """Rebuild cells from a saved sweep, RESCORING against `truths`.

    Returns the cells and the model tag the sweep used. Every verdict is
    recomputed from the ground truth on disk right now, so the difference
    between two rescores of one saved sweep is attributable to the
    adjudication and to nothing else -- no resampling, no temperature drift.
    That isolation is the reason this exists: re-running the model to see the
    effect of a human judgment changes two variables at once.
    """
    payload = json.loads(text)
    by_name = {t.name: t for t in truths}
    cells: dict[tuple[str, str], Cell] = {}
    for raw in payload["outcomes"]:
        truth = by_name.get(raw["fixture"])
        if truth is None:
            continue
        key = (raw["fixture"], raw["arm"])
        if key not in cells:
            cells[key] = Cell(fixture=raw["fixture"], arm=raw["arm"], truth=truth)
        titles = tuple(raw["titles"])
        # Absent in every file written before #534. Defaulting to all-`None`
        # keeps those rescoring exactly as they did; dropping them would cost
        # far more than the field is worth.
        raw_types = raw.get("types") or []
        types = tuple(
            (raw_types[i] if i < len(raw_types) else None) for i in range(len(titles))
        )
        cells[key].outcomes.append(
            RunOutcome(
                fixture=raw["fixture"],
                arm=raw["arm"],
                run_index=raw["run_index"],
                status=raw["status"],
                titles=titles,
                types=types,
                retained=raw["retained"],
                verdicts=score_run(titles, truth),
                latency_s=raw["latency_s"],
                twin_deleted_subjects=tuple(raw["twin_deleted_subjects"]),
                error=raw["error"],
            )
        )
    return list(cells.values()), str(payload["model"])


def resolve_arms(
    *, baseline: bool, temperatures: Sequence[float] | None
) -> list[tuple[str, float | None]]:
    """Turn the CLI's arm flags into ordered `(label, temperature)` pairs.

    Arms COMPOSE. `--temperature` once REPLACED the baseline rather than adding
    to it, which made the one comparison that matters -- model-default sampling
    against a pinned temperature, the two columns of #404's own tables --
    impossible to express in a single report. Passing neither still yields the
    baseline alone, so the bare invocation is unchanged.

    Baseline always sorts first, matching the column order of those tables.
    """
    arms: list[tuple[str, float | None]] = []
    if baseline or not temperatures:
        arms.append((BASELINE_ARM, None))
    arms.extend((f"t{t}", t) for t in temperatures or ())
    return arms


_UNION_JUDGE_SUFFIX = "+union-judge"
"""Arm-label suffix (#456) distinguishing a union+judge row from the
single-cap row it is compared against -- e.g. `baseline` vs.
`baseline+union-judge`. A suffix on the existing label rather than a new
report column: every scoring/reporting path keyed on `arm` as an opaque
string keeps working unchanged, and the two rows sort adjacent."""


def expand_union_judge_arms(
    arms: list[tuple[str, float | None]], *, union_judge_mode: str
) -> list[tuple[str, float | None, bool]]:
    """Cross each `(label, temperature)` arm with the pipeline dimension
    (#456), per `union_judge_mode`:

    - `"off"` (default): every arm runs the single-cap `extract_concept`
      pipeline only -- byte-identical to before this flag existed.
    - `"on"`: every arm runs the union+judge `extract_concept_union`
      pipeline only, labels unchanged (no suffix -- there is nothing to
      disambiguate from in this mode).
    - `"both"`: every arm runs BOTH pipelines, the union+judge row labeled
      with `_UNION_JUDGE_SUFFIX`, so a single report/rescore shows the
      before/after pair a reader can diff for the recall delta the
      Pre-Archive Measurement Gate requires.
    """
    if union_judge_mode == "on":
        return [(label, temp, True) for label, temp in arms]
    if union_judge_mode == "both":
        expanded: list[tuple[str, float | None, bool]] = []
        for label, temp in arms:
            expanded.append((label, temp, False))
            expanded.append((f"{label}{_UNION_JUDGE_SUFFIX}", temp, True))
        return expanded
    return [(label, temp, False) for label, temp in arms]


_STEM_TITLE_SUFFIX = "+stem-title"
"""Arm-label suffix (#459) distinguishing a stem-title row from the
production-derived-title row it is compared against. Same design as
`_UNION_JUDGE_SUFFIX`: a suffix on the opaque `arm` label, so every scoring
and reporting path keeps working unchanged and the paired rows sort
adjacent."""


def expand_title_mode_arms(
    arms: list[tuple[str, float | None, bool]], *, title_mode: str
) -> list[tuple[str, float | None, bool, bool]]:
    """Cross each `(label, temperature, union_judge)` arm with the source-title
    dimension (#459), per `title_mode`:

    - `"derived"` (default): every arm passes the production-derived title
      (`derive_source_title` on the source text, `ingest`'s behavior) --
      byte-identical to before this flag existed.
    - `"stem"`: every arm passes the file stem instead, labels unchanged.
    - `"both"`: every arm runs under BOTH titles, the stem row labeled with
      `_STEM_TITLE_SUFFIX`, so one report/rescore carries the A/B that
      isolates title priming from every other variable.
    """
    if title_mode == "stem":
        return [(label, temp, uj, True) for label, temp, uj in arms]
    if title_mode == "both":
        expanded: list[tuple[str, float | None, bool, bool]] = []
        for label, temp, uj in arms:
            expanded.append((label, temp, uj, False))
            expanded.append((f"{label}{_STEM_TITLE_SUFFIX}", temp, uj, True))
        return expanded
    return [(label, temp, uj, False) for label, temp, uj in arms]


def build_client(
    model: str, host: str | None, timeout: float, temperature: float | None
) -> OllamaClient:
    """Construct the client for one arm (baseline when `temperature is None`)."""
    if temperature is None:
        return OllamaClient(model=model, host=host, timeout=timeout)
    return OllamaClient(
        model=model,
        host=host,
        timeout=timeout,
        urlopen=temperature_urlopen(temperature),
    )


def source_title_for(source_text: str, source_path: Path) -> str:
    """Exactly what `ingest` passes to `extract_concept`.

    Replicated rather than approximated. Scoring a title the pipeline would
    never send makes both the twin rule and the "SOURCE TITLE (metadata only)"
    prompt line behave differently than in production, and the whole point of
    this harness is to measure the real thing.
    """
    derived = derive_source_title(source_text)
    return derived if derived is not None else source_path.stem.replace("-", " ")


def resolve_title(
    source_text: str, source_path: Path, *, stem_title: bool = False
) -> str:
    """The title an arm sends, per the #459 A/B dimension.

    `stem_title=False` is production (`source_title_for`). `stem_title=True`
    is the raw file stem -- the configuration `run_type_coverage.py` and the
    #456 gate measured, under which TS3005b.transcript produced ~10 candidates
    where the derived title produced 1. The stem is passed VERBATIM (no
    hyphen-respelling): the A/B needs the exact string the gate sent, not a
    third variant.
    """
    if stem_title:
        return source_path.stem
    return source_title_for(source_text, source_path)


def run_one(
    truth: GroundTruth,
    source_text: str,
    client: OllamaClient,
    arm: str,
    run_index: int,
    twin_risk: tuple[str, ...],
    *,
    union_judge: bool = False,
    stem_title: bool = False,
) -> RunOutcome:
    """Drive the REAL pipeline once; never raise.

    A backend failure is recorded as a failed run, exactly as `run_spike.py`
    does: a single bad call in a 30-call sweep must not discard the other 29.

    `union_judge` (#456) selects `extract_concept_union` -- the union-of-runs
    + selector-judge pipeline -- instead of the single-run, single-cap
    `extract_concept`. Both return the same `ExtractionOutcome` shape, so
    every downstream scoring/reporting path is unchanged; only the `arm`
    label (see `resolve_arms`) distinguishes which pipeline produced a row.
    """
    title = resolve_title(source_text, truth.source_path, stem_title=stem_title)
    started = time.perf_counter()
    extractor = extract_concept_union if union_judge else extract_concept
    try:
        outcome = extractor(source_text, source_title=title, llm=client)
    except OllamaError as exc:
        return RunOutcome(
            fixture=truth.name,
            arm=arm,
            run_index=run_index,
            status=_ERROR,
            titles=(),
            retained=0,
            verdicts=(),
            latency_s=time.perf_counter() - started,
            error=f"{type(exc).__name__}: {exc}",
        )
    except Exception as exc:  # one bad run must not abort the sweep
        return RunOutcome(
            fixture=truth.name,
            arm=arm,
            run_index=run_index,
            status=_ERROR,
            titles=(),
            retained=0,
            verdicts=(),
            latency_s=time.perf_counter() - started,
            error=f"unexpected {type(exc).__name__}: {exc}",
        )
    latency = time.perf_counter() - started
    titles = _positions_of(outcome)
    return RunOutcome(
        fixture=truth.name,
        arm=arm,
        run_index=run_index,
        status=_OK if titles else _EMPTY,
        titles=titles,
        types=_types_of(outcome),
        retained=outcome.report.retained,
        verdicts=score_run(titles, truth),
        latency_s=latency,
        twin_deleted_subjects=twin_risk,
    )


def evaluate_cell(
    truth: GroundTruth,
    arm: str,
    temperature: float | None,
    runs: int,
    model: str,
    host: str | None,
    timeout: float,
    *,
    union_judge: bool = False,
    stem_title: bool = False,
) -> Cell:
    """Run one fixture `runs` times under one arm; skip a missing source."""
    cell = Cell(fixture=truth.name, arm=arm, truth=truth)
    if not truth.source_exists:
        try:
            shown = str(truth.source_path.relative_to(_REPO_ROOT))
        except ValueError:
            shown = str(truth.source_path)
        cell.skip_reason = (
            f"source not present ({shown} is git-ignored; see the corpus .gitignore)"
        )
        return cell
    source_text = truth.read_source()
    twin_risk = twin_deleted_subjects(source_text, truth)
    client = build_client(model, host, timeout, temperature)
    for run_index in range(1, runs + 1):
        outcome = run_one(
            truth,
            source_text,
            client,
            arm,
            run_index,
            twin_risk,
            union_judge=union_judge,
            stem_title=stem_title,
        )
        cell.outcomes.append(outcome)
        _print_run_line(outcome)
    return cell


def _print_run_line(outcome: RunOutcome) -> None:
    """One-line progress trace: the verdict string is the run at a glance."""
    if outcome.status == _ERROR:
        detail = f"ERROR -- {outcome.error}"
    elif not outcome.titles:
        detail = "[] (nothing extracted)"
    else:
        marks = "".join(_VERDICT_MARK[v] for v in outcome.verdicts)
        detail = (
            f"produced={outcome.produced} retained={outcome.retained} "
            f"[{marks[: outcome.retained]}|{marks[outcome.retained :]}]"
        )
    print(
        f"  [{outcome.arm}] {outcome.fixture} run {outcome.run_index} "
        f"({outcome.latency_s:.1f}s): {detail}"
    )


# --------------------------------------------------------------------------- #
# Reporting.                                                                   #
# --------------------------------------------------------------------------- #


def _fmt(value: float) -> str:
    return f"{value:.2f}"


def _spread(values: Sequence[float]) -> str:
    """`mean ±sd [min-max] n=N` -- the shape #694 asks a metric to have.

    The issue's own argument is that a single number cannot say whether a
    changed figure is a real movement or the same distribution sampled again:
    yield on a byte-identical source varied ~40% across three runs. A mean with
    no spread beside it invites exactly that mistake, so every headline metric
    is rendered through this.

    `n` is printed because the spread of two runs and the spread of ten are not
    the same claim, and a reader must not have to look elsewhere to tell them
    apart. A single run reports its value with `n=1` and no spread rather than
    a fabricated `±0.00`."""
    if not values:
        return "-"
    if len(values) == 1:
        return f"{values[0]:.2f} n=1"
    return (
        f"{statistics.fmean(values):.2f} "
        f"±{statistics.stdev(values):.2f} "
        f"[{min(values):.2f}-{max(values):.2f}] n={len(values)}"
    )


def build_report(
    cells: Sequence[Cell], *, model: str, runs: int, generated_at: datetime
) -> str:
    """Render the markdown report: per fixture, then the adjudication queue."""
    lines: list[str] = []
    lines.append("# openkos extraction cap-and-decay eval (#404)")
    lines.append("")
    lines.append(f"_Generated: {generated_at.isoformat(timespec='seconds')}_")
    lines.append("")
    lines.append(f"Model: `{model}`. Runs per cell: **{runs}**.")
    lines.append("")
    lines.append(
        "**There is no corpus-wide average anywhere in this report, "
        "deliberately.** `small-04-pre-build-skills` is the same lesson as "
        "`large-03-skills-vs-tools` in Spanish at ~45% of the length; both "
        "ground-truth files forbid averaging them. Read each fixture on its "
        "own, and read that pair side by side."
    )
    lines.append("")
    lines.append(
        "Verdict marks: `S` a genuine subject, `F` a known facet (decay), "
        "`D` a near-duplicate re-naming a subject already emitted, `X` "
        "explicitly out of scope, `?` unjudged. The `|` in a run's mark string "
        "is where the cap cut."
    )
    lines.append("")

    for cell in cells:
        lines.extend(_fixture_section(cell))

    lines.extend(_adjudication_section(cells))
    return "\n".join(lines)


def _fixture_section(cell: Cell) -> list[str]:
    """One fixture/arm block: headline numbers, position curve, per-run detail."""
    truth = cell.truth
    lines = [f"## `{cell.fixture}` — arm `{cell.arm}`", ""]
    if cell.skip_reason:
        lines.extend([f"SKIPPED: {cell.skip_reason}", ""])
        return lines

    lines.append(
        f"Ground truth: **{truth.subject_count} genuine subjects**, "
        f"{len(truth.facets)} named facets, "
        f"{len(truth.out_of_scope)} out-of-scope exclusion(s), "
        f"{len(truth.near_duplicates)} near-duplicate pair(s)."
    )
    lines.append("")

    twin_risk = next(
        (o.twin_deleted_subjects for o in cell.outcomes if o.twin_deleted_subjects), ()
    )
    if twin_risk:
        lines.append(
            "> **Twin-rule collision on this fixture.** "
            + ", ".join(f"`{t}`" for t in twin_risk)
            + " normalizes equal to the derived source title, so "
            "`_drop_source_title_twins` deletes it whenever another object "
            "survives. A miss on it is NOT an extraction failure — do not "
            "record it as one."
        )
        lines.append("")

    if not cell.responded:
        lines.extend([f"No run responded ({cell.errors} backend error(s)).", ""])
        return lines

    lines.append("| metric | value |")
    lines.append("| --- | --- |")
    lines.append(f"| mean produced (pre-cap) | {_fmt(cell.mean_produced)} |")
    lines.append(f"| mean retained (post-cap) | {_fmt(cell.mean_retained)} |")
    lines.append(f"| subject recall pre-cap | {_spread(cell.recall_values_precap)} |")
    lines.append(f"| subject recall post-cap | {_spread(cell.recall_values_postcap)} |")
    # Precision sits directly beside `mean unjudged titles` below on purpose:
    # it is computed over the JUDGED positions only, so a high unjudged count
    # means the figure covers little of the output and must not be read as a
    # verdict on the extractor (#694).
    lines.append(
        f"| subject precision pre-cap | {_spread(cell.precision_values_precap)} |"
    )
    lines.append(
        f"| subject precision post-cap | {_spread(cell.precision_values_postcap)} |"
    )
    lines.append(
        f"| **mean cap_cost (subjects lost to the cap)** | "
        f"**{_fmt(cell.mean_cap_cost)}** |"
    )
    lines.append(f"| mean known facets produced (decay) | {_fmt(cell.mean_facets)} |")
    lines.append(
        f"| mean near-duplicates produced | {_fmt(cell.mean_near_duplicates)} |"
    )
    lines.append(f"| mean out-of-scope produced | {_fmt(cell.mean_out_of_scope)} |")
    lines.append(f"| mean unjudged titles | {_fmt(cell.mean_unjudged)} |")
    lines.append(
        f"| distinct title sets | {cell.distinct_title_sets}/{len(cell.responded)} |"
    )
    lines.append(f"| backend errors | {cell.errors} |")
    lines.append("")

    losses = cell.cap_cost_names
    if losses:
        lines.append("**Genuine subjects the cap discarded** (runs affected):")
        lines.append("")
        for title, count in losses.most_common():
            lines.append(f"- `{title}` — {count}/{len(cell.responded)} runs")
        lines.append("")
    else:
        lines.append(
            "The cap discarded no genuine subject in any responded run: every "
            "subject this model produced fit inside the retained prefix."
        )
        lines.append("")

    lines.append("### Position curve (does reply order track quality?)")
    lines.append("")
    lines.append("| position | subject | facet | near-dup | out of scope | unjudged |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for position, counter in cell.position_curve():
        marker = " *(cap)*" if position == _CAP else ""
        lines.append(
            f"| {position}{marker} | {counter[SUBJECT]} | {counter[FACET]} | "
            f"{counter[NEAR_DUPLICATE]} | {counter[OUT_OF_SCOPE]} | "
            f"{counter[UNJUDGED]} |"
        )
    lines.append("")

    lines.append("### Per-run detail")
    lines.append("")
    for outcome in cell.outcomes:
        if outcome.status == _ERROR:
            lines.append(f"- run {outcome.run_index}: ERROR — {outcome.error}")
            continue
        if not outcome.titles:
            lines.append(f"- run {outcome.run_index}: [] (nothing extracted)")
            continue
        rendered = ", ".join(
            f"{_VERDICT_MARK[v]}:{t}"
            for t, v in zip(outcome.titles, outcome.verdicts, strict=True)
        )
        lines.append(
            f"- run {outcome.run_index} ({outcome.latency_s:.1f}s, "
            f"produced {outcome.produced}, retained {outcome.retained}): {rendered}"
        )
    lines.append("")
    return lines


def _adjudication_section(cells: Sequence[Cell]) -> list[str]:
    """The queue a human works to make the next round's scores sharper."""
    lines = ["## Unjudged adjudication queue", ""]
    lines.append(
        "Titles matching neither a ground-truth subject nor a named facet. "
        "This harness never guesses which they are — no fuzzy matching, by "
        "design, since a matcher generous toward the hypothesis measures "
        "nothing. Work this queue by hand:"
    )
    lines.append("")
    lines.append(
        "1. A rephrasing of an existing subject → add it under `## Aliases` in "
        "that fixture's ground truth as `Canonical Title | the rephrasing`."
    )
    lines.append("2. A facet of a subject → add it to `## Facets, not subjects`.")
    lines.append(
        "2b. Something the document merely MENTIONS and is not about → add it "
        "to `## Out of scope`. Kept apart from facets so a scope error never "
        "reads as decay."
    )
    lines.append(
        "3. A genuine subject nobody listed → add it under `## Genuinely "
        "distinct subjects` and update `**Count:**` (the parser rejects the "
        "file if the two disagree)."
    )
    lines.append("")

    any_queue = False
    for cell in cells:
        queue = cell.unjudged_queue()
        if not queue:
            continue
        any_queue = True
        lines.append(f"### `{cell.fixture}` — arm `{cell.arm}`")
        lines.append("")
        for title, count in queue.most_common():
            lines.append(f"- `{title}` — seen {count} time(s)")
        lines.append("")
    if not any_queue:
        lines.append("_Empty: every produced title was already judged._")
        lines.append("")
    return lines


# --------------------------------------------------------------------------- #
# Self-test: prove parsing, scoring and reporting with NO model.               #
# --------------------------------------------------------------------------- #

_SAMPLE_GT = """# Ground truth — `sample.md`

Prose framing that must be ignored, with a `- ` looking bullet? No.

## Genuinely distinct subjects

**Count: 3.**

- Concept | Pre-built Skills
- Concept | Skill Creator
- Procedure | Building a Research Agent

Some prose explaining the judgment.

## Aliases

- Skill Creator | Skill-Creator Tool | El Creador de Skills

## Facets, not subjects

- Skill Creation Process
- Skill Packaging

## Out of scope

- MinerU

## Near-duplicates

- Pre-built Skills | Document Skills
"""


def _gt(text: str = _SAMPLE_GT, name: str = "sample") -> GroundTruth:
    """Build a GroundTruth from markdown, with a source path that will not exist."""
    parsed = parse_ground_truth(text, name=name)
    return GroundTruth(
        name=name,
        gt_path=Path("/nonexistent") / f"{name}.md",
        source_path=Path("/nonexistent") / f"{name}.md",
        subjects=parsed.subjects,
        facets=parsed.facets,
        near_duplicates=parsed.near_duplicates,
        out_of_scope=parsed.out_of_scope,
    )


def _run(
    titles: Sequence[str],
    retained: int,
    truth: GroundTruth,
    index: int = 1,
    types: Sequence[str | None] | None = None,
) -> RunOutcome:
    """Build a synthetic RunOutcome scored against `truth` (no model call)."""
    return RunOutcome(
        fixture=truth.name,
        arm="synthetic",
        run_index=index,
        status=_OK if titles else _EMPTY,
        titles=tuple(titles),
        types=tuple(types) if types is not None else (None,) * len(titles),
        retained=retained,
        verdicts=score_run(titles, truth),
        latency_s=1.0,
    )


def self_test() -> int:
    """Prove parsing, matching, scoring and rendering on synthetic data."""
    failures: list[str] = []

    def check(label: str, got: object, want: object) -> None:
        if got != want:
            failures.append(f"{label}: got {got!r}, want {want!r}")

    def raises(label: str, text: str, needle: str) -> None:
        try:
            parse_ground_truth(text, name="t")
        except GroundTruthError as exc:
            if needle not in str(exc):
                failures.append(f"{label}: message {str(exc)!r} lacks {needle!r}")
            return
        failures.append(f"{label}: expected GroundTruthError, none raised")

    # 1. Parsing.
    truth = _gt()
    check("subject count", truth.subject_count, 3)
    check(
        "subject titles",
        [s.title for s in truth.subjects],
        ["Pre-built Skills", "Skill Creator", "Building a Research Agent"],
    )
    check(
        "subject types",
        [s.type for s in truth.subjects],
        ["Concept", "Concept", "Procedure"],
    )
    check("facets", list(truth.facets), ["Skill Creation Process", "Skill Packaging"])
    check(
        "near-dup pairs",
        list(truth.near_duplicates),
        [("Pre-built Skills", "Document Skills")],
    )
    check(
        "near-dups (None identified)",
        list(
            _gt(
                _SAMPLE_GT.replace(
                    "- Pre-built Skills | Document Skills", "None identified."
                ),
                "nodups",
            ).near_duplicates
        ),
        [],
    )
    check(
        "aliases attached",
        truth.subjects[1].aliases,
        ("Skill-Creator Tool", "El Creador de Skills"),
    )
    check("no stray aliases", truth.subjects[0].aliases, ())

    # 2. Parser strictness -- the failures that would corrupt a score silently.
    raises(
        "count mismatch",
        _SAMPLE_GT.replace("**Count: 3.**", "**Count: 5.**"),
        "edited half-way",
    )
    raises(
        "no subjects section",
        "# t\n\n## Facets, not subjects\n\n- x\n",
        "no `## Genuinely distinct subjects`",
    )
    raises(
        "subject without a type",
        "## Genuinely distinct subjects\n\n- Pre-built Skills\n",
        "must read `Type | Title`",
    )
    raises(
        "alias for an unknown subject",
        "## Genuinely distinct subjects\n\n- Concept | A\n\n"
        "## Aliases\n\n- Ghost | g\n",
        "aliases name subjects that do not exist",
    )
    # A file with no declared Count still parses -- `corpus.py`'s stub has none.
    check(
        "count is optional",
        len(_gt(_SAMPLE_GT.replace("**Count: 3.**", ""), "nocount").subjects),
        3,
    )

    # 3. Matching: exact + curated alias only, and NOTHING else.
    check("exact", classify("Pre-built Skills", truth), SUBJECT)
    check("case/space insensitive", classify("  pre-built   SKILLS ", truth), SUBJECT)
    check("backtick-wrapped", classify("`Skill Creator`", truth), SUBJECT)
    check("curated alias", classify("El Creador de Skills", truth), SUBJECT)
    check("known facet", classify("Skill Packaging", truth), FACET)
    check("unknown", classify("Agent Security", truth), UNJUDGED)
    # Out-of-scope is its OWN verdict, never folded into facets: a facet means
    # decay (the argument against raising the cap), a scope error does not.
    check("out of scope", classify("MinerU", truth), OUT_OF_SCOPE)
    check("out of scope parsed", list(truth.out_of_scope), ["MinerU"])
    check("out of scope is not a facet", truth.is_facet("MinerU"), False)
    # A near-duplicate is NOT an alias: it must not credit the subject, because
    # it consumed a cap slot without adding knowledge.
    check("near-duplicate verdict", classify("Document Skills", truth), NEAR_DUPLICATE)
    check(
        "near-duplicate names its subject",
        truth.near_duplicate_of("Document Skills"),
        "Pre-built Skills",
    )
    check(
        "near-duplicate does not fill the subject",
        subjects_found(["Document Skills"], truth),
        set(),
    )
    # THE anti-fuzzy check. `Skill Creation Process` is a facet whose words
    # overlap the subject `Skill Creator` heavily; any similarity matcher would
    # credit it as a subject and shrink the measured defect. It must be a FACET.
    check("no fuzzy credit", classify("Skill Creation Process", truth), FACET)
    check("prefix is not a match", classify("Pre-built", truth), UNJUDGED)
    check(
        "superstring is not a match",
        classify("Pre-built Skills in Claude", truth),
        UNJUDGED,
    )

    # 4. Recall is coverage of the ground truth, not a count of strings.
    dupes = ["Pre-built Skills", "pre-built skills", "Skill-Creator Tool"]
    check(
        "distinct subjects",
        subjects_found(dupes, truth),
        {"Pre-built Skills", "Skill Creator"},
    )

    # 5. cap_cost: real subjects PRODUCED, then thrown away by the cap.
    #    Positions 1-2 retained; 3-4 discarded, and position 3 is a subject.
    lost = _run(
        ["Pre-built Skills", "Skill Packaging", "Skill Creator", "Noise"],
        retained=2,
        truth=truth,
    )
    check("verdict string", lost.verdicts, (SUBJECT, FACET, SUBJECT, UNJUDGED))
    check("discarded count", lost.discarded, 2)
    check("cap_cost names", cap_cost(lost, truth), {"Skill Creator"})
    # A subject the model never produced is NOT the cap's fault: `Building a
    # Research Agent` is absent from every position and must not be blamed here.
    check(
        "cap_cost ignores never-produced",
        "Building a Research Agent" in cap_cost(lost, truth),
        False,
    )
    # Nothing discarded -> no cap cost, even with subjects missing entirely.
    intact = _run(["Pre-built Skills"], retained=1, truth=truth)
    check("no cap cost when nothing cut", cap_cost(intact, truth), set())

    # 6. Cell aggregation, incl. the position curve and set instability.
    cell = Cell(fixture="sample", arm="synthetic", truth=truth)
    cell.outcomes = [
        _run(["Pre-built Skills", "Skill Creator", "Skill Packaging"], 2, truth, 1),
        _run(["Pre-built Skills", "Skill Creator", "Skill Packaging"], 2, truth, 2),
        _run(["Pre-built Skills", "Skill Creation Process"], 2, truth, 3),
    ]
    check("mean produced", cell.mean_produced, statistics.fmean([3.0, 3.0, 2.0]))
    check("mean retained", cell.mean_retained, 2.0)
    # Runs 1-2 recover 2/3 pre-cap, run 3 recovers 1/3.
    check(
        "recall pre-cap",
        round(cell.mean_recall_precap, 6),
        round(statistics.fmean([2 / 3, 2 / 3, 1 / 3]), 6),
    )
    # Runs 1-2 keep both subjects inside the retained prefix -> same post-cap.
    check(
        "recall post-cap",
        round(cell.mean_recall_postcap, 6),
        round(statistics.fmean([2 / 3, 2 / 3, 1 / 3]), 6),
    )
    check("mean cap cost", cell.mean_cap_cost, 0.0)
    check("mean facets", cell.mean_facets, statistics.fmean([1.0, 1.0, 1.0]))
    check("mean out of scope", cell.mean_out_of_scope, 0.0)
    # A run full of scope errors must NOT read as decay.
    scoped = Cell(fixture="sample", arm="synthetic", truth=truth)
    scoped.outcomes = [_run(["Pre-built Skills", "MinerU", "MinerU"], 3, truth, 1)]
    check("scope errors are not decay", scoped.mean_facets, 0.0)
    check("scope errors are counted", scoped.mean_out_of_scope, 2.0)
    check("scope errors leave the queue empty", dict(scoped.unjudged_queue()), {})
    # A run that emits the same subject twice under two names: recall counts it
    # ONCE, and the redundant slot is visible as a near-duplicate rather than
    # silently credited or left unjudged.
    dupey = Cell(fixture="sample", arm="synthetic", truth=truth)
    dupey.outcomes = [_run(["Pre-built Skills", "Document Skills"], 2, truth, 1)]
    check("near-dup verdicts", dupey.outcomes[0].verdicts, (SUBJECT, NEAR_DUPLICATE))
    check("near-dup counted", dupey.mean_near_duplicates, 1.0)
    check("near-dup is not decay", dupey.mean_facets, 0.0)
    check(
        "near-dup recall counts once",
        round(dupey.mean_recall_precap, 6),
        round(1 / 3, 6),
    )
    # Precision over the JUDGED positions: one subject, one near-duplicate.
    check("near-dup precision", dupey.precision_values_precap, [0.5])

    # #694's subtle case: the SAME subject emitted twice, uncurated. Both
    # positions classify SUBJECT, so a naive count would report precision 1.00
    # for a reply that added one subject and one repetition. Credit is given to
    # the first occurrence only.
    repeated = Cell(fixture="sample", arm="synthetic", truth=truth)
    repeated.outcomes = [_run(["Pre-built Skills", "Pre-built Skills"], 2, truth, 2)]
    check(
        "repeated subject verdicts are both SUBJECT",
        repeated.outcomes[0].verdicts,
        (SUBJECT, SUBJECT),
    )
    check(
        "a repeated subject is not credited twice",
        repeated.precision_values_precap,
        [0.5],
    )
    check(
        "a repeated subject still recalls once",
        round(repeated.mean_recall_precap, 6),
        round(1 / 3, 6),
    )

    # A reply nobody has judged has NO precision -- excluded from the mean
    # rather than scored zero, which would report an unannotated ground truth
    # as a model failure.
    unjudged_only = Cell(fixture="sample", arm="synthetic", truth=truth)
    unjudged_only.outcomes = [_run(["Something Nobody Ruled On"], 1, truth, 1)]
    check("unjudged reply has no precision", unjudged_only.precision_values_precap, [])
    check("unjudged spread renders as absent", _spread([]), "-")

    # Variance is the whole point of #694: a mean with no spread beside it
    # cannot say whether a moved figure is a real movement.
    check(
        "single run reports n=1 without a fabricated spread", _spread([0.5]), "0.50 n=1"
    )
    check(
        "several runs report mean, sd, range and n",
        _spread([0.0, 1.0]),
        "0.50 ±0.71 [0.00-1.00] n=2",
    )

    raises(
        "near-duplicate for an unknown subject",
        "## Genuinely distinct subjects\n\n- Concept | A\n\n"
        "## Near-duplicates\n\n- Ghost | g\n",
        "near-duplicates name subjects that do not exist",
    )
    raises(
        "near-duplicate without a pipe",
        "## Genuinely distinct subjects\n\n- Concept | A\n\n"
        "## Near-duplicates\n\n- just one thing\n",
        "needs `Canonical Subject |",
    )
    check("distinct title sets", cell.distinct_title_sets, 2)
    curve = cell.position_curve()
    check("curve depth", len(curve), 3)
    check("position 1 all subjects", curve[0][1][SUBJECT], 3)
    check("position 2 splits", (curve[1][1][SUBJECT], curve[1][1][FACET]), (2, 1))
    check("position 3 facets only", curve[2][1][FACET], 2)
    check("queue is empty here", dict(cell.unjudged_queue()), {})

    # 7. The unjudged queue collects what nobody has judged, by frequency.
    noisy = Cell(fixture="sample", arm="synthetic", truth=truth)
    noisy.outcomes = [
        _run(["Agent Security", "Agent Observability"], 2, truth, 1),
        _run(["Agent Security"], 1, truth, 2),
    ]
    check(
        "queue counts",
        dict(noisy.unjudged_queue()),
        {"Agent Security": 2, "Agent Observability": 1},
    )

    # 8. Backend errors drag nothing but are counted and excluded from means.
    erroring = Cell(fixture="sample", arm="synthetic", truth=truth)
    erroring.outcomes = [
        # Keyword-only past `latency_s`: this used to pass the trailing fields
        # positionally, and adding `types` (#534) silently slid `"boom"` into
        # `twin_deleted_subjects`. The self-test still passed -- nothing reads
        # those two here -- and only `mypy` caught it.
        RunOutcome("sample", "synthetic", 1, _ERROR, (), 0, (), 5.0, error="boom"),
        _run(["Pre-built Skills", "Skill Creator"], 2, truth, 2),
    ]
    check("errors counted", erroring.errors, 1)
    check("means skip errors", erroring.mean_produced, 2.0)

    # 8b. Round-trip: raw observations survive, verdicts are RECOMPUTED.
    #     This is what makes an adjudication isolatable -- rescoring the same
    #     saved sweep against an edited ground truth changes exactly one thing.
    # A second cell under a DIFFERENT arm, to prove cells regroup by
    # (fixture, arm) rather than collapsing into one.
    other = Cell(fixture="sample", arm="other", truth=truth)
    other.outcomes = [
        RunOutcome(
            "sample",
            "other",
            1,
            _OK,
            ("Pre-built Skills",),
            1,
            score_run(["Pre-built Skills"], truth),
            2.0,
        )
    ]
    saved = runs_to_json([cell, other], model="m", runs=3, stamp="s")
    if '"verdicts"' in saved:
        failures.append("saved runs must NOT carry verdicts (they would go stale)")
    reloaded, saved_model = cells_from_json(saved, [truth])
    check("round-trip model", saved_model, "m")
    check("round-trip regroups by arm", len(reloaded), 2)
    by_arm = {(c.fixture, c.arm): c for c in reloaded}
    check("round-trip keeps arms apart", by_arm[("sample", "other")].mean_produced, 1.0)
    restored = by_arm[("sample", "synthetic")]
    check("round-trip produced", restored.mean_produced, cell.mean_produced)
    check(
        "round-trip verdicts", restored.outcomes[0].verdicts, cell.outcomes[0].verdicts
    )
    # Rescoring the SAME bytes against a ground truth that now names
    # `Skill Packaging` a subject must move the score, with no model involved.
    promoted = _gt(
        _SAMPLE_GT.replace("**Count: 3.**", "**Count: 4.**").replace(
            "- Procedure | Building a Research Agent",
            "- Procedure | Building a Research Agent\n- Concept | Skill Packaging",
        ),
        "sample",
    )
    rescored, _ = cells_from_json(saved, [promoted])
    moved = {(c.fixture, c.arm): c for c in rescored}[("sample", "synthetic")]
    # Runs 1-2 held `Skill Packaging`, run 3 held `Skill Creation Process`, so
    # decay was 1.00/run. Promoting ONLY `Skill Packaging` must drop it to 1/3
    # -- not to zero, since run 3's facet is untouched by that judgment.
    check("decay before rescore", restored.mean_facets, 1.0)
    check("rescore reclassifies", round(moved.mean_facets, 6), round(1 / 3, 6))
    check(
        "rescore lifts recall",
        moved.mean_recall_precap > restored.mean_recall_precap,
        True,
    )

    # 8b. TYPES travel with the titles (#534), aligned by position.
    #
    # Unlike verdicts, a type is an OBSERVATION -- what the model said the
    # object was -- so it cannot go stale against a later adjudication and
    # belongs in the saved file. Without it no type question is rescorable and
    # every one costs a fresh sweep, which is what #534 records.
    # `_types_of` is the only place the live path builds this, so it is proved
    # here against a synthetic outcome rather than left to a model run.
    synth = ExtractionOutcome(
        objects=[
            ExtractionResult(
                type="Decision", title="Kept One", description="d", body=""
            ),
            ExtractionResult(
                type="Concept", title="Kept Two", description="d", body=""
            ),
        ],
        report=ExtractionReport(
            produced=4, retained=2, discarded_titles=("Cut One", "Cut Two")
        ),
    )
    check("types follow reply order", _types_of(synth)[:2], ("Decision", "Concept"))
    check("cap casualties have no type", _types_of(synth)[2:], (None, None))
    check("one type per position", len(_types_of(synth)), len(_positions_of(synth)))

    typed = Cell(fixture="sample", arm="typed", truth=truth)
    typed.outcomes = [
        _run(
            ["Pre-built Skills", "Skill Packaging", "Dropped By The Cap"],
            2,
            truth,
            1,
            types=("Concept", "Concept", None),
        )
    ]
    saved_typed = runs_to_json([typed], model="m", runs=1, stamp="s")
    check("types are serialized", '"types"' in saved_typed, True)
    # `_run` stamps `arm="synthetic"` on the OUTCOME, and `cells_from_json`
    # regroups by the outcome's own arm, not the cell's.
    back = {(c.fixture, c.arm): c for c in cells_from_json(saved_typed, [truth])[0]}
    got = back[("sample", "synthetic")].outcomes[0]
    check("round-trip types", got.types, ("Concept", "Concept", None))
    check("types align with titles", len(got.types), len(got.titles))

    # A file written BEFORE this field existed must still rescore. The 28
    # `runs-*.json` already on disk are exactly that, and silently dropping
    # them would cost more than the field is worth.
    legacy = json.loads(saved_typed)
    for raw in legacy["outcomes"]:
        del raw["types"]
    old = {
        (c.fixture, c.arm): c for c in cells_from_json(json.dumps(legacy), [truth])[0]
    }[("sample", "synthetic")].outcomes[0]
    check("legacy file still loads", old.titles, got.titles)
    check("legacy types are all None", old.types, (None, None, None))

    # 9. Temperature injection rewrites /api/chat ONLY.
    seen: list[dict[str, Any]] = []

    def _spy(request: Any, *_a: Any, **_k: Any) -> str:
        seen.append(json.loads(request.data.decode("utf-8")))
        return "ok"

    opener = temperature_urlopen(0.1, _spy)
    chat = urllib.request.Request(
        "http://127.0.0.1:11434/api/chat",
        data=json.dumps({"model": "m", "messages": []}).encode("utf-8"),
        method="POST",
    )
    opener(chat)
    check("temperature injected", seen[-1].get("options"), {"temperature": 0.1})
    check("payload preserved", seen[-1]["model"], "m")
    embed = urllib.request.Request(
        "http://127.0.0.1:11434/api/embed",
        data=json.dumps({"model": "m", "input": []}).encode("utf-8"),
        method="POST",
    )
    opener(embed)
    check("embed untouched", "options" in seen[-1], False)

    # 10. The twin-rule collision flag fires on a non-exempt shape, and no
    #     longer on the `Procedure` the rule stopped deleting in #413.
    tutorial = _gt(
        "## Genuinely distinct subjects\n\n"
        "- Procedure | Building a Research Agent with the Claude Agent SDK\n"
        "- Concept | Claude Agent SDK\n",
        "twin",
    )
    source = "# Building a Research Agent with the Claude Agent SDK\n\nBody.\n"
    check("procedure no longer at risk", twin_deleted_subjects(source, tutorial), ())
    echo = _gt(
        "## Genuinely distinct subjects\n\n"
        "- Concept | Building a Research Agent with the Claude Agent SDK\n"
        "- Concept | Claude Agent SDK\n",
        "twin-echo",
    )
    check(
        "non-procedure twin flagged",
        twin_deleted_subjects(source, echo),
        ("Building a Research Agent with the Claude Agent SDK",),
    )
    check(
        "no twin when the title names nothing",
        twin_deleted_subjects("# Something Else\n\nBody.\n", tutorial),
        (),
    )

    # 11. Report renders, names the cap cost, and carries the queue.
    text = build_report(
        [
            cell,
            noisy,
            Cell("ghost", "synthetic", truth, skip_reason="source not present"),
        ],
        model="test-model",
        runs=3,
        generated_at=datetime(2026, 8, 5, tzinfo=UTC),
    )
    for needle in (
        "cap-and-decay",
        "Position curve",
        "Unjudged adjudication queue",
        "Agent Security",
        "SKIPPED",
        "no corpus-wide average",
    ):
        if needle not in text:
            failures.append(f"report missing: {needle!r}")

    # 12. Arms compose: the baseline is ADDED to temperature arms, not replaced
    #     by them. The bare invocation stays baseline-only.
    check(
        "bare invocation",
        resolve_arms(baseline=False, temperatures=None),
        [(BASELINE_ARM, None)],
    )
    check(
        "temperature only",
        resolve_arms(baseline=False, temperatures=[0.1]),
        [("t0.1", 0.1)],
    )
    check(
        "two columns in one report",
        resolve_arms(baseline=True, temperatures=[0.1]),
        [(BASELINE_ARM, None), ("t0.1", 0.1)],
    )
    check(
        "baseline alone is not duplicated",
        resolve_arms(baseline=True, temperatures=None),
        [(BASELINE_ARM, None)],
    )
    check(
        "several temperatures keep order",
        resolve_arms(baseline=True, temperatures=[0.0, 0.1]),
        [(BASELINE_ARM, None), ("t0.0", 0.0), ("t0.1", 0.1)],
    )

    # 13. The real corpus ground truth parses. This is a REGRESSION GUARD on the
    #     committed fixtures, not on this harness: the files are hand-edited, and
    #     a broken one must fail here rather than skew a measurement later. The
    #     AMI transcript ground truth (#457) is scored by this same harness, so
    #     it sits under the same guard.
    for gt_dir, src_dir in (
        (_GROUND_TRUTH, _SOURCES),
        (_AMI_GROUND_TRUTH, _AMI_SOURCES),
    ):
        for path in sorted(gt_dir.glob("*.md")):
            try:
                loaded = load_ground_truth(path, sources_dir=src_dir)
            except GroundTruthError as exc:
                failures.append(f"real ground truth {path.name} does not parse: {exc}")
                continue
            if loaded.subject_count == 0:
                failures.append(f"real ground truth {path.name} lists no subject")

    # 14. Source resolution (#457): a ground-truth stem may pair with a `.md`
    #     source (the prose corpus) or a `.txt` source (the AMI transcripts).
    #     Missing sources stay a load-time non-event -- `source_exists` is the
    #     sweep's seam for that -- so resolution falls back to `.md` untried.
    with tempfile.TemporaryDirectory() as tmp:
        gt_dir = Path(tmp) / "ground_truth"
        src_dir = Path(tmp) / "sources"
        gt_dir.mkdir()
        src_dir.mkdir()
        (gt_dir / "m.md").write_text(_SAMPLE_GT, encoding="utf-8")
        (src_dir / "m.txt").write_text("A: hello\n", encoding="utf-8")
        check(
            "txt source resolves",
            load_ground_truth(gt_dir / "m.md", sources_dir=src_dir).source_path,
            src_dir / "m.txt",
        )
        (src_dir / "m.md").write_text("prose\n", encoding="utf-8")
        check(
            "md source wins over txt",
            load_ground_truth(gt_dir / "m.md", sources_dir=src_dir).source_path,
            src_dir / "m.md",
        )
        (src_dir / "m.md").unlink()
        (src_dir / "m.txt").unlink()
        missing = load_ground_truth(gt_dir / "m.md", sources_dir=src_dir)
        check("missing source falls back to md", missing.source_path, src_dir / "m.md")
        check("missing source is a non-event", missing.source_exists, False)
        check(
            "discovery threads sources_dir",
            [t.source_path for t in discover_ground_truth(gt_dir, sources_dir=src_dir)],
            [src_dir / "m.md"],
        )
        # A dotted stem (`TS3005a.transcript.md` -> `TS3005a.transcript`) must
        # pair with its dotted `.txt` source -- the shape the AMI fixtures use.
        (gt_dir / "d.stem.md").write_text(_SAMPLE_GT, encoding="utf-8")
        (src_dir / "d.stem.txt").write_text("A: hello\n", encoding="utf-8")
        check(
            "dotted stem resolves to txt",
            load_ground_truth(gt_dir / "d.stem.md", sources_dir=src_dir).source_path,
            src_dir / "d.stem.txt",
        )

    # 15. Title dimension (#459): the A/B seam between the production-derived
    #     source title and the file-stem title, and its arm expansion. The two
    #     titles are the collapse variable isolated on TS3005b.transcript, so
    #     the resolver must be provably mode-faithful.
    check(
        "derived title takes the H1",
        resolve_title(
            "# AMI meeting TS3005b\n\nA: hi\n", Path("/x/TS3005b.transcript.txt")
        ),
        "AMI meeting TS3005b",
    )
    check(
        "stem title ignores the H1",
        resolve_title(
            "# AMI meeting TS3005b\n\nA: hi\n",
            Path("/x/TS3005b.transcript.txt"),
            stem_title=True,
        ),
        "TS3005b.transcript",
    )
    # A raw speaker-turn transcript (no heading) is exactly the input where
    # production's derivation returns None -- the two modes then diverge in
    # respelling: production respells the stem, the stem arm never does.
    check(
        "derived title falls back like production",
        resolve_title("A: hello\nB: hi\n", Path("/x/some-doc.md")),
        "some doc",
    )
    check(
        "stem title never rewrites the stem",
        resolve_title("A: hello\nB: hi\n", Path("/x/some-doc.md"), stem_title=True),
        "some-doc",
    )
    three_arms: list[tuple[str, float | None, bool]] = [(BASELINE_ARM, None, False)]
    check(
        "title mode derived is a no-op",
        expand_title_mode_arms(three_arms, title_mode="derived"),
        [(BASELINE_ARM, None, False, False)],
    )
    check(
        "title mode stem flips every arm unlabeled",
        expand_title_mode_arms(three_arms, title_mode="stem"),
        [(BASELINE_ARM, None, False, True)],
    )
    check(
        "title mode both pairs each arm with a labeled stem row",
        expand_title_mode_arms(three_arms, title_mode="both"),
        [
            (BASELINE_ARM, None, False, False),
            (f"{BASELINE_ARM}{_STEM_TITLE_SUFFIX}", None, False, True),
        ],
    )
    check(
        "title and pipeline dimensions compose",
        expand_title_mode_arms(
            expand_union_judge_arms([(BASELINE_ARM, None)], union_judge_mode="both"),
            title_mode="both",
        ),
        [
            (BASELINE_ARM, None, False, False),
            (f"{BASELINE_ARM}{_STEM_TITLE_SUFFIX}", None, False, True),
            (f"{BASELINE_ARM}{_UNION_JUDGE_SUFFIX}", None, True, False),
            (
                f"{BASELINE_ARM}{_UNION_JUDGE_SUFFIX}{_STEM_TITLE_SUFFIX}",
                None,
                True,
                True,
            ),
        ],
    )

    if failures:
        print("SELF-TEST FAILED:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print(
        "SELF-TEST PASSED: parsing, strictness, exact-only matching, cap_cost, "
        "precision (incl. the repeated-subject rule), variance rendering, "
        "position curve, twin flag, temperature seam, and report OK."
    )
    return 0


# --------------------------------------------------------------------------- #
# CLI.                                                                         #
# --------------------------------------------------------------------------- #


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse the harness CLI arguments."""
    parser = argparse.ArgumentParser(
        prog="run_cap_eval.py",
        description="Cap-and-decay eval over examples/extraction-corpus/ (#404).",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Ollama model tag.")
    parser.add_argument(
        "--runs", type=int, default=DEFAULT_RUNS, help="Runs per fixture per arm."
    )
    parser.add_argument(
        "--temperature",
        type=float,
        action="append",
        default=None,
        help="Sampling temperature arm; repeatable. Omit for the model default "
        "(the `baseline` arm #404's tables use).",
    )
    parser.add_argument(
        "--baseline",
        action="store_true",
        help="Add the model-default arm ALONGSIDE any --temperature arms. "
        "Implied when no --temperature is given. Pass both to get the "
        "two-column comparison in ONE report.",
    )
    parser.add_argument(
        "--fixture",
        action="append",
        default=None,
        help="Ground-truth stem to include; repeatable. Default: all.",
    )
    parser.add_argument(
        "--title-mode",
        choices=("derived", "stem", "both"),
        default="derived",
        help="Source-title dimension (#459): 'derived' (default) sends the "
        "production-derived title (ingest's behavior, byte-identical to "
        "before this flag); 'stem' sends the file stem (the configuration "
        "the #456 gate measured); 'both' runs EACH arm under both titles "
        "(stem row labeled '+stem-title') for the title-priming A/B.",
    )
    parser.add_argument(
        "--ground-truth-dir",
        type=Path,
        default=_GROUND_TRUTH,
        help="Directory of ground-truth `.md` files. Default: the prose "
        "corpus. Point at evals/decision_extraction/ground_truth for the "
        "AMI transcripts (#457).",
    )
    parser.add_argument(
        "--sources-dir",
        type=Path,
        default=_SOURCES,
        help="Directory of raw sources paired by stem; `<name>.md` is "
        "preferred, `<name>.txt` is the fallback. Default: the prose "
        "corpus sources.",
    )
    parser.add_argument("--host", default=None, help="Ollama host.")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parent / "report.md",
        help="Markdown report path.",
    )
    parser.add_argument(
        "--rescore",
        type=Path,
        default=None,
        help="Rescore a saved runs-*.json against the CURRENT ground truth and "
        "exit. No model calls, so the only thing that changed since the "
        "sweep is your adjudication.",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Run the synthetic self-test and exit (no Ollama needed).",
    )
    parser.add_argument(
        "--union-judge",
        choices=("off", "on", "both"),
        default="off",
        help="Pipeline dimension (#456): 'off' (default) runs the single-cap "
        "extract_concept path only, byte-identical to before this flag "
        "existed; 'on' runs extract_concept_union only; 'both' runs EACH "
        "arm under both pipelines (union+judge row labeled '+union-judge'), "
        "for the before/after recall-delta comparison the Pre-Archive "
        "Measurement Gate requires.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point: self-test, or sweep the corpus and write the report."""
    args = parse_args(argv)
    if args.self_test:
        return self_test()

    if args.runs < 1:
        print("error: --runs must be >= 1", file=sys.stderr)
        return 2

    try:
        truths = discover_ground_truth(
            args.ground_truth_dir, sources_dir=args.sources_dir
        )
    except GroundTruthError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.fixture:
        wanted = set(args.fixture)
        truths = [t for t in truths if t.name in wanted]
    if not truths:
        print("error: no ground-truth fixture selected", file=sys.stderr)
        return 2

    if args.rescore is not None:
        replayed, model = cells_from_json(
            args.rescore.read_text(encoding="utf-8"), truths
        )
        if not replayed:
            print(
                f"error: {args.rescore} holds no run for the selected fixtures",
                file=sys.stderr,
            )
            return 2
        runs = max((len(c.outcomes) for c in replayed), default=0)
        text = build_report(
            replayed, model=model, runs=runs, generated_at=datetime.now(UTC)
        )
        args.output.write_text(text, encoding="utf-8")
        print(f"Rescored {args.rescore} against the current ground truth.")
        print(f"Wrote report: {args.output}")
        _print_queue_note(replayed)
        return 0

    base_arms = resolve_arms(baseline=args.baseline, temperatures=args.temperature)
    arms = expand_title_mode_arms(
        expand_union_judge_arms(base_arms, union_judge_mode=args.union_judge),
        title_mode=args.title_mode,
    )

    cells: list[Cell] = []
    for truth in truths:
        for arm, temperature, union_judge, stem_title in arms:
            print(f"\n=== {truth.name} [{arm}] ===")
            cell = evaluate_cell(
                truth,
                arm,
                temperature,
                args.runs,
                args.model,
                args.host,
                args.timeout,
                union_judge=union_judge,
                stem_title=stem_title,
            )
            if cell.skip_reason:
                print(f"  skipped: {cell.skip_reason}")
            cells.append(cell)

    generated_at = datetime.now(UTC)
    text = build_report(
        cells, model=args.model, runs=args.runs, generated_at=generated_at
    )
    args.output.write_text(text, encoding="utf-8")
    results_dir = args.output.parent / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    stamp = generated_at.strftime("%Y%m%dT%H%M%SZ")
    slug = args.model.replace(":", "-")
    (results_dir / f"cap-eval-{stamp}-{slug}.md").write_text(text, encoding="utf-8")
    # Always save the raw observations, never behind a flag. A sweep costs
    # minutes of GPU and its data is the only thing an adjudication can be
    # replayed against; a flag is something to forget exactly once.
    runs_path = results_dir / f"runs-{stamp}-{slug}.json"
    runs_path.write_text(
        runs_to_json(cells, model=args.model, runs=args.runs, stamp=stamp),
        encoding="utf-8",
    )
    print(f"\nWrote report: {args.output}")
    print(f"Saved raw runs: {runs_path}")
    print(f"  rescore after adjudicating: --rescore {runs_path}")
    _print_queue_note(cells)
    return 0


def _print_queue_note(cells: Sequence[Cell]) -> None:
    """Tell the operator how much human judgment the report is waiting on."""
    queued = sum(len(cell.unjudged_queue()) for cell in cells)
    if queued:
        print(
            f"{queued} unjudged title(s) need a human call — see the "
            f"adjudication queue at the end of the report."
        )


if __name__ == "__main__":
    raise SystemExit(main())
