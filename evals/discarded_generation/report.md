# What extraction generates and throws away — #692

_Generated: 2026-08-15T19:53:47+00:00_

Ratios are computed in CHARACTERS; a chars-to-token factor cancels out of a ratio. Absolute sizes are rendered as tokens at ~3.7 chars/token where that helps a reader, and the conversion is applied nowhere else.

- **discarded share** — generated candidate text belonging to candidates the run did not retain.
- **recoverable share** — the description+body half of that, the only part a title-first phase 1 would not have generated. Every candidate's type and title is generated either way.

## `es-anchored`

| metric | value |
| --- | --- |
| discarded share | **0.93 ±0.01 [0.93-0.94] n=6** |
| recoverable share (two-phase) | **0.92 ±0.01 [0.91-0.93] n=6** |
| generated candidate chars | 12257 ±67 [12183-12375] n=6 |
| discarded chars | 11389 ±59 [11322-11466] n=6 |
| recoverable chars | 11257 ±59 [11190-11334] n=6 |
| ≈ recoverable tokens | 3042 ±16 [3024-3063] n=6 |
| produced | 5.00 ±0.00 [5.00-5.00] n=6 |
| retained | 5.00 ±0.00 [5.00-5.00] n=6 |
| latency (s) | 92.22 ±1.17 [90.92-93.82] n=6 |

Discarded tail chars by the stage that killed them:

| stage | tail chars / run | title-only verdict? |
| --- | --- | --- |
| `_drop_framing_objects` | 11257 | yes |

**100% of the discarded tail was killed by a gate that reads only type and title**, so a title-first phase 1 could have dropped those candidates before their description and body existed. The remainder needs a different argument.

Discarded candidate titles, by run:

- run 1: Reunión de coordinación del proyecto de memoria institucional
- run 2: Reunión de coordinación del proyecto de memoria institucional
- run 3: Reunión de coordinación del proyecto de memoria institucional
- run 4: Reunión de coordinación del proyecto de memoria institucional
- run 5: Reunión de coordinación del proyecto de memoria institucional
- run 6: Reunión de coordinación del proyecto de memoria institucional

## `es-bare`

| metric | value |
| --- | --- |
| discarded share | **0.79 ±0.05 [0.75-0.85] n=6** |
| recoverable share (two-phase) | **0.78 ±0.05 [0.73-0.83] n=6** |
| generated candidate chars | 9368 ±584 [8754-10223] n=6 |
| discarded chars | 7404 ±166 [7205-7657] n=6 |
| recoverable chars | 7254 ±154 [7049-7490] n=6 |
| ≈ recoverable tokens | 1960 ±42 [1905-2024] n=6 |
| produced | 6.00 ±1.26 [5.00-8.00] n=6 |
| retained | 6.00 ±1.26 [5.00-8.00] n=6 |
| latency (s) | 72.49 ±3.89 [67.71-77.95] n=6 |

Discarded tail chars by the stage that killed them:

| stage | tail chars / run | title-only verdict? |
| --- | --- | --- |
| `_drop_framing_objects` | 6565 | yes |
| `judge.select` | 201 | no |

**97% of the discarded tail was killed by a gate that reads only type and title**, so a title-first phase 1 could have dropped those candidates before their description and body existed. The remainder needs a different argument.

Discarded candidate titles, by run:

- run 1: Reunión semanal de operación
- run 2: Reunión semanal de operación
- run 3: Decisión sobre la ventana de contexto y medición, Reunión semanal de operación
- run 4: Decisión sobre la ventana de contexto, Reunión semanal de operación
- run 5: Reunión semanal de operación
- run 6: Reunión semanal de operación

## `ami-ts3005a`

| metric | value |
| --- | --- |
| discarded share | **0.53 ±0.08 [0.43-0.62] n=6** |
| recoverable share (two-phase) | **0.52 ±0.08 [0.43-0.62] n=6** |
| generated candidate chars | 18716 ±4615 [14838-26954] n=6 |
| discarded chars | 9600 ±1123 [9017-11885] n=6 |
| recoverable chars | 9522 ±1108 [8958-11778] n=6 |
| ≈ recoverable tokens | 2574 ±299 [2421-3183] n=6 |
| produced | 3.50 ±3.21 [2.00-10.00] n=6 |
| retained | 3.50 ±3.21 [2.00-10.00] n=6 |
| latency (s) | 145.51 ±32.75 [121.70-208.99] n=6 |

Discarded tail chars by the stage that killed them:

| stage | tail chars / run | title-only verdict? |
| --- | --- | --- |
| `_drop_framing_objects` | 7751 | yes |
| `judge.select` | 1772 | no |

**81% of the discarded tail was killed by a gate that reads only type and title**, so a title-first phase 1 could have dropped those candidates before their description and body existed. The remainder needs a different argument.

Discarded candidate titles, by run:

- run 1: AMI meeting TS3005a, Meeting, Meeting Discussion
- run 2: Drawing Exercise Session, Meeting, Meeting Discussion
- run 3: AMI meeting TS3005a, Design Considerations for Remote Control, Meeting, Meeting Discussion
- run 4: AMI meeting TS3005a, Meeting, Meeting Discussion on Remote Control Design
- run 5: Drawing Exercise Session, Meeting, Meeting Discussion on Remote Control Design
- run 6: AMI meeting TS3005a, Meeting, Meeting Discussion
