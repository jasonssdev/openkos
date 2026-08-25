"""Where the failing judge call's tokens actually went (#866), and that the
prompt bound closes it.

The E2E signature: on every chunked Spanish source, `openkos ingest`
reported `judge selection unavailable after 2 attempts (unparseable:
no-json, unparseable: no-json)` and the run cost ~8 minutes. #866 mandates
one targeted measurement before any fix: record `prompt_eval_count`,
`eval_count`, `done_reason`, and the thinking/content split for the failing
judge calls.

Measured (transcription1, 53,578 chars, qwen3:8b, the shipped
`num_predict` 8192 / `num_ctx` 12288):

- TRUE judge prompt size (measured at `num_ctx` 32768): **16,091 tokens**
  against a 12,288-token window -- the prompt cannot fit, deterministically.
- At the production window the server logs `truncating input prompt:
  limit=6146 prompt=16091 keep=4` -- llama.cpp keeps 4 head tokens plus the
  LAST `(num_ctx - 4) / 2` = 6,142, so the SYSTEM PROMPT and the first ~60%
  of the source are silently cut.
- The failing call itself: `prompt_eval_count=6146`, `done_reason='stop'`,
  `eval_count=1995`, `thinking_chars=0`, `content_chars=7884` -- Spanish
  prose (`"Aqui tienes una estructura de conocimiento..."`), classified
  `unparseable: no-json`. NOT #830's thinking runaway: `think: false` held
  (zero thinking chars) and the model finished normally. It answered the
  decapitated prompt it was given, twice, identically.

This probe reproduces all three measurements from the real pipeline, plus
the post-fix arm: with the #866 bound in place, the pipeline's judge prompt
fits the window (`prompt_eval_count < num_ctx`, no server truncation) and
the judge parses.

    uv run python -u evals/judge_overflow/run_judge_overflow_probe.py --self-test
    uv run python -u evals/judge_overflow/run_judge_overflow_probe.py \
        --corpus <dir-with-large-transcripts> --runs 1

The corpus is NOT committed (the motivating sources are private
transcripts); measurement requires `--corpus`, and every committed result
row is counters, statuses, cause strings, and a filename DIGEST only --
never source text, filenames, titles, or reply text. The self-test pins that scrub structurally.
"""

import argparse
import hashlib
import json
import sys
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from openkos import config  # noqa: E402
from openkos.extraction import concept as concept_mod  # noqa: E402
from openkos.extraction import judge as judge_mod  # noqa: E402
from openkos.extraction.concept import ExtractionResult  # noqa: E402
from openkos.llm.base import LLMBackend, Message  # noqa: E402
from openkos.llm.ollama import OllamaClient  # noqa: E402

RESULTS_DIR = HERE / "results"

_TRUE_SIZE_WINDOW = 32_768
"""Window for the true-prompt-size call: large enough that the transcripts
this probe exists for cannot be truncated, so `prompt_eval_count` reports
the prompt's REAL token count rather than the post-truncation one."""

_MAX_COMMITTED_STRING = 80
"""Longest string a committed result row may carry (self-test-pinned): long
enough for a failure-cause string or a phase label, far too short for a
reply, a title list, or source text."""


# --------------------------------------------------------------------------- #
# Recording transport -- counters off the raw response, client untouched.      #
# --------------------------------------------------------------------------- #


class _ReplayResponse:
    """A read-once response the recorder already drained (the
    `evals/generation_ceiling/` pattern): `OllamaClient.chat` calls
    `.read()` exactly once, so replaying buffered bytes keeps the client's
    own parsing, `done_reason` guard, and error mapping untouched."""

    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body


class _RecordingTransport:
    """A `urlopen` stand-in logging every chat call's generation counters.

    `phase` is set from `extract_concept_union`'s own `on_progress` hook,
    which fires immediately before the call it describes -- the pipeline's
    public reporting seam, not a guess from prompt sizes."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.phase = "(before first reported phase)"

    def __call__(self, request: Any, timeout: float | None = None) -> _ReplayResponse:
        started = time.monotonic()
        # `{configured host}/api/chat`, built by `OllamaClient` from
        # user/env config -- never from document content.
        response = urllib.request.urlopen(request, timeout=timeout)  # noqa: S310
        body: bytes = response.read()
        entry: dict[str, Any] = {
            "phase": self.phase,
            "prompt_chars": len(request.data or b""),
            "prompt_eval_count": None,
            "eval_count": None,
            "done_reason": None,
            "content_chars": None,
            "thinking_chars": None,
            "seconds": round(time.monotonic() - started, 1),
        }
        try:
            data = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            self.calls.append(entry)
            return _ReplayResponse(body)
        if isinstance(data, dict):
            message = data.get("message") or {}
            entry["prompt_eval_count"] = data.get("prompt_eval_count")
            entry["eval_count"] = data.get("eval_count")
            entry["done_reason"] = data.get("done_reason")
            if isinstance(message, dict):
                entry["content_chars"] = len(message.get("content") or "")
                entry["thinking_chars"] = len(message.get("thinking") or "")
        self.calls.append(entry)
        return _ReplayResponse(body)


def _production_client(urlopen: Callable[..., Any]) -> OllamaClient:
    """The exact client shape `cli.main._chat_client` builds for `ingest`
    at packaged defaults -- ceiling, window, and timeout included."""
    return OllamaClient(
        model=config.DEFAULT_MODEL,
        timeout=config.DEFAULT_CHAT_TIMEOUT,
        max_generation_tokens=config.DEFAULT_MAX_GENERATION_TOKENS,
        context_window=config.DEFAULT_CONTEXT_WINDOW,
        urlopen=urlopen,
    )


# --------------------------------------------------------------------------- #
# Measurement.                                                                 #
# --------------------------------------------------------------------------- #


def _capture_judge_call(
    source_text: str, source_title: str, llm: LLMBackend
) -> tuple[dict[str, Any], list[ExtractionResult]]:
    """Run the REAL union pipeline once, freezing what reached the judge.

    Wraps `concept._select_with_progress` -- the one seam that sees the
    judge's input on its way in (the `evals/judge_cold_start/` pattern).
    Production is untouched: the wrapper delegates and records."""
    seen: dict[str, Any] = {}
    candidates: list[ExtractionResult] = []
    original = concept_mod._select_with_progress

    def _recording(
        source_text: str,
        judge_input: list[ExtractionResult],
        llm: LLMBackend,
        on_progress: Callable[[str], None] | None,
    ) -> judge_mod.JudgeOutcome:
        seen["judge_source_chars"] = len(source_text)
        candidates.extend(judge_input)
        return original(source_text, judge_input, llm, on_progress)

    concept_mod._select_with_progress = _recording
    try:
        outcome = concept_mod.extract_concept_union(
            source_text,
            source_title=source_title,
            llm=llm,
            on_progress=_phase_setter(llm),
        )
    finally:
        concept_mod._select_with_progress = original

    seen["judge_status"] = outcome.report.judge_status
    seen["judge_failure_causes"] = list(outcome.report.judge_failure_causes)
    seen["bounded_prompt_calls"] = list(outcome.report.bounded_prompt_calls)
    seen["produced"] = outcome.report.produced
    seen["retained"] = outcome.report.retained
    seen["chunks"] = outcome.report.chunks
    return seen, candidates


def _phase_setter(llm: LLMBackend) -> Callable[[str], None]:
    """Feed the pipeline's own phase labels to the recording transport."""
    transport = getattr(llm, "_urlopen", None)

    def _set(label: str) -> None:
        if isinstance(transport, _RecordingTransport):
            transport.phase = label

    return _set


def _judge_candidates(
    results: list[ExtractionResult],
) -> tuple[judge_mod.JudgeCandidate, ...]:
    return tuple(
        judge_mod.JudgeCandidate(type=c.type, title=c.title, description=c.description)
        for c in results
    )


def _one_judge_chat(
    messages: list[Message], options: dict[str, Any], timeout: float
) -> dict[str, Any]:
    """One raw judge chat with full counter read-back, `think: false`
    exactly as `OllamaClient.chat` sends it. Raw rather than through the
    client so `done_reason == "length"` records counters instead of
    raising."""
    body = {
        "model": config.DEFAULT_MODEL,
        "messages": messages,
        "stream": False,
        "think": False,
        "options": options,
    }
    request = urllib.request.Request(
        "http://127.0.0.1:11434/api/chat",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.monotonic()
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        data = json.loads(response.read())
    message = data.get("message") or {}
    content = message.get("content") or ""
    _, cause = judge_mod.classify_reply(content, ())
    return {
        "prompt_eval_count": data.get("prompt_eval_count"),
        "eval_count": data.get("eval_count"),
        "done_reason": data.get("done_reason"),
        "content_chars": len(content),
        "thinking_chars": len(message.get("thinking") or ""),
        "parse_cause": cause if cause is not None else "parsed",
        "seconds": round(time.monotonic() - started, 1),
    }


def measure_source(source_path: Path, run_index: int) -> dict[str, Any]:
    """All three measurements for one source: the post-fix pipeline run,
    the unbounded-judge defect demonstration, and the true prompt size."""
    source_text = source_path.read_text(encoding="utf-8")
    source_title = source_path.stem
    record: dict[str, Any] = {
        # A DIGEST of the filename, never the filename: the corpus is
        # private, and a transcript's own name can carry a person's name or
        # a meeting title -- the committed row must not (R1 review finding).
        "source": hashlib.sha256(source_path.stem.encode("utf-8")).hexdigest()[:12],
        "run": run_index,
        "source_chars": len(source_text),
        "num_ctx": config.DEFAULT_CONTEXT_WINDOW,
        "num_predict": config.DEFAULT_MAX_GENERATION_TOKENS,
        "model": config.DEFAULT_MODEL,
    }

    # 1. The post-fix pipeline, production client, counters recorded.
    transport = _RecordingTransport()
    client = _production_client(transport)
    print(f"  [{source_path.stem}] run {run_index}: pipeline ...", flush=True)
    started = time.monotonic()
    seen, candidates = _capture_judge_call(source_text, source_title, client)
    record["pipeline_seconds"] = round(time.monotonic() - started, 1)
    record["pipeline"] = seen
    judge_rows = [c for c in transport.calls if c["phase"].startswith("judging")]
    record["judge_calls"] = judge_rows
    fits = all(
        isinstance(row["prompt_eval_count"], int)
        and row["prompt_eval_count"] < config.DEFAULT_CONTEXT_WINDOW
        for row in judge_rows
    )
    record["judge_prompt_fits_window"] = bool(judge_rows) and fits
    print(
        f"    judge_status={seen['judge_status']} "
        f"bounded={seen['bounded_prompt_calls']} "
        f"judge_prompt_fits_window={record['judge_prompt_fits_window']}",
        flush=True,
    )

    # 2. The defect demonstration: the same judge input UNBOUNDED -- the
    # exact prompt the pre-fix pipeline sent.
    messages = judge_mod._build_judge_messages(
        source_text, _judge_candidates(candidates)
    )
    record["unbounded_prompt_chars"] = sum(len(m["content"]) for m in messages)
    print("    unbounded judge call ...", flush=True)
    record["unbounded_judge"] = _one_judge_chat(
        list(messages),
        {
            "num_ctx": config.DEFAULT_CONTEXT_WINDOW,
            "num_predict": config.DEFAULT_MAX_GENERATION_TOKENS,
        },
        config.DEFAULT_CHAT_TIMEOUT,
    )
    print(
        f"    unbounded: prompt_eval={record['unbounded_judge']['prompt_eval_count']} "
        f"done_reason={record['unbounded_judge']['done_reason']} "
        f"cause={record['unbounded_judge']['parse_cause']}",
        flush=True,
    )

    # 3. The true prompt size, at a window that cannot truncate it.
    print("    true-size call ...", flush=True)
    true_size = _one_judge_chat(
        list(messages),
        {"num_ctx": _TRUE_SIZE_WINDOW, "num_predict": 1},
        config.DEFAULT_CHAT_TIMEOUT,
    )
    record["true_prompt_tokens"] = true_size["prompt_eval_count"]
    print(f"    true prompt tokens: {record['true_prompt_tokens']}", flush=True)
    return record


# --------------------------------------------------------------------------- #
# Scrub invariant.                                                             #
# --------------------------------------------------------------------------- #


def committed_string_violations(value: Any, path: str = "$") -> list[str]:
    """Every string in a result tree longer than `_MAX_COMMITTED_STRING`.

    The corpus is private: a result row is counters and short causes, and
    this walk is what the self-test pins so a future field cannot quietly
    start carrying reply text or source text into the repository."""
    violations: list[str] = []
    if isinstance(value, str):
        if len(value) > _MAX_COMMITTED_STRING:
            violations.append(f"{path}: string of {len(value)} chars")
    elif isinstance(value, dict):
        for key, item in value.items():
            violations.extend(committed_string_violations(item, f"{path}.{key}"))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            violations.extend(committed_string_violations(item, f"{path}[{index}]"))
    return violations


# --------------------------------------------------------------------------- #
# Self-test (offline: no model call anywhere below).                           #
# --------------------------------------------------------------------------- #


def self_test() -> None:
    failures: list[str] = []

    def check(condition: bool, label: str) -> None:
        if condition:
            print(f"  ok: {label}")
        else:
            failures.append(label)
            print(f"  FAIL: {label}")

    # The scrub walk sees a long string wherever it hides.
    dirty = {"a": [{"b": "x" * 200}], "c": "short"}
    violations = committed_string_violations(dirty)
    check(len(violations) == 1 and "$.a[0].b" in violations[0], "scrub finds nesting")
    check(not committed_string_violations({"ok": "y" * 80}), "scrub passes short")

    # The production bound guarantees the judge prompt fits the planning
    # budget on exactly the class that failed: a synthetic source larger
    # than the reported transcripts, 24 max-size candidate lines.
    source = "\n".join(f"linea {i:04d} " + "palabra " * 8 for i in range(1_500))
    candidates = _judge_candidates(
        [
            ExtractionResult(
                type="Concept", title=f"Subject {i:02d}", description="d " * 30, body=""
            )
            for i in range(24)
        ]
    )
    overhead = judge_mod.prompt_overhead_chars(candidates)

    class _NoWindowLLM:
        """A backend with no `context_window` attribute: the bound plans at
        the packaged default, exactly as it does for any non-Ollama
        backend."""

        def chat(self, messages: object) -> str:
            raise AssertionError("the bound must not chat")

    bounded, excerpted = concept_mod._bounded_prompt_source(
        source,
        overhead_chars=overhead,
        llm=_NoWindowLLM(),
        generation_reserve_tokens=concept_mod._JUDGE_REPLY_RESERVE_TOKENS,
    )
    budget_chars = int(
        (
            concept_mod._PROMPT_PLANNING_CONTEXT_WINDOW
            - concept_mod._JUDGE_REPLY_RESERVE_TOKENS
        )
        / concept_mod._PROMPT_TOKENS_PER_CHAR
    )
    check(excerpted, "oversized judge source is excerpted")
    check(
        len(bounded) + overhead <= budget_chars,
        "bounded judge prompt fits the planning budget",
    )
    # The planning budget itself clears the window at every ratio this
    # repo has measured (0.277 and 0.341 tokens/char): even at the HIGHEST,
    # the planned prompt stays under the window minus the reply reserve.
    check(
        budget_chars * 0.341
        <= concept_mod._PROMPT_PLANNING_CONTEXT_WINDOW
        - concept_mod._JUDGE_REPLY_RESERVE_TOKENS,
        "planning ratio covers the highest measured tokens/char",
    )

    # classify_reply is the production parser this probe reports through.
    _, cause = judge_mod.classify_reply("Aqui tienes una estructura", ())
    check(cause == "unparseable: no-json", "prose classifies as no-json")

    if failures:
        raise SystemExit(f"self-test FAILED: {failures}")
    print("self-test passed")


# --------------------------------------------------------------------------- #
# Entry point.                                                                 #
# --------------------------------------------------------------------------- #


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--corpus", type=Path, default=None)
    parser.add_argument("--runs", type=int, default=1)
    args = parser.parse_args()

    if args.self_test:
        self_test()
        return
    if args.corpus is None:
        raise SystemExit("measurement needs --corpus <dir> (the corpus is private)")

    sources = sorted(
        p
        for p in args.corpus.glob("*.md")
        if len(p.read_text(encoding="utf-8")) >= concept_mod._MEETING_CHUNK_THRESHOLD
    )
    if not sources:
        raise SystemExit(f"no chunk-scale .md sources under {args.corpus}")

    records = []
    for source_path in sources:
        for run_index in range(1, args.runs + 1):
            records.append(measure_source(source_path, run_index))

    violations = [v for r in records for v in committed_string_violations(r)]
    if violations:
        raise SystemExit(f"REFUSING to write results, scrub violations: {violations}")

    RESULTS_DIR.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    out = RESULTS_DIR / f"runs-{stamp}-{config.DEFAULT_MODEL.replace(':', '-')}.json"
    out.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
    print(f"\nwrote {out.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
