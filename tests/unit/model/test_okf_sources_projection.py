"""Unit tests for `model/okf.py`'s `sources` projection (okf-v02-migration,
issue #1064, Phase 3): `project_sources`/`refresh_sources`, plus the
bundle-wide parity check used against a real ingest -> merge run.

Ingestion's ADDED requirement ("`sources` Is A Generated, One-Way Projection
Of `provenance`", design.md Decision 3) makes `project_sources` a pure
function of the `provenance` value alone -- see that function's docstring
for the exact per-entry table this file pins."""

import json
from collections.abc import Sequence
from pathlib import Path

from openkos import config
from openkos.application import ingest as ingest_service
from openkos.bundle import merge as bundle_merge
from openkos.llm.base import Message
from openkos.model import okf
from tests.unit.conftest import LOCAL_BACKEND_LOCALITY

# -- U1: `project_sources` (tasks 3.1-3.5) -----------------------------------


def test_project_sources_normalizes_concept_id_entries() -> None:
    """Every Concept-ID shape (bare, leading `/`, trailing `.md`, both)
    projects to the SAME normalized `{id, resource}` entry (design.md
    Decision 3's normalization table)."""
    for entry in (
        "sources/foo",
        "/sources/foo",
        "sources/foo.md",
        "/sources/foo.md",
    ):
        assert okf.project_sources([entry]) == [
            {"id": "sources/foo", "resource": "/sources/foo.md"}
        ]


def test_project_sources_skips_raw_entries_and_dedupes_and_orders() -> None:
    """A `raw/<name>` workspace path is never projected; a repeated
    Concept-ID entry keeps only its FIRST occurrence, in `provenance`
    order."""
    result = okf.project_sources(
        [
            "raw/call-with-maria.txt",
            "sources/call-with-maria",
            "sources/other-source",
            "sources/call-with-maria",
        ]
    )

    assert result == [
        {"id": "sources/call-with-maria", "resource": "/sources/call-with-maria.md"},
        {"id": "sources/other-source", "resource": "/sources/other-source.md"},
    ]


def test_project_sources_none_cases() -> None:
    """The whole projection is `None` when `provenance` is absent, a
    non-list value, a list of only non-string/empty/`raw/`-prefixed
    entries, or an empty list -- never an empty `[]` (design.md Decision
    3: a `None` projection means the builder writes no `sources` key at
    all, distinct from an explicit empty list)."""
    assert okf.project_sources(None) is None
    assert okf.project_sources("sources/foo") is None
    assert okf.project_sources([""]) is None
    assert okf.project_sources([42]) is None
    assert okf.project_sources([]) is None
    assert okf.project_sources(["raw/call-with-maria.txt"]) is None
    assert okf.project_sources(["raw/a.txt", "raw/b.txt"]) is None


def test_project_sources_key_order_is_id_then_resource() -> None:
    """Every projected entry's own key insertion order is `id`, then
    `resource` -- the OKF §5.1 example's own order."""
    result = okf.project_sources(["sources/foo"])

    assert result is not None
    assert list(result[0].keys()) == ["id", "resource"]


# -- U2: `refresh_sources` (tasks 3.6-3.7) -----------------------------------


def test_refresh_sources_maintenance_rules() -> None:
    """`sources` present + non-`None` projection -> replaced in place at
    its existing key position; present + `None` projection -> key removed;
    absent -> metadata returned unchanged, no key inserted (design.md
    Decision 3's maintenance rule)."""
    replaced = okf.refresh_sources(
        {
            "type": "Concept",
            "provenance": ["sources/new-source"],
            "sources": [
                {"id": "sources/old-source", "resource": "/sources/old-source.md"}
            ],
        }
    )
    assert replaced["sources"] == [
        {"id": "sources/new-source", "resource": "/sources/new-source.md"}
    ]
    assert list(replaced.keys()) == ["type", "provenance", "sources"]

    removed = okf.refresh_sources(
        {
            "type": "Concept",
            "provenance": ["raw/only-source.txt"],
            "sources": [{"id": "sources/stale", "resource": "/sources/stale.md"}],
        }
    )
    assert "sources" not in removed

    unchanged = okf.refresh_sources({"type": "Concept", "provenance": ["sources/x"]})
    assert "sources" not in unchanged
    assert unchanged == {"type": "Concept", "provenance": ["sources/x"]}


# -- Parity helper + bundle-wide e2e (task 3.16) -----------------------------


def assert_sources_parity(bundle_dir: Path) -> None:
    """For every non-reserved document under `bundle_dir` that carries a
    `sources` key, assert it equals `project_sources` of that SAME
    document's own `provenance` (design.md Testing Strategy: "Parity").

    A document with no `sources` key is skipped -- absence is a valid,
    unchecked state (a Source, or any document `project_sources` maps to
    `None`), not a parity violation."""
    checked = 0
    for path in okf.iter_bundle_markdown(bundle_dir):
        if path.name in okf.RESERVED_FILENAMES:
            continue
        metadata, _ = okf.load_frontmatter(path.read_text(encoding="utf-8"))
        if okf.SOURCES_KEY not in metadata:
            continue
        checked += 1
        assert metadata[okf.SOURCES_KEY] == okf.project_sources(
            metadata.get("provenance")
        ), f"{path}: sources does not match project_sources(provenance)"
    assert checked > 0, (
        "assert_sources_parity found no document carrying a `sources` key -- "
        "the precondition for this check is vacuous"
    )


class _FakeLLM:
    """A structural `LLMBackend` -- mirrors `tests/unit/application/
    test_ingest.py`'s own `_FakeLLM`: a fixed reply, zero network."""

    locality = LOCAL_BACKEND_LOCALITY

    def __init__(self, reply: str) -> None:
        self.reply = reply

    def chat(self, messages: Sequence[Message]) -> str:
        return self.reply

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [[0.0] * 8 for _ in texts]


def _default_cfg(**overrides: object) -> config.Config:
    """Mirrors `tests/unit/application/test_ingest.py::_default_cfg`."""
    fields: dict[str, object] = {
        "model": "qwen3:8b",
        "review": True,
        "default_sensitivity": "private",
        "freshness_window": "7d",
        "embedding_model": "bge-m3",
        "chat_timeout": config.DEFAULT_CHAT_TIMEOUT,
        "max_generation_tokens": config.DEFAULT_MAX_GENERATION_TOKENS,
        "context_window": config.DEFAULT_CONTEXT_WINDOW,
        "temperature": config.DEFAULT_TEMPERATURE,
        "seed": config.DEFAULT_SEED,
        "confidential_local_exemption": config.DEFAULT_CONFIDENTIAL_LOCAL_EXEMPTION,
        "volatility_windows": {},
        "type_tiers": {},
        "models": {},
        "union_judge": config.DEFAULT_UNION_JUDGE,
        "sufficiency_check": config.DEFAULT_SUFFICIENCY_CHECK,
        "concurrent_extraction": config.DEFAULT_CONCURRENT_EXTRACTION,
        "type_sensitivity_defaults": {},
        "rationale_language": config.DEFAULT_RATIONALE_LANGUAGE,
    }
    fields.update(overrides)
    return config.Config(**fields)  # type: ignore[arg-type]


def _concept_reply(title: str) -> str:
    return json.dumps(
        {
            "extract": True,
            "type": "Concept",
            "title": title,
            "description": f"Description of {title}.",
            "body": f"Body elaborating on {title}.",
        }
    )


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_sources_parity_after_ingest_and_merge(tmp_path: Path) -> None:
    """A real ingest (`application.ingest.compose_source_document` +
    `stage_derived_objects`, an offline `_FakeLLM`) followed by a real
    merge (`bundle.merge.plan_merge`) -- both via the application layer,
    no CLI subprocess and no Ollama -- produces documents whose `sources`
    parity holds bundle-wide (design.md Testing Strategy: "a bundle-wide
    sweep after an ingest -> merge e2e asserts it for every document that
    has `sources`").

    Two DISTINCT sources feed the survivor and the absorbed concept, so the
    merged survivor's unioned `provenance` genuinely differs from either
    side's own -- a fixture citing only one shared source would make a
    "unioned vs. survivor-only" call-site bug invisible here (confirmed by
    mutation: see below).

    **MUTATION**: dropping `project_sources`'s `.md` normalization makes
    the direct unit tests fail (`test_project_sources_normalizes_concept_
    id_entries`); temporarily computing this function's merged `sources`
    from `survivor_metadata.get("provenance")` instead of the unioned
    `merged.get("provenance")` makes THIS test fail -- both confirmed
    during implementation, then reverted (apply-progress.md records both).
    Swapping `project_sources`'s `id`/`resource` values does NOT fail this
    parity check: both sides of the assertion call the SAME (mutated)
    `project_sources`, so they still agree with each other -- a genuine,
    recorded finding about this check's blind spot; only the direct
    literal-comparison unit tests catch that mutation."""
    bundle_dir = tmp_path / "bundle"
    cfg = _default_cfg()
    timestamp = "2026-07-14T18:30:00Z"

    source_plan = ingest_service.compose_source_document(
        raw_content="Notes about the dichotomy of control.",
        source_stem="notes",
        source_display_path="notes.txt",
        source_document_display_path="sources/notes.md",
        resource="raw/notes.txt",
        origin_key=None,
        concept_text=None,
        cfg=cfg,
        timestamp=timestamp,
    )
    _write(bundle_dir / "sources" / "notes.md", source_plan.content)

    second_source_plan = ingest_service.compose_source_document(
        raw_content="Second notes about Stoic self-control.",
        source_stem="second-notes",
        source_display_path="second-notes.txt",
        source_document_display_path="sources/second-notes.md",
        resource="raw/second-notes.txt",
        origin_key=None,
        concept_text=None,
        cfg=cfg,
        timestamp=timestamp,
    )
    _write(bundle_dir / "sources" / "second-notes.md", second_source_plan.content)

    staged = ingest_service.stage_derived_objects(
        raw_content=source_plan.raw_content,
        source_title=source_plan.title,
        source_slug="notes",
        workspace_floor="private",
        stamp_sensitivity=source_plan.source_sensitivity,
        timestamp=timestamp,
        bundle_dir=bundle_dir,
        llm=_FakeLLM(_concept_reply("Dichotomy Of Control")),
        cfg=cfg,
    )
    assert staged.plans, "fixture setup: extraction must stage exactly one concept"
    survivor_plan = staged.plans[0]
    _write(survivor_plan.path, survivor_plan.content)

    # A second derived concept, from the SECOND source, to merge into the first.
    staged_2 = ingest_service.stage_derived_objects(
        raw_content=second_source_plan.raw_content,
        source_title=second_source_plan.title,
        source_slug="second-notes",
        workspace_floor="private",
        stamp_sensitivity=second_source_plan.source_sensitivity,
        timestamp=timestamp,
        bundle_dir=bundle_dir,
        llm=_FakeLLM(_concept_reply("Stoic Self Control")),
        cfg=cfg,
    )
    assert staged_2.plans, "fixture setup: extraction must stage a second concept"
    absorbed_plan = staged_2.plans[0]
    _write(absorbed_plan.path, absorbed_plan.content)

    index_text = okf.dump_frontmatter(
        {"okf_version": okf.OKF_VERSION},
        "# Concepts\n\n"
        f"* [{absorbed_plan.title}](/concepts/{absorbed_plan.slug}.md) - "
        f"{absorbed_plan.description}\n",
    )
    plan = bundle_merge.plan_merge(
        survivor_id=f"concepts/{survivor_plan.slug}",
        absorbed_id=f"concepts/{absorbed_plan.slug}",
        survivor_text=survivor_plan.content,
        absorbed_text=absorbed_plan.content,
        index_text=index_text,
        merged_at=timestamp,
    )
    _write(bundle_dir / "concepts" / f"{survivor_plan.slug}.md", plan.merged_survivor)
    (bundle_dir / "concepts" / f"{absorbed_plan.slug}.md").unlink()

    assert_sources_parity(bundle_dir)
