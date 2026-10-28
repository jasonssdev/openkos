"""The deterministic half of the Decision-fidelity measurement (#1649).

Pure functions over plain data, standard library only (no `openkos` import, no
model, no network), so the unit suite and the harness `--self-test` pin every
number a paid run rests on. The pre-registration is `PREREGISTRATION-1649.md`;
every constant marked REGISTERED below is fixed there and changes only by a new
registration.

Two failure classes, both measured at extraction (never at query time):

(a) VALUE FIDELITY. A Decision drops or misstates its concrete value (the final
    cost stored instead of the cap that was decided). The gold is AMI's own
    abstractive `decisions` sentences, anchored by `summlink` to the dialogue
    turns that carry them; a labeller adds, per sentence, the surface forms of
    the decided value and the distractor values the same turns also contain.
    A produced Decision matches an item through its quoted evidence line and is
    scored on its STATEMENT (title, description and the value line of the body),
    never on the quoted evidence: a value that survives only inside the quote
    is the very failure being measured, and is counted apart (`evidence_only`).

(b) PROPOSAL VERSUS COMMITMENT. A discarded or still-open proposal is stored as
    a Decision. A labeller marks each produced evidence line `committed`,
    `discarded`, `open` or `none`, reading the line with its surrounding turns.
    The guard under test is a lexical commitment cue on that evidence line; it
    is deterministic, so it is applied to stored runs offline.

Eligibility is fail-closed: the twelve development meetings carry the gold, six
free-pool meetings carry the guard's calibration, and nothing else is read.
"""

from __future__ import annotations

import hashlib
import importlib.util
import math
import random
import re
import sys
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

_HERE: Final = Path(__file__).resolve().parent


def load_sibling(name: str, path: Path) -> Any:
    """Load a module from a path under a unique name, so a same-named module
    already in `sys.modules` (several harnesses ship a `scoring.py`) is never
    picked up by mistake."""
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(str(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_matching = load_sibling(
    "fidelity_matching", _HERE.parent / "transcript_decisions" / "scoring.py"
)
"""The #1348 evidence-matching rule, reused unchanged: a Decision's evidence and
a gold turn match on a common run of at least 4 tokens covering 60% of the
shorter side."""


def evidence_matches(evidence: str, act: str) -> bool:
    """Whether a Decision's evidence and a gold turn are the same utterance cut
    differently (the #1348 rule)."""
    return bool(_matching.evidence_matches(evidence, act))


# --- eligibility -------------------------------------------------------------

DEV_SERIES: Final = ("ES2015", "IS1004", "TS3009")
GOLD_MEETINGS: Final = tuple(f"{s}{letter}" for s in DEV_SERIES for letter in "abcd")
"""REGISTERED. The development meetings: they carry the gold and the arms."""

CUE_MEETINGS: Final = (
    "IS1002b",
    "IS1002c",
    "IS1002d",
    "IS1005a",
    "IS1005b",
    "IS1005c",
)
"""REGISTERED. Free-pool meetings (in no thesis set) used ONLY to calibrate the
commitment cue, so the cue is frozen on text the arms never read."""

ELIGIBLE: Final = frozenset(GOLD_MEETINGS) | frozenset(CUE_MEETINGS)


class MeetingRefused(ValueError):
    """A meeting outside the registered pools."""


def require_eligible(meeting: str) -> str:
    """`meeting` if it may be read, else `MeetingRefused`. No override exists."""
    if meeting in ELIGIBLE:
        return meeting
    if meeting.startswith("TS3012"):
        raise MeetingRefused(
            f"{meeting!r} is ineligible: TS3012 is excluded by epic #1556 "
            "(production extraction hits the whole-source cap on it)"
        )
    raise MeetingRefused(
        f"{meeting!r} is not a registered meeting. Only the development series "
        f"({', '.join(DEV_SERIES)}) and the calibration pool may be read; every "
        "thesis evaluation or reserve series is refused."
    )


# --- text --------------------------------------------------------------------

_CURLY_APOSTROPHE: Final = "\N{RIGHT SINGLE QUOTATION MARK}"
_TOKEN_RE: Final = re.compile(r"\d+(?:[.,]\d+)*|[^\W\d_]+(?:'[^\W_]+)*")
_SPEAKER_RE: Final = re.compile(r"^\s*[A-Z]{1,2}:\s+")


def tokens(text: str) -> list[str]:
    """Casefolded word and number tokens; a number keeps its decimal point."""
    return _TOKEN_RE.findall(text.replace(_CURLY_APOSTROPHE, "'").casefold())


def states(text: str, forms: Sequence[str]) -> bool:
    """Whether `text` contains any of `forms` as a whole token sequence."""
    haystack = f" {' '.join(tokens(text))} "
    for form in forms:
        needle = " ".join(tokens(form))
        if needle and f" {needle} " in haystack:
            return True
    return False


def classify_value(
    statement: str, forms: Sequence[str], distractors: Sequence[str]
) -> str:
    """`correct` (a form, no distractor), `mixed` (both), `wrong` (a distractor
    only) or `missing` (neither)."""
    right = states(statement, forms)
    wrong = states(statement, distractors)
    if right and wrong:
        return "mixed"
    if right:
        return "correct"
    if wrong:
        return "wrong"
    return "missing"


VERDICT_RANK: Final = {"correct": 3, "mixed": 2, "wrong": 1, "missing": 0}


def split_decision(record: Mapping[str, Any]) -> tuple[str, str]:
    """`(statement, evidence)` of one stored Decision.

    The statement is the title, the description and the body lines that are not
    a quote; the evidence is the quoted lines (`> ...`), or the record's own
    `evidence` field when it has one."""
    statement_parts = [
        str(record.get("title") or ""),
        str(record.get("description") or ""),
    ]
    quoted: list[str] = []
    for line in str(record.get("body") or "").splitlines():
        if line.startswith(">"):
            quoted.append(line.lstrip(">").strip())
        elif line.strip():
            statement_parts.append(line)
    explicit = record.get("evidence")
    evidence = explicit if isinstance(explicit, str) else " ".join(quoted)
    return "\n".join(p for p in statement_parts if p), evidence.strip()


def line_key(line: str) -> str:
    """Stable key of an evidence line: its tokens, speaker label removed."""
    text = _SPEAKER_RE.sub("", line)
    joined = " ".join(tokens(text))
    return hashlib.sha1(joined.encode(), usedforsecurity=False).hexdigest()[:16]


# --- the commitment cue (REGISTERED) ----------------------------------------

COMMIT_PATTERNS: Final = tuple(
    re.compile(p)
    for p in (
        r"\b(?:we|i|you|they)(?:'ll| will)\b",
        r"\bwill (?:be|have|use|go|do|make|include|cost)\b",
        r"\bgoing to\b",
        r"\bgonna\b",
        r"\blet'?s\b",
        r"\bgo(?:ing)? with\b",
        r"\b(?:decided|decide|agreed|agree|settled)\b",
        r"\b(?:must|has to|have to|need to|needs to|got to)\b",
        r"\bwe(?:'re| are) (?:using|doing|having|going)\b",
    )
)
"""REGISTERED. A line carries commitment language when one non-question sentence
of it matches any pattern. The lexicon is frozen before any labelled line is
seen; the calibration gate (`stage0_gate`) decides whether it is used at all."""

_SENTENCE_SPLIT: Final = re.compile(r"(?<=[.!?])\s+")


def commitment_cue(line: str) -> bool:
    """Whether `line` has commitment language outside a question."""
    for sentence in _SENTENCE_SPLIT.split(
        line.replace(_CURLY_APOSTROPHE, "'").casefold()
    ):
        sentence = sentence.strip()
        if not sentence or sentence.endswith("?"):
            continue
        if any(p.search(sentence) for p in COMMIT_PATTERNS):
            return True
    return False


def apply_guard(
    decisions: Sequence[Mapping[str, Any]],
) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]]]:
    """`(kept, dropped)`: a Decision whose evidence has no cue is dropped (in
    production it would be demoted to a Concept; here it simply leaves the
    Decision set). A Decision with no evidence has shown no commitment."""
    kept: list[Mapping[str, Any]] = []
    dropped: list[Mapping[str, Any]] = []
    for record in decisions:
        _, evidence = split_decision(record)
        (kept if evidence and commitment_cue(evidence) else dropped).append(record)
    return kept, dropped


# --- labels ------------------------------------------------------------------

LABELS: Final = ("committed", "discarded", "open", "none")
PROPOSAL_CLASS: Final = frozenset({"discarded", "open"})
KINDS: Final = ("amount", "date", "name", "choice", "other")


def validate_label(label: str) -> str:
    if label not in LABELS:
        raise ValueError(f"label {label!r} is not one of {', '.join(LABELS)}")
    return label


MIN_STAGE0_PER_CLASS: Final = 20
STAGE0_SENSITIVITY_FLOOR: Final = 0.85
STAGE0_SPECIFICITY_FLOOR: Final = 0.50


def stage0_gate(labels: Mapping[str, str], texts: Mapping[str, str]) -> dict[str, Any]:
    """The pre-run gate on the commitment cue (REGISTERED).

    Sensitivity is the share of `committed` lines that carry the cue;
    specificity the share of `discarded` or `open` lines that do not. `none`
    lines are ignored. PASS needs at least `MIN_STAGE0_PER_CLASS` lines of each
    class, sensitivity >= 0.85 and specificity >= 0.50; fewer lines is
    UNDERPOWERED (the guard is not run), a miss is FAIL."""
    committed = hits = proposal = rejected = 0
    for key, label in labels.items():
        if label == "committed":
            committed += 1
            hits += commitment_cue(texts[key])
        elif label in PROPOSAL_CLASS:
            proposal += 1
            rejected += not commitment_cue(texts[key])
    sensitivity = hits / committed if committed else None
    specificity = rejected / proposal if proposal else None
    if committed < MIN_STAGE0_PER_CLASS or proposal < MIN_STAGE0_PER_CLASS:
        verdict = "UNDERPOWERED"
    elif (
        sensitivity is not None
        and specificity is not None
        and sensitivity >= STAGE0_SENSITIVITY_FLOOR
        and specificity >= STAGE0_SPECIFICITY_FLOOR
    ):
        verdict = "PASS"
    else:
        verdict = "FAIL"
    return {
        "committed": committed,
        "proposal": proposal,
        "sensitivity": sensitivity,
        "specificity": specificity,
        "verdict": verdict,
    }


# --- stored runs -------------------------------------------------------------


def load_runs(doc: Mapping[str, Any]) -> list[dict[str, list[dict[str, Any]]]]:
    """Per-run `{meeting: [decision, ...]}` of a stored arm file.

    The file is `{"arm": str, "runs": [{"meetings": {meeting: {"decisions":
    [{"title", "description", "body"}]}}}]}`. An ineligible meeting is
    refused, so a stray held-out result cannot be scored by accident."""
    runs: list[dict[str, list[dict[str, Any]]]] = []
    for run in doc.get("runs", []):
        meetings: dict[str, list[dict[str, Any]]] = {}
        for meeting, cell in run["meetings"].items():
            require_eligible(meeting)
            meetings[meeting] = [dict(d) for d in cell["decisions"]]
        runs.append(meetings)
    return runs


# --- value gold --------------------------------------------------------------


@dataclass(frozen=True)
class ValueItem:
    item_id: str
    meeting: str
    turns: tuple[int, ...]
    kind: str
    forms: tuple[str, ...]
    distractors: tuple[str, ...]
    valued: bool
    """Whether the item has a checkable value; an excluded item still counts
    toward recall."""


def load_value_gold(doc: Mapping[str, Any]) -> list[ValueItem]:
    """Validated items of a value-gold document. Fail-closed: an ineligible
    meeting, an unknown kind, a valued item with no forms, a distractor equal
    to a form, or a repeated id is an error."""
    items: list[ValueItem] = []
    seen: set[str] = set()
    for raw in doc.get("items", []):
        item_id = str(raw["id"])
        if item_id in seen:
            raise ValueError(f"duplicate item id {item_id!r}")
        seen.add(item_id)
        meeting = require_eligible(str(raw["meeting"]))
        turns = tuple(int(t) for t in raw["turns"])
        if not turns:
            raise ValueError(f"{item_id}: no anchor turns")
        kind = str(raw["kind"])
        if kind not in KINDS:
            raise ValueError(f"{item_id}: kind {kind!r} is not one of {KINDS}")
        valued = not bool(raw.get("exclude", False))
        forms = tuple(str(f) for f in raw.get("forms", []))
        distractors = tuple(str(d) for d in raw.get("distractors", []))
        if valued and not any(tokens(f) for f in forms):
            raise ValueError(f"{item_id}: a valued item needs forms")
        form_set = {" ".join(tokens(f)) for f in forms}
        for distractor in distractors:
            if " ".join(tokens(distractor)) in form_set:
                raise ValueError(f"{item_id}: distractor {distractor!r} equals a form")
        items.append(
            ValueItem(item_id, meeting, turns, kind, forms, distractors, valued)
        )
    return items


# --- scoring one run ---------------------------------------------------------


def score_run(
    decisions_by_meeting: Mapping[str, Sequence[Mapping[str, Any]]],
    items: Sequence[ValueItem],
    anchors: Mapping[str, Mapping[int, str]],
    labels: Mapping[str, str],
    *,
    guard: bool = False,
) -> dict[str, Any]:
    """Score one run of one arm.

    `anchors[meeting][turn_index]` is the rendered turn text of each gold
    anchor. With `guard=True` the commitment guard is applied first, so a
    dropped Decision can neither recall an item nor be labelled."""
    kept_total = dropped_total = 0
    surviving: dict[str, list[Mapping[str, Any]]] = {}
    for meeting, decisions in decisions_by_meeting.items():
        require_eligible(meeting)
        if guard:
            kept, dropped = apply_guard(decisions)
        else:
            kept, dropped = list(decisions), []
        surviving[meeting] = list(kept)
        kept_total += len(kept)
        dropped_total += len(dropped)

    item_scores: dict[str, dict[str, Any]] = {}
    evidence_only = 0
    valued = correct = 0
    for item in items:
        matched: list[Mapping[str, Any]] = []
        for record in surviving.get(item.meeting, []):
            _, evidence = split_decision(record)
            if evidence and any(
                evidence_matches(evidence, anchors[item.meeting][turn])
                for turn in item.turns
                if turn in anchors.get(item.meeting, {})
            ):
                matched.append(record)
        verdict: str | None = None
        if matched and item.valued:
            verdict = "missing"
            for record in matched:
                statement, _ = split_decision(record)
                got = classify_value(statement, item.forms, item.distractors)
                if VERDICT_RANK[got] > VERDICT_RANK[verdict]:
                    verdict = got
            if verdict == "missing" and any(
                states(split_decision(r)[1], item.forms) for r in matched
            ):
                evidence_only += 1
        item_scores[item.item_id] = {"recalled": int(bool(matched)), "verdict": verdict}
        valued += item.valued
        correct += verdict == "correct"

    total = labelled = uncommitted = discarded = none = 0
    by_meeting: dict[str, dict[str, int]] = {}
    for meeting, decisions in surviving.items():
        cell = {"labelled": 0, "uncommitted": 0}
        for record in decisions:
            total += 1
            label = labels.get(line_key(split_decision(record)[1]))
            if label is None:
                continue
            labelled += 1
            cell["labelled"] += 1
            if label in PROPOSAL_CLASS:
                uncommitted += 1
                cell["uncommitted"] += 1
            discarded += label == "discarded"
            none += label == "none"
        by_meeting[meeting] = cell
    return {
        "items": item_scores,
        "evidence_only": evidence_only,
        "value_hits": {"valued": valued, "correct": correct},
        "decisions": {
            "total": total,
            "labelled": labelled,
            "unlabelled": total - labelled,
            "uncommitted": uncommitted,
            "discarded": discarded,
            "none": none,
        },
        "by_meeting": by_meeting,
        "guard": {"kept": kept_total, "dropped": dropped_total},
    }


# --- aggregation -------------------------------------------------------------

Clusters = dict[str, tuple[int, int]]


def seeded_rng(seed: int) -> random.Random:
    """The one place a generator is built: a seeded sampler for shuffles and
    resamples, never a secret."""
    return random.Random(seed)  # noqa: S311 -- seeded sampling, not cryptography


def item_clusters(scores: Sequence[Mapping[str, Any]], metric: str) -> Clusters:
    """`item -> (numerator, denominator)` pooled over runs.

    `recall`: recalled over runs. `value`: correct over runs in which the item
    was recalled and had a value verdict (items never in that state are left
    out, they carry no information)."""
    num: defaultdict[str, int] = defaultdict(int)
    den: defaultdict[str, int] = defaultdict(int)
    for score in scores:
        for item_id, entry in score["items"].items():
            if metric == "recall":
                num[item_id] += entry["recalled"]
                den[item_id] += 1
            elif metric == "value":
                if entry["verdict"] is not None:
                    num[item_id] += entry["verdict"] == "correct"
                    den[item_id] += 1
            else:
                raise ValueError(f"unknown metric {metric!r}")
    return {k: (num[k], den[k]) for k in den}


def meeting_clusters(scores: Sequence[Mapping[str, Any]]) -> Clusters:
    """`meeting -> (uncommitted, labelled)` Decisions pooled over runs."""
    num: defaultdict[str, int] = defaultdict(int)
    den: defaultdict[str, int] = defaultdict(int)
    for score in scores:
        for meeting, cell in score["by_meeting"].items():
            num[meeting] += cell["uncommitted"]
            den[meeting] += cell["labelled"]
    return {k: (num[k], den[k]) for k in den if den[k]}


def _ratio(num: int, den: int) -> float:
    return num / den


def ratio_bootstrap(
    treatment: Mapping[str, tuple[int, int]],
    baseline: Mapping[str, tuple[int, int]],
    *,
    seed: int,
    reps: int = 10000,
) -> dict[str, Any]:
    """Paired cluster bootstrap of `ratio(treatment) - ratio(baseline)`.

    The cluster is the unit (a gold item, or a meeting): clusters present in
    both arms are resampled with replacement and each arm's ratio of sums is
    recomputed. 95% percentile interval. Resamples whose either denominator is
    zero are skipped and counted."""
    keys = sorted(set(treatment) & set(baseline))
    base: dict[str, Any] = {
        "clusters": len(keys),
        "diff": None,
        "lo": None,
        "hi": None,
        "skipped": 0,
    }
    if not keys:
        return base
    t_num = sum(treatment[k][0] for k in keys)
    t_den = sum(treatment[k][1] for k in keys)
    b_num = sum(baseline[k][0] for k in keys)
    b_den = sum(baseline[k][1] for k in keys)
    if not t_den or not b_den:
        return base
    rng = seeded_rng(seed)
    diffs: list[float] = []
    skipped = 0
    for _ in range(reps):
        pick = [keys[rng.randrange(len(keys))] for _ in keys]
        tn = sum(treatment[k][0] for k in pick)
        td = sum(treatment[k][1] for k in pick)
        bn = sum(baseline[k][0] for k in pick)
        bd = sum(baseline[k][1] for k in pick)
        if not td or not bd:
            skipped += 1
            continue
        diffs.append(_ratio(tn, td) - _ratio(bn, bd))
    if not diffs:
        return base
    diffs.sort()
    lo = diffs[max(0, math.floor(0.025 * len(diffs)))]
    hi = diffs[min(len(diffs) - 1, math.ceil(0.975 * len(diffs)) - 1)]
    return {
        "clusters": len(keys),
        "diff": _ratio(t_num, t_den) - _ratio(b_num, b_den),
        "lo": lo,
        "hi": hi,
        "skipped": skipped,
    }


# --- the decision rule (REGISTERED) -----------------------------------------

RECALL_MARGIN: Final = -0.05
"""Non-inferiority on decision recall: the lower bound of
(treatment - baseline) must exceed this."""

VALUE_MIN_GAIN: Final = 0.10
"""Superiority on value fidelity: the lower bound must exceed 0 and the point
estimate must reach this."""

PROPOSAL_MIN_DROP: Final = -0.05
"""Superiority on the uncommitted rate: the upper bound must be below 0 and the
point estimate at or below this."""


def _recall_verdict(recall: Mapping[str, Any]) -> str:
    """`ok`, `lost` (clearly inferior) or `unknown` for the recall contrast."""
    if recall["lo"] is None:
        return "unknown"
    if recall["lo"] > RECALL_MARGIN:
        return "ok"
    if recall["hi"] < RECALL_MARGIN:
        return "lost"
    return "unknown"


def decide_prompt(*, recall: Mapping[str, Any], value: Mapping[str, Any]) -> str:
    """ADOPT, REJECT or INCONCLUSIVE for the prompt clause."""
    held = _recall_verdict(recall)
    if held == "lost":
        return "REJECT"
    if value["lo"] is None:
        return "INCONCLUSIVE"
    if held == "ok" and value["lo"] > 0 and value["diff"] >= VALUE_MIN_GAIN:
        return "ADOPT"
    if value["hi"] < VALUE_MIN_GAIN:
        return "REJECT"
    return "INCONCLUSIVE"


def decide_guard(
    *, gate: str, recall: Mapping[str, Any], uncommitted: Mapping[str, Any]
) -> str:
    """ADOPT, REJECT, INCONCLUSIVE, or NOT RUN when the stage-0 gate did not pass."""
    if gate != "PASS":
        return "NOT RUN"
    held = _recall_verdict(recall)
    if held == "lost":
        return "REJECT"
    if uncommitted["lo"] is None:
        return "INCONCLUSIVE"
    if (
        held == "ok"
        and uncommitted["hi"] < 0
        and uncommitted["diff"] <= PROPOSAL_MIN_DROP
    ):
        return "ADOPT"
    if uncommitted["lo"] > PROPOSAL_MIN_DROP:
        return "REJECT"
    return "INCONCLUSIVE"
