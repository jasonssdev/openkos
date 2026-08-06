"""Turn AMI manual annotations into `openkos ingest` sources, plus ground truth.

MANUAL eval tool (NOT pytest, NOT part of the shipped package). It is the
PREPARATION half of the decision-extraction harness: it produces the inputs, it
never runs the model and never scores anything. Measuring is a separate step,
so a bad transcript build cannot be mistaken for a bad extraction result.

## What this is for

Every end-to-end run this project has measured used clean third-party prose,
and the resulting bundles were ~79% `Concept`: of the nine `CLASSIFIABLE_TYPES`
only `Concept`, `Event`, `Person` and `Procedure` ever appeared. `Decision`,
`Entity`, `Organization`, `Place` and `Project` never did.

That is not evidence the classifier is broken. Course material about agents and
protocols genuinely is almost all concepts -- it has no meetings, no companies,
no named decisions. The skew and the classifier are confounded, and only a
corpus that CONTAINS the missing types can separate them.

AMI's scenario meetings do. A design team with named members and roles, a
fictional company, a project with phases, and design decisions that are made,
revisited and reversed across four sessions.

## Why two meetings and not the manifest's twenty-eight

`manifests/ami_selected_28.txt` is the measurement set. This script defaults to
two meetings because the immediate question is a smoke test -- does the
extractor emit `Decision`/`Person`/`Organization`/`Project` AT ALL -- and that
is answered by the right two, not by volume.

The default pair is `TS3005a`/`TS3005b`, and it was chosen by counting AMI's
named-entity annotations across all twenty-eight rather than by reasoning about
what a session ought to contain. That distinction earned its place: the obvious
pick was a kick-off session, on the argument that it introduces the team and the
company. The annotations say otherwise -- `IS1001a` has nine `PERSON` mentions
and **zero** `ORGANIZATION`, so a run on it could never have told a classifier
failure from a corpus that afforded nothing.

`TS3005a` has the best simultaneous coverage in the manifest (17 `PERSON`,
4 `ORGANIZATION`, 3 `LOCATION`) and `TS3005b` adds `ORGANIZATION` from the same
series, so provenance across the two is traceable. Pass `--meetings` for
anything else, or `--all` for the full manifest.

## Two variants per meeting, and why that matters

For each meeting this writes BOTH:

- `<meeting>.transcript.txt` -- speaker-labelled turns in time order, verbatim.
- `<meeting>.summary.txt` -- AMI's own human-written abstractive summary.

Not for coverage. They isolate a confound. AMI transcripts are verbatim speech:
disfluencies, false starts, overlapping turns, "uh". Every corpus this project
has extracted from was edited prose. Run only the transcript and a poor result
cannot distinguish "the classifier does not reach these types" from "the
extractor does not digest spoken language" -- two different defects with two
different fixes. The summary is the same meeting's content in the register the
extractor has actually been tested on, so the pair separates them.

## Ground truth comes from the corpus, not from us

AMI's abstractive annotations carry a human-written `decisions` section per
meeting. This writes it to `ground_truth/<meeting>.decisions.txt` verbatim.

That is deliberately NOT a scoring rule -- scoring lives in the measurement
step, and `evals/extraction_cap/run_cap_eval.py`'s reasoning about deliberately
dumb matching applies there just as much. What it buys here is that the
reference for "what decisions this meeting contains" was written by AMI's
annotators before this project existed, so it cannot be tuned toward whatever
the extractor happens to produce.

## Reproducibility, and how this differs from `examples/extraction-corpus/`

That corpus is gitignored and its own `.gitignore` states the cost plainly:
"nobody else can reproduce the exact numbers, only the procedure." It is
third-party course material this Apache-2.0 repository holds no right to
relicense.

AMI is redistributable under its own terms, and this harness does not need to
redistribute it anyway: `ami_public_manual_1.6.2.sha256` pins the exact archive
and `manifests/ami_selected_28.txt` pins the exact meetings, so anyone who
downloads AMI reproduces the same bytes and therefore the same numbers. Verify
AMI's licence before committing any generated source -- the checksum route was
chosen so that decision stays open, and `sources/` is gitignored by default.

## What this script will NOT do

It fails loudly rather than guessing. The AMI annotation layout is read from
the archive as-is; if the expected `words/` and `segments/` files are missing or
shaped differently than this expects, it says exactly what it looked for and
stops. A transcript silently assembled from the wrong layer would be an input
nobody could trust, and every downstream number would inherit it.

Usage:

    uv run python -u evals/decision_extraction/scripts/build_sources.py \\
        --zip ~/Downloads/ami_public_manual_1.6.2.zip

    uv run python -u evals/decision_extraction/scripts/build_sources.py \\
        --extracted ~/ami --meetings IS1001a IS1001d
"""

from __future__ import annotations

import argparse
import hashlib
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CHECKSUM_FILE = ROOT / "ami_public_manual_1.6.2.sha256"
MANIFEST = ROOT / "manifests" / "ami_selected_28.txt"
SOURCES_DIR = ROOT / "sources"
GROUND_TRUTH_DIR = ROOT / "ground_truth"

DEFAULT_MEETINGS = ("TS3005a", "TS3005b")
"""Chosen from the named-entity annotations, not from an argument about what a
kick-off session ought to contain -- see the module docstring."""


def _parse_xml(path: Path) -> ET.Element:
    """Parse one AMI annotation file.

    `S314` flags `xml.etree` as unsafe for UNTRUSTED input, which this is not:
    on the `--zip` path the archive is sha256-verified against the pinned
    digest before anything is read, and on `--extracted` the tree is one the
    contributor unpacked themselves on their own machine. This is a local
    manual harness, never a service, and it parses no bytes that arrived over a
    network. Routed through ONE function so the exemption is stated once and
    reviewed once, rather than repeated at four call sites where the reasoning
    would drift."""
    return ET.parse(path).getroot()  # noqa: S314


_NITE_ID = "{http://nite.sourceforge.net/}id"
"""AMI declares `xmlns:nite="http://nite.sourceforge.net/"` -- WITH the trailing
slash. Getting this wrong does not raise: `element.get()` simply returns `None`
for every word, the word table comes out empty, and the build emits a
plausible-looking two-line transcript for a thirty-minute meeting. That is why
`build_transcript` refuses to write a transcript whose segments resolved to
nothing (see its resolution guard) rather than trusting these constants."""

_SEGMENT_START = "transcriber_start"
"""Segments carry `transcriber_start`/`transcriber_end`, NOT the `starttime`
the word layer uses. Two different attribute vocabularies in one corpus."""


@dataclass(frozen=True)
class Turn:
    """One speaker turn: everything one speaker said in one segment."""

    start: float
    speaker: str
    text: str


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _expected_checksum() -> str | None:
    """The pinned digest, or `None` when the checksum file is absent/unreadable.

    Absence degrades to a warning rather than a refusal: a contributor pointing
    `--extracted` at an already-unpacked tree has no archive to hash, and this
    script's job is to build inputs, not to police provenance. The warning is
    what keeps that trade visible."""
    try:
        first = CHECKSUM_FILE.read_text(encoding="utf-8").split()
    except OSError:
        return None
    return first[0] if first else None


def _verify_zip(zip_path: Path) -> None:
    expected = _expected_checksum()
    actual = _sha256(zip_path)
    if expected is None:
        print(f"warning: {CHECKSUM_FILE.name} unreadable; archive digest {actual}")
        return
    if actual != expected:
        raise SystemExit(
            f"checksum mismatch for {zip_path}\n"
            f"  expected {expected}\n"
            f"  actual   {actual}\n"
            "This is not the archive this harness's numbers were measured "
            "against. Re-download rather than proceeding: every figure "
            "downstream would silently describe different bytes."
        )
    print(f"archive verified: {actual}")


def _read_manifest() -> list[str]:
    try:
        lines = MANIFEST.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise SystemExit(f"cannot read {MANIFEST}: {exc}") from exc
    return [line.strip() for line in lines if line.strip()]


def _member_paths(root: Path, meeting: str, layer: str) -> list[Path]:
    """Every `<meeting>.<speaker>.<layer>.xml` under `root`, sorted.

    Searched with `rglob` rather than at a fixed depth because the archive and a
    hand-unpacked tree nest differently, and the layout is not something this
    script should assert from memory."""
    return sorted(root.rglob(f"{meeting}.*.{layer}.xml"))


def _join(spans: list[tuple[str, bool]]) -> str:
    """Join `(text, is_punctuation)` pairs so a turn reads as a sentence.

    AMI tags punctuation as its own `<w punc="true">` token, so a naive
    `" ".join` produces `Hmm . Mm .` -- which is not what the extractor is
    supposed to be tested against, and would make every transcript look worse
    than the corpus actually is."""
    out = ""
    for text, is_punc in spans:
        out += text if (is_punc or not out) else f" {text}"
    return out


def _parse_words(
    path: Path,
) -> tuple[dict[str, tuple[float, str]], set[str], list[str]]:
    """`{word-id: (start-time, text)}` for one speaker's word layer.

    Non-lexical children (`vocalsound`, `pause`, ...) carry no text and are
    skipped; a word with no `starttime` is kept with `inf` so it sorts last
    rather than silently collapsing to the start of the meeting."""
    words: dict[str, tuple[float, str]] = {}
    punctuation: set[str] = set()
    order: list[str] = []
    for element in _parse_xml(path):
        element_id = element.get(_NITE_ID)
        if element_id:
            order.append(element_id)
        if not element.tag.endswith("w"):
            continue
        word_id = element.get(_NITE_ID)
        text = (element.text or "").strip()
        if not word_id or not text:
            continue
        raw_start = element.get("starttime")
        try:
            start = float(raw_start) if raw_start is not None else float("inf")
        except ValueError:
            start = float("inf")
        words[word_id] = (start, text)
        if element.get("punc") == "true":
            punctuation.add(word_id)
    return words, punctuation, order


def _href_ids(href: str) -> list[str]:
    """The word ids a segment's `nite:child href` points at.

    A PLAIN `href`, not `xlink:href` -- the segment layer writes
    `<nite:child href="IS1001a.A.words.xml#id(...)"/>`.

    AMI writes either a single `...#id(x)` or a range `...#id(a)..id(b)`. Only
    the endpoints are extracted here; the caller resolves a range against the
    speaker's own ordered word list, because the ids between them are not
    derivable from the href alone."""
    fragment = href.split("#", 1)[-1]
    ids: list[str] = []
    for part in fragment.split(".."):
        part = part.strip()
        if part.startswith("id(") and part.endswith(")"):
            ids.append(part[3:-1])
    return ids


def _parse_segments(
    path: Path,
    words: dict[str, tuple[float, str]],
    punctuation: set[str],
    order: list[str],
) -> tuple[list[Turn], int]:
    """One speaker's segments resolved to text, plus the raw segment count.

    The count is returned so the caller can tell "this speaker genuinely said
    nothing" from "every segment failed to resolve" -- the second is a parsing
    bug and must never reach an output file."""
    speaker = path.name.split(".")[1]
    # Indexed over EVERY element in the word file, in document order -- not
    # over the lexical subset. A segment's range endpoints routinely land on a
    # `<disfmarker>` or `<vocalsound>`, so a lexical-only index reports a
    # perfectly ordinary range as unresolvable. Document order is also the
    # right order here: the file is already sequential, and sorting by
    # `starttime` would reshuffle the zero-duration markers between words.
    position = {element_id: index for index, element_id in enumerate(order)}

    turns: list[Turn] = []
    segment_count = 0
    unresolved: list[str] = []
    for segment in _parse_xml(path):
        segment_count += 1
        spans: list[tuple[str, bool]] = []
        for child in segment:
            href = child.get("href")
            if not href:
                continue
            endpoints = _href_ids(href)
            if not endpoints:
                unresolved.append(href)
                continue
            if len(endpoints) == 1:
                selected = [endpoints[0]]
            else:
                first, last = endpoints[0], endpoints[-1]
                if first not in position or last not in position:
                    unresolved.append(href)
                    continue
                lo, hi = sorted((position[first], position[last]))
                selected = order[lo : hi + 1]
            # An id absent from `words` but PRESENT in `known_ids` is a
            # non-lexical element -- `<vocalsound>`, `<disfmarker>`, `<pause>`
            # -- which carries no text and is correctly skipped. Only an id the
            # word file never declared at all is a resolution failure. Folding
            # the two together would refuse every real AMI meeting, since a
            # segment routinely spans a laugh or a disfluency marker.
            missing = [wid for wid in selected if wid not in position]
            if missing:
                unresolved.append(f"{href} (missing {len(missing)} word id(s))")
            spans.extend(
                (words[wid][1], wid in punctuation) for wid in selected if wid in words
            )
        if not spans:
            continue
        raw_start = segment.get(_SEGMENT_START)
        try:
            start = float(raw_start) if raw_start is not None else float("inf")
        except ValueError:
            start = float("inf")
        turns.append(Turn(start=start, speaker=speaker, text=_join(spans)))

    # A PARTIAL failure is the dangerous one. If one `nite:child` of a
    # multi-child segment fails to resolve, the other children still fill
    # `spans`, so the segment yields a turn with words silently missing -- a
    # transcript that reads fine and is not what the meeting said. The
    # zero-turn guard in `build_transcript` cannot see that, because turns
    # were produced. So every unresolved reference is refused here, not
    # counted and shrugged off.
    if unresolved:
        shown = "\n    ".join(unresolved[:5])
        more = f"\n    (+{len(unresolved) - 5} more)" if len(unresolved) > 5 else ""
        raise SystemExit(
            f"{path.name}: {len(unresolved)} word reference(s) did not resolve:"
            f"\n    {shown}{more}\n"
            "  Refusing to write a transcript with silently missing words. "
            "This is a layout drift, not an empty speaker -- check `_NITE_ID`, "
            "the plain `href` on `nite:child`, and the word-layer id scheme."
        )
    return turns, segment_count


def build_transcript(root: Path, meeting: str) -> str:
    """The meeting as speaker-labelled turns in time order."""
    segment_files = _member_paths(root, meeting, "segments")
    if not segment_files:
        raise SystemExit(
            f"no segment files found for {meeting}\n"
            f"  looked for: {meeting}.*.segments.xml under {root}\n"
            "Point --extracted at the directory containing AMI's `words/` and "
            "`segments/` annotation folders. Refusing to assemble a transcript "
            "from a layout this script cannot recognise."
        )

    turns: list[Turn] = []
    for segment_path in segment_files:
        speaker = segment_path.name.split(".")[1]
        word_candidates = _member_paths(root, meeting, "words")
        word_path = next(
            (p for p in word_candidates if p.name.split(".")[1] == speaker), None
        )
        if word_path is None:
            raise SystemExit(
                f"{meeting}: segments for speaker {speaker} have no matching "
                f"{meeting}.{speaker}.words.xml"
            )
        words, punctuation, order = _parse_words(word_path)
        speaker_turns, segment_count = _parse_segments(
            segment_path, words, punctuation, order
        )
        if segment_count and not speaker_turns:
            raise SystemExit(
                f"{meeting}: {segment_count} segment(s) for speaker {speaker} "
                "resolved to ZERO turns.\n"
                "  This is a parsing bug, not an empty speaker: the segment "
                "layer references words the word layer did not yield.\n"
                "  The usual cause is a namespace or attribute-name drift "
                "between AMI releases -- check `_NITE_ID`, the plain `href` on "
                "`nite:child`, and `_SEGMENT_START`.\n"
                "  Refusing to write a transcript that would look plausible "
                "and contain almost nothing."
            )
        turns.extend(speaker_turns)

    turns.sort(key=lambda turn: (turn.start, turn.speaker))
    header = (
        f"# AMI meeting {meeting}\n\n"
        "Verbatim transcript, speaker-labelled, in time order. Source: AMI "
        "Meeting Corpus manual annotations 1.6.2.\n\n"
    )
    body = "\n".join(f"{turn.speaker}: {turn.text}" for turn in turns)
    return header + body + "\n"


def build_summary(root: Path, meeting: str) -> str | None:
    """AMI's own abstractive summary, or `None` when the layer is absent."""
    paths = sorted(root.rglob(f"{meeting}.abssumm.xml"))
    if not paths:
        return None
    sections: list[str] = [f"# AMI meeting {meeting} — abstractive summary\n"]
    for element in _parse_xml(paths[0]).iter():
        tag = element.tag.rsplit("}", 1)[-1]
        if tag not in {"abstract", "decisions", "actions", "problems"}:
            continue
        lines = [
            (child.text or "").strip()
            for child in element
            if (child.text or "").strip()
        ]
        if not lines:
            continue
        sections.append(f"\n## {tag.title()}\n\n" + "\n".join(f"- {t}" for t in lines))
    return "\n".join(sections) + "\n" if len(sections) > 1 else None


def build_decisions_ground_truth(root: Path, meeting: str) -> str | None:
    """The human-written `decisions` section, verbatim, as reference material."""
    paths = sorted(root.rglob(f"{meeting}.abssumm.xml"))
    if not paths:
        return None
    for element in _parse_xml(paths[0]).iter():
        if element.tag.rsplit("}", 1)[-1] != "decisions":
            continue
        lines = [
            (child.text or "").strip()
            for child in element
            if (child.text or "").strip()
        ]
        if lines:
            return "\n".join(lines) + "\n"
    return None


def _resolve_root(args: argparse.Namespace, workdir: Path) -> Path:
    if args.extracted:
        root = Path(args.extracted).expanduser().resolve()
        if not root.is_dir():
            raise SystemExit(f"--extracted is not a directory: {root}")
        print(f"note: --extracted skips the checksum gate ({CHECKSUM_FILE.name})")
        return root
    zip_path = Path(args.zip).expanduser().resolve()
    if not zip_path.is_file():
        raise SystemExit(f"--zip is not a file: {zip_path}")
    _verify_zip(zip_path)
    print(f"extracting to {workdir}")
    with zipfile.ZipFile(zip_path) as archive:
        archive.extractall(workdir)
    return workdir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--zip", help="path to ami_public_manual_1.6.2.zip")
    group.add_argument("--extracted", help="directory of already-unpacked AMI")
    parser.add_argument("--meetings", nargs="+", help="meeting ids to build")
    parser.add_argument(
        "--all", action="store_true", help="build every meeting in the manifest"
    )
    parser.add_argument(
        "--workdir",
        default=str(ROOT / ".ami-unpacked"),
        help="where --zip is unpacked (default: .ami-unpacked, gitignored)",
    )
    args = parser.parse_args(argv)

    if args.all and args.meetings:
        raise SystemExit("--all and --meetings are mutually exclusive")
    meetings = _read_manifest() if args.all else list(args.meetings or DEFAULT_MEETINGS)

    workdir = Path(args.workdir).expanduser().resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    root = _resolve_root(args, workdir)

    SOURCES_DIR.mkdir(parents=True, exist_ok=True)
    GROUND_TRUTH_DIR.mkdir(parents=True, exist_ok=True)

    print(f"\nbuilding {len(meetings)} meeting(s): {', '.join(meetings)}\n")
    for meeting in meetings:
        transcript = build_transcript(root, meeting)
        transcript_path = SOURCES_DIR / f"{meeting}.transcript.txt"
        transcript_path.write_text(transcript, encoding="utf-8")
        turns = transcript.count("\n") - 3
        print(f"  {transcript_path.name}: {turns} turns, {len(transcript):,} chars")

        summary = build_summary(root, meeting)
        if summary is None:
            print(f"  {meeting}.summary.txt: SKIPPED (no abstractive layer found)")
        else:
            (SOURCES_DIR / f"{meeting}.summary.txt").write_text(
                summary, encoding="utf-8"
            )
            print(f"  {meeting}.summary.txt: {len(summary):,} chars")

        decisions = build_decisions_ground_truth(root, meeting)
        if decisions is None:
            print(f"  {meeting}.decisions.txt: SKIPPED (no decisions section)")
        else:
            (GROUND_TRUTH_DIR / f"{meeting}.decisions.txt").write_text(
                decisions, encoding="utf-8"
            )
            n = len(decisions.strip().splitlines())
            print(f"  ground_truth/{meeting}.decisions.txt: {n} annotated decision(s)")

    print(
        "\nNext: ingest these into a THROWAWAY workspace, never your real one --\n"
        "  mkdir -p ~/ami-probe && cd ~/ami-probe && openkos init\n"
        f"  openkos ingest '{SOURCES_DIR}/*.txt'\n"
        "then compare the type distribution against the nine CLASSIFIABLE_TYPES.\n"
        "Watch the per-source cap: _MAX_OBJECTS_PER_SOURCE is 6, and a 30-minute\n"
        "transcript will propose more, so read the 'cap reached' line before\n"
        "concluding anything about which types the extractor can reach."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
