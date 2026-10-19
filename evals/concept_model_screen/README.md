# Concept-extraction model screen (#1557)

Which local chat model should the `extraction` task use for concept
extraction? This harness answers that without hand annotation. The rules are
fixed in [`PREREGISTRATION.md`](PREREGISTRATION.md); the outcome is in
`RESULTS.md` once the screen has run.

## How it measures

- **Gold.** AMI's human abstractive summaries (`abstract`, `decisions`,
  `problems`; `actions` excluded). `summlink` ties each summary sentence to
  the dialogue acts it summarizes, which places it in one extraction window.
- **Items.** The 4,000-char windows production `_chunk_lines` cuts from a
  rendered transcript. The per-window extraction call is byte-identical
  whatever the meeting's length, so windows are paired across models.
- **Primary: coverage lift.** bge-m3 soft coverage of a window's own gold
  sentences by the `title: description` of the final objects that window
  produced, minus the same coverage of a foreign meeting's gold. The
  subtraction removes topical credit.
- **Guardrail: G1 precision.** `gpt-oss:20b` (never a candidate) judges each
  final object against the window that produced it.
- **Vetoes.** Failures and timeouts, wrong-language titles on the Spanish
  prose fixtures, volume, verbatim copying. Latency is reported, not gated.
- **Per-stage loss (#1559).** Every stage of the real pipeline is recorded,
  so coverage after each stage is a free rescore.

## Files

| File | Role |
|---|---|
| `run_concept_screen.py` | CLI: `prepare`, `stage0`, `run`, `judge`, `score`, `decide`, `--self-test` |
| `screen_data.py` | Allowlist, transcripts, windows, gold, `summlink` assignment |
| `screen_metrics.py` | Coverage, bootstrap, vetoes, cut/selection/confirmation rules, validity checks |
| `screen_pipeline.py` | Cached, timed backend; per-stage capture of production `extract_concept_union` |
| `screen_judge.py` | The `gpt-oss:20b` prompts and fail-closed label parsing |
| `windows.json` | Registered window hashes (no text) and gold counts per meeting |
| `drop_ablation.py` | #1572: per-rule L6/L7 drop ablation on the cached records, cache-only embeddings (`list`, `score`, `--self-test`); rules in `DROP_ABLATION_PREREGISTRATION.md`, outcome in `DROP_ABLATION_RESULTS.md` |

## Data stays outside the repository

The rendered transcripts (thesis renderer `afg.corpus.render@7d479d2c692a`)
and the AMI archive are copied into the runs directory
(`~/Dev/Projects/openkos-eval-runs/concept-screen/inputs/`) and read from
there. Generations, judge replies and embeddings are cached under the same
directory. Nothing AMI-derived is committed: only code, the registration,
window hashes and aggregate numbers.

`screen_data.ALLOWED_MEETINGS` is a fail-closed allowlist: the thesis's
development series (ES2015, IS1004, TS3009) and the free pool (IS1002 b-d,
IS1005 a-c, TS3012 a-d, IB4003, IB4005, IB4010, IB4011). Every other meeting is
refused before a file is opened, with no override.

## Worktrees

Four of the five `examples/extraction-corpus/` sources are gitignored. A
worktree has only one of them; copy the other four in. `run` and `prepare`
refuse to start unless 5 of 5 are present.

## Usage

```sh
uv run python evals/concept_model_screen/run_concept_screen.py --self-test
uv run python evals/concept_model_screen/run_concept_screen.py prepare
uv run python evals/concept_model_screen/run_concept_screen.py stage0
uv run python evals/concept_model_screen/run_concept_screen.py run --models qwen3:8b --pool DA --runs 1 --prose
uv run python evals/concept_model_screen/run_concept_screen.py score --name r1 --models qwen3:8b ... --pool DA --runs 1
uv run python evals/concept_model_screen/run_concept_screen.py decide --name r1 --round 1
```
