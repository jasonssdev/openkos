# Exploration: `status` surfaces pending duplicate groups

Issue #186 (P1), **signal 1 only**. Scope locked before exploration — see "Locked scope" below.

## Locked scope

Issue #186 asks to fold three signals into `needs_attention`. Only the first is
implementable today; the other two have no durable state to read.

| Signal | Verdict |
|---|---|
| 1. Pending duplicate groups | **IN SCOPE.** `resolution/candidates.py::find_candidates` already computes them. |
| 2. Sources with skipped / zero-concept extraction | **OUT.** No durable trace exists. Issue #187 (P1) is what would create it. |
| 3. Unmerged clusters adjudicated as SAME | **OUT.** `resolution/adjudication.py:82-83` — `AdjudicatedCandidate` is *"Ephemeral -- never a persisted OKF type or `bundle`/`state` file."* Related to #191. |

Signals 2 and 3 must not creep back in during spec, design, or apply.

## Current state

`needs_attention` is built at `src/openkos/cli/main.py:4563-4574` (the issue text
cites 4293-4302; the file has shifted) from exactly three sources:

- `survey.findings` — §9 conformance, from `okf.survey_bundle`
- rendered `lint_check.check_dangling_targets` findings
- a missing/empty `vectors.db` line

`find_candidates` is never consulted. The bug is real and confirmed.

The `#141` comment directly above the block documents that `status` already
deliberately calls `lint`'s own helpers and folds the rendered lines in. That is
the precedent for adding a `find_candidates` call the same way.

## Affected areas

- `src/openkos/cli/main.py:4554-4593` — the `needs_attention` build, plus the
  docstring at 4499-4521 which documents "THREE independent bundle walks" and
  must become four.
- `src/openkos/cli/main.py:661` — `_plural(n)`, reusable for count wording.
- `src/openkos/cli/main.py:687-697` — `_format_group_tally(high, low)`, which
  renders `"N candidate group(s) (X exact, Y near)"`. Available but **not**
  recommended here; see the tier finding below.
- `src/openkos/cli/main.py:4690-4757` — the `duplicates` command, reference
  consumer of `find_candidates` and source of the default-exclude convention.
- `openspec/specs/status/spec.md` — needs a fifth `Needs-Attention` requirement,
  styled like the existing four at :77 / :97 / :129 / :164, each carrying a
  "no issues" scenario and a "surfaced" scenario.
- `tests/unit/cli/test_status.py` — needs-attention tests from line ~260 onward.
- `tests/unit/cli/test_duplicates.py` — fixture vocabulary to copy.

## Findings

### 1. The remediation command is `openkos duplicates`, not `adjudicate`

- Issue #186's own proposed text names `duplicates` as the reference
  ("`duplicates` already finds them").
- `duplicates` is deterministic and stdlib-only: no Ollama, no LLM, no running
  server. That matches `status`'s read-only, Phase-A-only, dependency-free
  character.
- `adjudicate` builds a real `OllamaClient(model=cfg.model)` and can fail with
  `OllamaUnavailable` / `OllamaModelNotFound`. Pointing at it from a lightweight
  orientation command would send users to a heavier, network- and
  model-dependent command.
- `merge` does exist (`main.py:3483`) and is what `duplicates`'s own trailing
  hint points to (`"Next: openkos merge <survivor> <absorbed>"`), but it requires
  the user to already know which ids are survivor and absorbed — not a fact
  `status` can supply.

So `status` points at the REPORT step, exactly as `duplicates` points at the
ACTION step: a three-step chain, `status` → `duplicates` → `merge`/`adjudicate`,
each command naming the next.

### 2. Report the flat total, not a HIGH/LOW split

- Issue #186's own example is a flat count: "4 duplicate groups awaiting
  adjudication", with no tier breakdown.
- Issue #192 (open, P2) establishes that readers currently misread the HIGH/LOW
  tier as a confidence level when it actually encodes match *method* (exact-key
  vs near-match). Printing "X exact, Y near" in a second command would extend
  that vocabulary to a new surface and compound #192 instead of staying neutral
  to it.
- Recommended wording uses the total count only, with `_plural()` for the
  suffix, and no tier language.

This is a judgment call from cross-issue evidence, not a locked decision. The
proposal and spec phases should confirm it with a stated rationale rather than
adopt it silently.

### 3. Cost is acceptable and precedented

`status`'s docstring already documents three independent whole-bundle walks
(`survey_bundle`, `collect_docs`, conditionally `build_graph`) and explicitly
defers consolidating them to issue #195 as a non-goal. `find_candidates` adds a
fourth walk of the same shape (`_iter_eligible`, plus a
`lifecycle.deprecated_concept_ids` walk unless `include_deprecated`) under the
same accepted precedent. No shared-read opportunity is worth pursuing here.

### 4. No `vectors_missing` dependency

`find_candidates` reaches `similarity.near_match_score`, which is entirely
stdlib (`difflib.SequenceMatcher` over normalized title tokens,
`src/openkos/resolution/similarity.py`). It never touches embeddings or
`vectors.db`. The duplicates check must therefore run unconditionally, unlike
the edge-count summary which is gated on `vectors_missing`.

### 5. Deprecated concepts stay excluded by default

Consistent with `duplicates`'s own `include_deprecated: bool = False`. `status`
has no `--include-deprecated` flag today and adding one is outside what issue
#186 implies.

## Insertion point

`status` maintains a deliberate split between ACTIONABLE entries (appended to
`needs_attention`) and INFORMATIONAL ones (the edge-count summary, kept out so a
healthy workspace still prints "Nothing needs attention."). A duplicates line
names a concrete follow-up command, so it is unambiguously ACTIONABLE and
belongs in `needs_attention`.

Recommended position: after the dangling-reference block (line 4564) and before
the `vectors_missing` check (4569-4574) — grouped with the other structural and
content findings, ahead of the infra-availability check that gates the trailing
informational block. This is a judgment call; no existing test asserts overall
multi-entry ordering except the state-3-over-edge-summary case, which is
unaffected.

## Test coverage to extend

`tests/unit/cli/test_status.py`, joining the needs-attention wiring block
(marker comment at line 279). New cases needed:

- a bundle with two same-type docs sharing a normalized title, asserting the new
  line appears and `Nothing needs attention.` does not;
- a no-duplicates case asserting the line is absent;
- a deprecated-only duplicate, asserting the default exclusion (mirrors
  `test_duplicates_default_excludes_a_deprecated_group_member`);
- singular vs plural count wording.

`tests/unit/cli/test_duplicates.py` supplies the fixture vocabulary: `_write_doc`,
`_init_workspace`, HIGH via identical or near-identical titles, LOW via
`Stoicism` / `Stoic Philosophy`.

No existing status test exercises `find_candidates` at all, so this is entirely
new coverage. The existing healthy-bundle fixtures were checked and do not
incidentally produce duplicate-eligible titles.

## Recommendation

Add a fourth `needs_attention` source in `status`: call
`find_candidates(layout.bundle_dir)` with the default `include_deprecated=False`
and no new CLI flag. When the result is non-empty, append one line carrying the
flat group count via `_plural()` and naming `openkos duplicates` as the next
step. Insert it after the dangling-reference block and before the
`vectors_missing` check. Update the docstring's "THREE independent walks"
language to four. Add a fifth requirement to `openspec/specs/status/spec.md`
mirroring the existing four, recording the deprecated-exclusion default and the
no-tier-label rationale. Extend `tests/unit/cli/test_status.py` as above.

## Risks

- The flat-count-over-tier-split choice is reasoned from cross-issue evidence,
  not locked. Propose/spec should confirm or override it explicitly.
- Line ordering relative to `vectors_missing` is a recommendation, not
  test-enforced, and can move without invalidating this exploration.
- `adjudicate`'s docstring calls `merge` a "reserved ... slice 3" verb while
  `merge` is fully implemented at `main.py:3483`. Stale documentation elsewhere
  in the codebase; flagged, deliberately not fixed as a drive-by here.
- Signals 2 and 3 of issue #186 stay out of scope.

## Ready for proposal

Yes. The exact insertion point, the reusable helper, the fixture patterns to
copy, and a defensible wording recommendation are all established. The one open
question for the proposal to settle explicitly is the tier-split-versus-flat-count
wording.
