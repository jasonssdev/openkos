# Status: COMPLETE — slices 1, 2 and 4 SHIPPED, slice 3 closed UNSHIPPED (2026-08-16)

## What has shipped

| Slice | State |
| --- | --- |
| 1 — volume eval | SHIPPED, PR #716. Its Phase 0.2 gate returned **REJECT** on D2. |
| 2 — reverse the stub rule | SHIPPED. The anchor gate is retired. |
| 3 — two-lane budget | **CLOSED UNSHIPPED.** Its gate — D4's reopen trigger — was tested and does not exist. |
| 4 — advisory name grounding | SHIPPED. |

## What slice 1 decided, and what survived it

The Phase 0.2 gate REJECTed the D2 capture-prompt treatment: latency 1.92x
baseline (104.7s vs 54.6s) and merely-named count 0 vs 0. Per D2 a rejection
ships nothing prompt-level, so **task 2.4 is closed unshipped**. The rewrite
stays in `evals/named_person_volume/` as a reproducible monkeypatch.

Nothing else in the design was invalidated. D1/D3/D5/D6/D7 and all three
delta specs stood, and slices 2 and 4 are exactly D6 and D5.

## The owner ruling that unblocked slice 2

#712 carried an unresolved CONTRACT CONTRADICTION, stated in the issue itself:
`_PARTICIPANT_CAPTURE_SYSTEM_PROMPT` accepts "spoke in this meeting" as an
anchor, under which no speaker can ever be rejected, while D4/D5's stated
purpose is anti-flooding.

**Ruled: retire the anchor gate.** Identification grounds on the NAME appearing
in `source_text`, not on a role phrase in the model's own paraphrase.
Anti-flooding moves to the participant budget lane, which is where a volume
concern belongs — a lexicon was never going to carry it.

That ruling is what D6 and D5 already described, from opposite ends: D6 deletes
the gate, D5 adds the grounding that reads the source instead.

## Why slice 3 is closed unshipped

D3/D4 specify a participant budget lane whose entire purpose is to stop
participants evicting subjects from `_UNION_BACKSTOP`. D4 resolved its
truncation ordering *conditionally* and named the condition itself: "If D1
measures `p_max` well under the chosen capacity, ordering is moot", with a
reopen trigger of "a stored run whose participant lane actually truncates, or a
field report of participant-lane truncation on a real bundle".

That condition was tested against every stored run this repo has, on
2026-08-16:

| harness | runs | largest retained set | participant candidates |
| --- | --- | --- | --- |
| `participant_anchor` (`--participants` on) | 9 | 7 | max 5 |
| `named_person_volume` | 12 | 4 | `p_max` = 3 |
| `participant_language` | 24 | — | max 5 |
| `stage_attrition` | 45 | 9 | — |
| all other harnesses (JSON records) | 337 | 14 | — |

`_UNION_BACKSTOP` is **20**. It has never bound, and has never come within six
objects of binding; the single 14 belongs to the title-first treatment arm that
#728 rejected. `concept.py:3086` sets `produced = len(kept)` and
`retained = len(kept[:20])`, so the two fields — and therefore
`_extraction_cap_notice`, which fires on their divergence — can only differ when
that cap cuts. The conflation D3 exists to prevent is unreachable from any
observed state.

`_PARTICIPANT_BACKSTOP = max(8, ceil(1.5 * 3)) = 8` is itself a floor, not a
measurement: the 8 won. The lane would bound a population that peaks at 5.

Tasks 3.1 and 3.2 sharpen the point. They add a `schema` marker to `RunRecord`
and make `--rescore` refuse mixed-schema comparisons — a migration whose only
purpose is to protect stored eval runs from the re-basing that tasks 3.5/3.6
would themselves introduce. If the lane never truncates, that cost buys nothing
at all.

**Owner ruling 2026-08-16: close slice 3 unshipped with this evidence, not
deferred** — the same disposition as task 2.4, and the same discipline this
repo applied to six rejected prompt treatments (#613, #622, #715 slice 1, #713's
first shape, #699 carry-titles, #728 title-first). Nothing about the lane is
lost: D3/D4 stay in `design.md`, `_PARTICIPANT_BACKSTOP`'s derivation stays in
`evals/named_person_volume/report.md`, and D4's reopen trigger is still the
condition that would revive it.

At archive, the two delta requirements that describe only slice 3 were
DROPPED rather than merged, so `openspec/specs/` never claims behavior that
does not exist: `Participant Budget Lane Separate From the Subject Backstop`
(from the extraction-union-judge delta) and the whole ingestion delta
(`Participant-Lane Truncation Is Disclosed`).

## The earlier blocking analysis, superseded by the above

The two-lane budget exists to stop participants crowding subjects out of
`_UNION_BACKSTOP`. `evals/stage_attrition` (#715, PRs #717/#718) settled that
they are not competing at all:

- Baseline reproduces zero retained subjects on **9 of 9 runs**, three fixtures.
- `_extract_once` returns exactly ONE subject candidate per call on a
  meeting-shaped source — the Event naming the meeting — which
  `_drop_framing_objects` then correctly deletes.
- The backstop is 20 and only 3-5 objects are retained, so no capacity limit
  binds.

Subjects are not crowded out; **generation never produces them**. Building the
lane now would still fix a competition that is not happening.

That defect (#715) has its own authorized lever, and that lever is itself
blocked on #714 — a 16 KB transcript blows the 8192 generation ceiling once the
prompt asks for several objects. So the unblocking order is **#714 → #715 →
slice 3**, and slice 3 should not be started before the first two land.

Note that the blocking order recorded above (#714 → #715 → slice 3) did
complete: both #714 and #715 shipped on 2026-08-15, and #715's clause raised
subjects/run from 0.78 to 1.89. Subjects now exist, so the lane's premise
became testable for the first time — and the test above is what returned no.

## What would revive slice 3

D4's reopen trigger, unchanged: a stored run whose participant lane actually
truncates, or a field report of participant-lane truncation on a real bundle.
Concretely, a run retaining more than 20 objects, or one where
`_extraction_cap_notice` fires on a set containing `Person`/`Organization`
candidates. Re-read `design.md` D3/D4 and slice 1's report if either appears —
both are intact.

Independently, speakers-first ordering is still not implementable: it needs a
"did this person speak" signal, and the only candidate proxy
(`_PARTICIPANT_ANCHOR_RE`) was measured unreliable in both directions by #706.
