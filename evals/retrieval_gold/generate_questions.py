"""`generate` for #1650: the questions each Decision answers (doc2query).

The ONLY GPU step of `PREREGISTRATION-1650.md`. It reads the Decisions of a
built bundle and asks the registered chat model (`qwen3:8b`, the shipped ingest
model, through the production `OllamaClient`) for three questions each, with the
registered prompt verbatim. It takes a bundle and NO questions file: the gold
questions are not reachable from this command, which is what makes the leak
audit (run later by `run --doc2query`) a real check.

    generate     one chat call per Decision (one retry on a parse failure)
    cost         the registered cost bars, read back from a generated file
    --self-test  no model is contacted

The output is the artifact all arms share: `questions` (Decision -> kept
questions, read by `prereg_tools.py leak-audit` and `run --doc2query`) and
`entries` (keyed by the sha256 of the prompt and the Decision text, with the
raw replies, the per-call seconds and `eval_count`), plus the identity stamp
(harness commit, model digest, prompt hash). Paths for outputs must be OUTSIDE
the repository.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from typing import Any, Final

HERE: Final = pathlib.Path(__file__).resolve().parent
REPO_ROOT: Final = HERE.parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(HERE))
sys.path.append(str(REPO_ROOT / "evals"))

import doc2query_selftest as st  # noqa: E402
import gold_core as core  # noqa: E402
import gold_doc2query as d2q  # noqa: E402
import gold_prereg as pre  # noqa: E402
import harness_stamp  # noqa: E402

from openkos.llm.base import BackendUnavailable  # noqa: E402

DEFAULT_MODEL: Final = d2q.MODEL
# The production chat settings (`config.py` defaults): what `ingest` sends.
CHAT_TIMEOUT_S: Final = 600.0
MAX_GENERATION_TOKENS: Final = 8192
CONTEXT_WINDOW: Final = 12288
GPU_POLL_S: Final = 30.0
STATE_DIR: Final = "gold-build"


def _refuse_inside_repo(path: pathlib.Path) -> None:
    problem = core.outside_repo_problem(path, REPO_ROOT)
    if problem:
        raise SystemExit(problem)


def _atomic_write(path: pathlib.Path, data: object) -> None:
    """Write beside, then rename: an interrupted run leaves the last whole file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


class _Recorder:
    """Holds the diagnostics of the latest chat response (`eval_count`)."""

    def __init__(self) -> None:
        self.latest: Any = None

    def record(self, diagnostics: Any) -> None:
        self.latest = diagnostics

    def last(self) -> Any:
        latest, self.latest = self.latest, None
        return latest


class _OllamaChat:
    """The production `OllamaClient`, adapted to `gold_doc2query.Chat`."""

    def __init__(self, client: Any, recorder: _Recorder) -> None:
        self._client = client
        self._recorder = recorder

    def chat(self, messages: Sequence[dict[str, str]]) -> d2q.Reply:
        text = self._client.chat(messages)
        diagnostics = self._recorder.last()
        return d2q.Reply(text=text, eval_count=getattr(diagnostics, "eval_count", None))


def wait_for_gpu(
    probe: Callable[[], dict[str, Any]],
    *,
    sleep: Callable[[float], None],
    log: Callable[[str], None],
) -> None:
    """Block until the local Ollama reports no loaded model: it serializes, and
    a generation timed behind another run would measure the queue, not the model."""
    while probe().get("models"):
        log("waiting for the GPU: another model is loaded")
        sleep(GPU_POLL_S)


def _options() -> dict[str, Any]:
    return {
        "think": False,
        "timeout_s": CHAT_TIMEOUT_S,
        "max_generation_tokens": MAX_GENERATION_TOKENS,
        "context_window": CONTEXT_WINDOW,
        "temperature": None,
        "seed": None,
    }


def _default_manifest(bundle: pathlib.Path) -> pathlib.Path | None:
    path = bundle.parent / STATE_DIR / "manifest.json"
    return path if path.is_file() else None


def cmd_generate(args: argparse.Namespace) -> int:
    _refuse_inside_repo(args.out)
    decisions = d2q.read_decisions(args.bundle)
    if not decisions:
        raise SystemExit(f"{args.bundle} has no Decision concept")
    system = d2q.PROMPTS[args.prompt]
    previous: dict[str, Any] = {}
    if args.out.exists():
        if not args.resume:
            raise SystemExit(f"{args.out} exists; pass --resume to continue it")
        old = json.loads(args.out.read_text("utf-8"))
        if old.get("kind") != pre.GENERATED_KIND:
            raise SystemExit(f"{args.out} is not a {pre.GENERATED_KIND} file")
        if old["prompt"]["id"] != args.prompt or old["model"] != args.model:
            raise SystemExit("--resume must use the prompt and the model of the file")
        previous = old["entries"]
    manifest = args.manifest or _default_manifest(args.bundle)
    ingest = d2q.ingest_seconds(manifest) if manifest else None
    if args.stratum_sha256 is None:
        print(
            "NOTE: no --stratum-sha256; the registration records it before generating"
        )
    if args.wait_gpu:
        wait_for_gpu(
            lambda: harness_stamp.ollama_get_json("/api/ps"),
            sleep=time.sleep,
            log=print,
        )
    from openkos.llm.ollama import OllamaClient

    recorder = _Recorder()
    client = OllamaClient(
        model=args.model,
        timeout=CHAT_TIMEOUT_S,
        max_generation_tokens=MAX_GENERATION_TOKENS,
        context_window=CONTEXT_WINDOW,
        diagnostics_sink=recorder.record,
    )
    stamp = harness_stamp.build_stamp(
        model=args.model, prompts={d2q.PROMPT_IDS[args.prompt]: system}
    )
    for problem in harness_stamp.stamp_problems(stamp):
        print(f"STAMP: {problem}")

    def document(result: d2q.Generation, *, complete: bool) -> dict[str, Any]:
        return d2q.generated_document(
            result,
            prompt_id=args.prompt,
            system=system,
            model=args.model,
            stamp=stamp,
            options=_options(),
            bundle=str(args.bundle),
            stratum_sha256=args.stratum_sha256,
            complete=complete,
            ingest_seconds=ingest,
        )

    progress = d2q.Generation(entries=dict(previous))

    def persist(entry: dict[str, Any]) -> None:
        progress.entries[entry["key"]] = entry
        if entry["kept"]:
            progress.questions[entry["decision"]] = list(entry["kept"])
        _atomic_write(args.out, document(progress, complete=False))
        print(
            f"{entry['decision']}: {len(entry['kept'])} kept, "
            f"{entry['seconds']:.1f} s, {entry['attempts']} attempt(s)"
        )

    try:
        result = d2q.generate(
            decisions,
            _OllamaChat(client, recorder),
            system=system,
            prompt_id=args.prompt,
            clock=time.monotonic,
            previous=previous,
            on_entry=persist,
        )
    except (BackendUnavailable, KeyboardInterrupt) as exc:
        print(f"STOPPED ({type(exc).__name__}); {args.out} holds what finished")
        return 1
    _atomic_write(args.out, document(result, complete=True))
    print(f"wrote {args.out}")
    print(json.dumps(result.summary, indent=2))
    print(d2q.render_cost(d2q.cost_report(result.entries, ingest_seconds=ingest)))
    return 0


def cmd_cost(args: argparse.Namespace) -> int:
    raw = json.loads(args.generated.read_text("utf-8"))
    if raw.get("kind") != pre.GENERATED_KIND:
        raise SystemExit(f"not a {pre.GENERATED_KIND} file")
    ingest = d2q.ingest_seconds(args.manifest) if args.manifest else None
    report = d2q.cost_report(raw["entries"], ingest_seconds=ingest)
    print(d2q.render_cost(report))
    verdicts = [report["bar_p90"]["pass"], report["bar_total"]["pass"]]
    return 1 if False in verdicts else 0


# --------------------------------------------------------------------------- #
# Self-test (no model)                                                         #
# --------------------------------------------------------------------------- #


class _Checks:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self.total = 0

    def check(self, ok: bool, label: str) -> None:
        self.total += 1
        if not ok:
            self.failures.append(label)
            print(f"FAIL: {label}")


class _Probe:
    def __init__(self, loaded: list[bool]) -> None:
        self._loaded = loaded
        self.calls = 0

    def __call__(self) -> dict[str, Any]:
        self.calls += 1
        return {"models": [{"name": "x"}] if self._loaded.pop(0) else []}


class _FakeClient:
    def chat(self, messages: Sequence[dict[str, str]]) -> str:
        return "[]"


def _self_test_cli(c: _Checks) -> None:
    probe = _Probe([True, True, False])
    naps: list[float] = []
    logs: list[str] = []
    wait_for_gpu(probe, sleep=naps.append, log=logs.append)
    c.check(
        probe.calls == 3 and naps == [GPU_POLL_S, GPU_POLL_S],
        "waits while a model is loaded",
    )
    idle = _Probe([False])
    wait_for_gpu(idle, sleep=naps.append, log=logs.append)
    c.check(idle.calls == 1, "an idle GPU does not wait")

    rec = _Recorder()
    rec.record(type("Diagnostics", (), {"eval_count": 61})())
    adapter = _OllamaChat(_FakeClient(), rec)
    c.check(adapter.chat([]).eval_count == 61, "eval_count comes from the diagnostics")
    c.check(
        adapter.chat([]).eval_count is None, "a response with no diagnostics has none"
    )

    options = _options()
    c.check(
        options["think"] is False and options["context_window"] == 12288,
        "the production setting: think false, the shipped window",
    )
    with tempfile.TemporaryDirectory() as tmp:
        target = pathlib.Path(tmp) / "x.json"
        _atomic_write(target, {"a": 1})
        _atomic_write(target, {"a": 2})
        c.check(
            json.loads(target.read_text("utf-8")) == {"a": 2}, "atomic write replaces"
        )
        c.check(not target.with_name("x.json.tmp").exists(), "no temp file left")
    c.check(
        core.outside_repo_problem(REPO_ROOT / "x.json", REPO_ROOT) is not None,
        "an output inside the repository is refused",
    )
    c.check(STATE_DIR == "gold-build", "the manifest directory is build_bundle.py's")


def _self_test() -> int:
    c = _Checks()
    st.pure(c)
    _self_test_cli(c)
    print(f"self-test: {c.total - len(c.failures)}/{c.total} passed")
    return 1 if c.failures else 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true", help="no model, no network")
    sub = parser.add_subparsers(dest="command")

    gen = sub.add_parser("generate", help="the one GPU step: questions per Decision")
    gen.add_argument("--bundle", type=pathlib.Path, required=True)
    gen.add_argument("--out", type=pathlib.Path, required=True)
    gen.add_argument(
        "--prompt", choices=sorted(d2q.PROMPTS), default=d2q.PRIMARY_PROMPT
    )
    gen.add_argument("--model", default=DEFAULT_MODEL)
    gen.add_argument("--manifest", type=pathlib.Path, default=None)
    gen.add_argument("--stratum-sha256", default=None)
    gen.add_argument("--resume", action="store_true")
    gen.add_argument("--wait-gpu", action="store_true")
    gen.set_defaults(func=cmd_generate)

    cost = sub.add_parser("cost", help="the registered cost bars, no model")
    cost.add_argument("--generated", type=pathlib.Path, required=True)
    cost.add_argument("--manifest", type=pathlib.Path, default=None)
    cost.set_defaults(func=cmd_cost)
    return parser


if __name__ == "__main__":
    args = _parser().parse_args()
    if args.self_test:
        raise SystemExit(_self_test())
    if not getattr(args, "func", None):
        _parser().error("a subcommand or --self-test is required")
    raise SystemExit(args.func(args))
