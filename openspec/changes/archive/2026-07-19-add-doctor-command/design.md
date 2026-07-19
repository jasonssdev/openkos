# Design: `add-doctor-command` — `openkos doctor` environment health check

## Technical Approach

`doctor` is a new `@app.command()` in `cli/main.py`, a sibling of `status`/`lint`
but with a deliberately NEW control-flow shape: instead of `raise typer.Exit` on
the first failure, it runs ALL five checks, appends each to a `list[CheckResult]`,
renders every line unconditionally, then exits ONCE (`code=1`) if any *critical*
check FAILED. It reuses the existing detection layers verbatim
(`config.require_workspace` → `config.read_config` → `okf.survey_bundle`) and one
genuinely new library capability: `OllamaClient.list_models()` +
module-level `model_tag_matches()` in `llm/ollama.py` (config-free, leaf-safe).
Remediation TEXT lives only in `cli/main.py`; `llm/` gains only mechanical
plumbing so `test_llm_modules_do_not_import_config` stays green.

## Architecture Decisions

| # | Decision | Alternatives rejected | Rationale |
|---|---|---|---|
| **D1** | `list_models()` as a method on `OllamaClient`, cloning `chat()`'s urlopen + `_map_http_error`/`_unavailable` plumbing | Standalone `preflight.py` free function taking `host: str` | A method reuses `self._host`/`self._urlopen`/`self._timeout` and the single `OllamaUnavailable`/`OllamaError` vocabulary `doctor` needs. A free function would duplicate host-normalization + error mapping. |
| **D2** | Read each tag defensively as `entry.get("model") or entry.get("name")`, skip entries with neither | Assume `name` only | `/api/tags` field variance is documented upstream (ollama#9985); both keys appear across versions. |
| **D3** | `model_tag_matches(configured, installed: list[str])` is module-level (pure, stdlib-only) near the client | Method on the client | Pure string logic touches no instance state; module-level keeps it independently testable and leaf-safe. |
| **D4** | Case-**sensitive** exact match after normalizing a bare name to `<name>:latest` on BOTH sides | Case-insensitive lowercasing | Ollama tags are conventionally lowercase but not enforced; honest exact match avoids false positives. Symmetric normalization makes bare `qwen3` match installed `qwen3` AND `qwen3:latest`. |
| **D5** | Local `CheckResult` (frozen dataclass) + `_render_check` helper; accumulate then exit once | Each check `raise`s | The run-all-then-exit-once contract requires values, not exceptions, so a failed check never short-circuits later checks. |
| **D6** | When Ollama is unreachable, model-installed is `[SKIP]` "blocked", NOT `[FAIL]` | Fail both | Model-missing and Ollama-down share one root cause; skipping avoids double-reporting and a confusing double-remediation. `[SKIP]` never flips the exit code. |
| **D7** | Criticality split: config-valid / Ollama-reachable / model-installed are **critical**; workspace-initialized and bundle-readable are **informational** | All critical | Keeps `doctor` usable pre-`init` as a pure "is Ollama ready" preflight (maintainer intent): healthy Ollama + default model outside a workspace → exit 0. |

**ADR gate — verdict: NO ADR.** (1) Novel pattern/interface with tradeoffs? No —
`list_models()` mirrors `chat()`, and accumulate-then-exit-once is a small local
idiom, not a technology/architecture choice. (2) Hard to reverse? No — additive,
`git revert` removes it; no persisted state/schema/migration/dependency. BOTH must
hold; neither does. Matches the zero-ADR precedent of `add-query-command`.

## Interfaces / Contracts

`llm/ollama.py` — new method, symmetric to `chat()`:

```python
def list_models(self) -> list[str]:
    """GET `{host}/api/tags`; return installed model tags. Config-free (D1)."""
    url = f"{self._host}/api/tags"
    request = urllib.request.Request(url, method="GET")  # noqa: S310
    try:
        response = self._urlopen(request, timeout=self._timeout)
    except urllib.error.HTTPError as exc:
        raise _map_http_error(exc) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise self._unavailable(exc) from exc
    try:
        body = response.read()
    except (urllib.error.URLError, TimeoutError, OSError,
            http.client.IncompleteRead) as exc:
        raise self._unavailable(exc) from exc
    try:
        entries = json.loads(body)["models"]
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise OllamaError(f"Malformed response from Ollama: {exc}") from exc
    tags: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        tag = entry.get("model") or entry.get("name")   # D2 field variance
        if isinstance(tag, str) and tag:
            tags.append(tag)
    return tags
```

`llm/ollama.py` — new module-level pure helper (D3, D4):

```python
def model_tag_matches(configured: str, installed: list[str]) -> bool:
    """True if `configured` matches any installed tag. A bare name (no ':')
    normalizes to '<name>:latest' per Ollama convention; case-sensitive."""
    wanted = configured if ":" in configured else f"{configured}:latest"
    for tag in installed:
        normalized = tag if ":" in tag else f"{tag}:latest"
        if normalized == wanted:
            return True
    return False
```

`cli/main.py` — check representation, render helper, and control flow (D5):

```python
@dataclass(frozen=True)
class CheckResult:
    label: str
    status: str            # "pass" | "fail" | "skip"
    critical: bool
    remediation: str | None = None   # printed as "  -> {cmd}" under a FAIL
    detail: str | None = None        # appended to the label line

def _render_check(r: CheckResult) -> None:
    tag = {"pass": "[PASS]", "fail": "[FAIL]", "skip": "[SKIP]"}[r.status]
    line = f"{tag} {r.label}"
    if r.detail:
        line += f" — {r.detail}"
    typer.echo(line)
    if r.status == "fail" and r.remediation:
        typer.echo(f"  -> {r.remediation}")
```

Doctor flow (skeleton):

```python
@app.command()
def doctor() -> None:
    root = Path.cwd()
    results: list[CheckResult] = []

    # 1. workspace-initialized (informational)
    reason = config.require_workspace(root)
    in_ws = reason is None
    results.append(CheckResult("Workspace initialized",
        "pass" if in_ws else "fail", critical=False,
        remediation=None if in_ws else "openkos init",
        detail=None if in_ws else reason))

    # 2. config-valid (critical, workspace-only; SKIP outside)
    cfg = None
    if in_ws:
        try:
            cfg = config.read_config(root)
            results.append(CheckResult("Config valid", "pass", True,
                detail=f"model {cfg.model}"))
        except (OSError, ValueError) as exc:
            results.append(CheckResult("Config valid", "fail", True,
                remediation="fix openkos.yaml", detail=str(exc)))
    else:
        results.append(CheckResult("Config valid", "skip", True))

    model = cfg.model if cfg is not None else config.DEFAULT_MODEL

    # 3. Ollama-reachable (critical, always)
    reachable, installed = False, []
    client = OllamaClient(model=model)
    try:
        installed = client.list_models()
        reachable = True
        results.append(CheckResult("Ollama reachable", "pass", True,
            detail=f"{len(installed)} models"))
    except OllamaUnavailable as exc:
        results.append(CheckResult("Ollama reachable", "fail", True,
            remediation="ollama serve", detail=str(exc)))
    except OllamaError as exc:                       # non-transport server error
        results.append(CheckResult("Ollama reachable", "fail", True, detail=str(exc)))

    # 4. model-installed (critical, always; SKIP-blocked if unreachable — D6)
    label = f"Model '{model}' installed"
    if not reachable:
        results.append(CheckResult(label, "skip", True,
            detail="blocked: Ollama unreachable"))
    elif model_tag_matches(model, installed):
        results.append(CheckResult(label, "pass", True))
    else:
        results.append(CheckResult(label, "fail", True,
            remediation=f"ollama pull {model}"))

    # 5. bundle-readable (informational, workspace-only; SKIP outside)
    if in_ws:
        survey = okf.survey_bundle(config.WorkspaceLayout(root).bundle_dir)
        if not survey.findings:
            results.append(CheckResult("Bundle readable", "pass", False,
                detail=f"{survey.sources} sources, {survey.concepts} concepts"))
        else:
            results.append(CheckResult("Bundle readable", "fail", False,
                detail=f"{len(survey.findings)} issue(s)"))
    else:
        results.append(CheckResult("Bundle readable", "skip", False))

    typer.echo(f"openkos doctor: checking environment at {root}")
    typer.echo()
    for r in results:
        _render_check(r)

    if any(r.status == "fail" and r.critical for r in results):
        raise typer.Exit(code=1)
```

New import in `cli/main.py`: `from dataclasses import dataclass`; add
`model_tag_matches` to the existing `from openkos.llm.ollama import (...)` block.

## Exact literal output

Line format: `[PASS] <label>` / `[FAIL] <label>` / `[SKIP] <label>`, optional
` — <detail>` suffix; remediation on its own indented line `  -> <cmd>` only under
a `[FAIL]`.

Healthy run (in workspace) — exit 0:

```
openkos doctor: checking environment at /home/u/ws

[PASS] Workspace initialized
[PASS] Config valid — model qwen3:8b
[PASS] Ollama reachable — 3 models
[PASS] Model 'qwen3:8b' installed
[PASS] Bundle readable — 2 sources, 5 concepts
```

Broken run (Ollama down, in workspace) — exit 1:

```
openkos doctor: checking environment at /home/u/ws

[PASS] Workspace initialized
[PASS] Config valid — model qwen3:8b
[FAIL] Ollama reachable — Ollama not reachable at http://localhost:11434: <urlopen error ...>
  -> ollama serve
[SKIP] Model 'qwen3:8b' installed — blocked: Ollama unreachable
[PASS] Bundle readable — 2 sources, 5 concepts
```

## Data / control flow

```
doctor
  ├─ require_workspace(root) ── reason? ─→ [1] workspace-initialized (info)
  ├─ if in_ws: read_config ──── except(OSError,ValueError) ─→ [2] config-valid (CRIT)
  │            else SKIP
  ├─ model = cfg.model | DEFAULT_MODEL
  ├─ OllamaClient(model).list_models() ── OllamaUnavailable/OllamaError ─→ [3] reachable (CRIT)
  ├─ reachable ? model_tag_matches(model, installed) : SKIP-blocked ─────→ [4] model (CRIT, D6)
  ├─ if in_ws: survey_bundle(bundle_dir) else SKIP ─────────────────────→ [5] bundle (info)
  ├─ render every CheckResult
  └─ any(fail & critical) → Exit(1) ; else 0
```

## File Changes

| File | Action | Description |
|---|---|---|
| `src/openkos/llm/ollama.py` | Modify | Add `list_models()` method + module-level `model_tag_matches()` |
| `src/openkos/cli/main.py` | Modify | New `doctor` command, `CheckResult` dataclass, `_render_check`; import `dataclass` + `model_tag_matches` |
| `docs/cli.md` | Modify | New `### openkos doctor` section; command list 6→7 |
| `docs/roadmap.md` | Modify | Add `doctor` to MVP-1 command list (~line 46) |
| `tests/unit/llm/test_ollama.py` | Modify | `list_models` (success, HTTPError, transport, malformed body, name/model variance, skip-bad-entry) + `model_tag_matches` tests |
| `tests/unit/cli/test_doctor.py` | Create | Full CLI scenario coverage |

## Testing Strategy (strict TDD, ≥90 branch, no network)

| Layer | What | Approach |
|---|---|---|
| Unit | `list_models()` | Construct `OllamaClient(model, urlopen=<fake>)` with `_FakeResponse`; assert tags parsed, `model or name` variance handled, bad entries skipped; `HTTPError`→`OllamaError`, `URLError`/`TimeoutError`→`OllamaUnavailable`, bad JSON→`OllamaError` |
| Unit | `model_tag_matches()` | Pure table: exact `qwen3:8b`↔`qwen3:8b`; bare `qwen3`↔`qwen3` and ↔`qwen3:latest`; mismatch; empty list; case difference → no match |
| CLI | `doctor` scenarios | Monkeypatch `openkos.cli.main.OllamaClient` with a stub returning canned `list_models()` or raising the right `OllamaError` subclass. Cover: (a) all healthy → exit 0, all `[PASS]`; (b) Ollama down → `[FAIL] Ollama reachable`+`  -> ollama serve`, `[SKIP]` model, exit 1; (c) model missing → `[FAIL] Model ... installed`+`  -> ollama pull ...`, exit 1; (d) malformed `openkos.yaml` (write invalid YAML post-init) → `[FAIL] Config valid`, exit 1; (e) no workspace → `[FAIL]` workspace (non-critical), `[SKIP]` config+bundle, Ollama checks vs `DEFAULT_MODEL`, healthy → exit 0 |
| CLI | run-all-then-exit-once | A scenario where an EARLY critical check fails yet a LATER check still prints (e.g. Ollama down but bundle-readable still renders `[PASS]`) — proves no mid-scan short-circuit |

Seam: `list_models()` reuses the existing `urlopen=` constructor kwarg — no new
transport seam. CLI tests patch the `OllamaClient` symbol imported at `main.py:16`.

## docs plan

`docs/cli.md`: add `### openkos doctor` after `status`/`forget` — describe the 5
checks, the `[PASS]`/`[FAIL]`/`[SKIP]` format, remediation lines, criticality split,
outside-workspace behavior (usable pre-`init` as an Ollama preflight), and the
exit-code contract (1 iff a critical check failed). `docs/roadmap.md`: extend the
MVP-1 command list to include `doctor` (6→7 commands).

## Threat Matrix

**N/A** — no shell, subprocess, routing, VCS/PR automation, or executable-file
classification. The only new surface is a single `GET {host}/api/tags` to the
already-trusted, scheme-normalized local Ollama host — the SAME S310-audited
`urllib` pattern `chat()` already uses (host is user/env config, never derived
from document content). Every transport failure maps to a typed `OllamaError`;
nothing is executed.

## Migration / Rollout

No migration. Additive and local; `git revert` removes the command, the two
`llm/` additions, docs, and tests. No persisted state, config-schema, or
dependency change.

## Open Questions

- [ ] None blocking. Criticality split (D7), skip-blocked model check (D6), tag
      normalization (D4), and field-variance handling (D2) all resolved.
