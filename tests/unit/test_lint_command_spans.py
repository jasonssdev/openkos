"""Guard tests for the ONE cross-module contract `lint`'s finding details
carry: a detail that names a runnable command MUST spell that command as a
complete backtick span (issue #278).

`openkos next` recovers the command it prints by scanning the backtick spans
of the very detail these functions author (`cli/next_action.py`,
`_command_from_detail`). That couples two modules through prose formatting,
and the coupling is invisible from this side: rewording a detail so its
command is no longer a whole span does not fail anything here, in `lint`, or
in `status` -- it silently stops a `next` tier from firing, and `next` falls
through to a lower-ranked recommendation. A command is still printed. Just
the wrong one.

These tests live on the PRODUCING side deliberately. `test_next.py` already
covers the consuming side, but that is the wrong direction for a warning:
it catches the break only once someone has gone looking in `next_action.py`,
which is not a file the person editing a user-facing string in `lint.py` has
any reason to open.

What is pinned is the SPAN, not the substring. `"openkos ingest raw/foo.md"
in detail` -- which `test_lint.py` already asserts, for other reasons --
stays true after an edit that breaks the span, so it cannot stand in for
these.

Two edits are worth distinguishing, because issue #278 named the wrong one
as its failure mode:

- Adding a backticked phrase BEFORE the command is SAFE. #278 predicted
  this breaks tier 2; it does not. `_command_from_detail` scans every span
  and matches by verb, never by position -- a defense #265's own review
  added. Mutating `check_unextracted` exactly as the issue describes leaves
  all of these tests and all of `test_next.py` green.
- Splitting the command across spans (`` `openkos` `ingest x` ``) or
  dropping its backticks IS the real break, and it is silent: `next` falls
  through and prints a lower-ranked recommendation. That mutation fails the
  first test below, and seven in `test_next.py`.

There is a SECOND class of break, and it is worse, because the span it
produces is perfectly well-formed (#274/#285). These details interpolate
values the bundle's own documents control -- a Source's `resource` is the
raw, unsanitised basename `ingest` copied to disk (see
`okf.build_source_concept`'s contract), and a backtick is a legal POSIX
filename character. A backtick inside the value closes the span early, so
what a scanner reads out is a complete, runnable `openkos ingest <plain
path>` naming a DIFFERENT file than the finding is about. Nothing here is
malformed; the span is simply not the whole command.

That guard belongs on this side too. `next` corroborates the extracted
command against the document's own `resource` and declines (`test_next.py`),
but that defence protects exactly one consumer. The producing module is
where the truncated span is authored, so it is where "never emit a partial
command span a scanner would read as whole" has to be pinned -- otherwise
the next consumer inherits the same trap and has to rediscover it.
"""

import re
from pathlib import Path

from openkos import lint

_BACKTICK_SPAN = re.compile(r"`([^`]+)`")


def _spans(detail: str) -> list[str]:
    """Every complete backtick span in `detail`, in order -- the same shape
    `next_action._command_from_detail` iterates."""
    return _BACKTICK_SPAN.findall(detail)


def _source(
    identity: str,
    *,
    extraction_status: str = "",
    resource: str = "",
) -> lint.LintDoc:
    return lint.LintDoc(
        path=Path(f"/bundle/{identity}.md"),
        identity=identity,
        rel_dir="sources",
        body="",
        freshness="",
        type="Source",
        volatility="",
        extraction_status=extraction_status,
        resource=resource,
    )


def _concept(
    identity: str,
    *,
    doc_type: str = "Concept",
    sensitivity: str = "public",
    provenance: tuple[str, ...] = (),
) -> lint.LintDoc:
    return lint.LintDoc(
        path=Path(f"/bundle/{identity}.md"),
        identity=identity,
        rel_dir=str(Path(identity).parent) if "/" in identity else "",
        body="",
        freshness="",
        type=doc_type,
        volatility="",
        sensitivity=sensitivity,
        provenance=provenance,
    )


def test_unextracted_detail_spells_its_retry_command_as_a_whole_span() -> None:
    """`check_unextracted`'s retry hint is what tier 2 of `next` prints. The
    command must be one complete span, not a phrase straddling a backtick
    boundary."""
    docs = [_source("sources/notes", extraction_status="failed", resource="raw/foo.md")]

    findings = lint.check_unextracted(docs)

    assert "openkos ingest raw/foo.md" in _spans(findings[0].detail)


def test_unextracted_fallback_detail_spells_the_bare_verb_as_a_whole_span() -> None:
    """The empty-`resource` fallback names `openkos ingest` with no
    argument. `next` deliberately declines to recommend it (#276) -- but it
    declines by RECOGNISING it, so the span still has to be well-formed or
    the declination becomes an accident."""
    docs = [_source("sources/notes", extraction_status="failed", resource="")]

    findings = lint.check_unextracted(docs)

    assert "openkos ingest" in _spans(findings[0].detail)


def test_unextracted_detail_emits_no_partial_ingest_span_for_a_backtick_resource() -> (
    None
):
    """A backtick in `resource` closes the retry hint's span early, and what
    survives is a WELL-FORMED `openkos ingest <plain path>` naming a
    different file than the finding is about (#274). No span this module
    emits may begin with `openkos ingest ` unless it is the whole command,
    so for an unspellable value it emits none at all (#285). Pinned here, on
    the producing side, because this is where the truncated span would be
    authored."""
    docs = [
        _source(
            "sources/notes",
            extraction_status="failed",
            resource="raw/notes.txt`; rm -rf /",
        )
    ]

    findings = lint.check_unextracted(docs)

    assert not [
        span
        for span in _spans(findings[0].detail)
        if span.startswith("openkos ingest ")
    ]


def test_below_source_detail_spells_backfill_sensitivity_as_a_whole_span() -> None:
    """`check_below_source_sensitivity`'s remedy command is what tier 3 of
    `next` prints."""
    source = _concept("sources/a", doc_type="Source", sensitivity="confidential")
    descendant = _concept(
        "concepts/derived", sensitivity="public", provenance=("sources/a",)
    )

    findings = lint.check_below_source_sensitivity([source, descendant])

    below = [f for f in findings if f.kind == "below-source-sensitivity"]
    assert "openkos backfill-sensitivity" in _spans(below[0].detail)


def test_multi_source_uncovered_detail_spells_only_the_resolving_command() -> None:
    """The one whole span this detail spells is the command that WORKS (#693).

    It used to spell `openkos backfill-sensitivity` -- named only to rule it
    out ("is not covered by") -- and nothing else. `next` was kept away from
    it by tier 3's kind filter alone. That was fine while `next` printed
    nothing for this finding, and stopped being fine the moment it grew a
    tier of its own: the reason line echoes this detail verbatim, so a
    negated command sitting in copy-paste shape would reach the screen by
    the front door instead of the back.

    So the sweep is now named as prose and the only backtick command is
    `openkos set-sensitivity`. This test pins that: exactly one `openkos ...`
    span, and it is the resolving one.
    """
    source_public = _concept("sources/a", doc_type="Source", sensitivity="public")
    source_conf = _concept("sources/c", doc_type="Source", sensitivity="confidential")
    from_c = _concept(
        "concepts/from-c", sensitivity="confidential", provenance=("sources/c",)
    )
    mixed = _concept(
        "concepts/mixed",
        sensitivity="public",
        provenance=("sources/a", "concepts/from-c"),
    )

    findings = lint.check_below_source_sensitivity(
        [source_public, source_conf, from_c, mixed]
    )

    uncovered = [f for f in findings if f.kind == "multi-source-uncovered"]
    commands = [s for s in _spans(uncovered[0].detail) if s.startswith("openkos ")]
    assert commands == ["openkos set-sensitivity concepts/mixed confidential"]
    assert commands[0] == uncovered[0].remediation
