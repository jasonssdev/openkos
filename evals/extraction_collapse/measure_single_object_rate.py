"""How often would a `retained == 1` trigger fire? (#522)

Reads the stored `runs-*.json` under `evals/` and makes ZERO model calls:
every run's retained count is already on disk, so the question is answerable
from data the harnesses already paid for. Same move as
`extraction_cap/measure_acronym_fabrication.py`.

## The question

A deterministic guard for the collapse needs a trigger. `retained == 1` is
the obvious candidate -- but a trigger is only worth building if it fires
where the defect is and stays quiet where it is not, and "obvious" is how
this project has been wrong before.

Two earlier candidates died before reaching code, which is why this exists:

- **The source-title twin.** When the collapse fires on the probe's Spanish
  fixtures the lone object's title equals the source title, which looked
  like a free, exact detector. It is an artifact of those fixtures' titles.
  The positive control -- the case #522 was actually built on -- collapses
  to `Remote Product Development` from a source titled `TS3005b.summary`.
  A twin detector would miss it.
- **Retry on detection.** `extract_concept_union` ALREADY runs two passes,
  and the collapse survives it 10 times out of 10. The failure is
  systematic, not sampling noise, so any re-ask has to differ from the
  prompt that just failed.

## Reading the output

Rows are split by whether the material is known to collapse. On healthy
material a firing trigger is a FALSE POSITIVE; on the AMI transcripts it is
the defect being caught.

Run from the repository root:

    uv run python evals/extraction_collapse/measure_single_object_rate.py
"""

from __future__ import annotations

import json
import pathlib
from collections import defaultdict

COLLAPSING = ("TS3005a.transcript", "TS3005b.transcript", "TS3005b.summary")
"""Fixtures #522 records as collapsing. A single object here is the DEFECT,
so the trigger firing is a true positive and the rate is recall, not noise."""

SINGLE_SUBJECT_UNMEASURED = """\
NOT MEASURED, and it is the case that matters most for a re-ask:
a source that GENUINELY has one subject. `_drop_source_title_twins` names
the shape (an `mcp-launch` page: H1 "MCP Launching" -> Event:MCP Launching)
and deliberately keeps its only object. No fixture in `extraction_cap`
is of that class -- all four are multi-subject expository prose -- so the
false-positive rate below says nothing about it. A guard that REPLACES the
single object would be unbounded there; one that only ADDS subjects the
first pass missed is bounded whatever this rate turns out to be.

An instrument for it now exists: `NEGATIVE_CONTROL` in `collapse_fixtures.py`
is a source of exactly that class, 1-4 KB, where one object is the CORRECT
answer. It changes nothing below. `run_collapse_probe.py` writes no
`runs-*.json`, so its negative-control section is where that measurement is
read, and it has to be RUN -- the rate here stays blind to the case until
somebody spends the calls."""


def main() -> None:
    root = pathlib.Path(__file__).resolve().parents[2]
    files = sorted((root / "evals").rglob("runs-*.json"))

    # (harness, model, fixture) -> [ok runs, runs returning exactly one]
    cells: dict[tuple[str, str, str], list[int]] = defaultdict(lambda: [0, 0])

    for path in files:
        payload = json.loads(path.read_text(encoding="utf-8"))
        model = str(payload.get("model", "?"))
        harness = path.relative_to(root / "evals").parts[0]
        for outcome in payload.get("outcomes", []):
            if outcome.get("status") != "ok":
                continue
            retained = outcome.get("retained")
            if retained is None:
                continue
            key = (harness, model, str(outcome.get("fixture", "?")))
            cells[key][0] += 1
            if retained == 1:
                cells[key][1] += 1

    print(f"run files read: {len(files)} (no model calls)\n")

    for label, collapsing in (
        ("HEALTHY MATERIAL -- a firing trigger here is a FALSE POSITIVE", False),
        ("KNOWN-COLLAPSING MATERIAL -- firing here is the defect caught", True),
    ):
        print(label)
        print("=" * 72)
        totals = [0, 0]
        rows = [k for k in sorted(cells) if (k[2] in COLLAPSING) is collapsing]
        if not rows:
            print("  (no stored runs)\n")
            continue
        for key in rows:
            runs, single = cells[key]
            totals[0] += runs
            totals[1] += single
            rate = single / runs if runs else 0.0
            mark = "  <-- fires" if single else ""
            print(
                f"  {key[0]:20} {key[1]:12} {key[2]:32} {single}/{runs} ({rate:.0%}){mark}"
            )
        overall = totals[1] / totals[0] if totals[0] else 0.0
        print(f"  {'':20} {'':12} {'ALL':32} {totals[1]}/{totals[0]} ({overall:.0%})\n")

    print("PER-MODEL, healthy material only")
    print("=" * 72)
    per_model: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for key, (runs, single) in cells.items():
        if key[2] in COLLAPSING:
            continue
        per_model[key[1]][0] += runs
        per_model[key[1]][1] += single
    for model in sorted(per_model):
        runs, single = per_model[model]
        rate = single / runs if runs else 0.0
        # Flagged rather than ranked: #515 lets a deployment pick a different
        # model per task, so a guard tuned to the default model's rate is a
        # guard that misbehaves the moment someone exercises that feature.
        flag = "  <-- would fire often on healthy sources" if rate > 0.1 else ""
        print(f"  {model:14} {single}/{runs} ({rate:.0%}){flag}")

    print()
    print("THE GAP")
    print("=" * 72)
    print(SINGLE_SUBJECT_UNMEASURED)


if __name__ == "__main__":
    main()
