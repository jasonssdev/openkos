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
"""

from __future__ import annotations

import json
import pathlib
import re
from collections import defaultdict

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


def main() -> None:
    root = pathlib.Path(__file__).resolve().parents[2]
    run_files = sorted((root / "evals").rglob("runs-*.json"))

    # acronym -> fixture -> expansion -> count
    seen: dict[str, dict[str, dict[str, int]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(int))
    )
    runs_by_fixture: dict[str, int] = defaultdict(int)

    for path in run_files:
        payload = json.loads(path.read_text(encoding="utf-8"))
        for outcome in payload.get("outcomes", []):
            if outcome.get("status") != "ok":
                continue
            fixture = str(outcome["fixture"])
            runs_by_fixture[fixture] += 1
            for title in outcome.get("titles", []):
                for acronym, expansion in ACRONYM_FIRST.findall(title):
                    seen[acronym][fixture][expansion.strip()] += 1
                for expansion, acronym in EXPANSION_FIRST.findall(title):
                    seen[acronym][fixture][expansion.strip()] += 1

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


if __name__ == "__main__":
    main()
