# Tasks: Bound the Identity Stage's Call Budget

Strict TDD. Tests are written RED first (order fixed by design's Testing
Strategy table), then made GREEN by production code, then REFACTOR passes
(quality gates) close each slice. Do not reorder RED tests relative to each
other — later tests depend on earlier ones establishing the seam.

Two commit types recur throughout and must not be confused:

- **Assertion contracts** (existing pinned literals, stdout, prompt
  sequences) — MUST NOT be edited. Below-cap output stays byte-identical.
- **Patch-target conversions** (the 65 sites below) — MUST be edited. These
  are mechanical monkeypatch-target renames, not assertion changes.

## Slice boundary

**Slice A** (tasks 1-20): cap + ranking + `CandidateGroupReport` +
`find_candidates_report` + `candidate_group_truncation_notice` +
`curate`'s Identity disclosure. Stands alone, green and complete, without
Slice B.

**Slice B** (tasks 21-29): `duplicates` / `adjudicate` disclosure — a pure
addition over Slice A's report entry point. Ships in this change (spec's
never-silent requirement is not met until B lands), recommended as a
second chained PR.

---

## Slice A — cap, ranking, report, Identity disclosure

### RED: resolution-layer unit tests (`tests/unit/resolution/test_candidates.py`)

Sequential — each test after #1 depends on the constant/seam the prior
step establishes existing (even if not yet implemented, the fixtures and
imports build on each other).

1. [x] **Test: `_MAX_CANDIDATE_GROUPS == 50`** — house idiom   (`test_contradiction.py:179`).
   Satisfies: entity-resolution spec, "Bounded Candidate-Group Output Per
   Call".
2. [x] **Test: below-cap `produced == retained`, groups identical to today's   unbounded output.**
   Satisfies: entity-resolution spec, "Below-cap corpus is unaffected".
3. [x] **Test: over-cap module-scoped fixture (cap+10 HIGH pairs) —   `retained == cap`, `produced == cap+10`.** Build real files via
   `tmp_path_factory`; do NOT monkeypatch the constant.
   Satisfies: entity-resolution spec, "Adjudication call count never
   exceeds the cap".
4. [x] **Test: HIGH fills before LOW; global tier priority beats earlier   `okf_type`.**
   Satisfies: entity-resolution spec, "HIGH groups fill the cap before any
   LOW group is considered", "HIGH-tier ranking outranks a LOW-tier group
   in an earlier type".
5. [x] **Test: HIGH-only excess = first 50 by `(okf_type, member_ids)`;   LOW equal-score tie-break.**
   Satisfies: entity-resolution spec, "HIGH-only excess is tie-broken by
   (okf_type, member_ids)", "LOW-tier ties are broken deterministically".
6. [x] **Test: retained slice returned in canonical order, not rank order;   two calls over an unchanged bundle produce identical results.**
   Satisfies: entity-resolution spec, "Retained groups keep the module's
   existing output order", "Repeated calls over an unchanged bundle
   truncate identically".
7. [x] **Test: ACRONYM/LOW pair appears once, under ACRONYM, with the cap   engaged.**
   Satisfies: entity-resolution spec, "ACRONYM/LOW dedup holds when the
   cap is engaged".
8. [x] **Test: `find_candidates`' HIGH slice is a prefix of   `find_exact_title_groups`'s output.**
   Satisfies: design's amended equivalence contract (see task 13).

### GREEN: implement the resolution layer (`src/openkos/resolution/candidates.py`)

9. [x] **Add `_MAX_CANDIDATE_GROUPS: Final[int] = 50`.** Makes RED test 1   pass.
10. [x] **Implement `CandidateGroupReport(groups, produced, retained)`    (frozen dataclass), `_cap_rank_key`, and `find_candidates_report`.**
    `_cap_rank_key` MUST branch on `group.tier`: `score = float(trigger)`
    only for `Tier.LOW`, placeholder `0.0` for HIGH/ACRONYM — an
    unconditional `float()` raises `ValueError` on a HIGH normalized key
    or an ACRONYM string. Rank, slice to `[:_MAX_CANDIDATE_GROUPS]`,
    re-sort the retained slice into canonical order before returning.
    Makes RED tests 2-8 pass.
11. [x] **`find_candidates` delegates to `find_candidates_report(...).groups`**    — signature and return type unchanged (D1).
12. [x] **Implement `candidate_group_truncation_notice(report) -> str | None`**    in `candidates.py` (D3) — `None` unless `produced > retained`; wording
    `f"{retained} of {produced} candidate group(s) shown (cap reached)"`.
    Satisfies: entity-resolution spec, "Truncation Is Never Silent".

13. [x] **Amend `find_exact_title_groups`'s equivalence docstring**    (`candidates.py:296-306`) to state the equivalence holds verbatim only
    while the cap does not bind, and that the retained HIGH set is always
    a prefix of `find_exact_title_groups`'s output in the same order.
    **No test enforces this** — the pinned test at `test_candidates.py:636`
    passes with or without the amendment — so this is a standalone
    docstring-truth task; do not assume RED test 8 covers it.

14. [x] **Update `src/openkos/resolution/__init__.py`**: export    `CandidateGroupReport`, `find_candidates_report`,
    `candidate_group_truncation_notice`; update the public-surface
    docstring. Can run in parallel with task 13 (independent files).

### RED: CLI unit tests (`tests/unit/cli/test_curate.py`)

15. [x] **Test: over-cap Identity probe — notice printed before the cost    line, `probe.llm_calls == 50`.**
    Satisfies: curate-command spec, "Identity Cost Line Discloses
    Truncation" (cap-reached scenario), "Above-cap Identity cost line
    reflects the bounded count".
16. [x] **Test: below-cap Identity probe — no notice, cost line    byte-identical to pre-change wording.**
    Satisfies: curate-command spec, "Below-Cap Cost-Line Output Is
    Byte-Identical To Pre-Change Behavior".

### GREEN: implement the CLI layer (`src/openkos/cli/curate.py`)

17. [x] **`_identity_probe` calls `find_candidates_report` and sets    `StageProbe.notice` from `candidate_group_truncation_notice`** when
    truncation occurred; `probe.llm_calls` becomes `report.retained`.
    Makes RED tests 15-16 pass. `run_curate` already echoes
    `StageProbe.notice` unconditionally (`curate.py:924-925`) — no new
    plumbing.

### Mechanical patch-target conversions (NOT assertion changes)

18. [x] **Convert the 13 monkeypatch sites in `test_curate.py`** (lines 832,    874, 918, 980, 1072, 1465, 1511, 1556, 1606, 1655, 1696, 1740, 1869):
    `openkos.cli.curate.find_candidates` → `...find_candidates_report`,
    returning a `CandidateGroupReport`. One-line target-string edit per
    site; every pinned assertion, stdout literal, and prompt sequence
    stays untouched. Skipping any site leaves it patching a name nothing
    reads — the real function runs and
    `test_identity_probe_reads_find_candidates` (`test_curate.py:1065`)
    fails on an empty-queue workspace; other unpatched sites desync the
    pinned stdin prompt sequence.

### Slice A close-out

19. [x] **REFACTOR / verify: Slice A stands alone.** Run    `uv run pytest tests/unit/resolution/test_candidates.py
    tests/unit/cli/test_curate.py` green with Slice B not yet touched.
20. [x] **Slice A quality gates**: `uv run pytest` (full suite, confirms no    regression elsewhere), `uv run ruff check . && uv run ruff format
    --check .`, `uv run mypy .` (strict). Open/land PR #1 per
    `chained-pr` skill if the 400-line guard is exceeded (it is — see
    Review Workload Forecast below).

---

## Slice B — `duplicates` / `adjudicate` disclosure

Depends on Slice A's `find_candidates_report` / `candidate_group_truncation_
notice` existing. The two verbs (`duplicates`, `adjudicate`) can be done in
either order relative to each other — their RED/GREEN/conversion units are
independent (different call sites, different test files) — but each verb's
own RED→GREEN→conversion sequence is sequential.

### `duplicates` (`src/openkos/cli/main.py:7733`)

21. **RED: `test_duplicates.py` — over-cap bundle emits the truncation
    notice.**
    Satisfies: entity-resolution spec, "Truncation Is Never Silent"
    (duplicates as a caller).
22. **RED: `test_duplicates.py` — below-cap bundle emits nothing.**
23. **GREEN: `duplicates` switches to `find_candidates_report`, echoes
    the notice to stderr; docstring at `main.py:7699-7733` amended — no
    longer claims to return "every" group.** Makes tests 21-22 pass.
24. **Convert the 5 monkeypatch sites in `test_duplicates.py`** (lines
    189, 223, 257, 346, 369): `openkos.cli.main.find_candidates` →
    `...find_candidates_report`.

### `adjudicate` (`src/openkos/cli/main.py:7910`)

25. **RED: `test_adjudicate.py` — over-cap bundle emits the truncation
    notice.**
26. **RED: `test_adjudicate.py` — below-cap bundle emits nothing.**
27. **GREEN: `adjudicate` switches to `find_candidates_report`, echoes
    the notice to stderr; docstring at `main.py:7835` amended.** Makes
    tests 25-26 pass.
28. **Convert the 47 monkeypatch sites in `test_adjudicate.py`** (lines
    292 through 2898): same rename as task 24. The shared
    `_fake_find_candidates` / `_recording_find_candidates` / local
    `fake_find` helpers wrap their list in a `CandidateGroupReport`, so
    most individual sites reduce to a one-line target-string edit once
    the shared helpers are updated.

Note: `tests/unit/cli/test_confidential_local_exemption.py` needs no
conversion (0 sites, unaffected).

### Slice B close-out

29. **Slice B quality gates**: `uv run pytest` (full suite, both slices),
    `uv run ruff check . && uv run ruff format --check .`, `uv run mypy .`
    (strict; coverage gate `fail_under = 90`, branch coverage). Confirm the
    spec's never-silent requirement is met end-to-end (`curate`,
    `duplicates`, `adjudicate` all disclose truncation). Open/land PR #2
    per `chained-pr` skill, targeting PR #1's branch per that skill's
    stacked-PR convention.

---

## Review Workload Forecast

- **Estimated changed lines**: ~700-860 (production ~200 including this
  codebase's heavy docstrings; tests ~500-660). This is the design's own
  forecast, corrected upward twice already (proposal: 300-500; first
  design draft: 500-620, which omitted the monkeypatch seam entirely) —
  do not shade it down further.
- **400-line PR guard**: exceeded. 700-860 is well above the 400-line
  single-PR threshold that triggers the `chained-pr` skill.
- **Chained PRs recommended: Yes.** Two slices, as scoped above: Slice A
  (cap + ranking + report + Identity disclosure, ~13 conversions) stands
  alone green; Slice B (`duplicates`/`adjudicate` disclosure, ~52
  conversions) is a pure addition over A. Use the `chained-pr` skill's
  Stacked PRs to main strategy — PR #2 targets PR #1's branch.
- **1500-line session review budget**: 700-860 stays inside the 1500-line
  budget (single review pass across both slices remains affordable), but
  is close to the guard's midpoint. No stop is required against the
  1500-line ceiling.
- **Decision needed before apply**: confirm the two-PR split (Slice A /
  Slice B) is acceptable before `sdd-apply` starts, since it changes how
  the apply agent sequences commits and where it pauses for review. No
  other blocking decision is open — the design's Open Questions section
  is empty.

## Quality gates (run at the end of each slice, per above)

- `uv run pytest`
- `uv run ruff check . && uv run ruff format --check .`
- `uv run mypy .` (strict = true; coverage gate `fail_under = 90`, branch
  coverage)

## Conventional commit scope

Use `resolution` for `resolution/candidates.py` changes and `cli` for
`cli/curate.py` / `cli/main.py` changes.

`resolution` is an established scope in this repo, not an invention: it
appears 5 times in the commit history, most recently in `1dce91c`
(`fix(resolution): define the relation vocabulary the suggester is
handed`) and `9dcefa2`. `AGENTS.md:48` lists the scopes as a roadmap
snapshot and states outright that "the list grows as code lands", so its
omission of `resolution` reflects the list's age, not a prohibition.

Prefer splitting the cap itself (`resolution`) from the disclosure
surface (`cli`) where the commit boundary is natural. Do not collapse
resolution-layer work into `cli` to avoid the scope.
