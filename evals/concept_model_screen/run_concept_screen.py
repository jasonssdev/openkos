"""Concept-extraction model screen (#1557): run, score, decide.

Which local chat model should concept extraction use? This harness answers it
without hand annotation: AMI's human abstractive summaries are the gold, each
summary sentence is placed in the extraction window that holds its evidence
(`summlink`), and a model is scored by how much better its extracted
`title: description` units cover a meeting's OWN gold than a FOREIGN meeting's
gold (coverage lift). Read `PREREGISTRATION.md` before trusting any number.

Subcommands (all write under `--runs-dir`, outside both repositories):

- `prepare`   build windows and gold for every registered meeting; print the
              window manifest (hashes only) and gold counts.
- `stage0`    calibration: abort test, warm-up, 3 timed windows per model,
              `ollama ps` memory, judge timing; the timeouts and the forecast.
- `run`       production `extract_concept_union` per (model, meeting, run),
              plus the prose fixtures; every call cached.
- `judge`     the `gpt-oss:20b` G1 precision calls (and judge coverage).
- `score`     embeddings and every registered number for a round, as JSON.
- `decide`    the registered rule for a round (validity, round 1, round 2,
              confirmation), printed and stored.

Model-free check: `--self-test`.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import json
import os
import pathlib
import shutil
import sqlite3
import statistics
import struct
import subprocess
import sys
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict
from typing import Any, Final

HERE = pathlib.Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(HERE))
sys.path.append(str(REPO_ROOT / "evals"))

import screen_data as data  # noqa: E402
import screen_judge as sj  # noqa: E402
import screen_metrics as sm  # noqa: E402
import screen_pipeline as sp  # noqa: E402
from harness_prompts import extraction_prompts  # noqa: E402
from harness_stamp import build_stamp, model_identity  # noqa: E402

from openkos.extraction import concept as concept_mod  # noqa: E402
from openkos.llm.base import Message  # noqa: E402
from openkos.llm.ollama import (  # noqa: E402
    OllamaClient,
    OllamaGenerationCapped,
    OllamaUnavailable,
)

DEFAULT_RUNS_DIR: Final = (
    pathlib.Path.home() / "Dev/Projects/openkos-eval-runs/concept-screen"
)
BASELINE: Final = "qwen3:8b"
CANDIDATES: Final = ("gemma4:26b-a4b", "qwen3:14b", "qwen3.6:27b", "qwen3.6:35b-a3b")
EMBED_MODEL: Final = "bge-m3"
PROSE_FIXTURES: Final = (
    "large-03-skills-vs-tools",
    "medium-08-sdk-skills",
    "medium-09-sdk-skills-notes",
    "medium-10-reunion-plataforma",
    "small-04-pre-build-skills",
)
SPANISH_FIXTURES: Final = ("small-04-pre-build-skills", "medium-10-reunion-plataforma")
STAGES: Final = ("raw", "dedup", "drops", "pre_ceiling", "ceiling", "judge", "final")
TIMEOUT_FLOOR_S: Final = 60.0
TIMEOUT_MULTIPLE: Final = 4.0
WHOLE_SOURCE_TIMEOUT_MULTIPLE: Final = 3.0
STAGE0_TIMEOUT_S: Final = 900.0
JUDGE_TIMEOUT_S: Final = 600.0
COVERAGE_TAU: Final = 0.70

CORPUS_DIR: Final = REPO_ROOT / "examples" / "extraction-corpus"
REGISTERED_WINDOWS: Final[dict[str, list[str]]] = (
    {
        k: list(v["windows_sha256"])
        for k, v in json.loads(
            (HERE / "windows.json").read_text(encoding="utf-8")
        ).items()
    }
    if (HERE / "windows.json").is_file()
    else {}
)
"""The window hashes registered with the pre-registration: a run whose
windows differ (renderer, chunker or transcript drift) is refused."""


def refuse_judge_as_candidate(models: Iterable[str]) -> None:
    """Decision D3: the guardrail judge can never be a candidate."""
    bad = sorted(set(models) & sj.FORBIDDEN_CANDIDATES)
    if bad:
        raise SystemExit(
            f"refusing: {bad} is the guardrail judge and cannot be a candidate"
        )


def safe(model: str) -> str:
    return model.replace(":", "_").replace("/", "_")


# --- corpus ------------------------------------------------------------------


class Corpus:
    """Transcripts, windows and gold for the registered meetings."""

    def __init__(self, runs_dir: pathlib.Path) -> None:
        inputs = runs_dir / "inputs"
        self.transcripts_dir = inputs / "transcripts"
        self.archive = data.AmiArchive(inputs / "ami" / "ami_public_manual_1.6.2.zip")
        self._cache: dict[
            str, tuple[data.Transcript, list[data.Window], data.MeetingGold]
        ] = {}
        self._gold_only: dict[str, data.MeetingGold] = {}

    def meeting(
        self, meeting: str
    ) -> tuple[data.Transcript, list[data.Window], data.MeetingGold]:
        data.require_allowed(meeting)
        if meeting not in self._cache:
            transcript = data.load_transcript(self.transcripts_dir, meeting)
            windows = data.build_windows(
                meeting, transcript.body, concept_mod._chunk_lines
            )
            registered = REGISTERED_WINDOWS.get(meeting)
            if registered is not None and [w.sha256 for w in windows] != registered:
                raise SystemExit(
                    f"{meeting}: windows differ from the registered window list"
                )
            gold = data.meeting_gold(self.archive, meeting, transcript, windows)
            self._cache[meeting] = (transcript, windows, gold)
        return self._cache[meeting]

    def gold_only(self, meeting: str) -> data.MeetingGold:
        data.require_allowed(meeting)
        if meeting not in self._gold_only:
            self._gold_only[meeting] = data.meeting_gold(
                self.archive, meeting, None, None
            )
        return self._gold_only[meeting]


def load_prose(name: str) -> tuple[str, str, Any]:
    """(text, production title, ground truth) for one extraction-corpus fixture."""
    spec = importlib.util.spec_from_file_location(
        "run_cap_eval", REPO_ROOT / "evals" / "extraction_cap" / "run_cap_eval.py"
    )
    if spec is None or spec.loader is None:
        raise SystemExit("cannot load run_cap_eval")
    module = sys.modules.get("run_cap_eval")
    if module is None:
        module = importlib.util.module_from_spec(spec)
        sys.modules["run_cap_eval"] = module
        spec.loader.exec_module(module)
    truth = module.load_ground_truth(
        CORPUS_DIR / "ground-truth" / f"{name}.md", sources_dir=CORPUS_DIR / "sources"
    )
    if not truth.source_exists:
        raise SystemExit(
            f"prose fixture source missing (worktree?): {truth.source_path}"
        )
    text = truth.read_source()
    return text, module.source_title_for(text, truth.source_path), truth


def assert_prose_present() -> int:
    present = sum(
        1 for n in PROSE_FIXTURES if (CORPUS_DIR / "sources" / f"{n}.md").is_file()
    )
    if present != len(PROSE_FIXTURES):
        raise SystemExit(
            f"{present} of {len(PROSE_FIXTURES)} extraction-corpus sources present"
        )
    return present


# --- embeddings --------------------------------------------------------------


class EmbedStore:
    """sqlite cache of unit-normalised bge-m3 vectors, keyed by digest + text."""

    def __init__(
        self,
        path: pathlib.Path,
        embed: Callable[[list[str]], list[list[float]]],
        digest: str,
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS emb (key TEXT PRIMARY KEY, vec BLOB)"
        )
        self.embed = embed
        self.digest = digest

    def _key(self, text: str) -> str:
        import hashlib

        return hashlib.sha256(f"{self.digest}\x00{text}".encode()).hexdigest()

    def vectors(self, texts: Sequence[str]) -> dict[str, list[float]]:
        out: dict[str, list[float]] = {}
        missing: list[str] = []
        for text in dict.fromkeys(texts):
            row = self.db.execute(
                "SELECT vec FROM emb WHERE key=?", (self._key(text),)
            ).fetchone()
            if row is None:
                missing.append(text)
            else:
                blob = row[0]
                out[text] = list(struct.unpack(f"{len(blob) // 4}f", blob))
        for start in range(0, len(missing), 32):
            batch = missing[start : start + 32]
            for text, vec in zip(batch, self.embed(batch), strict=True):
                u = sm.unit(vec)
                self.db.execute(
                    "INSERT OR REPLACE INTO emb VALUES (?, ?)",
                    (self._key(text), struct.pack(f"{len(u)}f", *u)),
                )
                out[text] = u
            self.db.commit()
        return out


def unit_text(obj: Mapping[str, Any]) -> str:
    return f"{obj['title']}: {obj['description']}"


# --- records -----------------------------------------------------------------


def record_path(
    runs_dir: pathlib.Path, model: str, source: str, run: int
) -> pathlib.Path:
    return runs_dir / "records" / safe(model) / source / f"r{run}.json"


def load_record(
    runs_dir: pathlib.Path, model: str, source: str, run: int
) -> dict[str, Any] | None:
    path = record_path(runs_dir, model, source, run)
    if not path.is_file():
        return None
    loaded = json.loads(path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else None


def load_timeouts(runs_dir: pathlib.Path) -> dict[str, float]:
    path = runs_dir / "stage0.json"
    if not path.is_file():
        raise SystemExit(
            "stage0.json missing: run `stage0` first (it fixes the timeouts)"
        )
    stage0 = json.loads(path.read_text(encoding="utf-8"))
    return {m: float(v["timeout_s"]) for m, v in stage0["models"].items()}


def timeout_for(median_window_s: float) -> float:
    """The registered per-call timeout: max(60 s, 4 x the stage-0 median)."""
    return max(TIMEOUT_FLOOR_S, TIMEOUT_MULTIPLE * median_window_s)


# --- commands: prepare / stage0 / run ---------------------------------------


def cmd_prepare(args: argparse.Namespace) -> int:
    present = assert_prose_present()
    corpus = Corpus(args.runs_dir)
    manifest: dict[str, Any] = {}
    meetings = (
        list(data.POOL_DA) + list(data.POOL_F_WINDOWED) + list(data.POOL_F_MEETING_ONLY)
    )
    meetings += [m for m, _ in data.STAGE0_WINDOWS]
    for meeting in dict.fromkeys(meetings):
        _t, windows, gold = corpus.meeting(meeting)
        assigned = sum(1 for v in gold.window_of.values() if v is not None)
        foreign = data.foreign_meeting(meeting)
        manifest[meeting] = {
            "windows": [w.sha256 for w in windows],
            "chars": [len(w.text) for w in windows],
            "gold_sentences": len(gold.sentences),
            "gold_by_section": {
                s: sum(1 for g in gold.sentences if g.section == s)
                for s in data.GOLD_SECTIONS
            },
            "gold_assigned_to_a_window": assigned,
            "windows_with_gold": len(
                {v for v in gold.window_of.values() if v is not None}
            ),
            "has_summlink": gold.has_summlink,
            "foreign": foreign,
            "foreign_gold_sentences": len(corpus.gold_only(foreign).sentences),
        }
    out = args.runs_dir / "manifest.json"
    out.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"extraction-corpus sources present: {present} of {len(PROSE_FIXTURES)}")
    for meeting, row in manifest.items():
        print(
            f"{meeting}: {len(row['windows'])} windows, gold {row['gold_sentences']} "
            f"({row['gold_by_section']}), assigned {row['gold_assigned_to_a_window']}, "
            f"windows with gold {row['windows_with_gold']}, summlink {row['has_summlink']}, "
            f"foreign {row['foreign']} ({row['foreign_gold_sentences']})"
        )
    print(f"wrote {out}")
    return 0


def ollama_ps() -> str:
    try:
        done = subprocess.run(  # noqa: S603 -- fixed argv: the local ollama binary and "ps"
            [shutil.which("ollama") or "/usr/local/bin/ollama", "ps"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        return done.stdout
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"unavailable: {exc}"


def warm_up(model: str, host: str | None) -> float:
    """Load the model at the production context window before timing anything,
    so a cold load never lands inside a timed (and timeout-bound) call."""
    client = OllamaClient(
        model,
        host=host,
        timeout=STAGE0_TIMEOUT_S,
        context_window=sp.CONTEXT_WINDOW,
        max_generation_tokens=8,
    )
    started = time.monotonic()
    with contextlib.suppress(Exception):
        client.chat([{"role": "user", "content": "Reply with the word ok."}])
    return time.monotonic() - started


def abort_test(host: str | None, model: str) -> dict[str, Any]:
    """Does an aborted (timed-out) HTTP call stop generation on the server?

    T0: a tiny call on a warm model. D: an un-aborted long generation. Then the
    same long generation aborted after 5 s, immediately followed by a tiny call
    (T1). If T1 is close to T0 the server stopped; if T1 approaches the rest of
    D, the server kept generating behind the client's back."""
    prompt: list[Message] = [
        {
            "role": "user",
            "content": "Write every integer from 1 to 4000 in words, one per line. Do not stop early.",
        }
    ]
    tiny: list[Message] = [{"role": "user", "content": "Reply with the word ok."}]

    def timed(
        messages: list[Message], timeout: float, ceiling: int
    ) -> tuple[float, str]:
        client = OllamaClient(
            model,
            host=host,
            timeout=timeout,
            context_window=sp.CONTEXT_WINDOW,
            max_generation_tokens=ceiling,
            seed=7,
        )
        started = time.monotonic()
        try:
            client.chat(messages)
            outcome = "ok"
        except Exception as exc:
            outcome = type(exc).__name__
        return time.monotonic() - started, outcome

    warm_up(model, host)
    t0 = [timed(tiny, 120, 8)[0] for _ in range(3)]
    d, d_outcome = timed(prompt, 900, 3000)
    aborted, a_outcome = timed(prompt, 5, 3000)
    t1, _ = timed(tiny, 900, 8)
    t2, _ = timed(tiny, 120, 8)
    stops = t1 < statistics.median(t0) + 0.25 * max(d - 5.0, 0.0)
    return {
        "model": model,
        "t0_tiny_s": t0,
        "d_long_unaborted_s": d,
        "d_outcome": d_outcome,
        "aborted_after_s": aborted,
        "aborted_outcome": a_outcome,
        "t1_tiny_after_abort_s": t1,
        "t2_tiny_again_s": t2,
        "server_stops_on_abort": stops,
    }


def cmd_stage0(args: argparse.Namespace) -> int:
    models = [BASELINE, *CANDIDATES] if not args.models else args.models
    refuse_judge_as_candidate(models)
    corpus = Corpus(args.runs_dir)
    cache = sp.ChatCache(args.runs_dir / "cache" / "chat")
    out: dict[str, Any] = {"date": time.strftime("%Y-%m-%dT%H:%M:%S"), "models": {}}
    print("abort test on", BASELINE, flush=True)
    out["abort_test"] = abort_test(args.host, BASELINE)
    print(json.dumps(out["abort_test"], indent=2), flush=True)
    embedder = OllamaClient(EMBED_MODEL, host=args.host, timeout=300)
    for model in models:
        ident = model_identity(model, args.host)
        load_s = warm_up(model, args.host)
        backend = sp.ScreenBackend(
            model=model,
            digest=ident.get("digest"),
            run=0,
            window_timeout_s=STAGE0_TIMEOUT_S,
            whole_timeout_s=STAGE0_TIMEOUT_S,
            cache=cache,
            send=sp.ollama_sender(args.host),
        )
        times: list[float] = []
        outcomes: list[str] = []
        counters: list[tuple[Any, Any]] = []
        for meeting, index in data.STAGE0_WINDOWS:
            _t, windows, _g = corpus.meeting(meeting)
            with contextlib.suppress(Exception):
                concept_mod._extract_once(windows[index].text, meeting, backend)
            last = backend.records[-1]
            times.append(last.wall_s)
            outcomes.append(last.outcome)
            counters.append((last.prompt_eval_count, last.eval_count))
        embedder.embed(["memory probe"])
        ps = ollama_ps()
        median = statistics.median(times)
        out["models"][model] = {
            "digest": ident.get("digest"),
            "load_s": load_s,
            "window_s": times,
            "outcomes": outcomes,
            "tokens": counters,
            "median_window_s": median,
            "timeout_s": timeout_for(median),
            "whole_source_timeout_s": WHOLE_SOURCE_TIMEOUT_MULTIPLE
            * timeout_for(median),
            "ollama_ps": ps,
        }
        print(
            f"{model}: windows {[round(t, 1) for t in times]} s, outcomes {outcomes}, "
            f"timeout {timeout_for(median):.0f} s",
            flush=True,
        )
        print(ps, flush=True)
    # judge timing: one G1 call on the baseline's first stage-0 window output
    judge_client_s: list[float] = []
    warm_up(sj.JUDGE_MODEL, args.host)
    meeting, index = data.STAGE0_WINDOWS[0]
    _t, windows, _g = corpus.meeting(meeting)
    for _ in range(2):
        started = time.monotonic()
        with contextlib.suppress(Exception):
            OllamaClient(
                sj.JUDGE_MODEL,
                host=args.host,
                timeout=JUDGE_TIMEOUT_S,
                context_window=sp.CONTEXT_WINDOW,
                max_generation_tokens=sp.MAX_GENERATION_TOKENS,
                seed=0,
            ).chat(
                sj.g1_messages(
                    windows[index].text,
                    ["Remote control design: the team discusses the remote."],
                )
            )
        judge_client_s.append(time.monotonic() - started)
    out["judge_s"] = judge_client_s
    out["judge_ps"] = ollama_ps()
    out["stamp"] = build_stamp(
        model=BASELINE, prompts=extraction_prompts(), host=args.host
    )
    (args.runs_dir / "stage0.json").write_text(
        json.dumps(out, indent=2) + "\n", encoding="utf-8"
    )
    print(f"judge call s: {judge_client_s}")
    return 0


def run_one(
    args: argparse.Namespace,
    corpus: Corpus,
    model: str,
    digest: str | None,
    source: str,
    run: int,
    timeouts: Mapping[str, float],
) -> dict[str, Any]:
    cache = sp.ChatCache(args.runs_dir / "cache" / "chat")
    if source.startswith("prose-"):
        text, title, _truth = load_prose(source.removeprefix("prose-"))
        windows = (
            concept_mod._chunk_lines(text)
            if concept_mod.fans_out(text, source_title=title)
            else []
        )
    else:
        transcript, wins, _g = corpus.meeting(source)
        text, title, windows = transcript.body, source, [w.text for w in wins]
        if not concept_mod._is_meeting_shaped(title, text):
            raise SystemExit(
                f"{source}: not meeting-shaped; the run would not be production's"
            )
    backend = sp.ScreenBackend(
        model=model,
        digest=digest,
        run=run,
        window_timeout_s=timeouts[model],
        whole_timeout_s=WHOLE_SOURCE_TIMEOUT_MULTIPLE * timeouts[model],
        cache=cache,
        send=sp.ollama_sender(args.host),
        windows=windows,
    )
    record = sp.run_source(text=text, title=title, backend=backend, cache=cache)
    record.update({"model": model, "digest": digest, "source": source, "run": run})
    return record


def sources_for(pool: str, prose: bool) -> list[str]:
    if pool == "DA":
        meetings = list(data.POOL_DA)
    elif pool == "F":
        meetings = list(data.POOL_F_WINDOWED)
    elif pool == "F-IB":
        meetings = list(data.POOL_F_MEETING_ONLY)
    else:
        raise SystemExit(f"unknown pool {pool}")
    for m in meetings:
        data.require_allowed(m)
    if prose:
        meetings += [f"prose-{n}" for n in PROSE_FIXTURES]
    return meetings


def cmd_run(args: argparse.Namespace) -> int:
    refuse_judge_as_candidate(args.models)
    assert_prose_present()
    timeouts = load_timeouts(args.runs_dir)
    corpus = Corpus(args.runs_dir)
    for model in args.models:
        ident = model_identity(model, args.host)
        stamp_path = args.runs_dir / "records" / safe(model) / "stamp.json"
        stamp_path.parent.mkdir(parents=True, exist_ok=True)
        if not stamp_path.is_file():
            stamp_path.write_text(
                json.dumps(
                    build_stamp(
                        model=model, prompts=extraction_prompts(), host=args.host
                    ),
                    indent=2,
                ),
                encoding="utf-8",
            )
        warmed = False
        for source in sources_for(args.pool, args.prose):
            for run in args.runs:
                path = record_path(args.runs_dir, model, source, run)
                if path.is_file():
                    continue
                if not warmed:
                    print(
                        f"warm-up {model}: {warm_up(model, args.host):.1f} s",
                        flush=True,
                    )
                    warmed = True
                record = run_one(
                    args, corpus, model, ident.get("digest"), source, run, timeouts
                )
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8"
                )
                states = [w["outcome"] for w in record["windows"]]
                print(
                    f"{time.strftime('%H:%M:%S')} {model} {source} r{run}: "
                    f"{len(record['stages']['final'] or [])} objects, {record['wall_s']:.0f} s, "
                    f"windows {dict((s, states.count(s)) for s in set(states))}, failure {record['failure']}",
                    flush=True,
                )
    return 0


# --- judge -------------------------------------------------------------------


def judge_call(
    cache: sp.ChatCache,
    host: str | None,
    messages: list[Message],
    count: int,
    allowed: Sequence[str],
    send: sp.Sender | None = None,
) -> tuple[list[str] | None, float]:
    """One judged item: seed 0, one retry (seed 1000) on an unusable reply."""
    sender = send or sp.ollama_sender(host)
    total = 0.0
    for attempt in range(2):
        seed = attempt * sp.RETRY_SEED_STRIDE
        key = sp.call_key(
            model=sj.JUDGE_MODEL, digest=None, messages=messages, seed=seed
        )
        entry = cache.get(key)
        if entry is None:
            _c, entry = sender(sj.JUDGE_MODEL, messages, seed, JUDGE_TIMEOUT_S)
            entry["timeout_s"] = JUDGE_TIMEOUT_S
            cache.put(key, entry)
        total += float(entry.get("wall_s", 0.0))
        content = entry.get("content")
        if isinstance(content, str):
            labels = sj.parse_labels(content, count, allowed)
            if labels is not None:
                return labels, total
    return None, total


def g1_items(
    record: Mapping[str, Any], windows: Sequence[data.Window]
) -> list[tuple[int, list[str]]]:
    """(window, entries) for every window with at least one FINAL object."""
    by_window: dict[int, list[str]] = {}
    for obj in record["stages"]["final"] or []:
        if obj.get("window") is not None:
            by_window.setdefault(int(obj["window"]), []).append(unit_text(obj))
    return sorted(by_window.items())


def cmd_judge(args: argparse.Namespace) -> int:
    corpus = Corpus(args.runs_dir)
    cache = sp.ChatCache(args.runs_dir / "cache" / "judge")
    out_dir = args.runs_dir / "judge"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = out_dir / "stamp.json"
    if not stamp.is_file():
        stamp.write_text(
            json.dumps(
                build_stamp(
                    model=sj.JUDGE_MODEL,
                    prompts={
                        "screen/g1": sj.G1_SYSTEM,
                        "screen/coverage": sj.COVERAGE_SYSTEM,
                    },
                    host=args.host,
                ),
                indent=2,
            ),
            encoding="utf-8",
        )
    warm_up(sj.JUDGE_MODEL, args.host)
    for model in args.models:
        for meeting in sources_for(args.pool, prose=False):
            _t, windows, gold = corpus.meeting(meeting)
            for run in args.runs:
                record = load_record(args.runs_dir, model, meeting, run)
                if record is None:
                    raise SystemExit(f"no record for {model} {meeting} r{run}")
                path = out_dir / safe(model) / meeting / f"r{run}.json"
                if path.is_file():
                    continue
                result: dict[str, Any] = {"g1": [], "coverage": None}
                for index, entries in g1_items(record, windows):
                    labels, wall = judge_call(
                        cache,
                        args.host,
                        sj.g1_messages(windows[index].text, entries),
                        len(entries),
                        sj.G1_LABELS,
                    )
                    result["g1"].append(
                        {
                            "window": index,
                            "labels": labels,
                            "n": len(entries),
                            "wall_s": wall,
                        }
                    )
                if args.coverage:
                    entries = [unit_text(o) for o in record["stages"]["final"] or []]
                    sentences = [g.text for g in gold.sentences]
                    labels, wall = judge_call(
                        cache,
                        args.host,
                        sj.coverage_messages(entries, sentences),
                        len(sentences),
                        sj.COVERAGE_LABELS,
                    )
                    result["coverage"] = {
                        "labels": labels,
                        "wall_s": wall,
                        "sections": [g.section for g in gold.sentences],
                    }
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(result) + "\n", encoding="utf-8")
                failed = sum(1 for g in result["g1"] if g["labels"] is None)
                print(
                    f"{time.strftime('%H:%M:%S')} judge {model} {meeting} r{run}: {len(result['g1'])} windows, {failed} unparsed",
                    flush=True,
                )
    return 0


def load_judge(
    runs_dir: pathlib.Path, model: str, meeting: str, run: int
) -> dict[str, Any] | None:
    path = runs_dir / "judge" / safe(model) / meeting / f"r{run}.json"
    if not path.is_file():
        return None
    loaded = json.loads(path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else None


# --- scoring -----------------------------------------------------------------

FAILED_WINDOW_STATES: Final = frozenset(
    {"timeout", "capped", "error", "unparseable", "not_called"}
)


def window_lifts(
    record: Mapping[str, Any],
    gold: data.MeetingGold,
    foreign_vecs: Sequence[sm.Vector],
    vec: Mapping[str, sm.Vector],
    stage: str = "final",
) -> dict[int, float]:
    """Per-window lift on one stage's objects attributed to that window."""
    objs = record["stages"].get(stage) or []
    out: dict[int, float] = {}
    for index in sorted({v for v in gold.window_of.values() if v is not None}):
        own = [vec[g.text] for g in gold.window_sentences(index)]
        units = [vec[unit_text(o)] for o in objs if o.get("window") == index]
        value = sm.lift(own, foreign_vecs, units)
        if value is not None:
            out[index] = value
    return out


def meeting_scores(
    record: Mapping[str, Any],
    gold: data.MeetingGold,
    foreign_vecs: Sequence[sm.Vector],
    vec: Mapping[str, sm.Vector],
    stage: str = "final",
) -> dict[str, Any] | None:
    objs = record["stages"].get(stage)
    if objs is None:
        return None
    units = [vec[unit_text(o)] for o in objs]
    own = [vec[g.text] for g in gold.sentences]
    out: dict[str, Any] = {
        "own": sm.soft_cov(own, units),
        "foreign": sm.soft_cov(foreign_vecs, units),
        "lift": sm.lift(own, foreign_vecs, units),
        "n_units": len(units),
        "own_curve": [sm.cov_tau(own, units, t) for t in sm.TAU_GRID],
        "foreign_curve": [sm.cov_tau(foreign_vecs, units, t) for t in sm.TAU_GRID],
        "sections": {
            s: sm.soft_cov(
                [vec[g.text] for g in gold.sentences if g.section == s], units
            )
            for s in data.GOLD_SECTIONS
        },
        "own_sum": sum(sm.max_sims(own, units)),
        "own_covered_tau": sum(1 for x in sm.max_sims(own, units) if x >= COVERAGE_TAU),
        "foreign_covered_tau": sum(
            1 for x in sm.max_sims(foreign_vecs, units) if x >= COVERAGE_TAU
        ),
        "gold_n": len(own),
        "foreign_n": len(foreign_vecs),
    }
    return out


def collect_texts(records: Iterable[Mapping[str, Any]]) -> list[str]:
    texts: list[str] = []
    for record in records:
        for stage in STAGES:
            for obj in record["stages"].get(stage) or []:
                texts.append(unit_text(obj))
    return texts


def score_pool(
    runs_dir: pathlib.Path,
    models: Sequence[str],
    pool: str,
    runs: Sequence[int],
    store: EmbedStore,
    corpus: Corpus,
    *,
    prose_runs: Sequence[int] | None = None,
) -> dict[str, Any]:
    """Every registered number for (models x pool x runs)."""
    meetings = sources_for(pool, prose=False)
    records: dict[tuple[str, str, int], dict[str, Any]] = {}
    for model in models:
        for meeting in meetings:
            for run in runs:
                rec = load_record(runs_dir, model, meeting, run)
                if rec is None:
                    raise SystemExit(f"missing record {model} {meeting} r{run}")
                records[(model, meeting, run)] = rec
    texts = collect_texts(records.values())
    golds: dict[str, data.MeetingGold] = {}
    foreigns: dict[str, data.MeetingGold] = {}
    bodies: dict[str, str] = {}
    for meeting in meetings:
        transcript, _w, gold = corpus.meeting(meeting)
        golds[meeting] = gold
        bodies[meeting] = transcript.body
        foreigns[meeting] = corpus.gold_only(data.foreign_meeting(meeting))
        texts += [g.text for g in gold.sentences] + [
            g.text for g in foreigns[meeting].sentences
        ]
    vec = store.vectors(texts)

    out: dict[str, Any] = {"pool": pool, "runs": list(runs), "models": {}}
    per_window: dict[str, dict[str, dict[int, list[float]]]] = {}
    per_meeting: dict[str, dict[str, list[float]]] = {}
    for model in models:
        wl: dict[str, dict[int, list[float]]] = {}
        ml: dict[str, list[float]] = {}
        failures = sm.Ratio(0, 0)
        fail_causes: dict[str, int] = {}
        objects: list[int] = []
        verb_n = verb_d = 0
        en = es = neither = 0
        wall: list[float] = []
        window_s: list[float] = []
        stage_rows: dict[str, list[dict[str, Any]]] = {s: [] for s in STAGES}
        window_stage: dict[str, dict[str, dict[int, list[float]]]] = {
            s: {} for s in STAGES
        }
        reports: list[Mapping[str, Any]] = []
        for meeting in meetings:
            fvecs = [vec[g.text] for g in foreigns[meeting].sentences]
            for run in runs:
                rec = records[(model, meeting, run)]
                reports.append(rec.get("report") or {})
                for w in rec["windows"]:
                    failures = sm.Ratio(
                        failures.num + (w["outcome"] in FAILED_WINDOW_STATES),
                        failures.den + 1,
                    )
                    if w["outcome"] in FAILED_WINDOW_STATES:
                        fail_causes[w["outcome"]] = fail_causes.get(w["outcome"], 0) + 1
                for c in rec["calls"]:
                    if c["kind"] == sp.KIND_WINDOW and c["attempt"] == 0:
                        window_s.append(float(c["wall_s"]))
                wall.append(
                    float(rec["wall_s"])
                    if not all(c["cache_hit"] for c in rec["calls"])
                    else sum(float(c["wall_s"]) for c in rec["calls"])
                )
                final = rec["stages"]["final"] or []
                objects.append(len(final))
                for obj in final:
                    verb_d += 1
                    verb_n += sm.has_verbatim_run(
                        str(obj["description"]), bodies[meeting]
                    )
                    lang = concept_mod._title_language(str(obj["title"]))
                    en += lang == "en"
                    es += lang == "es"
                    neither += lang is None
                if golds[meeting].has_summlink:
                    for index, value in window_lifts(
                        rec, golds[meeting], fvecs, vec
                    ).items():
                        wl.setdefault(meeting, {}).setdefault(index, []).append(value)
                    for stage in STAGES:
                        for index, value in window_lifts(
                            rec, golds[meeting], fvecs, vec, stage
                        ).items():
                            window_stage[stage].setdefault(meeting, {}).setdefault(
                                index, []
                            ).append(value)
                ms = meeting_scores(rec, golds[meeting], fvecs, vec)
                if ms is not None and ms["lift"] is not None:
                    ml.setdefault(meeting, []).append(ms["lift"])
                for stage in STAGES:
                    row = meeting_scores(rec, golds[meeting], fvecs, vec, stage)
                    if row is not None:
                        row["meeting"] = meeting
                        row["run"] = run
                        stage_rows[stage].append(row)
        per_window[model] = wl
        per_meeting[model] = ml
        out["models"][model] = {
            "failures": asdict(failures),
            "failure_causes": fail_causes,
            "objects_per_meeting": sm.mean(objects) if objects else 0.0,
            "objects_total": sum(objects),
            "meeting_runs": len(objects),
            "verbatim": asdict(sm.Ratio(verb_n, verb_d)),
            "title_language": {"en": en, "es": es, "neither": neither},
            "median_window_s": statistics.median(window_s) if window_s else None,
            "window_calls_timed": len(window_s),
            "mean_s_per_meeting_run": sm.mean(wall) if wall else None,
            "total_s": sum(wall),
            "window_lift_mean": sm.mean(
                [sm.mean(v) for m in wl.values() for v in m.values()]
            )
            if wl
            else None,
            "meeting_lift_mean": sm.mean([sm.mean(v) for v in ml.values()])
            if ml
            else None,
            "stage_table": stage_summary(stage_rows),
            "stage_window_lift": {
                s: (
                    sm.mean(
                        [
                            sm.mean(v)
                            for m in window_stage[s].values()
                            for v in m.values()
                        ]
                    )
                    if window_stage[s]
                    else None
                )
                for s in STAGES
            },
            "counters": counter_summary(reports),
            "curves": curve_summary(stage_rows["final"]),
            "sections": {
                s: _mean_opt([r["sections"][s] for r in stage_rows["final"]])
                for s in data.GOLD_SECTIONS
            },
        }
    prose = score_prose(
        runs_dir, models, prose_runs if prose_runs is not None else runs, store
    )
    for model in models:
        out["models"][model]["prose"] = prose[model]
    baseline = BASELINE if BASELINE in models else None
    if baseline is not None:
        for model in models:
            if model == baseline:
                continue
            out["models"][model]["dlift"] = asdict(
                paired_window_delta(per_window[model], per_window[baseline])
            )
            out["models"][model]["dlift_meeting"] = asdict(
                paired_meeting_delta(per_meeting[model], per_meeting[baseline])
            )
        out["models"][baseline]["lift_ci"] = asdict(
            sm.two_stage_bootstrap(
                {
                    m: [sm.mean(v) for v in ws.values()]
                    for m, ws in per_window[baseline].items()
                },
                sm.mean,
            )
        )
    out["per_window"] = {
        m: {k: {str(i): v for i, v in ws.items()} for k, ws in w.items()}
        for m, w in per_window.items()
    }
    return out


def _mean_opt(values: Iterable[float | None]) -> float | None:
    vals = [v for v in values if v is not None]
    return sm.mean(vals) if vals else None


def stage_summary(rows: Mapping[str, list[dict[str, Any]]]) -> dict[str, Any]:
    table: dict[str, Any] = {}
    for stage, items in rows.items():
        if not items:
            table[stage] = None
            continue
        table[stage] = {
            "meeting_runs": len(items),
            "units": sum(r["n_units"] for r in items),
            "own": _mean_opt(r["own"] for r in items),
            "foreign": _mean_opt(r["foreign"] for r in items),
            "lift": _mean_opt(r["lift"] for r in items),
            "own_sum": sum(r["own_sum"] for r in items),
            "own_covered_tau": sum(r["own_covered_tau"] for r in items),
            "foreign_covered_tau": sum(r["foreign_covered_tau"] for r in items),
            "gold_n": sum(r["gold_n"] for r in items),
            "foreign_n": sum(r["foreign_n"] for r in items),
        }
    return table


def curve_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, list[float | None]]:
    return {
        "own": [
            _mean_opt(r["own_curve"][i] for r in rows) for i in range(len(sm.TAU_GRID))
        ],
        "foreign": [
            _mean_opt(r["foreign_curve"][i] for r in rows)
            for i in range(len(sm.TAU_GRID))
        ],
    }


def counter_summary(reports: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    def count(key: str) -> int:
        return sum(len(r.get(key) or []) for r in reports)

    return {
        "skipped_chunks": count("skipped_chunks"),
        "wrong_language_dropped": count("wrong_language_dropped_titles"),
        "recombined_dropped": count("recombined_dropped_titles"),
        "pre_judge_dropped": sum(int(r.get("pre_judge_dropped") or 0) for r in reports),
        "judged_out": count("judged_out_titles"),
        "backstop_discarded": count("discarded_titles"),
        "reask_added": count("reask_added_titles"),
        "participant_added": count("participant_capture_added_titles"),
        "judge_status": {
            s: sum(1 for r in reports if r.get("judge_status") == s)
            for s in ("ok", "failed", "empty", "skipped")
        },
        "optional_call_failures": count("optional_call_failures"),
    }


def paired_window_delta(
    cand: Mapping[str, Mapping[int, Sequence[float]]],
    base: Mapping[str, Mapping[int, Sequence[float]]],
) -> sm.Interval:
    clusters: dict[str, list[float]] = {}
    for meeting, windows in base.items():
        for index, values in windows.items():
            if meeting in cand and index in cand[meeting]:
                clusters.setdefault(meeting, []).append(
                    sm.mean(cand[meeting][index]) - sm.mean(values)
                )
    return sm.two_stage_bootstrap(clusters, sm.mean)


def paired_meeting_delta(
    cand: Mapping[str, Sequence[float]], base: Mapping[str, Sequence[float]]
) -> sm.Interval:
    clusters = {
        m: [sm.mean(cand[m]) - sm.mean(v)] for m, v in base.items() if m in cand
    }
    return sm.two_stage_bootstrap(clusters, sm.mean)


def score_prose(
    runs_dir: pathlib.Path,
    models: Sequence[str],
    runs: Sequence[int],
    store: EmbedStore,
) -> dict[str, Any]:
    """Secondary: exact and embedding-matched subject recall; V3 language."""
    calib = (
        json.loads((runs_dir / "calibration.json").read_text(encoding="utf-8"))
        if (runs_dir / "calibration.json").is_file()
        else None
    )
    threshold = calib.get("threshold") if calib else None
    out: dict[str, Any] = {}
    for model in models:
        wrong = total = dropped = 0
        exact = embed = subjects = 0
        failures = 0
        per_fixture: dict[str, Any] = {}
        for name in PROSE_FIXTURES:
            text, _title, truth = load_prose(name)
            dominant = concept_mod._dominant_language(text)
            fx_exact = fx_embed = fx_subjects = 0
            for run in runs:
                rec = load_record(runs_dir, model, f"prose-{name}", run)
                if rec is None:
                    continue
                final = rec["stages"]["final"] or []
                failures += rec["failure"] is not None
                titles = [str(o["title"]) for o in final]
                if name in SPANISH_FIXTURES:
                    for t in titles:
                        lang = concept_mod._title_language(t)
                        total += 1
                        wrong += lang is not None and lang != dominant
                    dropped += len(
                        (rec.get("report") or {}).get("wrong_language_dropped_titles")
                        or []
                    )
                found = sys.modules["run_cap_eval"].subjects_found(titles, truth)
                fx_exact += len(found)
                fx_subjects += truth.subject_count
                if threshold is not None:
                    fx_embed += len(
                        embedding_subjects(titles, truth, store, float(threshold))
                    )
            per_fixture[name] = {
                "exact": fx_exact,
                "embedding": fx_embed if threshold is not None else None,
                "of": fx_subjects,
            }
            exact += fx_exact
            embed += fx_embed
            subjects += fx_subjects
        out[model] = {
            "wrong_language": asdict(sm.Ratio(wrong, total)),
            "wrong_language_dropped": dropped,
            "subject_recall_exact": asdict(sm.Ratio(exact, subjects)),
            "subject_recall_embedding": asdict(sm.Ratio(embed, subjects))
            if threshold is not None
            else None,
            "whole_source_failures": failures,
            "per_fixture": per_fixture,
        }
    return out


def embedding_subjects(
    titles: Sequence[str], truth: Any, store: EmbedStore, threshold: float
) -> set[str]:
    """Subjects matched by an embedding: max cosine to the subject or an alias
    >= threshold, and nearer to that subject than to any listed facet."""
    subject_texts = {s.title: [s.title, *s.aliases] for s in truth.subjects}
    facets = list(truth.facets)
    texts = list(titles) + [t for ts in subject_texts.values() for t in ts] + facets
    vec = store.vectors(texts)
    found: set[str] = set()
    for title in titles:
        tv = vec[title]
        best_facet = max((sm.dot(tv, vec[f]) for f in facets), default=-1.0)
        for subject, names in subject_texts.items():
            sim = max(sm.dot(tv, vec[n]) for n in names)
            if sim >= threshold and sim > best_facet:
                found.add(subject)
    return found


def calibration(store: EmbedStore) -> dict[str, Any]:
    """Validity check 3: the matcher threshold from the adjudicated lists."""
    positives: list[float] = []
    negatives: list[float] = []
    for name in PROSE_FIXTURES:
        _text, _title, truth = load_prose(name)
        pairs_pos = [(s.title, a) for s in truth.subjects for a in s.aliases]
        pairs_neg = [(s.title, f) for s in truth.subjects for f in truth.facets]
        pairs_neg += [(s.title, o) for s in truth.subjects for o in truth.out_of_scope]
        vec = store.vectors([x for p in pairs_pos + pairs_neg for x in p])
        positives += [sm.dot(vec[a], vec[b]) for a, b in pairs_pos]
        negatives += [sm.dot(vec[a], vec[b]) for a, b in pairs_neg]
    threshold, precision, recall = sm.calibrate_threshold(positives, negatives)
    return {
        "positives": len(positives),
        "negatives": len(negatives),
        "threshold": threshold,
        "precision": precision,
        "recall": recall,
    }


def make_store(args: argparse.Namespace) -> EmbedStore:
    ident = model_identity(EMBED_MODEL, args.host)
    client = OllamaClient(EMBED_MODEL, host=args.host, timeout=600)
    return EmbedStore(
        args.runs_dir / "cache" / "embed.sqlite", client.embed, str(ident.get("digest"))
    )


def cmd_score(args: argparse.Namespace) -> int:
    store = make_store(args)
    corpus = Corpus(args.runs_dir)
    if not (args.runs_dir / "calibration.json").is_file():
        (args.runs_dir / "calibration.json").write_text(
            json.dumps(calibration(store), indent=2) + "\n", encoding="utf-8"
        )
    result = score_pool(
        args.runs_dir,
        args.models,
        args.pool,
        args.runs,
        store,
        corpus,
        prose_runs=args.prose_runs,
    )
    result["g1"] = (
        score_g1(args.runs_dir, args.models, args.pool, args.runs) if args.g1 else None
    )
    out = args.runs_dir / "scores" / f"{args.name}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out}")
    return 0


def score_g1(
    runs_dir: pathlib.Path, models: Sequence[str], pool: str, runs: Sequence[int]
) -> dict[str, Any]:
    per: dict[str, dict[str, dict[int, tuple[int, int]]]] = {}
    out: dict[str, Any] = {}
    for model in models:
        windows: dict[str, dict[int, tuple[int, int]]] = {}
        sup = part = uns = unparsed = 0
        cov: list[float] = []
        for meeting in sources_for(pool, prose=False):
            for run in runs:
                j = load_judge(runs_dir, model, meeting, run)
                if j is None:
                    raise SystemExit(f"no judge record {model} {meeting} r{run}")
                for item in j["g1"]:
                    if item["labels"] is None:
                        unparsed += 1
                        continue
                    counts = sj.g1_counts(item["labels"])
                    sup += counts["supported"]
                    part += counts["partial"]
                    uns += counts["unsupported"]
                    s0, t0 = windows.setdefault(meeting, {}).get(
                        int(item["window"]), (0, 0)
                    )
                    windows[meeting][int(item["window"])] = (
                        s0 + counts["supported"],
                        t0 + item["n"],
                    )
                if j.get("coverage") and j["coverage"]["labels"] is not None:
                    cov.append(sj.coverage_score(j["coverage"]["labels"]))
        per[model] = windows
        out[model] = {
            "supported": sup,
            "partial": part,
            "unsupported": uns,
            "unparsed_windows": unparsed,
            "precision": asdict(sm.Ratio(sup, sup + part + uns)),
            "judge_coverage_mean": sm.mean(cov) if cov else None,
        }
    if BASELINE in per:
        for model in models:
            if model == BASELINE:
                continue
            clusters: dict[str, list[tuple[int, int, int, int]]] = {}
            for meeting, wins in per[BASELINE].items():
                for index in sorted(set(wins) | set(per[model].get(meeting, {}))):
                    sb, tb = wins.get(index, (0, 0))
                    sc, tc = per[model].get(meeting, {}).get(index, (0, 0))
                    clusters.setdefault(meeting, []).append((sc, tc, sb, tb))
            out[model]["dprecision"] = asdict(
                sm.two_stage_bootstrap(clusters, sm.pooled_precision_delta)
            )
    return out


# --- decisions ---------------------------------------------------------------


def summary_from(
    scores: Mapping[str, Any], model: str, g1: Mapping[str, Any] | None
) -> sm.ModelSummary:
    m = scores["models"][model]
    dl = m.get("dlift")
    dp = (g1 or {}).get(model, {}).get("dprecision") if g1 else None
    return sm.ModelSummary(
        model=model,
        dlift=sm.Interval(**dl) if dl else None,
        failures=sm.Ratio(**m["failures"]),
        wrong_language=sm.Ratio(**m["prose"]["wrong_language"]),
        objects_per_meeting=float(m["objects_per_meeting"]),
        verbatim=sm.Ratio(**m["verbatim"]),
        dprecision=sm.Interval(**dp) if dp else None,
    )


def cmd_decide(args: argparse.Namespace) -> int:
    scores = json.loads(
        (args.runs_dir / "scores" / f"{args.name}.json").read_text(encoding="utf-8")
    )
    models = list(scores["models"])
    base = summary_from(scores, BASELINE, scores.get("g1"))
    cands = [summary_from(scores, m, scores.get("g1")) for m in models if m != BASELINE]
    out: dict[str, Any] = {"round": args.round}
    if args.round == "validity":
        ok, text = sm.separation_check(
            sm.Interval(**scores["models"][BASELINE]["lift_ci"])
        )
        out["separation"] = {"pass": ok, "text": text}
        own, other = deletion_deltas(args, scores)
        ok2, text2 = sm.deletion_check(own, other)
        out["deletion"] = {"pass": ok2, "text": text2}
        out["calibration"] = json.loads(
            (args.runs_dir / "calibration.json").read_text(encoding="utf-8")
        )
        out["primary"] = (
            "embedding coverage lift" if ok and ok2 else "FALLBACK: judge coverage"
        )
    elif args.round == "1":
        survivors, trace = sm.round1_cut(cands, base)
        out["survivors"] = survivors
        out["trace"] = [asdict(v) for v in trace]
    elif args.round == "2":
        winner, trace = sm.selection(cands, base)
        out["winner"] = winner
        out["top_two"] = sm.top_two(cands, base)
        out["trace"] = [asdict(v) for v in trace]
    elif args.round == "confirm":
        (cand,) = cands
        if cand.dlift is None or cand.dprecision is None:
            raise SystemExit("confirmation needs dlift and G1")
        ok, reasons = sm.confirmation(
            cand.dlift, cand.failures, base.failures, cand.dprecision
        )
        out["confirmed"] = ok
        out["reasons"] = reasons
    path = args.runs_dir / "decisions" / f"{args.name}-{args.round}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(out, indent=2))
    return 0


def deletion_deltas(
    args: argparse.Namespace, scores: Mapping[str, Any]
) -> tuple[list[float], list[float]]:
    """Validity check 2 on the baseline's cached output, meeting level."""
    store = make_store(args)
    corpus = Corpus(args.runs_dir)
    own_d: list[float] = []
    other_d: list[float] = []
    for meeting in sources_for(scores["pool"], prose=False):
        _t, _w, gold = corpus.meeting(meeting)
        if not gold.has_summlink:
            continue
        for run in scores["runs"]:
            rec = load_record(args.runs_dir, BASELINE, meeting, run)
            if rec is None:
                continue
            final = rec["stages"]["final"] or []
            vec = store.vectors(
                [unit_text(o) for o in final] + [g.text for g in gold.sentences]
            )
            gvec = {g.sentence_id: vec[g.text] for g in gold.sentences}
            before = dict(
                zip(
                    gvec,
                    sm.max_sims(
                        list(gvec.values()), [vec[unit_text(o)] for o in final]
                    ),
                    strict=True,
                )
            )
            for index in sorted({v for v in gold.window_of.values() if v is not None}):
                kept = [vec[unit_text(o)] for o in final if o.get("window") != index]
                if len(kept) == len(final):
                    continue  # nothing planted to delete for this window
                after = dict(
                    zip(gvec, sm.max_sims(list(gvec.values()), kept), strict=True)
                )
                for sid in gvec:
                    delta = after[sid] - before[sid]
                    if gold.window_of.get(sid) == index:
                        own_d.append(delta)
                    elif gold.window_of.get(sid) is not None:
                        other_d.append(delta)
    return own_d, other_d


# --- self-test ---------------------------------------------------------------


class _Fail(Exception):
    pass


def check(cond: bool, message: str) -> None:
    if not cond:
        raise _Fail(message)


def self_test() -> int:
    """Model-free: no Ollama, no AMI archive, no network."""
    tests: list[Callable[[], None]] = [
        _t_allowlist,
        _t_foreign,
        _t_windows_and_assignment,
        _t_abstractive_parse,
        _t_soft_cov_and_lift,
        _t_bootstrap,
        _t_vetoes,
        _t_round1,
        _t_selection,
        _t_confirmation,
        _t_validity,
        _t_calibration,
        _t_verbatim,
        _t_judge_parse,
        _t_backend_timeout_and_cache,
        _t_stage_capture,
        _t_judge_never_candidate,
    ]
    failed = 0
    for test in tests:
        try:
            test()
            print(f"ok   {test.__name__}")
        except _Fail as exc:
            failed += 1
            print(f"FAIL {test.__name__}: {exc}")
    print(f"self-test: {len(tests) - failed} of {len(tests)} passed")
    return 1 if failed else 0


def _t_allowlist() -> None:
    for meeting in (
        "ES2015a",
        "IS1004d",
        "TS3009c",
        "IS1002b",
        "IS1005c",
        "TS3012c",
        "IB4011",
    ):
        check(data.require_allowed(meeting) == meeting, f"{meeting} should be allowed")
    refused = [f"{s}{x}" for s in data.THESIS_EVALUATION_SERIES for x in "abcd"]
    refused += ["ES2003a", "IS1002a", "IB4001", "IB4004", "EN2001a", "bogus", "TS3005"]
    for meeting in refused:
        try:
            data.require_allowed(meeting)
        except data.DisallowedMeeting:
            continue
        raise _Fail(f"{meeting} was not refused")
    check(
        len(data.ALLOWED_MEETINGS) == 26, f"allowlist size {len(data.ALLOWED_MEETINGS)}"
    )
    every = (
        set(data.POOL_DA)
        | set(data.POOL_F_WINDOWED)
        | set(data.POOL_F_MEETING_ONLY)
        | {m for m, _ in data.STAGE0_WINDOWS}
    )
    check(every <= data.ALLOWED_MEETINGS, "a registered pool names a refused meeting")
    try:
        sources_for("DA", prose=False)
    except data.DisallowedMeeting as exc:
        raise _Fail(f"pool DA refused: {exc}") from exc


def _t_foreign() -> None:
    expected = {
        "ES2015a": "IS1004a",
        "IS1004b": "TS3009b",
        "TS3009c": "ES2015c",
        "IS1002d": "TS3012d",
        "IS1005a": "TS3012a",
        "TS3012a": "IS1005a",
        "TS3012b": "IS1002b",
        "IB4011": "IB4003",
    }
    for m, f in expected.items():
        check(
            data.foreign_meeting(m) == f,
            f"foreign({m}) = {data.foreign_meeting(m)}, want {f}",
        )
    for m in data.ALLOWED_MEETINGS:
        f = data.foreign_meeting(m)
        check(f != m and f in data.ALLOWED_MEETINGS, f"foreign({m}) = {f}")


def _t_windows_and_assignment() -> None:
    body = "\n".join(f"A: line {i} " + "x" * 30 for i in range(12))
    wins = data.build_windows(
        "ES2015a", body, lambda t: concept_mod._chunk_lines(t, 120)
    )
    check(len(wins) > 2, "expected several windows")
    check(all(body[w.start : w.end] == w.text for w in wins), "window offsets wrong")
    check(data.window_of_offset(wins, wins[1].start) == 1, "offset lookup")
    turns = tuple(
        data.Turn(body.find(f"line {i} "), body.find(f"line {i} ") + 5, (f"act{i}",))
        for i in range(12)
    )
    tr = data.Transcript("ES2015a", body, turns)
    aw = data.act_windows(tr, wins)
    sents = [
        data.GoldSentence("s1", "abstract", "x"),
        data.GoldSentence("s2", "decisions", "y"),
        data.GoldSentence("s3", "problems", "z"),
    ]
    w_last = aw["act11"]
    links = {"s1": ["act0", "act11", "act11"], "s2": ["act0", "act11"]}
    got = data.assign_windows(sents, links, aw)
    check(got["s1"] == w_last, f"majority window: {got}")
    check(got["s2"] == aw["act0"], f"tie goes to the earliest: {got}")
    check(got["s3"] is None, "unlinked sentence must be unassigned")
    try:
        data.build_windows("ES2015a", body, lambda t: [t[:10]])
    except ValueError:
        pass
    else:
        raise _Fail("a lossy chunker must be refused")


def _t_abstractive_parse() -> None:
    xml = (
        b'<?xml version="1.0"?><nite:root xmlns:nite="http://nite.sourceforge.net/">'
        b'<abstract nite:id="a"><sentence nite:id="m.s.1">One  two.</sentence></abstract>'
        b'<actions nite:id="b"><sentence nite:id="m.s.2">Act.</sentence></actions>'
        b'<decisions nite:id="c"><sentence nite:id="m.s.3">Dec.</sentence></decisions>'
        b'<problems nite:id="d"><sentence nite:id="m.s.4">Prob.</sentence></problems></nite:root>'
    )
    got = data.parse_abstractive(xml)
    check(
        [g.section for g in got] == ["abstract", "decisions", "problems"],
        f"actions must be excluded: {got}",
    )
    check(got[0].text == "One two.", "whitespace not collapsed")
    link = (
        b'<nite:root xmlns:nite="http://nite.sourceforge.net/"><summlink nite:id="x">'
        b'<nite:pointer role="extractive" href="m.A.dialog-act.xml#id(m.A.da.1)"/>'
        b'<nite:pointer role="abstractive" href="m.abssumm.xml#id(m.s.1)"/></summlink></nite:root>'
    )
    check(data.parse_summlink(link) == {"m.s.1": ["m.A.da.1"]}, "summlink parse")
    md = "---\nmeeting_id: ES2015a\n---\n\nPM: Hi there.\n\nME: Yes."
    tr = data.parse_transcript(
        "ES2015a",
        md,
        json.dumps(
            {
                "char_start": 4,
                "char_end": 13,
                "dialogue_act_ids": ["d1"],
                "text": "Hi there.",
            }
        ),
    )
    check(tr.body.startswith("PM: Hi"), "frontmatter not stripped")
    try:
        data.parse_transcript(
            "ES2015a",
            md,
            json.dumps(
                {"char_start": 0, "char_end": 3, "dialogue_act_ids": [], "text": "Hi!"}
            ),
        )
    except ValueError:
        pass
    else:
        raise _Fail("a misaligned turn index must be refused")
    try:
        data.parse_transcript("TS3005a", md, "")
    except data.DisallowedMeeting:
        pass
    else:
        raise _Fail("parse_transcript must refuse an evaluation meeting")


def _t_soft_cov_and_lift() -> None:
    a, b, c = sm.unit([1, 0, 0]), sm.unit([0, 1, 0]), sm.unit([1, 1, 0])
    check(sm.soft_cov([a, b], []) == 0.0, "empty units must cover nothing")
    check(sm.soft_cov([], [a]) is None, "no gold is undefined")
    check(abs((sm.soft_cov([a, b], [a]) or 0) - 0.5) < 1e-9, "soft_cov")
    check(abs((sm.lift([a], [b], [a]) or 0) - 1.0) < 1e-9, "own minus foreign")
    check(abs(sm.lift([a], [b], [c]) or 0) < 1e-9, "a topical unit lifts nothing")
    check(sm.cov_tau([a, b], [a], 0.9) == 0.5, "cov_tau")


def _t_bootstrap() -> None:
    iv = sm.two_stage_bootstrap(
        {"m1": [0.1, 0.2, 0.15], "m2": [0.12, 0.18]}, sm.mean, resamples=500
    )
    check(iv.low > 0 and iv.low <= iv.point <= iv.high, f"positive effect: {iv}")
    iv0 = sm.two_stage_bootstrap(
        {"m1": [0.1, -0.1, 0.05], "m2": [-0.12, 0.08]}, sm.mean, resamples=500
    )
    check(iv0.low < 0 < iv0.high, f"null effect must straddle 0: {iv0}")
    again = sm.two_stage_bootstrap(
        {"m1": [0.1, -0.1, 0.05], "m2": [-0.12, 0.08]}, sm.mean, resamples=500
    )
    check(again == iv0, "seeded bootstrap must be reproducible")
    one = sm.two_stage_bootstrap({"m": [0.3]}, sm.mean, resamples=50)
    check(one.low == one.high == 0.3, "degenerate sample")
    check(
        abs(sm.pooled_precision_delta([(9, 10, 8, 10), (0, 0, 1, 2)]) - (0.9 - 0.75))
        < 1e-9,
        "pooled precision",
    )


def _summ(
    model: str,
    point: float = 0.05,
    lo: float = 0.01,
    hi: float = 0.09,
    fail: tuple[int, int] = (0, 40),
    lang: tuple[int, int] = (0, 20),
    vol: float = 10.0,
    verb: tuple[int, int] = (0, 50),
    dp: tuple[float, float] | None = (0.0, -0.02),
) -> sm.ModelSummary:
    return sm.ModelSummary(
        model,
        sm.Interval(point, lo, hi),
        sm.Ratio(*fail),
        sm.Ratio(*lang),
        vol,
        sm.Ratio(*verb),
        sm.Interval(dp[0], dp[1], 0.1) if dp else None,
    )


def _t_vetoes() -> None:
    b = _summ("base", fail=(2, 100))
    check(
        sm.veto_reasons(_summ("c", fail=(4, 100)), b) == [], "4% <= 2% + 2pp must pass"
    )
    check(
        any("V1" in r for r in sm.veto_reasons(_summ("c", fail=(5, 100)), b)),
        "V1 relative",
    )
    check(
        any(
            "absolute" in r
            for r in sm.veto_reasons(_summ("c", fail=(3, 50)), _summ("b", fail=(3, 50)))
        ),
        "V1 absolute 6%",
    )
    check(any("V3" in r for r in sm.veto_reasons(_summ("c", lang=(2, 20)), b)), "V3")
    check(any("V4" in r for r in sm.veto_reasons(_summ("c", vol=4.9), b)), "V4 low")
    check(any("V4" in r for r in sm.veto_reasons(_summ("c", vol=15.1), b)), "V4 high")
    check(sm.veto_reasons(_summ("c", vol=15.0), b) == [], "V4 boundary inclusive")
    check(any("V5" in r for r in sm.veto_reasons(_summ("c", verb=(6, 50)), b)), "V5")


def _t_round1() -> None:
    b = _summ("base")
    cands = [
        _summ("a", 0.05),
        _summ("b", 0.02),
        _summ("c", -0.05, -0.09, -0.01),
        _summ("d", 0.08, fail=(9, 40)),
    ]
    survivors, trace = sm.round1_cut(cands, b)
    check(survivors == ["a", "b"], f"survivors {survivors}")
    check(
        any(v.model == "c" and not v.passed for v in trace),
        "an upper bound < 0 must drop",
    )
    check(
        any(v.model == "d" and not v.passed for v in trace),
        "a veto must drop the best point",
    )
    s2, _ = sm.round1_cut([_summ("a", 0.01), _summ("b", 0.03), _summ("e", 0.02)], b)
    check(s2 == ["b", "e"], f"top ceil(3/2)=2: {s2}")
    s3, _ = sm.round1_cut([_summ("a", 0.01, hi=-0.001)], b)
    check(s3 == [], "can come back empty")


def _t_selection() -> None:
    b = _summ("base")
    w, _ = sm.selection([_summ("a", 0.05, 0.01), _summ("b", 0.08, -0.01)], b)
    check(w == "a", f"lower bound must be > 0: {w}")
    w2, _ = sm.selection([_summ("a", 0.05, 0.01, dp=(0.0, -0.06))], b)
    check(w2 is None, "G1 below -0.05 must block; no winner keeps the baseline")
    w3, _ = sm.selection([_summ("a", 0.05, 0.01, dp=None)], b)
    check(w3 is None, "unmeasured G1 must block")
    top = sm.top_two(
        [
            _summ("a", 0.05, 0.01),
            _summ("b", -0.01, -0.05),
            _summ("c", 0.09, fail=(9, 40)),
        ],
        b,
    )
    check(top == ["a", "base"], f"top two {top}")


def _t_confirmation() -> None:
    ok, _ = sm.confirmation(
        sm.Interval(0.01, -0.02, 0.03),
        sm.Ratio(0, 60),
        sm.Ratio(0, 60),
        sm.Interval(0, -0.01, 0.01),
    )
    check(ok, "point > 0 with V1/G1 holding confirms")
    bad, _ = sm.confirmation(
        sm.Interval(-0.001, -0.02, 0.03),
        sm.Ratio(0, 60),
        sm.Ratio(0, 60),
        sm.Interval(0, -0.01, 0.01),
    )
    check(not bad, "a sign flip is not confirmed")


def _t_validity() -> None:
    check(sm.separation_check(sm.Interval(0.05, 0.02, 0.08))[0], "separation passes")
    check(
        not sm.separation_check(sm.Interval(0.02, 0.01, 0.03))[0],
        "point below 0.03 fails",
    )
    check(
        not sm.separation_check(sm.Interval(0.05, -0.01, 0.1))[0], "CI touching 0 fails"
    )
    check(sm.deletion_check([-0.1, -0.05], [-0.001, 0.0])[0], "deletion passes")
    check(not sm.deletion_check([-0.02], [0.0])[0], "a small loss fails")
    check(not sm.deletion_check([-0.1], [-0.2])[0], "own must lose more than the rest")


def _t_calibration() -> None:
    t, p, r = sm.calibrate_threshold([0.9, 0.85, 0.7], [0.6, 0.72, 0.5])
    check(t == 0.85 and p == 1.0 and abs(r - 2 / 3) < 1e-9, f"calibration {t} {p} {r}")
    t2, _, _ = sm.calibrate_threshold([0.5], [0.9])
    check(t2 is None, "no achievable precision must return None")


def _t_verbatim() -> None:
    src = "one two three four five six seven eight nine ten eleven twelve thirteen"
    check(
        sm.has_verbatim_run(
            "x one two three four five six seven eight nine ten eleven twelve", src
        ),
        "12-run",
    )
    check(
        not sm.has_verbatim_run(
            "one two three four five six seven eight nine ten eleven", src
        ),
        "11 is short",
    )
    check(
        sm.content_word_share("remote control design", "the remote control") == 2 / 3,
        "content share",
    )


def _t_judge_parse() -> None:
    check(
        sj.parse_labels(
            '[{"id":1,"label":"supported"},{"id":2,"label":"Partial"}]', 2, sj.G1_LABELS
        )
        == ["supported", "partial"],
        "parse",
    )
    check(
        sj.parse_labels('[{"id":1,"label":"supported"}]', 2, sj.G1_LABELS) is None,
        "missing item",
    )
    check(
        sj.parse_labels('[{"id":1,"label":"maybe"}]', 1, sj.G1_LABELS) is None,
        "bad label",
    )
    check(
        sj.parse_labels(
            '[{"id":1,"label":"supported"},{"id":1,"label":"partial"}]', 1, sj.G1_LABELS
        )
        is None,
        "duplicate id",
    )
    check(sj.parse_labels("no json", 1, sj.G1_LABELS) is None, "no json")
    check(
        sj.coverage_score(["covered", "partial", "not", "not"]) == 0.375,
        "coverage score",
    )


def _t_backend_timeout_and_cache() -> None:
    import tempfile

    sent: list[tuple[int, float]] = []

    def fake(
        model: str, messages: Sequence[Any], seed: int, timeout: float
    ) -> tuple[str | None, dict[str, Any]]:
        sent.append((seed, timeout))
        if "slow" in str(messages[-1]["content"]):
            return None, {"outcome": "timeout", "wall_s": timeout}
        if "cap" in str(messages[-1]["content"]):
            return None, {"outcome": "capped", "wall_s": 1.0, "error": "capped"}
        return '[{"type": "Concept", "title": "Remote", "description": "d"}]', {
            "outcome": "ok",
            "wall_s": 2.0,
            "content": '[{"type": "Concept", "title": "Remote", "description": "d"}]',
        }

    with tempfile.TemporaryDirectory() as tmp:
        cache = sp.ChatCache(pathlib.Path(tmp))
        be = sp.ScreenBackend(
            model="m",
            digest="d",
            run=2,
            window_timeout_s=60,
            whole_timeout_s=180,
            cache=cache,
            send=fake,
            windows=["first window slow", "second window ok", "third window cap"],
        )
        msgs = concept_mod._build_messages("first window slow", "T")
        for _ in range(2):
            try:
                be.chat(msgs)
            except sp.HarnessTimeout:
                pass
            else:
                raise _Fail("timeout must raise HarnessTimeout")
        check(len(sent) == 1, f"a timed-out call must not be re-sent: {sent}")
        check(
            be.records[0].kind == sp.KIND_WINDOW and be.records[0].window == 0,
            "window classification",
        )
        check(
            be.records[1].outcome == "timeout_not_resent", "suppressed retry recorded"
        )
        check(sent[0] == (2, 60), f"seed r, window timeout: {sent}")
        check(
            not issubclass(sp.HarnessTimeout, OllamaUnavailable),
            "timeout must not be BackendUnavailable",
        )
        cmsgs = concept_mod._build_messages("third window cap", "T")
        for _ in range(2):
            with contextlib.suppress(OllamaGenerationCapped):
                be.chat(cmsgs)
        check([s for s, _ in sent[1:]] == [2, 1002], f"retry is a fresh seed: {sent}")
        okm = concept_mod._build_messages("second window ok", "T")
        be.chat(okm)
        n = len(sent)
        be2 = sp.ScreenBackend(
            model="m",
            digest="d",
            run=2,
            window_timeout_s=60,
            whole_timeout_s=180,
            cache=cache,
            send=fake,
            windows=["first window slow", "second window ok", "third window cap"],
        )
        be2.chat(okm)
        check(
            len(sent) == n and be2.records[0].cache_hit,
            "a repeated call must replay from cache",
        )
        be3 = sp.ScreenBackend(
            model="m",
            digest="d",
            run=2,
            window_timeout_s=120,
            whole_timeout_s=360,
            cache=cache,
            send=fake,
            windows=["first window slow"],
        )
        with contextlib.suppress(sp.HarnessTimeout):
            be3.chat(msgs)
        check(len(sent) == n + 1, "a timeout under a shorter bound must not replay")


def _t_stage_capture() -> None:
    import tempfile

    lines = [
        f"{'AB'[i % 2]}: speaker line number {i} about the remote control design "
        + "y" * 60
        for i in range(160)
    ]
    text = "\n".join(lines)
    windows = concept_mod._chunk_lines(text)
    check(len(windows) >= 2, "fixture must chunk")

    def fake(
        model: str, messages: Sequence[Any], seed: int, timeout: float
    ) -> tuple[str | None, dict[str, Any]]:
        user = str(messages[-1]["content"])
        if str(messages[0]["content"]) == concept_mod._SYSTEM_PROMPT:
            if windows[1] in user:
                return None, {"outcome": "timeout", "wall_s": timeout}
            idx = next(i for i, w in enumerate(windows) if w in user)
            reply = json.dumps(
                [
                    {
                        "type": "Concept",
                        "title": f"Topic {idx}",
                        "description": f"About {idx}.",
                        "body": "b",
                    },
                    {
                        "type": "Concept",
                        "title": "Shared",
                        "description": "dup",
                        "body": "b",
                    },
                ]
            )
            return reply, {"outcome": "ok", "wall_s": 1.0, "content": reply}
        return "[]", {"outcome": "ok", "wall_s": 1.0, "content": "[]"}

    with tempfile.TemporaryDirectory() as tmp:
        cache = sp.ChatCache(pathlib.Path(tmp))
        be = sp.ScreenBackend(
            model="m",
            digest=None,
            run=1,
            window_timeout_s=60,
            whole_timeout_s=180,
            cache=cache,
            send=fake,
            windows=windows,
        )
        before = concept_mod._dedup_merged
        rec = sp.run_source(text=text, title="ES2015a", backend=be, cache=cache)
        check(
            concept_mod._dedup_merged is before, "production helpers must be restored"
        )
    check(rec["chunked"], "chunked path expected")
    outcomes = [w["outcome"] for w in rec["windows"]]
    check(
        outcomes[1] == "timeout" and outcomes.count("ok") == len(windows) - 1,
        f"outcomes {outcomes}",
    )
    raw = rec["stages"]["raw"]
    check(len(raw) == 2 * (len(windows) - 1), f"raw stage {len(raw)}")
    check(
        sum(1 for o in rec["stages"]["dedup"] if o["title"] == "Shared") == 1,
        "dedup stage",
    )
    check(
        all(o["window"] is not None for o in rec["stages"]["dedup"]),
        "provenance on every window object",
    )
    check(
        rec["stages"]["final"] is not None and rec["report"]["skipped_chunks"] == [2],
        f"report {rec['report']}",
    )


def _t_judge_never_candidate() -> None:
    try:
        refuse_judge_as_candidate([BASELINE, sj.JUDGE_MODEL])
    except SystemExit:
        pass
    else:
        raise _Fail("the judge was accepted as a candidate")
    check(
        sj.JUDGE_MODEL not in {BASELINE, *CANDIDATES},
        "registered candidates include the judge",
    )


# --- CLI ---------------------------------------------------------------------


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--self-test", action="store_true", help="model-free checks, then exit"
    )
    parser.add_argument("--runs-dir", type=pathlib.Path, default=DEFAULT_RUNS_DIR)
    parser.add_argument("--host", default=os.environ.get("OLLAMA_HOST"))
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("prepare")
    s0 = sub.add_parser("stage0")
    s0.add_argument("--models", nargs="*")
    r = sub.add_parser("run")
    r.add_argument("--models", nargs="+", required=True)
    r.add_argument("--pool", required=True, choices=("DA", "F", "F-IB"))
    r.add_argument("--runs", nargs="+", type=int, required=True)
    r.add_argument("--prose", action="store_true")
    j = sub.add_parser("judge")
    j.add_argument("--models", nargs="+", required=True)
    j.add_argument("--pool", required=True, choices=("DA", "F", "F-IB"))
    j.add_argument("--runs", nargs="+", type=int, required=True)
    j.add_argument("--coverage", action="store_true")
    sc = sub.add_parser("score")
    sc.add_argument("--name", required=True)
    sc.add_argument("--models", nargs="+", required=True)
    sc.add_argument("--pool", required=True, choices=("DA", "F", "F-IB"))
    sc.add_argument("--runs", nargs="+", type=int, required=True)
    sc.add_argument("--prose-runs", nargs="*", type=int)
    sc.add_argument("--g1", action="store_true")
    d = sub.add_parser("decide")
    d.add_argument("--name", required=True)
    d.add_argument("--round", required=True, choices=("validity", "1", "2", "confirm"))
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.self_test:
        return self_test()
    commands: dict[str, Callable[[argparse.Namespace], int]] = {
        "prepare": cmd_prepare,
        "stage0": cmd_stage0,
        "run": cmd_run,
        "judge": cmd_judge,
        "score": cmd_score,
        "decide": cmd_decide,
    }
    if args.command not in commands:
        raise SystemExit("choose a subcommand, or --self-test")
    return commands[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
