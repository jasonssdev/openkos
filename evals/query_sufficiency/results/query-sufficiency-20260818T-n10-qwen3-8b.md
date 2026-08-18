# Can a pre-synthesis sufficiency check refuse what attribution misses? (#760)

`qwen3:8b`, 14 documents, 10 run(s), `limit=5`, 400 checks.

The bar is NOT 'does it separate the classes'. The shipped `USED:` attribution (PR #763) already refuses 7 of 10 adjacent questions with 0 of 10 false refusals, for free. The bar is whether a pre-synthesis call catches the **3 it misses** while refusing none of the grounded 10.

| arm | grounded refused (any run) | grounded refused (all runs) | adjacent refused (all runs) | attribution survivors caught | median s |
| --- | ---: | ---: | ---: | ---: | ---: |
| `binary` | 1 of 10 | 0 of 10 | 10 of 10 | **3 of 3** | 0.85 |
| `quote` | 0 of 10 | 0 of 10 | 10 of 10 | **3 of 3** | 1.12 |

## Verdict

- **`binary`** — NEGATIVE (false refusals) -- refuses 1 of 10 grounded questions on at least one run. That is the cost that rejected the ruled distance floor, reproduced.
- **`quote`** — POSITIVE -- zero false refusals across 10 grounded questions, and it catches 3 of 3 that attribution misses, at a median 1.12s added per non-refused query.

## Per-question, the three survivors

`binary`:

    RRRRRRRRRR ¿cuáles son las mejores prácticas de chunking en sistemas RAG?
    RRRRRRRRRR ¿cómo se evalúa la calidad de un sistema de recuperación aumen
    RRRRRRRRRR ¿qué relación hay entre la trazabilidad y la verdad contextual  <-- #753's own question

`quote`:

    RRRRRRRRRR ¿cuáles son las mejores prácticas de chunking en sistemas RAG?
    RRRRRRRRRR ¿cómo se evalúa la calidad de un sistema de recuperación aumen
    RRRRRRRRRR ¿qué relación hay entre la trazabilidad y la verdad contextual  <-- #753's own question

## Every grounded question, because the cost is the point

`binary`:

    .......... ¿cómo se aprueban los cambios durante la curación?
    .......... ¿el bundle o el índice es la fuente de verdad?
    .......... ¿por qué se exigen citas textuales?
    .......... ¿quién quedó como responsable de la migración?
    R..R...... ¿quiénes participaron en las reuniones?  <-- FALSE REFUSAL
    .......... ¿qué alcance tiene el borrado de un concepto?
    .......... ¿qué aportó Gustavo Martínez al proyecto?
    .......... ¿qué decisiones se tomaron en las reuniones?
    .......... ¿qué pasa si ingiero el mismo archivo dos veces?
    .......... ¿qué se decidió sobre el historial de decisiones?

`quote`:

    .......... ¿cómo se aprueban los cambios durante la curación?
    .......... ¿el bundle o el índice es la fuente de verdad?
    .......... ¿por qué se exigen citas textuales?
    .......... ¿quién quedó como responsable de la migración?
    .......... ¿quiénes participaron en las reuniones?
    .......... ¿qué alcance tiene el borrado de un concepto?
    .......... ¿qué aportó Gustavo Martínez al proyecto?
    .......... ¿qué decisiones se tomaron en las reuniones?
    .......... ¿qué pasa si ingiero el mismo archivo dos veces?
    .......... ¿qué se decidió sobre el historial de decisiones?

`R` is a refusal, `.` is SUFFICIENT, one character per run.

## What this does not measure

Answer QUALITY after a SUFFICIENT verdict. This probe never calls synthesis, so it cannot say whether letting an answer through produced a good one -- only whether the gate would have opened.

It also runs one chat model on one synthetic corpus of 20 questions. Compliance and calibration are per-model properties; a different backend needs its own run.
