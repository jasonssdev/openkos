"""Deterministic scoring for the transcript decision pass harness (#1348).

Pure functions over plain data, standard library only (no `openkos` import),
so the harness's `--self-test` pins every metric without a model.

THE DEVELOPMENT-SERIES ALLOWLIST. `DEV_SERIES` is a fail-closed constant, not
a flag. The maintainer's thesis (ADR-0005 in that repository) splits the AMI
series into DEVELOPMENT (ES2015, IS1004, TS3009) and EVALUATION (eleven
others, including TS3005). This harness exists to tune and decide OpenKOS
behaviour, so scoring an evaluation-series meeting here would spend the
thesis's held-out data on a tuning decision. Anything not on the list --
including a meeting id that does not parse -- is refused.

THE MATCHING RULE. A produced Decision points at a gold dialogue act when the
two share a contiguous run of word tokens that is at least `MIN_MATCH_TOKENS`
long AND covers at least `MIN_MATCH_COVERAGE` of the SHORTER side. AMI's
dialogue-act segmentation and the rendered transcript's turn segmentation cut
the same words differently, so neither side need contain the other; full
containment is the special case of 100% coverage. Tokens are casefolded
alphanumeric words (apostrophes kept inside a word), so AMI's tokenised text
("it 's a remote control .", with `<gap>` markers) and the rendered transcript
("it's a remote control.") compare equal. This is `decision_pass`'s own
"casefold, collapse whitespace" rule, widened only by what the two renderings
of the same words differ in (punctuation spacing, gap tags, speaker labels).
"""

from __future__ import annotations

import re
import statistics
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

DEV_SERIES: Final = frozenset({"ES2015", "IS1004", "TS3009"})
"""The ONLY series this harness may score. See the module docstring."""

MIN_MATCH_TOKENS: Final = 4
"""Floor on the shorter side of a match. AMI dialogue acts include "yeah",
"okay" and "um , right": any of them is contained in a great many evidence
lines, so containment of one proves nothing. Four tokens is the shortest span
that still names a position ("we 'll use rubber"), and every decision act in
the ES2015 gold is longer; an act shorter than the floor can therefore never
be recalled, which is stated rather than hidden -- it costs recall, never
precision, which is the safe direction for a harness deciding whether to ship."""

MIN_MATCH_COVERAGE: Final = 0.6
"""Share of the SHORTER side's tokens the longest common run must cover. The
run floor alone would let four shared words of a long line count; coverage
ties the match to the act or evidence line being the same utterance cut
differently, not merely sharing a phrase."""

PARTIAL_STAMP: Final = "PARTIAL -- NOT THE PRE-REGISTERED RESULT"

STATUSES: Final = ("decision", "no_decision", "sin_soporte", "compuesta", "descartar")
NON_DECISION: Final = frozenset({"no_decision", "sin_soporte"})

_SERIES_RE: Final = re.compile(r"^([A-Z]{2}\d{4})[a-z]?$")
_GAP_RE: Final = re.compile(r"<[^<>]*>")
_SPEAKER_RE: Final = re.compile(r"^\s*[A-Z]{1,2}:\s+")
_TOKEN_RE: Final = re.compile(r"[^\W_]+(?:'[^\W_]+)*")


class SeriesRefused(ValueError):
    """A meeting outside the development allowlist."""


def series_of(meeting_id: str) -> str:
    match = _SERIES_RE.match(meeting_id)
    if match is None:
        raise SeriesRefused(f"{meeting_id!r} is not an AMI meeting id; refused")
    return match.group(1)


def require_dev_meetings(meeting_ids: Iterable[str]) -> None:
    """Raise `SeriesRefused` unless EVERY meeting is in a development series."""
    for meeting_id in meeting_ids:
        series = series_of(meeting_id)
        if series not in DEV_SERIES:
            raise SeriesRefused(
                f"{meeting_id} belongs to series {series}, which is not a "
                f"development series ({', '.join(sorted(DEV_SERIES))}). The thesis "
                "holds the other series out for its final evaluation; this harness "
                "tunes OpenKOS behaviour and must not touch them."
            )


# --- gold ------------------------------------------------------------------


@dataclass(frozen=True)
class GoldRow:
    decision_id: str
    meeting_id: str
    status: str
    """One of `STATUSES`, or "" when not yet adjudicated."""
    acts: tuple[tuple[str, ...], ...]
    """The dialogue acts of `evidence_text`, tokenised."""


def act_tokens(text: str) -> tuple[str, ...]:
    """Casefolded word tokens of one dialogue act or evidence line, without
    its speaker label and gap tags."""
    text = _SPEAKER_RE.sub("", text.replace("\u2019", "'"))
    text = _GAP_RE.sub(" ", text)
    return tuple(_TOKEN_RE.findall(text.casefold()))


def _longest_common_run(left: Sequence[str], right: Sequence[str]) -> int:
    """Length of the longest contiguous token run common to both sequences
    (longest common substring; O(len(left) * len(right)) time, O(len(right))
    memory)."""
    best = 0
    previous = [0] * (len(right) + 1)
    for a in left:
        current = [0] * (len(right) + 1)
        for j, b in enumerate(right, start=1):
            if a == b:
                current[j] = previous[j - 1] + 1
                best = max(best, current[j])
        previous = current
    return best


def evidence_matches(evidence: str, act: str) -> bool:
    """Whether an evidence text and a gold act contain one another; see the
    module docstring. The speaker label is stripped from each LINE of
    `evidence`, so a multi-line body is searched as one token stream."""
    left = tuple(tok for line in evidence.split("\n") for tok in act_tokens(line))
    right = act_tokens(act)
    return _tokens_match(left, right)


def _tokens_match(left: tuple[str, ...], right: tuple[str, ...]) -> bool:
    shorter = min(len(left), len(right))
    if shorter < MIN_MATCH_TOKENS:
        return False
    run = _longest_common_run(left, right)
    return run >= MIN_MATCH_TOKENS and run >= MIN_MATCH_COVERAGE * shorter


def rematch_records(
    records: Sequence[Mapping[str, Any]], rows: Sequence[GoldRow]
) -> tuple[list[dict[str, Any]], int]:
    """Copies of stored `records` with `matched_ids` recomputed under the
    CURRENT matching rule, plus how many items could not be: an item stored
    without its evidence (a baseline body is never persisted) keeps the match
    it was stored with, which may come from an older rule."""
    out: list[dict[str, Any]] = []
    kept = 0
    for record in records:
        items = []
        for item in record["items"]:
            evidence = item.get("evidence") or []
            if evidence:
                item = {**item, "matched_ids": match_item(evidence, rows)}
            else:
                kept += 1
            items.append(item)
        out.append({**record, "items": items})
    return out, kept


def load_gold(paths: Sequence[Path]) -> list[GoldRow]:
    """Rows of the adjudicated CSV(s). Only the five columns used are read;
    nothing else is kept. An unknown `final_status` is an error, not a blank."""
    import csv

    rows: list[GoldRow] = []
    for path in paths:
        with path.open(encoding="utf-8", newline="") as handle:
            for record in csv.DictReader(handle):
                status = (record.get("final_status") or "").strip()
                if status and status not in STATUSES:
                    raise SystemExit(
                        f"{path.name}: {record.get('decision_id')} has unknown "
                        f"final_status {status!r}"
                    )
                evidence = record.get("evidence_text") or ""
                acts = tuple(
                    tokens
                    for part in evidence.split(" | ")
                    if (tokens := act_tokens(part))
                )
                rows.append(
                    GoldRow(
                        decision_id=(record.get("decision_id") or "").strip(),
                        meeting_id=(record.get("meeting_id") or "").strip(),
                        status=status,
                        acts=acts,
                    )
                )
    return rows


def blank_rows(rows: Sequence[GoldRow]) -> list[str]:
    return [r.decision_id for r in rows if not r.status]


def match_item(texts: Sequence[str], rows: Sequence[GoldRow]) -> list[str]:
    """Ids of every gold row (ANY status, adjudicated or not) with an act that
    one of `texts` matches. Statuses are applied later, at scoring time, so a
    stored run can be rescored after more rows are adjudicated."""
    streams = [
        tuple(tok for line in text.split("\n") for tok in act_tokens(line))
        for text in texts
    ]
    return [
        row.decision_id
        for row in rows
        if any(_tokens_match(stream, act) for stream in streams for act in row.acts)
    ]


# --- metrics ---------------------------------------------------------------


def title_key(title: str) -> str:
    return " ".join(title.casefold().split()).rstrip(".,;:!? ")


def classify(matched_ids: Sequence[str], status_by_id: Mapping[str, str]) -> str:
    """Which bucket a produced Decision falls in. A match on a `decision` row
    wins over a match on a non-decision row: a baseline body can quote both,
    and this precedence flatters the baseline's precision, never the pass's
    (a pass item carries one evidence line). Full order: decision > non_decision
    > compuesta > descartar > unadjudicated > unannotated. `compuesta` outranks
    `descartar` because a compuesta row marks a sentence that DOES carry real
    decisions (its children hold them), while `descartar` marks a sentence with
    nothing usable; both are excluded from precision."""
    statuses = {status_by_id.get(i) for i in matched_ids}
    if "decision" in statuses:
        return "matched_decision"
    if statuses & NON_DECISION:
        return "matched_non_decision"
    if "compuesta" in statuses:
        return "matched_compuesta"
    if "descartar" in statuses:
        return "matched_descartar"
    if matched_ids:
        return "unadjudicated"
    return "unannotated"


def recall_credit(
    group_ids: Sequence[str], items_matched: Sequence[Sequence[str]]
) -> int:
    """Recall credit of one shared-evidence group: the number of `decision`
    rows in it, capped by how many DISTINCT produced Decisions matched it.
    Rows sharing identical evidence cannot be told apart by matching, so one
    Decision quoting that line would otherwise recall every one of them."""
    ids = set(group_ids)
    hits = sum(1 for matched in items_matched if ids & set(matched))
    return min(len(ids), hits)


def _ratio(num: int, den: int) -> float | None:
    return num / den if den else None


def series_run_metrics(
    records: Sequence[Mapping[str, Any]], rows: Sequence[GoldRow]
) -> dict[str, dict[str, Any]]:
    """Metrics per series for ONE run's records (all its meetings). Only
    adjudicated rows count; an item matching only a blank row is reported as
    `unadjudicated`, never as a hit or a miss."""
    status_by_id = {r.decision_id: r.status for r in rows if r.status}
    meeting_of = {r.decision_id: r.meeting_id for r in rows}
    by_series: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for record in records:
        by_series[record["series"]].append(record)
    out: dict[str, dict[str, Any]] = {}
    for series, recs in sorted(by_series.items()):
        buckets: dict[str, int] = defaultdict(int)
        titles: dict[str, set[str]] = defaultdict(set)
        for rec in recs:
            for item in rec["items"]:
                buckets[classify(item["matched_ids"], status_by_id)] += 1
                titles[title_key(item["title"])].add(rec["meeting"])
        meetings = {rec["meeting"] for rec in recs}
        in_scope = {i for i in status_by_id if meeting_of[i] in meetings}
        decision_ids = {i for i in in_scope if status_by_id[i] == "decision"}
        decision_rows = len(decision_ids)
        # Decision rows of one meeting with identical (normalized) acts form a
        # group; empty acts can never match, so they stay out of any group.
        groups: dict[tuple[str, tuple[tuple[str, ...], ...]], list[str]] = defaultdict(
            list
        )
        for r in rows:
            if r.decision_id in decision_ids and r.acts:
                groups[(r.meeting_id, r.acts)].append(r.decision_id)
        items_by_meeting: dict[str, list[Sequence[str]]] = defaultdict(list)
        for rec in recs:
            items_by_meeting[rec["meeting"]].extend(
                item["matched_ids"] for item in rec["items"]
            )
        recalled = sum(
            recall_credit(ids, items_by_meeting[meeting])
            for (meeting, _acts), ids in groups.items()
        )
        unrecallable = sum(
            1
            for r in rows
            if r.decision_id in decision_ids
            and not any(len(act) >= MIN_MATCH_TOKENS for act in r.acts)
        )
        md, mn = buckets["matched_decision"], buckets["matched_non_decision"]
        shared = {t: m for t, m in titles.items() if len(m) > 1}
        out[series] = {
            "decision_rows": decision_rows,
            "recalled": recalled,
            "unrecallable_rows": unrecallable,
            "shared_evidence_groups": sum(1 for g in groups.values() if len(g) > 1),
            "recall": _ratio(recalled, decision_rows),
            "compuesta_rows": sum(
                1 for i in in_scope if status_by_id[i] == "compuesta"
            ),
            "produced": sum(buckets.values()),
            "matched_decision": md,
            "matched_non_decision": mn,
            "matched_compuesta": buckets["matched_compuesta"],
            "matched_descartar": buckets["matched_descartar"],
            "descartar_rows": sum(
                1 for i in in_scope if status_by_id[i] == "descartar"
            ),
            "unadjudicated": buckets["unadjudicated"],
            "unannotated": buckets["unannotated"],
            "precision_on_annotated": _ratio(md, md + mn),
            "duplicate_titles": len(shared),
            "duplicate_extra": sum(len(m) - 1 for m in shared.values()),
            "windows": sum(r["windows"] for r in recs),
            "calls": sum(r["calls"] for r in recs),
            "failed_meetings": sum(1 for r in recs if r["failure"]),
            "meetings": len(recs),
            "wall_s": round(sum(r["wall_s"] for r in recs), 2),
        }
    return out


def aggregate(per_run: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """`{metric: {mean, min, max, n}}` over runs; a metric undefined in a run
    (None) is skipped and `n` says how many runs defined it."""
    keys = sorted({k for run in per_run for k in run})
    out: dict[str, dict[str, Any]] = {}
    for key in keys:
        values = [run[key] for run in per_run if run.get(key) is not None]
        if values:
            out[key] = {
                "mean": statistics.fmean(values),
                "min": min(values),
                "max": max(values),
                "n": len(values),
            }
    return out


def _fmt(stat: Mapping[str, Any], ratio: bool) -> str:
    if ratio:
        return f"{stat['mean']:.3f} ({stat['min']:.3f}-{stat['max']:.3f})"
    return f"{stat['mean']:.1f} ({stat['min']:g}-{stat['max']:g})"


def render_report(
    records: Sequence[Mapping[str, Any]],
    rows: Sequence[GoldRow],
    meetings: Sequence[str],
) -> str:
    """The markdown report. PARTIAL-stamped when any gold row is unadjudicated
    or any expected meeting has no record: such a number is never
    presentable as the pre-registered result."""
    present = {r["meeting"] for r in records}
    absent = [m for m in meetings if m not in present]
    partial = bool(blank_rows(rows)) or bool(absent)
    lines: list[str] = []
    if partial:
        why = []
        if blank_rows(rows):
            why.append(
                f"{len(blank_rows(rows))} of {len(rows)} gold rows unadjudicated"
            )
        if absent:
            why.append(
                f"{len(absent)} of {len(meetings)} meeting(s) without a run: {absent}"
            )
        lines += [f"# {PARTIAL_STAMP}", "", "; ".join(why), ""]
    arms = sorted({r["arm"] for r in records})
    for arm in arms:
        arm_records = [r for r in records if r["arm"] == arm]
        runs = sorted({r["run"] for r in arm_records})
        per_run = [
            series_run_metrics([r for r in arm_records if r["run"] == run], rows)
            for run in runs
        ]
        series_names = sorted({s for run in per_run for s in run})
        lines += [f"## Arm `{arm}` ({len(runs)} run(s))", ""]
        for series in series_names:
            agg = aggregate([run[series] for run in per_run if series in run])
            first = next(run[series] for run in per_run if series in run)
            lines += [
                f"### {series}: mean (min-max) over {len(runs)} run(s)",
                "",
                f"- recall: {_fmt(agg['recall'], True) if 'recall' in agg else 'n/a'} "
                f"-- gold decisions recalled: {_fmt(agg['recalled'], False)} of "
                f"{first['decision_rows']} decision rows",
                f"- decision rows no act of which reaches the {MIN_MATCH_TOKENS}-token "
                f"match floor (counted in the denominator, unrecallable by "
                f"construction): {first['unrecallable_rows']} of {first['decision_rows']}",
                f"- compuesta rows (excluded from recall): {first['compuesta_rows']}; "
                f"descartar rows (excluded from recall): {first['descartar_rows']}",
                f"- shared_evidence_groups (decision rows with identical evidence in "
                f"one meeting; recall credit capped by distinct matching Decisions): "
                f"{first['shared_evidence_groups']}",
                f"- produced: {_fmt(agg['produced'], False)} Decisions per run, split "
                f"matched_decision {_fmt(agg['matched_decision'], False)}, "
                f"matched_non_decision {_fmt(agg['matched_non_decision'], False)}, "
                f"matched_compuesta {_fmt(agg['matched_compuesta'], False)}, "
                f"matched_descartar {_fmt(agg['matched_descartar'], False)}, "
                f"unadjudicated {_fmt(agg['unadjudicated'], False)}, "
                f"unannotated {_fmt(agg['unannotated'], False)}",
                f"- precision-on-annotated: "
                f"{_fmt(agg['precision_on_annotated'], True) if 'precision_on_annotated' in agg else 'n/a'}"
                " (unannotated is NOT a false positive: the gold covers only AMI's "
                "abstractive decision sentences)",
                f"- duplicate titles across meetings: {_fmt(agg['duplicate_titles'], False)} "
                f"distinct, {_fmt(agg['duplicate_extra'], False)} Decisions that would "
                "merge at ingest",
                f"- windows {_fmt(agg['windows'], False)}, LLM calls "
                f"{_fmt(agg['calls'], False)}, meetings with a failure "
                f"{_fmt(agg['failed_meetings'], False)} of {first['meetings']}, "
                f"wall {_fmt(agg['wall_s'], False)} s",
                "",
            ]
    shaped = sum(1 for r in records if r.get("shaped"))
    lines.append(
        f"gate `_is_meeting_shaped` true: {shaped} of {len(records)} meeting-runs"
    )
    return "\n".join(lines)
