"""The deterministic half of the Ask model screen (#1560).

Four pieces a paid run rests on and no model can exercise:

- **The generation cache.** Every chat call is keyed by the sha256 of the
  exact request (model tag and digest, messages, sampling options, seed) and
  stored OUTSIDE the repository, so a rescore never calls a model and a second
  screen that sends the same bytes pays nothing.
- **The per-call timeout rule.** A call that runs past its budget is a
  FAILURE of the model under test, never a retry: a reply that arrives late
  still counts as a timeout. A timeout is cached too, and is reused only under
  a budget no larger than the one it ran out of. An ordinary error (refused
  connection, a 500) is not cached, because it says nothing about the model.
- **The paired item-level bootstrap.** Every model answers identical items,
  so the comparison is the per-item difference, resampled over items with a
  fixed seed. Unpaired item sets are refused rather than silently intersected.
- **Successive halving.** The pre-registered cut between rounds, and the
  final selection rule.

Standard library only and NO `sys.path` surgery, like `harness_report.py`, so
`tests/unit/test_ask_screen_core.py` loads it straight from its path.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import pathlib
import random
import tempfile
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any, Final

OUTCOME_OK: Final = "ok"
OUTCOME_TIMEOUT: Final = "timeout"
OUTCOME_ERROR: Final = "error"

DEFAULT_RESAMPLES: Final = 10_000
DEFAULT_SEED: Final = 42
"""The bootstrap constants #1557's design and the thesis's ADR-0011 use."""


def cache_key(payload: Mapping[str, Any]) -> str:
    """sha256 hex of `payload` as canonical JSON (sorted keys, no spaces).

    Canonical so that two requests equal as data hash equal regardless of the
    order a caller happened to build the mapping in."""
    text = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CallRecord:
    """One chat call as it happened: what came back, how long it took, and
    the budget it ran under."""

    outcome: str
    reply: str | None
    elapsed_s: float
    timeout_s: float
    error: str | None = None


def classify(record: CallRecord, timeout_s: float) -> str:
    """The outcome under budget `timeout_s`.

    A reply that arrived after the budget is a timeout: the screen's rule is
    about the model's wall-clock, not about whether the client happened to
    wait. Classified at READ time, so a cached reply measured under a larger
    budget is judged by the budget of the run reading it."""
    if record.outcome == OUTCOME_OK and record.elapsed_s > timeout_s:
        return OUTCOME_TIMEOUT
    return record.outcome


def reusable(record: CallRecord, timeout_s: float) -> bool:
    """Whether a cached record answers a call made under `timeout_s`.

    A reply always does (`classify` applies the budget). A timeout does only
    when the new budget is no larger than the one it ran out of: under a
    larger budget the call might have finished. An error never does."""
    if record.outcome == OUTCOME_OK:
        return True
    if record.outcome == OUTCOME_TIMEOUT:
        return record.timeout_s >= timeout_s
    return False


class GenerationCache:
    """One JSON file per call under `root/generations/<key[:2]>/<key>.json`.

    Writes are atomic (temp file + rename), so an interrupted run never
    leaves a half-written record that a later run would read as a reply."""

    def __init__(self, root: pathlib.Path) -> None:
        self.root = root

    def path_for(self, key: str) -> pathlib.Path:
        return self.root / "generations" / key[:2] / f"{key}.json"

    def get(self, key: str) -> CallRecord | None:
        path = self.path_for(key)
        if not path.is_file():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        return CallRecord(**payload["record"])

    def put(self, key: str, record: CallRecord, *, request: Mapping[str, Any]) -> None:
        path = self.path_for(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        body = json.dumps(
            {"key": key, "request": dict(request), "record": asdict(record)},
            ensure_ascii=False,
            indent=1,
        )
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(body)
            pathlib.Path(tmp).replace(path)
        except BaseException:
            pathlib.Path(tmp).unlink(missing_ok=True)
            raise


class ScreenCallFailed(Exception):
    """A call that failed under the screen's rules: a timeout or an error.

    The caller scores the item as a failure (zero) and counts it; it never
    retries, because the timeout is the experiment's own rule."""

    def __init__(self, record: CallRecord, outcome: str) -> None:
        super().__init__(f"{outcome}: {record.error or 'past the budget'}")
        self.record = record
        self.outcome = outcome


class CacheMiss(Exception):
    """Raised in offline mode: the call is not cached and no model may run."""


class CachedChat:
    """An `LLMBackend`-shaped wrapper: cache first, then the inner client
    under the timeout rule.

    `identity` is everything about the call that is not the messages -- model
    tag, digest, seed and sampling options -- and is part of the key, so two
    seeds or two digests of one tag never share a reply. `offline=True`
    refuses every miss, which is how a rescore proves it calls nothing.

    Single-threaded by design: the screen runs one call at a time, because a
    default Ollama serializes anyway and a concurrent call would distort the
    latency being measured."""

    def __init__(
        self,
        inner: Any,
        *,
        cache: GenerationCache,
        identity: Mapping[str, Any],
        timeout_s: float,
        clock: Callable[[], float] = time.monotonic,
        is_timeout: Callable[[BaseException], bool] = lambda _exc: False,
        offline: bool = False,
    ) -> None:
        self._inner = inner
        self._cache = cache
        self._identity = dict(identity)
        self._timeout_s = timeout_s
        self._clock = clock
        self._is_timeout = is_timeout
        self._offline = offline
        self.hits = 0
        self.misses = 0
        self.last_record: CallRecord | None = None

    def request_for(self, messages: Sequence[Any]) -> dict[str, Any]:
        return {"identity": self._identity, "messages": [dict(m) for m in messages]}

    def chat(self, messages: Sequence[Any]) -> str:
        request = self.request_for(messages)
        key = cache_key(request)
        cached = self._cache.get(key)
        if cached is not None and reusable(cached, self._timeout_s):
            self.hits += 1
            return self._settle(cached)
        if self._offline:
            raise CacheMiss(key)
        self.misses += 1
        started = self._clock()
        try:
            reply = str(self._inner.chat(messages))
        except Exception as exc:
            elapsed = self._clock() - started
            outcome = OUTCOME_TIMEOUT if self._is_timeout(exc) else OUTCOME_ERROR
            record = CallRecord(
                outcome=outcome,
                reply=None,
                elapsed_s=round(elapsed, 3),
                timeout_s=self._timeout_s,
                error=f"{type(exc).__name__}: {exc}",
            )
            if outcome == OUTCOME_TIMEOUT:
                self._cache.put(key, record, request=request)
            self.last_record = record
            raise ScreenCallFailed(record, outcome) from exc
        elapsed = self._clock() - started
        record = CallRecord(
            outcome=OUTCOME_OK,
            reply=reply,
            elapsed_s=round(elapsed, 3),
            timeout_s=self._timeout_s,
        )
        self._cache.put(key, record, request=request)
        return self._settle(record)

    def _settle(self, record: CallRecord) -> str:
        self.last_record = record
        outcome = classify(record, self._timeout_s)
        if outcome != OUTCOME_OK or record.reply is None:
            raise ScreenCallFailed(record, outcome)
        return record.reply

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


# --------------------------------------------------------------------------- #
# Paired item-level bootstrap                                                  #
# --------------------------------------------------------------------------- #


def item_means(pairs: Iterable[tuple[str, float]]) -> dict[str, float]:
    """Mean value per item over its runs: runs are repeated measurements of
    one item, never independent items."""
    sums: dict[str, float] = {}
    counts: dict[str, int] = {}
    for item, value in pairs:
        sums[item] = sums.get(item, 0.0) + value
        counts[item] = counts.get(item, 0) + 1
    return {item: sums[item] / counts[item] for item in sums}


@dataclass(frozen=True)
class PairedDelta:
    """Candidate minus baseline: the mean per-item difference and its
    percentile bootstrap interval."""

    n: int
    mean: float
    lo: float
    hi: float


def paired_bootstrap(
    base: Mapping[str, float],
    cand: Mapping[str, float],
    *,
    resamples: int = DEFAULT_RESAMPLES,
    seed: int = DEFAULT_SEED,
    alpha: float = 0.05,
) -> PairedDelta:
    """Resample ITEMS with replacement and average the per-item difference.

    Refuses item sets that differ: intersecting them silently would compare
    a model on the items it happened to finish, which is the selection a
    failure-as-zero rule exists to prevent."""
    if set(base) != set(cand):
        missing = sorted(set(base) ^ set(cand))
        raise ValueError(f"not paired: items differ between arms: {missing[:5]}")
    if not base:
        raise ValueError("no items to compare")
    items = sorted(base)
    diffs = [cand[i] - base[i] for i in items]
    n = len(diffs)
    mean = sum(diffs) / n
    rng = random.Random(seed)  # noqa: S311 -- bootstrap resampling, not secrets
    means = sorted(sum(rng.choices(diffs, k=n)) / n for _ in range(resamples))
    lo_index = math.floor(alpha / 2 * resamples)
    hi_index = max(lo_index, math.ceil((1 - alpha / 2) * resamples) - 1)
    return PairedDelta(n=n, mean=mean, lo=means[lo_index], hi=means[hi_index])


# --------------------------------------------------------------------------- #
# Successive halving and selection                                             #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Standing:
    """One candidate after a round: its paired delta on the role's primary
    metric and the vetoes it failed (empty when it failed none)."""

    model: str
    delta: PairedDelta
    vetoes: tuple[str, ...]


def halving_cut(
    standings: Sequence[Standing], *, keep_min: int = 2
) -> tuple[list[str], dict[str, str]]:
    """The pre-registered cut between rounds.

    1. A candidate failing any veto is dropped.
    2. A candidate whose 95% upper bound is below zero is dropped: it is a
       clear loser at this n.
    3. Of the rest, the top `ceil(k / 2)` by point delta are kept, where `k`
       is the number that ENTERED the round, and never fewer than `keep_min`
       when that many are eligible. Ties break by model name.
    """
    dropped: dict[str, str] = {}
    eligible: list[Standing] = []
    for s in standings:
        if s.vetoes:
            dropped[s.model] = "veto: " + ", ".join(s.vetoes)
        elif s.delta.hi < 0:
            dropped[s.model] = f"upper bound {s.delta.hi:+.3f} < 0"
        else:
            eligible.append(s)
    eligible.sort(key=lambda s: (-s.delta.mean, s.model))
    keep = max(math.ceil(len(standings) / 2), min(keep_min, len(eligible)))
    kept = [s.model for s in eligible[:keep]]
    for s in eligible[keep:]:
        dropped[s.model] = f"halving: rank below the top {keep}"
    return kept, dropped


def select(standings: Sequence[Standing]) -> str | None:
    """The winner: the largest point delta among candidates with no veto and
    a 95% lower bound above zero. `None` means the baseline stays."""
    winners = [s for s in standings if not s.vetoes and s.delta.lo > 0]
    if not winners:
        return None
    return min(winners, key=lambda s: (-s.delta.mean, s.model)).model


def forecast_hours(
    *, items: int, runs: int, calls_per_item: int, s_per_call: float
) -> float:
    """Model wall-clock for one model: calls x seconds per call, in hours."""
    return items * runs * calls_per_item * s_per_call / 3600
