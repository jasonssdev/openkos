"""Translate the 12 development meetings into Spanish (#1609).

The planned gold bundle is about 94% English and its only Spanish material is on
other topics, so a language effect cannot be told from a topic effect. The fix is
to ingest the same meetings in both languages. This script produces the Spanish
side: the 12 MCD-AFG thesis DEVELOPMENT meetings (ES2015, IS1004, TS3009; a to d),
translated with a local model through the production `OllamaClient`.

Nothing here runs unless you ask. `--self-test` contacts no model.

    uv run python evals/retrieval_gold/translate_meetings.py --self-test
    uv run python evals/retrieval_gold/translate_meetings.py run \\
        --model gemma4:26b-a4b --out ~/openkos-eval-runs/retrieval-gold/translations \\
        --transcripts DIR [--meetings IS1004a,...] [--retry-failed]
    uv run python evals/retrieval_gold/translate_meetings.py sample-sheet \\
        --out ~/openkos-eval-runs/retrieval-gold/translation-review.md \\
        --translations DIR --transcripts DIR
    uv run python evals/retrieval_gold/translate_meetings.py pin \\
        --translations DIR --transcripts DIR --reviewed

Only `run` talks to a model, and it is GPU-bound: read the README forecast first.

Properties that matter:

- **Fail closed on the meeting.** A meeting is translated only if it passes the
  thesis allowlist of `evals/concept_model_screen/screen_data.py` AND is one of the
  12 development meetings (`build_bundle.require_translatable`). The thesis
  evaluation and held-out series, the reserve series and the free pool are refused
  before any file is opened or any directory created.
- **Windows never split a turn.** The body is cut by the production
  `concept._chunk_lines` (a turn is one line). Each window is sent as plain
  `LABEL: text` lines and must come back with the same number of lines and the same
  labels in the same order.
- **Nothing is silently dropped.** A window whose reply is capped, times out, has
  the wrong structure or still reads as English is retried once with a different
  seed, then recorded as failed. A meeting with a failed window is not written at
  all; `--retry-failed` retries just those windows.
- **Resumable and pinned.** One record per window; a re-run skips windows already
  translated and refuses to resume under a different model or prompt.
- **Machine-translated and attributed.** AMI is CC BY 4.0; a translation is an
  adaptation, so each output carries the attribution, the licence link and a
  statement that it was machine-translated, in its frontmatter and in plain words.
- **A human gate before the bundle.** `sample-sheet` writes aligned EN/ES pairs for
  a native speaker; `pin` writes the reviewed files' hashes into `bundle_sources.json`
  only with `--reviewed`. `build_bundle.py` refuses to build until that is done.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import pathlib
import random
import re
import sys
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

HERE: Final = pathlib.Path(__file__).resolve().parent
REPO_ROOT: Final = HERE.parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.append(str(REPO_ROOT / "evals"))
sys.path.append(str(REPO_ROOT / "evals" / "concept_model_screen"))
sys.path.append(str(HERE))

import build_bundle  # noqa: E402
import harness_stamp  # noqa: E402
import screen_data  # noqa: E402

from openkos.extraction import concept  # noqa: E402
from openkos.llm.base import (  # noqa: E402
    BackendError,
    BackendGenerationCapped,
    BackendModelNotFound,
    BackendUnavailable,
    Message,
    is_timeout_failure,
)
from openkos.llm.ollama import OllamaClient  # noqa: E402

OUTPUT_SUFFIX: Final = build_bundle.TRANSLATION_SUFFIX
MANIFEST_NAME: Final = "manifest.json"
RUN_NAME: Final = "run.json"

PROMPT_ID: Final = "translate/system"
SYSTEM_PROMPT: Final = """\
You translate transcripts of spoken meetings from English into Spanish.

The user message gives a count and then that many lines. Each line is one speaker \
turn: a speaker label, a colon, a space, then what was said.

Rules:
1. Return exactly the same number of lines, in the same order, one output line per \
input line. Never merge, split, skip, add, reorder or number lines. No blank lines, \
no commentary, no code fences, no markdown.
2. Copy each speaker label exactly as it is. Translate only what follows the label.
3. Translate everything after the label into natural, neutral, conversational \
Spanish. Keep the register of speech: hesitations and fillers become their Spanish \
equivalents, repetitions stay, unfinished sentences stay unfinished. Do not tidy, \
summarize, correct or explain.
4. Keep people's names, product and brand names, numbers and acronyms as they are. \
Translate ordinary words, including the vocabulary of the meeting.
5. A line that is only a filler or a backchannel is translated as well, with its \
Spanish equivalent.
6. Write nothing in English except names, acronyms and literal product names.
"""

TEMPERATURE: Final = 0.2
"""Not zero: a retry with another seed must be able to differ. At temperature 0 the
seed would change nothing and the retry would repeat the failure."""
CONTEXT_WINDOW: Final = 8192
MAX_GENERATION_TOKENS: Final = 4096
"""A 4,000-character window is about 1,400 English tokens in and about 1,700 Spanish
tokens out; both fit the context with room, and the ceiling is well above the reply."""
TIMEOUT_S: Final = 600.0
SEED_BASE: Final = 1609
RETRY_SEED_OFFSET: Final = 7919

MIN_WORDS: Final = 4
"""A turn with fewer words has too few function words to vote on a language; it is
neither counted nor flagged (a backchannel such as `Okay.` is legitimately kept)."""
MIN_EN_VOTES: Final = 2
"""English function words needed before a turn can be called untranslated. Measured
on committed text with the repo's own vocabulary: 0 of 89 genuine Spanish lines are
flagged at 2 votes, and 401 of 507 English lines are (0.79). At 3 votes recall falls
to 0.66 with no gain in precision."""
WINDOW_LEAK_MAX: Final = 0.10
"""A window with more than this share of English-left long turns is retried."""
MEETING_LEAK_MAX: Final = 0.02
"""A meeting with more than this share of English-left long turns is not written."""

SPANISH_CONTROL: Final = (
    "examples/extraction-corpus/sources/medium-10-reunion-plataforma.md"
)
ENGLISH_CONTROL: Final = "evals/quiet_engine/corpus/sources"

ATTRIBUTION: Final = (
    "Adapted from the AMI Meeting Corpus (https://groups.inf.ed.ac.uk/ami/corpus/), "
    "licensed under CC BY 4.0 "
    "(https://groups.inf.ed.ac.uk/ami/corpus/license.shtml; "
    "https://creativecommons.org/licenses/by/4.0/)."
)
ADAPTATION: Final = (
    "Machine translation into Spanish of the English transcript {meeting}, produced "
    "with the model {model} run locally. Every utterance was translated; speaker "
    "labels and turn order are unchanged. It was checked automatically for "
    "structure and sampled by a native speaker, not proofread in full, and it may "
    "contain translation errors. It is an adaptation, not the original text."
)

_LINE_RE: Final = re.compile(r"^(?P<label>[A-Za-z0-9_]+): (?P<text>\S.*)$")


class TranslateError(ValueError):
    """The run cannot proceed or a result cannot be trusted; the message says why."""


class StructureError(ValueError):
    """A line is not a `LABEL: text` turn."""


@dataclass(frozen=True)
class TurnLine:
    label: str
    text: str

    @property
    def line(self) -> str:
        return f"{self.label}: {self.text}"


@dataclass(frozen=True)
class Reply:
    text: str
    eval_count: int | None


Translator = Callable[[Sequence[Message], int], Reply]
"""`(messages, seed) -> Reply`. The only implementation that contacts a model is
`ollama_translator`; the self-test passes a stub."""


# --- structure and language --------------------------------------------------


def parse_turns(lines: Sequence[str]) -> list[TurnLine]:
    turns: list[TurnLine] = []
    for index, line in enumerate(lines):
        match = _LINE_RE.match(line)
        if match is None:
            raise StructureError(
                f"line {index} is not a `LABEL: text` turn with text: {line[:60]!r}"
            )
        turns.append(TurnLine(match.group("label"), match.group("text")))
    return turns


def check_structure(src: Sequence[TurnLine], es: Sequence[TurnLine]) -> list[str]:
    problems: list[str] = []
    if len(src) != len(es):
        problems.append(f"expected {len(src)} turns, got {len(es)}")
    for index, (a, b) in enumerate(zip(src, es, strict=False)):
        if a.label != b.label:
            problems.append(
                f"speaker sequence differs at turn {index}: "
                f"expected {a.label}, got {b.label}"
            )
            break
    return problems


def _words(text: str) -> list[str]:
    return concept._LANGUAGE_TOKEN_RE.findall(text.lower())


def votes(text: str) -> tuple[int, int]:
    """`(spanish, english)` function-word votes: the vocabulary and tokenizer of the
    repo's own language voter (`concept._dominant_language`)."""
    tokens = _words(text)
    es = sum(1 for t in tokens if t in concept._ES_FUNCTION_WORDS)
    en = sum(1 for t in tokens if t in concept._EN_FUNCTION_WORDS)
    return es, en


def english_left(source_text: str, es_text: str) -> bool:
    """Is this Spanish turn still English? Either its function words vote for
    English by the repo's margin (at least `MIN_EN_VOTES`), or it is the English
    source back again (compared on letters only) and long enough to mean it."""
    es, en = votes(es_text)
    if en >= MIN_EN_VOTES and en > es * concept._DOMINANT_LANGUAGE_MARGIN:
        return True
    source_words = _words(source_text)
    return len(source_words) >= MIN_WORDS and source_words == _words(es_text)


def leakage(src: Sequence[TurnLine], es: Sequence[TurnLine]) -> dict[str, Any]:
    """English left untranslated, over the turns long enough to judge."""
    long_idx = [i for i, t in enumerate(src) if len(t.text.split()) >= MIN_WORDS]
    flagged = [
        i for i in long_idx if i < len(es) and english_left(src[i].text, es[i].text)
    ]
    return {
        "turns": len(src),
        "long_turns": len(long_idx),
        "english_left": len(flagged),
        "share": len(flagged) / len(long_idx) if long_idx else 0.0,
        "indexes": flagged,
    }


# --- one window --------------------------------------------------------------


def build_messages(lines: Sequence[str]) -> list[Message]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"Translate these {len(lines)} lines:\n" + "\n".join(lines),
        },
    ]


def _validate_reply(
    text: str, src: Sequence[TurnLine], threshold: float
) -> tuple[list[str], list[str], dict[str, Any] | None]:
    """`(problems, reply lines, leakage)` for one reply to one window."""
    lines = [ln.rstrip() for ln in text.strip().split("\n") if ln.strip()]
    try:
        es = parse_turns(lines)
    except StructureError as exc:
        return [str(exc)], lines, None
    problems = check_structure(src, es)
    if problems:
        return problems, lines, None
    stats = leakage(src, es)
    if stats["share"] > threshold:
        problems.append(
            f"English left untranslated in {stats['english_left']} of "
            f"{stats['long_turns']} long turns ({stats['share']:.0%} > "
            f"{threshold:.0%})"
        )
    return problems, lines, stats


def translate_window(
    window: screen_data.Window,
    translator: Translator,
    clock: Callable[[], float],
) -> dict[str, Any]:
    """Translate one window: try, retry once with another seed, else record failure.

    Returns the window record. `status` is `ok` or `failed`; a failed record keeps
    every attempt's reason. A server that is gone (`BackendUnavailable` that is not
    a timeout) or a missing model is not a window failure and propagates."""
    source_lines = [ln for ln in window.text.split("\n") if ln.strip()]
    record: dict[str, Any] = {
        "index": window.index,
        "window_sha256": window.sha256,
        "status": "ok",
        "lines": [],
        "attempts": [],
        "leakage": None,
        "seconds": 0.0,
    }
    if not source_lines:
        return record
    src = parse_turns(source_lines)
    messages = build_messages(source_lines)
    started = clock()
    for seed in (
        SEED_BASE + window.index,
        SEED_BASE + window.index + RETRY_SEED_OFFSET,
    ):
        attempt: dict[str, Any] = {"seed": seed, "eval_count": None}
        began = clock()
        try:
            reply = translator(messages, seed)
        except BackendGenerationCapped:
            attempt["outcome"] = "capped"
        except BackendModelNotFound:
            raise
        except BackendUnavailable as exc:
            if not is_timeout_failure(exc):
                raise
            attempt["outcome"] = "timeout"
        except BackendError as exc:
            attempt["outcome"] = f"error:{type(exc).__name__}"
        else:
            attempt["eval_count"] = reply.eval_count
            problems, lines, stats = _validate_reply(reply.text, src, WINDOW_LEAK_MAX)
            if problems:
                attempt["outcome"] = "invalid"
                attempt["problems"] = problems[:5]
                attempt["reply_head"] = reply.text[:300]
            else:
                attempt["outcome"] = "ok"
                record["lines"] = lines
                record["leakage"] = stats
        attempt["seconds"] = round(clock() - began, 3)
        record["attempts"].append(attempt)
        if attempt["outcome"] == "ok":
            break
    else:
        record["status"] = "failed"
    record["seconds"] = round(clock() - started, 3)
    return record


# --- one meeting -------------------------------------------------------------


def _write_text(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _write_json(path: pathlib.Path, data: Mapping[str, Any]) -> None:
    _write_text(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def _read_json(path: pathlib.Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    loaded = json.loads(path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else {}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def assemble(
    windows: Sequence[screen_data.Window], records: Sequence[Mapping[str, Any]]
) -> str:
    """The Spanish body: the English layout with each non-blank line replaced."""
    parts: list[str] = []
    for window, record in zip(windows, records, strict=True):
        translated = iter(record["lines"])
        parts.append(
            "\n".join(
                next(translated) if ln.strip() else ln for ln in window.text.split("\n")
            )
        )
        if next(translated, None) is not None:
            raise TranslateError(f"{window.meeting} window {window.index}: extra lines")
    return "\n".join(parts)


def render_frontmatter(meeting: str, model: str, turns: int) -> str:
    fields = {
        "meeting_id": f"{meeting}{OUTPUT_SUFFIX}",
        "translation_of": meeting,
        "language": "es",
        "source": "ami_public_manual_1.6.2",
        "licence": "CC BY 4.0",
        "attribution": ATTRIBUTION,
        "adaptation": ADAPTATION.format(meeting=meeting, model=model),
        "machine_translated": "true",
        "translation_model": model,
        "turns": str(turns),
    }
    quoted = {"attribution", "adaptation", "translation_model", "meeting_id"}
    body = "".join(
        f"{k}: {json.dumps(v, ensure_ascii=False) if k in quoted else v}\n"
        for k, v in fields.items()
    )
    return f"---\n{body}---\n"


def _body_lines(text: str) -> list[str]:
    return [ln for ln in screen_data.strip_frontmatter(text).split("\n") if ln.strip()]


def _window_path(out: pathlib.Path, meeting: str, index: int) -> pathlib.Path:
    return out / "windows" / meeting / f"{index:03d}.json"


def _translate_meeting(
    meeting: str,
    transcript: screen_data.Transcript,
    out: pathlib.Path,
    translator: Translator,
    *,
    model: str,
    retry_failed: bool,
    clock: Callable[[], float],
    log: Callable[[str], None],
) -> dict[str, Any]:
    windows = screen_data.build_windows(meeting, transcript.body, concept._chunk_lines)
    source_lines = [ln for ln in transcript.body.split("\n") if ln.strip()]
    if len(source_lines) != len(transcript.turns):
        raise TranslateError(
            f"{meeting}: {len(source_lines)} body turns but {len(transcript.turns)} "
            "in the turn index"
        )
    records: list[dict[str, Any]] = []
    for window in windows:
        path = _window_path(out, meeting, window.index)
        record = _read_json(path)
        reusable = record.get("window_sha256") == window.sha256 and (
            record.get("status") == "ok" or not retry_failed
        )
        if not reusable:
            record = translate_window(window, translator, clock)
            _write_json(path, record)
            log(
                f"{meeting} window {window.index + 1}/{len(windows)}: "
                f"{record['status']} ({record['seconds']}s)"
            )
        records.append(record)

    entry: dict[str, Any] = {
        "status": "done",
        "windows": [
            {k: r.get(k) for k in ("index", "status", "seconds", "attempts")}
            for r in records
        ],
        "output": None,
    }
    if any(r["status"] != "ok" for r in records):
        entry["status"] = "failed"
        return entry

    body = assemble(windows, records)
    es_turns = parse_turns([ln for ln in body.split("\n") if ln.strip()])
    src_turns = parse_turns(source_lines)
    stats = leakage(src_turns, es_turns)
    problems = check_structure(src_turns, es_turns)
    if stats["share"] > MEETING_LEAK_MAX:
        problems.append(
            f"English left untranslated in {stats['english_left']} of "
            f"{stats['long_turns']} long turns ({stats['share']:.1%} > "
            f"{MEETING_LEAK_MAX:.0%})"
        )
        # Mark the windows that leaked so `--retry-failed` has something to retry.
        for record in records:
            if (record.get("leakage") or {}).get("english_left"):
                record["status"] = "failed"
                record["attempts"].append(
                    {"outcome": "meeting-leak", "problems": problems[:1]}
                )
                _write_json(_window_path(out, meeting, record["index"]), record)
    if problems:
        entry["status"] = "failed"
        entry["problems"] = problems
        entry["windows"] = [
            {k: r.get(k) for k in ("index", "status", "seconds", "attempts")}
            for r in records
        ]
        return entry
    text = render_frontmatter(meeting, model, len(es_turns)) + "\n" + body
    if not text.endswith("\n"):
        text += "\n"
    target = out / f"{meeting}{OUTPUT_SUFFIX}.md"
    _write_text(target, text)
    data = text.encode("utf-8")
    entry["output"] = {
        "path": target.name,
        "sha256": _sha(data),
        "bytes": len(data),
        "turns": len(es_turns),
        "leakage": {k: v for k, v in stats.items() if k != "indexes"},
    }
    return entry


def _failures(meetings: Mapping[str, Any]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for meeting, entry in sorted(meetings.items()):
        for window in entry.get("windows", []):
            if window.get("status") == "failed":
                reasons = [
                    a.get("outcome", "") + ": " + "; ".join(a.get("problems", []))
                    for a in window.get("attempts", [])
                ]
                found.append(
                    {"meeting": meeting, "window": window["index"], "reasons": reasons}
                )
        if entry.get("status") == "failed" and entry.get("problems"):
            found.append(
                {"meeting": meeting, "window": None, "reasons": entry["problems"]}
            )
    return found


def run(
    meetings: Sequence[str],
    transcripts: pathlib.Path,
    out: pathlib.Path,
    translator: Translator,
    *,
    model: str,
    stamp: Callable[[str], dict[str, Any]],
    retry_failed: bool = False,
    expected_sha: Mapping[str, str] | None = None,
    clock: Callable[[], float] = time.monotonic,
    log: Callable[[str], None] = print,
) -> int:
    """Translate `meetings` into `out`. 0 if every one is done, 1 otherwise."""
    out = build_bundle.refuse_workspace(out)
    loaded: dict[str, screen_data.Transcript] = {}
    digests: dict[str, str] = {}
    for meeting in meetings:  # fail closed, before anything is created
        build_bundle.require_translatable(meeting)
        loaded[meeting] = screen_data.load_transcript(transcripts, meeting)
        digests[meeting] = _sha((transcripts / f"{meeting}.md").read_bytes())
        if expected_sha is not None and expected_sha.get(meeting) != digests[meeting]:
            raise TranslateError(
                f"{meeting}: the English transcript is not the pinned one "
                f"({digests[meeting][:12]} vs {str(expected_sha.get(meeting))[:12]})"
            )

    prompt_sha = _sha(SYSTEM_PROMPT.encode("utf-8"))
    options = {
        "temperature": TEMPERATURE,
        "context_window": CONTEXT_WINDOW,
        "max_generation_tokens": MAX_GENERATION_TOKENS,
        "seed_base": SEED_BASE,
        "retry_seed_offset": RETRY_SEED_OFFSET,
        "chunk_target": concept._CHUNK_TARGET,
    }
    identity = {"model": model, "prompt_sha256": prompt_sha, "options": options}
    previous = _read_json(out / RUN_NAME)
    if previous and previous != identity:
        raise TranslateError(
            f"{out} holds a run made with a different model, prompt or options; "
            "use a fresh --out so windows from two configurations never mix"
        )
    out.mkdir(parents=True, exist_ok=True)
    _write_json(out / RUN_NAME, identity)

    run_stamp = stamp(model)
    manifest = _read_json(out / MANIFEST_NAME)
    manifest_meetings: dict[str, Any] = dict(manifest.get("meetings", {}))
    try:
        for meeting in meetings:
            entry = _translate_meeting(
                meeting,
                loaded[meeting],
                out,
                translator,
                model=model,
                retry_failed=retry_failed,
                clock=clock,
                log=log,
            )
            entry["source_sha256"] = digests[meeting]
            manifest_meetings[meeting] = entry
            log(f"{meeting}: {entry['status']}")
    finally:
        _write_json(
            out / MANIFEST_NAME,
            {
                "kind": "retrieval-gold-translation-manifest",
                "version": 1,
                "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "stamp": run_stamp,
                "harness": run_stamp.get("harness"),
                "model": run_stamp.get("model"),
                "prompt": {"id": PROMPT_ID, "sha256": prompt_sha},
                "options": options,
                "thresholds": {
                    "min_words": MIN_WORDS,
                    "min_english_votes": MIN_EN_VOTES,
                    "window_leak_max": WINDOW_LEAK_MAX,
                    "meeting_leak_max": MEETING_LEAK_MAX,
                },
                "meetings": manifest_meetings,
                "failures": _failures(manifest_meetings),
            },
        )
    return 0 if all(manifest_meetings[m]["status"] == "done" for m in meetings) else 1


def ollama_translator(model: str, host: str | None) -> Translator:
    """The only client that contacts a model: production `OllamaClient`, one per
    call because the seed is a constructor argument."""

    def translate(messages: Sequence[Message], seed: int) -> Reply:
        seen: list[Any] = []
        client = OllamaClient(
            model,
            host=host,
            timeout=TIMEOUT_S,
            max_generation_tokens=MAX_GENERATION_TOKENS,
            temperature=TEMPERATURE,
            seed=seed,
            context_window=CONTEXT_WINDOW,
            diagnostics_sink=seen.append,
        )
        text = client.chat(messages)
        return Reply(text=text, eval_count=seen[-1].eval_count if seen else None)

    return translate


# --- review sheet and pin ----------------------------------------------------


def _checked_translation(
    translations: pathlib.Path, meeting: str
) -> tuple[pathlib.Path, dict[str, Any]]:
    """The translated file of a finished meeting whose bytes match the manifest."""
    manifest = _read_json(translations / MANIFEST_NAME)
    entry = manifest.get("meetings", {}).get(meeting)
    if not entry or entry.get("status") != "done" or not entry.get("output"):
        raise TranslateError(f"{meeting}: not translated (see {MANIFEST_NAME})")
    path = translations / entry["output"]["path"]
    if not path.is_file() or _sha(path.read_bytes()) != entry["output"]["sha256"]:
        raise TranslateError(f"{meeting}: {path.name} changed since the run")
    return path, manifest


def sample_sheet(
    out_path: pathlib.Path,
    transcripts: pathlib.Path,
    translations: pathlib.Path,
    meetings: Sequence[str],
    per_meeting: int,
    seed: int,
    min_words: int,
) -> int:
    """Write a seeded review sheet of aligned EN/ES turn pairs. Returns the pairs."""
    build_bundle.refuse_workspace(out_path)
    sections: list[str] = []
    total = 0
    model = ""
    for meeting in meetings:
        build_bundle.require_translatable(meeting)
        path, manifest = _checked_translation(translations, meeting)
        model = manifest["model"]["name"]
        english = [
            ln
            for ln in screen_data.load_transcript(transcripts, meeting).body.split("\n")
            if ln.strip()
        ]
        spanish = _body_lines(path.read_text(encoding="utf-8"))
        en_turns, es_turns = parse_turns(english), parse_turns(spanish)
        if check_structure(en_turns, es_turns):
            raise TranslateError(f"{meeting}: turns are not aligned; cannot sample")
        candidates = [
            i for i, t in enumerate(en_turns) if len(t.text.split()) >= min_words
        ]
        rng = random.Random(f"{seed}:{meeting}")  # noqa: S311 -- reproducible sample
        chosen = sorted(rng.sample(candidates, min(per_meeting, len(candidates))))
        sections.append(f"## {meeting}\n")
        for i in chosen:
            sections.append(
                f"### {meeting}-t{i:04d} ({en_turns[i].label})\n"
                f"EN: {en_turns[i].text}\n"
                f"ES: {es_turns[i].text}\n"
                "Verdict: \n"
                "Note: \n"
            )
            total += 1
    header = (
        "# Translation review sheet (#1609)\n\n"
        f"Model: {model}. Sample: seed {seed}, up to {per_meeting} turns of "
        f"{min_words}+ English words per meeting, drawn at random.\n\n"
        "For each pair write exactly one of these after the Verdict label: "
        "`accurate` (faithful and natural), `minor issue` (meaning kept; wording, "
        "register or a small slip), `wrong` (meaning lost, changed or invented, or "
        "left in English). Use Note for anything worth keeping.\n\n"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _write_text(out_path, header + "\n".join(sections))
    return total


def pin(
    translations: pathlib.Path,
    transcripts: pathlib.Path,
    sources_path: pathlib.Path,
    *,
    reviewed: bool,
) -> list[str]:
    """Write the reviewed translations' hash and size into the sources list.

    All or nothing. Refuses without `reviewed`, for a meeting that is not done, a
    file that changed since the run, a structure that no longer checks, or a hash
    that is already pinned to something else. Returns the pinned source ids."""
    if not reviewed:
        raise TranslateError(
            "pin needs the owner's review of the sample sheet: pass --reviewed"
        )
    raw = json.loads(sources_path.read_text(encoding="utf-8"))
    updates: list[tuple[dict[str, Any], str, int, dict[str, Any]]] = []
    for entry in raw["sources"]:
        if entry.get("kind") != build_bundle.TRANSLATION_KIND:
            continue
        meeting = entry["translation_of"]
        build_bundle.require_translatable(meeting)
        path, manifest = _checked_translation(translations, meeting)
        data = path.read_bytes()
        english = [
            ln
            for ln in screen_data.load_transcript(transcripts, meeting).body.split("\n")
            if ln.strip()
        ]
        en_turns = parse_turns(english)
        es_turns = parse_turns(_body_lines(data.decode("utf-8")))
        problems = check_structure(en_turns, es_turns)
        share = leakage(en_turns, es_turns)["share"]
        if share > MEETING_LEAK_MAX:
            problems.append(f"English left untranslated: {share:.1%}")
        if problems:
            raise TranslateError(f"{meeting}: {'; '.join(problems)}")
        digest = _sha(data)
        if entry["sha256"] not in (build_bundle.PENDING_SHA, digest):
            raise TranslateError(f"{entry['id']} is already pinned to another hash")
        info = {
            "model": manifest["model"],
            "prompt_sha256": manifest["prompt"]["sha256"],
        }
        updates.append((entry, digest, len(data), info))
    if not updates:
        raise TranslateError("the sources list has no translation entries")
    for entry, digest, size, info in updates:
        entry["sha256"] = digest
        entry["bytes"] = size
        entry["translation"] = info
    sources_path.write_text(
        json.dumps(raw, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    return [str(entry["id"]) for entry, *_ in updates]


# --- command line ------------------------------------------------------------


def _pinned_english() -> dict[str, str]:
    return {
        s.id: s.sha256
        for s in build_bundle.load_sources()
        if s.kind == "ami-meeting" and s.id in build_bundle.TRANSLATED_MEETINGS
    }


def _meetings(arg: str | None) -> list[str]:
    if not arg:
        return list(build_bundle.TRANSLATED_MEETINGS)
    return [part.strip() for part in arg.split(",") if part.strip()]


def _cmd_run(args: argparse.Namespace) -> int:
    host = args.host
    code = run(
        _meetings(args.meetings),
        args.transcripts,
        args.out,
        ollama_translator(args.model, host),
        model=args.model,
        stamp=lambda tag: harness_stamp.build_stamp(
            model=tag, prompts={PROMPT_ID: SYSTEM_PROMPT}, host=host
        ),
        retry_failed=args.retry_failed,
        expected_sha=_pinned_english(),
    )
    if code != 0:
        print(
            "some windows failed: see failures in the manifest; "
            "--retry-failed retries just those",
            file=sys.stderr,
        )
    return code


def _cmd_sheet(args: argparse.Namespace) -> int:
    pairs = sample_sheet(
        args.out,
        args.transcripts,
        args.translations,
        _meetings(args.meetings),
        args.per_meeting,
        args.seed,
        args.min_words,
    )
    print(f"{pairs} pairs written to {args.out}")
    return 0


def _cmd_pin(args: argparse.Namespace) -> int:
    ids = pin(
        args.translations,
        args.transcripts,
        args.sources,
        reviewed=args.reviewed,
    )
    print(f"pinned {len(ids)} translations in {args.sources}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--self-test", action="store_true", help="no model is contacted"
    )
    sub = parser.add_subparsers(dest="command")

    def common(cmd: argparse.ArgumentParser) -> None:
        cmd.add_argument("--transcripts", type=pathlib.Path, required=True)
        cmd.add_argument("--meetings", help="comma-separated ids (default: all 12)")

    run_cmd = sub.add_parser("run", help="translate the meetings (GPU-bound)")
    common(run_cmd)
    run_cmd.add_argument("--model", required=True, help="Ollama model tag")
    run_cmd.add_argument("--out", type=pathlib.Path, required=True)
    run_cmd.add_argument("--host", help="Ollama host (default: OLLAMA_HOST)")
    run_cmd.add_argument(
        "--retry-failed", action="store_true", help="retry windows recorded failed"
    )
    sheet = sub.add_parser("sample-sheet", help="write the owner's review sheet")
    common(sheet)
    sheet.add_argument("--translations", type=pathlib.Path, required=True)
    sheet.add_argument("--out", type=pathlib.Path, required=True)
    sheet.add_argument("--per-meeting", type=int, default=10)
    sheet.add_argument("--seed", type=int, default=1609)
    sheet.add_argument("--min-words", type=int, default=MIN_WORDS + 2)
    pin_cmd = sub.add_parser("pin", help="pin reviewed translations by sha256")
    pin_cmd.add_argument("--translations", type=pathlib.Path, required=True)
    pin_cmd.add_argument("--transcripts", type=pathlib.Path, required=True)
    pin_cmd.add_argument(
        "--sources", type=pathlib.Path, default=build_bundle.SOURCES_PATH
    )
    pin_cmd.add_argument(
        "--reviewed",
        action="store_true",
        help="the owner has reviewed the sample sheet and accepts the translations",
    )

    args = parser.parse_args(argv)
    if args.self_test:
        return _self_test()
    commands = {"run": _cmd_run, "sample-sheet": _cmd_sheet, "pin": _cmd_pin}
    if args.command not in commands:
        parser.error("a command is required (run, sample-sheet, pin) or --self-test")
    try:
        return commands[args.command](args)
    except (
        TranslateError,
        StructureError,
        build_bundle.BuildError,
        screen_data.DisallowedMeeting,
    ) as exc:
        print(f"translate_meetings: {exc}", file=sys.stderr)
        return 2
    except BackendUnavailable as exc:
        print(
            f"translate_meetings: the model server stopped answering ({exc}); "
            "run the same command again to resume",
            file=sys.stderr,
        )
        return 3


# --- self-test ---------------------------------------------------------------


class _Checks:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self.total = 0

    def check(self, ok: bool, label: str) -> None:
        self.total += 1
        if not ok:
            self.failures.append(label)
            print(f"FAIL: {label}")

    def raises(
        self, exc_type: type[BaseException], fn: Callable[[], object], label: str
    ) -> str:
        try:
            fn()
        except exc_type as exc:
            self.check(True, label)
            return str(exc)
        self.check(False, f"{label} (nothing was raised)")
        return ""


_FIXTURE_MEETING = "IS1004a"
_ES_LINE = "el equipo va a hablar de la reunión y es para los usuarios"


def _en_text(i: int) -> str:
    return (
        f"the team will talk about the design number {i} of the remote control "
        "and it is not for them"
    )


def _write_transcript(
    directory: pathlib.Path, meeting: str, turns: Sequence[tuple[str, str]]
) -> bytes:
    """A transcript in the renderer's layout: `.md` with frontmatter, `.jsonl`
    with one row per turn carrying exact body offsets."""
    body = ""
    rows = []
    for index, (label, text) in enumerate(turns):
        line = f"{label}: {text}"
        start = len(body)
        body += line
        rows.append(
            {
                "char_start": start,
                "char_end": len(body),
                "dialogue_act_ids": [f"{meeting}.{index}"],
                "text": line,
                "turn_index": index,
            }
        )
        body += "\n\n"
    md = f"---\nmeeting_id: {meeting}\nturns: {len(turns)}\n---\n\n{body}".encode()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{meeting}.md").write_bytes(md)
    (directory / f"{meeting}.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
    )
    return md


def _fixture_turns(n: int = 60) -> list[tuple[str, str]]:
    labels = ("PM", "ID", "UI", "ME")
    return [(labels[i % 4], _en_text(i)) for i in range(n)]


class _Stub:
    """A translator that contacts nothing. `plan(call_number, lines)` returns the
    reply text, or an exception to raise."""

    def __init__(
        self,
        plan: Callable[[int, list[str]], str | BaseException] | None = None,
    ) -> None:
        self.plan = plan or (lambda n, lines: _spanish(lines))
        self.seeds: list[int] = []
        self.sizes: list[int] = []

    @property
    def calls(self) -> int:
        return len(self.seeds)

    def __call__(self, messages: Sequence[Message], seed: int) -> Reply:
        lines = messages[-1]["content"].split("\n")[1:]
        self.seeds.append(seed)
        self.sizes.append(len(lines))
        out = self.plan(len(self.seeds), lines)
        if isinstance(out, BaseException):
            raise out
        return Reply(text=out, eval_count=len(out) // 3)


def _spanish(lines: Sequence[str]) -> str:
    return "\n".join(f"{ln.split(': ', 1)[0]}: {_ES_LINE}" for ln in lines)


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        self.now += 1.5
        return self.now


def _identify(tag: str) -> dict[str, Any]:
    return {"name": tag, "digest": f"digest-of-{tag}"}


def _stamp(tag: str) -> dict[str, Any]:
    return {
        "stamp_version": 1,
        "harness": {"commit": "0" * 40, "dirty": False},
        "model": _identify(tag),
        "prompts": [{"id": PROMPT_ID, "sha256_16": "0" * 16}],
    }


def _run(
    tmp: pathlib.Path, name: str, translator: Translator, **over: Any
) -> tuple[int, pathlib.Path]:
    out = tmp / name
    code = run(
        over.pop("meetings", [_FIXTURE_MEETING]),
        tmp / "transcripts",
        out,
        translator,
        model="stub:1b",
        stamp=_stamp,
        clock=_Clock(),
        log=lambda _msg: None,
        **over,
    )
    return code, out


def _manifest(out: pathlib.Path) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((out / "manifest.json").read_text("utf-8"))
    return loaded


def _self_test_structure(c: _Checks) -> None:
    src = parse_turns(["PM: hello there", "ID: okay"])
    c.check([t.label for t in src] == ["PM", "ID"], "turns parse into labels")
    c.raises(StructureError, lambda: parse_turns(["no label here"]), "unlabelled line")
    c.raises(StructureError, lambda: parse_turns(["PM: "]), "an empty turn")
    good = parse_turns(["PM: hola a todos", "ID: vale"])
    c.check(check_structure(src, good) == [], "same count and speakers pass")
    c.check(
        any("2 turns" in p for p in check_structure(src, good[:1])),
        "a dropped turn is reported with the counts",
    )
    swapped = parse_turns(["ID: hola a todos", "PM: vale"])
    c.check(
        any("speaker" in p for p in check_structure(src, swapped)),
        "a changed speaker sequence is reported",
    )
    c.check(
        check_structure(parse_turns(["PM: hi"]), parse_turns(["PM: hi"])) == [],
        "an identical short turn is not a problem by itself",
    )


def _self_test_language(c: _Checks) -> None:
    c.check(votes("el equipo va a hablar de la reunión")[0] >= 2, "Spanish votes ES")
    c.check(english_left(_en_text(1), _en_text(1)), "an untranslated turn is flagged")
    c.check(
        english_left("x " * 5, "we will see the thing of the day and it is on"),
        "an English reply to a non-English-looking source is flagged by votes",
    )
    c.check(not english_left(_en_text(1), _ES_LINE), "a Spanish turn is not flagged")
    c.check(not english_left("Okay.", "Vale."), "a short backchannel is not flagged")
    c.check(
        english_left("Thank you very much indeed.", "thank you very much indeed"),
        "a copied turn with no function words is flagged by the copy rule",
    )
    c.check(
        not english_left("Okay then.", "Okay then."),
        "a copied turn under the word floor is left alone",
    )
    spanish_control = (REPO_ROOT / SPANISH_CONTROL).read_text("utf-8").splitlines()
    long_es = [
        ln.strip().lstrip("-*#> ").strip()
        for ln in spanish_control
        if len(ln.split()) >= MIN_WORDS
    ]
    c.check(len(long_es) >= 50, f"the Spanish control has material: {len(long_es)}")
    c.check(
        not any(english_left("x", ln) for ln in long_es),
        "no genuine Spanish line is flagged (false positives are zero)",
    )
    english: list[str] = []
    for path in sorted((REPO_ROOT / ENGLISH_CONTROL).glob("*.md")):
        for ln in path.read_text("utf-8").splitlines():
            ln = ln.strip().lstrip("-*#> ").strip()
            if len(ln.split()) >= MIN_WORDS:
                english.append(ln)
    caught = sum(english_left("x", ln) for ln in english)
    c.check(
        len(english) >= 300 and caught / len(english) >= 0.7,
        f"untranslated English is caught ({caught} of {len(english)})",
    )
    src = parse_turns([f"PM: {_en_text(i)}" for i in range(10)])
    mixed = parse_turns(
        [f"PM: {_en_text(i) if i == 0 else _ES_LINE}" for i in range(10)]
    )
    stats = leakage(src, mixed)
    c.check(stats["english_left"] == 1 and stats["turns"] == 10, "leakage counts turns")
    c.check(abs(stats["share"] - 0.1) < 1e-9, "and reports the share of long turns")


def _self_test_pipeline(c: _Checks, tmp: pathlib.Path) -> None:
    turns = _fixture_turns()
    md = _write_transcript(tmp / "transcripts", _FIXTURE_MEETING, turns)

    # allowlist: refused before any file is opened or any directory created
    for bad in ("ES2002a", "TS3012a", "IB4003", "TS3005b", "ES2015e", "nonsense"):
        stub = _Stub()
        out = tmp / f"refused-{bad}"
        c.raises(
            screen_data.DisallowedMeeting,
            lambda bad=bad, stub=stub, out=out: run(  # type: ignore[misc]
                [bad],
                tmp / "transcripts",
                out,
                stub,
                model="stub:1b",
                stamp=_stamp,
                clock=_Clock(),
                log=lambda _m: None,
            ),
            f"{bad} is refused",
        )
        c.check(stub.calls == 0 and not out.exists(), f"{bad}: nothing happened")

    # end to end
    stub = _Stub()
    code, out = _run(tmp, "ok", stub)
    c.check(code == 0, "a clean run exits 0")
    target = out / f"{_FIXTURE_MEETING}-es.md"
    text = target.read_text("utf-8")
    body = screen_data.strip_frontmatter(text)
    es_lines = [ln for ln in body.split("\n") if ln.strip()]
    c.check(len(es_lines) == len(turns), "same number of turns in the output")
    c.check(
        [ln.split(": ", 1)[0] for ln in es_lines] == [t[0] for t in turns],
        "same speaker sequence in the output",
    )
    c.check(stub.calls == 2, f"60 turns of ~100 chars make two windows: {stub.calls}")
    c.check(sum(stub.sizes) == 60, "every turn was sent exactly once")
    for needle in (
        "CC BY 4.0",
        "https://groups.inf.ed.ac.uk/ami/corpus/license.shtml",
        "machine_translated: true",
        "translation_of: IS1004a",
        "language: es",
        "stub:1b",
    ):
        c.check(needle in text.split("\n---\n", 1)[0], f"frontmatter carries {needle}")
    c.check("machine translation" in text.lower(), "the statement is in plain words")
    c.check(
        text.split("\n---\n", 1)[1].startswith("\n" + es_lines[0]),
        "the layout is the English one: blank line between turns",
    )
    manifest = _manifest(out)
    entry = manifest["meetings"][_FIXTURE_MEETING]
    c.check(
        entry["source_sha256"] == hashlib.sha256(md).hexdigest(), "source sha256 pinned"
    )
    c.check(manifest["model"] == _identify("stub:1b"), "model tag and digest recorded")
    c.check(
        manifest["prompt"]["sha256"]
        == hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest(),
        "prompt sha256 recorded",
    )
    c.check(
        len(entry["windows"]) == 2
        and all(w["status"] == "ok" and w["seconds"] > 0 for w in entry["windows"]),
        "per-window timings recorded",
    )
    c.check(
        entry["status"] == "done"
        and entry["output"]["sha256"] == hashlib.sha256(target.read_bytes()).hexdigest()
        and entry["output"]["turns"] == 60,
        "output hash and turn count recorded",
    )
    c.check(manifest["failures"] == [], "no failures recorded")

    # resumable: nothing is translated twice, the output is byte-identical
    before = target.read_bytes()
    again = _Stub()
    code2, _ = _run(tmp, "ok", again)
    c.check(code2 == 0 and again.calls == 0, "a finished run translates nothing again")
    c.check(target.read_bytes() == before, "and the output is byte-identical")

    # an interrupted run resumes at the window it stopped on
    state = {"n": 0}

    def die_on_second(n: int, lines: list[str]) -> str | BaseException:
        state["n"] += 1
        if n == 2:
            return BackendUnavailable("server went away")
        return _spanish(lines)

    c.raises(
        BackendUnavailable,
        lambda: _run(tmp, "resume", _Stub(die_on_second)),
        "an unreachable server stops the run instead of recording failures",
    )
    c.check(
        not (tmp / "resume" / f"{_FIXTURE_MEETING}-es.md").exists(),
        "no partial transcript is written",
    )
    resumed = _Stub()
    code3, out3 = _run(tmp, "resume", resumed)
    c.check(code3 == 0 and resumed.calls == 1, "the resume translates only the rest")
    c.check(
        (out3 / f"{_FIXTURE_MEETING}-es.md").read_bytes() == before,
        "and ends byte-identical to the uninterrupted run",
    )

    # a stale prompt or model refuses to resume into the same directory
    c.raises(
        TranslateError,
        lambda: run(
            [_FIXTURE_MEETING],
            tmp / "transcripts",
            tmp / "ok",
            _Stub(),
            model="other:2b",
            stamp=_stamp,
            clock=_Clock(),
            log=lambda _m: None,
        ),
        "a different model cannot resume into an existing run directory",
    )

    # the pinned English twin must be the bytes that were translated
    c.raises(
        TranslateError,
        lambda: _run(tmp, "pinned", _Stub(), expected_sha={_FIXTURE_MEETING: "0" * 64}),
        "an English source that is not the pinned one is refused",
    )


def _self_test_retry(c: _Checks, tmp: pathlib.Path) -> None:
    capped = BackendGenerationCapped("capped")

    def cap_first(n: int, lines: list[str]) -> str | BaseException:
        return capped if n == 1 else _spanish(lines)

    stub = _Stub(cap_first)
    code, out = _run(tmp, "retry-ok", stub)
    c.check(code == 0 and stub.calls == 3, f"one capped reply is retried: {stub.calls}")
    c.check(stub.seeds[0] != stub.seeds[1], "the retry uses a different seed")
    first = _manifest(out)["meetings"][_FIXTURE_MEETING]["windows"][0]
    c.check(
        [a["outcome"] for a in first["attempts"]] == ["capped", "ok"],
        "both attempts are recorded",
    )

    def drop_a_turn(n: int, lines: list[str]) -> str | BaseException:
        return _spanish(lines[:-1])

    stub = _Stub(drop_a_turn)
    code, out = _run(tmp, "mismatch", stub)
    manifest = _manifest(out)
    c.check(code == 1, "a window that never comes out right fails the run")
    c.check(stub.calls == 4, f"two windows, two attempts each: {stub.calls}")
    c.check(
        not (out / f"{_FIXTURE_MEETING}-es.md").exists(),
        "a turn-count mismatch is never written out",
    )
    entry = manifest["meetings"][_FIXTURE_MEETING]
    c.check(entry["status"] == "failed", "the meeting is recorded failed")
    c.check(
        len(manifest["failures"]) == 2
        and "turns" in json.dumps(manifest["failures"][0]),
        "each failed window is listed with its reason",
    )

    stub = _Stub(lambda n, lines: capped)
    code, out = _run(tmp, "all-capped", stub)
    c.check(
        code == 1 and stub.calls == 4, "capped twice records a failure, no third try"
    )
    c.check(
        _manifest(out)["meetings"][_FIXTURE_MEETING]["windows"][0]["status"]
        == "failed",
        "the window is failed, never dropped or left untranslated",
    )
    again = _Stub()
    code2, _ = _run(tmp, "all-capped", again)
    c.check(code2 == 1 and again.calls == 0, "a failed window is not retried silently")
    retried = _Stub()
    code3, out3 = _run(tmp, "all-capped", retried, retry_failed=True)
    c.check(
        code3 == 0
        and retried.calls == 2
        and (out3 / f"{_FIXTURE_MEETING}-es.md").exists(),
        "--retry-failed translates the failed windows and finishes the meeting",
    )

    english_first = _Stub(
        lambda n, lines: "\n".join(lines) if n == 1 else _spanish(lines)
    )
    code, out = _run(tmp, "leak-retry", english_first)
    attempts = _manifest(out)["meetings"][_FIXTURE_MEETING]["windows"][0]["attempts"]
    c.check(
        code == 0 and [a["outcome"] for a in attempts] == ["invalid", "ok"],
        "an English window is retried and the retry is what is kept",
    )

    leaky = _Stub(lambda n, lines: "\n".join(lines))
    code, out = _run(tmp, "leaky", leaky)
    c.check(code == 1, "English returned as is fails the run")
    c.check(
        "English" in json.dumps(_manifest(out)["failures"]),
        "and the reason names the untranslated English",
    )
    c.check(not (out / f"{_FIXTURE_MEETING}-es.md").exists(), "and nothing is written")


def _self_test_review_and_pin(c: _Checks, tmp: pathlib.Path) -> None:
    _, out = _run(tmp, "reviewed", _Stub())
    sheet = tmp / "sheet.md"
    sample_sheet(
        sheet,
        tmp / "transcripts",
        out,
        meetings=[_FIXTURE_MEETING],
        per_meeting=5,
        seed=7,
        min_words=MIN_WORDS,
    )
    text = sheet.read_text("utf-8")
    c.check(text.count("Verdict:") == 5, "five pairs per meeting")
    c.check(
        "accurate" in text and "minor issue" in text and "wrong" in text,
        "the three verdicts are offered",
    )
    c.check(
        "EN: PM:" not in text
        and "EN: the team will talk" in text
        and f"ES: {_ES_LINE}" in text,
        "pairs are aligned, English beside Spanish",
    )
    again = tmp / "sheet2.md"
    sample_sheet(again, tmp / "transcripts", out, [_FIXTURE_MEETING], 5, 7, MIN_WORDS)
    c.check(again.read_text("utf-8") == text, "the same seed gives the same sample")
    other = tmp / "sheet3.md"
    sample_sheet(other, tmp / "transcripts", out, [_FIXTURE_MEETING], 5, 8, MIN_WORDS)
    c.check(other.read_text("utf-8") != text, "another seed gives another sample")
    c.raises(
        build_bundle.BuildError,
        lambda: sample_sheet(
            REPO_ROOT / "sheet-never.md",
            tmp / "transcripts",
            out,
            [_FIXTURE_MEETING],
            5,
            7,
            MIN_WORDS,
        ),
        "a sheet inside the repository is refused",
    )
    c.check(not (REPO_ROOT / "sheet-never.md").exists(), "and not created")

    entry = {
        "id": f"{_FIXTURE_MEETING}-es",
        "root": "translations",
        "path": f"{_FIXTURE_MEETING}-es.md",
        "language": "es",
        "kind": "ami-translation",
        "tier": "core",
        "bytes": 0,
        "sha256": build_bundle.PENDING_SHA,
        "translation_of": _FIXTURE_MEETING,
    }
    sources = tmp / "sources.json"
    sources.write_text(json.dumps({"version": 1, "sources": [entry]}), "utf-8")
    c.raises(
        TranslateError,
        lambda: pin(out, tmp / "transcripts", sources, reviewed=False),
        "pin refuses without the owner's review",
    )
    c.check(
        json.loads(sources.read_text("utf-8"))["sources"][0]["sha256"]
        == build_bundle.PENDING_SHA,
        "and leaves the sources untouched",
    )
    (out / f"{_FIXTURE_MEETING}-es.md").write_bytes(b"tampered")
    c.raises(
        TranslateError,
        lambda: pin(out, tmp / "transcripts", sources, reviewed=True),
        "pin refuses a file that changed since the run",
    )
    _, out2 = _run(tmp, "reviewed2", _Stub())
    pin(out2, tmp / "transcripts", sources, reviewed=True)
    pinned = json.loads(sources.read_text("utf-8"))["sources"][0]
    data = (out2 / f"{_FIXTURE_MEETING}-es.md").read_bytes()
    c.check(
        pinned["sha256"] == hashlib.sha256(data).hexdigest()
        and pinned["bytes"] == len(data),
        "pin writes the hash and size of the reviewed file",
    )
    c.check(
        not build_bundle.load_sources(sources)[0].pending,
        "and the build script then sees it as pinned",
    )
    _, bad = _run(tmp, "failed", _Stub(lambda n, lines: _spanish(lines[:-1])))
    sources.write_text(json.dumps({"version": 1, "sources": [entry]}), "utf-8")
    c.raises(
        TranslateError,
        lambda: pin(bad, tmp / "transcripts", sources, reviewed=True),
        "pin refuses a run with a failed meeting",
    )


def _self_test_cli(c: _Checks, tmp: pathlib.Path) -> None:
    code = main(
        [
            "run",
            "--model",
            "stub:1b",
            "--out",
            str(REPO_ROOT / "translations-never"),
            "--transcripts",
            str(tmp / "transcripts"),
        ]
    )
    c.check(code == 2, "run refuses an output directory inside the repository")
    c.check(not (REPO_ROOT / "translations-never").exists(), "and creates nothing")
    code = main(
        [
            "run",
            "--model",
            "stub:1b",
            "--out",
            str(tmp / "cli-out"),
            "--transcripts",
            str(tmp / "transcripts"),
            "--meetings",
            "ES2002a",
        ]
    )
    c.check(code == 2, "run refuses an evaluation-series meeting at the command line")
    c.check(not (tmp / "cli-out").exists(), "before creating its output directory")


def _self_test() -> int:
    c = _Checks()
    with (
        tempfile.TemporaryDirectory() as raw_tmp,
        contextlib.redirect_stdout(io.StringIO()) as out,
        contextlib.redirect_stderr(io.StringIO()),
    ):
        tmp = pathlib.Path(raw_tmp).resolve()
        _self_test_structure(c)
        _self_test_language(c)
        _self_test_pipeline(c, tmp)
        _self_test_retry(c, tmp)
        _self_test_review_and_pin(c, tmp)
        _self_test_cli(c, tmp)
    sys.stdout.write(
        "".join(
            ln + "\n" for ln in out.getvalue().splitlines() if ln.startswith("FAIL")
        )
    )
    if c.failures:
        print(f"self-test FAILED: {len(c.failures)} of {c.total} checks")
        return 1
    print(f"self-test passed ({c.total} checks, no model contacted)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
