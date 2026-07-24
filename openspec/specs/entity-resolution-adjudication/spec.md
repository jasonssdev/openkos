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

### Requirement: Machine-Readable `--json` Output Mode

`openkos adjudicate` MUST accept a `--json` flag. When set, on the SUCCESS
path, stdout MUST be a single valid JSON array, one object per entry in the
full `results` set (independent of `--same-only`), with EXACTLY these fields
per object: `member_ids` (list of strings, already sorted), `okf_type`
(string), `tier` (`"HIGH"` or `"LOW"`), `verdict` (`"SAME"`, `"DIFFERENT"`, or
`"UNCERTAIN"`), `rationale` (string). The object MUST NOT contain a
`confidence` field or any survivor/absorbed field.

#### Scenario: Exact field set, no confidence

- GIVEN adjudication results with mixed verdicts
- WHEN `adjudicate --json` runs
- THEN each parsed JSON object has exactly the keys `member_ids`, `okf_type`,
  `tier`, `verdict`, `rationale`
- AND no object contains a `confidence` key

#### Scenario: Example mixed-verdict payload

- GIVEN two candidate groups, one SAME and one DIFFERENT
- WHEN `adjudicate --json` runs
- THEN stdout parses to:
  ```json
  [
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
  ```

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
verdict. `adjudicate --json --same-only` MUST filter the emitted array to
objects where `verdict == "SAME"` only.

#### Scenario: `--json` alone includes all verdicts

- GIVEN results with SAME, DIFFERENT, and UNCERTAIN verdicts
- WHEN `adjudicate --json` runs
- THEN the parsed array contains one object per result, all verdicts present

#### Scenario: `--json --same-only` filters to SAME

- GIVEN results with SAME, DIFFERENT, and UNCERTAIN verdicts
- WHEN `adjudicate --json --same-only` runs
- THEN the parsed array contains only objects with `"verdict": "SAME"`

### Requirement: Empty State Emits Valid Empty Array Under `--json`

WHEN there are no candidate groups, OR `--same-only` filters every result out,
`adjudicate --json` MUST emit stdout that is a valid empty JSON array `[]`,
NOT the plain-text "no candidates" message used in the non-JSON path.

#### Scenario: No candidates, `--json`

- GIVEN a bundle with no candidate groups
- WHEN `adjudicate --json` runs
- THEN `json.loads(stdout) == []`

#### Scenario: `--same-only` filters all results out, `--json`

- GIVEN results containing zero SAME verdicts
- WHEN `adjudicate --json --same-only` runs
- THEN `json.loads(stdout) == []`

### Requirement: Deterministic, Pretty-Printed JSON

The JSON array MUST preserve the order of the `results` set (no re-sorting),
with `member_ids` already sorted, and MUST be pretty-printed with `indent=2`.
Identical input MUST yield byte-identical stdout across runs.

#### Scenario: Stable ordering across runs

- GIVEN the same fixture bundle and model responses
- WHEN `adjudicate --json` runs twice
- THEN both stdout outputs are byte-identical and array order matches
  `results` order

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
