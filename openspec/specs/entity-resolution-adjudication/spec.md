# Entity-Resolution Adjudication Specification

## Purpose

`resolution/adjudication.py` is a read-only, config-free precision layer over
slice 1's `find_candidates` output: it prompts an injected `LLMBackend` to
adjudicate each `CandidateGroup` — using member title + full body — into a
`SAME` / `DIFFERENT` / `UNCERTAIN` verdict with confidence and rationale,
surfaced through a read-only `adjudicate` CLI verb. It never merges, writes,
or decides; verdicts are ephemeral, for human review only.

## Non-Goals

This spec does not define: destructive `merge`/`resolve`, tombstones, merge
records, sensitivity recompute, or un-merge (slice 3); embeddings or
vector-based candidate generation; any change to slice-1 `find_candidates`
or its thresholds; any bundle/state write or persisted OKF type for the
adjudication result; batching of multiple groups into one LLM call; or
content truncation/summarization of member bodies.

## Requirements

### Requirement: Per-Group LLM Adjudication Preserving Order

`adjudicate_candidates(candidates, bundle_dir, llm)` MUST issue one LLM call
per input `CandidateGroup` with readable content (Approach A) and MUST
return exactly one `AdjudicatedCandidate` per input group, in the same order
as the input list. A group with zero readable members is a documented
exception: it MUST NOT trigger an `llm.chat` call (see Requirement:
Read-Only Full-Body Member Loading, Degrade Per Member).

#### Scenario: One verdict per input group, same order

- GIVEN a list of three `CandidateGroup` values and a fake `LLMBackend`
- WHEN `adjudicate_candidates` runs
- THEN the result has exactly three `AdjudicatedCandidate` entries, each
  referencing its corresponding input group, in input order

### Requirement: `Verdict` And `AdjudicatedCandidate` Shape

The system MUST define a `Verdict` enum with exactly `SAME`, `DIFFERENT`,
and `UNCERTAIN`, and a frozen, ephemeral `AdjudicatedCandidate(candidate,
verdict, confidence: float, rationale: str)` — never a persisted OKF type or
`bundle`/`state` file.

#### Scenario: Adjudicated result carries candidate, verdict, confidence, rationale

- GIVEN one `CandidateGroup` and a fake backend returning a valid reply
- WHEN it is adjudicated
- THEN the returned `AdjudicatedCandidate` exposes the original candidate,
  a `Verdict` member, a float confidence, and a non-empty rationale string

### Requirement: Cross-Type Prompt Honesty

`_build_messages` MUST render a single-type `CandidateGroup`'s prompt with
today's exact bytes (unchanged: `"OKF TYPE: {okf_type}"` plus each member's
untagged header). For a cross-type group (`member_types` holding more than
one distinct value), the prompt MUST instead name every distinct type
present — sourced from `member_types`, not the joined `okf_type` display
label — and MUST tag each member's own header with that member's own type,
so the LLM is never told a false single-type fact for a cross-type group.
The `Verdict` schema (`verdict`/`confidence`/`rationale`) and the
`adjudicate --json` payload's field set (`member_ids`, `okf_type`, `tier`,
`verdict`, `rationale`) MUST remain unchanged: no `member_types` field is
added to the `--json` payload or to `AdjudicatedCandidate`.

#### Scenario: Single-type group keeps today's exact prompt bytes

- GIVEN a same-type `CandidateGroup`
- WHEN `_build_messages` renders its prompt
- THEN the prompt bytes are identical to before this change

#### Scenario: Cross-type group names both types and tags each member

- GIVEN a cross-type `CandidateGroup` with a Concept member and an Entity
  member
- WHEN `_build_messages` renders its prompt
- THEN the prompt names both `Concept` and `Entity`, and each member's
  header is tagged with that member's own type

#### Scenario: Verdict schema is unchanged for a cross-type group

- GIVEN a cross-type group is adjudicated by a fake backend returning a
  valid reply
- WHEN `adjudicate_candidates` runs
- THEN the returned `AdjudicatedCandidate` exposes only `candidate`,
  `verdict`, `confidence`, and `rationale` — no new field

#### Scenario: `--json` payload keys are unchanged for a cross-type group

- GIVEN a cross-type group's adjudication result
- WHEN `adjudicate --json` runs
- THEN each parsed JSON object has exactly the existing keys `member_ids`,
  `okf_type`, `tier`, `verdict`, `rationale`, and no `member_types` key

### Requirement: Read-Only Full-Body Member Loading, Degrade Per Member

Adjudication MUST load each candidate member's title and full body read-only
via `okf.load_frontmatter`. A member whose document is unreadable or
malformed at adjudication time MUST be skipped from that group's prompt
without raising; the group MUST still be adjudicated using its remaining
readable members. If EVERY member of a group is unreadable, the group MUST
short-circuit to `Verdict.UNCERTAIN`, `confidence == 0.0`, and rationale
`"no readable member content"` — WITHOUT calling `llm.chat` for that group
(a documented exception to the one-call-per-group rule).

#### Scenario: Unreadable member is skipped, group still adjudicated

- GIVEN a candidate group where one member's document is unreadable and the
  other member is readable
- WHEN `adjudicate_candidates` runs
- THEN it does not raise, the unreadable member is excluded from the
  prompt, and the group receives a verdict based on the remaining member(s)

#### Scenario: All members unreadable short-circuits without an LLM call

- GIVEN a candidate group where every member's document is unreadable
- WHEN `adjudicate_candidates` runs
- THEN the group's result is `Verdict.UNCERTAIN` with `confidence == 0.0`
  and rationale `"no readable member content"`, and `llm.chat` is never
  called for that group

### Requirement: Fail-Closed Reply Parsing And Validation

The LLM reply for a group MUST be a JSON object
`{"verdict": "same"|"different"|"uncertain", "confidence": <0.0-1.0>,
"rationale": "<string>"}`. `verdict` MUST be matched case-insensitively; an
unrecognized verdict string MUST map to `UNCERTAIN`. `confidence` MUST be
clamped to the `[0.0, 1.0]` range. A reply that is unparseable as JSON, not
an object, or missing/invalid `rationale` MUST NOT crash the run: that group
MUST degrade to `Verdict.UNCERTAIN` with `confidence=0.0` and a rationale
noting the parse/validation failure — the group is never skipped or dropped.

#### Scenario: Valid reply maps faithfully

- GIVEN a fake backend returning `{"verdict": "SAME", "confidence": 0.92,
  "rationale": "Identical entity, different casing"}`
- WHEN the group is adjudicated
- THEN the result has `Verdict.SAME`, `confidence == 0.92`, and that
  rationale

#### Scenario: Out-of-range confidence is clamped

- GIVEN a fake backend returning `confidence: 1.5`
- WHEN the group is adjudicated
- THEN the result's confidence is `1.0`

#### Scenario: Malformed reply degrades to UNCERTAIN, run continues

- GIVEN a fake backend returning non-JSON text for one group and a valid
  reply for another
- WHEN `adjudicate_candidates` runs
- THEN the malformed group's result is `Verdict.UNCERTAIN` with
  `confidence == 0.0` and a rationale describing the parse failure, the
  second group's valid result is unaffected, and neither raises

### Requirement: All Three Verdicts Preserved, Never Auto-Dropped

The library MUST return every adjudicated group regardless of verdict.
`DIFFERENT` and `UNCERTAIN` results MUST NOT be silently dropped or
filtered by `adjudicate_candidates` itself.

#### Scenario: DIFFERENT verdict is present in the returned list

- GIVEN a fake backend returning `{"verdict": "different", ...}` for a group
- WHEN `adjudicate_candidates` runs
- THEN that group's `AdjudicatedCandidate` with `Verdict.DIFFERENT` is
  present in the returned list

### Requirement: The Rubric Is Type-Aware About What An Identical Title Means

The adjudication system prompt MUST state that the OKF type changes what an
identical title is evidence OF. For a `Person` or an `Organization` an
identical name is strong evidence of one entity; for an `Event` an identical
title usually names a RECURRING SERIES, and two records under it are usually
two occurrences of it.

The rubric MUST ask, before two Events are judged the same, for agreement on
a signal only one occurrence could carry — a date, who was present, or a
decision one of them records — and MUST say that a rationale calling one
member a continuation, a follow-up, or a later session of the other has
already decided they are different.

The paragraph MUST sit BEFORE the reply-shape instruction, so the JSON
contract stays the prompt's last word.

This is a judgment change and MUST NOT be adopted unmeasured. The
measurement MUST carry both directions: the recurring-occurrence class the
change is for, AND at minimum one class of Events that ARE one meeting
recorded twice, without which a rubric that simply stopped merging Events
would score perfectly.

#### Scenario: The rubric names the recurrence frame

- GIVEN the shipped adjudication system prompt
- WHEN it is inspected
- THEN it names a recurring series, names occurrences, distinguishes the
  Person case, and names continuation and follow-up as already-decided

#### Scenario: One meeting recorded twice is still SAME

- GIVEN two Events sharing a title whose bodies agree on the date, the
  attendees, and the decisions recorded
- WHEN they are adjudicated
- THEN the verdict is `SAME`

### Requirement: A `SAME` Verdict Whose Rationale Argues For Two Occurrences Is Withdrawn

When a `SAME` verdict's own rationale asserts that the members are separate
occurrences, instances, or sessions of one recurring event, the engine MUST
withdraw the verdict to `DIFFERENT` and MUST append a note to the rationale
naming the withdrawal, so the operator sees that the engine intervened and
on what evidence.

`DIFFERENT`, not `UNCERTAIN`: such a rationale does not express doubt, it
makes a claim, and recording that claim as "we do not know" would discard
evidence the model itself supplied.

The rule MUST be pure, deterministic, and reply-only — it reads the verdict
and the rationale, never the bundle, and never issues a model call. It MUST
be idempotent, because it runs both on freshly judged verdicts and on
verdicts served from the store, and a served row may already carry the note.

It MUST NOT act on a `DIFFERENT` or `UNCERTAIN` verdict: both already refuse
the merge, so re-deciding them would be inventing a verdict rather than
withdrawing one.

A marker phrase that is NEGATED earlier in its own sentence MUST NOT fire.
`"There is no indication of different occurrences"` argues FOR the verdict
it carries, and a rule reading the phrase without its negation withdraws
correct verdicts. The negation MUST share the marker's sentence and MUST
precede it: a negation belonging to a different claim cannot excuse this
one, and one arriving after the claim does not unmake it.

The marker vocabulary MUST cover the languages a rationale can be written
in, and MUST fail closed on a phrasing it does not cover — no withdrawal,
rather than a guessed one.

#### Scenario: A rationale calling the members separate occurrences withdraws SAME

- GIVEN a backend returning `same` with a rationale stating the members are
  different instances of the same recurring event
- WHEN `adjudicate_candidates` runs
- THEN the returned verdict is `DIFFERENT` and the rationale carries the
  withdrawal note

#### Scenario: A negated marker is not a self-refutation

- GIVEN a backend returning `same` with a rationale stating there is no
  indication of different occurrences
- WHEN `adjudicate_candidates` runs
- THEN the verdict stays `SAME` and the rationale is unchanged

#### Scenario: Only a SAME verdict is ever withdrawn

- GIVEN a `DIFFERENT` or `UNCERTAIN` verdict whose rationale carries a
  marker phrase
- WHEN the withdrawal runs
- THEN the verdict and the rationale are returned unchanged

#### Scenario: A verdict served from the store is withdrawn on read

- GIVEN a persisted `SAME` row, servable on every digest, whose stored
  rationale argues the members are separate occurrences
- WHEN `adjudicate` serves that row
- THEN the rendered verdict is `DIFFERENT`, the rationale carries the
  withdrawal note, and no model call is made

### Requirement: `OllamaError`-Family Propagates Unswallowed From The Leaf

Any `OllamaError`-family exception raised by `llm.chat` MUST propagate
unswallowed out of `adjudicate_candidates` to the caller; the leaf MUST NOT
catch or degrade transport/model-availability failures itself.

#### Scenario: Backend transport failure propagates

- GIVEN an `LLMBackend` that raises `OllamaUnavailable` on `chat`
- WHEN `adjudicate_candidates` runs
- THEN `OllamaUnavailable` propagates out of the call, uncaught by the leaf

### Requirement: Read-Only `adjudicate` CLI Verb

The CLI MUST expose a read-only verb named `adjudicate` — distinct from the
reserved `resolve`/`merge` verbs — that: gates on `require_workspace` like
`query`; builds `OllamaClient(model=cfg.model)` and injects it into
`adjudicate_candidates`; prints each group's verdict, confidence, and
rationale to stdout grouped for review; performs zero bundle writes; and
requires no confirmation gate.

#### Scenario: Verb renders verdicts with zero writes

- GIVEN a bundle with at least one candidate group and a configured model
- WHEN `adjudicate` runs
- THEN each group's verdict, confidence, and rationale are printed to
  stdout, and no bundle file is created or modified (bytes and mtime
  unchanged)

### Requirement: `--same-only` Is A Display-Only Filter

The `adjudicate` verb MAY accept a `--same-only` flag that hides
non-`SAME` verdicts from the printed report. This flag MUST NOT affect
`adjudicate_candidates`, which always returns every group regardless of the
flag.

#### Scenario: `--same-only` hides DIFFERENT/UNCERTAIN from output only

- GIVEN a bundle whose groups adjudicate to a mix of SAME, DIFFERENT, and
  UNCERTAIN
- WHEN `adjudicate --same-only` runs
- THEN only SAME verdicts appear in the printed report, while the
  underlying library call still received every group

### Requirement: Degrade-On-No-Model Mirrors `query`'s 3-Tier Catch

The `adjudicate` verb MUST catch `OllamaUnavailable`, then
`OllamaModelNotFound`, then generic `OllamaError` (in that subclass order),
report a clear actionable message, and write nothing, mirroring `query`'s
degrade contract. WHEN the caught exception is `OllamaUnavailable`, the
message MUST additionally point to `openkos doctor` to diagnose the
environment, mirroring `query`'s `OllamaUnavailable` wording; the
`OllamaModelNotFound` and generic `OllamaError` messages are unchanged.
(Previously: the `OllamaUnavailable` message told the user to run
`ollama serve` with no additional pointer to `openkos doctor`.)

#### Scenario: Ollama unreachable also points to doctor

- GIVEN `adjudicate_candidates` raises `OllamaUnavailable`
- WHEN `openkos adjudicate` runs
- THEN stderr tells the user to run `ollama serve` and also names
  `openkos doctor` to diagnose the environment
- AND the process exits 1 with zero bundle writes

#### Scenario: No model available degrades cleanly

- GIVEN no local Ollama server or configured model is reachable
- WHEN `adjudicate` runs
- THEN it reports a clear actionable error, performs zero bundle writes,
  and exits without an unhandled traceback

### Requirement: Deterministic Given A Fixed Backend

`adjudicate_candidates` MUST be deterministic for a fixed input and a fixed
backend reply sequence: running it twice with the same fake `LLMBackend`
over the same candidates MUST yield the same verdicts, confidences, and
rationales.

#### Scenario: Repeated runs with a fake backend are deterministic

- GIVEN the same candidate list and the same fake `LLMBackend` replies
- WHEN `adjudicate_candidates` runs twice
- THEN both runs return equal `AdjudicatedCandidate` lists

### Requirement: Leading Verdict Tally Line Over Full Results

When `openkos adjudicate` has one or more results, the report output MUST
include a leading summary line `adjudicated N: x SAME, y DIFFERENT` as the
first line of the report body (following the workspace-banner header and blank line),
where `N`, `x`, and `y` are counted over the FULL `results` set returned by
`adjudicate_candidates`, independent of the `--same-only` display filter. A
`, z UNCERTAIN` segment MUST be appended ONLY when `z > 0`; it MUST be
omitted entirely when `z == 0`.

#### Scenario: Mixed SAME/DIFFERENT, no UNCERTAIN

- GIVEN adjudication results with 2 SAME and 1 DIFFERENT verdicts and zero
  UNCERTAIN
- WHEN `adjudicate` runs
- THEN the report body begins with `adjudicated 3: 2 SAME, 1 DIFFERENT`

#### Scenario: Mixed results with UNCERTAIN present

- GIVEN adjudication results with 2 SAME, 1 DIFFERENT, and 1 UNCERTAIN
- WHEN `adjudicate` runs
- THEN the report body begins with
  `adjudicated 4: 2 SAME, 1 DIFFERENT, 1 UNCERTAIN`

#### Scenario: Zero UNCERTAIN omits the segment

- GIVEN adjudication results containing zero UNCERTAIN verdicts
- WHEN `adjudicate` runs
- THEN the tally line contains no `UNCERTAIN` segment

#### Scenario: All-SAME results

- GIVEN adjudication results where every verdict is SAME
- WHEN `adjudicate` runs
- THEN the report body begins with `adjudicated N: N SAME, 0 DIFFERENT` for
  the matching count `N`

#### Scenario: All-DIFFERENT results

- GIVEN adjudication results where every verdict is DIFFERENT
- WHEN `adjudicate` runs
- THEN the report body begins with `adjudicated N: 0 SAME, N DIFFERENT` for
  the matching count `N`

#### Scenario: `--same-only` filters display, not the tally count

- GIVEN adjudication results with a mix of SAME, DIFFERENT, and UNCERTAIN
- WHEN `adjudicate --same-only` runs
- THEN the tally line still reports counts over the full results set, while
  only SAME verdicts appear in the printed detail below it

### Requirement: One-Time Verdict-Column Legend Line

When at least one result is printed, `adjudicate` MUST print exactly one
legend line explaining the per-group verdict/confidence/rationale columns,
placed after the tally and BEFORE the results loop. The legend MUST NOT
repeat per group.

#### Scenario: Legend appears once regardless of result count

- GIVEN four adjudication results
- WHEN `adjudicate` runs
- THEN the legend line appears exactly once, before the first result's
  detail lines

### Requirement: Trailing Next-Action Hint

When at least one result is printed, the LAST line of `adjudicate` stdout
MUST be `Next: openkos merge <survivor> <absorbed>`.

#### Scenario: Hint is the final line

- GIVEN at least one adjudication result is displayed
- WHEN `adjudicate` runs
- THEN the last stdout line is `Next: openkos merge <survivor> <absorbed>`

### Requirement: Empty And Same-Only-Empty States Stay Single-Line

WHEN there are no candidate groups to adjudicate, OR `--same-only` filters
the display down to zero SAME verdicts, stdout MUST contain ONLY the
existing sole message for that path — no tally, no legend, and no `Next:`
hint. The full-`results` tally still exists conceptually but MUST NOT be
printed on these paths.

#### Scenario: No candidates to adjudicate

- GIVEN a bundle with no candidate groups
- WHEN `adjudicate` runs
- THEN stdout is exactly the existing "no candidates" message with no
  additional lines

#### Scenario: `--same-only` filters every result out

- GIVEN adjudication results containing zero SAME verdicts
- WHEN `adjudicate --same-only` runs
- THEN stdout is exactly the existing
  `"No SAME-verdict candidates to display (--same-only)."` message with no
  additional lines

### Requirement: Reusable Verdict-Tally Formatting Helper

The system MUST provide a pure formatting helper, sibling to
`_format_type_tally`, that renders the verdict tally line
(SAME/DIFFERENT/UNCERTAIN counts, UNCERTAIN segment omitted when zero) from
the per-verdict counts, reusing `_plural`. Given all-zero counts it MUST
return `""`. Its argument shape is an implementation detail; only the
returned string and the empty-on-zero contract are observable.

#### Scenario: Zero counts yield empty string

- GIVEN the helper is called with all-zero verdict counts
- WHEN it runs
- THEN it returns `""`

#### Scenario: Populated counts yield the tally line

- GIVEN the helper is called with counts for SAME, DIFFERENT, and UNCERTAIN
- WHEN it runs
- THEN it returns the `adjudicated N: x SAME, y DIFFERENT, z UNCERTAIN` line
  matching those counts, omitting the UNCERTAIN segment when its count is 0

### Requirement: Existing Detail Lines Stay Byte-Identical

All per-result detail lines (verdict, confidence, rationale) emitted by
`adjudicate` before this change MUST remain byte-identical after adding the
tally, legend, and hint lines.

#### Scenario: Pre-existing substring assertions still pass

- GIVEN any pre-existing CliRunner test asserting a per-result detail
  substring on `adjudicate` output
- WHEN `adjudicate` runs after this change
- THEN that substring is still present, unchanged

### Requirement: Persisted Verdicts Are Served Before Re-Judging

`openkos adjudicate` MUST persist every freshly judged verdict to the
`adjudications` tables of `.openkos/findings.db` (issue #779 --
`state.adjudications`, the findings store's second tenant, so `purge`'s
wholesale deletion and `forget`'s sweep cover it with no new privacy
surface), alongside one content-hash digest per member computed at
persist time. A later run MUST serve a candidate group from the store,
with NO model call for it, exactly when: the latest persisted row for the
group's member set matches the run's EFFECTIVE confidential inclusion
(`--include-confidential` OR the verified local-backend exemption -- the
same disjunction `sensitivity.should_block` applies, so the partition
runs only after the exemption is resolved; a verdict computed over a
different member subset must never serve), was computed under the
CURRENT judgment rubric (issue #838 -- the row's stored `rubric_digest`
equals `resolution.adjudication.rubric_digest()`, a fingerprint over the
adjudication system prompt plus the deterministic post-parse withdrawal
rule's defining data, so the prompt and the rule cannot drift apart; a
row with no stored digest, written before the column existed, is never
servable, because a verdict from an unknown rubric is not a verdict this
build would produce), carries a digest row for
EVERY current group member and no others, every stored digest equals the
member's CURRENT content hash, and the stored verdict is in the
vocabulary. The `rubric_digest` column MUST be added to a store a
pre-#838 build created by a real migration at the one place the tables
are created, and the read path MUST tolerate the pre-migration shape
(reporting those rows' rubric as unknown) rather than degrading the
whole store to a failed read.
Everything else re-judges, conservatively -- including a
present-but-corrupt store, which degrades to one stderr advisory and a
full fresh judge, and a persist failure, which costs one advisory, never
the run. A result any of whose members has no current digest MUST NOT be
persisted (a row whose staleness can never be checked would serve
forever -- this also keeps the no-readable-member UNCERTAIN
short-circuit out of the store).

The run MUST report the split on stderr
(`N of M candidate group(s) served from persisted adjudications; K judged
fresh.`), mirroring `contradictions`' line, and a `--fresh` flag MUST
bypass the serve and re-persist, mirroring `contradictions --fresh`.
The rubric-driven re-spend MUST be announced, not silent (issue #838's
explicit ruling): when at least one group re-judges only because its row
predates the current rubric (mismatched or absent digest), one stderr
line names the rubric as the cause -- a rubric change re-judging every
cached group would otherwise surface as a sudden `0 of N served` with no
stated reason. The line MUST be absent on the healthy path and on
ordinary member drift.
Served and fresh verdicts MUST render identically through every output
mode, in candidate order — and that identity is what obliges a served
verdict to pass through the SAME reply-only judgment a fresh one does
(see Requirement: A `SAME` Verdict Whose Rationale Argues For Two
Occurrences Is Withdrawn). A row written before that withdrawal existed
still carries the verdict its own rationale argues against; re-deciding it
on read costs no model call, because the rule is pure and reads only the
rationale the row already stores. Writes stay confined to derived state under
`.openkos/` -- the bundle remains untouched on a read-only run, exactly
as before.

#### Scenario: A repeat run on an unchanged bundle costs zero model calls

- GIVEN a bundle adjudicated once, unchanged since
- WHEN `openkos adjudicate` runs again
- THEN every group is served from the store, the model receives zero
  groups, and the split line reports `N of N ... 0 judged fresh`

#### Scenario: Member drift re-judges

- GIVEN a persisted verdict and a member edited since
- WHEN `openkos adjudicate` runs
- THEN that group is judged fresh and the new verdict re-persisted

#### Scenario: An effective-inclusion mismatch never serves

- GIVEN a verdict persisted from a run whose EFFECTIVE inclusion was
  exclusive (no flag, local exemption disabled)
- WHEN `openkos adjudicate --include-confidential` runs on the unchanged
  bundle
- THEN the group is judged fresh

#### Scenario: The store stays bounded by the live group set

- GIVEN a group re-judged (drift or `--fresh`)
- WHEN the fresh verdict is persisted
- THEN the group's superseded rows are replaced, not accumulated

#### Scenario: A rubric change re-judges and names the reason

- GIVEN a persisted verdict whose stored `rubric_digest` differs from the
  current build's
- WHEN `openkos adjudicate` runs on the unchanged bundle
- THEN the group is judged fresh, AND stderr carries one line naming the
  judgment rubric as the cause of the re-spend

#### Scenario: A pre-rubric row never serves

- GIVEN a persisted verdict row carrying no `rubric_digest` (written
  before the column existed)
- WHEN `openkos adjudicate` runs on the unchanged bundle
- THEN the group is judged fresh under the same announced reason

#### Scenario: The rubric line is absent when nothing was rubric-stale

- GIVEN a fully served repeat run, or a re-judge caused only by member
  drift
- WHEN `openkos adjudicate` runs
- THEN stderr carries no judgment-rubric line

### Requirement: Machine-Readable `--json` Output Mode

`openkos adjudicate` MUST accept a `--json` flag. When set, stdout MUST be a
single valid JSON OBJECT with EXACTLY these four keys: `partial` (boolean),
`adjudicated` (integer), `total` (integer), and `results` (array).

`results` holds one object per entry in the `results` set, with EXACTLY these
fields per object: `member_ids` (list of strings, already sorted), `okf_type`
(string), `tier` (`"HIGH"` or `"LOW"`), `verdict` (`"SAME"`, `"DIFFERENT"`, or
`"UNCERTAIN"`), `rationale` (string), and `cross_source` (boolean, issue
#776: `true` exactly when the verdict is SAME, the group has two members,
and their provenance sets are disjoint -- the same predicate the human
listing's note fires on, because the unattended pipelines `--json` serves
cannot read a stdout note). The object MUST NOT contain a
`confidence` field or any survivor/absorbed field.

`total` MUST be the number of candidate groups QUEUED for adjudication and
`adjudicated` the number the model returned verdicts for. Both describe the
RUN and MUST NOT be reduced by the `--same-only` display filter, which
narrows `results` alone. `partial` MUST be `true` exactly when the batch
carried a failure — that is, when the run stopped before adjudicating every
queued group.

#### Scenario: Exact field set, no confidence

- GIVEN adjudication results with mixed verdicts
- WHEN `adjudicate --json` runs
- THEN each parsed object inside `results` has exactly the keys `member_ids`,
  `okf_type`, `tier`, `verdict`, `rationale`
- AND no object contains a `confidence` key

#### Scenario: Example mixed-verdict payload

- GIVEN two candidate groups, one SAME and one DIFFERENT
- WHEN `adjudicate --json` runs
- THEN stdout parses to:
  ```json
  {
    "partial": false,
    "adjudicated": 2,
    "total": 2,
    "results": [
      {
        "member_ids": ["concept-a", "concept-b"],
        "okf_type": "person",
        "tier": "HIGH",
        "verdict": "SAME",
        "rationale": "Same individual; identical canonical name and role."
      },
      {
        "member_ids": ["concept-c", "concept-d"],
        "okf_type": "org",
        "tier": "LOW",
        "verdict": "DIFFERENT",
        "rationale": "Distinct organizations despite similar names."
      }
    ]
  }
  ```

### Requirement: Partial Batches Are Self-Describing Under `--json`

A partial batch emits its completed verdicts on stdout and reports the
failure on stderr with exit code 1. Because a redirect such as
`openkos adjudicate --json > out.json` preserves stdout but discards both
stderr and the exit code, the emitted payload MUST declare its own
incompleteness in band: `partial` MUST be `true` and `adjudicated` MUST be
less than `total`.

#### Scenario: Partial batch declares the truncation in the payload

- GIVEN two queued candidate groups where the model answers for one and the
  batch then fails
- WHEN `adjudicate --json` runs
- THEN the parsed payload has `"partial": true`, `"adjudicated": 1`, and
  `"total": 2`
- AND `results` holds exactly the one completed verdict
- AND the exit code is 1

#### Scenario: `--same-only` never reports a complete run as partial

- GIVEN three queued candidate groups that all adjudicate successfully, only
  one of them SAME
- WHEN `adjudicate --json --same-only` runs
- THEN `results` holds exactly one object
- AND `"adjudicated"` is 3, `"total"` is 3, and `"partial"` is `false`

### Requirement: `--json` Fully Suppresses Human Output

When `--json` is passed, stdout MUST contain ONLY the JSON payload — no
tally line, legend line, per-group detail lines, or `Next:` hint. The entire
stdout content MUST parse cleanly via `json.loads`.

#### Scenario: No human-output substrings under `--json`

- GIVEN adjudication results with at least one verdict
- WHEN `adjudicate --json` runs
- THEN stdout contains none of: `"adjudicated "`, the legend line, or
  `"Next: openkos merge"`
- AND `json.loads(stdout)` succeeds

### Requirement: `--same-only` Composes With `--json`

`adjudicate --json` MUST include every result by default, regardless of
verdict. `adjudicate --json --same-only` MUST filter `results` to objects
where `verdict == "SAME"` only, leaving `partial`, `adjudicated`, and `total`
untouched.

#### Scenario: `--json` alone includes all verdicts

- GIVEN results with SAME, DIFFERENT, and UNCERTAIN verdicts
- WHEN `adjudicate --json` runs
- THEN `results` contains one object per result, all verdicts present

#### Scenario: `--json --same-only` filters to SAME

- GIVEN results with SAME, DIFFERENT, and UNCERTAIN verdicts
- WHEN `adjudicate --json --same-only` runs
- THEN `results` contains only objects with `"verdict": "SAME"`

### Requirement: Empty State Emits Valid Empty `results` Under `--json`

WHEN there are no candidate groups, OR `--same-only` filters every result out,
`adjudicate --json` MUST emit the standard envelope with an empty `results`
array, NOT the plain-text "no candidates" message used in the non-JSON path.

#### Scenario: No candidates, `--json`

- GIVEN a bundle with no candidate groups
- WHEN `adjudicate --json` runs
- THEN `json.loads(stdout)` equals
  `{"partial": false, "adjudicated": 0, "total": 0, "results": []}`

#### Scenario: `--same-only` filters all results out, `--json`

- GIVEN one queued candidate group whose verdict is not SAME
- WHEN `adjudicate --json --same-only` runs
- THEN `json.loads(stdout)` equals
  `{"partial": false, "adjudicated": 1, "total": 1, "results": []}`

### Requirement: Deterministic, Pretty-Printed JSON

The `results` array MUST preserve the order of the `results` set (no
re-sorting), with `member_ids` already sorted, and the payload MUST be
pretty-printed with `indent=2`. Identical input MUST yield byte-identical
stdout across runs.

#### Scenario: Stable ordering across runs

- GIVEN the same fixture bundle and model responses
- WHEN `adjudicate --json` runs twice
- THEN both stdout outputs are byte-identical and `results` order matches
  the `results` set order

#### Scenario: Output is indented JSON

- GIVEN at least one adjudication result
- WHEN `adjudicate --json` runs
- THEN stdout parses as JSON and spans multiple indented lines (not a single
  compact line)

### Requirement: Error Paths Unaffected By `--json`

The Ollama-unavailable, model-not-found, and generic-error handlers MUST
remain unchanged when `--json` is passed: the error message MUST go to
stderr, the process MUST exit with code 1, and stdout MUST NOT contain any
JSON (partial or otherwise).

#### Scenario: Ollama unavailable with `--json`

- GIVEN Ollama is unreachable
- WHEN `adjudicate --json` runs
- THEN stderr contains the existing unavailability message
- AND the exit code is 1
- AND stdout does not parse as JSON and contains no partial payload

### Requirement: Non-JSON Output Stays Byte-Identical

Without `--json`, `adjudicate` output (tally, legend, per-group detail,
`Next:` hint, and empty-state messages) MUST remain byte-identical to its
behavior before this change.

#### Scenario: Human output unchanged when `--json` is absent

- GIVEN any pre-existing CliRunner assertion on `adjudicate` stdout without
  `--json`
- WHEN `adjudicate` runs after this change
- THEN that assertion still passes unchanged

### Requirement: `--apply` Eligibility Filter

`adjudicate --apply` MUST offer a group for interactive merge ONLY when
`verdict == SAME` AND the group has exactly 2 `member_ids`. DIFFERENT and
UNCERTAIN groups MUST NEVER be offered. A SAME group with more than 2 members
MUST NOT be prompted; it MUST print `skipped (N>2, merge manually)` for that
group, where `N` is its member count.

#### Scenario: SAME 2-member group is offered

- GIVEN a SAME-verdict group with exactly 2 members
- WHEN `adjudicate --apply` runs
- THEN that group is prompted for merge

#### Scenario: DIFFERENT group is never offered

- GIVEN a DIFFERENT-verdict group
- WHEN `adjudicate --apply` runs
- THEN that group is never prompted

#### Scenario: SAME group with >2 members is skipped, not prompted

- GIVEN a SAME-verdict group with 3 members
- WHEN `adjudicate --apply` runs
- THEN stdout shows `skipped (N>2, merge manually)` for that group
- AND the group is never prompted

### Requirement: Survivor/Absorbed Preview And Prompt

For each eligible group, the survivor MUST be the member with the RICHER
BODY (longer stripped body text), falling back to `member_ids[0]`
(ascending id) only on an exact tie (issue #776: string order alone made a
bilingual pleonasm the permanent Concept ID purely because `f` sorts
before `o`; the richer-body rule mirrors the extraction union's own
twin-drop precedent). An unreadable member measures below every readable
one and never survives on this rule. The criterion that DECIDED MUST be
stated in the preview (`survivor: <id> (richer body)` or `survivor: <id>
(id order -- equal body length)`) -- an arbitrary-looking choice with no
stated criterion is the defect, not the determinism. Before prompting, a
preview of what `prepare_merge` would fuse (survivor, absorbed, rewrites,
removed) MUST be printed. The prompt text MUST be exactly
`Merge <absorbed> into <survivor>? [y/N]` (issue #483: the same #398
contract `curate`'s per-item walks advertise -- the formerly advertised
`skip` token never had behavior distinct from a decline).

The same ordering rule and criterion disclosure apply to EVERY walk that
drives `_prepare_one_merge`: `--apply`, `--apply-same`, and `curate`'s
Identity stage. Each walk MUST compute the ordering ONCE, display it, and
PIN that exact `(survivor, absorbed)` pair through the prepare-and-write
that follows -- `--apply-same`'s Pass 2 in particular MUST apply the
direction Pass 1 previewed and the typed count consented to, never a live
recomputation: an earlier merge in the same batch can enrich a shared
member enough to flip a recomputed ordering, and the operator would then
get a direction they never saw. A structural consequence, deliberate: with the smaller
body always absorbed into the larger, a 2-member batch merge can no
longer reach #559's 80% stacked-share domination guardrail -- the hazard
that guardrail refused is now prevented by construction (the guardrail
itself remains as defense in depth).

#### Scenario: Preview precedes the exact prompt text

- GIVEN an eligible SAME 2-member group
- WHEN `adjudicate --apply` runs
- THEN a `prepare_merge` preview is printed before the prompt
- AND the prompt line is exactly `Merge <absorbed> into <survivor>? [y/N]`
  with `<survivor>` = the richer-body member and `<absorbed>` the other

#### Scenario: The richer body survives regardless of id order

- GIVEN a SAME 2-member group whose alphabetically-later member carries
  the longer body
- WHEN any apply walk previews it
- THEN that member is the survivor and the preview states `richer body`

#### Scenario: A tie keeps ascending-id order and says so

- GIVEN a SAME 2-member group whose members' stripped bodies have equal
  length
- WHEN any apply walk previews it
- THEN `member_ids[0]` is the survivor and the preview states the id-order
  tiebreak

### Requirement: Cross-Source SAME Verdicts Are Flagged And Batch-Gated

A SAME verdict over a 2-member group whose members BOTH carry non-empty
`provenance:` frontmatter with DISJOINT sets is the risky class (issue
#776: exactly this shape fused two meetings held a week apart into one
false Event). The read-only listing MUST mark such verdicts with a
`note: cross-source -- members share no source` line; the interactive
`--apply` walk MUST print the warning BEFORE its `[y/N]` prompt while
keeping per-item consent as the gate; and `--apply-same` MUST exclude the
pair from the batch by default -- out of the preview, the typed count,
and Pass 2 -- disclosing each exclusion with the exact manual
`openkos merge` command and counting it in the summary
(`cross-source: N`). A new `--include-cross-source` flag restores the
batch merge as an explicit opt-in, and MUST be refused (exit 2) without
`--apply-same`, because a silently ignored consent flag is worse than a
refusal.

Absent evidence MUST NOT flag: a member with no `provenance:` key, an
empty list, or an unreadable/unparseable document gives no signal, and
flagging on absence would mark every hand-written concept forever.

#### Scenario: The batch excludes a disjoint-provenance SAME pair

- GIVEN a SAME 2-member group whose members' provenance sets share no entry
- WHEN `adjudicate --apply-same` runs without `--include-cross-source`
- THEN the pair is skipped with a line naming the manual merge command,
  the typed count excludes it, no file changes, and the summary carries
  `cross-source: 1`

#### Scenario: --include-cross-source restores the batch merge

- GIVEN the same group
- WHEN `adjudicate --apply-same --include-cross-source` runs with the
  matching typed count
- THEN the pair is previewed, counted, and merged

#### Scenario: A shared source or absent provenance is never flagged

- GIVEN a SAME pair whose provenance sets intersect, and another whose
  members carry no provenance at all
- WHEN any adjudicate surface renders them
- THEN neither carries the cross-source note nor is excluded from the batch

### Requirement: Prompt Response Semantics

The prompt MUST be validated by the same helper `curate`'s per-item walks
use (`curate._confirm`, issue #483): `y`/`yes` (case/whitespace-insensitive)
MUST apply the merge; `n`/`no` and empty input (the documented `N` default)
MUST decline it and continue to the next group; any OTHER answer MUST be
re-asked with a one-line notice naming the accepted tokens, never silently
counted as a decline.

#### Scenario: `y` applies the merge

- GIVEN an eligible group and CliRunner `input="y\n"`
- WHEN `adjudicate --apply` runs
- THEN the merge is applied

#### Scenario: empty input does not merge

- GIVEN an eligible group and CliRunner `input="\n"`
- WHEN `adjudicate --apply` runs
- THEN the merge is NOT applied and the run continues

#### Scenario: an unrecognized answer is re-asked, not counted as a decline

- GIVEN an eligible group and CliRunner `input="skip\ny\n"`
- WHEN `adjudicate --apply` runs
- THEN a notice naming the accepted tokens is printed
- AND the merge is applied by the subsequent `y`

#### Scenario: `N`/`n` does not merge

- GIVEN an eligible group and CliRunner `input="n\n"`
- WHEN `adjudicate --apply` runs
- THEN the merge is NOT applied and the run continues

### Requirement: Accepted Merge Executes And Is Reversible

On `y`, `adjudicate --apply` MUST execute the merge via `merge_core`: the
survivor file is updated, the absorbed file is removed, index/log are
updated, and a `merged_from` ledger entry is written. The result MUST be
reversible via `unmerge`.

#### Scenario: Applied merge updates filesystem and ledger

- GIVEN an eligible group with `input="y\n"`
- WHEN `adjudicate --apply` runs
- THEN the survivor file is updated, the absorbed file no longer exists, and
  a `merged_from` ledger entry references the absorbed id

#### Scenario: Applied merge is unmerge-reversible

- GIVEN an applied merge from `adjudicate --apply`
- WHEN `unmerge` is run against the survivor
- THEN the absorbed member is restored

### Requirement: Per-Merge Auto-Commit

Each applied merge MUST be auto-committed independently — one commit per
merge, not one commit for the whole run.

#### Scenario: Two applied merges produce two commits

- GIVEN two eligible groups both answered `y`
- WHEN `adjudicate --apply` runs
- THEN two separate commits are created, one per applied merge

### Requirement: Stale-Id Guard Across Sequential Merges

Before acting on an eligible group, `adjudicate --apply` MUST re-verify both
member ids still exist. If an earlier accepted merge in the same run
absorbed a member that a later group references, that later group MUST print
`skipped (member already merged)` and MUST NOT be prompted or crash.

#### Scenario: Later group referencing an already-absorbed member is skipped

- GIVEN two SAME 2-member groups sharing one member id, the first merge
  accepted with `y`
- WHEN `adjudicate --apply` continues to the second group
- THEN stdout shows `skipped (member unresolved -- already merged or missing)`
  for that group
- AND the run does not crash

### Requirement: `--apply` Rejects `--json`

`adjudicate --apply --json` MUST be rejected with a clear stderr message and
exit code 2, since interactive and machine-readable modes are contradictory.

#### Scenario: `--apply --json` exits 2

- WHEN `adjudicate --apply --json` runs
- THEN stderr contains a clear rejection message
- AND the exit code is 2

### Requirement: `--apply` Composes With `--same-only` As A No-Op

`adjudicate --apply --same-only` MUST behave identically to
`adjudicate --apply` alone, since `--apply` is inherently SAME-only.

#### Scenario: `--apply --same-only` behaves like `--apply`

- GIVEN the same fixture and inputs
- WHEN `adjudicate --apply` and `adjudicate --apply --same-only` each run
- THEN both produce the same eligibility set, prompts, and outcomes

### Requirement: Mid-Run Write Failure Stops The Run

If `merge_core` fails for an accepted merge, `adjudicate --apply` MUST stop
immediately with a clear error message and MUST NOT silently continue to
remaining groups. Commits from prior successfully applied merges in the same
run MUST remain intact and reversible.

#### Scenario: `merge_core` failure halts remaining groups

- GIVEN two eligible groups, the first accepted merge fails inside
  `merge_core`
- WHEN `adjudicate --apply` runs
- THEN a clear error message is shown, the run stops before the second
  group, and the exit code is non-zero

### Requirement: End-Of-Run Summary With Breakdown

At the end of the run, `adjudicate --apply` MUST print a summary line
`applied X, skipped Y` where `Y` breaks down into N>2 skips, already-merged
skips, and declined (N/empty) prompts. After the summary line, each
operator-declined merge MUST be named on its own
`  declined: <absorbed> -> <survivor>` line — two-space indented, exactly as
the implementation emits it (issue #483, mirroring #398's decline listing) —
and no such line may appear for a merge that was applied.

#### Scenario: Summary reflects applied and skipped counts

- GIVEN a run with one applied merge, one N>2 skip, and one declined prompt
- WHEN `adjudicate --apply` completes
- THEN stdout shows `applied 1, skipped 2` with the breakdown of skip reasons

#### Scenario: Declined merges are named after the summary

- GIVEN a run with one applied merge and one operator-declined merge
- WHEN `adjudicate --apply` completes
- THEN the declined pair is named `declined: <absorbed> -> <survivor>` after
  the summary line
- AND the applied pair is not listed as declined

### Requirement: Empty / No-Eligible State

WHEN no SAME 2-member groups exist, `adjudicate --apply` MUST print a clear
message, apply nothing, and exit 0.

#### Scenario: No eligible groups, nothing applied

- GIVEN a bundle whose results contain no SAME 2-member groups
- WHEN `adjudicate --apply` runs
- THEN stdout shows a clear "nothing to apply" message
- AND no merge is performed
- AND the exit code is 0

### Requirement: Plain `adjudicate` Is Unchanged

`adjudicate` without `--apply` — plain, with `--json`, or with `--same-only`
— MUST behave exactly as before this change; no output, exit code, or
filesystem behavior on these paths may regress.

#### Scenario: Non-`--apply` behavior is unaffected

- GIVEN any pre-existing CliRunner assertion on `adjudicate`, `adjudicate
  --json`, or `adjudicate --same-only`
- WHEN that command runs after this change
- THEN the assertion still passes unchanged

### Requirement: `--apply-same` Eligibility Filter

`adjudicate --apply-same` MUST include a group in the batch ONLY when
`verdict == SAME` AND the group has exactly 2 `member_ids`. DIFFERENT and
UNCERTAIN groups MUST NEVER be included. A SAME group with more than 2
members MUST be skipped (not merged), mirroring `--apply`'s N>2 handling.

#### Scenario: Mixed report yields only SAME 2-member pairs

- GIVEN a report with SAME 2-member, SAME 3-member, DIFFERENT, and UNCERTAIN
  groups
- WHEN `adjudicate --apply-same` runs
- THEN only the SAME 2-member groups appear in the batch

#### Scenario: SAME group with >2 members is skipped

- GIVEN a SAME-verdict group with 3 members
- WHEN `adjudicate --apply-same` runs
- THEN that group is never merged and is reported as skipped

#### Scenario: DIFFERENT/UNCERTAIN groups are skipped

- GIVEN a DIFFERENT-verdict group and an UNCERTAIN-verdict group
- WHEN `adjudicate --apply-same` runs
- THEN neither group is merged

### Requirement: Aggregate Preview Before Any Write

Before any write, `adjudicate --apply-same` MUST print a preview listing
EVERY eligible merge, one per line in the form
`merge <absorbed> into <survivor> ...`, followed by the total eligible
count. No write MUST occur before the confirmation gate is resolved.

#### Scenario: Preview lists all eligible pairs and the count

- GIVEN 3 eligible SAME 2-member groups
- WHEN `adjudicate --apply-same` runs
- THEN stdout lists all 3 pairs before any prompt
- AND stdout shows the total eligible count
- AND no filesystem write has occurred yet

### Requirement: Typed-Count Confirmation Gate

The confirmation gate MUST be resolved in this order:

0. A `--confirm-count` value that is not a whole number (empty or
   non-numeric) MUST be refused (exit 2) BEFORE candidate discovery and
   before any model call (issue #779): it cannot possibly match any
   count, and validating it last made the cheapest possible check the
   most expensive step in the workflow.
1. If `--confirm-count <value>` is supplied on the command line, proceed
   ONLY when `value.strip()` exactly equals the eligible-merge count; a
   wrong number MUST abort with ZERO BUNDLE writes (persisted verdicts
   are derived state under `.openkos/`, written by the adjudication
   itself before this gate -- issue #779).
2. Else, if stdin is a TTY, print the full aggregate preview, then prompt
   the operator to type the exact eligible-merge count; the same
   exact-match-or-abort-zero-writes rule applies.
3. Else (non-TTY and no `--confirm-count`), `adjudicate --apply-same` MUST
   REFUSE with exit code 1 and ZERO writes — it cannot confirm unattended
   without the explicit flag.

Unattended/scripted apply IS possible, but ONLY via an explicit exact
`--confirm-count` match; it is never a silent bypass.

#### Scenario: `--confirm-count <exact>` proceeds

- GIVEN 3 eligible pairs and `--confirm-count 3`
- WHEN `adjudicate --apply-same` runs
- THEN all 3 merges proceed without any interactive prompt

#### Scenario: `--confirm-count <wrong/empty/non-numeric>` aborts with zero writes

- GIVEN 3 eligible pairs and `--confirm-count 2`, `--confirm-count 4`,
  `--confirm-count ""`, or `--confirm-count yes`
- WHEN `adjudicate --apply-same` runs
- THEN the run aborts and zero merges are written

#### Scenario: TTY prompt with exact count typed proceeds

- GIVEN 3 eligible pairs, no `--confirm-count`, a TTY stdin, and typed
  input `"3\n"`
- WHEN `adjudicate --apply-same` runs
- THEN the full aggregate preview is printed, then all 3 merges proceed

#### Scenario: TTY prompt with empty input aborts with zero writes

- GIVEN eligible pairs, no `--confirm-count`, a TTY stdin, and typed input
  `"\n"`
- WHEN `adjudicate --apply-same` runs
- THEN the run aborts, zero merges are written, and the workspace is
  byte-identical to before the run

#### Scenario: TTY prompt with wrong or non-numeric input aborts with zero writes

- GIVEN 3 eligible pairs, no `--confirm-count`, a TTY stdin, and typed
  input `"2\n"`, `"4\n"`, or `"yes\n"`
- WHEN `adjudicate --apply-same` runs
- THEN the run aborts and zero merges are written

#### Scenario: Non-TTY without `--confirm-count` refuses

- GIVEN eligible pairs, no `--confirm-count`, and a non-interactive/non-TTY
  invocation
- WHEN `adjudicate --apply-same` runs
- THEN the run refuses with exit code 1 and zero merges are written

### Requirement: Sequential Execution And Mid-Batch Failure Semantics

On an exact-match confirmation, accepted merges MUST execute sequentially,
reusing the shipped per-pair `prepare_merge`/`merge_core`/`_autocommit`
body, and each MUST land a `merged_from` ledger entry. A mid-batch failure
MUST stop the run but MUST KEEP already-committed merges intact, and the
final report MUST show what was applied versus not attempted.

#### Scenario: Mid-batch failure stops but keeps prior commits

- GIVEN 3 accepted pairs where the 2nd pair fails during `merge_core`
- WHEN `adjudicate --apply-same` runs
- THEN pair 1 remains applied and committed, pair 2 fails with a clear
  error, pair 3 is never attempted, and the run reports what was applied
  versus not

### Requirement: Stale-Id Guard Across Batch

`adjudicate --apply-same` MUST re-verify both member ids of each accepted
pair immediately before applying it. If an earlier merge in the same batch
already absorbed a member that a later pair references, that later pair
MUST be skipped (not crash), and the skip MUST be clearly reported.

#### Scenario: Shared-member pairs are handled without crashing

- GIVEN two eligible SAME pairs sharing one member id
- WHEN `adjudicate --apply-same` applies the first pair
- THEN the second pair is skipped or safely re-resolved, the run does not
  crash, and the skip is clearly reported

### Requirement: Reversibility Via Sequential Unmerge

Every merge applied by `adjudicate --apply-same` MUST be reversible via the
existing `unmerge` command, following the same LIFO per-survivor semantics
as `--apply`. No batch-undo command is provided.

#### Scenario: Batch round-trips via sequential unmerge

- GIVEN a batch of N applied merges
- WHEN `unmerge` is run N times in the correct LIFO order per survivor
- THEN the workspace is restored to byte parity with its pre-batch state

### Requirement: `--apply-same` Mutual Exclusion With `--apply` And `--json`

`adjudicate --apply-same` MUST be mutually exclusive with `--apply` and
with `--json`. Supplying more than one of these flags together MUST be
rejected with a clear stderr message and exit code 2, mirroring the
existing `--apply`/`--json` mutual-exclusion pattern.

#### Scenario: `--apply-same --apply` exits 2

- WHEN `adjudicate --apply-same --apply` runs
- THEN stderr contains a clear rejection message and the exit code is 2

#### Scenario: `--apply-same --json` exits 2

- WHEN `adjudicate --apply-same --json` runs
- THEN stderr contains a clear rejection message and the exit code is 2
