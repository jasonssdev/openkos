# A floor under the framing drop: the registered measurement

Result of the measurement registered in
[`PREREGISTRATION.md`](PREREGISTRATION.md)
([#1642](https://github.com/jasonssdev/openkos/issues/1642)), run with its
frozen commands and `measure_framing_floor.py`. Every bar is the registered one,
unchanged. Production code and defaults are untouched. Nothing from the private
corpus is committed: only counts, the titles of restored objects and verdicts.

## Verdict

| Arm | Verdict |
| --- | --- |
| `a0` | The shipped pipeline. The defect exists (B0 met). |
| `a1` (framing drop not applied when `a0` is empty) | **REJECTED** (B2 and B3 fail) |
| `a2` (wrong-language gate not applied when `a0` is empty) | **REJECTED** (B3 fails) |

Nothing is licensed. No production change follows from this run, and no default
changes.

## Inputs

| Item | Value |
| --- | --- |
| Repository commit | `b6bb7dce3ad902bd934b4e1d32e9b3068e6f3d76` (`main`, which contains the registration, `ccb522fe`) |
| Model | `qwen3:8b` (`DEFAULT_MODEL`), Ollama 0.32.9, `ollama list` ID `500a1f067a9f` |
| Model digest (recorded in every record) | `500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41` |
| Spanish population | 5 sources x 15 runs (seeds 1 to 15) = 75 records; 0 failed, 0 excluded |
| English population | 254 stored framing-shaped records (model-free replay) |
| Generation | `extract_concept_union`, `selector_judge` off, `transcript_decisions` off, via the screen's `run_source` |
| Phase 1 | `ES2015a-es` x 15 |
| Phase 2 | `ES2015d-es`, `IS1004b-es`, `IS1004d-es`, `TS3009c-es` x 15 |
| GPU wall time | 7.89 h (sum of per-record `wall_s`; elapsed 7.90 h including one model load) |
| Forecast | 5.5 to 7.6 h; the hard cap was 10 h (not reached) |

The run was 3% above the top of the forecast range (per-window time was above the
29.8 to 41.4 s assumed; `ES2015a-es` and `ES2015d-es` records took 100 to 725 s).

## Futility check (after phase 1)

`a0` zero rate on `ES2015a-es`: **8 of 15 = 0.533**, above the 0.20 stop. Phase 2
ran as registered. No arm number was consulted for this check.

## B0: the defect exists (precondition)

| Quantity | Observed | Required | Met |
| --- | --- | --- | --- |
| `a0` zero rate on `ES2015a-es` | 8 of 15 = 0.533 | >= 0.20 | yes |
| `a0` zero rate, all Spanish records | 8 of 75 = 0.107 | >= 0.10 | yes (narrowly) |

All 8 zero-object sources are `ES2015a-es`. The other four sources produced 0 of
60 empty results under `a0`. The pooled bar is met only because of the one
source #1642 was reported on; the defect did not reproduce on any of the other
four Spanish meetings.

## Table: Spanish population (n = 75)

| Quantity | `a0` | `a1` | `a2` |
| --- | --- | --- | --- |
| zero (empty final list) | 8 | 0 | 0 |
| fires (list differs from `a0`) | - | 8 | 8 |
| restored objects | - | 12 | 10 |
| restored, subject | - | 0 | 2 |
| restored, container (contested included) | - | 12 | 8 |
| distinct (source, type, title) triples restored | - | 7 | 8 |

`invariant_violations`: 0. No arm changed a source whose `a0` was non-empty.

## Table: English population (n = 254)

| Quantity | `a0` | `a1` | `a2` |
| --- | --- | --- | --- |
| zero | 10 | 0 | 9 |
| fires | - | 10 | 1 |
| restored objects | - | 25 | 2 |
| restored, subject | - | 0 | 1 |
| restored, container (contested included) | - | 25 | 1 |
| distinct triples restored | - | 16 | 2 |

`invariant_violations`: 0.

## Bars per arm

| Bar | Rule | `a1` | `a2` |
| --- | --- | --- | --- |
| B0 | defect exists | met | met |
| B1 benefit | arm zero count <= half of `a0` (4) | 0 of 8: **pass** | 0 of 8: **pass** |
| B2 English guard | restored containers <= max(1, floor(254/100)) = 2 | 25: **fail** | 1: pass |
| B3 restored quality | >= half of Spanish restored objects are subject | 0 of 12 = 0.00: **fail** | 2 of 10 = 0.20: **fail** |

## Restored objects and their classes

Classified from type, title and description only, before `decide` was first run.
Classification was done by the run's delegated agent, not the owner; a re-read
after this verdict is a new registration. Every `Event` is a container by the
registered rule.

Spanish, `a1` (12 restored, 7 distinct): all `Event`, all container.

| Count | Type | Title | Class |
| --- | --- | --- | --- |
| 3 | Event | Reunión de desarrollo del nuevo producto | container |
| 3 | Event | Reunión de Planificación del Proyecto | container |
| 2 | Event | Reunión de Presentación del Nuevo Producto | container |
| 1 | Event | Reunión de Desarrollo del Nuevo Producto | container |
| 1 | Event | Reunión de desarrollo del producto | container |
| 1 | Event | Reunión de planificación del proyecto | container |
| 1 | Event | Reunión de presentación del nuevo producto | container |

Spanish, `a2` (10 restored, 8 distinct): English-titled objects on a Spanish
source (the language leak, #618).

| Count | Type | Title | Class |
| --- | --- | --- | --- |
| 2 | Event | Discussion on Remote Control Design | container |
| 2 | Event | Conversation about Drawing a Dog and a Butterfly | container |
| 1 | Event | Conversation about Drawing a Dog and Discussing a Butterfly | container |
| 1 | Event | Drawing Session | container |
| 1 | Event | Discussion on Product Development | container |
| 1 | Event | Discussion on Remote Controls and Product Development | container |
| 1 | Decision | Budget and Product Pricing Decision | subject |
| 1 | Concept | Remote Control Design | subject |

English, `a1` (25 restored, 16 distinct): all `Event`, all container
(for example `Meeting`, `Team Meeting`, `Design Meeting`, `Meeting Discussion`,
`Reunión de trabajo`, `Reunión de plataforma — revisión quincenal`).

English, `a2` (2 restored): `Decision on Backup Encryption and Retention`
(subject) and `Decision on Open Issues` (contested, counted as container).

## Reading

- The defect is real but narrow. It reproduces on `ES2015a-es` (8 of 15) and on
  no other registered source (0 of 60). B0's pooled bar passes at 8 of 75, one
  record above its 0.10 line (7.5).
- `a1` fixes the zero rate (8 to 0) by storing exactly the framing objects
  #522/#533 removed on purpose: 12 of 12 restored Spanish objects are meeting
  containers, and on the English population it restores 25 containers across
  10 records against an allowance of 2. B2 and B3 both fail by a wide margin.
- `a2` also fixes the zero rate and stays within the English guard (1 container
  restored, allowance 2), but 8 of its 10 restored Spanish objects are still
  `Event` containers, now with English titles, and 2 of 10 are subject. B3 fails.
  Because `a2` stores English titles (a permanent wrong-language Concept ID) on a
  Spanish source, it would carry an owner decision even had it passed.
- The only objects a floor recovers that would clear B3 are the two `a2` subject
  objects (`Budget and Product Pricing Decision`, `Remote Control Design`) and
  one English decision. Those are too few to carry a rule.

## Deviations from the registration

1. **ADJUDICATION written and the harness committed with it.** The registration
   expects `ADJUDICATION` to be filled between `score` and `decide`; this is the
   registered flow, not a departure. It is noted because the classification was
   made by the delegated run agent rather than the owner (the registration
   permits this if disclosed).
2. **Wall time above the forecast range** (7.89 h against 5.5 to 7.6 h). Not a
   protocol change; the 10 h cap was not approached.
3. **Stage-local counting versus recorded finals.** The recorded production
   `final` stage was empty in 7 of 15 `ES2015a-es` records, while the replayed
   `a0` is empty in 8 of 15. The registration says the replay is production's
   functions on the recorded raw stage and that it does not compare with the
   recorded `drops`/`final` stages (Limits, last bullet), so this is not a
   departure; it is the disclosed stage-local difference (a later production
   stage, such as the re-ask, can add an object that the replay does not model).
   Every number above uses the replay, as registered.

No bar, arm, population, seed, window, stopping rule or command was changed. No
harness logic was altered; no harness bug occurred in `run`, `score` or `decide`.
`record` files and caches are under the eval-runs directory, outside the
repository.

## Limits

- Stage-local, as registered: a floor-kept stub could still trigger the
  sole-object re-ask (#584), whose effect is not replayable here.
- One corpus family and a machine-translated Spanish; one model (`qwen3:8b`).
- The decision rests on a defect confined to one source, where all 8 empties
  occurred. A different Spanish source set could give a different B0.
- Runs of one source are correlated; the distinct-triple counts above make a
  repeated title visible (12 `a1` objects are 7 distinct titles).

## Follow-ups

None licensed. #1642 stays open for a different mechanism (for example, the
upstream title-language behaviour on `ES2015a-es`), not for a floor. The
re-ask-then-redrop idea named in the registration applies only when `a1` clears
every bar but B2, which did not happen.
