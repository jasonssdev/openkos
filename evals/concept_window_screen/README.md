# Concept-extraction window-size screen (#1558)

Which window size (`_CHUNK_TARGET`) should concept extraction cut a chunked
source into? The rules are fixed in [`PREREGISTRATION.md`](PREREGISTRATION.md);
the outcome is in `RESULTS.md` once the screen has run.

This reuses the #1557 model-screen harness
([`../concept_model_screen/`](../concept_model_screen/README.md)): the same
AMI gold, the same coverage lift, the same vetoes and the same cached,
timed backend. What differs:

- **Arms are window sizes**, applied by patching `_CHUNK_TARGET` for each
  run; every run checks that production cut the arm's window count.
- **Arms are paired by meeting, not by window**, because a different window
  size means different windows.
- **Stage 0 per arm** fixes each arm's timeouts and whether its prompts need
  a raised `num_ctx`; every call logs its token counters against `num_ctx`.
- **Fragmentation** (#699's redundant emissions) is counted on AMI gold and
  on `medium-10`.
- **`--replay-only`** replays #1557's cached 4,000-char runs with a sender
  that refuses any uncached call.

## Files

| File | Role |
|---|---|
| `run_window_screen.py` | CLI: `prepare`, `stage0`, `run`, `judge`, `score`, `decide`, `--self-test` |
| `window_rules.py` | Timeouts and `num_ctx` per arm, feasibility, fragmentation, vetoes, selection |
| `windows.json` | Registered window hashes (no text) per arm and meeting |

## Data stays outside the repository

Runs, caches and copies of the transcripts and the AMI archive live in
`~/Dev/Projects/openkos-eval-runs/window-screen/`. Nothing AMI-derived is
committed: only code, the registration, window hashes and aggregate numbers.
Four of the five `examples/extraction-corpus/` sources are gitignored; a
worktree needs them copied in.

## Usage

```sh
uv run python evals/concept_window_screen/run_window_screen.py --self-test
uv run python evals/concept_window_screen/run_window_screen.py stage0 --model qwen3:8b --arms 6000 8000 12000
uv run python evals/concept_window_screen/run_window_screen.py run --model qwen3:8b --arm 4000 --runs 1 2 --prose --replay-only
uv run python evals/concept_window_screen/run_window_screen.py run --model qwen3:8b --arm 6000 --runs 1 2 3 --prose
uv run python evals/concept_window_screen/run_window_screen.py judge --model qwen3:8b --arm 6000 --runs 1 2 3
uv run python evals/concept_window_screen/run_window_screen.py score --name w8b --model qwen3:8b --arms 4000 6000 8000 --runs 1 2 3 --prose-runs 1 2 3
uv run python evals/concept_window_screen/run_window_screen.py decide --name w8b
```
