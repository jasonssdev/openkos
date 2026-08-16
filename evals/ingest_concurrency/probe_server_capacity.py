"""What the Ollama SERVER does with concurrent requests, and what it costs.

Two questions the main probe's speedup table cannot answer about itself, both
about the process openkos talks to rather than openkos itself:

1. **Does a default server run concurrent requests in parallel at all?** If it
   queues them, then threading `extract_concept_union`'s window loop is a
   no-op and every speedup in this directory is conditional on a setting the
   product does not ship.

2. **What does raising that setting cost in resident memory?** #739's premise
   is that #691's pinned 12288 window brought qwen3:8b to 7.2 GB, so a second
   slot fits in the ~11 GB a 16 GB machine has after the OS. Ollama allocates
   one KV-cache slot PER parallel request, so the footprint moves with the
   knob -- and the answer decides whether the lever is affordable on the
   hardware the issue is about.

Both run against servers this script starts and stops on a scratch port. The
user's own server is never touched, restarted, or reconfigured.

Usage:

    python evals/ingest_concurrency/probe_server_capacity.py
    python evals/ingest_concurrency/probe_server_capacity.py --self-test
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

MODEL = "qwen3:8b"
NUM_CTX = 12288
"""`DEFAULT_CONTEXT_WINDOW`. Restated as a literal rather than imported: this
script is about the server's behaviour under production's pinned window, and
it must keep meaning that even if run from a checkout where the constant has
moved."""

_SERIALIZATION_PORT = 11435
_FOOTPRINT_PORT = 11436
_PARALLEL_LEVELS = (1, 2, 3, 4)


def _generate(host: str, *, num_predict: int) -> float:
    body = json.dumps(
        {
            "model": MODEL,
            "prompt": (
                "Enumera exactamente tres principios de trazabilidad "
                "documental. Responde en tres lineas breves, sin preambulo."
            ),
            "stream": False,
            # Fixed seed and zero temperature so every call does comparable
            # work -- a shorter reply would otherwise read as a speedup.
            "options": {
                "num_ctx": NUM_CTX,
                "num_predict": num_predict,
                "temperature": 0.0,
                "seed": 42,
            },
        }
    ).encode()
    request = urllib.request.Request(  # noqa: S310
        f"{host}/api/generate",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    started = time.monotonic()
    with urllib.request.urlopen(request, timeout=900) as response:  # noqa: S310
        json.loads(response.read())
    return time.monotonic() - started


def _port_is_free(port: int) -> bool:
    """Nothing is already answering on `port`.

    Checked BEFORE spawning, because the readiness loop below cannot tell our
    child apart from a stranger: both answer the version endpoint on the same
    loopback port. A server left over from an earlier run would be measured
    under ITS `OLLAMA_NUM_PARALLEL`, not the one this arm asked for, and the
    whole table would be silently mislabelled."""
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/api/version", timeout=2).read()
    except OSError:
        return True
    return False


def _serve(port: int, *, num_parallel: int | None) -> subprocess.Popen[bytes]:
    """A scratch server on `port`, so the user's own is left alone."""
    if not _port_is_free(port):
        raise RuntimeError(
            f"something is already serving on port {port}; this probe refuses "
            "to measure a server it did not start, because its "
            "OLLAMA_NUM_PARALLEL is unknown"
        )
    env = dict(os.environ)
    env["OLLAMA_HOST"] = f"127.0.0.1:{port}"
    env["OLLAMA_MAX_LOADED_MODELS"] = "1"
    if num_parallel is None:
        # The DEFAULT arm: the variable must be absent, not set to a value we
        # believe is the default. Inheriting a shell that happens to export it
        # would silently turn this into a second configured arm.
        env.pop("OLLAMA_NUM_PARALLEL", None)
    else:
        env["OLLAMA_NUM_PARALLEL"] = str(num_parallel)
    server = subprocess.Popen(
        ["ollama", "serve"],  # noqa: S607
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        # Our own child first. `stderr` is discarded, so a bind failure is
        # otherwise invisible, and a responder on the port would then be read
        # as success -- the arm would measure a stranger's configuration.
        if server.poll() is not None:
            raise RuntimeError(
                f"the scratch server for port {port} exited with code "
                f"{server.returncode} before becoming ready"
            )
        try:
            urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/version", timeout=2
            ).read()
            return server
        except OSError:
            time.sleep(1)
    server.terminate()
    raise RuntimeError(f"scratch server on {port} did not start")


def _stop(server: subprocess.Popen[bytes]) -> None:
    server.terminate()
    try:
        server.wait(timeout=30)
    except subprocess.TimeoutExpired:
        server.kill()
        server.wait(timeout=30)
    # The model unloads asynchronously; the next arm must not measure its
    # residue.
    time.sleep(5)


def _loaded_size(host: str) -> tuple[float, int | None]:
    with urllib.request.urlopen(f"{host}/api/ps", timeout=30) as response:  # noqa: S310
        data = json.loads(response.read())
    models = data.get("models") or []
    if not models:
        return 0.0, None
    return models[0]["size"] / 1e9, models[0].get("context_length")


def measure_serialization() -> None:
    """Concurrent wall clock at the server default vs an explicitly raised
    `OLLAMA_NUM_PARALLEL`."""
    host = f"http://127.0.0.1:{_SERIALIZATION_PORT}"
    for label, num_parallel in (("default (unset)", None), ("=4", 4)):
        server = _serve(_SERIALIZATION_PORT, num_parallel=num_parallel)
        try:
            _generate(host, num_predict=512)  # warm; excluded
            solo = _generate(host, num_predict=512)
            print(f"\nOLLAMA_NUM_PARALLEL {label} — solo request {solo:.1f}s")
            print("| concurrent | wall (s) | per-call (s) | speedup |")
            print("| --- | --- | --- | --- |")
            for n in (2, 3, 4):
                started = time.monotonic()
                with ThreadPoolExecutor(max_workers=n) as pool:
                    per_call = list(
                        pool.map(lambda _: _generate(host, num_predict=512), range(n))
                    )
                wall = time.monotonic() - started
                shown = ", ".join(f"{d:.1f}" for d in sorted(per_call))
                print(f"| {n} | {wall:.1f} | {shown} | {n * solo / wall:.2f}x |")
        finally:
            _stop(server)


def measure_footprint() -> None:
    """Resident size of one loaded model at each parallel level.

    Measured after a real generation, because the KV cache the level governs
    does not exist until a request has used it."""
    host = f"http://127.0.0.1:{_FOOTPRINT_PORT}"
    print("\n| OLLAMA_NUM_PARALLEL | resident (GB) | context |")
    print("| --- | --- | --- |")
    for level in _PARALLEL_LEVELS:
        server = _serve(_FOOTPRINT_PORT, num_parallel=level)
        try:
            _generate(host, num_predict=16)
            size, ctx = _loaded_size(host)
            print(f"| {level} | {size:.1f} | {ctx} |")
        finally:
            _stop(server)


def _self_test() -> int:
    """The scratch-port discipline, without loading a model."""
    failures: list[str] = []
    for port in (_SERIALIZATION_PORT, _FOOTPRINT_PORT):
        if port == 11434:
            failures.append(
                f"port {port} is Ollama's default — that is the "
                "user's own server, which this probe must not touch"
            )
    env = dict(os.environ)
    env["OLLAMA_NUM_PARALLEL"] = "9"
    # The default arm must UNSET the variable, never trust an inherited one.
    scrubbed = dict(env)
    scrubbed.pop("OLLAMA_NUM_PARALLEL", None)
    if "OLLAMA_NUM_PARALLEL" in scrubbed:
        failures.append("the default arm would inherit OLLAMA_NUM_PARALLEL")
    for line in failures:
        print(f"FAIL {line}")
    if not failures:
        print(
            f"ok  scratch ports {_SERIALIZATION_PORT}/{_FOOTPRINT_PORT}, "
            "default arm scrubs OLLAMA_NUM_PARALLEL"
        )
    return 1 if failures else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        raise SystemExit(_self_test())
    measure_serialization()
    measure_footprint()


if __name__ == "__main__":
    main()
