# Delta for status

## ADDED Requirements

### Requirement: Needs-Attention Surfaces Unextracted Sources

`openkos status` MUST fold `lint`'s `unextracted` findings into its "needs
attention" section, naming the same retry command `lint` computes. This
requirement is deliberately spec-level, not an implementation detail: `status`
already runs four bundle walks (`main.py` docstring, consolidation tracked
separately under #195) and already folds `lint_check.collect_docs()`'s
dangling-reference findings into `needs_attention` without a fifth walk
(precedent: #216, where a repeated compute-then-discard walk was the bug).
`status` MUST consume the SAME in-memory `docs` list from the `collect_docs()`
call it already makes — it MUST NOT perform a second `collect_docs()` call or
any new `rglob`. Only `failed`-sourced `unextracted` findings reach
`needs_attention`; `status` remains read-only and MUST exit 0 regardless of
findings.

#### Scenario: Unextracted source surfaced under needs attention

- GIVEN a bundle containing a Source with `extraction_status: failed`
- WHEN `openkos status` runs
- THEN the retry command for that Source is listed under "needs attention",
  and the command still exits 0

#### Scenario: blocked-by-sensitivity never appears in the retry prompt

- GIVEN a bundle containing only a Source with
  `extraction_status: blocked-by-sensitivity`
- WHEN `openkos status` runs
- THEN no unextracted-source entry appears under "needs attention" for that
  Source, and it appears in no retry prompt

#### Scenario: No new bundle walk is introduced

- GIVEN `status` already calls `lint_check.collect_docs()` once for dangling
  findings
- WHEN the unextracted-source check also runs
- THEN it reuses that same in-memory `docs` list and `status` still performs
  no more bundle walks than before this change
