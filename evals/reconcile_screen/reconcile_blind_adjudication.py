"""Blind second labelling of the reconciliation fixture (#1563).

The fixture's labels began as one author's. Before registration the owner labels
every claim without seeing the first author's labels, the flagged alternatives,
the anchors the scorer looks for, or the reasoning; then the two label sets are
compared. This module is the model-free half of that: `build_sheet` writes the
sheet the second labeller fills in and `adjudicate` compares the filled sheet with
the fixture. Nothing here writes into `fixture.json`, and the sheet is refused
inside the repository so a filled copy is never committed by accident.

The question on the sheet is one a reader can answer from the two notes alone:
which note states this claim. What the sheet withholds, and why:

- the kind, the alternative and the anchors: they are the answer, or the means of
  scoring it;
- the claim ids, case ids and the source file of every quoted piece: they tie the
  sheet to the committed fixture, and an id such as `x-03` carries its position;
- the fixture order of the claims: the first author wrote a case's claims in
  groups, so the order alone would say which kind is which. Each case's claims
  are a seeded shuffle, and the cases themselves are shuffled too.

Opaque ids are hashes and the orders are seeded, so the mapping is recomputed,
never stored beside the sheet.
"""

from __future__ import annotations

import hashlib
import pathlib
import random
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final

import reconcile_core as core

SEED: Final = 1563
TOKEN: Final = {"a_only": "A", "b_only": "B", "both": "AB"}
TOKENS: Final = ("A", "B", "AB")
"""What the labeller writes: `A` (only the first note states it), `B` (only the
second), `AB` (both do, in any wording)."""

_HEADING: Final = re.compile(r"^### Claim (\w+)\s*$")
_LABEL: Final = re.compile(r"^Label:[ \t]*(.*)$")


def opaque_ids(cases: Sequence[core.Case], seed: int = SEED) -> dict[str, str]:
    """Claim id -> six hex characters, for every active claim."""
    ids = {
        c.id: hashlib.sha256(f"{seed}:{c.id}".encode()).hexdigest()[:6]
        for case in cases
        for c in case.active
    }
    if len(set(ids.values())) != len(ids):
        raise ValueError("opaque id collision; pick another seed")
    return ids


def _opaque_case(case: core.Case, seed: int) -> str:
    return hashlib.sha256(f"{seed}:case:{case.id}".encode()).hexdigest()[:4]


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


def build_sheet(cases: Sequence[core.Case], *, seed: int = SEED) -> str:
    ids = opaque_ids(cases, seed)
    order = list(cases)
    random.Random(seed).shuffle(order)  # noqa: S311 -- reproducible order, not security
    n_claims = sum(len(c.active) for c in cases)
    parts = [
        "# Blind labelling sheet: which note states this claim?",
        "",
        "Label every claim below. Do not look at `fixture.json`, the README, the "
        "pre-registration draft or any other labels first.",
        "",
        "Each case is two notes about one subject. Under each case is a list of claims. "
        "For each claim, decide which note states it. A note states a claim when it "
        "says it, in any wording; a note that only touches the topic does not.",
        "",
        "Write exactly one of `A` (only Note A states it), `B` (only Note B states it) "
        "or `AB` (both state it) after `Label:`. The `Note:` line is optional (use it "
        "where a second answer was also defensible).",
        "",
        f"Sheet seed: {seed}. Cases: {len(cases)}. Claims: {n_claims}.",
        "",
        "## Cases",
    ]
    for case in order:
        parts += [
            "",
            f"### Case {_opaque_case(case, seed)}",
            "",
            f"Subject: {case.title}",
            "",
            "Note A",
            "",
            _quote(case.survivor_body),
            "",
            "Note B",
            "",
            _quote(case.absorbed_body),
        ]
        claims = list(case.active)
        random.Random(f"{seed}:{case.id}").shuffle(claims)  # noqa: S311 -- reproducible order, not security
        for claim in claims:
            parts += [
                "",
                f"### Claim {ids[claim.id]}",
                "",
                f"Statement: {claim.claim}",
                "",
                "Label: ",
                "Note: ",
            ]
    return "\n".join(parts) + "\n"


def sheet_chrome(sheet: str) -> list[str]:
    """Words of the sheet outside the quoted notes, the statements and the
    `Label`/`Note` lines: what the sheet itself says, which must not carry a label."""
    words: list[str] = []
    for line in sheet.splitlines():
        if line.startswith((">", "Statement:", "Subject:")) or _LABEL.match(line):
            continue
        words += re.findall(r"[a-z_]+", line.lower())
    return words


def parse_sheet(text: str) -> dict[str, str]:
    """Opaque claim id -> the upper-cased text written after `Label:` (empty if
    left blank). Note text is quoted (`> `), so a heading or `Label:` line inside
    a note can never be read as structure."""
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
            labels[current] = label.group(1).strip().upper()
    return labels


@dataclass(frozen=True)
class Disagreement:
    claim: str
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
    """Cohen's kappa over the three labels; `None` when chance agreement is 1."""
    n = len(first)
    if n == 0 or n != len(second):
        return None
    observed = sum(a == b for a, b in zip(first, second, strict=True)) / n
    chance = sum((first.count(t) / n) * (second.count(t) / n) for t in TOKENS)
    if chance >= 1.0:
        return None
    return (observed - chance) / (1.0 - chance)


def adjudicate(
    cases: Sequence[core.Case], labels: Mapping[str, str], seed: int = SEED
) -> Adjudication:
    """Compare a parsed filled sheet with the FIRST author's labels (`first_kind`,
    not the resolved `kind`, so the recorded agreement stays reproducible).

    Any problem (missing, unknown, blank or invalid) returns the problems and no
    score: a partial sheet must not produce a flattering number."""
    ids = opaque_ids(cases, seed)
    claims = {ids[c.id]: c for case in cases for c in case.active}
    problems = [f"unknown claim {o}" for o in sorted(set(labels) - set(claims))]
    for opaque in sorted(claims):
        if opaque not in labels:
            problems.append(f"claim {opaque} is missing from the sheet")
        elif labels[opaque] == "":
            problems.append(f"claim {opaque} is not labelled")
        elif labels[opaque] not in TOKENS:
            problems.append(
                f"claim {opaque}: {labels[opaque]!r} is not one of {TOKENS}"
            )
    if problems:
        return Adjudication(problems=problems)
    ordered = [(o, claims[o]) for o in sorted(claims)]
    first = [TOKEN[c.first_kind] for _o, c in ordered]
    second = [labels[o] for o, _c in ordered]
    misses = [
        Disagreement(
            claim=c.id,
            opaque=o,
            first=TOKEN[c.first_kind],
            second=labels[o],
            is_alt=c.alt_label is not None and TOKEN[c.alt_label] == labels[o],
        )
        for o, c in ordered
        if TOKEN[c.first_kind] != labels[o]
    ]
    return Adjudication(
        n=len(ordered),
        agreed=len(ordered) - len(misses),
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
        f"claims: {result.n}",
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
            f"  {d.claim} [sheet {d.opaque}]: first={d.first} second={d.second}{note}"
        )
    lines.append(
        "Resolve each and record the outcome in the registration; this command "
        "writes nothing into fixture.json. A claim that moves between one-sided and "
        "shared needs a `once` literal (or its removal as `retired`) before it can be "
        "re-scored."
    )
    return "\n".join(lines)
