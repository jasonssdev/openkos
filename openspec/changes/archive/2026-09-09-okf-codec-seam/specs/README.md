# Spec Phase Decision — okf-codec-seam

## Decision: no delta spec written

This change gets no `specs/<capability>/spec.md`. The proposal's
`Capabilities: New — None / Modified — None` reading holds after testing it
against the source, not by default.

## The question tested

Do the two `_split_frontmatter_verbatim` messages —
`"index.md: missing or malformed frontmatter block"`
(`index.py:30`) and `"Source document: missing or malformed frontmatter
block"` (`source_titles.py:157`) — describe a capability-level requirement
that belongs in a spec, or are they implementation detail of a private
helper that belongs in tests and design only?

## Evidence

- Both raises are `ValueError`s inside a leading-underscore private helper,
  not a public API surface.
- **`source_titles.py`'s message is not operator-facing on its one call
  path.** `retitle_document`'s only caller, `_stage_retitles`
  (`source_titles.py:417-432`), wraps the call in `except ValueError:` and
  discards the exception text, reporting the stable reason token
  `"unpatchable-title"` in the warned bucket instead. The raw string never
  reaches an operator. This is exercised today by
  `test_retitle_document_refuses_a_crlf_framed_document`
  (`tests/unit/bundle/test_source_titles.py:187-197`), which is why that
  test can only assert the shared half of the message — the distinguishing
  prefix is unreachable from that call site's actual output.
- `index.py`'s message likely does reach an operator when `index.md` is
  corrupted mid-command, via this codebase's widespread
  `except ValueError as exc: typer.echo(f"...: {exc}", err=True)` pattern
  in `cli/main.py`. Confirming the exact call chain for every
  `insert_index_entry`/`remove_index_entry` site was not pursued further —
  it does not change the conclusion below.
- No capability spec in `openspec/specs/` documents either message today.
  `openspec/specs/source-title-backfill/spec.md` documents adjacent
  `retitle_document` refusals (`HeadingMismatchError`, an unrewritable
  `title:` scalar) but not the CRLF/malformed-frontmatter refusal path,
  even though it is real and tested — a pre-existing gap, not one this
  change opens or is scoped to close.
- `index.py` and `source_titles.py`'s copies of the helper are shared
  low-level parsing plumbing consumed by many unrelated capabilities
  (ingest, merge, unmerge, forget, backfill-sensitivity,
  backfill-source-titles, adjudicate). No single capability owns their
  error contract; assigning one to house this pin would be inventing
  ownership the codebase does not have.

## Conclusion

The messages are pre-existing, already-shipped behavior. This change's job
is to keep them byte-identical while moving and parameterizing the helper —
a regression risk, not a new or changed requirement. The proposal already
owns that risk at the right layer: a cross-module parity test and a golden
round-trip fixture landing **before** the move (In Scope #1), asserting both
full strings including their prefixes. That is where "WHAT must stay true"
belongs for an internal helper's exact wording — not a capability spec.
Writing one here would document HOW a private function signals failure,
which the spec skill's own rule forbids, and would not attach to any real
capability's behavior.

## What the next phase should carry forward

- `design.md` should record the parameterized-label decision (D-note) and
  the pin-before-move ordering; no spec pin exists to reference.
- `tasks.md` should schedule the parity test and golden fixture as the
  literal first task, each shown failing against the current duplication
  before the move lands, per the proposal's Success Criteria.
- The `source_titles.py`→`"unpatchable-title"` swallowing means the parity
  test must assert the raised `ValueError` text directly (calling the
  helper or catching it before `_stage_retitles`'s wrapper), not through
  `backfill-source-titles`'s CLI-visible output — that surface never shows
  it.
