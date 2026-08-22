"""The configured `chat_timeout` reaches every CHAT client (issue #405).

`config.Config.chat_timeout` is worthless if a call site forgets to pass it:
the client silently falls back to `OllamaClient`'s own default and the
workspace setting becomes decorative. These tests pin the wiring itself.

The source-inspection guard below is deliberate, and mirrors
`test_lint_command_spans.py`'s convention of pinning a cross-file coupling
from the side that would otherwise break silently. `main.py` and `curate.py`
each construct a chat client, and `curate.py` cannot import `main.py`
(`main.py` already imports `curate`, so the dependency only runs one way).
That leaves two construction sites which no type checker or ordinary unit
test forces to agree. A new chat verb added later would inherit the 120s-era
bug without a single failing assertion -- unless something reads the source
and insists.
"""

import ast
from pathlib import Path

from openkos import config
from openkos.cli import main as main_module

_SRC = Path(main_module.__file__).parent


def _is_chat_model_expr(value: ast.expr) -> bool:
    """True for an expression that resolves a CHAT model tag.

    Two accepted shapes, and the recursion that keeps them composable:

    - `cfg.model` / `ctx.cfg.model` -- an `ast.Attribute` named `model`.
      Keyed on the attribute name specifically: the embedding sites
      (`model=cfg.embedding_model`) and the liveness probes
      (`model=config.DEFAULT_MODEL`, or a resolved tag with
      `_PREFLIGHT_TIMEOUT`) are deliberately NOT governed by `chat_timeout`,
      and must not be flagged.
    - `config.resolve_task_model(cfg, task)` -- the per-task resolver (issue
      #515). This arm is not cosmetic: without it, a site migrated to a
      per-task model becomes an `ast.Call` that the original
      `ast.Attribute` test does not match, so it would vanish from `seen`
      and BOTH wiring guards below would silently stop protecting it while
      every assertion still passed.

    An `ast.IfExp` is walked through both branches, since a site may resolve
    a task model only when a task was named and otherwise fall back.
    """
    if isinstance(value, ast.IfExp):
        return _is_chat_model_expr(value.body) or _is_chat_model_expr(value.orelse)
    if isinstance(value, ast.Attribute):
        return value.attr == "model"
    if isinstance(value, ast.Call):
        func = value.func
        return isinstance(func, ast.Attribute) and func.attr == "resolve_task_model"
    return False


def _is_chat_model_arg(node: ast.keyword) -> bool:
    """True for a `model=` keyword whose value resolves a CHAT model tag."""
    return node.arg == "model" and _is_chat_model_expr(node.value)


def _chat_client_calls(tree: ast.AST) -> list[ast.Call]:
    """Every real `OllamaClient(model=<something>.model, ...)` CALL.

    Walks the AST rather than the raw text on purpose: several docstrings in
    `main.py` quote `OllamaClient(model=cfg.model)` while describing the
    injection seam, and a text scan flags those as offenders. Prose is not a
    construction site; only a `Call` node is.
    """
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "OllamaClient"
        and any(_is_chat_model_arg(kw) for kw in node.keywords)
    ]


def test_chat_client_applies_configured_timeout() -> None:
    """`_chat_client` hands the workspace's `chat_timeout` to the client."""
    cfg = _cfg(chat_timeout=42.5)

    client = main_module._chat_client(cfg)

    assert client._timeout == 42.5


def test_chat_client_uses_the_configured_model() -> None:
    """The helper is a wiring seam, not a model override: the tag still comes
    from config, so swapping call sites onto it cannot change which model
    runs."""
    cfg = _cfg(model="some-model:latest")

    assert main_module._chat_client(cfg)._model == "some-model:latest"


def test_every_chat_client_construction_passes_a_timeout() -> None:
    """No chat client is constructed on `OllamaClient`'s bare default.

    A site that omits `timeout=` compiles, type-checks, and runs -- it just
    silently ignores the workspace's `chat_timeout`. This is the assertion
    that turns that into a failing test.
    """
    offenders: list[str] = []
    seen = 0
    for path in sorted(_SRC.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for call in _chat_client_calls(tree):
            seen += 1
            if any(kw.arg == "timeout" for kw in call.keywords):
                continue
            offenders.append(f"{path.name}:{call.lineno}")

    # A guard that matches nothing passes vacuously; if the construction
    # shape is ever refactored past this detector, that must fail loudly
    # rather than quietly stop protecting anything.
    assert seen > 0, "no chat client constructions found -- the guard is blind"
    assert not offenders, (
        "chat client(s) constructed without an explicit timeout, so the "
        "workspace's `chat_timeout` is ignored there:\n  " + "\n  ".join(offenders)
    )


def test_chat_timeout_default_matches_the_transport_default() -> None:
    """`config.DEFAULT_CHAT_TIMEOUT` and `ollama.DEFAULT_TIMEOUT` agree.

    They are separate constants on purpose -- one is the workspace-facing
    setting, the other the floor every non-config caller inherits -- but a
    workspace that omits the key must behave identically to a caller that
    never passes one. If they drift, the same call gets two different
    deadlines depending on which path built the client.
    """
    from openkos.llm import ollama

    assert config.DEFAULT_CHAT_TIMEOUT == ollama.DEFAULT_TIMEOUT


# --- max_generation_tokens: the same wiring pinned for the generation
# ceiling (#422), mirroring every guard above for `chat_timeout` -----------


def test_chat_client_applies_configured_max_generation_tokens() -> None:
    """`_chat_client` hands the workspace's `max_generation_tokens` to the client."""
    cfg = _cfg(max_generation_tokens=2048)

    client = main_module._chat_client(cfg)

    assert client._max_generation_tokens == 2048


def test_every_chat_client_construction_passes_max_generation_tokens() -> None:
    """No chat client is constructed without the configured generation ceiling.

    Mirrors `test_every_chat_client_construction_passes_a_timeout`: a site
    that omits `max_generation_tokens=` compiles, type-checks, and runs -- it
    just silently ignores the workspace's ceiling, reintroducing the
    unbounded-generation defect #422 exists to close.
    """
    offenders: list[str] = []
    seen = 0
    for path in sorted(_SRC.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for call in _chat_client_calls(tree):
            seen += 1
            if any(kw.arg == "max_generation_tokens" for kw in call.keywords):
                continue
            offenders.append(f"{path.name}:{call.lineno}")

    assert seen > 0, "no chat client constructions found -- the guard is blind"
    assert not offenders, (
        "chat client(s) constructed without an explicit "
        "max_generation_tokens, so the workspace's generation ceiling is "
        "ignored there:\n  " + "\n  ".join(offenders)
    )


# --- context_window: the same wiring pinned for the context window (#691) ---


def test_chat_client_applies_configured_context_window() -> None:
    """`_chat_client` hands the workspace's `context_window` to the client."""
    cfg = _cfg(context_window=16384)

    client = main_module._chat_client(cfg)

    assert client._context_window == 16384


def test_chat_client_forwards_an_opted_out_context_window() -> None:
    """`context_window: null` reaches the client as `None`, so the opt-out is
    a real opt-out rather than a value the wiring quietly replaces."""
    cfg = _cfg(context_window=None)

    assert main_module._chat_client(cfg)._context_window is None


def test_every_chat_client_construction_passes_a_context_window() -> None:
    """No chat client is constructed without the configured window.

    A site that omits `context_window=` compiles, type-checks, and runs -- it
    just keeps reserving the model's own 32K default and its ~10 GB
    footprint, which is the whole defect #691 exists to close, and it would
    do so on only SOME verbs, which is worse than doing it on all of them.
    """
    offenders: list[str] = []
    seen = 0
    for path in sorted(_SRC.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for call in _chat_client_calls(tree):
            seen += 1
            if any(kw.arg == "context_window" for kw in call.keywords):
                continue
            offenders.append(f"{path.name}:{call.lineno}")

    assert seen > 0, "no chat client constructions found -- the guard is blind"
    assert not offenders, (
        "chat client(s) constructed without an explicit context_window, so "
        "the workspace's window is ignored there and the model reserves its "
        "own default:\n  " + "\n  ".join(offenders)
    )


# --- #515: the same seam now resolves a PER-TASK model ---------------------


def _cfg(**overrides: object) -> config.Config:
    """A packaged-default `Config` with `models` overridable per test."""
    base: dict[str, object] = {
        "model": "qwen3:8b",
        "review": True,
        "default_sensitivity": "private",
        "freshness_window": "7d",
        "embedding_model": "bge-m3",
        "chat_timeout": config.DEFAULT_CHAT_TIMEOUT,
        "max_generation_tokens": config.DEFAULT_MAX_GENERATION_TOKENS,
        "context_window": config.DEFAULT_CONTEXT_WINDOW,
        "confidential_local_exemption": True,
        "volatility_windows": {},
        "type_tiers": {},
        "models": {},
        "union_judge": config.DEFAULT_UNION_JUDGE,
        "sufficiency_check": config.DEFAULT_SUFFICIENCY_CHECK,
        "concurrent_extraction": config.DEFAULT_CONCURRENT_EXTRACTION,
        "type_sensitivity_defaults": dict(config.DEFAULT_TYPE_SENSITIVITY_DEFAULTS),
        "rationale_language": config.DEFAULT_RATIONALE_LANGUAGE,
    }
    base.update(overrides)
    return config.Config(**base)  # type: ignore[arg-type]


def test_chat_client_resolves_the_named_task_model() -> None:
    """`_chat_client(cfg, task=...)` builds on the task's own model (#515).

    This is the seam the whole issue turns on: edge typing's measured +0.37
    is collected here, at client construction, without any other verb's
    model moving.
    """
    cfg = _cfg(models={"edge_typing": "gemma2:27b"})

    assert main_module._chat_client(cfg, task="edge_typing")._model == "gemma2:27b"


def test_chat_client_without_a_task_keeps_the_global_model() -> None:
    """A caller that names NO task keeps `cfg.model`, byte-for-byte the
    pre-#515 behavior. `query` and the locality probe rely on this: neither
    is one of the five measured tasks, and neither should silently inherit a
    model chosen for edge typing."""
    cfg = _cfg(models={"edge_typing": "gemma2:27b"})

    assert main_module._chat_client(cfg)._model == "qwen3:8b"


def test_chat_client_falls_back_for_an_unkeyed_task() -> None:
    """A named task with no override resolves to the global model, so a
    workspace that keys one task does not move the other four."""
    cfg = _cfg(models={"edge_typing": "gemma2:27b"})

    assert main_module._chat_client(cfg, task="extraction")._model == "qwen3:8b"


def test_chat_client_keeps_timeout_and_ceiling_on_a_per_task_model() -> None:
    """A per-task model is still governed by the workspace's `chat_timeout`
    and `max_generation_tokens`. #515 moves WHICH model runs, never the two
    safety rails around it -- and a 27b model is exactly the case where a
    dropped deadline would hurt most."""
    cfg = _cfg(
        models={"edge_typing": "gemma2:27b"},
        chat_timeout=42.5,
        max_generation_tokens=2048,
    )

    client = main_module._chat_client(cfg, task="edge_typing")

    assert (client._timeout, client._max_generation_tokens) == (42.5, 2048)


def test_the_chat_client_ast_guard_still_sees_every_construction() -> None:
    """The two wiring guards above must not go blind when a site migrates to
    a per-task model.

    `_is_chat_model_arg` recognizes a chat client by SYNTAX --
    `model=cfg.model`, an `ast.Attribute`. A site rewritten as
    `model=config.resolve_task_model(cfg, ...)` is an `ast.Call`, which the
    original detector does not match: the site would vanish from `seen`,
    both `timeout` and `max_generation_tokens` guards would silently stop
    protecting it, and every assertion would still pass. This test pins the
    detector's own coverage so that failure mode cannot happen quietly.
    """
    seen = 0
    for path in sorted(_SRC.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        seen += len(_chat_client_calls(tree))

    # `main.py`'s `_chat_client` seam plus `curate.py`'s own construction
    # (which cannot import `main.py`) -- the two sites this module exists to
    # keep in agreement.
    assert seen >= 2, (
        "the chat-client detector matches fewer sites than the two known "
        "construction seams -- it has gone blind to a construction shape"
    )


_EXPECTED_VERB_TASKS = {
    "_ingest_single": "extraction",
    "adjudicate": "adjudication",
    "suggest_relations_cmd": "edge_typing",
    "suggest_volatility_cmd": "volatility_typing",
    "contradictions": "contradiction",
}
"""Which `main.py` function must resolve which task's model (#515).

Keyed by TASK, so `suggest_relations_cmd` and `curate`'s Structure stage
land on the same key and cannot drift onto different models -- the property
#515 decision 1 chose this schema shape to protect.

`query` and `curate` are deliberately absent: `query` synthesizes an answer,
which is not one of `TASK_MODEL_KEYS` and has no harness, and `curate`'s
only direct `_chat_client` call builds the locality probe, whose answer is a
property of the host rather than of any task."""


def _chat_client_tasks_by_function(tree: ast.AST) -> dict[str, set[str | None]]:
    """Map each enclosing function name to the `task=` values it passes to
    `_chat_client` -- `None` for a call that names no task at all."""
    found: dict[str, set[str | None]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for call in ast.walk(node):
            if not isinstance(call, ast.Call):
                continue
            if not (isinstance(call.func, ast.Name) and call.func.id == "_chat_client"):
                continue
            task: str | None = None
            for kw in call.keywords:
                if kw.arg == "task" and isinstance(kw.value, ast.Constant):
                    task = str(kw.value.value)
            found.setdefault(node.name, set()).add(task)
    return found


def test_every_llm_verb_resolves_its_own_task_model() -> None:
    """Each measured verb passes its task to `_chat_client`.

    A verb that forgets `task=` compiles, type-checks, and runs -- it just
    silently ignores the workspace's `models:` override and keeps the global
    default, which is the exact silent-wrong-model failure #515 decision 2
    refuses. Nothing but reading the source forces these five to agree with
    the config schema they are supposed to honor.
    """
    tree = ast.parse((_SRC / "main.py").read_text(encoding="utf-8"))
    by_function = _chat_client_tasks_by_function(tree)

    missing = sorted(set(_EXPECTED_VERB_TASKS) - set(by_function))
    assert not missing, (
        "these functions no longer call `_chat_client` -- the guard has gone "
        f"stale and protects nothing for them: {missing}"
    )
    wrong = {
        name: sorted(str(t) for t in by_function[name])
        for name, expected in _EXPECTED_VERB_TASKS.items()
        if by_function[name] != {expected}
    }
    assert not wrong, (
        "these verbs do not resolve the task model they are measured on, so "
        f"a `models:` override would be silently ignored there: {wrong}"
    )


def test_query_and_the_locality_probe_stay_on_the_global_model() -> None:
    """`query` and `curate`'s locality probe name NO task, on purpose.

    Pinned rather than left implicit: adding `task=` to either would be a
    quiet policy change. `query` has no harness, so #508's rule forbids
    picking a model for it; the locality probe asks about the HOST, and a
    per-task locality is not a thing that exists.
    """
    tree = ast.parse((_SRC / "main.py").read_text(encoding="utf-8"))
    by_function = _chat_client_tasks_by_function(tree)

    assert by_function.get("query") == {None}
    assert by_function.get("curate") == {None}
