"""`OpenAICompatibleClient.chat` must stay safe to call from several threads
on one instance, mirroring `OllamaClient.chat`'s contract and
`test_ollama.py`'s AST guard (#748), copied here for the sibling client
(issue #1057 Phase 4, task 4.23).
"""

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_MODULE_PATH = _REPO_ROOT / "src" / "openkos" / "llm" / "openai_compatible.py"


def _self_mutation_offenders(source: str, *, method: str = "chat") -> list[int]:
    """Line numbers inside `method` that write to state reachable from
    `self` -- copied from `tests/unit/llm/test_ollama.py`'s equivalent
    (#748) for this sibling client. See that module for the full
    rationale: it asks the AST what a node IS (a `Store`/`Del` context on a
    `self`-rooted `Attribute`/`Subscript`, or a bare mutating call/
    `setattr`), never what form it was written in, so every assignment
    form the language has is covered with no form-by-form tail to
    maintain.
    """
    tree = ast.parse(source)
    owner = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef)
            and any(
                isinstance(item, ast.FunctionDef) and item.name == method
                for item in node.body
            )
        ),
        None,
    )
    assert owner is not None, f"no class defines a method named {method!r}"
    methods = {
        item.name: item for item in owner.body if isinstance(item, ast.FunctionDef)
    }

    def _is_self(node: ast.expr) -> bool:
        return isinstance(node, ast.Name) and node.id == "self"

    def _rooted_at_self(node: ast.expr) -> bool:
        while isinstance(node, ast.Attribute | ast.Subscript):
            node = node.value
        return _is_self(node)

    def _mutating_call(node: ast.stmt) -> bool:
        if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
            return False
        func = node.value.func
        if isinstance(func, ast.Name) and func.id == "setattr":
            return bool(node.value.args) and _is_self(node.value.args[0])
        return isinstance(func, ast.Attribute) and _rooted_at_self(func.value)

    def _self_methods_called(fn: ast.FunctionDef) -> set[str]:
        return {
            node.func.attr
            for node in ast.walk(fn)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and _is_self(node.func.value)
            and node.func.attr in methods
        }

    offenders: list[int] = []
    pending, seen = [method], {method}
    while pending:
        fn = methods[pending.pop()]
        for node in ast.walk(fn):
            if (isinstance(node, ast.stmt) and _mutating_call(node)) or (
                isinstance(node, ast.Attribute | ast.Subscript)
                and isinstance(node.ctx, ast.Store | ast.Del)
                and _rooted_at_self(node.value)
            ):
                offenders.append(node.lineno)
        for name in _self_methods_called(fn) - seen:
            seen.add(name)
            pending.append(name)
    return sorted(offenders)


def test_chat_writes_no_instance_attribute() -> None:
    """`OpenAICompatibleClient.chat` must remain free of writes to `self`,
    including through every same-class helper it transitively calls
    (`_build_request`, `_map_http_error`, `_unavailable`, `locality`,
    `resolved_base_url`) -- the property that would let a future
    `extraction.concept._fan_out_windows`-style concurrent fan-out call it
    safely against one shared client, exactly as `OllamaClient.chat`
    already supports (#744/#748).

    **RED today**: `openai_compatible.py` does not exist yet."""
    source = _MODULE_PATH.read_text(encoding="utf-8")

    assert _self_mutation_offenders(source) == []


def test_the_self_mutation_guard_catches_a_mutation() -> None:
    """Mutation-proof: a `self.<name> = ...` assignment inserted into
    `chat`'s body makes the guard fail. Proves the guard is not silently
    inert (mirrors `test_ollama.py`'s own form-catalog test, reduced to one
    representative form since the shared helper is already exhaustively
    proven there)."""

    def _chat_body(line: str) -> str:
        return f"class C:\n    def chat(self, messages):\n        {line}\n"

    assert _self_mutation_offenders(_chat_body("self._last = messages")) == [3]
    assert _self_mutation_offenders(_chat_body("url = self._model")) == []
