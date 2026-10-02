"""The one loader for the engine's LLM prompts (#1277, ADR-0043).

Each system prompt, and each fragment spliced into one, is a file under
`src/openkos/prompts/<task>/<name>.md`, read here through
`importlib.resources` the way `config` reads `templates/`. A prompt is
addressed by its id, `"<task>/<name>"`.

This module is a leaf: stdlib only, no import of any other `openkos`
module, the constraint `llm.base`, `llm.parsing` and `llm.prompting` hold,
so extraction, resolution and retrieval can all use it without a new
layering edge.

Bytes are exact. The file is read as bytes and decoded as UTF-8: no newline
is stripped or added and no line ending is translated, so the returned text
is the file's content and its hash is the file's hash. (`.gitattributes`
marks the folder `-text` so a checkout cannot rewrite line endings.)

A prompt that must embed a value owned by code carries a `{{name}}`
placeholder and is filled by keyword arguments. This is substitution only,
and it is strict: an unfilled placeholder or an unused argument raises.

The version of a prompt is its content hash (`prompt_hash`), derived and
never hand-bumped.
"""

import functools
import hashlib
import re
from importlib import resources

_ID_RE = re.compile(r"[a-z0-9_]+/[a-z0-9_]+")
_PLACEHOLDER_RE = re.compile(r"\{\{([a-z0-9_]+)\}\}")

PROMPTS_PACKAGE_DIR = "prompts"
PROMPT_SUFFIX = ".md"


@functools.cache
def _read(prompt_id: str) -> str:
    task, name = prompt_id.split("/")
    path = (
        resources.files("openkos")
        / PROMPTS_PACKAGE_DIR
        / task
        / f"{name}{PROMPT_SUFFIX}"
    )
    return path.read_bytes().decode("utf-8")


def load_prompt(prompt_id: str, **placeholders: str) -> str:
    """Return the prompt `prompt_id`, with each `{{name}}` filled from
    `placeholders`.

    Raises `ValueError` for a malformed id, a placeholder in the file that
    no argument fills, or an argument the file has no placeholder for;
    `FileNotFoundError` for an id with no file."""
    if not _ID_RE.fullmatch(prompt_id):
        raise ValueError(f"malformed prompt id {prompt_id!r}; expected '<task>/<name>'")
    text = _read(prompt_id)
    wanted = set(_PLACEHOLDER_RE.findall(text))
    given = set(placeholders)
    if wanted != given:
        raise ValueError(
            f"prompt {prompt_id!r}: placeholders {sorted(wanted)} "
            f"do not match arguments {sorted(given)}"
        )
    if not placeholders:
        return text
    return _PLACEHOLDER_RE.sub(lambda m: placeholders[m.group(1)], text)


def prompt_hash(text: str) -> str:
    """The derived version of a prompt text: `sha256(text)[:16]`."""
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def raw_prompt_hash(prompt_id: str) -> str:
    """The hash of the prompt FILE as stored, placeholders unfilled."""
    return prompt_hash(_read(prompt_id))
