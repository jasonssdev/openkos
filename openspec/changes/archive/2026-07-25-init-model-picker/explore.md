# Exploration: init-model-picker (issue #128)

## Current State

`openkos init`'s model resolution lives entirely in `_resolve_model`
(`src/openkos/cli/main.py:103-121`), called from `init` (main.py:204-275) at
line 252:

- Precedence: `--model` flag (line 115-116, wins outright, no prompt even on
  TTY) > TTY free-text `typer.prompt("Model", default=config.DEFAULT_MODEL)`
  (line 117-120) > silent default on non-TTY (line 121).
- TTY detection: `sys.stdin.isatty()` (line 117), same idiom used ~10x elsewhere
  in main.py (e.g. lines 1085, 1558, 2073).
- Every path funnels through `config.validate_model` (config.py:56-83), which
  raises `ValueError` on blank/unsafe input; `init` catches this at
  main.py:253-255 and refuses (exit 1, no writes) — this is Phase A, before any
  file is written.
- Persistence: `config.write_config(root, model=resolved_model)` (main.py:262) —
  last Phase-B write, template-substitution based.
- Non-fatal Ollama preflight (main.py:333-352): runs strictly AFTER the
  workspace is written. Builds `OllamaClient(model=resolved_model, timeout=5.0)`,
  calls `probe.list_models()` then `model_tag_matches(...)`, wrapped in bare
  `except Exception`, sets `ready = False` and prints a stderr note pointing at
  `openkos doctor`. `init`'s exit code is 0 regardless. This is the exact
  non-fatal shape a picker must preserve if Ollama is unreachable *before* the
  prompt is shown (picker probes earlier, in Phase A, before any write).
- Test pattern: `tests/unit/cli/test_init.py` fakes `OllamaClient` via
  `monkeypatch.setattr("openkos.cli.main.OllamaClient", _fake_ollama_client(...))`,
  a minimal stub exposing ONLY `list_models`. TTY simulation:
  `monkeypatch.setattr(_NamedTextIOWrapper, "isatty", lambda self: True)`
  (class-level). Prompt answers via `runner.invoke(app, ["init"], input="...\n")`.

## The Three Reported Defects (root cause confirmed)

1. **`read_config` type gap** — config.py:384 `model = raw.get("model")`, then
   config.py:392 `model=model if model is not None else DEFAULT_MODEL`. Only
   checks `is not None`; never `isinstance(model, str)`. A YAML 1.1
   boolean/null value survives untouched into `Config.model: str`. A str check
   must slot into the same conditional without breaking the
   `review: false`-survives-untouched behavior.

2. **`validate_model` allowlist gap** — config.py:56-83,
   `_MODEL_TOKEN_RE = re.compile(r"[A-Za-z0-9._:/-]+")` (line 53). Pure ASCII
   allowlist. Existing tests (`tests/unit/test_config.py:117-154`) never assert
   rejection of `yes/no/on/off/true/false/null` — all pass today (pure letters).
   `yes` → written into `openkos.yaml` → PyYAML (YAML 1.1 resolver,
   `yaml.safe_load` at config.py:377) reads it back as Python `True`.

3. **`doctor` crash on non-str model** — main.py:5603
   `model_tag_matches(model, installed)` where `model = cfg.model`
   (main.py:5552). `model_tag_matches` (ollama.py:284-296) does
   `wanted = configured if ":" in configured else f"{configured}:latest"` at
   line 291 — `":" in True` raises `TypeError`. Check 2 "Config valid"
   (main.py:5529-5550) already wraps `read_config` in
   `try/except (OSError, ValueError)` — so fixing defect #1 to raise
   `ValueError` on a non-str model would already turn this into a clean
   `[FAIL] Config valid`, IF read_config raises. Checks 4/5 (main.py:5595+) have
   no independent guard. Doctor convention: `CheckResult` (main.py:5440-5448) is
   "accumulated, never raised", rendered via `_render_check` (5451-5461) as
   `[PASS]`/`[FAIL]`/`[SKIP] <label>` with optional `  -> <remediation>`. Reuse
   this convention.

## OllamaClient.list_models() — shape and embedding distinguishability

`list_models(self) -> list[str]` (ollama.py:236-276): GET `{host}/api/tags`,
parses `json.loads(body)["models"]`, extracts
`entry.get("model") or entry.get("name")` per entry — **discards every other
field** (no `details`/`family`/`parameter_size`). Confirmed via
`tests/unit/llm/test_ollama.py:502-541`. Already consumed by doctor checks 3/4/5
this way.

**Consequence for "exclude embedding models"**: no capability/family signal is
available through the current `list_models()` contract to distinguish `bge-m3`
from `qwen3:8b`. Real Ollama `/api/tags` carries a `details` object, but this
client throws it away. Two options:
- (a) widen `list_models()`'s return type to preserve per-entry detail for a
  family-based filter (touches doctor's two call sites);
- (b) heuristic name-based exclusion — `cfg.embedding_model` /
  `DEFAULT_EMBEDDING_MODEL` (`"bge-m3"`) at minimum, possibly a small
  known-embedding-family denylist (`bge`, `nomic-embed`, `mxbai-embed`,
  `all-minilm`). Lower effort, does not touch doctor, but incomplete for unknown
  embedding models.

This is a real proposal/design-time decision, not a foregone conclusion.

## Existing Test Conventions Relevant to the Picker

- CLI prompts: `typer.testing.CliRunner` + `runner.invoke(app, [...], input="...\n")`;
  TTY simulated by monkeypatching `_NamedTextIOWrapper.isatty` (class-level).
- Ollama mocking: minimal hand-rolled fake client via
  `monkeypatch.setattr("openkos.cli.main.OllamaClient", ...)`, exposing ONLY the
  methods needed. `tests/unit/cli/test_init.py` already has `_fake_ollama_client`
  (lines 32-49) + autouse fixture (52-70) — reuse/extend, don't reinvent.
- `list_models()` unit tests (`tests/unit/llm/test_ollama.py`) mock at the
  `urlopen` layer via `_fake_urlopen` + `_tags_body` — the layer to extend if
  the return shape changes.
- `validate_model`/`read_config` tests: `pytest.mark.parametrize` tables in
  `tests/unit/test_config.py`.

## Affected Areas

- `src/openkos/cli/main.py` — `_resolve_model` (103-121), `init` (204-352),
  `doctor` (5464-5634+).
- `src/openkos/config.py` — `validate_model` (56-83), `read_config` (355-411,
  model field at 384/392).
- `src/openkos/llm/ollama.py` — `list_models` (236-276), `model_tag_matches`
  (284-296).
- `tests/unit/cli/test_init.py`, `tests/unit/test_config.py`,
  `tests/unit/cli/test_doctor.py` (not read this pass — read before writing
  doctor tests), `tests/unit/llm/test_ollama.py` (only if list_models changes).

## Approaches

1. **Two-slice split: hardening first, picker second** — Slice A ships defects
   #1-#3 as an independent low-risk PR; Slice B ships the picker on the hardened
   config layer.
   - Pros: hardening is small, isolated, immediately de-risks the crash; matches
     repo slice convention; each slice well under review budget.
   - Cons: two PRs; picker depends on hardening merging first.
   - Effort: Low (hardening) + Medium (picker).
2. **Single combined PR** — ship hardening + picker together.
   - Pros: one review pass; matches the issue's framing.
   - Cons: picker's embedding-exclusion fork inflates scope into the same diff;
     combined diff likely exceeds budget, forcing a chained split anyway.
   - Effort: Medium-High.

## Recommendation

Approach 1 (two-slice split): the hardening defects are independently valuable
bug fixes with zero design ambiguity; the picker has one genuine open design
question (embedding exclusion) that benefits from isolation. Recommend
`sdd-propose` scope the hardening slice first (defects #1-#3), then a second
change/slice for the picker.

## Risks

- Embedding-model exclusion cannot use a true capability signal with the current
  `list_models()` contract — any name-based heuristic is incomplete for unlisted
  embedding models. Explicit scope decision at proposal time.
- Hardening #1 (read_config raises ValueError on non-str model) may already
  subsume #3 for the reported crash path via doctor's existing
  `(OSError, ValueError)` guard. Verify before task-splitting to avoid redundant
  patches; an independent doctor-side guard may still be warranted for
  defense-in-depth.
- Reserved-word rejection (#2) must be case-insensitive and match PyYAML's exact
  YAML 1.1 boolean/null resolver set, not a guessed list.
- `tests/unit/cli/test_doctor.py` not read this pass — read before writing the
  doctor hardening spec/tests.

## Ready for Proposal

Yes. Open item for the proposal author: decide embedding-model exclusion
mechanism (name heuristic vs. `list_models()` contract widening) before scoping
the picker slice.
