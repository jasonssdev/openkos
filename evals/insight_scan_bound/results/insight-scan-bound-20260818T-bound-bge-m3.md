# What one `query --save` duplicate scan costs (#764)

Embedding model `bge-m3`, median of 3 runs per point, synthetic bundle over 170 real stored questions.

| filed insights | scan | disk read | embed | payload | x baseline |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 0 | 0.000s | 0.000s | 0.000s | 0.1 KiB | -- |
| 1 | 0.132s | 0.000s | 0.132s | 0.1 KiB | 1.0x |
| 10 | 0.234s | 0.001s | 0.233s | 0.5 KiB | 1.8x |
| 25 | 0.403s | 0.001s | 0.402s | 1.2 KiB | 3.1x |
| 50 | 0.709s | 0.002s | 0.707s | 2.4 KiB | 5.4x |
| 100 | 1.277s | 0.004s | 1.273s | 4.8 KiB | 9.7x |
| 200 | 2.449s | 0.007s | 2.442s | 9.5 KiB | 18.6x |
| 400 | 4.774s | 0.014s | 4.759s | 18.8 KiB | 36.2x |
| 800 | 9.442s | 0.036s | 9.406s | 37.6 KiB | 71.6x |
| 1600 | 18.904s | 0.063s | 18.841s | 75.2 KiB | 143.3x |

## Where the curve crosses a human threshold

- **0.5s** per save: first exceeded at 50 filed insights.
- **1s** per save: first exceeded at 100 filed insights.
- **2s** per save: first exceeded at 200 filed insights.
- **5s** per save: first exceeded at 800 filed insights.
