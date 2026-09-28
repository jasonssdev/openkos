# Proposal: decision-revision-detector — find decisions that were later reversed, refined or reaffirmed

## Intent

Refs #1014, piece **(a)**, sub-change 2 of 3 (umbrella exploration:
`openspec/changes/decision-revision-detection/exploration.md`).

A decision taken in one meeting is often reversed, refined or simply repeated
in a later one. Today OpenKOS keeps both Decision objects side by side with
nothing connecting them, so `query` can serve a decision that was reversed
weeks ago as if it were current. Sub-change 1 (`revises-relation`, archived)
gave `reconcile` the two write targets: `supersedes` for a reversal (hides the
old decision) and `revises` for a refinement (hides nothing). Sub-change
`source-event-time` gave every Source an `event_date`. What is missing is the
**detector**: something that proposes which Decision pairs are a reversal, a
refinement or a reaffirmation, with the direction taken from the calendar, and
hands them to the human through the existing `reconcile` consent flow.

Success: a user runs one verb, sees a reviewable list of candidate decision
revisions with quotes from both sides and a date-derived direction, and can
apply each one through `reconcile --from-findings` with per-item consent.
Nothing in the bundle changes without that consent.

Decisions already taken (not reopened here): reversal maps to `supersedes`,
refinement to `revises`; subject and value come from a separate post-hoc LLM
pass over Decisions only, with the shared extraction prompt untouched;
REAFFIRMS is a finding that writes no relation; UNRELATED writes nothing;
direction comes only from Source `event_date` and is withheld (the human is
asked) when a side has no date or more than one distinct date; candidate
blocking is by the cosine similarity of Decision embeddings (sub-change 3
revised this from the originally-proposed lexical subject similarity, which
measured 14-15 of 24 candidate recall against 19 of 24 for embeddings); findings
are persisted and applied through `reconcile`.

## Scope

### In Scope

1. **Subject pass.** A post-hoc LLM call per Decision that returns
   `subject`, `value` and one `evidence` quote, parsed fail-closed through
   `llm/parsing.py`. The quote MUST appear verbatim in the Decision body, or
   the evidence is dropped (the subject still stands). Stored in a derived,
   digest-keyed cache (see Decisions), computed lazily by the detector run.
2. **Decision event date.** A helper that resolves a Decision's date through
   `bundle/provenance.provenance_source_ancestors` and `okf.read_event_date`,
   returning either one date or an explicit "no direction" reason:
   `missing` (any reached Source has no or a malformed `event_date`),
   `multiple` (more than one distinct date), or `none-reached`.
3. **Candidate generation.** Decision×Decision pairs whose Source sets are
   disjoint, blocked by the cosine similarity of precomputed Decision
   embeddings (a pure leaf: it receives vectors keyed by concept id and
   never calls an embedder itself), top-k per Decision, a global cap with a
   truncation notice, and exclusion of: pairs joined by any
   `RESOLUTION_RELATION_TYPES` edge, deprecated concepts, and confidential
   concepts unless `--include-confidential` (mirroring `contradiction.py`).
   Exclusions apply before counting and capping, so the cost gate stays
   exact. A Decision with no embedding forms no candidate and is counted
   separately.
4. **Judge.** One fail-closed JSON call per pair returning
   `REVERSES | REFINES | REAFFIRMS | UNRELATED`, a confidence, a rationale, and
   one quote from each side. Pairs with a known direction are presented
   earlier-first and labelled with their dates; undated pairs are presented in
   id order and labelled "order unknown". The reply schema has **no direction
   field**: direction is never read from the model.
5. **Verb `openkos revisions`**, labelled experimental: zero-LLM probe, cost
   gate, per-pair loop with a partial batch on failure, serving of persisted
   findings whose input digests are unchanged, and a report grouped per
   Decision that includes REAFFIRMS lines ("reaffirmed by `<id>` on
   `<date>`"). It writes only `.openkos/findings.db`, never the bundle.
6. **Revision findings store**: a sibling table family in `findings.db`
   (verdict, confidence, rationale, both quotes, direction state, input
   digests covering both Decision bodies and their reached Sources), joined to
   the `forget`/`purge` privacy sweep together with the subject cache.
7. **`reconcile --from-findings` consumes revision findings.** For each
   fresh, high-confidence REVERSES or REFINES finding it offers, per item and
   TTY-only as today: REVERSES → `supersedes` held by the later Decision;
   REFINES → `revises` held by the later Decision. When the direction is
   unknown it first asks the human which Decision is the later one (or skip).
   REAFFIRMS and UNRELATED findings are never offered.
8. Delta specs, `docs/cli.md` (`revisions`, `reconcile`), ADR-0025 (written in
   design), tests.

### Out of Scope

- **A `curate` stage.** Deferred to sub-change 3 (see Decisions: opt-in until
  measured). The core is shaped so the stage is a thin caller.
- The measurement harness, fixture and any claim that the subject or judge
  prompt wording is good (sub-change 3).
- Changing the shared extraction/classification prompt.
- Embedding-based subject blocking (revisit only if sub-change 3 shows low
  candidate-stage recall).
- A `speaker` field (no consumer needs it; add when one does).
- A per-decision history view in `query`/`list` (#1014 piece b). REAFFIRMS is
  surfaced only in the `revisions` report for now.
- Counting revision findings in `status`, `next` or pending-work surfaces.
- Overriding the detected kind inside the `--from-findings` walk. A human who
  disagrees skips the item and uses `reconcile --winner`/`--revision` by hand.
- Changes to `examples/good-life-demo/`.

## Decisions

| Decision | Chosen | Rejected | Why |
| --- | --- | --- | --- |
| Verdict vocabulary | `REVERSES / REFINES / REAFFIRMS / UNRELATED` | `REVISES` for a reversal | `revises` already names the *refinement* relation (ADR-0024). A verdict `REVISES` that maps to `supersedes` would read backwards in every log and prompt. |
| Verdict → relation | REVERSES → `supersedes`, REFINES → `revises`, both held by the later Decision | Letting the model name the holder | Matches the taken human decision and ADR-0024's "newer concept asserts the edge". |
| Direction source | Source `event_date` only; equal dates also count as "no direction" | Model-inferred order; ingest time | Taken decision. Equal dates cannot order a pair, so they are treated like a missing date rather than guessed. |
| Where subject/value/evidence live | Derived cache in `.openkos/findings.db`, keyed by concept id + body digest + prompt version | Frontmatter extension via `model/okf.py` | The values are LLM output from an unmeasured prompt that sub-change 3 is expected to change. In frontmatter they would rewrite users' canonical documents silently, go stale on every prompt change, and be treated as source of truth. As a cache they rebuild on demand (AGENTS.md: derived stores are caches), and losing the cache costs only LLM time. `okf.py` stays untouched. |
| When the subject pass runs | Lazily inside the detector run, only for Decisions whose cache key misses | At ingest | Keeps `ingest` and its cost gate unchanged and pays only when the user asks for detection. |
| Cost gate | Two gates in sequence, each exact when shown: subject calls (cache misses), then pair judgments (after candidates exist). Zero LLM calls before each. | One combined number | Pair count depends on subjects, so a single up-front number would be a guess. Showing one exact number per gate avoids the split-gate defect where one number hides the other queue. |
| Findings storage | Sibling tables in `findings.db`, same digest and sweep conventions | Rows in the existing `findings` table | Verified hazard: `_partition_persisted_serves`, `status`, `next`, `pending` and `_run_reconcile_from_findings` read every `findings` row as a contradiction and key the latest row by pair. A revision row for the same pair would override or re-trigger contradiction serving. A sibling table keeps those readers unchanged. See Product decisions, item 3. |
| Candidate eligibility | Disjoint Source sets; cosine similarity of precomputed Decision embeddings above a named threshold (`EMBEDDING_SIMILARITY_THRESHOLD`, revised by sub-change 3 from an originally-proposed lexical subject-overlap score); top-k 5 per Decision; global cap 200 | Every Decision pair (no blocking); lexical subject similarity (sub-change 3's own harness measured 14-15 of 24 candidate recall for it against 19 of 24 for embeddings, with union of the two adding nothing) | Same-source pairs are one meeting, not a change over time. `top_k`/`cap` mirror `contradiction.py`; the embedding threshold is calibrated on sub-change 3's own fixture and short text, and MUST be re-measured on production-shaped text before Phase B. |
| Module shape | Config-free leaf `resolution/decision_revision.py` (candidates, prompt, parse, verdict types) and `resolution/decision_subject.py` (subject prompt and parse); cache access through a `Protocol`, as `insight_identity.QuestionVectorCache` does | Importing `state` from `resolution` | The layering test forbids `resolution` → `state`. Orchestration lives in an `application/` service (ADR-0018) so the verb and the later `curate` stage share one core. Design picks final names. |
| Opt-in until measured | Standalone verb only, labelled experimental in `--help` and with one stderr line stating quality is unmeasured; no `curate` stage yet | Live `curate` stage now | `curate` is the default maintenance run. Adding an unmeasured LLM stage there spends model time on every run and puts unvalidated suggestions in front of every user. Nothing is written without consent either way, so the only cost of waiting is discoverability. |
| REAFFIRMS surface | The `revisions` report, per Decision, served from persisted findings with zero LLM calls on re-run | A new history view now | No per-decision history view exists yet; it is #1014 piece (b). See Product decisions, item 2. |
| ADR | Yes, ADR-0025, written in design | None | It sets a precedent that will be copied: LLM-derived per-concept attributes live in a derived cache, not frontmatter, and temporal direction never comes from the model. Design re-applies the ADR gate. |

## Capabilities

### New Capabilities

- `decision-revision-detection`: the subject pass and its cache, Decision event
  date resolution, candidate generation, the judge and its verdicts, the
  `revisions` verb (probe, gates, serving, report, experimental label), and the
  revision findings store.

### Modified Capabilities

- `reconcile-command`: ADDED "Revision Findings Walk" — `--from-findings`
  offers fresh high-confidence REVERSES/REFINES findings as `supersedes` or
  `revises` held by the later Decision, asks for the later Decision when the
  direction is unknown, never offers REAFFIRMS/UNRELATED, and stays TTY-only
  per item. Existing contradiction behavior is unchanged.
- `forget-command`: MODIFIED "Deletion Sweep Includes Persisted Findings" —
  the sweep also erases revision findings and subject-cache rows referencing
  the purge set.
- `privacy-purge`: only if its spec restates the findings sweep separately
  (spec phase verifies).

`contradiction-detection`, `curate-command`, `status`, `pending-work` and
`next-action-pointer` get no delta: the sibling table leaves their readers
unchanged.

## Approach

- **Subject pass** (`resolution/decision_subject.py`): prompt, fail-closed
  parse to `DecisionSubject(subject, value, evidence | None)`, verbatim-quote
  check. Prompt version is a constant that is part of the cache key.
- **Detector core** (`resolution/decision_revision.py`): pure functions over
  already-loaded Decisions, subjects and resolved dates. Candidate
  generation, prompt, parse, `RevisionVerdict`, and a batch type with a
  partial result on failure, copied (not imported) from `contradiction.py`.
- **Application service** (`application/revisions.py`, name per design):
  loads Decisions, resolves dates via `provenance_source_ancestors` and
  `okf.read_event_date`, fills the subject cache, runs probe/judge, persists,
  serves unchanged findings.
- **State** (`state/revision_findings.py` or inside `state/findings.py`, per
  design): subject cache table and revision findings tables, `open_*`/
  `record_*`/`delete_*_referencing` with the checked VACUUM +
  `wal_checkpoint` erasure used by the existing sweeps.
- **CLI**: `revisions` verb in `cli/main.py`; `_run_reconcile_from_findings`
  gains a second walk over revision findings reusing `_reconcile_pair` with
  the `--winner`/`--revision` modes shipped in sub-change 1.
- Core stays synchronous; `LLMBackend` only; no new dependencies; OKF seam
  untouched.

### Slice plan (stacked to main, dependency order)

| # | Slice | Contents | Forecast (authored lines, specs excluded) |
| --- | --- | --- | --- |
| 1 | Subject pass + cache | `decision_subject.py`, subject cache table, privacy sweep join, tests | ~350 |
| 2 | Detector core | Decision date resolution, candidate generation, judge prompt/parse/verdicts, batch type, tests (no CLI) | ~420 |
| 3 | Verb + findings | `application/` service, revision findings tables and sweep, `openkos revisions` with probe, two gates, serving, REAFFIRMS report, experimental label, `docs/cli.md`, tests | ~430 |
| 4 | Reconcile consumption | `--from-findings` revision walk, unknown-direction prompt, `docs/cli.md` `reconcile`, ADR-0025, tests | ~350 |

Total ~1,550 authored lines. Each slice is green on its own: slices 1 and 2
add unused-but-tested library code; slice 3 is the first user-visible surface;
slice 4 closes the loop to the bundle.

## Affected Areas

| Area | Impact | Description |
| --- | --- | --- |
| `src/openkos/resolution/decision_subject.py` | New | subject prompt, parse, quote check |
| `src/openkos/resolution/decision_revision.py` | New | candidates, judge, verdicts, batch |
| `src/openkos/application/revisions.py` | New | orchestration shared by the verb and a future `curate` stage |
| `src/openkos/state/` (findings module or sibling) | New/Modified | subject cache, revision findings, sweeps |
| `src/openkos/cli/main.py` | Modified | `revisions` verb; `--from-findings` revision walk; forget/purge sweep calls |
| `docs/cli.md` | Modified | `revisions` section, `reconcile --from-findings` wording |
| `docs/adr/0025-*.md`, `docs/adr/README.md` | New/Modified | ADR, status Proposed |
| `tests/unit/...` | New/Modified | per slice |

## Principles Impact

- **Human curates:** the verb writes only derived state; every bundle write
  goes through `reconcile` with per-item consent.
- **Reconstructible:** subjects and findings are caches keyed by input digests;
  deleting `findings.db` loses only LLM time.
- **Sensitivity:** confidential Decisions are excluded unless
  `--include-confidential`, matching contradiction detection; new tables join
  the privacy sweep.
- **Representation, not truth:** the detector proposes a relationship between
  two recorded decisions; it adjudicates neither.
- **Adopt OKF / immutable `raw/` / local-first:** untouched.

## Risks

| Risk | Likelihood | Mitigation |
| --- | --- | --- |
| Judge and subject prompts are unmeasured | High | Experimental label, no `curate` stage, consent per item; sub-change 3 measures before promotion. |
| Embedding threshold is calibrated on the eval fixture itself and on short text (title + body), so it risks overfitting and may not transfer to full OKF documents | Med | Threshold's docstring states the calibration basis and mandates re-measurement on production-shaped text before Phase B wiring lands. |
| A new table not joined to the privacy sweep leaks quoted text | Med | Sweep join is in the same slice as each table, with a test per table. |
| Many Decisions are undated, so most findings ask the human for direction | Med | Stated in the report; `ingest --event-date` and backfill exist. |
| Two sequential gates confuse `--auto` users | Low | Mirror existing `--confirm-count` conventions per gate; design fixes the flag shape. |
| `cli/main.py` grows further | Low | Orchestration lives in `application/`; the verb is thin. |

## Rollback Plan

Revert the slices in reverse order. No bundle migration exists: the verb writes
only `findings.db` tables that old code never reads. Relations written through
`reconcile --from-findings` are ordinary `supersedes`/`revises` edges from
sub-change 1 and stay valid; removing any is a hand edit or git revert. The new
tables can be left in place or deleted with `findings.db`.

## Dependencies

- `revises-relation` (merged, #1020-#1022) and `source-event-time` (merged,
  #1016-#1019).
- Sub-change 3 (harness) depends on this one and owns promotion to `curate`.

## Success Criteria

- [ ] `openkos revisions` on a bundle with two dated Decisions from different
      Sources that the judge classifies REVERSES reports the later one as
      holder, with quotes from both sides, and writes nothing to `bundle/`.
- [ ] A pair where either side has no date, several dates, or equal dates is
      still judged and reported with "direction unknown"; no reply field can
      set direction (test with a model reply that claims an order).
- [ ] Re-running with unchanged inputs makes zero LLM calls (subjects and
      findings served); editing one Decision or its Source's `event_date`
      re-judges only affected pairs.
- [ ] Each gate prints an exact count before any LLM call; exclusions apply
      before counting.
- [ ] `reconcile --from-findings` offers REVERSES as `supersedes` and REFINES
      as `revises`, asks for the later Decision when direction is unknown,
      and never offers REAFFIRMS/UNRELATED; contradiction walks are unchanged.
- [ ] `forget`/`purge` erase subject-cache and revision-finding rows for the
      purge set.
- [ ] `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy .`,
      `uv run pytest --cov` green with the 90% branch gate.
- [ ] ADR-0025 exists, status `Proposed`, indexed.

## Product decisions for the human

1. **Keep revision detection out of `curate` until it is measured?**
   Recommended: yes. Stake: users running their routine `curate` would
   otherwise spend model time on, and review, suggestions whose accuracy has
   not been checked; the cost of waiting is that users must run
   `openkos revisions` explicitly.
2. **REAFFIRMS appears only in the `revisions` report for now**, not in a
   decision's history, because no history view exists until #1014 piece (b).
   Stake: "reaffirmed in the meeting of `<date>`" is visible only when the
   user runs the detector report.
3. **Revision findings are stored beside, not inside, contradiction findings.**
   This refines the earlier "findings table" instruction. Stake: none for the
   user if accepted; if rejected, contradiction checks could re-run or
   misreport pairs that also have a revision finding.

## Human confirmation (2026-09-25)

The owner confirmed that the detector ships only as the experimental `openkos revisions` verb, with no `curate` stage until sub-change 3's harness measures it.
