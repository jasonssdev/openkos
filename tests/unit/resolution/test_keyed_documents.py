"""The public eligibility walk and the base/`-N` identity rule that
attach-at-ingest reuses instead of re-deriving (#1268)."""

from pathlib import Path

import pytest

from openkos.resolution import candidates
from openkos.resolution.normalize import canonical_family_member, is_suffix_family


def _write(bundle: Path, concept_id: str, frontmatter: str) -> None:
    path = bundle / f"{concept_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{frontmatter}\n---\n\n# body\n", encoding="utf-8")


def test_keyed_documents_lists_id_type_and_normalized_key(tmp_path: Path) -> None:
    _write(tmp_path, "places/cafe-central", "type: Place\ntitle: Café Central")
    assert candidates.keyed_documents(tmp_path) == [
        ("places/cafe-central", "Place", "cafe central")
    ]


def test_keyed_documents_excludes_sources_blank_titles_and_unreadable(
    tmp_path: Path,
) -> None:
    _write(tmp_path, "sources/a", "type: Source\ntitle: A")
    _write(tmp_path, "concepts/blank", "type: Concept\ntitle: '  '")
    _write(tmp_path, "concepts/untyped", "title: X")
    (tmp_path / "concepts" / "broken.md").write_text(
        "---\n: : :\n---\n", encoding="utf-8"
    )
    _write(tmp_path, "concepts/ok", "type: Concept\ntitle: Ok")
    assert [doc[0] for doc in candidates.keyed_documents(tmp_path)] == ["concepts/ok"]


def test_keyed_documents_excludes_deprecated_unless_asked(tmp_path: Path) -> None:
    _write(tmp_path, "concepts/old", "type: Concept\ntitle: Old\nstatus: deprecated")
    _write(tmp_path, "concepts/new", "type: Concept\ntitle: New")
    assert [doc[0] for doc in candidates.keyed_documents(tmp_path)] == ["concepts/new"]
    both = candidates.keyed_documents(tmp_path, include_deprecated=True)
    assert sorted(doc[0] for doc in both) == ["concepts/new", "concepts/old"]


def test_is_suffix_family() -> None:
    assert is_suffix_family("concepts/skill", "concepts/skill-2")
    assert not is_suffix_family("concepts/skill", "concepts/skill-word")
    assert not is_suffix_family("concepts/skill", "places/skill-2")
    assert not is_suffix_family("concepts/skill-2", "concepts/skill")


@pytest.mark.parametrize(
    ("ids", "expected"),
    [
        (["concepts/skill-3", "concepts/skill", "concepts/skill-2"], "concepts/skill"),
        (["concepts/skill-3", "concepts/skill-2"], "concepts/skill-2"),
        (["concepts/skill-10", "concepts/skill-9"], "concepts/skill-9"),
        (["concepts/b", "concepts/a"], "concepts/a"),
        (["concepts/only"], "concepts/only"),
    ],
)
def test_canonical_family_member(ids: list[str], expected: str) -> None:
    assert canonical_family_member(ids) == expected


def test_canonical_family_member_refuses_an_empty_group() -> None:
    with pytest.raises(ValueError, match="at least one member"):
        canonical_family_member([])
