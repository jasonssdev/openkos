"""Where the gold documents come from: the draft bundle or a built workspace.

`Corpus` is the one seam the commands read documents through, so the harness
runs unchanged over the 35-document draft bundle (`DRAFT`, rebuilt from
committed material) or over a workspace built by `build_bundle.py`
(`from_workspace`, e.g. `bundle-v1`). A workspace is read, never written: its
`bundle/` is copied into a scratch directory before the derived stores are
rebuilt there.
"""

from __future__ import annotations

import pathlib
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

import gold_bundle as bundle_mod

from openkos.model import okf

WORKSPACE_BUNDLE_ID: Final = "retrieval-gold-v1"
_RESERVED: Final = frozenset({"index.md", "log.md"})


@dataclass(frozen=True)
class Corpus:
    bundle_id: str
    documents: Callable[[], list[bundle_mod.Doc]]
    materialize: Callable[[pathlib.Path], pathlib.Path]


DRAFT: Final = Corpus(
    bundle_mod.BUNDLE_ID, bundle_mod.documents, bundle_mod.write_bundle
)


def workspace_documents(workspace: pathlib.Path) -> list[bundle_mod.Doc]:
    """Every concept document of `workspace/bundle`, in path order.

    The reserved `index.md` and `log.md` and any dot-directory are not
    documents (they are never indexed)."""
    root = workspace / "bundle"
    if not root.is_dir():
        raise SystemExit(f"{workspace} has no bundle/ directory")
    docs: list[bundle_mod.Doc] = []
    for path in sorted(root.rglob("*.md")):
        rel = path.relative_to(root)
        if rel.name in _RESERVED or any(p.startswith(".") for p in rel.parts):
            continue
        meta, body = okf.load_frontmatter(path.read_text(encoding="utf-8"))
        doc_id = rel.with_suffix("").as_posix()
        docs.append(
            bundle_mod.Doc(
                doc_id,
                str(meta.get("title") or doc_id),
                str(meta.get("type") or ""),
                "",
                body.strip(),
            )
        )
    return docs


def from_workspace(workspace: pathlib.Path) -> Corpus:
    workspace = workspace.expanduser().resolve()

    def materialize(root: pathlib.Path) -> pathlib.Path:
        target = root / "bundle"
        shutil.copytree(
            workspace / "bundle",
            target,
            ignore=shutil.ignore_patterns(".state"),
        )
        return target

    return Corpus(
        WORKSPACE_BUNDLE_ID, lambda: workspace_documents(workspace), materialize
    )
