"""The Source fallback of #1741 on long AMI Sources: the second registration.

Read `PREREGISTRATION-long.md` first. The first registration
(`PREREGISTRATION.md`, `measure_source_fallback.py`) stopped at NO-EXPOSURE
because its fixture Sources were about 4 KB and fit their share of the window.
This one runs the SAME arms, readouts, bars and stop order over a fixture whose
Sources are full AMI meeting transcripts, longer than their share, which is the
regime of the failure #1741 reports.

Nothing is copied into the repository: the harness reads the adjudicated gold
CSVs and the rendered transcripts from the paths it is given (the private
thesis repository), exactly as `evals/decision_windows/` does. The gold files
are pinned by SHA-256 there and here.

Subcommands: `exposure` (model-free, the registered precondition and the
fixture hash), `run`, `futility`, `fallback`, `score`, `decide`, `report`
(model-free from `score` on). Model-free check: `--self-test`.
"""

from __future__ import annotations

import argparse
import csv
import functools
import hashlib
import json
import math
import pathlib
import re
import statistics
import sys
import unicodedata
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Final

HERE = pathlib.Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "evals" / "decision_windows"))
sys.path.insert(0, str(REPO_ROOT / "evals" / "transcript_decisions"))

import measure_decision_windows as mdw  # noqa: E402
import measure_source_fallback as msf  # noqa: E402
import scoring  # noqa: E402

from openkos.config import DEFAULT_MAX_GENERATION_TOKENS  # noqa: E402
from openkos.model import okf  # noqa: E402
from openkos.retrieval.answer import DEFAULT_QUERY_LIMIT  # noqa: E402

SERIES: Final = tuple(mdw.REQUIRED_SERIES)
MEETINGS: Final = tuple(f"{s}{letter}" for s in SERIES for letter in "abcd")
PER_MEETING: Final = 3
SELECTION_SALT: Final = "1741-long"
MIN_VALUE_STEMS: Final = 2
MIN_GLOSS_STEMS: Final = 1
ORACLE_REACH_MIN: Final = 0.5
STEM_LEN: Final = 5
STEM_MATCH_SHARE: Final = 0.5
EXCEED_MIN: Final = 0.75
CAP_HOURS_RUN: Final = 2.0
CAP_HOURS_FALLBACK: Final = 1.0
FIRE_SHARE_DENOMINATOR: Final = 6
FIRE_MIN_LONG: Final = 15
REGISTERED_CORE_SHA256: Final = (
    "7d799c74462ebaefb7ae5284700d0c8c0711646fc16497a0193b89c5192a8209"
)
REGISTERED_FIXTURE_SHA256: Final = (
    "22ff6ef42eb7438ec547e505bc407f1220a5aee05ffab04695fc2b2d7bb3ec7f"
)
DEFAULT_GLOSSES: Final = HERE / "local" / "glosses.json"
SKIPPED_NO_GLOSSES: Final = "skipped: no glosses file"
DEFAULT_OUT: Final = (
    pathlib.Path.home() / "Dev/Projects/openkos-eval-runs/source-fallback-long"
)


# --------------------------------------------------------------------------- #
# Core (pure, model-free)                                                      #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Row:
    """One adjudicated gold row (only the columns this harness reads)."""

    decision_id: str
    meeting: str
    status: str
    object_: str
    content: str
    evidence: str


@dataclass(frozen=True, eq=False)
class Data:
    """The fixture's inputs: the Source bodies and every gold row. Identity
    equality, so one derivation is cached per loaded fixture."""

    bodies: Mapping[str, str]
    rows: tuple[Row, ...]
    authored: Mapping[str, str] = field(default_factory=dict)
    glosses: Mapping[str, str] | None = None
    """The English value glosses, or None when no glosses file was given."""


@dataclass(frozen=True)
class Fact:
    decision_id: str
    meeting: str
    object_: str
    content: str
    acts: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class GoldQuestion:
    fact: Fact
    stems: tuple[str, ...]
    """The scored value stems: those of the English gloss."""
    text: str
    """The authored English question."""
    gloss: str
    """The English gloss of the value (from the local file), used only to score."""


ABSENT: Final[tuple[tuple[str, str, str, str], ...]] = (
    (
        "ua01",
        "Which company was hired to manufacture the casings of the remote control?",
        r"manufactur",
        r"supplier|vendor|subcontract|contract(ed)? (with|to)",
    ),
    (
        "ua02",
        "Which advertising channel did the team approve for announcing the product?",
        r"advertis",
        r"billboard|newspaper|magazine|radio|commercial|campaign",
    ),
    (
        "ua03",
        "On what date was the launch of the product to the market approved?",
        r"launch",
        r"january|february|march|april|june|july|august|september|october|"
        r"november|december|\b20\d\d\b|next (week|month|year)",
    ),
    (
        "ua04",
        "How many units was the team approved to sell during the first year?",
        r"\bunits\b",
        r"\d+ units|(thousand|million|hundred) units",
    ),
    (
        "ua05",
        "How many hours of training were approved for the team?",
        r"training",
        r"\d+ hours|hours of training",
    ),
    (
        "ua06",
        "Which competitor was chosen as the official price reference?",
        r"competitor",
        r"sony|philips|samsung|panasonic|apple|nokia",
    ),
    (
        "ua07",
        "Which brand of batteries was chosen for the remote control?",
        r"batter",
        r"duracell|energizer|panasonic|varta|\bbrand",
    ),
    (
        "ua08",
        "What marketing budget was approved for the launch?",
        r"marketing",
        r"marketing budget|budget for (the )?marketing|"
        r"\d+ (euro|dollar)s? (for|on) marketing",
    ),
)
"""(id, question, topic regex, forbidden-value regex). The topic regex must
match a line of some Source (the question is on topic); the forbidden regex
must match no topic line (no Source states a value of that kind)."""

_STOP: Final = frozenset(
    [
        "de",
        "del",
        "la",
        "las",
        "el",
        "los",
        "lo",
        "un",
        "una",
        "unos",
        "unas",
        "que",
        "con",
        "por",
        "para",
        "al",
        "en",
        "y",
        "o",
        "u",
        "se",
        "su",
        "sus",
        "como",
        "mas",
        "pero",
        "sin",
        "sobre",
        "entre",
        "este",
        "esta",
        "esto",
        "estos",
        "estas",
        "hacia",
        "solo",
        "ser",
        "hay",
        "les",
        "muy",
        "tambien",
        "cada",
        "todo",
        "toda",
        "todos",
        "todas",
        "desde",
        "hasta",
        "donde",
        "cuando",
        "dice",
        "dicho",
        "dijo",
        "como",
        "fue",
        "son",
        "sera",
        "seran",
    ]
)
"""Spanish function words dropped before stemming; registered with the rule."""

_STOP_EN: Final = frozenset(
    [
        "the",
        "and",
        "for",
        "with",
        "that",
        "this",
        "these",
        "those",
        "than",
        "then",
        "from",
        "into",
        "onto",
        "are",
        "was",
        "were",
        "has",
        "have",
        "had",
        "not",
        "but",
        "its",
        "his",
        "her",
        "our",
        "their",
        "your",
        "who",
        "whom",
        "what",
        "when",
        "where",
        "which",
        "why",
        "how",
        "will",
        "would",
        "should",
        "could",
        "can",
        "does",
        "did",
        "been",
        "being",
        "also",
        "very",
        "just",
        "about",
        "over",
        "out",
        "all",
        "any",
        "each",
        "some",
        "such",
        "only",
        "one",
    ]
)
"""English function words dropped before stemming; registered with the rule."""

QUESTIONS_EN: Final[dict[str, str]] = {
    "ES2015a.d01": "In ES2015a, what kind of product is the team being asked to develop?",
    "ES2015a.d02": "In ES2015a, what selling price does the project manager want the team to aim for?",
    "ES2015a.d05": "In ES2015a, what upper limit does the project manager set for the cost of making it?",
    "ES2015b.d02": "In ES2015b, what position has the management taken on putting teletext in the remote?",
    "ES2015b.d03": "In ES2015b, what branding element does the project manager require on every remote?",
    "ES2015b.d05": "In ES2015b, what does the project manager conclude about the alarm idea for finding a lost remote?",
    "ES2015c.d03": "In ES2015c, which feature from the earlier discussion does the marketing expert want to stick to?",
    "ES2015c.d04": "In ES2015c, how will the remote be powered, according to what the team decided?",
    "ES2015c.d08": "In ES2015c, which functions does the team agree the remote should offer?",
    "ES2015d.d03": "In ES2015d, what does the project manager suggest about how many different shades the product should come in?",
    "ES2015d.d05": "In ES2015d, what shape does the project manager describe for the remote?",
    "ES2015d.d06": "In ES2015d, what is the remote going to be made of?",
    "IS1004a.d01": "In IS1004a, what is the stated goal of the project?",
    "IS1004a.d02": "In IS1004a, what selling price does the project manager say the team should target?",
    "IS1004a.d03": "In IS1004a, in which part of the world should the team aim to sell?",
    "IS1004b.d01": "In IS1004b, what does the head office want the remote to be restricted to?",
    "IS1004b.d05": "In IS1004b, did the team decide to add technology that lets users operate the remote by talking?",
    "IS1004b.d06": "In IS1004b, what elements should the product visibly carry to show which company made it?",
    "IS1004d.d02": "In IS1004d, what does each of the two wheels on the prototype do, and on which side is it?",
    "IS1004d.d04": "In IS1004d, which communication component does the prototype include for talking to the television?",
    "IS1004d.d10": "In IS1004d, which grade of chip did the industrial designer say was chosen?",
    "TS3009a.d01": "In TS3009a, what did the project manager ask each team member to do before the next meeting?",
    "TS3009b.d02": "In TS3009b, which idea for the casing did the user interface designer want to pursue further?",
    "TS3009b.d03": "In TS3009b, which design philosophy did the participants prefer?",
    "TS3009b.d06": "In TS3009b, did the team include a dedicated control for entering two-digit channel numbers?",
    "TS3009c.d04": "In TS3009c, where on the remote did the user interface designer say the screen should go?",
    "TS3009c.d06": "In TS3009c, what extra feature did the project manager mention while reviewing the function list?",
    "TS3009c.d08": "In TS3009c, what finishing touch was suggested for the front of the remote?",
    "TS3009d.d01": "In TS3009d, where did the team decide to leave the display, given the prototype they looked at?",
    "TS3009d.d03": "In TS3009d, what happened to the titanium idea?",
}
"""The hand-authored English questions, keyed by gold `decision_id`: the question
text only (written from the row's subject and the meeting, never saying the
value). The English glosses used to score are NOT here: they paraphrase private
gold annotations, so they load at run time from a gitignored local file."""

_CHILD: Final = re.compile(r"-\d+$")


def _norm(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text.casefold())
    return "".join(ch for ch in folded if not unicodedata.combining(ch))


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", _norm(text))


def _stems(text: str) -> tuple[str, ...]:
    out: list[str] = []
    for tok in _tokens(text):
        if tok in _STOP:
            continue
        if tok.isdigit():
            stem = tok
        elif len(tok) >= 4:
            stem = tok[:STEM_LEN]
        else:
            continue
        if stem not in out:
            out.append(stem)
    return tuple(out)


def stems_en(text: str) -> tuple[str, ...]:
    """The scored stems of an English text: lower-cased, accents removed,
    function words dropped, words of four or more letters cut to `STEM_LEN`,
    digit tokens and 2 to 3 letter ACRONYMS (written in capitals) kept whole."""
    folded = "".join(
        ch
        for ch in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(ch)
    )
    out: list[str] = []
    for raw in re.findall(r"[A-Za-z0-9]+", folded):
        tok = raw.lower()
        if tok in _STOP_EN:
            continue
        if tok.isdigit() or (raw.isupper() and raw.isalpha() and 2 <= len(raw) <= 3):
            stem = tok
        elif len(tok) >= 4:
            stem = tok[:STEM_LEN]
        else:
            continue
        if stem not in out:
            out.append(stem)
    return tuple(out)


def leaks(question: str, stems: Sequence[str]) -> list[str]:
    """The scored value stems the question already says. A whole-token stem
    (digit, acronym) leaks on an equal token, a word stem on a shared stem."""
    return [s for s in stems if s in set(stems_en(question))]


def value_stems(object_: str, content: str) -> tuple[str, ...]:
    """The stems of the gold value: those of the content the question itself
    (the object) does not already say."""
    said = set(_stems(object_))
    return tuple(s for s in _stems(content) if s not in said)


def value_hit(answer: str, stems: Sequence[str]) -> bool:
    """At least `STEM_MATCH_SHARE` of the stems appear in the answer. A digit
    or acronym stem matches a whole token; a word stem matches a token prefix."""
    if not stems:
        return False
    toks = _tokens(answer)
    tokset = set(toks)
    hits = sum(
        1
        for stem in stems
        if (
            stem in tokset
            if stem.isdigit() or len(stem) < 4
            else any(t.startswith(stem) for t in toks)
        )
    )
    return hits >= max(1, math.ceil(STEM_MATCH_SHARE * len(stems)))


def meeting_of(concept_id: str) -> str:
    head, _, rest = concept_id.partition("/")
    return rest.split("-")[0] if head == "decisions" else rest


def _stream(text: str) -> tuple[str, ...]:
    return tuple(tok for line in text.split("\n") for tok in scoring.act_tokens(line))


def _holds_fact(fact: Fact, text: str) -> bool:
    """Whether `text` holds the evidence of `fact`: one gold act of at least
    `MIN_MATCH_TOKENS` tokens, verbatim or under the matching rule of
    `evals/transcript_decisions/scoring.py` (language-free: the gold value is
    Spanish, the Source English, the evidence is the English line)."""
    stream = _stream(text)
    joined = " " + " ".join(stream) + " "
    for act in fact.acts:
        if len(act) < scoring.MIN_MATCH_TOKENS:
            continue
        if " " + " ".join(act) + " " in joined:
            return True
        if scoring._tokens_match(stream, act):
            return True
    return False


def _fact(row: Row) -> Fact:
    acts = tuple(
        tokens
        for part in row.evidence.split(" | ")
        if (tokens := scoring.act_tokens(part))
    )
    return Fact(row.decision_id, row.meeting, row.object_, row.content, acts)


def _is_decision_row(row: Row) -> bool:
    return (
        row.status == "decision"
        and _CHILD.search(row.decision_id) is None
        and bool(row.object_.strip())
        and bool(row.content.strip())
    )


def build_data(
    records: Sequence[Mapping[str, str]],
    bodies: Mapping[str, str],
    authored: Mapping[str, str] | None = None,
    glosses: Mapping[str, str] | None = None,
) -> Data:
    rows = tuple(
        Row(
            decision_id=(r.get("decision_id") or "").strip(),
            meeting=(r.get("meeting_id") or "").strip()
            or scoring.meeting_from_decision_id((r.get("decision_id") or "").strip()),
            status=(r.get("final_status") or "").strip(),
            object_=(r.get("final_object") or "").strip(),
            content=(r.get("final_content") or "").strip(),
            evidence=(r.get("evidence_text") or "").strip(),
        )
        for r in records
    )
    return Data(
        dict(bodies),
        rows,
        dict(authored or {}),
        None if glosses is None else dict(glosses),
    )


@functools.lru_cache(maxsize=16)
def _derive(data: Data) -> tuple[GoldQuestion, ...]:
    objects = Counter((r.meeting, _norm(r.object_).strip()) for r in data.rows)
    out: list[GoldQuestion] = []
    for meeting in MEETINGS:
        body = data.bodies.get(meeting)
        if body is None:
            continue
        pool: list[GoldQuestion] = []
        for row in data.rows:
            if row.meeting != meeting or not _is_decision_row(row):
                continue
            stems = value_stems(row.object_, row.content)
            if len(stems) < MIN_VALUE_STEMS:
                continue
            if objects[(meeting, _norm(row.object_).strip())] != 1:
                continue
            fact = _fact(row)
            if not _holds_fact(fact, body):
                continue
            text = data.authored.get(row.decision_id, "")
            gloss = (data.glosses or {}).get(row.decision_id, "")
            pool.append(GoldQuestion(fact, stems_en(gloss), text, gloss))
        pool.sort(
            key=lambda q: hashlib.sha256(
                f"{SELECTION_SALT}:{q.fact.decision_id}".encode()
            ).hexdigest()
        )
        out.extend(sorted(pool[:PER_MEETING], key=lambda q: q.fact.decision_id))
    return tuple(out)


def derive_questions(data: Data) -> list[GoldQuestion]:
    """The point-fact questions, derived from the gold rows alone: per meeting,
    the `PER_MEETING` eligible rows first in SHA-256 order of
    `SELECTION_SALT:decision_id` (no choice by hand), listed in meeting and
    decision-id order. Eligible: a `decision` row, not a split child, with an
    object and a content, at least `MIN_VALUE_STEMS` value stems, an object no
    other gold row of the meeting shares, and its evidence found in its own
    transcript. The selection reads the gold rows alone: the authored English
    question and gloss are attached afterwards and never change who is chosen."""
    return list(_derive(data))


def authored_problems(data: Data) -> list[str]:
    """Every reason the authored English questions are refused, model-free: a
    selected row without a question, a gloss with fewer than `MIN_GLOSS_STEMS`
    scoring stems, a question that already says a scored value stem (a leak),
    or a question for a row that is not selected. The gloss checks need the
    glosses file and are skipped when `data.glosses` is None."""
    qs = derive_questions(data)
    chosen = {q.fact.decision_id for q in qs}
    problems: list[str] = []
    for q in qs:
        did = q.fact.decision_id
        if not q.text.strip():
            problems.append(f"{did}: no authored question")
            continue
        if data.glosses is None:
            continue
        if len(q.stems) < MIN_GLOSS_STEMS:
            problems.append(f"{did}: the gloss has no scoring stem")
        said = leaks(q.text, q.stems)
        if said:
            problems.append(f"{did}: the question leaks the value stems {said}")
    problems.extend(
        f"{did}: an authored question for a row that is not selected"
        for did in sorted(set(data.authored) - chosen)
    )
    return problems


def build_items(data: Data) -> list[msf.Item]:
    qs = derive_questions(data)
    out = [
        msf.Item(f"q{n:02d}-en", msf.LOSS, msf.B_LOSS, q.text, q)
        for n, q in enumerate(qs, 1)
    ]
    out += [
        msf.Item(f"q{n:02d}i-en", msf.INTACT, msf.B_INTACT, q.text, q)
        for n, q in enumerate(qs, 1)
    ]
    out += [
        msf.Item(aid, msf.UNANS, msf.B_LOSS, text, None) for aid, text, _, _ in ABSENT
    ]
    return out


def absent_problems(
    bodies: Mapping[str, str],
    absent: Sequence[tuple[str, str, str, str]] = ABSENT,
) -> list[str]:
    lines = [line for body in bodies.values() for line in body.split("\n")]
    problems: list[str] = []
    for aid, _, topic, forbidden in absent:
        on_topic = [ln for ln in lines if re.search(topic, ln, re.IGNORECASE)]
        if not on_topic:
            problems.append(f"{aid}: no Source mentions the topic")
        if any(re.search(forbidden, ln, re.IGNORECASE) for ln in on_topic):
            problems.append(f"{aid}: a topic line states a value")
    return problems


def fire_min_for(n_loss_cells: int) -> int:
    """The exposure floor: a sixth of the loss cells (the first registration's
    6 of 36)."""
    return math.ceil(n_loss_cells / FIRE_SHARE_DENOMINATOR)


class _Window:
    context_window = msf.CONTEXT_WINDOW
    max_generation_tokens = DEFAULT_MAX_GENERATION_TOKENS


def exposure_report(
    bodies: Mapping[str, str],
    texts: Sequence[str],
    questions: Sequence[GoldQuestion] = (),
) -> dict[str, Any]:
    """The registered model-free exposure precondition.

    The prompt budget is the shipped one (`fallback_budget`, the most generous
    over the questions); a Source EXCEEDS its share when its body is longer
    than the share the fallback gives it (`budget / FALLBACK_SOURCES`, the
    widest share of either arm). The precondition passes when at least
    `EXCEED_MIN` of the Sources exceed it. The oracle reach (readout, no bar)
    is the share of questions whose evidence survives the query-aware excerpt
    of ITS OWN Source at the fallback share and at the shipped share
    (`budget / (limit // 2)`): an upper bound on reach, retrieval aside."""
    labels = [
        msf.label_of(msf.Source(f"sources/{m}", f"AMI meeting {m}", ""))
        for m in MEETINGS[:3]
    ]
    budget = max(msf.fallback_budget(_Window(), t, labels) for t in texts)
    fb_share = budget // int(msf.BARS["FALLBACK_SOURCES"])
    shipped_share = budget // max(1, DEFAULT_QUERY_LIMIT // 2)
    sizes = {m: len(b) for m, b in bodies.items()}
    exceeding = sum(1 for n in sizes.values() if n > fb_share)
    share_exceeding = exceeding / len(sizes) if sizes else 0.0
    ordered = sorted(sizes.values())

    def oracle(share: int) -> float:
        if not questions:
            return 0.0
        hits = 0
        for q in questions:
            source = msf.Source(
                f"sources/{q.fact.meeting}",
                f"AMI meeting {q.fact.meeting}",
                bodies[q.fact.meeting],
            )
            block = msf.bound_sources([source], q.text, share)[0]
            hits += 1 if _holds_fact(q.fact, block["text"]) else 0
        return hits / len(questions)

    return {
        "budget": budget,
        "fallback_share": fb_share,
        "shipped_share": shipped_share,
        "sources": len(sizes),
        "size_min": ordered[0] if ordered else 0,
        "size_median": statistics.median(ordered) if ordered else 0,
        "size_max": ordered[-1] if ordered else 0,
        "median_size_over_fallback_share": (
            statistics.median(ordered) / fb_share if ordered and fb_share else 0.0
        ),
        "sizes": sizes,
        "exceeding": exceeding,
        "share_exceeding": share_exceeding,
        "passes": share_exceeding >= EXCEED_MIN,
        "oracle_reach_fallback": oracle(fb_share),
        "oracle_reach_shipped": oracle(shipped_share),
    }


def _digest(payload: Mapping[str, Any]) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def core_payload(data: Data) -> dict[str, Any]:
    """Everything the fixture holds except the glosses: items, the gold
    question rows with their Spanish stems and the authored questions, the
    absent-fact questions, every `Decision` page row and every Source body."""
    return {
        "items": [[i.id, i.cond, i.bundle, i.text] for i in build_items(data)],
        "gold": [
            [
                q.fact.decision_id,
                q.fact.object_,
                q.fact.content,
                list(value_stems(q.fact.object_, q.fact.content)),
                q.text,
            ]
            for q in derive_questions(data)
        ],
        "absent": [list(a) for a in ABSENT],
        "decisions": sorted(
            [r.decision_id, r.object_, r.content]
            for r in data.rows
            if _is_decision_row(r)
        ),
        "sources": {
            m: hashlib.sha256(b.encode("utf-8")).hexdigest()
            for m, b in sorted(data.bodies.items())
        },
    }


def core_hash(data: Data) -> str:
    """The gloss-free component of the fixture hash: reproducible by anyone
    holding the gold CSVs and the transcripts."""
    return _digest(core_payload(data))


def fixture_hash(data: Data) -> str:
    """The full fixture hash: the core plus every gloss and its scored stems.
    It needs the glosses file; without one the gloss part is empty and the
    value is not the registered one."""
    return _digest(
        {
            "core": core_hash(data),
            "glosses": [
                [q.fact.decision_id, q.gloss, list(q.stems)]
                for q in derive_questions(data)
            ],
        }
    )


def score(
    item: msf.Item, text: str, cited: Sequence[str], outcome: str
) -> dict[str, Any]:
    q = item.question
    if q is None:
        return {"correct": False, "foreign_value": False}
    answer = text.removeprefix(msf.SOURCES_BANNER)
    meetings = {meeting_of(cid) for cid in cited}
    foreign = bool(meetings - {q.fact.meeting})
    correct = (
        outcome == "ok"
        and bool(cited)
        and value_hit(answer, q.stems)
        and q.fact.meeting in meetings
        and not foreign
    )
    return {"correct": correct, "foreign_value": foreign}


def build_bundle(data: Data, kind: str, root: pathlib.Path) -> pathlib.Path:
    bundle = root / "bundle"
    for meeting in MEETINGS:
        path = bundle / "sources" / f"{meeting}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        meta: dict[str, object] = {
            "type": "Source",
            "title": f"AMI meeting {meeting}",
            "sensitivity": "private",
        }
        path.write_text(
            okf.dump_frontmatter(meta) + data.bodies[meeting], encoding="utf-8"
        )
    if kind == msf.B_INTACT:
        for row in sorted(data.rows, key=lambda r: r.decision_id):
            if not _is_decision_row(row):
                continue
            path = bundle / "decisions" / f"{row.decision_id.replace('.', '-')}.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            dmeta: dict[str, object] = {
                "type": "Decision",
                "title": row.object_,
                "sensitivity": "private",
                "provenance": [f"sources/{row.meeting}"],
            }
            path.write_text(
                okf.dump_frontmatter(dmeta) + f"# {row.object_}\n\n{row.content}\n",
                encoding="utf-8",
            )
    return bundle


def install(data: Data, *, fire_min: int | None = None) -> None:
    """Point the shared harness at this fixture (the seam `msf.HOOKS`)."""
    msf.BARS["FIRE_MIN"] = FIRE_MIN_LONG if fire_min is None else fire_min
    items_now = build_items(data)
    msf.HOOKS = msf.Hooks(
        items=lambda: list(items_now),
        fixture_sha256=lambda: fixture_hash(data),
        build_bundle=lambda kind, root: build_bundle(data, kind, root),
        holds=lambda item, text: (
            item.question is not None and _holds_fact(item.question.fact, text)
        ),
        score=score,
        kinds=(msf.B_LOSS, msf.B_INTACT),
    )


def refuse_duplicate_cells(cells: Sequence[Mapping[str, Any]]) -> None:
    seen = Counter((c["item"], c["run"]) for c in cells)
    dups = sorted(k for k, n in seen.items() if n > 1)
    if dups:
        raise SystemExit(
            f"REFUSED: duplicate cells {dups}; the report reads one final store"
        )


def check_store_fixture(store: Mapping[str, Any], expected: str) -> None:
    got = store.get("fixture_sha256")
    if got != expected:
        raise SystemExit(
            f"REFUSED: the store was written for fixture {got}, not {expected}"
        )


def load_glosses(path: pathlib.Path | None) -> dict[str, str] | None:
    """The English glosses, `{decision_id: gloss}`, from a gitignored local file
    (they paraphrase private gold annotations and are not versioned). None when
    there is no file."""
    if path is None or not path.is_file():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in raw.items()
    ):
        raise SystemExit(f"{path}: expected a JSON object of decision_id to gloss")
    return dict(raw)


def load_data(
    gold_dir: pathlib.Path,
    transcripts: pathlib.Path,
    glosses: pathlib.Path | None = None,
) -> Data:
    paths = [gold_dir / f"{s}.decisions.adjudicated.csv" for s in SERIES]
    for path in paths:
        if not path.is_file():
            raise SystemExit(f"no gold file: {path}")
    mdw.verify_gold(paths)
    scoring.require_dev_meetings(MEETINGS)
    records: list[dict[str, str]] = []
    for path in paths:
        with path.open(encoding="utf-8", newline="") as handle:
            records.extend(csv.DictReader(handle))
    bodies: dict[str, str] = {}
    for meeting in MEETINGS:
        path = transcripts / f"{meeting}.md"
        if not path.is_file():
            raise SystemExit(f"no transcript for {meeting}: {path}")
        meta, body = okf.load_frontmatter(path.read_text(encoding="utf-8"))
        if not meta:
            raise SystemExit(f"{path.name}: no frontmatter; not a rendered transcript")
        bodies[meeting] = body
    return build_data(records, bodies, QUESTIONS_EN, load_glosses(glosses))


def reach_gate(reach: float) -> str | None:
    """The registered model-free reach gate: with the right Source chosen, the
    share of answerable questions whose excerpt at the fallback share holds the
    gold evidence must be at least `ORACLE_REACH_MIN`. Below it nothing the
    fallback does can be measured, so no model is called."""
    if reach < ORACLE_REACH_MIN:
        return (
            f"NO-REACH-PRE: the oracle reach {reach:.3f} at the fallback share is "
            f"below {ORACLE_REACH_MIN}"
        )
    return None


def reach_problems(report: Mapping[str, Any]) -> list[str]:
    problem = reach_gate(float(report["oracle_reach_fallback"]))
    return [problem] if problem else []


def preconditions(data: Data) -> tuple[dict[str, Any], list[str]]:
    """The exposure report plus every reason the registration refuses to
    explore. The size precondition, the oracle reach and the core hash need no
    glosses; the leak check and the full hash need the glosses file and are
    reported as skipped without it (`gate_problems` then refuses a run)."""
    qs = derive_questions(data)
    texts = [i.text for i in build_items(data) if i.cond == msf.LOSS]
    report = exposure_report(data.bodies, texts, qs)
    core = core_hash(data)
    implied = fire_min_for(len(qs) * len(msf.RUNS))
    problems = absent_problems(data.bodies) + authored_problems(data)
    problems += reach_problems(report)
    if not report["passes"]:
        problems.append(
            f"NO-EXPOSURE: {report['share_exceeding']:.2f} of the Sources exceed "
            f"their share, below {EXCEED_MIN}"
        )
    if core != REGISTERED_CORE_SHA256:
        problems.append(f"core fixture hash {core} is not the registered one")
    full: str = SKIPPED_NO_GLOSSES
    if data.glosses is not None:
        full = fixture_hash(data)
        if full != REGISTERED_FIXTURE_SHA256:
            problems.append(f"fixture hash {full} is not the registered one")
    if implied != FIRE_MIN_LONG:
        problems.append(f"FIRE_MIN {FIRE_MIN_LONG} is not {implied} for {len(qs)} q")
    report.update(
        {
            "questions": len(qs),
            "per_meeting": dict(Counter(q.fact.meeting for q in qs)),
            "core_sha256": core,
            "fixture_sha256": full,
            "leak_check": "run" if data.glosses is not None else SKIPPED_NO_GLOSSES,
            "fire_min": FIRE_MIN_LONG,
            "fire_min_implied": implied,
        }
    )
    return report, problems


def gate_problems(data: Data) -> list[str]:
    """What `run` and `fallback` refuse on: every precondition, and a missing
    glosses file (the answers cannot be scored without it)."""
    _, problems = preconditions(data)
    if data.glosses is None:
        problems.append("no glosses file (pass --glosses PATH)")
    return problems


# --------------------------------------------------------------------------- #
# Commands                                                                     #
# --------------------------------------------------------------------------- #


def _configure(args: argparse.Namespace) -> Data:
    data = load_data(args.gold_dir, args.transcripts, args.glosses)
    install(data)
    return data


def cmd_exposure(args: argparse.Namespace) -> int:
    data = _configure(args)
    report, problems = preconditions(data)
    report["problems"] = problems
    print(json.dumps(report, indent=1, ensure_ascii=False))
    return 0 if not problems else 2


def _gate(args: argparse.Namespace) -> None:
    data = _configure(args)
    problems = gate_problems(data)
    if problems:
        raise SystemExit("REFUSED: " + "; ".join(problems))


def cmd_run(args: argparse.Namespace) -> int:
    _gate(args)
    args.cap_hours = args.cap_hours if args.cap_hours is not None else CAP_HOURS_RUN
    return msf.cmd_run(args)


def cmd_futility(args: argparse.Namespace) -> int:
    _configure(args)
    return msf.cmd_futility(args)


def cmd_fallback(args: argparse.Namespace) -> int:
    _gate(args)
    args.cap_hours = (
        args.cap_hours if args.cap_hours is not None else CAP_HOURS_FALLBACK
    )
    return msf.cmd_fallback(args)


def _read_store(args: argparse.Namespace) -> None:
    data = _configure(args)
    if data.glosses is None:
        raise SystemExit("REFUSED: no glosses file (pass --glosses PATH)")
    store = msf._read(msf._cells_path(args.out))
    refuse_duplicate_cells(store["cells"])
    check_store_fixture(store, fixture_hash(data))


def cmd_score(args: argparse.Namespace) -> int:
    _read_store(args)
    return msf.cmd_score(args)


def cmd_decide(args: argparse.Namespace) -> int:
    _read_store(args)
    return msf.cmd_decide(args)


def cmd_report(args: argparse.Namespace) -> int:
    _read_store(args)
    msf.cmd_score(args)
    return msf.cmd_decide(args)


# --------------------------------------------------------------------------- #
# Self-test (written first; model-free, no private data)                       #
# --------------------------------------------------------------------------- #

_OBJ: Final = ("alfa", "beta", "gama", "delta", "omega")
_MAT: Final = ("acero", "cobre", "vidrio", "carbon", "madera")


def _filler(n: int) -> str:
    return "\n".join(
        f"UI: filler line {i} about nothing in particular for the remote control."
        for i in range(n)
    )


def _evidence(m: str, k: int) -> str:
    return f"we will adopt the sturdy {_MAT[k - 1]} material number {k} for {m} casing"


def synthetic(long: bool) -> Data:
    """Twelve meetings, five clean gold rows each; `long` pads the Source. The
    authored questions are written for the rows the selection chose."""
    records: list[dict[str, str]] = []
    bodies: dict[str, str] = {}
    for m in MEETINGS:
        lines = []
        for k in range(1, 6):
            lines.append(f"PM: {_evidence(m, k)}")
            records.append(
                {
                    "decision_id": f"{m}.d{k:02d}",
                    "meeting_id": m,
                    "final_status": "decision",
                    "final_object": f"componente {_OBJ[k - 1]}",
                    "final_content": f"adoptar material {_MAT[k - 1]} resistente numero {k}",
                    "evidence_text": f"A: {_evidence(m, k)}",
                }
            )
        bodies[m] = "\n".join(lines) + "\n" + _filler(400 if long else 5)
    chosen = derive_questions(build_data(records, bodies))
    authored = {
        q.fact.decision_id: (
            f"In {q.fact.meeting}, what was chosen for part number "
            f"{int(q.fact.decision_id[-2:])}?"
        )
        for q in chosen
    }
    glosses = {
        q.fact.decision_id: f"{_MAT[int(q.fact.decision_id[-2:]) - 1]} sturdy"
        for q in chosen
    }
    return build_data(records, bodies, authored, glosses)


def _extra(data: Data) -> Data:
    """`synthetic` plus rows that must never become a question (ES2015a)."""
    m = "ES2015a"
    bad = [
        ("d06-1", "decision", "hijo", "adoptar material rojo brillante", "child"),
        ("d07", "no_decision", "nada", "adoptar material rojo brillante", "status"),
        ("d08", "decision", "repetido", "adoptar material azul brillante", "dup"),
        ("d09", "decision", "repetido", "adoptar material verde brillante", "dup"),
        ("d10", "decision", "ausente", "adoptar material negro brillante", "gone"),
        ("d11", "decision", "pobre", "rojo", "stems"),
    ]
    rows = list(data.rows)
    bodies = dict(data.bodies)
    for did, status, obj, content, tag in bad:
        text = f"{tag} sturdy line about a unique zebra {did}"
        rows.append(
            Row(
                f"{m}.{did}",
                m,
                status,
                obj,
                content,
                text if tag != "gone" else "nothing that is in the transcript ever",
            )
        )
        if tag != "gone":
            bodies[m] += f"\nPM: {text}"
    return replace(data, bodies=bodies, rows=tuple(rows))


def _t_derivation(c: msf._Checks) -> None:
    data = synthetic(True)
    qs = derive_questions(data)
    c.check(len(qs) == len(MEETINGS) * PER_MEETING, "3 questions per meeting")
    c.check(derive_questions(data) == qs, "derivation is deterministic")
    shuffled = build_data(
        [
            {
                "decision_id": r.decision_id,
                "meeting_id": r.meeting,
                "final_status": r.status,
                "final_object": r.object_,
                "final_content": r.content,
                "evidence_text": r.evidence,
            }
            for r in reversed(data.rows)
        ],
        data.bodies,
        data.authored,
        data.glosses,
    )
    c.check(derive_questions(shuffled) == qs, "derivation ignores the row order")
    ids = [it.id for it in build_items(data)]
    c.check(len(set(ids)) == len(ids), "item ids are unique")
    by_cond = Counter(it.cond for it in build_items(data))
    c.check(by_cond[msf.LOSS] == len(qs), "one loss item per question")
    c.check(by_cond[msf.INTACT] == len(qs), "one intact item per question")
    c.check(by_cond[msf.UNANS] == len(ABSENT), "one unanswerable item per absent")
    texts = {it.text for it in build_items(data) if it.cond == msf.LOSS}
    c.check(
        all(q.text in texts and q.fact.meeting in q.text for q in qs),
        "the question is the authored one and names the meeting",
    )
    extra = _extra(data)
    bad = {f"ES2015a.{d}" for d in ("d06-1", "d07", "d08", "d09", "d10", "d11")}
    got = {q.fact.decision_id for q in derive_questions(extra)}
    c.check(not (got & bad), "child, non-decision, duplicate, absent, thin rows out")
    c.check(len(derive_questions(extra)) == len(qs), "the clean rows still fill 3")
    c.check(
        all(sum(1 for q in qs if q.fact.meeting == m) == PER_MEETING for m in MEETINGS),
        "no meeting is over its cap",
    )


def _t_scoring(c: msf._Checks) -> None:
    c.check(
        value_stems("componente alfa", "adoptar material acero numero 1")
        == ("adopt", "mater", "acero", "numer", "1"),
        "residual value stems (the selection rule)",
    )
    stems = stems_en("adopt material acero resistente number 1")
    c.check(value_hit("se adoptó material acero", stems), "half the stems is a hit")
    c.check(not value_hit("no se decidió nada", stems), "no stem is a miss")
    c.check(not value_hit("material", stems), "one stem of six is a miss")
    c.check(
        value_hit("el costo es 12 euros", value_stems("costo", "maximo 12 euros")),
        "a digit stem matches whole tokens",
    )
    c.check(
        not value_hit("el costo es 125", value_stems("costo", "12 euros")),
        "a digit stem is not a prefix match",
    )
    c.check(meeting_of("sources/IS1004b") == "IS1004b", "source id to meeting")
    c.check(meeting_of("decisions/IS1004b-d02") == "IS1004b", "decision id to meeting")
    data = synthetic(True)
    install(data, fire_min=fire_min_for(36 * 3))
    q = derive_questions(data)[0]
    item = next(i for i in build_items(data) if i.question == q and i.cond == msf.LOSS)
    good = "se adoptó material " + _MAT[int(q.fact.decision_id[-2:]) - 1]
    gold_src = f"sources/{q.fact.meeting}"
    s = msf.score_item(item, good, [gold_src], "ok")
    c.check(s["correct"] and not s["foreign_value"], "value, gold cited, clean")
    s = msf.score_item(item, msf.SOURCES_BANNER + "\n" + good, [gold_src], "ok")
    c.check(s["correct"], "the provenance banner is not part of the answer")
    other = next(m for m in MEETINGS if m != q.fact.meeting)
    s = msf.score_item(item, good, [gold_src, f"sources/{other}"], "ok")
    c.check(not s["correct"] and s["foreign_value"], "another meeting cited: mixed")
    s = msf.score_item(item, good, [f"sources/{other}"], "ok")
    c.check(not s["correct"], "the gold meeting must be cited")
    c.check(not msf.score_item(item, good, [], "ok")["correct"], "no citation: wrong")
    c.check(not msf.score_item(item, good, [gold_src], "failed")["correct"], "failed")
    k = int(q.fact.decision_id[-2:])
    c.check(
        msf.HOOKS.holds(item, "PM: " + _evidence(q.fact.meeting, k)),
        "holds: the evidence act is in the text",
    )
    c.check(
        not msf.HOOKS.holds(item, "PM: something else entirely here"),
        "holds: absent evidence",
    )


def _t_exposure(c: msf._Checks) -> None:
    short, long = synthetic(False), synthetic(True)
    texts = [i.text for i in build_items(long) if i.cond == msf.LOSS]
    r_short = exposure_report(short.bodies, texts)
    r_long = exposure_report(long.bodies, texts)
    c.check(not r_short["passes"], "a short-Source fixture is refused")
    c.check(r_short["share_exceeding"] == 0.0, "no short Source exceeds its share")
    c.check(r_long["passes"], "a long-Source fixture is accepted")
    c.check(r_long["share_exceeding"] == 1.0, "every long Source exceeds its share")
    share = r_long["fallback_share"]
    c.check(
        share == r_long["budget"] // msf.BARS["FALLBACK_SOURCES"], "share = budget/3"
    )
    # the boundary: a body of exactly `share` characters does not exceed it
    edge = {m: "x" * share for m in MEETINGS}
    c.check(exposure_report(edge, texts)["share_exceeding"] == 0.0, "equal is not over")
    edge1 = {m: "x" * (share + 1) for m in MEETINGS}
    c.check(exposure_report(edge1, texts)["share_exceeding"] == 1.0, "one over is over")
    k = math.ceil(EXCEED_MIN * len(MEETINGS))
    mixed = {
        m: ("x" * (share + 1) if n < k else "x" * share) for n, m in enumerate(MEETINGS)
    }
    c.check(exposure_report(mixed, texts)["passes"], "exactly the floor passes")
    mixed[MEETINGS[k - 1]] = "x" * share
    c.check(not exposure_report(mixed, texts)["passes"], "one under the floor fails")
    c.check(fire_min_for(108) == 18 and fire_min_for(99) == 17, "fire floor is a sixth")
    qs = derive_questions(short)
    r_or = exposure_report(short.bodies, texts, qs)
    c.check(r_or["oracle_reach_fallback"] == 1.0, "a short Source is read whole")
    c.check(r_or["oracle_reach_shipped"] == 1.0, "and at the shipped share")


def _synthetic_cells(
    fired: int, gold: bool, *, loss_ids: Sequence[str]
) -> list[dict[str, Any]]:
    cells: list[dict[str, Any]] = []
    for n, iid in enumerate(loss_ids):
        plan = (
            {"source_ids": [], "budget": 100, "block_chars": 90, "gold_in_prompt": gold}
            if n < fired
            else None
        )
        cells.append(
            {
                "item": iid,
                "run": 1,
                "base": {
                    "outcome": "ok",
                    "answer": "",
                    "cited": [],
                    "cause": msf.INSUFFICIENT if plan else "none",
                    "elapsed_s": 1.0,
                },
                "plan": plan,
                "fb": None,
            }
        )
    return cells


def _summary(**kw: Any) -> dict[str, Any]:
    s: dict[str, Any] = {
        "invariant_problems": [],
        "missing": 0,
        "pending_fallbacks": 0,
        "failed_share": 0.0,
        "fired_loss": 40,
        "reach": 0.9,
        "loss_base": 0.1,
        "loss_delta": 0.4,
        "loss_lb": 0.2,
        "span_n": 10,
        "span_share": 0.9,
        "unans_base": 0.0,
        "unans_fb": 0.0,
        "precision_n": 10,
        "precision": 0.9,
        "foreign_base": 0.0,
        "foreign_fb": 0.0,
        "latency_base": 5.0,
        "latency_fb": 5.5,
    }
    s.update(kw)
    return s


def _t_stop_order(c: msf._Checks) -> None:
    data = synthetic(True)
    install(data, fire_min=fire_min_for(36 * 3))
    loss_ids = [i.id for i in build_items(data) if i.cond == msf.LOSS]
    need = int(msf.BARS["FIRE_MIN"])
    few = _synthetic_cells(need - 1, True, loss_ids=loss_ids)
    c.check(msf.futility(few) == msf.NO_EXPOSURE, "one under the floor: NO-EXPOSURE")
    enough = _synthetic_cells(need, False, loss_ids=loss_ids)
    c.check(msf.futility(enough) == msf.NO_REACH, "fired but out of reach: NO-REACH")
    ok = _synthetic_cells(need, True, loss_ids=loss_ids)
    c.check(msf.futility(ok) is None, "exposed and reachable: no stop")
    d = msf.decide
    c.check(d(_summary())["verdict"] == msf.SUPPORTED, "all bars hold: SUPPORTED")
    c.check(
        d(_summary(fired_loss=need - 1, reach=0.0, loss_base=0.9))["verdict"]
        == msf.NO_EXPOSURE,
        "NO-EXPOSURE outranks NO-REACH and NO-HEADROOM",
    )
    c.check(
        d(_summary(reach=0.0, loss_base=0.9))["verdict"] == msf.NO_REACH,
        "NO-REACH outranks NO-HEADROOM",
    )
    c.check(d(_summary(loss_base=0.9))["verdict"] == msf.NO_HEADROOM, "NO-HEADROOM")
    c.check(d(_summary(loss_delta=0.0))["verdict"] == msf.REJECTED, "REJECTED on B1")
    c.check(d(_summary(unans_fb=0.04))["failed"] == ["B3"], "B3 is strict")
    c.check(d(_summary(missing=1))["verdict"] == msf.INCONCLUSIVE, "a missing cell")
    c.check(msf.BARS["FIRE_MIN"] == fire_min_for(36 * 3), "install sets the fire floor")


def _t_hash(c: msf._Checks) -> None:
    data = synthetic(True)
    h = fixture_hash(data)
    c.check(len(h) == 64 and h == fixture_hash(synthetic(True)), "the hash is stable")
    bodies = dict(data.bodies)
    bodies["ES2015a"] += " "
    c.check(
        fixture_hash(replace(data, bodies=bodies)) != h,
        "a Source byte changes it",
    )
    rows = list(data.rows)
    rows[0] = Row(
        rows[0].decision_id,
        rows[0].meeting,
        rows[0].status,
        rows[0].object_,
        rows[0].content + " mas",
        rows[0].evidence,
    )
    c.check(
        fixture_hash(replace(data, rows=tuple(rows))) != h,
        "a gold row changes it",
    )
    first = derive_questions(data)[0].fact.decision_id
    q_edit = dict(data.authored)
    q_edit[first] += " "
    c.check(fixture_hash(replace(data, authored=q_edit)) != h, "a question changes it")
    c.check(
        core_hash(replace(data, authored=q_edit)) != core_hash(data),
        "a question changes the core hash",
    )
    g_edit = dict(data.glosses or {})
    g_edit[first] += " extra"
    c.check(fixture_hash(replace(data, glosses=g_edit)) != h, "a gloss changes it")
    c.check(
        core_hash(replace(data, glosses=g_edit)) == core_hash(data),
        "a gloss does not change the core hash",
    )
    c.check(
        core_hash(replace(data, glosses=None)) == core_hash(data),
        "the core hash needs no glosses",
    )
    c.check(fixture_hash(_extra(data)) != h, "an extra row changes it")


def _t_leak(c: msf._Checks) -> None:
    c.check(
        stems_en("25 Euros, twenty five") == ("25", "euros", "twent", "five"),
        "english stems: digits whole, words cut, function words dropped",
    )
    c.check(stems_en("TV, television") == ("tv", "telev"), "an acronym is kept whole")
    stems = stems_en("double curved")
    c.check(not leaks("What shape did the manager describe?", stems), "no overlap")
    c.check(
        leaks("Is the remote double shaped?", stems) == ["doubl"],
        "a question that says a value stem leaks",
    )
    c.check(
        leaks("Is it a TV remote?", stems_en("TV")) == ["tv"],
        "an acronym value stem leaks as a whole token",
    )
    c.check(
        not leaks("Is it a TVs remote?", stems_en("TV")),
        "an acronym stem is not a prefix match",
    )
    c.check(
        value_hit("it is a TV remote", stems_en("TV")),
        "an acronym value stem matches a whole answer token",
    )
    data = synthetic(True)
    c.check(not authored_problems(data), "a clean authored table has no problem")
    qs = derive_questions(data)
    first = qs[0].fact.decision_id
    bad = dict(data.authored)
    bad[first] += " Was it sturdy?"
    leaky = authored_problems(replace(data, authored=bad))
    c.check(
        len(leaky) == 1 and first in leaky[0] and "leaks" in leaky[0],
        "a leaking question is refused",
    )
    gone = {k: v for k, v in data.authored.items() if k != first}
    miss = authored_problems(replace(data, authored=gone))
    c.check(
        len(miss) == 1 and first in miss[0],
        "a selected row with no question is refused",
    )
    thin = dict(data.glosses or {})
    thin[first] = "the of"
    c.check(
        len(authored_problems(replace(data, glosses=thin))) == 1,
        "a gloss with no stem cannot score and is refused",
    )
    extra = dict(data.authored)
    extra["ES2015a.d99"] = "In ES2015a, what?"
    c.check(
        len(authored_problems(replace(data, authored=extra))) == 1,
        "a question for an unselected row is refused",
    )
    c.check(all(q.text for q in qs), "every question is authored")


def _t_glosses(c: msf._Checks) -> None:
    import tempfile

    data = synthetic(True)
    bare = replace(data, glosses=None)
    first = derive_questions(data)[0].fact.decision_id
    leaky = dict(bare.authored)
    leaky[first] += " Was it sturdy?"
    c.check(
        not authored_problems(replace(bare, authored=leaky)),
        "without glosses the leak check is skipped, not failed",
    )
    report, problems = preconditions(bare)
    c.check(
        report["leak_check"] == SKIPPED_NO_GLOSSES, "exposure reports no leak check"
    )
    c.check(report["fixture_sha256"] == SKIPPED_NO_GLOSSES, "and no full hash")
    c.check(report["core_sha256"] == core_hash(data), "the core hash is reported")
    c.check(
        not any("glosses" in p for p in problems),
        "exposure itself does not refuse for want of glosses",
    )
    c.check(
        any("no glosses file" in p for p in gate_problems(bare)),
        "run and fallback refuse without glosses",
    )
    c.check(
        not any("no glosses file" in p for p in gate_problems(data)),
        "and do not refuse when glosses are given",
    )
    full, _ = preconditions(data)
    c.check(full["leak_check"] == "run", "with glosses the leak check runs")
    c.check(len(full["fixture_sha256"]) == 64, "and the full hash is computed")
    with tempfile.TemporaryDirectory() as tmp:
        good = pathlib.Path(tmp) / "g.json"
        good.write_text('{"a": "b"}', encoding="utf-8")
        c.check(load_glosses(good) == {"a": "b"}, "a glosses file is read")
        c.check(load_glosses(pathlib.Path(tmp) / "none.json") is None, "no file: None")
        c.check(load_glosses(None) is None, "no path: None")
        badf = pathlib.Path(tmp) / "b.json"
        badf.write_text('{"a": 1}', encoding="utf-8")
        try:
            load_glosses(badf)
            refused = False
        except SystemExit:
            refused = True
        c.check(refused, "a glosses file that is not a string map is refused")
    c.check(
        len(QUESTIONS_EN) == 30 and all(type(v) is str for v in QUESTIONS_EN.values()),
        "the committed table holds question strings only (no gloss)",
    )
    src = pathlib.Path(__file__).read_text(encoding="utf-8")
    c.check(
        "glosses.json" in src and '"gloss"' not in src.split("QUESTIONS_EN")[1][:6000],
        "the table carries no gloss field",
    )
    c.check(
        len({q for q in QUESTIONS_EN.values()}) == len(QUESTIONS_EN),
        "the questions are distinct",
    )


def _t_reach_gate(c: msf._Checks) -> None:
    below = reach_gate(ORACLE_REACH_MIN - 0.01)
    c.check(below is not None and "NO-REACH-PRE" in below, "below the floor: refused")
    c.check(reach_gate(ORACLE_REACH_MIN) is None, "at the floor: allowed")
    c.check(reach_gate(ORACLE_REACH_MIN + 0.01) is None, "above the floor: allowed")
    c.check(reach_gate(0.0) is not None, "zero reach: refused")
    c.check(ORACLE_REACH_MIN == 0.5, "the floor is the registered one")
    data = synthetic(True)
    qs = derive_questions(data)
    texts = [i.text for i in build_items(data) if i.cond == msf.LOSS]
    rep = exposure_report(data.bodies, texts, qs)
    c.check(
        0.0 <= rep["oracle_reach_fallback"] <= 1.0
        and 0.0 <= rep["oracle_reach_shipped"] <= 1.0,
        "reach is reported at both shares",
    )
    tail = {}
    for m, b in data.bodies.items():
        ls = b.split("\n")
        tail[m] = "\n".join(
            ls[5:105] + ls[:5] + ls[105:]
        )  # evidence off the default cut
    c.check(
        exposure_report(tail, texts, qs)["oracle_reach_fallback"] >= ORACLE_REACH_MIN,
        "a question with lexical signal finds evidence deep in a long Source",
    )
    mute = Data(
        tail,
        data.rows,
        {k: "Zebra quokka marmoset?" for k in data.authored},
        data.glosses,
    )
    mq = derive_questions(mute)
    mrep = exposure_report(mute.bodies, [q.text for q in mq], mq)
    problems = reach_problems(mrep)
    c.check(
        mrep["oracle_reach_fallback"] < ORACLE_REACH_MIN
        and any("NO-REACH-PRE" in p for p in problems),
        "a question with no lexical signal is stopped before any model call",
    )
    c.check(
        not reach_problems(
            exposure_report(data.bodies, texts, qs)
            | {"oracle_reach_fallback": ORACLE_REACH_MIN}
        ),
        "reach at the floor passes through the report",
    )


def _t_absent(c: msf._Checks) -> None:
    bodies = {"ES2015a": "PM: the launch is soon\nPM: no date given"}
    ok = (("ux1", "q?", r"launch", r"january"),)
    c.check(not absent_problems(bodies, ok), "on topic, no value: clean")
    off = (("ux2", "q?", r"warranty", r"months"),)
    c.check(bool(absent_problems(bodies, off)), "a topic no Source mentions is refused")
    val = (("ux3", "q?", r"launch", r"soon"),)
    c.check(bool(absent_problems(bodies, val)), "a value on a topic line is refused")
    c.check(
        len(ABSENT) >= 6 and len({a[0] for a in ABSENT}) == len(ABSENT),
        "the absent-fact questions are registered and distinct",
    )


def _t_store(c: msf._Checks) -> None:
    def refused(call: Any) -> bool:
        try:
            call()
        except SystemExit as exc:
            return "REFUSED" in str(exc)
        return False

    dup = [{"item": "a", "run": 1}, {"item": "a", "run": 1}]
    c.check(refused(lambda: refuse_duplicate_cells(dup)), "a duplicate cell is refused")
    refuse_duplicate_cells([{"item": "a", "run": 1}, {"item": "a", "run": 2}])
    c.check(
        refused(lambda: check_store_fixture({"fixture_sha256": "x"}, "y")),
        "a store from another fixture is refused",
    )
    check_store_fixture({"fixture_sha256": "y"}, "y")
    c.check(True, "a matching store is read")


def _t_bundle(c: msf._Checks) -> None:
    import tempfile

    data = synthetic(False)
    install(data, fire_min=fire_min_for(36 * 3))
    with tempfile.TemporaryDirectory() as tmp:
        loss = msf.HOOKS.build_bundle(msf.B_LOSS, pathlib.Path(tmp) / "a")
        intact = msf.HOOKS.build_bundle(msf.B_INTACT, pathlib.Path(tmp) / "b")
        c.check(not (loss / "decisions").exists(), "the lossy bundle has no Decision")
        c.check(len(list((loss / "sources").iterdir())) == len(MEETINGS), "12 Sources")
        c.check(
            len(list((intact / "decisions").iterdir())) == len(data.rows),
            "the intact bundle has a Decision per clean gold row",
        )
        meta, body = okf.load_frontmatter(
            (intact / "decisions" / "ES2015a-d01.md").read_text(encoding="utf-8")
        )
        c.check(meta.get("type") == "Decision", "a Decision page is typed")
        c.check(meta.get("provenance") == ["sources/ES2015a"], "it cites its Source")
        c.check(
            body.strip().endswith(data.rows[0].content), "its body is the gold value"
        )
        c.check(
            [p.name for p in (loss / "sources").iterdir()].count("ES2015a.md") == 1,
            "a Source is sources/<meeting>",
        )


def _t_prereg(c: msf._Checks) -> None:
    text = (HERE / "PREREGISTRATION-long.md").read_text(encoding="utf-8")
    for key, value in msf.BARS.items():
        if key == "FIRE_MIN":
            continue
        c.check(f"{key} = {value}" in text, f"the registration states {key}")
    for name, value in (
        ("PER_MEETING", PER_MEETING),
        ("MIN_VALUE_STEMS", MIN_VALUE_STEMS),
        ("MIN_GLOSS_STEMS", MIN_GLOSS_STEMS),
        ("ORACLE_REACH_MIN", ORACLE_REACH_MIN),
        ("STEM_LEN", STEM_LEN),
        ("STEM_MATCH_SHARE", STEM_MATCH_SHARE),
        ("EXCEED_MIN", EXCEED_MIN),
        ("FIRE_MIN", FIRE_MIN_LONG),
        ("CAP_HOURS_RUN", CAP_HOURS_RUN),
        ("CAP_HOURS_FALLBACK", CAP_HOURS_FALLBACK),
    ):
        c.check(f"{name} = {value}" in text, f"the registration states {name}")
    c.check(REGISTERED_FIXTURE_SHA256 in text, "the registration states the hash")
    c.check(REGISTERED_CORE_SHA256 in text, "the registration states the core hash")
    c.check(msf.SOURCES_BANNER in text, "the registration states the banner")


def _t_mutation_guard(c: msf._Checks) -> None:
    """The lines a mutation check targets."""
    long = synthetic(True)
    texts = [i.text for i in build_items(long) if i.cond == msf.LOSS]
    share = exposure_report(long.bodies, texts)["fallback_share"]
    edge = {m: "x" * share for m in MEETINGS}
    c.check(
        not exposure_report(edge, texts)["passes"], "guard: equal share is not over"
    )
    c.check(
        exposure_report(synthetic(False).bodies, texts)["passes"] is False,
        "guard: short Sources are refused",
    )
    stems = value_stems("a", "alfa beta gama delta")
    c.check(value_hit("alfa beta", stems), "guard: half is enough")
    c.check(not value_hit("alfa", stems), "guard: under half is not")


def self_test() -> int:
    c = msf._Checks()
    for fn in (
        _t_derivation,
        _t_scoring,
        _t_exposure,
        _t_stop_order,
        _t_hash,
        _t_leak,
        _t_glosses,
        _t_reach_gate,
        _t_absent,
        _t_store,
        _t_bundle,
        _t_prereg,
        _t_mutation_guard,
    ):
        fn(c)
    if c.failures:
        print(f"{len(c.failures)} of {c.total} checks failed")
        return 1
    print(f"self-test ok: {c.total} checks")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    sub = parser.add_subparsers(dest="command")

    def add(name: str, fn: Any, help_text: str) -> argparse.ArgumentParser:
        sp = sub.add_parser(name, help=help_text)
        sp.add_argument("--gold-dir", type=pathlib.Path, required=True)
        sp.add_argument("--transcripts", type=pathlib.Path, required=True)
        sp.add_argument("--out", type=pathlib.Path, default=DEFAULT_OUT)
        sp.add_argument(
            "--glosses",
            type=pathlib.Path,
            default=DEFAULT_GLOSSES,
            help="JSON {decision_id: gloss}; a gitignored local file",
        )
        sp.add_argument("--timeout", type=float, default=600.0)
        sp.set_defaults(fn=fn, cap_hours=None)
        return sp

    add("exposure", cmd_exposure, "model-free: the registered precondition and hash")
    r = add("run", cmd_run, "model-bound: the shipped answers and the fallback plans")
    r.add_argument("--runs", type=int, nargs="+", default=list(msf.RUNS))
    r.add_argument("--cap-hours", dest="cap_hours", type=float, default=None)
    add("futility", cmd_futility, "model-free: the exposure and reach stops")
    f = add("fallback", cmd_fallback, "model-bound: re-check and synthesis")
    f.add_argument("--cap-hours", dest="cap_hours", type=float, default=None)
    add("score", cmd_score, "model-free: every metric, no bar applied")
    add("decide", cmd_decide, "model-free: the registered bars")
    add("report", cmd_report, "model-free: score then decide, final store only")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    if not getattr(args, "fn", None):
        parser.error("choose a subcommand or --self-test")
    return int(args.fn(args))


if __name__ == "__main__":
    raise SystemExit(main())
