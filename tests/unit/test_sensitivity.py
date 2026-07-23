"""Unit tests for `sensitivity.py`: the shared fail-closed sensitivity
predicate (sensitivity-fail-closed-filter, MVP-3 gap #8 · S3, Phase 1/PR1).

`sensitive_concept_ids` is the ONE shared predicate every `llm.chat`-calling
seam (query, contradictions, adjudicate, suggest-relations, suggest-
volatility) filters against before sending concept content to the LLM -- see
`openspec/changes/sensitivity-fail-closed-filter/design.md`. Fixture helper
mirrors `tests/unit/test_lifecycle.py::_write_doc`.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest

from openkos import sensitivity
from openkos.model import okf


def _write_doc(
    path: Path,
    *,
    sensitivity_value: str | None = None,
    sensitivity_raw: str | None = None,
    body: str = "",
) -> None:
    """Write a minimal concept `.md` file with an optional `sensitivity:`
    frontmatter field. `sensitivity_raw` overrides it with a hand-written
    frontmatter line (for malformed-shape cases); passing neither omits the
    key entirely (absent case)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---", "type: Concept", "title: Stub"]
    if sensitivity_raw is not None:
        lines.append(sensitivity_raw)
    elif sensitivity_value is not None:
        lines.append(f"sensitivity: {sensitivity_value}")
    lines.append("---")
    frontmatter = "\n".join(lines) + "\n"
    path.write_text(f"{frontmatter}{body}", encoding="utf-8")


def test_explicit_confidential_is_blocked(tmp_path: Path) -> None:
    """A concept with `sensitivity: confidential` resolves to blocked (spec:
    Explicit confidential is blocked)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "secret.md", sensitivity_value="confidential")

    blocked = sensitivity.sensitive_concept_ids(bundle_dir)

    assert blocked == frozenset({"concepts/secret"})


def test_missing_sensitivity_key_is_blocked(tmp_path: Path) -> None:
    """A concept with no `sensitivity` frontmatter field fails closed to
    blocked -- `okf._rank(None)` alone would return private, so this MUST
    NOT be delegated to `_rank` (spec: Missing, malformed, or unreadable
    fails closed)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "no-field.md")

    blocked = sensitivity.sensitive_concept_ids(bundle_dir)

    assert blocked == frozenset({"concepts/no-field"})


def test_blank_sensitivity_value_is_blocked(tmp_path: Path) -> None:
    """A blank/whitespace-only `sensitivity` value fails closed to blocked --
    `okf._rank("")` alone would return private, so this MUST NOT be delegated
    to `_rank` either (spec: Missing, malformed, or unreadable fails
    closed)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "blank.md", sensitivity_raw='sensitivity: "   "'
    )

    blocked = sensitivity.sensitive_concept_ids(bundle_dir)

    assert blocked == frozenset({"concepts/blank"})


def test_malformed_frontmatter_is_blocked(tmp_path: Path) -> None:
    """A document whose frontmatter fails to parse fails closed to blocked
    (spec: Missing, malformed, or unreadable fails closed)."""
    bundle_dir = tmp_path / "bundle"
    path = bundle_dir / "concepts" / "malformed.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("---\ntitle: [unterminated\n---\nbody\n", encoding="utf-8")

    blocked = sensitivity.sensitive_concept_ids(bundle_dir)

    assert blocked == frozenset({"concepts/malformed"})


def test_unreadable_file_is_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A document that raises on read (simulating an unreadable file) fails
    closed to blocked (spec: Missing, malformed, or unreadable fails
    closed)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "unreadable.md", sensitivity_value="private")

    original_iter_docs = okf._iter_docs

    def _raising_iter_docs(target_bundle_dir: Path) -> Iterator[okf.DocScan]:
        for scan in original_iter_docs(target_bundle_dir):
            yield okf.DocScan(
                path=scan.path,
                metadata=None,
                read_error=OSError("simulated unreadable file"),
                parse_error=None,
            )

    monkeypatch.setattr(okf, "_iter_docs", _raising_iter_docs)

    blocked = sensitivity.sensitive_concept_ids(bundle_dir)

    assert blocked == frozenset({"concepts/unreadable"})


def test_unknown_sensitivity_value_is_blocked(tmp_path: Path) -> None:
    """A concept with `sensitivity: top-secret` (not one of the three known
    ranks) fails closed to blocked (spec: Unknown sensitivity value fails
    closed)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "weird.md", sensitivity_value="top-secret")

    blocked = sensitivity.sensitive_concept_ids(bundle_dir)

    assert blocked == frozenset({"concepts/weird"})


def test_private_sensitivity_is_sent(tmp_path: Path) -> None:
    """A concept with `sensitivity: private` is NOT in the blocked set (spec:
    Private and public concepts reach llm.chat)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "priv.md", sensitivity_value="private")

    blocked = sensitivity.sensitive_concept_ids(bundle_dir)

    assert blocked == frozenset()


def test_public_sensitivity_is_sent(tmp_path: Path) -> None:
    """A concept with `sensitivity: public` is NOT in the blocked set (spec:
    Private and public concepts reach llm.chat)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "pub.md", sensitivity_value="public")

    blocked = sensitivity.sensitive_concept_ids(bundle_dir)

    assert blocked == frozenset()


def test_mixed_bundle_blocks_only_confidential_and_fallbacks(tmp_path: Path) -> None:
    """Triangulation: a bundle mixing all sensitivity states blocks exactly
    the confidential/missing/blank/unknown ones, leaving private/public
    live."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "a.md", sensitivity_value="confidential")
    _write_doc(bundle_dir / "concepts" / "b.md", sensitivity_value="private")
    _write_doc(bundle_dir / "concepts" / "c.md", sensitivity_value="public")
    _write_doc(bundle_dir / "concepts" / "d.md")  # absent
    _write_doc(bundle_dir / "concepts" / "e.md", sensitivity_value="top-secret")

    blocked = sensitivity.sensitive_concept_ids(bundle_dir)

    assert blocked == frozenset({"concepts/a", "concepts/d", "concepts/e"})


def test_all_public_private_bundle_returns_empty_frozenset(tmp_path: Path) -> None:
    """A bundle with no confidential/missing/malformed concept returns an
    empty set."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "a.md", sensitivity_value="private")
    _write_doc(bundle_dir / "concepts" / "b.md", sensitivity_value="public")

    blocked = sensitivity.sensitive_concept_ids(bundle_dir)

    assert blocked == frozenset()
