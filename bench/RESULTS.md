# Vartalaap TTS bench — LiveKit plugin — 2026-11-08 01:53 — https://api.vartaloop.ai

## Latency (8 interleaved iterations, warm)

| path | text | TTFA p50 ms | TTFA p95 ms | E2E p50 ms | audio s | plugin − raw TTFA p50 ms |
|---|---|---|---|---|---|---|
| rest/raw | 1s | 42.2 | 44.4 | 327 | 2.92 |  |
| rest/plugin | 1s | 44.4 | 50.8 | 350 | 3.17 | +2.1 |
| rest/raw | 3s | 46.1 | 47.9 | 1477 | 14.72 |  |
| rest/plugin | 3s | 46.9 | 50.3 | 1506 | 15.05 | +1.5 |
| ws/raw | 1s | 40.8 | 52.7 | 326 | 2.96 |  |
| ws/plugin | 1s | 44.3 | 54.4 | 328 | 2.96 | +3.2 |
| ws/raw | 3s | 40.5 | 46.9 | 1462 | 15.00 |  |
| ws/plugin | 3s | 45.5 | 56.9 | 1524 | 15.04 | +4.9 |

## Sustained throughput (20 s per cell, 3-sentence text)

| path | streams | utterances | audio s / wall s | RTF per stream | TTFA p50 ms | TTFA p95 ms |
|---|---|---|---|---|---|---|
| ws/raw | 8 | 34 | 23.1 | 0.330 | 61 | 74 |
| ws/plugin | 8 | 34 | 22.7 | 0.338 | 72 | 559 |
| ws/raw | 16 | 48 | 28.1 | 0.561 | 88 | 109 |
| ws/plugin | 16 | 48 | 27.9 | 0.560 | 97 | 1188 |
