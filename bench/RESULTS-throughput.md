# Vartaloop TTS bench — LiveKit plugin — 2026-11-08 01:56 — https://api.vartaloop.ai


## Sustained throughput (20 s per cell, 3-sentence text, pool warmed)

| path | streams | utterances | audio s / wall s | RTF per stream | TTFA p50 ms | TTFA p95 ms |
|---|---|---|---|---|---|---|
| ws/raw | 8 | 34 | 23.0 | 0.337 | 66 | 74 |
| ws/plugin | 8 | 32 | 23.6 | 0.337 | 72 | 81 |
| ws/raw | 16 | 48 | 28.0 | 0.567 | 82 | 103 |
| ws/plugin | 16 | 48 | 28.4 | 0.560 | 93 | 116 |
