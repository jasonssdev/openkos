# Proposal: `openkos init` — user-selectable local model

## Intent

`openkos init` writes `openkos.yaml` as a **byte-identical** copy of the packaged
template (`src/openkos/config.py::write_config`, zero parameters), which pins
`model: qwen3:8b` as a static line. Users on different hardware want another local
model (gemma, mistral, etc.), and `docs/cli.md:50` already promises "Model
selection during `init`" as this change. Today the only path is hand-editing the
file post-init — undiscoverable and unguided. This change keeps `qwen3:8b` as the
default but lets the user choose at init time, via flag or interactive prompt.

## Scope

### In Scope

- `openkos init --model <TAG>` flag (default `qwen3:8b`).
- Interactive prompt (`typer.prompt`, default `qwen3:8b`) **only when `--model`
  is absent AND stdin is a TTY**. Non-TTY (CI) with no flag → default silently.
  Precedence: **flag > prompt > default**.
- Minimal sanity validation only: reject empty/blank after trim; disallow tokens
  that would break the YAML line (newlines/quotes/colons). No allowlist.
- Write the chosen tag via **constrained plain-text token replacement** into the
  template (single safe placeholder), **never** a YAML dumper.
- Amend the `workspace-init` "Static openkos.yaml Template" requirement (delta
  spec) so the `model:` value is the one user-selectable field; all other fields
  and the no-directory-derived-field rule stay byte-identical.

### Out of Scope (deferred, named)

| Deferred | Why |
|---|---|
| Any consumer of `model:` (engine/inference/`ollama run`) | Nothing in `src/` reads `model:` back; validation is cosmetic by design today |
| Live `ollama list` / `ollama show` validation | Would be init's first subprocess/network call — explicitly deferred |
| Model download / `ollama pull` | Larger blast radius; named as deferred in prior design's Threat Matrix |
| Curated model allowlist | Contradicts "any local model your hardware permits"; ongoing maintenance |
| Repo-wide model-guidance doc refresh | `refresh-model-guidance` follow-up |

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- `workspace-init`: **"Static openkos.yaml Template"** requirement is amended.
  `model:` becomes the single user-selectable value (flag > prompt > default
  `qwen3:8b`); all other fields and the no-per-workspace-substitution /
  no-directory-derived-field guarantees remain. New scenarios: flag override,
  TTY prompt, non-TTY default, blank-input rejection.

## Approach

- `init()` (`src/openkos/cli/main.py`) gains a `--model` `typer.Option` (default
  `None`). Resolve: flag if given; else if `sys.stdin.isatty()` → `typer.prompt`
  with default `qwen3:8b`; else `qwen3:8b`. Trim + sanity-check the result.
- `write_config(root)` gains a `model: str` parameter. It substitutes a single
  placeholder token in the template with the validated tag — plain-text
  replacement, no `ruamel.yaml` dumper. This deliberately avoids the D5
  corruption class (the archived `add-init-command` reverted directory-name
  interpolation after `ruamel.yaml` folded a double-spaced value).
- **Naming**: new selection logic stays in `cli/main.py` / `config.py`. Do NOT
  create a package colliding with `src/openkos/model/` (the OKF Canonical
  Knowledge Object package, unrelated to LLM model tags).

## Affected Areas

| Area | Impact | Description |
|---|---|---|
| `src/openkos/cli/main.py` | Modified | `init()` gains `--model` option + TTY-guarded prompt |
| `src/openkos/config.py` | Modified | `write_config(root, model)`; placeholder substitution |
| `src/openkos/templates/openkos.yaml.template` | Modified | `model:` line becomes a single safe placeholder |
| `openspec/specs/workspace-init/spec.md` | Modified (delta) | Amend "Static openkos.yaml Template"; add scenarios |
| `tests/unit/test_config.py` | Modified | `byte_identical` / `ignores_directory_name` assume zero-param static output — re-verify against the default path |
| `tests/unit/cli/test_init.py` | Modified | `fresh_empty_directory` assumes no-flag behavior; add flag/prompt/non-TTY paths |
| `docs/cli.md` | Modified | Record that `init` now selects the model; resolve the deferred promise |

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Reintroducing YAML substitution reopens the D5 corruption class | Med | Constrained single-token plain-text replacement; no dumper; sanity-reject unsafe chars; regression test for a double-spaced dir name still passes |
| Spec requirement textually pins `qwen3:8b` and forbids substitution — silent reinterpretation | High | Amend explicitly in the delta spec; the default value is unchanged |
| Prompt breaks CI / scripted use | Med | TTY guard: prompt only when `stdin.isatty()`; non-TTY uses default silently |
| `test_write_config_byte_identical` breaks because template is no longer literally static | Med | Redefine "byte-identical" against the rendered default; keep the placeholder resolving to `qwen3:8b` by default |
| Validation is cosmetic (no consumer yet); false sense of safety | Low | Explicitly scoped: a future `model:` consumer decides invalid-value contract |

## ADR candidates (for design to weigh — not created here)

- **The `--model` flag + TTY prompt CLI surface.** This decides a public
  interface (ADR gate condition 1), but it is small and reversible — a flag/prompt
  is cheap to change before a wider consumer exists (condition 2 likely unmet).
  The archived `add-init-command` named comparable interface decisions as
  candidates and created **no ADR**; mirroring that precedent, design should
  assess the gate but will most likely record **no ADR**. Per
  `openspec/config.yaml:22-27`, both conditions must hold.

## Rollback Plan

Purely additive and reversible: `git revert` the change commit(s). `write_config`
returns to zero-parameter byte-copy behavior; `init()` loses `--model`. No
persisted state, no migration, no published artifact. Existing workspaces are
untouched — `init` never overwrites.

## Dependencies

- None beyond current `main`. `typer` is already a runtime dependency;
  `typer.Option` / `typer.prompt` are new usages, not new deps.

## Testing Expectations

Strict TDD (`strict_tdd: true`): RED-GREEN-REFACTOR, `uv run pytest`, branch
coverage ≥ 90%. New behavioral paths (flag override, TTY prompt, non-TTY default,
blank rejection, unsafe-token rejection) land test-first.

## Success Criteria

- [ ] `openkos init --model gemma3` writes `model: gemma3` into `openkos.yaml`.
- [ ] `openkos init` with no flag on a TTY prompts, default `qwen3:8b`.
- [ ] `openkos init` with no flag, non-TTY, writes `model: qwen3:8b` silently.
- [ ] Empty/blank or unsafe-token model input is rejected; nothing is written.
- [ ] Chosen model is written via plain-text token replacement, no YAML dumper;
      the double-spaced-directory regression still passes.
- [ ] Delta spec amends "Static openkos.yaml Template" with the new scenarios.
- [ ] `uv run pytest --cov` ≥ 90% branch; ruff/mypy green.
