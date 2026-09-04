"""`examples/good-life-demo/` must teach the product that actually ships (#920).

The canonical example is the first real bundle most readers see, so a stale one
miscalibrates everyone who opens it -- and it drifts silently, because nothing
imports it and no test read it before this module. The 2026-09-03 truth sweep
found exactly that:

* `openkos.yaml` predated at least five config keys (`embedding_model`,
  `confidential_local_exemption`, `chat_timeout`, `max_generation_tokens`,
  `context_window`) and declared `raw:`/`bundle:` layout keys the engine does
  not read at all -- so the reference config taught two things that do nothing.
* Four concepts carried `provenance:` entries naming RAW FILE PATHS
  (`raw/notes-on-the-enchiridion-2026-07-05.txt`). The engine writes concept
  ids (`cli/main.py`: `provenance_key = f"sources/{source_slug}"`), and its own
  `lint`/`status` reported all four as dangling provenance against the shipped
  bundle.

This is the same failure shipped templates have had before: a default taught for
a whole release because no test reads a file that only humans read.
"""

from pathlib import Path

import pytest

from openkos import config
from openkos.model import okf

_EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "good-life-demo"


def test_the_example_exists() -> None:
    """Guards every test below from passing vacuously if the example moves."""
    assert (_EXAMPLE / "bundle" / "index.md").is_file()


def test_config_is_byte_identical_to_a_fresh_init(tmp_path: Path) -> None:
    """The example's `openkos.yaml` must be what `openkos init` writes today.

    `write_config` substitutes only `model` and `embedding_model`; every other
    line is byte-identical to the packaged template regardless of the choice.
    The example uses both defaults, so a fresh write is the exact expected file
    and any difference is drift -- including drift that lives purely in comment
    prose, which is where a wrong default hid for a whole release last time.
    """
    config.write_config(tmp_path)

    assert (_EXAMPLE / "openkos.yaml").read_text(encoding="utf-8") == (
        tmp_path / "openkos.yaml"
    ).read_text(encoding="utf-8")


def test_agents_md_is_byte_identical_to_a_fresh_init(tmp_path: Path) -> None:
    """`AGENTS.md` is the operating manual a reader is told to follow; an old
    copy in the reference example teaches superseded instructions."""
    config.write_agents(tmp_path)

    assert (_EXAMPLE / "AGENTS.md").read_text(encoding="utf-8") == (
        tmp_path / "AGENTS.md"
    ).read_text(encoding="utf-8")


def _concept_docs() -> list[Path]:
    return [
        path
        for path in sorted((_EXAMPLE / "bundle").rglob("*.md"))
        if path.name not in okf.RESERVED_FILENAMES
    ]


def _provenance_of(doc: Path) -> list[str]:
    """The document's `provenance` list as strings.

    `load_frontmatter` returns a plain `dict[str, object]` (the engine keeps
    frontmatter untyped on purpose -- `model/okf.py`: "no pydantic"), so the
    value needs narrowing before it can be iterated under strict mypy.
    """
    metadata, _ = okf.load_frontmatter(doc.read_text(encoding="utf-8"))
    raw = metadata.get("provenance")
    if not isinstance(raw, list):
        return []
    return [str(entry) for entry in raw]


def test_there_are_concept_documents_to_check() -> None:
    """Without this, the provenance test below would pass over an empty list."""
    assert len(_concept_docs()) >= 6


@pytest.mark.parametrize("doc", _concept_docs(), ids=lambda p: p.stem)
def test_provenance_names_concept_ids_not_raw_paths(doc: Path) -> None:
    """`provenance` points at Source CONCEPTS, never at files under `raw/`.

    `raw/` is outside the bundle by design, so a provenance entry naming one is
    unresolvable: `lint` and `status` both report it as dangling. The single
    allowed bridge out of the bundle is a Source concept's `resource:` field,
    which is checked separately below.
    """
    entries = _provenance_of(doc)

    offending = [entry for entry in entries if entry.startswith("raw/")]

    assert offending == [], (
        f"{doc.name}: provenance must name Source concept ids "
        f"(e.g. 'sources/<slug>'), not raw file paths"
    )


@pytest.mark.parametrize("doc", _concept_docs(), ids=lambda p: p.stem)
def test_every_provenance_entry_resolves_to_a_real_document(doc: Path) -> None:
    """The positive half. Without it, the test above would pass against a
    provenance list that was simply emptied instead of corrected."""
    bundle_dir = _EXAMPLE / "bundle"

    missing = [
        entry
        for entry in _provenance_of(doc)
        if not (bundle_dir / f"{entry}.md").is_file()
    ]

    assert missing == []


def test_source_concepts_are_the_only_bridge_to_raw() -> None:
    """A Source's `resource:` may name a file under `raw/` -- that is the one
    documented bridge out of the bundle. Nothing else may."""
    reaching_out = {}
    for doc in _concept_docs():
        metadata, _ = okf.load_frontmatter(doc.read_text(encoding="utf-8"))
        resource = str(metadata.get("resource") or "")
        if resource.startswith("raw/") and metadata.get("type") != "Source":
            reaching_out[doc.name] = resource

    assert reaching_out == {}
