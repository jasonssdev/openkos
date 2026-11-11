"""Model-free excerpt-strategy reach comparison on a Source (#1759).

Question: with the right Source chosen, how often does the excerpt that the
answer prompt keeps of a long Source contain the evidence line, under the
shipped strategy (BM25 over 800-character windows) and five alternatives (smaller
windows, a hybrid BM25 + embedding ranking, a leaner elision marker)? The
pre-registration is `PREREGISTRATION.md`; read it before trusting a number from
here.

Model-free except bge-m3 embeddings (the two hybrid arms): no chat model answers
anything. Nothing here changes a production default.

Subcommands: `run` (the held-out questions; refuses unless their sha256 is the
registered one), `report` (model-free, reads only the final store),
`fixture-old` (the arms on the OLD 30-question fixture of the diagnostic, a
pre-run check on data that is not held out). Model-free check: `--self-test`.

Usage:

    OLLAMA_HOST=127.0.0.1:1 uv run python evals/excerpt_strategy/measure_excerpt_strategy.py --self-test
"""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import math
import pathlib
import re
import sys
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

HERE = pathlib.Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.append(str(REPO_ROOT / "evals"))
sys.path.append(str(REPO_ROOT / "evals" / "transcript_decisions"))

import scoring  # noqa: E402
from harness_stamp import git_identity, model_identity  # noqa: E402

from openkos import prompt_budget  # noqa: E402
from openkos.model import okf  # noqa: E402
from openkos.retrieval.answer import (  # noqa: E402
    _CONTEXT_CHUNK_TARGET,
    CONTEXT_ELISION_MARKER,
)

# --- the registered configuration (PREREGISTRATION.md). Fixed before any run.

MEETINGS: Final = tuple(
    f"{series}{letter}"
    for series in ("ES2015", "TS3009", "IS1004")
    for letter in "abcd"
)
"""Twelve meetings, four per development series (`scoring.DEV_SERIES`)."""
N_ANSWERABLE: Final = 36
N_UNANSWERABLE: Final = 12
REGISTERED_QUESTIONS_SHA256: Final = (
    "beac8f024ded10c59dab741f47d862e3a29ec6580fe4aae98e6dc36d8ecc758c"
)
SHARE_PRIMARY: Final = 2179
"""The shipped per-Source share, in characters. The primary readout."""
SHARE_SECONDARY: Final = 2906
"""The Source fallback's share. Secondary, reported."""
SHARES: Final = (SHARE_PRIMARY, SHARE_SECONDARY)
MARKER: Final = CONTEXT_ELISION_MARKER
LEAN_MARKER: Final = "\n[...]\n"
"""The leaner elision marker of the `bm25-400-lean` arm: 7 characters."""
RRF_K: Final = 60
EMBED_MODEL: Final = "bge-m3:latest"
PASS_GAIN: Final = 6
"""An arm passes only if its reach at the primary share is at least the
shipped reach plus this many questions (of 36)."""
PASS_MAX_LOSSES: Final = 2
"""... and it loses at most this many questions the shipped arm reaches."""
HYBRID_MARGIN: Final = 3
"""A hybrid arm is eligible only if it beats the BM25 arm of the same window
by at least this many questions at the primary share."""
BAIT_FACTOR: Final = 2
"""An arm whose on-topic lines pulled in for the unanswerable controls exceed
this multiple of the shipped arm's count is flagged `premise-bait risk`."""
RERUN_TOLERANCE: Final = 1
EMBED_BATCH: Final = 32

SUPPORTED: Final = "SUPPORTED"
SAFETY_GATED: Final = "SAFETY-GATED"
NOT_SUPPORTED: Final = "NOT SUPPORTED"

STORE_KIND: Final = "excerpt-strategy-store-v1"
DEFAULT_OUT: Final = (
    pathlib.Path.home() / "Dev/Projects/openkos-eval-runs/excerpt-strategy"
)
DEFAULT_GOLD: Final = (
    pathlib.Path.home() / "Dev/Learning/UC/mcd-afg/data/processed/decisions"
)

# The diagnostic's table on the OLD 30-question fixture (issue #1759, first
# comment): oracle reach at (2179, 2906), n of 30. The `fixture-old` check must
# reproduce it; the lean arm had no diagnostic cell.
DIAGNOSTIC_OLD: Final[dict[str, tuple[int, int]]] = {
    "shipped": (10, 14),
    "bm25-400": (15, 19),
    "bm25-200": (16, 17),
    "hybrid-400": (18, 20),
    "hybrid-200": (20, 21),
}

Scorer = Callable[[str, Sequence[str]], list[float]]
"""`(question, windows) -> one embedding score per window` (cosine)."""


@dataclass(frozen=True)
class Arm:
    name: str
    kind: str  # "engine" | "bm25" | "hybrid"
    window: int
    marker: str
    new_state: int
    """0 for an arm that adds no derived state; 1 for one that needs a
    persisted chunk-embedding cache."""


ARMS: Final = (
    Arm("shipped", "engine", _CONTEXT_CHUNK_TARGET, MARKER, 0),
    Arm("bm25-400", "bm25", 400, MARKER, 0),
    Arm("bm25-200", "bm25", 200, MARKER, 0),
    Arm("hybrid-400", "hybrid", 400, MARKER, 1),
    Arm("hybrid-200", "hybrid", 200, MARKER, 1),
    Arm("bm25-400-lean", "bm25", 400, LEAN_MARKER, 0),
)
ARM_NAMES: Final = tuple(a.name for a in ARMS)
"""The registered order, which is also the final tie-break."""


@dataclass(frozen=True)
class Question:
    id: str
    meeting: str
    text: str
    evidence: str


@dataclass(frozen=True)
class Control:
    id: str
    meeting: str
    text: str
    topic: str
    forbidden: str


@dataclass(frozen=True)
class ArmResult:
    reach: int
    """Answerable questions whose evidence is in the excerpt, primary share."""
    losses: int
    """Questions the shipped arm reaches and this arm does not."""
    bait_lines: int
    """On-topic lines in the excerpts of the unanswerable controls."""
    reach_secondary: int = 0
    gains: int = 0


# --- pure core (model-free) -----------------------------------------------------


def arm_by_name(name: str) -> Arm:
    for arm in ARMS:
        if arm.name == name:
            return arm
    raise KeyError(name)


def holds_line(evidence: str, text: str) -> bool:
    """Whether the evidence line is in the excerpt, verbatim and whole.

    The held-out evidence is one transcript line copied by line number and
    occurs exactly once in its meeting (checked by `questions_problems` before
    any arm runs), so substring containment of the stripped line cannot match
    another line, and a window cut mid-line does not hold it. The long
    harness's looser token rule exists for gold acts, which are not lines."""
    needle = evidence.strip()
    return bool(needle) and needle in text


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if not na or not nb:
        return 0.0
    return sum(x * y for x, y in zip(a, b, strict=True)) / (na * nb)


def rrf_scores(a: Sequence[float], b: Sequence[float], k: int = RRF_K) -> list[float]:
    """Reciprocal rank fusion of two score lists over the same windows; ties go
    to the earlier window, as in the engine's own ranking."""

    def ranks(scores: Sequence[float]) -> list[int]:
        order = sorted(range(len(scores)), key=lambda i: (-scores[i], i))
        out = [0] * len(scores)
        for position, index in enumerate(order):
            out[index] = position
        return out

    ra, rb = ranks(a), ranks(b)
    return [1 / (k + ra[i]) + 1 / (k + rb[i]) for i in range(len(a))]


def pack(
    windows: Sequence[str], scores: Sequence[float], *, budget: int, marker: str
) -> str:
    """The engine's greedy selection (`prompt_budget._query_aware_excerpt`) for
    arbitrary window scores: highest score first, ties to the earlier window, a
    window that does not fit is skipped, a join costs 1 character when adjacent
    and `len(marker)` across a gap. The self-test pins it equal to the engine's
    function under BM25 scores."""
    if not windows:
        return ""
    ranked = sorted(range(len(windows)), key=lambda i: (-scores[i], i))

    def join_cost(left: int, right: int) -> int:
        return 1 if right == left + 1 else len(marker)

    picked: list[int] = []
    size = 0
    for index in ranked:
        position = bisect.bisect_left(picked, index)
        before = picked[position - 1] if position > 0 else None
        after = picked[position] if position < len(picked) else None
        added = len(windows[index])
        if before is not None:
            added += join_cost(before, index)
        if after is not None:
            added += join_cost(index, after)
        if before is not None and after is not None:
            added -= join_cost(before, after)
        if size + added > budget:
            continue
        picked.insert(position, index)
        size += added
    if not picked:
        return windows[ranked[0]][:budget]
    parts: list[str] = []
    previous: int | None = None
    for index in picked:
        if previous is not None:
            parts.append(marker if index > previous + 1 else "\n")
        parts.append(windows[index])
        previous = index
    return "".join(parts)


def excerpt(
    arm: Arm, body: str, question: str, budget: int, scorer: Scorer | None
) -> str:
    """What the prompt would keep of `body` for `question` under `arm`, with the
    right Source chosen. The shipped and BM25 arms go through the engine's own
    `bounded_text`; a hybrid arm ranks by RRF of BM25 and embedding."""
    share = prompt_budget.fair_shares([len(body)], budget=budget)[0]
    windows = prompt_budget.chunk_lines(body, arm.window)
    if arm.kind in ("engine", "bm25"):
        text, _ = prompt_budget.bounded_text(
            body, budget=share, windows=windows, marker=arm.marker, query=question
        )
        return text
    if len(body) <= share:
        return body
    if scorer is None:
        raise ValueError(f"{arm.name} needs an embedding scorer")
    fused = rrf_scores(
        prompt_budget._bm25_scores(windows, question), scorer(question, windows)
    )
    return pack(windows, fused, budget=share, marker=arm.marker)


def paired(arm: Mapping[str, bool], base: Mapping[str, bool]) -> tuple[int, int]:
    """(gained, lost) of `arm` against `base`, question by question."""
    gains = sum(1 for k, v in base.items() if arm[k] and not v)
    losses = sum(1 for k, v in base.items() if v and not arm[k])
    return gains, losses


def arm_passes(reach: int, shipped_reach: int, losses: int) -> bool:
    return reach >= shipped_reach + PASS_GAIN and losses <= PASS_MAX_LOSSES


def hybrid_justified(hybrid_reach: int, bm25_reach: int) -> bool:
    return hybrid_reach >= bm25_reach + HYBRID_MARGIN


def premise_bait(arm_lines: int, shipped_lines: int) -> bool:
    return arm_lines > BAIT_FACTOR * shipped_lines


def _choose(names: Sequence[str], results: Mapping[str, ArmResult]) -> str | None:
    """The registered choice among arms that pass: a hybrid arm stands only if
    it beats the BM25 arm of its window by the margin, and then it replaces that
    arm; then fewest new state, then highest reach, then the registered order."""
    eligible: list[str] = []
    for name in names:
        arm = arm_by_name(name)
        if arm.kind == "hybrid":
            if hybrid_justified(
                results[name].reach, results[f"bm25-{arm.window}"].reach
            ):
                eligible.append(name)
        else:
            replaced = any(
                arm_by_name(o).kind == "hybrid"
                and name == f"bm25-{arm_by_name(o).window}"
                and hybrid_justified(results[o].reach, results[name].reach)
                for o in names
            )
            if not replaced:
                eligible.append(name)
    if not eligible:
        return None
    return min(
        eligible,
        key=lambda n: (arm_by_name(n).new_state, -results[n].reach, ARM_NAMES.index(n)),
    )


def decide(results: Mapping[str, ArmResult]) -> tuple[str, str | None]:
    """`(verdict, arm)` from the per-arm results. SUPPORTED names the
    recommended arm; SAFETY-GATED names an arm that passes on reach but is
    flagged premise-bait (it cannot be the recommendation without a model-side
    safety measurement); NOT SUPPORTED keeps shipped."""
    shipped = results["shipped"]
    passing = [
        n
        for n in ARM_NAMES
        if n != "shipped"
        and arm_passes(results[n].reach, shipped.reach, results[n].losses)
    ]
    clear = [
        n
        for n in passing
        if not premise_bait(results[n].bait_lines, shipped.bait_lines)
    ]
    chosen = _choose(clear, results)
    if chosen is not None:
        return SUPPORTED, chosen
    if not clear:
        gated = _choose(passing, results)
        if gated is not None:
            return SAFETY_GATED, gated
    return NOT_SUPPORTED, None


# --- cells, store, report ---------------------------------------------------------


def _is_marker_line(line: str) -> bool:
    return line.strip() in {MARKER.strip(), LEAN_MARKER.strip()}


def control_counts(text: str, control: Control) -> tuple[int, int]:
    """(on-topic lines, forbidden-value lines) of an excerpt for a control."""
    lines = [ln for ln in text.split("\n") if not _is_marker_line(ln)]
    topic = sum(1 for ln in lines if re.search(control.topic, ln, re.IGNORECASE))
    forbidden = sum(
        1 for ln in lines if re.search(control.forbidden, ln, re.IGNORECASE)
    )
    return topic, forbidden


def run_cells(
    questions: Sequence[Question],
    controls: Sequence[Control],
    bodies: Mapping[str, str],
    scorer: Scorer | None,
    *,
    stamp: Mapping[str, Any],
    questions_sha256: str | None = None,
) -> dict[str, Any]:
    cells: list[dict[str, Any]] = []
    ctrl: list[dict[str, Any]] = []
    for arm in ARMS:
        for share in SHARES:
            for q in questions:
                text = excerpt(arm, bodies[q.meeting], q.text, share, scorer)
                cells.append(
                    {
                        "arm": arm.name,
                        "share": share,
                        "id": q.id,
                        "hit": holds_line(q.evidence, text),
                    }
                )
            for c in controls:
                text = excerpt(arm, bodies[c.meeting], c.text, share, scorer)
                topic, forbidden = control_counts(text, c)
                ctrl.append(
                    {
                        "arm": arm.name,
                        "share": share,
                        "id": c.id,
                        "topic_lines": topic,
                        "forbidden_lines": forbidden,
                    }
                )
    return {
        "kind": STORE_KIND,
        "questions_sha256": questions_sha256,
        "stamp": dict(stamp),
        "answerable_ids": [q.id for q in questions],
        "control_ids": [c.id for c in controls],
        "cells": cells,
        "controls": ctrl,
    }


def store_problems(store: Mapping[str, Any], *, expect_sha: str | None) -> list[str]:
    problems: list[str] = []
    if store.get("kind") != STORE_KIND:
        problems.append(f"not a {STORE_KIND} store")
        return problems
    if expect_sha is not None and store.get("questions_sha256") != expect_sha:
        problems.append("the store was not run on the registered questions file")
    ids = list(store.get("answerable_ids", []))
    cids = list(store.get("control_ids", []))
    if len(ids) != N_ANSWERABLE or len(set(ids)) != len(ids):
        problems.append(f"expected {N_ANSWERABLE} distinct answerable ids")
    if len(cids) != N_UNANSWERABLE or len(set(cids)) != len(cids):
        problems.append(f"expected {N_UNANSWERABLE} distinct control ids")
    for label, rows, want in (
        ("cells", store.get("cells", []), ids),
        ("controls", store.get("controls", []), cids),
    ):
        keys = [(r["arm"], r["share"], r["id"]) for r in rows]
        if len(set(keys)) != len(keys):
            problems.append(f"REFUSED: {label} hold a duplicate (arm, share, id)")
        expected = {(a, s, i) for a in ARM_NAMES for s in SHARES for i in want}
        if set(keys) != expected:
            problems.append(f"{label} are not exactly arms x shares x ids")
    return problems


def summarize(store: Mapping[str, Any]) -> dict[str, ArmResult]:
    ids = list(store["answerable_ids"])

    def hits(arm: str, share: int) -> dict[str, bool]:
        got = {
            c["id"]: bool(c["hit"])
            for c in store["cells"]
            if c["arm"] == arm and c["share"] == share
        }
        return {i: got[i] for i in ids}

    base = hits("shipped", SHARE_PRIMARY)
    out: dict[str, ArmResult] = {}
    for name in ARM_NAMES:
        mine = hits(name, SHARE_PRIMARY)
        gains, losses = paired(mine, base)
        lines = sum(
            int(c["topic_lines"])
            for c in store["controls"]
            if c["arm"] == name and c["share"] == SHARE_PRIMARY
        )
        out[name] = ArmResult(
            reach=sum(mine.values()),
            losses=losses,
            bait_lines=lines,
            reach_secondary=sum(hits(name, SHARE_SECONDARY).values()),
            gains=gains,
        )
    return out


def _rerun_note(a: Mapping[str, ArmResult], b: Mapping[str, ArmResult]) -> str:
    moved: list[str] = []
    for name in ARM_NAMES:
        tol = RERUN_TOLERANCE if arm_by_name(name).kind == "hybrid" else 0
        for label, x, y in (
            ("2179", a[name].reach, b[name].reach),
            ("2906", a[name].reach_secondary, b[name].reach_secondary),
        ):
            if abs(x - y) > tol:
                moved.append(f"{name}@{label}: {x} vs {y}")
    if moved:
        return "rerun: DISCLOSE (" + "; ".join(moved) + ")"
    return "rerun: within tolerance"


def render_report(
    store: Mapping[str, Any], rerun: Mapping[str, Any] | None = None
) -> str:
    results = summarize(store)
    verdict, arm = decide(results)
    shipped = results["shipped"]
    lines = [
        "excerpt strategy reach, oracle Source chosen, n of 36 answerable questions",
        "",
        f"{'arm':<15}{'@2179':>7}{'@2906':>7}{'gain':>6}{'lost':>6}{'bait lines':>12}  flags",
    ]
    for name in ARM_NAMES:
        r = results[name]
        flags: list[str] = []
        if name != "shipped":
            if arm_passes(r.reach, shipped.reach, r.losses):
                flags.append("passes")
            if premise_bait(r.bait_lines, shipped.bait_lines):
                flags.append("premise-bait risk")
            a = arm_by_name(name)
            if a.kind == "hybrid":
                m = r.reach - results[f"bm25-{a.window}"].reach
                flags.append(f"vs bm25-{a.window}: {m:+d}")
        lines.append(
            f"{name:<15}{r.reach:>7}{r.reach_secondary:>7}{r.gains:>6}{r.losses:>6}"
            f"{r.bait_lines:>12}  {', '.join(flags)}"
        )
    forbidden = sum(int(c["forbidden_lines"]) for c in store["controls"])
    lines += [
        "",
        f"forbidden-value lines pulled in by any arm (must be 0): {forbidden}",
        f"verdict: {verdict}",
        f"recommend: {arm if arm else 'keep shipped'}",
    ]
    if verdict == SAFETY_GATED:
        lines.append(
            "the arm above passes on reach but is flagged premise-bait: it is not "
            "the recommendation without a model-side safety measurement"
        )
    if rerun is not None:
        lines.append(_rerun_note(results, summarize(rerun)))
    return "\n".join(lines)


# --- data loading (held-out questions, transcripts) -------------------------------


def load_questions(path: pathlib.Path) -> tuple[list[Question], list[Control]]:
    """The held-out questions, refused unless the file is byte-for-byte the
    registered one. The `answer` field is deliberately never read."""
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != REGISTERED_QUESTIONS_SHA256:
        raise SystemExit(
            f"REFUSED: questions file sha256 {digest} is not the registered "
            f"{REGISTERED_QUESTIONS_SHA256}"
        )
    data = json.loads(raw)
    qs = [
        Question(r["id"], r["meeting"], r["question"], r["evidence"])
        for r in data["answerable"]
    ]
    cs = [
        Control(
            r["id"], r["meeting"], r["question"], r["topic_regex"], r["forbidden_regex"]
        )
        for r in data["unanswerable"]
    ]
    return qs, cs


def questions_problems(
    qs: Sequence[Question], cs: Sequence[Control], bodies: Mapping[str, str]
) -> list[str]:
    """Preconditions on the DATA, not a reach: the registered counts, development
    meetings only, every evidence line occurring exactly once in its meeting."""
    problems: list[str] = []
    if len(qs) != N_ANSWERABLE or len(cs) != N_UNANSWERABLE:
        problems.append(f"expected {N_ANSWERABLE} + {N_UNANSWERABLE} questions")
    if len({q.id for q in qs} | {c.id for c in cs}) != len(qs) + len(cs):
        problems.append("question ids are not unique")
    items: list[Question | Control] = [*qs, *cs]
    for item in items:
        if item.meeting not in MEETINGS:
            problems.append(f"{item.id}: meeting {item.meeting} is not registered")
    for q in qs:
        body = bodies.get(q.meeting, "")
        if body.count(q.evidence.strip()) != 1:
            problems.append(f"{q.id}: evidence does not occur exactly once")
    for c in cs:
        for pattern in (c.topic, c.forbidden):
            try:
                re.compile(pattern)
            except re.error:
                problems.append(f"{c.id}: bad regex")
    return problems


def load_bodies(transcripts: pathlib.Path) -> dict[str, str]:
    scoring.require_dev_meetings(MEETINGS)
    bodies: dict[str, str] = {}
    for meeting in MEETINGS:
        path = transcripts / f"{meeting}.md"
        if not path.is_file():
            raise SystemExit(f"no transcript for {meeting}: {path}")
        meta, body = okf.load_frontmatter(path.read_text(encoding="utf-8"))
        if not meta:
            raise SystemExit(f"{path.name}: no frontmatter; not a rendered transcript")
        bodies[meeting] = body
    return bodies


# --- embeddings (the only model) ----------------------------------------------------


class EmbeddingCache:
    """bge-m3 vectors keyed by the sha1 of the text, persisted as JSON outside
    the repository. A derived cache: it holds vectors, never text."""

    def __init__(
        self,
        embed: Callable[[Sequence[str]], list[list[float]]],
        path: pathlib.Path | None = None,
    ) -> None:
        self._embed = embed
        self._path = path
        self._vec: dict[str, list[float]] = {}
        if path is not None and path.is_file():
            self._vec = json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _key(text: str) -> str:
        return hashlib.sha1(text.encode(), usedforsecurity=False).hexdigest()

    def vectors(self, texts: Sequence[str]) -> list[list[float]]:
        todo = list(dict.fromkeys(t for t in texts if self._key(t) not in self._vec))
        for i in range(0, len(todo), EMBED_BATCH):
            batch = todo[i : i + EMBED_BATCH]
            for text, vec in zip(batch, self._embed(batch), strict=True):
                self._vec[self._key(text)] = list(vec)
        return [self._vec[self._key(t)] for t in texts]

    def scorer(self) -> Scorer:
        def score(question: str, windows: Sequence[str]) -> list[float]:
            (qv,) = self.vectors([question])
            return [cosine(qv, v) for v in self.vectors(windows)]

        return score

    def save(self) -> None:
        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(self._vec), encoding="utf-8")


def _ollama_cache(path: pathlib.Path | None) -> EmbeddingCache:
    from openkos.llm.ollama import OllamaClient

    client = OllamaClient(EMBED_MODEL, timeout=300)
    return EmbeddingCache(client.embed, path)


# --- commands -------------------------------------------------------------------------


def _load_store(path: pathlib.Path) -> dict[str, Any]:
    store = json.loads(path.read_text(encoding="utf-8"))
    problems = store_problems(store, expect_sha=REGISTERED_QUESTIONS_SHA256)
    if problems:
        raise SystemExit(f"REFUSED: {path}: " + "; ".join(problems))
    return dict(store)


def cmd_run(args: argparse.Namespace) -> int:
    qs, cs = load_questions(args.questions)
    bodies = load_bodies(args.transcripts)
    problems = questions_problems(qs, cs, bodies)
    if problems:
        raise SystemExit("REFUSED: " + "; ".join(problems))
    stamp: dict[str, Any] = {
        "harness": git_identity(),
        "embedding_model": model_identity(EMBED_MODEL),
        "arms": list(ARM_NAMES),
    }
    if stamp["harness"].get("dirty"):
        raise SystemExit(
            "REFUSED: the harness checkout has uncommitted tracked changes"
        )
    cache = _ollama_cache(args.emb_cache or args.out / "emb-cache.json")
    t0 = time.time()
    store = run_cells(
        qs,
        cs,
        bodies,
        cache.scorer(),
        stamp=stamp,
        questions_sha256=REGISTERED_QUESTIONS_SHA256,
    )
    cache.save()
    store["wall_seconds"] = round(time.time() - t0, 1)
    args.out.mkdir(parents=True, exist_ok=True)
    name = time.strftime("excerpts-%Y%m%dT%H%M%S.json", time.gmtime())
    (args.out / name).write_text(json.dumps(store, indent=1), encoding="utf-8")
    print(f"wrote {args.out / name}")
    print(render_report(store))
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    store = _load_store(args.store)
    rerun = _load_store(args.rerun) if args.rerun else None
    print(render_report(store, rerun))
    return 0


def cmd_fixture_old(args: argparse.Namespace) -> int:
    """The arms on the OLD 30-question fixture, through the long harness's own
    loaders and containment rule: a pre-run check that they reproduce the
    diagnostic's table. Not held-out data; no verdict is drawn from it."""
    import importlib

    sys.path.insert(0, str(REPO_ROOT / "evals" / "source_fallback"))
    long = importlib.import_module("measure_source_fallback_long")
    data = long.load_data(args.gold, args.transcripts, args.glosses)
    questions = long.derive_questions(data)
    cache = _ollama_cache(args.emb_cache)
    scorer = cache.scorer()
    engine_equal = True
    table: dict[str, list[int]] = {}
    hit_sets: dict[str, list[bool]] = {}
    for arm in ARMS:
        counts: list[int] = []
        for share in SHARES:
            hits: list[bool] = []
            for q in questions:
                body = data.bodies[q.fact.meeting]
                text = excerpt(arm, body, q.text, share, scorer)
                hits.append(bool(long._holds_fact(q.fact, text)))
                if arm.kind == "engine":
                    ref = long.msf.bound_sources(
                        [long.msf.Source("s", "t", body)], q.text, share
                    )[0]["text"]
                    engine_equal = engine_equal and ref == text
            counts.append(sum(hits))
            if share == SHARE_PRIMARY:
                hit_sets[arm.name] = hits
        table[arm.name] = counts
    cache.save()
    reproduced = True
    print(f"old fixture: {len(questions)} questions; oracle reach (@2179, @2906)")
    for name in ARM_NAMES:
        got = tuple(table[name])
        want = DIAGNOSTIC_OLD.get(name)
        mine = {str(i): h for i, h in enumerate(hit_sets[name])}
        base = {str(i): h for i, h in enumerate(hit_sets["shipped"])}
        gains, losses = paired(mine, base)
        note = "no diagnostic cell" if want is None else f"diagnostic {want}"
        if want is not None and got != want:
            reproduced = False
            note += "  MISMATCH"
        print(f"  {name:<15}{got}  gain {gains} lost {losses}  {note}")
    print(f"shipped arm equals the engine's bound_sources text: {engine_equal}")
    print(
        f"reproduces the diagnostic: {'yes' if reproduced and engine_equal else 'NO'}"
    )
    return 0 if reproduced and engine_equal else 1


# --- self-test (model-free; synthetic data only) -------------------------------


def _synthetic_bodies() -> dict[str, str]:
    bodies: dict[str, str] = {}
    for m in MEETINGS:
        lines = [
            f"A: so the remote control design team talks about the battery and "
            f"the case, item {k}, and then we move on to the next thing now."
            for k in range(60)
        ]
        lines[30] = f"B: the zorblax{m} costs fourteen euros each so we keep it."
        bodies[m] = "\n".join(lines)
    return bodies


def _synthetic_questions() -> tuple[list[Question], list[Control]]:
    qs = [
        Question(f"{m}-q{i}", m, f"How much does the zorblax{m} cost?", ln)
        for m in MEETINGS
        for i, ln in enumerate(
            [f"B: the zorblax{m} costs fourteen euros each so we keep it."] * 3
        )
    ]
    cs = [
        Control(
            f"{m}-u",
            m,
            "What is the battery lifetime in hours?",
            "battery",
            r"\d+ hours",
        )
        for m in MEETINGS
    ]
    return qs, cs


def _fake_scorer(question: str, windows: Sequence[str]) -> list[float]:
    q = set(question.lower().split())
    return [len(q & set(w.lower().split())) / (1 + len(w) / 100) for w in windows]


def _self_test() -> int:
    failures: list[str] = []

    def check(label: str, ok: bool) -> None:
        if not ok:
            failures.append(label)

    # containment
    check("evidence line is held", holds_line("B: five euros", "x\nB: five euros\ny"))
    check("a cut line is not held", not holds_line("B: five euros", "B: five eu"))
    check("absent evidence is not held", not holds_line("B: five euros", "A: ten"))
    check("empty evidence is never held", not holds_line("  ", "anything"))

    # scoring helpers
    check(
        "cosine of identical vectors is 1",
        abs(cosine([1.0, 2.0], [1.0, 2.0]) - 1) < 1e-9,
    )
    check(
        "cosine of orthogonal vectors is 0", abs(cosine([1.0, 0.0], [0.0, 1.0])) < 1e-9
    )
    check("cosine with a zero vector is 0", cosine([0.0, 0.0], [1.0, 1.0]) == 0.0)
    r = rrf_scores([3.0, 2.0, 1.0], [1.0, 3.0, 2.0])
    check("rrf prefers the window ranked well by both", r[1] > r[0] > r[2])
    tie = rrf_scores([1.0, 1.0], [1.0, 1.0])
    check("rrf ties go to the earlier window", tie[0] > tie[1])

    # pack == the engine's own function under BM25 scores
    wins = prompt_budget.chunk_lines(_synthetic_bodies()["ES2015a"], 200)
    for query in ("zorblaxES2015a cost", "battery case", "remote control design"):
        for budget in (300, 900, 2179, 2906):
            for mk in (MARKER, LEAN_MARKER):
                eng = prompt_budget._query_aware_excerpt(
                    wins, budget=budget, marker=mk, query=query
                )
                mine = pack(
                    wins,
                    prompt_budget._bm25_scores(wins, query),
                    budget=budget,
                    marker=mk,
                )
                check(f"pack == engine ({query!r}, {budget})", eng == mine)
    check(
        "lean marker is shorter", len(LEAN_MARKER) < len(MARKER) and len(MARKER) == 51
    )

    # the shipped arm IS the engine path; windows are the shipped 800
    bodies = _synthetic_bodies()
    shipped = arm_by_name("shipped")
    body = bodies["TS3009b"]
    share = prompt_budget.fair_shares([len(body)], budget=SHARE_PRIMARY)[0]
    eng_text, _ = prompt_budget.bounded_text(
        body,
        budget=share,
        windows=prompt_budget.chunk_lines(body, _CONTEXT_CHUNK_TARGET),
        marker=CONTEXT_ELISION_MARKER,
        query="battery case",
    )
    check(
        "shipped arm equals bounded_text",
        excerpt(shipped, body, "battery case", SHARE_PRIMARY, None) == eng_text,
    )
    check(
        "shipped window is 800", shipped.window == 800 and _CONTEXT_CHUNK_TARGET == 800
    )
    for a in ARMS:
        out = excerpt(a, body, "battery case", SHARE_PRIMARY, _fake_scorer)
        check(f"{a.name} stays within the share", len(out) <= SHARE_PRIMARY)
    check(
        "a Source that fits is returned whole",
        excerpt(arm_by_name("hybrid-200"), "a\nb", "a", SHARE_PRIMARY, _fake_scorer)
        == "a\nb",
    )

    # paired gains and losses
    base = {"a": True, "b": True, "c": False, "d": False}
    arm = {"a": True, "b": False, "c": True, "d": True}
    check("paired gains and losses", paired(arm, base) == (2, 1))

    # the registered bars, at their boundaries
    check("pass at +6 and 2 losses", arm_passes(16, 10, 2))
    check("no pass at +5", not arm_passes(15, 10, 0))
    check("no pass at 3 losses", not arm_passes(30, 10, 3))
    check("hybrid justified at +3", hybrid_justified(18, 15))
    check("hybrid not justified at +2", not hybrid_justified(17, 15))
    check("bait at 2x + 1", premise_bait(11, 5))
    check("no bait at exactly 2x", not premise_bait(10, 5))
    check(
        "any on-topic line is bait when shipped pulled none",
        premise_bait(1, 0) and not premise_bait(0, 0),
    )

    def res(reach: int, losses: int = 0, bait: int = 5) -> ArmResult:
        return ArmResult(reach, losses, bait)

    def rs(**kw: ArmResult) -> dict[str, ArmResult]:
        out = {"shipped": res(10, 0, 5)}
        out.update({k.replace("_", "-"): v for k, v in kw.items()})
        for a in ARM_NAMES:
            out.setdefault(a, res(10, 0, 5))
        return out

    check("nothing passes: keep shipped", decide(rs()) == (NOT_SUPPORTED, None))
    check("only +5 passes nothing", decide(rs(bm25_400=res(15)))[0] == NOT_SUPPORTED)
    check(
        "a lone BM25 pass is recommended",
        decide(rs(bm25_400=res(16))) == (SUPPORTED, "bm25-400"),
    )
    check(
        "losses above 2 disqualify", decide(rs(bm25_400=res(20, 3)))[0] == NOT_SUPPORTED
    )
    check(
        "hybrid within +2 of BM25 is dropped",
        decide(rs(bm25_200=res(16), hybrid_200=res(18))) == (SUPPORTED, "bm25-200"),
    )
    check(
        "hybrid at +3 over BM25 is preferred over it",
        decide(rs(bm25_200=res(16), hybrid_200=res(19))) == (SUPPORTED, "hybrid-200"),
    )
    check(
        "a hybrid pass with a non-passing BM25 twin needs the margin",
        decide(rs(bm25_200=res(15), hybrid_200=res(17)))[0] == NOT_SUPPORTED
        and decide(rs(bm25_200=res(14), hybrid_200=res(17)))
        == (SUPPORTED, "hybrid-200"),
    )
    check(
        "fewest new state beats higher reach",
        decide(rs(bm25_400=res(16), hybrid_200=res(25, 0, 5), bm25_200=res(10)))
        == (SUPPORTED, "bm25-400"),
    )
    check(
        "then highest reach",
        decide(rs(bm25_400=res(16), bm25_400_lean=res(18)))
        == (SUPPORTED, "bm25-400-lean"),
    )
    check(
        "then the registered order",
        decide(rs(bm25_400=res(16), bm25_200=res(16))) == (SUPPORTED, "bm25-400"),
    )
    check(
        "a flagged arm cannot be the recommendation",
        decide(rs(bm25_400=res(16, 0, 11), bm25_200=res(16, 0, 5)))
        == (SUPPORTED, "bm25-200"),
    )
    check(
        "only flagged passers is safety-gated",
        decide(rs(bm25_400=res(16, 0, 11))) == (SAFETY_GATED, "bm25-400"),
    )
    check(
        "a flagged hybrid leaves its BM25 twin standing",
        decide(rs(bm25_200=res(16), hybrid_200=res(20, 0, 11)))
        == (SUPPORTED, "bm25-200"),
    )

    # a synthetic end-to-end run, then the store contract
    qs, cs = _synthetic_questions()
    store = run_cells(qs, cs, bodies, _fake_scorer, stamp={"synthetic": True})
    check("store is complete", not store_problems(store, expect_sha=None))
    summary = summarize(store)
    check(
        "synthetic: every arm reaches every question",
        all(s.reach == 36 for s in summary.values()),
    )
    check(
        "synthetic: nothing to gain, keep shipped",
        decide(summary) == (NOT_SUPPORTED, None),
    )
    text = render_report(store)
    check(
        "report names the verdict",
        "verdict: NOT SUPPORTED" in text and "shipped" in text,
    )
    cells = [dict(c) for c in store["cells"]]
    for c in cells:
        if (
            c["arm"] == "shipped"
            and c["share"] == SHARE_PRIMARY
            and int(c["id"][-1]) < 2
        ):
            c["hit"] = False
    doctored = {**store, "cells": cells}
    s2 = summarize(doctored)
    check(
        "a doctored store gives bm25 a win",
        s2["bm25-400"].reach - s2["shipped"].reach == 24 and decide(s2)[0] == SUPPORTED,
    )
    check(
        "report prints the chosen arm", "recommend: bm25-400" in render_report(doctored)
    )
    dup = {**store, "cells": store["cells"] + store["cells"][:1]}
    check("duplicate cells are refused", bool(store_problems(dup, expect_sha=None)))
    short = {**store, "cells": store["cells"][:-1]}
    check(
        "an incomplete store is refused", bool(store_problems(short, expect_sha=None))
    )
    check(
        "the wrong fixture hash is refused",
        bool(store_problems(store, expect_sha=REGISTERED_QUESTIONS_SHA256)),
    )
    rr = render_report(store, store)
    check("an identical rerun is within tolerance", "rerun: within tolerance" in rr)
    moved = [dict(c) for c in store["cells"]]
    n = 0
    for c in moved:
        if c["arm"] == "hybrid-200" and c["share"] == SHARE_PRIMARY and n < 2:
            c["hit"] = not c["hit"]
            n += 1
    check(
        "a rerun moving 2 questions is disclosed",
        "rerun: DISCLOSE" in render_report(store, {**store, "cells": moved}),
    )

    # the question file contract
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "q.json"
        path.write_text(
            json.dumps({"answerable": [], "unanswerable": []}), encoding="utf-8"
        )
        refused = False
        try:
            load_questions(path)
        except SystemExit as exc:
            refused = "REFUSED" in str(exc) and "sha256" in str(exc)
        check("a questions file with another sha256 is refused", refused)
    check(
        "registered sha256 is a sha256",
        re.fullmatch(r"[0-9a-f]{64}", REGISTERED_QUESTIONS_SHA256) is not None,
    )
    check(
        "12 meetings, 4 per development series",
        len(set(MEETINGS)) == 12 and N_ANSWERABLE == 36 and N_UNANSWERABLE == 12,
    )
    scoring.require_dev_meetings(MEETINGS)
    check(
        "six registered arms, shipped first",
        ARM_NAMES[0] == "shipped" and len(ARMS) == 6,
    )
    check("shares are the registered ones", SHARES == (2179, 2906))

    if failures:
        print("self-test FAILED:\n  " + "\n  ".join(failures))
        return 1
    print("self-test ok")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--self-test", action="store_true")
    sub = parser.add_subparsers(dest="cmd")
    run_p = sub.add_parser("run", help="run the arms on the held-out questions")
    run_p.add_argument("--questions", type=pathlib.Path, required=True)
    run_p.add_argument("--transcripts", type=pathlib.Path, required=True)
    run_p.add_argument("--out", type=pathlib.Path, default=DEFAULT_OUT)
    run_p.add_argument("--emb-cache", type=pathlib.Path)
    rep_p = sub.add_parser("report", help="model-free report of a final store")
    rep_p.add_argument("store", type=pathlib.Path)
    rep_p.add_argument("--rerun", type=pathlib.Path)
    old_p = sub.add_parser(
        "fixture-old", help="the arms on the OLD 30-question fixture"
    )
    old_p.add_argument("--gold", type=pathlib.Path, default=DEFAULT_GOLD)
    old_p.add_argument("--transcripts", type=pathlib.Path, required=True)
    old_p.add_argument("--glosses", type=pathlib.Path)
    old_p.add_argument("--emb-cache", type=pathlib.Path)
    args = parser.parse_args(argv)
    if args.self_test:
        return _self_test()
    if args.cmd == "run":
        return cmd_run(args)
    if args.cmd == "report":
        return cmd_report(args)
    if args.cmd == "fixture-old":
        return cmd_fixture_old(args)
    parser.error("a subcommand or --self-test is required")


if __name__ == "__main__":
    sys.exit(main())
