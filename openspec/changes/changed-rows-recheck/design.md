# Design: A Changed Decision Can Be Checked Again, And A Decision About A Missing Page Is Never Offered

Inputs: `proposal.md`, issue #1475 items 1 and 2, the specs `curate-decisions`,
`pending-work`, `judging-application-service`, `stable-python-api` and
`desktop-review`, and ADR-0053, ADR-0059, ADR-0060 and ADR-0066.

## Evidence

On a copy of the reporter's workspace (`identity:39fec53a…`, `concepts/tool` and
`concepts/tools`, judged same, then `concepts/tool` rewritten by a `relate`):

| Step | Result |
| --- | --- |
| `list_decisions` | `fresh=False`, `judged_same`, actions merge, keep_distinct, skip |
| `preview_decision(MERGE)` then `apply_decision` | `Stale("row_inputs_changed")` |
| `quote_judging("identity")` | the pair is among `to_judge` (its stored verdict's digest for `concepts/tool` no longer matches) |
| `run_judging` with a scripted judge | completed, verdict persisted; the row still lists `fresh=False` |
| `refresh_decision_queue` | identity `replaced=2`; the row lists `fresh=True`, judged same, mergeable |

28 of the 33 open rows name a page absent from the bundle; 2 of them are
contradictions found while combining.

## Decisions

### D1. Preview refuses a changed row with the apply's `Stale`

Two options: the list withholds every write action of a changed row, or the
preview refuses it. The list's contract is "a stale item is listed, never hidden,
and an apply against it is Stale" (`curate-decisions`, "Staleness Is Marked,
Never Hidden"); withholding would add a withheld reason to the public
`WithheldAction` literal and change what `actions` means for a row a client may
already render. The preview refusal reuses an existing reason a client already
handles (`row_inputs_changed`, already mapped to 409 `stale`), so preview and
apply agree with no new vocabulary. The check runs after the action checks, so an
action the row never offered is still `NotApplied`, and skip, which writes
nothing, still answers `NotApplied("skipped")`. Merge, merge-group, relate,
set-tier and keep-distinct are all refused, because every one of their commits
refuses the same row.

### D2. The refresh is the publisher; the screen runs it around every check

The run's write-through is amend-only on purpose (judging-application-service,
pending-work "Writing A Verdict Through …"): replacing a row whose inputs moved
retires a row and inserts one, which is a producer's statistics, not a judge's.
The model-free refresh (#1391) is that producer, and it already replaces a
changed row from the persisted verdict. So the engine does not change here; the
screen calls `POST /v1/queue/refresh` before quoting (a changed row whose verdict
is already servable becomes decidable at no cost) and after a run ends (the fresh
verdicts are published). A refresh failure is ignored, as after a reopen: the
list is read either way.

### D3. Check again, for the decision on screen

A changed decision's options are **Check again** and **Decide later**. Check
again refreshes and reloads; a decision gone from the list is "These pages
changed, and this question no longer applies."; a decision now fresh is shown as
it is, with its own options; a decision still changed opens the consent for its
kind (`POST /v1/judging/quote` with the decision's kind). The consent is the
batch consent Check all shows, so spending is still agreed in bulk and never per
item (brief, rule 6), and checking still never combines anything. The consent's
heading names what is checked by kind: pairs, connections or suggestions.

### D4. No one-item quote

A quote scoped to one decision key would let Check again reach a changed group
the candidate search no longer retains (the search keeps its first 50 groups on
that copy, of 59). It would need the scope in the quote token, a run that judges
outside the search, and a publisher that replaces a row the search does not
report — three surfaces for a corner case. A changed row of that kind stays
listed as changed, offering Check again (whose consent then says nothing is left
to check) and Decide later. Tracked as a known limitation.

### D5. Refresh retires every open identity and relation row naming a missing page

The retirement runs inside a commit section, before the producers, and checks
absence there, so a page restored between a read and the write (an unmerge) is
not mistaken for gone. Absence is "the concept path does not exist"; an
unreadable path is not absence, and an id that is not a concept path is ignored.
Only targets count, never input refs, which may be synthetic. Only `identity`
and `relation_type`: their actions write to the pages they name, so a missing
page can never be decided; a `contradiction` names an absorbed page on purpose,
and `volatility`, `revision` and `watch_refusal` do not name concepts that must
exist. It is independent of completeness: absence of a page is proven by the
bundle, not by a pass reaching a row. It is model-free and reconstructible: it
reads the bundle and writes only the queue.

### D6. The screen refreshes on each visit

`main.ts` calls `ReviewModel.visit()`, which refreshes and then loads, on every
visit to To review, which already reads everything again on each visit. Rows
naming a missing page are therefore gone before the list is drawn, and a changed
row whose verdict is already servable is decidable without any check.

## Sequence: Check again

```
Person        Review model            Local API
  | Check again   |                       |
  |-------------->| POST /queue/refresh   |
  |               |---------------------->| (model-free)
  |               | GET /decisions, pages |
  |               |---------------------->|
  |               | still changed?        |
  |               |  yes: POST /judging/quote {kind}
  |               |---------------------->|
  |  consent      |                       |
  |<--------------|                       |
  | Check n       | POST /judging/runs    |
  |-------------->|---------------------->| ... poll ...
  |               | POST /queue/refresh   |
  |               | GET /decisions        |
  |  decision as it now is                |
  |<--------------|                       |
```

## ADR gate

No decision here is both significant and hard to reverse: D1 reuses a Stale
reason, D2 and D3 are screen behaviour, D5 is a refresh rule a later change can
widen or narrow. No ADR.
