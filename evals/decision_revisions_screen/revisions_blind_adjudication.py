"""Blind second adjudication of the decision-revisions fixture (#1563).

The fixture's labels are one drafter's; the owner accepted the 16 pairs the
drafter flagged as contested and nobody labelled the other 42 a second time
(`README.md`). Before registration a second person labels every pair without
seeing the drafter's labels, notes, hard-case tags or contested flags; then the
two label sets are compared. This module is the model-free half of that:
`build_sheet` writes the sheet the second adjudicator fills in and `adjudicate`
compares the filled sheet with the fixture. Nothing here writes into the
fixture, and the sheet is refused inside the repository so a filled copy is never
committed by accident. The method is the volatility and edge-typing screens'.

What the sheet shows: the production judge's definitions VERBATIM (the four
verdicts and the symmetric clause for an unordered pair, taken from the shipped
system prompt, so the human decides by the rule the model is given) and, per
pair, the two Decisions exactly as the judge is shown them: titles and bodies,
the earlier one first with its date when the dates order the pair, or both under
`ORDER UNKNOWN` when they do not.

What the sheet withholds, and why:

- the label, the note, the contested flag and the hard-case tag: they are the
  answer (a tag such as `reversal-reads-as-refine` names it);
- the concept ids: a slug such as `first-saturday-book-swap-opening` is part of
  the answer's story. The judge sees the ids; the question put to the adjudicator
  is what the two Decisions SAY;
- the source ids and the split: a source id names a meeting, the split says which
  pairs were written after the prompt was tuned;
- the fixture item id: it ties the sheet to the committed fixture.

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

import revisions_screen_core as core

from openkos.resolution.decision_revision import (
    DecisionDate,
    JudgeSide,
    build_judge_messages,
)

SEED: Final = 1563
RUBRIC_START: Final = "Decide how the second Decision relates"
RUBRIC_END: Final = "\n\nQuote one sentence"

_HEADING: Final = re.compile(r"^### Item (\w+)\s*$")
_LABEL: Final = re.compile(r"^Label:[ \t]*(.*)$")

ANSWERS: Final = tuple(core.WIRE[label] for label in core.LABELS)
"""What the adjudicator writes after `Label:` -- the judge's own wire verdicts."""


def judge_prompt() -> str:
    """The shipped judge system prompt, read through production's own builder."""
    side = JudgeSide("decisions/x", "x", "x", DecisionDate(value=None, state="missing"))
    return build_judge_messages(side, side)[0]["content"]


def rubric(prompt: str | None = None) -> str:
    """The definitions of the production judge prompt, verbatim: from the
    instruction to relate the two Decisions up to the instruction to quote."""
    text = judge_prompt() if prompt is None else prompt
    start, end = text.find(RUBRIC_START), text.find(RUBRIC_END)
    if start < 0 or end < start:
        raise ValueError("the production rubric was not found in the judge prompt")
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


def presentation(item: core.Item) -> list[tuple[str, str, str]]:
    """`(header, title, body)` for each Decision in the order the judge sees it:
    earlier first under a dated header when the dates order the pair, otherwise
    both in sorted-id order under `DECISION n`, with no date."""
    if item.earlier is not None:
        order = [item.earlier, 1 - item.earlier]
        names = ["EARLIER DECISION", "LATER DECISION"]
        return [
            (f"{name} ({core.date_text(item, n)})", item.titles[n], item.bodies[n])
            for name, n in zip(names, order, strict=True)
        ]
    return [(f"DECISION {n + 1}", item.titles[n], item.bodies[n]) for n in (0, 1)]


UNORDERED_NOTE: Final = (
    "ORDER UNKNOWN: the dates of these decisions do not establish which came first."
)


def build_sheet(items: Sequence[core.Item], *, seed: int = SEED) -> str:
    ids = opaque_ids(items, seed)
    parts = [
        "# Blind labelling sheet: how does the later Decision relate to the earlier?",
        "",
        "Label every item below. Do not look at the fixture, the README, the "
        "pre-registration draft, `evals/decision_revisions/` or any other labels "
        "first.",
        "",
        "Each item is a pair of Decisions recorded in a small library committee's "
        "notes. Decide the single verdict that describes how the second Decision "
        "relates to the first one's choice, using the rule below, which is the one "
        "the model is given. Where the earlier Decision is named, the later one is "
        "the one whose choice you are judging against it; where the order is "
        "unknown, judge the pair symmetrically.",
        "",
        "Write exactly one of "
        + ", ".join(f"`{a}`" for a in ANSWERS)
        + " after `Label:`. The `Note:` line is optional (use it where a second "
        "verdict was also defensible).",
        "",
        "## Rule (the production judge prompt, verbatim)",
        "",
        quote(rubric()),
        "",
        f"Sheet seed: {seed}. Items: {len(items)}.",
        "",
        "## Items",
    ]
    for item in shuffled(items, seed):
        parts += ["", f"### Item {ids[item.id]}", ""]
        if item.earlier is None:
            parts += [UNORDERED_NOTE, ""]
        for header, title, body in presentation(item):
            parts += [header, "", quote(f"**{title}**"), ">", quote(body), ""]
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

    Decision text is quoted (`> `), so a heading or `Label:` line inside a body
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
    contested: bool
    scored_alike: bool
    """Both labels are the same class once an undirected REVERSES and REFINES are
    one untyped change: the disagreement cannot move any score."""


@dataclass(frozen=True)
class Adjudication:
    n: int = 0
    agreed: int = 0
    agreed_scored: int = 0
    kappa: float | None = None
    disagreements: list[Disagreement] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def raw_agreement(self) -> float | None:
        return self.agreed / self.n if self.n else None

    @property
    def scored_agreement(self) -> float | None:
        return self.agreed_scored / self.n if self.n else None

    @property
    def to_resolve(self) -> list[Disagreement]:
        return [d for d in self.disagreements if not d.scored_alike]


def cohen_kappa(first: Sequence[str], second: Sequence[str]) -> float | None:
    """Cohen's kappa over the four verdicts; `None` when chance agreement is 1."""
    n = len(first)
    if n == 0 or n != len(second):
        return None
    observed = sum(a == b for a, b in zip(first, second, strict=True)) / n
    chance = sum((first.count(t) / n) * (second.count(t) / n) for t in ANSWERS)
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
        elif labels[opaque] not in ANSWERS:
            problems.append(
                f"item {opaque}: {labels[opaque]!r} is not one of {ANSWERS}"
            )
    if problems:
        return Adjudication(problems=problems)
    first = [core.WIRE[i.first_label] for i in items]
    second = [labels[ids[i.id]] for i in items]
    misses = [
        Disagreement(
            item=i.id,
            opaque=ids[i.id],
            first=core.WIRE[i.first_label],
            second=labels[ids[i.id]],
            contested=i.contested,
            scored_alike=core.scored_class(i, i.first_label)
            == core.scored_class(i, labels[ids[i.id]]),
        )
        for i in items
        if core.WIRE[i.first_label] != labels[ids[i.id]]
    ]
    return Adjudication(
        n=len(items),
        agreed=len(items) - len(misses),
        agreed_scored=len(items) - sum(not d.scored_alike for d in misses),
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
        f"agreement on the scored classes: {result.agreed_scored}/{result.n} = "
        f"{result.scored_agreement:.3f} (an undirected reverses and refines are "
        "one untyped change)",
        f"Cohen's kappa (four verdicts, raw): {kappa}",
        f"disagreements to resolve: {len(result.to_resolve)} "
        f"(of {len(result.disagreements)} label differences)",
    ]
    for d in result.disagreements:
        notes = []
        if d.contested:
            notes.append("the drafter flagged this pair contested")
        if d.scored_alike:
            notes.append("scores alike, nothing to resolve")
        suffix = f" ({'; '.join(notes)})" if notes else ""
        lines.append(
            f"  {d.item} [sheet {d.opaque}]: first={d.first} second={d.second}{suffix}"
        )
    lines.append(
        "Resolve each and record the outcome in `adjudication.json`; this command "
        "writes nothing."
    )
    return "\n".join(lines)
