# openkos model-spike: derived-object extraction comparison

_Generated: 2026-07-19T23:24:04+00:00_

Runs per fixture: **3**. Fixtures: `call-with-maria` (target 2), `notes-on-enchiridion` (target 2). Total target objects per run-set: **4**.

## Per-model summary

| Model | Installed | schema_valid | type_acc | anti_enum | avg_objs | avg_lat_s | errors | composite |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `qwen3:8b` | yes | 1.00 | 0.75 | 0.83 | 2.00 | 6.41 | 0 | 0.86 |
| `mistral:7b` | yes | 1.00 | 0.25 | 0.78 | 1.33 | 5.03 | 0 | 0.68 |
| `gemma4:e4b` | yes | 1.00 | 0.50 | 0.51 | 3.17 | 7.29 | 0 | 0.67 |

- **schema_valid**: fraction of attempted runs returning a non-empty list of valid objects (empty replies and backend errors count against it).
- **type_acc**: multiset recall of target types (`sum min(produced[t], target[t]) / sum target[t]`).
- **anti_enum**: over-production penalty (`1.0` at/under target; `target_count / (target_count + over)` when excess/wrong-type stubs appear).
- **composite**: equal-weight mean of schema_valid, type_acc, anti_enum; latency breaks ties.

## Per-fixture detail (raw [type:title] per run)

### `call-with-maria`

- Source title: Call with Maria Salazar — 2026-07-14
- Target: 2 -> {'Decision': 1, 'Person': 1}

- `qwen3:8b`:
    - run 1 (6.7s): [Person:Maria Salazar]
    - run 2 (6.1s): [Person:Maria Salazar]
    - run 3 (6.3s): [Person:Maria Salazar]
- `mistral:7b`:
    - run 1 (16.0s): [Concept:Apatheia]
    - run 2 (6.9s): [Concept:Apatheia], [Event:Call with Maria Salazar — 2026-07-14]
    - run 3 (2.8s): [Concept:Apatheia], [Concept:Dichotomy of control]
- `gemma4:e4b`:
    - run 1 (13.8s): [Concept:Apatheia], [Concept:Dichotomy of Control], [Event:Call with Maria Salazar]
    - run 2 (5.0s): [Concept:Apatheia], [Concept:Dichotomy of Control], [Event:Call with Maria Salazar]
    - run 3 (6.2s): [Concept:Apatheia], [Concept:Dichotomy of Control], [Event:Call regarding philosophy concepts]

### `notes-on-enchiridion`

- Source title: Reading notes — Enchiridion, 2026-07-05
- Target: 2 -> {'Concept': 2}

- `qwen3:8b`:
    - run 1 (7.0s): [Concept:Dichotomy of Control], [Concept:Stoicism], [Concept:Apatheia]
    - run 2 (6.2s): [Concept:Dichotomy of Control], [Concept:Stoicism], [Concept:Apatheia]
    - run 3 (6.1s): [Concept:Dichotomy of Control], [Concept:Stoicism], [Concept:Apatheia]
- `mistral:7b`:
    - run 1 (1.8s): [Concept:Apatheia]
    - run 2 (1.0s): [Concept:Apatheia]
    - run 3 (1.5s): [Concept:Apatheia]
- `gemma4:e4b`:
    - run 1 (5.6s): [Concept:Dichotomy of Control], [Concept:Stoicism vs. Epicureanism], [Concept:Apatheia]
    - run 2 (6.2s): [Concept:Dichotomy of Control], [Concept:Stoic Philosophy vs Epicureanism], [Concept:Apatheia]
    - run 3 (7.0s): [Concept:Dichotomy of Control], [Concept:Stoic vs Epicurean Views on Life], [Concept:Apatheia], [Concept:Enchiridion]

## Recommendation

**Recommended default: `qwen3:8b`** (composite 0.86).

Reasoning: it leads on the equal-weight blend of schema-valid rate (1.00), type accuracy (0.75), and anti-enumeration (0.83) at 6.41s avg latency and 0 backend error(s).

Other candidates by composite: `mistral:7b` (0.68); `gemma4:e4b` (0.67).

Sanity-check the raw [type:title] lists above before committing to a default -- the composite cannot see extraction QUALITY, only shape.
