"""Local runtime screen (#1586): Ollama vs llama.cpp vs MLX on Apple Silicon.

Stage 1 measures performance with this runner. Stage 2 replays the #1560 and
#1557 harnesses through `--backend openai-compatible` (see `README.md`).
The rules are in `runtime_core.py` and quoted in `PREREGISTRATION.md`.

    uv run python -u evals/runtime_screen/run_runtime_screen.py --self-test
    ... perf  --runtime llama.cpp --label qwen3:8b --model-ref <path> --port 8081 \\
              --server-argv '["llama-server", "-m", "<path>", ...]' --out <dir>
    ... perf  --runtime ollama --label qwen3:8b --model-ref qwen3:8b --out <dir>
    ... swap  --runtime ollama --host 127.0.0.1:11500 --out <dir> ...
    ... floor --host 127.0.0.1:11500 --out <dir> --workspaces <a> <b>
    ... decide --out <dir>

Every server this runner starts is its own child, bound to 127.0.0.1, and is
stopped by the PID it was given; nothing else is ever signalled.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import pathlib
import shutil
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from typing import Any, Final

_HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

sys.path.append(str(_HERE.parent))

import runtime_core as core  # noqa: E402
from harness_stamp import build_stamp  # noqa: E402

PREFILL_WORDS: Final = 9_000
"""About 10,000 `qwen3:8b` tokens: production context (12,288) minus the
decode budget and the chat template."""
DECODE_TOKENS: Final = 512
CONTEXT_WINDOW: Final = 12_288
REPS: Final = 3
LOADS: Final = 3
SWAP_CYCLES: Final = 3
REQUEST_TIMEOUT_S: Final = 900.0
READY_TIMEOUT_S: Final = 600.0
UV: Final = shutil.which("uv") or "/opt/homebrew/bin/uv"
OLLAMA_LOG: Final = pathlib.Path("/opt/homebrew/var/log/ollama.log")
TINY: Final = [{"role": "user", "content": "Reply with the word ok."}]
INSTRUCTION: Final = "\n\nSummarize the text above in detail, section by section."


def _post(url: str, body: dict[str, Any], timeout: float) -> Any:
    request = urllib.request.Request(  # noqa: S310 -- loopback http only
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    return urllib.request.urlopen(request, timeout=timeout)  # noqa: S310 -- loopback


def _bench_messages(nonce: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": "You are a careful summarizer."},
        {
            "role": "user",
            "content": core.filler_text(PREFILL_WORDS, nonce=nonce) + INSTRUCTION,
        },
    ]


# --- transports -------------------------------------------------------------


def ollama_chat(
    host: str,
    model: str,
    messages: Sequence[dict[str, str]],
    *,
    num_predict: int,
    seed: int,
    stream: bool = True,
) -> dict[str, Any]:
    """One streamed `/api/chat` call at production context, thinking off."""
    body = {
        "model": model,
        "messages": list(messages),
        "stream": stream,
        "think": False,
        "options": {
            "num_ctx": CONTEXT_WINDOW,
            "num_predict": num_predict,
            "seed": seed,
        },
    }
    started = time.monotonic()
    first: float | None = None
    final: dict[str, Any] = {}
    text: list[str] = []
    with _post(f"http://{host}/api/chat", body, REQUEST_TIMEOUT_S) as response:
        for raw in response:
            chunk = json.loads(raw)
            piece = (chunk.get("message") or {}).get("content") or ""
            if piece and first is None:
                first = time.monotonic()
            text.append(piece)
            if chunk.get("done"):
                final = chunk
    finished = time.monotonic()
    out: dict[str, Any] = {
        "wall_s": finished - started,
        "reply_chars": len("".join(text)),
    }
    if final:
        server = core.ollama_rates(final)
        out["server"] = server
        if first is not None:
            out["client"] = core.stream_rates(
                started=started,
                first_token=first,
                finished=finished,
                prompt_tokens=server["prompt_tokens"],
                completion_tokens=server["completion_tokens"],
            )
    return out


def openai_chat(
    base_url: str,
    model: str,
    messages: Sequence[dict[str, str]],
    *,
    max_tokens: int,
    seed: int,
) -> dict[str, Any]:
    """One streamed `/v1/chat/completions` call, usage requested."""
    body = {
        "model": model,
        "messages": list(messages),
        "stream": True,
        "stream_options": {"include_usage": True},
        "max_tokens": max_tokens,
        "seed": seed,
    }
    started = time.monotonic()
    first: float | None = None
    usage: dict[str, Any] = {}
    server: dict[str, Any] | None = None
    text: list[str] = []
    reasoning = 0
    chunks = 0
    with _post(f"{base_url}/v1/chat/completions", body, REQUEST_TIMEOUT_S) as response:
        for payload in core.iter_sse(response):
            for choice in payload.get("choices") or []:
                delta = choice.get("delta") or {}
                piece = delta.get("content") or ""
                reasoning += len(delta.get("reasoning_content") or "")
                if piece:
                    chunks += 1
                    if first is None:
                        first = time.monotonic()
                    text.append(piece)
            if payload.get("usage"):
                usage = payload["usage"]
            server = core.llamacpp_rates(payload) or server
    finished = time.monotonic()
    reply = "".join(text)
    out: dict[str, Any] = {
        "wall_s": finished - started,
        "reply_chars": len(reply),
        "reasoning_chars": reasoning,
        "think_tag": "<think>" in reply,
        "usage": usage,
        "content_chunks": chunks,
    }
    if server:
        out["server"] = server
    if first is not None and usage.get("prompt_tokens"):
        out["client"] = core.stream_rates(
            started=started,
            first_token=first,
            finished=finished,
            prompt_tokens=int(usage["prompt_tokens"]),
            completion_tokens=int(usage.get("completion_tokens") or chunks),
        )
    return out


# --- processes and memory ---------------------------------------------------


def descendants(pid: int) -> list[int]:
    """`pid` and every descendant, read with `pgrep -P` (read-only)."""
    out = [pid]
    frontier = [pid]
    while frontier:
        parent = frontier.pop()
        done = subprocess.run(  # noqa: S603 -- fixed argv
            ["/usr/bin/pgrep", "-P", str(parent)],
            capture_output=True,
            text=True,
            check=False,
        )
        for line in done.stdout.split():
            child = int(line)
            out.append(child)
            frontier.append(child)
    return out


def tree_memory(pid: int) -> dict[str, Any]:
    """Resident and footprint summed over a server's process tree."""
    total = {"resident_bytes": 0, "footprint_bytes": 0}
    per: dict[int, dict[str, int]] = {}
    for member in descendants(pid):
        done = subprocess.run(  # noqa: S603 -- fixed argv
            ["/usr/bin/vmmap", "-summary", str(member)],
            capture_output=True,
            text=True,
            check=False,
        )
        with contextlib.suppress(ValueError):
            parsed = core.parse_vmmap(done.stdout)
            per[member] = parsed
            total["resident_bytes"] += parsed["resident_bytes"]
            total["footprint_bytes"] += parsed.get("footprint_bytes", 0)
    return {
        **total,
        "resident_gib": total["resident_bytes"] / 1024**3,
        "footprint_gib": total["footprint_bytes"] / 1024**3,
        "per_pid": per,
    }


def ollama_serve_pid(host: str) -> int:
    """The PID listening on `host`'s port (read-only `lsof`)."""
    port = host.rsplit(":", 1)[1]
    done = subprocess.run(  # noqa: S603 -- fixed argv
        ["/usr/sbin/lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
        capture_output=True,
        text=True,
        check=False,
    )
    pids = [int(p) for p in done.stdout.split()]
    if len(pids) != 1:
        raise SystemExit(f"expected one listener on {host}, found {pids}")
    return pids[0]


def ollama(host: str, *argv: str) -> str:
    env = {**os.environ, "OLLAMA_HOST": host}
    done = subprocess.run(  # noqa: S603 -- the local ollama CLI, fixed verbs
        ["/opt/homebrew/bin/ollama", *argv],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    return done.stdout


class Server:
    """A server process this runner started, and only that one."""

    def __init__(
        self,
        argv: Sequence[str],
        log_path: pathlib.Path,
        env: dict[str, str] | None = None,
    ):
        self.log_path = log_path
        self._log = log_path.open("ab")
        self.started = time.monotonic()
        self.proc = subprocess.Popen(  # noqa: S603 -- argv given by the operator
            list(argv),
            stdout=self._log,
            stderr=subprocess.STDOUT,
            env={**os.environ, **(env or {})},
            start_new_session=True,
        )

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=60)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=30)
        self._log.close()


def wait_first_reply(call: Callable[[], Any], deadline_s: float) -> float:
    """Seconds until `call` first succeeds (a server still loading refuses
    or errors; both are retried until the deadline)."""
    started = time.monotonic()
    while True:
        try:
            call()
            return time.monotonic() - started
        except urllib.error.URLError, ConnectionError, OSError, ValueError:
            if time.monotonic() - started > deadline_s:
                raise
            time.sleep(0.25)


# --- stages -----------------------------------------------------------------


def _median(values: Sequence[float | None]) -> float | None:
    real = [v for v in values if v is not None]
    return statistics.median(real) if real else None


def runtime_stamp(args: argparse.Namespace) -> dict[str, Any]:
    """`harness_stamp`'s identity, with the model entry naming the runtime,
    what it was asked for and the weights digest given (no Ollama reports
    one for another runtime)."""
    stamp = build_stamp(
        model=args.label,
        prompts={"runtime/bench": _bench_messages("stamp")[0]["content"] + INSTRUCTION},
    )
    if args.runtime != "ollama":
        stamp["model"] = {
            "name": args.label,
            "digest": args.digest,
            "backend": args.runtime,
            "served_model": args.model_ref,
        }
    return stamp


def cmd_perf(args: argparse.Namespace) -> int:
    out_dir: pathlib.Path = args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = f"{args.runtime}-{args.label}".replace(":", "-").replace("/", "_")
    record: dict[str, Any] = {
        "runtime": args.runtime,
        "label": args.label,
        "model_ref": args.model_ref,
        "server_argv": args.server_argv,
        "date": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "loads": [],
        "bench": [],
        "stamp": runtime_stamp(args),
    }
    server: Server | None = None
    base_url = f"http://127.0.0.1:{args.port}"

    def tiny() -> Any:
        if args.runtime == "ollama":
            return ollama_chat(
                args.host, args.model_ref, TINY, num_predict=8, seed=0, stream=False
            )
        return openai_chat(base_url, args.model_ref, TINY, max_tokens=8, seed=0)

    log_offset = OLLAMA_LOG.stat().st_size if args.runtime == "ollama" else 0
    try:
        for i in range(args.loads):
            if args.runtime == "ollama":
                ollama(args.host, "stop", args.model_ref)
                time.sleep(3)
                load_s = wait_first_reply(tiny, READY_TIMEOUT_S)
            else:
                if server is not None:
                    server.stop()
                    time.sleep(3)
                server = Server(
                    json.loads(args.server_argv), out_dir / f"{slug}.server.log"
                )
                load_s = wait_first_reply(tiny, READY_TIMEOUT_S)
                load_s = time.monotonic() - server.started
            record["loads"].append(load_s)
            print(f"{slug} load {i + 1}: {load_s:.2f} s", flush=True)
        for k in range(args.reps):
            messages = _bench_messages(f"{slug}-r{k}")
            if args.runtime == "ollama":
                rep = ollama_chat(
                    args.host,
                    args.model_ref,
                    messages,
                    num_predict=DECODE_TOKENS,
                    seed=k + 1,
                )
            else:
                rep = openai_chat(
                    base_url,
                    args.model_ref,
                    messages,
                    max_tokens=DECODE_TOKENS,
                    seed=k + 1,
                )
            record["bench"].append(rep)
            print(f"{slug} bench {k + 1}: {json.dumps(rep.get('client'))}", flush=True)
        pid = server.proc.pid if server is not None else ollama_serve_pid(args.host)
        record["memory"] = tree_memory(pid)
        if args.runtime == "ollama":
            record["ollama_ps"] = ollama(args.host, "ps")
            with OLLAMA_LOG.open("rb") as log:
                log.seek(log_offset)
                record["loader_mib"] = core.loader_report_mib(
                    log.read().decode("utf-8", "replace")
                )
        elif args.runtime == "llama.cpp":
            record["loader_mib"] = core.loader_report_mib(
                (out_dir / f"{slug}.server.log").read_text(
                    encoding="utf-8", errors="replace"
                )
            )
    finally:
        if server is not None:
            server.stop()
    clients = [b.get("client") or {} for b in record["bench"]]
    record["summary"] = {
        "load_s_median": _median(record["loads"]),
        "load_s_first": record["loads"][0] if record["loads"] else None,
        "prefill_tps": _median([c.get("prefill_tps") for c in clients]),
        "decode_tps": _median([c.get("decode_tps") for c in clients]),
        "resident_gib": record.get("memory", {}).get("resident_gib"),
        "footprint_gib": record.get("memory", {}).get("footprint_gib"),
        "loader_gib": (record["loader_mib"] / 1024)
        if record.get("loader_mib")
        else None,
    }
    path = out_dir / f"perf-{slug}.json"
    path.write_text(json.dumps(record, indent=1), encoding="utf-8")
    print(json.dumps(record["summary"], indent=1))
    print(f"wrote {path}")
    return 0


def cmd_swap(args: argparse.Namespace) -> int:
    """One model resident at a time: time each switch to the first reply.

    `--first`/`--second` name the two models as the runtime is asked for
    them. Ollama is the operator's second server (`--host`) started with
    `OLLAMA_MAX_LOADED_MODELS=1`; llama.cpp is a router (`--models-max 1`);
    MLX loads whichever model a request names."""
    out_dir: pathlib.Path = args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    server: Server | None = None
    base_url = f"http://127.0.0.1:{args.port}"

    def tiny(model: str) -> Any:
        if args.runtime == "ollama":
            return ollama_chat(
                args.host, model, TINY, num_predict=8, seed=0, stream=False
            )
        return openai_chat(base_url, model, TINY, max_tokens=8, seed=0)

    record: dict[str, Any] = {
        "runtime": args.runtime,
        "first": args.first,
        "second": args.second,
        "switches": [],
    }
    try:
        if args.server_argv:
            server = Server(
                json.loads(args.server_argv),
                out_dir / f"swap-{args.runtime}.server.log",
            )
        wait_first_reply(lambda: tiny(args.first), READY_TIMEOUT_S)
        for cycle in range(SWAP_CYCLES):
            for model in (args.second, args.first):
                started = time.monotonic()
                tiny(model)
                seconds = time.monotonic() - started
                ps = ollama(args.host, "ps") if args.runtime == "ollama" else None
                record["switches"].append(
                    {"cycle": cycle, "to": model, "seconds": seconds, "ps": ps}
                )
                print(f"swap {args.runtime} -> {model}: {seconds:.2f} s", flush=True)
    finally:
        if server is not None:
            server.stop()
    record["swap_s_median"] = _median([s["seconds"] for s in record["switches"]])
    path = out_dir / f"swap-{args.runtime}.json"
    path.write_text(json.dumps(record, indent=1), encoding="utf-8")
    print(f"median {record['swap_s_median']:.2f} s; wrote {path}")
    return 0


def cmd_floor(args: argparse.Namespace) -> int:
    """#1587: #1585's protocol against a one-model Ollama (`--host`)."""
    out_dir: pathlib.Path = args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(_HERE.parent / "query_grounding"))
    from grounding_corpus import QUESTIONS

    questions = [q for _label, q in QUESTIONS[:6]]
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    env["OLLAMA_HOST"] = args.host
    results: list[dict[str, Any]] = []
    for seq in (1, 2, 3):
        for arm, workspace in (("a", args.workspaces[0]), ("b", args.workspaces[1])):
            for model in ("qwen3:8b", "gemma4:26b-a4b", "bge-m3"):
                ollama(args.host, "stop", model)
            time.sleep(2)
            entry: dict[str, Any] = {
                "arm": arm,
                "seq": seq,
                "before": ollama(args.host, "ps"),
                "times": [],
            }
            for i, question in enumerate(questions):
                started = time.monotonic()
                done = subprocess.run(  # noqa: S603 -- the repo's own CLI
                    [
                        UV,
                        "run",
                        "--project",
                        str(args.project),
                        "openkos",
                        "query",
                        question,
                    ],
                    cwd=workspace,
                    capture_output=True,
                    text=True,
                    env=env,
                    check=False,
                )
                seconds = time.monotonic() - started
                (out_dir / f"{arm}-s{seq}-q{i}.txt").write_text(
                    f"rc={done.returncode}\n--stdout--\n{done.stdout}\n--stderr--\n{done.stderr}",
                    encoding="utf-8",
                )
                entry["times"].append(
                    {
                        "q": i,
                        "seconds": round(seconds, 2),
                        "rc": done.returncode,
                        "ps": ollama(args.host, "ps"),
                    }
                )
                print(arm, seq, i, round(seconds, 2), done.returncode, flush=True)
            entry["after"] = ollama(args.host, "ps")
            results.append(entry)
            (out_dir / "results.json").write_text(
                json.dumps(results, indent=1), encoding="utf-8"
            )
    verdict = core.floor_verdict(results)
    (out_dir / "verdict.json").write_text(
        json.dumps(verdict, indent=1), encoding="utf-8"
    )
    print(json.dumps(verdict, indent=1))
    return 0


def cmd_embed_check(args: argparse.Namespace) -> int:
    """The `bge-m3` path on llama.cpp: dimensions and agreement with Ollama."""
    texts = [
        "The team agreed to ship the compiler first.",
        "La reunión decidió publicar el compilador primero.",
        "Bruno owns the migration of the old corpus.",
    ]
    with _post(
        f"http://127.0.0.1:{args.port}/v1/embeddings",
        {"model": "bge-m3", "input": texts},
        120,
    ) as r:
        llama = [
            row["embedding"]
            for row in sorted(json.load(r)["data"], key=lambda x: x["index"])
        ]
    with _post(
        f"http://{args.host}/api/embed", {"model": "bge-m3", "input": texts}, 120
    ) as r:
        ollama_vectors = json.load(r)["embeddings"]

    def cosine(a: Sequence[float], b: Sequence[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b, strict=True))
        na = sum(x * x for x in a) ** 0.5
        nb = sum(y * y for y in b) ** 0.5
        return float(dot / (na * nb))

    out = {
        "dims": [len(v) for v in llama],
        "cosine_vs_ollama": [
            cosine(a, b) for a, b in zip(llama, ollama_vectors, strict=True)
        ],
    }
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "embed-check.json").write_text(
        json.dumps(out, indent=1), encoding="utf-8"
    )
    print(json.dumps(out, indent=1))
    return 0


# --- self-test --------------------------------------------------------------


def _self_test() -> int:
    """Model-free: the rules and parsers this runner reports through."""
    failures: list[str] = []

    def check(name: str, cond: bool) -> None:
        print(("ok   " if cond else "FAIL ") + name)
        if not cond:
            failures.append(name)

    rates = core.stream_rates(
        started=0,
        first_token=2,
        finished=7,
        prompt_tokens=10_000,
        completion_tokens=501,
    )
    check("prefill rate", rates["prefill_tps"] == 5_000)
    check("decode rate", rates["decode_tps"] == 100)
    check(
        "sse", list(core.iter_sse([b'data: {"x": 1}', b"data: [DONE]"])) == [{"x": 1}]
    )
    prompt = _bench_messages("n")[1]["content"]
    check("bench prompt length", len(prompt.split()) >= PREFILL_WORDS)
    check("bench prompt distinct per rep", prompt != _bench_messages("m")[1]["content"])
    perf = core.Perf(prefill_tps=1000, decode_tps=50, resident_gib=7, load_s=5)
    check("equal runtime fails stage 1", not core.stage1_verdict(perf, perf)["passes"])
    faster = core.Perf(prefill_tps=1000, decode_tps=60, resident_gib=7, load_s=5)
    check("faster runtime passes stage 1", core.stage1_verdict(perf, faster)["passes"])
    check(
        "loader report",
        core.loader_report_mib(
            "x  MTL0_Mapped model buffer size = 10.00 MiB\nMTL0 KV buffer size = 2.00 MiB\n"
        )
        == 12.0,
    )
    print(f"{len(failures)} failing")
    return 1 if failures else 0


# --- CLI --------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--self-test", action="store_true")
    sub = parser.add_subparsers(dest="command")
    for name in ("perf", "swap"):
        p = sub.add_parser(name)
        p.add_argument(
            "--runtime", required=True, choices=("ollama", "llama.cpp", "mlx")
        )
        p.add_argument("--out", type=pathlib.Path, required=True)
        p.add_argument("--host", default="127.0.0.1:11434")
        p.add_argument("--port", type=int, default=8081)
        p.add_argument(
            "--server-argv", default=None, help="JSON argv of the server to start"
        )
    perf = sub.choices["perf"]
    perf.add_argument("--label", required=True)
    perf.add_argument("--model-ref", required=True, help="Ollama tag or served model")
    perf.add_argument("--reps", type=int, default=REPS)
    perf.add_argument("--digest", default=None, help="weights digest (not Ollama)")
    perf.add_argument("--loads", type=int, default=LOADS)
    swap = sub.choices["swap"]
    swap.add_argument("--first", required=True)
    swap.add_argument("--second", required=True)
    floor = sub.add_parser("floor")
    floor.add_argument("--host", required=True)
    floor.add_argument("--out", type=pathlib.Path, required=True)
    floor.add_argument("--workspaces", type=pathlib.Path, nargs=2, required=True)
    floor.add_argument("--project", type=pathlib.Path, required=True)
    embed = sub.add_parser("embed-check")
    embed.add_argument("--port", type=int, required=True)
    embed.add_argument("--host", default="127.0.0.1:11434")
    embed.add_argument("--out", type=pathlib.Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.self_test:
        return _self_test()
    commands: dict[str, Callable[[argparse.Namespace], int]] = {
        "perf": cmd_perf,
        "swap": cmd_swap,
        "floor": cmd_floor,
        "embed-check": cmd_embed_check,
    }
    if args.command not in commands:
        raise SystemExit("choose a subcommand, or --self-test")
    return commands[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
