"""The pre-run code of `PREREGISTRATION-1650.md` (index the questions a Decision
answers), with no model call of its own.

- **The generator's contract**: the registered prompts P1 and P2 verbatim, the
  user message, the key an output is stored under, the deterministic output
  handling (fail-closed parse, 4 to 25 words, ends in `?`, the Decision's
  language, Jaccard 0.8 collapse, fewer than two kept means none), and the
  per-Decision loop. `generate` takes a `chat` object and the Decisions and
  NOTHING ELSE: no argument can carry a gold question to the model.
- **The cost bars** (p90 per Decision, share of the bundle's ingest wall time).
- **The arms**, built on the production stores without changing `src/`:

  | Arm | Construction |
  | --- | --- |
  | `a0` | the shipped pipeline |
  | `a1` | question vectors are extra chunk rows of the Decision in the same `vectors` table |
  | `a2` | a SEPARATE vector store, one row per question under its Decision; its top-10 is a third list in `rrf_fuse` |
  | `a3` | the questions appended to the Decision's text for the FTS build only |
  | `c1` | `a2` with every Decision's questions moved to another Decision (seeded derangement) |

  A question embedding is computed once (`MemoEmbedder`) and shared by every arm.

Everything here is pure or touches only local SQLite files; the only GPU step is
`generate_questions.py generate`, and `run --doc2query` embeds with the
embedder it is handed.
"""

from __future__ import annotations

import hashlib
import json
import math
import pathlib
import random
import re
import shutil
import tempfile
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final, Protocol

import gold_core as core
import gold_prereg as pre

from openkos import lifecycle
from openkos.extraction.concept import _dominant_language
from openkos.llm.base import BackendError, BackendUnavailable, Embedder
from openkos.llm.parsing import _embedded_json_values, _strip_code_fence
from openkos.model import okf
from openkos.retrieval import fusion
from openkos.state import fts as fts_mod
from openkos.state.vectorstore import (
    VecHit,
    VectorStore,
    open_vector_store,
)

# --------------------------------------------------------------------------- #
# Registered constants (PREREGISTRATION-1650.md)                               #
# --------------------------------------------------------------------------- #

MODEL: Final = "qwen3:8b"
SEED: Final = 1650

P1_SYSTEM: Final = (
    "You write search questions for a personal knowledge base. You are given one "
    "decision that was recorded in it. Write 3 different questions that a person "
    "who has not read this decision might ask, and whose answer is stated in this "
    "decision. Write every question in the same language as the decision. Each "
    "question must be answerable from the decision text alone and must be 25 words "
    "or fewer. Reply with a JSON array of exactly 3 strings and nothing else."
)
_REPLY_SENTENCE: Final = (
    "Reply with a JSON array of exactly 3 strings and nothing else."
)
P2_SENTENCE: Final = (
    "Describe what the person wants to know; do not copy the distinctive words of "
    "the decision when an ordinary description of the topic would do."
)
P2_SYSTEM: Final = P1_SYSTEM.replace(
    _REPLY_SENTENCE, f"{P2_SENTENCE} {_REPLY_SENTENCE}"
)
USER_TEMPLATE: Final = "Decision title: {title}\n\nDecision:\n{body}"

PROMPTS: Final = {"p1": P1_SYSTEM, "p2": P2_SYSTEM}
PROMPT_IDS: Final = {"p1": "doc2query/p1_system", "p2": "doc2query/p2_system"}
PRIMARY_PROMPT: Final = "p1"

MIN_WORDS: Final = 4
MAX_WORDS: Final = 25
COLLAPSE_JACCARD: Final = 0.8
MIN_KEPT: Final = 2
PARSE_ATTEMPTS: Final = 2
"""One call and one retry with the same prompt, then the failure is recorded."""

BAR_P90_SECONDS: Final = 6.0
BAR_TOTAL_SHARE: Final = 0.10

ARMS: Final = ("a0", "a1", "a2", "a3", "c1")
EMBED_BATCH: Final = 32
GENERATED_VERSION: Final = 1

_DROP_REASONS: Final = ("length", "no_question_mark", "language", "duplicate")


# --------------------------------------------------------------------------- #
# The generator's contract                                                     #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class DecisionText:
    id: str
    title: str
    body: str


@dataclass(frozen=True)
class Reply:
    text: str
    eval_count: int | None


class Chat(Protocol):
    def chat(self, messages: Sequence[dict[str, str]]) -> Reply: ...


def user_message(title: str, body: str) -> str:
    return USER_TEMPLATE.format(title=title, body=body)


def build_messages(system: str, title: str, body: str) -> list[dict[str, str]]:
    """The whole wire input: the system prompt and the Decision. Nothing else."""
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user_message(title, body)},
    ]


def generation_key(system: str, title: str, body: str) -> str:
    """sha256 of the prompt and the Decision text: the key an output is stored
    under, so a changed prompt or a revised Decision is generated again."""
    payload = json.dumps([system, title, body], ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def read_decisions(bundle: pathlib.Path) -> list[DecisionText]:
    """The title and stored body of every `type: Decision` concept, by id."""
    out: list[DecisionText] = []
    for path in okf.iter_bundle_markdown(bundle):
        loaded = okf.try_load_frontmatter(path.read_text("utf-8"))
        if loaded is None:
            continue
        metadata, body = loaded
        if str(metadata.get("type") or "") != "Decision":
            continue
        doc_id = path.relative_to(bundle).with_suffix("").as_posix()
        out.append(DecisionText(doc_id, str(metadata.get("title") or ""), body.strip()))
    return sorted(out, key=lambda d: d.id)


def parse_string_array(raw: object) -> list[str] | None:
    """A JSON array of strings, or `None` (fail closed).

    The same three steps as `llm.parsing.extract_json_items` (raw, fenced,
    embedded scan), but `extract_json_items` keeps only OBJECT elements and so
    cannot return the strings this reply is. An array with any non-string
    element is a parse failure, not a partial answer."""
    if not isinstance(raw, str):
        return None
    fenced = _strip_code_fence(raw)
    for candidate in (raw, fenced):
        if candidate is None:
            continue
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError, TypeError:
            continue
        if isinstance(parsed, list):
            return _strings(parsed)
    for value in _embedded_json_values(fenced if fenced is not None else raw):
        if isinstance(value, list):
            strings = _strings(value)
            if strings is not None:
                return strings
    return None


def _strings(items: list[Any]) -> list[str] | None:
    return [str(i) for i in items] if all(isinstance(i, str) for i in items) else None


_WORDS: Final = re.compile(r"[^\W\d_]+")


def _word_set(text: str) -> set[str]:
    return {w.lower() for w in _WORDS.findall(text)}


def jaccard(a: str, b: str) -> float:
    x, y = _word_set(a), _word_set(b)
    return len(x & y) / len(x | y) if x | y else 0.0


def dominant_language(text: str) -> str | None:
    """The production function-word vote (`extraction.concept`)."""
    return _dominant_language(text)


@dataclass(frozen=True)
class Selection:
    language: str | None
    kept: list[str]
    dropped: dict[str, int]


def select_questions(items: Sequence[str], decision_text: str) -> Selection:
    """The registered deterministic output handling for one reply.

    An item is kept when it has 4 to 25 words, ends in `?` and the function-word
    vote gives the Decision's language; items sharing a Jaccard of 0.8 or more
    with an earlier kept one are collapsed. When the Decision itself has no
    vote there is no language to hold an item to; an item that has no vote is
    NOT the Decision's language."""
    language = dominant_language(decision_text)
    dropped = dict.fromkeys(_DROP_REASONS, 0)
    kept: list[str] = []
    for item in items:
        question = item.strip()
        if not MIN_WORDS <= len(question.split()) <= MAX_WORDS:
            dropped["length"] += 1
        elif not question.endswith("?"):
            dropped["no_question_mark"] += 1
        elif language is not None and dominant_language(question) != language:
            dropped["language"] += 1
        elif any(jaccard(question, k) >= COLLAPSE_JACCARD for k in kept):
            dropped["duplicate"] += 1
        else:
            kept.append(question)
    return Selection(language=language, kept=kept, dropped=dropped)


def accepted(selection: Selection) -> list[str]:
    """What the Decision gets: its kept items, or none when under `MIN_KEPT`."""
    return selection.kept if len(selection.kept) >= MIN_KEPT else []


# --------------------------------------------------------------------------- #
# The generation loop                                                          #
# --------------------------------------------------------------------------- #


@dataclass
class Generation:
    entries: dict[str, dict[str, Any]] = field(default_factory=dict)
    questions: dict[str, list[str]] = field(default_factory=dict)
    summary: dict[str, Any] = field(default_factory=dict)


def _new_entry(decision: DecisionText, key: str) -> dict[str, Any]:
    return {
        "decision": decision.id,
        "key": key,
        "title": decision.title,
        "language": dominant_language(f"{decision.title}\n{decision.body}"),
        "attempts": 0,
        "raw_replies": [],
        "parsed": False,
        "selected": [],
        "kept": [],
        "dropped": dict.fromkeys(_DROP_REASONS, 0),
        "too_few": False,
        "failed": False,
        "error": None,
        "call_seconds": [],
        "eval_counts": [],
        "seconds": 0.0,
    }


def _generate_one(
    decision: DecisionText,
    chat: Chat,
    *,
    system: str,
    key: str,
    clock: Callable[[], float],
) -> dict[str, Any]:
    entry = _new_entry(decision, key)
    messages = build_messages(system, decision.title, decision.body)
    for _ in range(PARSE_ATTEMPTS):
        started = clock()
        try:
            reply = chat.chat(messages)
        except BackendUnavailable:
            raise
        except BackendError as exc:
            entry["call_seconds"].append(clock() - started)
            entry["attempts"] += 1
            entry["failed"] = True
            entry["error"] = f"{type(exc).__name__}: {exc}"
            break
        entry["call_seconds"].append(clock() - started)
        entry["attempts"] += 1
        entry["eval_counts"].append(reply.eval_count)
        entry["raw_replies"].append(reply.text)
        items = parse_string_array(reply.text)
        if items is None:
            continue
        entry["parsed"] = True
        selection = select_questions(items, f"{decision.title}\n{decision.body}")
        entry["selected"] = selection.kept
        entry["dropped"] = selection.dropped
        entry["kept"] = accepted(selection)
        entry["too_few"] = not entry["kept"]
        break
    else:
        entry["failed"] = True
    entry["seconds"] = sum(entry["call_seconds"])
    return entry


def generate(
    decisions: Sequence[DecisionText],
    chat: Chat,
    *,
    system: str,
    prompt_id: str,
    clock: Callable[[], float],
    previous: Mapping[str, dict[str, Any]] | None = None,
    on_entry: Callable[[dict[str, Any]], None] | None = None,
) -> Generation:
    """One chat call per Decision (one retry with the same prompt on a failed
    parse). The signature is the leak guarantee: Decisions in, questions out,
    and no parameter through which a gold question could reach the model.

    `previous` entries are reused by key, so a resumed run does not spend a call
    on a Decision it already answered, failures included (the registration allows
    one retry, not a second go on the next launch)."""
    result = Generation()
    known = dict(previous or {})
    for decision in sorted(decisions, key=lambda d: d.id):
        key = generation_key(system, decision.title, decision.body)
        entry = known.get(key)
        if entry is None:
            entry = _generate_one(decision, chat, system=system, key=key, clock=clock)
            known[key] = entry
            if on_entry is not None:
                on_entry(entry)
        result.entries[key] = entry
        if entry["kept"]:
            result.questions[decision.id] = list(entry["kept"])
    dropped: Counter[str] = Counter()
    for entry in result.entries.values():
        dropped.update(entry["dropped"])
    result.summary = {
        "prompt_id": prompt_id,
        "decisions": len(decisions),
        "with_questions": len(result.questions),
        "too_few": sum(1 for e in result.entries.values() if e["too_few"]),
        "failed_parse": sum(
            1 for e in result.entries.values() if e["failed"] and e["error"] is None
        ),
        "failed_backend": sum(1 for e in result.entries.values() if e["error"]),
        "questions": sum(len(q) for q in result.questions.values()),
        "dropped": dict(dropped),
    }
    return result


def generated_document(
    result: Generation,
    *,
    prompt_id: str,
    system: str,
    model: str,
    stamp: Mapping[str, Any],
    options: Mapping[str, Any],
    bundle: str,
    stratum_sha256: str | None,
    complete: bool = True,
    ingest_seconds: float | None = None,
) -> dict[str, Any]:
    """The stored artifact all arms share (`retrieval-gold-generated-questions`)."""
    return {
        "kind": pre.GENERATED_KIND,
        "version": GENERATED_VERSION,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "complete": complete,
        "bundle": bundle,
        "model": model,
        "options": dict(options),
        "prompt": {
            "id": prompt_id,
            "system_sha256": hashlib.sha256(system.encode()).hexdigest(),
        },
        "stratum_sha256": stratum_sha256,
        "stamp": dict(stamp),
        "summary": result.summary,
        "cost": cost_report(result.entries, ingest_seconds=ingest_seconds),
        "questions": result.questions,
        "entries": result.entries,
    }


def document_prompt_system(raw: Mapping[str, Any]) -> str:
    """The system prompt a generated file names, looked up by id in THIS module
    (a file is not trusted to say what its prompt was)."""
    prompt = raw.get("prompt") or {}
    system = PROMPTS.get(str(prompt.get("id")))
    if system is None:
        raise SystemExit(
            f"the generated file names an unknown prompt {prompt.get('id')!r}"
        )
    if hashlib.sha256(system.encode()).hexdigest() != prompt.get("system_sha256"):
        raise SystemExit(
            "the generated file's prompt hash is not the registered prompt"
        )
    return system


def verify_generated(
    raw: Mapping[str, Any], decisions: Sequence[DecisionText]
) -> list[str]:
    """Why a generated file cannot be used with these Decisions (empty: it can).

    Every current Decision must have an entry under the key of ITS text and the
    registered prompt, and no entry may belong to a Decision that is gone."""
    problems: list[str] = []
    if raw.get("complete") is not True:
        problems.append("the generated file is not complete (the run was interrupted)")
    system = document_prompt_system(raw)
    entries: Mapping[str, Any] = raw.get("entries") or {}
    expected = {
        generation_key(system, d.title, d.body): d.id
        for d in sorted(decisions, key=lambda d: d.id)
    }
    for key, doc_id in expected.items():
        if key not in entries:
            problems.append(
                f"{doc_id}: no generation for its current text (changed, or new since)"
            )
    for key, entry in entries.items():
        if key not in expected:
            problems.append(
                f"{entry.get('decision')}: generated for text that is no longer in the bundle"
            )
    return problems


# --------------------------------------------------------------------------- #
# Cost                                                                         #
# --------------------------------------------------------------------------- #


def nearest_rank(values: Sequence[float], share: float) -> float:
    """The `share` percentile by nearest rank (the ceil(share * n)-th smallest)."""
    if not values:
        raise ValueError("no values")
    ordered = sorted(values)
    rank = max(1, math.ceil(share * len(ordered)))
    return ordered[rank - 1]


def ingest_seconds(manifest: pathlib.Path) -> float:
    """The bundle's recorded ingest wall time: every source's seconds plus the
    reindex, from `build_bundle.py`'s manifest."""
    raw = json.loads(manifest.read_text("utf-8"))
    total = sum(float(s.get("seconds") or 0.0) for s in raw.get("sources", []))
    reindex = raw.get("reindex") or {}
    return total + float(reindex.get("seconds") or 0.0)


def cost_report(
    entries: Mapping[str, Mapping[str, Any]], *, ingest_seconds: float | None
) -> dict[str, Any]:
    """The registered bar K from the recorded calls: p90 per-Decision generation
    seconds (a retried Decision counts both calls) at most 6, and the total at
    most 10% of the bundle's ingest wall time."""
    per_decision = [float(e["seconds"]) for e in entries.values()]
    calls = sum(len(e["call_seconds"]) for e in entries.values())
    evals = [
        int(n) for e in entries.values() for n in e["eval_counts"] if n is not None
    ]
    total = sum(per_decision)
    p90 = nearest_rank(per_decision, 0.9) if per_decision else None
    share = total / ingest_seconds if ingest_seconds else None
    return {
        "decisions": len(entries),
        "calls": calls,
        "p90_s": p90,
        "median_s": nearest_rank(per_decision, 0.5) if per_decision else None,
        "total_s": total,
        "eval_count_mean": sum(evals) / len(evals) if evals else None,
        "ingest_seconds": ingest_seconds,
        "bar_p90": {
            "bar": BAR_P90_SECONDS,
            "pass": None if p90 is None else p90 <= BAR_P90_SECONDS,
        },
        "bar_total": {
            "bar": BAR_TOTAL_SHARE,
            "share": share,
            "pass": None if share is None else share <= BAR_TOTAL_SHARE,
        },
    }


def render_cost(report: Mapping[str, Any]) -> str:
    def verdict(passed: bool | None) -> str:
        return "unknown" if passed is None else "PASS" if passed else "FAIL"

    p90, share = report["bar_p90"], report["bar_total"]
    p90_text = "n/a" if report["p90_s"] is None else f"{report['p90_s']:.2f} s"
    share_text = "n/a" if share["share"] is None else f"{share['share']:.1%}"
    mean = report["eval_count_mean"]
    return "\n".join(
        [
            f"{report['decisions']} Decisions, {report['calls']} calls, "
            f"{report['total_s']:.1f} s of generation",
            f"  p90 per Decision: {p90_text} (bar {p90['bar']:.0f} s): {verdict(p90['pass'])}",
            f"  share of the bundle's ingest wall time: {share_text} "
            f"(bar {share['bar']:.0%}, ingest {report['ingest_seconds']} s): "
            f"{verdict(share['pass'])}",
            "  mean eval_count per call: " + ("n/a" if mean is None else f"{mean:.0f}"),
        ]
    )


# --------------------------------------------------------------------------- #
# The control and the question vectors                                         #
# --------------------------------------------------------------------------- #


def derangement(ids: Sequence[str], *, seed: int) -> dict[str, str]:
    """A seeded permutation of `ids` with no fixed point (Sattolo's algorithm: a
    single cycle). Sorted first, so the order of the input does not matter."""
    ordered = sorted(set(ids))
    if len(ordered) < 2:
        raise ValueError("a derangement needs at least two ids")
    rng = random.Random(seed)  # noqa: S311 -- a seeded shuffle, not security
    perm = list(range(len(ordered)))
    for i in range(len(perm) - 1, 0, -1):
        j = rng.randrange(i)
        perm[i], perm[j] = perm[j], perm[i]
    return {ordered[i]: ordered[perm[i]] for i in range(len(ordered))}


def reassign[V](vectors: Mapping[str, V], mapping: Mapping[str, str]) -> dict[str, V]:
    """C1: the questions of Decision `d` placed under `mapping[d]`."""
    if set(mapping) != set(vectors) or set(mapping.values()) != set(vectors):
        raise ValueError("the mapping is not a permutation of the Decisions")
    fixed = [d for d, to in mapping.items() if d == to]
    if fixed:
        raise ValueError(f"fixed points keep their own questions: {fixed}")
    return {to: vectors[d] for d, to in mapping.items()}


def embed_questions(
    embedder: Embedder,
    questions: Mapping[str, Sequence[str]],
    *,
    batch: int = EMBED_BATCH,
) -> dict[str, list[list[float]]]:
    """One vector per generated question, kept under its Decision."""
    flat = [(d, q) for d in sorted(questions) for q in questions[d]]
    vectors: list[list[float]] = []
    for start in range(0, len(flat), batch):
        vectors.extend(embedder.embed([q for _, q in flat[start : start + batch]]))
    if len(vectors) != len(flat):
        raise RuntimeError("the embedder returned a different number of vectors")
    out: dict[str, list[list[float]]] = {d: [] for d in questions}
    for (doc, _), vector in zip(flat, vectors, strict=True):
        out[doc].append(vector)
    return out


class MemoEmbedder:
    """An embedder that asks the model once per distinct text, so a question is
    embedded once however many arms read it."""

    def __init__(self, inner: Embedder) -> None:
        self._inner = inner
        self._cache: dict[str, list[float]] = {}
        self.misses = 0

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        fresh = list(dict.fromkeys(t for t in texts if t not in self._cache))
        if fresh:
            for text, vector in zip(fresh, self._inner.embed(fresh), strict=True):
                self._cache[text] = vector
            self.misses += len(fresh)
        return [self._cache[t] for t in texts]


def build_question_store(
    path: pathlib.Path, vectors: Mapping[str, Sequence[Sequence[float]]]
) -> None:
    """A2/C1: a SEPARATE vector store with one row per question under its
    Decision. The production store's own minimum-distance collapse then returns
    the Decisions nearest the user question."""
    with open_vector_store(path) as db:
        db.upsert_many(
            [(doc, list(vs), f"doc2query:{doc}") for doc, vs in sorted(vectors.items())]
        )
        db.commit()


def query_hits(
    db: VectorStore,
    embedding: Sequence[float],
    *,
    depth: int,
    excluded: frozenset[str],
) -> list[str]:
    hits: list[VecHit] = lifecycle.filter_hits(db.query(embedding, depth), excluded)
    return [h.concept_id for h in hits]


def question_hits(
    path: pathlib.Path,
    embedding: Sequence[float],
    *,
    depth: int,
    excluded: frozenset[str],
) -> list[str]:
    with open_vector_store(path) as db:
        return query_hits(db, embedding, depth=depth, excluded=excluded)


def _decode(blob: bytes) -> list[float]:
    from array import array

    values = array("f")
    values.frombytes(blob)
    return list(values)


def extend_decision_rows(
    path: pathlib.Path, vectors: Mapping[str, Sequence[Sequence[float]]]
) -> int:
    """A1: the question vectors as extra chunk rows of the Decision in the SAME
    `vectors` table, written through the production `upsert_many` with the
    Decision's own stored chunks first and its stored content hash unchanged.
    Returns the number of rows added."""
    added = 0
    with open_vector_store(path) as db:
        hashes = db.meta_hashes()
        for doc, vs in sorted(vectors.items()):
            if doc not in hashes:
                raise ValueError(f"{doc} has no stored vector rows to extend")
            rows = db._conn.execute(
                "SELECT embedding FROM vectors WHERE concept_id = ? ORDER BY chunk_index",
                (doc,),
            ).fetchall()
            own = [_decode(r[0]) for r in rows]
            db.upsert_many([(doc, [*own, *map(list, vs)], hashes[doc])])
            added += len(vs)
        db.commit()
    return added


def append_questions_to_bundle(
    src: pathlib.Path, dst: pathlib.Path, questions: Mapping[str, Sequence[str]]
) -> int:
    """A3: a copy of the bundle in which each Decision's text is followed by its
    questions, to build the FTS index from. `.state` does not travel and the
    source bundle is never written. Returns the number of Decisions rewritten."""
    missing = [d for d in questions if not (src / f"{d}.md").is_file()]
    if missing:
        raise ValueError(f"not in the bundle: {missing}")
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(".state"))
    for doc, qs in sorted(questions.items()):
        path = dst / f"{doc}.md"
        text = path.read_text("utf-8").rstrip("\n")
        path.write_text(text + "\n\n" + "\n".join(qs) + "\n", encoding="utf-8")
    return len(questions)


def fused_by_k(
    fts_ids: Sequence[str],
    dense_ids: Sequence[str],
    third_ids: Sequence[str],
    ks: Sequence[int],
) -> dict[str, list[str]]:
    """The fused ranking per `k` with a third list in the reciprocal rank fusion
    and then the shipped `select_top`. An empty third list is the shipped fusion."""
    fused = pre.rrf_fuse([fts_ids, dense_ids, third_ids])
    return {str(k): fusion.select_top(fused, k) for k in ks}


# --------------------------------------------------------------------------- #
# The arms through the production seams                                        #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Plumbing:
    """The runner's own functions, handed in so this module never imports the
    script that imports it."""

    build_workspace: Callable[..., tuple[pathlib.Path, pathlib.Path, pathlib.Path]]
    excluded_ids: Callable[[pathlib.Path], frozenset[str]]
    retrieve: Callable[..., dict[str, Any]]


def _run_header(
    *,
    arm: str,
    corpus_id: str,
    model_tag: str,
    questions: Sequence[core.Question],
    rankings: dict[str, Any],
    info: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "kind": core.RUN_KIND,
        "stamp": datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"),
        "bundle": corpus_id,
        "embed_model": model_tag,
        "channels": ["fts", "dense", "fused"],
        "ks": list(core.KS),
        "pool_depth": core.POOL_DEPTH,
        "questions_sha256": core.questions_sha256(questions),
        "arm": {"name": arm, **info},
        "rankings": rankings,
    }


def run_arms(
    questions: Sequence[core.Question],
    generated: Mapping[str, Any],
    *,
    embedder: Embedder,
    model_tag: str,
    corpus: Any,
    arms: Sequence[str],
    plumbing: Plumbing,
) -> dict[str, dict[str, Any]]:
    """One run per arm over ONE build of the bundle and ONE embedding of every
    question. The generated file is checked against the Decisions of the bundle
    first (stale or incomplete: nothing is run)."""
    unknown = [a for a in arms if a not in ARMS]
    if unknown:
        raise SystemExit(f"unknown arm(s) {unknown}; one of {ARMS}")
    memo = MemoEmbedder(embedder)
    out: dict[str, dict[str, Any]] = {}
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        bundle, fts_path, vectors_path = plumbing.build_workspace(
            root, memo, model_tag=model_tag, corpus=corpus
        )
        problems = verify_generated(generated, read_decisions(bundle))
        if problems:
            raise SystemExit(
                "the generated questions do not belong to this bundle:\n  - "
                + "\n  - ".join(problems)
            )
        gen = pre.generated_questions(generated)
        excluded = plumbing.excluded_ids(bundle)
        vectors = embed_questions(memo, gen)
        base_info: dict[str, Any] = {
            "prompt": generated["prompt"]["id"],
            "generated_decisions": len(gen),
            "generated_questions": sum(len(v) for v in gen.values()),
        }
        stores: dict[str, pathlib.Path] = {}
        if "a2" in arms:
            stores["a2"] = root / "d2q-a2.db"
            build_question_store(stores["a2"], vectors)
        if "c1" in arms:
            mapping = derangement(sorted(vectors), seed=SEED)
            stores["c1"] = root / "d2q-c1.db"
            build_question_store(stores["c1"], reassign(vectors, mapping))
            base_info["c1_derangement_seed"] = SEED
        a1_path = root / ".openkos-a1" / "vectors.db"
        if "a1" in arms:
            a1_path.parent.mkdir(parents=True)
            shutil.copy2(vectors_path, a1_path)
            base_info["a1_rows_added"] = extend_decision_rows(a1_path, vectors)
        a3_fts = root / ".openkos-a3" / "fts.db"
        if "a3" in arms:
            a3_fts.parent.mkdir(parents=True)
            a3_bundle = root / "bundle-a3"
            append_questions_to_bundle(bundle, a3_bundle, gen)
            fts_mod.refresh_fts_index(a3_fts, a3_bundle)

        def open_fts(path: pathlib.Path) -> Any:
            index = fts_mod.open_fts_index_readonly(path)
            if index is None:
                raise RuntimeError(f"the FTS store {path} was not written")
            return index

        for arm in arms:
            rankings: dict[str, Any] = {}
            fts_index = open_fts(a3_fts if arm == "a3" else fts_path)
            try:
                with open_vector_store(a1_path if arm == "a1" else vectors_path) as db:
                    qdb = (
                        open_vector_store(stores[arm]) if arm in ("a2", "c1") else None
                    )
                    try:
                        for q in questions:
                            shipped = plumbing.retrieve(
                                q.question,
                                fts_index=fts_index,
                                embedder=memo,
                                vector_store=db,
                                excluded=excluded,
                            )
                            if qdb is not None:
                                third = query_hits(
                                    qdb,
                                    memo.embed([q.question])[0],
                                    depth=core.POOL_DEPTH,
                                    excluded=excluded,
                                )
                                shipped["d2q"] = third
                                shipped["fused"] = fused_by_k(
                                    shipped["fts"], shipped["dense"], third, core.KS
                                )
                            rankings[q.id] = shipped
                    finally:
                        if qdb is not None:
                            qdb.close()
            finally:
                fts_index.close()
            out[arm] = _run_header(
                arm=arm,
                corpus_id=corpus.bundle_id,
                model_tag=model_tag,
                questions=questions,
                rankings=rankings,
                info=base_info if arm != "a0" else {},
            )
    return out
