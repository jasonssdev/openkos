"""Are the fabricated acronym expansions in issue #423 specific to Spanish?

Reads the stored `runs-*.json` under `evals/` and makes ZERO model calls:
every emitted title of every scored run is already on disk, so the question
is answerable from data the cap-eval runs already paid for.

The test is self-contradiction, which needs no ground truth. An acronym the
extractor expands parenthetically should expand the SAME way every time; two
distinct expansions of one acronym within a fixture mean at least one
emission is fabricated. That makes the measurement independent of whether a
human has adjudicated the fixture -- the trap recorded in
`spanish-recall-gap-was-adjudication-debt`, where recall was under-reported
until the queue was worked.

Run from the repository root:

    python evals/extraction_cap/measure_acronym_fabrication.py
    python evals/extraction_cap/measure_acronym_fabrication.py --self-test
"""

from __future__ import annotations

import json
import pathlib
import re
import sys
from collections import defaultdict
from collections.abc import Iterable

ACRONYM_FIRST = re.compile(r"\b([A-Z][A-Z0-9]{1,5})\s*\(([^)]+)\)")
"""`MCP (Model Context Protocol)` -- acronym first, expansion parenthesized."""

EXPANSION_FIRST = re.compile(
    r"\b([A-Z][a-zA-Z]+(?:\s+[A-Za-z]+){1,5})\s*\(([A-Z][A-Z0-9]{1,5})\)"
)
"""`Model Context Protocol (MCP)` -- expansion first, acronym parenthesized.
Both orders are counted against the same acronym, since the extractor picks
either shape freely and the contradiction is in the expansion, not the form."""

SPANISH_FIXTURES = frozenset({"small-04-pre-build-skills"})
"""The corpus' one non-English fixture. It is the same lesson as
`large-03-skills-vs-tools`, translated and condensed to ~45% -- which is what
makes the EN/ES comparison meaningful at all."""


def pairs_in(title: str) -> list[tuple[str, str]]:
    """Every `(acronym, expansion)` this title claims, in either written
    order. Identical extraction to `measure_expansion_grounding.py`'s
    `pairs_in` -- the two probes must see the same emissions or their
    numbers cannot be compared."""
    out = [(m.group(1), m.group(2)) for m in ACRONYM_FIRST.finditer(title)]
    out += [(m.group(2), m.group(1)) for m in EXPANSION_FIRST.finditer(title)]
    return out


def tally_from_outcomes(
    outcomes: Iterable[dict[str, object]],
) -> dict[str, dict[str, dict[str, int]]]:
    """`acronym -> fixture -> expansion -> count`, over `ok` outcomes only.

    Pure function, extracted from `main` so a self-test can prove the
    self-contradiction tally -- the parsing, the status filter and the
    aggregation -- without reading a single stored `runs-*.json` file or
    making a model call.
    """
    seen: dict[str, dict[str, dict[str, int]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(int))
    )
    for outcome in outcomes:
        if outcome.get("status") != "ok":
            continue
        fixture = str(outcome["fixture"])
        titles = outcome.get("titles", [])
        if not isinstance(titles, list):
            continue
        for title in titles:
            for acronym, expansion in pairs_in(str(title)):
                seen[acronym][fixture][expansion.strip()] += 1
    return seen


def _self_test() -> int:
    """Prove the self-contradiction tally with synthetic outcomes -- no
    stored runs, no model."""
    failures: list[str] = []

    def check(label: str, actual: object, expected: object) -> None:
        if actual != expected:
            failures.append(f"{label}: expected {expected!r}, got {actual!r}")

    # Extraction: both written orders resolve to the same (acronym, expansion).
    check(
        "acronym-first",
        pairs_in("MCP (Machine Control Protocol)"),
        [("MCP", "Machine Control Protocol")],
    )
    check(
        "expansion-first",
        pairs_in("Model Context Protocol (MCP)"),
        [("MCP", "Model Context Protocol")],
    )
    check("no pair", pairs_in("Pre-built Skills"), [])

    outcomes: list[dict[str, object]] = [
        {"status": "ok", "fixture": "f1", "titles": ["MCP (Model Context Protocol)"]},
        {"status": "ok", "fixture": "f1", "titles": ["MCP (Machine Control Protocol)"]},
        {"status": "ok", "fixture": "f2", "titles": ["MCP (Model Context Protocol)"]},
        # An `error` run must contribute NOTHING -- counting a failed
        # extraction's titles would tally emissions that were never
        # actually retained.
        {"status": "error", "fixture": "f1", "titles": ["MCP (Ghost Expansion)"]},
    ]
    tally = tally_from_outcomes(outcomes)

    # THE case this harness exists for: two DISTINCT expansions of the same
    # acronym within one fixture prove at least one emission is fabricated.
    check(
        "self-contradictory fixture has two distinct expansions",
        dict(tally["MCP"]["f1"]),
        {"Model Context Protocol": 1, "Machine Control Protocol": 1},
    )
    check("self-contradiction detected", len(tally["MCP"]["f1"]) > 1, True)

    # A CONSISTENTLY fabricated expansion is this harness's documented blind
    # spot (see `measure_expansion_grounding.py`'s docstring): one expansion
    # repeated produces no contradiction, so it tallies as a single,
    # unflagged variant.
    check(
        "consistent fixture has one variant",
        dict(tally["MCP"]["f2"]),
        {"Model Context Protocol": 1},
    )
    check("consistent fixture not flagged", len(tally["MCP"]["f2"]) > 1, False)

    # The error-status outcome must not have leaked a third expansion in.
    check("error status excluded", len(tally["MCP"]["f1"]), 2)

    for line in failures:
        print(f"FAIL {line}")
    print(f"\nself-test: {'FAILED' if failures else 'passed'}")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if "--self-test" in args:
        return _self_test()

    root = pathlib.Path(__file__).resolve().parents[2]
    run_files = sorted((root / "evals").rglob("runs-*.json"))

    # acronym -> fixture -> expansion -> count
    seen: dict[str, dict[str, dict[str, int]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(int))
    )
    runs_by_fixture: dict[str, int] = defaultdict(int)

    for path in run_files:
        payload = json.loads(path.read_text(encoding="utf-8"))
        outcomes = payload.get("outcomes", [])
        for outcome in outcomes:
            if outcome.get("status") != "ok":
                continue
            runs_by_fixture[str(outcome["fixture"])] += 1
        for acronym, by_fixture in tally_from_outcomes(outcomes).items():
            for fixture, expansions in by_fixture.items():
                for expansion, count in expansions.items():
                    seen[acronym][fixture][expansion] += count

    print(f"run files read: {len(run_files)} (no model calls)")
    print("\nsuccessful runs per fixture:")
    for fixture, count in sorted(runs_by_fixture.items()):
        language = "ES" if fixture in SPANISH_FIXTURES else "EN"
        print(f"  [{language}] {fixture}: {count}")

    print("\nacronyms emitted with a parenthetical expansion:")
    for acronym in sorted(seen):
        print(f"\n  {acronym}")
        for fixture in sorted(seen[acronym]):
            language = "ES" if fixture in SPANISH_FIXTURES else "EN"
            variants = seen[acronym][fixture]
            emissions = sum(variants.values())
            flag = "  <-- SELF-CONTRADICTORY" if len(variants) > 1 else ""
            print(
                f"    [{language}] {fixture}: {emissions} emission(s), "
                f"{len(variants)} distinct expansion(s){flag}"
            )
            for expansion, count in sorted(
                variants.items(), key=lambda item: (-item[1], item[0])
            ):
                print(f"         x{count}  {expansion}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
