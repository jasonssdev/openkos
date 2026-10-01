"""The unattended spend budget: counting, admission and the day's sum.

Model spend is bounded where nobody is there to answer a cost gate. The unit
is the chat CALL, because that is what the existing cost probes measure
exactly: `CountingBackend` counts every `chat` a run issues (retries and
re-asks included, a call that raised included -- it was still issued) and never
an embedding. Admission is by ESTIMATE (a unit whose estimate exceeds what
remains is never started); accounting is by the COUNT (the calls actually made).

The daily total is the sum of `chat_calls` over the jobs that started on the
current local calendar day, read from `jobs.db` (`state/jobs.py`), which is
disposable: an absent record is no history and zero spent, with no error and
no file created; a record that exists but cannot be read refuses the run before
any client exists, naming deletion as the remedy, rather than guessing the
spend.

**Runner-only.** The wrapper is installed only by the job runner, through
`start_budgeted_run`: a person's CLI run, attended or `--auto` or a non-TTY
batch, is neither limited nor counted (`--auto` means "skip the question", never
"subject to the daemon's budget"). No `cli/` or `mcp/` module may bind this
module; `tests/unit/application/test_budget.py` pins that by AST, so a
convenient future import fails a test instead of silently budgeting a verb.

The runner records each job's `started_at` as a UTC `YYYY-MM-DDTHH:MM:SSZ`
string, the format `local_day_start_utc` produces, so the day boundary is a
plain string comparison in `jobs.chat_calls_since`.
"""

import sqlite3
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, time
from pathlib import Path
from typing import Any

from openkos import config
from openkos.llm.base import LLMBackend, Message
from openkos.state import jobs

JOBS_DB_NAME = "jobs.db"
"""The job record's file name under `.openkos/`."""

PASS_LIMIT_KEY = "max_calls_per_pass"  # noqa: S105 -- a config key, not a password
DAY_LIMIT_KEY = "max_calls_per_day"
"""The `unattended:` key a refusal names, also the `detail_code` a job records
for `budget_exhausted`."""


class BudgetRefused(Exception):
    """A budgeted run was refused before any model call."""


class SpendRecordUnreadable(BudgetRefused):
    """`jobs.db` exists but cannot be read, so today's spend is unknown.

    The run makes no model call: guessing the day's spend from a corrupt file is
    worse than a reset the user chooses, so the message names deleting the
    record (disposable operational state) as the remedy. It is never deleted or
    repaired on the user's behalf."""

    def __init__(self, record: Path, reason: str) -> None:
        self.record = record
        self.reason = reason
        super().__init__(
            f"an unattended run made no model call: its spend record {record} "
            f"exists but cannot be read ({reason}). It is disposable job "
            "history, so delete it to start a fresh one (this loses only the "
            "job history and today's call count)."
        )


class CallCounter:
    """A thread-safe count of chat calls, shared by every client a run builds
    (extraction may call one backend from several threads)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._count = 0

    def add(self) -> None:
        with self._lock:
            self._count += 1

    @property
    def count(self) -> int:
        with self._lock:
            return self._count


class CountingBackend:
    """Wraps an `LLMBackend` and counts its `chat` calls into a `CallCounter`.

    The call is counted BEFORE it is delegated, so one that raises still counts
    (a retry is a new call). Every other attribute -- `locality`, `embed`,
    `model` -- is forwarded untouched, so the confidential local exemption is
    still decided from the real client and embeddings stay uncounted."""

    def __init__(self, inner: LLMBackend, counter: CallCounter) -> None:
        self._inner = inner
        self._counter = counter

    @property
    def calls(self) -> int:
        return self._counter.count

    def chat(self, messages: Sequence[Message]) -> str:
        self._counter.add()
        return self._inner.chat(messages)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def local_day_start_utc(now: datetime) -> str:
    """Local midnight of `now`'s calendar day as a UTC `...Z` timestamp.

    `now` MUST be timezone-aware and in the LOCAL zone (the runner passes
    `datetime.now().astimezone()`); the day is the calendar day in that zone."""
    if now.tzinfo is None:
        raise ValueError("`now` must carry a timezone to find the local day")
    midnight = datetime.combine(now.date(), time.min, tzinfo=now.tzinfo)
    return midnight.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _daily_calls_spent(layout: config.WorkspaceLayout, now: datetime) -> int:
    record = layout.openkos_dir / JOBS_DB_NAME
    try:
        conn = jobs.open_jobs_read_only(record)
        if conn is None:
            return 0
        try:
            return jobs.chat_calls_since(conn, local_day_start_utc(now))
        finally:
            conn.close()
    except jobs.JobsStoreUnreadableError as exc:
        raise SpendRecordUnreadable(record, exc.reason) from exc
    except sqlite3.Error as exc:
        raise SpendRecordUnreadable(record, str(exc)) from exc


@dataclass(frozen=True)
class Admission:
    """Whether a unit of work may start, and if not, why."""

    admitted: bool
    limit: str | None = None
    """The `unattended:` key that refused it (`max_calls_per_pass` or
    `max_calls_per_day`); `None` when admitted."""
    never_fits: bool = False
    """The estimate exceeds the whole PASS budget, so no later pass can run it
    either: the caller records it for a person to ingest by hand."""


class BudgetedRun:
    """One budgeted pass: its limits, what the day had already spent, and the
    counter every client of the pass shares. Built only by
    `start_budgeted_run`."""

    def __init__(
        self,
        unattended: config.UnattendedConfig,
        spent_today_at_start: int,
        counter: CallCounter,
    ) -> None:
        self._unattended = unattended
        self.spent_today_at_start = spent_today_at_start
        self._counter = counter

    @property
    def spent(self) -> int:
        """Chat calls this pass has made: the number a job records."""
        return self._counter.count

    def _pass_left(self) -> int:
        return self._unattended.max_calls_per_pass - self.spent

    def _day_left(self) -> int:
        return (
            self._unattended.max_calls_per_day - self.spent_today_at_start - self.spent
        )

    @property
    def remaining(self) -> int:
        """Calls this pass may still make: the tighter of the pass and the day,
        never below zero (a source's retry slack can overrun its estimate)."""
        return max(0, min(self._pass_left(), self._day_left()))

    def admit(self, estimate: int) -> Admission:
        """Admit a unit by its call ESTIMATE, whole: a unit whose estimate
        exceeds what remains in the pass or the day is not started."""
        pass_left, day_left = self._pass_left(), self._day_left()
        if estimate <= pass_left and estimate <= day_left:
            return Admission(admitted=True)
        # Name the limit that binds: the pass when it is the tighter one (a tie
        # goes to the pass, the one a person tunes per run).
        limit = PASS_LIMIT_KEY if pass_left <= day_left else DAY_LIMIT_KEY
        return Admission(
            admitted=False,
            limit=limit,
            never_fits=estimate > self._unattended.max_calls_per_pass,
        )

    def counting(self, inner: LLMBackend) -> CountingBackend:
        """Wrap `inner` so its chat calls count against this pass."""
        return CountingBackend(inner, self._counter)

    def wrap_chat_client(
        self, build: Callable[..., LLMBackend]
    ) -> Callable[..., CountingBackend]:
        """Wrap a service's `chat_client` port (whatever its arity) so every
        client it builds counts into this pass's one counter."""

        def counted(*args: Any, **kwargs: Any) -> CountingBackend:
            return self.counting(build(*args, **kwargs))

        return counted


def start_budgeted_run(
    layout: config.WorkspaceLayout,
    unattended: config.UnattendedConfig,
    now: datetime,
) -> BudgetedRun:
    """Begin a budgeted pass: the factory the job runner calls, and nothing else.

    Reads today's spend from `jobs.db` (absent: zero; unreadable:
    `SpendRecordUnreadable`, before any client exists). `now` must be
    timezone-aware and local (see `local_day_start_utc`)."""
    return BudgetedRun(unattended, _daily_calls_spent(layout, now), CallCounter())
