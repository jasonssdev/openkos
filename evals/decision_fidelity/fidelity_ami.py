"""Reading AMI for the Decision-fidelity measurement (#1649), and the blind
sheets built from it.

Everything is model-free and deterministic. The AMI archive and the rendered
transcripts stay outside the repository and are passed by path; nothing here
copies a transcript into a stored artifact except the blind sheets, which are
working files for the labeller and are not committed.

Three sheets, each blind to the arm that produced a line and to the outcome:

- the VALUE sheet: one item per AMI abstractive `decisions` sentence of a
  development meeting, with the dialogue turns `summlink` ties it to and their
  neighbours, for the labeller to write the surface forms of the decided value
  and the distractor values the same turns carry;
- the CUE sheet: turns of the calibration meetings, drawn from three strata
  that the labeller does not see, for labelling `committed`, `discarded`,
  `open` or `none`;
- the LINE sheet: the distinct evidence lines the arms actually produced, with
  context, for the same labels.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Final

import fidelity_core as core

_screen = core.load_sibling(
    "fidelity_screen_data",
    Path(__file__).resolve().parent.parent / "concept_model_screen" / "screen_data.py",
)

CONTEXT_RADIUS: Final = 2
"""Turns shown on each side of an anchor on the value sheet."""

LINE_CONTEXT_RADIUS: Final = 3
"""Turns shown on each side of an evidence line on the cue and line sheets."""

MIN_LINE_TOKENS: Final = 5
"""A cue-sheet turn shorter than this is backchannel, not a candidate."""

PROPOSAL_MARKERS: Final = re.compile(
    r"\b(?:what about|how about|maybe|perhaps|could we|we could|should we|"
    r"do we want|i think we should|what if|or we)\b",
    re.IGNORECASE,
)
"""Sampling stratum only. It decides which unlinked turns are drawn as likely
proposals; it is never a label and never reaches the scorer."""

VALUE_RUBRIC: Final = (
    "For each item, read the AMI summary sentence and the numbered turns it "
    "was written from (turns marked ANCHOR are the ones AMI linked to it; the "
    "others are context). Answer with one JSON object per item: "
    '{"id", "kind", "forms", "distractors", "exclude", "note"}. '
    "kind is amount, date, name, choice or other. forms are the surface "
    "spellings a Decision would use to state the DECIDED value, as spoken in "
    "the turns or as written in the summary (e.g. 12.50, twelve fifty, "
    "twelve euro fifty); give every spelling you can justify. distractors are "
    "OTHER values the same turns contain that a wrong Decision might state "
    "instead (a figure that is an actual cost when the decision is the cap, "
    "an option that was rejected). exclude is true when the sentence has no "
    "checkable value (state why in note). Use only this sheet."
)

LINE_RUBRIC: Final = (
    "For each item, read the line in its context and answer "
    '{"id", "label", "note"} with label one of: committed (the participants '
    "settle the choice, agree, or a requirement or constraint is stated and "
    "nobody contests it), discarded (the line names an option that the turns "
    "around it reject, withdraw or replace), open (an option is proposed, "
    "questioned or discussed and left unsettled in the turns shown), none "
    "(no choice is involved: chatter, a question about something else, an "
    "action item). Judge the line as the evidence for a stored decision. Use "
    "only this sheet."
)


# --- reading -----------------------------------------------------------------


def open_archive(path: Path, *, verify: bool = True) -> Any:
    return _screen.AmiArchive(path, verify=verify)


def load_turns(directory: Path, meeting: str) -> list[str]:
    """Rendered text of every turn of `meeting`, indexed by turn position."""
    core.require_eligible(meeting)
    transcript = _screen.load_transcript(directory, meeting)
    return [transcript.body[t.char_start : t.char_end] for t in transcript.turns]


def act_to_turn(directory: Path, meeting: str) -> dict[str, int]:
    """Dialogue-act id -> turn position."""
    core.require_eligible(meeting)
    transcript = _screen.load_transcript(directory, meeting)
    return {
        act: index
        for index, turn in enumerate(transcript.turns)
        for act in turn.dialogue_act_ids
    }


def load_anchors(directory: Path, meetings: Sequence[str]) -> dict[str, dict[int, str]]:
    """`meeting -> turn index -> turn text`, for the scorer's evidence match."""
    return {m: dict(enumerate(load_turns(directory, m))) for m in meetings}


def decision_sentences(
    archive: Any, directory: Path, meeting: str
) -> tuple[list[dict[str, Any]], list[str]]:
    """`(anchored, unanchored)` decision sentences of one meeting.

    An anchored sentence is `{"id", "text", "turns"}` with `turns` the sorted
    distinct turn positions of its linked dialogue acts. A sentence AMI marks
    `NA` is skipped; one with no resolvable linked act is unanchored (listed by
    id, never put on a sheet)."""
    core.require_eligible(meeting)
    sentences = [
        s
        for s in _screen.parse_abstractive(archive.abstractive(meeting))
        if s.section == "decisions" and s.text.strip(" .").upper() != "NA"
    ]
    link_bytes = archive.summlink(meeting)
    links = _screen.parse_summlink(link_bytes) if link_bytes is not None else {}
    to_turn = act_to_turn(directory, meeting)
    anchored: list[dict[str, Any]] = []
    unanchored: list[str] = []
    for sentence in sentences:
        turns = sorted(
            {to_turn[a] for a in links.get(sentence.sentence_id, ()) if a in to_turn}
        )
        if turns:
            anchored.append(
                {"id": sentence.sentence_id, "text": sentence.text, "turns": turns}
            )
        else:
            unanchored.append(sentence.sentence_id)
    return anchored, unanchored


def linked_turns(archive: Any, directory: Path, meeting: str) -> set[int]:
    """Turns tied by `summlink` to any `decisions` sentence."""
    anchored, _ = decision_sentences(archive, directory, meeting)
    return {t for s in anchored for t in s["turns"]}


def context_indices(anchors: Sequence[int], count: int, radius: int) -> list[int]:
    """Sorted, merged turn positions within `radius` of any anchor."""
    keep: set[int] = set()
    for anchor in anchors:
        keep.update(range(max(0, anchor - radius), min(count, anchor + radius + 1)))
    return sorted(keep)


def _context(
    turns: Sequence[str], anchors: Sequence[int], radius: int, mark: str = "anchor"
) -> list[dict[str, Any]]:
    marked = set(anchors)
    return [
        {"i": i, "text": turns[i], mark: i in marked}
        for i in context_indices(anchors, len(turns), radius)
    ]


# --- sheets ------------------------------------------------------------------


def value_sheet(
    archive: Any, directory: Path, meetings: Sequence[str], seed: int
) -> dict[str, Any]:
    """The value sheet over `meetings` (gold meetings only), shuffled by seed."""
    items: list[dict[str, Any]] = []
    unanchored: list[str] = []
    for meeting in meetings:
        if meeting not in core.GOLD_MEETINGS:
            raise core.MeetingRefused(f"{meeting!r} is not a gold meeting")
        turns = load_turns(directory, meeting)
        anchored, missing = decision_sentences(archive, directory, meeting)
        unanchored.extend(missing)
        for sentence in anchored:
            items.append(
                {
                    "id": sentence["id"],
                    "meeting": meeting,
                    "sentence": sentence["text"],
                    "turns": _context(turns, sentence["turns"], CONTEXT_RADIUS),
                }
            )
    core.seeded_rng(seed).shuffle(items)
    return {
        "kind": "value-sheet",
        "version": 1,
        "seed": seed,
        "rubric": VALUE_RUBRIC,
        "unanchored": unanchored,
        "items": items,
    }


STRATA: Final = ("linked", "proposal", "cued_unlinked")


def cue_sheet(
    archive: Any,
    directory: Path,
    meetings: Sequence[str],
    seed: int,
    per_stratum: int,
) -> tuple[dict[str, Any], dict[str, str]]:
    """`(sheet, strata)`: the blind sheet and, apart, each item's stratum."""
    pools: dict[str, list[tuple[str, int]]] = {s: [] for s in STRATA}
    texts: dict[str, list[str]] = {}
    for meeting in meetings:
        if meeting not in core.CUE_MEETINGS:
            raise core.MeetingRefused(f"{meeting!r} is not a calibration meeting")
        turns = load_turns(directory, meeting)
        texts[meeting] = turns
        linked = linked_turns(archive, directory, meeting)
        for index, text in enumerate(turns):
            if len(core.tokens(text)) < MIN_LINE_TOKENS:
                continue
            if index in linked:
                pools["linked"].append((meeting, index))
            elif PROPOSAL_MARKERS.search(text):
                pools["proposal"].append((meeting, index))
            elif core.commitment_cue(text):
                pools["cued_unlinked"].append((meeting, index))
    rng = core.seeded_rng(seed)
    picked: list[tuple[str, str, int]] = []
    for stratum in STRATA:
        pool = sorted(pools[stratum])
        rng.shuffle(pool)
        picked.extend((stratum, m, i) for m, i in pool[:per_stratum])
    rng.shuffle(picked)
    items = [
        {
            "id": f"{meeting}#{index}",
            "meeting": meeting,
            "text": texts[meeting][index],
            "turns": _context(texts[meeting], [index], LINE_CONTEXT_RADIUS, "line"),
        }
        for _, meeting, index in picked
    ]
    strata = {f"{m}#{i}": s for s, m, i in picked}
    sheet = {
        "kind": "cue-sheet",
        "version": 1,
        "seed": seed,
        "rubric": LINE_RUBRIC,
        "items": items,
    }
    return sheet, strata


def locate_line(turns: Sequence[str], line: str) -> int | None:
    """Position of the turn that contains `line` as a whole token run, or the
    best evidence-rule match, or `None`."""
    needle = core.tokens(line)
    if not needle:
        return None
    for index, text in enumerate(turns):
        hay = core.tokens(text)
        for start in range(len(hay) - len(needle) + 1):
            if hay[start : start + len(needle)] == needle:
                return index
    for index, text in enumerate(turns):
        if core.evidence_matches(line, text):
            return index
    return None


def line_sheet(
    runs: Sequence[Mapping[str, Sequence[Mapping[str, Any]]]],
    directory: Path,
    seed: int,
    turn_loader: Callable[[Path, str], list[str]] = load_turns,
) -> dict[str, Any]:
    """The sheet of distinct evidence lines across `runs` (all arms pooled)."""
    seen: dict[str, dict[str, Any]] = {}
    cache: dict[str, list[str]] = {}
    for run in runs:
        for meeting, decisions in run.items():
            core.require_eligible(meeting)
            for record in decisions:
                _, line = core.split_decision(record)
                if not line:
                    continue
                key = core.line_key(line)
                if key in seen:
                    continue
                turns = cache.setdefault(meeting, turn_loader(directory, meeting))
                where = locate_line(turns, line)
                seen[key] = {
                    "id": key,
                    "meeting": meeting,
                    "text": line,
                    "located": where is not None,
                    "turns": []
                    if where is None
                    else _context(turns, [where], LINE_CONTEXT_RADIUS, "line"),
                }
    items = sorted(seen.values(), key=lambda item: item["id"])
    core.seeded_rng(seed).shuffle(items)
    return {
        "kind": "line-sheet",
        "version": 1,
        "seed": seed,
        "rubric": LINE_RUBRIC,
        "items": items,
    }


# --- importing a filled sheet -----------------------------------------------


def _answers(
    filled: Sequence[Mapping[str, Any]], ids: Sequence[str]
) -> dict[str, Mapping[str, Any]]:
    answers: dict[str, Mapping[str, Any]] = {}
    for entry in filled:
        item_id = str(entry["id"])
        if item_id in answers:
            raise ValueError(f"item {item_id!r} is answered twice")
        answers[item_id] = entry
    unknown = sorted(set(answers) - set(ids))
    if unknown:
        raise ValueError(f"answers for ids not on the sheet: {unknown[:3]}")
    missing = sorted(set(ids) - set(answers))
    if missing:
        raise ValueError(
            f"{len(missing)} sheet items are unanswered, e.g. {missing[:3]}"
        )
    return answers


def import_value(
    sheet: Mapping[str, Any], filled: Sequence[Mapping[str, Any]], labeller: str
) -> dict[str, Any]:
    """The value-gold document from a filled value sheet (validated)."""
    ids = [item["id"] for item in sheet["items"]]
    answers = _answers(filled, ids)
    items: list[dict[str, Any]] = []
    for item in sorted(sheet["items"], key=lambda i: i["id"]):
        answer = answers[item["id"]]
        anchors = [t["i"] for t in item["turns"] if t.get("anchor")]
        items.append(
            {
                "id": item["id"],
                "meeting": item["meeting"],
                "turns": anchors,
                "kind": str(answer["kind"]),
                "forms": [str(f) for f in answer.get("forms", [])],
                "distractors": [str(d) for d in answer.get("distractors", [])],
                "exclude": bool(answer.get("exclude", False)),
                "note": str(answer.get("note", "")),
            }
        )
    doc = {
        "version": 1,
        "labeller": labeller,
        "seed": sheet["seed"],
        "items": items,
    }
    core.load_value_gold(doc)
    return doc


def import_labels(
    sheet: Mapping[str, Any], filled: Sequence[Mapping[str, Any]], labeller: str
) -> dict[str, Any]:
    """The label document from a filled cue or line sheet (validated)."""
    ids = [item["id"] for item in sheet["items"]]
    answers = _answers(filled, ids)
    labels = {i: core.validate_label(str(answers[i]["label"])) for i in sorted(ids)}
    return {"version": 1, "labeller": labeller, "seed": sheet["seed"], "labels": labels}


def render_markdown(sheet: Mapping[str, Any]) -> str:
    """A readable rendering of any sheet, for the labeller."""
    lines = [f"# {sheet['kind']} (seed {sheet['seed']})", "", sheet["rubric"], ""]
    for number, item in enumerate(sheet["items"], start=1):
        lines.append(f"## {number}. {item['id']}")
        if "sentence" in item:
            lines.append(f"AMI summary: {item['sentence']}")
        if "text" in item:
            lines.append(f"Line: {item['text']}")
        if item.get("located") is False:
            lines.append("(context not located)")
        for turn in item["turns"]:
            flag = (
                "ANCHOR" if turn.get("anchor") else ("LINE" if turn.get("line") else "")
            )
            lines.append(f"- [{turn['i']}] {flag} {turn['text']}".replace("  ", " "))
        lines.append("")
    return "\n".join(lines)


def dump(path: Path, document: Any) -> None:
    path.write_text(
        json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
