"""Inputs for the concept-extraction model screen (#1557): meetings, windows, gold.

Everything here is deterministic and model-free. It answers four questions:

- **Which meetings may be read at all?** `ALLOWED_MEETINGS` is a fail-closed
  allowlist: the thesis's development series (ES2015, IS1004, TS3009) and the
  free pool of AMI meetings outside every thesis set. Every other meeting --
  the thesis's eleven evaluation series and its eighteen reserve series among
  them -- is refused by `require_allowed` before any file is opened. There is
  no override.
- **What text does the extractor see?** The rendered transcript body (the
  thesis renderer's speaker-turn markdown, frontmatter removed), cut into
  windows by the production `concept._chunk_lines`. Each window is identified
  by the sha256 of its bytes, so a stored run can be checked against the
  registered window list.
- **What is the gold?** AMI's human abstractive summary (`abstract`,
  `decisions`, `problems`; `actions` excluded, decision D5 of #1557).
- **Where does each gold sentence live?** `summlink` ties every summary
  sentence to the dialogue acts it summarizes; the turn index (`.jsonl`) gives
  each act a character offset, and so a window. A sentence goes to the window
  holding most of its linked acts, ties to the earliest.

The data is never committed: transcripts and the AMI archive are copied into
the runs directory outside both repositories and passed by flag.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import re
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final

AMI_ZIP_SHA256: Final = (
    "b56e5babb2496b8795deeeda7e71178d7fbc9963f94276cf2a3f4b56ebbc9f9d"
)
"""The exact AMI manual-annotations archive 1.6.2, as pinned by
`evals/decision_extraction/ami_public_manual_1.6.2.sha256`."""

DEV_SERIES: Final = ("ES2015", "IS1004", "TS3009")
"""The thesis's development series: spent on tuning, so usable here."""

FREE_POOL: Final = (
    "IS1002b",
    "IS1002c",
    "IS1002d",
    "IS1005a",
    "IS1005b",
    "IS1005c",
    "TS3012a",
    "TS3012b",
    "TS3012c",
    "TS3012d",
    "IB4003",
    "IB4005",
    "IB4010",
    "IB4011",
)
"""AMI meetings with an abstractive summary that are in NO thesis set."""

ALLOWED_MEETINGS: Final = frozenset(
    {f"{series}{letter}" for series in DEV_SERIES for letter in "abcd"} | set(FREE_POOL)
)

THESIS_EVALUATION_SERIES: Final = (
    "ES2002",
    "ES2008",
    "ES2014",
    "ES2016",
    "IS1003",
    "IS1006",
    "IS1008",
    "IS1009",
    "TS3003",
    "TS3005",
    "TS3011",
)
"""Named only so the refusal can say WHY; the allowlist is what refuses."""

POOL_DA: Final = ("ES2015a", "IS1004b", "TS3009c", "ES2015d", "IS1004d")
"""Stage A selection pool: five whole development meetings (#1557 design 3.2)."""

POOL_F_WINDOWED: Final = (
    "IS1002b",
    "IS1002c",
    "IS1002d",
    "IS1005a",
    "IS1005b",
    "IS1005c",
    "TS3012a",
    "TS3012b",
    "TS3012d",
)
"""Confirmation meetings with `summlink` (window-level gold)."""

POOL_F_MEETING_ONLY: Final = ("IB4003", "IB4005", "IB4010", "IB4011")
"""Confirmation meetings without `summlink`: meeting-level score only."""

STAGE0_WINDOWS: Final = (("ES2015b", 1), ("IS1004c", 4), ("TS3009b", 6))
"""(meeting, 0-based window index) timed in stage 0. Deliberately taken from
development meetings that are NOT in `POOL_DA`, so the calibration calls can
never be reused as, or confused with, a scored item."""

GOLD_SECTIONS: Final = ("abstract", "decisions", "problems")
"""`actions` is excluded from the primary metric (decision D5)."""

_FOREIGN_CYCLES: Final = (
    ("ES2015", "IS1004", "TS3009"),
    ("IS1002", "IS1005", "TS3012"),
    ("IB4003", "IB4005", "IB4010", "IB4011"),
)

_MEETING_RE: Final = re.compile(r"^(?P<series>[A-Z]{2}\d{4})(?P<letter>[a-d]?)$")


class DisallowedMeeting(ValueError):
    """A meeting outside `ALLOWED_MEETINGS` was requested."""


def require_allowed(meeting: str) -> str:
    """Return `meeting` if it may be read, else raise `DisallowedMeeting`."""
    if meeting in ALLOWED_MEETINGS:
        return meeting
    match = _MEETING_RE.match(meeting)
    series = match.group("series") if match else None
    if series in THESIS_EVALUATION_SERIES:
        reason = "it belongs to a thesis EVALUATION series"
    elif match is None:
        reason = "its id does not parse"
    else:
        reason = "it is not on the allowlist (thesis reserve or unused)"
    raise DisallowedMeeting(f"refusing meeting {meeting!r}: {reason}")


def series_of(meeting: str) -> str:
    match = _MEETING_RE.match(meeting)
    if match is None:
        raise DisallowedMeeting(f"meeting id does not parse: {meeting!r}")
    return match.group("series")


def letter_of(meeting: str) -> str:
    match = _MEETING_RE.match(meeting)
    if match is None:
        raise DisallowedMeeting(f"meeting id does not parse: {meeting!r}")
    return match.group("letter")


def foreign_meeting(meeting: str) -> str:
    """The meeting whose gold is the null control for `meeting`.

    Same letter, the NEXT series in the meeting's cycle that has that letter
    on the allowlist (the thesis's cross-team control). IB meetings have no
    letter and cycle among themselves."""
    require_allowed(meeting)
    series = series_of(meeting)
    letter = letter_of(meeting)
    for cycle in _FOREIGN_CYCLES:
        if series in cycle:
            start = cycle.index(series)
            for step in range(1, len(cycle)):
                candidate = f"{cycle[(start + step) % len(cycle)]}{letter}"
                if candidate in ALLOWED_MEETINGS:
                    return candidate
            break
    raise DisallowedMeeting(f"no foreign meeting for {meeting!r}")


# --- transcripts -------------------------------------------------------------


@dataclass(frozen=True)
class Turn:
    char_start: int
    char_end: int
    dialogue_act_ids: tuple[str, ...]


@dataclass(frozen=True)
class Transcript:
    meeting: str
    body: str
    turns: tuple[Turn, ...]


def strip_frontmatter(text: str) -> str:
    """The rendered body: everything after the closing `---` and its blank line."""
    if not text.startswith("---\n"):
        raise ValueError("transcript has no frontmatter")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise ValueError("transcript frontmatter is not closed")
    return text[end + len("\n---\n") :].lstrip("\n")


def parse_transcript(meeting: str, md_text: str, jsonl_text: str) -> Transcript:
    """Pair the body with its turn index, checking every offset against the body."""
    require_allowed(meeting)
    body = strip_frontmatter(md_text)
    turns: list[Turn] = []
    for line in jsonl_text.splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        turn = Turn(
            char_start=int(row["char_start"]),
            char_end=int(row["char_end"]),
            dialogue_act_ids=tuple(str(x) for x in row["dialogue_act_ids"]),
        )
        if body[turn.char_start : turn.char_end] != row["text"]:
            raise ValueError(
                f"{meeting}: turn {row.get('turn_index')} offsets do not match the body"
            )
        turns.append(turn)
    return Transcript(meeting=meeting, body=body, turns=tuple(turns))


def load_transcript(directory: pathlib.Path, meeting: str) -> Transcript:
    require_allowed(meeting)
    md = (directory / f"{meeting}.md").read_text(encoding="utf-8")
    jsonl = (directory / f"{meeting}.jsonl").read_text(encoding="utf-8")
    return parse_transcript(meeting, md, jsonl)


# --- windows -----------------------------------------------------------------


@dataclass(frozen=True)
class Window:
    meeting: str
    index: int
    start: int
    """Character offset of the window's first character in the body."""
    text: str

    @property
    def end(self) -> int:
        return self.start + len(self.text)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


def build_windows(
    meeting: str, body: str, chunk: Callable[[str], list[str]]
) -> list[Window]:
    """Windows with their offsets. `chunk` is production `_chunk_lines`, which
    is lossless: the windows joined by newline are the body, which is what
    makes the offsets exact. That property is checked, not assumed."""
    texts = chunk(body)
    if "\n".join(texts) != body:
        raise ValueError(f"{meeting}: the chunker is not lossless on this body")
    windows: list[Window] = []
    offset = 0
    for index, text in enumerate(texts):
        windows.append(Window(meeting=meeting, index=index, start=offset, text=text))
        offset += len(text) + 1
    return windows


def window_of_offset(windows: Sequence[Window], offset: int) -> int:
    for window in windows:
        if window.start <= offset < window.end + 1:
            return window.index
    raise ValueError(f"offset {offset} lies outside every window")


# --- gold --------------------------------------------------------------------


@dataclass(frozen=True)
class GoldSentence:
    sentence_id: str
    section: str
    text: str
    act_ids: tuple[str, ...] = ()


@dataclass
class MeetingGold:
    meeting: str
    sentences: list[GoldSentence]
    has_summlink: bool
    window_of: dict[str, int | None] = field(default_factory=dict)
    """sentence id -> assigned window (None: no linked act, or no summlink)."""

    def window_sentences(self, index: int) -> list[GoldSentence]:
        return [s for s in self.sentences if self.window_of.get(s.sentence_id) == index]


_NITE_ID: Final = "{http://nite.sourceforge.net/}id"
_HREF_ID_RE: Final = re.compile(r"#id\(([^)]+)\)")


def parse_abstractive(xml_bytes: bytes) -> list[GoldSentence]:
    """Gold sentences of `GOLD_SECTIONS`, in document order."""
    root = ET.fromstring(xml_bytes)  # noqa: S314 -- pinned, checksummed AMI archive
    out: list[GoldSentence] = []
    for section in root:
        if section.tag not in GOLD_SECTIONS:
            continue
        for sentence in section.iter("sentence"):
            text = " ".join("".join(sentence.itertext()).split())
            sentence_id = sentence.get(_NITE_ID)
            if text and sentence_id:
                out.append(GoldSentence(sentence_id, section.tag, text))
    return out


def parse_summlink(xml_bytes: bytes) -> dict[str, list[str]]:
    """abstractive sentence id -> linked dialogue-act ids, in file order."""
    root = ET.fromstring(xml_bytes)  # noqa: S314 -- pinned, checksummed AMI archive
    links: dict[str, list[str]] = {}
    for link in root.iter("summlink"):
        extractive: list[str] = []
        abstractive: list[str] = []
        for pointer in link:
            match = _HREF_ID_RE.search(pointer.get("href", ""))
            if match is None:
                continue
            if pointer.get("role") == "extractive":
                extractive.append(match.group(1))
            elif pointer.get("role") == "abstractive":
                abstractive.append(match.group(1))
        for sentence_id in abstractive:
            links.setdefault(sentence_id, []).extend(extractive)
    return links


def assign_windows(
    sentences: Sequence[GoldSentence],
    links: Mapping[str, Sequence[str]],
    act_window: Mapping[str, int],
) -> dict[str, int | None]:
    """The registered assignment rule: majority window of the linked acts,
    ties to the EARLIEST window; no resolvable act means no window."""
    out: dict[str, int | None] = {}
    for sentence in sentences:
        counts = Counter(
            act_window[act]
            for act in links.get(sentence.sentence_id, ())
            if act in act_window
        )
        if not counts:
            out[sentence.sentence_id] = None
            continue
        top = max(counts.values())
        out[sentence.sentence_id] = min(w for w, c in counts.items() if c == top)
    return out


def act_windows(transcript: Transcript, windows: Sequence[Window]) -> dict[str, int]:
    out: dict[str, int] = {}
    for turn in transcript.turns:
        index = window_of_offset(windows, turn.char_start)
        for act in turn.dialogue_act_ids:
            out[act] = index
    return out


class AmiArchive:
    """Read-only access to the pinned AMI zip, gated by the allowlist."""

    def __init__(self, path: pathlib.Path, *, verify: bool = True) -> None:
        if verify:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest != AMI_ZIP_SHA256:
                raise ValueError(f"AMI archive checksum mismatch: {digest}")
        self._zip = zipfile.ZipFile(path)
        self._names = set(self._zip.namelist())

    def abstractive(self, meeting: str) -> bytes:
        require_allowed(meeting)
        return self._zip.read(f"abstractive/{meeting}.abssumm.xml")

    def summlink(self, meeting: str) -> bytes | None:
        require_allowed(meeting)
        name = f"extractive/{meeting}.summlink.xml"
        return self._zip.read(name) if name in self._names else None


def meeting_gold(
    archive: AmiArchive,
    meeting: str,
    transcript: Transcript | None,
    windows: Sequence[Window] | None,
) -> MeetingGold:
    """Gold for one meeting; window assignment when a transcript and summlink exist."""
    sentences = parse_abstractive(archive.abstractive(meeting))
    link_bytes = archive.summlink(meeting)
    gold = MeetingGold(
        meeting=meeting, sentences=sentences, has_summlink=link_bytes is not None
    )
    if link_bytes is not None and transcript is not None and windows is not None:
        links = parse_summlink(link_bytes)
        gold.sentences = [
            GoldSentence(
                s.sentence_id, s.section, s.text, tuple(links.get(s.sentence_id, ()))
            )
            for s in sentences
        ]
        gold.window_of = assign_windows(
            gold.sentences, links, act_windows(transcript, windows)
        )
    else:
        gold.window_of = {s.sentence_id: None for s in sentences}
    return gold
