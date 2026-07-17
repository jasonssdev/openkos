# Design: `openkos init` — user-selectable local model

## Technical Approach

`init()` resolves a model tag (**flag > TTY prompt > default `qwen3:8b`**) and
passes it to `write_config(root, model)`, which substitutes a single placeholder
token in the packaged template via plain `str.replace` — never a YAML dumper, so
the D5 fold/whitespace-collapse class cannot recur. Validation and the default
live in `config.py`; resolution lives in `cli/main.py`. Verified against HEAD:
`config.py:144-155` (`write_config`, zero params), `main.py:19-75` (`init`, zero
flags), template line 1 (`model: qwen3:8b …`), `fsio.write_exclusive` ("x" mode).
The `workspace-init` "Static openkos.yaml Template" requirement is amended in the
delta spec (separate phase).

## Architecture Decisions

### Decision: placeholder token + plain-text substitution

**Choice**: Template line 1 becomes `model: __OPENKOS_MODEL__` followed by the
**same trailing spaces + comment** as today. `write_config` reads the template,
asserts exactly one `__OPENKOS_MODEL__` (packaging invariant), then
`template.replace("__OPENKOS_MODEL__", tag)`. With the default `qwen3:8b` the
output is **byte-identical** to today's static file (same trailing whitespace →
same bytes).
**Alternatives considered**: `ruamel.yaml` load/dump (the exact D5 corruption
class); rebuilding the whole `model:` line via f-string (loses the verbatim-copy
guarantee for the rest of the file).
**Rationale**: `str.replace` inserts the validated bytes verbatim — no folding,
no quoting, no whitespace normalization — so the D5 fold/double-space bug is
structurally impossible. The token is directory-independent (never reads
`root.name`), so `test_write_config_ignores_directory_name` still holds by
construction. The one-occurrence assert catches template drift.
**ADR?** No (see ADR assessment).

### Decision: resolution in CLI, validation + default in config

**Choice**: `config.DEFAULT_MODEL = "qwen3:8b"`; `config.validate_model(tag) ->
str` (pure: trim + reject). `write_config(root, model: str = DEFAULT_MODEL)`
calls `validate_model` before substituting — a single chokepoint so no unsafe
byte reaches disk. `cli/main.py::_resolve_model(flag: str | None)`: flag →
`validate_model`; else `sys.stdin.isatty()` → `typer.prompt("Model",
default=DEFAULT_MODEL)` → validate; else `DEFAULT_MODEL`. Called in Phase A,
before any write.
**Alternatives considered**: a new LLM-tag module (naming trap — collides with
the OKF `src/openkos/model/` package); validating only in the CLI (leaves
`write_config` unsafe as a standalone unit).
**Rationale**: keeps LLM-tag logic out of the OKF `model/` package; resolution
touches typer/stdin (CLI concern), byte-safety touches the file (config concern).
The default in `write_config`'s signature keeps existing call sites terse and
encodes the "default renders byte-identical" invariant.
**ADR?** No.

### Decision: validation predicate

**Choice**: after `strip()`, reject when the tag is empty, or contains any
**whitespace**, `"`, `'`, or `#`. **Colon is allowed.** `validate_model` raises
`ValueError`; `init` catches it and reuses the existing refusal path
(`"openkos init: refusing to initialize -- {reason}."`, stderr, exit 1) — before
Phase B, so nothing is written.
**Alternatives considered**: rejecting `:` (what the proposal's decision text
literally lists — see Open Questions); curated allowlist (rejected in explore);
live `ollama` query (deferred).
**Rationale**: the hazards for a single-line unquoted YAML plain scalar are a
newline/whitespace (D5 + line break), a leading quote, and `#` starting a comment
(`model: #x` parses to a null value). Rejecting all whitespace makes the only
dangerous colon form — `: ` (colon-space) — unreachable, so `name:tag` tags and
the default `qwen3:8b` stay valid.

## Substitution mechanism

    init() ──flag / TTY prompt / default──▶ _resolve_model ──▶ validate_model ──▶ tag
                                                                                   │
        template ──str.replace("__OPENKOS_MODEL__", tag)──▶ fsio.write_exclusive("x")

## File Changes

| File | Action | Description |
|------|--------|-------------|
| `src/openkos/templates/openkos.yaml.template` | Modify | `qwen3:8b` → `__OPENKOS_MODEL__`; keep trailing spaces + comment verbatim |
| `src/openkos/config.py` | Modify | Add `DEFAULT_MODEL`, `validate_model`; `write_config(root, model=DEFAULT_MODEL)` substitutes the token |
| `src/openkos/cli/main.py` | Modify | `init(model: str \| None = typer.Option(None, "--model"))`; `_resolve_model`; refusal on invalid; `import sys` |
| `openspec/specs/workspace-init/spec.md` | Modify (delta) | Amend "Static openkos.yaml Template"; new scenarios (spec phase) |
| `tests/unit/test_config.py` | Modify | Byte-identity/`ignores_directory_name` expected → token-substituted default; add validation + substitution tests |
| `tests/unit/cli/test_init.py` | Modify | Add flag, TTY-prompt, non-TTY-default, blank/unsafe-rejection paths |
| `docs/cli.md` | Modify | Record model selection at init; resolve the deferred promise |

## Interfaces

```python
DEFAULT_MODEL = "qwen3:8b"
def validate_model(tag: str) -> str: ...          # trim; raise ValueError on unsafe
def write_config(root: Path, model: str = DEFAULT_MODEL) -> None: ...
def _resolve_model(flag: str | None) -> str: ...  # cli/main.py; flag > isatty prompt > default
```

## Testing Strategy

| Layer | What | Approach |
|-------|------|----------|
| Unit | `--model gemma3` writes `model: gemma3` | `runner.invoke(app, ["init", "--model", "gemma3"])`, assert file bytes |
| Unit | TTY prompt (value + empty→default) | monkeypatch `sys.stdin.isatty`→True, `input="gemma3\n"` / `"\n"` |
| Unit | non-TTY default silent | existing `invoke(["init"])` (CliRunner stdin is non-tty) → `model: qwen3:8b` |
| Unit | blank + unsafe (`" "`, `a b`, `a"b`, `a#b`) rejected | exit 1, stderr "refusing", `_snapshot` unchanged |
| Unit | default byte-identical; double-spaced dir regression | expected = template with token→default; `write_config(workspace)` on 40+40 double-space name |

## Threat Matrix

N/A — no routing, shell, subprocess, VCS/PR automation, executable-file
classification, or process-integration boundary (the `ollama` subprocess is
explicitly deferred; `typer.prompt` is interactive stdin, not process
integration). The injection-adjacent surface (a user string written into a
structured file) is the D5 class and is covered by the `validate_model` RED tests
and the double-spaced-dir regression, not by the git/shell matrix.

## ADR assessment

No ADR. The `--model` flag + TTY prompt decides a public CLI interface (gate
condition 1 **met**), but it is **not hard-to-reverse** (condition 2 **unmet**):
purely additive, no persisted state, no consumer reads `model:` back, and
`git revert` fully removes it. Per `openspec/config.yaml:22-27` both conditions
must hold. Precedent: archived `add-init-command` created zero ADRs for the whole
init CLI surface, and `harden-init-workspace` recorded "No ADR" for comparable
interface/pattern decisions. Recorded inline above.

## Migration / Rollout

No migration. No persisted state. `git revert` restores the zero-param byte-copy
`write_config` and drops `--model`. `init` never overwrites existing workspaces.

## Open Questions

- [ ] **Proposal contradiction (resolved here, confirm in spec)**: the locked
  decision text lists "colon" among rejected characters, but the default
  `qwen3:8b` and virtually all Ollama `name:tag` tags contain a colon — a literal
  colon rejection is self-contradictory. This design rejects whitespace/quote/`#`
  (which neutralizes the only hazardous colon form `: `) and allows colon. The
  delta spec's blank/unsafe scenarios must match this predicate, not a literal
  colon ban.
