"""Can a title that restates its own type reach the document it duplicates? (#804)

MANUAL eval tool (NOT pytest, NOT part of the shipped package). Needs NO
model and NO GPU: the rule under test is deterministic and stdlib-only, so
the whole sweep is a pair scan over real titles.

## The question

#804 reports two documents describing one project under two names --
`Project Helios` (typed `Project`) and `Helios Data Platform` (typed
`Concept`) -- that no candidate tier can group. HIGH needs an exact
normalized key and the keys differ; ACRONYM and LOW compare within a single
type and the types differ.

Widening the per-type block is one half and it is measured elsewhere. It is
not enough on its own: even compared, the pair fails LOW, because
containment requires an equivalent for `project` and the larger title has
none (`project`/`platform` scores 0.267, far under the 0.75 threshold).

So the second half asks: is `project` in `Project Helios` a *name*, or is it
the document restating the type it is already filed under? If the latter, it
could be excused from having to find an equivalent, and the pair would meet
at `helios`.

## The arms, and which way round they face

- **baseline** -- production's own `near_match_score`, imported. Unchanged
  by this probe by construction: the candidate rule was never shipped.
- **candidate** -- the excusal rule, frozen in this file, applied ONLY where
  the baseline already declined. Additive by construction, so every delta
  pair is newly matched and a regression is impossible rather than merely
  unobserved (`--self-test` pins that).

This is the inverse of `evals/duplicate_function_words/`, where the baseline
is the frozen copy because the treatment shipped. The orientation rule is
the same one in both: **the arm that does not live in production is the arm
that lives in the probe**, so the harness keeps measuring a real delta
instead of comparing production against itself.

## Synthetic typing, and why the result is an UPPER BOUND

The harvested corpus stores titles without their OKF types, and the rule
needs a type per document. Each title is therefore assigned the OKF type
word it contains, if any -- the assignment that excuses the most. A real
bundle can only ever excuse a SUBSET of that: a `Concept` titled `Project
Management` restates nothing and keeps both tokens.

So a clean sweep here would not have proven the rule safe. A dirty one does
prove it unsafe, but only for the false positives that survive real typing
-- which is why each one below is recorded with the type assignment that
reaches it, and the report names them rather than counting them.

## Adjudication is explicit, never inferred

Deriving `duplicate`/`distinct` from any similarity rule would beg the
question, so it lives in `adjudication.json`, hand-written, keyed by the
normalized pair. Anything unadjudicated is REPORTED as unadjudicated and
counted in neither column. `--rescore` re-derives every verdict from the
stored pairs after the file is edited, with no re-scan.

Usage:

    uv run python -u evals/type_restatement/run_type_restatement_probe.py --self-test
    uv run python -u evals/type_restatement/run_type_restatement_probe.py
    uv run python -u evals/type_restatement/run_type_restatement_probe.py --rescore
"""

from __future__ import annotations

import argparse
import importlib.util
import itertools
import json
import sys
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Final

from openkos.model.types import REGISTRY
from openkos.resolution.normalize import normalize_key
from openkos.resolution.similarity import (
    MATCH_FUNCTION_WORDS,
    _content_tokens,
    _match_tokens,
    near_match_score,
)

HERE = Path(__file__).resolve().parent
RESULTS_DIR = HERE / "results"
ADJUDICATION_PATH = HERE / "adjudication.json"
_EVALS_ROOT = HERE.parent

DUPLICATE: Final = "duplicate"
DISTINCT: Final = "distinct"

TYPE_WORDS: Final[frozenset[str]] = frozenset(
    normalize_key(object_type.name) for object_type in REGISTRY
)
"""Every OKF type name, normalized through the same function that produced
the keys being compared."""


# --------------------------------------------------------------------------- #
# The candidate rule
# --------------------------------------------------------------------------- #


def apposed_type_word(key: str, type_word: str) -> bool:
    """Is `type_word` a bare apposition in `key`, rather than its subject?

    `Project Helios` and `Onboarding Procedure` name a thing and file it.
    `Decision on Bilingual Documentation` and `Decisión sobre la fuente
    canónica` do not: they are ABOUT something, and the something is not the
    document. Excusing the type word there turns a decision into the thing
    decided, which is how the loose form of this rule reached 136 pairs
    instead of 29.

    The separator is the function word. The type word must sit at the very
    start or the very end of the title, and its one neighbour must be a
    content word -- no `on`, no `sobre`, no `de`.
    """
    words = key.split()
    if len(words) < 2:
        return False
    if words[0] == type_word and words[1] not in MATCH_FUNCTION_WORDS:
        return True
    return words[-1] == type_word and words[-2] not in MATCH_FUNCTION_WORDS


def candidate_near_match_score(
    key_a: str,
    key_b: str,
    *,
    type_a: str | None,
    type_b: str | None,
) -> float | None:
    """`near_match_score`, plus the type-restatement excusal under test.

    Strictly additive: the baseline answer is returned whenever it is a
    match, so the excusal can only ever ADD a pair. Where the baseline
    declined, the smaller title may drop the one token that restates its own
    type, subject to three guards:

    - the type must be a real OKF type, so a caller passing a slug or a
      title cannot dissolve the requirement;
    - the type word must be APPOSED (see `apposed_type_word`);
    - what remains must match EXACTLY. A token excused from the requirement
      is a discount, and the tokens left behind pay full price for it --
      without this, `Re-ranking Procedure` reached `Corporate Branding in
      Product Design` on `ranking`/`branding` at 0.8.

    A title with nothing left after the excusal keeps its token: an empty
    requirement is vacuously satisfied and would contain into every title in
    the bundle.
    """
    baseline = near_match_score(key_a, key_b)
    if baseline is not None:
        return baseline
    tokens_a = _content_tokens(_match_tokens(key_a))
    tokens_b = _content_tokens(_match_tokens(key_b))
    if not tokens_a or not tokens_b:
        return None
    (smaller, smaller_type, smaller_key), (larger, _, _) = (
        ((tokens_a, type_a, key_a), (tokens_b, type_b, key_b))
        if len(tokens_a) <= len(tokens_b)
        else ((tokens_b, type_b, key_b), (tokens_a, type_a, key_a))
    )
    if smaller_type is None or smaller_type not in TYPE_WORDS:
        return None
    if not apposed_type_word(smaller_key, smaller_type):
        return None
    required = tuple(token for token in smaller if token != smaller_type)
    if not required or len(required) == len(smaller):
        return None
    for token in required:
        if max(SequenceMatcher(None, token, other).ratio() for other in larger) < 1.0:
            return None
    return 1.0


def synthetic_type(key: str) -> str | None:
    """The OKF type word `key` contains, if any -- the MAXIMUM-exposure type
    assignment (see the module docstring). `None` when the title names no
    type, which is the majority of the corpus."""
    for token in key.split():
        if token in TYPE_WORDS:
            return token
    return None


# --------------------------------------------------------------------------- #
# Corpus
# --------------------------------------------------------------------------- #


def harvest_titles(root: Path) -> list[str]:
    """Every distinct object title stored under `root`'s eval results.

    Borrowed from `evals/duplicate_function_words/` rather than copied: the
    two probes measure two rules over ONE corpus, and a second harvester
    would let the corpora drift apart while both reports still said "every
    stored title".
    """
    path = _EVALS_ROOT / "duplicate_function_words" / "run_function_word_probe.py"
    spec = importlib.util.spec_from_file_location("_fw_probe", path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise SystemExit(f"cannot load the shared corpus harvester from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["_fw_probe"] = module
    spec.loader.exec_module(module)
    harvest: Any = module.harvest_titles
    return list(harvest(root))


# --------------------------------------------------------------------------- #
# Delta
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class DeltaPair:
    title_a: str
    title_b: str
    excused: str

    @property
    def key(self) -> str:
        """Adjudication key: the two NORMALIZED titles, sorted. Normalized
        so two runs differing only in casing or accents do not need two
        rulings; sorted so the pair's order in the scan cannot change it."""
        return " || ".join(
            sorted((normalize_key(self.title_a), normalize_key(self.title_b)))
        )


def find_delta(titles: list[str]) -> list[DeltaPair]:
    """Every DISTINCT comparison the candidate rule adds to the baseline.

    Deduplicated by adjudication key: `near_match_score` takes NORMALIZED
    keys, so two title pairs differing only in case or accents are the
    identical computation, and counting both would report one finding twice.
    """
    keys = {title: normalize_key(title) for title in titles}
    types = {title: synthetic_type(keys[title]) for title in titles}
    delta: dict[str, DeltaPair] = {}
    for title_a, title_b in itertools.combinations(titles, 2):
        key_a, key_b = keys[title_a], keys[title_b]
        if key_a == key_b:
            # An exact normalized-key match is a HIGH-tier candidate handled
            # upstream; the near-match rule never decides it, so a change
            # here could not reach a user.
            continue
        type_a, type_b = types[title_a], types[title_b]
        if type_a is None and type_b is None:
            continue
        if near_match_score(key_a, key_b) is not None:
            continue
        if (
            candidate_near_match_score(key_a, key_b, type_a=type_a, type_b=type_b)
            is None
        ):
            continue
        smaller_is_a = len(_content_tokens(_match_tokens(key_a))) <= len(
            _content_tokens(_match_tokens(key_b))
        )
        excused = (type_a if smaller_is_a else type_b) or ""
        pair = DeltaPair(title_a, title_b, excused)
        delta.setdefault(pair.key, pair)
    return sorted(delta.values(), key=lambda pair: pair.key)


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #


@dataclass
class Score:
    recovered: list[str]
    false_positives: list[str]
    unadjudicated: list[str]

    @property
    def exposed(self) -> int:
        """Delta pairs that carry a ruling. The denominator without which a
        zero in `false_positives` means nothing."""
        return len(self.recovered) + len(self.false_positives)

    @property
    def verdict(self) -> str:
        if self.false_positives:
            return (
                "REFUTED -- the rule reports pairs adjudicated as distinct. "
                "The #630 bar is ONE false positive, and these survive real "
                "typing: an Event about designing a remote is not the "
                "Project, and a Decision about re-ranking is not the "
                "Procedure. The surviving token is what decides, and "
                "'helios' is structurally identical to 'ranking' -- no "
                "pairwise lexical rule separates them."
            )
        if not self.exposed:
            return (
                "UNFALSIFIABLE -- no delta pair carries a ruling, so the "
                "zero above is the corpus staying silent, not the rule "
                "proving safe."
            )
        if not self.recovered:
            return (
                "NO EFFECT -- nothing adjudicated as a duplicate was "
                "recovered; the rule moved only pairs that did not matter."
            )
        return "SHIPPABLE at this bar"


def score(delta: list[DeltaPair], labels: dict[str, str]) -> Score:
    out = Score([], [], [])
    for pair in delta:
        label = labels.get(pair.key)
        shown = f"{pair.title_a!r} || {pair.title_b!r} (excused {pair.excused!r})"
        if label is None:
            out.unadjudicated.append(pair.key)
        elif label == DUPLICATE:
            out.recovered.append(shown)
        else:
            out.false_positives.append(shown)
    return out


def render(delta: list[DeltaPair], result: Score, *, titles: int, pairs: int) -> str:
    lines = [
        "# A title that restates its own type (#804)",
        "",
        f"Population: **{titles}** distinct stored titles, **{pairs}** pairs "
        "compared. Deterministic, stdlib-only -- no model, no GPU.",
        "",
        "Types are assigned SYNTHETICALLY, at maximum exposure: every title "
        "is treated as being of the type it names. A real bundle excuses a "
        "subset of this, so the counts below are an UPPER BOUND -- which is "
        "why the false positives are named, not merely counted.",
        "",
        "| metric | count |",
        "| --- | --- |",
        f"| newly matched (the rule is additive) | {len(delta)} |",
        f"| **exposed** (adjudicated) | {result.exposed} |",
        f"| recovered duplicates | {len(result.recovered)} |",
        f"| **false positives** | {len(result.false_positives)} |",
        f"| unadjudicated | {len(result.unadjudicated)} |",
        "",
        f"**Verdict:** {result.verdict}",
        "",
    ]
    for label, rows in (
        ("False positives", result.false_positives),
        ("Recovered duplicates", result.recovered),
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
    bad = {k: v for k, v in data.items() if v not in (DUPLICATE, DISTINCT)}
    if bad:
        raise SystemExit(
            f"adjudication.json carries {len(bad)} ruling(s) outside "
            f"{{{DUPLICATE!r}, {DISTINCT!r}}}: {sorted(bad)[:3]}. A typo "
            "would silently move a pair into the unadjudicated column."
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
                        "title_a": pair.title_a,
                        "title_b": pair.title_b,
                        "excused": pair.excused,
                        "key": pair.key,
                    }
                    for pair in delta
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
        [DeltaPair(r["title_a"], r["title_b"], r["excused"]) for r in data["delta"]],
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

    helios = normalize_key("Project Helios")
    platform = normalize_key("Helios Data Platform")

    # The motivating pair: unreachable today, reachable under the rule.
    check("baseline declines #804's pair", near_match_score(helios, platform), None)
    check(
        "candidate reaches #804's pair",
        candidate_near_match_score(helios, platform, type_a="project", type_b="concept")
        is not None,
        True,
    )

    # Guard 1 -- the type must be a real OKF type.
    check(
        "a non-OKF type excuses nothing",
        candidate_near_match_score(
            normalize_key("Alpha Helios"), platform, type_a="alpha", type_b="concept"
        ),
        None,
    )

    # Guard 2 -- the type word must be apposed, not the subject of an 'about'.
    check(
        "'Decision on X' keeps its type word",
        candidate_near_match_score(
            normalize_key("Decision on Bilingual Documentation"),
            normalize_key("Bilingual Documentation Rules"),
            type_a="decision",
            type_b="concept",
        ),
        None,
    )
    check("apposed accepts a bare prefix", apposed_type_word(helios, "project"), True)
    check(
        "apposed accepts a bare suffix",
        apposed_type_word(normalize_key("Onboarding Procedure"), "procedure"),
        True,
    )
    check(
        "apposed rejects a preposition",
        apposed_type_word(normalize_key("Decisión sobre el bundle"), "decision"),
        False,
    )

    # Guard 3 -- what remains pays full price.
    check(
        "an excused title matches only exactly",
        candidate_near_match_score(
            normalize_key("Re-ranking Procedure"),
            normalize_key("Corporate Branding in Product Design"),
            type_a="procedure",
            type_b="concept",
        ),
        None,
    )

    # Guard 4 -- a title that is only its type keeps its token.
    check(
        "a title equal to its type is not emptied",
        candidate_near_match_score(
            normalize_key("Project"), platform, type_a="project", type_b="concept"
        ),
        None,
    )

    # Additivity -- the property that makes every delta pair newly matched.
    stoic = (normalize_key("Stoicism"), normalize_key("Stoic Philosophy"))
    check(
        "a baseline match is returned unchanged",
        candidate_near_match_score(*stoic, type_a="concept", type_b="concept"),
        near_match_score(*stoic),
    )

    # Synthetic typing is the maximum-exposure assignment.
    check("synthetic type reads the title", synthetic_type(helios), "project")
    check("synthetic type is None when absent", synthetic_type(platform), None)

    for failure in failures:
        print(f"FAIL {failure}")
    print("self-test OK" if not failures else f"{len(failures)} self-test failure(s)")
    return 1 if failures else 0


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument(
        "--rescore",
        action="store_true",
        help="re-derive verdicts from the stored delta, with no re-scan",
    )
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()

    if args.rescore:
        stored = RESULTS_DIR / "delta.json"
        if not stored.is_file():
            raise SystemExit(f"no stored delta at {stored}; run without --rescore")
        delta, titles, pairs = read_delta(stored)
    else:
        harvested = harvest_titles(_EVALS_ROOT)
        keys = {title: normalize_key(title) for title in harvested}
        pairs = sum(
            1 for a, b in itertools.combinations(harvested, 2) if keys[a] != keys[b]
        )
        titles = len(harvested)
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
