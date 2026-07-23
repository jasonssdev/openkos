# Proposal: Two-Output Rule — file a query answer back as a concept

## Intent

`query` produces only its FIRST output: a cited answer printed to stdout, strictly
read-only (main.py:3915-4106). The SECOND output — "a good answer can be filed
back as a new OKF concept" (roadmap.md:67; cli.md:117 "not automated in this
slice") — is still manual. This is the LAST unwired MVP-2 deliverable; closing it
brings MVP-2 to 100% and closes the compounding-knowledge loop so answers become
durable, provenance-linked concepts instead of ephemeral stdout.

## Scope

### In Scope
- Opt-in `--save` flag on `query` (default OFF). Absent → `query` stays
  BYTE-IDENTICAL read-only (read-only purity preserved via opt-in).
- With `--save`: after printing the answer, reuse ingest's stage → Phase-A preview
  → confirm gate (+ `--auto`) → Phase-B write pipeline to file the answer.
- Filed concept via `okf.build_concept`: body = answer text; TITLE = question
  (`--title` override); DESCRIPTION = question (build_concept requires non-empty
  single-line); TYPE = "Concept" (`--type` override).
- PROVENANCE = cited concept ids (`result.citations`) → a genuine derived concept
  the graph + forget/purge cascade (`find_provenance_descendants`) see.
- SENSITIVITY = high-water-mark over re-read cited-concept frontmatter
  (`okf.combine_sensitivity`); fallback `cfg.default_sensitivity`; auto-yields
  confidential under `--include-confidential`.
- Reuse `_slugify`, `write_exclusive`, `insert_index_entry`, and a distinct
  "filed answer" `insert_log_entry` line.

### Out of Scope / Non-Goals
- No auto-reindex — `reindex` required after (consistent with ingest).
- No LLM-generated title (extra cost); no mandatory `--title`.
- No change to `query`'s default read-only behavior.

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- `query-command`: adds the opt-in `--save` write path. Its spec Non-Goals today
  explicitly exclude "automated re-filing of an answer back as a concept"; this
  change reverses that, gated behind `--save`, and adds requirements for the
  build/preview/confirm/write flow, provenance = cited ids, and the sensitivity
  high-water-mark fold.

## Approach

`--save` computes nothing when absent. When set, after rendering the answer,
re-read each cited concept's frontmatter for the sensitivity fold (Citation carries
only id+title), then feed title/description/type/provenance/sensitivity/body to the
existing ingest builder → Phase-A preview → confirm/`--auto` → `write_exclusive` +
index/log insert. Two properties make this a CORRECT two-output rule, not a
note-dump: (1) read-only purity preserved by opt-in; (2) provenance = cited ids +
sensitivity high-water-mark, so the filed concept is a first-class derived node the
graph and cascade already understand.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `cli/main.py` query (3915-4106) | Modified | `--save`/`--title`/`--type` flags; post-answer file path |
| `model/okf.py` build_concept (140-204) | Modified | parameterize `## Related` wording for concept→concept (answer-filed) case |
| `openspec/specs/query-command/spec.md` | Modified | reverse re-filing Non-Goal; add `--save` requirements |

## Risks

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| `--save` alters read-only path | Low | Flag default OFF; absent path byte-identical; assert in tests |
| Confidential answer filed as non-confidential | Low | High-water-mark fold + `default_sensitivity` fallback (fail-safe) |
| `## Related` "extracted from" wording wrong for filed answer | Med | Parameterize wording for concept→concept provenance |
| Filed concept invisible until reindex confuses users | Low | Stated Non-Goal; surface a reindex hint in output |

## Rollback Plan

Purely additive opt-in flag. Revert the `query` diff and the `build_concept`
wording tweak; no data migration — absent `--save`, behavior is unchanged.

## Dependencies

- `okf.build_concept` / `combine_sensitivity`, `_slugify`, `write_exclusive`,
  `insert_index_entry`, `insert_log_entry`, and ingest's preview/confirm/write
  ordering — all shipped, reused not modified (except the wording tweak).

## Success Criteria

- [ ] `query` without `--save` is byte-identical to today (read-only).
- [ ] `query --save` files a concept: body=answer, title/description=question,
      type=Concept, provenance=cited ids, sensitivity=high-water-mark.
- [ ] Filed concept is seen by the forget/purge cascade (provenance-linked).
- [ ] `--title`/`--type` overrides and `--auto` confirm bypass work.
- [ ] Under `--include-confidential`, a confidential-cited answer is filed confidential.

## Arc Note

The LAST unwired MVP-2 deliverable (roadmap.md:67). This is a WRITE verb but NOT
destructive — a small additive slice. Closing it brings MVP-2 to 100%.
