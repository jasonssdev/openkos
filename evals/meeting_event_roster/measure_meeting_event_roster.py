"""Roster-grounded meeting Event body (#1689, second arm): replay, compose, decide.

The first body measurement (`evals/meeting_event_body`) was REJECTED on E5: the
composed bodies named participants the source lacks. Read `PREREGISTRATION.md`
first. This harness reuses that one (cases, deterministic checks, bars) and adds:

- `roster_of`           the speaker labels a source declares.
- `absent_participants` the participants a body names that the source lacks
                        (the model-free check, also the gate of arm `r1`).
- `replay`              the check against the 28 recorded bodies of the last arm
                        and their recorded adjudication (no model, no new label).
- `compose`             model-bound: one call per case, the roster in the prompt.
- `score` / `decide`    model-free; arm `r0` (prompt only) and `r1` (prompt plus
                        the check as a gate).

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
for sub in (
    "src",
    "evals",
    "evals/concept_model_screen",
    "evals/framing_drop_scope",
    "evals/framing_floor",
    "evals/meeting_event",
    "evals/meeting_event_body",
):
    sys.path.insert(0, str(REPO_ROOT / sub))

import measure_framing_floor as mff  # noqa: E402
import measure_meeting_event_body as mmb  # noqa: E402
from harness_stamp import build_stamp  # noqa: E402

from openkos.extraction import concept as concept_mod  # noqa: E402

ARMS: Final = ("r0", "r1")
SEED_BASE: Final = 5000
"""Fresh against the last run (3000 + run, retry slots 4000 + run, never used):
attempt k of a call is sent with `SEED_BASE + run + k * RETRY_SEED_STRIDE`."""
ROSTER_MIN_TURNS: Final = 2
REPLAY_CATCH_MIN: Final = 0.90
REPLAY_FALSE_REJECT_MAX: Final = 1
YIELD_MIN: Final = 20

_SPEAKER_RE: Final = re.compile(r"^([A-Z][A-Z0-9]{1,3}):[ \t]", re.MULTILINE)
_SENTENCE_RE: Final = re.compile(r"(?<=[.!?])\s+")
_PARTICIPATION_RE: Final = re.compile(
    r"\b(?:participaron|participó|participaban|asistieron|asistió|took part|"
    r"attended|participated)\b",
    re.IGNORECASE,
)
_PAREN_RE: Final = re.compile(r"[()]")
_LIST_SPLIT_RE: Final = re.compile(r",|;|\s+y\s+|\s+e\s+|\s+and\s+|\s*&\s*")
_ARTICLE_RE: Final = re.compile(r"^(?:el|la|los|las|un|una|the|a|an)\s+", re.IGNORECASE)

# --- logic (pure) -------------------------------------------------------------


def roster_of(source: str) -> tuple[str, ...]:
    """The speaker labels a source declares: `LABEL:` opening a line (two to
    four capitals or digits), on at least two lines, in order of first
    appearance. A label seen once is a stray, not a speaker."""
    turns = Counter(_SPEAKER_RE.findall(source))
    return tuple(label for label, n in turns.items() if n >= ROSTER_MIN_TURNS)


def participant_items(body: str) -> list[str]:
    """What a body lists as taking part: in each sentence with a participation
    VERB (never the noun 'participantes'), the enumeration after the verb,
    split on commas, semicolons, parentheses and 'y' / 'e' / 'and' / '&', with
    the leading article removed."""
    items: list[str] = []
    for sentence in _SENTENCE_RE.split(body):
        verb = _PARTICIPATION_RE.search(sentence)
        if verb is None:
            continue
        rest = _PAREN_RE.sub(",", sentence[verb.end() :].strip(" .!?:"))
        for part in _LIST_SPLIT_RE.split(rest):
            item = _ARTICLE_RE.sub("", part.strip()).strip()
            if item:
                items.append(item)
    return items


def absent_participants(body: str, source: str) -> list[str]:
    """The listed participants that the source does not contain: not a
    whole-word, case-insensitive substring of it. A roster label occurs in the
    source by construction (it opens the speaker's lines), so it needs no case
    of its own."""
    lowered = source.casefold()
    return [
        item
        for item in participant_items(body)
        if re.search(rf"(?<!\w){re.escape(item.casefold())}(?!\w)", lowered) is None
    ]


EVENT_ROSTER_SYSTEM_PROMPT: Final = (
    "You write the record of ONE meeting for a knowledge base. The user turn "
    "gives the meeting's source text, the complete list of speaker labels that "
    "occur in it, and the titles of the objects already extracted from it (its "
    "decisions, topics and tasks). Reply with ONE JSON object and nothing "
    'else: {"description": "...", "body": "..."}.\n\n'
    "Rules:\n"
    "- Write in the language of the source text.\n"
    "- description: one sentence saying what the meeting was and what it was "
    "about. Name no participant in it.\n"
    "- body: at most 1200 characters of plain prose. No transcript, no "
    "quotation. It says, in this order: when the meeting took place, only if "
    "the source states it (otherwise leave the date out; never guess or invent "
    "one); who took part; and what the meeting produced, naming each listed "
    "object by its exact title.\n"
    "- Who took part: the SPEAKERS list is complete. Name participants only by "
    "the labels in that list, exactly as listed; a name the source itself "
    "gives for a speaker may follow its label in parentheses. Do not expand a "
    'label into a job title or role, and do not add anyone else: no "other '
    'members", no "the team", no group or role that is not in the list. If '
    "the list is empty, do not say who took part.\n"
    "- Never copy sentences from the source. Never add a fact the source does "
    "not state.\n"
    "- Name an object only if it is in the list. Do not invent objects."
)
"""Frozen by the registration; tuning it after a reply is a new registration."""


def build_roster_messages(
    source_text: str,
    event_title: str,
    subject_titles: Sequence[str],
    roster: Sequence[str],
) -> list[dict[str, str]]:
    """The frozen compose prompt with the source's speaker roster handed over."""
    listed = "\n".join(f"- {t}" for t in dict.fromkeys(subject_titles))
    speakers = ", ".join(roster) if roster else "none labelled"
    return [
        {"role": "system", "content": EVENT_ROSTER_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"{concept_mod._LANGUAGE_ANCHOR}\n\nMEETING TITLE: {event_title}\n\n"
                f"SPEAKERS IN THE SOURCE (complete list): {speakers}\n\n"
                f"EXTRACTED OBJECTS:\n{listed}\n\nSOURCE TEXT:\n{source_text}"
            ),
        },
    ]


def replay_counts(rows: Iterable[tuple[str, Sequence[str]]]) -> dict[str, int]:
    """The check against already-labelled bodies: `(label, absent participants)`.
    `caught` = unfaithful and flagged; `wrongly_rejected` = faithful and flagged."""
    items = list(rows)
    unfaithful = [a for label, a in items if label == mmb.UNFAITHFUL]
    faithful = [a for label, a in items if label == mmb.FAITHFUL]
    return {
        "bodies": len(items),
        "unfaithful": len(unfaithful),
        "faithful": len(faithful),
        "caught": sum(1 for a in unfaithful if a),
        "missed": sum(1 for a in unfaithful if not a),
        "wrongly_rejected": sum(1 for a in faithful if a),
    }


def check_verdict(counts: Mapping[str, int]) -> str:
    """CHECK-SOUND when it catches at least 0.90 of the unfaithful bodies and
    wrongly rejects at most one faithful body."""
    if counts["unfaithful"] == 0:
        return "INCONCLUSIVE"
    sound = (
        counts["caught"] >= mmb.need_for(REPLAY_CATCH_MIN, counts["unfaithful"])
        and counts["wrongly_rejected"] <= REPLAY_FALSE_REJECT_MAX
    )
    return "CHECK-SOUND" if sound else "CHECK-UNSOUND"


def survives(case: Mapping[str, Any]) -> bool:
    """The gate of arm `r1`: a body exists and names no absent participant."""
    return case["checks"] is not None and not case["absent"]


def decide_arms(
    cases: Sequence[Mapping[str, Any]],
    planned: int,
    placement: Mapping[str, Mapping[str, int]],
    extraction_mean_s: float | None,
    faithful_of: Callable[[Sequence[str]], int | None],
) -> dict[str, dict[str, Any]]:
    """`r0` (the prompt alone, judged on every scored body) and `r1` (the same
    bodies behind the participant check, judged on the survivors, plus E7)."""
    full = mmb.summarize_bodies(cases, planned)
    r0 = mmb.decide(
        full,
        placement,
        phase="composed",
        faithful=faithful_of([c["id"] for c in cases if c["checks"] is not None]),
        extraction_mean_s=extraction_mean_s,
    )
    kept = [c for c in cases if survives(c)]
    if r0["verdict"] == "INCONCLUSIVE" or r0["failed"] == ["P1-P2 placement"]:
        return {"r0": r0, "r1": r0}
    if len(kept) < YIELD_MIN:
        return {
            "r0": r0,
            "r1": {"verdict": "REJECTED", "failed": ["E7 yield"], "bars": {}},
        }
    summary = {
        **mmb.summarize_bodies(
            [c for c in cases if c["failed"] or survives(c)], planned
        ),
        "mean_wall_s": full["mean_wall_s"],
    }
    r1 = mmb.decide(
        summary,
        placement,
        phase="composed",
        faithful=faithful_of([c["id"] for c in kept]),
        extraction_mean_s=extraction_mean_s,
    )
    return {"r0": r0, "r1": r1}


def stop_reading(summary: Mapping[str, Any], total: int) -> dict[str, Any]:
    """The futility stop over the SAME denominators as the bars it guards.

    E1, E2, E4 are shares of the SCORED bodies and E3 an allowance on them, so
    a failed call changes them only by leaving a case unscored; failed calls
    count against E6 (at most 10% of planned) and the INCONCLUSIVE floors
    (20 scored bodies, at most 15% failed). Every way the `remaining` cases can
    still end is tried: `j` of them scored (all passing, the best case for a
    bar) and the rest failed. Only the endings that clear both floors can end
    in a verdict. No such ending: INCONCLUSIVE is certain. Otherwise a bar that
    no such ending can pass is out of reach: REJECTED is certain. It can only
    end a run towards REJECTED or INCONCLUSIVE, never LICENSED."""
    scored, failed = int(summary["scored"]), int(summary["failed"])
    remaining = total - scored - failed
    if scored + failed == 0:
        return {"reasons": [], "reading": None}
    failed_max = math.floor(mmb.RECORD_FAILURE_MAX * total + 1e-9)
    call_max = math.floor(mmb.CALL_FAILURE_MAX * total + 1e-9)
    endings = [
        j
        for j in range(remaining + 1)
        if scored + j >= mmb.MIN_CASES and failed + remaining - j <= failed_max
    ]
    if not endings:
        return {"reasons": [], "reading": "INCONCLUSIVE"}

    def share_reachable(passes: int, share: float) -> bool:
        return any(passes + j >= mmb.need_for(share, scored + j) for j in endings)

    bars = {
        "E1 restate": share_reachable(summary["restate_ok"], mmb.RESTATE_PASS_MIN),
        "E2 linked": share_reachable(summary["linked_ok"], mmb.LINKED_PASS_MIN),
        "E3 dates": any(
            int(summary["dates_bad"]) <= mmb.date_allowance(scored + j) for j in endings
        ),
        "E4 bounded": share_reachable(summary["bounded_ok"], mmb.BOUNDED_PASS_MIN),
        "E6 failed calls": any(failed + remaining - j <= call_max for j in endings),
    }
    reasons = [name for name, reachable in bars.items() if not reachable]
    return {"reasons": reasons, "reading": "REJECTED" if reasons else None}


def compose_one(
    source_text: str,
    event_title: str,
    subject_titles: Sequence[str],
    llm: Any,
) -> dict[str, Any]:
    """One compose call. Never raises: a failure is the exception TYPE only."""
    roster = roster_of(source_text)
    overhead = sum(
        len(m["content"])
        for m in build_roster_messages("", event_title, subject_titles, roster)
    )
    prompt_source, bounded = concept_mod._bounded_prompt_source(
        source_text,
        overhead_chars=overhead,
        llm=llm,
        generation_reserve_tokens=concept_mod._OPTIONAL_CALL_RESERVE_TOKENS,
    )
    messages = build_roster_messages(prompt_source, event_title, subject_titles, roster)
    attempts, first_reply = 0, None
    reply: str | None = None
    try:
        reply = llm.chat(messages)
        attempts = 1
        if mmb.parse_compose_reply(reply) is None:
            # One retry of a reply that does not parse, mirroring production's
            # one retry per failed call (`_extract_window_or_skip`, a single
            # pass on an empty result). The identical prompt makes the backend
            # draw a fresh seed (`RETRY_SEED_STRIDE`). A second failure stands.
            first_reply = reply
            reply = llm.chat(messages)
            attempts = 2
    except Exception as exc:
        return {
            "reply": None,
            "failure": type(exc).__name__,
            "bounded": bounded,
            "attempts": max(attempts, 1),
            "first_reply": first_reply,
        }
    return {
        "reply": reply,
        "failure": None,
        "bounded": bounded,
        "attempts": attempts,
        "first_reply": first_reply,
    }


ADJUDICATION: Final[dict[str, tuple[str, str]]] = {
    "ES2015a-es/r1": (
        "faithful",
        "Roles PM/ME/ID/UI, team-building, favorite animal and remote design all in source.",
    ),
    "ES2015a-es/r2": (
        "faithful",
        "Team-building with animal drawings is proposed and carried out.",
    ),
    "ES2015a-es/r5": (
        "faithful",
        "Roles introduced; team-building with animal drawings present.",
    ),
    "ES2015a-es/r8": (
        "faithful",
        "Animal team-building and functional design phase both stated.",
    ),
    "ES2015a-es/r11": (
        "faithful",
        "Whiteboard team-building, 25 euro price, international market, remote improvements all stated.",
    ),
    "ES2015a-es/r12": (
        "faithful",
        "Favorite-animal exercise and UI's dog drawing are in source.",
    ),
    "ES2015d-es/r1": (
        "faithful",
        "Prototype, budget check and the four roles all present; no date claimed.",
    ),
    "ES2015d-es/r2": (
        "faithful",
        "Evaluation criteria and alarm system both discussed.",
    ),
    "ES2015d-es/r3": ("faithful", "Prototype presentation and alarm system discussed."),
    "ES2015d-es/r4": ("faithful", "Alarm system discussed; prototype presented."),
    "ES2015d-es/r5": ("faithful", "Single-hand device and alarm system both stated."),
    "ES2015d-es/r6": ("faithful", "Alarm system discussed; prototype presented."),
    "IS1004b-es/r1": (
        "faithful",
        "Program-based interface, voice recognition, remote design all discussed.",
    ),
    "IS1004b-es/r2": (
        "faithful",
        "LCD, program interface, universal remote, buttons, TV focus, market all discussed.",
    ),
    "IS1004b-es/r3": (
        "faithful",
        "LCD, program-based interface, product name (Mando) discussed.",
    ),
    "IS1004b-es/r4": ("faithful", "Generic but consistent with source objects."),
    "IS1004b-es/r5": (
        "contested",
        "'Dise\u00f1o de controles remotos para accesibilidad' not stated; only left/right-handed designs arguably fit; rest (hidden door, simplified) present.",
    ),
    "IS1004b-es/r6": (
        "faithful",
        "LCD, program interface, 'dispositivo de moda' all in source.",
    ),
    "IS1004d-es/r1": (
        "faithful",
        "Prototype, evaluation and banana remote all in source.",
    ),
    "IS1004d-es/r2": ("faithful", "Teletext and banana remote present."),
    "IS1004d-es/r3": (
        "faithful",
        "Reviews prior decisions, prototype, evaluation; teletext and banana present.",
    ),
    "IS1004d-es/r4": (
        "faithful",
        "Teletext, base station, coverage, energy, safety all discussed.",
    ),
    "IS1004d-es/r5": ("faithful", "Teletext and banana remote present."),
    "IS1004d-es/r6": ("faithful", "Teletext and banana remote present."),
    "TS3009c-es/r1": (
        "faithful",
        "Conceptual design, interface, modern features of remote in source.",
    ),
    "TS3009c-es/r2": (
        "faithful",
        "Component design, materials, visual look in source.",
    ),
    "TS3009c-es/r3": ("faithful", "Component design and conceptual design in source."),
    "TS3009c-es/r4": (
        "faithful",
        "Component design, UI prototype, decisions in source.",
    ),
    "TS3009c-es/r5": ("faithful", "Component design, look and materials in source."),
    "TS3009c-es/r6": (
        "faithful",
        "Component design, interface prototype, appearance decisions in source.",
    ),
}
"""`"source/rN"` -> (label, reason) for every scored body of the roster phase.
Filled by the adjudicator after `score` listed the bodies and BEFORE `decide`
is first run; the replay never writes here (it reads the last arm's registry)."""


def faithful_of(ids: Sequence[str]) -> int | None:
    """Bodies adjudicated faithful, or None while any body is unlabelled."""
    labels = [ADJUDICATION.get(i) for i in ids]
    if any(entry is None for entry in labels):
        return None
    return sum(1 for entry in labels if entry is not None and entry[0] == mmb.FAITHFUL)


# --- records and commands -----------------------------------------------------


def _gather(args: argparse.Namespace) -> dict[str, Any]:
    population, _failed, texts, mean_wall = mmb.load_spanish(
        args.base_dir, args.translations_dir, args.model
    )
    english = mmb.english_placement(args.runs_root, args.prose_sources)
    selected = mmb.select_cases(population, args.per_source)
    cases = mmb.body_cases(args, "composed", selected, texts)
    for c in cases:
        c["absent"] = (
            absent_participants(c["body"], texts[c["id"].split("/")[0]])
            if c["body"]
            else []
        )
    total = len(mmb.select_cases(population, mmb.PER_SOURCE))
    return {
        "placement": {
            "spanish": mmb.summarize_placement(population),
            "english": mmb.summarize_placement(english),
        },
        "extraction_mean_s": mean_wall,
        "full_total": total,
        "planned": len(selected),
        "cases": cases,
    }


def cmd_replay(args: argparse.Namespace) -> int:
    rows: list[tuple[str, list[str]]] = []
    flagged: dict[str, list[str]] = {"missed": [], "wrongly_rejected": []}
    for source in mmb.SOURCES:
        text = mff.translation_text(args.translations_dir, source)
        for path in sorted(
            (args.prior_dir / "compose" / mff.safe(args.model) / source).glob("r*.json")
        ):
            rec = json.loads(path.read_text(encoding="utf-8"))
            parsed = (
                None
                if rec.get("failure")
                else mmb.parse_compose_reply(rec.get("reply"))
            )
            entry = mmb.ADJUDICATION.get(
                ("composed", mmb.case_id(source, int(rec["run"])))
            )
            if parsed is None or entry is None:
                continue
            absent = absent_participants(parsed["body"], text)
            rows.append((entry[0], absent))
            cid = mmb.case_id(source, int(rec["run"]))
            if entry[0] == mmb.UNFAITHFUL and not absent:
                flagged["missed"].append(cid)
            if entry[0] == mmb.FAITHFUL and absent:
                flagged["wrongly_rejected"].append(cid)
    counts = replay_counts(rows)
    print(
        json.dumps(
            {"counts": counts, "flagged": flagged, "verdict": check_verdict(counts)},
            indent=2,
        )
    )
    return 0


def cmd_score(args: argparse.Namespace) -> int:
    data = _gather(args)
    cases = data.pop("cases")
    full = mmb.summarize_bodies(cases, data["planned"])
    kept = [c for c in cases if c["failed"] or survives(c)]
    data["r0_bodies"] = full
    data["r1_bodies"] = {
        **mmb.summarize_bodies(kept, data["planned"]),
        "yield": sum(1 for c in cases if survives(c)),
    }
    data["stop_reading"] = stop_reading(full, data["full_total"])
    data["futility_stop"] = data["stop_reading"]["reading"] is not None
    data["to_adjudicate"] = [
        {k: c[k] for k in ("id", "title", "description", "body")}
        for c in cases
        if c["checks"] is not None and c["id"] not in ADJUDICATION
    ]
    data["case_checks"] = {
        c["id"]: {**c["checks"], "absent": c["absent"]} for c in cases if c["checks"]
    }
    print(json.dumps(data, indent=2, ensure_ascii=False))
    return 0


def cmd_decide(args: argparse.Namespace) -> int:
    if args.per_source != mmb.PER_SOURCE:
        raise SystemExit(
            f"decide: the whole registered set only (--per-source {mmb.PER_SOURCE})"
        )
    data = _gather(args)
    verdicts = decide_arms(
        data["cases"],
        data["planned"],
        data["placement"],
        data["extraction_mean_s"],
        faithful_of,
    )
    print(json.dumps({"verdicts": verdicts}, indent=2, ensure_ascii=False))
    return 0


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
                "event-roster-system": EVENT_ROSTER_SYSTEM_PROMPT,
                "language-anchor": concept_mod._LANGUAGE_ANCHOR,
            },
            host=args.host,
        )
        stamp_path.write_text(json.dumps(stamp, indent=2), encoding="utf-8")
    population, _failed, texts, _mean = mmb.load_spanish(
        args.base_dir, args.translations_dir, args.model
    )
    cache = sp.ChatCache(args.out_dir / "cache" / "chat")
    warmed = False
    for c in mmb.select_cases(population, args.per_source):
        path = mmb.compose_path(args.out_dir, args.model, c["source"], c["run"])
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
        titles = [str(o["title"]) for o in c["a0"][: mmb.CAP]]
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

SRC = (
    "---\nmeeting_id: x\nlicence: CC\n---\n\n"
    "PM: Hola, soy Heather, la Project Manager.\n\n"
    "ID: Soy Poppy, la Industrial Designer.\n\n"
    "PM: Vale. OK: esto no es un hablante.\n\n"
    "ID: Muy bien.\n\n"
)


def _check(cond: bool, message: str) -> None:
    if not cond:
        raise AssertionError(message)


def _t_roster_reads_speaker_labels() -> None:
    _check(roster_of(SRC) == ("PM", "ID"), str(roster_of(SRC)))
    _check(roster_of("sin etiquetas") == (), "no labels, no roster")
    _check(roster_of("OK: una vez\n\nNB: una vez\n") == (), "one turn is not a speaker")


def _t_items_come_from_participation_verbs_only() -> None:
    got = participant_items(
        "Tuvo lugar hoy. Participaron el PM, ID y otros miembros del equipo. Fin."
    )
    _check(got == ["PM", "ID", "otros miembros del equipo"], str(got))
    _check(
        participant_items("Los participantes expresaron ideas.") == [],
        "the noun 'participantes' is not a participation verb",
    )
    got = participant_items("Participaron Heather (Project Manager) y Poppy.")
    _check(got == ["Heather", "Project Manager", "Poppy"], str(got))
    _check(participant_items("Attendees took part: Ann and Bob.") != [], "english verb")


def _t_absent_participants_are_the_ones_the_source_lacks() -> None:
    def absent(body: str) -> list[str]:
        return absent_participants(body, SRC)

    _check(absent("Participaron el PM y el ID.") == [], "roster labels are present")
    _check(absent("Participaron Heather y Poppy.") == [], "names the source states")
    _check(
        absent("Participaron la Project Manager y la Industrial Designer.") == [],
        "role labels the source uses",
    )
    _check(
        absent("Participaron el PM y otros miembros.") == ["otros miembros"], "group"
    )
    _check(
        absent("Participaron el Product Manager.") == ["Product Manager"],
        "a role label the source never uses",
    )
    _check(absent("El PM habló con el ID.") == [], "no participation verb, no items")
    _check(
        absent("Participaron el Design.") == ["Design"],
        "whole words only: 'Designer' in the source does not admit 'Design'",
    )


def _t_roster_prompt_is_frozen_and_hands_over_the_roster() -> None:
    msgs = build_roster_messages("FUENTE", "Reunión X", ["A", "B", "A"], ("PM", "ID"))
    _check(msgs[0]["content"] == EVENT_ROSTER_SYSTEM_PROMPT, "frozen system prompt")
    user = msgs[1]["content"]
    _check(user.startswith(concept_mod._LANGUAGE_ANCHOR), "language anchor first")
    _check("SPEAKERS IN THE SOURCE (complete list): PM, ID" in user, user)
    _check("MEETING TITLE: Reunión X" in user and user.count("- A") == 1, user)
    _check(user.endswith("SOURCE TEXT:\nFUENTE"), "source last")
    none = build_roster_messages("F", "T", ["A"], ())[1]["content"]
    _check("SPEAKERS IN THE SOURCE (complete list): none labelled" in none, none)
    for phrase in ("complete", "exactly as listed", "other members", "never guess"):
        _check(phrase in EVENT_ROSTER_SYSTEM_PROMPT, phrase)


def _t_replay_counts_catch_and_false_reject() -> None:
    rows = [
        ("unfaithful", ["otros miembros"]),
        ("unfaithful", ["Product Manager"]),
        ("unfaithful", []),
        ("faithful", []),
        ("faithful", ["Marketing Expert"]),
    ]
    c = replay_counts(rows)
    _check(
        c
        == {
            "bodies": 5,
            "unfaithful": 3,
            "faithful": 2,
            "caught": 2,
            "missed": 1,
            "wrongly_rejected": 1,
        },
        str(c),
    )


def _t_check_verdict_thresholds() -> None:
    def v(unf: int, caught: int, wrong: int) -> str:
        return check_verdict(
            {"unfaithful": unf, "caught": caught, "wrongly_rejected": wrong}
        )

    _check(v(23, 21, 1) == "CHECK-SOUND", "21 of 23 and one false reject hold")
    _check(v(23, 20, 0) == "CHECK-UNSOUND", "20 of 23 misses the catch bar")
    _check(v(23, 23, 2) == "CHECK-UNSOUND", "two false rejects miss the bar")
    _check(v(0, 0, 0) == "INCONCLUSIVE", "nothing to catch is not a verdict")


GOOD: Final = {
    "overlap": 0.1,
    "restate_ok": True,
    "linked_ok": True,
    "dates_ok": True,
    "bounded_ok": True,
}
CLEAN: Final = {"displaced": 0, "bad_cardinality": 0, "overflow": 0}
PLACED: Final = {"spanish": CLEAN, "english": CLEAN}


def _c(
    i: int, *, absent: list[str] | None = None, failed: bool = False
) -> dict[str, Any]:
    return {
        "id": f"S/r{i}",
        "checks": None if failed else GOOD,
        "failed": failed,
        "wall_s": 20.0,
        "absent": absent or [],
    }


def _all(ids: Sequence[str]) -> int | None:
    return len(ids)


def _t_survivor_has_a_body_and_names_nobody_absent() -> None:
    _check(survives(_c(1)), "clean body survives")
    _check(not survives(_c(1, absent=["otros miembros"])), "absent participant gates")
    _check(not survives(_c(1, failed=True)), "no body, no survivor")


def _t_decide_arms_prompt_and_gate() -> None:
    cases = [_c(i) for i in range(30)]
    out = decide_arms(cases, 30, PLACED, 379.0, _all)
    _check(
        out["r0"]["verdict"] == "LICENSED" and out["r1"]["verdict"] == "LICENSED",
        str(out),
    )
    # 12 bodies name an absent participant: r1 keeps 18 < 20, so its yield fails.
    mixed = [_c(i, absent=["x"] if i < 12 else None) for i in range(30)]
    out = decide_arms(
        mixed, 30, PLACED, 379.0, lambda ids: len(ids) - (12 if len(ids) == 30 else 0)
    )
    _check(
        out["r1"]["verdict"] == "REJECTED" and "E7 yield" in out["r1"]["failed"],
        str(out["r1"]),
    )
    _check(
        out["r0"]["verdict"] == "REJECTED" and "E5 faithful" in out["r0"]["failed"],
        str(out["r0"]),
    )
    # 10 gated: r1 keeps 20 (yield holds, all faithful), r0 is judged on all 30.
    ten = [_c(i, absent=["x"] if i < 10 else None) for i in range(30)]
    out = decide_arms(
        ten, 30, PLACED, 379.0, lambda ids: len(ids) - (10 if len(ids) == 30 else 0)
    )
    _check(out["r1"]["verdict"] == "LICENSED", str(out["r1"]))
    _check(out["r0"]["verdict"] == "REJECTED", str(out["r0"]))


def _t_decide_arms_floors_and_adjudication() -> None:
    few = [_c(i, failed=i >= 12) for i in range(30)]
    out = decide_arms(few, 30, PLACED, 379.0, _all)
    _check(
        out["r0"]["verdict"] == "INCONCLUSIVE"
        and out["r1"]["verdict"] == "INCONCLUSIVE",
        str(out),
    )
    out = decide_arms([_c(i) for i in range(30)], 30, PLACED, 379.0, lambda ids: None)
    _check(out["r0"]["verdict"] == "NEEDS-ADJUDICATION", str(out["r0"]))
    _check(out["r1"]["verdict"] == "NEEDS-ADJUDICATION", str(out["r1"]))
    bad = {"spanish": {**CLEAN, "displaced": 1}, "english": CLEAN}
    out = decide_arms([_c(i) for i in range(30)], 30, bad, 379.0, _all)
    _check(
        out["r0"]["verdict"] == "REJECTED" and out["r1"]["verdict"] == "REJECTED",
        "placement",
    )


def _t_compose_one_degrades_and_records() -> None:
    class Boom:
        context_window = 12288

        def chat(self, messages: Any) -> str:
            raise RuntimeError("down")

    out = compose_one("fuente corta", "Reunión", ["A"], Boom())
    _check(out["reply"] is None and out["failure"] == "RuntimeError", str(out))


def _summary(
    *, scored: int, failed: int, restate: int, linked: int, bounded: int, dates: int = 0
) -> dict[str, Any]:
    return {
        "scored": scored,
        "failed": failed,
        "restate_ok": restate,
        "linked_ok": linked,
        "bounded_ok": bounded,
        "dates_bad": dates,
    }


def _t_stop_uses_the_scored_denominator_not_failed_calls() -> None:
    # The run that stopped last time: 8 scored all passing, 2 failed. Over
    # scored bodies E4 is still reachable (18 more scored: 26 of 26 -> need 25).
    s = _summary(scored=8, failed=2, restate=8, linked=8, bounded=8)
    got = stop_reading(s, 30)
    _check(got == {"reasons": [], "reading": None}, str(got))
    # The old rule counted the two failures against 29 of 30: it would stop.
    _check(mmb.futility(s, 30), "the inherited rule stops here (the flaw)")


def _t_stop_fires_on_a_bar_out_of_reach_over_scored_bodies() -> None:
    # 10 scored, 6 bounded: at best 26 of 30 scored -> need 29, out of reach.
    s = _summary(scored=10, failed=0, restate=10, linked=10, bounded=6)
    got = stop_reading(s, 30)
    _check(got["reading"] == "REJECTED", str(got))
    _check(got["reasons"] == ["E4 bounded"], str(got))
    # The same count is fine while enough cases remain to dilute it:
    # 4 scored of 4 done, 2 bounded -> at best 28 of 30, need 29: out of reach.
    s = _summary(scored=4, failed=0, restate=4, linked=4, bounded=2)
    _check(stop_reading(s, 30)["reasons"] == ["E4 bounded"], "early and certain")
    # 3 scored, 2 bounded: 29 of 30 possible, need 29: still reachable.
    s = _summary(scored=3, failed=0, restate=3, linked=3, bounded=2)
    _check(stop_reading(s, 30)["reading"] is None, "29 of 30 is reachable")


def _t_stop_names_each_deterministic_bar() -> None:
    base = {"scored": 10, "failed": 0, "restate": 10, "linked": 10, "bounded": 10}
    got = stop_reading(_summary(**{**base, "restate": 5}), 30)
    _check(got["reasons"] == ["E1 restate"], str(got))
    got = stop_reading(_summary(**{**base, "linked": 2}), 30)
    _check(got["reasons"] == ["E2 linked"], str(got))
    got = stop_reading(_summary(**{**base, "dates": 3}), 30)
    _check(got["reasons"] == ["E3 dates"], str(got))
    _check(got["reading"] == "REJECTED", str(got))


def _t_failed_calls_count_only_against_e6_and_the_floors() -> None:
    ok = dict(restate=0, linked=0, bounded=0)
    # 4 failed calls: E6 (at most 3) is lost, the floor (at most 4) still holds.
    s = _summary(scored=26, failed=4, **{k: 26 for k in ok})
    got = stop_reading(s, 30)
    _check(got == {"reasons": ["E6 failed calls"], "reading": "REJECTED"}, str(got))
    # 3 failed calls: E6 still holds.
    s = _summary(scored=27, failed=3, **{k: 27 for k in ok})
    _check(stop_reading(s, 30)["reading"] is None, "3 failed calls hold E6")
    # 5 failed calls: more than 15% of 30, INCONCLUSIVE whatever follows.
    s = _summary(scored=25, failed=5, **{k: 25 for k in ok})
    got = stop_reading(s, 30)
    _check(got["reading"] == "INCONCLUSIVE", str(got))
    # Too few scored bodies can remain: INCONCLUSIVE, never REJECTED, even with
    # a bar that would otherwise be out of reach.
    s = _summary(scored=5, failed=5, restate=0, linked=0, bounded=0)
    _check(stop_reading(s, 30)["reading"] == "INCONCLUSIVE", "floor beats a bar")
    _check(stop_reading(s, 30)["reasons"] == [], "no bar named under a floor")


def _t_stop_silent_before_any_call() -> None:
    s = _summary(scored=0, failed=0, restate=0, linked=0, bounded=0)
    _check(stop_reading(s, 30)["reading"] is None, "nothing done, nothing to stop")


class _Script:
    """A scripted LLM: each call returns the next item; an Exception is raised."""

    context_window = 12288

    def __init__(self, *replies: Any) -> None:
        self.replies = list(replies)
        self.calls = 0

    def chat(self, messages: Any) -> str:
        self.calls += 1
        item = self.replies.pop(0)
        if isinstance(item, Exception):
            raise item
        return str(item)


TRUNCATED: Final = '{"description": "d", "body": "Participaron PM y ID."'
WHOLE: Final = '{"description": "d", "body": "Participaron PM y ID."}'


def _t_unparseable_reply_gets_exactly_one_retry() -> None:
    llm = _Script(TRUNCATED, WHOLE)
    out = compose_one("fuente", "Reunión", ["A"], llm)
    _check(llm.calls == 2 and out["attempts"] == 2, str(out))
    _check(out["reply"] == WHOLE and out["failure"] is None, str(out))
    _check(out["first_reply"] == TRUNCATED, "the first reply is kept")


def _t_a_parseable_reply_is_not_retried() -> None:
    llm = _Script(WHOLE)
    out = compose_one("fuente", "Reunión", ["A"], llm)
    _check(llm.calls == 1 and out["attempts"] == 1, str(out))
    _check(out["first_reply"] is None, "no retry, no first reply to keep")


def _t_a_second_failure_is_final() -> None:
    llm = _Script(TRUNCATED, TRUNCATED, WHOLE)
    out = compose_one("fuente", "Reunión", ["A"], llm)
    _check(llm.calls == 2 and out["attempts"] == 2, "never a third call")
    _check(mmb.parse_compose_reply(out["reply"]) is None, "second failure stands")


def _t_a_raised_call_is_not_retried_here() -> None:
    llm = _Script(RuntimeError("down"), WHOLE)
    out = compose_one("fuente", "Reunión", ["A"], llm)
    _check(llm.calls == 1 and out["failure"] == "RuntimeError", str(out))
    _check(out["reply"] is None and out["attempts"] == 1, str(out))


def _t_the_retry_is_a_fresh_seed() -> None:
    import tempfile

    import screen_pipeline as sp

    seeds: list[int] = []
    replies = iter([TRUNCATED, WHOLE])

    def send(
        model: str, messages: Any, seed: int, timeout_s: float
    ) -> tuple[str | None, dict[str, Any]]:
        seeds.append(seed)
        content = next(replies)
        return content, {"outcome": "ok", "content": content, "wall_s": 0.1}

    with tempfile.TemporaryDirectory() as tmp:
        backend = sp.ScreenBackend(
            model="m",
            digest=None,
            run=SEED_BASE + 1,
            window_timeout_s=10.0,
            whole_timeout_s=10.0,
            cache=sp.ChatCache(pathlib.Path(tmp)),
            send=send,
        )
        out = compose_one("fuente", "Reunión", ["A"], backend)
    _check(out["attempts"] == 2, str(out))
    _check(seeds == [SEED_BASE + 1, SEED_BASE + 1 + sp.RETRY_SEED_STRIDE], str(seeds))


def _t_seeds_are_fresh_against_the_last_run() -> None:
    import screen_pipeline as sp

    last = {3000 + r + a * sp.RETRY_SEED_STRIDE for r in range(1, 7) for a in (0, 1)}
    now = {
        SEED_BASE + r + a * sp.RETRY_SEED_STRIDE for r in range(1, 7) for a in (0, 1)
    }
    _check(not (last & now), f"seeds reused: {sorted(last & now)}")
    _check(min(now) > 3000 + sp.RETRY_SEED_STRIDE + 30, "above every earlier seed")


def _t_constants_reuse_the_registered_bars() -> None:
    _check(mmb.SEED_BASE + mmb.PER_SOURCE * len(mmb.SOURCES) < SEED_BASE, "fresh seeds")
    _check(YIELD_MIN == mmb.MIN_CASES, "yield floor equals the scored floor")
    _check(mmb.FAITHFUL_MIN == 0.90 and mmb.OVERLAP_MAX == 0.50, "E1-E5 unchanged")
    _check(mmb.CAP == concept_mod._MAX_OBJECTS_PER_SOURCE, "the cap mirrors production")


def self_test() -> int:
    tests: list[Callable[[], None]] = [
        _t_roster_reads_speaker_labels,
        _t_items_come_from_participation_verbs_only,
        _t_absent_participants_are_the_ones_the_source_lacks,
        _t_roster_prompt_is_frozen_and_hands_over_the_roster,
        _t_replay_counts_catch_and_false_reject,
        _t_check_verdict_thresholds,
        _t_survivor_has_a_body_and_names_nobody_absent,
        _t_decide_arms_prompt_and_gate,
        _t_decide_arms_floors_and_adjudication,
        _t_compose_one_degrades_and_records,
        _t_constants_reuse_the_registered_bars,
        _t_stop_uses_the_scored_denominator_not_failed_calls,
        _t_stop_fires_on_a_bar_out_of_reach_over_scored_bodies,
        _t_stop_names_each_deterministic_bar,
        _t_failed_calls_count_only_against_e6_and_the_floors,
        _t_stop_silent_before_any_call,
        _t_unparseable_reply_gets_exactly_one_retry,
        _t_a_parseable_reply_is_not_retried,
        _t_a_second_failure_is_final,
        _t_a_raised_call_is_not_retried_here,
        _t_the_retry_is_a_fresh_seed,
        _t_seeds_are_fresh_against_the_last_run,
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
        "--out-dir", type=pathlib.Path, default=runs_root / "meeting-event-roster-retry"
    )
    parser.add_argument(
        "--prior-dir",
        type=pathlib.Path,
        default=runs_root / "meeting-event-body",
        help="the last arm's records, read-only (replay)",
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
    parser.add_argument("--model", default=mmb.MODEL)
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("replay", help="model-free: the check against the last arm's bodies")
    compose = sub.add_parser("compose", help="model-bound: one compose call per case")
    compose.add_argument("--per-source", type=int, default=mmb.PER_SOURCE)
    compose.add_argument("--host", default=None)
    compose.add_argument(
        "--screen-dir",
        type=pathlib.Path,
        default=runs_root / "concept-screen",
        help="holds stage0.json, the registered per-call timeouts",
    )
    for name in ("score", "decide"):
        p = sub.add_parser(name)
        p.add_argument("--per-source", type=int, default=mmb.PER_SOURCE)
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    commands = {
        "replay": cmd_replay,
        "compose": cmd_compose,
        "score": cmd_score,
        "decide": cmd_decide,
    }
    if args.command not in commands:
        raise SystemExit("choose a subcommand, or --self-test")
    return commands[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
