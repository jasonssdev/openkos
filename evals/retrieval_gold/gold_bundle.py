"""The bundle the draft questions run over: committed material only.

Three sources, all already in the repository, so a fresh clone rebuilds the
same 35 documents byte for byte and nothing private is read:

- `evals/quiet_engine/corpus/sources/` -- 20 English notes of a fictional
  volunteer weather-station project (synthetic by its manifest). Indexed as
  `type: Source`, the way `ingest` stores a raw document.
- `evals/query_grounding/grounding_corpus.py` -- the Spanish meetings, concepts
  and decisions about a knowledge compiler (14 documents). Its `DOCS` mixes the
  three types a compiled bundle has, so the Source-share cap in
  `fusion.select_top` has something to act on.
- `examples/extraction-corpus/sources/medium-10-reunion-plataforma.md` -- one
  Spanish platform-review meeting, written for this repository.

The English twin of the Spanish corpus (`evals/query_attribution/`) is left
out on purpose: a translated twin gives every Spanish question a second,
equally relevant document in another language, and the judgment sheet would
have to rule on cross-language duplicates. That is a question for a later
gold set, not for the first one.

Frontmatter is rendered by the shipped `okf.dump_frontmatter`; `--self-test`
asks the shipped reader whether every document parses (#895).
"""

from __future__ import annotations

import pathlib
import re
from dataclasses import dataclass
from typing import Final

from grounding_corpus import DOCS as GROUNDING_DOCS

from openkos.model import okf

HERE: Final = pathlib.Path(__file__).resolve().parent
REPO_ROOT: Final = HERE.parents[1]
BUNDLE_ID: Final = "retrieval-gold-v0"

QUIET_ENGINE_SOURCES: Final = REPO_ROOT / "evals/quiet_engine/corpus/sources"
MEDIUM_10: Final = (
    REPO_ROOT / "examples/extraction-corpus/sources/medium-10-reunion-plataforma.md"
)

_TYPE_BY_FOLDER: Final = {
    "sources": "Source",
    "concepts": "Concept",
    "decisions": "Decision",
}


@dataclass(frozen=True)
class Doc:
    id: str
    title: str
    type: str
    language: str
    body: str


def _heading_title(text: str) -> str:
    match = re.search(r"^#\s+(.+?)\s*$", text, re.MULTILINE)
    if match is None:
        raise ValueError("document has no level-one heading to use as a title")
    return match.group(1)


def documents() -> list[Doc]:
    """Every document of the bundle, in a fixed order."""
    docs: list[Doc] = []
    for path in sorted(QUIET_ENGINE_SOURCES.glob("*.md")):
        text = path.read_text(encoding="utf-8").strip()
        docs.append(
            Doc(f"sources/{path.stem}", _heading_title(text), "Source", "en", text)
        )
    medium = MEDIUM_10.read_text(encoding="utf-8").strip()
    docs.append(
        Doc(
            "sources/medium-10-reunion-plataforma",
            _heading_title(medium),
            "Source",
            "es",
            medium,
        )
    )
    for doc_id, (title, body) in GROUNDING_DOCS.items():
        folder = doc_id.split("/", 1)[0]
        docs.append(Doc(doc_id, title, _TYPE_BY_FOLDER[folder], "es", body.strip()))
    return docs


def write_bundle(root: pathlib.Path) -> pathlib.Path:
    """Materialize the bundle under `root/bundle` as real OKF documents."""
    bundle = root / "bundle"
    for doc in documents():
        path = bundle / f"{doc.id}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        frontmatter = okf.dump_frontmatter(
            {"type": doc.type, "title": doc.title, "sensitivity": "private"}
        )
        path.write_text(f"{frontmatter}{doc.body}\n", encoding="utf-8")
    return bundle


def unparseable_documents() -> list[str]:
    """Ids of documents the shipped reader cannot parse (they would never be
    indexed, and `reindex` would only count them `skipped`)."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        bundle = write_bundle(pathlib.Path(tmp))
        return sorted(
            okf.concept_id_for(scan.path, bundle)
            for scan in okf._iter_docs(bundle)
            if scan.read_error is not None or scan.parse_error is not None
        )
