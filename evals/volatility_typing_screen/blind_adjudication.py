"""Blind second adjudication of the volatility-typing fixture (#1563).

The fixture's labels began as one author's. Before registration a second person
labels every item without seeing the first author's labels, the flagged
alternatives, the per-type registry defaults or the first author's reasoning;
then the two label sets are compared. This module is the model-free half of
that: `build_sheet` writes the sheet the second adjudicator fills in and
`adjudicate` compares the filled sheet with the fixture. Nothing here writes
into `fixture.json`, and the sheet is refused inside the repository so a filled
copy is never committed by accident.

What the sheet withholds, and why:

- the primary label, the alternative and `how`: they are the answer;
- the item **type**: every type has a registry default (`Decision` -> static,
  `Concept` -> slow, ...), so showing the type shows the default and anchors the
  adjudicator on the very predictor the screen compares models against;
- the document `source` paths and ids: a path such as `concepts/x.md` is the
  type again;
- the fixture item id: it ties the sheet to the committed table in the README.

Item ids are opaque hashes and the order is a seeded shuffle, so the mapping is
recomputed, never stored beside the sheet.
"""

from __future__ import annotations

import hashlib
import pathlib
import random
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final

import screen_core as core

SEED: Final = 1563
ADR_PATH: Final = core.REPO_ROOT / "docs/adr/0007-volatility-taxonomy.md"
RUBRIC_QUOTE_START: Final = "We adopt a **fixed three-tier volatility taxonomy**"
RUBRIC_QUOTE_END: Final = (
    "(continuity with today's global default for fast-moving types)."
)
"""The two sentences of ADR-0007's Decision that define the tiers. The next
paragraph lists the per-type defaults, so it is deliberately not quoted."""

_HEADING: Final = re.compile(r"^### Item (\w+)\s*$")
_LABEL: Final = re.compile(r"^Label:[ \t]*(.*)$")


def normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def read_adr(path: pathlib.Path = ADR_PATH) -> str:
    return path.read_text(encoding="utf-8")


def rubric(adr_text: str) -> str:
    """ADR-0007's tier definition, verbatim (line wraps collapsed)."""
    flat = normalise(adr_text)
    start = flat.find(RUBRIC_QUOTE_START)
    end = flat.find(RUBRIC_QUOTE_END)
    if start < 0 or end < start:
        raise ValueError("ADR-0007's tier definition was not found")
    return flat[start : end + len(RUBRIC_QUOTE_END)]


def opaque_ids(items: Sequence[core.Item], seed: int = SEED) -> dict[str, str]:
    ids = {
        i.id: hashlib.sha256(f"{seed}:{i.id}".encode()).hexdigest()[:6] for i in items
    }
    if len(set(ids.values())) != len(ids):
        raise ValueError("opaque id collision; pick another seed")
    return ids


def shuffled(items: Sequence[core.Item], seed: int = SEED) -> list[core.Item]:
    order = list(items)
    random.Random(seed).shuffle(order)  # noqa: S311 -- reproducible order, not security
    return order


def outside_repo_problem(path: pathlib.Path) -> str | None:
    resolved = path.expanduser().resolve()
    if resolved == core.REPO_ROOT or core.REPO_ROOT in resolved.parents:
        return (
            f"{resolved} is inside the repository; write the sheet elsewhere so a "
            "filled copy is never committed"
        )
    return None


def _quote(text: str) -> str:
    return "\n".join(f"> {line}".rstrip() for line in text.splitlines())


def build_sheet(items: Sequence[core.Item], *, adr_text: str, seed: int = SEED) -> str:
    ids = opaque_ids(items, seed)
    parts = [
        "# Blind labelling sheet: how fast does this knowledge go stale?",
        "",
        "Label every item below. Do not look at `fixture.json`, the README's item "
        "table, the pre-registration draft or any other labels first.",
        "",
        "For each item you see the concept documents of one group. Decide how "
        "long until a reader should re-check the facts in those bodies, using the "
        "rubric below. Judge the **content shown**.",
        "",
        "Write exactly one of `static`, `slow`, `volatile` after `Label:`. The "
        "`Note:` line is optional (use it where a second tier was also defensible).",
        "",
        "## Rubric (ADR-0007, verbatim)",
        "",
        _quote(rubric(adr_text)),
        "",
        f"Sheet seed: {seed}. Items: {len(items)}.",
        "",
        "## Items",
    ]
    for item in shuffled(items, seed):
        parts += ["", f"### Item {ids[item.id]}", ""]
        for n, doc in enumerate(item.docs, 1):
            parts += [f"Document {n}", "", _quote(f"**{doc.title}**"), ">"]
            parts += [_quote(doc.text), ""]
        parts += ["Label: ", "Note: "]
    return "\n".join(parts) + "\n"


def item_blocks(sheet: str) -> list[str]:
    """The text of each `### Item` block (heading included)."""
    blocks: list[list[str]] = []
    for line in sheet.splitlines():
        if _HEADING.match(line):
            blocks.append([])
        if blocks:
            blocks[-1].append(line)
    return ["\n".join(b) for b in blocks]


def item_chrome(block: str) -> list[str]:
    """Words of a block outside the quoted document text and the Label line."""
    words: list[str] = []
    for line in block.splitlines():
        if line.startswith(">") or _LABEL.match(line):
            continue
        words += re.findall(r"[a-z]+", line.lower())
    return words


def parse_sheet(text: str) -> dict[str, str]:
    """Opaque id -> the text written after `Label:` (empty if left blank).

    Document text is quoted (`> `), so a heading or `Label:` line inside a body
    can never be read as structure.
    """
    labels: dict[str, str] = {}
    current: str | None = None
    for line in text.splitlines():
        heading = _HEADING.match(line)
        if heading:
            current = heading.group(1)
            labels.setdefault(current, "")
            continue
        label = _LABEL.match(line)
        if label and current is not None and not labels[current]:
            labels[current] = label.group(1).strip().lower()
    return labels


@dataclass(frozen=True)
class Disagreement:
    item: str
    opaque: str
    first: str
    second: str
    is_alt: bool


@dataclass(frozen=True)
class Adjudication:
    n: int = 0
    agreed: int = 0
    kappa: float | None = None
    disagreements: list[Disagreement] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def raw_agreement(self) -> float | None:
        return self.agreed / self.n if self.n else None


def cohen_kappa(first: Sequence[str], second: Sequence[str]) -> float | None:
    """Cohen's kappa over the tiers; `None` when chance agreement is 1."""
    n = len(first)
    if n == 0 or n != len(second):
        return None
    observed = sum(a == b for a, b in zip(first, second, strict=True)) / n
    chance = sum((first.count(t) / n) * (second.count(t) / n) for t in core.TIERS)
    if chance >= 1.0:
        return None
    return (observed - chance) / (1.0 - chance)


def adjudicate(
    items: Sequence[core.Item], labels: Mapping[str, str], seed: int = SEED
) -> Adjudication:
    """Compare a parsed filled sheet with the FIRST author's labels (`first_label`,
    not the resolved `label`, so the recorded agreement stays reproducible).

    Any problem (missing, unknown, blank or invalid) returns the problems and no
    score: a partial sheet must not produce a flattering number.
    """
    ids = opaque_ids(items, seed)
    by_opaque = {ids[i.id]: i for i in items}
    problems = [f"unknown item {o}" for o in sorted(set(labels) - set(by_opaque))]
    for opaque in sorted(by_opaque):
        if opaque not in labels:
            problems.append(f"item {opaque} is missing from the sheet")
        elif labels[opaque] == "":
            problems.append(f"item {opaque} is not labelled")
        elif labels[opaque] not in core.TIERS:
            problems.append(
                f"item {opaque}: {labels[opaque]!r} is not one of {core.TIERS}"
            )
    if problems:
        return Adjudication(problems=problems)
    first = [i.first_label for i in items]
    second = [labels[ids[i.id]] for i in items]
    misses = [
        Disagreement(
            item=i.id,
            opaque=ids[i.id],
            first=i.first_label,
            second=labels[ids[i.id]],
            is_alt=i.alt_label == labels[ids[i.id]],
        )
        for i in items
        if i.first_label != labels[ids[i.id]]
    ]
    return Adjudication(
        n=len(items),
        agreed=len(items) - len(misses),
        kappa=cohen_kappa(first, second),
        disagreements=misses,
    )


def render(result: Adjudication) -> str:
    if result.problems:
        return "SHEET REJECTED, nothing scored:\n" + "\n".join(
            f"  - {p}" for p in result.problems
        )
    kappa = "undefined" if result.kappa is None else f"{result.kappa:.3f}"
    lines = [
        f"items: {result.n}",
        f"raw agreement: {result.agreed}/{result.n} = {result.raw_agreement:.3f}",
        f"Cohen's kappa: {kappa}",
        f"disagreements to resolve: {len(result.disagreements)}",
    ]
    for d in result.disagreements:
        note = (
            " (second label is the first author's flagged alternative)"
            if d.is_alt
            else ""
        )
        lines.append(
            f"  {d.item} [sheet {d.opaque}]: first={d.first} second={d.second}{note}"
        )
    lines.append(
        "Resolve each and record the outcome in the registration; this command "
        "writes nothing into fixture.json."
    )
    return "\n".join(lines)
