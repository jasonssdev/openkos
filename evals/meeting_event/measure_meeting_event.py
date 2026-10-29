"""Keep the meeting Event beside its subjects (#1689): replay, score, decide.

`_drop_framing_objects` removes every non-`Procedure` object whose own title
is meeting-shaped on a meeting-shaped source, even when the reply also carries
the subjects the meeting discussed (#522/#533). Read `PREREGISTRATION.md`
first: it fixes the arms, the bars and the stopping rules before any scoring.

Arms, both replayed from ONE recorded raw stage through production's own
functions (strip, dedup, wrong-language gate, framing drop, source-title
twins), exactly as `evals/framing_floor` does:

- `a0`  the shipped pipeline.
- `a1`  `a0`, plus at most ONE meeting `Event` the framing drop removed,
        appended AFTER the subjects, and only when `a0` is non-empty.

No `run` subcommand exists: the registered records already hold the raw
stage, so this measurement makes no model call. What a replay cannot see (the
Event's body: date, participants, links to subjects) is out of scope.

Subcommands:

- `score`    model-free. Replays the recorded runs and prints the counts the
             registration names, plus the kept objects to adjudicate.
- `decide`   model-free. Applies the registered bars (needs `ADJUDICATION`
             complete for every kept object).

Model-free check: `--self-test`.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any, Final

HERE = pathlib.Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "evals" / "framing_floor"))

import measure_framing_floor as mff  # noqa: E402

from openkos.extraction import concept as concept_mod  # noqa: E402

Obj = dict[str, Any]

ARMS: Final = ("a0", "a1")

CAP: Final = 6
"""`_MAX_OBJECTS_PER_SOURCE`, mirrored by value; a self-test pins it to the
production constant so a drift is visible instead of silently changing the
cap-reach number."""

KEPT_TYPE: Final = "Event"
"""The only type `a1` keeps: the issue asks for a meeting `Event`."""

# Registered bars (PREREGISTRATION.md, "Bars"). All proposed.
OPPORTUNITY_MIN: Final = 0.10
CAP_CUT_MAX: Final = 0.25
CONTAINER_SHARE_MIN: Final = 0.90

CONTAINER: Final = "container"
SUBJECT: Final = "subject"
CONTESTED: Final = "contested"
CLASSES: Final = (CONTAINER, SUBJECT, CONTESTED)

ADJUDICATION: Final[dict[tuple[str, str], tuple[str, str]]] = {
    ("Event", "Afternoon Meeting"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "April Movie Club Meeting"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Concept Design Meeting for Remote Control"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Conceptual Design Meeting"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Conceptual Design Meeting for Remote Control"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Conceptual design meeting"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Conceptual design meeting for remote control"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Department Office Allocation Meeting"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Design Discussion Meeting"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Design Meeting"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Design Meeting for New Device"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Design Team Planning Meeting"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Design and Budget Discussion Meeting"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Detailed Design Meeting for Product"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Detailed Product Design and Evaluation Meeting"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Detailed design review meeting"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Detailed product design evaluation meeting"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Discussion Meeting"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Final Detail Design Meeting for Remote Control Prototype"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Final Meeting about the Detail Design of the Remote Control"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Final Meeting on Remote Control Design"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Final Proposal Review Meeting"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Final design meeting for remote control product"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "First New Product Development Meeting"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "First Product Design Kick-off Meeting"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Functional Design Meeting"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Functional Design Phase Kickoff Meeting"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Functional Design Phase Meeting"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Functional design meeting"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Functional design phase meeting"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Group Meeting Discussion"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Initial Design Direction Meeting"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Mando Banana Design Review Meeting"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Meeting"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Meeting About Relocating to New Office Space"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Meeting Discussion"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Meeting Discussion on Product Design and Budget Constraints"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Meeting Discussion on Remote Control Design"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Meeting Discussion on Remote Control Requirements"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Meeting Discussion on Remote Control Technology"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Meeting Discussion on Remote Control and Technology"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Meeting ES2015d-es"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Meeting TS3009c-es"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Meeting about film candidates"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Meeting about functional design"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Meeting about movie candidates"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Meeting about the functional design of a remote control"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Meeting on Conceptual Design and Remote Control Concepts"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Meeting on Functional Design"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Meeting on Functional Design Agenda and Teletext Removal Impact"): (
        "contested",
        "title names a decision or outcome, not only the gathering",
    ),
    ("Event", "Meeting on Movie Club Poster Design for 'The Big Lebowski'"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Meeting on Office Layout Proposal Constraints"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Meeting on Office Layout and Visitor Space Planning"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Meeting on Office Space Allocation and Constraints"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Meeting on Office Space Distribution and Lounge Usage"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Meeting on Product Design"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Meeting on Remote Control Concept Design"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Meeting on Remote Control Conceptual Design"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Meeting on Remote Control Design"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Meeting on conceptual design and remote control decisions"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Meeting on functional design and teletext removal"): (
        "contested",
        "title names a decision or outcome, not only the gathering",
    ),
    ("Event", "Meeting regarding final proposal circulation"): (
        "contested",
        "title names a decision or outcome, not only the gathering",
    ),
    ("Event", "Meeting regarding lounge layout and separation"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Meeting to Decide Movie and Advertising Content"): (
        "contested",
        "title names a decision or outcome, not only the gathering",
    ),
    ("Event", "Meeting to Decide on Movie and Advertising Content"): (
        "contested",
        "title names a decision or outcome, not only the gathering",
    ),
    ("Event", "Meeting to Decide on Remote Control Concept"): (
        "contested",
        "title names a decision or outcome, not only the gathering",
    ),
    ("Event", "Meeting to discuss highly ranked movies"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Meeting with D and C"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Montreux English Movie Club meeting"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Movie Club Meeting"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Movie Club Meeting - April"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Movie Club Meeting - April Projection Selection"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Movie Club Meeting for April Projection Selection"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Movie Club Meeting on Movie Selection and Advertising"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Movie Discussion Meeting"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Movie Selection Meeting"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Movie club meeting"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Movie selection and poster design meeting"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Next Tuesday afternoon meeting"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Office Space Arrangement Meeting"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Office layout planning meeting"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Office space allocation meeting"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Planning Meeting for New Office/Building Space"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Product Demo Meeting"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Product Design Meeting"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Product Design Review Meeting"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Product Detail Design Meeting"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Product Detailed Design Evaluation Meeting"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Product Detailed Design Meeting"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Product Development Meeting"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Product Presentation Meeting"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Product Review Meeting"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Production cost review meeting"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Project Design and Budget Review Meeting"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Project Meeting"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Project Meeting on User Requirements"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Project Status Meeting on Remote Control Requirements"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Project Team Meeting"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Prototype Design Review Meeting"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Prototype Evaluation Meeting"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Prototype Presentation Meeting"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Prototype presentation and evaluation meeting"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Remote Control Conceptual Design Meeting"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Remote Control Design Meeting"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Remote Control Design Presentation Meeting"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Remote Control Design Review Meeting"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Remote Control Product Design Meeting"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Remote Control Usability Study Presentation Meeting"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Remote control conceptual design meeting"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Remote control design evaluation meeting"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Reunión de Desarrollo del Nuevo Producto"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Reunión de Diseño Conceptual"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Reunión de Diseño Detallado del Control Remoto"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Reunión de Diseño Final del Control Remoto"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Reunión de Diseño Funcional"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Reunión de Planificación del Proyecto"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Reunión de Proyecto"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Reunión de Trabajo"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Reunión de cumplimiento"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Reunión de decisiones técnicas"): (
        "contested",
        "title names a decision or outcome, not only the gathering",
    ),
    ("Event", "Reunión de diseño del control remoto"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Reunión de diseño detallado del control remoto"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Reunión de planificación del proyecto"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Reunión de presentación del prototipo"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Reunión de proyecto"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Reunión de revisión de procesos"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Reunión de revisión técnica sobre deriva de embeddings y duplicación"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Reunión de revisión técnica y cumplimiento"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Reunión de seguimiento"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Reunión de sincronización técnica sobre respaldos y modelo vectorial"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Reunión de trabajo"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Reunión sobre diseño detallado del control remoto"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Second functional design meeting"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Team Meeting"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Team Meeting to Develop New Product"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "Team Meeting to Evaluate Prototype and Discuss Finances"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "User Requirements Meeting"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
    ("Event", "User Requirements Presentation Meeting"): (
        "container",
        "title names one gathering, optionally with its topic area",
    ),
}
"""(type, title) -> (class, reason) for every object `a1` keeps. Filled by a
delegated agent (not the owner) after `score` listed the kept objects and BEFORE
`decide` is first run, reading each object's type, title and description only
(never its model or run). A contested object counts against the arm."""

_WORD_RE: Final = re.compile(r"\w{3,}")


# --- replay -------------------------------------------------------------------


def key(obj: Mapping[str, Any]) -> tuple[str, str]:
    return (str(obj["type"]), str(obj["title"]))


def faithfulness(title: str, source_text: str) -> float:
    """Share of the title's words (3+ letters) that occur in the source."""
    words = _WORD_RE.findall(title.lower())
    if not words:
        return 0.0
    present = set(_WORD_RE.findall(source_text.lower()))
    return sum(1 for w in words if w in present) / len(words)


def _obj(result: concept_mod.ExtractionResult) -> Obj:
    return {
        "type": result.type,
        "title": result.title,
        "description": result.description,
    }


def replay(
    raw: Sequence[Mapping[str, Any]],
    *,
    source_text: str,
    source_title: str,
    framing_shaped: bool,
) -> dict[str, Any]:
    """`{"a0": [...], "a1": [...], "kept": obj | None, "candidates": n}`.

    Production's own functions, in the chunked-path order. The candidates are
    the meeting `Event`s the framing drop removed that survive the language
    gate and the source-title twin rule: what `a0` removed and nothing else
    would have removed."""
    stripped = concept_mod._strip_ungrounded_expansions(
        [
            concept_mod.ExtractionResult(
                type=str(o["type"]),
                title=str(o["title"]),
                description=str(o.get("description", "")),
                body="",
            )
            for o in raw
        ],
        source_text=source_text,
    )
    deduped = concept_mod._dedup_merged(stripped)
    gated = concept_mod._drop_wrong_language_titles(deduped, source_text=source_text)[0]

    def twins(items: list[concept_mod.ExtractionResult]) -> list[Obj]:
        kept = concept_mod._drop_source_title_twins(items, source_title=source_title)
        return [_obj(r) for r in kept]

    a0 = twins(concept_mod._drop_framing_objects(gated, framing_shaped=framing_shaped))
    held = Counter(key(o) for o in a0)
    candidates: list[Obj] = []
    for o in twins(gated):
        if held[key(o)] > 0:
            held[key(o)] -= 1
        elif o["type"] == KEPT_TYPE:
            candidates.append(o)
    kept: Obj | None = None
    best = -1.0
    for o in candidates:
        score = faithfulness(str(o["title"]), source_text)
        if score > best:  # strict: a tie keeps the earlier reply position
            kept, best = o, score
    if not a0 or kept is None:
        return {"a0": a0, "a1": list(a0), "kept": None, "candidates": len(candidates)}
    return {"a0": a0, "a1": [*a0, kept], "kept": kept, "candidates": len(candidates)}


def displacement_ok(outputs: Mapping[str, Any]) -> bool:
    """`a1` starts with exactly `a0`, in order: no subject lost or moved."""
    a0 = [key(o) for o in outputs["a0"]]
    return [key(o) for o in outputs["a1"]][: len(a0)] == a0


def cardinality_ok(outputs: Mapping[str, Any]) -> bool:
    """`a1` adds exactly one object when `a0` is non-empty and a candidate
    exists, and none otherwise."""
    added = len(outputs["a1"]) - len(outputs["a0"])
    expected = 1 if outputs["a0"] and outputs["candidates"] else 0
    return added == expected


def classify(obj: Mapping[str, Any]) -> str:
    entry = ADJUDICATION.get(key(obj))
    if entry is None:
        raise SystemExit(f"unadjudicated kept object: {key(obj)}")
    return entry[0]


# --- scoring ------------------------------------------------------------------


def summarize(cases: Iterable[Mapping[str, Any]], *, classes: bool) -> dict[str, Any]:
    """Counts over replayed cases, each `{"source", "outputs"}`. With
    `classes`, kept objects are classified (an unadjudicated one stops the
    run)."""
    out: dict[str, Any] = {
        "n": 0,
        "a0_empty": 0,
        "a0_nonempty": 0,
        "qualifies": 0,
        "cap_cut": 0,
        "displacement_violations": 0,
        "cardinality_violations": 0,
        "kept_container": 0,
        "kept_subject": 0,
        "kept_titles": [],
    }
    for case in cases:
        outputs = case["outputs"]
        out["n"] += 1
        if not outputs["a0"]:
            out["a0_empty"] += 1
        else:
            out["a0_nonempty"] += 1
        if not displacement_ok(outputs):
            out["displacement_violations"] += 1
        if not cardinality_ok(outputs):
            out["cardinality_violations"] += 1
        kept = outputs["kept"]
        if kept is None:
            continue
        out["qualifies"] += 1
        if len(outputs["a0"]) >= CAP:
            out["cap_cut"] += 1
        out["kept_titles"].append(
            [kept["type"], kept["title"], str(kept.get("description", ""))]
        )
        if classes:
            if classify(kept) == CONTAINER:
                out["kept_container"] += 1
            else:
                out["kept_subject"] += 1
    return out


def decide(*, spanish: Mapping[str, Any], english: Mapping[str, Any]) -> dict[str, Any]:
    """The registered bars, in the registered order."""
    pops = {"spanish": spanish, "english": english}
    rates = {
        name: p["qualifies"] / p["a0_nonempty"] if p["a0_nonempty"] else 0.0
        for name, p in pops.items()
    }
    cuts = {
        name: p["cap_cut"] / p["qualifies"] if p["qualifies"] else 0.0
        for name, p in pops.items()
    }
    kept = sum(p["kept_container"] + p["kept_subject"] for p in pops.values())
    share = sum(p["kept_container"] for p in pops.values()) / kept if kept else 0.0
    bars = {
        "B0_opportunity": all(r >= OPPORTUNITY_MIN for r in rates.values()),
        "B1_no_displacement": all(
            p["displacement_violations"] == 0 for p in pops.values()
        ),
        "B2_cardinality": all(p["cardinality_violations"] == 0 for p in pops.values()),
        "B3_container": kept > 0 and share >= CONTAINER_SHARE_MIN,
        "B4_cap_reach": all(c <= CAP_CUT_MAX for c in cuts.values()),
    }
    if not bars["B0_opportunity"]:
        verdict = "NO-OPPORTUNITY"
    elif not (
        bars["B1_no_displacement"] and bars["B2_cardinality"] and bars["B3_container"]
    ):
        verdict = "REJECTED"
    elif not bars["B4_cap_reach"]:
        verdict = "ADVANCE-WITH-CAP-EXEMPTION"
    else:
        verdict = "ADVANCE-TO-GENERATION"
    return {
        "verdict": verdict,
        "bars": bars,
        "qualify_rate": rates,
        "cap_cut_share": cuts,
        "container_share": share,
    }


# --- records ------------------------------------------------------------------


def spanish_cases(
    out_dir: pathlib.Path, translations_dir: pathlib.Path, model: str
) -> tuple[list[dict[str, Any]], int]:
    """Replayed Spanish cases, and the number of failed records skipped. Reads
    the records `evals/framing_floor` stored; generates nothing."""
    cases: list[dict[str, Any]] = []
    failed = 0
    for source in mff.SOURCES:
        text = mff.translation_text(translations_dir, source)
        for path in sorted(
            (out_dir / "records" / mff.safe(model) / source).glob("r*.json")
        ):
            rec = json.loads(path.read_text(encoding="utf-8"))
            if rec.get("failure") or rec["stages"]["raw"] is None:
                failed += 1
                continue
            title = str(rec["title"])
            outputs = replay(
                rec["stages"]["raw"],
                source_text=text,
                source_title=title,
                framing_shaped=concept_mod._framing_shaped(title, text),
            )
            cases.append({"source": source, "run": rec["run"], "outputs": outputs})
    return cases, failed


def english_cases(
    runs_root: pathlib.Path, prose_sources: pathlib.Path
) -> list[dict[str, Any]]:
    """Replayed stored English (and prose) records that were framing-shaped:
    the population #522/#533/#1575/#1642 measured. Reads records only."""
    import measure_framing_scope as mfs

    records, _stats = mfs.load_records(runs_root, prose_sources)
    return [
        {
            "source": r["source"],
            "outputs": replay(
                r["raw"],
                source_text=r["body"],
                source_title=r["title"],
                framing_shaped=True,
            ),
        }
        for r in records
        if r["framing_shaped"] and r["raw"]
    ]


def _gather(args: argparse.Namespace, *, classes: bool) -> dict[str, Any]:
    es, failed = spanish_cases(args.out_dir, args.translations_dir, args.model)
    en = english_cases(args.runs_root, args.prose_sources)
    return {
        "spanish": summarize(es, classes=classes),
        "spanish_failed_records": failed,
        "english": summarize(en, classes=classes),
    }


def cmd_score(args: argparse.Namespace) -> int:
    print(json.dumps(_gather(args, classes=False), indent=2, ensure_ascii=False))
    return 0


def cmd_decide(args: argparse.Namespace) -> int:
    data = _gather(args, classes=True)
    verdict = decide(spanish=data["spanish"], english=data["english"])
    print(
        json.dumps({"numbers": data, "verdict": verdict}, indent=2, ensure_ascii=False)
    )
    return 0


# --- self-test ----------------------------------------------------------------

ES_TEXT = (
    "Hola a todos, la reunión de hoy es sobre el diseño del mando a distancia. "
    "Creo que el botón de encendido debe estar en la parte de arriba, y que el "
    "diseño tiene que ser sencillo para los usuarios que no usan mucho la "
    "tecnología. Estamos de acuerdo en que el proyecto es para el mercado.\n"
)


def _check(cond: bool, message: str) -> None:
    if not cond:
        raise AssertionError(message)


def _raw(*items: tuple[str, str]) -> list[Obj]:
    return [
        {"type": t, "title": ti, "description": "d", "body_chars": 1, "window": 0}
        for t, ti in items
    ]


def _titles(out: Sequence[Mapping[str, Any]]) -> list[str]:
    return [str(o["title"]) for o in out]


def _replay(raw: list[Obj], *, shaped: bool = True) -> dict[str, Any]:
    return replay(
        raw, source_text=ES_TEXT, source_title="Reunión X", framing_shaped=shaped
    )


SUBJ = ("Concept", "Diseño del mando a distancia")
EVT = ("Event", "Reunión de diseño del mando")


def _t_keeps_the_event_after_the_subjects() -> None:
    out = _replay(_raw(EVT, SUBJ))
    _check(_titles(out["a0"]) == [SUBJ[1]], f"a0: {out['a0']}")
    _check(_titles(out["a1"]) == [SUBJ[1], EVT[1]], f"a1: {out['a1']}")
    _check(out["kept"]["title"] == EVT[1], f"kept: {out['kept']}")
    _check(displacement_ok(out) and cardinality_ok(out), "invariants")


def _t_no_subject_keeps_nothing() -> None:
    out = _replay(_raw(EVT))
    _check(out["a0"] == [] and out["a1"] == [], f"a1 must stay empty: {out['a1']}")
    _check(out["kept"] is None, "nothing kept")
    _check(cardinality_ok(out), "an empty a0 adds nothing")


def _t_not_framing_shaped_changes_nothing() -> None:
    out = _replay(_raw(EVT, SUBJ), shaped=False)
    _check(out["a1"] == out["a0"], "no framing drop ran, so nothing to keep")
    _check(out["kept"] is None, "nothing kept")


def _t_only_events_are_kept() -> None:
    out = _replay(_raw(("Project", "Reunión de planificación del proyecto"), SUBJ))
    _check(out["a1"] == out["a0"], f"a non-Event is not kept: {out['a1']}")
    _check(out["candidates"] == 0, "no Event candidate")
    _check(cardinality_ok(out), "cardinality with no candidate")


def _t_exactly_one_event_the_most_faithful() -> None:
    far = ("Event", "Reunión de brainstorming creativo estelar")
    near = ("Event", "Reunión sobre el diseño del mando")
    out = _replay(_raw(far, near, SUBJ))
    _check(out["candidates"] == 2, f"two candidates: {out['candidates']}")
    _check(len(out["a1"]) == len(out["a0"]) + 1, "exactly one added")
    _check(out["kept"]["title"] == near[1], f"most faithful wins: {out['kept']}")


def _t_tie_goes_to_the_earlier_event() -> None:
    first = ("Event", "Reunión de diseño")
    second = ("Event", "Reunión de mercado")
    out = _replay(_raw(first, second, SUBJ))
    _check(out["kept"]["title"] == first[1], f"earlier wins a tie: {out['kept']}")


def _t_faithfulness_is_the_share_of_grounded_words() -> None:
    _check(faithfulness("reunión diseño mando", ES_TEXT) == 1.0, "all grounded")
    _check(faithfulness("reunión zzzz yyyy", ES_TEXT) == 1 / 3, "one of three")
    _check(faithfulness("a de", ES_TEXT) == 0.0, "no 3-letter words scores 0")


def _t_wrong_language_event_is_not_kept() -> None:
    out = _replay(_raw(("Event", "Team meeting about remote design"), SUBJ))
    _check(out["kept"] is None, f"the language gate still applies: {out['kept']}")
    _check(out["a1"] == out["a0"], "a1 equals a0")


def _t_checks_detect_violations() -> None:
    s = {"type": "Concept", "title": "S"}
    e = {"type": "Event", "title": "E"}
    t = {"type": "Concept", "title": "T"}
    _check(
        not displacement_ok({"a0": [s, t], "a1": [t, s, e], "candidates": 1}),
        "reordering subjects is displacement",
    )
    _check(
        not displacement_ok({"a0": [s, t], "a1": [s, e], "candidates": 1}),
        "dropping a subject is displacement",
    )
    _check(
        not cardinality_ok({"a0": [s], "a1": [s, e, e], "candidates": 2}),
        "two kept is a cardinality violation",
    )
    _check(
        not cardinality_ok({"a0": [s], "a1": [s], "candidates": 1}),
        "a qualifying record that kept nothing is a violation",
    )


def _t_cap_is_the_production_cap() -> None:
    _check(CAP == concept_mod._MAX_OBJECTS_PER_SOURCE, "cap drifted from production")


def _case(source: str, n_subjects: int, *, event: bool = True) -> dict[str, Any]:
    a0 = [
        {"type": "Concept", "title": f"S{i}", "description": ""}
        for i in range(n_subjects)
    ]
    kept = {"type": "Event", "title": "Reunión", "description": "d"} if event else None
    a1 = [*a0, kept] if kept and a0 else list(a0)
    return {
        "source": source,
        "outputs": {
            "a0": a0,
            "a1": a1,
            "kept": kept if a0 else None,
            "candidates": 1 if event else 0,
        },
    }


def _t_summarize_counts() -> None:
    cases = [
        _case("s", 2),
        _case("s", 0),
        _case("s", 3, event=False),
        _case("s", CAP),
    ]
    s = summarize(cases, classes=False)
    _check(s["n"] == 4 and s["a0_empty"] == 1, f"n/empty: {s}")
    _check(s["a0_nonempty"] == 3, f"nonempty: {s['a0_nonempty']}")
    _check(s["qualifies"] == 2, f"qualifies: {s['qualifies']}")
    _check(s["cap_cut"] == 1, f"cap_cut counts a0 at the cap: {s['cap_cut']}")
    _check(s["displacement_violations"] == 0 and s["cardinality_violations"] == 0, "ok")
    _check(len(s["kept_titles"]) == 2, f"kept titles: {s['kept_titles']}")
    try:
        summarize(cases, classes=True)
    except SystemExit:
        return
    raise AssertionError("an unadjudicated kept object must stop the run")


def _t_summarize_flags_violations() -> None:
    bad = _case("s", 2)
    bad["outputs"]["a1"] = list(reversed(bad["outputs"]["a1"]))
    s = summarize([bad], classes=False)
    _check(s["displacement_violations"] == 1, f"displacement: {s}")


def _spanish(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "n": 75,
        "a0_nonempty": 60,
        "qualifies": 30,
        "cap_cut": 3,
        "displacement_violations": 0,
        "cardinality_violations": 0,
        "kept_container": 29,
        "kept_subject": 1,
    }
    return {**base, **over}


def _t_decide_bars() -> None:
    ok = decide(spanish=_spanish(), english=_spanish(n=254, a0_nonempty=200))
    _check(ok["verdict"] == "ADVANCE-TO-GENERATION", f"ok: {ok}")
    nopp = decide(spanish=_spanish(qualifies=3), english=_spanish())
    _check(nopp["verdict"] == "NO-OPPORTUNITY", f"opportunity: {nopp['verdict']}")
    disp = decide(spanish=_spanish(displacement_violations=1), english=_spanish())
    _check(disp["verdict"] == "REJECTED", f"displacement: {disp['verdict']}")
    card = decide(spanish=_spanish(), english=_spanish(cardinality_violations=1))
    _check(card["verdict"] == "REJECTED", f"cardinality: {card['verdict']}")
    junk = decide(
        spanish=_spanish(kept_container=20, kept_subject=10), english=_spanish()
    )
    _check(junk["verdict"] == "REJECTED", f"quality: {junk['verdict']}")
    cut = decide(spanish=_spanish(cap_cut=20), english=_spanish())
    _check(cut["verdict"] == "ADVANCE-WITH-CAP-EXEMPTION", f"cap: {cut['verdict']}")


def _t_registry_is_consistent() -> None:
    for entry_key, (label, reason) in ADJUDICATION.items():
        _check(label in CLASSES and bool(reason), f"bad entry {entry_key}")
    _check(set(ARMS) == {"a0", "a1"}, "arms")


def _t_spanish_cases_replay_stored_records() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        tr = root / "tr"
        tr.mkdir()
        for name in mff.SOURCES:
            (tr / f"{name}.md").write_text(ES_TEXT, encoding="utf-8")
        rec_dir = root / "records" / mff.safe("m") / mff.LEAD
        rec_dir.mkdir(parents=True)
        stages = {"raw": _raw(EVT, SUBJ)}
        good = {"title": "Reunión X", "failure": None, "run": 1, "stages": stages}
        bad = {"title": "Reunión X", "failure": "timeout", "run": 2, "stages": stages}
        (rec_dir / "r1.json").write_text(json.dumps(good), encoding="utf-8")
        (rec_dir / "r2.json").write_text(json.dumps(bad), encoding="utf-8")
        cases, failed = spanish_cases(root, tr, "m")
    _check(failed == 1 and len(cases) == 1, f"failed {failed}, cases {len(cases)}")
    _check(cases[0]["outputs"]["kept"]["title"] == EVT[1], "replayed from stored raw")
    try:
        mff.translation_text(pathlib.Path(), "ES2008a-es")
    except SystemExit:
        return
    raise AssertionError("an unregistered source must be refused")


def self_test() -> int:
    tests: list[Callable[[], None]] = [
        _t_keeps_the_event_after_the_subjects,
        _t_no_subject_keeps_nothing,
        _t_not_framing_shaped_changes_nothing,
        _t_only_events_are_kept,
        _t_exactly_one_event_the_most_faithful,
        _t_tie_goes_to_the_earlier_event,
        _t_faithfulness_is_the_share_of_grounded_words,
        _t_wrong_language_event_is_not_kept,
        _t_checks_detect_violations,
        _t_cap_is_the_production_cap,
        _t_summarize_counts,
        _t_summarize_flags_violations,
        _t_decide_bars,
        _t_registry_is_consistent,
        _t_spanish_cases_replay_stored_records,
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
    parser.add_argument(
        "--out-dir",
        type=pathlib.Path,
        default=home / "Dev/Projects/openkos-eval-runs/framing-floor",
        help="holds the Spanish records `evals/framing_floor` stored",
    )
    parser.add_argument(
        "--translations-dir",
        type=pathlib.Path,
        default=home / "openkos-eval-runs/retrieval-gold/translations",
    )
    parser.add_argument("--model", default=mff.MODEL)
    parser.add_argument(
        "--runs-root",
        type=pathlib.Path,
        default=home / "Dev/Projects/openkos-eval-runs",
    )
    parser.add_argument(
        "--prose-sources",
        type=pathlib.Path,
        default=home / "Dev/Projects/openkos/examples/extraction-corpus/sources",
    )
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("score")
    sub.add_parser("decide")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    commands = {"score": cmd_score, "decide": cmd_decide}
    if args.command not in commands:
        raise SystemExit("choose a subcommand, or --self-test")
    return commands[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
