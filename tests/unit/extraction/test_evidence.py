"""Unit tests for `extraction/evidence.py`: the quoted-evidence line a
derived object's written text carries, or `None` when it carries none
(issue #801).

Pure string functions -- no `LLMBackend` fake is needed here, and none is
built: the module is a leaf that never imports `concept.py` and never sees
an `ExtractionResult`, so a test that had to construct one would be
testing the wrong seam.
"""

from openkos.extraction import evidence as evidence_mod


def test_evidence_line_finds_a_line_quoted_from_the_source() -> None:
    """The healthy case, and the invariant #801 found already holding for
    seven of eight objects in one real run: the object's body carries a
    line the source actually writes, so the stored object can support a
    citation back to it."""
    source = (
        "# Platform sync\n"
        "- Priya owns the schema migration plan.\n"
        "- The team picked MySQL as the primary datastore.\n"
    )

    line = evidence_mod.evidence_line("Priya owns the schema migration plan.", source)

    assert line == "Priya owns the schema migration plan."


def test_evidence_line_returns_none_for_a_pure_paraphrase() -> None:
    """#801's defect, reduced to its seam. The written text restates the
    subject in the model's OWN words -- every content word is a synonym or
    a nominalization of the source's -- so no line of it appears in the
    source, and the object records that a decision exists while dropping
    the fact worth storing."""
    source = "- Priya owns the schema migration plan.\n"

    assert (
        evidence_mod.evidence_line(
            "The decision regarding who owns the schema migration plan "
            "for Project Helios.",
            source,
        )
        is None
    )


def test_evidence_line_floor_rejects_a_three_word_line_present_verbatim() -> None:
    """The floor is what keeps this check from being VACUOUS. A one- or
    two-word line (`Priya Nair`, `MySQL 8`) appears verbatim in almost any
    source that mentions the subject at all, so without a floor nearly
    every object would pass and the check would prove nothing.

    Asserted on a line that IS present verbatim, deliberately: a test that
    rejected an ABSENT short line could not tell the floor from the
    substring test, and would stay green with the floor removed."""
    source = "Priya Nair leads platform. Priya owns the schema migration plan.\n"

    assert evidence_mod.evidence_line("Priya Nair leads", source) is None


def test_evidence_line_accepts_at_exactly_the_floor() -> None:
    """Four words is admitted, not rejected -- the floor is a minimum, and
    an off-by-one here would silently raise it to five and start returning
    `None` for objects that do quote their source."""
    source = "Priya owns the schema migration plan.\n"

    assert evidence_mod.evidence_line("Priya owns the schema", source) == (
        "Priya owns the schema"
    )


def test_evidence_line_strips_a_markdown_list_marker() -> None:
    """A model writes its body as a bullet list far more often than as
    prose, and the source's own line may or may not carry the marker. The
    marker is layout, never content, so comparing it would make a quoted
    line look invented."""
    source = "Priya owns the schema migration plan.\n"

    for bullet in ("- ", "* ", "+ ", "1. "):
        assert (
            evidence_mod.evidence_line(
                f"{bullet}Priya owns the schema migration plan.", source
            )
            == "Priya owns the schema migration plan."
        )


def test_evidence_line_strips_heading_and_blockquote_markers() -> None:
    """Same reasoning as the list marker, for the two other structures a
    generated body routinely opens a line with."""
    source = "Priya owns the schema migration plan.\n"

    assert (
        evidence_mod.evidence_line("## Priya owns the schema migration plan.", source)
        == "Priya owns the schema migration plan."
    )
    assert (
        evidence_mod.evidence_line("> Priya owns the schema migration plan.", source)
        == "Priya owns the schema migration plan."
    )


def test_evidence_line_accepts_a_body_line_that_extends_a_source_line() -> None:
    """The reverse direction, and the false positive that made it mandatory.

    A model that quotes the source and carries the sentence further
    reproduces every word of the source line, yet the body line is not a
    SUBSTRING of the source, so a one-directional test scores a correct
    extraction as quoting nothing. Worse, that verdict is unclearable: the
    token is deliberately excluded from `main._extraction_retry_due`, so a
    plain re-ingest of an unchanged source skips extraction and the marker
    never recomputes -- the Source would sit under `openkos status`'s
    needs-attention forever."""
    source = "- Priya owns the schema migration plan.\n"

    line = evidence_mod.evidence_line(
        "Priya owns the schema migration plan for Project Helios.", source
    )

    assert line == "Priya owns the schema migration plan."


def test_evidence_line_returns_the_source_line_the_body_extended() -> None:
    """When the reverse arm matches, the SOURCE line is the evidence.

    It is the text actually quoted, and it is what a reader following this
    notice would search the source for -- handing back the body's longer
    sentence would send them looking for a string the source does not
    contain, which is the opposite of what an advisory is for."""
    source = "Notes.\n- Priya owns the schema migration plan.\nMore notes.\n"

    line = evidence_mod.evidence_line(
        "In practice Priya owns the schema migration plan, at least for now.",
        source,
    )

    assert line == "Priya owns the schema migration plan."


def test_reverse_arm_does_not_rescue_a_genuine_paraphrase() -> None:
    """The symmetric arm must widen what counts as a quote, never dissolve
    the check. #801's real object restates its description in the model's
    own words, so NEITHER direction matches, and it is still flagged."""
    source = "- Priya owns the schema migration plan.\n"

    assert (
        evidence_mod.evidence_line(
            "The decision regarding who owns the schema migration plan "
            "for Project Helios.",
            source,
        )
        is None
    )


def test_reverse_arm_applies_the_word_floor_to_the_source_line() -> None:
    """The floor is load-bearing on BOTH arms, for one reason.

    Without it, any short phrase the source happens to contain -- and a
    long body line will contain many -- admits the object, which is the
    vacuous-guard failure the floor exists to prevent, arriving from the
    other direction. Asserted on a source line that IS reproduced inside
    the body, so the test cannot pass by accident of absence."""
    source = "Notes about the plan.\nPriya owns it\nMore notes about the plan.\n"
    body = "Later that week Priya owns it and everything else that follows."

    assert evidence_mod.evidence_line(body, source) is None


def test_longest_wins_across_both_directions() -> None:
    """The longest-wins rule spans the two arms combined, not each one
    separately: the evidence reported is the most substantive quote found,
    whichever direction found it."""
    source = (
        "Priya owns the schema migration plan for the whole of this quarter.\n"
        "Dan runs the rollout.\n"
    )
    body = (
        "Dan runs the rollout.\n"
        "As agreed, Priya owns the schema migration plan for the whole of "
        "this quarter, barring surprises.\n"
    )

    assert evidence_mod.evidence_line(body, source) == (
        "Priya owns the schema migration plan for the whole of this quarter."
    )


def test_evidence_line_returns_the_longest_qualifying_line() -> None:
    """Among several quoted lines the MOST substantive one is the evidence
    worth reporting. Returning an incidental short line instead would make
    a well-grounded object look barely grounded to whoever reads the
    notice."""
    source = (
        "The team met on Tuesday.\n"
        "Priya owns the schema migration plan through the end of the quarter.\n"
    )
    text = (
        "The team met on Tuesday.\n"
        "Priya owns the schema migration plan through the end of the quarter.\n"
    )

    assert evidence_mod.evidence_line(text, source) == (
        "Priya owns the schema migration plan through the end of the quarter."
    )


def test_evidence_line_prefers_the_first_of_two_equally_long_lines() -> None:
    """Ties resolve in document order, so the return value is deterministic
    -- a notice that named a different line on each run over the same bytes
    would read as nondeterminism in extraction itself."""
    source = "Priya owns the migration plan. Dan owns the rollout plan.\n"
    text = "Priya owns the migration plan.\nDan owns the rollout plan.\n"

    assert evidence_mod.evidence_line(text, source) == (
        "Priya owns the migration plan."
    )


def test_evidence_line_is_casefolded_and_whitespace_collapsed() -> None:
    """Byte-for-byte the normalization `concept._quoted_verbatim` applies.
    A body that re-cased a heading, or collapsed a source's double space,
    is still quoting it."""
    source = "PRIYA   OWNS the schema\tmigration plan.\n"

    assert (
        evidence_mod.evidence_line("priya owns the schema migration plan.", source)
        == "priya owns the schema migration plan."
    )


def test_evidence_line_skips_blank_lines() -> None:
    """A blank line normalizes to the empty string, which is a substring of
    every source. Admitting it would make every multi-line body 'quoted'
    and the check permanently green.

    What actually rejects it is the LONGEST-WINS TIE-BREAK, not the word
    floor. A zero-word line fails `words > best_words` on the very first
    comparison, since `best_words` starts at 0. `_MIN_EVIDENCE_WORDS`
    filters it too, one step earlier in `_qualifying_lines` -- but drop
    that floor to 0 and this test stays GREEN while the two tests that name
    the floor go red. Measured, not assumed, and the earlier version of
    this docstring asserted the opposite.

    So read this test as guarding the tie-break's SECOND job. The floor is
    proven by the two tests that exist for it."""
    assert evidence_mod.evidence_line("\n\n   \n", "any source text at all\n") is None


def test_evidence_line_returns_none_for_empty_text() -> None:
    """No lines, no evidence. Reached in practice through
    `concept._unevidenced_titles`, whose fallback can still be empty only
    if validation let an empty description through -- fail-closed rather
    than crash."""
    assert (
        evidence_mod.evidence_line("", "Priya owns the schema migration plan.") is None
    )


def test_evidence_line_returns_none_against_an_empty_source() -> None:
    """An empty source grounds nothing. The substring test would otherwise
    be `"..." in ""`, which is `False` for every candidate anyway; asserted
    so the degenerate input is a decided case rather than an accident."""
    assert (
        evidence_mod.evidence_line("Priya owns the schema migration plan.", "") is None
    )


def test_evidence_module_imports_no_sibling_extraction_module() -> None:
    """The leaf rule `judge.JudgeCandidate` states for `judge.py` (design
    D2), enforced here for `evidence.py` too: this module deals in plain
    strings, so `concept.py` can call it without either module importing
    the other. Asserted structurally rather than trusted, because an import
    added later would still pass every behavioural test above."""
    import ast
    from pathlib import Path

    source = Path(evidence_mod.__file__).read_text(encoding="utf-8")
    imported: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)

    assert not [name for name in imported if name.startswith("openkos")]
