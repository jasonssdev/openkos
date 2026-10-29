# Results: keep the meeting Event beside its subjects (#1689)

Registration: `PREREGISTRATION.md` (merged in #1691, commit `74fbd2d7`), run
unchanged. Run at commit `74fbd2d7d0d7fd889422baddcb73bd54d4231ec4` plus the
`ADJUDICATION` registry added by this change. Zero model calls, zero GPU:
both commands ran with `OLLAMA_HOST=127.0.0.1:1` (a closed port). Raw
`score`/`decide` output stays outside the repository.

## Verdict

**ADVANCE-WITH-CAP-EXEMPTION.** B0 to B3 hold, B4 fails on the English
population. This licenses only a later generation arm (which measures the
Event body) using the cap-exempt placement. It is not an adoption and not a
recommendation to change a default.

## Records

| Population | Scored | Failed | Excluded |
|---|---|---|---|
| Spanish (framing-floor, `qwen3:8b`, 5 sources x 15 runs) | 75 of 75 | 0 of 75 | 0 |
| English (stored framing-shaped records, de-duplicated) | 254 of 254 | not applicable (records with no raw stage are never loaded) | 0 |

## Counts

| | Spanish | English |
|---|---|---|
| records | 75 | 254 |
| a0 empty | 8 of 75 | 10 of 254 |
| a0 non-empty | 67 of 75 | 244 of 254 |
| qualifies | 67 of 67 | 231 of 244 |
| cap cut (a0 already holds 6 or more) | 10 of 67 | 153 of 231 |
| displacement violations | 0 of 75 | 0 of 254 |
| cardinality violations | 0 of 75 | 0 of 254 |

Kept objects: every one is an `Event` (the only candidate type). 298 kept
(67 + 231). Distinct (type, title) pairs: 18 Spanish, 121 English, 138
overall (one pair, `Meeting`, appears in both). Titles repeat heavily:
the most common Spanish pairs are `Reunion de Diseno Funcional` (14 of 67) and
`Reunion de presentacion del prototipo` (14 of 67); English, `Meeting` (16 of
231) and `Product Design Meeting` (11 of 231). Runs of one source are
correlated, so 298 is not 298 independent observations.

## Bars

| Bar | Rule | Spanish | English | Result |
|---|---|---|---|---|
| B0 opportunity | qualifies / a0 non-empty at least 0.10 on each | 67 of 67 (1.000) | 231 of 244 (0.947) | holds |
| B1 no displacement | 0 violations on both | 0 | 0 | holds |
| B2 cardinality | 0 violations on both | 0 | 0 | holds |
| B3 container share | at least 0.90 pooled, contested against | 67 of 67 | 224 of 231 | holds: 291 of 298 (0.977) |
| B4 reach under cap | cap cut / qualifies at most 0.25 on each | 10 of 67 (0.149) | 153 of 231 (0.662) | **fails** (English) |

Reading B4: appending the Event after the subjects would lose it to the cap in
about two thirds of English qualifying cases, because the shipped pipeline
already holds 6 or more subjects there. The Spanish population (0.149) is
under the bar. That asymmetry is the whole reason for the verdict.

## Adjudication (disclosed)

**A delegated agent adjudicated, not the owner**, as the registration allowed
(item 7 of "The owner confirms"). The registry was filled between `score` and
`decide`, from each kept object's type, title and description only, never its
model or run, and `decide` was run once afterwards. It is in
`measure_meeting_event.py` (`ADJUDICATION`), one entry per distinct
(type, title) pair, 138 entries.

Rule applied: a title that names one gathering (a meeting, optionally with
its topic area) is a **container**; a title that names a decision or outcome
of the gathering is **contested** (counts against B3). Seven pairs were
contested, 7 of 298 kept objects, all English except one Spanish pair:
`Meeting on Functional Design Agenda and Teletext Removal Impact`,
`Meeting on functional design and teletext removal`, `Meeting regarding final
proposal circulation`, `Meeting to Decide Movie and Advertising Content`,
`Meeting to Decide on Movie and Advertising Content`, `Meeting to Decide on
Remote Control Concept`, `Reunion de decisiones tecnicas`. No pair was
classified as a plain subject. The registry applies the rule mechanically to
titles; the descriptions of the generic titles (`Meeting`, `Meeting
Discussion`) were read and all describe a gathering. The per-title class is
the same for every run that shares a pair, so a different reading of a
borderline title moves B3 by that title's count, not by one. Even if all
seven contested objects were counted as subjects, which is how they are
counted here, the share is 0.977; B3 would still hold with about 22 more
objects reclassified (0.90 of 298 is 269).

The Spanish kept objects' titles are mostly generic ("Meeting TS3009c-es",
"Reunion de Trabajo"), several naming the source file; the registration's own
limit applies (a title can be reconstructed from the source, #522), and B3
does not test that.

## Limits (as registered, restated)

- **The Event body was not measurable.** The stored records carry type, title,
  description and body length, not body text. A kept Event whose body restates
  the source, or whose links point nowhere, would pass every bar. A verdict
  here can only advance a generation arm; it can never license adoption.
- Stage-local: after the source-title twin rule. The sole-object re-ask
  (#584), the ceiling, judge and backstop are not replayed, and `a1` can change
  whether a reply has exactly one surviving object.
- Title grounding is a lexical proxy for a faithful Event, not a quality
  judgment; titles like `Meeting` are maximally weak and still count as
  containers here. The selection rule had little to choose from in many cases.
- Cardinality "one per source" is assumed; how a re-ingest of the same meeting
  reconciles with an existing Event is not replayable.
- One corpus family (AMI, its machine Spanish translation, plus a few prose
  fixtures), `qwen3:8b` and other stored-run models at production defaults.
  Other models and human-written meetings may differ. The English population
  mixes records from several experiments, so its prevalence is not a rate for
  any single production workload.
- The cap-exempt placement itself is not replayed; this verdict only says the
  append-after placement is not enough on English, not that the exemption is
  harmless. Exempting the Event changes where the cap counts and needs its own
  measurement.
- Classification was delegated and mechanical on titles; a re-read after the
  verdict is a new registration.

## Deviations

None from the registered commands, populations, bars or verdict vocabulary.
The harness changed only by filling `ADJUDICATION` (and its docstring), which
the registration prescribes.
