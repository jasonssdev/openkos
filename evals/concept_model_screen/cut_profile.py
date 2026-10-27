"""What the selector judge's cut removes, by type, size and richness (#1579).

Reads the #1571 replay records (`judge_study.py`); never writes them. No model
is called and no embedding is needed: every figure is a count or a quantile of
fields already stored on each ceiling object (`type`, `body_chars`). This is a
descriptive profile for `NEXT_STEP.md`, not a registered study: it has no
decision rule and decides nothing.

Subcommand `profile`: for each judge arm (seed index 0, primary records),
the cut rate per object type, the bundle size per source with the judge versus
the ceiling alone, and the body-size quartiles of cut versus kept objects.

Model-free check: `--self-test`.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Final

HERE = pathlib.Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(HERE))
sys.path.append(str(REPO_ROOT / "evals"))

import cut_precision as cp  # noqa: E402
import judge_study as js  # noqa: E402
import screen_data as sd  # noqa: E402

Obj = dict[str, Any]

BACKSTOP: Final = 20
"""`concept._UNION_BACKSTOP`, restated so the profile reads the constant the
production path applies when no judge ran (pinned by a self-test)."""
ARMS: Final = cp.ARMS


# --- pure ----------------------------------------------------------------------


def type_rates(
    ceiling: Sequence[Mapping[str, Any]], flags: Sequence[bool]
) -> dict[str, tuple[int, int]]:
    """type -> (cut, total) over one record's ceiling objects."""
    out: dict[str, list[int]] = {}
    for obj, kept in zip(ceiling, flags, strict=True):
        row = out.setdefault(str(obj["type"]), [0, 0])
        row[1] += 1
        row[0] += 0 if kept else 1
    return {k: (v[0], v[1]) for k, v in out.items()}


def merge_rates(
    parts: Sequence[Mapping[str, tuple[int, int]]],
) -> dict[str, tuple[int, int]]:
    """Sum several records' `type_rates`."""
    out: dict[str, list[int]] = {}
    for part in parts:
        for key, (cut, total) in part.items():
            row = out.setdefault(key, [0, 0])
            row[0] += cut
            row[1] += total
    return {k: (v[0], v[1]) for k, v in out.items()}


def size_row(n_ceiling: int, n_kept: int) -> tuple[int, int, int]:
    """(judge kept, ceiling-only kept, objects the backstop alone would cut)
    for one source: with no judge the production path hands the ceiling
    objects to the positional backstop of 20."""
    alone = min(n_ceiling, BACKSTOP)
    return n_kept, alone, n_ceiling - alone


def quartiles(values: Sequence[int]) -> tuple[float, float, float] | None:
    """(q1, median, q3) by linear interpolation; None for no values."""
    if not values:
        return None
    xs = sorted(values)

    def at(p: float) -> float:
        pos = p * (len(xs) - 1)
        lo = int(pos)
        hi = min(lo + 1, len(xs) - 1)
        return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)

    return at(0.25), at(0.5), at(0.75)


def body_split(
    ceiling: Sequence[Mapping[str, Any]], flags: Sequence[bool]
) -> dict[str, list[int]]:
    """Body sizes of kept and cut objects of one record."""
    out: dict[str, list[int]] = {"kept": [], "cut": []}
    for obj, kept in zip(ceiling, flags, strict=True):
        out["kept" if kept else "cut"].append(int(obj["body_chars"]))
    return out


# --- run -----------------------------------------------------------------------


def _load(path: pathlib.Path) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return loaded


def collect(study_dir: pathlib.Path, arm: str) -> dict[str, Any]:
    """Profile of one arm over every primary record (seed index 0)."""
    rates: list[dict[str, tuple[int, int]]] = []
    sizes: list[tuple[int, int, int]] = []
    bodies: dict[str, list[int]] = {"kept": [], "cut": []}
    records = 0
    for model, runs in js.PRIMARY:
        for meeting in sd.POOL_DA:
            for run in runs:
                records += 1
                base = _load(js.replay_path(study_dir, "self", model, meeting, run, 0))
                ceiling = base["stages"]["ceiling"] or []
                judge_arm = "fixed" if model == js.JUDGE_MODEL else arm
                rec = _load(
                    js.replay_path(study_dir, judge_arm, model, meeting, run, 0)
                )
                if not js.same_objects(rec["stages"]["ceiling"], ceiling):
                    continue
                flags = cp.partition(ceiling, rec["stages"]["judge"] or [])
                rates.append(type_rates(ceiling, flags))
                sizes.append(size_row(len(ceiling), sum(flags)))
                split = body_split(ceiling, flags)
                bodies["kept"] += split["kept"]
                bodies["cut"] += split["cut"]
    return {
        "records": records,
        "rates": merge_rates(rates),
        "sizes": sizes,
        "bodies": bodies,
    }


def render(arm: str, prof: Mapping[str, Any]) -> list[str]:
    lines = [f"## arm: {arm}", ""]
    lines.append(f"records {len(prof['sizes'])} of {prof['records']} measurable")
    lines += ["", "cut rate by type (cut of total):"]
    for key, (cut, total) in sorted(prof["rates"].items()):
        lines.append(f"  {key}: {cut} of {total} ({cut / total:.3f})")
    sizes = prof["sizes"]
    kept = sum(s[0] for s in sizes)
    alone = sum(s[1] for s in sizes)
    over = sum(1 for s in sizes if s[2] > 0)
    lines += [
        "",
        f"objects kept with the judge: {kept}; with the ceiling and backstop "
        f"alone: {alone}",
        f"sources where the backstop alone would cut: {over} of {len(sizes)}",
        f"per-source kept with the judge: {sorted(s[0] for s in sizes)}",
        f"per-source with ceiling alone:  {sorted(s[1] for s in sizes)}",
        "",
        "body chars (q1, median, q3):",
    ]
    for side in ("kept", "cut"):
        q = quartiles(prof["bodies"][side])
        lines.append(f"  {side} (n={len(prof['bodies'][side])}): {q}")
    return [*lines, ""]


def cmd_profile(args: argparse.Namespace) -> int:
    lines: list[str] = []
    for arm in ARMS:
        lines += render(arm, collect(args.study_dir, arm))
    print("\n".join(lines))
    return 0


# --- self-test -----------------------------------------------------------------


def _check(cond: bool, message: str) -> None:
    if not cond:
        raise AssertionError(message)


def _o(kind: str, body: int = 100) -> Obj:
    return {"type": kind, "title": "t", "description": "d", "body_chars": body}


def _t_rates() -> None:
    ceiling = [_o("Decision"), _o("Decision"), _o("Event")]
    got = type_rates(ceiling, [True, False, False])
    _check(got == {"Decision": (1, 2), "Event": (1, 1)}, f"per-type cut: {got}")
    merged = merge_rates([got, {"Decision": (2, 3), "Person": (0, 1)}])
    _check(
        merged == {"Decision": (3, 5), "Event": (1, 1), "Person": (0, 1)},
        f"merge sums: {merged}",
    )
    _check(type_rates([], []) == {}, "empty record")


def _t_size() -> None:
    _check(BACKSTOP == js.concept_mod._UNION_BACKSTOP, "backstop constant drifted")
    _check(size_row(12, 9) == (9, 12, 0), "under the backstop nothing is cut")
    _check(size_row(24, 10) == (10, 20, 4), "ceiling 24 -> backstop cuts 4")
    _check(size_row(20, 3) == (3, 20, 0), "exactly the backstop")


def _t_quartiles() -> None:
    _check(quartiles([]) is None, "no values")
    _check(quartiles([5]) == (5.0, 5.0, 5.0), "single value")
    _check(quartiles([4, 1, 3, 2, 5]) == (2.0, 3.0, 4.0), "odd, unsorted input")
    _check(quartiles([1, 2, 3, 4]) == (1.75, 2.5, 3.25), "interpolated")


def _t_bodies() -> None:
    ceiling = [_o("A", 10), _o("B", 20), _o("C", 30)]
    got = body_split(ceiling, [True, False, True])
    _check(got == {"kept": [10, 30], "cut": [20]}, f"split by flag: {got}")


def self_test() -> int:
    tests: list[Callable[[], None]] = [_t_rates, _t_size, _t_quartiles, _t_bodies]
    failed = 0
    for test in tests:
        try:
            test()
            print(f"ok   {test.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"FAIL {test.__name__}: {exc}")
    print(f"self-test: {len(tests) - failed} of {len(tests)} passed")
    return 1 if failed else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--self-test", action="store_true", help="model-free checks, then exit"
    )
    parser.add_argument("--study-dir", type=pathlib.Path, default=js.OUT_DIR)
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("profile")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    if args.command != "profile":
        raise SystemExit("choose a subcommand, or --self-test")
    return cmd_profile(args)


if __name__ == "__main__":
    raise SystemExit(main())
