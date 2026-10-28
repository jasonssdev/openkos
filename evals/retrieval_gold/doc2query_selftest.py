"""Model-free checks for the #1650 pre-run code (`gold_doc2query`).

Shared by `generate_questions.py --self-test` (the generator, the output
handling, the cost) and `run_retrieval_gold.py --self-test` (the arms through the
production retrieval seams). Nothing here contacts a model: the chat model is a
scripted stub and the embedder is a hashed bag of words.

Each check takes a `c` with `check(ok, label)`; a registered constant is pinned
here against the REGISTRATION TEXT, not against the module that defines it.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import math
import pathlib
import re
import tempfile
from collections.abc import Sequence
from typing import Any, Final, Protocol

import gold_doc2query as d2q
import gold_prereg as pre

from openkos.llm.base import EMBED_DIM, BackendError, BackendUnavailable
from openkos.retrieval import fusion
from openkos.state.vectorstore import open_vector_store

HERE: Final = pathlib.Path(__file__).resolve().parent
REGISTRATION: Final = HERE / "PREREGISTRATION-1650.md"
QUESTIONS_DRAFT: Final = HERE / "questions.draft.json"


class Checks(Protocol):
    def check(self, ok: bool, label: str) -> None: ...


class HashEmbedder:
    """Deterministic hashed bag-of-words vectors. Not a model."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        out: list[list[float]] = []
        for text in texts:
            vec = [0.0] * EMBED_DIM
            for word in re.findall(r"\w+", text.lower()):
                digest = hashlib.sha256(word.encode()).digest()
                vec[int.from_bytes(digest[:4], "big") % EMBED_DIM] += 1.0
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            out.append([v / norm for v in vec])
        return out


class ScriptedChat:
    """Replies from a script, in order; records every message list it was sent."""

    def __init__(self, replies: Sequence[str | BaseException]) -> None:
        self._replies = list(replies)
        self.sent: list[list[dict[str, str]]] = []

    def chat(self, messages: Sequence[dict[str, str]]) -> d2q.Reply:
        self.sent.append([dict(m) for m in messages])
        if not self._replies:
            raise AssertionError("the stub chat ran out of replies")
        item = self._replies.pop(0)
        if isinstance(item, BaseException):
            raise item
        return d2q.Reply(text=item, eval_count=len(item.split()))


def _fenced_blocks(text: str) -> list[str]:
    return re.findall(r"```text\n(.*?)\n```", text, flags=re.DOTALL)


def registered_prompts(c: Checks) -> None:
    blocks = _fenced_blocks(REGISTRATION.read_text("utf-8"))
    c.check(
        len(blocks) >= 2, f"the registration has its two prompt blocks: {len(blocks)}"
    )
    if len(blocks) < 2:
        return
    c.check(blocks[0] == d2q.P1_SYSTEM, "P1 is the registered system message, verbatim")
    c.check(blocks[1] == d2q.USER_TEMPLATE, "the user message is the registered one")
    sentence = (
        "Describe what the person wants to know; do not copy the distinctive "
        "words of the decision when an ordinary description of the topic would do."
    )
    last = "Reply with a JSON array of exactly 3 strings and nothing else."
    c.check(d2q.P1_SYSTEM.endswith(last), "P1 ends with the reply-format sentence")
    c.check(
        d2q.P1_SYSTEM.replace(last, f"{sentence} {last}") == d2q.P2_SYSTEM,
        "P2 is P1 with the sentence inserted before the last one",
    )
    c.check(
        d2q.P2_SYSTEM.count(sentence) == 1 and len(d2q.P2_SYSTEM) > len(d2q.P1_SYSTEM),
        "P2 carries the sentence exactly once",
    )
    c.check(d2q.MODEL == "qwen3:8b", "the registered model is qwen3:8b")
    c.check(d2q.SEED == 1650, "the registered seed is 1650")
    msgs = d2q.build_messages(d2q.P1_SYSTEM, "T", "Body line.")
    c.check(
        msgs
        == [
            {"role": "system", "content": d2q.P1_SYSTEM},
            {"role": "user", "content": "Decision title: T\n\nDecision:\nBody line."},
        ],
        "messages are the system prompt and the filled user template, nothing else",
    )
    c.check(
        d2q.generation_key(d2q.P1_SYSTEM, "T", "b")
        != d2q.generation_key(d2q.P2_SYSTEM, "T", "b"),
        "the key changes with the prompt",
    )
    c.check(
        d2q.generation_key(d2q.P1_SYSTEM, "T", "b")
        != d2q.generation_key(d2q.P1_SYSTEM, "T", "b2"),
        "the key changes with the Decision text",
    )
    c.check(
        d2q.generation_key(d2q.P1_SYSTEM, "T", "b")
        == d2q.generation_key(d2q.P1_SYSTEM, "T", "b"),
        "the key is stable",
    )


def parsing(c: Checks) -> None:
    ok = '["What is A?", "How is B done?", "Why C?"]'
    c.check(
        d2q.parse_string_array(ok) == ["What is A?", "How is B done?", "Why C?"],
        "array",
    )
    c.check(d2q.parse_string_array(f"```json\n{ok}\n```") is not None, "a fenced array")
    c.check(
        d2q.parse_string_array(f"Here you go: {ok} Hope it helps") is not None,
        "embedded",
    )
    c.check(d2q.parse_string_array("not json") is None, "prose is a parse failure")
    c.check(d2q.parse_string_array('{"a": 1}') is None, "an object is not an array")
    c.check(
        d2q.parse_string_array('["ok?", 3]') is None,
        "a non-string element fails closed",
    )
    c.check(d2q.parse_string_array("[]") == [], "an empty array parses, to nothing")
    c.check(d2q.parse_string_array(None) is None, "a non-string reply fails closed")


def _es() -> str:
    return "Se decidió usar una base de datos local para guardar las lecturas de las estaciones."


def _en() -> str:
    return "The team decided to use a local database to store the readings of the stations."


def selection(c: Checks) -> None:
    en = d2q.select_questions(
        [
            "Which database stores the readings of the stations?",
            "Why was a local database chosen for the readings?",
            "What did the team decide about where the data is kept?",
        ],
        _en(),
    )
    c.check(
        len(en.kept) == 3 and not any(en.dropped.values()), f"three clean items: {en}"
    )
    c.check(en.language == "en", "the Decision's language is the function-word vote")

    short = d2q.select_questions(
        ["Why?", "What now?", "What is the database for?"], _en()
    )
    c.check(short.kept == ["What is the database for?"], f"4 to 25 words: {short.kept}")
    c.check(short.dropped["length"] == 2, "too-short items are counted")
    long = "What " + " ".join(["very"] * 25) + " long question is this?"
    c.check(
        d2q.select_questions([long], _en()).dropped["length"] == 1, "26+ words dropped"
    )
    words = [
        "What",
        "is",
        "the",
        "reason",
        "for",
        "the",
        "database",
        "of",
        "the",
        "stations",
        "and",
        "the",
        "team",
    ]
    at_edge = " ".join([*words, *["of", "the"] * 6]) + "?"
    c.check(len(at_edge.split()) == 25, "fixture: exactly 25 words")
    c.check(
        d2q.select_questions([at_edge], _en()).kept == [at_edge], "25 words is kept"
    )
    twenty_six = " ".join([*words, *["of", "the"] * 6, "end"]) + "?"
    c.check(len(twenty_six.split()) == 26, "fixture: exactly 26 words")
    c.check(
        d2q.select_questions([twenty_six], _en()).dropped["length"] == 1,
        "26 words is dropped",
    )
    three = "What is that?"
    c.check(
        d2q.select_questions([three], _en()).dropped["length"] == 1,
        "3 words is dropped",
    )
    four = "Which database is used?"
    c.check(len(four.split()) == 4, "fixture: exactly 4 words")
    c.check(d2q.select_questions([four], _en()).kept == [four], "4 words is kept")

    no_mark = d2q.select_questions(["Tell me which database is used"], _en())
    c.check(
        no_mark.kept == [] and no_mark.dropped["no_question_mark"] == 1, "must end in ?"
    )
    c.check(
        d2q.select_questions(["Which database is used?  "], _en()).kept
        == ["Which database is used?"],
        "items are stripped before they are judged",
    )

    wrong = d2q.select_questions(
        ["¿Qué base de datos guarda las lecturas de las estaciones?"], _en()
    )
    c.check(
        wrong.kept == [] and wrong.dropped["language"] == 1,
        "a Spanish item for an English Decision",
    )
    es = d2q.select_questions(
        ["¿Qué base de datos guarda las lecturas de las estaciones?"], _es()
    )
    c.check(
        es.language == "es" and len(es.kept) == 1,
        "a Spanish item for a Spanish Decision",
    )
    none = d2q.select_questions(["Which database is used?"], "MQTT QoS 1 LiFePO4")
    c.check(
        none.language is None and len(none.kept) == 1,
        "no vote on the Decision: no language gate",
    )
    novote = d2q.select_questions(["MQTT QoS 1 LiFePO4 broker settings?"], _en())
    c.check(
        novote.kept == [] and novote.dropped["language"] == 1,
        "an item with no vote is not the language",
    )

    dup = d2q.select_questions(
        [
            "Which database stores the readings of the stations?",
            "Which database stores the readings of the stations now?",
            "Why was a local database chosen for the readings?",
        ],
        _en(),
    )
    c.check(
        len(dup.kept) == 2 and dup.dropped["duplicate"] == 1,
        f"Jaccard >= 0.8 collapses: {dup.kept}",
    )
    c.check(
        dup.kept[0] == "Which database stores the readings of the stations?",
        "the first of a collapsed pair survives",
    )
    near = d2q.select_questions(
        [
            "Which database stores the readings of the stations?",
            "Which database stores the data of the stations?",
        ],
        _en(),
    )
    c.check(len(near.kept) == 2, "Jaccard under 0.8 is not collapsed")
    c.check(d2q.jaccard("a b c d", "a b c e") == 3 / 5, "Jaccard of word sets")
    c.check(d2q.jaccard("", "") == 0.0, "two empty sets share nothing")

    c.check(
        d2q.accepted(d2q.select_questions(["Which database is used?"], _en())) == [],
        "fewer than two kept items: the Decision gets none",
    )
    two = d2q.select_questions(
        ["Which database is used?", "Why was it chosen over others?"], _en()
    )
    c.check(len(d2q.accepted(two)) == 2, "two kept items is enough")


def _decisions() -> list[d2q.DecisionText]:
    return [
        d2q.DecisionText("decisions/db", "Local database", _en()),
        d2q.DecisionText("decisions/bd", "Base local", _es()),
        d2q.DecisionText(
            "decisions/radio", "Radio", "The team decided to use a radio link."
        ),
    ]


GOOD_EN = json.dumps(
    [
        "Which database stores the readings of the stations?",
        "Why was a local database chosen for the readings?",
        "What did the team decide about where the data is kept?",
    ]
)
GOOD_ES = json.dumps(
    [
        "¿Qué base de datos guarda las lecturas de las estaciones?",
        "¿Por qué se eligió una base de datos local para las lecturas?",
        "¿Qué decidió el equipo sobre dónde se guardan los datos?",
    ]
)


def _clock() -> Any:
    ticks = iter(range(0, 10_000, 2))
    return lambda: float(next(ticks))


def generation(c: Checks) -> None:
    gold = json.loads(QUESTIONS_DRAFT.read_text("utf-8"))["questions"]
    gold_texts = [q["question"] for q in gold]
    params = set(inspect.signature(d2q.generate).parameters)
    c.check(
        not params & {"questions", "gold", "judgments", "gold_questions"},
        f"generate has no way to receive a question: {sorted(params)}",
    )

    chat = ScriptedChat([GOOD_ES, GOOD_EN, "garbage", "still garbage"])
    result = d2q.generate(
        _decisions(), chat, system=d2q.P1_SYSTEM, prompt_id="p1", clock=_clock()
    )
    c.check(
        len(chat.sent) == 4, f"3 Decisions, one of them retried once: {len(chat.sent)}"
    )
    wire = json.dumps(chat.sent)
    c.check(not any(g in wire for g in gold_texts), "no gold question is on the wire")
    c.check(
        all(m[0] == {"role": "system", "content": d2q.P1_SYSTEM} for m in chat.sent),
        "every call carries P1 verbatim",
    )
    c.check(
        chat.sent[2] == chat.sent[3],
        "the retry is the SAME prompt, never a second one",
    )
    c.check(
        chat.sent[1][1]["content"].endswith("Decision:\n" + _en()),
        "the user message holds the title and the stored body",
    )
    by = {e["decision"]: e for e in result.entries.values()}
    c.check(
        by["decisions/db"]["attempts"] == 1 and len(by["decisions/db"]["kept"]) == 3,
        "clean",
    )
    c.check(by["decisions/bd"]["language"] == "es", "the Spanish Decision is voted es")
    radio = by["decisions/radio"]
    c.check(
        radio["attempts"] == 2 and radio["failed"] is True,
        "two parse failures: recorded",
    )
    c.check(
        radio["kept"] == [] and radio["raw_replies"] == ["garbage", "still garbage"],
        "raw replies kept",
    )
    c.check(
        set(result.questions) == {"decisions/db", "decisions/bd"},
        "failed Decision gets none",
    )
    c.check(result.summary["failed_parse"] == 1, "the failure is counted")
    c.check(result.summary["decisions"] == 3, "decisions counted")
    c.check(result.summary["with_questions"] == 2, "decisions with questions counted")
    c.check(
        all(
            e["key"] == k
            and k
            == d2q.generation_key(
                d2q.P1_SYSTEM,
                e["title"],
                {d.id: d.body for d in _decisions()}[e["decision"]],
            )
            for k, e in result.entries.items()
        ),
        "entries are keyed by the sha256 of the prompt and the Decision text",
    )
    c.check(
        by["decisions/db"]["call_seconds"] == [2.0]
        and by["decisions/db"]["eval_counts"] == [len(GOOD_EN.split())],
        "seconds and eval_count are recorded per call",
    )
    c.check(
        by["decisions/radio"]["seconds"] == 4.0, "a retried Decision costs both calls"
    )

    # A reply that parses but leaves under two kept items: counted, not retried.
    chat2 = ScriptedChat([json.dumps(["Which database is used?", "No mark here"])])
    one = d2q.generate(
        _decisions()[:1], chat2, system=d2q.P1_SYSTEM, prompt_id="p1", clock=_clock()
    )
    c.check(len(chat2.sent) == 1, "a parsed reply is never retried")
    c.check(
        one.summary["too_few"] == 1 and one.questions == {},
        "fewer than two kept: none, counted",
    )

    # Resume: an entry with the same key is reused and costs no call.
    chat3 = ScriptedChat([GOOD_EN])
    again = d2q.generate(
        _decisions()[:2],
        chat3,
        system=d2q.P1_SYSTEM,
        prompt_id="p1",
        clock=_clock(),
        previous=result.entries,
    )
    c.check(len(chat3.sent) == 0, "every Decision already generated: no call")
    c.check(
        again.questions == {k: v for k, v in result.questions.items()},
        "resumed questions",
    )
    changed = [d2q.DecisionText("decisions/db", "Local database", _en() + " More.")]
    chat4 = ScriptedChat([GOOD_EN])
    d2q.generate(
        changed,
        chat4,
        system=d2q.P1_SYSTEM,
        prompt_id="p1",
        clock=_clock(),
        previous=result.entries,
    )
    c.check(len(chat4.sent) == 1, "a changed Decision text is generated again")
    chat5 = ScriptedChat([GOOD_EN])
    d2q.generate(
        _decisions()[:1],
        chat5,
        system=d2q.P2_SYSTEM,
        prompt_id="p2",
        clock=_clock(),
        previous=result.entries,
    )
    c.check(len(chat5.sent) == 1, "another prompt is generated again")
    chat6 = ScriptedChat([])
    kept_fail = d2q.generate(
        _decisions()[2:],
        chat6,
        system=d2q.P1_SYSTEM,
        prompt_id="p1",
        clock=_clock(),
        previous=result.entries,
    )
    c.check(len(chat6.sent) == 0, "a recorded failure is not retried on resume")
    c.check(kept_fail.questions == {}, "and it still has no questions")

    # A backend error: the server being gone stops the run, anything else is recorded.
    try:
        d2q.generate(
            _decisions()[:1],
            ScriptedChat([BackendUnavailable("down")]),
            system=d2q.P1_SYSTEM,
            prompt_id="p1",
            clock=_clock(),
        )
        c.check(False, "an unavailable server stops the run")
    except BackendUnavailable:
        c.check(True, "an unavailable server stops the run")
    broken = d2q.generate(
        _decisions()[:1],
        ScriptedChat([BackendError("boom")]),
        system=d2q.P1_SYSTEM,
        prompt_id="p1",
        clock=_clock(),
    )
    e = next(iter(broken.entries.values()))
    c.check(
        e["failed"] is True and "boom" in e["error"],
        "another backend error is recorded",
    )

    seen: list[str] = []
    d2q.generate(
        _decisions()[:2],
        ScriptedChat([GOOD_ES, GOOD_EN]),
        system=d2q.P1_SYSTEM,
        prompt_id="p1",
        clock=_clock(),
        on_entry=lambda entry: seen.append(entry["decision"]),
    )
    c.check(
        seen == ["decisions/bd", "decisions/db"],
        "on_entry fires per Decision, in id order",
    )


def cost(c: Checks) -> None:
    c.check(
        d2q.nearest_rank([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 0.9) == 9,
        "p90 of 10 is the 9th",
    )
    c.check(d2q.nearest_rank([5.0], 0.9) == 5.0, "p90 of one value")
    c.check(d2q.nearest_rank([3, 1, 2], 0.9) == 3, "unsorted input")
    c.check(d2q.nearest_rank([4, 1, 3, 2], 0.5) == 2, "the median is the 2nd of 4")
    try:
        d2q.nearest_rank([], 0.9)
        c.check(False, "p90 of nothing is refused")
    except ValueError:
        c.check(True, "p90 of nothing is refused")

    def entry(seconds: list[float], evals: list[int | None]) -> dict[str, Any]:
        return {"call_seconds": seconds, "eval_counts": evals, "seconds": sum(seconds)}

    entries = {f"k{i}": entry([float(i + 1)], [60]) for i in range(10)}
    rep = d2q.cost_report(entries, ingest_seconds=1000.0)
    c.check(rep["decisions"] == 10 and rep["calls"] == 10, "counts")
    c.check(rep["p90_s"] == 9.0 and rep["total_s"] == 55.0, "p90 and total")
    c.check(rep["bar_p90"]["pass"] is False, "p90 9 s is over the 6 s bar")
    c.check(
        rep["bar_total"]["pass"] is True
        and abs(rep["bar_total"]["share"] - 0.055) < 1e-9,
        "55 s of 1000 s is 5.5%",
    )
    ok = d2q.cost_report({"a": entry([2.0], [50])}, ingest_seconds=10.0)
    c.check(ok["bar_p90"]["pass"] is True, "2 s is under the bar")
    c.check(ok["bar_total"]["pass"] is False, "2 s of 10 s is 20%, over 10%")
    edge = d2q.cost_report({"a": entry([6.0], [50])}, ingest_seconds=60.0)
    c.check(
        edge["bar_p90"]["pass"] is True and edge["bar_total"]["pass"] is True,
        "bars are inclusive",
    )
    unknown = d2q.cost_report({"a": entry([2.0], [None])}, ingest_seconds=None)
    c.check(
        unknown["bar_total"]["pass"] is None,
        "no ingest wall time: the total bar is unknown",
    )
    c.check(unknown["eval_count_mean"] is None, "no eval_count: no mean")
    c.check(
        d2q.cost_report({}, ingest_seconds=1.0)["calls"] == 0, "no entries: no calls"
    )
    mean = d2q.cost_report({"a": entry([1.0, 1.0], [10, 30])}, ingest_seconds=None)
    c.check(
        mean["calls"] == 2 and mean["eval_count_mean"] == 20.0,
        "mean eval_count over calls",
    )
    with tempfile.TemporaryDirectory() as tmp:
        manifest = pathlib.Path(tmp) / "manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "sources": [
                        {"seconds": 100.0},
                        {"seconds": 50.5},
                        {"seconds": None},
                    ],
                    "reindex": {"seconds": 20.0},
                }
            ),
            "utf-8",
        )
        c.check(
            d2q.ingest_seconds(manifest) == 170.5,
            "ingest wall time: sources plus reindex",
        )
        manifest.write_text(json.dumps({"sources": [{"seconds": 1.0}]}), "utf-8")
        c.check(d2q.ingest_seconds(manifest) == 1.0, "a manifest with no reindex block")
    c.check(d2q.render_cost(rep).count("\n") >= 3, "the cost report renders")


def derangement(c: Checks) -> None:
    ids = [f"decisions/d{i}" for i in range(12)]
    m = d2q.derangement(ids, seed=1650)
    c.check(
        set(m) == set(ids) and set(m.values()) == set(ids), "a permutation of the ids"
    )
    c.check(all(m[i] != i for i in ids), "no Decision keeps its own questions")
    c.check(
        m == d2q.derangement(list(reversed(ids)), seed=1650),
        "order of the input does not matter",
    )
    c.check(m == d2q.derangement(ids, seed=1650), "seeded: repeatable")
    c.check(m != d2q.derangement(ids, seed=1651), "another seed, another assignment")
    two = d2q.derangement(["a", "b"], seed=1650)
    c.check(two == {"a": "b", "b": "a"}, "two Decisions swap")
    for n in range(2, 9):
        d = d2q.derangement([f"x{i}" for i in range(n)], seed=1650 + n)
        c.check(all(k != v for k, v in d.items()), f"{n} ids: derangement")
    try:
        d2q.derangement(["only"], seed=1650)
        c.check(False, "one id cannot be deranged")
    except ValueError:
        c.check(True, "one id cannot be deranged")


def stores(c: Checks) -> None:
    emb = HashEmbedder()
    questions = {
        "decisions/db": [
            "Which database stores the readings?",
            "Why a local database?",
        ],
        "decisions/radio": [
            "Which radio link do remote stations use?",
            "How often is a packet sent?",
        ],
    }
    vectors = d2q.embed_questions(emb, questions)
    c.check(
        {d: len(v) for d, v in vectors.items()}
        == {"decisions/db": 2, "decisions/radio": 2},
        "one vector per question, kept under its Decision",
    )
    c.check(
        vectors["decisions/db"][0] == emb.embed([questions["decisions/db"][0]])[0],
        "the vector is the embedding of the question text",
    )
    batches = HashEmbedder()
    many = {"d": [f"Question number {i} about stations?" for i in range(65)]}
    out = d2q.embed_questions(batches, many, batch=32)
    c.check(
        len(out["d"]) == 65 and [len(b) for b in batches.calls] == [32, 32, 1],
        "batched in order",
    )

    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        # A2: a separate table, one row per question, mapped to its Decision.
        qpath = root / "questions.db"
        d2q.build_question_store(qpath, vectors)
        with open_vector_store(qpath) as db:
            conn = db._conn
            rows = conn.execute(
                "SELECT concept_id FROM vectors ORDER BY concept_id"
            ).fetchall()
            c.check(len(rows) == 4, "four question rows")
            probe = emb.embed(["Which database stores the readings?"])[0]
            hits = db.query(probe, 10)
            c.check(
                hits[0].concept_id == "decisions/db" and len(hits) == 2,
                "minimum distance per Decision: the nearest question names its Decision",
            )
            c.check(hits[0].distance < 1e-3, "the question itself is at distance ~0")
        ids = d2q.question_hits(qpath, probe, depth=10, excluded=frozenset())
        c.check(ids == ["decisions/db", "decisions/radio"], f"third list: {ids}")
        c.check(
            d2q.question_hits(
                qpath, probe, depth=10, excluded=frozenset({"decisions/db"})
            )
            == ["decisions/radio"],
            "the shipped deprecated/confidential filter applies to the third list",
        )
        c.check(
            d2q.question_hits(qpath, probe, depth=1, excluded=frozenset())
            == ["decisions/db"],
            "pool depth",
        )

        # C1: the same questions under a different Decision.
        swapped = d2q.reassign(vectors, d2q.derangement(sorted(vectors), seed=1650))
        c.check(
            set(swapped) == set(vectors)
            and all(swapped[d] != vectors[d] for d in vectors),
            "every Decision holds another Decision's vectors",
        )
        c.check(
            swapped["decisions/radio"] == vectors["decisions/db"],
            "with two Decisions the questions swap",
        )
        cpath = root / "control.db"
        d2q.build_question_store(cpath, swapped)
        ctl = d2q.question_hits(cpath, probe, depth=10, excluded=frozenset())
        c.check(
            ctl[0] == "decisions/radio",
            "the control sends the question to the WRONG Decision",
        )
        try:
            d2q.reassign({"only": [[0.0]]}, {"only": "only"})
            c.check(False, "a fixed point is refused")
        except ValueError:
            c.check(True, "a fixed point is refused")

        # A1: extra rows for the Decision in the SAME vectors table.
        main = root / "main.db"
        with open_vector_store(main) as db:
            db.upsert_many(
                [
                    (
                        "decisions/db",
                        [emb.embed(["Use a local store for all readings."])[0]],
                        "h-db",
                    ),
                    (
                        "decisions/radio",
                        [emb.embed(["Radio link for remote sites."])[0]],
                        "h-radio",
                    ),
                    (
                        "sources/long",
                        [
                            emb.embed(["unrelated long note"])[0],
                            emb.embed(["more of it"])[0],
                        ],
                        "h-src",
                    ),
                ]
            )
            db.commit()
            before = db.query(probe, 10)
        c.check(
            before[0].concept_id != "decisions/db" or before[0].distance > 1e-3,
            "fixture: not at distance 0 before",
        )
        added = d2q.extend_decision_rows(main, vectors)
        c.check(added == 4, f"four rows added: {added}")
        with open_vector_store(main) as db:
            conn = db._conn
            counts = dict(
                conn.execute("SELECT concept_id, chunk_count FROM vector_meta")
            )
            c.check(
                counts == {"decisions/db": 3, "decisions/radio": 3, "sources/long": 2},
                f"chunk counts: {counts}",
            )
            hashes = db.meta_hashes()
            c.check(hashes["decisions/db"] == "h-db", "the content hash is unchanged")
            after = db.query(probe, 10)
            c.check(
                after[0].concept_id == "decisions/db" and after[0].distance < 1e-3,
                "the existing minimum-distance collapse picks the question vector",
            )
            c.check(
                len({h.concept_id for h in after}) == len(after),
                "still one hit per Decision",
            )
        try:
            d2q.extend_decision_rows(main, {"decisions/missing": [[0.0] * EMBED_DIM]})
            c.check(False, "a Decision with no stored rows is refused")
        except ValueError:
            c.check(True, "a Decision with no stored rows is refused")

        # A3: the questions appended to the Decision's text, for FTS only.
        src = root / "bundle"
        (src / "decisions").mkdir(parents=True)
        (src / "sources").mkdir()
        (src / ".state").mkdir()
        (src / "decisions" / "db.md").write_text(
            "---\ntype: Decision\ntitle: Local database\n---\n\nUse a local store.\n",
            "utf-8",
        )
        (src / "sources" / "note.md").write_text(
            "---\ntype: Source\ntitle: Note\n---\n\nPlain note.\n", "utf-8"
        )
        (src / ".state" / "x.md").write_text("hidden", "utf-8")
        dst = root / "bundle-a3"
        n = d2q.append_questions_to_bundle(
            src, dst, {"decisions/db": questions["decisions/db"]}
        )
        c.check(n == 1, "one Decision rewritten")
        text = (dst / "decisions" / "db.md").read_text("utf-8")
        c.check(text.startswith("---\ntype: Decision"), "frontmatter intact")
        c.check(
            "Use a local store." in text
            and "Which database stores the readings?" in text,
            "text plus questions",
        )
        c.check(
            (dst / "sources" / "note.md").read_text("utf-8")
            == (src / "sources" / "note.md").read_text("utf-8"),
            "other files untouched",
        )
        c.check(not (dst / ".state").exists(), ".state does not travel")
        c.check(
            "Which database" not in (src / "decisions" / "db.md").read_text("utf-8"),
            "the source bundle is not written",
        )
        try:
            d2q.append_questions_to_bundle(
                src, root / "bundle-bad", {"decisions/nope": ["Why not?"]}
            )
            c.check(False, "a Decision that is not in the bundle is refused")
        except ValueError:
            c.check(True, "a Decision that is not in the bundle is refused")


def fusion_lists(c: Checks) -> None:
    fts_ids = ["a", "b", "c"]
    dense_ids = ["b", "a", "d"]
    base = fusion.select_top(fusion.fuse(_hits_fts(fts_ids), _hits_vec(dense_ids)), 5)
    c.check(
        d2q.fused_by_k(fts_ids, dense_ids, [], (5,))["5"] == base,
        "an empty third list leaves the shipped fused order alone",
    )
    three = d2q.fused_by_k(fts_ids, dense_ids, ["z", "z2", "a"], (5, 8))
    c.check(set(three) == {"5", "8"}, "one ranking per k")
    c.check(
        three["5"] == pre.rrf_fuse([fts_ids, dense_ids, ["z", "z2", "a"]])[:5],
        "the third list joins RRF",
    )
    c.check(three["5"] != base, "and it moves the order")


def _hits_fts(ids: Sequence[str]) -> list[Any]:
    from openkos.state.fts import FtsHit

    return [FtsHit(concept_id=i, score=0.0) for i in ids]


def _hits_vec(ids: Sequence[str]) -> list[Any]:
    from openkos.state.vectorstore import VecHit

    return [VecHit(concept_id=i, distance=0.1 * n) for n, i in enumerate(ids)]


def memo(c: Checks) -> None:
    inner = HashEmbedder()
    m = d2q.MemoEmbedder(inner)
    first = m.embed(["a b", "c d"])
    second = m.embed(["c d", "e f"])
    c.check(first[1] == second[0], "a repeated text returns the same vector")
    c.check(
        inner.calls == [["a b", "c d"], ["e f"]], "only unseen texts reach the model"
    )
    c.check(m.misses == 3, "misses are counted")
    c.check(m.embed([]) == [] and len(inner.calls) == 2, "nothing to embed, no call")


def verify_generated_checks(c: Checks) -> None:
    decisions = _decisions()
    chat = ScriptedChat([GOOD_ES, GOOD_EN, GOOD_EN])
    res = d2q.generate(
        decisions, chat, system=d2q.P1_SYSTEM, prompt_id="p1", clock=_clock()
    )
    raw = d2q.generated_document(
        res,
        prompt_id="p1",
        system=d2q.P1_SYSTEM,
        model="qwen3:8b",
        stamp={"s": 1},
        options={},
        bundle="b",
        stratum_sha256=None,
    )
    c.check(
        raw["kind"] == pre.GENERATED_KIND,
        "the file carries the kind the leak audit reads",
    )
    c.check(pre.generated_questions(raw) == res.questions, "and its questions parse")
    c.check(
        raw["prompt"]["id"] == "p1" and raw["model"] == "qwen3:8b",
        "prompt and model recorded",
    )
    c.check(
        raw["prompt"]["system_sha256"]
        == hashlib.sha256(d2q.P1_SYSTEM.encode()).hexdigest(),
        "the prompt hash is recorded",
    )
    c.check(raw["complete"] is True, "a finished run says so")
    c.check(
        d2q.verify_generated(raw, decisions) == [],
        "a file matches the Decisions it was made from",
    )
    stale = [
        *decisions[:2],
        d2q.DecisionText("decisions/radio", "Radio", "Changed text."),
    ]
    problems = d2q.verify_generated(raw, stale)
    c.check(
        len(problems) >= 1 and "decisions/radio" in problems[0],
        f"a changed Decision is stale: {problems}",
    )
    extra = [
        *decisions,
        d2q.DecisionText("decisions/new", "New", "A new decision here."),
    ]
    c.check(
        d2q.verify_generated(raw, extra) != [],
        "a Decision with no generation is reported",
    )
    c.check(
        d2q.verify_generated(raw, decisions[:2]) != [],
        "an entry for a Decision no longer in the bundle is reported",
    )
    partial = dict(raw)
    partial["complete"] = False
    c.check(
        d2q.verify_generated(partial, decisions) != [], "an unfinished file is refused"
    )
    c.check(
        d2q.document_prompt_system(raw) == d2q.P1_SYSTEM,
        "the prompt is looked up by id, not trusted from the file",
    )


def pure(c: Checks) -> None:
    registered_prompts(c)
    parsing(c)
    selection(c)
    generation(c)
    cost(c)
    derangement(c)
    verify_generated_checks(c)
    fusion_lists(c)
    memo(c)
    stores(c)
