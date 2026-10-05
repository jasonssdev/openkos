"""The Quiet Engine arc's product metrics: decisions per source, and time to a cited answer (#1268).

#1268 (deliverable 6) asks for two product metrics measured before and after
the arc on one committed corpus, with bars fixed before the arc starts:

- **(a) human decisions per ingested source**, and
- **(b) time from dropping a file into the inbox to the first answer that
  can cite it**,

plus the arc's secondary exit checks: `-N` duplicates for same-type,
same-key concepts outside the excluded types, false sufficiency refusals
attributable to a stale index, and every automatic action listed with its
undo. `PREREGISTRATION.md` beside this file defines each one; this module is
the mechanical reading of those definitions.

WHAT IS DRIVEN. A real `openkos` executable, given as an argv prefix
(`--cli`), so the same harness drives v0.4.0 from one checkout and main from
another. Nothing here imports the CLI under test: the only engine code this
module imports is `openkos.model.okf` from the harness's OWN checkout, to read
frontmatter -- a format-level parse that does not depend on which version
wrote the bundle.

ONE RUN, IN ORDER (`run_once`):

1. `init` in a fresh directory, then append the `unattended:` block (and, for
   the attribution arm, a `models:` block pinning both judges).
2. `ingest <corpus>/sources --auto` -- the 20 batch sources.
3. `daemon --once` -- the first maintenance pass; the pending queue is read.
4. `daemon` (foreground, in the background of this process).
5. Drop probe v1 into the inbox; poll `query` until an answer cites it, or
   the time-to-cite timeout (metric b).
6. Overwrite the probe with v2 (a watched source edited: supersession and the
   #1259 re-extraction path).
7. Ask the five manifest questions once; stop the daemon; `reindex`; ask
   the ones that failed again (the stale-index check).
8. Scan the bundle (`-N` families), the git log (automatic actions) and the
   pending queue.
9. `curate --auto` under a pseudo-terminal, answering EVERY per-item prompt
   with Enter -- the default, which never accepts (metric a).

NOTHING IS EVER ACCEPTED. Enter is `N` on every `[y/N...]` prompt and skip on
the identity prompt that has one. A cost gate (`Proceed?`) is the one prompt
answered `y`: it consents to model spend, never to a write, and `--auto`
should leave none to answer -- one that appears is counted and flags the run.

Usage:

    uv run python evals/quiet_engine/run_quiet_engine_eval.py --plan
    uv run python -u evals/quiet_engine/run_quiet_engine_eval.py \\
        --arm v0.4.0 --cli "/path/to/v0.4.0/.venv/bin/openkos" --runs 3
    uv run python -u evals/quiet_engine/run_quiet_engine_eval.py \\
        --arm main --cli "uv run --project /path/to/main openkos" \\
        --checkout /path/to/main --runs 3
    uv run python evals/quiet_engine/run_quiet_engine_eval.py \\
        --report results/runs-v0.4.0-*.json results/runs-main-*.json
    uv run python evals/quiet_engine/run_quiet_engine_eval.py --self-test
"""

from __future__ import annotations

import argparse
import codecs
import contextlib
import hashlib
import json
import os
import re
import shlex
import signal
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
import unicodedata
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.append(str(REPO_ROOT / "evals"))

from harness_report import arm_identity_line  # noqa: E402
from harness_stamp import build_stamp, model_identity, stamp_report_line  # noqa: E402

PROTOCOL_VERSION: Final = 1
"""Bumped whenever a phase, an answer policy or a metric definition changes:
two runs-*.json files with different protocol versions are not comparable."""

CORPUS_DIR: Final = HERE / "corpus"
RESULTS_DIR: Final = HERE / "results"

EXCLUDED_TYPES: Final = frozenset({"Event", "Person"})
"""Types the arc keeps forking at ingest (ADR-0044, ADR-0045): their `-N`
families are reported, never counted against the exit bar."""

NOT_CONCEPT_TYPES: Final = frozenset({"Source"})
"""A new version of a source is a new Source by design (ADR-0041), so a
`sources/<stem>-N` document is not a duplicate."""

TEXT_SUFFIXES: Final = frozenset(
    {".md", ".markdown", ".txt", ".text", ".rst", ".org", ".adoc", ".vtt", ".srt"}
)

ARMS: Final[dict[str, dict[str, str | None]]] = {
    "v0.4.0": {"judge_model": "qwen3:8b", "pin_judges": None, "version": "0.4.0"},
    "main": {"judge_model": "gemma4:26b-a4b", "pin_judges": None, "version": None},
    "main-judges-qwen3": {
        "judge_model": "qwen3:8b",
        "pin_judges": "qwen3:8b",
        "version": None,
    },
}
"""The pre-registered arms. `judge_model` is what the arm's judges resolve to
(stamped, not configured); `pin_judges` is written into `models:`; `version`,
when set, must appear in `openkos --version` or the arm refuses to run."""

DEFAULT_CHAT_MODEL: Final = "qwen3:8b"
DEFAULT_EMBEDDING_MODEL: Final = "bge-m3"


@dataclass(frozen=True)
class Timeouts:
    """Every wait in a run, in seconds. The defaults are the pre-registered
    ones; the self-test shrinks them."""

    ingest: float = 3 * 3600
    maintenance: float = 2 * 3600
    cite: float = 1800
    """Metric (b)'s censoring point: no citing answer by then is `> cite`."""
    poll_interval: float = 20
    """Minimum seconds between the STARTS of two time-to-cite queries."""
    landing_poll: float = 0.5
    imported: float = 1800
    query: float = 900
    reindex: float = 1800
    curate: float = 4 * 3600
    stall: float = 120
    """Silence after a prompt-shaped line before it is answered as
    unrecognized (and the run flagged)."""
    daemon_stop: float = 900


# ---------------------------------------------------------------------------
# Corpus
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Question:
    id: str
    role: str
    question: str
    answer_token: str
    expected_files: tuple[str, ...]


@dataclass(frozen=True)
class Manifest:
    root: Path
    sources_dir: Path
    probe_name: str
    probe_v1: Path
    probe_v2: Path
    fed_files: int
    questions: tuple[Question, ...]

    @property
    def cite_question(self) -> Question:
        return next(q for q in self.questions if q.role == "time_to_cite")


def load_manifest(root: Path = CORPUS_DIR) -> Manifest:
    raw = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    probe = raw["probe"]
    return Manifest(
        root=root,
        sources_dir=root / raw["sources_dir"],
        probe_name=probe["file_name"],
        probe_v1=root / probe["v1"],
        probe_v2=root / probe["v2"],
        fed_files=int(raw["fed_files"]),
        questions=tuple(
            Question(
                id=q["id"],
                role=q["role"],
                question=q["question"],
                answer_token=q["answer_token"],
                expected_files=tuple(q["expected_files"]),
            )
            for q in raw["questions"]
        ),
    )


def corpus_files(manifest: Manifest) -> list[Path]:
    return sorted(
        p for p in manifest.sources_dir.iterdir() if p.suffix.lower() in TEXT_SUFFIXES
    )


def corpus_digest(manifest: Manifest) -> str:
    """sha256 over every corpus file's relative path and bytes, plus the
    manifest: the identity of what was measured."""
    digest = hashlib.sha256()
    paths = sorted(p for p in manifest.root.rglob("*") if p.is_file())
    for path in paths:
        digest.update(str(path.relative_to(manifest.root)).encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()[:16]


def corpus_problems(
    manifest: Manifest,
    *,
    expected_sources: int = 20,
    min_bytes: int = 2000,
    max_bytes: int = 10_240,
) -> list[str]:
    """Why the committed corpus is not the one the pre-registration describes
    (empty means it is). Run by the self-test against the real corpus."""
    problems: list[str] = []
    files = corpus_files(manifest)
    if len(files) != expected_sources:
        problems.append(f"{len(files)} sources, expected {expected_sources}")
    for path in files:
        size = path.stat().st_size
        if not min_bytes <= size <= max_bytes:
            problems.append(
                f"{path.name}: {size} bytes outside {min_bytes}-{max_bytes}"
            )
    if manifest.probe_v1.name != manifest.probe_name:
        problems.append("probe v1 is not named like the probe")
    if manifest.probe_v2.name != manifest.probe_name:
        problems.append("probe v2 is not named like the probe (it must overwrite v1)")
    if manifest.probe_v1.read_bytes() == manifest.probe_v2.read_bytes():
        problems.append("probe v2 is byte-identical to v1: no new version")
    if manifest.fed_files != expected_sources + 2:
        problems.append(f"fed_files {manifest.fed_files} != sources + 2")
    if sum(q.role == "time_to_cite" for q in manifest.questions) != 1:
        problems.append("exactly one time_to_cite question is required")
    every = [*files, manifest.probe_v1, manifest.probe_v2]
    for q in manifest.questions:
        holders = {
            str(p.relative_to(manifest.root))
            for p in every
            if q.answer_token in p.read_text(encoding="utf-8")
        }
        if holders != set(q.expected_files):
            problems.append(
                f"{q.id}: answer token found in {sorted(holders)}, "
                f"expected exactly {sorted(q.expected_files)}"
            )
    return problems


# ---------------------------------------------------------------------------
# Text parsing (pure)
# ---------------------------------------------------------------------------

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_CITATION = re.compile(r"^\s*→ (\S+) \(")
_REFUSAL = "none of them answers this question"
_STALE = "derived indexes are stale"
_ITEM_PROMPT = re.compile(
    r"(\[y/N(?:/[a-z])*\]|\[y\]es / \[s\]kip / \[d\]istinct[^\n]*?)\s*:\s*$"
)
_GATE_PROMPT = re.compile(r"^\s*Proceed\?")
_QUESTION_TAIL = re.compile(r"[:?\]]\s*$")
_STAGE_MARKER = re.compile(
    r"openkos curate: (Preconditions|Identity|Structure|Metadata|Contradictions): checking"
)
_MANUAL_MERGE = re.compile(
    r"openkos merge (?:--include-cross-type )?([^\s`]+) ([^\s`]+)"
)
_CAP_NOTICE = re.compile(r"(\d+) of (\d+) [^\n]*?\(cap reached\)")
_WATCH_REPORT = re.compile(
    r"openkos daemon: watch: (\S+)(?: \([^)]*\))? -- calls=(\d+) done=(\d+) deferred=(\d+)"
)
_UNDO = re.compile(r"git revert|openkos (?:unmerge|forget|unrelate)\b")


def strip_ansi(text: str) -> str:
    return _ANSI.sub("", text)


def parse_citations(stdout: str) -> list[str]:
    """The concept ids under a `Citations:` block (the format is identical in
    v0.4.0 and main: `  → <id> (<title>)<markers>`)."""
    ids: list[str] = []
    in_block = False
    for line in strip_ansi(stdout).splitlines():
        if line.strip() == "Citations:":
            in_block = True
            continue
        if in_block:
            match = _CITATION.match(line)
            if match:
                ids.append(match.group(1))
            elif line.strip():
                in_block = False
    return ids


def is_refusal(stdout: str) -> bool:
    """The sufficiency refusal (`insufficient_context`), not a zero-hit miss."""
    return _REFUSAL in stdout


def is_stale(stderr: str) -> bool:
    return _STALE in stderr


def classify_prompt(tail: str) -> str | None:
    """`"item"` for a per-item decision prompt, `"gate"` for a cost gate,
    `None` for anything else. `tail` is the current, unterminated line."""
    line = strip_ansi(tail)
    if not _ITEM_PROMPT.search(line):
        return None
    return "gate" if _GATE_PROMPT.match(line) else "item"


def looks_like_question(tail: str) -> bool:
    return bool(tail.strip()) and bool(_QUESTION_TAIL.search(strip_ansi(tail)))


def stage_at(text: str, offset: int) -> str:
    """The curate stage whose marker last appeared before `offset`."""
    stage = "(before any stage)"
    for match in _STAGE_MARKER.finditer(text, 0, offset):
        stage = match.group(1)
    return stage


def manual_merge_commands(text: str) -> list[str]:
    """Distinct `openkos merge <a> <b>` commands printed in the Identity stage
    (a >2-member group or a cross-type pair: a decision with no prompt)."""
    seen: dict[str, None] = {}
    for line in strip_ansi(text).splitlines():
        for match in _MANUAL_MERGE.finditer(line):
            seen[f"{match.group(1)} {match.group(2)}"] = None
    return list(seen)


def hidden_by_caps(text: str) -> int:
    """Sum of `M - N` over every `N of M ... (cap reached)` notice: candidate
    work a cap kept off the screen (#1265)."""
    return sum(int(m) - int(n) for n, m in _CAP_NOTICE.findall(strip_ansi(text)))


def watch_reports(text: str) -> list[tuple[str, int]]:
    """`(outcome, done)` for every daemon `watch` job line in `text`."""
    return [(o, int(done)) for o, _c, done, _d in _WATCH_REPORT.findall(text)]


# ---------------------------------------------------------------------------
# Bundle reading
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Doc:
    id: str
    type: str
    title: str
    provenance: tuple[str, ...]
    status: str
    resource: str


_RESERVED = frozenset({"index.md", "log.md"})


def scan_bundle(workspace: Path) -> dict[str, Doc]:
    """Every non-reserved bundle document, by Concept ID."""
    from openkos.model import okf  # deferred: the fake CLI must start fast

    bundle = workspace / "bundle"
    docs: dict[str, Doc] = {}
    if not bundle.is_dir():
        return docs
    for path in sorted(bundle.rglob("*.md")):
        if path.name in _RESERVED or ".state" in path.parts:
            continue
        try:
            parsed = okf.try_load_frontmatter(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue
        meta = parsed[0] if parsed is not None else {}
        provenance = meta.get("provenance")
        concept_id = path.relative_to(bundle).with_suffix("").as_posix()
        docs[concept_id] = Doc(
            id=concept_id,
            type=str(meta.get("type") or ""),
            title=str(meta.get("title") or ""),
            provenance=tuple(str(p) for p in provenance)
            if isinstance(provenance, list)
            else (),
            status=str(meta.get("status") or ""),
            resource=str(meta.get("resource") or ""),
        )
    return docs


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sources_by_hash(workspace: Path, docs: Mapping[str, Doc]) -> dict[str, set[str]]:
    """sha256 of a Source's raw file -> the Source ids resting on those bytes.
    Matching by BYTES, not by name, is what lets a renamed `raw/<stem>-N`
    version be found without knowing either version's naming rule."""
    out: dict[str, set[str]] = defaultdict(set)
    for doc in docs.values():
        if doc.type != "Source" or not doc.resource:
            continue
        raw = workspace / doc.resource
        with contextlib.suppress(OSError):
            out[sha256_bytes(raw.read_bytes())].add(doc.id)
    return out


def expected_source_ids(
    workspace: Path, docs: Mapping[str, Doc], manifest: Manifest, question: Question
) -> set[str]:
    by_hash = sources_by_hash(workspace, docs)
    ids: set[str] = set()
    for rel in question.expected_files:
        ids |= by_hash.get(sha256_bytes((manifest.root / rel).read_bytes()), set())
    return ids


def cites_expected(
    citations: Iterable[str], docs: Mapping[str, Doc], expected: set[str]
) -> bool:
    """A citation of an expected Source, or of a document whose provenance
    names one."""
    if not expected:
        return False
    for cid in citations:
        if cid in expected:
            return True
        doc = docs.get(cid)
        if doc is not None and expected & set(doc.provenance):
            return True
    return False


def normalize_key(title: str) -> str:
    """Frozen copy of `openkos.resolution.normalize.normalize_key` (the key
    ADR-0045 attaches on), so the metric does not move when the engine's
    copy does. The self-test asserts they still agree."""
    decomposed = unicodedata.normalize("NFKD", title)
    without_marks = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    folded = without_marks.casefold()
    spaced = "".join(ch if ch.isalnum() else " " for ch in folded)
    return " ".join(spaced.split())


_SUFFIX = re.compile(r"^(.*)-(\d+)$")


def suffix_duplicates(docs: Mapping[str, Doc]) -> dict[str, Any]:
    """`-N` duplicates: within each (type, normalized title key) group, the
    ids that share one base once a trailing `-<digits>` is stripped form a
    family, and a family of k members is k - 1 duplicates. Source documents
    are never counted (a new version is a new Source by design); Event and
    Person are counted separately (`excluded`)."""
    groups: dict[tuple[str, str], list[str]] = defaultdict(list)
    for doc in docs.values():
        if doc.type in NOT_CONCEPT_TYPES or not doc.type:
            continue
        key = normalize_key(doc.title)
        if key:
            groups[(doc.type, key)].append(doc.id)
    counted: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for (type_name, key), ids in sorted(groups.items()):
        families: dict[str, list[str]] = defaultdict(list)
        for cid in ids:
            match = _SUFFIX.match(cid)
            families[match.group(1) if match else cid].append(cid)
        for base, members in sorted(families.items()):
            if len(members) < 2:
                continue
            entry = {
                "type": type_name,
                "key": key,
                "base": base,
                "members": sorted(members),
                "duplicates": len(members) - 1,
                "deprecated": sorted(
                    m for m in members if docs[m].status in ("deprecated", "superseded")
                ),
            }
            (excluded if type_name in EXCLUDED_TYPES else counted).append(entry)
    return {
        "counted": counted,
        "excluded": excluded,
        "count": sum(e["duplicates"] for e in counted),
        "excluded_count": sum(e["duplicates"] for e in excluded),
    }


def read_pending(workspace: Path) -> dict[str, Any]:
    """Open pending-queue rows by kind, read-only (the table is unchanged
    between v0.4.0 and main). `present: false` means not computed."""
    path = workspace / ".openkos" / "findings.db"
    if not path.exists():
        return {"present": False, "open": 0, "by_kind": {}}
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        return {"present": False, "open": 0, "by_kind": {}, "error": str(exc)}
    try:
        has = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='pending_items'"
        ).fetchone()
        if not has:
            return {"present": False, "open": 0, "by_kind": {}}
        rows = conn.execute(
            "SELECT kind, COUNT(*) FROM pending_items "
            "WHERE status IN ('pending','claimed') GROUP BY kind"
        ).fetchall()
    except sqlite3.Error as exc:
        return {"present": False, "open": 0, "by_kind": {}, "error": str(exc)}
    finally:
        conn.close()
    by_kind = {str(k): int(n) for k, n in rows}
    return {"present": True, "open": sum(by_kind.values()), "by_kind": by_kind}


# ---------------------------------------------------------------------------
# Processes
# ---------------------------------------------------------------------------


def child_env(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """The environment every CLI child gets: unbuffered output, a pinned git
    identity (`GIT_CONFIG_COUNT` satisfies `git config`; `GIT_AUTHOR_*` does
    not), and a wide terminal so wrapped prose never splits a command."""
    env = dict(os.environ)
    env.update(
        {
            "PYTHONUNBUFFERED": "1",
            "GIT_CONFIG_COUNT": "2",
            "GIT_CONFIG_KEY_0": "user.name",
            "GIT_CONFIG_VALUE_0": "quiet-engine-eval",
            "GIT_CONFIG_KEY_1": "user.email",
            "GIT_CONFIG_VALUE_1": "quiet-engine-eval@localhost",
            "COLUMNS": "1000",
        }
    )
    if extra:
        env.update(extra)
    return env


@dataclass
class Completed:
    args: list[str]
    code: int | None
    stdout: str
    stderr: str
    elapsed_s: float
    timed_out: bool = False


def run_cli(
    cli: Sequence[str],
    args: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    timeout: float,
) -> Completed:
    argv = [*cli, *args]
    started = time.monotonic()
    try:
        done = subprocess.run(  # noqa: S603 -- the operator's own CLI, argv list
            argv,
            cwd=cwd,
            env=dict(env),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return Completed(
            list(args),
            None,
            _as_text(exc.stdout),
            _as_text(exc.stderr),
            time.monotonic() - started,
            timed_out=True,
        )
    return Completed(
        list(args),
        done.returncode,
        done.stdout,
        done.stderr,
        time.monotonic() - started,
    )


def _as_text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return value if isinstance(value, str) else ""


@dataclass
class Prompt:
    stage: str
    kind: str
    text: str


@dataclass
class TtyResult:
    code: int | None
    transcript: str
    prompts: list[Prompt]
    elapsed_s: float
    timed_out: bool


def _set_winsize(fd: int, cols: int = 1000, rows: int = 60) -> None:
    import fcntl
    import struct
    import termios

    with contextlib.suppress(OSError):
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def drive_tty(
    argv: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    timeout: float,
    stall: float,
) -> TtyResult:
    """Run `argv` on a pseudo-terminal, answering every prompt it presents.

    Per-item prompts and unrecognized prompt-shaped lines get Enter (never an
    accept); a cost gate gets `y` (spend, not a write). Each answered prompt
    is recorded with the curate stage it appeared in."""
    import pty
    import select

    master, slave = pty.openpty()
    _set_winsize(slave)
    started = time.monotonic()
    proc = subprocess.Popen(  # noqa: S603 -- the operator's own CLI, argv list
        list(argv),
        cwd=cwd,
        env=dict(env),
        stdin=slave,
        stdout=slave,
        stderr=slave,
        start_new_session=True,
        close_fds=True,
    )
    os.close(slave)
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    text = ""
    prompts: list[Prompt] = []
    answered_at = -1
    last_output = time.monotonic()
    timed_out = False
    try:
        while True:
            ready, _, _ = select.select([master], [], [], 0.1)
            if ready:
                try:
                    data = os.read(master, 65536)
                except OSError:
                    data = b""
                if not data:
                    break
                text += decoder.decode(data)
                last_output = time.monotonic()
            elif proc.poll() is not None:
                # Child gone and nothing readable: drain once more, then stop.
                continue_reading = select.select([master], [], [], 0.2)[0]
                if not continue_reading:
                    break
            line_start = max(text.rfind("\n"), text.rfind("\r")) + 1
            if line_start != answered_at:
                tail = text[line_start:]
                kind = classify_prompt(tail)
                silent = time.monotonic() - last_output
                if kind is None and silent >= stall and looks_like_question(tail):
                    kind = "unrecognized"
                if kind is not None and (kind != "item" or silent >= 0.05):
                    os.write(master, b"y\n" if kind == "gate" else b"\n")
                    prompts.append(
                        Prompt(stage_at(text, line_start), kind, tail.strip())
                    )
                    answered_at = line_start
            if time.monotonic() - started > timeout:
                timed_out = True
                _kill_group(proc)
                break
    finally:
        with contextlib.suppress(OSError):
            os.close(master)
    try:
        code = proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        code = None
    return TtyResult(
        code, strip_ansi(text), prompts, time.monotonic() - started, timed_out
    )


def _kill_group(proc: subprocess.Popen[Any]) -> None:
    with contextlib.suppress(OSError, ProcessLookupError):
        os.killpg(proc.pid, signal.SIGKILL)


class Daemon:
    """`openkos daemon` running in the background, its combined output in a
    file this process re-reads (no pipe to fill, no reader thread)."""

    def __init__(
        self, cli: Sequence[str], *, cwd: Path, env: Mapping[str, str], log: Path
    ) -> None:
        self.log = log
        self._handle = log.open("wb")
        self.proc = subprocess.Popen(  # noqa: S603 -- the operator's own CLI
            [*cli, "daemon"],
            cwd=cwd,
            env=dict(env),
            stdin=subprocess.DEVNULL,
            stdout=self._handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    def text(self) -> str:
        try:
            return self.log.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    def stop(self, timeout: float) -> int | None:
        if self.proc.poll() is None:
            # The whole group: a `uv run` wrapper must not swallow the stop.
            with contextlib.suppress(OSError, ProcessLookupError):
                os.killpg(self.proc.pid, signal.SIGTERM)
            try:
                self.proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                _kill_group(self.proc)
                self.proc.wait(timeout=30)
        self._handle.close()
        return self.proc.returncode


def wait_until(predicate: Callable[[], bool], timeout: float, interval: float) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        if predicate():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(interval)


def drop_file(source: Path, inbox: Path, name: str) -> float:
    """Place `source`'s bytes at `inbox/name` atomically and return the
    monotonic time of the drop. The temporary name carries a non-text
    extension, so the watch's allowlist never sees a half-written file."""
    partial = inbox / f"{name}.part"
    partial.write_bytes(source.read_bytes())
    partial.replace(inbox / name)
    return time.monotonic()


def git(workspace: Path, *args: str) -> str:
    done = subprocess.run(  # noqa: S603 -- fixed git argv
        ["git", *args],  # noqa: S607 -- git from PATH, as the CLI under test uses
        cwd=workspace,
        capture_output=True,
        text=True,
        check=False,
        env=child_env(),
    )
    return done.stdout.strip() if done.returncode == 0 else ""


def inside_git_worktree(path: Path) -> bool:
    return git(path, "rev-parse", "--is-inside-work-tree") == "true"


def automatic_actions(
    workspace: Path, since: str, until: str, daemon_text: str
) -> list[dict[str, Any]]:
    """Every commit the daemon made, and whether its own output listed it with
    an undo: a line naming the commit's short sha AND an undo command, or,
    for every bundle document the commit added or removed, a line naming that
    document AND an undo command."""
    if not since or not until or since == until:
        return []
    shas = git(workspace, "rev-list", "--reverse", f"{since}..{until}").split()
    undo_lines = [line for line in daemon_text.splitlines() if _UNDO.search(line)]
    actions: list[dict[str, Any]] = []
    for sha in shas:
        subject = git(workspace, "log", "-1", "--format=%s", sha)
        changed = git(workspace, "show", "--name-status", "--format=", sha).splitlines()
        touched = sorted(
            Path(parts[-1]).relative_to("bundle").with_suffix("").as_posix()
            for parts in (line.split("\t") for line in changed)
            if len(parts) >= 2
            and parts[0][:1] in ("A", "D")
            and parts[-1].startswith("bundle/")
            and parts[-1].endswith(".md")
            and Path(parts[-1]).name not in _RESERVED
        )
        by_sha = any(sha[:7] in line for line in undo_lines)
        by_ids = bool(touched) and all(
            any(cid in line for line in undo_lines) for cid in touched
        )
        actions.append(
            {
                "sha": sha,
                "subject": subject,
                "touched": touched,
                "listed_with_undo": by_sha or by_ids,
            }
        )
    return actions


# ---------------------------------------------------------------------------
# One run
# ---------------------------------------------------------------------------


@dataclass
class RunConfig:
    cli: list[str]
    manifest: Manifest
    run_dir: Path
    chat_model: str = DEFAULT_CHAT_MODEL
    embedding_model: str = DEFAULT_EMBEDDING_MODEL
    pin_judges: str | None = None
    timeouts: Timeouts = field(default_factory=Timeouts)
    env_extra: dict[str, str] = field(default_factory=dict)
    log: Callable[[str], None] = print


def configure_workspace(workspace: Path, inbox: Path, pin_judges: str | None) -> str:
    """Append the run's config blocks to the generated `openkos.yaml` and
    return the final text. Refuses if the template already sets a key at top
    level: a second mapping key would be a YAML error or a silent override."""
    path = workspace / "openkos.yaml"
    text = path.read_text(encoding="utf-8")
    blocks = [f"unattended:\n  inbox: {json.dumps(str(inbox))}\n"]
    if pin_judges:
        blocks.append(
            "models:\n"
            f"  adjudication: {json.dumps(pin_judges)}\n"
            f"  contradiction: {json.dumps(pin_judges)}\n"
        )
    for block in blocks:
        key = block.split(":", 1)[0]
        if re.search(rf"^{key}:", text, flags=re.MULTILINE):
            raise RuntimeError(f"openkos.yaml already sets `{key}:` at top level")
    text = text.rstrip("\n") + "\n\n# quiet_engine harness\n" + "".join(blocks)
    path.write_text(text, encoding="utf-8")
    return text


def _query_record(
    done: Completed,
    *,
    docs: Mapping[str, Doc],
    expected: set[str],
    token: str,
    t0: float | None = None,
    started: float | None = None,
) -> dict[str, Any]:
    citations = parse_citations(done.stdout)
    record: dict[str, Any] = {
        "code": done.code,
        "timed_out": done.timed_out,
        "elapsed_s": round(done.elapsed_s, 3),
        "citations": citations,
        "cites_expected": cites_expected(citations, docs, expected),
        "refused": is_refusal(done.stdout),
        "stale_warning": is_stale(done.stderr),
        "answer_has_token": token in done.stdout,
    }
    if t0 is not None and started is not None:
        record["t_start_s"] = round(started - t0, 3)
        record["t_end_s"] = round(started - t0 + done.elapsed_s, 3)
    return record


def run_once(cfg: RunConfig) -> dict[str, Any]:
    """One full protocol run. Returns the run record; never raises for a CLI
    failure (it is recorded), only for a harness defect."""
    m = cfg.manifest
    t = cfg.timeouts
    env = child_env(cfg.env_extra)
    ws = cfg.run_dir / "workspace"
    inbox = cfg.run_dir / "inbox"
    ws.mkdir(parents=True)
    inbox.mkdir()
    rec: dict[str, Any] = {"run_dir": str(cfg.run_dir), "phases": {}, "flags": []}
    phases: dict[str, Any] = rec["phases"]

    def cli(args: Sequence[str], timeout: float) -> Completed:
        return run_cli(cfg.cli, args, cwd=ws, env=env, timeout=timeout)

    def phase(name: str, done: Completed) -> Completed:
        phases[name] = {
            "args": done.args,
            "code": done.code,
            "elapsed_s": round(done.elapsed_s, 3),
            "timed_out": done.timed_out,
            "stdout_tail": done.stdout[-4000:],
            "stderr_tail": done.stderr[-4000:],
        }
        if done.code != 0:
            rec["flags"].append(f"{name}: exit {done.code}")
        return done

    # 1. init + config
    cfg.log("  init")
    phase(
        "init",
        cli(
            [
                "init",
                "--model",
                cfg.chat_model,
                "--embedding-model",
                cfg.embedding_model,
            ],
            600,
        ),
    )
    rec["config_text"] = configure_workspace(ws, inbox, cfg.pin_judges)

    # 2. batch ingest
    cfg.log(f"  ingest {len(corpus_files(m))} sources")
    phase("ingest", cli(["ingest", str(m.sources_dir), "--auto"], t.ingest))
    head_after_ingest = git(ws, "rev-parse", "HEAD")

    # 3. first maintenance pass
    cfg.log("  daemon --once")
    once = phase("daemon_once", cli(["daemon", "--once"], t.maintenance))
    rec["pending_after_maintenance"] = read_pending(ws)

    # 4-7. the watch, metric (b), the version, the stale check
    cfg.log("  daemon (background)")
    daemon = Daemon(cfg.cli, cwd=ws, env=env, log=cfg.run_dir / "daemon.log")
    daemon_code: int | None = None
    try:
        rec["time_to_cite"] = _measure_time_to_cite(cfg, ws, inbox, daemon, cli)
        cfg.log("  probe v2 (new version of a watched source)")
        rec["version_import"] = _import_version(cfg, ws, inbox, daemon)
        cfg.log("  stale-index questions")
        before = _ask_all(cfg, ws, cli)
    finally:
        daemon_code = daemon.stop(t.daemon_stop)
    daemon_text = daemon.text()
    rec["daemon"] = {"exit": daemon_code, "log_tail": daemon_text[-8000:]}
    phase("reindex", cli(["reindex"], t.reindex))
    after = _ask_all(
        cfg, ws, cli, only=[q for q, r in before.items() if not r["cites_expected"]]
    )
    rec["stale_check"] = _stale_check(before, after)

    # 8. bundle, actions, queue
    docs = scan_bundle(ws)
    rec["documents"] = len(docs)
    rec["sources_in_bundle"] = sum(d.type == "Source" for d in docs.values())
    rec["suffix_duplicates"] = suffix_duplicates(docs)
    head_now = git(ws, "rev-parse", "HEAD")
    rec["automatic_actions"] = automatic_actions(
        ws, head_after_ingest, head_now, once.stdout + once.stderr + daemon_text
    )
    rec["pending_final"] = read_pending(ws)
    rec["pending_text"] = cli(["pending"], 300).stdout[-8000:]

    # 9. curate under a pty
    cfg.log("  curate --auto (pty; every prompt answered with Enter)")
    tty = drive_tty(
        [*cfg.cli, "curate", "--auto"],
        cwd=ws,
        env=env,
        timeout=t.curate,
        stall=t.stall,
    )
    rec["curate"] = _decisions(tty, m.fed_files)
    if tty.code != 0:
        rec["flags"].append(f"curate: exit {tty.code}")
    if tty.timed_out:
        rec["flags"].append("curate: timed out")
    (cfg.run_dir / "curate.transcript.txt").write_text(tty.transcript, encoding="utf-8")
    return rec


def _measure_time_to_cite(
    cfg: RunConfig,
    ws: Path,
    inbox: Path,
    daemon: Daemon,
    cli: Callable[[Sequence[str], float], Completed],
) -> dict[str, Any]:
    """Metric (b). Drop probe v1; wait for its bytes to land in `raw/` (no
    answer can cite a file the workspace does not hold, so not querying
    before then loses nothing); then query every `poll_interval` seconds
    until an answer cites it or `cite` seconds have passed since the drop."""
    m = cfg.manifest
    t = cfg.timeouts
    q = m.cite_question
    v1_hash = sha256_bytes(m.probe_v1.read_bytes())
    log_offset = len(daemon.text())
    t0 = drop_file(m.probe_v1, inbox, m.probe_name)

    def landed() -> bool:
        raw = ws / "raw"
        return raw.is_dir() and any(
            p.is_file() and sha256_bytes(p.read_bytes()) == v1_hash
            for p in raw.iterdir()
        )

    landed_ok = wait_until(landed, t.cite, t.landing_poll)
    landed_at = time.monotonic() - t0 if landed_ok else None
    queries: list[dict[str, Any]] = []
    first: float | None = None
    while landed_ok and time.monotonic() - t0 < t.cite:
        started = time.monotonic()
        done = cli(["query", q.question], t.query)
        docs = scan_bundle(ws)
        expected = expected_source_ids(ws, docs, m, q)
        record = _query_record(
            done,
            docs=docs,
            expected=expected,
            token=q.answer_token,
            t0=t0,
            started=started,
        )
        queries.append(record)
        if record["cites_expected"]:
            first = record["t_end_s"]
            break
        pause = t.poll_interval - (time.monotonic() - started)
        if pause > 0:
            time.sleep(min(pause, max(0.0, t.cite - (time.monotonic() - t0))))
    imported = wait_until(
        lambda: any(n >= 1 for _o, n in watch_reports(daemon.text()[log_offset:])),
        t.imported,
        t.landing_poll,
    )
    return {
        "question_id": q.id,
        "landed_s": None if landed_at is None else round(landed_at, 3),
        "first_cited_answer_s": first,
        "censored_at_s": None if first is not None else t.cite,
        "queries": queries,
        "import_reported": imported,
        "import_reported_s": round(time.monotonic() - t0, 3),
    }


def _import_version(
    cfg: RunConfig, ws: Path, inbox: Path, daemon: Daemon
) -> dict[str, Any]:
    m = cfg.manifest
    v2_hash = sha256_bytes(m.probe_v2.read_bytes())
    log_offset = len(daemon.text())
    t1 = drop_file(m.probe_v2, inbox, m.probe_name)

    def landed() -> bool:
        raw = ws / "raw"
        return any(
            p.is_file() and sha256_bytes(p.read_bytes()) == v2_hash
            for p in raw.iterdir()
        )

    landed_ok = wait_until(landed, cfg.timeouts.imported, cfg.timeouts.landing_poll)
    imported = landed_ok and wait_until(
        lambda: any(n >= 1 for _o, n in watch_reports(daemon.text()[log_offset:])),
        cfg.timeouts.imported,
        cfg.timeouts.landing_poll,
    )
    return {
        "landed": landed_ok,
        "import_reported": imported,
        "elapsed_s": round(time.monotonic() - t1, 3),
    }


def _ask_all(
    cfg: RunConfig,
    ws: Path,
    cli: Callable[[Sequence[str], float], Completed],
    only: Sequence[str] | None = None,
) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for q in cfg.manifest.questions:
        if only is not None and q.id not in only:
            continue
        done = cli(["query", q.question], cfg.timeouts.query)
        docs = scan_bundle(ws)
        expected = expected_source_ids(ws, docs, cfg.manifest, q)
        out[q.id] = _query_record(
            done, docs=docs, expected=expected, token=q.answer_token
        )
        out[q.id]["expected_sources"] = sorted(expected)
    return out


def _stale_check(
    before: Mapping[str, Mapping[str, Any]], after: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    """A false refusal attributable to a stale index: refused WITH the stale
    warning before `reindex`, and citing an expected source after it. A
    stale miss is the same without the refusal (answered, wrong citations)."""
    false_refusals: list[str] = []
    stale_misses: list[str] = []
    for qid, b in before.items():
        a = after.get(qid)
        if a is None or not b["stale_warning"] or not a["cites_expected"]:
            continue
        (false_refusals if b["refused"] else stale_misses).append(qid)
    return {
        "before": dict(before),
        "after_reindex": dict(after),
        "false_refusals": sorted(false_refusals),
        "stale_misses": sorted(stale_misses),
    }


def _decisions(tty: TtyResult, fed_files: int) -> dict[str, Any]:
    """Metric (a) from the curate transcript."""
    by_stage: dict[str, int] = defaultdict(int)
    for p in tty.prompts:
        if p.kind == "item":
            by_stage[p.stage] += 1
    identity_text = _stage_text(tty.transcript, "Identity")
    manual = manual_merge_commands(identity_text)
    items = sum(p.kind == "item" for p in tty.prompts)
    unrecognized = sum(p.kind == "unrecognized" for p in tty.prompts)
    decisions = items + len(manual) + unrecognized
    return {
        "item_prompts": items,
        "item_prompts_by_stage": dict(by_stage),
        "manual_merge_commands": manual,
        "gate_prompts": sum(p.kind == "gate" for p in tty.prompts),
        "unrecognized_prompts": [
            p.text for p in tty.prompts if p.kind == "unrecognized"
        ],
        "hidden_by_caps": hidden_by_caps(tty.transcript),
        "decisions": decisions,
        "fed_files": fed_files,
        "decisions_per_source": round(decisions / fed_files, 4),
        "elapsed_s": round(tty.elapsed_s, 3),
        "exit": tty.code,
        "timed_out": tty.timed_out,
    }


def _stage_text(transcript: str, stage: str) -> str:
    """The transcript between `stage`'s marker and the next stage marker."""
    start = None
    for match in _STAGE_MARKER.finditer(transcript):
        if start is not None:
            return transcript[start : match.start()]
        if match.group(1) == stage:
            start = match.end()
    return transcript[start:] if start is not None else ""


# ---------------------------------------------------------------------------
# Bars: the approved pre-registration (evals/quiet_engine/PREREGISTRATION.md, #1268)
# ---------------------------------------------------------------------------

BARS: Final = {
    "decisions_per_source": 1.0,
    "baseline_validity_min": 2.0,
    "cite_median_s": 120.0,
    "cite_max_s": 300.0,
}
"""The numbers of the approved pre-registration
(`evals/quiet_engine/PREREGISTRATION.md`, #1268). Each bar reads every run
of the primary arm (no averaging can hide one bad run)."""

PRIMARY_ARM: Final = "main"
"""The only arm the bars apply to (main as shipped). Every other non-baseline
arm is reported for attribution, with no verdict."""


def summarize_arm(runs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    def values(get: Callable[[Mapping[str, Any]], Any]) -> list[Any]:
        return [get(r) for r in runs]

    cite = values(lambda r: r["time_to_cite"]["first_cited_answer_s"])
    return {
        "n": len(runs),
        "decisions_per_source": values(lambda r: r["curate"]["decisions_per_source"]),
        "decisions": values(lambda r: r["curate"]["decisions"]),
        "pending_open_after_maintenance": values(
            lambda r: r["pending_after_maintenance"]["open"]
        ),
        "pending_open_final": values(lambda r: r["pending_final"]["open"]),
        "first_cited_answer_s": cite,
        "suffix_duplicates": values(lambda r: r["suffix_duplicates"]["count"]),
        "suffix_duplicates_excluded": values(
            lambda r: r["suffix_duplicates"]["excluded_count"]
        ),
        "stale_false_refusals": values(
            lambda r: len(r["stale_check"]["false_refusals"])
        ),
        "stale_misses": values(lambda r: len(r["stale_check"]["stale_misses"])),
        "automatic_actions": values(lambda r: len(r["automatic_actions"])),
        "unlisted_actions": values(
            lambda r: sum(not a["listed_with_undo"] for a in r["automatic_actions"])
        ),
        "hidden_by_caps": values(lambda r: r["curate"]["hidden_by_caps"]),
        "flagged_runs": sum(bool(r["flags"]) for r in runs),
        "documents": values(lambda r: r["documents"]),
    }


def _median(xs: Sequence[float]) -> float | None:
    return statistics.median(xs) if xs else None


def evaluate_bars(
    treatment: Mapping[str, Any], baseline: Mapping[str, Any] | None
) -> dict[str, str]:
    """PASS / FAIL / INCONCLUSIVE / NOT_MEASURED per exit bar, read off two
    `summarize_arm` results. Mechanical; the human adopts."""
    verdicts: dict[str, str] = {}
    n = treatment["n"]
    if n == 0:
        return {"all": "NOT_MEASURED"}
    dps = treatment["decisions_per_source"]
    if baseline is not None and baseline["n"]:
        base_median = _median(baseline["decisions_per_source"])
        valid = base_median is not None and base_median >= BARS["baseline_validity_min"]
    else:
        valid = False
    if not valid:
        verdicts["B1 decisions per source"] = (
            "INCONCLUSIVE (baseline arm absent or below the validity floor)"
        )
    else:
        verdicts["B1 decisions per source"] = (
            "PASS" if max(dps) <= BARS["decisions_per_source"] else "FAIL"
        )
    verdicts["B2 -N duplicates (non-excluded)"] = (
        "PASS" if max(treatment["suffix_duplicates"]) == 0 else "FAIL"
    )
    verdicts["B3 stale-index false refusals"] = (
        "PASS" if max(treatment["stale_false_refusals"]) == 0 else "FAIL"
    )
    if min(treatment["automatic_actions"]) == 0:
        verdicts["B4 automatic actions listed with undo"] = (
            "NOT_MEASURED (a run made no automatic action)"
        )
    else:
        verdicts["B4 automatic actions listed with undo"] = (
            "PASS" if max(treatment["unlisted_actions"]) == 0 else "FAIL"
        )
    cite = treatment["first_cited_answer_s"]
    if any(c is None for c in cite):
        verdicts["B5 time to first cited answer"] = "FAIL (a run never cited the probe)"
    else:
        med = _median(cite)
        ok = (
            med is not None
            and med <= BARS["cite_median_s"]
            and max(cite) <= BARS["cite_max_s"]
        )
        verdicts["B5 time to first cited answer"] = "PASS" if ok else "FAIL"
    if baseline is not None and baseline["n"]:
        base_pending = _median(baseline["pending_open_final"])
        worst = max(treatment["pending_open_final"])
        verdicts["G1 pending rows not shifted into the queue"] = (
            "PASS" if base_pending is not None and worst <= base_pending else "FAIL"
        )
    if treatment["flagged_runs"]:
        verdicts["validity"] = (
            f"{treatment['flagged_runs']} flagged run(s): read the flags before adopting"
        )
    return verdicts


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


def _fmt(xs: Sequence[Any]) -> str:
    shown = [
        "censored" if x is None else (f"{x:.2f}" if isinstance(x, float) else str(x))
        for x in xs
    ]
    nums = [float(x) for x in xs if isinstance(x, int | float)]
    med = _median(nums)
    return f"{', '.join(shown)}" + (f" (median {med:.2f})" if med is not None else "")


def merge_arm_files(arms: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """One entry per arm, holding every run from every file of that arm.

    Decision 6 adds runs to an arm in a later invocation, which writes a
    second runs file for the same arm. Keying the report by arm label would
    let the later file shadow the earlier one, so the files are merged here:
    runs are concatenated in file order, and the first file's metadata
    stands. Runs of one arm from a different checkout or corpus are not the
    same measurement and are refused rather than pooled.
    """
    merged: dict[str, dict[str, Any]] = {}
    for arm in arms:
        label = arm["arm"]
        if label not in merged:
            merged[label] = {**arm, "runs": list(arm["runs"])}
            continue
        held = merged[label]
        for key in ("checkout_commit", "corpus_digest"):
            if held.get(key) != arm.get(key):
                raise ValueError(
                    f"arm {label!r}: runs files disagree on {key} "
                    f"({held.get(key)!r} vs {arm.get(key)!r}); refusing to pool them"
                )
        held["runs"].extend(arm["runs"])
    return list(merged.values())


def render_report(arms: Sequence[Mapping[str, Any]], baseline_label: str | None) -> str:
    summaries = {a["arm"]: summarize_arm(a["runs"]) for a in arms}
    lines = [
        "# The Quiet Engine — product metrics (#1268)",
        "",
        f"_Generated: {datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}_ · protocol v{PROTOCOL_VERSION}.",
        "",
        "Bars are the approved pre-registration (`evals/quiet_engine/PREREGISTRATION.md`, #1268); "
        f"they apply to the primary arm `{PRIMARY_ARM}` only.",
        "",
    ]
    for arm in arms:
        lines.append(f"## Arm `{arm['arm']}`")
        lines.append("")
        lines.append(
            f"CLI `{' '.join(arm['cli'])}` · version `{arm['cli_version']}` · checkout `{arm.get('checkout_commit')}` · corpus `{arm['corpus_digest']}`."
        )
        lines.append(stamp_report_line(arm["stamp"]))
        lines.append(arm["identity_line"])
        lines.append("")
        s = summaries[arm["arm"]]
        lines.extend(
            [
                "| metric | per run |",
                "| --- | --- |",
                f"| decisions per source (a) | {_fmt(s['decisions_per_source'])} |",
                f"| decisions | {_fmt(s['decisions'])} |",
                f"| first cited answer, s (b) | {_fmt(s['first_cited_answer_s'])} |",
                f"| -N duplicates, non-excluded | {_fmt(s['suffix_duplicates'])} |",
                f"| -N duplicates, Event/Person | {_fmt(s['suffix_duplicates_excluded'])} |",
                f"| stale-index false refusals | {_fmt(s['stale_false_refusals'])} |",
                f"| stale-index misses | {_fmt(s['stale_misses'])} |",
                f"| automatic actions / unlisted | {_fmt(s['automatic_actions'])} / {_fmt(s['unlisted_actions'])} |",
                f"| pending open rows (after maintenance / final) | {_fmt(s['pending_open_after_maintenance'])} / {_fmt(s['pending_open_final'])} |",
                f"| candidate work hidden by caps | {_fmt(s['hidden_by_caps'])} |",
                f"| documents | {_fmt(s['documents'])} |",
                f"| flagged runs | {s['flagged_runs']} |",
                "",
            ]
        )
    if baseline_label is not None and baseline_label in summaries:
        for label, s in summaries.items():
            if label == baseline_label:
                continue
            if label != PRIMARY_ARM:
                lines.append(f"## `{label}`: attribution only, no bars")
                lines.append("")
                continue
            lines.append(
                f"## Exit bars (approved pre-registration, #1268): "
                f"`{label}` against `{baseline_label}`"
            )
            lines.append("")
            for bar, verdict in evaluate_bars(s, summaries[baseline_label]).items():
                lines.append(f"- **{bar}**: {verdict}")
            lines.append("")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Forecast
# ---------------------------------------------------------------------------

FORECAST_MINUTES: Final = {
    "ingest": (
        21.0,
        "MEASURED on the 0.4.0 human E2E (#1268): 20 markdown sources, qwen3:8b; a different corpus",
    ),
    "maintenance": (
        15.0,
        "ESTIMATE; bounded by job_deadline_seconds (30 min) and max_calls_per_pass (100)",
    ),
    "probe_censored": (
        30.0,
        "the time-to-cite timeout; v0.4.0 is expected to hit it (#1260)",
    ),
    "probe_cited": (3.0, "ESTIMATE: quiet_seconds 30 + one small ingest + queries"),
    "version_import": (2.0, "ESTIMATE: one 1.8 KB source"),
    "questions_and_reindex": (4.0, "ESTIMATE: up to 10 queries + one reindex"),
    "curate": (
        30.0,
        "ESTIMATE: E2E presented 69 curate prompts; inference time at this size unmeasured",
    ),
    "judge_swaps": (
        5.0,
        "ESTIMATE: gemma4:26b-a4b swapped in and out by stage (ADR-0047), main arm only",
    ),
}


def forecast(arm: str, runs: int) -> tuple[float, list[str]]:
    f = FORECAST_MINUTES
    parts = [
        "ingest",
        "maintenance",
        "version_import",
        "questions_and_reindex",
        "curate",
    ]
    parts.append("probe_censored" if arm == "v0.4.0" else "probe_cited")
    if arm == "main":
        parts.append("judge_swaps")
    per_run = sum(f[p][0] for p in parts)
    notes = [f"- {p}: {f[p][0]:.0f} min — {f[p][1]}" for p in parts]
    return per_run * runs, notes


# ---------------------------------------------------------------------------
# Running an arm
# ---------------------------------------------------------------------------


def cli_version(cli: Sequence[str]) -> str:
    done = run_cli(cli, ["--version"], cwd=Path.cwd(), env=child_env(), timeout=300)
    return (done.stdout or done.stderr).strip()


def run_arm(
    *,
    arm: str,
    cli: list[str],
    runs: int,
    work_root: Path,
    checkout: Path | None,
    chat_model: str,
    timeouts: Timeouts,
    out: Path,
    env_extra: dict[str, str] | None = None,
    preset: Mapping[str, str | None] | None = None,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    preset = preset if preset is not None else ARMS[arm]
    manifest = load_manifest()
    if inside_git_worktree(work_root):
        raise SystemExit(
            f"refusing: {work_root} is inside a git working tree; `init` would "
            "commit into the host repository. Pass --work-root outside any repo."
        )
    version = cli_version(cli)
    wanted = preset.get("version")
    if wanted and wanted not in version:
        raise SystemExit(
            f"refusing: arm {arm} wants version {wanted}, CLI says {version!r}"
        )
    judge = preset.get("judge_model") or chat_model
    stamp = build_stamp(
        model=chat_model,
        prompts={
            f"quiet_engine/question/{q.id}": q.question for q in manifest.questions
        },
    )
    identity = arm_identity_line(
        max_generation_tokens=_config_int("max_generation_tokens"),
        context_window=_config_int("context_window"),
        extra=[f"arm `{arm}`", f"judges `{judge}`", f"chat `{chat_model}`"],
    )
    record: dict[str, Any] = {
        "protocol_version": PROTOCOL_VERSION,
        "arm": arm,
        "cli": cli,
        "cli_version": version,
        "checkout_commit": git(checkout, "rev-parse", "HEAD") if checkout else None,
        "pin_judges": preset.get("pin_judges"),
        "judge_model": model_identity(judge) if judge != chat_model else stamp["model"],
        "stamp": stamp,
        "identity_line": identity,
        "corpus_digest": corpus_digest(manifest),
        "timeouts": asdict(timeouts),
        "runs": [],
    }
    for index in range(runs):
        log(f"{arm}: run {index + 1}/{runs}")
        run_dir = work_root / f"{arm}-r{index + 1}"
        started = time.monotonic()
        result = run_once(
            RunConfig(
                cli=cli,
                manifest=manifest,
                run_dir=run_dir,
                chat_model=chat_model,
                pin_judges=preset.get("pin_judges"),
                timeouts=timeouts,
                env_extra=env_extra or {},
                log=log,
            )
        )
        result["wall_clock_s"] = round(time.monotonic() - started, 1)
        record["runs"].append(result)
        out.write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")
    return record


def _config_int(name: str) -> int:
    """The packaged default, read from the harness checkout's config module
    (unchanged between v0.4.0 and main for both keys). 0 if unavailable."""
    try:
        from openkos import config
    except ImportError:
        return 0
    value = getattr(config, f"DEFAULT_{name.upper()}", 0)
    return int(value) if isinstance(value, int) else 0


# ---------------------------------------------------------------------------
# The fake CLI (self-test only)
# ---------------------------------------------------------------------------
#
# A deterministic stand-in for `openkos`, driven through the SAME argv,
# pseudo-terminal and file surfaces as the real one. `QE_FAKE_MODE=old`
# behaves like v0.4.0 (fork to `-N`, daemon imports leave the index stale,
# no action listing, identity prompt `[y/N]`); `new` like the arc's goal
# (attach, fresh index, every import listed with `git revert`, identity
# prompt with skip). Headings are concepts: `## Person: X`, `## Event: X`,
# anything else a Concept.

_FAKE_DIRS: Final = {"Concept": "concepts", "Person": "people", "Event": "events"}
_STOP = {"what", "which", "does", "the", "this", "that", "with", "from", "after"}


def _fake_slug(title: str) -> str:
    return "-".join(normalize_key(title).split())


def _fake_write_doc(path: Path, meta: Mapping[str, Any], body: str) -> None:
    lines = ["---"]
    for key, value in meta.items():
        if isinstance(value, list):
            lines.append(f"{key}:")
            lines.extend(f"- {json.dumps(v)}" for v in value)
        else:
            lines.append(f"{key}: {json.dumps(value)}")
    lines.extend(["---", "", body, ""])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _fake_git(ws: Path, *args: str) -> str:
    done = subprocess.run(  # noqa: S603 -- fixed git argv, self-test only
        ["git", *args],  # noqa: S607 -- git from PATH
        cwd=ws,
        capture_output=True,
        text=True,
        check=False,
    )
    return done.stdout.strip()


def _fake_docs(ws: Path) -> dict[str, tuple[dict[str, Any], str]]:
    out: dict[str, tuple[dict[str, Any], str]] = {}
    bundle = ws / "bundle"
    for path in sorted(bundle.rglob("*.md")):
        if path.name in _RESERVED:
            continue
        text = path.read_text(encoding="utf-8")
        _, head, body = text.split("---\n", 2)
        meta: dict[str, Any] = {}
        key = ""
        for line in head.splitlines():
            if line.startswith("- "):
                meta.setdefault(key, []).append(json.loads(line[2:]))
            elif line.endswith(":"):
                key = line[:-1]
                meta[key] = []
            else:
                k, v = line.split(": ", 1)
                meta[k] = json.loads(v)
        out[path.relative_to(bundle).with_suffix("").as_posix()] = (meta, body)
    return out


def _fake_reindex(ws: Path) -> None:
    index = {
        cid: (meta.get("title", "") + " " + body).lower()
        for cid, (meta, body) in _fake_docs(ws).items()
    }
    state = ws / ".openkos"
    state.mkdir(exist_ok=True)
    (state / "index.json").write_text(json.dumps(index), encoding="utf-8")


def _fake_import(ws: Path, path: Path, mode: str) -> str:
    """Import one file; returns the new Source id ("" when already held)."""
    data = path.read_bytes()
    raw = ws / "raw"
    target = raw / path.name
    if target.exists():
        if target.read_bytes() == data:
            return ""
        n = 1
        while (raw / f"{path.stem}-{n}{path.suffix}").exists():
            n += 1
        target = raw / f"{path.stem}-{n}{path.suffix}"
    target.write_bytes(data)
    source_id = f"sources/{target.stem}"
    text = data.decode("utf-8")
    title = text.splitlines()[0].lstrip("# ").strip()
    bundle = ws / "bundle"
    _fake_write_doc(
        bundle / f"{source_id}.md",
        {
            "type": "Source",
            "title": title,
            "resource": f"raw/{target.name}",
            "status": "stable",
        },
        text,
    )
    for block in text.split("\n## ")[1:]:
        heading, _, body = block.partition("\n")
        type_name, _, name = heading.partition(": ")
        if type_name not in ("Person", "Event"):
            type_name, name = "Concept", heading
        slug = _fake_slug(name)
        base = bundle / _FAKE_DIRS[type_name] / f"{slug}.md"
        if base.exists() and mode == "new" and type_name == "Concept":
            docs = _fake_docs(ws)
            cid = base.relative_to(bundle).with_suffix("").as_posix()
            meta, old = docs[cid]
            meta["provenance"] = [*meta["provenance"], source_id]
            _fake_write_doc(base, meta, old.rstrip("\n") + "\n" + body.strip())
            continue
        dest = base
        n = 2
        while dest.exists():
            dest = base.with_name(f"{slug}-{n}.md")
            n += 1
        _fake_write_doc(
            dest,
            {
                "type": type_name,
                "title": name.strip(),
                "provenance": [source_id],
                "status": "stable",
            },
            body.strip(),
        )
    _fake_git(ws, "add", "-A", "raw", "bundle")
    _fake_git(ws, "commit", "-q", "-m", f"ingest: {target.name}")
    return source_id


def _fake_index_is_stale(ws: Path) -> bool:
    path = ws / ".openkos" / "index.json"
    if not path.exists():
        return True
    index = json.loads(path.read_text(encoding="utf-8"))
    return set(index) != set(_fake_docs(ws))


def _fake_query(ws: Path, question: str) -> int:
    path = ws / ".openkos" / "index.json"
    index: dict[str, str] = (
        json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    )
    if _fake_index_is_stale(ws):
        print(
            "warning: derived indexes are stale (fts) -- this answer may be degraded; run `openkos reindex`.",
            file=sys.stderr,
        )
    tokens = {
        w
        for w in re.findall(r"[a-z0-9]+", question.lower())
        if len(w) >= 4 and w not in _STOP
    }
    scored = sorted(
        ((sum(t in text for t in tokens), cid) for cid, text in index.items()),
        reverse=True,
    )
    if not scored or scored[0][0] < 2:
        print(
            f"Found {len(index)} matching concepts, but none of them answers this question -- the compiled bundle does not cover it."
        )
        return 0
    best = [
        cid
        for score, cid in scored
        if score == scored[0][0] and not cid.startswith("sources/")
    ][:2]
    best = best or [scored[0][1]]
    print(f"Answer from {', '.join(best)}: {index[best[0]][:80]}")
    print()
    print("Citations:")
    for cid in best:
        print(f"  → {cid} ({cid.rsplit('/', 1)[-1]})")
    return 0


def _fake_config(ws: Path) -> tuple[Path | None, float]:
    inbox = None
    for line in (ws / "openkos.yaml").read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("inbox:"):
            inbox = Path(json.loads(line.split(":", 1)[1].strip()))
    return inbox, 1.0


def _fake_families(ws: Path) -> list[list[str]]:
    groups: dict[tuple[str, str], list[str]] = defaultdict(list)
    for cid, (meta, _body) in _fake_docs(ws).items():
        if meta["type"] != "Source":
            groups[(meta["type"], normalize_key(meta["title"]))].append(cid)
    return [sorted(ids) for _k, ids in sorted(groups.items()) if len(ids) > 1]


def _fake_daemon(ws: Path, mode: str, once: bool) -> int:
    if once:
        db = ws / ".openkos" / "findings.db"
        db.parent.mkdir(exist_ok=True)
        conn = sqlite3.connect(db)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS pending_items (kind TEXT, status TEXT)"
        )
        rows = [("identity", "pending") for _ in _fake_families(ws)] + [
            ("volatility", "pending"),
            ("contradiction", "applied"),
        ]
        conn.executemany("INSERT INTO pending_items VALUES (?, ?)", rows)
        conn.commit()
        conn.close()
        print("openkos daemon: maintenance: ok -- calls=3 done=3 deferred=0")
        return 0
    stop = {"flag": False}

    def handler(_signum: int, _frame: object) -> None:
        stop["flag"] = True

    signal.signal(signal.SIGTERM, handler)
    inbox, quiet = _fake_config(ws)
    seen: dict[str, str] = {}
    while not stop["flag"]:
        if inbox is not None:
            for path in sorted(inbox.iterdir()):
                if (
                    path.suffix not in TEXT_SUFFIXES
                    or time.time() - path.stat().st_mtime < quiet
                ):
                    continue
                digest = sha256_bytes(path.read_bytes())
                if seen.get(path.name) == digest:
                    continue
                seen[path.name] = digest
                source_id = _fake_import(ws, path, mode)
                sha = _fake_git(ws, "rev-parse", "HEAD")
                if mode == "new":
                    _fake_reindex(ws)
                    print(
                        f"  {sha[:7]} imported {source_id} (undo: git revert {sha[:7]})"
                    )
                print(
                    "openkos daemon: watch: ok -- calls=2 done=1 deferred=0", flush=True
                )
        time.sleep(0.1)
    print("openkos daemon: stopped.")
    return 0


def _fake_curate(ws: Path, mode: str) -> int:
    if not sys.stdin.isatty():
        print(
            "openkos curate: Identity: refusing without confirmation -- not a terminal."
        )
        return 0
    suffix = (
        "[y/N]"
        if mode == "old"
        else "[y]es / [s]kip / [d]istinct (d records a permanent keep-distinct ruling)"
    )
    print("openkos curate: Preconditions: checking...")
    print("openkos curate: Identity: checking...")
    if mode == "old":
        print("openkos curate: Identity: 3 of 5 candidate group(s) shown (cap reached)")
    for family in _fake_families(ws):
        if len(family) == 2:
            input(f"Merge {family[1]} into {family[0]}? {suffix}: ")
        else:
            for other in family[1:]:
                print(f"  openkos merge {family[0]} {other}")
    print("openkos curate: Structure: checking...")
    for n in range(2):
        input(f"[?] edge {n} -> related_to? [y/N/s/a]: ")
    print("openkos curate: Metadata: checking...")
    types = sorted(
        {
            meta["type"]
            for meta, _b in _fake_docs(ws).values()
            if meta["type"] != "Source"
        }
    )
    for type_name in types:
        input(f"Set {type_name} -> slow? [y/N]: ")
    print("openkos curate: Contradictions: checking...")
    print("Done.")
    return 0


def fake_cli_main(argv: Sequence[str]) -> int:
    mode = os.environ.get("QE_FAKE_MODE", "old")
    ws = Path.cwd()
    if not argv:
        return 2
    verb, rest = argv[0], list(argv[1:])
    if verb == "--version":
        print(f"openkos 0.0.0+fake-{mode}")
        return 0
    if verb == "init":
        model = rest[rest.index("--model") + 1]
        (ws / "raw").mkdir()
        (ws / "bundle").mkdir()
        (ws / "bundle" / "index.md").write_text("# Index\n", encoding="utf-8")
        (ws / "openkos.yaml").write_text(
            f"model: {model}\n# unattended:\n#   inbox: ../inbox\n", encoding="utf-8"
        )
        (ws / ".gitignore").write_text(".openkos/\n", encoding="utf-8")
        _fake_git(ws, "init", "-q")
        _fake_git(ws, "add", "-A")
        _fake_git(ws, "commit", "-q", "-m", "chore(openkos): initialize workspace")
        return 0
    if verb == "ingest":
        src = Path(rest[0])
        files = (
            sorted(p for p in src.iterdir() if p.suffix in TEXT_SUFFIXES)
            if src.is_dir()
            else [src]
        )
        for path in files:
            _fake_import(ws, path, mode)
        _fake_reindex(ws)
        return 0
    if verb == "reindex":
        _fake_reindex(ws)
        return 0
    if verb == "query":
        return _fake_query(ws, rest[0])
    if verb == "daemon":
        return _fake_daemon(ws, mode, "--once" in rest)
    if verb == "pending":
        print("pending (fake)")
        return 0
    if verb == "curate":
        return _fake_curate(ws, mode)
    return 2


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

_FAKE_CORPUS: Final = {
    "sources/a.md": "# Alpha notes\n\n## Widget\nThe widget sorts parcels by weight.\n\n## Person: Kim Lee\nKim Lee is the engineer.\n",
    "sources/b.md": "# Beta notes\n\n## Widget\nThe widget also labels parcels.\n\n## Person: Kim Lee\nKim Lee is a councillor.\n\n## Event: Weekly Sync\nWeekly sync on Monday.\n",
    "sources/c.md": "# Gamma notes\n\n## Widget\nThe widget was painted blue.\n\n## Event: Weekly Sync\nWeekly sync on Friday.\n",
    "inbox/v1/p.md": "# Probe incident\n\n## Gizmo\nThe gizmo part number is ZQ-4412 for the harbour unit.\n\n## Widget\nThe widget failed during the storm.\n",
    "inbox/v2/p.md": "# Probe incident\n\n## Gizmo\nThe gizmo part number is ZQ-4412 for the harbour unit. Corrected torque value is 77 newton.\n\n## Widget\nThe widget failed during the storm; replaced.\n",
}

_FAKE_MANIFEST: Final = {
    "corpus_version": 0,
    "sources_dir": "sources",
    "probe": {"file_name": "p.md", "v1": "inbox/v1/p.md", "v2": "inbox/v2/p.md"},
    "fed_files": 5,
    "questions": [
        {
            "id": "probe",
            "role": "time_to_cite",
            "question": "What is the gizmo part number for the harbour unit?",
            "answer_token": "ZQ-4412",
            "expected_files": ["inbox/v1/p.md", "inbox/v2/p.md"],
        },
        {
            "id": "v2",
            "role": "stale_check",
            "question": "What is the corrected torque value?",
            "answer_token": "77",
            "expected_files": ["inbox/v2/p.md"],
        },
        {
            "id": "corpus",
            "role": "stale_check",
            "question": "What does the widget sort parcels by?",
            "answer_token": "weight",
            "expected_files": ["sources/a.md"],
        },
    ],
}

_SELF_TEST_TIMEOUTS: Final = Timeouts(
    ingest=60,
    maintenance=60,
    cite=4,
    poll_interval=0.3,
    landing_poll=0.05,
    imported=20,
    query=30,
    reindex=30,
    curate=30,
    stall=3,
    daemon_stop=10,
)

_EXPECTED: Final = {
    # mode: (decisions, identity prompts, manual merges, -N counted, -N excluded,
    #        stale false refusals, actions, unlisted, cited?, pending A, hidden)
    "old": (12, 3, 4, 5, 2, 2, 2, 2, False, 4, 2),
    "new": (7, 2, 0, 0, 2, 0, 2, 0, True, 3, 0),
}


def _self_test_pure(failures: list[str]) -> None:
    def check(cond: bool, label: str) -> None:
        if not cond:
            failures.append(label)

    # Prompt grammar, as both versions' sources spell it.
    check(
        classify_prompt("Merge a into b? [y/N]: ") == "item", "v0.4.0 identity prompt"
    )
    check(
        classify_prompt(
            "Merge a into b? [y]es / [s]kip / [d]istinct (d records a permanent keep-distinct ruling): "
        )
        == "item",
        "main identity prompt",
    )
    for suffix in ("[y/N/s/a]", "[y/N/s]", "[y/N/s/a/r]", "[y/N/a/r]"):
        check(
            classify_prompt(f"Relate x -> y as part_of? {suffix}: ") == "item", suffix
        )
    check(classify_prompt("Proceed? [y/N]: ") == "gate", "cost gate")
    check(
        classify_prompt("openkos curate: Identity: judging 3/40...") is None,
        "progress line",
    )
    check(
        classify_prompt("Merge a into b? [y/N]") is None, "prompt without its colon yet"
    )
    check(looks_like_question("Type the count: "), "unrecognized prompt shape")
    # Citations and refusals, as both versions print them.
    out = "Answer.\n\nCitations:\n  → concepts/permissions (Permissions)\n  → sources/02-x (How) [partial]\n"
    check(parse_citations(out) == ["concepts/permissions", "sources/02-x"], "citations")
    check(
        parse_citations("no block\n  → concepts/a (A)\n") == [], "arrow outside a block"
    )
    check(
        is_refusal(
            "Found 10 matching concepts, but none of them answers this question -- the compiled bundle does not cover it."
        ),
        "refusal",
    )
    check(is_stale("warning: derived indexes are stale (fts) -- this answer"), "stale")
    check(
        hidden_by_caps(
            "x 50 of 59 candidate group(s) shown (cap reached)\n3 of 3 (cap reached)"
        )
        == 9,
        "caps",
    )
    check(
        manual_merge_commands(
            "  openkos merge a b\n  `openkos merge --include-cross-type c d`\nopenkos merge a b"
        )
        == ["a b", "c d"],
        "manual merges deduplicated",
    )
    check(
        watch_reports("openkos daemon: watch: ok -- calls=2 done=1 deferred=0")
        == [("ok", 1)],
        "watch line",
    )
    check(
        watch_reports(
            "openkos daemon: watch: budget_exhausted (cap) -- calls=0 done=0 deferred=3"
        )
        == [("budget_exhausted", 0)],
        "watch line with detail",
    )

    # -N families: a family of k is k - 1; types and keys separate families.
    def doc(cid: str, type_name: str, title: str, status: str = "stable") -> Doc:
        return Doc(cid, type_name, title, (), status, "")

    docs = {
        d.id: d
        for d in [
            doc("concepts/mqtt", "Concept", "MQTT"),
            doc("concepts/mqtt-2", "Concept", "mqtt"),
            doc("concepts/mqtt-3", "Concept", "M.Q.T.T.", "deprecated"),
            doc("concepts/version-2", "Concept", "Version 2"),
            doc("concepts/version", "Concept", "Version"),
            doc("procedures/mqtt", "Procedure", "MQTT"),
            doc("people/sam-patel", "Person", "Sam Patel"),
            doc("people/sam-patel-2", "Person", "Sam Patel"),
            doc("sources/notes", "Source", "Notes"),
            doc("sources/notes-1", "Source", "Notes"),
        ]
    }
    dup = suffix_duplicates(docs)
    check(
        dup["count"] == 1, f"-N count {dup['count']} != 1 (M.Q.T.T. keys differently)"
    )
    check(dup["excluded_count"] == 1, "Person family excluded")
    check(
        [e["members"] for e in dup["counted"]]
        == [["concepts/mqtt", "concepts/mqtt-2"]],
        "family members",
    )
    docs["concepts/mqtt-3"] = doc("concepts/mqtt-3", "Concept", "MQTT", "deprecated")
    dup = suffix_duplicates(docs)
    check(
        dup["count"] == 2
        and [e["deprecated"] for e in dup["counted"]] == [["concepts/mqtt-3"]],
        "deprecated copy counted",
    )
    # The frozen key still agrees with the engine's.
    from openkos.resolution.normalize import normalize_key as engine_key

    for title in ("Café Ñandú", "MQTT — QoS", "  Spaced   out ", "Part HX-7745", "Ⅻ ﬁ"):
        check(
            normalize_key(title) == engine_key(title),
            f"normalize_key drifted on {title!r}",
        )
    # Stale check: refused + stale then cited after reindex = false refusal.
    base = {"stale_warning": True, "refused": True, "cites_expected": False}
    sc = _stale_check(
        {
            "a": base,
            "b": {**base, "refused": False},
            "c": {**base, "stale_warning": False},
        },
        {
            "a": {"cites_expected": True},
            "b": {"cites_expected": True},
            "c": {"cites_expected": True},
        },
    )
    check(
        sc["false_refusals"] == ["a"] and sc["stale_misses"] == ["b"],
        "stale classification",
    )
    # Citation check reaches through provenance.
    pdocs = {
        "concepts/x": Doc("concepts/x", "Concept", "X", ("sources/p",), "stable", "")
    }
    check(cites_expected(["concepts/x"], pdocs, {"sources/p"}), "provenance citation")
    check(
        not cites_expected(["concepts/x"], pdocs, set()), "empty expected never cites"
    )
    check(
        not cites_expected(["concepts/y"], pdocs, {"sources/p"}), "unrelated citation"
    )
    # Bars read every run.
    summary = {
        "n": 3,
        "decisions_per_source": [0.5, 0.9, 1.2],
        "suffix_duplicates": [0, 0, 0],
        "stale_false_refusals": [0, 0, 0],
        "automatic_actions": [2, 2, 2],
        "unlisted_actions": [0, 0, 0],
        "first_cited_answer_s": [60.0, 90.0, 100.0],
        "pending_open_final": [5, 5, 5],
        "flagged_runs": 0,
    }
    baseline = {
        **summary,
        "decisions_per_source": [4.0, 4.3, 4.1],
        "pending_open_final": [9, 9, 9],
    }
    verdicts = evaluate_bars(summary, baseline)
    check(
        verdicts["B1 decisions per source"] == "FAIL", "one run above the bar fails B1"
    )
    check(verdicts["B5 time to first cited answer"] == "PASS", "B5 pass")
    low = {**baseline, "decisions_per_source": [1.0, 1.1, 1.2]}
    check(
        evaluate_bars(summary, low)["B1 decisions per source"].startswith(
            "INCONCLUSIVE"
        ),
        "validity floor",
    )
    censored = {**summary, "first_cited_answer_s": [60.0, None, 100.0]}
    check(
        evaluate_bars(censored, baseline)["B5 time to first cited answer"].startswith(
            "FAIL"
        ),
        "censored run fails B5",
    )
    # Runs added to an arm by a later invocation (decision 6) are read
    # together: two files of one arm become one arm with every run, never
    # one file shadowing the other.
    first = {"arm": "main", "checkout_commit": "c", "corpus_digest": "d"}
    batch_a = {**first, "runs": [{"n": 1}, {"n": 2}, {"n": 3}]}
    batch_b = {**first, "runs": [{"n": 4}, {"n": 5}, {"n": 6}]}
    other = {**first, "arm": "v0.4.0", "runs": [{"n": 0}]}
    merged = merge_arm_files([other, batch_a, batch_b])
    check(
        [a["arm"] for a in merged] == ["v0.4.0", "main"],
        "merge keeps one entry per arm, in first-seen order",
    )
    check(
        [r["n"] for r in merged[1]["runs"]] == [1, 2, 3, 4, 5, 6],
        "merge concatenates every run of an arm",
    )
    try:
        merge_arm_files([batch_a, {**batch_b, "checkout_commit": "other"}])
        check(False, "merge refuses runs of one arm from different commits")
    except ValueError:
        pass
    # The committed corpus is the one the pre-registration describes.
    real = load_manifest()
    for problem in corpus_problems(real):
        failures.append(f"corpus: {problem}")


def _self_test_protocol(failures: list[str]) -> None:
    with tempfile.TemporaryDirectory(prefix="qe-selftest-") as tmp:
        root = Path(tmp)
        corpus = root / "corpus"
        for rel, text in _FAKE_CORPUS.items():
            (corpus / rel).parent.mkdir(parents=True, exist_ok=True)
            (corpus / rel).write_text(text, encoding="utf-8")
        (corpus / "manifest.json").write_text(
            json.dumps(_FAKE_MANIFEST), encoding="utf-8"
        )
        manifest = load_manifest(corpus)
        if inside_git_worktree(root):
            failures.append(
                f"temp dir {root} is inside a git worktree; cannot self-test"
            )
            return
        for mode, expected in _EXPECTED.items():
            decisions = expected[0]
            rec = run_once(
                RunConfig(
                    cli=[sys.executable, str(Path(__file__).resolve()), "--fake-cli"],
                    manifest=manifest,
                    run_dir=root / mode,
                    timeouts=_SELF_TEST_TIMEOUTS,
                    env_extra={"QE_FAKE_MODE": mode},
                    log=lambda _msg: None,
                )
            )
            c = rec["curate"]
            observed = (
                c["decisions"],
                c["item_prompts_by_stage"].get("Identity", 0),
                len(c["manual_merge_commands"]),
                rec["suffix_duplicates"]["count"],
                rec["suffix_duplicates"]["excluded_count"],
                len(rec["stale_check"]["false_refusals"]),
                len(rec["automatic_actions"]),
                sum(not a["listed_with_undo"] for a in rec["automatic_actions"]),
                rec["time_to_cite"]["first_cited_answer_s"] is not None,
                rec["pending_after_maintenance"]["open"],
                c["hidden_by_caps"],
            )
            if observed != expected:
                failures.append(f"{mode}: observed {observed}, expected {expected}")
            if c["gate_prompts"] or c["unrecognized_prompts"]:
                failures.append(f"{mode}: unexpected gate/unrecognized prompts {c}")
            if c["decisions_per_source"] != round(decisions / 5, 4):
                failures.append(
                    f"{mode}: decisions_per_source {c['decisions_per_source']}"
                )
            if rec["flags"]:
                failures.append(f"{mode}: run flagged {rec['flags']}")
            if c["item_prompts_by_stage"].get("Metadata") != 3:
                failures.append(
                    f"{mode}: metadata prompts {c['item_prompts_by_stage']}"
                )
            summary = summarize_arm([json.loads(json.dumps(rec, default=str))])
            if summary["n"] != 1 or summary["decisions"] != [decisions]:
                failures.append(f"{mode}: summarize_arm {summary}")


def _self_test() -> int:
    failures: list[str] = []
    _self_test_pure(failures)
    _self_test_protocol(failures)
    for failure in failures:
        print(f"FAIL: {failure}")
    print("quiet_engine self-test:", "FAIL" if failures else "PASS")
    return 1 if failures else 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    args_in = list(sys.argv[1:] if argv is None else argv)
    if args_in[:1] == ["--fake-cli"]:
        return fake_cli_main(args_in[1:])
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--arm", choices=sorted(ARMS))
    parser.add_argument(
        "--cli", help="argv prefix of the openkos under test, shell-quoted"
    )
    parser.add_argument(
        "--checkout", type=Path, help="that CLI's checkout, for its commit"
    )
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--model", default=DEFAULT_CHAT_MODEL)
    parser.add_argument("--work-root", type=Path)
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=RESULTS_DIR,
        help="where runs-*.json and reports go (a pilot uses results/pilot-<stamp>/)",
    )
    parser.add_argument(
        "--plan", action="store_true", help="print the protocol forecast; no run"
    )
    parser.add_argument(
        "--report", nargs="+", type=Path, help="render a report from runs-*.json"
    )
    parser.add_argument(
        "--baseline", default="v0.4.0", help="the arm the bars compare against"
    )
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(args_in)

    if args.self_test:
        return _self_test()
    if args.plan:
        total = 0.0
        for arm in ARMS:
            minutes, notes = forecast(arm, args.runs)
            total += minutes
            print(f"{arm}: ~{minutes / 60:.1f} h for {args.runs} run(s)")
            print("\n".join(notes))
        print(f"all arms: ~{total / 60:.1f} h")
        return 0
    if args.report:
        arms = merge_arm_files(
            [json.loads(p.read_text(encoding="utf-8")) for p in args.report]
        )
        text = render_report(arms, args.baseline)
        args.results_dir.mkdir(parents=True, exist_ok=True)
        out = (
            args.results_dir
            / f"report-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.md"
        )
        out.write_text(text, encoding="utf-8")
        print(text)
        print(f"wrote {out}")
        return 0
    if not args.arm or not args.cli:
        parser.error(
            "--arm and --cli are required to run (or use --plan/--report/--self-test)"
        )
    work_root = args.work_root or Path(tempfile.mkdtemp(prefix="quiet-engine-"))
    work_root.mkdir(parents=True, exist_ok=True)
    args.results_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = (
        args.results_dir
        / f"runs-{args.arm}-{stamp}-{args.model.replace(':', '-')}.json"
    )
    minutes, _notes = forecast(args.arm, args.runs)
    print(
        f"forecast: ~{minutes / 60:.1f} h; workspaces under {work_root}; results {out}"
    )
    run_arm(
        arm=args.arm,
        cli=shlex.split(args.cli),
        runs=args.runs,
        work_root=work_root,
        checkout=args.checkout,
        chat_model=args.model,
        timeouts=Timeouts(),
        out=out,
    )
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
