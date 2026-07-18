"""OKF (Open Knowledge Format) adapter.

The one seam that knows the on-disk shape of OKF v0.1: frontmatter framing,
reserved filenames, and the conformance rules of §9. Nothing outside this
module parses or emits frontmatter, or reasons about reserved files
(AGENTS.md:41, docs/architecture.md:113).

Rule 3 of §9 (reserved-file structure) is not implemented here; it needs
concept-doc structure the model layer does not have, and is deferred to
`lint` (docs/okf-alignment.md).
"""

from pathlib import Path
from typing import Final

import frontmatter

OKF_VERSION: Final = "0.1"
"""The OKF version this engine targets and declares, per §11."""

RESERVED_FILENAMES: Final[frozenset[str]] = frozenset({"index.md", "log.md"})
"""§6/§7 give these a fixed structure; §9 rule 1 exempts them from frontmatter."""


def dump_frontmatter(metadata: dict[str, object], body: str = "") -> str:
    """Render `metadata` as a YAML frontmatter block over `body`, per §4.1."""
    post = frontmatter.Post(body)
    post.metadata = metadata
    return frontmatter.dumps(post) + "\n"


def load_frontmatter(text: str) -> tuple[dict[str, object], str]:
    """Parse the frontmatter block and body out of `text`, per §4.1."""
    post = frontmatter.loads(text)
    return post.metadata, post.content


def build_source_concept(
    *,
    title: str,
    description: str,
    resource: str,
    tags: list[str],
    timestamp: str,
    sensitivity: str,
    provenance: list[str],
) -> str:
    """Build a conformant OKF Source concept document (D4).

    Plain dict -> `dump_frontmatter`, no pydantic: every field is
    engine-derived from trusted local inputs (workspace config, the source's
    filename, an injected clock, the raw path) rather than untrusted
    structured LLM output, so `check_conformance` (§9 rules 1-2: parseable
    frontmatter, non-empty `type`) is the only gate this slice needs.
    `description` is passed through verbatim -- callers MUST phrase it as an
    honest null-compiler description (imported, not yet compiled/extracted),
    never claiming extraction occurred, matching this slice's scope.
    """
    metadata: dict[str, object] = {
        "type": "Source",
        "title": title,
        "description": description,
        "resource": resource,
        "tags": tags,
        "timestamp": timestamp,
        "status": "active",
        "version": 1,
        "freshness": "snapshot",
        "sensitivity": sensitivity,
        "provenance": provenance,
    }
    body = f"# {title}\n\n{description}\n\n# Citations\n"
    return dump_frontmatter(metadata, body)


def check_conformance(bundle_dir: Path) -> list[str]:
    """Check §9 rules 1-2 against every non-reserved `.md` file under `bundle_dir`.

    An empty list means conformant; a fresh, empty bundle passes vacuously
    because there are no non-reserved `.md` files to violate either rule.
    May raise `OSError` or `UnicodeDecodeError` when a candidate file cannot
    be read or decoded -- those are inspection failures, never reported as
    conformance violations.
    """
    violations: list[str] = []
    for path in sorted(bundle_dir.rglob("*.md")):
        if path.name in RESERVED_FILENAMES:
            continue
        text = path.read_text(encoding="utf-8")
        try:
            post = frontmatter.loads(text)
        except Exception as exc:  # broad: any parse failure is a rule-1 violation
            violations.append(f"{path}: no parseable frontmatter ({exc})")
            continue
        if post.handler is None:
            violations.append(f"{path}: no parseable frontmatter")
        elif not post.metadata.get("type"):
            violations.append(f"{path}: missing non-empty 'type'")
    return violations
