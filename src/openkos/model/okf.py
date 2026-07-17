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
