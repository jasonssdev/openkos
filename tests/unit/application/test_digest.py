"""The unattended 'what changed' digest: one line per automatic action, each
naming the commit and the command that undoes it (#1268)."""

from __future__ import annotations

from openkos.application import digest


def test_concept_ids_are_bundle_paths_minus_the_suffix_without_reserved_files() -> None:
    paths = [
        "raw/note.md",
        "bundle/sources/note.md",
        "bundle/topics/mqtt.md",
        "bundle/index.md",
        "bundle/log.md",
        "bundle/topics/index.md",
    ]
    assert digest.concept_ids(paths) == ("sources/note", "topics/mqtt")


def test_an_action_line_names_the_sha_the_concepts_and_the_undo_on_one_line() -> None:
    action = digest.record_action(
        "9c1d2e3",
        ["raw/a.md", "bundle/sources/a.md", "bundle/topics/mqtt.md", "bundle/log.md"],
        "openkos: ingest a.md (+1 concepts)",
    )

    line = digest.action_line(action)

    assert line == (
        "9c1d2e3 ingest a.md (+1 concepts) [sources/a, topics/mqtt] "
        "-- undo: git revert 9c1d2e3"
    )
    assert "\n" not in line


def test_a_long_concept_list_is_capped_but_the_sha_and_undo_stay() -> None:
    paths = [f"bundle/topics/c{i}.md" for i in range(9)]
    action = digest.record_action("abc1234", paths, "openkos: ingest big.md")

    line = digest.action_line(action)

    assert "c0" in line
    assert "+5 more" in line
    assert "c8" not in line
    assert line.startswith("abc1234 ")
    assert line.endswith("git revert abc1234")


def test_the_digest_lists_every_action_newest_first_with_an_ordering_note() -> None:
    older = digest.record_action("1111111", ["bundle/topics/a.md"], "openkos: one")
    newer = digest.record_action("2222222", ["bundle/topics/b.md"], "openkos: two")

    rendered = digest.render([older, newer])
    assert rendered is not None
    header, items, note = rendered

    assert header == "what changed -- 2 automatic commits, newest first"
    assert [i.split()[0] for i in items] == ["2222222", "1111111"]
    assert "newest first" in note
    assert "latest" in note


def test_an_empty_digest_renders_nothing() -> None:
    assert digest.render([]) is None


def test_a_single_action_is_singular() -> None:
    one = digest.record_action("1111111", ["bundle/topics/a.md"], "openkos: one")
    rendered = digest.render([one])
    assert rendered is not None
    assert rendered[0] == "what changed -- 1 automatic commit"
