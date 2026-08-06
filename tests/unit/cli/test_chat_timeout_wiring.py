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


def _is_chat_model_arg(node: ast.keyword) -> bool:
    """True for `model=cfg.model` / `model=ctx.cfg.model` -- a CHAT client.

    Keyed on the attribute name specifically: the embedding sites
    (`model=cfg.embedding_model`) and the liveness probes
    (`model=config.DEFAULT_MODEL`, or a resolved tag with
    `_PREFLIGHT_TIMEOUT`) are deliberately NOT governed by `chat_timeout`,
    and must not be flagged.
    """
    return (
        node.arg == "model"
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "model"
    )


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
    cfg = config.Config(
        model="qwen3:8b",
        review=True,
        default_sensitivity="private",
        freshness_window="7d",
        embedding_model="bge-m3",
        chat_timeout=42.5,
        max_generation_tokens=config.DEFAULT_MAX_GENERATION_TOKENS,
        confidential_local_exemption=True,
        volatility_windows={},
        type_tiers={},
    )

    client = main_module._chat_client(cfg)

    assert client._timeout == 42.5


def test_chat_client_uses_the_configured_model() -> None:
    """The helper is a wiring seam, not a model override: the tag still comes
    from config, so swapping call sites onto it cannot change which model
    runs."""
    cfg = config.Config(
        model="some-model:latest",
        review=True,
        default_sensitivity="private",
        freshness_window="7d",
        embedding_model="bge-m3",
        chat_timeout=config.DEFAULT_CHAT_TIMEOUT,
        max_generation_tokens=config.DEFAULT_MAX_GENERATION_TOKENS,
        confidential_local_exemption=True,
        volatility_windows={},
        type_tiers={},
    )

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
    cfg = config.Config(
        model="qwen3:8b",
        review=True,
        default_sensitivity="private",
        freshness_window="7d",
        embedding_model="bge-m3",
        chat_timeout=config.DEFAULT_CHAT_TIMEOUT,
        max_generation_tokens=2048,
        confidential_local_exemption=True,
        volatility_windows={},
        type_tiers={},
    )

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
