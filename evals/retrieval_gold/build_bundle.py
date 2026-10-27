"""Build the retrieval gold bundle with production `ingest` (#1609).

The 35-document draft bundle in `gold_bundle.py` is too small and not
representative (a top 10 is 29% of it; half of it is raw `Source` notes). This
script builds the replacement: a fresh workspace, every source in
`bundle_sources.json` ingested through the shipped `openkos ingest`, then the
shipped `openkos reindex`, then a manifest that pins what was built.

Nothing here runs unless you ask. `--self-test` contacts no model: every
subprocess and every server read is replaced by a stub.

    uv run python evals/retrieval_gold/build_bundle.py --self-test
    uv run python evals/retrieval_gold/build_bundle.py plan --transcripts DIR
    uv run python evals/retrieval_gold/build_bundle.py build \
        --workspace ~/openkos-eval-runs/retrieval-gold/bundle-v1 \
        --transcripts DIR --prose-dir DIR [--limit N] [--no-synthetic]

`build` is the only command that talks to a model, and it is GPU-bound: read the
forecast in the README first.

Properties that matter:

- **Allowed material only.** Every AMI meeting must pass the fail-closed
  allowlist of `evals/concept_model_screen/screen_data.py`, and TS3012a is
  refused outright (#1607: production extraction fails on it). The check runs
  when the source list is loaded, before any file is opened.
- **Pinned.** Each source carries the sha256 of the bytes that were selected;
  a file that differs is refused before the first ingest.
- **Outside the repository.** The workspace may not be inside this repository
  or inside any other git work tree: `ingest` auto-commits, and a workspace
  nested in a checkout would commit into it.
- **Resumable, and never silently re-ingesting.** A per-source state file lives
  next to the workspace's `raw/` and `bundle/` (never inside `bundle/`). A
  source recorded `done` is skipped. A source recorded `started` without
  `done` means a previous run died inside `ingest`; re-ingesting would
  accumulate objects rather than replace them, so the run stops and names it.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

HERE: Final = pathlib.Path(__file__).resolve().parent
REPO_ROOT: Final = HERE.parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.append(str(REPO_ROOT / "evals"))
sys.path.append(str(REPO_ROOT / "evals" / "concept_model_screen"))

import harness_stamp  # noqa: E402
import screen_data  # noqa: E402

SOURCES_PATH: Final = HERE / "bundle_sources.json"
BUNDLE_ID: Final = "retrieval-gold-v1"
STATE_DIR: Final = "gold-build"
"""Beside `raw/` and `bundle/`, never inside the OKF bundle."""

REFUSED_MEETINGS: Final = {
    "TS3012a": "whole-source cap (#1607): production extraction fails on it",
}
"""Allowed by the thesis allowlist but unusable here."""

KNOWN_ROOTS: Final = ("transcripts", "extraction-corpus", "repo")
TIERS: Final = ("core", "synthetic")
OPENKOS_ARGV: Final = (
    sys.executable,
    "-c",
    "import sys; from openkos.cli.main import app; sys.argv[0] = 'openkos'; app()",
)

Runner = Callable[[Sequence[str], pathlib.Path], "subprocess.CompletedProcess[str]"]


class BuildError(ValueError):
    """The build cannot proceed; the message says why and what to do."""


@dataclass(frozen=True)
class Source:
    id: str
    root: str
    path: str
    language: str
    kind: str
    tier: str
    bytes: int
    sha256: str

    @property
    def filename(self) -> str:
        return pathlib.PurePosixPath(self.path).name


def load_sources(path: pathlib.Path = SOURCES_PATH) -> list[Source]:
    """The selected sources, validated. Refuses before any file is opened."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    sources: list[Source] = []
    for index, entry in enumerate(raw.get("sources", [])):
        try:
            source = Source(**{f: entry[f] for f in Source.__dataclass_fields__})
        except (KeyError, TypeError) as exc:
            raise BuildError(f"source #{index} is malformed: {exc!r}") from exc
        sources.append(_validated(source))
    ids = [s.id for s in sources]
    for dup in sorted({i for i in ids if ids.count(i) > 1}):
        raise BuildError(f"duplicate source id {dup!r}")
    names = [s.filename for s in sources]
    for dup in sorted({n for n in names if names.count(n) > 1}):
        raise BuildError(
            f"two sources share the file name {dup!r}; raw/ keeps basenames"
        )
    return sources


def _validated(source: Source) -> Source:
    if source.kind == "ami-meeting":
        if source.id in REFUSED_MEETINGS:
            raise BuildError(f"refusing {source.id}: {REFUSED_MEETINGS[source.id]}")
        try:
            screen_data.require_allowed(source.id)
        except screen_data.DisallowedMeeting as exc:
            raise BuildError(str(exc)) from exc
    if source.root not in KNOWN_ROOTS:
        raise BuildError(f"{source.id}: unknown root {source.root!r}")
    if source.tier not in TIERS:
        raise BuildError(f"{source.id}: unknown tier {source.tier!r}")
    if not re.fullmatch(r"[0-9a-f]{64}", source.sha256):
        raise BuildError(f"{source.id}: sha256 is not 64 hex characters")
    rel = pathlib.PurePosixPath(source.path)
    if rel.is_absolute() or ".." in rel.parts or not rel.name:
        raise BuildError(f"{source.id}: path {source.path!r} escapes its root")
    return source


def resolve(source: Source, roots: Mapping[str, pathlib.Path]) -> pathlib.Path:
    return roots[source.root] / source.path


def verify_sources(
    sources: Sequence[Source], roots: Mapping[str, pathlib.Path]
) -> list[str]:
    """Why the selected bytes are not the bytes on disk (empty list: they are)."""
    problems: list[str] = []
    for source in sources:
        if source.root not in roots:
            problems.append(f"{source.id}: no directory given for root {source.root!r}")
            continue
        path = resolve(source, roots)
        if not path.is_file():
            problems.append(f"{source.id}: missing file {path}")
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != source.sha256:
            problems.append(
                f"{source.id}: sha256 {digest[:12]} is not the selected "
                f"{source.sha256[:12]}"
            )
    return problems


def refuse_workspace(path: pathlib.Path) -> pathlib.Path:
    """The resolved workspace path, or `BuildError` if it is not safe.

    Refused: this repository, anything inside it, and anything inside any git
    work tree (a `.git` directory, or the `.git` file of a linked worktree).
    `ingest` auto-commits, so a nested workspace would commit into the outer
    repository."""
    resolved = path.expanduser().resolve()
    if resolved == REPO_ROOT or REPO_ROOT in resolved.parents:
        raise BuildError(f"refusing {resolved}: it is inside the repository")
    for ancestor in (resolved, *resolved.parents):
        if (ancestor / ".git").exists():
            raise BuildError(
                f"refusing {resolved}: it is inside the git work tree {ancestor}"
            )
    return resolved


def count_documents(bundle: pathlib.Path) -> dict[str, Any]:
    """Concept documents in the bundle by top-level folder (reserved files and
    hidden directories such as `.state/` are not documents)."""
    by_folder: dict[str, int] = {}
    if bundle.is_dir():
        for doc in sorted(bundle.rglob("*.md")):
            rel = doc.relative_to(bundle)
            if len(rel.parts) < 2 or any(p.startswith(".") for p in rel.parts):
                continue
            by_folder[rel.parts[0]] = by_folder.get(rel.parts[0], 0) + 1
    return {"total": sum(by_folder.values()), "by_folder": by_folder}


def _read_json(path: pathlib.Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    loaded = json.loads(path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else {}


def _write_json(path: pathlib.Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _check_state(sources: Sequence[Source], state: Mapping[str, Any]) -> None:
    for source in sources:
        entry = state.get(source.id)
        if entry is None:
            continue
        if entry.get("sha256") != source.sha256:
            raise BuildError(
                f"{source.id} changed since it was ingested (state "
                f"{str(entry.get('sha256'))[:12]}, selected {source.sha256[:12]}); "
                "start a new workspace"
            )
        if entry.get("status") == "started":
            raise BuildError(
                f"{source.id} was interrupted inside `ingest`. Re-ingesting would "
                "accumulate objects instead of replacing them: inspect the "
                f"workspace, then delete its entry in {STATE_DIR}/state.json "
                "(or start a new workspace)"
            )


def _config_tag(text: str, key: str) -> str | None:
    match = re.search(rf"^{key}:\s*(\S+)", text, re.MULTILINE)
    return match.group(1) if match else None


def _openkos_version() -> str | None:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("openkos")
    except PackageNotFoundError:
        return None


def _manifest(
    sources: Sequence[Source],
    state: Mapping[str, Any],
    workspace: pathlib.Path,
    reindex: Mapping[str, Any] | None,
    identify: Callable[[str], dict[str, Any]],
) -> dict[str, Any]:
    config_path = workspace / "openkos.yaml"
    text = config_path.read_text(encoding="utf-8") if config_path.is_file() else ""
    chat = _config_tag(text, "model") or "qwen3:8b"
    embedding = _config_tag(text, "embedding_model") or "bge-m3"
    rows = []
    for source in sources:
        entry = state.get(source.id, {})
        rows.append(
            {
                "id": source.id,
                "root": source.root,
                "path": source.path,
                "language": source.language,
                "kind": source.kind,
                "tier": source.tier,
                "bytes": source.bytes,
                "sha256": source.sha256,
                "status": entry.get("status", "pending"),
                "seconds": entry.get("seconds"),
                "returncode": entry.get("returncode"),
            }
        )
    done = all(r["status"] == "done" for r in rows)
    return {
        "kind": "retrieval-gold-bundle-manifest",
        "version": 1,
        "bundle": BUNDLE_ID,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "complete": bool(done and reindex and reindex.get("returncode") == 0),
        "openkos": {**harness_stamp.git_identity(), "version": _openkos_version()},
        "models": {"chat": identify(chat), "embedding": identify(embedding)},
        "config": {
            "text": text,
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        },
        "reindex": dict(reindex) if reindex else None,
        "documents": count_documents(workspace / "bundle"),
        "sources": rows,
    }


def build(
    sources: Sequence[Source],
    roots: Mapping[str, pathlib.Path],
    workspace: pathlib.Path,
    *,
    runner: Runner,
    identify: Callable[[str], dict[str, Any]],
    limit: int | None = None,
) -> int:
    """Ingest every pending source, reindex, write the manifest. Exit status."""
    workspace = refuse_workspace(workspace)
    problems = verify_sources(sources, roots)
    if problems:
        raise BuildError(
            "the selected sources are not on disk:\n  " + "\n  ".join(problems)
        )
    state_path = workspace / STATE_DIR / "state.json"
    state = _read_json(state_path)
    _check_state(sources, state)

    workspace.mkdir(parents=True, exist_ok=True)
    if not (workspace / "openkos.yaml").is_file():
        done = runner([*OPENKOS_ARGV, "init"], workspace)
        if done.returncode != 0:
            raise BuildError(f"`openkos init` failed in {workspace}: {done.stderr}")

    failed = False
    ingested = 0
    for source in sources:
        if state.get(source.id, {}).get("status") == "done":
            continue
        if limit is not None and ingested >= limit:
            break
        ingested += 1
        state[source.id] = {"sha256": source.sha256, "status": "started"}
        _write_json(state_path, state)
        started = time.monotonic()
        argv = [*OPENKOS_ARGV, "ingest", "--auto", str(resolve(source, roots))]
        done = runner(argv, workspace)
        state[source.id] = {
            "sha256": source.sha256,
            "status": "done" if done.returncode == 0 else "failed",
            "returncode": done.returncode,
            "seconds": round(time.monotonic() - started, 1),
        }
        _write_json(state_path, state)
        entry = state[source.id]
        print(f"{source.id}: {entry['status']} ({entry['seconds']}s)")
        if done.returncode != 0:
            failed = True
            break

    reindex: dict[str, Any] | None = None
    pending = any(state.get(s.id, {}).get("status") != "done" for s in sources)
    if not failed and not pending:
        started = time.monotonic()
        done = runner([*OPENKOS_ARGV, "reindex"], workspace)
        reindex = {
            "returncode": done.returncode,
            "seconds": round(time.monotonic() - started, 1),
        }
        failed = done.returncode != 0
    _write_json(
        workspace / STATE_DIR / "manifest.json",
        _manifest(sources, state, workspace, reindex, identify),
    )
    return 1 if failed else 0


# --- self-test ---------------------------------------------------------------


class _Checks:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self.total = 0

    def check(self, ok: bool, label: str) -> None:
        self.total += 1
        if not ok:
            self.failures.append(label)
            print(f"FAIL: {label}")

    def raises(self, fn: Callable[[], object], label: str) -> str:
        try:
            fn()
        except BuildError as exc:
            self.check(True, label)
            return str(exc)
        self.check(False, f"{label} (nothing was raised)")
        return ""


def _entry(**over: Any) -> dict[str, Any]:
    base = {
        "id": "ES2015a",
        "root": "transcripts",
        "path": "ES2015a.md",
        "language": "en",
        "kind": "ami-meeting",
        "tier": "core",
        "bytes": 1,
        "sha256": "0" * 64,
    }
    base.update(over)
    return base


def _write_sources(tmp: pathlib.Path, entries: list[dict[str, Any]]) -> pathlib.Path:
    path = tmp / "sources.json"
    path.write_text(json.dumps({"version": 1, "sources": entries}), encoding="utf-8")
    return path


def _loader(path: pathlib.Path) -> Callable[[], object]:
    return lambda: load_sources(path)


def _self_test_sources(c: _Checks, tmp: pathlib.Path) -> None:
    sources = load_sources()
    ids = [s.id for s in sources]
    c.check(len(ids) == len(set(ids)), "source ids are unique")
    ami = [s for s in sources if s.kind == "ami-meeting"]
    c.check(len(ami) == 25, f"25 AMI meetings, got {len(ami)}")
    c.check("TS3012a" not in ids, "TS3012a is not selected")
    c.check(
        all(s.id in screen_data.ALLOWED_MEETINGS for s in ami),
        "every AMI meeting is on the allowlist",
    )
    c.check({s.language for s in sources} == {"en", "es"}, "English and Spanish")
    c.check(
        {s.tier for s in sources} == set(TIERS), "both the core and synthetic tiers"
    )
    c.check(
        all(re.fullmatch(r"[0-9a-f]{64}", s.sha256) for s in sources),
        "every source carries a sha256",
    )
    names = [s.filename for s in sources]
    c.check(
        len(names) == len(set(names)), "basenames are unique (raw/ keeps basenames)"
    )
    c.check(
        not any("medium-09" in s.id for s in sources),
        "the byte-identical medium-08 twin is excluded",
    )

    bad: dict[str, dict[str, Any]] = {
        "an evaluation-series meeting": _entry(id="ES2002a", path="ES2002a.md"),
        "TS3012a": _entry(id="TS3012a", path="TS3012a.md"),
        "an unknown root": _entry(root="elsewhere"),
        "a short sha256": _entry(sha256="abc"),
        "an unknown tier": _entry(tier="extra"),
        "a path escaping its root": _entry(path="../ES2015a.md"),
    }
    for label, entry in bad.items():
        path = _write_sources(tmp, [entry])
        c.raises(_loader(path), f"refuses {label}")
    c.raises(
        lambda: load_sources(_write_sources(tmp, [_entry(), _entry()])),
        "refuses a duplicate id",
    )


def _self_test_verify(c: _Checks, tmp: pathlib.Path) -> None:
    root = tmp / "files"
    root.mkdir()
    data = b"hello\n"
    (root / "a.md").write_bytes(data)
    good = Source(
        "a",
        "repo",
        "a.md",
        "en",
        "x",
        "core",
        len(data),
        hashlib.sha256(data).hexdigest(),
    )
    roots = {"repo": root}
    c.check(verify_sources([good], roots) == [], "matching bytes verify")
    (root / "a.md").write_bytes(b"hellO\n")
    problems = verify_sources([good], roots)
    c.check(len(problems) == 1 and "sha256" in problems[0], "changed bytes are named")
    missing = Source("b", "repo", "b.md", "en", "x", "core", 1, "0" * 64)
    c.check(
        "missing" in " ".join(verify_sources([missing], roots)), "missing file named"
    )
    c.check(
        "no directory" in " ".join(verify_sources([good], {})),
        "an unset root is named",
    )
    c.check(resolve(good, roots) == root / "a.md", "resolve joins root and path")


def _self_test_workspace(c: _Checks, tmp: pathlib.Path) -> None:
    c.raises(
        lambda: refuse_workspace(REPO_ROOT / "gold"), "refuses a path inside the repo"
    )
    c.raises(lambda: refuse_workspace(REPO_ROOT), "refuses the repo root")
    nested = tmp / "checkout"
    (nested / ".git").mkdir(parents=True)
    c.raises(
        lambda: refuse_workspace(nested / "ws" / "deep"),
        "refuses a path inside another git work tree",
    )
    linked = tmp / "linked"
    linked.mkdir()
    (linked / ".git").write_text("gitdir: /elsewhere\n", encoding="utf-8")
    c.raises(
        lambda: refuse_workspace(linked / "ws"),
        "refuses a linked worktree (.git is a file)",
    )
    ok = tmp / "outside" / "ws"
    c.check(
        refuse_workspace(ok) == ok.resolve(), "accepts a path outside every work tree"
    )
    link = tmp / "lnk"
    link.symlink_to(REPO_ROOT)
    c.raises(lambda: refuse_workspace(link / "ws"), "resolves a symlink into the repo")


class _FakeRunner:
    def __init__(self, fail_on: str | None = None) -> None:
        self.calls: list[tuple[list[str], pathlib.Path]] = []
        self.fail_on = fail_on

    def __call__(
        self, argv: Sequence[str], cwd: pathlib.Path
    ) -> subprocess.CompletedProcess[str]:
        args = list(argv[len(OPENKOS_ARGV) :])
        self.calls.append((args, cwd))
        code = 0
        if args[:1] == ["init"]:
            (cwd / "raw").mkdir(exist_ok=True)
            (cwd / "bundle" / "sources").mkdir(parents=True, exist_ok=True)
            (cwd / "openkos.yaml").write_text(
                "model: qwen3:8b\nembedding_model: bge-m3\n", encoding="utf-8"
            )
        elif args[:1] == ["ingest"]:
            name = pathlib.Path(args[-1]).stem
            if self.fail_on == name:
                code = 1
            else:
                (cwd / "bundle" / "sources" / f"{name}.md").write_text(
                    "x", encoding="utf-8"
                )
                (cwd / "bundle" / "concepts").mkdir(exist_ok=True)
                (cwd / "bundle" / "concepts" / f"{name}-c.md").write_text(
                    "x", encoding="utf-8"
                )
        return subprocess.CompletedProcess(list(argv), code, "", "boom" if code else "")

    def ingested(self) -> list[str]:
        return [pathlib.Path(a[-1]).stem for a, _ in self.calls if a[:1] == ["ingest"]]


def _fake_sources(
    tmp: pathlib.Path, n: int
) -> tuple[list[Source], dict[str, pathlib.Path]]:
    root = tmp / "src"
    root.mkdir(exist_ok=True)
    out: list[Source] = []
    for i in range(n):
        data = f"source {i}\n".encode()
        (root / f"s{i}.md").write_bytes(data)
        out.append(
            Source(
                f"s{i}",
                "repo",
                f"s{i}.md",
                "en",
                "x",
                "core",
                len(data),
                hashlib.sha256(data).hexdigest(),
            )
        )
    return out, {"repo": root}


def _identify(tag: str) -> dict[str, Any]:
    return {"name": tag, "digest": f"digest-of-{tag}"}


def _self_test_build(c: _Checks, tmp: pathlib.Path) -> None:
    sources, roots = _fake_sources(tmp, 4)
    ws = tmp / "ws-a" / "bundle-v1"
    runner = _FakeRunner()
    code = build(sources, roots, ws, runner=runner, identify=_identify)
    c.check(code == 0, "a clean build exits 0")
    verbs = [a[0] for a, _ in runner.calls]
    c.check(
        verbs == ["init", "ingest", "ingest", "ingest", "ingest", "reindex"],
        f"init, one ingest per source in order, then reindex: {verbs}",
    )
    c.check(
        all(cwd == ws.resolve() for _, cwd in runner.calls),
        "every command runs in the workspace",
    )
    c.check(
        all("--auto" in a for a, _ in runner.calls if a[0] == "ingest"),
        "ingest runs unattended with --auto",
    )
    c.check(not any("--force" in a for a, _ in runner.calls), "reindex is not forced")
    manifest = json.loads((ws / STATE_DIR / "manifest.json").read_text("utf-8"))
    c.check(manifest["complete"] is True, "the manifest says complete")
    c.check(manifest["bundle"] == BUNDLE_ID, "the manifest names the bundle")
    c.check(
        [s["id"] for s in manifest["sources"]] == [s.id for s in sources],
        "the manifest lists the sources in order",
    )
    c.check(
        all(
            s["sha256"] == src.sha256
            for s, src in zip(manifest["sources"], sources, strict=True)
        ),
        "the manifest carries each source sha256",
    )
    c.check(
        all(s["status"] == "done" for s in manifest["sources"]), "every source is done"
    )
    c.check("commit" in manifest["openkos"], "the manifest records the openkos commit")
    c.check(
        manifest["models"]["chat"]
        == {"name": "qwen3:8b", "digest": "digest-of-qwen3:8b"}
        and manifest["models"]["embedding"]["digest"] == "digest-of-bge-m3",
        "the manifest records both model digests",
    )
    c.check(
        manifest["config"]["text"].startswith("model: qwen3:8b")
        and len(manifest["config"]["sha256"]) == 64,
        "the manifest records the workspace config and its hash",
    )
    c.check(
        manifest["documents"]["total"] == 8
        and manifest["documents"]["by_folder"] == {"concepts": 4, "sources": 4},
        f"document counts by folder: {manifest['documents']}",
    )
    c.check(not (ws / "bundle" / STATE_DIR).exists(), "no build file inside bundle/")

    # Resume: nothing left to do, nothing is re-ingested.
    again = _FakeRunner()
    c.check(
        build(sources, roots, ws, runner=again, identify=_identify) == 0,
        "a finished build re-runs clean",
    )
    c.check(again.ingested() == [], "a finished build ingests nothing again")
    c.check(
        [a[0] for a, _ in again.calls] == ["reindex"],
        "a finished build still reindexes (it is idempotent)",
    )


def _self_test_resume(c: _Checks, tmp: pathlib.Path) -> None:
    sources, roots = _fake_sources(tmp, 4)
    ws = tmp / "ws-b" / "bundle-v1"
    first = _FakeRunner(fail_on="s2")
    code = build(sources, roots, ws, runner=first, identify=_identify)
    c.check(code != 0, "a failed ingest exits non-zero")
    c.check(first.ingested() == ["s0", "s1", "s2"], "it stops at the failing source")
    c.check(
        "reindex" not in [a[0] for a, _ in first.calls], "no reindex after a failure"
    )
    manifest = json.loads((ws / STATE_DIR / "manifest.json").read_text("utf-8"))
    status = {s["id"]: s["status"] for s in manifest["sources"]}
    c.check(
        manifest["complete"] is False
        and status == {"s0": "done", "s1": "done", "s2": "failed", "s3": "pending"},
        f"a failed build writes an honest manifest: {status}",
    )
    # A failed source is retried on resume, the done ones are skipped.
    second = _FakeRunner()
    c.check(
        build(sources, roots, ws, runner=second, identify=_identify) == 0,
        "resume completes",
    )
    c.check(
        second.ingested() == ["s2", "s3"],
        f"resume skips done sources: {second.ingested()}",
    )
    c.check("init" not in [a[0] for a, _ in second.calls], "resume does not re-init")

    # --limit stops early and says so.
    ws2 = tmp / "ws-c" / "bundle-v1"
    lim = _FakeRunner()
    c.check(
        build(sources, roots, ws2, runner=lim, identify=_identify, limit=2) == 0,
        "a limited build exits 0",
    )
    c.check(lim.ingested() == ["s0", "s1"], "--limit caps the ingests")
    m2 = json.loads((ws2 / STATE_DIR / "manifest.json").read_text("utf-8"))
    c.check(m2["complete"] is False, "a limited build is not complete")


def _self_test_interrupted(c: _Checks, tmp: pathlib.Path) -> None:
    sources, roots = _fake_sources(tmp, 3)
    ws = tmp / "ws-d" / "bundle-v1"
    c.check(
        build(sources, roots, ws, runner=_FakeRunner(), identify=_identify, limit=1)
        == 0,
        "setup",
    )
    state_path = ws / STATE_DIR / "state.json"
    state = json.loads(state_path.read_text("utf-8"))
    state["s1"] = {"sha256": sources[1].sha256, "status": "started"}
    state_path.write_text(json.dumps(state), encoding="utf-8")
    runner = _FakeRunner()
    message = c.raises(
        lambda: build(sources, roots, ws, runner=runner, identify=_identify),
        "an interrupted ingest stops the run",
    )
    c.check("s1" in message, "the interrupted source is named")
    c.check(
        runner.ingested() == [], "nothing is ingested on top of an interrupted source"
    )

    # A source whose bytes changed since it was ingested is refused.
    state["s1"] = {"sha256": "f" * 64, "status": "done"}
    state_path.write_text(json.dumps(state), encoding="utf-8")
    message = c.raises(
        lambda: build(sources, roots, ws, runner=_FakeRunner(), identify=_identify),
        "a source that changed since it was ingested stops the run",
    )
    c.check("s1" in message, "the changed source is named")


def _self_test_refusals(c: _Checks, tmp: pathlib.Path) -> None:
    sources, roots = _fake_sources(tmp, 2)
    inside = REPO_ROOT / "gold-build-test-never-created"
    runner = _FakeRunner()
    c.raises(
        lambda: build(sources, roots, inside, runner=runner, identify=_identify),
        "build refuses a workspace inside the repo",
    )
    created = inside.exists()
    if created:  # only reachable when the guard is broken: do not litter the repo
        shutil.rmtree(inside)
    c.check(runner.calls == [] and not created, "and touches nothing")
    (roots["repo"] / "s1.md").write_bytes(b"drifted\n")
    runner = _FakeRunner()
    c.raises(
        lambda: build(
            sources, roots, tmp / "ws-e" / "b", runner=runner, identify=_identify
        ),
        "build refuses a source whose bytes drifted",
    )
    c.check(runner.calls == [], "before running a single command")


def _self_test_cli(c: _Checks, tmp: pathlib.Path) -> None:
    nowhere = str(tmp / "nowhere")
    c.check(
        main(["plan", "--transcripts", nowhere, "--prose-dir", nowhere]) == 1,
        "plan reports sources missing from disk",
    )
    code = main(
        [
            "build",
            "--workspace",
            str(REPO_ROOT / "gold-never"),
            "--transcripts",
            nowhere,
        ]
    )
    c.check(code == 2, "build refuses a workspace inside the repo at the command line")
    litter = REPO_ROOT / "gold-never"
    created = litter.exists()
    if created:
        shutil.rmtree(litter)
    c.check(not created, "and creates nothing there")


def _self_test() -> int:
    c = _Checks()
    with (
        tempfile.TemporaryDirectory() as raw_tmp,
        contextlib.redirect_stdout(io.StringIO()) as out,
        contextlib.redirect_stderr(io.StringIO()),
    ):
        tmp = pathlib.Path(raw_tmp).resolve()
        _self_test_sources(c, tmp)
        _self_test_verify(c, tmp)
        _self_test_workspace(c, tmp)
        _self_test_build(c, tmp)
        _self_test_resume(c, tmp)
        _self_test_interrupted(c, tmp)
        _self_test_refusals(c, tmp)
        _self_test_cli(c, tmp)
    sys.stdout.write(
        "".join(
            ln + "\n" for ln in out.getvalue().splitlines() if ln.startswith("FAIL")
        )
    )
    if c.failures:
        print(f"self-test FAILED: {len(c.failures)} of {c.total} checks")
        return 1
    print(f"self-test passed ({c.total} checks, no model contacted)")
    return 0


def _run_subprocess(
    argv: Sequence[str], cwd: pathlib.Path
) -> subprocess.CompletedProcess[str]:
    # stderr is inherited on purpose: ingest reports per-chunk progress there
    # and a long run should show it live.
    return subprocess.run(  # noqa: S603 -- argv is built here from fixed verbs and a verified path
        list(argv), cwd=cwd, stdout=subprocess.PIPE, text=True, check=False
    )


def _selected(args: argparse.Namespace) -> list[Source]:
    sources = load_sources()
    if args.no_synthetic:
        sources = [s for s in sources if s.tier != "synthetic"]
    return sources


def _roots(args: argparse.Namespace) -> dict[str, pathlib.Path]:
    roots: dict[str, pathlib.Path] = {"repo": REPO_ROOT}
    roots["extraction-corpus"] = (
        args.prose_dir or REPO_ROOT / "examples" / "extraction-corpus" / "sources"
    )
    if args.transcripts is not None:
        roots["transcripts"] = args.transcripts
    return roots


def _cmd_plan(args: argparse.Namespace) -> int:
    sources = _selected(args)
    for label, key in (("language", "language"), ("kind", "kind"), ("tier", "tier")):
        counts: dict[str, int] = {}
        for source in sources:
            value = getattr(source, key)
            counts[value] = counts.get(value, 0) + 1
        print(f"by {label}: {dict(sorted(counts.items()))}")
    print(f"{len(sources)} sources, {sum(s.bytes for s in sources):,} bytes")
    problems = verify_sources(sources, _roots(args))
    for problem in problems:
        print(f"PROBLEM: {problem}")
    return 1 if problems else 0


def _cmd_build(args: argparse.Namespace) -> int:
    try:
        return build(
            _selected(args),
            _roots(args),
            args.workspace,
            runner=_run_subprocess,
            identify=harness_stamp.model_identity,
            limit=args.limit,
        )
    except BuildError as exc:
        print(f"build_bundle: {exc}", file=sys.stderr)
        return 2


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--self-test", action="store_true", help="no model is contacted"
    )
    sub = parser.add_subparsers(dest="command")
    for name, help_text in (
        ("plan", "verify the selected sources on disk; no model, no workspace"),
        ("build", "ingest the sources into a fresh workspace (GPU-bound)"),
    ):
        cmd = sub.add_parser(name, help=help_text)
        cmd.add_argument(
            "--transcripts", type=pathlib.Path, help="AMI transcripts directory"
        )
        cmd.add_argument(
            "--prose-dir", type=pathlib.Path, help="extraction-corpus sources directory"
        )
        cmd.add_argument(
            "--no-synthetic", action="store_true", help="drop the synthetic tier"
        )
        if name == "build":
            cmd.add_argument("--workspace", type=pathlib.Path, required=True)
            cmd.add_argument(
                "--limit", type=int, help="ingest at most N pending sources"
            )
    args = parser.parse_args(argv)
    if args.self_test:
        return _self_test()
    if args.command == "plan":
        return _cmd_plan(args)
    if args.command == "build":
        return _cmd_build(args)
    parser.error("a command is required (plan, build) or --self-test")


if __name__ == "__main__":
    raise SystemExit(main())
