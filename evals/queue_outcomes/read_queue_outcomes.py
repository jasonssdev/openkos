"""How much of the pending-work queue is applied as proposed (#1214).

The roadmap asks how mechanical the curation queue is before anything is
automated on top of it. The queue already records, per row, how a human path
closed it (`resolution`: `as_proposed`, `modified`, `declined` or `stale`;
`resolved_by`: the verb; `created_at` / `resolved_at`). `openkos pending
--stats` prints lifetime counts of that. This reader adds what `--stats` does
not give: a stated period, the `resolved_by` split, and, for every kind, a
statement of whether `as_proposed` could have been anything else.

READ-ONLY. The store is opened with `mode=ro`; a missing or unreadable queue is
reported, never created. Standard library only; it never calls a model.

THE COHORT. A row belongs to the period when its `created_at` falls inside it,
so every row lands in exactly ONE outcome and the outcomes sum to the cohort:

- `as_proposed`: applied, and the applied change equals the proposal;
- `modified`: applied, with a different value than proposed;
- `declined`: a keep-distinct, a decline, or an `adjudicate` DIFFERENT verdict;
- `stale`: the engine retired it (its inputs changed or a concept vanished);
- `open`: still pending or claimed. A row a human SKIPPED (`s` at the prompt)
  is not recorded as skipped: skipping leaves the row open, so `open` is
  "unresolved so far", skipped and never-looked-at together.

Every rate is printed as `n of TOTAL`, never as a bare percentage. The headline
fraction is `as_proposed / (as_proposed + modified + declined)`: of the rows a
human actually answered. Stale and open rows are outside it and are shown.

WHEN `as_proposed` MEANS NOTHING. A kind whose proposal carries no value the
human could change can only ever read `as_proposed` (a contradiction says
"reconcile this pair" and the direction is the human's; a watch refusal is
answered by the ingest itself). Its fraction is vacuous and the report says so
instead of printing a flattering 100%. `revision` has no resolving write path
today, so its rows only ever end `stale` or stay `open`.

LIFETIME CAVEAT. The queue is a derived cache: a rebuild or `purge` resets it,
so the history here covers only the queue's current lifetime.

Usage:

    uv run python evals/queue_outcomes/read_queue_outcomes.py WORKSPACE
    uv run python evals/queue_outcomes/read_queue_outcomes.py WORKSPACE \\
        --since 2026-10-01 --until 2026-11-01
    uv run python evals/queue_outcomes/read_queue_outcomes.py --self-test
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

KINDS = (
    "identity",
    "relation_type",
    "volatility",
    "contradiction",
    "revision",
    "watch_refusal",
)

VACUOUS_AS_PROPOSED = {
    "contradiction": "the row proposes no value; any direction is as_proposed",
    "watch_refusal": "ingest answers it; there is no proposed value to change",
    "revision": "no write path resolves it; it only ends stale or stays open",
}
"""Kinds whose `as_proposed` fraction cannot discriminate, and why. Mirrors
`application/queue_resolution.py`; the README names it as the thing to re-read
when that module changes."""

OUTCOMES = ("as_proposed", "modified", "declined", "stale", "open")

_FINDINGS_RELATIVE = Path(".openkos") / "findings.db"


@dataclass
class KindOutcomes:
    kind: str
    counts: dict[str, int] = field(default_factory=lambda: dict.fromkeys(OUTCOMES, 0))
    by_resolver: dict[str, dict[str, int]] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    @property
    def answered(self) -> int:
        c = self.counts
        return c["as_proposed"] + c["modified"] + c["declined"]


@dataclass(frozen=True)
class Reading:
    """What the queue holds, or why it could not be read."""

    status: str  # "ok" | "absent" | "no-queue" | "unreadable"
    kinds: tuple[KindOutcomes, ...] = ()
    unclassified: int = 0
    """Rows whose status/resolution pair matched no outcome. Must be 0."""


def _outcome(status: str, resolution: str | None) -> str | None:
    if status in ("pending", "claimed"):
        return "open"
    if status == "stale":
        return "stale"
    if status == "declined":
        return "declined" if resolution == "declined" else None
    if status == "applied" and resolution in ("as_proposed", "modified"):
        return resolution
    return None


def read_outcomes(
    db_path: Path, *, since: str | None = None, until: str | None = None
) -> Reading:
    """Per-kind outcomes of the rows created in `[since, until)` (ISO strings
    compared lexically against `created_at`, which the queue stores as ISO UTC)."""
    try:
        present = db_path.is_file()
    except OSError:
        present = False
    if not present:
        return Reading("absent")
    try:
        conn = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
    except sqlite3.Error:
        return Reading("unreadable")
    try:
        conn.execute("PRAGMA query_only = ON")
        has_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='pending_items'"
        ).fetchone()
        if not has_table:
            return Reading("no-queue")
        clauses: list[str] = []
        params: list[str] = []
        if since is not None:
            clauses.append("created_at >= ?")
            params.append(since)
        if until is not None:
            clauses.append("created_at < ?")
            params.append(until)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = conn.execute(
            "SELECT kind, status, resolution, resolved_by, COUNT(*)"  # noqa: S608
            f" FROM pending_items{where}"
            " GROUP BY kind, status, resolution, resolved_by",
            params,
        ).fetchall()
    except sqlite3.Error:
        return Reading("unreadable")
    finally:
        conn.close()
    by_kind: dict[str, KindOutcomes] = {}
    unclassified = 0
    for kind, status, resolution, resolved_by, n in rows:
        outcome = _outcome(status, resolution)
        if outcome is None:
            unclassified += n
            continue
        entry = by_kind.setdefault(kind, KindOutcomes(kind))
        entry.counts[outcome] += n
        if resolved_by:
            split = entry.by_resolver.setdefault(
                resolved_by, dict.fromkeys(OUTCOMES, 0)
            )
            split[outcome] += n
    ordered = [by_kind[k] for k in KINDS if k in by_kind]
    ordered += [by_kind[k] for k in sorted(by_kind) if k not in KINDS]
    return Reading("ok", tuple(ordered), unclassified)


def render(reading: Reading, *, since: str | None, until: str | None) -> str:
    window = f"created in [{since or 'the start'}, {until or 'now'})"
    lines = [
        "# Pending-work queue outcomes (#1214)",
        "",
        f"Cohort: rows {window}. Lifetime caveat: the queue is a derived cache;"
        " a rebuild or purge resets this history.",
        "",
    ]
    if reading.status != "ok":
        lines.append(f"Queue not readable: {reading.status}. Nothing measured.")
        return "\n".join(lines) + "\n"
    if not reading.kinds:
        lines.append("No rows in the cohort. Nothing measured.")
        return "\n".join(lines) + "\n"
    lines += [
        "| kind | rows | as proposed | modified | declined | stale"
        " | open (incl. skipped) |",
        "|---|---|---|---|---|---|---|",
    ]
    for k in reading.kinds:
        c = k.counts
        lines.append(
            f"| {k.kind} | {k.total} | {c['as_proposed']} | {c['modified']}"
            f" | {c['declined']} | {c['stale']} | {c['open']} |"
        )
    lines += ["", "## As proposed, of the rows a human answered", ""]
    for k in reading.kinds:
        head = (
            f"- {k.kind}: as proposed {k.counts['as_proposed']} of {k.answered}"
            " answered"
        )
        if k.kind in VACUOUS_AS_PROPOSED:
            lines.append(f"{head} -- VACUOUS: {VACUOUS_AS_PROPOSED[k.kind]}.")
        elif k.answered == 0:
            lines.append(f"{head} -- nothing answered yet; no reading.")
        else:
            lines.append(
                f"{head}; modified {k.counts['modified']} of {k.answered};"
                f" declined {k.counts['declined']} of {k.answered}."
            )
    lines += ["", "## By resolving verb", ""]
    for k in reading.kinds:
        for verb in sorted(k.by_resolver):
            s = k.by_resolver[verb]
            lines.append(
                f"- {k.kind} / {verb}: as proposed {s['as_proposed']},"
                f" modified {s['modified']}, declined {s['declined']},"
                f" stale {s['stale']}"
            )
    if reading.unclassified:
        lines += ["", f"WARNING: {reading.unclassified} row(s) matched no outcome."]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- self-test

_Row = tuple[str, str, str | None, str | None, str]


def _synthetic_db(path: Path, rows: Sequence[_Row]) -> None:
    """Build the queue through the ENGINE's own schema (so schema drift fails the
    self-test), then insert `(kind, status, resolution, resolved_by, created_at)`."""
    from openkos.state import pending_queue as pq

    conn = sqlite3.connect(path)
    try:
        pq.ensure_schema(conn)
        for i, (kind, status, resolution, by, created) in enumerate(rows):
            conn.execute(
                "INSERT INTO pending_items (decision_key, kind, producer, payload,"
                " payload_digest, status, resolution, resolved_by, created_at,"
                " last_seen_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    f"k{i}",
                    kind,
                    "p/1",
                    "{}",
                    "d",
                    status,
                    resolution,
                    by,
                    created,
                    created,
                ),
            )
        conn.commit()
    finally:
        conn.close()


def _self_test() -> int:
    failures: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        if not ok:
            failures.append(f"{name} {detail}".strip())

    jan, feb = "2026-01-10T00:00:00+00:00", "2026-02-10T00:00:00+00:00"
    rows: list[_Row] = [
        ("identity", "applied", "as_proposed", "merge", jan),
        ("identity", "applied", "as_proposed", "merge", jan),
        ("identity", "applied", "modified", "merge", jan),
        ("identity", "declined", "declined", "keep-distinct", jan),
        ("identity", "stale", "stale", None, jan),
        ("identity", "pending", None, None, jan),
        ("identity", "claimed", None, None, feb),
        ("volatility", "applied", "modified", "set-volatility", feb),
        ("contradiction", "applied", "as_proposed", "reconcile", jan),
        ("revision", "stale", "stale", None, jan),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "findings.db"
        _synthetic_db(db, rows)
        before = db.read_bytes()

        r = read_outcomes(db)
        check("status", r.status == "ok", r.status)
        k = {x.kind: x for x in r.kinds}
        ident = k["identity"].counts
        expected = {
            "as_proposed": 2,
            "modified": 1,
            "declined": 1,
            "stale": 1,
            "open": 2,
        }
        check("identity counts", ident == expected, str(ident))
        check("identity total is the cohort", k["identity"].total == 7)
        check("answered excludes stale and open", k["identity"].answered == 4)
        order = [x.kind for x in r.kinds]
        check(
            "order",
            order == ["identity", "volatility", "contradiction", "revision"],
            str(order),
        )
        check("unclassified", r.unclassified == 0)
        check("verb split", k["identity"].by_resolver["merge"]["modified"] == 1)

        # The window is on created_at, half-open.
        w = read_outcomes(db, since="2026-02-01", until="2026-03-01")
        wk = {x.kind: x for x in w.kinds}
        check("window kinds", set(wk) == {"identity", "volatility"}, str(set(wk)))
        check(
            "window identity",
            wk["identity"].total == 1 and wk["identity"].counts["open"] == 1,
        )
        edge = read_outcomes(db, since="2026-01-10T00:00:00+00:00", until=feb)
        check("since inclusive, until exclusive", edge.kinds[0].total == 6, str(edge))
        none = read_outcomes(db, since="2027-01-01")
        check("empty window", none.status == "ok" and none.kinds == ())

        text = render(r, since=None, until=None)
        check("denominator printed", "as proposed 2 of 4 answered" in text, text)
        check(
            "vacuous flagged",
            "contradiction: as proposed 1 of 1 answered -- VACUOUS" in text,
        )
        check(
            "revision flagged",
            "revision: as proposed 0 of 0 answered -- VACUOUS" in text,
        )
        check("no bare percent", "%" not in text)
        check("skipped is named", "open (incl. skipped)" in text)
        check(
            "volatility reading",
            "volatility: as proposed 0 of 1 answered; modified 1 of 1" in text,
            text,
        )

        # Read-only: reading never changes the file, a missing store is not created.
        check("db untouched", db.read_bytes() == before)
        gone = Path(tmp) / "nowhere" / "findings.db"
        check("absent", read_outcomes(gone).status == "absent" and not gone.exists())
        empty = Path(tmp) / "empty.db"
        sqlite3.connect(empty).close()
        check("no queue table", read_outcomes(empty).status == "no-queue")
        junk = Path(tmp) / "junk.db"
        junk.write_bytes(b"not a database" * 100)
        check("unreadable", read_outcomes(junk).status == "unreadable")
        check(
            "render absent",
            "Nothing measured" in render(read_outcomes(gone), since=None, until=None),
        )

        # A row of a shape no outcome covers is surfaced, never dropped.
        odd = Path(tmp) / "odd.db"
        _synthetic_db(odd, [("identity", "applied", "declined", "merge", jan)])
        check("unclassified surfaced", read_outcomes(odd).unclassified == 1)

    print("queue_outcomes self-test:", "FAIL" if failures else "PASS")
    for f in failures:
        print("  -", f)
    return 1 if failures else 0


def _parse_day(value: str) -> str:
    try:
        datetime.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"not an ISO date: {value!r}") from exc
    return value


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("workspace", nargs="?", type=Path)
    parser.add_argument("--since", type=_parse_day, help="ISO date, inclusive")
    parser.add_argument("--until", type=_parse_day, help="ISO date, exclusive")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        return _self_test()
    if args.workspace is None:
        parser.error("WORKSPACE is required (or use --self-test)")
    reading = read_outcomes(
        args.workspace / _FINDINGS_RELATIVE, since=args.since, until=args.until
    )
    sys.stdout.write(render(reading, since=args.since, until=args.until))
    sys.stdout.write(f"\nRead at {datetime.now(UTC).isoformat(timespec='seconds')}.\n")
    return 0 if reading.status == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
