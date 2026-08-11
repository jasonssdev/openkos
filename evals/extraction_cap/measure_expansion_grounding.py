"""Is the acronym expansion actually IN the source? (#423 follow-up)

Reads the stored `runs-*.json` under `evals/` and makes ZERO model calls:
every emitted title is already on disk. Same move as
`measure_acronym_fabrication.py` and `extraction_collapse/measure_single_object_rate.py`.

## The hole this closes

`measure_acronym_fabrication.py` detects a fabricated expansion by
SELF-CONTRADICTION: two distinct expansions of one acronym within a fixture
prove at least one emission is invented. That test needs no ground truth,
which is its strength -- and it has one blind spot, stated in its own
premise. **A CONSISTENTLY fabricated expansion produces no contradiction.**
If the extractor answers `MCP (Machine Control Protocol)` on every run of a
fixture, there is nothing to disagree with and the rate reads zero.

This probe asks a different question with a different oracle: is the
expansion PRESENT IN THE SOURCE TEXT? A string the document never contains
was not read off the document, however consistently it is emitted. The two
probes are complements, not substitutes -- run both.

## The matching rule is deliberately dumb, and that is the point

An expansion counts as grounded ONLY when it appears in the source after
strip, casefold and whitespace collapse. No stemming, no token overlap, no
edit distance, anywhere in this file.

`run_cap_eval.py` argues the general case at length: a matcher that can be
generous in the direction of the hypothesis measures nothing. Here the bias
runs the other way and must be declared -- a legitimate expansion the source
writes with an intervening line break, a hyphen, or an inflected word scores
as FABRICATED. So the headline number is an UPPER BOUND on fabrication, and
the per-expansion listing is what a human reads to discount it. It is not a
verdict on any single emission.

## What this deliberately does NOT do

It does not check whether a TITLE is grounded. Extraction is supposed to
synthesize a subject's name, not copy a span -- `run_type_coverage.py` puts
it as "a mention is a span of words, an object is a subject worth a
document" -- so an ungrounded title is the normal case, not a defect. Only
the parenthetical expansion of an acronym makes a checkable factual claim
about what the source says, which is why the scope stops there.

Run from the repository root:

    uv run python evals/extraction_cap/measure_expansion_grounding.py
    uv run python evals/extraction_cap/measure_expansion_grounding.py --self-test
"""

from __future__ import annotations

import json
import pathlib
import re
import sys
from collections import defaultdict

ACRONYM_FIRST = re.compile(r"\b([A-Z][A-Z0-9]{1,5})\s*\(([^)]+)\)")
"""`MCP (Model Context Protocol)` -- acronym first, expansion parenthesized.
Identical to `measure_acronym_fabrication.py`'s pattern on purpose: the two
probes must see the same emissions or their numbers cannot be compared."""

EXPANSION_FIRST = re.compile(
    r"\b([A-Z][a-zA-Z]+(?:\s+[A-Za-z]+){1,5})\s*\(([A-Z][A-Z0-9]{1,5})\)"
)
"""`Model Context Protocol (MCP)` -- expansion first, acronym parenthesized."""

_SOURCE_DIRS = (
    ("examples/extraction-corpus/sources", ".md"),
    ("evals/decision_extraction/sources", ".txt"),
)
"""Where a fixture's raw text lives, tried in order by stem. The corpus
sources are git-ignored third-party material, so a fresh clone has none of
them; an unresolved fixture is REPORTED AND SKIPPED, never crashed on and
never silently counted as grounded."""


def normalize(text: str) -> str:
    """Casefold and collapse whitespace. The whole matching rule."""
    return " ".join(text.split()).casefold()


def pairs_in(title: str) -> list[tuple[str, str]]:
    """Every `(acronym, expansion)` this title claims, in either written order.

    Both orders are read against the same acronym: the extractor picks either
    shape freely and the factual claim is in the expansion, not the form.
    """
    out = [(m.group(1), m.group(2)) for m in ACRONYM_FIRST.finditer(title)]
    out += [(m.group(2), m.group(1)) for m in EXPANSION_FIRST.finditer(title)]
    return out


def is_grounded(expansion: str, source: str) -> bool:
    """Does the source contain this expansion, under the dumb rule above?"""
    return normalize(expansion) in normalize(source)


def resolve_source(fixture: str, root: pathlib.Path) -> pathlib.Path | None:
    """The raw text for a fixture, or `None` when no source dir holds it."""
    for directory, suffix in _SOURCE_DIRS:
        candidate = root / directory / f"{fixture}{suffix}"
        if candidate.is_file():
            return candidate
    return None


def collect(
    run_files: list[pathlib.Path], root: pathlib.Path
) -> tuple[dict[tuple[str, str, str], int], set[str], set[str]]:
    """Walk every stored run and tally each `(fixture, acronym, expansion)`.

    Returns the tally, the fixtures whose source could not be resolved, and
    the fixtures that WERE resolved -- the caller needs all three to state
    coverage rather than imply it.
    """
    tally: dict[tuple[str, str, str], int] = defaultdict(int)
    unresolved: set[str] = set()
    resolved: set[str] = set()
    cache: dict[str, str | None] = {}

    for path in run_files:
        payload = json.loads(path.read_text(encoding="utf-8"))
        for outcome in payload.get("outcomes", []):
            fixture = outcome.get("fixture")
            if not fixture:
                continue
            if fixture not in cache:
                found = resolve_source(fixture, root)
                cache[fixture] = (
                    found.read_text(encoding="utf-8") if found is not None else None
                )
            source = cache[fixture]
            if source is None:
                unresolved.add(fixture)
                continue
            resolved.add(fixture)
            for title in outcome.get("titles", []):
                for acronym, expansion in pairs_in(title):
                    if not is_grounded(expansion, source):
                        tally[(fixture, acronym, expansion)] += 1
    return tally, unresolved, resolved


def _report(
    tally: dict[tuple[str, str, str], int],
    unresolved: set[str],
    resolved: set[str],
    run_files: int,
) -> None:
    print(f"run files read: {run_files} (no model calls)")
    print(f"fixtures with a source on disk: {len(resolved)}")
    if unresolved:
        print(f"fixtures SKIPPED, no source on disk: {', '.join(sorted(unresolved))}")
    print()
    print("UNGROUNDED ACRONYM EXPANSIONS -- an upper bound, read the list")
    print("=" * 72)
    if not tally:
        print("  (none)")
        return
    by_fixture: dict[str, list[tuple[str, str, int]]] = defaultdict(list)
    for (fixture, acronym, expansion), count in tally.items():
        by_fixture[fixture].append((acronym, expansion, count))
    for fixture in sorted(by_fixture):
        rows = sorted(by_fixture[fixture], key=lambda r: (-r[2], r[0], r[1]))
        total = sum(r[2] for r in rows)
        print(f"\n  {fixture}  --  {total} emission(s), {len(rows)} distinct")
        for acronym, expansion, count in rows:
            print(f"    {count:3}x  {acronym} = {expansion!r}")


def _self_test() -> int:
    """Prove the logic with no runs, no sources and no model."""
    failures: list[str] = []

    def check(label: str, actual: object, expected: object) -> None:
        if actual != expected:
            failures.append(f"{label}: expected {expected!r}, got {actual!r}")

    source = "The Model Context Protocol (MCP) lets a client mount servers.\n"

    # The two written orders both yield the same (acronym, expansion) claim.
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

    # A title making no expansion claim contributes NOTHING. Counting it
    # either way would move a rate it has no evidence about.
    check("no pair", pairs_in("Pre-built Skills"), [])
    check("bare acronym", pairs_in("MCP"), [])

    # The oracle.
    check("present", is_grounded("Model Context Protocol", source), True)
    check("absent", is_grounded("Machine Control Protocol", source), False)

    # Casing and whitespace are not fabrication. A line break inside the
    # expansion is the false-positive shape the docstring warns about, and
    # collapsing whitespace is exactly what keeps it from firing.
    check("casing", is_grounded("model context protocol", source), True)
    check("wrapped", is_grounded("Model  Context\nProtocol", source), True)

    # THE CASE THIS PROBE EXISTS FOR. `measure_acronym_fabrication.py` scores
    # a run set with ONE consistent expansion at zero, because there is no
    # second expansion to contradict it. Grounding must still flag it.
    consistent = [("MCP", "Machine Control Protocol")] * 5
    caught = [p for p in consistent if not is_grounded(p[1], source)]
    check("consistent fabrication is caught", len(caught), 5)
    distinct = {e for _, e in consistent}
    check("...and self-contradiction sees nothing", len(distinct) > 1, False)

    # An unresolvable fixture resolves to None rather than raising.
    check("missing source", resolve_source("no-such-fixture", pathlib.Path("/")), None)

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
    tally, unresolved, resolved = collect(run_files, root)
    _report(tally, unresolved, resolved, len(run_files))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
