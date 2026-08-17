"""Does excluding function words from containment cost a false positive? (#755)

MANUAL eval tool (NOT pytest, NOT part of the shipped package). Needs NO
model and NO GPU: `resolution/similarity.py` is deterministic and stdlib-only,
so the whole sweep is a pair scan over real titles.

## The two arms, and which way round they face

- **baseline** -- the PRE-#755 algorithm, frozen in this file.
- **treatment** -- production's own `near_match_score`, imported.

That orientation is deliberate and it is the one this repo learned the hard
way: once a treatment ships, a probe that reimplements it measures the
candidate against ITSELF and reports a guaranteed tie. Freezing the BASELINE
instead means this harness keeps measuring the real delta after the fix has
landed, and turns into an ablation rather than a tautology.

## What is scored, and why only the delta

Every pair whose verdict CHANGED between the arms, in both directions:

- **recovered** -- newly matched, adjudicated `duplicate`. The point of #755.
- **false positive** -- newly matched, adjudicated `distinct`. ONE rejects
  the change, per the #630 bar.
- **fp removed** -- newly LOST, adjudicated `distinct`. A pair the baseline
  wrongly matched. These exist: `model` scores exactly 0.750 against `del`,
  so a Spanish function word sitting in the larger set was a lexical target
  that manufactured matches between unrelated titles.
- **regression** -- newly lost, adjudicated `duplicate`. Also rejects.

Pairs whose verdict did not change are not scored: they are identical under
both arms by construction, and counting them would drown the delta in
thousands of agreements and make any rate look excellent.

## Exposure is reported, because a zero can be vacuous

`exposed` counts the delta pairs that were adjudicated at all. Zero false
positives out of zero exposure is the corpus staying silent, not the change
proving safe, and the verdict says UNFALSIFIABLE rather than SHIPPABLE. This
harness has been burned by the other reading before (#706, #622).

## Adjudication is explicit, never inferred

Deriving `duplicate`/`distinct` from any similarity rule would beg the
question, so it lives in `adjudication.json`, hand-written, keyed by the
normalized pair. Anything unadjudicated is REPORTED as unadjudicated and
counted in neither column. `--rescore` re-derives every verdict from the
stored pairs after the file is edited, with no re-scan.

Usage:

    uv run python -u evals/duplicate_function_words/run_function_word_probe.py --self-test
    uv run python -u evals/duplicate_function_words/run_function_word_probe.py
    uv run python -u evals/duplicate_function_words/run_function_word_probe.py --rescore
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Final

from openkos.resolution.normalize import normalize_key
from openkos.resolution.similarity import (
    MIN_TOKEN_LENGTH,
    SIMILARITY_THRESHOLD,
    _match_tokens,
    near_match_score,
)

HERE = Path(__file__).resolve().parent
RESULTS_DIR = HERE / "results"
ADJUDICATION_PATH = HERE / "adjudication.json"
_EVALS_ROOT = HERE.parent

DUPLICATE = "duplicate"
DISTINCT = "distinct"
CROSS_TYPE = "cross-type"
"""A pair production would never compare, so neither arm's verdict on it can
reach a user.

`candidates.py` compares SAME-TYPE documents only, and the corpus harvested
here is titles without their types -- this scan is therefore a strict
SUPERSET of the comparisons production makes. That direction is the safe one
for a false-positive bar (it can over-report, never hide), but charging the
treatment for a pair that cannot occur would reject a fix on an artifact of
the harness. Ruled explicitly, never inferred, and reported in its own
column so the exclusion is visible rather than quiet."""

_TITLE_KEYS: Final = (
    "titles",
    "kept",
    "objects",
    "retained_titles",
    "reask_added_titles",
    "discarded_titles",
)
"""JSON keys under which stored eval runs carry object titles. Harvested
rather than hand-written: the population must be titles extraction ACTUALLY
produced, in both languages, or the false-positive risk is measured against
prose I chose."""

_MIN_TITLE_CHARS: Final = 3
_MAX_TITLE_CHARS: Final = 120


# --------------------------------------------------------------------------- #
# The frozen baseline: pre-#755 `near_match_score`
# --------------------------------------------------------------------------- #


def baseline_near_match_score(key_a: str, key_b: str) -> float | None:
    """`near_match_score` EXACTLY as it stood before #755.

    Copied deliberately, not imported: this is the arm the change is measured
    against, and it must not move when production does. `--self-test` pins it
    against three behaviours production still shares, so a copy that drifted
    into something production never did is caught rather than trusted.
    """
    tokens_a = _match_tokens(key_a)
    tokens_b = _match_tokens(key_b)
    if not tokens_a or not tokens_b:
        return None
    smaller, larger = (
        (tokens_a, tokens_b) if len(tokens_a) <= len(tokens_b) else (tokens_b, tokens_a)
    )
    weakest = 1.0
    for small_token in smaller:
        best = max(
            SequenceMatcher(None, small_token, large_token).ratio()
            for large_token in larger
        )
        if best < SIMILARITY_THRESHOLD:
            return None
        weakest = min(weakest, best)
    return weakest


# --------------------------------------------------------------------------- #
# Corpus
# --------------------------------------------------------------------------- #


def harvest_titles(root: Path) -> list[str]:
    """Every distinct object title stored under `root`'s eval results."""
    found: set[str] = set()
    for path in sorted(root.rglob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            continue
        _walk(data, found)
    return sorted(t for t in found if _MIN_TITLE_CHARS <= len(t) <= _MAX_TITLE_CHARS)


def _walk(node: Any, found: set[str]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key in _TITLE_KEYS and isinstance(value, list):
                for item in value:
                    if isinstance(item, str) and item.strip():
                        found.add(item.strip())
                    elif isinstance(item, list) and item and isinstance(item[0], str):
                        found.add(item[0].strip())
            elif key == "title" and isinstance(value, str) and value.strip():
                found.add(value.strip())
            else:
                _walk(value, found)
    elif isinstance(node, list):
        for item in node:
            _walk(item, found)


# --------------------------------------------------------------------------- #
# Delta
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class DeltaPair:
    title_a: str
    title_b: str
    baseline: float | None
    treatment: float | None

    @property
    def key(self) -> str:
        """Adjudication key: the two NORMALIZED titles, sorted. Normalized so
        two runs that differ only in casing or accents do not need two
        rulings; sorted so the pair's order in the scan cannot change it."""
        return " || ".join(
            sorted((normalize_key(self.title_a), normalize_key(self.title_b)))
        )

    @property
    def newly_matched(self) -> bool:
        return self.baseline is None and self.treatment is not None


def find_delta(titles: list[str]) -> list[DeltaPair]:
    """Every DISTINCT comparison whose verdict differs between the two arms.

    Deduplicated by adjudication key, and that is a correctness point rather
    than tidiness: `near_match_score` takes NORMALIZED keys, so two title
    pairs differing only in case or accents are the identical computation.
    Counting both would report one finding twice -- inflating `exposed`,
    halving an apparent false-positive rate, and asking for the same ruling
    under two spellings.
    """
    keys = {title: normalize_key(title) for title in titles}
    delta: dict[str, DeltaPair] = {}
    for title_a, title_b in itertools.combinations(titles, 2):
        key_a, key_b = keys[title_a], keys[title_b]
        if key_a == key_b:
            # An exact normalized-key match is a HIGH-tier candidate handled
            # upstream; the near-match rule never decides it, so a change here
            # could not reach a user.
            continue
        base = baseline_near_match_score(key_a, key_b)
        treat = near_match_score(key_a, key_b)
        if (base is None) != (treat is None):
            pair = DeltaPair(title_a, title_b, base, treat)
            delta.setdefault(pair.key, pair)
    return sorted(delta.values(), key=lambda p: p.key)


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #


@dataclass
class Score:
    recovered: list[str]
    false_positives: list[str]
    fp_removed: list[str]
    regressions: list[str]
    unadjudicated: list[str]
    cross_type: list[str]

    @property
    def exposed(self) -> int:
        """Delta pairs that carry a ruling. The denominator without which a
        zero in `false_positives` means nothing."""
        return (
            len(self.recovered)
            + len(self.false_positives)
            + len(self.fp_removed)
            + len(self.regressions)
        )

    @property
    def verdict(self) -> str:
        if self.regressions:
            return (
                "REJECTED -- it drops a pair adjudicated as a genuine "
                "duplicate; the fix cannot cost recall it was meant to add."
            )
        if self.false_positives:
            return (
                "REJECTED -- it reports a pair adjudicated as distinct. The "
                "#630 bar is ONE false positive, and LOW tier is already the "
                "high-recall tier."
            )
        if not self.exposed:
            return (
                "UNFALSIFIABLE -- no delta pair carries a ruling, so the zero "
                "above is the corpus staying silent, not the change proving "
                "safe."
            )
        if not self.recovered:
            return (
                "NO EFFECT -- nothing adjudicated as a duplicate was "
                "recovered; the change moved only pairs that did not matter."
            )
        return "SHIPPABLE at this bar"


def score(delta: list[DeltaPair], labels: dict[str, str]) -> Score:
    out = Score([], [], [], [], [], [])
    for pair in delta:
        label = labels.get(pair.key)
        shown = f"{pair.title_a!r} || {pair.title_b!r}"
        if label is None:
            out.unadjudicated.append(pair.key)
        elif label == CROSS_TYPE:
            out.cross_type.append(shown)
        elif pair.newly_matched and label == DUPLICATE:
            out.recovered.append(shown)
        elif pair.newly_matched and label == DISTINCT:
            out.false_positives.append(shown)
        elif not pair.newly_matched and label == DISTINCT:
            out.fp_removed.append(shown)
        elif not pair.newly_matched and label == DUPLICATE:
            out.regressions.append(shown)
    return out


def render(delta: list[DeltaPair], result: Score, *, titles: int, pairs: int) -> str:
    lines = [
        "# Function words must not decide a containment (#755)",
        "",
        f"Population: **{titles}** distinct stored titles, **{pairs}** pairs "
        "compared. Deterministic, stdlib-only -- no model, no GPU.",
        "",
        "| metric | count |",
        "| --- | --- |",
        f"| delta pairs (verdict changed) | {len(delta)} |",
        f"| newly matched | {sum(1 for p in delta if p.newly_matched)} |",
        f"| newly lost | {sum(1 for p in delta if not p.newly_matched)} |",
        f"| **exposed** (adjudicated) | {result.exposed} |",
        f"| recovered duplicates | {len(result.recovered)} |",
        f"| **false positives** | {len(result.false_positives)} |",
        f"| false positives REMOVED | {len(result.fp_removed)} |",
        f"| regressions | {len(result.regressions)} |",
        f"| cross-type (production never compares) | {len(result.cross_type)} |",
        f"| unadjudicated | {len(result.unadjudicated)} |",
        "",
        f"**Verdict:** {result.verdict}",
        "",
    ]
    for label, rows in (
        ("False positives", result.false_positives),
        ("Regressions", result.regressions),
        ("Recovered duplicates", result.recovered),
        ("False positives removed", result.fp_removed),
        (
            "Cross-type -- excluded, production compares same-type only",
            result.cross_type,
        ),
    ):
        if rows:
            lines += [f"## {label}", ""]
            lines += [f"- {row}" for row in sorted(rows)]
            lines += [""]
    if result.unadjudicated:
        lines += [
            "## Unadjudicated -- counted in NEITHER column",
            "",
            "Add a `duplicate`/`distinct` ruling for each in "
            "`adjudication.json`, then re-run with `--rescore`.",
            "",
        ]
        lines += [f"- `{key}`" for key in sorted(result.unadjudicated)]
        lines += [""]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #


def load_labels() -> dict[str, str]:
    if not ADJUDICATION_PATH.is_file():
        return {}
    data = json.loads(ADJUDICATION_PATH.read_text(encoding="utf-8"))
    bad = {k: v for k, v in data.items() if v not in (DUPLICATE, DISTINCT, CROSS_TYPE)}
    if bad:
        raise SystemExit(
            f"adjudication.json carries {len(bad)} ruling(s) outside "
            f"{{{DUPLICATE!r}, {DISTINCT!r}, {CROSS_TYPE!r}}}: {sorted(bad)[:3]}. "
            "A typo would silently move a pair into the unadjudicated column."
        )
    return dict(data)


def write_delta(delta: list[DeltaPair], *, titles: int, pairs: int) -> Path:
    RESULTS_DIR.mkdir(exist_ok=True)
    out = RESULTS_DIR / "delta.json"
    out.write_text(
        json.dumps(
            {
                "titles": titles,
                "pairs": pairs,
                "delta": [
                    {
                        "title_a": p.title_a,
                        "title_b": p.title_b,
                        "baseline": p.baseline,
                        "treatment": p.treatment,
                        "key": p.key,
                    }
                    for p in delta
                ],
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return out


def read_delta(path: Path) -> tuple[list[DeltaPair], int, int]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return (
        [
            DeltaPair(r["title_a"], r["title_b"], r["baseline"], r["treatment"])
            for r in data["delta"]
        ],
        int(data["titles"]),
        int(data["pairs"]),
    )


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #


def _self_test() -> int:
    failures: list[str] = []

    def check(label: str, got: object, want: object) -> None:
        if got != want:
            failures.append(f"{label}: got {got!r}, want {want!r}")

    check("MIN_TOKEN_LENGTH is still 3", MIN_TOKEN_LENGTH, 3)
    check("SIMILARITY_THRESHOLD is still 0.75", SIMILARITY_THRESHOLD, 0.75)

    # The frozen baseline must still agree with production wherever #755 did
    # NOT change anything. A copy that drifted would invent a delta.
    for label, a, b in (
        ("stoicism containment", "stoicism", "stoic philosophy"),
        ("#555's retained short token", "ai agent", "ai agents"),
        ("#555's manufactured single token", "claude md", "claude code"),
        ("plainly unrelated", "trazabilidad", "presupuesto anual"),
    ):
        check(
            f"baseline matches production on {label}",
            baseline_near_match_score(a, b),
            near_match_score(a, b),
        )

    # And it must DISAGREE exactly where #755 acts, or the harness is measuring
    # nothing at all.
    blocked_a = "arquitectura de sistemas de extraccion de decisiones"
    blocked_b = "arquitectura del sistema de extraccion"
    check(
        "baseline blocks #755's pair",
        baseline_near_match_score(blocked_a, blocked_b),
        None,
    )
    check(
        "production recovers it",
        near_match_score(blocked_a, blocked_b) is not None,
        True,
    )

    # Scoring.
    pair_new = DeltaPair("Fuente canónica del bundle", "Uso del bundle", None, 0.9)
    pair_lost = DeltaPair(
        "Integración del knowledge source", "Object Model", 0.75, None
    )
    check("a newly matched pair says so", pair_new.newly_matched, True)
    check("a newly lost pair says so", pair_lost.newly_matched, False)
    check(
        "the key is order-independent",
        DeltaPair("B tit", "A tit", None, 1.0).key,
        DeltaPair("A tit", "B tit", None, 1.0).key,
    )

    cross = score([pair_new], {pair_new.key: CROSS_TYPE})
    check("a cross-type pair is excluded from scoring", cross.exposed, 0)
    check("and named in its own column", len(cross.cross_type), 1)
    check(
        "so it can neither pass nor reject the change",
        cross.verdict.startswith("UNFALSIFIABLE"),
        True,
    )

    empty = score([pair_new], {})
    check("an unruled pair is unadjudicated", len(empty.unadjudicated), 1)
    check("and exposes nothing", empty.exposed, 0)
    check(
        "so zero false positives is UNFALSIFIABLE, never a pass",
        empty.verdict.startswith("UNFALSIFIABLE"),
        True,
    )

    good = score([pair_new], {pair_new.key: DUPLICATE})
    check("a recovered duplicate is shippable", good.verdict, "SHIPPABLE at this bar")
    bad = score([pair_new], {pair_new.key: DISTINCT})
    check("one false positive rejects", bad.verdict.startswith("REJECTED"), True)
    removed = score([pair_lost], {pair_lost.key: DISTINCT})
    check("a removed false positive is not a regression", removed.regressions, [])
    check(
        "but it alone is NO EFFECT -- nothing was recovered",
        removed.verdict.startswith("NO EFFECT"),
        True,
    )
    regressed = score([pair_lost], {pair_lost.key: DUPLICATE})
    check("a lost duplicate rejects", regressed.verdict.startswith("REJECTED"), True)

    report = render(
        [pair_new], score([pair_new], {pair_new.key: DUPLICATE}), titles=2, pairs=1
    )
    check("the report renders its verdict", "SHIPPABLE" in report, True)

    if failures:
        print("SELF-TEST FAILED:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print(
        "SELF-TEST PASSED: the frozen baseline agrees with production "
        "everywhere #755 did not act and disagrees exactly where it did, the "
        "adjudication key is order-independent, one false positive or one "
        "regression rejects, and an unruled delta reads UNFALSIFIABLE rather "
        "than clean."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument(
        "--rescore",
        action="store_true",
        help="re-derive the verdict from the stored delta, no re-scan",
    )
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()

    if args.rescore:
        stored = RESULTS_DIR / "delta.json"
        if not stored.is_file():
            print(f"error: no stored delta at {stored}", file=sys.stderr)
            return 2
        delta, titles, pairs = read_delta(stored)
    else:
        harvested = harvest_titles(_EVALS_ROOT)
        titles = len(harvested)
        pairs = titles * (titles - 1) // 2
        print(f"harvested {titles} distinct titles ({pairs} pairs)", flush=True)
        delta = find_delta(harvested)
        write_delta(delta, titles=titles, pairs=pairs)

    result = score(delta, load_labels())
    report = render(delta, result, titles=titles, pairs=pairs)
    RESULTS_DIR.mkdir(exist_ok=True)
    (RESULTS_DIR / "report.md").write_text(report, encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
