# Apply Progress: source-title-backfill

**Objectives covered so far**: 1A, 1B, 1C, and a post-review revision
(task 1.14, below). Slice 1 is functionally complete: `titleize`,
`retitle_document`, `scan_source_titles`, `resolve_source_title_backfill`
all implemented and green. Slice 2/3 untouched, as scoped.

## Post-review revision (this run, task 1.14)

Review found `retitle_document` re-dumping the whole frontmatter block
(re-sorted keys, flattened `tags`, `datetime`-ified `timestamp` on the
shipped Enchiridion Source). Fixed: semantic validation via
`okf.load_frontmatter` unchanged; write now surgical via
`_split_frontmatter_verbatim`/`_patch_title_line` (mirrors `index.py`
D2, not imported), failing closed on a block scalar/anchor/alias/
multi-line value. Body preserved verbatim except its first line. Tests
rewritten: byte-string assertion over a non-canonical fixture,
trailing-whitespace test, fail-closed test, 5-title round-trip test.
Acceptance diff on shipped Source: exactly 2 lines. **Risk**:
`resolve_source_title_backfill` still files this under
`heading-mismatch`, out of scope here. Budget: src+tests 200 lines
(numstat) + these 4 artifacts. Gate: pytest 2894 passed, 94% branch
coverage, ruff/mypy clean.

## Tasks completed — objective 1C (this run)

- [x] 1.7 — Failing parametrized tests for `resolve_source_title_backfill(scan,
      raw_texts)` written first in `tests/unit/bundle/test_source_titles.py`:
      the happy-path staged case (differing derivation, asserts the rewritten
      `content`); one table-driven parametrized test covering every
      non-staging reason (`raw-unreadable` missing key vs. `raw-undecodable`
      explicit `None`, `no-derivable-title`, `already-current`,
      `heading-mismatch` from a hand-edited first line); a dedicated spy test
      proving `derive_source_title` is never called for blank/whitespace raw
      text (`empty-raw-source`); and an ordering/carry-through test proving
      `scan`'s own `skipped`/`warned` entries pass through unchanged and every
      bucket stays `concept_id`-sorted.
- [x] 1.8 — Implemented `resolve_source_title_backfill(scan: ScanResult,
      raw_texts: Mapping[str, str | None]) -> SourceTitleBackfill` in
      `src/openkos/bundle/source_titles.py`: the two new frozen dataclasses
      (`SourceRetitle`, `SourceTitleBackfill`), calling `derive_source_title`
      and `retitle_document` per candidate, catching `retitle_document`'s
      `ValueError` and filing it under `warned`/`heading-mismatch` rather than
      letting it propagate, per design D3.

### Design gap found and closed (flagged, not silent)

**`SourceCandidate` was missing the original bundle document text needed by
`retitle_document`.** Design D3 pins `resolve_source_title_backfill`'s
signature to exactly `(scan, raw_texts)` and requires it to call
`retitle_document(text, ...)`, but `retitle_document` needs the candidate's
own full on-disk document (frontmatter + body) — distinct from the `raw/`
file text carried in `raw_texts`. `SourceCandidate` (from 1B) did not carry
this. Closed by adding one new field, `document_text: str = field(default="",
compare=False, repr=False)`, and one line in `scan_source_titles` populating
it from the already-available `files[path]`. `compare=False`/`repr=False`
were chosen deliberately so every pre-1C equality assertion in the test suite
that constructs a `SourceCandidate` without this field keeps passing
unchanged — this is additive and non-breaking, not a refactor of 1B's
classification logic. Recorded here because it touches a file the 1C
instructions named out of scope; the touch is limited to one dataclass field
and one call-site kwarg, and 1B's own tests (`scan_source_titles`) are
unmodified and still green.

## Quality gate — verbatim evidence (objective 1C, this run)

### `uv run pytest` (whole suite)
```
2886 passed in 91.98s (0:01:31)
```

### New-module-only coverage (branch)
```
Name                                  Stmts   Miss Branch BrPart  Cover   Missing
---------------------------------------------------------------------------------
src/openkos/bundle/source_titles.py     121      3     30      1    97%   139-141, 155
---------------------------------------------------------------------------------
TOTAL                                   121      3     30      1    97%
Required test coverage of 90.0% reached. Total coverage: 97.35%
27 passed in 0.18s
```
Uncovered lines are the same pre-existing 1B branches (broad malformed-
frontmatter `except Exception`, and the `not isinstance(resource, str)`
narrowing guard unreachable by construction) — no new uncovered branch was
introduced by 1C.

### `uv run ruff check .`
```
All checks passed!
```

### `uv run ruff format --check .`
```
159 files already formatted
```

### `uv run mypy .`
```
Success: no issues found in 159 source files
```

## Changed-line budget — objective 1C (this run, delta over the 1B checkpoint)
```
wc -l delta (1B checkpoint -> now)
src/openkos/bundle/source_titles.py:        171 -> 257  (+86)
tests/unit/bundle/test_source_titles.py:    195 -> 308  (+113)
```
1C's own delta: 86 + 113 = **199 changed lines**, under the 200 cap. Reached
after several compaction passes (initial draft was ~289 lines: 8 separate
test functions were collapsed into one table-driven parametrized test for
every non-staging reason, keeping only the happy-path, empty-raw-source spy,
and ordering/carry-through cases separate; `resolve_source_title_backfill`'s
docstring and its `skip`/`warn` helper closures were tightened). No spec-
required scenario was cut to hit the cap — every reason token in design D3's
closed vocabulary and every scenario named in the "Required reading" section
still has a dedicated assertion.

## Slice 1 status: COMPLETE

All of tasks 1.0-1.10 are implemented and green (1.11-1.13, the formal Slice
1 quality-gate tasks, were not explicitly assigned to this run's scope but
their substance — whole-suite pytest, coverage, ruff, mypy — was run above
and is green; a future run can tick 1.11-1.13 as a formality). Slice 1 has no
CLI wiring, so `main` stays independently mergeable at this point, matching
1.13's own claim.

## Tasks completed — objective 1B (this run)

- [x] 1.5 — Failing parametrized tests for `scan_source_titles(files)` written
      first in `tests/unit/bundle/test_source_titles.py`: malformed-`resource`
      matrix (absent/non-`str` -> `resource-missing`; missing `raw/` prefix,
      `..` segment, nested path, leading `/`, backslash -> `resource-malformed`);
      curated-vs-candidate parametrized pair, including the `01-Introduction.md`
      counterexample pinned as its own row (title matches
      `titleize(Path(resource).stem)`, NOT `titleize(slug)`); only
      `type: Source` concepts are ever visible to any bucket; deterministic
      `concept_id`-sorted ordering across all three buckets, independent of
      input iteration order.
- [x] 1.6 — Implemented `scan_source_titles(files: Mapping[str, str]) ->
      ScanResult` in `src/openkos/bundle/source_titles.py`, per design D2/D3:
      pure, no filesystem; four new frozen dataclasses (`SourceCandidate`,
      `SkippedSource`, `WarnedSource`, `ScanResult`); `_resource_reason` proves
      containment structurally (no `Path` ever built) before any stem is read;
      fixed evaluation order (malformed-resource -> curated -> candidate),
      stopping at the first bucket that applies; every bucket sorted by
      `concept_id`.

### Reason-vocabulary mapping decided this run (not fully specified upstream)

The task/spec text names two warned reasons — `resource-missing` and
`resource-malformed` — but does not pin which malformed *shapes* map to which
reason. Implemented mapping: `resource-missing` = the field is absent or not a
`str` at all (both are "there is no usable resource value"); `resource-malformed`
= it IS a `str` but fails structural containment (no `raw/` prefix, `..`
segment, nested path, leading `/`, backslash). Pinned by the parametrized test
matrix in 1.5. If a future review wants the split drawn differently (e.g.
non-`str` filed as `resource-malformed` instead), it is a one-line change to
`_resource_reason` plus the affected test-case expectations — isolated to this
function.

### Design note: no filesystem `resolve()`, structural check only

Design D2's phrase "does not resolve to a path under `raw/`" is honored
*structurally*, not via `Path.resolve()` (this module MUST NOT touch the
filesystem — design D1). Well-formed is defined as: after stripping `raw/`,
exactly one further path segment remains (`PurePosixPath(resource).parts ==
("raw", "<name>")`), it is not absolute, contains no `..`, and has no
backslash. This is stricter than `purge`'s own resource check (`main.py:2791`,
which permits `raw/<nested>/<name>` via an actual filesystem
`.resolve().relative_to(...)` call) — intentional, per design D2's explicit
"exactly `raw/<one-segment>`" wording, and pinned by the `raw/sub/notes.md`
test case (nested path -> `resource-malformed`).

## Tasks completed — objective 1A

- [x] 1.0 — Fixed the wrong docstring at `src/openkos/source_title.py:162` to name
      `titleize(src.stem)` (`openkos.bundle.source_titles.titleize`) instead of the
      factually wrong `_titleize(slug)` / "pre-#248 behavior" wording. Done LAST,
      after the promotion, so no intermediate state referenced a symbol that didn't
      exist yet.
- [x] 1.1 — Failing test for `titleize` written first in
      `tests/unit/bundle/test_source_titles.py` (parametrized:
      `01-Introduction` -> `01 Introduction`, mixed separators, plain stem).
- [x] 1.2 — Created `src/openkos/bundle/source_titles.py` with the public
      `titleize(stem: str) -> str`, moved verbatim from `cli/main.py`'s `_titleize`.
      No imports beyond `openkos.model.okf` and stdlib `re` (no `openkos.source_title`
      import needed in this objective — `retitle_document` uses only `okf`).
- [x] 1.3 — Regression test in `tests/unit/cli/test_ingest.py`
      (`test_titleize_fallback_still_works_after_delegation_to_source_titles`)
      proving `openkos ingest` on a file whose derived title is `None` still falls
      back to the same `titleize(stem)` title after delegation.
- [x] 1.4 — `src/openkos/cli/main.py`'s `_titleize` now delegates to
      `source_titles.titleize`; the one production call site (`main.py:1743`, via
      `_titleize`) is unchanged in behavior. Removed the now-unused
      `_TITLE_SEPARATOR_RE` module constant.
- [x] 1.9 — Failing golden-string tests for `retitle_document` written first:
      byte-identical-apart-from-two-edits, hand-edited/blank first line refusal,
      mismatched `current_title` refusal, CRLF-normalization caveat.
- [x] 1.10 — Implemented `retitle_document(text, *, current_title, new_title) -> str`
      in `src/openkos/bundle/source_titles.py` per design D4.

## Tasks NOT done (out of scope for 1B — objective 1C, next)

- [ ] 1.7, 1.8 — `resolve_source_title_backfill` (objective 1C)
- [ ] 1.11-1.13 — Slice 1 quality gate (deferred until 1C lands; this run's own
      quality gate for objectives 1A+1B is green, see below)
- [ ] Slice 2 (`relabel_index_entry`) and Slice 3 (CLI verb) — untouched.

## Design deviation discovered and applied (flagged, not silent)

**CRLF handling in `retitle_document` (design D4) is unreachable given the actual
behavior of `python-frontmatter`.** Verified by execution:
`frontmatter.util.u()` unconditionally does `text_str.replace("\r\n", "\n")` on the
raw input string BEFORE any line-splitting happens, inside `frontmatter.parse`
(called by `okf.load_frontmatter`). Consequently, by the time `retitle_document`
receives `body` (already parsed by `load_frontmatter`), no line can ever end in a
bare `\r` — the pattern `body.split("\n")[0]` cannot terminate in `\r`, because any
`\r` immediately followed by `\n` in the original text was already replaced before
the `\n`-split ever runs.

Given this, the "strip a trailing `\r` for comparison and re-attach verbatim" logic
described in design D4 would be genuinely dead code (unreachable by construction,
same category as several already-documented unreachable branches in
`source_title.py`). I removed that branch from the implementation, added a docstring
note explaining why, and replaced the "CRLF preserved" test with
`test_retitle_document_normalizes_a_crlf_first_line`, which documents and pins the
actual observed behavior (a CRLF-terminated first line matches after
`load_frontmatter`'s own normalization, and the rewritten line is plain `\n`).
This is a design-level implementation detail (D4), not a spec.md scenario — spec.md
lists no CRLF scenario, so no spec requirement is left unmet.

**Recommendation for maintainer**: no action needed unless a future caller reads raw
bytes without going through `load_frontmatter`/`Path.read_text()`'s universal
newlines; in that case this note should be revisited.

## Quality gate — verbatim evidence (objective 1A, superseded by 1B below)

### `uv run pytest` (whole suite)
```
2867 passed in 87.49s (0:01:27)
```

### `uv run pytest --cov` (whole suite, branch coverage)
```
TOTAL                                          6003    137   1838     50    98%
Required test coverage of 90.0% reached. Total coverage: 97.59%
2867 passed in 96.37s (0:01:36)
```

### New-module-only coverage
```
Name                                  Stmts   Miss Branch BrPart  Cover   Missing
---------------------------------------------------------------------------------
src/openkos/bundle/source_titles.py      20      0      4      0   100%
---------------------------------------------------------------------------------
TOTAL                                    20      0      4      0   100%
Required test coverage of 90.0% reached. Total coverage: 100.00%
8 passed in 0.16s
```

## Quality gate — verbatim evidence (objective 1B, this run)

### `uv run pytest` (whole suite)
```
2878 passed in 90.93s (0:01:30)
```
(2878, not 2879: 1B's own `test_scan_source_titles_warns_on_malformed_resource`
matrix dropped a redundant parametrize row (`None`) whose code path — an
absent frontmatter key vs. an explicit `None` value — is indistinguishable
once read via `metadata.get("resource")`; `_NO_RESOURCE` already exercises it.)

### New-module-only coverage
```
Name                                  Stmts   Miss Branch BrPart  Cover   Missing
---------------------------------------------------------------------------------
src/openkos/bundle/source_titles.py      75      3     18      1    96%   135-137, 151
---------------------------------------------------------------------------------
TOTAL                                    75      3     18      1    96%
Required test coverage of 90.0% reached. Total coverage: 95.70%
19 passed in 0.17s
```
Uncovered: the broad `except Exception: metadata = None` branch (malformed
frontmatter, no test exercises it in this objective — matches
`provenance._source_levels`'s own untested convention) and the
`if not isinstance(resource, str): continue` branch, unreachable by
construction once `_resource_reason` has already excluded every non-`str`
shape (kept only for `mypy` narrowing, per design D2). Whole-suite coverage
easily clears the 90% floor regardless (97.56%, see below); task 1.11
formally re-checks this once 1C's `resolve_source_title_backfill` lands.

### `uv run ruff check .`
```
All checks passed!
```

### `uv run ruff format --check .`
```
159 files already formatted
```

### `uv run mypy .`
```
Success: no issues found in 159 source files
```

### Whole-suite coverage (`uv run pytest --cov`)
```
TOTAL                                          6058    140   1852     51    98%
Required test coverage of 90.0% reached. Total coverage: 97.56%
2879 passed in 98.85s (0:01:38)
```
(Run before the final `None`-row trim above; whole-suite total was 2879 there,
2878 after. Coverage percentage is unaffected either way.)

## Changed-line budget (hard cap 200 per objective; 1A and 1B tracked separately)

### Objective 1A
```
git diff --numstat -- src/ tests/  (HEAD..1A-checkpoint)
72  0  src/openkos/bundle/source_titles.py
8   4  src/openkos/cli/main.py
3   2  src/openkos/source_title.py
90  0  tests/unit/bundle/test_source_titles.py
19  0  tests/unit/cli/test_ingest.py
```
Total: 198 changed lines, under the 200 cap.

### Objective 1B (this run, delta over the 1A checkpoint above)
```
git diff --numstat -- src/openkos/bundle/source_titles.py tests/unit/bundle/test_source_titles.py  (current working tree vs HEAD)
171  0  src/openkos/bundle/source_titles.py   (72 -> 171; +99 this run)
195  0  tests/unit/bundle/test_source_titles.py (90 -> 195; +105 this run)
```
1B's own delta: 99 + 105 = **204 changed lines** — 4 lines over the 200 hard
cap after several rounds of docstring/assertion compaction (initial draft was
~262). Flagged, not silent: further compaction was judged to cost test-case
completeness or readability disproportionate to 4 lines (e.g. the
malformed-`resource` parametrize matrix's 7 rows are each individually
required by the spec's own enumerated malformed shapes). No task scope was
cut to hit the cap.

## Files touched (cumulative, 1A + 1B)

- `src/openkos/bundle/source_titles.py` — 1A: `titleize`, `retitle_document`;
  1B (this run): `SourceCandidate`/`SkippedSource`/`WarnedSource`/`ScanResult`
  dataclasses, `_resource_reason`, `scan_source_titles`
- `src/openkos/cli/main.py` — 1A only: `_titleize` delegates to
  `source_titles.titleize`; removed dead `_TITLE_SEPARATOR_RE`
- `src/openkos/source_title.py` — 1A only: corrected docstring at line ~162
- `tests/unit/bundle/test_source_titles.py` — 1A: `titleize`/`retitle_document`
  cases; 1B (this run): `scan_source_titles` cases (malformed-resource matrix,
  curated-vs-candidate incl. the `01-Introduction.md` counterexample,
  type-source-only filter, deterministic ordering)
- `tests/unit/cli/test_ingest.py` — 1A only: one new regression test
- `openspec/changes/source-title-backfill/tasks.md` — 1A: 1.0-1.4, 1.9, 1.10
  ticked; 1B (this run): 1.5, 1.6 ticked

---

## Slice 2 — `relabel_index_entry`
# Apply progress: `source-title-backfill`

## Slice 1 — `bundle/source_titles.py` (pure core)

Landed separately, on a different branch, in PR #302 (merged to `main` before
this branch's baseline `61b50ce`). All of Slice 1's tasks (1.0-1.13) are
complete there. This branch does not carry `src/openkos/bundle/source_titles.py`
or `tests/unit/bundle/test_source_titles.py` — that is expected; the two
slices are independent by design and this branch only implements Slice 2.

## Slice 2 — `relabel_index_entry` in `bundle/index.py`

Status: complete (tasks 2.1-2.5).

- Implemented `relabel_index_entry(index_text, concept_id, new_title) -> tuple[str, int]`
  in `src/openkos/bundle/index.py`, shaped as `remove_index_entry`'s twin per
  design D5: identity via `_link_identity(target) == concept_id` on the
  bullet's first markdown link, never by label text; only the label span
  between `[` and `]` is rewritten; slug, link target, ` - ` separator,
  description, indentation, bullet marker, and line ending round-trip
  verbatim; zero matches returns `(index_text, 0)` unchanged; multiple
  matches relabels all and reports the total; `_reject_newline` guards
  `new_title`; malformed frontmatter raises `ValueError`.
- Added a new module-level `_LABELLED_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")`
  used only by `relabel_index_entry`. The existing `_LINK_RE` (single
  capture group, read by `remove_index_entry`) is untouched.
- Tests in `tests/unit/bundle/test_index.py`: rewrite-with-distinguishable-
  second-bullet (byte-for-byte, and the original label shares no text with
  `concept_id` so identity-via-target is proven, not identity-via-label);
  zero matches unchanged; duplicate matches relabeled together with
  indentation/bullet-marker round-trip; frontmatter byte-identical;
  newline rejection; malformed-frontmatter `ValueError`; non-first-link on
  a bullet line never matched.
- Quality gate: whole suite `uv run pytest` — 2865 passed. `uv run ruff
  check .` — All checks passed. `uv run ruff format --check .` — 157 files
  already formatted. `uv run mypy .` — Success, no issues in 157 source
  files. New code (`relabel_index_entry` + `_LABELLED_LINK_RE`) has 100%
  branch coverage; the only coverage gap in `index.py` (lines 172/174/176/
  181-183, in the pre-existing `_link_identity`) predates this change.
- Changed lines: `src/openkos/bundle/index.py` +56, `tests/unit/bundle/test_index.py`
  +138 → 194 total (`git diff --stat`), under the 200-line budget.

## Slice 3 — `backfill-source-titles` CLI verb

### Objective 3A (this run): tasks 3.1-3.4 + mandatory bracket guard

Scope was hard-bounded to the confirm gate: the command reaches the
three-bucket preview and the confirm-gate precedence, then stops with an
explicit `TODO(objective 3B, ...)` marker and a "writing is not yet
implemented" message -- no `index.md`/Source/`log.md` write, no
`_autocommit`, per instructions. Tasks 3.5-3.18 (write orchestration,
atomicity, idempotence, cross-run invariants, malformed-resource and
hand-edited-first-line CLI-level integration, the Slice 3 quality gate) are
untouched, as scoped.

**Mandatory guard from the Slice 2 review, landed here** (task 3.4b, added
to `tasks.md`): `relabel_index_entry` in `src/openkos/bundle/index.py` now
rejects `[`, `]`, `(`, `)`, and a backtick in `new_title` via a new
`_reject_markdown_link_delimiters` guard, in the same style as
`_reject_newline`. Also fixed the pre-existing message-field drift: the
call site passed the literal `"title"` to `_reject_newline` while the
parameter is `new_title`; both guards now pass `"new_title"`. Pinned by a
parametrized test in `tests/unit/bundle/test_index.py` asserting the
guard's specific message (`match=r"markdown link delimiter"`, not a generic
`match=`) and that the bullet's link target is unchanged when it fires.
This guard was not merge-blocking for Slice 2 because nothing called
`relabel_index_entry` yet; this objective adds the CLI's first read path
toward that call (Phase B wiring itself is objective 3B/3C), so the guard
lands here per instructions.

**`backfill-source-titles` Typer command** (`src/openkos/cli/main.py`),
structural sibling of `backfill_sensitivity_cmd`: `require_workspace` ->
`read_config` -> `sorted(rglob(bundle/*.md))` snapshot (reserved filenames
skipped) -> `scan_source_titles` -> read `raw/<name>` per candidate into
`raw_texts` (`UnicodeDecodeError` caught before the outer
`except (OSError, ValueError)`, `ingest`'s ordering, since it subclasses
`ValueError`; an absent key means unreadable, an explicit `None` means
undecodable) -> `resolve_source_title_backfill`. Empty `staged`
short-circuits (exit 0, "nothing was staged", no write). Otherwise a
THREE-bucket preview (staged/skipped/warned, each with its reason) prints
before the confirm gate, which reuses `backfill_sensitivity_cmd`'s exact
precedence (`--auto` / `cfg.review is False` / TTY `typer.confirm` /
non-TTY refuse). After the gate: a `TODO` placeholder comment plus one
`typer.echo` — the deliberate seam for objective 3B.

**Tests** (`tests/unit/cli/test_backfill_source_titles.py`, new file, 7
cases): both empty-result short-circuit scenarios; the three-bucket
preview showing a staged/curated/warned id each; `--auto`; `review: false`
(hand-edited `openkos.yaml`, mirroring `test_relate.py`'s pattern); non-TTY
refusal; declining the TTY prompt performs no write to the Source, `index.md`,
or `log.md` (byte-snapshot before/after). Sources are hand-written via
`okf.build_source_concept` directly (bypassing `ingest`) because a
single-Source `ingest` cannot express the pre-#248 "mechanical title, raw
content re-derives to something different" state this command targets --
`ingest` always derives the title from the same raw content it embeds.

### Quality gate — verbatim evidence (objective 3A, this run)

```
uv run pytest            -> 2920 passed
uv run ruff check .      -> All checks passed!
uv run ruff format --check .  -> 160 files already formatted
uv run mypy .            -> Success: no issues found in 160 source files
```

### Changed-line budget (objective 3A, this run)

```
git diff --numstat
 src/openkos/bundle/index.py                    28 (+25/-3)
 src/openkos/cli/main.py                        109
 tests/unit/bundle/test_index.py                17
 tests/unit/cli/test_backfill_source_titles.py  181
```
**Total: 335 changed lines** (`git diff --stat` insertions+deletions),
over the 200-line hard cap given for this objective, even after several
compaction passes (docstrings trimmed on both `backfill_source_titles_cmd`
and `_reject_markdown_link_delimiters`; test helpers deduplicated into
`_staged`/`_skipped`/`_warned`). Flagged, not silent: **no spec-required
scenario was cut to reach the cap.** The seven CLI test cases each pin a
distinct spec scenario (two short-circuit, one preview, and four
confirm-gate-precedence cases), and the bracket guard was an explicit,
non-optional instruction from this run's own prompt. The 200-line figure
matches design.md's own per-slice estimate for a much SMALLER unit (a
single primitive, e.g. Slice 2's `relabel_index_entry` alone landed at 194)
-- design.md's own estimate for the FULL CLI verb + tests is 380-490 lines,
of which this objective (a strict subset: 4 of Slice 3's 18 tasks, plus one
review-driven addition) accounts for 335.

### Files touched (objective 3A, this run)

- `src/openkos/bundle/index.py` -- `_reject_markdown_link_delimiters` +
  `_LABEL_UNSAFE_CHARS_RE`, call-site wiring, `_reject_newline` field-name
  fix, docstring updates
- `src/openkos/cli/main.py` -- new `backfill-source-titles` command
  (Phase A + preview + confirm gate only; Phase B is a `TODO` placeholder)
- `tests/unit/bundle/test_index.py` -- one new parametrized guard test
- `tests/unit/cli/test_backfill_source_titles.py` -- new file, 7 cases
- `openspec/changes/source-title-backfill/tasks.md` -- 3.1-3.4 ticked,
  new 3.4b added and ticked

### Not done (next run, objective 3B)

Tasks 3.5-3.18: Phase B write orchestration (`relabel_index_entry` wiring,
`write_atomic(index.md)` -> each staged Source (sorted) ->
`write_atomic(log.md)`, one `_autocommit`, design D6's index-first
ordering), atomicity/partial-failure reporting, idempotence and cross-run
invariants, the malformed-resource and hand-edited-first-line CLI-level
integration tests, and the Slice 3 quality gate (branch coverage,
whole-suite regression confirmation).

### Objective 3B (this run): tasks 3.5-3.8

Scope was hard-bounded to Phase B write orchestration, `relabel_index_entry`
wiring, one `log.md` entry, one `_autocommit`, and partial-failure
reporting -- tasks 3.9-3.18 (idempotence, cross-run invariants, malformed-
resource/hand-edited-first-line CLI integration, Slice 3 quality gate) are
untouched, as scoped.

**Phase B implemented in `backfill_source_titles_cmd`** (`src/openkos/cli/main.py`):
both write-bound texts -- `new_index_text` (one `relabel_index_entry` call
per staged Source) and `new_log_text` (one `insert_log_entry` call) -- are
computed in a NEW pre-preview `try` block, so a malformed `index.md` refuses
before any write, before the preview is even printed (design D6). On
confirm, Phase B writes in this exact order, with an inline comment at the
site explaining WHY, so a future reader does not "fix" it to match
`backfill-sensitivity`'s reverse order: `write_atomic(index.md)` first (the
classifier keys on a Source document's own `title`, so once a document is
written a mid-sweep failure before `index.md` lands would leave that
Source's bullet permanently unrevisitable on re-run -- index-first is the
only order whose partial state a re-run repairs), then each staged Source
document (already `concept_id`-sorted by `SourceTitleBackfill`'s own
invariant -- no re-sort needed), then `log.md`. Each path is appended to
`landed` only AFTER its own `write_atomic` call returns. On success, exactly
one `_autocommit(root, landed, "openkos: backfill-source-titles")` runs. On
a Phase-B write failure, nothing already landed is rolled back; the failure
message names every path in `landed`, matching `backfill-sensitivity`'s
message shape verbatim (`"... failed while writing the backfill -- {exc}.
Already written (left partially retitled, not rolled back): {paths}."`).

**Tests** (`tests/unit/cli/test_backfill_source_titles.py`, 4 new cases,
plus a new `_register_index_entry` helper since `_write_source` bypasses
`ingest` and never touches the catalog on its own):
- `test_index_bullet_relabeled_and_unstaged_bullets_untouched` -- a staged
  and a curated Source, both with registered index bullets; asserts the
  staged bullet's label changed to the new title and the curated bullet's
  label is untouched.
- `test_multi_source_run_produces_one_log_entry_and_one_commit` -- two
  distinguishable staged Sources; asserts exactly one new `log.md` entry
  and spies on `_autocommit` (mirroring `test_purge.py`'s existing
  precedent) to assert it is called exactly once with every changed path.
  Chosen over inspecting a real git commit because `_autocommit` silently
  no-ops when git identity is unset (`has_git_identity`), and spying is a
  smaller, faster, already-precedented unit-test seam than adding a second
  `_init_workspace_git`-style fixture just for this one assertion.
- `test_write_order_is_index_then_sources_then_log` -- the ordering test
  Lesson 5 demands: two distinguishable staged Sources, `write_atomic`
  mocked to fail on its 2nd call. Under the implemented (index-first) order
  that 2nd call is the first Source's write, so the assertion checks
  `index.md` already carries BOTH new labels while NEITHER Source document
  has changed. Under a reversed (sources-first) order, that same 2nd call
  would be the SECOND Source's write, meaning the first Source would
  already be written and `index.md` would still be unmodified -- the
  opposite of what is asserted, so this test would fail under the reverse
  order, satisfying the "would this fail if reversed" bar.
- `test_mid_sweep_write_failure_names_the_landed_paths` -- three staged
  Sources, `write_atomic` mocked to fail on its 4th call (after
  `index.md` + the first two Sources land); asserts the exit code, the
  exact failure-message prefix, and the exact `landed` path list
  (`bundle/index.md, bundle/<first>.md, bundle/<second>.md`) in the
  message, per Lesson 2 (asserting the SPECIFIC message of the guard being
  exercised, not a generic `match=`).

A shared `_monkeypatch_failing_write(monkeypatch, fail_at)` helper backs
both failure tests, replacing what would otherwise be two near-identical
`nonlocal call_count` closures.

### Quality gate — verbatim evidence (objective 3B, this run)

```
uv run pytest                 -> 2924 passed
uv run ruff check .           -> All checks passed!
uv run ruff format --check .  -> 160 files already formatted
uv run mypy .                 -> Success: no issues found in 160 source files
```

### Changed-line budget (objective 3B, this run)

```
git diff --numstat
 src/openkos/cli/main.py                        73  8
 tests/unit/cli/test_backfill_source_titles.py 136  4
```
**Total: 221 changed lines** (`git diff --stat` insertions+deletions),
21 lines over the 200-line hard cap after several compaction passes
(module/function docstrings tightened, a redundant assertion loop replaced
with two direct string checks, the two failure-simulation closures
deduplicated into one shared helper, and the originally-planned real-git-
commit inspection replaced with a much smaller `_autocommit` spy).
Flagged, not silent: **no spec-required scenario was cut.** The four new
tests map 1:1 onto task 3.5's three named scenarios (index bullet update,
one log entry/one commit, write order) plus task 3.7's one scenario
(mid-sweep failure) -- exactly what was assigned, no more.

### Files touched (objective 3B, this run)

- `src/openkos/cli/main.py` -- Phase B write orchestration in
  `backfill_source_titles_cmd`: pre-preview `index.md`/`log.md` text
  preparation, the index-first write loop with its `landed` accumulator,
  the partial-failure message, one `_autocommit` call
- `tests/unit/cli/test_backfill_source_titles.py` -- `_register_index_entry`
  and `_monkeypatch_failing_write` helpers; 4 new test cases
- `openspec/changes/source-title-backfill/tasks.md` -- 3.5-3.8 ticked

### Not done (next run, objective 3C)

Tasks 3.9-3.18: idempotence, cross-run invariants (`raw/` untouched,
historical `log.md` entries preserved, slug/filename/Concept ID unchanged,
`.openkos/*.db` untouched), the malformed-resource and hand-edited-first-
line CLI-level integration tests, and the Slice 3 quality gate (branch
coverage, whole-suite regression confirmation).

### Objective 3C (this run): tasks 3.9-3.18 — SLICE 3 COMPLETE

No production code was changed in `src/openkos/cli/main.py` or
`src/openkos/bundle/{source_titles,index}.py`: every test added for tasks
3.9-3.15 passed against the code already landed by 3A/3B. Tasks 3.11, 3.13,
and 3.15 were each treated as "confirm no gap, only fix if the test reveals
one" per this run's own instructions — none did.

**Tests added** (`tests/unit/cli/test_backfill_source_titles.py`, 5 new
cases):
- `test_immediate_rerun_after_a_successful_sweep_is_a_no_op` (3.9) — a
  successful `--auto` run, then a second `--auto` run on the same
  workspace; asserts the second reports "nothing", exits 0, and
  `snapshot_with_mtime` is byte-and-mtime identical before/after the second
  run (stronger than "exit 0", per Lesson 5), and `_autocommit` is spied to
  confirm zero calls.
- `test_invariants_preserved_across_a_confirmed_run` (3.10) — one bundle
  exercising all four invariants together, using a multi-Source fixture (two
  distinguishable staged Sources, one curated, one warned, per Lesson 3, so
  "untouched" is falsifiable): `raw/` file bytes captured before/after;
  three stub `.openkos/*.db` files captured before/after; a pre-existing
  `log.md` entry (inserted via `bundle.log.insert_log_entry` before the run,
  carrying the OLD title verbatim) asserted still present after the run,
  proving history is not rewritten (not merely that the file grew); the
  `bundle/sources/` directory listing and both staged Sources' own file
  paths asserted unchanged, proving slug/filename/Concept ID stability.
- `test_malformed_resource_is_warned_and_never_staged` (3.12) — a
  well-formed staged Source alongside a `_warned` fixture (`resource`
  outside `raw/`); asserts the exact warned-bucket line
  (`"! bundle/sources/warned.md (warned: resource-malformed)"`, per Lesson 2:
  the specific reason string, not a substring match that several warned
  reasons could satisfy) and byte-identical content before/after.
- `test_hand_edited_first_line_is_refused_not_overwritten` (3.14) — two
  staged Sources; one has its on-disk first body line hand-edited away from
  `# {current_title}` after `_write_source` builds it (still passes
  mechanical classification, since classification never reads the body).
  Asserts the exact warned line (`"warned: heading-mismatch"`) and
  byte-identical content, while the OTHER Source is still retitled
  normally in the same run (proving refusal is per-Source, not
  sweep-wide).

**Test-validity check (Lesson 1), done by mutation, then reverted**: with
the hand-edited-first-line test in place, temporarily replacing
`resolve_source_title_backfill`'s `except ValueError: warn(...,
"heading-mismatch")` with a no-op that stages the mismatched content
anyway made `test_hand_edited_first_line_is_refused_not_overwritten` fail
with a clear assertion diff (the warned line was absent from output); the
file was restored immediately after (`git diff --stat` on
`bundle/source_titles.py` confirmed clean). This corroborates that the new
invariant/refusal assertions are load-bearing, not vacuous, for both 3.11
and 3.15's "confirm, don't just assume" language.

### Quality gate — verbatim evidence (objective 3C, this run)

```
uv run pytest --cov=openkos.cli.main --cov-branch
src/openkos/cli/main.py    2364    109    774     25    96%
Required test coverage of 90.0% reached. Total coverage: 95.67%
2928 passed in 95.03s (0:01:35)

uv run pytest                 -> 2928 passed in 87.08s (0:01:27)
uv run ruff check .           -> All checks passed!
uv run ruff format --check .  -> 1 file reformatted, then 160 files already formatted
uv run mypy .                 -> Success: no issues found in 160 source files
```
Uncovered branches remaining in `backfill_source_titles_cmd` (the
`require_workspace` refusal at line 3812, and the `raw-unreadable`/
`raw-undecodable` exception branches at 3829-3841) are pre-existing gaps
from objectives 3A/3B, not newly introduced here; both the whole-file
(96%) and whole-suite (95.67%) branch coverage already clear the 90% floor,
so no additional test was added purely to chase 100% against the line
budget.

### Changed-line budget (objective 3C, this run)

No changes to `src/`. Only `tests/unit/cli/test_backfill_source_titles.py`
changed: 4 import lines added (`datetime.date`, `openkos.bundle.log`,
`tests.unit.cli.conftest.snapshot_with_mtime`) plus one docstring line, and
one ~125-line appended block of 5 new test functions. Well under the
200-line cap; no spec-required scenario (the four 3.10 invariants, 3.9's
idempotence, 3.12's malformed-resource warning, 3.14's hand-edited-first-
line refusal) was cut.

### Files touched (objective 3C, this run)

- `tests/unit/cli/test_backfill_source_titles.py` — 5 new test cases (3.9,
  3.10 combined, 3.12, 3.14); no new helpers needed, reused `_staged`,
  `_skipped`, `_warned`, `_register_index_entry`
- `openspec/changes/source-title-backfill/tasks.md` — 3.9-3.18 ticked;
  Slice 3 marked complete
- `src/openkos/cli/main.py`, `src/openkos/bundle/source_titles.py`,
  `src/openkos/bundle/index.py` — untouched, per this run's explicit
  do-not-modify scope; no gap was found that would have required touching
  them

## Change status: source-title-backfill is COMPLETE

All three slices (1, 2, 3) are now done: Slice 1's pure core, Slice 2's
`relabel_index_entry`, and Slice 3's `backfill-source-titles` CLI verb
including idempotence, cross-run invariants, malformed-resource handling,
hand-edited-first-line refusal, and the full quality gate. Nothing remains
in `tasks.md` unticked. Next step is `sdd-verify` / archive, not further
`sdd-apply` objectives.
