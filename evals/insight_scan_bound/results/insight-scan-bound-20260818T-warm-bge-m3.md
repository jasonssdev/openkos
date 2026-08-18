# What one `query --save` duplicate scan costs (#764)

Embedding model `bge-m3`, median of 3 runs per point, synthetic bundle over 170 real stored questions.

| filed insights | cold scan | WARM scan | disk read | payload | cold/warm |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 0 | 0.000s | **0.000s** | 0.000s | 0.1 KiB | 1x |
| 1 | 0.119s | **0.108s** | 0.000s | 0.1 KiB | 1x |
| 10 | 0.222s | **0.107s** | 0.001s | 0.5 KiB | 2x |
| 25 | 0.395s | **0.110s** | 0.001s | 1.2 KiB | 4x |
| 50 | 0.690s | **0.113s** | 0.002s | 2.4 KiB | 6x |
| 100 | 1.283s | **0.119s** | 0.004s | 4.8 KiB | 11x |
| 200 | 2.452s | **0.129s** | 0.008s | 9.5 KiB | 19x |
| 400 | 4.800s | **0.152s** | 0.015s | 18.8 KiB | 32x |
| 800 | 9.479s | **0.200s** | 0.031s | 37.6 KiB | 47x |
| 1600 | 18.820s | **0.285s** | 0.074s | 75.2 KiB | 66x |

## Where the curve crosses a human threshold

- **0.5s** per save: cold 50 filed insights, warm never at any measured size.
- **1s** per save: cold 100 filed insights, warm never at any measured size.
- **2s** per save: cold 200 filed insights, warm never at any measured size.
- **5s** per save: cold 800 filed insights, warm never at any measured size.
