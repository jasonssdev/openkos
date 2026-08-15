# Design: Always Identify Named People (#712)

## Technical Approach

Four measure-gated slices. Slice 1 is eval-only and produces the one number
slices 3–4 need. Slice 2 flips one prompt paragraph and deletes one boolean
conjunct. Slice 3 splits a single positional slice into two lanes with their
own disclosure. Slice 4 adds an advisory-only, non-rejecting grounding signal.
`_SYSTEM_PROMPT` (`concept.py:127-139`) and `test_concept.py:1488` are never
touched; the lever is `_PARTICIPANT_CAPTURE_SYSTEM_PROMPT` (`concept.py:1956`).

**Read confirmed, and it changes D5**: `normalize_names_cmd`
(`cli/main.py:7113`) normalizes on-disk *filenames* to Unicode NFC (#474). It
does no person-name canonicalization whatsoever. There is nothing to reuse and
nothing to duplicate. Its NFC precedent does inform D5's accent handling.

## Architecture Decisions

### D1 — The slice-1 eval

**Choice**: new `evals/named_person_volume/`, `--self-test` runnable with no
model, stored JSONL under `results/`, `--rescore` re-derives every verdict from
stored runs, treatment applied as a monkeypatch on the module constant with
production untouched (the `evals/language_leak` + `participant_anchor` shape).
qwen3:8b, 2 fixtures × 2 arms × 3 runs, `_MAX_GENERATION_TOKENS = 8192`, `-u`.

| Item | Value |
|---|---|
| Fixtures | `es-bare` (no source states any role — reuse #706's shape) and AMI `evals/decision_extraction/sources/TS3005a.transcript.txt` (real corpus, single-letter labels) |
| Arms | `baseline` = shipped capture prompt; `treatment` = D2 rewrite |
| Metric A | distinct `Person`/`Organization` in `retained`, per run |
| Metric B | of those, how many are *merely named* — by hand-written `adjudication.json`, never regex-derived (#706 precedent) |
| **Metric C** | **subject recall**: `Decision`/`Event`/`Concept`/`Procedure` titles in `retained` matched against a hand-written expected-subject list per fixture |
| Metric D | `produced`/`retained`, `judge_status`, run latency |
| Emitted capacity | `p_max` = max participant occupancy over all treatment runs; lane capacity = `max(8, ceil(1.5 × p_max))`, recorded in the report as derived, not chosen |

**Rationale**: metric C exists because a prompt that wins on people while
losing a Decision would otherwise read as a pass. Alternatives rejected:
re-running `evals/participant_anchor` (closed, forbidden); person count alone
(unfalsifiable); a synthetic-only corpus (AMI is where single-letter labels and
the real failure live).

### D2 — Capture-prompt rewrite

**Choice**: replace the anchor-demand paragraph with a permission paragraph.

| Guarantee | Fate |
|---|---|
| "Only report a participant you can anchor… A name alone… is NOT a valid answer" | **Removed** |
| "do not promote a passing mention with no stated role or affiliation" | **Removed** |
| Closed two-value vocabulary (`"Person"`\|`"Organization"`) | Preserved verbatim |
| Empty array `[]` is CORRECT and EXPECTED | Preserved verbatim |
| "Do not invent a participant" | Preserved **and strengthened** to "use only names the source itself writes" |
| JSON-only, no fences, no outer object | Preserved verbatim |

New semantics: report every person or organization the source *names* as
present or represented, including one who never speaks and is only named;
state whatever role, affiliation, or relation the source gives, and when it
gives none, say so plainly instead of omitting the person or guessing a role.

**Rationale**: the anchor demand was doing double duty as an invention brake,
so removing it without strengthening the no-invention clause would trade a
suppression defect for a fabrication defect.

**REJECT rule** (any one rejects the treatment): subject recall (metric C)
drops below baseline on either fixture; run latency ≥ 1.5× baseline (#563's
0.69→0.63-for-double-latency bar); merely-named person count does not increase
over baseline (no benefit bought); any proposed name absent from the source on
a name-bearing fixture (fabrication). Rejection ships nothing prompt-level and
the treatment stays in the harness as a reproducible monkeypatch.

### D3 — Two-lane budget

**Choice**: new `_PARTICIPANT_BACKSTOP` constant (value from D1), sliced
independently at `concept.py:2809`. `_UNION_BACKSTOP = 20` stays the SUBJECT
ceiling. Rebuild `retained` by walking `kept` in order and keeping members of
both lane sets, so downstream ordering (`_sole_object_restates_source`, file
write order) is unchanged in shape.

`produced` / `retained` / `discarded_titles` become **subject-lane only**. New
fields `participant_produced`, `participant_retained`,
`participant_discarded_titles`, plus a new `_participant_lane_notice()` beside
`cli/main.py:3055`, wording distinct from `_extraction_cap_notice`
(`cli/main.py:3072`) — e.g. "N of M named participant(s) kept (participant lane
full); not recorded: …".

**Alternative rejected**: keep the existing triple as whole-set totals.
`_extraction_cap_notice` triggers on `produced > retained` and says "extracted
object(s) … (cap reached)", so a participant-only truncation would print as a
subject cap notice — precisely the conflation this decision forbids.

**Consequence 1, must become a task**: narrowing `produced` changes an existing
field's meaning. Enumerate every reader of
`ExtractionReport.produced`/`.retained`/`.discarded_titles` before implementing.

**Consequence 2 — silent re-basing of stored eval runs.**
`run_participant_anchor_probe.py:438-439` records `produced`/`retained` into
`RunRecord`. The file keeps running after this change, so nothing breaks; the
numbers simply stop counting participants, while sitting in the same
`results/` directory under the same field names. A later reader comparing a
pre-change and a post-change run would silently compare two different
quantities.

**Handling — a schema marker on the record, not a note in prose**: add a
`schema: int` field to `RunRecord` (absent ⇒ `1` = whole-set counts; `2` =
subject-lane counts), stamped at write time. `--rescore` refuses to aggregate
records of mixed `schema` across one comparison and says why. Alternatives
rejected: a disclosed note in the eval report (a reader who never opens the
report still gets a wrong comparison — this project has been bitten by exactly
that); renaming the fields in `RunRecord` (breaks re-reading old JSONL, which
is the property that makes the record immutable and useful). The same marker
covers D6's bucket-string rename, so one mechanism handles both re-basings.

### D4 — Lane truncation ordering (resolved conditionally)

**Choice**: positional first-N in each lane — the status quo semantics, no new
policy. If D1 measures `p_max` well under the chosen capacity, ordering is
**moot** and any speakers-first rule would be unmeasured policy.

**Reopen trigger** (name it in the eval report): a stored run whose participant
lane actually truncates, or a field report of participant-lane truncation on a
real bundle. Independently, speakers-first is not implementable today — it
needs a "did this person speak" signal, and the only candidate proxy
(`_PARTICIPANT_ANCHOR_RE`) was measured unreliable in both directions by #706.

### D5 — Name grounding (advisory only)

**Choice**: `_names_absent_from_source(results, *, source_text) -> tuple[str, ...]`,
report-only, never rejecting. Comparison reuses the module's deliberately-dumb
idiom (`" ".join(value.casefold().split())` + substring, as at
`concept.py:1133-1136`), extended with NFD decomposition + combining-mark strip
on **both** sides so `Germán` matches `German`.

**Label-only exemption**: skipped entirely when every speaker label the
`_transcript_shaped_text` label regex (`concept.py:297-310`) finds is ≤2
characters. On such a source the field is `()`, never a flood of false alarms
across every AMI participant.

| Case | Behavior | Trade |
|---|---|---|
| `Germán` vs `German` | matches | bought by NFD strip |
| source `G. Vega` → proposed `Germán Vega` | flagged | **accepted false positive**; cost is a printed line, not a drop |
| source `Sepúlveda` → proposed `Jason Sepúlveda` | flagged | accepted, same reason |
| source full name → proposed surname only | not flagged | accepted false negative (permissive direction) |
| two people sharing a first name | not distinguished | out of scope — #668 D8 identity seam |

**Declared bias** (following `_strip_ungrounded_expansions`'s own precedent of
declaring its trade): this check prefers to miss an invented name over to
accuse a real one — the *inverse* of `_PARTICIPANT_ANCHOR_RE`'s bias. The
inversion is deliberate: the consequence changed from "drop a person" to
"print a line". Promotion to a rejecting filter requires its own eval and is
not in this change.

### D6 — `_has_participant_anchor` retained for measurement reproducibility

**Choice**: delete only `and _has_participant_anchor(c)` at `concept.py:2772`,
leaving `c.type == _TWIN_EXEMPT_TYPE or meeting_shaped`. **Keep** the function
and `_PARTICIPANT_ANCHOR_RE` as exported symbols, with docstrings rewritten to
state they are no longer a gate and survive only as measurement inputs.

**Why keep them — the reproducibility argument, not the import argument**:
`run_participant_anchor_probe.py --rescore` calls `_PARTICIPANT_ANCHOR_RE.search`
at line 546 to decide which stored candidates *the shipped lexicon already
admits*, and `_has_participant_anchor` at line 425 to stamp `anchored` on each
recorded candidate. Delete either and #706's verdict stops being re-derivable
from its own stored runs — the property `report.md` asserts in its opening
lines ("every number below is re-derivable from `results/*.jsonl` with
`--rescore`"). An import that merely breaks could be fixed by editing the read
site; a measurement that can no longer be reproduced cannot.

**Scope boundary corrected.** The immutable artifacts are
`evals/participant_anchor/report.md` and everything under `results/` — the
measured record of #706. `run_participant_anchor_probe.py`, `README.md`, and
`adjudication.json` are **maintainable**: updating a read site so the harness
keeps running is maintenance, not rewriting a result. The earlier
whole-directory reading of the proposal's out-of-scope item was too broad and
is superseded here.

**#668 D1 check (deletion and additive sites must not share one predicate)**:
passes. `_has_participant_anchor` is documented as used only at judge
re-admission and never by the twin-drop or framing-drop deletion sites; after
this change it has zero production consumers, so no shared predicate arises.

**Knock-on, and the contradiction it created, resolved**:
`participant_anchorless_discarded_titles` keeps computing on non-meeting-shaped
sources, where `_participant_stub_notice`'s wording ("no role, affiliation, or
relation cue beyond the name") becomes false — so the field and notice are
renamed to `participant_unreadmitted_discarded_titles` with wording naming the
real cause (the source is not meeting-shaped). That rename has two verified
read sites, both of which are maintained in the same slice:

| Read site | Fix |
|---|---|
| `evals/participant_anchor/run_participant_anchor_probe.py:376` (`_bucket_of`) | Update the attribute; rename the bucket string `anchorless-discarded` → `unreadmitted-discarded` and note in `README.md` that stored runs predating this change carry the old label |
| `evals/decision_extraction/scripts/run_type_coverage.py:257` | Update the attribute; the local name `anchorless_discarded_total` follows |

Leaving the field name unchanged was considered and rejected: a field named
`anchorless` that no longer measures anchorlessness is the exact class of
silent-meaning drift D3 also guards against.

### D7 — Test strategy (strict TDD, RED first, `uv run pytest`)

| Test | Action |
|---|---|
| `test_concept.py:2887` re-admission-vs-selected | Rewrite: fixture Person carries no anchor and is still re-admitted |
| `test_concept.py:2925` anchorless discard | Rewrite: the same fixture is now re-admitted; discarded list is `()` |
| `test_concept.py:1488` | **Do not touch** — verbatim `_SYSTEM_PROMPT` pin |
| NEW | Bare-name Person on a NON-meeting-shaped source is still not re-admitted (proves only the second conjunct half was removed) |
| NEW | Lane isolation: participant overflow does not evict a subject, and vice versa; the two discarded lists are disjoint |
| NEW | `_participant_lane_notice` and `_extraction_cap_notice` can both fire in one run and neither text is a substring of the other |
| NEW | Grounding: absent name flagged; accented variant not flagged; label-only source computes nothing |
| NEW (harness `--self-test`) | `--rescore` refuses to aggregate `schema: 1` and `schema: 2` records in one comparison, and names the reason |

Every test that passes on its first run must be mutation-confirmed against its
exact target line, with `__pycache__` purged, before it is trusted.

## Data Flow

    _SYSTEM_PROMPT pass ──┐
                          ├─→ _dedup_merged ─→ _MAX_JUDGE_CANDIDATES ─→ judge.select
    capture prompt pass ──┘                                                   │
                                                                     re-admission
                                                            (meeting_shaped only)
                                                                              │
                                        ┌─────────── kept ─────────────┐
                            subjects[:_UNION_BACKSTOP]   participants[:_PARTICIPANT_BACKSTOP]
                                        │                              │
                          _extraction_cap_notice        _participant_lane_notice
                                                        + grounding advisory (report-only)

## File Changes

| File | Action | Description |
|---|---|---|
| `evals/named_person_volume/` | Create | D1 harness, `--self-test`, `--rescore`, `results/`, `adjudication.json`, `report.md` |
| `src/openkos/extraction/concept.py` | Modify | Capture prompt (D2), conjunct at `:2772` (D6), `_PARTICIPANT_BACKSTOP` + lane split at `:2809` (D3), `_names_absent_from_source` (D5), report fields |
| `src/openkos/cli/main.py` | Modify | `_participant_lane_notice` near `:3055`; rename `_participant_stub_notice` wording (D6) |
| `openspec/specs/extraction-union-judge/spec.md` | Modify | Delta (owned by sdd-spec) |
| `openspec/specs/ingestion/spec.md` | Modify | Delta (owned by sdd-spec) |
| `tests/unit/extraction/test_concept.py` | Modify | D7 |
| `docs/` | Modify | Grep and correct stale "Person needs an anchor" prose |
| `evals/participant_anchor/report.md`, `evals/participant_anchor/results/**` | **Immutable** | The measured record of #706; never restated by a later run |
| `evals/participant_anchor/run_participant_anchor_probe.py` | Modify (maintenance) | Renamed field at `:376`; `RunRecord.schema` marker; `--rescore` mixed-schema refusal |
| `evals/participant_anchor/README.md` | Modify (maintenance) | Document the `schema` marker and the old `anchorless-discarded` bucket label |
| `evals/decision_extraction/scripts/run_type_coverage.py` | Modify (maintenance) | Renamed field at `:257` |

## Testing Strategy

| Layer | What | Approach |
|---|---|---|
| Eval | Person volume, subject recall, capacity number | Manual harness, stored runs, `--rescore` |
| Unit | Re-admission, lane isolation, notice distinctness, grounding | `uv run pytest`, RED first, mutation-confirmed |
| Integration | `ingest` prints both notices without conflation | CLI-level test over a stubbed backend |

## Threat Matrix

N/A — no routing, shell, subprocess, VCS/PR automation, executable-file
classification, or process-integration boundary.

## Migration / Rollout

No data migration. Slices revert independently (proposal's rollback plan).
Slice 1 is eval-only; slices 2–4 are gated on its report.

## Open Questions

- [ ] Exact `_PARTICIPANT_BACKSTOP` value — set by D1, not before.
- [ ] Full reader list for `ExtractionReport.produced` (D3 consequence).
- [ ] ADR-0015 `{Person: 1}` sensitivity at higher Person volume — flagged, no
      ADR change proposed here.
