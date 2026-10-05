# `evals/` — measurement harnesses

Manual, model-backed harnesses that measure how OpenKOS behaves on real or
frozen inputs, so a change is adopted on evidence rather than intuition.
They are not pytest tests and are not part of the shipped `openkos`
package. Each harness's README owns its question, method and verdict; this
index only points to them.

## Conventions

- Each harness lives in its own directory with a `README.md`.
- Run a harness with `uv run python -u evals/<harness>/<runner>.py ...`;
  the `-u` keeps progress unbuffered during long model runs.
- Every runnable harness supports `--self-test`, which checks its scoring
  logic with no model. `evals/run_self_tests.py` discovers every declared
  `--self-test` and runs them, and CI runs that sweep model-free.
- Model-backed runs need a local Ollama unless a README says otherwise.
- `evals/harness_stamp.py` is the shared identity stamp and timing helper: every stored `runs-*.json` of a bake-off harness carries a `stamp` (harness commit and dirty flag, model name and the local Ollama's digest, and `sha256(prompt)[:16]` per system prompt sent), plus per-run wall-clock (`run_latencies_s`, or per-row `elapsed_s` / `latency_s` where a harness already recorded it). Standard-library only; `--self-test` runs model-free.
- `evals/harness_report.py` is the shared report helper: it renders the one line every report carries to name its arm (generation ceiling and context window, plus any harness-specific segments), so a stored run can be told apart from one measured under other settings. It is standard-library only; the rest of each report stays per-harness.
- Timestamped reports and `runs-*.json` files under a harness's `results/`
  are historical records of what was measured; they are not edited after
  the fact. A later measurement is a new file.

## Shorthand

An **arm** is one condition of a comparison, run for a stated number of
runs. **Baseline** is the arm that runs the production behavior;
**treatment** is the arm that changes one thing against it. A **fixture** is
the frozen, labelled input a harness scores against. A **pre-registered
rule** is a decision rule written down before the first live run and
applied mechanically to the result. A **self-test** is the model-free check
of a harness's own scoring code.

## Harnesses

- [`adjudication`](adjudication/README.md) — scoring the identity adjudicator's SAME/DIFFERENT verdicts.
- [`auto_merge`](auto_merge/README.md) — whether a confidence threshold makes auto-merge of one narrow class safe.
- [`bakeoff`](bakeoff/README.md) — the shared driver for the #1269 model bake-off: eligibility gates and a staged knockout over the other harnesses.
- [`contradictions`](contradictions/README.md) — the contradiction judge's accuracy, stability and confidence.
- [`decision_extraction`](decision_extraction/README.md) — extraction of the nine OpenKOS types over the AMI meeting corpus.
- [`decision_granularity`](decision_granularity/README.md) — whether one-sentence prompt edits split multi-decision sources or keep a twice-named person.
- [`decision_revisions`](decision_revisions/README.md) — the decision-revision detector's subject pass, direction rule and judge.
- [`discarded_generation`](discarded_generation/README.md) — how much extraction generates and then throws away.
- [`duplicate_function_words`](duplicate_function_words/README.md) — function words deciding a title-containment near-match.
- [`edge_typing`](edge_typing/README.md) — the edge-type suggester's typing quality.
- [`extraction_cap`](extraction_cap/README.md) — the extraction cap-and-decay behavior against the extraction corpus.
- [`extraction_collapse`](extraction_collapse/README.md) — what collapses extraction to a single object, via matched pairs.
- [`generation_ceiling`](generation_ceiling/README.md) — which calls hit the generation ceiling, and whether chunking helps.
- [`generation_runaway`](generation_runaway/README.md) — which calls run away, and whether a lower bound can cut them.
- [`generation_thinking`](generation_thinking/README.md) — where a runaway generation's tokens go.
- [`ingest_concurrency`](ingest_concurrency/README.md) — wall-clock and quality cost of concurrent per-window extraction calls.
- [`insight_scan_bound`](insight_scan_bound/README.md) — cost of the near-duplicate scan per saved insight.
- [`judge_cold_start`](judge_cold_start/README.md) — why the selector judge fails, by replaying one frozen call.
- [`judge_overflow`](judge_overflow/README.md) — where the failing judge call's tokens go.
- [`language_leak`](language_leak/README.md) — title-language leakage on chunked non-English sources.
- [`model_spike`](model_spike/README.md) — comparing local models for derived-object extraction.
- [`named_person_volume`](named_person_volume/README.md) — how many merely-named people a transcript yields, and their cost.
- [`pair_nomination`](pair_nomination/README.md) — the chunked-vector pair-nomination gate.
- [`participant_anchor`](participant_anchor/README.md) — whether the participant anchor lexicon is too tight.
- [`participant_language`](participant_language/README.md) — whether the participant pass translates the source.
- [`proximity_threshold`](proximity_threshold/README.md) — calibrating the candidate-edge similarity floor.
- [`query_attribution`](query_attribution/README.md) — whether answers keep their attribution line.
- [`query_citation`](query_citation/README.md) — whether self-attribution makes the citation list meaningful.
- [`query_entailment`](query_entailment/README.md) — whether an answer is entailed by its context.
- [`query_grounding`](query_grounding/README.md) — whether a relevance floor separates grounded from ungrounded questions.
- [`query_identity`](query_identity/README.md) — whether any signal detects two filed insights being the same object.
- [`query_sufficiency`](query_sufficiency/README.md) — whether a pre-synthesis sufficiency check can refuse what attribution misses.
- [`query_title`](query_title/README.md) — subject-named versus question-named titles for saved insights.
- [`quiet_engine`](quiet_engine/README.md) — The Quiet Engine arc's product metrics: decisions per source and time to a cited answer, v0.4.0 against main.
- [`retrieval_stability`](retrieval_stability/README.md) — citation stability across near-identical questions.
- [`section_coverage`](section_coverage/README.md) — whether a per-section coverage signal can see a lost section.
- [`stage_attrition`](stage_attrition/README.md) — which pipeline stage removes the subjects.
- [`title_first`](title_first/README.md) — costing an earlier framing gate before body generation.
- [`type_restatement`](type_restatement/README.md) — titles that restate their own type.
