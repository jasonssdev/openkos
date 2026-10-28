"""Blind sheet generator and importer for the Decision-fidelity measurement (#1649).

Model-free. It reads the pinned AMI archive and the rendered transcripts (both
outside this repository, passed by path) and writes working files for the
labeller, never committed:

    uv run python -u evals/decision_fidelity/build_sheets.py --self-test
    uv run python -u evals/decision_fidelity/build_sheets.py value-sheet \\
        --ami-zip <ami_public_manual_1.6.2.zip> --transcripts <dir> --out <dir>
    uv run python -u evals/decision_fidelity/build_sheets.py cue-sheet \\
        --ami-zip <zip> --transcripts <dir> --out <dir> [--per-stratum 50]
    uv run python -u evals/decision_fidelity/build_sheets.py line-sheet \\
        --runs <runs-*.json ...> --transcripts <dir> --out <dir>
    uv run python -u evals/decision_fidelity/build_sheets.py import-value \\
        --sheet value-sheet.json --filled filled.json --labeller <name> --out value_gold.json
    uv run python -u evals/decision_fidelity/build_sheets.py import-labels \\
        --sheet cue-sheet.json --filled filled.json --labeller <name> --out cue_labels.json

The sheets are blind: the line sheet carries no arm and no run, the cue sheet
keeps each turn's sampling stratum in a separate `cue-strata.json` the labeller
never opens, and the labeller reads only the sheet. `import-*` refuses an
unanswered, repeated or unknown item and any value outside the vocabulary.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import zipfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import fidelity_ami as ami  # noqa: E402
import fidelity_core as core  # noqa: E402

DEFAULT_SEED: Final = 1649
"""REGISTERED. The seed of every shuffle and sample."""

DEFAULT_PER_STRATUM: Final = 50

# --- a synthetic archive, for the self-test and the unit suite ---------------

_NITE: Final = 'xmlns:nite="http://nite.sourceforge.net/"'

_ES2015A: Final = (
    ("PM", "Welcome to the meeting everyone and thanks for coming."),
    ("PM", "So the control will cost a maximum of twelve fifty euros to produce."),
    ("ID", "Okay twelve fifty euros is fine, but the sample speaker costs one twenty."),
    ("ME", "What about a banana shaped case for the remote?"),
    ("PM", "No that is too expensive so we will use rubber for the case."),
    ("UI", "Fine."),
    ("PM", "Selling price will be twenty five euros."),
    ("PM", "Right let's close the meeting now."),
)

_IS1002B: Final = (
    ("PM", "Welcome everybody to the kick off meeting today."),
    ("ID", "What about a banana shaped case for the remote."),
    ("ME", "Maybe we could use a solar cell instead of batteries."),
    ("PM", "Okay so we will use rubber for the case then."),
    ("UI", "Yeah that sounds good to me, rubber it is."),
    ("PM", "Right so we are going to make it a television remote."),
    ("ME", "I think we should check the budget before that."),
    ("PM", "Let's go with kinetic power for the battery."),
    ("ID", "We'll keep the design simple and cheap overall."),
    ("PM", "uh yeah"),
)


def _write_transcript(
    directory: Path, meeting: str, turns: Sequence[tuple[str, str]]
) -> None:
    body = ""
    rows: list[dict[str, Any]] = []
    for index, (role, text) in enumerate(turns):
        prefix = f"{role}: "
        start = len(body) + len(prefix)
        body += prefix + text
        rows.append(
            {
                "char_start": start,
                "char_end": start + len(text),
                "dialogue_act_ids": [f"{meeting}.A.dialog-act.t.{index}"],
                "text": text,
                "turn_index": index,
            }
        )
        body += "\n\n"
    body = body.rstrip("\n")
    (directory / f"{meeting}.md").write_text(
        f"---\nmeeting_id: {meeting}\n---\n\n{body}\n", encoding="utf-8"
    )
    (directory / f"{meeting}.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )


def _abstractive(meeting: str, sentences: Sequence[tuple[int, str]]) -> str:
    body = "".join(
        f'<sentence nite:id="{meeting}.elana.s.{n}">{text}</sentence>'
        for n, text in sentences
    )
    return (
        f'<?xml version="1.0"?><nite:root {_NITE}><abstract nite:id="a">'
        f'<sentence nite:id="{meeting}.elana.s.1">Opening.</sentence></abstract>'
        f'<decisions nite:id="d">{body}</decisions></nite:root>'
    )


def _summlink(meeting: str, links: Sequence[tuple[str, Sequence[int]]]) -> str:
    out = ""
    for sentence, turns in links:
        acts = "".join(
            f'<nite:pointer role="extractive" href="{meeting}.A.dialog-act.xml'
            f'#id({meeting}.A.dialog-act.t.{t})"/>'
            for t in turns
        )
        out += (
            f'<summlink>{acts}<nite:pointer role="abstractive" '
            f'href="{meeting}.abssumm.xml#id({meeting}.elana.s.{sentence})"/></summlink>'
        )
    return f'<?xml version="1.0"?><nite:root {_NITE}>{out}</nite:root>'


def make_synthetic(root: Path) -> tuple[Path, Path]:
    """A tiny AMI-shaped archive and transcripts directory under `root`.

    `ES2015a` (a gold meeting) has a cap sentence anchored to turns 1 and 2, a
    rubber sentence anchored to turn 4, an `NA` sentence and an unanchored
    one; `IS1002b` (a calibration meeting) has turns for every cue stratum."""
    transcripts = root / "transcripts"
    transcripts.mkdir()
    _write_transcript(transcripts, "ES2015a", _ES2015A)
    _write_transcript(transcripts, "IS1002b", _IS1002B)
    archive = root / "ami.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr(
            "abstractive/ES2015a.abssumm.xml",
            _abstractive(
                "ES2015a",
                [
                    (
                        15,
                        "The control will cost a maximum of twelve Euro and fifty cents to produce.",
                    ),
                    (12, "The case will be made of rubber."),
                    (99, "NA."),
                    (98, "It will be shiny."),
                ],
            ),
        )
        bundle.writestr(
            "extractive/ES2015a.summlink.xml",
            _summlink("ES2015a", [("15", [1, 2]), ("12", [4])]),
        )
        bundle.writestr(
            "abstractive/IS1002b.abssumm.xml",
            _abstractive(
                "IS1002b", [(7, "The case will be rubber and a television remote.")]
            ),
        )
        bundle.writestr(
            "extractive/IS1002b.summlink.xml", _summlink("IS1002b", [("7", [3, 5])])
        )
    return archive, transcripts


# --- command line ------------------------------------------------------------


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _emit(out: Path, name: str, sheet: dict[str, Any]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    ami.dump(out / f"{name}.json", sheet)
    (out / f"{name}.md").write_text(ami.render_markdown(sheet), encoding="utf-8")
    print(f"wrote {out / name}.json and .md ({len(sheet['items'])} items)")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--self-test", action="store_true")
    sub = parser.add_subparsers(dest="command")
    for name in ("value-sheet", "cue-sheet", "line-sheet"):
        p = sub.add_parser(name)
        p.add_argument("--transcripts", type=Path, required=True)
        p.add_argument("--out", type=Path, required=True)
        p.add_argument("--seed", type=int, default=DEFAULT_SEED)
        if name != "line-sheet":
            p.add_argument("--ami-zip", type=Path, required=True)
            p.add_argument("--no-verify", action="store_true")
        if name == "cue-sheet":
            p.add_argument("--per-stratum", type=int, default=DEFAULT_PER_STRATUM)
        if name == "line-sheet":
            p.add_argument("--runs", type=Path, nargs="+", required=True)
    for name in ("import-value", "import-labels"):
        p = sub.add_parser(name)
        p.add_argument("--sheet", type=Path, required=True)
        p.add_argument("--filled", type=Path, required=True)
        p.add_argument("--labeller", required=True)
        p.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()
    if args.command == "value-sheet":
        archive = ami.open_archive(args.ami_zip, verify=not args.no_verify)
        sheet = ami.value_sheet(
            archive, args.transcripts, list(core.GOLD_MEETINGS), args.seed
        )
        _emit(args.out, "value-sheet", sheet)
        print(f"unanchored (not on the sheet): {len(sheet['unanchored'])}")
    elif args.command == "cue-sheet":
        archive = ami.open_archive(args.ami_zip, verify=not args.no_verify)
        sheet, strata = ami.cue_sheet(
            archive,
            args.transcripts,
            list(core.CUE_MEETINGS),
            args.seed,
            args.per_stratum,
        )
        _emit(args.out, "cue-sheet", sheet)
        ami.dump(args.out / "cue-strata.json", strata)
    elif args.command == "line-sheet":
        runs: list[dict[str, list[dict[str, Any]]]] = []
        for path in args.runs:
            runs.extend(core.load_runs(_read(path)))
        _emit(args.out, "line-sheet", ami.line_sheet(runs, args.transcripts, args.seed))
    elif args.command == "import-value":
        doc = ami.import_value(_read(args.sheet), _read(args.filled), args.labeller)
        ami.dump(args.out, doc)
        print(f"wrote {args.out} ({len(doc['items'])} items)")
    elif args.command == "import-labels":
        doc = ami.import_labels(_read(args.sheet), _read(args.filled), args.labeller)
        ami.dump(args.out, doc)
        print(f"wrote {args.out} ({len(doc['labels'])} labels)")
    else:
        parser.error("a command or --self-test is required")
    return 0


# --- self-test ---------------------------------------------------------------


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def self_test() -> int:
    """Model-free checks of the sheets over a synthetic archive; raises on a
    failure, returns 0 otherwise."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        zip_path, transcripts = make_synthetic(root)
        out = root / "out"

        archive = ami.open_archive(zip_path, verify=False)
        sheet = ami.value_sheet(archive, transcripts, ["ES2015a"], DEFAULT_SEED)
        _check(
            len(sheet["items"]) == 2,
            "value sheet should hold the two anchored sentences",
        )
        _check(
            sheet["unanchored"] == ["ES2015a.elana.s.98"],
            "unanchored sentence not listed",
        )
        _emit(out, "value-sheet", sheet)

        filled = [
            {
                "id": "ES2015a.elana.s.15",
                "kind": "amount",
                "forms": ["12.50", "twelve fifty"],
                "distractors": ["1.20"],
                "exclude": False,
            },
            {
                "id": "ES2015a.elana.s.12",
                "kind": "choice",
                "forms": ["rubber"],
                "distractors": ["banana"],
                "exclude": False,
            },
        ]
        ami.dump(out / "filled.json", filled)
        main(
            [
                "import-value",
                "--sheet",
                str(out / "value-sheet.json"),
                "--filled",
                str(out / "filled.json"),
                "--labeller",
                "self-test",
                "--out",
                str(out / "gold.json"),
            ]
        )
        items = core.load_value_gold(_read(out / "gold.json"))
        _check(len(items) == 2, "gold should reload with two items")

        cue, strata = ami.cue_sheet(archive, transcripts, ["IS1002b"], DEFAULT_SEED, 2)
        _check(set(strata.values()) == set(ami.STRATA), "every stratum should be drawn")
        _check(
            all("stratum" not in i for i in cue["items"]),
            "the sheet must not show strata",
        )

        runs = [
            {
                "ES2015a": [
                    {
                        "title": "t",
                        "description": "d",
                        "body": "v\n\n> so we will use rubber for the case",
                    }
                ]
            }
        ]
        lines = ami.line_sheet(runs, transcripts, DEFAULT_SEED)
        _check(
            len(lines["items"]) == 1 and lines["items"][0]["located"],
            "line not located",
        )

        for bad in ("TS3012a", "TS3005a", "ES2002a"):
            try:
                ami.load_turns(transcripts, bad)
            except core.MeetingRefused:
                continue
            raise AssertionError(f"{bad} was not refused")
    print("self-test: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
