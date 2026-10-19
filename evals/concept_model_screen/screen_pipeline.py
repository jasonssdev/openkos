"""Running production concept extraction for the model screen (#1557).

Three pieces, each kept small enough to check:

- `ScreenBackend`: an `LLMBackend` around the real `OllamaClient` that adds
  (a) a content-addressed cache of every chat call, so a rescore or a replay
  costs nothing; (b) the registered per-call wall-clock timeout, raised as
  `HarnessTimeout` (a `BackendError`, NOT `BackendUnavailable`, so production
  treats it like any failed window: retry, then skip) and never re-sent on the
  production retry; (c) a per-call record (kind, window, attempt, seed, wall
  time, token counters, outcome).
- `capture_stages`: wraps the module-level helpers `extract_concept_union`
  calls, so the candidate set after every stage is recorded from the REAL
  pipeline run, not from a re-implementation of it (#1559's loss table).
- `run_source`: one production `extract_concept_union` call on one source,
  returning a JSON-safe record.

Production code is not modified; the wrappers are restored on exit.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import pathlib
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any, Final

from openkos.extraction import concept as concept_mod
from openkos.extraction import judge as judge_mod
from openkos.llm import parsing
from openkos.llm.base import BackendError
from openkos.llm.ollama import OllamaClient, OllamaGenerationCapped, OllamaUnavailable

CONTEXT_WINDOW: Final = 12_288
MAX_GENERATION_TOKENS: Final = 8_192
RETRY_SEED_STRIDE: Final = 1_000
"""Attempt k of a call in run r is sent with seed r + k * 1000, so the
production retry is a fresh sample (as it is in production, where the seed
is unset) while staying reproducible from cache."""

KIND_WINDOW: Final = "window"
KIND_WHOLE: Final = "whole_source_extract"
KIND_JUDGE: Final = "selector_judge"
KIND_REASK: Final = "reask"
KIND_PARTICIPANT: Final = "participant_capture"
KIND_OTHER: Final = "other"


class HarnessTimeout(BackendError):
    """The registered per-call wall-clock timeout fired (#1557)."""


@dataclass
class CallRecord:
    kind: str
    window: int | None
    attempt: int
    seed: int
    timeout_s: float
    outcome: str
    """ok | capped | timeout | timeout_not_resent | error"""
    wall_s: float
    cache_hit: bool
    key: str
    prompt_eval_count: int | None = None
    eval_count: int | None = None
    done_reason: str | None = None
    reply_chars: int | None = None
    thinking_chars: int | None = None
    error: str | None = None
    num_ctx: int = CONTEXT_WINDOW


def call_key(
    *,
    model: str,
    digest: str | None,
    messages: Sequence[Any],
    seed: int,
    num_ctx: int = CONTEXT_WINDOW,
) -> str:
    """Content address of one chat call. `num_ctx` is the context window SENT
    to Ollama; at the shipped default the key is byte-identical to #1557's,
    which is what lets the window-size screen (#1558) replay its cache."""
    payload = json.dumps(
        {
            "model": model,
            "digest": digest,
            "messages": list(messages),
            "seed": seed,
            "think": False,
            "num_ctx": num_ctx,
            "num_predict": MAX_GENERATION_TOKENS,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def classify_call(
    messages: Sequence[Any], windows: Sequence[str]
) -> tuple[str, int | None]:
    """Which pipeline call this is, from its system prompt; for an extraction
    call, which window (by exact containment of the window text)."""
    system = str(messages[0].get("content", "")) if messages else ""
    user = "\n".join(str(m.get("content", "")) for m in messages[1:])
    if system == concept_mod._SYSTEM_PROMPT:
        matches = [i for i, w in enumerate(windows) if w and w in user]
        if len(windows) > 1 and len(matches) == 1:
            return KIND_WINDOW, matches[0]
        return KIND_WHOLE, None
    if system == judge_mod._JUDGE_SYSTEM_PROMPT:
        return KIND_JUDGE, None
    if system == concept_mod._REASK_SYSTEM_PROMPT:
        return KIND_REASK, None
    if system == concept_mod._PARTICIPANT_CAPTURE_SYSTEM_PROMPT:
        return KIND_PARTICIPANT, None
    return KIND_OTHER, None


class _CapturingOpener:
    """`urlopen` stand-in that remembers the last response body for counters."""

    def __init__(self) -> None:
        self.last_body: bytes | None = None

    def __call__(self, request: urllib.request.Request, timeout: float) -> Any:
        response = urllib.request.urlopen(request, timeout=timeout)  # noqa: S310 -- local Ollama
        outer = self

        class _Wrapped:
            def read(self) -> bytes:
                body: bytes = response.read()
                outer.last_body = body
                return body

        return _Wrapped()


class ChatCache:
    def __init__(self, root: pathlib.Path) -> None:
        self.root = root

    def _path(self, key: str) -> pathlib.Path:
        return self.root / key[:2] / f"{key}.json"

    def get(self, key: str) -> dict[str, Any] | None:
        path = self._path(key)
        if not path.is_file():
            return None
        loaded = json.loads(path.read_text(encoding="utf-8"))
        return loaded if isinstance(loaded, dict) else None

    def put(self, key: str, entry: dict[str, Any]) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(entry, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)


Sender = Callable[[str, Sequence[Any], int, float], tuple[str | None, dict[str, Any]]]
"""(model, messages, seed, timeout_s) -> (content or None, entry fields)."""


def ollama_sender(host: str | None, num_ctx: int = CONTEXT_WINDOW) -> Sender:
    """The real transport: production `OllamaClient` with the production
    ceiling and context window (or a raised `num_ctx`, #1558), plus a
    per-call seed and timeout."""

    def send(
        model: str, messages: Sequence[Any], seed: int, timeout_s: float
    ) -> tuple[str | None, dict[str, Any]]:
        opener = _CapturingOpener()
        client = OllamaClient(
            model,
            host=host,
            timeout=timeout_s,
            max_generation_tokens=MAX_GENERATION_TOKENS,
            context_window=num_ctx,
            seed=seed,
            urlopen=opener,
        )
        started = time.monotonic()
        entry: dict[str, Any] = {}
        content: str | None = None
        try:
            content = client.chat(list(messages))
            entry["outcome"] = "ok"
        except OllamaGenerationCapped as exc:
            entry["outcome"] = "capped"
            entry["error"] = str(exc)[:300]
        except OllamaUnavailable as exc:
            elapsed = time.monotonic() - started
            cause = exc.__cause__
            timed_out = isinstance(cause, TimeoutError) or (
                isinstance(cause, urllib.error.URLError)
                and isinstance(cause.reason, TimeoutError)
            )
            if timed_out or elapsed >= timeout_s * 0.95:
                entry["outcome"] = "timeout"
            else:
                raise
        except BackendError as exc:
            entry["outcome"] = "error"
            entry["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
        entry["wall_s"] = time.monotonic() - started
        if opener.last_body is not None:
            with contextlib.suppress(ValueError):
                data = json.loads(opener.last_body)
                if isinstance(data, dict):
                    entry["prompt_eval_count"] = data.get("prompt_eval_count")
                    entry["eval_count"] = data.get("eval_count")
                    entry["done_reason"] = data.get("done_reason")
                    message = data.get("message")
                    thinking = (
                        message.get("thinking") if isinstance(message, dict) else None
                    )
                    entry["thinking_chars"] = (
                        len(thinking) if isinstance(thinking, str) else 0
                    )
        if content is not None:
            entry["content"] = content
        return content, entry

    return send


class ScreenBackend:
    """Cached, timed, recorded `LLMBackend` for one source run.

    `context_window` is what the PIPELINE plans its whole-source prompt bound
    against and stays the shipped 12288; `num_ctx` is what is sent (its
    sender must be built with the same value) and enters the cache key."""

    context_window: int = CONTEXT_WINDOW
    max_generation_tokens: int = MAX_GENERATION_TOKENS

    def __init__(
        self,
        *,
        model: str,
        digest: str | None,
        run: int,
        window_timeout_s: float,
        whole_timeout_s: float,
        cache: ChatCache,
        send: Sender,
        windows: Sequence[str] = (),
        num_ctx: int = CONTEXT_WINDOW,
    ) -> None:
        self.model = model
        self.num_ctx = num_ctx
        self.digest = digest
        self.run = run
        self.window_timeout_s = window_timeout_s
        self.whole_timeout_s = whole_timeout_s
        self.cache = cache
        self.send = send
        self.windows = list(windows)
        self.records: list[CallRecord] = []
        self._attempts: dict[str, int] = {}
        self._timed_out: set[str] = set()

    def chat(self, messages: Sequence[Any]) -> str:
        kind, window = classify_call(messages, self.windows)
        prompt_id = hashlib.sha256(
            json.dumps(list(messages), sort_keys=True).encode("utf-8")
        ).hexdigest()
        attempt = self._attempts.get(prompt_id, 0)
        self._attempts[prompt_id] = attempt + 1
        seed = self.run + attempt * RETRY_SEED_STRIDE
        timeout_s = (
            self.window_timeout_s if kind == KIND_WINDOW else self.whole_timeout_s
        )
        key = call_key(
            model=self.model,
            digest=self.digest,
            messages=messages,
            seed=seed,
            num_ctx=self.num_ctx,
        )
        if prompt_id in self._timed_out:
            # The registered rule: a timed-out call is a failure, and the
            # production retry is NOT re-sent (it would cost another timeout).
            self.records.append(
                CallRecord(
                    kind,
                    window,
                    attempt,
                    seed,
                    timeout_s,
                    "timeout_not_resent",
                    0.0,
                    True,
                    key,
                    num_ctx=self.num_ctx,
                )
            )
            raise HarnessTimeout("timeout (retry not re-sent)")
        entry = self.cache.get(key)
        cache_hit = entry is not None
        if (
            entry is not None
            and entry.get("outcome") == "timeout"
            and float(entry.get("timeout_s", 0)) < timeout_s
        ):
            entry = None  # timed out under a SHORTER bound: not a valid replay
            cache_hit = False
        if entry is None:
            _content, entry = self.send(self.model, messages, seed, timeout_s)
            entry["timeout_s"] = timeout_s
            self.cache.put(key, entry)
        content = entry.get("content")
        outcome = str(entry.get("outcome"))
        self.records.append(
            CallRecord(
                kind=kind,
                window=window,
                attempt=attempt,
                seed=seed,
                timeout_s=timeout_s,
                outcome=outcome,
                wall_s=float(entry.get("wall_s", 0.0)),
                cache_hit=cache_hit,
                key=key,
                prompt_eval_count=entry.get("prompt_eval_count"),
                eval_count=entry.get("eval_count"),
                done_reason=entry.get("done_reason"),
                reply_chars=len(content) if isinstance(content, str) else None,
                thinking_chars=entry.get("thinking_chars"),
                error=entry.get("error"),
                num_ctx=self.num_ctx,
            )
        )
        if outcome == "ok" and isinstance(content, str):
            return content
        if outcome == "timeout":
            self._timed_out.add(prompt_id)
            raise HarnessTimeout(f"timeout after {timeout_s:.0f}s")
        if outcome == "capped":
            raise OllamaGenerationCapped(str(entry.get("error", "capped")))
        raise BackendError(str(entry.get("error", outcome)))


# --- stage capture -----------------------------------------------------------


@dataclass
class StageRecorder:
    raw_windows: list[list[Any]] = field(default_factory=list)
    skipped: tuple[int, ...] = ()
    stripped: list[list[Any]] = field(default_factory=list)
    chunked: bool = False
    dedup: list[Any] | None = None
    drops: list[Any] | None = None
    pre_ceiling: list[Any] | None = None
    ceiling: list[Any] | None = None


@contextlib.contextmanager
def capture_stages(rec: StageRecorder) -> Iterator[None]:
    """Record every stage's candidate list while the real pipeline runs."""
    originals: dict[str, Any] = {}

    def patch(name: str, wrapper: Callable[..., Any]) -> None:
        originals[name] = getattr(concept_mod, name)
        setattr(concept_mod, name, wrapper)

    o_fan = concept_mod._fan_out_window_lists
    o_strip = concept_mod._strip_ungrounded_expansions
    o_dedup = concept_mod._dedup_merged
    o_twins = concept_mod._drop_source_title_twins
    o_ceiling = concept_mod._ceiling_round_robin

    def fan(*args: Any, **kwargs: Any) -> Any:
        lists, skipped = o_fan(*args, **kwargs)
        rec.chunked = True
        rec.raw_windows = [list(x) for x in lists]
        rec.skipped = tuple(skipped)
        return lists, skipped

    def strip(results: Any, **kwargs: Any) -> Any:
        out = o_strip(results, **kwargs)
        if rec.chunked and len(rec.stripped) < len(rec.raw_windows):
            rec.stripped.append(list(out))
        return out

    def dedup(results: Any) -> Any:
        out = o_dedup(results)
        if rec.dedup is None:
            # First call only: the re-ask and participant capture call it
            # again on the merged list plus their additions.
            rec.dedup = list(out)
        return out

    def twins(results: Any, **kwargs: Any) -> Any:
        out = o_twins(results, **kwargs)
        if rec.drops is None:
            rec.drops = list(out)
        return out

    def ceiling(merged: Any, provenance: Any) -> Any:
        rec.pre_ceiling = list(merged)
        out = o_ceiling(merged, provenance)
        rec.ceiling = list(out[0])
        return out

    patch("_fan_out_window_lists", fan)
    patch("_strip_ungrounded_expansions", strip)
    patch("_dedup_merged", dedup)
    patch("_drop_source_title_twins", twins)
    patch("_ceiling_round_robin", ceiling)
    try:
        yield
    finally:
        for name, fn in originals.items():
            setattr(concept_mod, name, fn)


def _obj(result: Any, window: int | None) -> dict[str, Any]:
    return {
        "type": result.type,
        "title": result.title,
        "description": result.description,
        "body_chars": len(result.body or ""),
        "window": window,
    }


def window_outcome(
    records: Sequence[CallRecord],
    index: int,
    reply_ok: Callable[[str], bool] | None = None,
) -> str:
    """ok | empty | unparseable | timeout | capped | error for one window, from
    its calls (`unparseable`/`empty` are filled in by `run_source`)."""
    calls = [r for r in records if r.kind == KIND_WINDOW and r.window == index]
    if not calls:
        return "not_called"
    last = calls[-1]
    if (
        any(r.outcome in ("timeout", "timeout_not_resent") for r in calls)
        and last.outcome != "ok"
    ):
        return "timeout"
    return last.outcome


def run_source(
    *,
    text: str,
    title: str,
    backend: ScreenBackend,
    cache: ChatCache,
) -> dict[str, Any]:
    """One production `extract_concept_union` call, every stage recorded."""
    rec = StageRecorder()
    started = time.monotonic()
    failure: str | None = None
    outcome = None
    with capture_stages(rec):
        try:
            outcome = concept_mod.extract_concept_union(
                text, source_title=title, llm=backend, transcript_decisions=False
            )
        except BackendError as exc:
            failure = f"{type(exc).__name__}: {str(exc)[:200]}"
    wall = time.monotonic() - started

    provenance: dict[int, int] = {}
    for index, results in enumerate(rec.stripped):
        for result in results:
            provenance[id(result)] = index

    def objs(items: Sequence[Any] | None) -> list[dict[str, Any]] | None:
        if items is None:
            return None
        return [_obj(r, provenance.get(id(r))) for r in items]

    final = list(outcome.objects) if outcome is not None else []
    report = outcome.report if outcome is not None else None
    judge_stage: list[Any] | None = None
    if rec.ceiling is not None and report is not None:
        final_ids = {id(r) for r in final}
        discarded = set(report.discarded_titles)
        judge_stage = [
            r for r in rec.ceiling if id(r) in final_ids or r.title in discarded
        ]

    windows_out: list[dict[str, Any]] = []
    for index, window in enumerate(backend.windows if len(backend.windows) > 1 else []):
        state = window_outcome(backend.records, index)
        if state == "ok":
            last = [
                r
                for r in backend.records
                if r.kind == KIND_WINDOW and r.window == index
            ][-1]
            entry = cache.get(last.key) or {}
            reply = str(entry.get("content", ""))
            if not parsing.extract_json_items(reply):
                state = "empty" if _is_empty_array(reply) else "unparseable"
        windows_out.append(
            {
                "index": index,
                "sha256": hashlib.sha256(window.encode("utf-8")).hexdigest(),
                "outcome": state,
            }
        )

    return {
        "title": title,
        "failure": failure,
        "wall_s": wall,
        "chunked": rec.chunked,
        "windows": windows_out,
        "calls": [asdict(r) for r in backend.records],
        "stages": {
            "raw": [_obj(r, i) for i, rs in enumerate(rec.raw_windows) for r in rs]
            if rec.chunked
            else None,
            "dedup": objs(rec.dedup),
            "drops": objs(rec.drops),
            "pre_ceiling": objs(rec.pre_ceiling),
            "ceiling": objs(rec.ceiling),
            "judge": objs(judge_stage),
            "final": objs(final),
        },
        "report": None
        if report is None
        else {
            "chunks": report.chunks,
            "skipped_chunks": list(report.skipped_chunks),
            "judge_status": report.judge_status,
            "pre_judge_dropped": report.pre_judge_dropped,
            "judged_out_titles": list(report.judged_out_titles),
            "discarded_titles": list(report.discarded_titles),
            "wrong_language_dropped_titles": list(report.wrong_language_dropped_titles),
            "recombined_dropped_titles": list(report.recombined_dropped_titles),
            "reask_added_titles": list(report.reask_added_titles),
            "participant_capture_added_titles": list(
                report.participant_capture_added_titles
            ),
            "optional_call_failures": [str(x) for x in report.optional_call_failures],
            "judge_failure_causes": [str(x) for x in report.judge_failure_causes],
            "bounded_prompt_calls": list(report.bounded_prompt_calls),
        },
    }


def _is_empty_array(reply: str) -> bool:
    text = reply.strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    try:
        return bool(json.loads(text) == [])
    except ValueError:
        return False
