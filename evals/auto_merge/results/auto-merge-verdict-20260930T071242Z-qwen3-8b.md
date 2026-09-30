# auto-merge eval -- verdict (#1054)

Calibration: `runs-calibration-20260930T065600Z-qwen3-8b.json`. Confirmation: `runs-confirmation-20260930T071159Z-qwen3-8b.json`.

**Verdict: FAIL**

`B` (highest calibration negative `same` confidence): 0.9500.
`t*`: undefined.

| bar | value |
| --- | --- |
| R1 exposure (negative trials) | 0 |
| R2 false auto-merges | 0 |
| R4 retention | 0.00 |
| R5 stability | 0.00 |

## Failed bars

- no separator: no calibration positive exceeds the highest negative

## Secondary result -- cross-source-excluded population (never deciding)

Verdict under the same rule, excluding every cross-source trial: **FAIL**.

## What a PASS does not establish

Constructed labels are rubric-consistency, not agreement with a human on a real bundle; one model; de-identified analogues may be easier than real documents.
