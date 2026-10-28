"""Blind second adjudication of the edge-typing fixture (#1563).

The fixture's labels are one author's. Before registration a second person
labels every item without seeing the first author's labels, the flagged
alternatives, the reversed-probe markers or the first author's reasoning; then
the two label sets are compared. This module is the model-free half of that:
`build_sheet` writes the sheet the second adjudicator fills in and `adjudicate`
compares the filled sheet with the fixture. Nothing here writes into
`fixture.json`, and the sheet is refused inside the repository so a filled copy
is never committed by accident. The method is the volatility screen's.

What the sheet shows: the production rubric VERBATIM (the definitions, the
tie-breaks and the closing guard of `edge_typing._SYSTEM_PROMPT`, so the human
decides by the rule the model is given) and, per item, the two documents'
titles and bodies as the model reads them, SOURCE first.

What the sheet withholds, and why:

- the primary label, the alternative and `how`: they are the answer;
- the **trap marker** and the **stratum**: a reversed probe and a constructed
  pair are recognisable by their text, but the sheet does not say so;
- the concept **ids** and object types: the id's folder names the object type
  (`people/`, `projects/`), and three type pairs have a deterministic answer in
  the model-free `type signature` baseline the screen compares models against.
  The model sees the ids; the adjudication question is what the DOCUMENTS say,
  and showing the folder would anchor a person on that predictor exactly as the
  registry default anchored the volatility screen's first author;
- the document `source` paths, which name the corpus and the file;
- the fixture item id: it ties the sheet to the committed fixture.

Item ids are opaque hashes and the order is a seeded shuffle, so the mapping is
recomputed, never stored beside the sheet. Both orientations of one document
pair (the reversed probes) appear as separate items, apart in the shuffle; that
is the question being asked of them, not a leak.
"""

from __future__ import annotations

import hashlib
import pathlib
import random
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final

import edge_screen_core as core

from openkos.resolution import edge_typing as et

SEED: Final = 1563
RUBRIC_START: Final = "What each one means, read as SOURCE -> TARGET:"
RUBRIC_END: Final = "\n\nReturn ONLY a JSON object"

_HEADING: Final = re.compile(r"^### Item (\w+)\s*$")
_LABEL: Final = re.compile(r"^Label:[ \t]*(.*)$")


def rubric(prompt: str | None = None) -> str:
    """The definitions, tie-breaks and guard of the production system prompt,
    verbatim: from the definitions to the instruction about the JSON reply."""
    text = et._SYSTEM_PROMPT if prompt is None else prompt
    start, end = text.find(RUBRIC_START), text.find(RUBRIC_END)
    if start < 0 or end < start:
        raise ValueError("the production rubric was not found in the system prompt")
    return text[start:end].strip()


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


def quote(text: str) -> str:
    return "\n".join(f"> {line}".rstrip() for line in text.splitlines())


def build_sheet(items: Sequence[core.Item], *, seed: int = SEED) -> str:
    ids = opaque_ids(items, seed)
    parts = [
        "# Blind labelling sheet: how does SOURCE relate to TARGET?",
        "",
        "Label every item below. Do not look at `fixture.json`, the README, the "
        "pre-registration draft, `evals/edge_typing/` or any other labels first.",
        "",
        "Each item is one existing link between two documents. Decide the single "
        "relation type that describes how SOURCE relates to TARGET, using the rule "
        "below, which is the one the model is given. Judge what the two documents "
        "SAY, and read the type as SOURCE -> TARGET: the same two documents shown "
        "the other way round can deserve a different answer.",
        "",
        "Write exactly one of "
        + ", ".join(f"`{t}`" for t in core.TYPES)
        + " after `Label:`. The `Note:` line is optional (use it where a second "
        "type was also defensible).",
        "",
        "## Rule (the production prompt, verbatim)",
        "",
        quote(rubric()),
        "",
        f"Sheet seed: {seed}. Items: {len(items)}.",
        "",
        "## Items",
    ]
    for item in shuffled(items, seed):
        parts += ["", f"### Item {ids[item.id]}", ""]
        for role, doc in (("SOURCE", item.source), ("TARGET", item.target)):
            parts += [role, "", quote(f"**{doc.title}**"), ">", quote(doc.text), ""]
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
        words += re.findall(r"[a-z_]+", line.lower())
    return words


def parse_sheet(text: str) -> dict[str, str]:
    """Opaque id -> the text written after `Label:` (empty if left blank).

    Document text is quoted (`> `), so a heading or `Label:` line inside a body
    can never be read as structure."""
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
    """Cohen's kappa over the relation types; `None` when chance agreement is 1."""
    n = len(first)
    if n == 0 or n != len(second):
        return None
    observed = sum(a == b for a, b in zip(first, second, strict=True)) / n
    chance = sum((first.count(t) / n) * (second.count(t) / n) for t in core.TYPES)
    if chance >= 1.0:
        return None
    return (observed - chance) / (1.0 - chance)


def adjudicate(
    items: Sequence[core.Item], labels: Mapping[str, str], seed: int = SEED
) -> Adjudication:
    """Compare a parsed filled sheet with the FIRST author's labels (`first_label`,
    not the resolved `label`, so the recorded agreement stays reproducible).

    Any problem (missing, unknown, blank or invalid) returns the problems and no
    score: a partial sheet must not produce a flattering number."""
    ids = opaque_ids(items, seed)
    by_opaque = {ids[i.id]: i for i in items}
    problems = [f"unknown item {o}" for o in sorted(set(labels) - set(by_opaque))]
    for opaque in sorted(by_opaque):
        if opaque not in labels:
            problems.append(f"item {opaque} is missing from the sheet")
        elif labels[opaque] == "":
            problems.append(f"item {opaque} is not labelled")
        elif labels[opaque] not in core.TYPES:
            problems.append(
                f"item {opaque}: {labels[opaque]!r} is not one of {core.TYPES}"
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


def import_labels(
    items: Sequence[core.Item], labels: Mapping[str, str], seed: int = SEED
) -> tuple[dict[str, str], list[str]]:
    """Fixture item id -> the second labeller's label, from a parsed filled sheet.

    Validation is `adjudicate`'s: any missing, unknown, blank or invalid entry
    returns the problems and NO labels, so a partial sheet imports nothing."""
    problems = adjudicate(items, labels, seed).problems
    if problems:
        return {}, problems
    ids = opaque_ids(items, seed)
    return {i.id: labels[ids[i.id]] for i in items}, []


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
