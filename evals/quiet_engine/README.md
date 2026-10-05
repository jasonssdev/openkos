# `quiet_engine`: The Quiet Engine arc's product metrics

**Question.** Does the arc ([#1268](https://github.com/jasonssdev/openkos/issues/1268), [ADR-0044](../../docs/adr/0044-the-quiet-engine-arc-precedes-interoperability.md)) cut the human decisions per ingested source? Does it make a file dropped into the inbox answerable with a citation soon after it lands? Its secondary exit checks are `-N` duplicates, stale-index false refusals, and automatic actions listed with their undo.

The definitions, arms, `n`, the bars and the owner's decisions live in the approved pre-registration, [`PREREGISTRATION.md`](PREREGISTRATION.md) (#1268). The bars apply to the `main` arm only; `main-judges-qwen3` is reported for attribution. This README covers how to run the harness.

## What it drives

A real `openkos` executable, given as an argv prefix, so one harness compares two checkouts. The harness drives the CLI's own surfaces:

- the `Citations:` block of `query`;
- the pending queue's `pending_items` table, read-only;
- the git log of the workspace;
- the daemon's `watch` job lines;
- `curate`'s per-item prompts on a pseudo-terminal, every one answered with Enter, the non-accepting default.

It never accepts a merge, a relation or a tier.

## Running it

Build each CLI in its own checkout. The v0.4.0 one comes from the tag:

```
git worktree add ../openkos-v0.4.0 v0.4.0
uv sync --project ../openkos-v0.4.0
```

Then, with Ollama running and `qwen3:8b`, `bge-m3` and `gemma4:26b-a4b` pulled:

```
uv run python evals/quiet_engine/run_quiet_engine_eval.py --plan        # forecast, no model call
uv run python -u evals/quiet_engine/run_quiet_engine_eval.py \
    --arm v0.4.0 --cli ../openkos-v0.4.0/.venv/bin/openkos \
    --checkout ../openkos-v0.4.0 --runs 3
uv run python -u evals/quiet_engine/run_quiet_engine_eval.py \
    --arm main --cli <main checkout>/.venv/bin/openkos --checkout <main checkout> --runs 3
uv run python -u evals/quiet_engine/run_quiet_engine_eval.py \
    --arm main-judges-qwen3 --cli <main checkout>/.venv/bin/openkos --checkout <main checkout> --runs 3
uv run python evals/quiet_engine/run_quiet_engine_eval.py \
    --report evals/quiet_engine/results/runs-*.json --baseline v0.4.0
uv run python evals/quiet_engine/run_quiet_engine_eval.py --self-test   # model-free
```

Pass the virtualenv's `openkos` binary rather than `uv run`. The daemon is stopped with `SIGTERM` to its process group, and a direct binary has no wrapper in between.

Workspaces go under a fresh temporary directory, or `--work-root`. That directory must not be inside a git working tree, because `init` would then commit into the host repository, so the harness refuses one. Each run leaves its workspace, `daemon.log` and `curate.transcript.txt` there for inspection. `results/runs-<arm>-<timestamp>-<model>.json` is rewritten after every run, so an interrupted arm keeps its completed runs.

## Self-test

`--self-test` runs two checks.

1. **Pure scoring.** This covers the prompt grammar (checked against both versions' strings), citation and refusal parsing, `-N` families, the frozen title key against the engine's, the stale-index classification, the bars, and the committed corpus's own invariants (file count, sizes, each answer token in exactly its expected files).
2. **The whole protocol** against a deterministic fake CLI (`--fake-cli`), in two modes. `old` forks to `-N`, leaves the daemon's index stale, and lists nothing. `new` attaches, refreshes, and lists every import with `git revert`. Every metric must come out at its hand-computed value.

It takes about 12 s and makes no model call.
