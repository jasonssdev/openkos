# Design: Lifecycle Application Service

Issue [#918](https://github.com/jasonssdev/openkos/issues/918), lifecycle slice —
the third and last bounded context. Applies ADR-0018; no new ADR (see the gate
below). Every claim here was checked against `src/openkos/cli/main.py` at design
time; three of them correct the exploration, and the corrections change the slice
plan.

## Three corrections the evidence forced

The proposal's five decisions (D1–D5) stand. Three of the *facts* underneath them
do not.

**C1 — `purge`'s gate is not boolean, and `--auto` cannot reach it.**
`main.py:7316–7337` is a typed-**phrase** challenge: `_purge_confirm_phrase`
(`6500`) builds `purge <id>` / `purge <id> (<N> concepts)`, `--confirm-phrase`
supplies it non-interactively, a TTY is prompted with
`Type '<phrase>' to proceed`, a non-TTY is refused naming **`--confirm-phrase`**,
and a mismatch aborts. The code comment is explicit: "EXACT match only -- no
--auto bypass (irreversible)". The exploration attributed `forget`'s gate
(`6344/6351`, inside `forget`'s body, `5918–6499`) to `purge`. D1's *shape* is
unaffected — see the union below, which still has exactly two variants — but a
`BooleanConfirmation` for `purge` would have silently invented an `--auto` bypass
on the one irreversible verb.

**C2 — `_execute_single_unmerge` is not a presentation-free Phase B.**
It spans `10606–11044` (439 lines) and its own docstring calls it "the full Phase
A / preview / confirm-gate / drift-guard / Phase B machinery". It contains the
preview echoes (`10893–10918`), the confirm gate (`10920–10929`), the failure
echoes (`10888`, `11015`), the success echo (`11020`) and `_autocommit`
(`11031`). "Zero `typer.echo` in `10606–10887`" is true of that *sub-range* only.
D3 (a full public pair) is therefore right for a *stronger* reason than stated —
but it is a three-way split of a 439-line function, not a promotion, and it does
not fit one slice.

**C3 — the adjudicate injection seam is 4 dangerous sites, not 96.**
Measured: `tests/unit/cli/test_adjudicate.py` holds **154** string-literal
`"openkos.cli.main.X"` targets, broken down as `adjudicate_candidates` 81,
`find_candidates_report` 65, `merge_core` 2, `prepare_merge` 2,
`_reconcile_merged_survivor` 2, `OllamaClient` 2. The first two are the
**discovery half's** collaborators: both `_run_adjudicate_apply` (`2643`) and
`_run_adjudicate_apply_same` (`2845`) take `results: Sequence[AdjudicatedCandidate]`
as a parameter, so neither name is reachable from the code that relocates. They
never move. `OllamaClient` stays for the query slice's D1 reason (118 sites
repo-wide). `_reconcile_merged_survivor` stays — see D4 below. That leaves
`merge_core` ×2 and `prepare_merge` ×2, and those cannot be deferred to a later
slice (they break the moment Slice 1 lands), so the proposal's Slice 6 has no
content of its own.

## Technical Approach

Same three-actor shape both predecessors shipped, with the confirmation gate as
the one new seam:

```
  cli/main.py (adapter)                    application/lifecycle.py (service)
  ---------------------------------        -----------------------------------
  require_workspace, resolve ids  ──ids──▶  prepare_X()            [pure, reads]
                                  ◀─PreparedX + ConfirmationRequest
  render the preview from PreparedX
  hard refusal gates  (adapter, exit 1)     ── never a ConfirmationRequest (D2)
  if not auto and prepared.review:  ──────── the DECISION to ask stays here
      confirm(request.prompt) / prompt      the WORDS come from the request
      non-TTY -> echo request.non_tty_refusal, exit 1
      typed   -> request.matches(answer) or echo request.mismatch_abort, exit 1
  _reject_drifted_targets(drift_targets)
                                  ──────▶   X_core()              [writes only]
                                  ◀─XResult─
  success echo, _autocommit, _echo_commit_disclosure,
  _refresh_derived_after_write, exit codes
```

The service never renders, never prompts, never calls `sys.stdin.isatty()`, never
touches `openkos.vcs`, and never runs `_autocommit`. **The adapter keeps deciding
*whether* to ask; the service decides *what is asked and what counts as a
match*.** That split is what makes byte-identity cheap: the five `if not auto and
cfg.review:` lines stay exactly where they are, character for character, and only
the string literals inside them become field reads.

## Architecture Decisions

### D1 — the union is two frozen dataclasses with a `Literal` tag, in a new `application/consent.py`

```python
# src/openkos/application/consent.py  -- no typer, no rich, no openkos.cli

@dataclass(frozen=True)
class BooleanConfirmation:
    prompt: str                   # exact typer.confirm text
    bypass_flag: str | None       # "--auto", or None when the gate has no bypass
    non_tty_refusal: str | None   # exact stderr line, or None when no refusal arm exists
    kind: Literal["boolean"] = "boolean"


@dataclass(frozen=True)
class TypedChallengeConfirmation:
    prompt: str                   # "Type 'purge x (3 concepts)' to proceed"
    expected: str                 # "purge x (3 concepts)" | "7"
    supplying_flag: str           # "--confirm-phrase" | "--confirm-count"
    non_tty_refusal: str
    mismatch_abort: str
    match_mode: Literal["exact", "strip-then-exact"] = "exact"
    kind: Literal["typed-challenge"] = "typed-challenge"

    def matches(self, response: str) -> bool:
        candidate = response.strip() if self.match_mode == "strip-then-exact" else response
        return candidate == self.expected


ConfirmationRequest = BooleanConfirmation | TypedChallengeConfirmation
```

**The five verbs, reproduced field by field** (every string below is verbatim
from the working tree):

| Verb | Variant | `prompt` | flag | non-TTY refusal / abort |
|---|---|---|---|---|
| `merge` (`10182`) | boolean | `Proceed with these changes?` | `--auto` | `openkos merge: refusing to write without confirmation -- stdin is not a TTY; re-run with --auto.` |
| `unmerge` (`10498`, `10920`) | boolean | same | `--auto` | same, `openkos unmerge:` |
| `forget` (`6344`) | boolean | `Delete {N} concepts?` (`--scope source`) else `Proceed with these changes?` | `--auto` | same, `openkos forget:` |
| `adjudicate --apply` (`2759`) | boolean | `Merge {absorbed} into {survivor}? [y/N]` | `None` | `None` |
| `purge` (`7316`) | typed, `exact` | `Type '{phrase}' to proceed` | `--confirm-phrase` | `... refusing to purge -- stdin is not a TTY; re-run with --confirm-phrase.` / `... aborted -- confirmation phrase did not match exactly; nothing was written.` |
| `adjudicate --apply-same` (`3045`) | typed, `strip-then-exact` | `Type the eligible count ({total}) to proceed` | `--confirm-count` | `... refusing to apply -- stdin is not a TTY; re-run with --confirm-count.` / `... aborted -- confirmation count did not match exactly; nothing was written.` |

`bypass_flag=None, non_tty_refusal=None` is how one boolean variant covers
`adjudicate --apply`'s per-item walk without the service learning that
`curate._confirm` exists: both `None`s say "this gate has no bypass and no
refusal arm", and the adapter routes it to the validating `[y/N]` loop it already
uses (`curate.py:684`). `match_mode` exists because the two typed gates genuinely
differ — `3057` compares `typed_count.strip()`, `7331` compares the raw string —
and a policy re-derived at a call site is a policy that drifts (the ingest
design's own words about `lost_in_staging`).

**Rejected — a `Protocol`.** It describes behaviour, not data, so an adapter
cannot exhaustively enumerate variants and mypy-strict cannot check that it did.
Worse, any object with a `prompt` would structurally satisfy a count gate, which
is the exact defeat D1 exists to prevent. **Rejected — one untyped shape with an
optional `expected_count`.** Same defeat, plus `None` becomes load-bearing.
**Rejected — a `granted`/`response` field on the request.** The request is the
question; the answer is the adapter's value. One object carrying both makes a
hard refusal expressible as "a request that was granted", which D2 forbids by
construction. `match`-on-`kind` is how the adapter *consumes* the union, not an
alternative to it: with `assert_never` in the default arm, a future third variant
is a type error at every renderer instead of a silent fall-through.

**Why `consent.py` now, not `lifecycle.py` later.** The query slice named two
gates (`main.py:17454–17468`, `17469–17478`) that will need this type. If it
lives in `lifecycle.py`, `application/query.py` must import
`application/lifecycle.py` to use it — a service→service dependency for a type
neither owns, and ADR-0018 makes `application/*` modules siblings, not a
hierarchy. Moving it afterwards renames a public type that MVP-3 adapters will by
then import. Against that, `rules.tasks`'s "create a package only when its code
arrives" is satisfied: `consent.py` arrives in Slice 1 with its complete content
and a live consumer, which is not empty scaffolding. It costs nothing to guard —
`test_layering.py` already iterates `application/*.py`. If the query gates never
adopt it, the downside is a 60-line module with one importer.

### D2 — hard refusals never become requests, and the layering guard is what proves it

`forget`'s Gate 1 (`6315–6338`) and `purge`'s rails 1–5 (`7211–7314`) are
refusals independent of any human answer. They stay in the adapter, verbatim,
*before* the gate, and they are structurally unable to become consent because
neither dataclass has a `granted`, `force`, or `override` field anywhere in the
union. A new `tests/unit/application/test_lifecycle_seams.py` asserts the
absence by name, so a later field addition cannot quietly re-open it.

### D3 — the service returns typed data; the two already-string purge notices move unchanged

Default posture, everywhere: `stage_derived_objects`'s. The service returns typed
plan/disclosure data and **every `typer.echo` moves verbatim to the call site** —
~20 for `forget`, ~25 for `purge`, the batch loop's for `adjudicate`. That is what
kept ~290 output-text assertions unmodified through the ingest slice, and it is
what D4 asks for: the *templates* end up single-defined in `lifecycle.py`, no
adapter re-authors the IRREVERSIBLE wording, and the 76 `test_purge.py` runner
tests plus full-stream goldens are the proof.

The one exception is not an exception to the rule but a consequence of it:
`_purge_dropped_store_notice` (`6679`) and `_purge_residual_store_notice`
(`6753`) **already return `str | None` today**. They move as-is. Rewriting them
into typed tokens would mean re-authoring exactly the safety-critical erasure
wording D4 protects, to buy a consumer that does not exist — the same trade the
ingest design rejected for typed notice tokens.

### D4 — `_reconcile_merged_survivor` stays adapter-side; only the *decision* moves

`_reconcile_merged_survivor` (`9864`) builds `_chat_client(cfg)` — a concrete
backend — and catches `OllamaError`. Both are forbidden under
`test_application_modules_bind_no_concrete_llm_backend`. `_reconcile_planned`
(`2428`), a pure predicate and the #688 single source of truth, moves; the
execution and its `typer.echo` notice (`_apply_reconciliation`, `2495`) stay.
This is the query slice's D1 hybrid, applied to a third asymmetric name, and it
is why the two `_reconcile_merged_survivor` patch sites need no repoint.

### D5 — `_snapshot_read` is promoted to `fsio.snapshot_read`; `main` keeps a delegator

`prepare_merge` calls `_snapshot_read` (`main.py:595`) four times plus once per
bundle file, and cannot import `openkos.cli`. The shipped precedent is `_slugify`
(`3201`), promoted to `bundle.source_titles.slugify` for this exact reason, with
`main._slugify` left as a one-line delegator "rather than a duplicate that could
silently drift". Follow it: `snapshot_read` moves to `openkos/fsio.py` (a leaf
module `application/` may already import), `lifecycle.py` imports it there, and
`main._snapshot_read` becomes `return fsio.snapshot_read(path)`.

Deleting `main._snapshot_read` instead would break ~10 unrelated verbs' patch
sites (`ingest`, `relate`, `set-sensitivity`, `query --save`) that this change has
no business touching. Add `snapshot_read` to
`test_shared_write_helpers_are_never_forked`'s set — a 1-line change — because it
is now the first read helper shared *across* the layer boundary, and a fork of it
silently breaks the #318 one-observation invariant that every drift guard rests
on.

`_canonicalize_concept_id` (`5832`) and `_resolve_concept_path` (`5861`) move
into `lifecycle.py` and are bound back in `main` the way `application_ingest`
already does it (`main.py:4021–4024`).

## Interfaces / Contracts

```python
# src/openkos/application/lifecycle.py  -- synchronous; no typer/rich/openkos.cli/openkos.vcs

# S1 -- relocated verbatim from cli/main.py
class StackedBodyReport: ...            # main.py:9285
class PreparedMerge: ...                # main.py:9329  (unchanged fields)
class MergeResult: ...                  # main.py:9373
def prepare_merge(bundle_dir, index_path, log_path, survivor_path, absorbed_path,
                  survivor_canonical, absorbed_canonical, root, *, now) -> PreparedMerge
def merge_core(bundle_dir, index_path, log_path, prepared) -> MergeResult
def merge_drift_targets(layout, prepared) -> dict[Path, bytes]     # was _merge_drift_targets
def canonicalize_concept_id(concept_id) -> str
def resolve_concept_path(bundle_dir, concept_id) -> tuple[Path, str]
def boolean_confirmation(verb: str, *, prompt: str) -> BooleanConfirmation

# S2a / S2b -- unmerge, split from _execute_single_unmerge (main.py:10606-11044)
@dataclass(frozen=True)
class PreparedUnmerge:
    plan: bundle_merge.UnmergePlan
    new_log_text: str
    link_reversed_texts: dict[str, str]
    relation_reversed_texts: dict[str, str]
    provenance_restored_texts: dict[str, str]
    rewritten_files: list[str]
    relation_rewrite_files: list[str]
    provenance_rewrite_files: list[str]
    catalog_log_drifted: bool
    review: bool
    index_bytes: bytes; log_bytes: bytes; survivor_bytes: bytes
    rewrite_bytes: dict[str, bytes]

@dataclass(frozen=True)
class UnmergeResult:
    survivor_canonical: str; absorbed_canonical: str; committed_paths: list[str]

def prepare_unmerge(root, layout, survivor_path, survivor_canonical,
                    absorbed_canonical, *, now, cfg) -> PreparedUnmerge   # raises OSError/ValueError
def unmerge_core(layout, prepared) -> UnmergeResult                        # writes only
def unwind_step_preview_lines(entry, survivor_canonical) -> list[str]      # main.py:10558, pure

# S3 -- forget
ReferenceKind = Literal["link", "relation", "unverifiable"]

@dataclass(frozen=True)
class ReferenceDisclosure:
    member: str; referrer_id: str; kind: ReferenceKind
    relation_type: str | None; count: int          # #567 aggregation, service-owned

@dataclass(frozen=True)
class ForgetPlan:
    purge_ids: list[str]; total_removed: int
    new_index_text: str; new_log_text: str
    references: tuple[ReferenceDisclosure, ...]    # insertion order == render order
    resurrection_pairs: tuple[tuple[str, str], ...]
    surviving_refs: int; unverifiable_refs: int    # Gate 1's inputs -- NOT consent (D2)
    confirmation: BooleanConfirmation
    drift_targets: dict[Path, bytes]

def prepare_forget(root, layout, concept_id, *, scope, now, cfg) -> ForgetPlan
def forget_core(layout, plan) -> ForgetResult

# S4 -- purge
@dataclass(frozen=True)
class PurgeDisclosure:
    expunge_targets: tuple[str, ...]
    resource_warnings: tuple[str, ...]
    raw_absence: bool                              # renders main.py:7202-7206
    cascade_total: int | None                      # source scope only

@dataclass(frozen=True)
class PurgePlan:
    canonical_id: str; purge_ids: list[str]
    disclosure: PurgeDisclosure
    verified_refs: int; unverifiable_refs: int     # rail 1's inputs -- NOT consent
    confirmation: TypedChallengeConfirmation
    drift_targets: dict[Path, bytes]

def purge_confirm_phrase(canonical_id, purge_ids, scope) -> str            # main.py:6500
def prepare_purge(root, layout, concept_id, *, scope, now) -> PurgePlan
def dropped_store_notice(dropped) -> str | None                            # main.py:6679, verbatim
def residual_store_notice(undeleted) -> str | None                         # main.py:6753, verbatim

# S5 -- adjudicate
def prepare_one_merge(root, layout, index_path, log_path, group,
                      *, ordered_pair=None) -> PreparedMerge | None        # main.py:2364
def reconcile_planned(prepared, *, no_reconcile, reconcile=False) -> bool  # main.py:2428
def ordered_merge_pair(bundle_dir, member_ids) -> tuple[str, str, str]     # main.py:2206

@dataclass(frozen=True)
class BatchApplyPreview:
    previewed: tuple[PreviewedPair, ...]
    skipped_n_gt2: int; refused_stacked: int
    skipped_cross_source: int; skipped_cross_type: int
    confirmation: TypedChallengeConfirmation       # expected == str(len(previewed))

def preview_apply_same(root, layout, index_path, log_path, results, *,
                       include_cross_source, include_cross_type) -> BatchApplyPreview
```

**Staying adapter-side, by name**: `_reject_drifted_targets`, `_autocommit`,
`_refresh_derived_after_write`, `_echo_commit_disclosure`, `_require_member_baseline`,
`_format_merge_preview_line`, `_apply_reconciliation`, `_reconcile_merged_survivor`,
`_commit_one_merge`, `_echo_n_gt2_skip`, `_refused_stacked_line`, every
`vcs_git.*` call, `_purge_clean_live_index`/`_purge_clean_live_log`/
`_purge_rebuild_indexes`, and `curate._confirm`.

## Sequence: the confirm gate, both variants, both TTY states

```
 operator      cli/main.py (adapter)                application/lifecycle.py
    |                  |                                       |
    |                  |── prepare_X(...) ────────────────────▶ |
    |                  |◀─ PreparedX{..., confirmation} ─────── |   no prompt,
    |                  |                                        |   no isatty,
    |  ◀─ preview ─────|  render every line from PreparedX      |   no write
    |                  |
    |                  |── hard refusals (Gate 1 / rails 1-5) ──▶ exit 1, nothing written
    |                  |
    |                  |  BOOLEAN:  if not auto and prepared.review:
    |                  |     TTY ──▶ typer.confirm(req.prompt, abort=True)
    |  ── y/n ────────▶|          decline ──▶ Abort, exit 1, nothing written
    |                  |     non-TTY ──▶ echo req.non_tty_refusal (stderr), exit 1
    |                  |     bypass  ──▶ (skipped; window still re-validated below)
    |                  |
    |                  |  TYPED:    always (purge: no bypass exists)
    |                  |     --flag ──▶ answer = supplied
    |                  |     TTY    ──▶ answer = typer.prompt(req.prompt)
    |                  |     non-TTY──▶ echo req.non_tty_refusal (stderr), exit 1
    |                  |     if not req.matches(answer):
    |                  |            echo req.mismatch_abort (stderr), exit 1
    |                  |
    |                  |── _reject_drifted_targets(plan.drift_targets) ──▶ exit 3 on drift
    |                  |── X_core(...) ───────────────────────▶ |  writes only
    |                  |◀─ XResult ─────────────────────────── |
    |  ◀─ success ─────|  echo, _autocommit, _echo_commit_disclosure, _refresh_*
```

Two invariants this diagram pins. The refusal branch echoes to **stderr** and the
preview to **stdout** — the service supplies neither stream, so stream choice
cannot drift with the wording. And the drift guard runs on *every* path past the
gate, including the bypassed ones, because `--auto` skips the prompt but not the
window it stood in (`main.py:10682–10684`).

## Sequence: `adjudicate --apply-same`, the batch loop

```
  cli/main.py                                    application/lifecycle.py
      |                                                     |
      |── preview_apply_same(results, ...) ────────────────▶|  Pass 1:
      |                                                      |   per SAME 2-member group
      |                                                      |    ordered_merge_pair
      |                                                      |    prepare_one_merge
      |                                                      |    guardrail: stacked/cross-*
      |◀─ BatchApplyPreview{previewed, skipped_*, confirm} ──|   (no echo, no prompt)
      |
      |  for pair in previewed:  echo preview line + "  survivor: ..." (verbatim)
      |  echo f"Total: {len(previewed)}"
      |
      |  if len(previewed) == 0:  echo the "applied 0, skipped N (...)" summary, return 0
      |                            ^ BEFORE the gate -- an empty batch must never
      |                              refuse on non-TTY nor force typing "0"
      |
      |  TYPED gate on preview.confirmation  (expected == str(len(previewed)))
      |
      |  for pair in previewed:            Pass 2 -- re-resolve, never reuse Pass 1
      |      prepare_one_merge(ordered_pair=pair.ordered) ──▶|  pinned direction (#776)
      |      None -> skipped_already_merged += 1; continue    |
      |      _apply_reconciliation (adapter: LLM + notice)    |
      |      _reject_drifted_targets(merge_drift_targets(...))|
      |      _commit_one_merge  ->  merge_core + _autocommit ▶|
      |      on OSError/ValueError: echo partial summary, exit 1  (prior commits stand)
      |  _refresh_derived_after_write once, only if applied
```

Pass 2 re-preparing rather than reusing Pass 1's `PreparedMerge` is preserved
behaviour, not an optimisation to revisit: an earlier merge in the same batch can
absorb a later pair's member, and `ordered_pair` pins the direction the operator
actually consented to.

## The injection seam, and how a silent no-op is caught

**Inventory method, in the order that finds what the previous order missed:**

| Step | Command | Result |
|---|---|---|
| 1 | `rg -c '"openkos\.cli\.main\.[A-Za-z_]+"' tests/unit/cli/test_adjudicate.py` | 154 |
| 2 | `rg -o '"openkos\.cli\.main\.[A-Za-z_]+"' … \| sort \| uniq -c` | 81 / 65 / 2 / 2 / 2 / 2 — **only 4 relocate** |
| 3 | `rg -U 'monkeypatch\.setattr\(\s*(main\|cli_main\|module)\s*,\s*"[A-Za-z_]+"' tests/` | 40 repo-wide; 11 in the five lifecycle files, incl. `main._purge_confirm_phrase` — **invisible to steps 1–2** |
| 4 | `rg -U 'monkeypatch\.setattr\(\s*(main\|module)\s*,\s*[a-z_]+\s*,' tests/` | the computed-name form (ingest's trap). Expected 0 here — but a zero from step 3 alone is precisely what made the ingest survey wrong, so step 4 is run, not reasoned about |
| 5 | `rg -n 'from openkos\.cli\.main import' tests/unit/cli/test_merge_core.py` | 53 direct references — collect-time failure, self-detecting |

**Classify every site by what a no-op does.** A site is *safe* when a dead patch
removes a behaviour the test asserts, and *dangerous* when a dead patch restores
real behaviour the test suppresses:

| Site | Count | No-op consequence | Class |
|---|---|---|---|
| `"…main.merge_core"` | 2 | a **real merge runs** | **dangerous** |
| `"…main.prepare_merge"` | 2 | real Phase A runs; the injected failure never fires | **dangerous** |
| `main._purge_confirm_phrase` | 1 | the fake also edits a file to simulate drift; no drift ⇒ asserted exit 3 becomes exit 0 | safe |
| `main._snapshot_read` (racing) | 4 | same shape — the fake *causes* the drift being asserted | safe |
| `find_candidates_report`, `adjudicate_candidates`, `OllamaClient`, `_reconcile_merged_survivor` | 146 | n/a — target never moves | untouched |

**The mechanical guarantee.** `main` imports the service **module**, never its
names — `from openkos.application import lifecycle as application_lifecycle`,
called as `application_lifecycle.prepare_merge(...)`, exactly as
`application_ingest` is used today (`main.py:28`, `4027`). Consequently
`main.prepare_merge` / `main.merge_core` / `main._execute_single_unmerge` /
`main._purge_confirm_phrase` cease to exist as module attributes, `ruff` F401
forces the dead imports out, and a stale
`monkeypatch.setattr("openkos.cli.main.merge_core", …)` raises `AttributeError`
under pytest's default `raising=True`. The repointed target
`"openkos.application.lifecycle.merge_core"` *does* intercept, because the
adapter resolves the attribute on the module at call time.

**The one thing that would re-open the trap**: a compatibility alias
(`merge_core = application_lifecycle.merge_core`) in `main`. This design forbids
aliasing any relocated **callable**. `main` may bind back relocated **types** it
annotates with — the ingest precedent aliases `_DerivedPlan` plus three
functions (`main.py:4021–4024`); the three *function* aliases are the shape we do
not repeat, and the reason is that those three had zero patch sites while ours
have four dangerous ones. `main._snapshot_read` and `main._slugify` remain
delegators, not aliases, for the D5 reason.

**How a reviewer confirms each repointed site still intercepts**, without
re-reading the diff: (1) `test_lifecycle_seams.py` asserts
`not hasattr(main, name)` for each of the four relocated callables, so a future
re-import cannot silently re-open the target; (2) each repointed double is a
recording spy whose test asserts `spy.calls` is non-empty, so a dead patch fails
on an empty call list rather than on a wording diff; (3) the repoint lands in the
same commit as the move it is forced by, and that commit's RED step is running
the repointed tests against the pre-move tree and watching them fail.

## File Changes

| File | Action | Description |
|---|---|---|
| `src/openkos/application/consent.py` | Create | `ConfirmationRequest` union (S1) |
| `src/openkos/application/lifecycle.py` | Create | The service; S1 seeds it, S2a–S5 fill it |
| `src/openkos/fsio.py` | Modify | `snapshot_read` promoted from `cli/main` (S1) |
| `src/openkos/cli/main.py` | Modify | Five verbs become adapters; ~2,400 lines leave, the echoes move to the call sites; `_snapshot_read`/`_slugify`-style delegators remain |
| `tests/unit/application/test_layering.py` | Modify | +`snapshot_read` in the shared-helper set; +`openkos.vcs` to the offender list (S1) |
| `tests/unit/application/test_lifecycle.py` | Create | Filesystem-light service unit tests |
| `tests/unit/application/test_lifecycle_seams.py` | Create | `hasattr` absence guard; D2 field-absence guard (S1/S5) |
| `tests/unit/cli/test_merge_core.py` | Modify | 53 references → one aliased import line (query slice's `test_query_save.py` precedent) |
| `tests/unit/cli/test_adjudicate.py` | Modify | 4 patch targets (S1); call-site repoints (S5) |
| `tests/unit/cli/test_merge.py`, `test_unmerge.py`, `test_unmerge_surgical_catalog.py`, `test_forget.py`, `test_purge.py` | Modify | Patch-target repoints only; **zero output-text assertions changed** |

## Slice Plan (revises the proposal's table — C2 and C3)

`delivery_strategy` `auto-chain`, `stacked-to-main`, 400-line budget. Every slice
leaves all five verbs working end to end.

| # | Content | Est. | Still works because |
|---|---|---|---|
| S1 | `lifecycle.py` + `consent.py`; `snapshot_read`→`fsio`; relocate the merge core, `StackedBodyReport`, id resolution, `merge_drift_targets`; the union; **the 4 forced `test_adjudicate.py` repoints + `test_merge_core.py`'s import** | 350–400 | `merge()` calls the service and renders unchanged; `adjudicate` drives the same relocated core |
| S2a | `unmerge_core` — Phase B writes only (`10938`–`11013`); adapter keeps Phase A, preview, gate, guard, `_autocommit` | 250–320 | The gate and guard are untouched; only the write block is called out of line |
| S2b | `prepare_unmerge` + `PreparedUnmerge` (`10700`–`10891`) and `unwind_step_preview_lines`; adapter renders the preview from the dataclass | 350–400 | Both unmerge forms (classic, `--to`) drive the same pair; `confirmed` short-circuit unchanged |
| S3 | De-present `forget`: `ForgetPlan`, `ReferenceDisclosure` (#567 aggregation), Gate 1 stays adapter-side | 300–400 | 89 CLI tests; only 1 patch site (`_snapshot_read`, safe class) |
| S4 | De-present `purge`: `PurgePlan`, `PurgeDisclosure`, `purge_confirm_phrase`, the two string notices; all six rails stay adapter-side | 350–400 | 76 CLI tests; 5 patch sites, all safe class |
| S5 | De-present `adjudicate --apply`/`--apply-same`: `prepare_one_merge`, `reconcile_planned`, `BatchApplyPreview`, the typed-count request; `test_lifecycle_seams.py` | 380–400 | Both walks share one preview unit; the patch targets already moved in S1 |

Total ≈ **1,980–2,320** across six PRs. `Decision needed before apply: No`
(auto-chain). Strict pairs: S1→S2a→S2b, S1→S5. S3 and S4 are independent of each
other and of S2. Any prefix is a valid stopping point.

**What changed from the proposal**: its Slice 6 (drafted as a 96-site repoint,
since corrected in place) has no content — the 4 real sites are forced into S1 (C3), the same argument the query
slice used to absorb its 123 `answer` edits into Slice 1. Its Slice 2 splits in
two because `_execute_single_unmerge` is a 439-line command-shaped function, not
a clean Phase B (C2). Six slices either way.

## Testing Strategy

| Layer | What to test | Approach |
|---|---|---|
| Unit (service) | Every `ConfirmationRequest` field per verb; `matches()` on both `match_mode`s incl. the whitespace asymmetry; `ForgetPlan` reference aggregation (#567 count-of-1 singular wording); `PurgeDisclosure` raw-absence arm; Pass-1 guardrail exclusions | `tests/unit/application/test_lifecycle.py`, no Typer runner |
| Unit (layering) | No `openkos.cli`/`typer`/`rich`/`openkos.vcs`; `openkos.llm.base` only; `snapshot_read` + the three write helpers single-defined | AST scan over `application/*.py` |
| Unit (seams) | `not hasattr(main, X)` ×4; no `granted`/`force`/`override` field in the union (D2) | `test_lifecycle_seams.py` |
| Characterization | Full stdout + stderr + exit code per gate arm | Committed goldens, per slice |
| Integration | The ~443 existing tests across the five verbs, **output-text assertions unmodified** | `uv run pytest` |

**Golden matrix** (one per rendering branch, generated on the pre-move tree in
each slice's first commit): boolean gate × {`--auto`, `review: false`, TTY
accept, TTY decline, non-TTY refuse}; typed gate × {flag match, flag mismatch,
TTY match, TTY mismatch, non-TTY refuse}; `forget` scope `self` vs `source`
(different prompt text); `forget` Gate 1 refuse and `--force` bypass; each of
`purge`'s six rails; `purge` raw-absent vs raw-resolved; `adjudicate --apply-same`
zero-eligible short-circuit (before the gate) and mid-batch failure partial
summary; every drift refusal (exit 3).

**Falsification, because a test that passes first try proves nothing.** Per
slice: mutate exactly one character in exactly one *relocated* string, purge
`__pycache__` (a same-size mutation otherwise runs stale bytecode and the verdict
is fiction), run `uv run pytest` unpiped, confirm RED, revert with the inverse
replace — never `git checkout --`. Do it for one preview line, one refusal line,
and one `mismatch_abort`. Additionally, for the two dangerous patch classes:
delete one repointed patch line, confirm the spy-call assertion goes RED, restore.

Strict TDD holds — RED-first for the genuinely new surface (the union, `matches`,
`prepare_unmerge`, the typed plans); the goldens are characterization pins, and
the falsification step is what keeps them non-vacuous. Branch coverage stays above
90%: the gate arms become reachable without a Typer runner.

## Threat Matrix

`purge` runs `git filter-repo`; `merge`/`unmerge`/`forget` run `_autocommit`. The
matrix is therefore **applicable**, unlike both predecessors' — but the design
response in every row is that the boundary does not move.

| Boundary | Applicability | Design response | Planned RED test |
|---|---|---|---|
| Documentation-like paths | **N/A** — no file classification, no execution of ingested content | — | — |
| Git repository selection | **Applicable** — `purge` rail 3 requires the workspace root to *be* the repo root and never uses `git -C <userpath>` (`7256–7278`) | Every `vcs_git.*` call stays adapter-side; `application/lifecycle.py` imports nothing from `openkos.vcs` | Extend the layering guard's offender list with `openkos.vcs` (6 lines, S1) |
| Commit state | **Applicable** — `purge` rail 4 (dirty tree, `7280–7299`); `_autocommit`'s path lists | Rails and `_autocommit` unchanged and adapter-side; `merge_core`/`unmerge_core` return the `committed_paths` list the adapter passes to `_autocommit`, so the ledger sidecar cannot fall out of git | Golden: rail-4 refusal wording incl. the 10-path cap and overflow clause |
| Push state | **Applicable** — `purge` rail 5 (`has_published_commits`, `7301–7314`) | Unchanged, adapter-side, before the gate | Golden: rail-5 refusal |
| PR commands | **N/A** — no PR automation anywhere in the region | — | — |
| Path-traversal deletion (added row) | **Applicable** — `canonicalize_concept_id`/`resolve_concept_path` **relocate**, and they are the path-safety gate `forget` and `purge` run *first* (`main.py:7011–7016`) | Move as a unit with their existing tests; the caller's ordering (path safety before descendant resolution) is preserved verbatim | RED: the relocated `resolve_concept_path` still refuses `../` escape, absolute paths, and a symlink pointing outside `bundle/` |

## ADR Gate

Both conditions, honestly:

1. *Does it decide a technology, pattern, interface, or trade-off?* **Yes** —
   `ConfirmationRequest` is a new public interface, and `snapshot_read`'s
   promotion is a small pattern decision.
2. *Is that decision hard to reverse?* **No.** The union has exactly one consumer
   (`cli/main.py`), no on-disk representation, no wire format, and no published
   API — the `api`/`mcp` adapters do not exist, and the headless-consent
   *transport* is explicitly out of scope. Reversing it is a rename inside one
   package plus its tests. `snapshot_read` is a one-function move with one
   definition, pinned by a guard.

Both must be true. **No ADR.** ADR-0018 already decided the layer, its
granularity, the import invariant, "services never render" and "services stage,
adapters write"; this change *applies* them, and the typed confirmation contract
is the literal restatement of its D4, with `QueryOutcome`'s typed degrade flags as
the shipped precedent. Recording an application of an accepted decision as a new
one is what `rules.design`'s "not every change" and "when in doubt, do not create
one" exist to prevent.

**The trigger that would flip condition 2, named rather than pre-empted**: the
first change that puts `ConfirmationRequest` on a wire — an HTTP or MCP
consent exchange with a serialized form and an external client — makes the shape
hard-to-reverse and earns its own ADR. That is the follow-on this change
deliberately does not do.

## Migration / Rollout

No migration. No change to the knowledge model, on-disk format, ledger semantics,
or CLI surface. Additive-then-subtractive per slice; reverting a merge commit
restores `cli/main.py` in full. Commits use `Refs #918`; #918 closes at archive,
this being the last of its three contexts.

## Open Questions

- [ ] None blocking. Design questions 1–7 are answered above. The three
      exploration corrections (C1–C3) change the slice table and the seam
      estimate, not the proposal's D1–D5, which stand as decided.
- [ ] `relate` and `set_volatility` already have pure pairs (`main.py:9733`,
      `9833`) sitting beside the relocated merge core after S1. D5 keeps them
      out; the follow-on is a near-mechanical relocation once `lifecycle.py`
      exists.
