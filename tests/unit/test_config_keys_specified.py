"""Every `openkos.yaml` key the engine reads is named in a living spec.

`openkos.yaml` is a public interface, and `openspec/specs/{domain}/spec.md` is
the contract for public interfaces. Three keys shipped with behaviour in
`docs/cli.md` and a docstring in `config.py` but no requirement anywhere in the
specs. The accepted-key set is read from `config.read_config` itself, so a key
added there without a spec mention fails here instead of landing unspecified.
"""

import ast
import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CONFIG = _REPO_ROOT / "src" / "openkos" / "config.py"
_SPECS = _REPO_ROOT / "openspec" / "specs"


def _accepted_keys() -> set[str]:
    """String keys `read_config` pulls out of the parsed YAML mapping `raw`."""
    tree = ast.parse(_CONFIG.read_text(encoding="utf-8"))
    read_config = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "read_config"
    )
    keys: set[str] = set()
    for node in ast.walk(read_config):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "raw"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            keys.add(node.args[0].value)
    return keys


# Keys whose requirement is a delta spec of an in-flight change and is not yet
# merged into `openspec/specs/`. Each entry MUST name the change; the archive
# phase of that change merges the delta and REMOVES the entry (a stale entry
# is caught by `test_the_pending_allowlist_holds_only_unspecified_keys`).
_PENDING_ARCHIVE: dict[str, str] = {
    "attach_at_ingest": "attach-at-ingest",
}


def _spec_text() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(_SPECS.glob("*/spec.md"))
    )


def test_the_accepted_key_set_is_read_from_config_and_is_not_empty() -> None:
    keys = _accepted_keys()

    # Anchors so a refactor of `read_config` that hides the keys from this
    # scan fails here rather than turning the test below into a vacuous pass.
    assert {"model", "backend", "rationale_language", "volatility_windows"} <= keys
    assert len(keys) >= 20


def test_every_accepted_config_key_is_named_in_a_living_spec() -> None:
    specs = _spec_text()

    # A key is named when it appears as inline code, bare (`key`) or as a
    # config line (`key: value`, `key:`).
    unspecified = sorted(
        key
        for key in _accepted_keys() - _PENDING_ARCHIVE.keys()
        if re.search(rf"`{re.escape(key)}[`:]", specs) is None
    )

    assert unspecified == []


def test_the_pending_allowlist_holds_only_unspecified_keys() -> None:
    specs = _spec_text()

    stale = sorted(
        key
        for key in _PENDING_ARCHIVE
        if re.search(rf"`{re.escape(key)}[`:]", specs) is not None
    )

    # Once archive merges the delta, the entry has done its job: delete it.
    assert stale == []
    assert set(_PENDING_ARCHIVE) <= _accepted_keys()
