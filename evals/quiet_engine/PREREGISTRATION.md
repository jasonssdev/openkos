# Pre-registration (DRAFT): The Quiet Engine product metrics

**Status: DRAFT.** Written before any live run. It becomes binding when the owner fixes the items marked **OWNER** and it is posted on [#1268](https://github.com/jasonssdev/openkos/issues/1268). Until then every bar below is a proposal. The harness is [`run_quiet_engine_eval.py`](run_quiet_engine_eval.py). It encodes these definitions and bars and has a model-free `--self-test`.

## Question

Does the arc ([ADR-0044](../../docs/adr/0044-the-quiet-engine-arc-precedes-interoperability.md)) cut the decisions OpenKOS asks of a person per ingested source to at most one? Does it make a dropped file answerable, with a citation, soon after it lands? #1268 deliverable 6 names both metrics and four exit criteria. This document fixes how each one is measured.

## Arms

| Arm | CLI | Chat model | Judges (`adjudication`, `contradiction`) | Role |
| --- | --- | --- | --- | --- |
| `v0.4.0` | the `v0.4.0` tag | `qwen3:8b` | `qwen3:8b` (v0.4.0 has one model for every task) | baseline |
| `main` | main at the commit under test, as shipped | `qwen3:8b` | `gemma4:26b-a4b` (packaged default, ADR-0047) | **primary treatment; the bars apply here** |
| `main-judges-qwen3` | same commit as `main` | `qwen3:8b` | `qwen3:8b`, pinned via `models:` | attribution only; no bars |

All three arms use `init --model qwen3:8b --embedding-model bge-m3` and the packaged defaults for everything else (`temperature`, `seed`, `context_window`, `max_generation_tokens`, `union_judge`, `attach_at_ingest`, `sufficiency_check`, every `unattended:` limit). The only lines the harness adds to `openkos.yaml` are `unattended.inbox` (all arms) and the two `models:` entries (attribution arm). Each run records the final config text, the CLI's `--version`, the checkout commit, the corpus digest, and the identity stamp (`evals/harness_stamp.py`: harness commit, model digests, question hashes).

## Corpus

[`corpus/`](corpus/) holds synthetic notes for a fictional volunteer weather-station project. It contains no real people and no private material.

- `sources/`: 20 markdown files, 2.1 to 9.5 KB (about 71 KB total). They are batch-ingested. The text is deliberately messy: recurring concepts across files (MQTT in 9 files, sensor calibration in 13), near-duplicate headings, a **Person homonym** (Sam Patel the firmware volunteer and Councillor Sam Patel), and an **Event homonym** (two "Weekly Sync" meetings on different dates). `manifest.json` lists each designed overlap, measured by regex over the committed text.
- `inbox/v1/21-breakwater-gust-incident.md`: the probe dropped for metric (b). It holds one unique fact, the part number `HX-7745`.
- `inbox/v2/` (same file name): an edited version, dropped over v1 in the watched inbox. This exercises supersession and the #1259 re-extraction path.
- Five questions. Each answer token appears verbatim in exactly the expected file(s). The self-test enforces this, and also the file count and size bounds.

Denominator: **22 fed files** (20 sources, the probe, and its new version).

**Threat to validity.** A synthetic corpus may produce fewer decisions than the real E2E corpus. `examples/extraction-corpus/README.md` explains why clean synthetic text under-measures. That is why v0.4.0 is re-measured on this corpus (see the validity floor). The ~4.3 from the E2E is context, not the baseline.

## Protocol (one run)

1. `init`, then append `unattended.inbox` (plus the judge pin, attribution arm only).
2. `ingest corpus/sources --auto`.
3. `daemon --once`: the first maintenance pass. The open pending rows are read.
4. Start `daemon` in the background.
5. Drop probe v1 into the inbox and measure metric (b).
6. Drop v2 over it. Wait until its bytes land in `raw/` and the daemon reports a `watch` job with `done>=1`.
7. Ask all five questions once. Stop the daemon. Run `reindex`. Ask again every question that did not cite its expected source (the stale-index check).
8. Read the bundle (`-N` families), the git log since step 2 (automatic actions) and the pending queue.
9. `curate --auto` on a pseudo-terminal. **Every per-item prompt is answered with Enter**, the default, which never accepts. That is `N` on every `[y/N...]` prompt, and skip on main's identity prompt. A cost gate (`Proceed?`) is answered `y`, because it consents to model spend, never to a write. Under `--auto` no cost gate should appear, so one that does flags the run.

## Metric definitions

### (a) Decisions per ingested source (primary)

`D = I + M + U`, reported as `D / 22`, where, from step 9's transcript:

- `I`: per-item decision prompts presented, in any stage (Identity, Structure, Metadata). A prompt is a line ending in a `[y/N]`-family choice (`[y/N]`, `[y/N/s]`, `[y/N/s/a]`, `[y/N/a/r]`, `[y/N/s/a/r]`) or main's `[y]es / [s]kip / [d]istinct ...`, followed by `:`. The grammar was checked against both versions' source.
- `M`: distinct `openkos merge <a> <b>` commands printed in the Identity stage. These are groups the walk will not prompt for (more than two members, or cross-type), so a human must decide them by hand.
- `U`: prompt-shaped lines the grammar did not recognize, answered after 120 s of silence. They are counted, so a decision cannot hide behind new wording, and they flag the run.

**Why prompts and not pending rows.** The E2E's 4.3 counted prompts (17 `adjudicate --apply` + 69 `curate` = 86 for 20 sources). A pending row is a queue entry, and the arc changes the queue directly: deliverable 5 makes `pending` a digest, and attach-at-ingest removes rows at their source. Counting rows would let a change to the queue's bookkeeping read as fewer decisions. Counting what a person is asked to judge, item by item, does not move when presentation changes. Batching does not move it either: an "accept all" key still presents each item, and the harness never uses one.

**Why only `curate`.** `curate` runs `adjudicate --apply`'s merge walk as its Identity stage. Running both would count every identity decision twice.

**What is not a decision.** Contradiction findings, which `curate` prints without asking. Pending-only rows: the `supersedes` relation row, watch refusals, revisions. Both are reported, never counted (see G1 and open question 4).

### (b) Time from drop to the first cited answer (primary)

- **Drop.** The probe's bytes are written to `<name>.part`, a non-text extension the watch ignores, then atomically renamed into the inbox. `t0` is the rename.
- **Landing.** The harness polls `raw/` every 0.5 s for a file with the probe's sha256. Nothing can be cited before the workspace holds the file, so waiting costs no resolution.
- **Polling.** After landing, `query "<probe question>"` runs repeatedly. Each query starts 20 s after the previous one started, or as soon as it ends if it took longer. The daemon keeps running, and queries contend with its model calls, the same on every arm.
- **Citation check.** An answer cites the probe when a `Citations:` entry is a Source whose raw file has the probe v1 or v2 bytes (matched by hash, not by name), or a document whose `provenance` names such a Source.
- **Value.** Seconds from `t0` to the end of the first citing query. No citing answer within **1800 s** is **censored** and reads as "> 1800 s". Whether the answer contains `HX-7745` is recorded, not scored.

### Secondary exit checks

- **S1, `-N` duplicates.** After step 7: group bundle documents by (OKF `type`, `normalize_key(title)`). The key is a frozen copy of the engine's, and the self-test asserts the two agree. Within a group, ids sharing a base once a trailing `-<digits>` is stripped form a family, and a family of k is k - 1 duplicates. Deprecated copies count, because the criterion is about duplicates created. Source documents are excluded, since a new version is a new Source by design (ADR-0041). Event and Person families are reported separately and never counted (ADR-0044's exclusions).
- **S2, stale-index false refusals.** A step-7 question that (before `reindex`) printed the stale-index warning and was refused ("none of them answers this question"), and (after `reindex`) cites its expected source. The same without the refusal (answered with the wrong citations) is a **stale miss**, reported only.
- **S3, automatic actions listed with undo.** Every commit made after step 2 is an automatic action, since only the daemon writes between step 2 and step 9. It is **listed** when the daemon's own output has a line naming its short sha together with an undo command (`git revert`, `openkos unmerge`, `openkos forget`, `openkos unrelate`), or names every bundle document the commit added or removed, each on such a line.
- **G1, pending rows (guard).** Open pending rows after step 7. Reported per arm and used only as a guard against moving decisions out of `curate` into the queue.
- Reported only: candidate work hidden by caps (`N of M ... (cap reached)`), documents, Event/Person `-N` families, every query's raw citations.

## n

**n = 3 runs per arm.** Every bar reads **every** run (`max`, never a mean), so one bad run fails the arm. The effect the arc targets is large (from about 4.3 to at most 1), and #1268 requires the outcome on the corpus, not an estimated mean. Three runs show whether the outcome holds across the model's nondeterminism without a 15-run sweep at about 80 minutes a run. A treatment run that misses a bar by less than the baseline's own run-to-run spread is reported as such, and the owner decides.

## Exit bars (primary arm `main` against `v0.4.0`)

| Bar | Rule | #1268 criterion |
| --- | --- | --- |
| **B1** | every `main` run: `D / 22 <= 1.00` | "Decisions per source at or below 1 on the corpus, down from about 4.3" |
| **B2** | every `main` run: S1 = 0 | "Zero `-N` duplicates created for same-type, same-key concepts outside the excluded types" |
| **B3** | every `main` run: S2 = 0 | "No false sufficiency refusal attributable to a stale index" |
| **B4** | every `main` run: every automatic action listed with undo | "Every automatic action listed with its undo" |
| **B5** (**OWNER**) | every `main` run cites the probe within 300 s, and the median is <= 120 s | none yet: #1268 names metric (b) without a bar |
| **G1** (**OWNER**) | every `main` run's final open pending rows <= the `v0.4.0` median | none: proposed guard |

**Validity floor.** B1 reads **INCONCLUSIVE**, never PASS, unless the `v0.4.0` median `D / 22` is at least **2.0**. A corpus that cannot reproduce the load cannot show it was cut. If the floor fails, the corpus is made harder before any re-run, and the bar is not lowered.

**B4 when nothing happened.** A run whose daemon made no commit reads **NOT_MEASURED** for B4, never PASS. The probe import should always make one.

**Pass.** The arc's measured exit is met when B1 to B5 and G1 all read PASS for `main`. A flagged run (a non-zero CLI exit, a timeout, an unrecognized prompt, or a cost gate under `--auto`) must be explained before the verdict is adopted. The attribution arm has no bars. It reports how much of each metric moves with the judge model alone.

## When it runs

- **Interim:** `v0.4.0` and `main` at the current head, to see where the arc stands. Not binding.
- **Binding:** at the arc's exit, on the commit that closes its last deliverable. That run decides the exit criteria.

Before either, one **pilot run per CLI** checks that the protocol works live: the inbox path, the daemon's `watch` lines on v0.4.0, and curate's transcript. The bars do not read it. If the pilot forces a protocol change, `PROTOCOL_VERSION` is bumped and this document amended before the counted runs.

## Run forecast

From `run_quiet_engine_eval.py --plan`, per run:

| Phase | Minutes | Source |
| --- | --- | --- |
| ingest (20 sources) | 21 | **measured**, 0.4.0 human E2E (#1268), `qwen3:8b`, on that corpus, not this one |
| `daemon --once` | 15 | estimate, capped by `job_deadline_seconds` (30 min) |
| metric (b) | 30 (`v0.4.0`, expected censored, #1260) / 3 (`main`) | estimate |
| version import | 2 | estimate |
| questions + `reindex` | 4 | estimate |
| `curate` | 30 | estimate (the E2E presented 69 curate prompts; inference time at this size is unmeasured) |
| judge model swaps | 5 (`main` only) | estimate (ADR-0047: one chat model at a time) |

Per run: about 102 min for `v0.4.0`, 80 for `main`, and 75 for `main-judges-qwen3`. At n = 3: **about 5.1 h, 4.0 h and 3.8 h, about 12.8 h in total**, plus about 1.5 h of pilots. The first counted run of each arm replaces these estimates with measurements.

## Open questions

1. **Judge model: as shipped, or pinned?** Main defaults the contradiction and identity judges to `gemma4:26b-a4b` (ADR-0047). v0.4.0 used `qwen3:8b` everywhere. Measuring as shipped mixes the judge change with the arc's structural changes. Pinning both judges to `qwen3:8b` isolates the arc, but it measures a configuration nobody gets by default. **Recommendation:** the primary arm is **as shipped** (`main`), because the exit criteria are product outcomes a user gets. A secondary arm (`main-judges-qwen3`) is reported for attribution, with no bars. If the two arms disagree on a bar, the report says so, and the owner decides whether the arc or the judge earned it.
2. **Is v0.4.0's non-interactive surface enough?** Partly. Neither version has `--json` on `curate`, `pending` or `query`. v0.4.0 has `adjudicate --json`, but it covers identity only. Both versions refuse `curate`'s per-item walk without a terminal. So the harness drives `curate` on a pseudo-terminal and parses its transcript, using a prompt grammar checked against both versions' source (v0.4.0 identity is `Merge <a> into <b>? [y/N]`, main's adds `[y]es / [s]kip / [d]istinct`). It reads `findings.db`'s `pending_items` table read-only, and the schema is the same in both versions. It parses `query`'s `Citations:` block, whose format is identical in both. Stage attribution relies on the TTY-only `openkos curate: <Stage>: checking...` line, which both versions print. The risk is a wording change the grammar misses. That is what `U` (unrecognized prompts) and the run flag catch. **Recommendation:** accept, and confirm on the pilot.
3. **The bar for metric (b)** (**OWNER**). #1268 names the metric without a number. **Recommendation:** B5 as drafted. Every run within 300 s and a median within 120 s, against a floor of about 40 s (the default `quiet_seconds` of 30 plus a 10 s poll) for a source that is "searchable as soon as it lands" (deliverable 3).
4. **Do pending-only rows count as decisions?** The `supersedes` row a new version queues, watch refusals and revisions are things a person must act on that `curate` never presents. **Recommendation:** not in `D`, which stays comparable with the E2E's prompt count. G1 instead guards against decisions moving into the queue.
5. **One model or two?** #1268 asks whether `qwen3:8b` alone is the measured chat model. **Recommendation:** `qwen3:8b` alone, the packaged default. A second chat model would double the 12.8 h without changing which arc deliverables pass.
6. **Is n = 3 enough?** It is enough for an every-run bar on a large effect. It is not enough to estimate a mean. If a `main` run lands within 0.2 of the 1.00 bar, the recommendation is to add runs before deciding rather than to read the margin.
7. **Synthetic corpus vs the E2E corpus.** The E2E corpus was private and is not reproducible here. **Recommendation:** keep the validity floor (v0.4.0 median `D / 22` >= 2.0). If v0.4.0 misses it, extend the corpus (more overlap, longer files) and re-run, without lowering the bar.
8. **B4's matching rule** is fixed now (short sha, or every touched document, on a line with an undo command), before deliverable 5's digest exists. If the digest lists actions in another shape, for example by concept title, B4 would FAIL on format alone. **Recommendation:** keep the rule and make the digest name ids or shas. If the owner prefers adapting the rule, amend it here before the binding run, never after.
