"""The measurement-only judge for the concept model screen (#1557).

`gpt-oss:20b` (decision D3): its family is not among the candidates, and it
must never become one. Two prompts, both harness-owned (no production prompt
is reused or changed):

- **G1, source-grounded precision.** One call per window per model-run: is
  each produced `title: description` supported by the window that produced it?
- **Judge coverage.** One call per meeting-run (finalists), or per window (the
  registered fallback primary): is each gold summary sentence covered by the
  produced entries?

Replies are parsed fail-closed by production `parsing.extract_json_items`.
An unparseable reply is retried once with a different seed; a second failure
excludes that item and is counted.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Final

from openkos.llm import parsing
from openkos.llm.base import Message

JUDGE_MODEL: Final = "gpt-oss:20b"
FORBIDDEN_CANDIDATES: Final = frozenset({JUDGE_MODEL})

G1_SYSTEM: Final = """You check knowledge-base entries against the source excerpt they were extracted from.

For EACH numbered entry, decide whether the excerpt supports it:
- "supported": everything the entry states is said in, or directly implied by, the excerpt.
- "partial": some of what it states is supported, but some is not.
- "unsupported": the excerpt does not support it, or it contradicts the excerpt.

Judge only against the excerpt. Do not use outside knowledge. Generic or vague
entries are judged on what they actually claim.

Reply with ONLY a JSON array, one object per entry, in entry order:
[{"id": 1, "label": "supported"}, {"id": 2, "label": "partial"}]"""

COVERAGE_SYSTEM: Final = """You check whether a set of knowledge-base entries covers the sentences of a human-written meeting summary.

For EACH numbered summary sentence, decide whether the entries convey its content:
- "covered": the entries, taken together, state what the sentence says.
- "partial": the entries state part of it.
- "not": the entries do not state it.

Judge only from the entries. A sentence is not covered merely because the entries are on the same topic.

Reply with ONLY a JSON array, one object per summary sentence, in order:
[{"id": 1, "label": "covered"}, {"id": 2, "label": "not"}]"""

G1_LABELS: Final = ("supported", "partial", "unsupported")
COVERAGE_LABELS: Final = ("covered", "partial", "not")


def g1_messages(window: str, entries: Sequence[str]) -> list[Message]:
    lines = "\n".join(f"{i}. {e}" for i, e in enumerate(entries, start=1))
    user = f"SOURCE EXCERPT:\n{window}\n\nENTRIES:\n{lines}"
    return [{"role": "system", "content": G1_SYSTEM}, {"role": "user", "content": user}]


def coverage_messages(
    entries: Sequence[str], sentences: Sequence[str]
) -> list[Message]:
    kb = "\n".join(f"- {e}" for e in entries) or "- (no entries)"
    gold = "\n".join(f"{i}. {s}" for i, s in enumerate(sentences, start=1))
    user = f"ENTRIES:\n{kb}\n\nSUMMARY SENTENCES:\n{gold}"
    return [
        {"role": "system", "content": COVERAGE_SYSTEM},
        {"role": "user", "content": user},
    ]


def parse_labels(reply: str, count: int, allowed: Sequence[str]) -> list[str] | None:
    """Labels in item order, or None when the reply does not label every item
    exactly once with an allowed label."""
    items = parsing.extract_json_items(reply)
    labels: dict[int, str] = {}
    for item in items:
        raw_id: Any = item.get("id")
        label = str(item.get("label", "")).strip().casefold()
        try:
            index = int(raw_id)
        except TypeError, ValueError:
            return None
        if label not in allowed or index in labels or not 1 <= index <= count:
            return None
        labels[index] = label
    if len(labels) != count:
        return None
    return [labels[i] for i in range(1, count + 1)]


def g1_counts(labels: Sequence[str]) -> Mapping[str, int]:
    return {label: sum(1 for x in labels if x == label) for label in G1_LABELS}


def coverage_score(labels: Sequence[str]) -> float:
    """covered = 1, partial = 0.5, not = 0; mean over sentences."""
    weights = {"covered": 1.0, "partial": 0.5, "not": 0.0}
    return sum(weights[x] for x in labels) / len(labels) if labels else 0.0
