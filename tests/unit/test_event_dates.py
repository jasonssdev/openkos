"""Unit tests for `event_dates.py`: the bounded event-date resolver
(superseded-history-in-query, slice 1, design.md Decision 1).

`resolve_event_date` is a package-root leaf with no consumer in this
slice -- `resolution/decision_revision.py`'s `DateState` alias
(`test_decision_revision.py`) and `retrieval/history.py` (slice 2b) are
wired in later. These tests exercise the resolver directly against a
hand-written bundle of Source/intermediate `.md` files.
"""

from collections.abc import Mapping
from datetime import date
from pathlib import Path

import pytest

from openkos import event_dates
from openkos.model import okf


def _write_doc(
    path: Path,
    *,
    event_date: str | None = None,
    provenance: list[str] | None = None,
    body: str = "",
) -> None:
    """Write a minimal concept `.md` file with an optional `event_date:`
    and/or `provenance:` frontmatter field."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---", "type: Concept", "title: Stub"]
    if event_date is not None:
        lines.append(f"event_date: {event_date}")
    if provenance is not None:
        lines.append("provenance:")
        for entry in provenance:
            lines.append(f"  - {entry}")
    lines.append("---")
    text = "\n".join(lines) + "\n" + body
    path.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# 1.1 -- all four DateState values
# ---------------------------------------------------------------------------


def test_resolve_event_date_dated(tmp_path: Path) -> None:
    """A single admitted Source with one event date resolves `"dated"`."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "sources" / "s1.md", event_date="2026-07-14")
    metadata: dict[str, object] = {"provenance": ["sources/s1"]}

    result = event_dates.resolve_event_date(bundle_dir, metadata)

    assert result.state == "dated"
    assert result.earliest == date(2026, 7, 14)
    assert result.latest == date(2026, 7, 14)


@pytest.mark.parametrize(
    "case",
    ["absent", "malformed", "unreadable", "refused"],
)
def test_resolve_event_date_missing(tmp_path: Path, case: str) -> None:
    """An admitted Source that is absent, malformed, unreadable, or
    refused by `admit` all resolve `"missing"` (design.md Decision 1 step
    4). Kills swapping the missing/multiple check order and dropping the
    `admit` parameter (a refused value would otherwise count)."""
    bundle_dir = tmp_path / "bundle"
    metadata: dict[str, object] = {"provenance": ["sources/s1"]}
    admit = None

    if case == "absent":
        _write_doc(bundle_dir / "sources" / "s1.md")
    elif case == "malformed":
        _write_doc(bundle_dir / "sources" / "s1.md", event_date="not-a-date")
    elif case == "unreadable":
        pass  # the file is never written -- the guarded read fails
    elif case == "refused":
        _write_doc(bundle_dir / "sources" / "s1.md", event_date="2026-07-14")

        def _refuse(concept_id: str, meta: Mapping[str, object]) -> bool:
            return False

        admit = _refuse
    else:  # pragma: no cover -- exhaustive parametrize
        raise AssertionError(case)

    result = event_dates.resolve_event_date(bundle_dir, metadata, admit=admit)

    assert result.state == "missing"
    assert result.earliest is None
    assert result.latest is None


def test_resolve_event_date_multiple(tmp_path: Path) -> None:
    """More than one distinct date across reached Sources resolves
    `"multiple"`, with `earliest`/`latest` set."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "sources" / "s1.md", event_date="2026-01-01")
    _write_doc(bundle_dir / "sources" / "s2.md", event_date="2026-03-01")
    metadata: dict[str, object] = {"provenance": ["sources/s1", "sources/s2"]}

    result = event_dates.resolve_event_date(bundle_dir, metadata)

    assert result.state == "multiple"
    assert result.earliest == date(2026, 1, 1)
    assert result.latest == date(2026, 3, 1)


@pytest.mark.parametrize(
    "case",
    ["empty_provenance", "intermediate_only_no_provenance"],
)
def test_resolve_event_date_none_reached(tmp_path: Path, case: str) -> None:
    """Empty `provenance:`, or `provenance:` naming only non-Source
    intermediates whose own `provenance:` is empty, resolves
    `"none-reached"`."""
    bundle_dir = tmp_path / "bundle"

    if case == "empty_provenance":
        metadata: dict[str, object] = {"provenance": []}
    elif case == "intermediate_only_no_provenance":
        _write_doc(bundle_dir / "concepts" / "mid.md")
        metadata = {"provenance": ["concepts/mid"]}
    else:  # pragma: no cover -- exhaustive parametrize
        raise AssertionError(case)

    result = event_dates.resolve_event_date(bundle_dir, metadata)

    assert result.state == "none-reached"
    assert result.earliest is None
    assert result.latest is None


def test_missing_precedes_multiple_in_aggregation(tmp_path: Path) -> None:
    """When one reached Source is unusable while the others contribute
    multiple distinct dates, `"missing"` wins over `"multiple"` (design.md
    Decision 1 step 5's checked order: none-reached -> any-missing ->
    multiple -> dated). Kills swapping the missing/multiple check order."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "sources" / "s1.md", event_date="2026-01-01")
    _write_doc(bundle_dir / "sources" / "s2.md", event_date="2026-03-01")
    metadata: dict[str, object] = {
        "provenance": ["sources/s1", "sources/s2", "sources/does-not-exist"]
    }

    result = event_dates.resolve_event_date(bundle_dir, metadata)

    assert result.state == "missing"
    assert result.earliest is None
    assert result.latest is None


# ---------------------------------------------------------------------------
# 1.2 -- one-hop resolution bound
# ---------------------------------------------------------------------------


def test_one_hop_resolution_bound(tmp_path: Path) -> None:
    """An intermediate's own `sources/` entries count toward resolution; a
    Source reachable only through a second intermediate (two hops from the
    member) does not. Kills recursing past hop 2: if the far Source's very
    different date counted, the result would be `"multiple"` instead of
    `"dated"` with only the near Source's date."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "mid1.md",
        provenance=["sources/s-near", "concepts/mid2"],
    )
    _write_doc(bundle_dir / "concepts" / "mid2.md", provenance=["sources/s-far"])
    _write_doc(bundle_dir / "sources" / "s-near.md", event_date="2026-01-01")
    _write_doc(bundle_dir / "sources" / "s-far.md", event_date="2026-09-01")
    metadata: dict[str, object] = {"provenance": ["concepts/mid1"]}

    result = event_dates.resolve_event_date(bundle_dir, metadata)

    assert result.state == "dated"
    assert result.earliest == date(2026, 1, 1)
    assert result.latest == date(2026, 1, 1)


# ---------------------------------------------------------------------------
# 1.3 -- a refused intermediate is not traversed
# ---------------------------------------------------------------------------


def test_refused_intermediate_is_not_traversed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An intermediate entry that `admit` refuses contributes nothing, and
    its own `provenance:` is never read: the deep Source it names is never
    opened at all (asserted via a `Path.read_text` call-count spy). Kills
    traversing into a refused intermediate's `sources/` entries."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "mid.md", provenance=["sources/deep"])
    _write_doc(bundle_dir / "sources" / "deep.md", event_date="2026-07-14")
    metadata: dict[str, object] = {"provenance": ["concepts/mid"]}

    read_calls: list[Path] = []
    original_read_text = Path.read_text

    def _spy_read_text(
        self: Path, encoding: str | None = None, errors: str | None = None
    ) -> str:
        read_calls.append(self)
        return original_read_text(self, encoding=encoding, errors=errors)

    monkeypatch.setattr(Path, "read_text", _spy_read_text)

    def _refuse_intermediate(concept_id: str, meta: Mapping[str, object]) -> bool:
        return concept_id != "concepts/mid"

    result = event_dates.resolve_event_date(
        bundle_dir, metadata, admit=_refuse_intermediate
    )

    assert result.state == "none-reached"
    deep_path = okf.concept_path_for("sources/deep", bundle_dir)
    assert deep_path not in read_calls


# ---------------------------------------------------------------------------
# 1.5 -- the leaf's imports are bounded (standing regression guard)
# ---------------------------------------------------------------------------


def test_leaf_imports_are_bounded() -> None:
    """`event_dates.py` imports only the standard library or
    `openkos.model.okf`/`openkos.model.types` (design.md Decision 1's leaf
    discipline, matching `lifecycle.py`). A standing regression guard
    against a future disallowed import (e.g. `sensitivity`, `resolution`)."""
    import ast
    import sys

    repo_root = Path(__file__).resolve().parents[2]
    module_path = repo_root / "src" / "openkos" / "event_dates.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"))

    targets: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            targets.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                targets.add(f"{node.module}.{alias.name}")

    allowed_openkos = {"openkos.model.okf", "openkos.model.types"}
    for target in targets:
        if target in allowed_openkos:
            continue
        top_level = target.split(".", 1)[0]
        assert top_level != "openkos", f"event_dates.py imports {target!r}"
        assert top_level in sys.stdlib_module_names, (
            f"event_dates.py imports non-stdlib target {target!r}"
        )
