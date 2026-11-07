# livekit-plugins-vartalaap

[Vartalaap](https://vartalaap.si) text-to-speech for [LiveKit Agents](https://docs.livekit.io/agents/): Hinglish and
Indian-language voices (model `narad`), streamed from the first 80 ms frame. This repository is the development home of
the plugin that ships in [livekit/agents](https://github.com/livekit/agents) as `livekit-plugins/livekit-plugins-vartalaap`.

```bash
pip install livekit-plugins-vartalaap
export VARTALAAP_API_KEY=vt_live_…        # from https://tts.vartaloop.ai
```

```python
from livekit.agents import AgentSession
from livekit.plugins import vartalaap

tts = vartalaap.TTS(voice="saanvi", expression="neutral", digits="auto")
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
| `api_key` | `$VARTALAAP_API_KEY` | `Authorization: Bearer` | never logged; handshake errors are redacted |
| `base_url` | `https://api.vartaloop.ai` | | |
| `tokenizer` | `tokenize.basic.SentenceTokenizer()` | | ends sentences on `.?!` and the Devanagari danda |

Errors: a `503` (every worker busy; the server adds `Retry-After`) and `5xx` are retryable and the framework retries per
its `APIConnectOptions`; `400` (bad text, voice, expression), `401`/`403` (key), `402` (credits) and `413` are not.

## Examples

- `examples/agent.py` — a room agent speaking Hinglish (`python examples/agent.py console`).
- `examples/say.py` — one sentence to a WAV file.

## Benchmark

`bench/bench.py` compares the plugin with raw REST and raw WebSocket calls from the same machine, interleaved, and
measures sustained throughput at 8 and 16 concurrent streams. Results: see `bench/RESULTS.md` (pending the first run
with a key).

## Development

The upstream layout is mirrored here (`livekit/plugins/vartalaap`, `tests/`). Lint and type-check as upstream does:

```bash
ruff format . && ruff check . && mypy livekit
pytest tests --plugin vartalaap     # inside the livekit/agents workspace
```
