"""Is there a relevance floor that refuses ungrounded questions? (#753)

MANUAL eval tool (NOT pytest, NOT part of the shipped package). Needs Ollama
for the EMBEDDING model only -- it makes zero chat calls, because the
question is about retrieval's signal, not about what the model writes with it.

## The question, stated so it can come back NO

#753's ruling is: below a relevance floor, refuse to answer. That presumes a
floor exists -- a number that separates questions the bundle can answer from
questions it cannot. This probe measures whether one does, and it is written
to be able to report that none does.

## Which signal, and why not the obvious one

NOT the fused score. `fusion.fuse` is reciprocal rank fusion: a document's
score is `1/(60 + rank)`, summed across channels. That encodes POSITION and
nothing else, so the top result scores identically whether it is a perfect
match or the least bad of ten irrelevant documents -- which is #753's own
root-cause sentence ("a weak best-match looks identical to a strong one")
restated as arithmetic. A floor cannot be built on it.

`FtsHit.score` is bm25: a magnitude, but one that moves with corpus
statistics and query length, so it is not comparable across questions.

That leaves `VecHit.distance` -- cosine distance between the question's
embedding and the document's. It is the only signal in the pipeline that is
both a magnitude and comparable question to question, so it is the only
candidate floor. Three readings of it are scored:

- `best`      -- the nearest document's distance.
- `gap`       -- `second - best`, how PEAKED the neighbourhood is. A question
                 with one specific answer should stand out from its runners-up;
                 a question the corpus merely shares vocabulary with should
                 sit in a flat field.
- `mean_top3` -- the average of the three nearest, less hostage to one
                 lucky document than `best`.

## The bar

A floor is only useful if it separates the classes CLEANLY. `separation`
reports the gap between the worst grounded question and the best adjacent
one under each reading; a negative value means the classes overlap and NO
threshold can split them -- refusing #753's question would also refuse a
question the issue itself lists as working.

Report the overlap rather than picking the threshold that minimises harm.
Four prompt-level treatments have already been measured and rejected in this
repo; a threshold adopted across an overlap would be the first one adopted
against its own evidence.

Usage:

    uv run python -u evals/query_grounding/run_query_grounding_probe.py --self-test
    uv run python -u evals/query_grounding/run_query_grounding_probe.py --runs 3
    uv run python -u evals/query_grounding/run_query_grounding_probe.py --rescore <runs.json>

**Use `-u`.** Piping a run through `tee` makes Python buffer, and a long run
then looks hung.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import statistics
import sys
import tempfile
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Final

sys.path.append(str(pathlib.Path(__file__).resolve().parent.parent))

from grounding_corpus import ADJACENT, DOCS, GROUNDED, QUESTIONS
from harness_report import arm_identity_line

from openkos.llm.ollama import OllamaClient
from openkos.state import reindex as reindex_module
from openkos.state.vectorstore import open_vector_store

HERE = pathlib.Path(__file__).resolve().parent
RESULTS_DIR = HERE / "results"

EMBED_MODEL: Final = "bge-m3"
_POOL: Final = 10
"""`pool.POOL_FLOOR`, restated: the probe must read the same neighbourhood
production's dense channel reads."""

_DEFAULT_RUNS: Final = 3
"""Embeddings are deterministic for a fixed model and text, so repeated runs
measure re-indexing stability rather than sampling noise -- three is enough
to catch a store that did not settle, and more would buy nothing. This is
NOT the n=15 case: there is no sampling model anywhere in this measurement."""

READINGS: Final = ("best", "gap", "mean_top3")


@dataclass
class Row:
    run: int
    label: str
    question: str
    best: float
    second: float
    mean_top3: float
    nearest_id: str

    @property
    def gap(self) -> float:
        return self.second - self.best


def measure(embedder: OllamaClient, run: int) -> list[Row]:
    """One full pass: build the corpus, index it, and read every question's
    dense neighbourhood."""
    rows: list[Row] = []
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        bundle = root / "bundle"
        for doc_id, (title, body) in DOCS.items():
            path = bundle / f"{doc_id}.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            doc_type = {
                "sources": "Source",
                "concepts": "Concept",
                "decisions": "Decision",
            }[doc_id.split("/")[0]]
            # The title is JSON-quoted, which is valid YAML and is not
            # cosmetic: four of this corpus's titles carry a colon
            # (`Decisión: ...`), and an unquoted YAML scalar containing `: `
            # is a parse error. `_iter_docs` degrades a parse error to a
            # SKIP rather than raising, so the first run of this probe
            # indexed 10 of 14 documents and would have measured a corpus
            # missing its entire `decisions/` folder.
            path.write_text(
                f"---\ntype: {doc_type}\ntitle: {json.dumps(title, ensure_ascii=False)}\n"
                f"sensitivity: private\n---\n\n{body.strip()}\n",
                encoding="utf-8",
            )
        vectors = root / ".openkos" / "vectors.db"
        fts = root / ".openkos" / "fts.db"
        with open_vector_store(vectors) as store:
            report = reindex_module.reindex(
                bundle, store, embedder, fts_db_path=fts, model_tag=EMBED_MODEL
            )
            if report.embedded != len(DOCS):
                raise SystemExit(
                    f"run {run}: indexed {report.embedded} of {len(DOCS)} docs "
                    "-- a partial index would measure a corpus that is not the "
                    "one described."
                )
        with open_vector_store(vectors) as store:
            for label, question in QUESTIONS:
                hits = store.query(embedder.embed([question])[0], k=_POOL)
                if len(hits) < 3:
                    raise SystemExit(
                        f"run {run}: {len(hits)} hit(s) for {question!r}; "
                        "`mean_top3` needs three."
                    )
                rows.append(
                    Row(
                        run=run,
                        label=label,
                        question=question,
                        best=hits[0].distance,
                        second=hits[1].distance,
                        mean_top3=statistics.fmean(h.distance for h in hits[:3]),
                        nearest_id=hits[0].concept_id,
                    )
                )
    return rows


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #


def _values(rows: Sequence[Row], label: str, reading: str) -> list[float]:
    return [float(getattr(row, reading)) for row in rows if row.label == label]


@dataclass
class Separation:
    reading: str
    grounded_worst: float
    adjacent_best: float
    margin: float
    """How much room a threshold has. POSITIVE means the classes are cleanly
    split and any value inside the margin works as a floor; NEGATIVE means
    they overlap and no threshold exists."""
    overlapping_grounded: list[str]
    """Grounded questions a floor tight enough to catch every adjacent one
    would ALSO refuse. The cost side, named rather than counted."""


def separation(rows: Sequence[Row], reading: str) -> Separation:
    """How cleanly `reading` splits the two classes.

    `best` and `mean_top3` are distances (LOWER is more relevant), so a
    grounded question is worst at its MAXIMUM and an adjacent one is most
    dangerous at its MINIMUM. `gap` runs the other way -- larger is more
    peaked, therefore more grounded -- so its comparison is mirrored. Getting
    that backwards would report a clean split for a signal pointing the wrong
    way, which is why the direction is derived here once and not per caller.
    """
    grounded = _values(rows, GROUNDED, reading)
    adjacent = _values(rows, ADJACENT, reading)
    if reading == "gap":
        grounded_worst = min(grounded)
        adjacent_best = max(adjacent)
        margin = grounded_worst - adjacent_best
        offenders = [
            r.question
            for r in rows
            if r.label == GROUNDED and float(getattr(r, reading)) <= adjacent_best
        ]
    else:
        grounded_worst = max(grounded)
        adjacent_best = min(adjacent)
        margin = adjacent_best - grounded_worst
        offenders = [
            r.question
            for r in rows
            if r.label == GROUNDED and float(getattr(r, reading)) >= adjacent_best
        ]
    return Separation(
        reading=reading,
        grounded_worst=grounded_worst,
        adjacent_best=adjacent_best,
        margin=margin,
        overlapping_grounded=sorted(set(offenders)),
    )


def verdict(separations: Sequence[Separation]) -> str:
    usable = [s for s in separations if s.margin > 0]
    if usable:
        best = max(usable, key=lambda s: s.margin)
        return (
            f"FLOOR EXISTS on `{best.reading}` -- the classes separate with a "
            f"margin of {best.margin:.4f}. A threshold inside that margin "
            "refuses every adjacent question and no grounded one."
        )
    closest = max(separations, key=lambda s: s.margin)
    return (
        "NO FLOOR EXISTS on any measured reading -- every one OVERLAPS. The "
        f"closest is `{closest.reading}` at {closest.margin:.4f}. Any "
        "threshold tight enough to refuse the ungrounded question #753 "
        "reports would also refuse "
        f"{len(closest.overlapping_grounded)} question(s) the bundle answers, "
        "including ones the issue itself lists as working. The ruling cannot "
        "be implemented on this signal; it needs a different one."
    )


def render(rows: list[Row], *, runs: int) -> str:
    separations = [separation(rows, reading) for reading in READINGS]
    lines = [
        "# Is there a relevance floor? (#753)",
        "",
        arm_identity_line(
            max_generation_tokens=0,
            context_window=0,
            extra=(
                f"embedding model `{EMBED_MODEL}`",
                f"pool `{_POOL}`",
                f"runs `{runs}`",
                "chat calls `0`",
            ),
        ),
        "",
        f"{len(DOCS)} documents, {len(QUESTIONS)} questions "
        f"({sum(1 for label, _ in QUESTIONS if label == GROUNDED)} grounded / "
        f"{sum(1 for label, _ in QUESTIONS if label == ADJACENT)} adjacent), "
        f"{runs} run(s).",
        "",
        "| reading | grounded worst | adjacent best | margin | separates |",
        "| --- | --- | --- | --- | --- |",
    ]
    for sep in separations:
        lines.append(
            f"| `{sep.reading}` | {sep.grounded_worst:.4f} | "
            f"{sep.adjacent_best:.4f} | {sep.margin:+.4f} | "
            f"{'YES' if sep.margin > 0 else 'no'} |"
        )
    lines += ["", f"**Verdict:** {verdict(separations)}", ""]

    for sep in separations:
        if sep.margin <= 0 and sep.overlapping_grounded:
            lines += [
                f"## Grounded questions a `{sep.reading}` floor would refuse",
                "",
            ]
            lines += [f"- {q}" for q in sep.overlapping_grounded]
            lines += [""]

    lines += [
        "## Every question",
        "",
        "| class | best | gap | mean_top3 | nearest | question |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in sorted(rows, key=lambda r: (r.label, r.best)):
        if row.run != 1:
            continue
        lines.append(
            f"| {row.label} | {row.best:.4f} | {row.gap:.4f} | "
            f"{row.mean_top3:.4f} | `{row.nearest_id}` | {row.question} |"
        )
    return "\n".join(lines)


def write_results(rows: list[Row], *, stamp: str) -> pathlib.Path:
    RESULTS_DIR.mkdir(exist_ok=True)
    out = RESULTS_DIR / f"runs-{stamp}-{EMBED_MODEL}.json"
    out.write_text(
        json.dumps(
            {"stamp": stamp, "model": EMBED_MODEL, "rows": [asdict(r) for r in rows]},
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return out


def _self_test() -> int:
    failures: list[str] = []

    def check(label: str, got: object, want: object) -> None:
        if got != want:
            failures.append(f"{label}: got {got!r}, want {want!r}")

    check(
        "every question carries a known label",
        {label for label, _ in QUESTIONS} <= {GROUNDED, ADJACENT},
        True,
    )
    check("both classes are populated", len({label for label, _ in QUESTIONS}), 2)
    check(
        "#753's own reported question is in the adjacent set",
        any(
            label == ADJACENT and "verdad contextual" in question
            for label, question in QUESTIONS
        ),
        True,
    )
    check("no question is duplicated", len({q for _, q in QUESTIONS}), len(QUESTIONS))

    def row(label: str, best: float, second: float, mean3: float) -> Row:
        return Row(1, label, f"q{best}{label}", best, second, mean3, "concepts/x")

    # Cleanly separated on `best`: every grounded question is nearer than
    # every adjacent one.
    clean = [
        row(GROUNDED, 0.70, 0.90, 0.80),
        row(GROUNDED, 0.75, 0.95, 0.85),
        row(ADJACENT, 0.95, 0.96, 0.96),
        row(ADJACENT, 1.10, 1.11, 1.11),
    ]
    sep = separation(clean, "best")
    check("a clean split has a positive margin", round(sep.margin, 4), 0.20)
    check("and names no casualties", sep.overlapping_grounded, [])
    check(
        "and the verdict says a floor exists",
        verdict([separation(clean, r) for r in READINGS]).startswith("FLOOR EXISTS"),
        True,
    )

    # Overlapping: one grounded question sits behind an adjacent one.
    overlap = [
        row(GROUNDED, 0.70, 0.90, 0.80),
        row(GROUNDED, 1.05, 1.06, 1.06),
        row(ADJACENT, 1.02, 1.03, 1.03),
        row(ADJACENT, 1.10, 1.11, 1.11),
    ]
    sep_overlap = separation(overlap, "best")
    check("an overlap has a negative margin", sep_overlap.margin < 0, True)
    check(
        "and names the grounded question a floor would refuse",
        len(sep_overlap.overlapping_grounded),
        1,
    )
    check(
        "and the verdict refuses to pick a threshold anyway",
        verdict([separation(overlap, r) for r in READINGS]).startswith("NO FLOOR"),
        True,
    )

    # `gap` runs the OTHER WAY -- larger is more grounded. A reading whose
    # direction was copied from `best` would call this pair separated when
    # the grounded question is the FLATTER of the two.
    directional = [
        row(GROUNDED, 0.70, 0.95, 0.90),  # gap 0.25
        row(ADJACENT, 1.00, 1.02, 1.02),  # gap 0.02
    ]
    gap_sep = separation(directional, "gap")
    check("a peaked grounded question separates on gap", gap_sep.margin > 0, True)
    inverted = [
        row(GROUNDED, 0.70, 0.72, 0.72),  # gap 0.02 -- flat
        row(ADJACENT, 1.00, 1.25, 1.20),  # gap 0.25 -- peaked
    ]
    check(
        "and a flat grounded question does NOT",
        separation(inverted, "gap").margin < 0,
        True,
    )

    check(
        "the report renders",
        "Is there a relevance floor" in render(clean, runs=1),
        True,
    )

    if failures:
        print("SELF-TEST FAILED:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print(
        "SELF-TEST PASSED: both question classes are populated and #753's own "
        "question is among the adjacent ones, a clean split reports a positive "
        "margin with no casualties, an overlap reports a negative one and NAMES "
        "the grounded questions a floor would refuse, and `gap`'s direction is "
        "mirrored so a flat grounded question cannot read as separated."
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=_DEFAULT_RUNS)
    parser.add_argument("--model", default=EMBED_MODEL)
    parser.add_argument("--host", default=None)
    parser.add_argument("--rescore", type=pathlib.Path, default=None)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()

    if args.rescore is not None:
        data = json.loads(args.rescore.read_text(encoding="utf-8"))
        stored = [Row(**r) for r in data["rows"]]
        print(render(stored, runs=max(r.run for r in stored)))
        return 0

    embedder = OllamaClient(model=args.model, host=args.host)
    rows: list[Row] = []
    for run in range(1, args.runs + 1):
        print(f"=== run {run}/{args.runs}", flush=True)
        rows.extend(measure(embedder, run))

    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    report = render(rows, runs=args.runs)
    saved = write_results(rows, stamp=stamp)
    saved.with_suffix(".md").write_text(report, encoding="utf-8")
    print()
    print(report)
    print(f"\nSaved raw runs: {saved}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
