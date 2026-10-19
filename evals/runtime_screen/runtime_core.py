"""The deterministic half of the local runtime screen (#1586).

Standard library only, so `tests/unit/test_runtime_screen_core.py` can load it
from its path and the harness's `--self-test` can run it without a model:

- the streaming arithmetic (time to first token splits prefill from decode),
  and the servers' own timing reports, read as a cross-check;
- the memory parsers: `vmmap -summary` (resident and physical footprint of a
  process) and the loader's buffer report (ADR-0047's method);
- the pre-registered stage-1 performance gate and the stage-2 equivalence
  rules over each harness's own report;
- the shortening ladder that keeps the measured time inside its budget.

Every number a rule compares against is a named constant here, and
`PREREGISTRATION.md` quotes the same values.
"""

from __future__ import annotations

import hashlib
import json
import re
import statistics
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

# --- stage 1 bars ------------------------------------------------------------

GAIN_RATIO: Final = 1.15
"""Prefill or decode tokens/s at least this multiple of Ollama's is a gain."""
REGRESSION_RATIO: Final = 0.90
"""Prefill or decode below this multiple of Ollama's is a regression."""
MEMORY_GAIN_RATIO: Final = 0.85
MEMORY_REGRESSION_RATIO: Final = 1.10
LOAD_REGRESSION_RATIO: Final = 2.0
LOAD_REGRESSION_SLACK_S: Final = 10.0
"""A slower load is a regression only when it is BOTH over the ratio and more
than this many seconds slower: a 3 s load becoming 7 s is not a product cost."""
SWAP_GAIN_RATIO: Final = 0.5
BUDGET_GIB: Final = 24.0
"""ADR-0047's memory budget for the 32 GB product floor."""
EMBED_GIB: Final = 0.66
"""`bge-m3` at production context, by the loader report (#1560 stage 0)."""

# --- stage 2 bars (quoted from the two harnesses' registrations) ------------

ASK_GATING_VETOES: Final = ("V1", "V2", "S2", "X1")
ASK_REPORTED_VETOES: Final = ("S1",)
CONCEPT_BASELINE: Final = "qwen3:8b"
CONCEPT_FAILURE_MARGIN: Final = 0.02
CONCEPT_FAILURE_CEILING: Final = 0.05
CONCEPT_LANGUAGE_MARGIN: Final = 0.02
CONCEPT_VOLUME_BAND: Final = (0.5, 1.5)
CONCEPT_VERBATIM_MARGIN: Final = 0.10
CONCEPT_G1_FLOOR: Final = -0.05


# --- streaming ---------------------------------------------------------------


def iter_sse(lines: Iterable[bytes | str]) -> Iterator[dict[str, Any]]:
    """The JSON payloads of a server-sent-event stream, up to `[DONE]`.

    A line that is not a `data:` line (a comment, a blank separator) is
    skipped, and so is a `data:` line that does not parse: a stream is read
    for its timing, and one malformed chunk does not void it."""
    for raw in lines:
        line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
        line = line.strip()
        if not line.startswith("data:"):
            continue
        body = line[len("data:") :].strip()
        if body == "[DONE]":
            return
        try:
            payload = json.loads(body)
        except ValueError:
            continue
        if isinstance(payload, dict):
            yield payload


def stream_rates(
    *,
    started: float,
    first_token: float,
    finished: float,
    prompt_tokens: int,
    completion_tokens: int,
) -> dict[str, float | None]:
    """Prefill and decode rates from client-side stream timestamps.

    Prefill ends when the first token arrives, so prefill tokens/s is the
    prompt over the time to first token, and decode is the REMAINING tokens
    over the rest. One token leaves no decode interval to measure."""
    if first_token < started:
        raise ValueError("the first token cannot arrive before the request")
    ttft = first_token - started
    decode_span = finished - first_token
    decode = (
        (completion_tokens - 1) / decode_span
        if completion_tokens > 1 and decode_span > 0
        else None
    )
    return {
        "ttft_s": ttft,
        "prefill_tps": prompt_tokens / ttft if ttft > 0 else None,
        "decode_tps": decode,
    }


def ollama_rates(final: Mapping[str, Any]) -> dict[str, Any]:
    """Ollama's own report in the final chunk of its native chat reply (nanoseconds)."""
    prompt = int(final["prompt_eval_count"])
    completion = int(final["eval_count"])
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "prefill_tps": prompt / (int(final["prompt_eval_duration"]) / 1e9),
        "decode_tps": completion / (int(final["eval_duration"]) / 1e9),
    }


def llamacpp_rates(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    """`llama-server`'s `timings` block, when the chunk carries one."""
    timings = payload.get("timings")
    if not isinstance(timings, dict):
        return None
    return {
        "prompt_tokens": int(timings["prompt_n"]),
        "completion_tokens": int(timings["predicted_n"]),
        "prefill_tps": float(timings["prompt_per_second"]),
        "decode_tps": float(timings["predicted_per_second"]),
    }


# --- memory ------------------------------------------------------------------

_UNITS: Final = {"": 1, "B": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}


def parse_size(token: str) -> int:
    """`vmmap`'s sizes: `2976K`, `33.2M`, `1.5G`, or a bare byte count."""
    match = re.fullmatch(r"([\d.]+)([BKMGT]?)", token.strip())
    if match is None:
        raise ValueError(f"not a size: {token!r}")
    return int(float(match.group(1)) * _UNITS[match.group(2)])


def parse_vmmap(text: str) -> dict[str, int]:
    """Resident size and physical footprint from `vmmap -summary <pid>`.

    Resident is the RESIDENT column of the FIRST `TOTAL` row (the region
    table). It counts file-backed weights that a runtime maps, which the
    physical footprint does not; the second `TOTAL` is the malloc zones."""
    out: dict[str, int] = {}
    for line in text.splitlines():
        match = re.match(r"Physical footprint( \(peak\))?:\s+(\S+)", line.strip())
        if match:
            key = "footprint_peak_bytes" if match.group(1) else "footprint_bytes"
            out[key] = parse_size(match.group(2))
        elif line.startswith("TOTAL") and "resident_bytes" not in out:
            fields = line.split()
            out["resident_bytes"] = parse_size(fields[2])
    if "resident_bytes" not in out:
        raise ValueError("vmmap output has no region TOTAL row")
    return out


_BUFFER: Final = re.compile(
    r"(\S+)\s+(model|KV|output|compute|RS)\s+buffer size\s*=\s*([\d.]+)\s*MiB"
)


def loader_report_mib(log_text: str) -> float | None:
    """The loader's buffer report for the MOST RECENT load (ADR-0047's
    method): weights, KV, recurrent state and output, plus the largest
    compute reservation per device. A load starts at the first model buffer
    line that follows a non-model buffer line."""
    loads: list[list[tuple[str, str, float]]] = []
    previous: str | None = None
    for match in _BUFFER.finditer(log_text):
        device, kind, mib = match.group(1), match.group(2), float(match.group(3))
        if kind == "model" and previous != "model":
            loads.append([])
        if loads:
            loads[-1].append((device, kind, mib))
        previous = kind
    if not loads:
        return None
    total = 0.0
    compute: dict[str, float] = {}
    for device, kind, mib in loads[-1]:
        if kind == "compute":
            compute[device] = max(compute.get(device, 0.0), mib)
        else:
            total += mib
    return total + sum(compute.values())


# --- prompt and identity -----------------------------------------------------

_VOCAB: Final = tuple(
    " ".join(
        (
            "archive budget cable decision engine folder garden harbor index",
            "journal kernel ledger meeting network option planner quarter record",
            "schedule ticket update vendor window yield anchor bridge canvas draft",
            "estimate factor gateway history insight lattice margin notice outline",
            "pattern quorum review signal summary threshold uplink version workflow",
        )
    ).split(" ")
)


def filler_text(n_words: int, *, nonce: str) -> str:
    """Deterministic synthetic prose for the prefill benchmark. The nonce
    makes each repetition's prompt distinct from its first token, so no
    runtime can serve it from a prompt cache."""
    words: list[str] = [f"[{nonce}]"]
    counter = 0
    while len(words) < n_words + 1:
        block = hashlib.sha256(f"{nonce}:{counter}".encode()).digest()
        words.extend(_VOCAB[b % len(_VOCAB)] for b in block)
        counter += 1
    sentences = [
        " ".join(words[i : i + 16]).capitalize() + "." for i in range(0, len(words), 16)
    ]
    return " ".join(sentences)


def weights_digest(entries: Sequence[tuple[str, str]]) -> str:
    """One digest for a set of weight files, from (name, sha256) pairs."""
    joined = "".join(f"{name}\0{sha}\n" for name, sha in sorted(entries))
    return "sha256:" + hashlib.sha256(joined.encode("utf-8")).hexdigest()


# --- stage 1 -----------------------------------------------------------------


@dataclass(frozen=True)
class Perf:
    prefill_tps: float | None
    decode_tps: float | None
    resident_gib: float | None
    load_s: float | None


@dataclass(frozen=True)
class Swap:
    swap_s: float | None
    both_resident: bool
    both_gib: float | None


def _values(perf: Perf) -> tuple[float, float, float, float]:
    """(prefill, decode, resident, load), refusing a missing measurement."""
    out: list[float] = []
    for name in ("prefill_tps", "decode_tps", "resident_gib", "load_s"):
        value = getattr(perf, name)
        if value is None:
            raise ValueError(f"stage 1 needs {name}; it was not measured")
        out.append(float(value))
    return out[0], out[1], out[2], out[3]


def _co_resident(swap: Swap | None) -> bool:
    return (
        swap is not None
        and swap.both_resident
        and swap.both_gib is not None
        and swap.both_gib + EMBED_GIB <= BUDGET_GIB
    )


def stage1_verdict(
    base: Perf,
    cand: Perf,
    base_swap: Swap | None = None,
    cand_swap: Swap | None = None,
) -> dict[str, Any]:
    """The pre-registered performance gate: no regression on any measured
    axis, and a material gain on at least one."""
    b_prefill, b_decode, b_resident, b_load = _values(base)
    c_prefill, c_decode, c_resident, c_load = _values(cand)
    gains: list[str] = []
    regressions: list[str] = []
    for label, b, c in (
        ("decode", b_decode, c_decode),
        ("prefill", b_prefill, c_prefill),
    ):
        ratio = c / b
        if ratio >= GAIN_RATIO:
            gains.append(f"{label} {ratio:.2f}x")
        elif ratio < REGRESSION_RATIO:
            regressions.append(f"{label} {ratio:.2f}x")
    memory = c_resident / b_resident
    if memory <= MEMORY_GAIN_RATIO:
        gains.append(f"memory {memory:.2f}x")
    elif memory > MEMORY_REGRESSION_RATIO:
        regressions.append(f"memory {memory:.2f}x")
    load = c_load / b_load if b_load > 0 else float("inf")
    if load > LOAD_REGRESSION_RATIO and c_load - b_load > LOAD_REGRESSION_SLACK_S:
        regressions.append(f"load {load:.2f}x")
    both = cand_swap.both_gib if cand_swap is not None else None
    if both is not None and _co_resident(cand_swap) and not _co_resident(base_swap):
        gains.append(f"both resident in {both + EMBED_GIB:.2f} GiB")
    elif (
        base_swap is not None
        and cand_swap is not None
        and base_swap.swap_s
        and cand_swap.swap_s is not None
        and cand_swap.swap_s / base_swap.swap_s <= SWAP_GAIN_RATIO
    ):
        gains.append(f"swap {cand_swap.swap_s / base_swap.swap_s:.2f}x")
    return {
        "passes": bool(gains) and not regressions,
        "gains": gains,
        "regressions": regressions,
    }


# --- stage 2 -----------------------------------------------------------------


def ask_equivalence(compare: Mapping[str, Any], model: str) -> dict[str, Any]:
    """#1560's sufficiency arm: no clear loss, and its registered margins.

    S1 (grounded refusals not above the baseline) has a margin of zero: it
    was written to SELECT a better model, and an equal runtime would trip it
    on half of all runs by sampling alone. A rise in grounded refusals
    already lowers the primary delta, so S1 is reported and does not gate."""
    entry = compare["roles"]["sufficiency"]["candidates"][model]
    reasons: list[str] = []
    hi = float(entry["delta"]["hi"])
    if hi < 0:
        reasons.append(f"sufficiency delta upper bound {hi:+.3f} < 0")
    vetoes = [str(v) for v in entry.get("vetoes", [])]
    reasons.extend(v for v in vetoes if v.split()[0] in ASK_GATING_VETOES)
    reported = [v for v in vetoes if v.split()[0] in ASK_REPORTED_VETOES]
    return {"passes": not reasons, "reasons": reasons, "reported": reported}


def _rate(ratio: Mapping[str, Any]) -> float:
    den = int(ratio["den"])
    return int(ratio["num"]) / den if den else 0.0


def concept_equivalence(
    scores: Mapping[str, Any], model: str, *, baseline: str = CONCEPT_BASELINE
) -> dict[str, Any]:
    """#1557's window-level replay: no clear loss on the primary (its
    round-1 drop rule), V1, V3, V4, V5 and G1 at their registered values."""
    cand = scores["models"][model]
    base = scores["models"][baseline]
    reasons: list[str] = []
    high = float(cand["dlift"]["high"])
    if high < 0:
        reasons.append(f"dlift upper bound {high:+.4f} < 0")
    fail_c, fail_b = _rate(cand["failures"]), _rate(base["failures"])
    if fail_c > fail_b + CONCEPT_FAILURE_MARGIN or fail_c > CONCEPT_FAILURE_CEILING:
        reasons.append(f"V1 failures {fail_c:.3f} (baseline {fail_b:.3f})")
    lang_c = _rate(cand["prose"]["wrong_language"])
    lang_b = _rate(base["prose"]["wrong_language"])
    if lang_c > lang_b + CONCEPT_LANGUAGE_MARGIN:
        reasons.append(f"V3 wrong-language titles {lang_c:.3f}")
    low, high_band = (
        f * float(base["objects_per_meeting"]) for f in CONCEPT_VOLUME_BAND
    )
    volume = float(cand["objects_per_meeting"])
    if not low <= volume <= high_band:
        reasons.append(f"V4 volume {volume:.2f} outside [{low:.2f}, {high_band:.2f}]")
    verb_c, verb_b = _rate(cand["verbatim"]), _rate(base["verbatim"])
    if verb_c > verb_b + CONCEPT_VERBATIM_MARGIN:
        reasons.append(f"V5 verbatim {verb_c:.3f}")
    g1 = scores.get("g1")
    entry = g1.get(model) if isinstance(g1, Mapping) else None
    if not isinstance(entry, Mapping) or "dprecision" not in entry:
        reasons.append("G1 not measured")
    elif float(entry["dprecision"]["low"]) < CONCEPT_G1_FLOOR:
        reasons.append(f"G1 precision lower bound {entry['dprecision']['low']:+.3f}")
    return {"passes": not reasons, "reasons": reasons}


# --- shortening --------------------------------------------------------------


def shorten(
    parts: Mapping[str, float],
    steps: Sequence[tuple[str, str, float]],
    *,
    budget_h: float,
) -> tuple[list[str], float]:
    """Apply the registered steps IN ORDER until the forecast fits the
    budget. Each step scales one part's forecast by its factor. Returns the
    steps taken and the final forecast, which may still overrun."""
    current = dict(parts)
    taken: list[str] = []
    for label, part, factor in steps:
        if sum(current.values()) <= budget_h:
            break
        current[part] *= factor
        taken.append(label)
    return taken, sum(current.values())


# --- #1587: the sufficiency swap on the 32 GB floor ---------------------------

FLOOR_WARM_RATIO: Final = 1.5
FLOOR_COLD_S: Final = 30.0
"""ADR-0076's adoption bar, as #1585 applied it: arm (b)'s warm median per
question at most 1.5x arm (a)'s, and its cold first question at most 30 s."""


def floor_verdict(results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """#1585's statistics over `swap_measure`-shaped sequences: the cold
    question is the median of each arm's first questions, warm is the median
    of every later question pooled over the arm's sequences. A question
    that exited non-zero voids the run rather than counting as a time."""
    cold: dict[str, list[float]] = {}
    warm: dict[str, list[float]] = {}
    for seq in results:
        arm = str(seq["arm"])
        for item in seq["times"]:
            if int(item["rc"]) != 0:
                raise ValueError(
                    f"arm {arm} question {item['q']} exit code {item['rc']}"
                )
            bucket = cold if int(item["q"]) == 0 else warm
            bucket.setdefault(arm, []).append(float(item["seconds"]))
    cold_s = {arm: statistics.median(v) for arm, v in sorted(cold.items())}
    warm_s = {arm: statistics.median(v) for arm, v in sorted(warm.items())}
    ratio = warm_s["b"] / warm_s["a"]
    reasons: list[str] = []
    if cold_s["b"] > FLOOR_COLD_S:
        reasons.append(f"cold {cold_s['b']:.2f} s > {FLOOR_COLD_S:.0f} s")
    if ratio > FLOOR_WARM_RATIO:
        reasons.append(f"warm ratio {ratio:.2f} > {FLOOR_WARM_RATIO}")
    return {
        "cold_s": cold_s,
        "warm_median_s": warm_s,
        "warm_ratio": ratio,
        "passes": not reasons,
        "reasons": reasons,
    }
