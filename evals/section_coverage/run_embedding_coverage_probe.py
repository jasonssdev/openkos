"""Can embedding similarity separate a LOST section from a covered one? (#793)

The third coverage candidate, and the first of the two #793's refutation
deliberately left untried: `quote` (verbatim quoting) and `overlap` (content-
word share) are both REFUTED -- on two predicates and two models the healthy
transcript scored *worse* than the constructed loss. This probe measures
whether the shipped embedder (`bge-m3`, the same model `state/vectorstore`
retrieves with) can do what the lexical rules could not.

## What is scored, and with which rule

Every number is a **max cosine similarity** between one section's body and
one set of object texts (an object's text is `"{title}. {description}
{body}"` -- everything the object carries). Two classes per stored run:

- **quiet** -- a section at least one object QUOTES, scored against ALL of
  the run's objects. It produced something, so a coverage signal must read
  it as covered: HIGH similarity.
- **loss** -- the same section scored against the run's objects MINUS the
  ones quoting it (the canonical leave-one-section-out construction). Its
  contribution has been deleted, so a working signal must read it as
  uncovered: LOW similarity.

Attribution is by `quoting_objects` -- the VERBATIM rule -- while scoring is
by embedding. That independence is the point, twice over: attributing with
the rule you score with makes every trial a hit by construction (the trap
`covers_by_quoting` exists to refuse), and the harness's own excluded-column
lesson says the attribution rule decides which sections can be measured at
all, so the counts of what was skipped are printed beside what was not.

The reported 0.2.8 failure joins as a third source of rows: each
`helios-overview` run is cut down to the fixture's `reported_objects`
(matched by casefolded `type: title` equality), and the two sections that
run lost (`## Storage`, `## Components`) are scored as **reported-loss**
while the two that produced objects are **reported-quiet**.

## The verdict, pre-registered

Per model, the signal SEPARATES only if the LOWEST quiet similarity sits
ABOVE the HIGHEST loss similarity -- zero false flags, the same bar every
prior coverage candidate was held to, applied to the worst case rather than
the averages. Anything else OVERLAPS. Shipping additionally requires
separation on BOTH stored models; the prior candidate separated on
`qwen3:8b` and died on `phi4:14b`, which is why one model is not evidence.

Usage:

    uv run python -u evals/section_coverage/run_embedding_coverage_probe.py --self-test
    uv run python -u evals/section_coverage/run_embedding_coverage_probe.py
    uv run python -u evals/section_coverage/run_embedding_coverage_probe.py \
        --rescore evals/section_coverage/results/<file>.json

`--self-test` and `--rescore` make no model calls. The measurement itself
needs only EMBED calls (`bge-m3`) -- the stored extraction runs are the
committed ones, so no chat call is spent.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import pathlib
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, fields
from typing import Any, Final

_HERE: Final = pathlib.Path(__file__).resolve().parent
_REPO: Final = _HERE.parent.parent
RESULTS_DIR: Final = _HERE / "results"

sys.path.insert(0, str(_REPO / "src"))

from openkos.config import DEFAULT_EMBEDDING_MODEL  # noqa: E402
from openkos.llm.ollama import OllamaClient  # noqa: E402


def _load(path: pathlib.Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise SystemExit(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_coverage = _load(_HERE / "section_coverage.py", "_section_coverage_embedding")
_fixtures = _load(_HERE / "section_fixtures.py", "_section_fixtures_embedding")

STORED_RUNS: Final = (
    RESULTS_DIR / "runs-20260821T233809Z-qwen3-8b.json",
    RESULTS_DIR / "runs-20260823T150831Z-phi4-14b.json",
)
"""The committed sweeps every prior verdict was measured on. Scoring the
same runs is what makes this column comparable to the refuted ones."""

QUIET: Final = "quiet"
LOSS: Final = "loss"
REPORTED_QUIET: Final = "reported-quiet"
REPORTED_LOSS: Final = "reported-loss"

_QUIET_CLASSES: Final = frozenset({QUIET, REPORTED_QUIET})
_LOSS_CLASSES: Final = frozenset({LOSS, REPORTED_LOSS})
_ALL_CLASSES: Final = _QUIET_CLASSES | _LOSS_CLASSES


def object_text(obj: dict[str, Any]) -> str:
    """Everything one stored object carries, as the text to embed."""
    return f"{obj.get('title', '')}. {obj.get('description', '')} {obj.get('body', '')}"


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Plain cosine similarity; 0.0 when either vector is all zeros, so a
    degenerate embedding reads as no-similarity rather than raising."""
    # strict: a silent zip over mismatched dimensions would score the
    # overlapping prefix as if it were the whole vector; the client already
    # refuses a wrong-dimension response, and this keeps the invariant here.
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if not norm_a or not norm_b:
        return 0.0
    return dot / (norm_a * norm_b)


class EmbeddingIndex:
    """Batch-embeds distinct texts once and answers max-similarity queries.

    Distinct-once matters beyond cost: the same object text appears in many
    (run, section) rows, and re-embedding it would let a non-deterministic
    embedder hand two rows two different vectors for one text.
    """

    def __init__(self, embed: Callable[[Sequence[str]], list[list[float]]]) -> None:
        self._embed = embed
        self._vectors: dict[str, list[float]] = {}

    def prime(self, texts: Sequence[str]) -> None:
        fresh = sorted({text for text in texts if text not in self._vectors})
        if not fresh:
            return
        for text, vector in zip(fresh, self._embed(fresh), strict=True):
            self._vectors[text] = vector

    def max_similarity(self, section_body: str, texts: Sequence[str]) -> float:
        self.prime([section_body, *texts])
        section_vector = self._vectors[section_body]
        return max(cosine(section_vector, self._vectors[text]) for text in texts)


@dataclass(frozen=True)
class SectionStat:
    """One (run, section, class) row: the raw statistic, never a verdict.

    Storing the similarity rather than a thresholded boolean is what lets
    `--rescore` sweep every threshold with no embed call -- the same
    record-the-statistic design the #837 measurement used."""

    model: str
    fixture: str
    run: int
    heading: str
    klass: str
    objects: int
    """How many object texts this row was scored against."""
    similarity: float


_FIELD_NAMES: Final = tuple(field.name for field in fields(SectionStat))
_FIELD_KINDS: Final[dict[str, tuple[type, ...]]] = {
    field.name: (str,)
    if field.type == "str"
    else (int, float)
    if field.type == "float"
    else (int,)
    for field in fields(SectionStat)
}


def _parse_stats(value: object) -> list[SectionStat]:
    """The rescore shape gate every probe in this family carries."""
    if not isinstance(value, list):
        raise SystemExit(
            f"rescore file must hold a JSON array of rows, got {type(value).__name__}"
        )
    stats: list[SectionStat] = []
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
            if isinstance(cell, bool) or not isinstance(cell, _FIELD_KINDS[name]):
                kinds = " or ".join(k.__name__ for k in _FIELD_KINDS[name])
                raise SystemExit(
                    f"rescore row {index}: field {name!r} holds "
                    f"{type(cell).__name__} {cell!r}, not {kinds}"
                )
        # An off-vocabulary class would silently enter NEITHER the quiet nor
        # the loss set and disappear from the verdict -- a skew, not an
        # error, which is exactly what this gate exists to turn into a
        # sentence.
        if row["klass"] not in _ALL_CLASSES:
            raise SystemExit(
                f"rescore row {index}: klass {row['klass']!r} is not one of "
                f"{sorted(_ALL_CLASSES)}"
            )
        stats.append(SectionStat(**row))
    return stats


@dataclass(frozen=True)
class Exclusions:
    """What the walk could NOT score, said out loud -- a filtered probe that
    hides its complement reads as cleaner than it is."""

    unattributed: int
    total_removal: int
    unmatched_reported_runs: int


_EXCLUSION_FIELDS: Final = tuple(field.name for field in fields(Exclusions))


def _parse_exclusions(value: object) -> Exclusions:
    """The `exclusions` half of the rescore gate: exact keys, int counts.

    `Exclusions(**stored)` on a drifted file raises a TypeError naming a
    Python parameter, which is a traceback about this code rather than a
    sentence about that file."""
    if not isinstance(value, dict) or sorted(value) != sorted(_EXCLUSION_FIELDS):
        raise SystemExit(
            f"rescore 'exclusions' must be an object with exactly "
            f"{sorted(_EXCLUSION_FIELDS)}, got "
            f"{sorted(value) if isinstance(value, dict) else type(value).__name__}"
        )
    for name in _EXCLUSION_FIELDS:
        cell = value[name]
        if isinstance(cell, bool) or not isinstance(cell, int):
            raise SystemExit(
                f"rescore 'exclusions' field {name!r} holds "
                f"{type(cell).__name__} {cell!r}, not int"
            )
    return Exclusions(**value)


def score_run(
    row: dict[str, Any],
    fixture: Any,
    index: EmbeddingIndex,
) -> tuple[list[SectionStat], Exclusions]:
    """Every scorable (section, class) row of one stored run."""
    texts = [object_text(obj) for obj in row.get("objects") or []]
    stats: list[SectionStat] = []
    unattributed = total_removal = unmatched = 0
    model = str(row.get("model", ""))
    run = int(row.get("run", 0))
    if texts:
        for section in _coverage.split_sections(fixture.text):
            if not section.body.strip():
                continue
            attributed = set(_coverage.quoting_objects(texts, section.body))
            if not attributed:
                unattributed += 1
                continue
            stats.append(
                SectionStat(
                    model=model,
                    fixture=fixture.name,
                    run=run,
                    heading=section.heading,
                    klass=QUIET,
                    objects=len(texts),
                    similarity=round(index.max_similarity(section.body, texts), 4),
                )
            )
            remaining = [
                text
                for position, text in enumerate(texts)
                if position not in attributed
            ]
            if not remaining:
                total_removal += 1
                continue
            stats.append(
                SectionStat(
                    model=model,
                    fixture=fixture.name,
                    run=run,
                    heading=section.heading,
                    klass=LOSS,
                    objects=len(remaining),
                    similarity=round(index.max_similarity(section.body, remaining), 4),
                )
            )
    if fixture.reported_objects:
        reported = {label.casefold() for label in fixture.reported_objects}
        kept = [
            object_text(obj)
            for obj in row.get("objects") or []
            if f"{obj.get('type', '')}: {obj.get('title', '')}".casefold() in reported
        ]
        if not kept:
            unmatched += 1
        else:
            fired = set(fixture.must_fire)
            quiet_headings = set(fixture.must_stay_quiet)
            for section in _coverage.split_sections(fixture.text):
                if not section.body.strip():
                    continue
                if section.heading in fired:
                    klass = REPORTED_LOSS
                elif section.heading in quiet_headings:
                    klass = REPORTED_QUIET
                else:
                    continue
                stats.append(
                    SectionStat(
                        model=model,
                        fixture=fixture.name,
                        run=run,
                        heading=section.heading,
                        klass=klass,
                        objects=len(kept),
                        similarity=round(index.max_similarity(section.body, kept), 4),
                    )
                )
    return stats, Exclusions(unattributed, total_removal, unmatched)


def _merge(exclusions: Sequence[Exclusions]) -> Exclusions:
    return Exclusions(
        unattributed=sum(e.unattributed for e in exclusions),
        total_removal=sum(e.total_removal for e in exclusions),
        unmatched_reported_runs=sum(e.unmatched_reported_runs for e in exclusions),
    )


def verdict_for(stats: Sequence[SectionStat], model: str) -> str:
    """One model's verdict: worst quiet against worst loss, zero-false-flag."""
    quiet = [
        s.similarity for s in stats if s.model == model and s.klass in _QUIET_CLASSES
    ]
    loss = [
        s.similarity for s in stats if s.model == model and s.klass in _LOSS_CLASSES
    ]
    if not quiet or not loss:
        return f"{model}: NO VERDICT -- both classes are required, got {len(quiet)} quiet and {len(loss)} loss row(s)."
    lowest_quiet = min(quiet)
    highest_loss = max(loss)
    if lowest_quiet > highest_loss:
        return (
            f"{model}: SEPARATES -- every quiet section ({lowest_quiet:.4f} at "
            f"the lowest) sits above every constructed or reported loss "
            f"({highest_loss:.4f} at the highest); any threshold between the "
            "two flags every loss and no covered section."
        )
    return (
        f"{model}: OVERLAPS -- the lowest quiet section ({lowest_quiet:.4f}) "
        f"does not clear the highest loss ({highest_loss:.4f}); a threshold "
        "catching every loss also flags a section that produced objects, and "
        "zero false flags is the bar every coverage candidate is held to."
    )


def render(stats: list[SectionStat], exclusions: Exclusions) -> str:
    lines: list[str] = [
        "",
        "=" * 78,
        "CAN EMBEDDING SIMILARITY SEPARATE A LOST SECTION? (#793)",
        "=" * 78,
        "",
    ]
    if not stats:
        lines.append("NO DATA -- no scorable row.")
        return "\n".join(lines)
    models = sorted({s.model for s in stats})
    lines.append(
        f"   {'model':<12}{'fixture':<18}{'class':<16}{'n':>4}{'min':>8}"
        f"{'median':>8}{'max':>8}"
    )
    import statistics as _stats

    for model in models:
        for fixture in sorted({s.fixture for s in stats if s.model == model}):
            for klass in (QUIET, LOSS, REPORTED_QUIET, REPORTED_LOSS):
                sims = [
                    s.similarity
                    for s in stats
                    if s.model == model and s.fixture == fixture and s.klass == klass
                ]
                if not sims:
                    continue
                lines.append(
                    f"   {model:<12}{fixture:<18}{klass:<16}{len(sims):>4}"
                    f"{min(sims):>8.4f}{_stats.median(sims):>8.4f}{max(sims):>8.4f}"
                )
    lines += [
        "",
        f"   Excluded, and counted rather than hidden: {exclusions.unattributed} "
        "section-runs no object quotes (no constructed loss exists), "
        f"{exclusions.total_removal} where removal empties the object list, "
        f"{exclusions.unmatched_reported_runs} run(s) where no object matched "
        "the fixture's reported set.",
        "",
    ]
    for model in models:
        lines.append(f"VERDICT {verdict_for(stats, model)}")
    return "\n".join(lines)


def threshold_predicate(index: EmbeddingIndex, tau: float) -> Any:
    """The `CoveragePredicate` this statistic would ship as, at one
    threshold -- built ONLY so the self-test can hold this probe's walk
    against the canonical `leave_one_section_out`, and so a shipping slice
    would have its shape already named. `checkable` is non-blank text: an
    embedder scores any text, so the gate excludes only what has no bytes
    to embed."""
    return _coverage.CoveragePredicate(
        name=f"embedding@{tau:g}",
        covers=lambda texts, body: index.max_similarity(body, list(texts)) >= tau,
        checkable=lambda body: bool(body.strip()),
        describe=f"max cosine of {DEFAULT_EMBEDDING_MODEL} object-vs-section >= {tau:g}",
        covers_by_quoting=False,
    )


def _self_test() -> int:
    failures: list[str] = []

    def check(condition: bool, why: str) -> None:
        if not condition:
            failures.append(why)

    check(cosine([1.0, 0.0], [1.0, 0.0]) == 1.0, "identical vectors must score 1.0")
    check(cosine([1.0, 0.0], [0.0, 1.0]) == 0.0, "orthogonal vectors must score 0.0")
    check(
        cosine([0.0, 0.0], [1.0, 0.0]) == 0.0, "a zero vector must score 0.0, not raise"
    )

    # A fake embedder deterministic enough to reason about: the vector is
    # keyword indicator coordinates, so similarity is 1.0 exactly when two
    # texts share their keyword.
    # Keyword coordinates chosen from the section BODIES (split_sections
    # keeps the heading out of the body, so "storage" would never appear).
    keywords = ("mysql", "components", "ownership", "helios")

    def fake_embed(texts: Sequence[str]) -> list[list[float]]:
        return [
            [1.0 if keyword in text.casefold() else 0.0 for keyword in keywords]
            for text in texts
        ]

    index = EmbeddingIndex(fake_embed)
    check(
        index.max_similarity("MySQL things", ["about mysql", "about ownership"]) == 1.0,
        "max similarity must take the best-matching text",
    )

    # Distinct-once: priming twice must not re-embed. A counting embedder
    # proves it.
    calls: list[int] = []

    def counting_embed(texts: Sequence[str]) -> list[list[float]]:
        calls.append(len(texts))
        return [[1.0] for _ in texts]

    once = EmbeddingIndex(counting_embed)
    once.prime(["a", "b", "a"])
    once.prime(["a", "b"])
    check(calls == [2], f"each distinct text must embed exactly once (got {calls})")

    # The walk against the CANONICAL leave-one-section-out, at a threshold.
    # Same fixture bytes, same attribution rule; if this probe's rows and
    # the canonical rows disagree about which sections have a constructed
    # loss, the statistic is not measuring what the framework measures.
    fixture = _fixtures.HELIOS_OVERVIEW
    run_row = {
        "fixture": fixture.name,
        "run": 1,
        "model": "fake",
        "error": None,
        "objects": [
            {
                "type": "Concept",
                "title": "Helios Data Platform",
                "description": "The platform.",
                "body": "The Helios Data Platform, usually shortened to HDP, is the ingestion and\nquery layer used by the internal analytics team.",
            },
            {
                "type": "Concept",
                "title": "Storage decision",
                "description": "About storage.",
                "body": "HDP standardized on MySQL 8 as its primary datastore. The decision was\ndriven by the operations team's existing MySQL tooling.",
            },
        ],
    }
    stats, exclusions = score_run(run_row, fixture, index)
    check(
        exclusions
        == Exclusions(unattributed=2, total_removal=0, unmatched_reported_runs=0),
        "the toy run's exclusions must be counted: Components and Ownership "
        f"have no quoting object (got {exclusions})",
    )
    texts = [object_text(obj) for obj in run_row["objects"]]
    canonical = _coverage.leave_one_section_out(
        texts, fixture.text, threshold_predicate(index, 0.5)
    )
    mine_loss = {(s.heading, s.similarity >= 0.5) for s in stats if s.klass == LOSS}
    theirs = {(row.heading, not row.named_after) for row in canonical}
    check(
        mine_loss == theirs,
        f"the probe's loss rows must match canonical leave-one-section-out "
        f"at the same threshold (mine {sorted(mine_loss)}, canonical "
        f"{sorted(theirs)})",
    )
    quiet_rows = [s for s in stats if s.klass == QUIET]
    check(
        bool(quiet_rows) and all(s.similarity == 1.0 for s in quiet_rows),
        "a quoted section must score 1.0 against the full set under the "
        "keyword embedder",
    )

    # The reported-ablation arm: only reported objects are kept, must_fire
    # sections become reported-loss, and a run with no matching object is
    # excluded with its count.
    reported = [s for s in stats if s.klass in (REPORTED_LOSS, REPORTED_QUIET)]
    check(
        {s.heading for s in reported if s.klass == REPORTED_LOSS}
        == set(fixture.must_fire),
        "every must-fire section must be scored as reported-loss",
    )
    no_match = {
        "fixture": fixture.name,
        "run": 2,
        "model": "fake",
        "error": None,
        "objects": [
            {"type": "Concept", "title": "Nothing", "description": "x", "body": "y"}
        ],
    }
    # One attribution row may or may not survive; the reported arm must not.
    _, missing = score_run(no_match, fixture, index)
    check(
        missing.unmatched_reported_runs == 1,
        "a run with no reported-object match must be counted, not skipped silently",
    )
    empty_run = {"fixture": fixture.name, "run": 3, "model": "fake", "objects": []}
    empty_stats, empty_excl = score_run(empty_run, fixture, index)
    check(
        not [s for s in empty_stats if s.klass in (QUIET, LOSS)]
        and empty_excl.unmatched_reported_runs == 1,
        "a run with no objects must produce no attribution rows and count "
        "its unmatched reported arm",
    )

    # The verdict, both ways, on hand-built stats.
    def _stat(klass: str, sim: float, model: str = "m") -> SectionStat:
        return SectionStat(
            model=model,
            fixture="f",
            run=1,
            heading="## H",
            klass=klass,
            objects=1,
            similarity=sim,
        )

    separated = [_stat(QUIET, 0.8), _stat(LOSS, 0.4), _stat(REPORTED_LOSS, 0.5)]
    check(
        "SEPARATES" in verdict_for(separated, "m"),
        "quiet above every loss must SEPARATE",
    )
    overlapped = [_stat(QUIET, 0.45), _stat(LOSS, 0.5)]
    check(
        "OVERLAPS" in verdict_for(overlapped, "m"),
        "a quiet section under a loss must OVERLAP",
    )
    check(
        "NO VERDICT" in verdict_for([_stat(QUIET, 0.9)], "m"),
        "one class alone must be NO VERDICT",
    )

    # The rescore gate.
    roundtrip = _parse_stats(json.loads(json.dumps([asdict(_stat(QUIET, 0.5))])))
    check(roundtrip == [_stat(QUIET, 0.5)], "stats must round-trip unchanged")

    def _refused(value: Any) -> str:
        try:
            _parse_stats(value)
        except SystemExit as exc:
            return str(exc)
        return "NO REFUSAL"

    check("JSON array" in _refused({}), "a non-list file must be refused")
    mistyped = asdict(_stat(QUIET, 0.5))
    mistyped["similarity"] = "0.5"
    check(
        "'similarity'" in _refused([mistyped]),
        "a string similarity must be refused by name",
    )
    # A class outside the four-member vocabulary would enter NEITHER the
    # quiet nor the loss set and silently vanish from the verdict.
    off_vocab = asdict(_stat(QUIET, 0.5))
    off_vocab["klass"] = "bogus"
    check(
        "'bogus'" in _refused([off_vocab]),
        "an off-vocabulary klass must be refused by name, never dropped "
        "from both classes silently",
    )

    # The exclusions half of the rescore gate.
    check(
        _parse_exclusions(
            {"unattributed": 1, "total_removal": 0, "unmatched_reported_runs": 2}
        )
        == Exclusions(1, 0, 2),
        "a well-formed exclusions object must parse",
    )

    def _excl_refused(value: Any) -> str:
        try:
            _parse_exclusions(value)
        except SystemExit as exc:
            return str(exc)
        return "NO REFUSAL"

    check(
        "exactly" in _excl_refused({"unattributed": 1}),
        "an exclusions object missing keys must be refused with the expected key set",
    )
    check(
        "'unattributed'"
        in _excl_refused(
            {"unattributed": True, "total_removal": 0, "unmatched_reported_runs": 0}
        ),
        "a boolean exclusion count must be refused -- bool subclasses int",
    )

    check(
        "VERDICT" in render(separated, Exclusions(0, 0, 0)),
        "render must print verdicts",
    )
    check(
        "NO DATA" in render([], Exclusions(0, 0, 0)), "an empty render must say NO DATA"
    )

    if failures:
        for why in failures:
            print(f"SELF-TEST FAILED: {why}")
        return 1
    print("self-test OK (no model calls)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="http://127.0.0.1:11434")
    parser.add_argument("--embedding-model", default=DEFAULT_EMBEDDING_MODEL)
    parser.add_argument("--rescore", type=pathlib.Path, default=None)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()

    if args.rescore is not None:
        stored = json.loads(args.rescore.read_text())
        if not isinstance(stored, dict) or "stats" not in stored:
            raise SystemExit(
                f"{args.rescore} does not look like this probe's output "
                "(expected an object with 'stats' and 'exclusions')"
            )
        stats = _parse_stats(stored["stats"])
        print(render(stats, _parse_exclusions(stored.get("exclusions"))))
        return 0

    fixtures_by_name = {f.name: f for f in _fixtures.build_fixtures()}
    client = OllamaClient(args.embedding_model, host=args.host)
    index = EmbeddingIndex(client.embed)
    all_stats: list[SectionStat] = []
    all_exclusions: list[Exclusions] = []
    for stored_path in STORED_RUNS:
        rows = json.loads(stored_path.read_text())
        for row in rows:
            if row.get("error"):
                continue
            fixture = fixtures_by_name.get(str(row.get("fixture", "")))
            if fixture is None:
                raise SystemExit(
                    f"{stored_path} names fixture {row.get('fixture')!r}, which "
                    "section_fixtures.py does not define"
                )
            stats, exclusions = score_run(row, fixture, index)
            all_stats.extend(stats)
            all_exclusions.append(exclusions)
    merged = _merge(all_exclusions)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    out = RESULTS_DIR / f"embedding-coverage-{stamp}.json"
    out.write_text(
        json.dumps(
            {
                "embedding_model": args.embedding_model,
                "stats": [asdict(s) for s in all_stats],
                "exclusions": asdict(merged),
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    print(render(all_stats, merged))
    print(f"\nstored {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
