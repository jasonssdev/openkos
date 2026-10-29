"""Cap-exempt meeting Event, generation arm (#1689): place, recover, compose, decide.

The model-free replay (`evals/meeting_event`, PR #1693) found that appending a
kept meeting `Event` after the subjects loses it to the object cap in 153 of
231 English qualifying cases, and could not measure the Event's body. Read
`PREREGISTRATION.md` first: it fixes the arms, the bars and the stopping rules
before any model call.

Arms (the Event is selected by `evals/meeting_event`'s own rule, so only the
placement and the body are new):

- `a0`  the shipped pipeline: no meeting Event.
- `a2`  `a0` capped at six, plus at most ONE meeting `Event` placed OUTSIDE the
        cap (a seventh object), only when `a0` is non-empty.
- `e0`  the Event body as the model already wrote it, recovered from the
        stored chat cache (no model call).
- `e1`  the Event body composed by one bounded call per case, given the source
        and the titles of the retained subjects (the only model-bound step).

Subcommands:

- `compose`  model-bound. One call per selected case. Needs Ollama.
- `score`    model-free. Placement counts, body checks, the cases to
             adjudicate, and the futility flag.
- `decide`   model-free. The registered bars (needs `ADJUDICATION`).

Model-free check: `--self-test`.
"""

from __future__ import annotations

import argparse
import json
import math
import pathlib
import re
import sys
import time
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any, Final

HERE = pathlib.Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "evals"))
sys.path.insert(0, str(REPO_ROOT / "evals" / "concept_model_screen"))
sys.path.insert(0, str(REPO_ROOT / "evals" / "framing_drop_scope"))
sys.path.insert(0, str(REPO_ROOT / "evals" / "framing_floor"))
sys.path.insert(0, str(REPO_ROOT / "evals" / "meeting_event"))

import measure_framing_floor as mff  # noqa: E402
import measure_meeting_event as mme  # noqa: E402
from harness_stamp import build_stamp  # noqa: E402

from openkos.extraction import concept as concept_mod  # noqa: E402
from openkos.llm import parsing  # noqa: E402

Obj = dict[str, Any]

CAP: Final = mme.CAP
MODEL: Final = mff.MODEL
SOURCES: Final = mff.SOURCES
PHASES: Final = ("natural", "composed")

PER_SOURCE: Final = 6
"""Selected cases per source (first qualifying runs, in run order)."""
FIRST_STAGE_PER_SOURCE: Final = 2
"""Phase 1a: the cases read before the futility check."""
SEED_BASE: Final = 2000
"""Compose seed is `SEED_BASE + run`: above every generation seed (1 to 30)."""

# Registered bars (PREREGISTRATION.md, "Bars"). All proposed.
OVERLAP_N: Final = 6
OVERLAP_MAX: Final = 0.50
RESTATE_PASS_MIN: Final = 0.90
LINK_MIN: Final = 2
LINKED_PASS_MIN: Final = 0.80
DATES_PER: Final = 20
BODY_MIN_CHARS: Final = 80
BODY_MAX_CHARS: Final = 1500
BOUNDED_PASS_MIN: Final = 0.95
FAITHFUL_MIN: Final = 0.90
CALL_FAILURE_MAX: Final = 0.10
RECORD_FAILURE_MAX: Final = 0.15
RECOVERY_MIN: Final = 0.90
MIN_CASES: Final = 20

FAITHFUL: Final = "faithful"
UNFAITHFUL: Final = "unfaithful"
CONTESTED: Final = "contested"
LABELS: Final = (FAITHFUL, UNFAITHFUL, CONTESTED)

ADJUDICATION: Final[dict[tuple[str, str], tuple[str, str]]] = {
    ("composed", "ES2015a-es/r1"): (
        FAITHFUL,
        "participants, roles, date absence and produced objects all stated by the source",
    ),
    ("composed", "ES2015a-es/r5"): (
        FAITHFUL,
        "participants, roles, date absence and produced objects all stated by the source",
    ),
    ("composed", "ES2015a-es/r11"): (
        FAITHFUL,
        "participants, roles, date absence and produced objects all stated by the source",
    ),
    ("composed", "IS1004b-es/r1"): (
        FAITHFUL,
        "participants, roles, date absence and produced objects all stated by the source",
    ),
    ("composed", "TS3009c-es/r5"): (
        FAITHFUL,
        "participants, roles, date absence and produced objects all stated by the source",
    ),
    ("composed", "ES2015a-es/r12"): (
        UNFAITHFUL,
        "names unnamed 'other members'; the source has four speakers (PM, ID, UI, ME) only",
    ),
    ("composed", "ES2015a-es/r2"): (
        UNFAITHFUL,
        "names unnamed 'other members'; the source has four speakers (PM, ID, UI, ME) only",
    ),
    ("composed", "ES2015a-es/r8"): (
        UNFAITHFUL,
        "names unnamed 'other members'; the source has four speakers (PM, ID, UI, ME) only",
    ),
    ("composed", "ES2015d-es/r1"): (
        UNFAITHFUL,
        "names unnamed 'other members'; the source has four speakers (PM, ID, UI, ME) only",
    ),
    ("composed", "ES2015d-es/r2"): (
        UNFAITHFUL,
        "names unnamed 'other members'; the source has four speakers (PM, ID, UI, ME) only",
    ),
    ("composed", "ES2015d-es/r3"): (
        UNFAITHFUL,
        "names unnamed 'other members'; the source has four speakers (PM, ID, UI, ME) only",
    ),
    ("composed", "ES2015d-es/r4"): (
        UNFAITHFUL,
        "names unnamed 'other members'; the source has four speakers (PM, ID, UI, ME) only",
    ),
    ("composed", "ES2015d-es/r5"): (
        UNFAITHFUL,
        "names unnamed 'other members'; the source has four speakers (PM, ID, UI, ME) only",
    ),
    ("composed", "ES2015d-es/r6"): (
        UNFAITHFUL,
        "names unnamed 'other members'; the source has four speakers (PM, ID, UI, ME) only",
    ),
    ("composed", "IS1004b-es/r2"): (
        UNFAITHFUL,
        "gives role labels the source never uses (Product Manager / President / Executive Member / User of Interface); source has none",
    ),
    ("composed", "IS1004b-es/r3"): (
        UNFAITHFUL,
        "gives role labels the source never uses (Product Manager / President / Executive Member / User of Interface); source has none",
    ),
    ("composed", "IS1004b-es/r4"): (
        UNFAITHFUL,
        "gives role labels the source never uses (Product Manager / President / Executive Member / User of Interface); source has none",
    ),
    ("composed", "IS1004b-es/r6"): (
        UNFAITHFUL,
        "gives role labels the source never uses (Product Manager / President / Executive Member / User of Interface); source has none",
    ),
    ("composed", "IS1004d-es/r1"): (
        UNFAITHFUL,
        "names unnamed 'other members'; the source has four speakers (PM, ID, UI, ME) only",
    ),
    ("composed", "IS1004d-es/r2"): (
        UNFAITHFUL,
        "names unnamed 'other members'; the source has four speakers (PM, ID, UI, ME) only",
    ),
    ("composed", "IS1004d-es/r3"): (
        UNFAITHFUL,
        "names unnamed 'other members'; the source has four speakers (PM, ID, UI, ME) only",
    ),
    ("composed", "IS1004d-es/r4"): (
        UNFAITHFUL,
        "names unnamed 'other members'; the source has four speakers (PM, ID, UI, ME) only",
    ),
    ("composed", "IS1004d-es/r5"): (
        UNFAITHFUL,
        "names unnamed 'other members'; the source has four speakers (PM, ID, UI, ME) only",
    ),
    ("composed", "IS1004d-es/r6"): (
        UNFAITHFUL,
        "names unnamed 'other members'; the source has four speakers (PM, ID, UI, ME) only",
    ),
    ("composed", "TS3009c-es/r1"): (
        UNFAITHFUL,
        "names unnamed 'other members'; the source has four speakers (PM, ID, UI, ME) only",
    ),
    ("composed", "TS3009c-es/r2"): (
        UNFAITHFUL,
        "names unnamed 'other members'; the source has four speakers (PM, ID, UI, ME) only",
    ),
    ("composed", "TS3009c-es/r3"): (
        UNFAITHFUL,
        "names unnamed 'other members'; the source has four speakers (PM, ID, UI, ME) only",
    ),
    ("composed", "TS3009c-es/r6"): (
        UNFAITHFUL,
        "names unnamed 'other members'; the source has four speakers (PM, ID, UI, ME) only",
    ),
}
"""(phase, "source/rN") -> (label, reason) for every scored body. Filled by the
adjudicator (the owner, or a delegated reader disclosed in RESULTS) after
`score` listed the bodies and BEFORE `decide` is first run, reading the body,
description and the source only. A contested body counts against the arm."""

EVENT_COMPOSE_SYSTEM_PROMPT: Final = (
    "You write the record of ONE meeting for a knowledge base. The user turn "
    "gives the meeting's source text and the titles of the objects already "
    "extracted from it (its decisions, topics and tasks). Reply with ONE JSON "
    'object and nothing else: {"description": "...", "body": "..."}.\n\n'
    "Rules:\n"
    "- Write in the language of the source text.\n"
    "- description: one sentence saying what the meeting was and what it was "
    "about.\n"
    "- body: at most 1200 characters of plain prose. No transcript, no "
    "quotation. It says, in this order: when the meeting took place, only if "
    "the source states it (otherwise leave the date out; never guess or invent "
    "one); who took part, only by the names or role labels the source uses; "
    "and what the meeting produced, naming each listed object by its exact "
    "title.\n"
    "- Never copy sentences from the source. Never add a fact the source does "
    "not state.\n"
    "- Name an object only if it is in the list. Do not invent objects."
)
"""Frozen by the registration; tuning it after a reply is a new registration."""

_WORD_RE: Final = re.compile(r"\w+")
_MONTHS: Final = (
    "enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|"
    "octubre|noviembre|diciembre|january|february|march|april|may|june|july|"
    "august|september|october|november|december"
)
_DATE_RE: Final = re.compile(
    rf"\b(?:\d{{4}}-\d{{2}}-\d{{2}}"
    rf"|\d{{1,2}}[/.-]\d{{1,2}}[/.-]\d{{2,4}}"
    rf"|\d{{1,2}}\s+(?:de\s+)?(?:{_MONTHS})(?:\s+(?:de\s+)?(?:19|20)\d{{2}})?"
    rf"|(?:{_MONTHS})\s+\d{{1,2}}(?:,\s*(?:19|20)\d{{2}})?"
    rf"|(?:19|20)\d{{2}})\b",
    re.IGNORECASE,
)

# --- logic (pure) -------------------------------------------------------------


def place_cap_exempt(a0: Sequence[Obj], kept: Obj | None) -> list[Obj]:
    """`a2`: the cap-limited `a0`, plus the kept Event OUTSIDE the cap, only
    when `a0` is non-empty (a subject survived)."""
    head = list(a0[:CAP])
    if not a0 or kept is None:
        return head
    return [*head, kept]


def placement_flags(
    a0: Sequence[Obj], kept: Obj | None, placed: Sequence[Obj]
) -> dict[str, bool]:
    """What went wrong (or is worth counting) in one placement."""
    head = [mme.key(o) for o in a0[:CAP]]
    expected = 1 if a0 and kept is not None else 0
    return {
        "displaced": [mme.key(o) for o in placed[: len(head)]] != head,
        "bad_cardinality": len(placed) - len(head) != expected,
        "overflow": len(placed) > CAP + 1,
        "beyond_cap": expected == 1 and len(a0) >= CAP,
    }


_PLACEMENT_FIELDS: Final = (
    "records",
    "a0_nonempty",
    "qualifies",
    "displaced",
    "bad_cardinality",
    "overflow",
    "beyond_cap",
    "sole_object",
)


def summarize_placement(cases: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """Counts over replayed cases, each `{"a0": [...], "kept": obj | None}`."""
    n: Counter[str] = Counter()
    for case in cases:
        a0, kept = case["a0"], case["kept"]
        flags = placement_flags(a0, kept, place_cap_exempt(a0, kept))
        n["records"] += 1
        if a0:
            n["a0_nonempty"] += 1
        if a0 and kept is not None:
            n["qualifies"] += 1
            if len(a0) == 1:
                n["sole_object"] += 1
        for name, hit in flags.items():
            if hit:
                n[name] += 1
    return {name: n[name] for name in _PLACEMENT_FIELDS}


def ngram_overlap(body: str, source: str, *, n: int = OVERLAP_N) -> float:
    """Share of the body's word n-grams that occur verbatim in the source."""
    mine = _WORD_RE.findall(body.lower())
    theirs = _WORD_RE.findall(source.lower())
    if len(mine) < n:
        return 0.0
    seen = {tuple(theirs[i : i + n]) for i in range(len(theirs) - n + 1)}
    grams = [tuple(mine[i : i + n]) for i in range(len(mine) - n + 1)]
    return sum(1 for g in grams if g in seen) / len(grams)


def ungrounded_dates(body: str, source: str) -> list[str]:
    """Date-shaped tokens in the body that the source does not contain."""
    lowered = source.lower()
    return [
        m.group(0) for m in _DATE_RE.finditer(body) if m.group(0).lower() not in lowered
    ]


def links_named(body: str, titles: Iterable[str]) -> int:
    """How many distinct subject titles the body names, case-insensitively."""
    lowered = body.lower()
    return sum(1 for t in dict.fromkeys(titles) if t.strip() and t.lower() in lowered)


def case_checks(body: str, source: str, titles: Sequence[str]) -> dict[str, Any]:
    """The deterministic checks of one body against its source."""
    distinct = [t for t in dict.fromkeys(titles) if t.strip()]
    overlap = ngram_overlap(body, source)
    size = len(body.strip())
    return {
        "overlap": overlap,
        "restate_ok": overlap <= OVERLAP_MAX,
        "linked_ok": bool(distinct)
        and links_named(body, distinct) >= min(LINK_MIN, len(distinct)),
        "dates_ok": not ungrounded_dates(body, source),
        "bounded_ok": BODY_MIN_CHARS <= size <= BODY_MAX_CHARS,
    }


def recover_bodies(
    calls: Iterable[Mapping[str, Any]],
    cache_get: Callable[[str], Mapping[str, Any] | None],
) -> dict[tuple[str, str], str]:
    """(type, title) -> body, from the cached replies of a record's window
    calls. The first reply position wins; an unreadable reply adds nothing."""
    bodies: dict[tuple[str, str], str] = {}
    for call in calls:
        if call.get("kind") != "window" or call.get("outcome") != "ok":
            continue
        entry = cache_get(str(call.get("key")))
        content = entry.get("content") if entry else None
        if not isinstance(content, str):
            continue
        for item in parsing.extract_json_items(content):
            result = concept_mod._validate(item)
            if result is not None:
                bodies.setdefault((result.type, result.title), result.body)
    return bodies


def select_cases(
    population: Iterable[Mapping[str, Any]], per_source: int
) -> list[Mapping[str, Any]]:
    """The first `per_source` qualifying runs of each registered source, in run
    order. No other choice is made."""
    out: list[Mapping[str, Any]] = []
    for source in SOURCES:
        mine = sorted(
            (c for c in population if c["source"] == source and c["qualifies"]),
            key=lambda c: int(c["run"]),
        )
        out.extend(mine[:per_source])
    return out


def need_for(share: float, total: int) -> int:
    """The smallest count that reaches `share` of `total`."""
    return math.ceil(share * total - 1e-9)


def unreachable(passes: int, done: int, total: int, need: int) -> bool:
    """Even if every remaining case passed, `need` passes cannot be reached."""
    return passes + (total - done) < need


def date_allowance(scored: int) -> int:
    """Bodies with an invented date tolerated: one per twenty scored, at least one."""
    return max(1, scored // DATES_PER)


def summarize_bodies(
    cases: Iterable[Mapping[str, Any]], planned: int
) -> dict[str, Any]:
    """Counts over body cases `{"failed": bool, "checks": dict | None,
    "wall_s": float | None}`. A failed call has no checks; a case that is not
    failed and has no checks is unrecovered."""
    items = list(cases)
    scored = [c["checks"] for c in items if c["checks"] is not None]
    walls = [float(c["wall_s"]) for c in items if c.get("wall_s") is not None]
    return {
        "planned": planned,
        "scored": len(scored),
        "failed": sum(1 for c in items if c["failed"]),
        "unrecovered": sum(1 for c in items if not c["failed"] and c["checks"] is None),
        "restate_ok": sum(1 for c in scored if c["restate_ok"]),
        "linked_ok": sum(1 for c in scored if c["linked_ok"]),
        "dates_bad": sum(1 for c in scored if not c["dates_ok"]),
        "bounded_ok": sum(1 for c in scored if c["bounded_ok"]),
        "mean_overlap": (sum(c["overlap"] for c in scored) / len(scored))
        if scored
        else 0.0,
        "mean_wall_s": (sum(walls) / len(walls)) if walls else None,
    }


def futility(summary: Mapping[str, Any], total: int) -> bool:
    """After the first stage: some deterministic bar is out of reach however
    the remaining cases go. It can only end a run towards REJECTED."""
    done = int(summary["scored"]) + int(summary["failed"])
    if done == 0:
        return False
    return (
        unreachable(
            summary["restate_ok"], done, total, need_for(RESTATE_PASS_MIN, total)
        )
        or unreachable(
            summary["linked_ok"], done, total, need_for(LINKED_PASS_MIN, total)
        )
        or unreachable(
            summary["bounded_ok"], done, total, need_for(BOUNDED_PASS_MIN, total)
        )
        or unreachable(
            done - int(summary["dates_bad"]), done, total, total - date_allowance(total)
        )
    )


def faithful_count(phase: str, ids: Iterable[str]) -> int | None:
    """Bodies adjudicated faithful, or None while any body is unadjudicated."""
    count = 0
    for case_id in ids:
        entry = ADJUDICATION.get((phase, case_id))
        if entry is None:
            return None
        count += entry[0] == FAITHFUL
    return count


def decide(
    summary: Mapping[str, Any],
    placement: Mapping[str, Mapping[str, int]],
    *,
    phase: str,
    faithful: int | None,
    extraction_mean_s: float | None,
) -> dict[str, Any]:
    """The registered verdict for one phase. Never an adoption."""
    if phase not in PHASES:
        raise ValueError(f"phase {phase!r} not in {PHASES}")
    planned, scored = int(summary["planned"]), int(summary["scored"])
    placed = all(
        not p["displaced"] and not p["bad_cardinality"] and not p["overflow"]
        for p in placement.values()
    )
    if not placed:
        return {"verdict": "REJECTED", "failed": ["P1-P2 placement"], "bars": {}}
    if phase == "natural":
        inconclusive = scored < need_for(RECOVERY_MIN, planned)
    else:
        inconclusive = scored < MIN_CASES or int(summary["failed"]) > (
            RECORD_FAILURE_MAX * planned
        )
    if inconclusive:
        return {"verdict": "INCONCLUSIVE", "failed": [], "bars": {}}
    bars = {
        "E1 not a restatement": summary["restate_ok"]
        >= need_for(RESTATE_PASS_MIN, scored),
        "E2 linked to subjects": summary["linked_ok"]
        >= need_for(LINKED_PASS_MIN, scored),
        "E3 no invented date": summary["dates_bad"] <= date_allowance(scored),
        "E4 bounded": summary["bounded_ok"] >= need_for(BOUNDED_PASS_MIN, scored),
    }
    if phase == "composed":
        mean = summary["mean_wall_s"]
        bars["E6 cost"] = (
            int(summary["failed"]) <= math.floor(CALL_FAILURE_MAX * planned + 1e-9)
            and mean is not None
            and extraction_mean_s is not None
            and mean <= extraction_mean_s
        )
    failed = [name for name, ok in bars.items() if not ok]
    if faithful is None and not failed:
        # Only now could faithfulness change the verdict, so only now is it read.
        return {"verdict": "NEEDS-ADJUDICATION", "failed": [], "bars": bars}
    if faithful is not None:
        bars["E5 faithful"] = faithful >= need_for(FAITHFUL_MIN, scored)
        bars = dict(sorted(bars.items()))
        failed = [name for name, ok in bars.items() if not ok]
    if phase == "natural":
        verdict = "NATURAL-INSUFFICIENT" if failed else "NATURAL-SUFFICES"
    else:
        verdict = "REJECTED" if failed else "LICENSED"
    return {"verdict": verdict, "failed": failed, "bars": bars}


def build_compose_messages(
    source_text: str, event_title: str, subject_titles: Sequence[str]
) -> list[dict[str, str]]:
    """The frozen compose prompt: the Event's title is fixed by the selection
    rule; the model writes its description and body."""
    listed = "\n".join(f"- {t}" for t in dict.fromkeys(subject_titles))
    return [
        {"role": "system", "content": EVENT_COMPOSE_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"{concept_mod._LANGUAGE_ANCHOR}\n\nMEETING TITLE: {event_title}\n\n"
                f"EXTRACTED OBJECTS:\n{listed}\n\nSOURCE TEXT:\n{source_text}"
            ),
        },
    ]


def parse_compose_reply(reply: str | None) -> dict[str, str] | None:
    """`{"description", "body"}` from a reply, or None when it has no usable body."""
    if reply is None:
        return None
    obj = parsing.extract_json_object(reply)
    if not obj or not isinstance(obj.get("body"), str) or not obj["body"].strip():
        return None
    return {"description": str(obj.get("description", "")), "body": obj["body"]}


# --- records ------------------------------------------------------------------


def compose_path(
    out_dir: pathlib.Path, model: str, source: str, run: int
) -> pathlib.Path:
    return out_dir / "compose" / mff.safe(model) / source / f"r{run}.json"


def case_id(source: str, run: int) -> str:
    return f"{source}/r{run}"


def cache_entry(root: pathlib.Path, key: str) -> dict[str, Any] | None:
    path = root / key[:2] / f"{key}.json"
    if not path.is_file():
        return None
    loaded = json.loads(path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else None


def load_spanish(
    base_dir: pathlib.Path, translations_dir: pathlib.Path, model: str
) -> tuple[list[dict[str, Any]], int, dict[str, str], float]:
    """Replayed Spanish records (read-only), the failed count, the source texts
    and the mean wall-clock seconds of a full extraction run."""
    population: list[dict[str, Any]] = []
    texts: dict[str, str] = {}
    walls: list[float] = []
    failed = 0
    for source in SOURCES:
        text = mff.translation_text(translations_dir, source)
        texts[source] = text
        for path in sorted(
            (base_dir / "records" / mff.safe(model) / source).glob("r*.json")
        ):
            rec = json.loads(path.read_text(encoding="utf-8"))
            if rec.get("failure") or rec["stages"]["raw"] is None:
                failed += 1
                continue
            walls.append(float(rec["wall_s"]))
            title = str(rec["title"])
            out = mme.replay(
                rec["stages"]["raw"],
                source_text=text,
                source_title=title,
                framing_shaped=concept_mod._framing_shaped(title, text),
            )
            population.append(
                {
                    "source": source,
                    "run": int(rec["run"]),
                    "title": title,
                    "a0": out["a0"],
                    "kept": out["kept"],
                    "qualifies": bool(out["a0"]) and out["kept"] is not None,
                    "calls": rec.get("calls", []),
                }
            )
    return population, failed, texts, (sum(walls) / len(walls)) if walls else 0.0


def english_placement(
    runs_root: pathlib.Path, prose_sources: pathlib.Path
) -> list[dict[str, Any]]:
    return [
        {"a0": c["outputs"]["a0"], "kept": c["outputs"]["kept"]}
        for c in mme.english_cases(runs_root, prose_sources)
    ]


def body_cases(
    args: argparse.Namespace,
    phase: str,
    selected: Sequence[Mapping[str, Any]],
    texts: Mapping[str, str],
) -> list[dict[str, Any]]:
    """One body case per selected record that has been read: recovered from
    the chat cache (`natural`) or composed (`composed`; a case not yet
    composed is absent)."""
    cache_root = args.base_dir / "cache" / "chat"
    cases: list[dict[str, Any]] = []
    for c in selected:
        kept = c["kept"]
        titles = [str(o["title"]) for o in c["a0"][:CAP]]
        failed, wall, description = False, None, str(kept["description"])
        if phase == "natural":
            bodies = recover_bodies(c["calls"], lambda k: cache_entry(cache_root, k))
            body = bodies.get(mme.key(kept))
        else:
            path = compose_path(args.out_dir, args.model, c["source"], c["run"])
            if not path.is_file():
                continue
            rec = json.loads(path.read_text(encoding="utf-8"))
            parsed = (
                None if rec.get("failure") else parse_compose_reply(rec.get("reply"))
            )
            failed = parsed is None
            body = parsed["body"] if parsed else None
            description = parsed["description"] if parsed else ""
            wall = rec.get("wall_s")
        cases.append(
            {
                "id": case_id(c["source"], c["run"]),
                "title": str(kept["title"]),
                "description": description,
                "body": body,
                "failed": failed,
                "wall_s": wall,
                "checks": case_checks(body, texts[c["source"]], titles)
                if body
                else None,
            }
        )
    return cases


def _gather(args: argparse.Namespace) -> dict[str, Any]:
    population, failed, texts, mean_wall = load_spanish(
        args.base_dir, args.translations_dir, args.model
    )
    english = english_placement(args.runs_root, args.prose_sources)
    selected = select_cases(population, args.per_source)
    cases = body_cases(args, args.phase, selected, texts)
    total = len(select_cases(population, PER_SOURCE))
    summary = summarize_bodies(cases, planned=len(selected))
    return {
        "placement": {
            "spanish": summarize_placement(population),
            "english": summarize_placement(english),
        },
        "spanish_failed_records": failed,
        "extraction_mean_s": mean_wall,
        "full_total": total,
        "bodies": summary,
        "cases": cases,
    }


def cmd_score(args: argparse.Namespace) -> int:
    data = _gather(args)
    cases = data.pop("cases")
    data["futility_stop"] = args.phase == "composed" and futility(
        data["bodies"], data["full_total"]
    )
    data["to_adjudicate"] = [
        {k: c[k] for k in ("id", "title", "description", "body")}
        for c in cases
        if c["checks"] is not None and (args.phase, c["id"]) not in ADJUDICATION
    ]
    data["case_checks"] = {c["id"]: c["checks"] for c in cases}
    print(json.dumps(data, indent=2, ensure_ascii=False))
    return 0


def cmd_decide(args: argparse.Namespace) -> int:
    if args.per_source != PER_SOURCE:
        raise SystemExit(
            f"decide: the whole registered set only (--per-source {PER_SOURCE})"
        )
    data = _gather(args)
    cases = data.pop("cases")
    faithful = faithful_count(
        args.phase, [c["id"] for c in cases if c["checks"] is not None]
    )
    verdict = decide(
        data["bodies"],
        data["placement"],
        phase=args.phase,
        faithful=faithful,
        extraction_mean_s=data["extraction_mean_s"],
    )
    print(
        json.dumps(
            {"numbers": data, "faithful": faithful, "verdict": verdict},
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


def compose_one(
    source_text: str, event_title: str, subject_titles: Sequence[str], llm: Any
) -> dict[str, Any]:
    """One compose call. Never raises: a failure is the exception TYPE only."""
    overhead = sum(
        len(m["content"])
        for m in build_compose_messages("", event_title, subject_titles)
    )
    prompt_source, bounded = concept_mod._bounded_prompt_source(
        source_text,
        overhead_chars=overhead,
        llm=llm,
        generation_reserve_tokens=concept_mod._OPTIONAL_CALL_RESERVE_TOKENS,
    )
    try:
        reply = llm.chat(
            build_compose_messages(prompt_source, event_title, subject_titles)
        )
    except Exception as exc:
        return {"reply": None, "failure": type(exc).__name__, "bounded": bounded}
    return {"reply": reply, "failure": None, "bounded": bounded}


def cmd_compose(args: argparse.Namespace) -> int:
    import run_concept_screen as rcs
    import screen_pipeline as sp

    args.backend, args.models = "ollama", [args.model]
    timeouts = rcs.load_timeouts(args.screen_dir)
    digest = rcs.run_digest(args, args.model)
    stamp_path = args.out_dir / "compose" / mff.safe(args.model) / "stamp.json"
    stamp_path.parent.mkdir(parents=True, exist_ok=True)
    if not stamp_path.is_file():
        stamp = build_stamp(
            model=args.model,
            prompts={
                "event-compose-system": EVENT_COMPOSE_SYSTEM_PROMPT,
                "language-anchor": concept_mod._LANGUAGE_ANCHOR,
            },
            host=args.host,
        )
        stamp_path.write_text(json.dumps(stamp, indent=2), encoding="utf-8")
    population, _failed, texts, _mean = load_spanish(
        args.base_dir, args.translations_dir, args.model
    )
    cache = sp.ChatCache(args.out_dir / "cache" / "chat")
    warmed = False
    for c in select_cases(population, args.per_source):
        path = compose_path(args.out_dir, args.model, c["source"], c["run"])
        if path.is_file():
            continue
        if not warmed:
            print(f"warm-up {args.model}: {rcs.run_warm_up(args, args.model):.1f} s")
            warmed = True
        seed = SEED_BASE + c["run"]
        backend = sp.ScreenBackend(
            model=args.model,
            digest=digest,
            run=seed,
            window_timeout_s=timeouts[args.model],
            whole_timeout_s=rcs.WHOLE_SOURCE_TIMEOUT_MULTIPLE * timeouts[args.model],
            cache=cache,
            send=rcs.run_sender(args),
            windows=[],
        )
        started = time.monotonic()
        titles = [str(o["title"]) for o in c["a0"][:CAP]]
        outcome = compose_one(
            texts[c["source"]], str(c["kept"]["title"]), titles, backend
        )
        record = {
            **outcome,
            "model": args.model,
            "digest": digest,
            "source": c["source"],
            "run": c["run"],
            "seed": seed,
            "wall_s": time.monotonic() - started,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
        print(
            f"{time.strftime('%H:%M:%S')} {c['source']} r{c['run']}: "
            f"{record['wall_s']:.0f} s, failure {record['failure']}",
            flush=True,
        )
    return 0


# --- self-test ----------------------------------------------------------------

ES_TEXT = (
    "Reunión del 12 de mayo de 2005. PM: hoy decidimos el diseño del mando a "
    "distancia y el precio del producto para el mercado europeo. UI: el botón "
    "de encendido debe estar arriba, con un diseño sencillo y una carcasa "
    "resistente para los usuarios que no usan mucho la tecnología.\n"
)
SUBJECTS = ("Diseño del mando a distancia", "Precio del producto", "Carcasa resistente")


def _check(cond: bool, message: str) -> None:
    if not cond:
        raise AssertionError(message)


def _o(type_: str, title: str) -> Obj:
    return {"type": type_, "title": title, "description": "d"}


def _subjects(n: int) -> list[Obj]:
    return [_o("Concept", f"Tema {i}") for i in range(n)]


EVT = _o("Event", "Reunión de diseño")


def _t_event_goes_outside_the_cap() -> None:
    placed = place_cap_exempt(_subjects(8), EVT)
    _check(len(placed) == CAP + 1, f"len {len(placed)}")
    _check(placed[-1] == EVT, "the Event comes last")
    _check(
        [o["title"] for o in placed[:CAP]] == [f"Tema {i}" for i in range(CAP)], "head"
    )


def _t_event_follows_a_short_list() -> None:
    placed = place_cap_exempt(_subjects(2), EVT)
    _check(
        [o["title"] for o in placed] == ["Tema 0", "Tema 1", EVT["title"]], str(placed)
    )


def _t_no_subject_no_event() -> None:
    _check(place_cap_exempt([], EVT) == [], "an empty a0 keeps nothing")
    _check(len(place_cap_exempt(_subjects(3), None)) == 3, "no candidate adds nothing")


def _t_flags_detect_each_failure() -> None:
    a0 = _subjects(3)
    good = place_cap_exempt(a0, EVT)
    clean = placement_flags(a0, EVT, good)
    _check(not clean["displaced"] and not clean["bad_cardinality"], str(clean))
    _check(placement_flags(a0, EVT, [EVT, *a0])["displaced"], "event first displaces")
    _check(placement_flags(a0, EVT, a0)["bad_cardinality"], "missing event")
    _check(placement_flags(a0, EVT, [*good, EVT])["bad_cardinality"], "two events")
    _check(
        placement_flags(a0, None, [*a0, EVT])["bad_cardinality"], "event w/o candidate"
    )
    _check(
        placement_flags(a0, EVT, [*good, EVT])["overflow"] is False, "7 is not overflow"
    )
    _check(
        placement_flags(_subjects(6), EVT, [*_subjects(6), EVT, EVT])["overflow"], "8"
    )
    _check(
        placement_flags(_subjects(6), EVT, place_cap_exempt(_subjects(6), EVT))[
            "beyond_cap"
        ],
        "6",
    )
    _check(
        not placement_flags(_subjects(5), EVT, place_cap_exempt(_subjects(5), EVT))[
            "beyond_cap"
        ],
        "5",
    )


def _t_summarize_placement_counts() -> None:
    cases: list[dict[str, Any]] = [
        {"a0": _subjects(8), "kept": EVT},
        {"a0": _subjects(1), "kept": EVT},
        {"a0": [], "kept": EVT},
        {"a0": _subjects(2), "kept": None},
    ]
    s = summarize_placement(cases)
    _check(s["records"] == 4 and s["a0_nonempty"] == 3, str(s))
    _check(
        s["qualifies"] == 2 and s["beyond_cap"] == 1 and s["sole_object"] == 1, str(s)
    )
    _check(
        s["displaced"] == 0 and s["bad_cardinality"] == 0 and s["overflow"] == 0, str(s)
    )


def _t_overlap_is_verbatim_ngrams() -> None:
    _check(ngram_overlap(ES_TEXT, ES_TEXT) == 1.0, "a copy overlaps fully")
    _check(
        ngram_overlap("la reunión trató el precio y el diseño final", ES_TEXT) == 0.0,
        "paraphrase",
    )
    _check(ngram_overlap("muy corto", ES_TEXT) == 0.0, "shorter than n words")


def _t_dates_must_come_from_the_source() -> None:
    _check(ungrounded_dates("Fue el 12 de mayo de 2005.", ES_TEXT) == [], "grounded")
    _check(
        ungrounded_dates("Fue el 3 de junio de 2007.", ES_TEXT)
        == ["3 de junio de 2007"],
        "invented",
    )
    _check(ungrounded_dates("Sin fecha alguna.", ES_TEXT) == [], "no date, no problem")
    _check(ungrounded_dates("Año 1999.", ES_TEXT) == ["1999"], "bare year")
    _check(ungrounded_dates("On 2005-05-12.", ES_TEXT) == ["2005-05-12"], "iso")


def _t_links_name_subject_titles() -> None:
    body = "Se decidió el diseño del mando a distancia y el precio del producto."
    _check(links_named(body, SUBJECTS) == 2, "two named, case-insensitive")
    _check(links_named(body, [SUBJECTS[0], SUBJECTS[0]]) == 1, "distinct titles")
    _check(links_named("nada", SUBJECTS) == 0, "none")


def _t_case_checks_combine() -> None:
    body = (
        "Reunión de diseño con PM y UI. Se acordó el Diseño del mando a distancia "
        "y el Precio del producto, además de revisar los materiales finales "
        "propuestos por el equipo para la siguiente fase."
    )
    c = case_checks(body, ES_TEXT, SUBJECTS)
    _check(
        c["restate_ok"] and c["linked_ok"] and c["dates_ok"] and c["bounded_ok"], str(c)
    )
    copied = case_checks(ES_TEXT, ES_TEXT, SUBJECTS)
    _check(not copied["restate_ok"], "a copy is a restatement")
    _check(not case_checks("x" * 2000, ES_TEXT, SUBJECTS)["bounded_ok"], "too long")
    _check(not case_checks("corto", ES_TEXT, SUBJECTS)["bounded_ok"], "too short")
    _check(
        case_checks(body, ES_TEXT, ["Solo uno que no aparece"])["linked_ok"] is False,
        "unnamed",
    )
    _check(
        case_checks(body, ES_TEXT, [SUBJECTS[0]])["linked_ok"],
        "one subject needs one link",
    )
    _check(not case_checks(body, ES_TEXT, [])["linked_ok"], "no subject, no link")
    _check(
        not case_checks(body + " Fue el 1 de enero de 1999.", ES_TEXT, SUBJECTS)[
            "dates_ok"
        ],
        "date",
    )


def _reply(*items: tuple[str, str, str]) -> str:
    return json.dumps(
        [{"type": t, "title": ti, "description": "d", "body": b} for t, ti, b in items]
    )


def _getter(
    cache: Mapping[str, dict[str, Any]],
) -> Callable[[str], Mapping[str, Any] | None]:
    return lambda key: cache.get(key)


def _t_bodies_are_recovered_from_cached_replies() -> None:
    cache: dict[str, dict[str, Any]] = {
        "k0": {
            "content": _reply(
                ("Event", "Reunión A", "cuerpo A"), ("Concept", "Tema", "t")
            )
        },
        "k1": {"content": _reply(("Event", "Reunión A", "cuerpo duplicado"))},
        "k2": {"content": "not json at all"},
        "k4": {"content": None},
    }
    calls = [
        {"kind": "window", "outcome": "ok", "key": "k0"},
        {"kind": "window", "outcome": "ok", "key": "k1"},
        {"kind": "window", "outcome": "ok", "key": "k2"},
        {"kind": "window", "outcome": "timeout", "key": "k0"},
        {"kind": "window", "outcome": "ok", "key": "missing"},
        {"kind": "window", "outcome": "ok", "key": "k4"},
        {"kind": "judge", "outcome": "ok", "key": "k1"},
    ]
    bodies = recover_bodies(calls, _getter(cache))
    _check(bodies[("Event", "Reunión A")] == "cuerpo A", "first reply position wins")
    _check(bodies[("Concept", "Tema")] == "t" and len(bodies) == 2, str(bodies))
    _check(
        recover_bodies(
            [{"kind": "window", "outcome": "timeout", "key": "k0"}], _getter(cache)
        )
        == {},
        "",
    )


def _pop(source: str, run: int, qualifies: bool = True) -> dict[str, Any]:
    return {"source": source, "run": run, "qualifies": qualifies}


def _t_selection_is_first_qualifying_runs() -> None:
    pop = [_pop(SOURCES[0], r, qualifies=r != 2) for r in (10, 3, 2, 1, 4)]
    pop += [_pop(SOURCES[1], r) for r in (5, 1)] + [_pop("other", 1)]
    got = [(c["source"], c["run"]) for c in select_cases(pop, 2)]
    _check(
        got == [(SOURCES[0], 1), (SOURCES[0], 3), (SOURCES[1], 1), (SOURCES[1], 5)],
        str(got),
    )


def _t_reach_and_futility() -> None:
    _check(
        need_for(0.9, 30) == 27 and need_for(0.8, 30) == 24 and need_for(0.5, 10) == 5,
        "need",
    )
    _check(unreachable(3, 10, 30, 24) and not unreachable(4, 10, 30, 24), "boundary")
    _check(
        date_allowance(30) == 1 and date_allowance(60) == 3 and date_allowance(5) == 1,
        "dates",
    )
    ok = {
        "scored": 10,
        "failed": 0,
        "restate_ok": 10,
        "linked_ok": 10,
        "bounded_ok": 10,
        "dates_bad": 0,
    }
    _check(not futility(ok, 30), "all passing is not futile")
    _check(futility({**ok, "linked_ok": 3}, 30), "linked cannot reach 24")
    _check(not futility({**ok, "linked_ok": 4}, 30), "linked can still reach 24")
    _check(futility({**ok, "restate_ok": 6}, 30), "restate cannot reach 27")
    _check(
        futility({**ok, "dates_bad": 2}, 30),
        "two invented dates exceed the allowance of one",
    )
    _check(not futility({**ok, "dates_bad": 1}, 30), "one is allowed")
    _check(
        futility(
            {
                **ok,
                "scored": 6,
                "failed": 4,
                "restate_ok": 6,
                "linked_ok": 6,
                "bounded_ok": 6,
            },
            30,
        ),
        "failed calls do not pass",
    )
    _check(
        not futility({**ok, "scored": 0, "failed": 0, "restate_ok": 0}, 30),
        "nothing read yet",
    )


def _case(
    checks: dict[str, Any] | None, *, failed: bool = False, wall: float | None = 10.0
) -> dict[str, Any]:
    return {"checks": checks, "failed": failed, "wall_s": wall}


_GOOD: Final = {
    "overlap": 0.1,
    "restate_ok": True,
    "linked_ok": True,
    "dates_ok": True,
    "bounded_ok": True,
}


def _t_summarize_bodies_separates_failed_from_unrecovered() -> None:
    cases = [
        _case(_GOOD),
        _case({**_GOOD, "restate_ok": False, "overlap": 0.9, "dates_ok": False}),
        _case(None, failed=True, wall=5.0),
        _case(None, wall=None),
    ]
    s = summarize_bodies(cases, planned=4)
    _check(s["scored"] == 2 and s["failed"] == 1 and s["unrecovered"] == 1, str(s))
    _check(s["restate_ok"] == 1 and s["dates_bad"] == 1 and s["linked_ok"] == 2, str(s))
    _check(
        abs(s["mean_overlap"] - 0.5) < 1e-9 and abs(s["mean_wall_s"] - 25 / 3) < 1e-9,
        str(s),
    )


def _summary(**over: Any) -> dict[str, Any]:
    base = {
        "planned": 30,
        "scored": 30,
        "failed": 0,
        "unrecovered": 0,
        "restate_ok": 30,
        "linked_ok": 30,
        "dates_bad": 0,
        "bounded_ok": 30,
        "mean_overlap": 0.1,
        "mean_wall_s": 60.0,
    }
    return {**base, **over}


CLEAN: Final = {"displaced": 0, "bad_cardinality": 0, "overflow": 0}


def _decide(
    phase: str,
    summary: dict[str, Any],
    *,
    faithful: int | None = 30,
    placement: Any = None,
    mean: float | None = 379.0,
) -> dict[str, Any]:
    p = placement if placement is not None else {"spanish": CLEAN, "english": CLEAN}
    return decide(summary, p, phase=phase, faithful=faithful, extraction_mean_s=mean)


def _t_decide_composed_bars() -> None:
    _check(_decide("composed", _summary())["verdict"] == "LICENSED", "all hold")
    for field, value, bar in (
        ("restate_ok", 26, "E1"),
        ("linked_ok", 23, "E2"),
        ("dates_bad", 2, "E3"),
        ("bounded_ok", 28, "E4"),
    ):
        v = _decide("composed", _summary(**{field: value}))
        _check(
            v["verdict"] == "REJECTED" and v["failed"][0].startswith(bar),
            f"{field}: {v}",
        )
    edge = _decide(
        "composed", _summary(restate_ok=27, linked_ok=24, dates_bad=1, bounded_ok=29)
    )
    _check(edge["verdict"] == "LICENSED", f"thresholds are inclusive: {edge}")
    v = _decide("composed", _summary(), faithful=26)
    _check(v["failed"] == ["E5 faithful"], str(v))
    _check(
        _decide("composed", _summary(), faithful=27)["verdict"] == "LICENSED",
        "27 of 30",
    )


def _t_decide_cost_bar() -> None:
    _check(
        _decide(
            "composed",
            _summary(failed=3, scored=27, restate_ok=27, linked_ok=27, bounded_ok=27),
        )["verdict"]
        == "LICENSED",
        "3 of 30 is 0.10",
    )
    _check(
        _decide("composed", _summary(mean_wall_s=400.0))["failed"] == ["E6 cost"],
        "slower than extraction",
    )
    _check(
        _decide("composed", _summary(), mean=None)["failed"] == ["E6 cost"],
        "no reference, no pass",
    )
    _check(
        "E6 cost" not in _decide("natural", _summary(mean_wall_s=None))["bars"],
        "no cost bar for the cache replay",
    )


def _t_decide_inconclusive_floors() -> None:
    _check(
        _decide("composed", _summary(scored=19, planned=30, failed=2))["verdict"]
        == "INCONCLUSIVE",
        "fewer than 20 scored",
    )
    _check(
        _decide("composed", _summary(scored=25, failed=5))["verdict"] == "INCONCLUSIVE",
        "more than 15% failed",
    )
    _check(
        _decide("natural", _summary(scored=26))["verdict"] == "INCONCLUSIVE",
        "under 90% recovered",
    )
    _check(
        _decide(
            "natural",
            _summary(scored=27, restate_ok=27, linked_ok=27, bounded_ok=27),
            faithful=27,
        )["verdict"]
        == "NATURAL-SUFFICES",
        "27 of 30 recovered",
    )


def _t_decide_natural_and_placement() -> None:
    v = _decide("natural", _summary(restate_ok=5))
    _check(
        v["verdict"] == "NATURAL-INSUFFICIENT" and v["failed"][0].startswith("E1"),
        str(v),
    )
    for bad in ("displaced", "bad_cardinality", "overflow"):
        p = {"spanish": CLEAN, "english": {**CLEAN, bad: 1}}
        _check(
            _decide("composed", _summary(), placement=p)["verdict"] == "REJECTED", bad
        )
        _check(
            _decide("natural", _summary(scored=0), placement=p)["verdict"]
            == "REJECTED",
            f"{bad} before floors",
        )
    try:
        _decide("typo", _summary())
    except ValueError:
        return
    raise AssertionError("an unknown phase must be refused")


def _t_faithful_is_unknown_until_adjudicated() -> None:
    saved = dict(ADJUDICATION)
    try:
        ADJUDICATION[("natural", "A/r1")] = (FAITHFUL, "x")
        ADJUDICATION[("natural", "A/r2")] = (CONTESTED, "x")
        _check(
            faithful_count("natural", ["A/r1", "A/r2"]) == 1, "contested counts against"
        )
        _check(
            faithful_count("natural", ["A/r1", "A/r9"]) is None,
            "one unread body: unknown",
        )
        _check(
            faithful_count("composed", ["A/r1"]) is None, "the phase is part of the key"
        )
    finally:
        ADJUDICATION.clear()
        ADJUDICATION.update(saved)


def _t_decide_needs_adjudication_only_when_it_could_matter() -> None:
    v = _decide("natural", _summary(restate_ok=5), faithful=None)
    _check(
        v["verdict"] == "NATURAL-INSUFFICIENT" and "E5 faithful" not in v["bars"],
        str(v),
    )
    held = _decide("natural", _summary(), faithful=None)
    _check(held["verdict"] == "NEEDS-ADJUDICATION", str(held))
    held = _decide("composed", _summary(), faithful=None)
    _check(held["verdict"] == "NEEDS-ADJUDICATION", str(held))
    v = _decide("composed", _summary(linked_ok=3), faithful=None)
    _check(v["verdict"] == "REJECTED" and v["failed"][0].startswith("E2"), str(v))


def _t_compose_messages_are_frozen_and_anchored() -> None:
    msgs = build_compose_messages("FUENTE", "Reunión X", ["A", "B", "A"])
    _check(
        msgs[0]["content"] == EVENT_COMPOSE_SYSTEM_PROMPT,
        "system prompt is the frozen one",
    )
    user = msgs[1]["content"]
    _check(user.startswith(concept_mod._LANGUAGE_ANCHOR), "language anchor first")
    _check(
        "MEETING TITLE: Reunión X" in user and user.count("- A") == 1 and "- B" in user,
        user,
    )
    _check(user.endswith("SOURCE TEXT:\nFUENTE"), "source last")
    _check("never guess or invent" in EVENT_COMPOSE_SYSTEM_PROMPT, "date rule present")


def _t_parse_compose_reply() -> None:
    ok = parse_compose_reply('{"description": "d", "body": "texto"}')
    _check(ok == {"description": "d", "body": "texto"}, str(ok))
    _check(parse_compose_reply(None) is None, "no reply")
    _check(parse_compose_reply('{"description": "d"}') is None, "no body")
    _check(parse_compose_reply('{"body": "   "}') is None, "blank body")
    _check(parse_compose_reply("sin json") is None, "not json")


def _t_compose_one_degrades_and_records() -> None:
    class Boom:
        context_window = 12288

        def chat(self, messages: Any) -> str:
            raise RuntimeError("down")

    out = compose_one("fuente corta", "Reunión", ["A"], Boom())
    _check(out["reply"] is None and out["failure"] == "RuntimeError", str(out))


def _t_constants_and_registry() -> None:
    _check(CAP == concept_mod._MAX_OBJECTS_PER_SOURCE, "the cap mirrors production")
    _check(set(SOURCES) == set(mff.SOURCES), "development series only")
    _check(SEED_BASE > 30 and PER_SOURCE >= FIRST_STAGE_PER_SOURCE, "seeds and stages")
    _check(PER_SOURCE * len(SOURCES) >= MIN_CASES, "the floor is reachable")
    for entry_key, (label, reason) in ADJUDICATION.items():
        _check(
            label in LABELS and bool(reason) and entry_key[0] in PHASES, str(entry_key)
        )
    try:
        mff.translation_text(pathlib.Path(), "ES2008a-es")
    except SystemExit:
        return
    raise AssertionError("an unregistered source must be refused")


def self_test() -> int:
    tests: list[Callable[[], None]] = [
        _t_event_goes_outside_the_cap,
        _t_event_follows_a_short_list,
        _t_no_subject_no_event,
        _t_flags_detect_each_failure,
        _t_summarize_placement_counts,
        _t_overlap_is_verbatim_ngrams,
        _t_dates_must_come_from_the_source,
        _t_links_name_subject_titles,
        _t_case_checks_combine,
        _t_bodies_are_recovered_from_cached_replies,
        _t_selection_is_first_qualifying_runs,
        _t_reach_and_futility,
        _t_summarize_bodies_separates_failed_from_unrecovered,
        _t_decide_composed_bars,
        _t_decide_cost_bar,
        _t_decide_inconclusive_floors,
        _t_decide_natural_and_placement,
        _t_faithful_is_unknown_until_adjudicated,
        _t_decide_needs_adjudication_only_when_it_could_matter,
        _t_compose_messages_are_frozen_and_anchored,
        _t_parse_compose_reply,
        _t_compose_one_degrades_and_records,
        _t_constants_and_registry,
    ]
    failed = 0
    for test in tests:
        try:
            test()
            print(f"ok   {test.__name__}")
        except (AssertionError, NotImplementedError) as exc:
            failed += 1
            print(f"FAIL {test.__name__}: {exc!r}")
    print(f"self-test: {len(tests) - failed} of {len(tests)} passed")
    return 1 if failed else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--self-test", action="store_true", help="model-free checks")
    home = pathlib.Path.home()
    runs_root = home / "Dev/Projects/openkos-eval-runs"
    parser.add_argument(
        "--out-dir", type=pathlib.Path, default=runs_root / "meeting-event-body"
    )
    parser.add_argument(
        "--base-dir",
        type=pathlib.Path,
        default=runs_root / "framing-floor",
        help="the framing-floor records and chat cache, read-only",
    )
    parser.add_argument(
        "--translations-dir",
        type=pathlib.Path,
        default=home / "openkos-eval-runs/retrieval-gold/translations",
    )
    parser.add_argument("--runs-root", type=pathlib.Path, default=runs_root)
    parser.add_argument(
        "--prose-sources",
        type=pathlib.Path,
        default=home / "Dev/Projects/openkos/examples/extraction-corpus/sources",
    )
    parser.add_argument("--model", default=MODEL)
    sub = parser.add_subparsers(dest="command")
    compose = sub.add_parser("compose", help="model-bound: one compose call per case")
    compose.add_argument("--per-source", type=int, default=PER_SOURCE)
    compose.add_argument("--host", default=None)
    compose.add_argument(
        "--screen-dir",
        type=pathlib.Path,
        default=runs_root / "concept-screen",
        help="holds stage0.json, the registered per-call timeouts",
    )
    for name in ("score", "decide"):
        p = sub.add_parser(name)
        p.add_argument("--phase", choices=PHASES, required=True)
        p.add_argument("--per-source", type=int, default=PER_SOURCE)
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    commands = {"compose": cmd_compose, "score": cmd_score, "decide": cmd_decide}
    if args.command not in commands:
        raise SystemExit("choose a subcommand, or --self-test")
    return commands[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
