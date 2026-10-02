"""The local Ollama, for the bake-off's eligibility gates (#1269).

Everything here talks to the LOCAL Ollama only; a non-loopback host is refused
before any request, the same rule `harness_stamp` applies. The reads are
`/api/tags` (what is pulled, with digests), `/api/show` (license text, native
context) and `/api/ps` (what is resident and how much memory it holds -- the
numbers `ollama ps` prints). The only writes are loading a model (an empty
`/api/generate` with `num_ctx`, an `/api/embed` for `bge-m3`) and unloading it
(`keep_alive: 0`), which is how a memory peak is measured at a chosen context.

No model is ever PULLED here: that is a network download, which this driver
never does. A candidate that is not pulled is reported as pending its pull.

`classify_license`, `native_context` and `decide_eligibility` are pure, so the
gates are checked with no server.

Usage:

    python evals/bakeoff/bakeoff_ollama.py --self-test
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from typing import Any

sys.path.append(str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.append(str(pathlib.Path(__file__).resolve().parent))

import harness_stamp
from bakeoff_spec import (
    BUDGET_GB,
    EMBEDDING_MODEL,
    LICENSE_ALLOWLIST,
    MIN_NATIVE_CONTEXT,
    PRODUCTION_NUM_CTX,
)

GB = 1e9
"""Decimal gigabyte: the unit `ollama list` and `ollama ps` print."""

LOAD_TIMEOUT_S = 900.0
"""A cold load of a 24 GB model from disk can take minutes."""
_KEEP_ALIVE = "10m"

Opener = Callable[..., Any]


class OllamaLocal:
    """A thin client for the local Ollama's read endpoints and model loading."""

    def __init__(
        self,
        host: str | None = None,
        *,
        opener: Opener = urllib.request.urlopen,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        base = harness_stamp._loopback_url(harness_stamp.resolve_host(host))
        if base is None:
            raise ValueError(
                f"refusing a non-loopback Ollama host: {harness_stamp.resolve_host(host)!r}"
            )
        self._base = base
        self._opener = opener
        self._clock = clock

    def _call(
        self, path: str, payload: Mapping[str, Any] | None = None, timeout: float = 30.0
    ) -> Any:
        if payload is None:
            request: Any = f"{self._base}{path}"
        else:
            request = urllib.request.Request(  # noqa: S310 -- http(s) URL on a loopback host, enforced in __init__
                f"{self._base}{path}",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
        with self._opener(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def tags(self) -> list[dict[str, Any]]:
        return list(self._call("/api/tags").get("models", []))

    def show(self, model: str) -> dict[str, Any]:
        return dict(self._call("/api/show", {"model": model}))

    def ps(self) -> list[dict[str, Any]]:
        return list(self._call("/api/ps").get("models", []))

    def unload_all(self) -> int:
        """Unload everything resident; returns how many models were unloaded."""
        resident = self.ps()
        for entry in resident:
            self._call(
                "/api/generate",
                {"model": entry["name"], "keep_alive": 0},
                timeout=60.0,
            )
        return len(resident)

    def load_embedder(self) -> None:
        self._call(
            "/api/embed",
            {"model": EMBEDDING_MODEL, "input": "warm", "keep_alive": _KEEP_ALIVE},
            timeout=LOAD_TIMEOUT_S,
        )

    def load_chat(self, model: str, num_ctx: int) -> float:
        """Load `model` at `num_ctx` with an empty prompt; returns load seconds."""
        started = self._clock()
        self._call(
            "/api/generate",
            {
                "model": model,
                "prompt": "",
                "stream": False,
                "keep_alive": _KEEP_ALIVE,
                "options": {"num_ctx": num_ctx},
            },
            timeout=LOAD_TIMEOUT_S,
        )
        return self._clock() - started

    def measure_memory(self, model: str, num_ctx: int) -> dict[str, Any]:
        """Peak resident memory with `bge-m3` loaded and `model` loaded at
        `num_ctx`: every other model is unloaded first, so the sum is exactly
        the two. `total_bytes` sums `size` (all memory, GPU and CPU) across
        what `ollama ps` lists."""
        self.unload_all()
        self.load_embedder()
        load_s = self.load_chat(model, num_ctx)
        resident = self.ps()
        per_model = {
            str(e["name"]): {
                "size": int(e.get("size", 0)),
                "size_vram": int(e.get("size_vram", 0)),
            }
            for e in resident
        }
        return {
            "num_ctx": num_ctx,
            "load_s": round(load_s, 2),
            "total_bytes": sum(m["size"] for m in per_model.values()),
            "per_model": per_model,
        }


# --------------------------------------------------------------------------
# Pure gates.
# --------------------------------------------------------------------------


def classify_license(text: str | None) -> str:
    """`apache-2.0`, `mit`, `other`, or `unknown` (no text) from a model's
    license text. Anything that is neither is `other`: the allowlist is
    closed."""
    if not text or not text.strip():
        return "unknown"
    lowered = text.lower()
    if "apache license" in lowered and "version 2.0" in lowered:
        return "apache-2.0"
    if (
        "mit license" in lowered
        or "permission is hereby granted, free of charge" in lowered
    ):
        return "mit"
    return "other"


def native_context(show: Mapping[str, Any]) -> int | None:
    """The model's native context from `/api/show` `model_info`
    (`<arch>.context_length`), or `None` when it does not say."""
    values = [
        int(v)
        for k, v in (show.get("model_info") or {}).items()
        if str(k).endswith(".context_length") and isinstance(v, int | float)
    ]
    return max(values) if values else None


def listed_digest(tags: Sequence[Mapping[str, Any]], model: str) -> str | None:
    for entry in tags:
        names = (str(entry.get("name", "")), str(entry.get("model", "")))
        if model in names or f"{model}:latest" in names:
            digest = entry.get("digest")
            return str(digest) if digest else None
    return None


def decide_eligibility(
    *,
    pulled: bool,
    license_id: str | None,
    native_ctx: int | None,
    memory_bytes_at_production: int | None,
) -> dict[str, Any]:
    """`{"state": eligible|ineligible|pending, "reasons": [...]}`.

    `license_id` is `None` while the license has not been read (a model that is
    not pulled has no `/api/show`); `classify_license` yields `unknown` for a
    pulled model that carries no text, and that IS ineligible.

    `ineligible` is final (a license, context or memory failure -- the pre-reg
    drops it for every family). `pending` means a fact is missing (not pulled,
    memory not measured yet) and nothing is decided. Every failed gate is
    listed, not only the first: the report should say all of why."""
    reasons: list[str] = []
    if license_id is not None and license_id not in LICENSE_ALLOWLIST:
        reasons.append(
            f"license {license_id!r} is not in the allowlist {list(LICENSE_ALLOWLIST)}"
        )
    if native_ctx is not None and native_ctx < MIN_NATIVE_CONTEXT:
        reasons.append(
            f"native context {native_ctx} is below production num_ctx {MIN_NATIVE_CONTEXT}"
        )
    if (
        memory_bytes_at_production is not None
        and memory_bytes_at_production > BUDGET_GB * GB
    ):
        reasons.append(
            f"{memory_bytes_at_production / GB:.1f} GB resident at num_ctx "
            f"{PRODUCTION_NUM_CTX} with {EMBEDDING_MODEL} exceeds the "
            f"{BUDGET_GB:.0f} GB budget"
        )
    if reasons:
        return {"state": "ineligible", "reasons": reasons}
    pending: list[str] = []
    if not pulled:
        pending.append("not pulled (this driver never downloads a model)")
    if native_ctx is None and pulled:
        pending.append("native context not reported by /api/show")
    if memory_bytes_at_production is None:
        pending.append("memory at the production context not measured yet")
    if pending:
        return {"state": "pending", "reasons": pending}
    return {"state": "eligible", "reasons": []}


# --------------------------------------------------------------------------
# Self-test: a fake server, no network.
# --------------------------------------------------------------------------


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

    apache = "Apache License\nVersion 2.0, January 2004\n..."
    mit = "MIT License\n\nCopyright (c) Microsoft Corporation."
    check("apache is recognised", classify_license(apache), "apache-2.0")
    check("mit is recognised", classify_license(mit), "mit")
    check(
        "mit by its permission grant",
        classify_license("Permission is hereby granted, free of charge, to any person"),
        "mit",
    )
    check(
        "gemma terms are not on the allowlist",
        classify_license("Gemma Terms of Use"),
        "other",
    )
    check("no text is unknown, not allowed", classify_license("  "), "unknown")
    check("None is unknown", classify_license(None), "unknown")
    check(
        "apache without a version is not apache",
        classify_license("Apache License"),
        "other",
    )

    check(
        "native context from model_info",
        native_context(
            {
                "model_info": {
                    "general.architecture": "qwen3",
                    "qwen3.context_length": 40960,
                }
            }
        ),
        40960,
    )
    check("no context key is None", native_context({"model_info": {"a.b": 1}}), None)
    check("missing model_info is None", native_context({}), None)
    check(
        "digest by tag",
        listed_digest([{"name": "phi4:14b", "digest": "abc"}], "phi4:14b"),
        "abc",
    )
    check(
        "digest by :latest",
        listed_digest([{"name": "bge-m3:latest", "digest": "d"}], "bge-m3"),
        "d",
    )
    check("an unpulled model has no digest", listed_digest([], "x"), None)

    ok_bytes = int(20 * GB)
    over_bytes = int(24.1 * GB)
    check(
        "all gates pass",
        decide_eligibility(
            pulled=True,
            license_id="mit",
            native_ctx=16384,
            memory_bytes_at_production=ok_bytes,
        ),
        {"state": "eligible", "reasons": []},
    )
    check(
        "exactly the budget fits",
        decide_eligibility(
            pulled=True,
            license_id="mit",
            native_ctx=16384,
            memory_bytes_at_production=int(24 * GB),
        )["state"],
        "eligible",
    )
    over = decide_eligibility(
        pulled=True,
        license_id="apache-2.0",
        native_ctx=40960,
        memory_bytes_at_production=over_bytes,
    )
    check("over budget is ineligible", over["state"], "ineligible")
    check(
        "the memory reason names the budget", "24 GB budget" in over["reasons"][0], True
    )
    small_ctx = decide_eligibility(
        pulled=True,
        license_id="apache-2.0",
        native_ctx=8192,
        memory_bytes_at_production=ok_bytes,
    )
    check(
        "native context below 12288 is ineligible (gemma2)",
        small_ctx["state"],
        "ineligible",
    )
    exactly = decide_eligibility(
        pulled=True,
        license_id="mit",
        native_ctx=12288,
        memory_bytes_at_production=ok_bytes,
    )
    check("native context of exactly 12288 passes", exactly["state"], "eligible")
    gemma = decide_eligibility(
        pulled=True,
        license_id="other",
        native_ctx=8192,
        memory_bytes_at_production=over_bytes,
    )
    check("every failed gate is listed", len(gemma["reasons"]), 3)
    check(
        "a pulled model with no license text is ineligible",
        decide_eligibility(
            pulled=True,
            license_id="unknown",
            native_ctx=16384,
            memory_bytes_at_production=ok_bytes,
        )["state"],
        "ineligible",
    )
    check(
        "an unpulled model, license unread, is pending",
        decide_eligibility(
            pulled=False,
            license_id=None,
            native_ctx=None,
            memory_bytes_at_production=None,
        )["state"],
        "pending",
    )
    check(
        "unmeasured memory is pending",
        decide_eligibility(
            pulled=True,
            license_id="mit",
            native_ctx=16384,
            memory_bytes_at_production=None,
        )["state"],
        "pending",
    )

    # A fake server: records every call, answers /api/ps from what was loaded.
    calls: list[tuple[str, Any]] = []
    resident: dict[str, int] = {"old:1b": 1_000_000_000}

    def fake_open(request: Any, timeout: float) -> _FakeResponse:
        body: dict[str, Any] = {}
        if isinstance(request, str):
            path = request.split("11434", 1)[1]
        else:
            path = request.full_url.split("11434", 1)[1]
            body = json.loads(request.data)
        calls.append((path, body))
        if path == "/api/ps":
            return _FakeResponse(
                {
                    "models": [
                        {"name": n, "size": s, "size_vram": s // 2}
                        for n, s in resident.items()
                    ]
                }
            )
        if path == "/api/generate" and body.get("keep_alive") == 0:
            resident.pop(body["model"], None)
        elif path == "/api/generate":
            resident[body["model"]] = 8_000_000_000 + body["options"]["num_ctx"] * 1000
        elif path == "/api/embed":
            resident[EMBEDDING_MODEL] = 1_200_000_000
        elif path == "/api/tags":
            return _FakeResponse({"models": [{"name": "m:1b", "digest": "dd"}]})
        elif path == "/api/show":
            return _FakeResponse(
                {"license": mit, "model_info": {"x.context_length": 32768}}
            )
        return _FakeResponse({})

    client = OllamaLocal(
        "127.0.0.1:11434", opener=fake_open, clock=iter([0.0, 7.5]).__next__
    )
    check("tags are listed", client.tags(), [{"name": "m:1b", "digest": "dd"}])
    show = client.show("m:1b")
    check(
        "show feeds the pure gates",
        (classify_license(show["license"]), native_context(show)),
        ("mit", 32768),
    )
    calls.clear()
    measured = client.measure_memory("m:1b", 12288)
    check(
        "unloads the stranger first",
        calls[1],
        ("/api/generate", {"model": "old:1b", "keep_alive": 0}),
    )
    check(
        "loads the embedder, then the chat model at the requested context",
        [c[0] for c in calls if c[0] in ("/api/embed", "/api/generate")][-2:],
        ["/api/embed", "/api/generate"],
    )
    check("the chat load carries num_ctx", calls[-2][1]["options"], {"num_ctx": 12288})
    check(
        "memory sums exactly the embedder and the chat model",
        sorted(measured["per_model"]),
        [EMBEDDING_MODEL, "m:1b"],
    )
    check(
        "total is the sum of the two",
        measured["total_bytes"],
        1_200_000_000 + 8_000_000_000 + 12288 * 1000,
    )
    check("load seconds come from the injected clock", measured["load_s"], 7.5)
    check("the stranger really left", "old:1b" in measured["per_model"], False)

    try:
        OllamaLocal("http://example.com:11434", opener=fake_open)
        check("non-loopback host refused", True, False)
    except ValueError:
        check("non-loopback host refused", True, True)

    for name in failures:
        print(f"FAIL: {name}")
    print(f"self-test: {checks - len(failures)}/{checks} passed")
    return 1 if failures else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="check the eligibility gates against a fake server, with no network",
    )
    args = parser.parse_args(argv)
    if args.self_test:
        return _self_test()
    parser.error("nothing to run without --self-test; import this module instead")


if __name__ == "__main__":
    sys.exit(main())
