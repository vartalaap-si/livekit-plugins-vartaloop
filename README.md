# livekit-plugins-vartaloop

[Vartaloop](https://vartaloop.ai) text-to-speech for [LiveKit Agents](https://docs.livekit.io/agents/): Hinglish and
Indian-language voices (model `narad`), streamed from the first 80 ms frame. This repository is the development home of
the plugin that ships in [livekit/agents](https://github.com/livekit/agents) as `livekit-plugins/livekit-plugins-vartaloop`.

```bash
pip install livekit-plugins-vartaloop
export VARTALOOP_API_KEY=vt_live_…        # from https://tts.vartaloop.ai
```

```python
from livekit.agents import AgentSession
from livekit.plugins import vartaloop

tts = vartaloop.TTS(voice="saanvi", expression="neutral", digits="auto")
tts.prewarm()                               # open the WebSocket before the first turn
session = AgentSession(stt=..., llm=..., tts=tts)
```

## How it streams

| path | transport | what happens |
|---|---|---|
| `tts.stream()` (what `AgentSession` uses) | one pooled `WS /v1/tts/stream` per agent, reopened every 5 min | text is cut into sentences as the LLM produces it; every sentence is sent at once as its own utterance; the server serves them in order and streams the PCM of each back, so sentence N+1 is synthesised while N plays |
| `tts.synthesize(text)` | `POST /v1/tts`, chunked | the body is forwarded chunk by chunk as it arrives |

Nothing buffers a whole utterance. The first frame of every utterance (80 ms, 3 840 bytes) goes to the emitter the moment
it lands; the emitter starts with a 20 ms frame. Output is 16-bit PCM, mono, 24 kHz.

## Options

Constructor arguments; every one of them is also a keyword of `tts.update_options(...)` and applies from the next
sentence without reconnecting.

| option | default | API field | notes |
|---|---|---|---|
| `voice` | `saanvi` | `voice` | any id from `GET /v1/voices` (`await tts.list_voices()`); the voice decides the language and the normaliser |
| `model` | `narad` | — | the model family; reported as `tts.model`, the server picks the model from the voice |
| `expression` | `neutral` | `style` | `neutral` sends nothing; `excited`, `sad`, `whisper` on the voices that carry them |
| `digits` | `auto` | `digits` | `hi`, `mixed`, `en`, `auto` (Hinglish voices) |
| `speed` | voice's pace | `speed` | 0.5–2.0, pitch preserved |
| `temperature` | server 0.3 | `temperature` | 0–2 |
| `seed` | random | `seed` | reproducible audio |
| `postfx` | server on | `postfx` | the loudness / high-pass / limiter chain |
| `profile` | `default` | `profile` | `telephony` for 8 kHz mu-law delivery |
| `raw` | `False` | `raw` | skip the normaliser |
| `api_key` | `$VARTALOOP_API_KEY` | `Authorization: Bearer` | never logged; handshake errors are redacted |
| `base_url` | `https://api.vartaloop.ai` | | |
| `tokenizer` | `tokenize.basic.SentenceTokenizer()` | | ends sentences on `.?!` and the Devanagari danda |

Errors: a `503` (every worker busy; the server adds `Retry-After`) and `5xx` are retryable and the framework retries per
its `APIConnectOptions`; `400` (bad text, voice, expression), `401`/`403` (key), `402` (credits) and `413` are not.

## Examples

- `examples/agent.py` — a room agent speaking Hinglish (`python examples/agent.py console`).
- `examples/say.py` — one sentence to a WAV file.

## Benchmark

`bench/bench.py` compares the plugin with raw REST and raw WebSocket calls to the same API from the same machine,
interleaved per iteration so network drift hits every path alike. From a Mac on a home connection to `api.vartaloop.ai`
(Mumbai), 2026-11-08, 8 iterations, warm prefill cache. TTFA is the time from the request to the first audio bytes
reaching the caller; E2E to the last byte.

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

The plugin adds 1.5 to 5 ms of time-to-first-audio over the raw call on the same transport; the server's own first audio
arrives after 40 to 45 ms including the network. Nothing in the plugin buffers: the server's first 80 ms frame goes to
the emitter as it lands, and the emitter's first frame is 20 ms.

Sustained throughput, N streams in one process each speaking the 3-sentence text back to back for 20 s, pool warmed
(a plugin in production has its connections open before a burst):

| path | streams | utterances | audio s / wall s | RTF per stream | TTFA p50 ms | TTFA p95 ms |
|---|---|---|---|---|---|---|
| ws/raw | 8 | 34 | 23.0 | 0.337 | 66 | 74 |
| ws/plugin | 8 | 32 | 23.6 | 0.337 | 72 | 81 |
| ws/raw | 16 | 48 | 28.0 | 0.567 | 82 | 103 |
| ws/plugin | 16 | 48 | 28.4 | 0.560 | 93 | 116 |

Audio produced per wall second is the same through the plugin as raw; under load the plugin's TTFA sits 6 to 11 ms above
raw at p50 and 7 to 13 ms at p95, the cost of running 16 sentence tokenizers and emitters on one event loop. A first
run without the warm round showed p95 of 0.5 to 1.2 s on the plugin path: that was the connection pool opening N sockets
one at a time under its lock when all streams start cold, not buffering, and is why `prewarm()` exists.

Raw numbers: `bench/RESULTS.md`, `bench/RESULTS-throughput.md`, `bench/results*.json`.

## Development

The upstream layout is mirrored here (`livekit/plugins/vartaloop`, `tests/`). Lint and type-check as upstream does:

```bash
ruff format . && ruff check . && mypy livekit
pytest tests --plugin vartaloop     # inside the livekit/agents workspace
```
