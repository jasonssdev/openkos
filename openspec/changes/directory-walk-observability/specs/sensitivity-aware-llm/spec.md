# Delta for Sensitivity-Aware LLM

## ADDED Requirements

### Requirement: Walk-Incompleteness Observability

The system MUST detect when the directory walk underlying the fail-closed
sensitivity filter is provably incomplete (`okf._walk_errors` reports one or
more unlistable subdirectories) and MUST emit a warning to STDERR identifying
the incomplete-walk condition, for each of the five sensitivity-filter verbs:
`query`, `contradictions`, `adjudicate`, `suggest-relations`,
`suggest-volatility`. The command MUST still exit 0 (WARN, not refuse). The
warning MUST be skipped when `--include-confidential` is passed, since the
filter is then deliberately disabled. A future cloud-egress mode that instead
REFUSES on this condition is explicitly out of scope for this change.

#### Scenario: Incomplete walk warns and still exits 0
- GIVEN a bundle where `okf._walk_errors` reports at least one unlistable
  subdirectory
- WHEN `query`, `contradictions`, `adjudicate`, `suggest-relations`, or
  `suggest-volatility` runs without `--include-confidential`
- THEN the command prints a warning to STDERR identifying the incomplete walk
- AND exits 0

#### Scenario: Clean bundle produces no warning
- GIVEN a bundle where `okf._walk_errors` reports no unlistable subdirectories
- WHEN any of the five verbs runs
- THEN no incomplete-walk warning is printed to STDERR

#### Scenario: `--include-confidential` suppresses the warning
- GIVEN a bundle where `okf._walk_errors` reports an unlistable subdirectory
- WHEN any of the five verbs runs WITH `--include-confidential`
- THEN no incomplete-walk warning is printed, since the filter is
  deliberately off

### Requirement: Defense-in-Depth Sensitivity Re-Check at Load

Each of `contradictions`, `adjudicate`, `suggest-relations`, and
`suggest-volatility` MUST apply an independent fail-closed re-check — via
`sensitivity.blocks_llm_send` against that document's own frontmatter — at
the point a candidate/member/pair document is loaded by direct path, before
its content enters the `llm.chat` payload. This re-check MUST NOT depend on
whether the document was present in the precomputed blocked set built during
the directory walk: a confidential document absent from that set (e.g.
because its subtree became unlistable, or a permission change occurred,
after the walk but before the load) MUST still be excluded.
`--include-confidential` MUST bypass this re-check identically to how it
bypasses walk-based exclusion, restoring byte-identical pre-filter behavior.
`query` already implements this re-check (S3 FIX-2, answer.py:211-214) and
requires no behavior change.

#### Scenario: Confidential doc absent from the precomputed blocked set is caught at load
- GIVEN a confidential document that was NOT added to the precomputed
  blocked set (its containing subtree lost read permission after indexing,
  but the doc is still reachable and loaded by direct path)
- WHEN `contradictions`, `adjudicate`, `suggest-relations`, or
  `suggest-volatility` loads that document without `--include-confidential`
- THEN the independent per-doc re-check excludes it before it enters the
  `llm.chat` payload

#### Scenario: `--include-confidential` bypasses the re-check
- GIVEN the same confidential document as above
- WHEN any of the four verbs runs WITH `--include-confidential`
- THEN the document is loaded and sent exactly as pre-filter behavior would

#### Scenario: Query is already conformant
- GIVEN `query`'s existing send-time `sensitivity.blocks_llm_send` re-check
  (S3 FIX-2, answer.py:211-214)
- WHEN this change ships
- THEN `query`'s behavior is unchanged — it already independently re-checks
  each candidate at load, satisfying this requirement without modification
