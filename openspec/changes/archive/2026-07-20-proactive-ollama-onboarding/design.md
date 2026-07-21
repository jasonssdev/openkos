# Design: proactive-ollama-onboarding (MVP-2 onboarding UX)

## Technical Approach

Connect / observe / warn — never manage. Four additive, localized edits, ALL in
`src/openkos/cli/main.py`, reusing the already-config-free `llm/ollama.py`
primitives (`OllamaClient`, `list_models()`, `model_tag_matches()`; D1). No
helper extraction, no schema/interface change, `llm/` stays config-free
(remediation text lives in `cli/`). See proposal + spec.

## Architecture Decisions

### D1 — `_PREFLIGHT_TIMEOUT = 5.0` module constant
**Choice**: define `_PREFLIGHT_TIMEOUT = 5.0` at module scope right after
`app = typer.Typer()` (main.py:40), carrying the "fast interactive diagnostic"
comment currently at 2349-2350. Replace the bare `5.0` at main.py:2351 with it;
reuse it in init's preflight.
**Rejected**: two independent `5.0` literals (drift risk).
**Rationale**: one named symbol shared by doctor + init; no leak into `llm/`.

### D2 — init post-success preflight (new code after main.py:141)
**Choice**: after both success echoes, ONE non-fatal probe:
```python
try:
    probe = OllamaClient(model=resolved_model, timeout=_PREFLIGHT_TIMEOUT)
    ready = model_tag_matches(resolved_model, probe.list_models())
except Exception:            # ALL probe failures -> degrade, never fatal
    ready = False
if not ready:
    typer.echo(
        "openkos init: note -- Ollama isn't ready for model "
        f"'{resolved_model}' yet. Run `openkos doctor` to diagnose "
        "(ingest and query need it; the workspace was still created).",
        err=True,
    )
```
`except Exception` (not `BaseException`) catches OllamaUnavailable /
OllamaModelNotFound / OllamaError AND any unexpected error while still letting
Ctrl-C / SystemExit through — nothing in the probe raises `typer.Exit`. `ready`
True ⇒ silent. init exits 0 on EVERY outcome.
**Rejected**: two tailored sub-messages (would duplicate doctor's remediation
without a shared helper — DRY risk); precondition gating (breaks pure-file-writer
guarantee).
**Rationale**: single unified message defers detail to `doctor`, zero remediation
duplication, no pull, no server spawn.

### D3 — doctor not-installed-vs-off via `shutil.which("ollama")`
**Choice**: in the `OllamaUnavailable` branch (main.py:2363-2372) only, gate the
remediation:
```python
if shutil.which("ollama") is None:
    remediation = (
        "no `ollama` binary found on PATH -- install from "
        "https://ollama.com, or if Ollama is already installed (e.g. the "
        "macOS app) start it with `ollama serve`"
    )
else:
    remediation = "ollama serve"
```
`detail=str(exc)` unchanged. `import shutil` at top; used ONLY here.
**Rejected**: pgrep/launchctl/systemd probing (platform-specific, subprocess,
lifecycle coupling); literal "not installed" wording (false-positive on macOS
`Ollama.app` without CLI on PATH).
**Rationale**: `None` is narrow ("no binary on PATH") and covers BOTH remedies to
stay honest under uncertainty; a resolved binary ⇒ present-but-off ⇒ `ollama
serve`. Never over-claims. The generic `OllamaError` branch (2373-2376) and the
model-installed remediation (2389-2392) are untouched.

### D4 — verb pointer appended to OllamaUnavailable only
**Choice**: append `" Or run `openkos doctor` to diagnose the environment."` to
the existing OllamaUnavailable message of `query` (2202-2208), `adjudicate`
(1965-1971), `suggest-relations` (2072-2078). E.g. query:
`"...Start it with `ollama serve`, then try again. Or run `openkos doctor` to
diagnose the environment."`.
**Rejected**: touching OllamaModelNotFound/OllamaError branches; adding to
`ingest` (structurally different degrade path, exit 0).
**Rationale**: specific-before-general ordering + exit codes byte-unchanged; only
the most common failure (server off) gains the pointer.

### D5 — No ADR (confirmed, concurring with proposal)
Additive, single-file, reuses established patterns, zero schema/interface change,
trivially reversible. Fails the significant/hard-to-reverse ADR gate. Concur — no
ADR.

## Data Flow

    init (Phase B success) ─▶ OllamaClient(_PREFLIGHT_TIMEOUT).list_models()
         │  except Exception -> ready=False (never fatal, exit 0 always)
         └─ not ready ─▶ one stderr note -> "run `openkos doctor`"

    doctor "Ollama reachable" [FAIL] ─▶ shutil.which("ollama")
         None -> install-or-serve   |   path -> `ollama serve`

    query/adjudicate/suggest-relations  OllamaUnavailable
         └─▶ existing `ollama serve` msg + "…or `openkos doctor`"

## File Changes

| File | Action | Description |
|---|---|---|
| `src/openkos/cli/main.py` | Modify | `import shutil`; `_PREFLIGHT_TIMEOUT` constant (after :40); init preflight block (after :141); doctor which-gated remediation (:2363-2372); replace `5.0` (:2351); append pointer to 3 OllamaUnavailable branches (:1965, :2072, :2202) |
| `tests/unit/cli/test_init.py` | Modify | preflight outcome branches |
| `tests/unit/cli/test_doctor.py` | Modify | which-None / which-found remediation |
| `tests/unit/cli/test_query.py`, `test_adjudicate.py`, `test_suggest_relations.py` | Modify | appended pointer assertion |
| `docs/cli.md` | Modify | preflight note + not-installed-vs-off wording |

## Interfaces / Contracts

No new interfaces. Reuses `OllamaClient(model, *, timeout)`,
`list_models() -> list[str]`, `model_tag_matches(configured, installed) -> bool`.

## Testing Strategy (branch matrix for 90% gate → feeds sdd-tasks)

| Layer | What | Approach |
|---|---|---|
| Unit (init) | preflight: (a) reachable+model present ⇒ SILENT, exit 0; (b) unreachable (OllamaUnavailable) ⇒ warning, exit 0; (c) reachable+model MISSING (`model_tag_matches` False) ⇒ warning, exit 0; (d) unexpected `Exception` from probe ⇒ warning, exit 0 | fake `urlopen`/injected client, `CliRunner` |
| Unit (doctor) | reachable-check matrix: `which`→None vs `which`→path, each × unreachable; assert exact remediation string per branch; reachable-pass still passes | monkeypatch `shutil.which` + fake client |
| Unit (verbs) | each of query/adjudicate/suggest-relations: OllamaUnavailable message ENDS with doctor pointer; OllamaModelNotFound + OllamaError messages UNCHANGED; exit code 1 preserved | `CliRunner`, forced errors |
| Guard | `llm/` no-config-import AST test stays green (remediation stays in `cli/`) | existing scan, unchanged |

## Threat Matrix

N/A — no routing, VCS/PR automation, or executable-file classification.
`shutil.which` is a read-only PATH lookup; no subprocess is spawned, no Ollama
process/server is managed. init preflight failure modes (DNS, proxy, slow host,
unexpected error) are caught broadly and are strictly non-fatal (RED tests b/c/d).

## Migration / Rollout

No migration. Additive, single file. Rollback = revert the four edits; behavior
returns byte-identical. Well under the 400-line budget — single PR (flag the new
branch count to sdd-tasks for the coverage gate).

## Open Questions

- None blocking.
