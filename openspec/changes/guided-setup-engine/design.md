# Design: Setup Readiness And A Model Pull

## Layers

```
desktop/client  ──HTTP──▶  localapi/_routes.py  ──▶  openkos.api (_setup.py)
                               │ pulls: localapi/_pulls.py (daemon thread)
                                                       │
                                                       ▼
                                          application/setup.py
                                       (ports: client factory, memory)
                                                       │
                                     llm/base.py ModelRuntime Protocol
                                                       │
                                          llm/ollama.py OllamaClient
                                       GET /api/version, /api/tags
                                       POST /api/pull (NDJSON stream)
```

`application/setup.py` imports `config`, `llm.base` and `application.doctor`
(for the shared task-model enumeration) and never a concrete client. The API
binds the concrete classes in `api/_wiring.py`, as judging does.

## Decisions

1. **Reuse `doctor`, do not call it.** `run_diagnostics` returns fifteen labelled
   strings and runs git, vector and ledger probes a setup screen does not need,
   and three of its reads may raise. Readiness instead reuses its primitives:
   `backends.diagnostics_client` (the same resolver and endpoint precedence),
   `model_tag_matches`, and a new `doctor.configured_task_models(cfg)` that
   `doctor`'s check 5b now calls too, so the two cannot enumerate differently.
   `doctor`'s output is unchanged (its tests are the pin).
2. **Required models.** The chat and embedding models are required: ingest
   embeds and ask retrieves. Task models are reported but optional, since a
   missing judge model fails only the stage that names it. `ready` is "the
   runtime answers and every required model is installed". This differs from
   `doctor`'s criticality, which decides an exit code, not a first-run step.
3. **The version probe decides reachability on Ollama.** `GET /api/version` is
   ADR-0057's intended probe. When it answers, `/api/tags` is read; when it does
   not, models are `installed: null` (unknown, not absent). On
   `openai-compatible`, reachability is `list_models` and `version` is null.
   Probes use the CLI's preflight timeout (5 s).
4. **Memory.** Total physical memory comes from `os.sysconf` (page size times
   pages); where that is unavailable it is null and so is `meets_floor`. The
   floor is ADR-0047's 32 GB of unified memory, as 32 GiB (what a 32 GB Mac
   reports). It is reported, never enforced: the screen decides what to say.
5. **Only configured models can be pulled.** The requested tag must equal one of
   the tags readiness lists. This keeps setup's network use to what the
   workspace needs and keeps an arbitrary name out of the request Ollama makes.
   The refusal is a returned `PullRefused`, like `RunRefused`.
6. **Progress is aggregated by layer digest.** Ollama reports one line per layer
   with that layer's `completed` and `total`; the service keeps the latest pair
   per digest and reports the sums, so the bar never goes backwards between
   layers. The phase is normalised to `preparing`, `downloading`, `verifying`
   or `finishing`; Ollama's raw status text is never forwarded.
7. **Stop is cooperative and between lines.** The client checks `should_stop`
   after each line and closes the response. Ollama keeps the partial blobs and
   resumes the next pull (ADR-0057, S5). An error line (`{"error": ...}`) raises
   the neutral `BackendError` family (`BackendModelNotFound` when Ollama says the
   model does not exist); its text never reaches an HTTP body.
8. **A separate, small registry.** `localapi/_pulls.py` mirrors `_runs.py`
   (admission, at most one active, 16 finished kept, shutdown grace) rather than
   generalising the judging registry, which would change a shipped, pinned
   module for no client benefit. Admission is the service's first stop check,
   after the refusal and installed checks and before the stream starts, so a
   refusal or an unreachable runtime answers the start request itself.
9. **No lock, so no busy.** Neither operation touches `raw/`, `bundle/` or the
   derived stores, so neither takes the workspace lock and a busy workspace
   never blocks them. The existing busy row stays the "try again" mapping for
   every write.
10. **No workspace creation (#1487).** The server is bound to an existing
    workspace; the shell already can run the engine's `init`, which writes the
    recommended defaults non-interactively. Adding an API function with no HTTP
    route and no client would freeze surface without a consumer (ADR-0039).
11. **No ADR.** Each choice is reversible within the additive rule; the
    hard-to-reverse runtime choice is ADR-0057's, which stays Proposed until the
    setup screen ships.

## Pull sequence

```
client          route            registry/thread         service              Ollama
  │ POST pulls ──▶ start(model) ──▶ thread: pull_model ──▶ refusal? installed?
  │                                                       stop.is_set() ─▶ admitted
  │ ◀── 202 job ◀───────────────── (admission or finished)
  │                                                       client.pull ───────▶ /api/pull
  │ GET pull ─────▶ snapshot                               ◀── NDJSON lines (progress)
  │ POST stop ────▶ stop.set()                             should_stop ─▶ close
  │ GET pull ─────▶ finished(outcome: stopped|completed)
```
