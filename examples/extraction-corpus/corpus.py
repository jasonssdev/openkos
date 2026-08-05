"""Maintain the extraction measurement corpus: survey candidates, then add.

Two modes.

`survey <dir>` walks a directory of markdown and ranks every file by how
likely it is to EXHIBIT the extraction defects we need to measure (#404).
This is the mode that matters when choosing from a large pile of course
notes: the goal is not "good writing", it is material that stresses the
extractor.

`add <path>` copies one chosen file into `sources/` under a normalized name
and scaffolds its ground-truth stub in `ground-truth/`, which a HUMAN then
fills in.

Run with the repo's interpreter, e.g.:

    uv run python examples/extraction-corpus/corpus.py survey ~/courses
    uv run python examples/extraction-corpus/corpus.py add ~/courses/skills.md

Not a test, not wired into CI, not part of the shipped package -- the same
posture `evals/model_spike/` takes.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SOURCES = _HERE / "sources"
_GROUND_TRUTH = _HERE / "ground-truth"

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$", re.MULTILINE)
_FENCE_RE = re.compile(r"^(```|~~~)", re.MULTILINE)

# Size bands, in bytes. Drawn from #404's own measurements rather than
# invented: the sub-kilobyte demo fixtures never approached the cap, 6-17 KB
# real documents produced 7-61 objects, and the defect scaled with size.
# A corpus wants one fixture per band, not three of the same size.
_BANDS: tuple[tuple[str, int, int], ...] = (
    ("tiny", 0, 2_000),
    ("small", 2_000, 9_000),
    ("medium", 9_000, 15_000),
    ("large", 15_000, 40_000),
    ("huge", 40_000, 1 << 30),
)


def _band(size: int) -> str:
    for name, low, high in _BANDS:
        if low <= size < high:
            return name
    return "huge"


@dataclass(frozen=True)
class Candidate:
    """One surveyed markdown file and the signals that predict decay risk."""

    path: Path
    size: int
    headings: int
    facet_clusters: tuple[tuple[str, int], ...]
    """Heading groups sharing a leading word, e.g. ("Skill", 9).

    This is the strongest automatable predictor of the #404 tail. The measured
    decay was literally `Skill Modifiability`, `Skill Reusability`,
    `Skill Customization`, `Skill Collaboration` -- one subject shredded into
    attributes. A document whose own headings already cluster that way hands
    the model that shape directly.
    """

    @property
    def band(self) -> str:
        return _band(self.size)

    @property
    def facet_risk(self) -> int:
        """Headings sitting inside a shared-prefix cluster of 3 or more."""
        return sum(count for _word, count in self.facet_clusters)

    @property
    def score(self) -> int:
        """Crude ranking: stress the extractor, do not judge the prose.

        Deliberately NOT a quality measure. A well-written, single-topic essay
        scores low here and that is correct -- it cannot exhibit the defect.
        """
        size_points = min(self.size // 1_000, 30)
        return size_points + self.headings + 3 * self.facet_risk


def _strip_fenced_code(text: str) -> str:
    """Blank every line inside a fenced block, keeping line count stable.

    A tutorial is mostly code, and `# comment` lines inside a shell block
    otherwise register as headings -- which would rank code-heavy files as
    multi-subject when they are not.
    """
    out: list[str] = []
    fence: str | None = None
    for line in text.split("\n"):
        stripped = line.lstrip()
        marker = stripped[:3]
        if fence is None:
            if _FENCE_RE.match(stripped):
                fence = marker
                out.append("")
            else:
                out.append(line)
        else:
            if _FENCE_RE.match(stripped) and marker == fence:
                fence = None
            out.append("")
    return "\n".join(out)


def _facet_clusters(titles: list[str]) -> tuple[tuple[str, int], ...]:
    """Group headings by their leading word; keep groups of 3 or more."""
    leading = Counter(
        title.split()[0].strip(":,.-").casefold() for title in titles if title.split()
    )
    clusters = [(word, n) for word, n in leading.items() if n >= 3]
    return tuple(sorted(clusters, key=lambda pair: -pair[1]))


def _survey_one(path: Path) -> Candidate | None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    body = _strip_fenced_code(text)
    titles = [match.group(2) for match in _HEADING_RE.finditer(body)]
    return Candidate(
        path=path,
        size=len(text.encode("utf-8")),
        headings=len(titles),
        facet_clusters=_facet_clusters(titles),
    )


def _survey(root: Path) -> int:
    if not root.is_dir():
        print(f"not a directory: {root}", file=sys.stderr)
        return 1
    found = [c for p in sorted(root.rglob("*.md")) if (c := _survey_one(p)) is not None]
    if not found:
        print(f"no readable markdown under {root}", file=sys.stderr)
        return 1

    found.sort(key=lambda c: -c.score)
    print(f"{len(found)} markdown file(s) under {root}\n")
    print(f"{'SIZE':>8}  {'BAND':<7} {'HEADS':>5} {'FACETS':>6}  FILE")
    print("-" * 78)
    for c in found:
        facets = c.facet_clusters[0] if c.facet_clusters else ("", 0)
        note = f"{facets[0]}x{facets[1]}" if facets[1] else "-"
        rel = c.path.name
        print(f"{c.size:>7}B  {c.band:<7} {c.headings:>5} {note:>6}  {rel[:44]}")

    print()
    print("Pick ONE per band -- a spread of sizes gives a curve, three files of")
    print("the same size give an anecdote (#404 measured 6, 13 and 17 KB).")
    print("High FACETS means the document's own headings already cluster around")
    print("one subject, which is the exact shape that produced the decayed tail.")
    covered = {c.band for c in found}
    for band in ("small", "medium", "large"):
        if band not in covered:
            print(f"NOTE: nothing in the '{band}' band here.")
    return 0


def _normalized_name(path: Path, band: str) -> str:
    stem = re.sub(r"[^a-z0-9]+", "-", path.stem.casefold()).strip("-") or "source"
    return f"{band}-{stem}{path.suffix or '.md'}"


def _add(path: Path, *, name: str | None) -> int:
    candidate = _survey_one(path)
    if candidate is None:
        print(f"cannot read as UTF-8 text: {path}", file=sys.stderr)
        return 1

    _SOURCES.mkdir(parents=True, exist_ok=True)
    _GROUND_TRUTH.mkdir(parents=True, exist_ok=True)

    target_name = name or _normalized_name(path, candidate.band)
    target = _SOURCES / target_name
    if target.exists():
        print(f"refusing to overwrite {target.relative_to(_HERE)}", file=sys.stderr)
        return 1

    shutil.copyfile(path, target)

    stub = _GROUND_TRUTH / f"{target.stem}.md"
    if not stub.exists():
        stub.write_text(_STUB.format(source=target_name, size=candidate.size), "utf-8")

    print(f"added   sources/{target_name}  ({candidate.size} B, {candidate.band})")
    print(f"stub    ground-truth/{stub.name}  <- fill this in by hand")
    print()
    print("The ground truth is the measurement. It cannot be generated by a")
    print("model without measuring one model against another's opinion.")
    print()
    print("REMINDER: this repository is public and Apache-2.0. Anything copied")
    print("into sources/ is published under that license on the next push.")
    return 0


_STUB = """# Ground truth — `{source}`

Source size: {size} B

Fill this in BY HAND, before running any extraction against this file.
Writing it afterwards means recording what the model said, not what is true,
and the measurement stops meaning anything.

## Genuinely distinct subjects

List every subject this source is actually ABOUT, one per line, as
`Type | Title`. A subject earns a line when a reader would expect its own
document. Use the nine classifiable types: Person, Organization, Place,
Event, Procedure, Decision, Project, Concept, Entity.

- Concept | ...

## Facets, not subjects

List headings or terms that exist only to EXPLAIN a subject above. These are
the expected tail: an extractor emitting them is decaying, not enumerating.
Naming them here is what lets a run be scored rather than eyeballed.

- ...

## Near-duplicates

Pairs where two plausible objects name the same thing (the measured case was
`ADK Evaluation Framework` against `Agent Evaluation`). Distinct from facets:
both look like real subjects, but only one belongs in the bundle.

- ... / ...

## Notes

Anything a later reader needs to judge a run fairly -- ambiguous boundaries,
a subject that is defensibly two, a section that is off-topic.
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="mode", required=True)

    survey = sub.add_parser("survey", help="rank markdown under a directory")
    survey.add_argument("directory", type=Path)

    add = sub.add_parser("add", help="copy one file in and scaffold its ground truth")
    add.add_argument("path", type=Path)
    add.add_argument("--name", default=None, help="override the normalized filename")

    args = parser.parse_args(argv)
    if args.mode == "survey":
        return _survey(args.directory.expanduser())
    return _add(args.path.expanduser(), name=args.name)


if __name__ == "__main__":
    raise SystemExit(main())
