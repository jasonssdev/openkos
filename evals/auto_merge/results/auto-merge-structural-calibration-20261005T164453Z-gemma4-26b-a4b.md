# auto-merge eval -- arm `calibration` -- fixture `structural` (#1298)

_Generated: 20261005T164453Z_ · model `gemma4:26b-a4b` · **15 runs** over 28 labelled pairs.

Generation ceiling `8192` · context window `12288`.

Fixture digest: `sha256:9f802d70e9a75d9f5665c9c9f3e4a3bd184fff84b23ab0bb4bcbc6d85f67c2ec` · git `63b5f551541f7e0e98c939331898f747e23f38da`.
Labels are CONSTRUCTED, not adjudicated -- see `structural_fixtures.py`.

| probe | expected | n | same | different | uncertain | missing |
| --- | --- | --- | --- | --- | --- | --- |
| key-homonym | `different` | 165 | 0.00 | 1.00 | 0.00 | 0.00 |
| key-part-whole | `different` | 30 | 0.00 | 1.00 | 0.00 | 0.00 |
| key-distinct-instance | `different` | 45 | 0.00 | 1.00 | 0.00 | 0.00 |
| key-cross-source-dup | `same` | 120 | 1.00 | 0.00 | 0.00 | 0.00 |
| key-reingest-dup | `same` | 30 | 1.00 | 0.00 | 0.00 | 0.00 |
| key-asym-dup | `same` | 30 | 0.97 | 0.03 | 0.00 | 0.00 |
