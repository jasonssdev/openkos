# Why does the selector judge fail? (#754)

Replays ONE frozen judge call — captured from a real `extract_concept_union`
run over the 40 KB AMI transcript — and records what actually comes back.

```
uv run python -u evals/judge_cold_start/run_judge_cold_start_probe.py --self-test
uv run python -u evals/judge_cold_start/run_judge_cold_start_probe.py --capture
uv run python -u evals/judge_cold_start/run_judge_cold_start_probe.py --capture --pad-source --pad-from main
uv run python -u evals/judge_cold_start/run_judge_cold_start_probe.py --runs 15
uv run python -u evals/judge_cold_start/run_judge_cold_start_probe.py --runs 15 --pad-to 23 --arms warm
uv run python -u evals/judge_cold_start/run_judge_cold_start_probe.py --rescore <runs.json>
```

## Why the probe exists

`judge.select` collapses three different failures into one `None` — a raised
`llm.chat`, a reply carrying no JSON object, and a JSON object of the wrong
shape — so `judge selection unavailable` names an OUTCOME and hides the
CAUSE. #644 filed this same symptom, hypothesised cold loading, and the
hypothesis was falsified by measurement; the real cause was a full-line echo.
#754 filed it again with `ollama ps` evidence. That evidence proves the model
was cold. It does not prove being cold is what broke the call, because
nothing recorded what came back.

So this records the raw reply and the exception, and classifies the failure
with production's own parse chain. `--self-test` pins that the classifier
agrees with production `select()` on every reply shape, so it cannot drift
into measuring a chain production does not run.

## Result 1, 2026-08-17 — cold start does NOT reproduce

9 candidates, 40,778 source chars, `qwen3:8b`, generation ceiling 8192,
context window 12288.

| arm | runs | excluded | ok | chat_error | unparseable | wrong_shape | fail rate |
| --- | --- | --- | --- | --- | --- | --- | --- |
| cold | 15 | 0 | 15 | 0 | 0 | 0 | **0.00** |
| warm | 15 | 0 | 15 | 0 | 0 | 0 | **0.00** |

Fifteen CONFIRMED evictions — the probe re-reads `/api/ps` after each unload
and excludes any run whose eviction it could not prove, rather than counting
it as cold. Zero failures in either arm. Zero full-line echoes salvaged.

**#754's failure did not reproduce here at all**, so this says nothing yet
about which cause fires — and the verdict refuses to read that as a pass.

## Result 2 — candidate count does not reproduce it either

The obvious remaining difference from the reported run: it carried **23**
candidates and the first sweep carried 9. Candidate count drives both the
prompt's tail and how many titles the model must echo back, and the reply is
where a shape or length failure would surface. The spare candidates come from
repeated real captures (extraction is stochastic, so repeats propose partly
different objects) — never invented strings.

| arm | candidates | runs | ok | failures |
| --- | --- | --- | --- | --- |
| warm | 23 | 15 | 15 | **0** |

Warm-only, deliberately: the paired sweep above already answered the
cold-start question on 15 confirmed evictions, so this arm isolates candidate
count rather than re-running a question that was settled. The verdict names
that limit itself — a single-arm run reports its own result and states
plainly that the cold comparison was not made.

**45 judge calls across the two sweeps, at 9 and 23 candidates, cold and
warm. Zero failures.**

## The retry's cost, stated

`select` inherits the workspace `chat_timeout`. Against a backend that HANGS
rather than refuses, two attempts wait up to twice that deadline before the
judge is declared unavailable — once per source in a batch. That is accepted,
not mitigated: a backoff would slow the common case to shrink a worst case
that is already pathological, since the two extraction calls ahead of the
judge would have had to succeed against the same hanging backend first.

## What is still unexplained

The reported failure is real; it is in the e2e log. This harness has not
reproduced it, which means the cause is something neither arm varies: a
different source, a different bundle, a longer prompt, or a transient the
sweep did not happen to hit.

That does not block the fix, and this is worth being explicit about because
it inverts the usual order. The remedy #754 ships — ask the judge twice, and
when it is still unusable keep the whole unranked set instead of letting a
positional cap cut it by arrival order — is **cause-agnostic**. It is correct
for a transient error, for a one-off bad reply, and for a sampling model that
occasionally answers in prose. What the measurement changes is the CLAIM: the
retry is not shipped as "the cold-start fix", because cold start is not the
demonstrated cause.
