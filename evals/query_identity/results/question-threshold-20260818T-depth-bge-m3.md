# Does the shipped duplicate threshold survive more relations?

`bge-m3`, question signal only, 561 unique-question pairs over 11 paraphrase families. Zero chat calls.

- paraphrase pairs: 35 (worst 0.4380, median 0.9105)
- different pairs: 526 (best 0.9152, median 0.3730)
- **margin: -0.4772** -- **DOES NOT SEPARATE**

Shipped threshold `DUPLICATE_QUESTION_SIMILARITY = 0.93`:
- paraphrases it would DISCLOSE: 11 of 35
- strangers it would disclose (false positives): 0 of 526

## Worst pair per family

| family | members scored | worst | reaches threshold |
| --- | ---: | ---: | :---: |
| decision-almacenamiento | 3 | 0.8718 | **no** |
| por-que-importa-trazabilidad | 3 | 0.7714 | **no** |
| por-que-importan-inmutables | 6 | 0.8077 | **no** |
| que-es-mvp | 3 | 0.4380 | **no** |
| que-es-rag | 3 | 0.8523 | **no** |
| que-es-trazabilidad | 6 | 0.9569 | yes |
| que-es-verdad-contextual | 1 | 0.9549 | yes |
| que-son-inmutables | 3 | 0.8980 | **no** |
| relacion-mvp-inmutables | 1 | 0.9916 | yes |
| relacion-trazabilidad-verdad | 3 | 0.8788 | **no** |
| responsable-migracion | 3 | 0.8343 | **no** |

## Sensitivity: dropping the author's own contested calls

The families here were authored by the same party measuring against them, so the verdict is reported both ways. Contested calls were flagged in `_PROBES` BEFORE any score was seen.

- paraphrase pairs: 25 (worst 0.8343)
- different pairs: 353 (best 0.9152)
- **margin: -0.0809** -- **still DOES NOT SEPARATE**
- at threshold 0.93: discloses 11 of 25 paraphrases, 0 of 353 strangers

## Hardest negatives (same topic, different question)

- 0.9152 -- '¿para qué sirve que las fuentes sean inmutables?' vs '¿qué son las fuentes inmutables?'
- 0.9142 -- '¿para qué sirve que las fuentes sean inmutables?' vs '¿qué significa que una fuente sea inmutable?'
- 0.8974 -- '¿por qué son importantes las fuentes inmutables?' vs '¿qué son las fuentes inmutables?'
- 0.8783 -- '¿a qué se le llama fuente inmutable?' vs '¿para qué sirve que las fuentes sean inmutables?'
- 0.8766 -- '¿qué es la trazabilidad?' vs '¿qué se gana con la trazabilidad?'
