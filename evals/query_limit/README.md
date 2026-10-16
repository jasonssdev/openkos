# Is `--limit 5` the right query default? (#1377)

The bar is `PREREGISTRATION-1377.md`, committed before any model run. Needs
Ollama for `bge-m3` and the chat model.

```
uv run python -u evals/query_limit/run_query_limit_probe.py --self-test
uv run python -u evals/query_limit/run_query_limit_probe.py --runs 15
uv run python -u evals/query_limit/run_query_limit_probe.py --rescore <runs.json>
```

Result (`qwen3:8b`, 15 runs per arm, 19 questions): the registered rule adopts
`k = 8`; see `results/query-limit-20261016-n15-qwen3-8b.md` and
[ADR-0062](../../docs/adr/0062-query-limit-defaults-to-8-measured-against-5.md).
The corpus is synthetic and favours enumeration questions; it licenses the
default for that shape, not for every bundle.
