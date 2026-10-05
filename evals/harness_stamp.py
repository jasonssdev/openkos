"""The identity stamp every stored harness result carries, and per-call timing (#1269).

A stored `runs-*.json` that names only its model tag cannot be tied to what it
measured: a tag is a movable pointer (`ollama pull` replaces what it names), a
harness is a moving checkout, and a prompt is a string constant that a review
can change without anyone noticing. #1269's bake-off compares models across
many sessions, so a result file must pin all three:

- the harness **commit** (and whether the tracked tree was dirty),
- the **model name and the digest** the local Ollama reports for it,
- the **prompt id and hash** of every system prompt the harness sent.

PROMPT HASH DEFINITION. `sha256(text.encode())[:16]` as hex -- the definition
`decision_subject.SUBJECT_PROMPT_VERSION` and
`decision_revision.JUDGE_PROMPT_VERSION` already use, and the one #1277
generalizes when it moves prompts to files. The harness passes the text of the
constant it ACTUALLY sent (after any treatment-arm swap), so the hash is the
identity of the bytes on the wire, and it will equal #1277's hash for the same
prompt without this module depending on that refactor. The prompt ids follow
#1277's folder layout (`<task>/<name>`, e.g. `contradiction/system`).

LOCAL ONLY. The digest comes from the local Ollama's read-only `/api/tags`;
a non-loopback host is refused rather than contacted. Nothing here raises on
an unreachable server: a stamp that cannot be completed records WHY (`error`)
and `stamp_problems` names it, so the caller decides whether that matters
instead of a long run dying at its last line.

Per-call timing: `TimedBackend` wraps any `LLMBackend` and records the
wall-clock of every `chat` call; `summarize_latencies` reduces a list the way
the bake-off's latency budget reads it (MEDIAN, not mean -- single cold-start
runs reach 135 s).

Standard library only and NO `sys.path` surgery, like `harness_report.py`, so
a unit test can load it straight from its path.

Usage:

    python evals/harness_stamp.py --self-test
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import pathlib
import statistics
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any

PROMPT_HASH_LENGTH = 16
"""Hex characters kept from the sha256 -- the length `SUBJECT_PROMPT_VERSION`
and `JUDGE_PROMPT_VERSION` already use."""

STAMP_VERSION = 1

_DEFAULT_HOST = "http://127.0.0.1:11434"
_LOOPBACK_HOSTNAMES = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0"})  # noqa: S104 -- a server bind address, normalized to loopback below, never connected to as-is
_TAGS_TIMEOUT_S = 5.0
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

Opener = Callable[..., Any]
Runner = Callable[..., Any]


def prompt_hash(text: str) -> str:
    """`sha256(text)[:16]`, the same definition #1277 versions prompts by."""
    return hashlib.sha256(text.encode()).hexdigest()[:PROMPT_HASH_LENGTH]


def prompt_identity(prompt_id: str, text: str) -> dict[str, str]:
    """`{"id", "sha256_16"}` for one prompt the harness sent."""
    if not prompt_id.strip():
        raise ValueError("a prompt needs a non-empty id")
    return {"id": prompt_id, "sha256_16": prompt_hash(text)}


def git_identity(
    repo_root: pathlib.Path = _REPO_ROOT, *, runner: Runner = subprocess.run
) -> dict[str, Any]:
    """`{"commit": <sha|None>, "dirty": <bool|None>}` of the harness checkout.

    `dirty` is over TRACKED files only: a previous run's untracked
    `results/` files would otherwise mark every later run dirty and the flag
    would mean nothing. `None` (not `False`) when git could not answer."""

    def _git(*args: str) -> str | None:
        try:
            done = runner(
                ["git", *args],
                cwd=repo_root,
                capture_output=True,
                text=True,
                check=False,
                timeout=15,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return str(done.stdout).strip() if done.returncode == 0 else None

    commit = _git("rev-parse", "HEAD")
    status = _git("status", "--porcelain", "--untracked-files=no")
    return {
        "commit": commit or None,
        "dirty": None if status is None else bool(status),
    }


def resolve_host(host: str | None = None) -> str:
    """Host arg > `OLLAMA_HOST` > the default, as a URL with a scheme."""
    raw = host or os.environ.get("OLLAMA_HOST") or _DEFAULT_HOST
    return raw if "://" in raw else f"http://{raw}"


def _loopback_url(host: str) -> str | None:
    """The URL to contact, or `None` for a non-loopback host (refused)."""
    parsed = urllib.parse.urlsplit(host)
    name = (parsed.hostname or "").lower()
    if name not in _LOOPBACK_HOSTNAMES:
        return None
    if name == "0.0.0.0":  # noqa: S104 -- normalized, see above
        netloc = f"127.0.0.1:{parsed.port}" if parsed.port else "127.0.0.1"
        return f"{parsed.scheme}://{netloc}"
    return host.rstrip("/")


def _model_matches(listed: str, wanted: str) -> bool:
    return listed == wanted or listed == f"{wanted}:latest"


def ollama_get_json(
    path: str, host: str | None = None, *, opener: Opener = urllib.request.urlopen
) -> Any:
    """GET `path` from the LOCAL Ollama. Raises `OSError`/`ValueError` on any
    failure, and `ValueError` for a non-loopback host (never contacted)."""
    base = _loopback_url(resolve_host(host))
    if base is None:
        raise ValueError(f"refusing a non-loopback Ollama host: {resolve_host(host)!r}")
    with opener(f"{base}{path}", timeout=_TAGS_TIMEOUT_S) as response:
        return json.loads(response.read().decode("utf-8"))


def model_identity(
    model: str, host: str | None = None, *, opener: Opener = urllib.request.urlopen
) -> dict[str, Any]:
    """`{"name", "digest"}` (plus `"error"` when the digest is unavailable).

    The digest is what the local Ollama's `/api/tags` lists for the tag NOW:
    the identity of the weights this run used, which the tag alone is not."""
    out: dict[str, Any] = {"name": model, "digest": None}
    try:
        payload = ollama_get_json("/api/tags", host, opener=opener)
        for entry in payload.get("models", []):
            if _model_matches(str(entry.get("name", "")), model) or _model_matches(
                str(entry.get("model", "")), model
            ):
                digest = entry.get("digest")
                out["digest"] = str(digest) if digest else None
                break
        else:
            out["error"] = "model not listed by the local Ollama"
    except (OSError, ValueError, AttributeError) as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
    if out["digest"] is None and "error" not in out:
        out["error"] = "the local Ollama listed the model without a digest"
    return out


def build_stamp(
    *,
    model: str,
    prompts: Mapping[str, str],
    repo_root: pathlib.Path = _REPO_ROOT,
    host: str | None = None,
    opener: Opener = urllib.request.urlopen,
    runner: Runner = subprocess.run,
) -> dict[str, Any]:
    """The full identity stamp: harness commit, model + digest, prompts.

    `prompts` maps a prompt id to the TEXT the harness sent (the constant as
    it stands when this is called -- after a treatment swap, not before)."""
    if not prompts:
        raise ValueError("a stamp names at least one prompt")
    return {
        "stamp_version": STAMP_VERSION,
        "harness": git_identity(repo_root, runner=runner),
        "model": model_identity(model, host, opener=opener),
        "prompts": [prompt_identity(pid, text) for pid, text in prompts.items()],
    }


def stamp_problems(stamp: Mapping[str, Any]) -> list[str]:
    """Why a stamp does not pin its result (empty list means it does).

    A dirty tree is NOT a problem here -- it is recorded, and a reader may
    still compare against the commit -- but a missing commit, digest or
    prompt hash means the result cannot be tied to what it measured."""
    problems: list[str] = []
    if stamp.get("stamp_version") != STAMP_VERSION:
        problems.append(f"unknown stamp_version {stamp.get('stamp_version')!r}")
    harness = stamp.get("harness") or {}
    if not harness.get("commit"):
        problems.append("harness commit unavailable")
    model = stamp.get("model") or {}
    if not model.get("digest"):
        problems.append(f"model digest unavailable ({model.get('error', 'unknown')})")
    prompts = stamp.get("prompts") or []
    if not prompts:
        problems.append("no prompt identity")
    for entry in prompts:
        if len(str(entry.get("sha256_16", ""))) != PROMPT_HASH_LENGTH:
            problems.append(f"malformed prompt hash for {entry.get('id')!r}")
    return problems


def stamp_report_line(stamp: Mapping[str, Any]) -> str:
    """One markdown line naming the stamp, for a report's header."""
    harness = stamp.get("harness") or {}
    model = stamp.get("model") or {}
    commit = str(harness.get("commit") or "unknown")[:12]
    dirty = {True: " (dirty)", False: "", None: " (dirty unknown)"}[
        harness.get("dirty")
    ]
    digest = str(model.get("digest") or "unavailable")[:12]
    prompts = ", ".join(
        f"`{p['id']}` `{p['sha256_16']}`" for p in stamp.get("prompts") or []
    )
    return (
        f"Harness commit `{commit}`{dirty} · model `{model.get('name')}` digest "
        f"`{digest}` · prompts {prompts}."
    )


def sidecar_path(results_path: pathlib.Path) -> pathlib.Path:
    """`<results file>.stamp`: where the stamp of a result file that cannot
    carry one in-band lives (a JSONL ledger or a bare JSON list, whose
    loaders read every row as a record).

    The `.stamp` suffix, not `.json`, keeps the sidecar out of every
    `runs-*.json` glob a harness or a reader uses to find its results."""
    return results_path.with_name(results_path.name + ".stamp")


def write_stamp_sidecar(
    results_path: pathlib.Path, stamp: Mapping[str, Any]
) -> pathlib.Path:
    """Write `stamp` beside `results_path` and return the sidecar's path."""
    path = sidecar_path(results_path)
    path.write_text(json.dumps(stamp, indent=2) + "\n", encoding="utf-8")
    return path


def load_stamp(results_path: pathlib.Path) -> Mapping[str, Any] | None:
    """The stamp a stored result carries: in-band (`"stamp"` of a JSON
    object) or its `.stamp` sidecar. `None` when it carries none."""
    side = sidecar_path(results_path)
    if side.is_file():
        loaded = json.loads(side.read_text(encoding="utf-8"))
        return loaded if isinstance(loaded, dict) else None
    try:
        payload = json.loads(results_path.read_text(encoding="utf-8"))
    except ValueError:
        return None
    stamp = payload.get("stamp") if isinstance(payload, dict) else None
    return stamp if isinstance(stamp, dict) else None


def prompt_map(stamp: Mapping[str, Any]) -> dict[str, str]:
    """`{prompt id: sha256_16}` of a stamp, for a self-test to compare."""
    return {str(p["id"]): str(p["sha256_16"]) for p in stamp.get("prompts") or []}


def identity_section(stamps: Iterable[Mapping[str, Any]]) -> str:
    """A markdown `## Identity` section, one `stamp_report_line` per stamp,
    for a harness whose stored result is a REPORT (no `runs-*.json` to carry
    the stamp in-band)."""
    lines = ["", "## Identity", ""]
    lines += [f"- {stamp_report_line(stamp)}" for stamp in stamps]
    return "\n".join(lines) + "\n"


def summarize_latencies(values: Iterable[float]) -> dict[str, Any]:
    """`{"n", "median_s", "mean_s", "total_s"}`; `None`s when empty."""
    seq = [float(v) for v in values]
    if not seq:
        return {"n": 0, "median_s": None, "mean_s": None, "total_s": 0.0}
    return {
        "n": len(seq),
        "median_s": round(statistics.median(seq), 3),
        "mean_s": round(statistics.fmean(seq), 3),
        "total_s": round(sum(seq), 3),
    }


class TimedBackend:
    """Wraps an `LLMBackend`, recording the wall-clock of every `chat` call.

    Every other attribute forwards to the wrapped client, so a caller that
    reads one (`context_window`, a model tag) is unaffected. The call is
    recorded even when it raises: a call that burned 600 s and failed is the
    latency datum a budget most needs."""

    def __init__(
        self, inner: Any, *, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._inner = inner
        self._clock = clock
        self.call_latencies_s: list[float] = []

    def chat(self, messages: Sequence[Any]) -> str:
        started = self._clock()
        try:
            return str(self._inner.chat(messages))
        finally:
            self.call_latencies_s.append(self._clock() - started)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


# ---------------------------------------------------------------------------
# Self-test: model-free, no network, no git.
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, payload: object) -> None:
        self._raw = json.dumps(payload).encode()

    def read(self) -> bytes:
        return self._raw

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


def _self_test() -> int:
    failures: list[str] = []
    checks = 0

    def check(label: str, got: object, want: object) -> None:
        nonlocal checks
        checks += 1
        if got != want:
            failures.append(f"{label}: got {got!r}, want {want!r}")

    # Independently known sha256("abc"), so the definition is pinned to bytes
    # and not to this module's own implementation.
    check("prompt hash is sha256[:16]", prompt_hash("abc"), "ba7816bf8f01cfea")
    check(
        "prompt identity shape",
        prompt_identity("x/system", "abc"),
        {"id": "x/system", "sha256_16": "ba7816bf8f01cfea"},
    )
    try:
        prompt_identity(" ", "abc")
        check("blank prompt id refused", True, False)
    except ValueError:
        check("blank prompt id refused", True, True)

    seen_urls: list[str] = []

    def fake_open(url: str, timeout: float) -> _FakeResponse:
        seen_urls.append(url)
        return _FakeResponse(
            {
                "models": [
                    {"name": "qwen3:8b", "digest": "aaa111"},
                    {"name": "bge-m3:latest", "digest": "bbb222"},
                ]
            }
        )

    check(
        "digest found by exact tag",
        model_identity("qwen3:8b", "localhost:11434", opener=fake_open),
        {"name": "qwen3:8b", "digest": "aaa111"},
    )
    check(
        "tag-less name resolves :latest",
        model_identity("bge-m3", opener=fake_open)["digest"],
        "bbb222",
    )
    check(
        "digest request goes to the local tags endpoint",
        seen_urls[0],
        "http://localhost:11434/api/tags",
    )
    absent = model_identity("nope:1b", opener=fake_open)
    check("unlisted model has no digest", absent["digest"], None)
    check("unlisted model says why", "not listed" in absent["error"], True)
    seen_urls.clear()
    remote = model_identity("qwen3:8b", "http://example.com:11434", opener=fake_open)
    check("non-loopback host is never contacted", seen_urls, [])
    check("non-loopback host is reported", "non-loopback" in remote["error"], True)
    check(
        "0.0.0.0 server bind is normalized to loopback",
        _loopback_url("http://0.0.0.0:11434"),
        "http://127.0.0.1:11434",
    )

    def broken_open(url: str, timeout: float) -> _FakeResponse:
        raise OSError("connection refused")

    down = model_identity("qwen3:8b", opener=broken_open)
    check("unreachable server does not raise", down["digest"], None)
    check("unreachable server is named", "connection refused" in down["error"], True)

    class _Done:
        def __init__(self, code: int, out: str) -> None:
            self.returncode = code
            self.stdout = out

    def fake_git(argv: list[str], **_kw: object) -> _Done:
        if argv[1] == "rev-parse":
            return _Done(0, "deadbeef\n")
        return _Done(0, " M evals/x.py\n")

    check(
        "git identity reads commit and dirtiness",
        git_identity(runner=fake_git),
        {"commit": "deadbeef", "dirty": True},
    )
    check(
        "clean tree is dirty=False, not None",
        git_identity(
            runner=lambda argv, **_k: _Done(
                0, "c0ffee" if argv[1] == "rev-parse" else ""
            )
        ),
        {"commit": "c0ffee", "dirty": False},
    )

    def no_git(argv: list[str], **_kw: object) -> _Done:
        raise OSError("no git")

    check(
        "no git yields None, not a guess",
        git_identity(runner=no_git),
        {"commit": None, "dirty": None},
    )

    stamp = build_stamp(
        model="qwen3:8b",
        prompts={"contradiction/system": "abc"},
        host="localhost",
        opener=fake_open,
        runner=fake_git,
    )
    check(
        "stamp shape",
        sorted(stamp),
        ["harness", "model", "prompts", "stamp_version"],
    )
    check("complete stamp has no problems", stamp_problems(stamp), [])
    check("stamp is JSON-serializable", json.loads(json.dumps(stamp)), stamp)
    check(
        "report line names commit, digest and prompt",
        stamp_report_line(stamp),
        "Harness commit `deadbeef` (dirty) · model `qwen3:8b` digest `aaa111` "
        "· prompts `contradiction/system` `ba7816bf8f01cfea`.",
    )
    unpinned = build_stamp(
        model="nope:1b",
        prompts={"p": "abc"},
        opener=fake_open,
        runner=no_git,
    )
    check(
        "an unpinned stamp names every gap",
        [p.split(" (")[0] for p in stamp_problems(unpinned)],
        ["harness commit unavailable", "model digest unavailable"],
    )
    try:
        build_stamp(model="m", prompts={}, opener=fake_open, runner=fake_git)
        check("empty prompts refused", True, False)
    except ValueError:
        check("empty prompts refused", True, True)

    import tempfile

    with tempfile.TemporaryDirectory() as scratch:
        ledger = pathlib.Path(scratch) / "runs-x.jsonl"
        ledger.write_text("{}\n", encoding="utf-8")
        side = write_stamp_sidecar(ledger, stamp)
        check("sidecar sits beside its ledger", side.name, "runs-x.jsonl.stamp")
        check("sidecar round-trips the stamp", json.loads(side.read_text()), stamp)
        check(
            "sidecar is invisible to a runs-*.json glob",
            list(pathlib.Path(scratch).glob("runs-*.json")),
            [],
        )

    with tempfile.TemporaryDirectory() as scratch:
        inband = pathlib.Path(scratch) / "a.json"
        inband.write_text(json.dumps({"stamp": stamp}), encoding="utf-8")
        check("in-band stamp is found", load_stamp(inband), stamp)
        bare = pathlib.Path(scratch) / "b.json"
        bare.write_text("[]", encoding="utf-8")
        check("a result without a stamp yields None", load_stamp(bare), None)
    check(
        "prompt_map pairs id and hash",
        prompt_map(stamp),
        {"contradiction/system": "ba7816bf8f01cfea"},
    )

    check(
        "identity section lists every stamp's line",
        identity_section([stamp, stamp]).count("- Harness commit `deadbeef`"),
        2,
    )

    ticks = iter([0.0, 2.5, 10.0, 10.75])

    class _Inner:
        context_window = 12288

        def chat(self, messages: Sequence[Any]) -> str:
            if messages and messages[0] == "boom":
                raise RuntimeError("boom")
            return "ok"

    timed = TimedBackend(_Inner(), clock=lambda: next(ticks))
    check("timed backend forwards the reply", timed.chat(["hi"]), "ok")
    with contextlib.suppress(RuntimeError):
        timed.chat(["boom"])
    check("every call is timed, a failing one too", timed.call_latencies_s, [2.5, 0.75])
    check("other attributes forward", timed.context_window, 12288)
    check(
        "latency summary uses the median",
        summarize_latencies([1.0, 2.0, 9.0]),
        {"n": 3, "median_s": 2.0, "mean_s": 4.0, "total_s": 12.0},
    )
    check(
        "empty latency summary is honest",
        summarize_latencies([]),
        {"n": 0, "median_s": None, "mean_s": None, "total_s": 0.0},
    )

    for name in failures:
        print(f"FAIL: {name}")
    print(f"self-test: {checks - len(failures)}/{checks} passed")
    return 1 if failures else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="check the stamp, hashing and timing with no model and no network",
    )
    args = parser.parse_args(argv)
    if args.self_test:
        return _self_test()
    parser.error("nothing to run without --self-test; import this module instead")


if __name__ == "__main__":
    sys.exit(main())
