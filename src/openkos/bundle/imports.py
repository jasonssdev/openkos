"""Where an imported bundle lives in the workspace (okf-import, design D2).

A leaf of the canonical layer: it knows the NAMES the import layout is made of
(the `imports/` directory, the namespace rule, the anchor ids, the staging
prefix, the imported-concept predicate) and nothing about how a document is
adopted or written. Every adopted Concept ID is `imports/<namespace>/<foreign
Concept ID>`; every anchor Source is `imports/<namespace>--<label>`.

The namespace is an ASCII slug with single hyphens, so it has no NFC/NFD or
case variants, and it cannot contain `--`, which keeps `<ns>--<label>`
collision-free against every other namespace's directory and anchors.
"""

import re
from typing import Final

IMPORTS_DIR: Final = "imports"
"""The directory under `bundle/` that holds every import."""

NAMESPACE_MAX_LENGTH: Final = 64

NAMESPACE_RE: Final = re.compile(r"\A[a-z0-9]+(?:-[a-z0-9]+)*\Z")
"""One slug segment: lowercase ASCII letters and digits joined by single
hyphens, never starting or ending with one."""


def namespace_reason(namespace: str) -> str | None:
    """Why `namespace` is not a valid import namespace, or `None` when it is.

    The reason names the rule that failed, so a usage error can say what to
    change rather than only that something is wrong."""
    if not namespace:
        return "the namespace must not be empty"
    if len(namespace) > NAMESPACE_MAX_LENGTH:
        return (
            f"the namespace must be at most {NAMESPACE_MAX_LENGTH} characters, "
            f"got {len(namespace)}"
        )
    if NAMESPACE_RE.match(namespace):
        return None
    if namespace != namespace.lower():
        return "the namespace must be lowercase"
    if "--" in namespace:
        return "the namespace must not contain `--` (it separates an anchor's label)"
    if namespace.startswith("-") or namespace.endswith("-"):
        return "the namespace must not start or end with a hyphen"
    return (
        "the namespace must be one segment of lowercase ASCII letters, digits "
        "and single hyphens"
    )


def namespace_prefix(namespace: str) -> str:
    """The Concept ID prefix every document of `namespace` carries."""
    return f"{IMPORTS_DIR}/{namespace}"


def anchor_id(namespace: str, label: str) -> str:
    """The Concept ID of the anchor Source for `label` in `namespace`."""
    return f"{IMPORTS_DIR}/{namespace}--{label}"


def staging_name_prefix(namespace: str) -> str:
    """The name prefix of a staging directory beside the namespace's final
    directory. It starts with a dot, so every bundle walk ignores it."""
    return f".{namespace}.openkos-import-"


def is_imported_concept(concept_id: str) -> bool:
    """Whether `concept_id` lives under `imports/`: the FIRST segment is
    exactly `imports`, so `concepts/imports/x`, `imports-x/y` and `import/x`
    are not imported."""
    return concept_id.startswith(f"{IMPORTS_DIR}/")
