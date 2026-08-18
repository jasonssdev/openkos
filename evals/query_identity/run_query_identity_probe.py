"""Can any signal tell that two filed insights are the SAME object? (#762)

MANUAL eval tool (NOT pytest, NOT part of the shipped package). Needs Ollama
for the EMBEDDING model only -- zero chat calls, because every answer it
scores was already generated and stored by `evals/query_title/`.

## The question

#762's harm: two people asking the same thing in different words file two
insights whose content barely differs and whose slugs differ substantially,
so they look unrelated and duplicate detection does not group them. The slug
is the permanent OKF Concept ID, so this is an identity defect, not a titling
one -- `evals/query_title/` measured four titling arms and every one scored
exactly the 8-of-16 baseline, because changing which STRING the slug is does
not make two different strings the same object.

#762 lists three directions and says the open question for the first is
"what signal decides same". This probe answers exactly that, and it is
written to be able to say NONE DOES.

## Why #760's negative result does not settle this

#760 measured question-to-DOCUMENT embedding distance and found no floor:
an adjacent question shares the corpus's vocabulary, so proximity reports
topical relatedness rather than whether the document answers it. #762 quotes
that conclusion when it says embedding distance "reports topical relatedness
rather than sameness".

That is a DIFFERENT comparison. Here both sides are filed insights -- two
syntheses over the same bundle, answering paraphrases of one question. The
regime is near-duplicate text against near-duplicate text, not question
against document. So the conclusion has to be re-measured rather than
inherited, and this probe scores it against the shipped signal as a control.

## The three pair classes, and which one matters

- `same-question`  -- two runs of the identical question. An EASY positive; a
                      signal that cannot group these is not worth reading.
- `paraphrase`     -- same subject family, DIFFERENT wording. This is #762's
                      actual case, and the only class that decides anything.
- `different`      -- different subjects. The negatives.

The bar is the same one `query_grounding` used: a signal separates only if
its worst `paraphrase` pair scores above its best `different` pair. A
positive margin means a threshold exists; a negative one means the classes
overlap and no threshold can split them, whatever value is chosen.

`same-question` is reported but never decides the verdict -- it is the
sanity check that would expose a signal broken outright.

## Signals scored

1. `title` -- `resolution.similarity.near_match_score` over the slugs the
   SHIPPED titling rung produces. This is the control: it is what identity
   already runs on elsewhere in this codebase, and #762 predicts it fails.
2. `answer` -- cosine similarity of the two insight BODIES. The untested
   candidate, and the one the regime argument above is about.
3. `question` -- cosine similarity of the two source QUESTIONS. Cheaper to
   reach at write time than the body, so worth knowing whether it suffices.

Usage:

    uv run python -u evals/query_identity/run_query_identity_probe.py --self-test
    uv run python -u evals/query_identity/run_query_identity_probe.py
    uv run python -u evals/query_identity/run_query_identity_probe.py --rescore <pairs.json>
"""

from __future__ import annotations

import argparse
import itertools
import json
import pathlib
import statistics
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final

_EVALS = pathlib.Path(__file__).resolve().parent.parent
sys.path.append(str(_EVALS))
sys.path.append(str(_EVALS / "query_title"))

from run_query_title_probe import _PROBES, resolve_title  # noqa: E402

from openkos.llm.ollama import OllamaClient  # noqa: E402

# Production's own cosine, imported rather than re-implemented -- the same
# rule this file applies to `resolve_title` below. A private import is the
# lesser evil against two copies of one comparison silently disagreeing.
from openkos.resolution.insight_identity import _cosine  # noqa: E402
from openkos.resolution.similarity import near_match_score  # noqa: E402

HERE: Final = pathlib.Path(__file__).resolve().parent
RESULTS_DIR: Final = HERE / "results"
STORED_RUNS: Final = _EVALS / "query_title" / "results"

EMBED_MODEL: Final = "bge-m3"
DEFAULT_TIMEOUT: Final = 1800.0

SHIPPED_ARM: Final = "clause"
"""The titling rung `query --save` actually ships (#696). The control has to
be what production does, not the best arm on the bench."""

SAME_QUESTION: Final = "same-question"
PARAPHRASE: Final = "paraphrase"
DIFFERENT: Final = "different"

SIGNALS: Final = ("title", "answer", "question")


def subject_of(question: str) -> str:
    """The convergence family key, read from `query_title`'s own probe table.

    Imported rather than restated: those `subject=` fields ARE the ground
    truth for what counts as a paraphrase, and a second copy here is how two
    harnesses end up disagreeing about which questions are the same."""
    for probe in _PROBES:
        if probe.question == question:
            return probe.subject
    return ""


@dataclass(frozen=True)
class Pair:
    pair_class: str
    signal: str
    score: float
    question_a: str
    question_b: str


def load_rows() -> list[dict[str, Any]]:
    """Every stored filing from `evals/query_title/results/`, unchanged."""
    rows: list[dict[str, Any]] = []
    for path in sorted(STORED_RUNS.glob("runs-*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        for row in payload.get("rows", []):
            if row.get("answer") and row.get("question"):
                rows.append(row)
    return rows


def classify(a: dict[str, Any], b: dict[str, Any]) -> str:
    """The pair class.

    Two different questions are DIFFERENT objects unless they share a
    non-empty subject family. That is `query_title`'s own semantics: a
    family is a paraphrase relation, and its probe table comments that
    `¿qué es la trazabilidad?` and `¿por qué es importante la trazabilidad?`
    "ask different things and SHOULD file as different objects" — they were
    grouped in an earlier revision and that was the MEASURE being wrong.

    An earlier revision of THIS probe dropped any pair whose members lacked
    a subject, reasoning that absence of a family key is not proof of
    difference. That was wrong in a way that flattered every signal: the
    surviving negatives were only cross-family pairs between the two labelled
    subjects — `trazabilidad` against `fuentes inmutables` — which share no
    vocabulary at all. The `title` signal scored 0.0000 on all 484 of them
    and looked like a perfect separator. The pairs it dropped are precisely
    the HARD negatives: same topic, different question. They decide the
    result, so they are scored."""
    if a["question"] == b["question"]:
        return SAME_QUESTION
    sa, sb = subject_of(a["question"]), subject_of(b["question"])
    if sa and sb and sa == sb:
        return PARAPHRASE
    return DIFFERENT


def measure(embedder: OllamaClient) -> list[Pair]:
    rows = load_rows()
    print(f"loaded {len(rows)} stored filings", flush=True)

    answers = sorted({r["answer"] for r in rows})
    questions = sorted({r["question"] for r in rows})
    print(
        f"embedding {len(answers)} distinct answers + {len(questions)} questions",
        flush=True,
    )
    answer_vec = dict(zip(answers, embedder.embed(answers), strict=True))
    question_vec = dict(zip(questions, embedder.embed(questions), strict=True))

    titles = {
        r["question"] + "\x00" + r["answer"]: resolve_title(
            SHIPPED_ARM, r["question"], r["answer"]
        )[0]
        for r in rows
    }

    pairs: list[Pair] = []
    for a, b in itertools.combinations(rows, 2):
        klass = classify(a, b)
        title_a = titles[a["question"] + "\x00" + a["answer"]]
        title_b = titles[b["question"] + "\x00" + b["answer"]]
        # `near_match_score` returns None when the keys share no content
        # token at all. That is a real 'not a match' verdict, not missing
        # data, so it scores 0.0 rather than being dropped -- dropping it
        # would hide exactly the pairs the shipped signal fails hardest on.
        title_score = near_match_score(title_a, title_b) or 0.0
        for signal, score in (
            ("title", title_score),
            ("answer", _cosine(answer_vec[a["answer"]], answer_vec[b["answer"]])),
            (
                "question",
                _cosine(question_vec[a["question"]], question_vec[b["question"]]),
            ),
        ):
            pairs.append(Pair(klass, signal, score, a["question"], b["question"]))
    return pairs


@dataclass(frozen=True)
class Separation:
    signal: str
    paraphrase_n: int
    paraphrase_worst: float
    paraphrase_median: float
    different_n: int
    different_best: float
    different_median: float
    same_question_median: float

    @property
    def margin(self) -> float:
        return self.paraphrase_worst - self.different_best

    @property
    def separates(self) -> bool:
        return self.margin > 0.0


def separation(pairs: Sequence[Pair], signal: str) -> Separation:
    def vals(klass: str) -> list[float]:
        return [p.score for p in pairs if p.signal == signal and p.pair_class == klass]

    para, diff, same = vals(PARAPHRASE), vals(DIFFERENT), vals(SAME_QUESTION)
    return Separation(
        signal=signal,
        paraphrase_n=len(para),
        paraphrase_worst=min(para) if para else 0.0,
        paraphrase_median=statistics.median(para) if para else 0.0,
        different_n=len(diff),
        different_best=max(diff) if diff else 0.0,
        different_median=statistics.median(diff) if diff else 0.0,
        same_question_median=statistics.median(same) if same else 0.0,
    )


def verdict(seps: Sequence[Separation]) -> str:
    winners = [s for s in seps if s.separates]
    if not winners:
        return (
            "NEGATIVE -- every signal OVERLAPS. No threshold on any of them "
            "refuses a different-subject pair without also refusing a real "
            "paraphrase, so write-time duplicate detection cannot be built on "
            "what is available today. #762's third direction (do nothing, "
            "deliberately) or its second (detect after the fact, in curation) "
            "are what remain."
        )
    best = max(winners, key=lambda s: s.margin)
    return (
        f"POSITIVE -- `{best.signal}` separates with margin {best.margin:+.4f}: "
        f"its worst paraphrase pair ({best.paraphrase_worst:.4f}) scores above "
        f"its best different-subject pair ({best.different_best:.4f}), so a "
        f"threshold between them groups every paraphrase and no stranger."
    )


def render(pairs: Sequence[Pair]) -> str:
    seps = [separation(pairs, s) for s in SIGNALS]
    lines = [
        "# Can any signal tell that two filed insights are the same object? (#762)",
        "",
        f"`{EMBED_MODEL}`, zero chat calls, "
        f"{len([p for p in pairs if p.signal == 'title'])} scored pairs from the "
        f"stored `evals/query_title/` population.",
        "",
        "| signal | paraphrase worst | different best | margin | separates | "
        "paraphrase median | different median | same-question median |",
        "| --- | ---: | ---: | ---: | :---: | ---: | ---: | ---: |",
    ]
    for s in seps:
        lines.append(
            f"| `{s.signal}` | {s.paraphrase_worst:.4f} | {s.different_best:.4f} "
            f"| **{s.margin:+.4f}** | {'yes' if s.separates else 'no'} "
            f"| {s.paraphrase_median:.4f} | {s.different_median:.4f} "
            f"| {s.same_question_median:.4f} |"
        )
    lines += [
        "",
        f"`paraphrase` n={seps[0].paraphrase_n}, `different` n={seps[0].different_n} "
        f"(per signal).",
        "",
        "## Verdict",
        "",
        verdict(seps),
        "",
        "## Reading the columns",
        "",
        "`margin` is the worst paraphrase pair minus the best different-subject "
        "pair. Positive means a threshold sits between the classes; negative "
        "means they overlap and no value can split them — the same bar "
        "`evals/query_grounding/` used to reject the relevance floor.",
        "",
        "`same-question median` is the sanity check: two runs of the identical "
        "question. A signal scoring those low is broken outright and its other "
        "columns should not be read.",
        "",
        "## Limits",
        "",
        "The ground truth is `query_title`'s `subject=` families, which are TWO "
        "subjects. The pair counts are large because each family recurs across "
        "runs and generations, but they rest on two paraphrase relations — so "
        "this measures the SIGNAL's behavior on that regime, not a population "
        "estimate of how often users paraphrase.",
        "",
        "One embedding model, one corpus, one language. The answers were "
        "generated by `qwen3:8b` for a different experiment and are reused "
        "unchanged.",
    ]
    return "\n".join(lines) + "\n"


def _self_test() -> int:
    failures: list[str] = []

    def check(name: str, cond: bool) -> None:
        if not cond:
            failures.append(name)

    check(
        "cosine of identical vectors is 1",
        abs(_cosine([1.0, 0.0], [1.0, 0.0]) - 1.0) < 1e-9,
    )
    check(
        "cosine of orthogonal vectors is 0", abs(_cosine([1.0, 0.0], [0.0, 1.0])) < 1e-9
    )
    check("cosine guards a zero vector", _cosine([0.0, 0.0], [1.0, 0.0]) == 0.0)

    a = {"question": "¿qué es la trazabilidad?"}
    b = {"question": "¿qué significa la trazabilidad?"}
    c = {"question": "¿qué es un MVP?"}
    check(
        "identical questions are same-question", classify(a, dict(a)) == SAME_QUESTION
    )
    check("family members are paraphrase", classify(a, b) == PARAPHRASE)
    check(
        "a same-topic different-question pair is a NEGATIVE",
        classify(a, c) == DIFFERENT,
    )
    d = {
        "question": "¿por qué es importante la trazabilidad en un sistema de conocimiento?"
    }
    check(
        "the hard negative is scored, not dropped -- same topic, different question",
        classify(a, d) == DIFFERENT,
    )

    over = [
        Pair(PARAPHRASE, "answer", 0.50, "q1", "q2"),
        Pair(DIFFERENT, "answer", 0.60, "q1", "q3"),
    ]
    check("overlap reported as no separation", not separation(over, "answer").separates)
    check(
        "overlap verdict is negative",
        verdict([separation(over, "answer")]).startswith("NEGATIVE"),
    )

    clean = [
        Pair(PARAPHRASE, "answer", 0.90, "q1", "q2"),
        Pair(DIFFERENT, "answer", 0.60, "q1", "q3"),
    ]
    check("clean split separates", separation(clean, "answer").separates)
    check(
        "clean verdict is positive",
        verdict([separation(clean, "answer")]).startswith("POSITIVE"),
    )

    total = 11
    for name in failures:
        print(f"FAIL: {name}")
    print(f"self-test: {total - len(failures)}/{total} passed")
    return 1 if failures else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--rescore", type=pathlib.Path, default=None)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--stamp", default="manual")
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()

    if args.rescore is not None:
        payload = json.loads(args.rescore.read_text(encoding="utf-8"))
        print(render([Pair(**p) for p in payload["pairs"]]))
        return 0

    embedder = OllamaClient(model=EMBED_MODEL, timeout=args.timeout)
    pairs = measure(embedder)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (RESULTS_DIR / f"pairs-{args.stamp}-{EMBED_MODEL}.json").write_text(
        json.dumps(
            {"embed_model": EMBED_MODEL, "pairs": [p.__dict__ for p in pairs]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    report = render(pairs)
    out = RESULTS_DIR / f"query-identity-{args.stamp}-{EMBED_MODEL}.md"
    out.write_text(report, encoding="utf-8")
    print(report)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
