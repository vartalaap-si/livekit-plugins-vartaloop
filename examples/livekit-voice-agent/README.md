# Vartaloop voice agent on LiveKit

A complete, runnable voice assistant you can talk to from your browser:

```
browser mic ─▶ LiveKit room ─▶ Silero VAD ─▶ Deepgram STT ─▶ Claude (or OpenAI) ─▶ Vartaloop TTS ─▶ browser speaker
```

Three small pieces, all in this folder:

| file | what it is |
|---|---|
| [`agent.py`](agent.py) | the LiveKit Agents worker: STT → LLM → TTS, with Vartaloop as the voice |
| [`token_server.py`](token_server.py) | mints a room token for the browser and serves the web page (one URL) |
| [`web/`](web/) | the client: Connect button, mic, agent audio, live transcript, typed fallback |

The TTS is the [`livekit-plugins-vartaloop`](../../README.md) plugin: as the LLM writes, each finished sentence is sent
to `api.vartaloop.ai` over one pooled WebSocket and its audio plays while the next sentence is still being written.
Default voice is `sahil` at speed 1.1 (Hinglish and Indian English); `kiara` and `kevin` are the English voices.

## Prerequisites

- Python 3.10 or newer.
- A LiveKit server. Either of:
  - **local**: `brew install livekit` (or the [binary](https://docs.livekit.io/home/self-hosting/local/)) and run
    `livekit-server --dev`. It listens on `ws://localhost:7880` with API key `devkey` / secret `secret`.
  - **LiveKit Cloud**: a free project at [cloud.livekit.io](https://cloud.livekit.io); copy URL, key and secret from
    *Settings → Keys*.
- API keys: [Vartaloop](https://tts.vartaloop.ai), [Deepgram](https://console.deepgram.com), and one LLM:
  [Anthropic](https://console.anthropic.com) (default) or [OpenAI](https://platform.openai.com/api-keys).
- A browser with a microphone (Chrome, Edge, Safari, Firefox).

## Run it (about 5 minutes)

```bash
cd examples/livekit-voice-agent
python -m venv .venv && source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
# inside a checkout of this repository you can use the local plugin instead of the git URL:
#   pip install -e ../..

cp .env.example .env         # then open .env and paste your keys
python agent.py download-files   # one-off: fetches the VAD model weights
```

Open three terminals in this folder (each with the venv activated):

```bash
livekit-server --dev            # 1. the LiveKit server (skip with LiveKit Cloud)
```

```bash
lk agent dev                    # 2. the agent worker; "registered worker" means it is ready
```

`lk agent dev` (LiveKit CLI 2.19+, `brew install livekit-cli`) finds `agent.py`, uses the `.venv` and `.env` in this folder
and hot-reloads on edits. `python agent.py dev` does the same without the CLI.

```bash
python token_server.py          # 3. token server + web page on http://localhost:8080
```

Then open **http://localhost:8080**, press **Connect & talk**, allow the microphone. Within a second or two the agent
joins the room and greets you in Sahil's voice. Say anything, for example:

- *"What can you do?"*
- *"Tell me about Mumbai in two sentences."*
- *"मुझे एक joke सुनाओ"* or *"Mumbai ke baare mein do line mein batao"*

The transcript panel shows your words as Deepgram hears them and the agent's reply as it is spoken. If you have no
microphone, type in the box at the bottom: the message goes through the same LLM → TTS path.

### Try it without a browser

`lk agent console` (or `python agent.py console`) runs the same agent against your laptop's microphone and speakers, no LiveKit server or
token needed. Useful for a first smoke test of the keys.

## Configuration

Everything is an environment variable (`.env` is read on start-up; see [`.env.example`](.env.example)).

| variable | default | notes |
|---|---|---|
| `LIVEKIT_URL` / `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET` | `ws://localhost:7880` / `devkey` / `secret` | the `--dev` server's placeholder values; replace with your Cloud project's |
| `DEEPGRAM_API_KEY` | — | required |
| `DEEPGRAM_LANGUAGE` | `hi` | Hindi model; transcribes Hinglish with its English words (measured). `en` / `en-IN` for English-only callers. `multi` dropped a Hindi sentence in our test. |
| `LLM_PROVIDER` | `anthropic` | `anthropic` (Claude) or `openai`; only that provider's key is needed |
| `ANTHROPIC_API_KEY` | — | required when `LLM_PROVIDER=anthropic` |
| `ANTHROPIC_MODEL` | `claude-opus-5-5` | best answers; `claude-haiku-5-5` for the fastest first token (measured below) |
| `OPENAI_API_KEY` | — | required when `LLM_PROVIDER=openai` |
| `OPENAI_MODEL` | `gpt-4o-mini` | any chat model; faster is better for voice |
| `VARTALOOP_API_KEY` | — | required; from the Vartaloop portal |
| `VARTALOOP_VOICE` | `sahil` | `saanvi`, `meera`, `mahesh`, `ananya`, `arjun` (Hinglish / Indian English), `kiara`, `kevin` (English) |
| `VARTALOOP_SPEED` | `1.1` | 0.5–2.0, pitch preserved |
| `AGENT_INSTRUCTIONS` / `AGENT_GREETING` | see `agent.py` | the system prompt and the first sentence |

### Hinglish

The agent answers in whatever language the user speaks (the system prompt says so). Deepgram's `hi` model, the default,
hears Hinglish the way people speak it and writes it the way they type it: *"Mumbai के बारे में दो line में बताओ"*. It
handled a pure-English question in the same session too. nova-3's `multi` mode dropped a plain Hindi sentence in our
test, so use `hi` for Hinglish and `en` / `en-IN` only for English-only callers. Keep a Hinglish voice (`sahil`,
`saanvi`, …); the English voices read Devanagari poorly.

## How the pieces talk to each other

1. The browser asks `GET /token`. `token_server.py` signs a JWT with your LiveKit secret that allows joining one room
   (a fresh random room per visitor) and returns it with `LIVEKIT_URL`. The secret never reaches the browser.
2. The browser connects, publishes its microphone, and registers a handler for the `lk.transcription` text-stream topic.
3. The LiveKit server sees a new room and dispatches the worker (`lk agent dev` registered it). The worker runs
   `entrypoint()`: builds the `AgentSession`, opens the Vartaloop WebSocket (`tts.prewarm()`), joins, and says the greeting.
4. Each user turn: VAD detects end of speech → Deepgram returns the transcript → Claude (or OpenAI) streams a reply → the plugin
   cuts it into sentences and streams each to Vartaloop → 24 kHz PCM frames go onto the agent's audio track.
5. The agent publishes transcripts for both sides. User segments carry `lk.transcribed_track_id`; the client uses that
   to put them on the right. Segments update in place by `lk.segment_id`, so the agent's bubble grows as it speaks.

## Turn detection and latency

A voice agent feels fast when two things are short: the gap between the user's last word and the agent deciding the
turn is over, and the gap from that decision to the first audio. `agent.py` sets both up deliberately:

| stage | what is configured | why |
|---|---|---|
| end of speech | Silero VAD, `min_silence_duration=0.4` | first, cheap signal that the user paused |
| end of turn | `TurnHandlingOptions(turn_detection=inference.TurnDetector(local_fallback=True), endpointing=EndpointingOptions(mode="dynamic", min_delay=0.3, max_delay=3.0))` | a small transformer reads the transcript so far and predicts whether the sentence is finished (English and Hindi). It waits `min_delay` 0.3 s when the turn is clearly over and up to `max_delay` 3 s when the user is mid-thought, so "I want to book a… " is not cut off. Hosted on LiveKit Cloud; the bundled local model elsewhere. |
| STT | Deepgram `interim_results`, `no_delay`, `endpointing_ms=25` | partial transcripts stream in while the user is still speaking |
| LLM start | `PreemptiveGenerationOptions(enabled=True)` | the LLM starts on the interim transcript while the turn detector is still deciding; a wrong guess is discarded, a right one hides most of the end-of-utterance delay |
| LLM | Claude via `anthropic.LLM(max_tokens=300, caching="ephemeral")` | the system prompt is cached on Anthropic's side after the first turn; replies are bounded |
| TTS | Vartaloop, `tts.prewarm()` | WebSocket opened before the greeting; each sentence streams as the LLM finishes it, first frame in tens of milliseconds |
| barge-in | `InterruptionOptions(mode="vad", min_duration=0.4, resume_false_interruption=True)` | the user can talk over the agent; a cough does not leave silence |

Measured on this machine against the live services (one spoken question, `python agent.py dev`, local `livekit-server`):

| stage | metric | `claude-haiku-5-5` | `claude-opus-5-5` |
|---|---|---|---|
| turn detector | end-of-utterance delay, first turn | 0.42 s | 0.42 s |
| turn detector + preemptive generation | end-of-utterance delay, later turns | 0.00 s | 0.00 s |
| LLM | time to first token (four runs) | 0.97–1.46 s | 1.72–2.22 s |
| Vartaloop TTS | time to first byte | 74–89 ms | 131–175 ms |

Both models gave the same answer. Opus is the default for answer quality; with preemptive generation most of its extra
first-token time overlaps the turn decision. Set `ANTHROPIC_MODEL=claude-haiku-5-5` when every tenth of a second counts.
The LLM is the largest share of the gap; the turn detector and Vartaloop together add well under half a second.

Every assistant turn logs one `turn metrics` line in the worker output with `llm_node_ttft`, `llm_node_ttfs` (first
sentence), `tts_node_ttfb` and the end-of-turn delays, so you can see the effect of any change immediately.

### Nothing touches disk in the hot path

Everything a turn needs is in memory before the first word: the VAD and turn-detector weights are loaded once per worker
process at start-up (`prewarm`), the Vartaloop WebSocket is opened before the greeting, the token server is stateless, and
no request reads a file, a database or a cache. The only I/O during a conversation is the network traffic to LiveKit,
Deepgram, the LLM and Vartaloop. Logs go to stdout.

## Troubleshooting

| symptom | cause and fix |
|---|---|
| Page connects but you hear nothing; a **Tap to enable audio** button appears | the browser's autoplay policy blocked audio that started without a click. Tap the button (it calls `room.startAudio()`). |
| Agent never joins (status stays *waiting for the agent*) | `lk agent dev` is not running, or points at a different `LIVEKIT_URL`/key than the token server. Both read the same `.env`. |
| `401` / `403` in the agent log mentioning `vartaloop` | wrong or missing `VARTALOOP_API_KEY`. `402` means the key has no credits. |
| `401` from `api.deepgram.com`, `api.anthropic.com` or `openai` | the corresponding key is missing or invalid. |
| Agent log: `429 … Your account is not active` from OpenAI | the key is valid but the OpenAI account has no active billing. Fix billing, or set `LLM_PROVIDER=anthropic`. |
| Agent hears nothing you say | check the browser's mic permission and that **Mute mic** is not toggled. In the agent log, Deepgram should print interim transcripts as you speak. |
| Hindi comes out as English nonsense, or a Hindi sentence is never transcribed | `DEEPGRAM_LANGUAGE` is `en` or `multi`; set `hi`. |
| The agent interrupts itself | you are on speakers without echo cancellation; use headphones. The client requests `echoCancellation: true`, but some devices ignore it. |
| `token server replied 500` | `LIVEKIT_API_KEY`/`LIVEKIT_API_SECRET` not set for `token_server.py`; it refuses to start without them. |
| `livekit-server --dev` and the browser are on different machines | the browser must reach `LIVEKIT_URL` itself; use the server's LAN address or LiveKit Cloud. |
| Agent log: `cloud turn detector failed (401) … falling back to local mini model` | harmless on a self-hosted server: LiveKit's hosted turn detector needs a Cloud project; the local model is used instead. |
| Browser console: `v1 RTC path not found … Retrying` | an older `livekit-server` with a newer JS SDK; the SDK retries the legacy path and connects. Upgrade the server to silence it. |

## Production notes

- Run the worker with `python agent.py start` (no hot reload, INFO logs) and set `LIVEKIT_URL` to your Cloud project
  or self-hosted server. One worker handles many rooms; LiveKit load-balances across workers.
- Put `token_server.py` behind your own auth. As written it hands a token to anyone who asks, which is right for a
  demo and wrong for a product.
- Keys stay in the environment or your secret manager. Nothing in this folder logs them; the Vartaloop plugin redacts
  its key from connection errors.
