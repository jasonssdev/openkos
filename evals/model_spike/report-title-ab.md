# openkos title-anchor A/B (issue #377, proposal D1)

_Generated: 2026-08-04T13:43:44+00:00_

Model: **`qwen3:8b`**. Runs per fixture per arm: **3**. Fixtures: `call-with-maria` (target 3), `notes-on-enchiridion` (target 2).

`_SYSTEM_PROMPT` is byte-identical across every arm. The only variable is the `SOURCE TITLE:` value in the user turn.

## Arms

| Arm | Meaning | Title sent |
| --- | --- | --- |
| `h1` | v0.2.1 -- `derive_source_title(raw)` (the document's own H1) | `call-with-maria`: "Call with Maria Salazar — 2026-07-14"; `notes-on-enchiridion`: "Reading notes — Epictetus, Enchiridion — 2026-07-05" |
| `stem` | v0.2.0 -- `titleize(path.stem)` (the filename) | `call-with-maria`: "call with maria 2026 07 14"; `notes-on-enchiridion`: "notes on the enchiridion 2026 07 05" |
| `none` | control -- no `SOURCE TITLE:` line in the user turn | `call-with-maria`: (omitted); `notes-on-enchiridion`: (omitted) |

## Per-arm summary

| Arm | avg_objects | twin_rate | schema_valid | type_acc | anti_enum | avg_lat_s | errors |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `h1` | **2.17** | 0.00 | 1.00 | 0.67 | 0.81 | 27.45 | 0 |
| `stem` | **2.00** | 0.08 | 1.00 | 0.61 | 0.79 | 23.72 | 0 |
| `none` | **2.00** | 0.00 | 1.00 | 0.67 | 0.83 | 23.61 | 0 |

- **avg_objects**: mean produced-object count per run. The primary signal, because the regression is a count (3 -> 1).
- **twin_rate**: fraction of produced objects whose title merely restates the SOURCE TITLE this arm sent (proposal D4). The anchor's fingerprint. The `none` arm has no title to echo, so it reads 0.00 by construction, not by merit.

## Per-fixture detail (raw [type:title] per run)

### `call-with-maria`

- Target: 3 -> {'Decision': 1, 'Person': 1, 'Concept': 1}

- `h1`:
    - run 1 (45.7s): [Person:Maria Salazar]
    - run 2 (20.4s): [Person:Maria Salazar]
    - run 3 (22.3s): [Person:Maria Salazar]
- `stem`:
    - run 1 (27.0s): [Event:Call with Maria 2026 07 14]
    - run 2 (21.0s): [Person:Maria Salazar]
    - run 3 (22.1s): [Person:Maria Salazar]
- `none`:
    - run 1 (23.9s): [Person:Maria Salazar]
    - run 2 (22.8s): [Person:Maria Salazar]
    - run 3 (23.2s): [Person:Maria Salazar]

### `notes-on-enchiridion`

- Target: 2 -> {'Concept': 2}

- `h1`:
    - run 1 (25.9s): [Concept:Dichotomy of Control], [Concept:Stoicism], [Concept:Apatheia]
    - run 2 (22.4s): [Concept:Dichotomy of Control], [Concept:Stoicism], [Concept:Apatheia]
    - run 3 (28.2s): [Concept:Dichotomy of Control], [Concept:Stoicism], [Concept:Apatheia], [Concept:Enchiridion]
- `stem`:
    - run 1 (26.0s): [Concept:Dichotomy of Control], [Concept:Stoicism], [Concept:Apatheia]
    - run 2 (22.8s): [Concept:Dichotomy of Control], [Concept:Stoicism], [Concept:Apatheia]
    - run 3 (23.4s): [Concept:Dichotomy of Control], [Concept:Stoicism], [Concept:Apatheia]
- `none`:
    - run 1 (24.1s): [Concept:Dichotomy of Control], [Concept:Stoicism], [Concept:Apatheia]
    - run 2 (23.3s): [Concept:Dichotomy of Control], [Concept:Stoicism], [Concept:Apatheia]
    - run 3 (24.3s): [Concept:Dichotomy of Control], [Concept:Stoicism], [Concept:Apatheia]

## Verdict

- Object-count spread across arms: **0.17** (`stem` 2.00 -> `h1` 2.17).
- `h1` twin_rate: **0.00**.
- `stem` twin_rate: **0.08**.
- `none` twin_rate: **0.00**.

**The anchor looks innocent.** The arms are within noise of each other, so the `SOURCE TITLE:` value does not carry the 3->1 regression. Do NOT rewrite the prompt on this evidence: widen the search inside slice 1. The measured baseline recorded here is still what #379's gate needs.
